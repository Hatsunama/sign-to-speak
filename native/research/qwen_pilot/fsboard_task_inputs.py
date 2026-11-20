"""Revalidate private adaptive-frame FSboard media and attest its matched Qwen input contract."""
import argparse
from collections import defaultdict
from dataclasses import dataclass
import hashlib
import io
from importlib.metadata import version
import json
import math
from pathlib import Path
import re
import signal
import stat
import sys
import tempfile

from PIL import Image

import fsboard_evaluate as evaluation
import fsboard_task_admission as admission_gate
import fsboard_task_protocol as protocol
import fsboard_training_intake as intake
import train_qwen_pilot as pilot
from fsboard_admission import (
    AdmissionError, CLIP_BYTES, FRAME_COUNT, MAX_DECODED_FRAMES, MAX_EDGE, MIB,
    annotation_window, write_json,
)

MIN_FRAMES = 8
ADMISSION_LIMIT = 10 * MIB
SUMMARY_LIMIT = 16 * 1024
ROLE_COUNTS = {"holdout": 64, "development": 32, "training": 192}
MODEL_FIELDS = frozenset({"input_ids", "attention_mask", "mm_token_type_ids", "image_grid_thw", "pixel_values"})
TRANSFORMERS_VERSION = "5.12.0"


@dataclass(frozen=True)
class Admission:
    root: Path
    summary: dict
    clips: dict
    frozen_protocol: dict


def check_path(path, *, directory=False):
    """Check one expected component without following filesystem links or junctions."""
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise AdmissionError("unexpected filesystem link in admitted input path")
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_type(info.st_mode):
        raise AdmissionError("unexpected filesystem object in admitted input path")
    return info


def read_bounded(path, limit):
    if check_path(path).st_size > limit:
        raise AdmissionError("admitted input file exceeds its size limit")
    with path.open("rb") as handle:
        payload = handle.read(limit + 1)
    if len(payload) > limit:
        raise AdmissionError("admitted input file exceeds its size limit")
    return payload


def read_json(payload):
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AdmissionError("invalid UTF-8 admission JSON") from exc
    if not isinstance(document, dict):
        raise AdmissionError("expected admission JSON object")
    return document


def valid_sha256(value):
    return isinstance(value, str) and admission_gate.SHA256.fullmatch(value) is not None


def load_admission(*, admission, admission_sha256, proposal, proposal_sha256, protocol_sha256, previous_receipt):
    """Bind the full manifest to independent admission, proposal and previous receipt pins."""
    if not valid_sha256(admission_sha256):
        raise AdmissionError("expected a trusted lowercase admission SHA-256")
    root = admission.parent
    check_path(root, directory=True)
    payload = read_bounded(admission, ADMISSION_LIMIT)
    if hashlib.sha256(payload).hexdigest() != admission_sha256:
        raise AdmissionError("private admission receipt integrity check failed")
    private = read_json(payload)
    if (
        private.get("mode") != "full" or private.get("status") != "full_media_verified"
        or private.get("training_media_complete") is not True
        or private.get("contains_private_signer_ids_recordings_and_targets") is not True
    ):
        raise AdmissionError("expected a complete full media admission")
    proposal_doc = admission_gate.read_proposal(proposal, proposal_sha256, protocol_sha256, previous_receipt)
    limits = proposal_doc["protocol"]["input"]
    if limits != {
        "native_video": False,
        "min_ordered_rgb_stills": MIN_FRAMES,
        "max_ordered_rgb_stills": FRAME_COUNT,
        "sampling": "distinct_pts_uniform_no_padding",
        "max_frame_edge": MAX_EDGE,
        "dense_max_sequence_tokens": 6144,
    }:
        raise AdmissionError("frozen protocol does not use the adaptive FSboard input profile")
    min_frames, max_frames = limits["min_ordered_rgb_stills"], limits["max_ordered_rgb_stills"]
    expected_summary = {
        "schema_version": 1, "experiment_id": protocol.EXPERIMENT_ID, "mode": "full",
        "status": "full_media_verified", "training_media_complete": True,
        "proposal_sha256": proposal_sha256, "protocol_sha256": protocol_sha256,
        "previous_receipt_sha256": intake.PREVIOUS_RECEIPT_SHA256, "clips_verified": ROLE_COUNTS,
        "original_bytes": sum(clip["original_bytes"] for clips in proposal_doc["clips"].values() for clip in clips),
        "min_frames_per_clip": min_frames, "max_frames_per_clip": max_frames,
        "max_frame_edge": MAX_EDGE, "training_eligible": False,
        "processor_token_contract_verified": False, "training_started": False, "model_scoring_started": False,
    }
    header = {key: private.get(key) for key in expected_summary}
    if (admission_gate.canonical_sha256(header) != admission_gate.canonical_sha256(expected_summary)
            or private.get("dataset") != proposal_doc["dataset"]):
        raise AdmissionError("full admission summary disagrees with frozen proposal provenance or counts")
    roles = private.get("clips")
    if not isinstance(roles, dict) or roles.keys() != ROLE_COUNTS.keys():
        raise AdmissionError("admission roles disagree with frozen proposal")
    source_dir = root / "source"
    source_present = source_dir.exists() or source_dir.is_symlink()
    if source_present:
        check_path(source_dir, directory=True)
    source_digests = set()
    sampled_counts = {}
    for role, quota in ROLE_COUNTS.items():
        clips = roles[role]
        if not isinstance(clips, list) or len(clips) != quota:
            raise AdmissionError("admission role count disagrees with frozen proposal")
        sampled_counts[role] = []
        for position, (clip, expected) in enumerate(zip(clips, proposal_doc["clips"][role], strict=True), 1):
            clip_id = f"{role}-{position:03d}"
            if (
                not isinstance(clip, dict) or clip.get("id") != clip_id or clip.get("role") != role
                or {key: clip.get(key) for key in expected} != expected
            ):
                raise AdmissionError("admitted clip identity or metadata disagrees with frozen proposal")
            filename = f"{clip_id}.mp4"
            digest, size = clip.get("original_sha256"), clip["original_bytes"]
            if (
                clip.get("original_path") != f"source/{filename}"
                or not valid_sha256(digest) or digest in source_digests
                or type(size) is not int or not 0 < size <= CLIP_BYTES
            ):
                raise AdmissionError("invalid or duplicate admitted source video provenance")
            if source_present and check_path(source_dir / filename).st_size != size:
                raise AdmissionError("retained source video size disagrees with admission")
            source_digests.add(digest)
            sampled_counts[role].append(sampled_frame_count(clip, min_frames, max_frames))
    if admission_gate.canonical_sha256(private.get("sampled_frame_counts")) != admission_gate.canonical_sha256(sampled_counts):
        raise AdmissionError("admission sampled frame counts disagree with clip records")
    public_summary = read_json(read_bounded(root / "summary.json", SUMMARY_LIMIT))
    expected_summary.update(admission_sha256=admission_sha256, sampled_frame_counts=sampled_counts)
    if admission_gate.canonical_sha256(public_summary) != admission_gate.canonical_sha256(expected_summary):
        raise AdmissionError("admission summary does not bind to the trusted private receipt")
    return Admission(root, expected_summary, roles, proposal_doc["protocol"])


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def sampled_frame_count(clip, min_frames, max_frames):
    frames, count = clip.get("frames"), clip.get("sampled_frame_count")
    if (type(count) is not int or not min_frames <= count <= max_frames
            or not isinstance(frames, list) or len(frames) != count):
        raise AdmissionError("invalid sampled frame count in admitted clip")
    return count


def verify_frame_timing(clip, frame, slot, count, previous_index, previous_pts):
    start, end = annotation_window(clip)
    origin, period = clip.get("clip_pts_origin_seconds"), clip.get("frame_period_seconds")
    declared_start, declared_end = clip.get("annotation_start_clip_seconds"), clip.get("annotation_end_clip_seconds")
    index, pts = frame.get("frame_index"), frame.get("source_pts_seconds")
    actual, requested = frame.get("actual_seconds"), frame.get("requested_seconds")
    offset = frame.get("annotation_offset_seconds")
    if (
        type(index) is not int or not previous_index < index < MAX_DECODED_FRAMES
        or not all(map(finite_number, (pts, actual, requested, offset, origin, period, declared_start, declared_end)))
        or pts <= previous_pts or period <= 0 or frame.get("duplicate") is not False
    ):
        raise AdmissionError("admitted frame order or timing is invalid")
    expected = start + (end - start) * slot / (count - 1)
    close = dict(rel_tol=0, abs_tol=1e-8)
    if (
        not start <= actual <= end or abs(actual - requested) > period + 1e-9
        or not math.isclose(requested, expected, **close)
        or not math.isclose(actual, pts - origin, **close)
        or not math.isclose(offset, actual - start, **close)
        or not math.isclose(start, declared_start, **close)
        or not math.isclose(end, declared_end, **close)
    ):
        raise AdmissionError("admitted frame timing disagrees with annotation window")
    return index, pts


def read_frame(path, sample):
    payload = read_bounded(path, admission_gate.FRAME_BYTES)
    if not valid_sha256(sample.get("sha256")) or hashlib.sha256(payload).hexdigest() != sample["sha256"]:
        raise AdmissionError("admitted frame PNG failed its SHA-256 check")
    width, height = sample.get("width"), sample.get("height")
    if any(type(edge) is not int or not 1 <= edge <= MAX_EDGE for edge in (width, height)):
        raise AdmissionError("admitted frame dimensions exceed the frozen RGB contract")
    try:
        with Image.open(io.BytesIO(payload), formats=("PNG",)) as image:
            if image.mode != "RGB" or image.size != (width, height) or image.n_frames != 1:
                raise AdmissionError("admitted PNG disagrees with declared RGB frame geometry")
            image.verify()
        with Image.open(io.BytesIO(payload), formats=("PNG",)) as image:
            image.load()
            return image.copy()
    except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise AdmissionError("admitted PNG failed integrity or decoding checks") from exc


def load_frames(root, clip, *, min_frames=MIN_FRAMES, max_frames=FRAME_COUNT):
    """Return the verified stored pixels in the frozen chronological slot order."""
    clip_id = clip.get("id")
    if not isinstance(clip_id, str) or not re.fullmatch(r"(holdout|development|training)-[0-9]{3}", clip_id):
        raise AdmissionError("invalid admitted clip ID for frame path")
    directory = root / "frames" / clip_id
    for path in (root, directory.parent, directory):
        check_path(path, directory=True)
    count = sampled_frame_count(clip, min_frames, max_frames)
    if {path.name for path in directory.iterdir()} != {f"{slot:02d}.png" for slot in range(count)}:
        raise AdmissionError("admitted frame directory disagrees with manifest slots")

    images = []
    previous_index, previous_pts = -1, -math.inf
    try:
        for slot, sample in enumerate(clip["frames"]):
            path = directory / f"{slot:02d}.png"
            if not isinstance(sample, dict) or sample.get("path") != path.relative_to(root).as_posix():
                raise AdmissionError("admitted frame path disagrees with frozen slot")
            previous_index, previous_pts = verify_frame_timing(clip, sample, slot, count, previous_index, previous_pts)
            images.append(read_frame(path, sample))
    except BaseException:
        for image in images:
            image.close()
        raise
    return images


def tokenize(processor, messages, *, generation):
    encoded = processor.apply_chat_template(
        messages, tokenize=True, return_dict=True, return_tensors="np",
        add_generation_prompt=generation, enable_thinking=False,
        processor_kwargs={"padding": False, "truncation": False, "return_attention_mask": True,
                          "return_mm_token_type_ids": True},
    )
    if encoded.keys() != MODEL_FIELDS:
        raise AdmissionError("processor returned unexpected model input fields")
    return encoded


def validate_encoding(np, processor, batch, frame_count):
    ids, mask = batch["input_ids"], batch["attention_mask"]
    modalities = batch["mm_token_type_ids"]
    if ids.ndim != 2 or ids.shape[0] != 1 or not ids.size or ids.dtype.kind not in "iu" or (ids < 0).any():
        raise AdmissionError("processor did not return a single valid token sequence")
    if mask.shape != ids.shape or mask.dtype.kind not in "biu" or not (mask == 1).all():
        raise AdmissionError("processor attention mask disagrees with the unpadded token sequence")
    if (
        modalities.shape != ids.shape or modalities.dtype.kind not in "iu"
        or not np.array_equal(modalities, ids == processor.image_token_id)
    ):
        raise AdmissionError("processor multimodal token types disagree with image token positions")
    grid = batch["image_grid_thw"]
    image_processor = processor.image_processor
    merge, patch, temporal_patch = (
        image_processor.merge_size, image_processor.patch_size, image_processor.temporal_patch_size,
    )
    if (
        grid.shape != (frame_count, 3) or grid.dtype.kind not in "iu" or not (grid[:, 0] == 1).all()
        or any(type(value) is not int or value <= 0 for value in (merge, patch, temporal_patch))
        or (grid[:, 1:] <= 0).any() or (grid[:, 1:] % merge).any()
    ):
        raise AdmissionError("processor grid disagrees with the ordered still-image count")
    patches = [math.prod(row) for row in grid.tolist()]
    pixels = batch["pixel_values"]
    if (
        pixels.dtype.kind != "f" or pixels.shape != (sum(patches), 3 * temporal_patch * patch**2)
        or not np.isfinite(pixels).all()
    ):
        raise AdmissionError("processor pixel patches disagree with the image grid")
    sequence = ids[0].tolist()
    starts = [index for index, token in enumerate(sequence) if token == processor.vision_start_token_id]
    ends = [index for index, token in enumerate(sequence) if token == processor.vision_end_token_id]
    if len(starts) != frame_count or len(ends) != frame_count or int(modalities.sum()) * merge**2 != sum(patches):
        raise AdmissionError("processor image token groups disagree with image patches")
    for start, end, image_patches in zip(starts, ends, patches, strict=True):
        if sequence[start + 1:end] != [processor.image_token_id] * (image_patches // merge**2):
            raise AdmissionError("processor image token order disagrees with image grid")
    return sequence


def generation_eos_ids(tokenizer_eos, model_eos, special_ids):
    """Resolve the assistant end-of-turn token and model-declared special stop tokens."""
    if type(tokenizer_eos) is not int or tokenizer_eos <= 0 or tokenizer_eos not in special_ids:
        raise AdmissionError("tokenizer did not declare a valid assistant end-of-turn token")
    if model_eos is None:
        return (tokenizer_eos,)
    declared = model_eos if isinstance(model_eos, (list, tuple)) else (model_eos,)
    if not declared or any(
        type(token) is not int or token < 0 or token not in special_ids for token in declared
    ):
        raise AdmissionError("model declared an invalid generation EOS token or list")
    return tuple(dict.fromkeys((tokenizer_eos, *declared)))


def encode_example(processor, images, reference, *, context=evaluation.PROFILE.max_sequence_tokens,
                   generation_cap=evaluation.MAX_NEW_TOKENS, min_frames=MIN_FRAMES, max_frames=FRAME_COUNT):
    """Build and validate single-example inference and target-only teacher-forcing batches."""
    import numpy as np

    frame_count = len(images)
    if not min_frames <= frame_count <= max_frames or any(
        image.mode != "RGB" or min(image.size) < 1 or max(image.size) > MAX_EDGE for image in images
    ):
        raise AdmissionError("ordered RGB still image count or size is outside the frozen bounds")
    if not isinstance(reference, str) or re.fullmatch(protocol.TARGET_PATTERN, reference) is None:
        raise AdmissionError("invalid literal FSboard fingerspelling reference")
    messages = evaluation.inference_messages(images)
    prefix = tokenize(processor, messages, generation=True)
    prefix_ids = validate_encoding(np, processor, prefix, frame_count)
    if len(prefix_ids) + generation_cap > context:
        raise AdmissionError("inference prefix and reserved generation exceed context")
    full = tokenize(processor, [*messages, {"role": "assistant", "content": reference}], generation=False)
    full_ids = validate_encoding(np, processor, full, frame_count)
    if len(full_ids) > context:
        raise AdmissionError("teacher-forcing sequence exceeds context")
    if any(not np.array_equal(prefix[field], full[field]) for field in ("pixel_values", "image_grid_thw")):
        raise AdmissionError("teacher forcing changed visual input")
    eos = processor.tokenizer.eos_token_id
    if type(eos) is not int or eos < 0:
        raise AdmissionError("tokenizer did not declare a valid assistant end-of-turn token")
    try:
        labels = pilot.completion_labels(prefix_ids, full_ids, eos)
    except ValueError as exc:
        raise AdmissionError(str(exc)) from exc
    completion = [token for token in labels if token != -100]
    if len(completion) > generation_cap:
        raise AdmissionError("literal reference and end-of-turn exceed generation cap")
    if (
        set(completion[:-1]).intersection(processor.tokenizer.all_special_ids)
        or full_ids[len(prefix_ids):].count(eos) != 1
        or processor.tokenizer.decode(completion[:-1], clean_up_tokenization_spaces=False) != reference
    ):
        raise AdmissionError("supervised tokens do not reproduce the literal fingerspelling reference")
    full["labels"] = np.asarray([labels], dtype=np.int64)
    full["pixel_values"] = prefix["pixel_values"]
    receipt = {
        "sampled_frame_count": frame_count,
        "prompt_tokens": len(prefix_ids), "teacher_forcing_tokens": len(full_ids),
        "supervised_tokens_including_eos": len(completion), "eos_token_id": eos,
        "image_grid_thw": prefix["image_grid_thw"].tolist(),
        "pixel_values_sha256": hashlib.sha256(np.ascontiguousarray(prefix["pixel_values"])).hexdigest(),
        "model_input_fields": sorted(MODEL_FIELDS),
    }
    return prefix, full, receipt


def inspect_examples(verified, processor=None):
    """Check every admitted example. An injected processor cannot attest the cloud runtime."""
    examples = defaultdict(list)
    limits = verified.frozen_protocol
    frame_bounds = {
        "min_frames": limits["input"]["min_ordered_rgb_stills"],
        "max_frames": limits["input"]["max_ordered_rgb_stills"],
    }
    for role, clips in verified.clips.items():
        for clip in clips:
            try:
                images = load_frames(verified.root, clip, **frame_bounds)
                try:
                    row = {
                        "id": clip["id"], "original_sha256": clip["original_sha256"],
                        "sampled_frame_count": clip["sampled_frame_count"],
                        "ordered_png_sha256": [frame["sha256"] for frame in clip["frames"]],
                    }
                    if processor is not None:
                        row.update(encode_example(
                            processor, images, clip["reference"],
                            context=limits["input"]["dense_max_sequence_tokens"],
                            generation_cap=limits["evaluation"]["max_new_tokens"], **frame_bounds,
                        )[2])
                    examples[role].append(row)
                finally:
                    for image in images:
                        image.close()
            except AdmissionError as exc:
                raise AdmissionError(f"{clip['id']} {exc}") from exc

    return {
        "schema_version": 1, "experiment_id": protocol.EXPERIMENT_ID,
        "status": "STRUCTURAL_VERIFIED" if processor is None else "PROCESSOR_CONTRACT_CHECKED",
        "scope": "all_roles_matched_ordered_rgb_stills",
        "admission_sha256": verified.summary["admission_sha256"],
        "proposal_sha256": verified.summary["proposal_sha256"],
        "protocol_sha256": verified.summary["protocol_sha256"],
        "previous_receipt_sha256": verified.summary["previous_receipt_sha256"],
        "model": limits["model"], "input": limits["input"],
        "prompt": limits["training"]["video_prompt"],
        "generation_max_new_tokens": limits["evaluation"]["max_new_tokens"],
        "clips_verified": {role: len(rows) for role, rows in examples.items()},
        "source_video_sha256_rehashed": False,
        "processor_token_contract_verified": False, "training_eligible": False,
        "training_started": False, "model_scoring_started": False,
        "examples": dict(examples),
    }


def verify_with_cloud_processor(verified):
    """Check the input contract with the pinned processor and Transformers version."""
    if version("transformers") != TRANSFORMERS_VERSION:
        raise AdmissionError("processor verification requires the pinned Transformers version")

    model = verified.frozen_protocol["model"]
    # Transformers prefers a local path over a Hub ID even when a revision is pinned.
    try:
        Path(model["id"]).lstat()
    except (FileNotFoundError, NotADirectoryError):
        pass
    else:
        raise AdmissionError("local path shadows the pinned Hub model")

    from transformers import AutoProcessor

    processor = AutoProcessor.from_pretrained(
        model["id"], revision=model["revision"], token=False, trust_remote_code=False,
    )
    if type(processor).__name__ != "Qwen3VLProcessor":
        raise AdmissionError("the pinned Qwen revision returned an unexpected processor")
    report = inspect_examples(verified, processor)
    if report["clips_verified"] != ROLE_COUNTS:
        raise AdmissionError("processor contract did not cover the exact frozen role counts")
    report.update(
        status="INPUTS_VERIFIED", processor_token_contract_verified=True,
        processor_class=type(processor).__name__,
        image_processor_class=type(processor.image_processor).__name__,
        runtime={name: version(name) for name in
                 ("transformers", "tokenizers", "numpy", "Pillow", "torch", "torchvision")},
    )
    return report


def publish_private(output, report):
    """Atomically publish one UTF-8 private receipt without replacing existing evidence."""
    with tempfile.TemporaryDirectory(prefix=".fsboard-inputs-", dir=output.parent) as staging:
        pending = Path(staging) / "receipt.json"
        write_json(pending, report)
        pending.chmod(0o600)
        output.hardlink_to(pending)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admission", type=Path, required=True, help="full private admission.json beside summary.json and frames")
    parser.add_argument("--admission-sha256", required=True, help="independently trusted SHA-256 of the private admission")
    parser.add_argument("--proposal", type=Path, required=True, help="frozen private-proposal.json")
    parser.add_argument("--proposal-sha256", required=True)
    parser.add_argument("--protocol-sha256", required=True)
    parser.add_argument("--previous-receipt", type=Path, required=True, help="immutable fsboard-eval-v3-approved.json")
    parser.add_argument("--output", type=Path, required=True, help="fresh JSON file on private hard-link-capable storage")
    parser.add_argument("--cloud-processor", action="store_true", help="run the pinned processor on all examples")
    arguments = vars(parser.parse_args(argv))
    output = arguments.pop("output")
    cloud_processor = arguments.pop("cloud_processor")
    old_handler = signal.signal(signal.SIGTERM, admission_gate.handle_sigterm)
    try:
        if output.exists() or output.is_symlink():
            raise AdmissionError("input receipt already exists")
        verified = load_admission(**arguments)
        report = verify_with_cloud_processor(verified) if cloud_processor else inspect_examples(verified)
        publish_private(output, report)
    except KeyboardInterrupt:
        print("FSboard input verification cancelled.", file=sys.stderr)
        return 130
    except AdmissionError as exc:
        print(f"FSboard input verification failed. {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"FSboard input verification failed. {type(exc).__name__}.", file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, old_handler)
    print(json.dumps({key: report[key] for key in (
        "status", "clips_verified", "processor_token_contract_verified", "training_eligible",
    )}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

