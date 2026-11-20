"""Prepare gated, on-demand training inputs for the frozen FSboard v4 ablation."""

from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from functools import partial
from io import BytesIO
from itertools import cycle, islice
from pathlib import Path
import re

from PIL import Image

import fsboard_task_admission as admission_gate
import fsboard_task_inputs as gate
import fsboard_task_protocol as protocol
import paired_experiment as paired
import static_admission
import train_qwen_pilot as pilot


ENCODING_FIELDS = frozenset({
    "sampled_frame_count", "prompt_tokens", "teacher_forcing_tokens", "supervised_tokens_including_eos",
    "eos_token_id", "image_grid_thw", "pixel_values_sha256", "model_input_fields",
})


def _read_pinned(path, digest):
    if not gate.valid_sha256(digest):
        raise gate.AdmissionError("expected an independently trusted SHA-256 digest")
    payload = gate.read_bounded(path, gate.ADMISSION_LIMIT)
    if paired.sha256(payload) != digest:
        raise gate.AdmissionError(f"pinned JSON digest mismatch for {path.name}")
    return gate.read_json(payload)


def _static_id(value):
    return isinstance(value, str) and value.startswith("static-") and gate.valid_sha256(value[7:])


def load_prior_static_ids(path):
    """Recover the historical arm B order from the exact paired-80 schedule bytes."""
    document = _read_pinned(path, protocol.PREVIOUS_SCHEDULE_SHA256)
    count = protocol.PREVIOUS_STATIC_ROW_COUNT
    if document.keys() != {"A", "B"} or any(
        not isinstance(steps, list) or len(steps) != count for steps in document.values()
    ):
        raise gate.AdmissionError("expected the paired-80 schedule with both prior arms")
    ids = []
    for arm_a, arm_b in zip(document["A"], document["B"], strict=True):
        if any(not isinstance(step, dict) or step.keys() != {"video_ids", "static_id"} for step in (arm_a, arm_b)):
            raise gate.AdmissionError("invalid prior schedule step")
        video = arm_a["video_ids"]
        if (
            arm_a["static_id"] is not None or not _static_id(arm_b["static_id"])
            or not isinstance(video, list) or len(video) != 2
            or not all(gate.valid_sha256(item) for item in video)
            or video[0] == video[1] or video != arm_b["video_ids"]
        ):
            raise gate.AdmissionError("invalid prior video pairing or static ID")
        ids.append(arm_b["static_id"])
    if len(set(ids)) != count:
        raise gate.AdmissionError("expected 80 distinct prior arm B static IDs")
    return tuple(ids)


def _training_clips(verified):
    if (
        not isinstance(verified, gate.Admission) or verified.clips.keys() != gate.ROLE_COUNTS.keys()
        or verified.summary.get("status") != "full_media_verified"
        or verified.summary.get("training_media_complete") is not True
        or verified.summary.get("clips_verified") != gate.ROLE_COUNTS
    ):
        raise gate.AdmissionError("expected the gate-verified FSboard role admission")
    seen_groups = set()
    for role, count in gate.ROLE_COUNTS.items():
        clips = verified.clips[role]
        if len(clips) != count:
            raise gate.AdmissionError("FSboard role does not match its frozen count")
        groups = set()
        for index, clip in enumerate(clips, 1):
            if clip.get("role") != role or clip.get("id") != f"{role}-{index:03d}":
                raise gate.AdmissionError("FSboard clip ID or role disagrees with the admitted order")
            for field in ("signer_id", "full_video_filename"):
                value = clip.get(field)
                if not isinstance(value, str) or not value.strip():
                    raise gate.AdmissionError("FSboard role lacks a publisher signer or recording ID")
                groups.add((field, value.strip().casefold()))
        if groups & seen_groups:
            raise gate.AdmissionError("FSboard publisher signer or recording IDs overlap across roles")
        seen_groups.update(groups)
    return {clip["id"]: clip for clip in verified.clips["training"]}


def build_schedule(verified, static_ids):
    """Build both arms using the admitted order as the input to the shared shuffle."""
    videos = _training_clips(verified)
    count = protocol.PREVIOUS_STATIC_ROW_COUNT
    if len(static_ids) != count or not all(_static_id(item) for item in static_ids) or len(set(static_ids)) != count:
        raise gate.AdmissionError("expected 80 distinct ordered static IDs")
    steps = protocol.planned_optimizer_steps(len(videos))
    video_stream = paired.shuffled_epochs(videos, pilot.SEED)
    paired_video = tuple(tuple(islice(video_stream, pilot.GRAD_ACCUMULATION)) for _ in range(steps))
    return {
        "A": tuple(paired.Step(video) for video in paired_video),
        "B": tuple(paired.Step(video, item) for video, item in zip(paired_video, cycle(static_ids))),
    }


def _static_source_bytes(root, row):
    path = root / row["path"]
    gate.check_path(path.parent, directory=True)
    payload = gate.read_bounded(path, row["original_bytes"])
    if len(payload) != row["original_bytes"] or paired.sha256(payload) != row["sha256"]:
        raise gate.AdmissionError(f"static source bytes disagree with the manifest for {row['id']}")
    return payload


def load_static_rows(verified, manifest, static_ids):
    """Validate the selected training rows and their original files before arm execution."""
    document = _read_pinned(manifest, protocol.STATIC_MANIFEST_SHA256)
    if (
        type(document.get("schema_version")) is not int or document["schema_version"] != 1
        or document.get("role") != "training_eligible" or not isinstance(document.get("samples"), list)
    ):
        raise gate.AdmissionError("invalid static training manifest")
    selected = set(static_ids)
    rows = {}
    for row in document["samples"]:
        if not isinstance(row, dict) or not _static_id(row.get("id")):
            raise gate.AdmissionError("invalid static training manifest row ID")
        item = row["id"]
        if item not in selected:
            continue
        label, source = row.get("label"), row.get("source_path")
        if (
            item in rows or row.get("role") != "training_eligible" or row.get("split") != "train"
            or not isinstance(label, str) or label not in paired.STATIC_LETTERS
            or row.get("sha256") != item[7:] or row.get("path") != f"images/{item}.jpg"
            or not isinstance(source, str) or re.fullmatch(rf"{label}/{label}[0-9]{{4}}_test[.]jpg", source) is None
            or row.get("source_group") != static_admission.SOURCE_GROUP
            or type(row.get("original_bytes")) is not int
            or not 1 <= row["original_bytes"] <= static_admission.IMAGE_CAP
            or any(type(row.get(key)) is not int or not 1 <= row[key] <= static_admission.IMAGE_EDGE
                   for key in ("width", "height"))
        ):
            raise gate.AdmissionError("invalid selected static training row")
        rows[item] = row
    if rows.keys() != selected or {row["label"] for row in rows.values()} != paired.STATIC_LETTERS:
        raise gate.AdmissionError("selected static training rows must exist and cover all 24 letters")

    clips = [clip for role in verified.clips.values() for clip in role]
    used_paths = {clip["source_path"].casefold() for clip in clips}
    video_digests = {clip["original_sha256"] for clip in clips}
    for row in rows.values():
        source = row["source_path"].casefold()
        if source in used_paths or row["sha256"] in video_digests:
            raise gate.AdmissionError("static training sources repeat a source path or reuse admitted video bytes")
        used_paths.add(source)
        _static_source_bytes(manifest.parent, row)
    return {item: rows[item] for item in static_ids}


def _validate_encoding_receipt(row, count, context, generation_cap):
    prompt, full, supervised, eos = (row.get(key) for key in (
        "prompt_tokens", "teacher_forcing_tokens", "supervised_tokens_including_eos", "eos_token_id",
    ))
    grid = row.get("image_grid_thw")
    if (
        any(type(number) is not int for number in (prompt, full, supervised, eos))
        or not 0 < prompt <= context - generation_cap or not prompt < full <= context
        or not 1 <= supervised <= min(full - prompt, generation_cap) or eos < 0
        or row.get("model_input_fields") != sorted(gate.MODEL_FIELDS)
        or not gate.valid_sha256(row.get("pixel_values_sha256"))
        or not isinstance(grid, list) or len(grid) != count
        or any(not isinstance(image, list) or len(image) != 3 or image[0] != 1
               or any(type(edge) is not int or edge < 1 for edge in image) for image in grid)
    ):
        raise gate.AdmissionError("processor receipt lacks an attested encoding for an admitted clip")


def validate_processor_receipt(verified, path, trusted_sha256):
    """Bind an independently pinned cloud processor receipt to all 288 admitted clips."""
    report = _read_pinned(path, trusted_sha256)
    limits = verified.frozen_protocol
    context = limits["input"]["dense_max_sequence_tokens"]
    generation_cap = limits["evaluation"]["max_new_tokens"]
    binding = {key: verified.summary.get(key) for key in (
        "admission_sha256", "proposal_sha256", "protocol_sha256", "previous_receipt_sha256",
    )}
    expected = {
        **binding, "schema_version": 1, "experiment_id": protocol.EXPERIMENT_ID,
        "status": "INPUTS_VERIFIED", "scope": "all_roles_matched_ordered_rgb_stills",
        "model": limits["model"], "input": limits["input"], "prompt": limits["training"]["video_prompt"],
        "generation_max_new_tokens": generation_cap, "clips_verified": gate.ROLE_COUNTS,
        "processor_class": "Qwen3VLProcessor", "processor_token_contract_verified": True,
        "training_eligible": False, "training_started": False, "model_scoring_started": False,
    }
    runtime = report.get("runtime")
    examples = report.get("examples")
    image_class = report.get("image_processor_class")
    if (
        not all(gate.valid_sha256(value) for value in binding.values())
        or admission_gate.canonical_sha256(limits) != binding["protocol_sha256"]
        or admission_gate.canonical_sha256({key: report.get(key) for key in expected})
           != admission_gate.canonical_sha256(expected)
        or not isinstance(image_class, str) or not image_class
        or not isinstance(runtime, dict) or runtime.get("transformers") != gate.TRANSFORMERS_VERSION
        or not all(isinstance(runtime.get(name), str) and runtime[name]
                   for name in ("tokenizers", "numpy", "Pillow", "torch", "torchvision"))
        or not isinstance(examples, dict) or examples.keys() != gate.ROLE_COUNTS.keys()
    ):
        raise gate.AdmissionError("processor receipt does not attest the pinned admission, protocol, or model")

    for role, clips in verified.clips.items():
        rows = examples[role]
        if not isinstance(rows, list) or len(rows) != gate.ROLE_COUNTS[role]:
            raise gate.AdmissionError("processor receipt must attest every admitted clip")
        for clip, row in zip(clips, rows, strict=True):
            count = clip["sampled_frame_count"]
            if (
                not isinstance(row, dict) or row.get("id") != clip["id"]
                or row.get("original_sha256") != clip["original_sha256"]
                or row.get("ordered_png_sha256") != [frame["sha256"] for frame in clip["frames"]]
                or type(row.get("sampled_frame_count")) is not int or row["sampled_frame_count"] != count
            ):
                raise gate.AdmissionError("processor receipt clip identities, frame counts or ordered hashes disagree")
            _validate_encoding_receipt(row, count, context, generation_cap)
    return report


def _video_batch(verified, attested, torch, processor, clip):
    limits = verified.frozen_protocol
    frame_bounds = {
        "min_frames": limits["input"]["min_ordered_rgb_stills"],
        "max_frames": limits["input"]["max_ordered_rgb_stills"],
    }
    images = gate.load_frames(verified.root, clip, **frame_bounds)
    try:
        prefix, full, receipt = gate.encode_example(
            processor, images, clip["reference"],
            context=limits["input"]["dense_max_sequence_tokens"],
            generation_cap=limits["evaluation"]["max_new_tokens"], **frame_bounds,
        )
        del prefix
        if receipt != attested[clip["id"]]:
            raise gate.AdmissionError(f"{clip['id']} encoding differs from the attested processor input")
        return {name: torch.from_numpy(array) for name, array in full.items()}
    finally:
        for image in images:
            image.close()


def _static_batch(root, torch, processor, row):
    payload = _static_source_bytes(root, row)
    with closing(Image.open(BytesIO(payload), formats=("JPEG",))) as image:
        if image.mode != "RGB" or image.size != (row["width"], row["height"]) or getattr(image, "n_frames", 1) != 1:
            raise gate.AdmissionError("static image disagrees with the manifest RGB dimensions")
        image.load()
        return paired.static_training_example(torch, processor, image, row["label"])


class _LazyBatches(Mapping):
    """Keep only row metadata and build a fresh batch for each requested microbatch."""

    def __init__(self, rows, load):
        self._rows = rows
        self._load = load

    def __getitem__(self, item_id):
        return self._load(self._rows[item_id])

    def __iter__(self):
        return iter(self._rows)

    def __len__(self):
        return len(self._rows)

    def __contains__(self, item_id):
        return item_id in self._rows


@dataclass(frozen=True)
class PreparedTraining:
    _verified: gate.Admission
    _video_rows: dict
    _static_root: Path
    _static_rows: dict
    _attested: dict
    _processor_classes: tuple[str, str]
    _schedule: dict

    def arm_inputs(self, arm, *, torch, processor):
        """Return the lazy CPU mappings and schedule for paired_experiment.train_arm."""
        schedule = self._schedule[arm]
        classes = type(processor).__name__, type(processor.image_processor).__name__
        if classes != self._processor_classes:
            raise gate.AdmissionError("runtime processor classes disagree with the trusted processor receipt")
        video = _LazyBatches(self._video_rows, partial(_video_batch, self._verified, self._attested, torch, processor))
        static_rows = self._static_rows if arm == "B" else {}
        static = _LazyBatches(static_rows, partial(_static_batch, self._static_root, torch, processor))
        return video, static, schedule


def prepare_training(verified, *, processor_receipt, processor_receipt_sha256, prior_schedule, static_manifest):
    """Check the training inputs so the caller can provide the model and run each arm."""
    videos = _training_clips(verified)
    report = validate_processor_receipt(verified, processor_receipt, processor_receipt_sha256)
    static_ids = load_prior_static_ids(prior_schedule)
    schedules = build_schedule(verified, static_ids)
    static_rows = load_static_rows(verified, static_manifest, static_ids)
    attested = {row["id"]: {key: row[key] for key in ENCODING_FIELDS}
                for row in report["examples"]["training"]}
    return PreparedTraining(
        verified, videos, static_manifest.parent, static_rows, attested,
        (report["processor_class"], report["image_processor_class"]), schedules,
    )
