"""Evaluate a completed Qwen pair and its frozen base on approved FSboard clips."""

import argparse
from dataclasses import replace
import gc
from io import BytesIO
import json
import math
from pathlib import Path
import re
import sys
import time
import traceback
import unicodedata

import fsboard_admission as fsboard
import paired_experiment as paired
import train_qwen_pilot as pilot


PROMPT = (
    "These images show American Sign Language fingerspelling in time order. "
    "Transcribe the spelled text. Reply with only that text."
)
CONDITIONS = ("matched", "reordered", "black", "wrong_video")
CANDIDATES = ("base", "A", "B")
MAX_NEW_TOKENS = 32
OUTPUT_ROOT = Path("/data/out")
PROFILE = replace(pilot.DENSE, name="fsboard", evaluation_role="evaluation_only")


def normalize(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def edit_distance(left, right):
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for row, left_char in enumerate(left, 1):
        current = [row]
        for column, right_char in enumerate(right, 1):
            current.append(min(
                previous[column] + 1, current[-1] + 1,
                previous[column - 1] + (left_char != right_char),
            ))
        previous = current
    return previous[-1]


def score(decoded, reference):
    predicted = normalize(decoded)
    expected = normalize(reference)
    if not expected:
        raise ValueError("Reference is empty after normalization")
    edits = edit_distance(predicted, expected)
    return {
        "exact": predicted == expected, "edits": edits,
        "reference_characters": len(expected), "cer": edits / len(expected),
    }


def aggregate(rows):
    edits = sum(row["scores"]["edits"] for row in rows)
    characters = sum(row["scores"]["reference_characters"] for row in rows)
    exact = sum(row["scores"]["exact"] for row in rows)
    result = {
        "examples": len(rows), "exact_matches": exact, "exact_match_rate": exact / len(rows),
        "edits": edits, "reference_characters": characters, "cer": edits / characters,
        "normalized_outputs_equal_to_matched": sum(row["output_equals_matched"] for row in rows),
        "stop_counts": {stop: sum(row["stop"] == stop for row in rows) for stop in ("eos", "max_tokens")},
    }
    supervised = [row["reference_nll"] for row in rows if "reference_nll" in row]
    if supervised:
        tokens = sum(item["tokens_including_eos"] for item in supervised)
        result["reference_nll"] = {
            "examples": len(supervised),
            "tokens_including_eos": tokens,
            "mean_per_token": sum(item["total"] for item in supervised) / tokens,
        }
    return result


def finite_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def digest_value(value):
    return isinstance(value, str) and paired.SHA256.fullmatch(value) is not None


def read_json(path, expected_digest=None):
    content = path.read_bytes()
    content_digest = paired.sha256(content)
    if expected_digest is not None and content_digest != expected_digest:
        raise ValueError(f"JSON digest mismatch for {path.name}")
    document = json.loads(content)
    if not isinstance(document, dict):
        raise ValueError(f"Expected JSON object in {path.name}")
    return document, content_digest


def verified_frame_bytes(root, frame):
    path = paired.sample_path(root, frame["path"])
    content = path.read_bytes()
    if paired.sha256(content) != frame["sha256"]:
        raise ValueError("Frame digest does not match the approved admission")
    return content


def validate_timing(clip, frames):
    origin, period, duration, start, end = (clip.get(key) for key in (
        "clip_pts_origin_seconds", "frame_period_seconds", "decoded_duration_seconds",
        "annotation_start_clip_seconds", "annotation_end_clip_seconds",
    ))
    vstart, vend, astart, aend = (clip.get(key) for key in (
        "clip_start_full_video_seconds", "clip_end_full_video_seconds",
        "annotation_start_full_video_seconds", "annotation_end_full_video_seconds",
    ))
    if (not all(finite_number(value) for value in (origin, period, duration, start, end, vstart, vend, astart, aend))
            or period <= 0 or duration <= 0 or not 0 <= start < end <= duration + period + 1e-9
            or not 0 <= vstart <= astart < aend <= vend
            or not math.isclose(astart - vstart, start, abs_tol=1e-8)
            or not math.isclose(aend - vstart, end, abs_tol=1e-8)):
        raise ValueError("Invalid approved annotation or clip timing")
    duration_tolerance = fsboard.timing_tolerance(period, clip.get("decoded_timestamp_tick_seconds"))
    if abs(vend - vstart - duration) > duration_tolerance + 1e-9:
        raise ValueError("Invalid approved annotation or clip timing")
    previous_index, previous_pts = -1, -math.inf
    for slot, frame in enumerate(frames):
        index = frame.get("frame_index")
        pts, requested, actual, offset = (frame.get(key) for key in (
            "source_pts_seconds", "requested_seconds", "actual_seconds", "annotation_offset_seconds",
        ))
        if (type(index) is not int or index <= previous_index
                or not finite_number(pts) or pts <= previous_pts):
            raise ValueError("Frame indices and presentation timestamps must be strictly increasing")
        if frame.get("duplicate") is not False:
            raise ValueError("Every admitted frame must have a false duplicate flag")
        if (not all(finite_number(value) for value in (requested, actual, offset))
                or not start - 1e-9 <= actual <= end + 1e-9
                or abs(actual - requested) > period + 1e-9
                or not math.isclose(requested, start + (end - start) * slot / (len(frames) - 1), abs_tol=1e-8)
                or not math.isclose(actual, pts - origin, abs_tol=1e-8)
                or not math.isclose(offset, actual - start, abs_tol=1e-8)):
            raise ValueError("Frame timing does not match approved annotation and sampling slots")
        previous_index, previous_pts = index, pts


def load_admission(path, expected_digest):
    if not digest_value(expected_digest):
        raise ValueError("A lowercase SHA-256 for the approved admission is required")
    document, _ = read_json(path, expected_digest)
    if (type(document.get("schema_version")) is not int or document["schema_version"] != 1
            or document.get("role") != "evaluation_only"
            or document.get("status") != "evaluation_eligible"):
        raise ValueError("Admission must have role evaluation_only and status evaluation_eligible")
    dataset = document.get("dataset")
    if not isinstance(dataset, dict) or any(dataset.get(key) != expected for key, expected in {
        "id": fsboard.DATASET, "version": fsboard.VERSION, "split": fsboard.SPLIT, "license": "CC-BY-4.0",
    }.items()):
        raise ValueError("Admission must identify the pinned FSboard test split and license")
    clips = document.get("clips")
    if (not isinstance(clips, list) or not fsboard.MIN_COUNT <= len(clips) <= fsboard.MAX_COUNT
            or document.get("verified_count") != len(clips)
            or document.get("frame_count") != fsboard.FRAME_COUNT
            or document.get("max_frame_edge") != fsboard.MAX_EDGE):
        raise ValueError("Invalid admitted clip count or dense frame profile")
    seen = set()
    for clip in clips:
        clip_id = clip.get("id")
        if not isinstance(clip_id, str) or not paired.SAFE_ID.fullmatch(clip_id):
            raise ValueError("Invalid evaluation clip ID")
        if (clip.get("role") != "evaluation_only" or clip.get("split") != fsboard.SPLIT
                or clip.get("status") not in {"review_required", "evaluation_eligible"}):
            raise ValueError("All clips must be designated evaluation_only in the FSboard test split")
        signer, reference, source, filename, video, original = (clip.get(key) for key in (
            "signer_id", "reference", "source_path", "clip_filename", "full_video_filename", "original_sha256",
        ))
        if (not all(isinstance(value, str) and normalize(value) for value in (signer, reference, source, filename, video))
                or not re.fullmatch(r"[a-z]{3,8}", reference)
                or any(not fsboard.SAFE_BASENAME.fullmatch(name) for name in (filename, video))
                or source != f"{fsboard.VIDEO_DIR}/{filename}"
                or not digest_value(original)):
            raise ValueError("Invalid evaluation label or source provenance")
        keys = {("id", normalize(clip_id)), ("source_path", normalize(source)), ("digest", original)}
        if keys & seen:
            raise ValueError("Duplicate clip identity, source path or original digest")
        seen.update(keys)
        frames = clip.get("frames")
        if not isinstance(frames, list) or len(frames) != fsboard.FRAME_COUNT:
            raise ValueError("Each evaluation clip must have exactly 32 frames")
        for slot, frame in enumerate(frames):
            if frame.get("path") != f"frames/{clip_id}/{slot:02d}.png":
                raise ValueError("Frame path must be a unique numbered PNG under its clip directory")
            if (not digest_value(frame.get("sha256"))
                    or any(type(frame.get(key)) is not int or not 1 <= frame[key] <= fsboard.MAX_EDGE
                           for key in ("width", "height"))):
                raise ValueError("Invalid frame digest or dimensions")
            verified_frame_bytes(path.parent, frame)
        validate_timing(clip, frames)
    return document


def pair_path(root, relative):
    path = root / relative
    if path.is_symlink() or path.resolve() != path:
        raise ValueError("Pair artifacts may not traverse symlinks")
    return path


def load_pair(root, dense_admission):
    root = root.resolve()
    success_path = pair_path(root, "success.json")
    if not success_path.is_file():
        raise ValueError("Evaluation requires a completed A/B pair success receipt")
    success, success_sha = read_json(success_path)
    arms = success.get("arms")
    if (success.get("status") != "completed_exploratory_pair" or not isinstance(arms, dict)
            or set(arms) != {"A", "B"} or success.get("evaluation_role") != pilot.DENSE.evaluation_role):
        raise ValueError("Evaluation requires a completed A/B pair with development controls")
    experiment, experiment_sha = read_json(pair_path(root, "experiment.json"))
    dense_sha = experiment.get("dense_admission_sha256")
    if not digest_value(dense_sha) or not digest_value(experiment.get("static_manifest_sha256")):
        raise ValueError("Pair lacks a pinned dense admission or static manifest")
    dense, training, development, _ = paired.load_dense_admission(dense_admission, dense_sha)
    if (experiment.get("model") != dense["model"] or experiment.get("dataset") != dense["dataset"]
            or experiment.get("training_clips") != [clip["id"] for clip in training]
            or experiment.get("development_controls") != [clip["id"] for clip in development]):
        raise ValueError("Pair experiment differs from the frozen model or video cohorts")
    steps = experiment.get("planned_optimizer_steps_per_arm")
    if type(steps) is not int or not 1 <= steps <= paired.MAX_STEPS:
        raise ValueError("Invalid planned pair step count")
    adapter_paths = {}
    pair_digests = {"success.json": success_sha, "experiment.json": experiment_sha}
    for arm, receipt in arms.items():
        summary, summary_sha = read_json(pair_path(root, f"{arm}/summary.json"))
        if (not isinstance(receipt, dict) or receipt != summary or receipt.get("optimizer_steps") != steps
                or not digest_value(receipt.get("adapter_sha256"))):
            raise ValueError("Final arm summary disagrees with the pair success receipt")
        adapter_path = pair_path(root, f"{arm}/adapter")
        for name in ("adapter_config.json", "adapter_model.safetensors"):
            pair_path(root, f"{arm}/adapter/{name}")
        if paired.adapter_digest(adapter_path) != receipt["adapter_sha256"]:
            raise ValueError(f"Adapter digest mismatch for arm {arm}")
        config, _ = read_json(adapter_path / "adapter_config.json")
        if config.get("base_model_name_or_path") != dense["model"]["id"]:
            raise ValueError("Final adapter targets a different base model")
        if config.get("revision") not in {None, dense["model"]["revision"]}:
            raise ValueError("Final adapter targets a different base revision")
        adapter_paths[arm] = adapter_path
        pair_digests[f"{arm}/summary.json"] = summary_sha
        pair_digests[f"{arm}/adapter"] = receipt["adapter_sha256"]
    return experiment, training, development, adapter_paths, pair_digests


def source_keys(clip):
    return {
        normalize(clip[key]) for key in ("source_path", "source_url", "source_group", "full_video_filename", "path")
        if isinstance(clip.get(key), str) and normalize(clip[key])
    }


def content_digests(clip):
    return {item[key] for item in (clip, *clip.get("frames", [])) for key in ("sha256", "original_sha256")
            if digest_value(item.get(key))}


def check_holdout(clips, training_or_development):
    known_ids = {normalize(row["id"]) for row in training_or_development if isinstance(row.get("id"), str)}
    known_sources = set().union(*(source_keys(row) for row in training_or_development))
    known_digests = set().union(*(content_digests(row) for row in training_or_development))
    for clip in clips:
        if (normalize(clip["id"]) in known_ids or source_keys(clip) & known_sources
                or content_digests(clip) & known_digests):
            raise ValueError("Known evaluation overlap with a training or development ID, source or digest")


def assign_donors(clips):
    # Find a one-to-one assignment without a factorial search if provenance is repeated.
    donors = {}

    def try_assign(target, tried):
        for donor in clips:
            donor_id = donor["id"]
            if (donor_id in tried or target["id"] == donor_id
                    or normalize(target["signer_id"]) == normalize(donor["signer_id"])
                    or normalize(target["full_video_filename"]) == normalize(donor["full_video_filename"])
                    or normalize(target["reference"]) == normalize(donor["reference"])
                    or target["original_sha256"] == donor["original_sha256"]):
                continue
            tried.add(donor_id)
            if donor_id not in donors or try_assign(donors[donor_id][0], tried):
                donors[donor_id] = (target, donor)
                return True
        return False

    if not all(try_assign(clip, set()) for clip in clips):
        raise ValueError("Wrong-video controls require unrelated donor clips with distinct references")
    return {target["id"]: donor for target, donor in donors.values()}


def read_frame(image_module, root, frame):
    with image_module.open(BytesIO(verified_frame_bytes(root, frame))) as image:
        if (image.format != "PNG" or image.mode != "RGB" or getattr(image, "n_frames", 1) != 1
                or image.size != (frame["width"], frame["height"])):
            raise ValueError("Decoded frame does not match the admitted single RGB PNG")
        return image.copy()


def condition_images(image_module, root, clip, donor):
    matched = [read_frame(image_module, root, frame) for frame in clip["frames"]]
    other = [read_frame(image_module, root, frame) for frame in donor["frames"]]
    return {
        "matched": (matched, {"input_clip_id": clip["id"], "frame_indices": PROFILE.matched}),
        "reordered": ([matched[index] for index in PROFILE.shuffled],
                      {"input_clip_id": clip["id"], "frame_indices": PROFILE.shuffled}),
        "black": ([image_module.new("RGB", image.size) for image in matched],
                  {"input_clip_id": None, "frame_indices": PROFILE.matched}),
        "wrong_video": (other, {"input_clip_id": donor["id"], "frame_indices": PROFILE.matched}),
    }


def inference_messages(images):
    return [{"role": "user", "content": [
        *({"type": "image", "image": image} for image in images), {"type": "text", "text": PROMPT},
    ]}]


def encode_inputs(image_module, processor, root, clip, donor):
    visual_digests = set()
    for condition, (images, details) in condition_images(image_module, root, clip, donor).items():
        messages = inference_messages(images)
        encoded = pilot.tokenize_messages(processor, messages, generation=True, profile=PROFILE)
        prompt_length = encoded["input_ids"].shape[1]
        if prompt_length + MAX_NEW_TOKENS > PROFILE.max_sequence_tokens:
            raise ValueError("Prompt plus reserved completion exceeds the dense sequence budget")
        digest = pilot.tensor_digest(encoded["pixel_values"])
        if digest in visual_digests:
            raise ValueError("Two conditions encode indistinguishable pixel values")
        visual_digests.add(digest)
        yield condition, (messages, encoded, {
            **details, "prompt_tokens": prompt_length, "pixel_values_sha256": digest,
            "rgb_sizes": [image.size for image in images],
            "ordered_rgb_sha256": [paired.sha256(image.tobytes()) for image in images],
            "image_grid_thw": encoded["image_grid_thw"].tolist(),
        })


def reference_nll(torch, model, processor, messages, encoded, reference):
    full = pilot.tokenize_messages(processor, [*messages, {
        "role": "assistant", "content": reference,
    }], generation=False, profile=PROFILE)
    for name in ("pixel_values", "image_grid_thw"):
        if not torch.equal(encoded[name], full[name]):
            raise ValueError("Teacher forcing changed the visual input")
    labels = pilot.completion_labels(
        encoded["input_ids"][0].tolist(), full["input_ids"][0].tolist(), processor.tokenizer.eos_token_id,
    )
    targets = [token for token in labels if token != -100]
    if (len(targets) > MAX_NEW_TOKENS
            or processor.tokenizer.decode(targets[:-1], clean_up_tokenization_spaces=False) != reference):
        raise ValueError("Reference completion does not reproduce the admitted label")
    full["labels"] = torch.tensor([labels], dtype=torch.long)
    loss = pilot.completion_loss(torch, model, {key: value.to("cuda") for key, value in full.items()}).item()
    if not math.isfinite(loss):
        raise RuntimeError("Reference completion loss was not finite")
    return {"tokens_including_eos": len(targets), "mean_per_token": loss, "total": loss * len(targets)}


def evaluate_model(torch, image_module, processor, model, clips, donors, root, output, status, with_nll):
    model.eval()
    rows = []
    visual_receipts = {}
    eos = model.generation_config.eos_token_id
    if eos is None:
        raise RuntimeError("The model has no configured end-of-sequence token")
    with torch.inference_mode():
        for clip in clips:
            clip_id = clip["id"]
            status.update(clip_id=clip_id, condition="preprocessing")
            matched_text = None
            inputs = encode_inputs(image_module, processor, root, clip, donors[clip_id])
            for condition in CONDITIONS:
                status["condition"] = condition
                actual_condition, (messages, encoded, visual) = next(inputs)
                if actual_condition != condition:
                    raise RuntimeError("Encoded conditions were returned out of order")
                generated = model.generate(
                    **{name: tensor.to("cuda") for name, tensor in encoded.items()},
                    max_new_tokens=MAX_NEW_TOKENS, do_sample=False, use_cache=True,
                    return_dict_in_generate=False,
                )
                tokens = generated[0, visual["prompt_tokens"]:].tolist()
                stop = paired.generation_status(tokens, eos, MAX_NEW_TOKENS)
                decoded = processor.tokenizer.decode(
                    tokens, skip_special_tokens=True, clean_up_tokenization_spaces=False,
                )
                if condition == "matched":
                    matched_text = normalize(decoded)
                result = {
                    "clip_id": clip_id, "condition": condition,
                    "signer_id": clip["signer_id"], "source": clip["full_video_filename"],
                    "model_input": visual, "generated_token_ids": tokens,
                    "stop": stop, "generated_tokens": len(tokens), "decoded": decoded,
                    "reference": clip["reference"], "scores": score(decoded, clip["reference"]),
                    "output_equals_matched": normalize(decoded) == matched_text,
                }
                if with_nll and condition in {"matched", "wrong_video"}:
                    result["reference_nll"] = reference_nll(
                        torch, model, processor, messages, encoded, clip["reference"],
                    )
                pilot.save_json(output / clip_id / f"{condition}.json", result)
                rows.append(result)
                visual_receipts[clip_id, condition] = visual
    return rows, visual_receipts


def summarize(rows):
    result = {}
    for condition in CONDITIONS:
        selected = [row for row in rows if row["condition"] == condition]
        result[condition] = {"aggregate": aggregate(selected)}
        for key in ("signer_id", "source"):
            groups = {row[key] for row in selected}
            result[condition][f"by_{key}"] = {
                group: aggregate([row for row in selected if row[key] == group]) for group in sorted(groups)
            }
    if "reference_nll" in result["matched"]["aggregate"]:
        details = {(row["clip_id"], row["condition"]): row["reference_nll"]["mean_per_token"]
                   for row in rows if "reference_nll" in row}
        deltas = {
            clip_id: nll - details[clip_id, "matched"]
            for (clip_id, condition), nll in details.items() if condition == "wrong_video"
        }
        result["paired_reference_nll"] = {
            "wrong_video_minus_matched_by_clip": deltas,
            "clips_with_lower_matched_nll": sum(value > 0 for value in deltas.values()),
        }
    return result


def run(args, output, status):
    started = time.monotonic()
    status["stage"] = "admission"
    admitted = load_admission(args.admission, args.admission_sha256)
    clips = admitted["clips"]
    donors = assign_donors(clips)

    status["stage"] = "pair_verification"
    experiment, training, development, adapters, pair_digests = load_pair(args.pair_root, args.dense_admission)
    manifest, static_sha = read_json(args.static_manifest, experiment["static_manifest_sha256"])
    static = paired.validate_static_manifest(manifest, args.static_manifest.parent, training, development)
    check_holdout(clips, [*training, *development, *static.values()])

    status["stage"] = "runtime"
    import torch
    from peft import PeftModel
    from PIL import Image
    from transformers import AutoProcessor, Qwen3_5ForConditionalGeneration

    paired.validate_runtime(torch, output)
    requirements = Path(__file__).with_name("requirements-train-cloud.txt")
    pilot.save_json(output / "evaluation.json", {
        "status": "approved_inputs_verified", "role": "evaluation_only",
        "contains_private_evaluation_references": True,
        "model": experiment["model"], "evaluation_dataset": admitted["dataset"],
        "pair_video_dataset": experiment["dataset"],
        "admission_sha256": args.admission_sha256, "pair_artifact_sha256": pair_digests,
        "dense_admission_sha256": experiment["dense_admission_sha256"], "static_manifest_sha256": static_sha,
        "source_sha256": {path.name: paired.sha256(path.read_bytes()) for path in (
            Path(__file__), Path(fsboard.__file__), Path(paired.__file__), Path(pilot.__file__), requirements,
        )},
        "clips": len(clips), "signers": len({clip["signer_id"] for clip in clips}),
        "sources": len({clip["full_video_filename"] for clip in clips}),
        "donors": {clip_id: donor["id"] for clip_id, donor in donors.items()},
        "candidates": CANDIDATES, "conditions": CONDITIONS, "prompt": PROMPT,
        "max_new_tokens": MAX_NEW_TOKENS, "do_sample": False, "frame_count": len(PROFILE.matched),
        "max_frame_edge": fsboard.MAX_EDGE, "max_sequence_tokens": PROFILE.max_sequence_tokens,
        "reference_nll": args.reference_nll,
        "overlap_scope": "Compared recorded IDs, source fields and digests. Signer IDs are local to FSboard.",
        "normalization": "NFKC, casefold, collapse and trim whitespace. Punctuation is retained.",
        "cer": "Levenshtein character edits divided by total normalized reference characters.",
    })
    model_id, revision = (experiment["model"][key] for key in ("id", "revision"))
    status["stage"] = "processor"
    processor = AutoProcessor.from_pretrained(model_id, revision=revision, token=False, trust_remote_code=False)
    summaries = {}
    base_visuals = None
    for candidate in CANDIDATES:
        status.update(stage="model_loading", candidate=candidate)
        torch.cuda.reset_peak_memory_stats()
        model = Qwen3_5ForConditionalGeneration.from_pretrained(
            model_id, revision=revision, token=False, use_safetensors=True,
            dtype=torch.bfloat16, attn_implementation="sdpa",
        ).to("cuda")
        if model.config.model_type != "qwen3_5":
            raise RuntimeError("Loaded model is not Qwen 3.5 multimodal")
        if candidate in adapters:
            if paired.adapter_digest(adapters[candidate]) != pair_digests[f"{candidate}/adapter"]:
                raise ValueError("Adapter changed before inference loading")
            model = PeftModel.from_pretrained(model, str(adapters[candidate]), is_trainable=False)
        model.requires_grad_(False)
        status["stage"] = "generation"
        rows, visuals = evaluate_model(
            torch, Image, processor, model, clips, donors, args.admission.parent,
            output / candidate, status, args.reference_nll,
        )
        if base_visuals is None:
            base_visuals = visuals
        elif visuals != base_visuals:
            raise RuntimeError("Visual inputs changed between candidates")
        status.pop("clip_id", None)
        status.pop("condition", None)
        summary = summarize(rows)
        pilot.save_json(output / candidate / "summary.json", {
            "conditions": summary, "peak_gpu_allocated_bytes": torch.cuda.max_memory_allocated(),
        })
        summaries[candidate] = summary
        del model
        gc.collect()
        torch.cuda.empty_cache()
    status["stage"] = "completion"
    status.pop("candidate", None)
    pilot.save_json(output / "success.json", {
        "status": "completed_fsboard_evaluation", "role": "evaluation_only", "results": summaries,
        "admission_sha256": args.admission_sha256, "pair_artifact_sha256": pair_digests,
        "evaluation_sha256": paired.sha256((output / "evaluation.json").read_bytes()),
        "elapsed_seconds": time.monotonic() - started,
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admission", required=True, type=Path)
    parser.add_argument("--admission-sha256", required=True)
    parser.add_argument("--pair-root", required=True, type=Path)
    parser.add_argument("--dense-admission", required=True, type=Path,
                        help="Orientation-validated dense manifest pinned by the pair")
    parser.add_argument("--static-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="Fresh /data/out/fs-eval-<name> directory")
    parser.add_argument("--reference-nll", action="store_true", help="Also score matched and wrong-video reference tokens")
    args = parser.parse_args()
    if not digest_value(args.admission_sha256):
        parser.error("admission digest must be a lowercase SHA-256")
    output = args.output
    if (sys.platform != "linux" or output.parent != OUTPUT_ROOT or not OUTPUT_ROOT.is_dir()
            or not re.fullmatch(r"fs-eval-[a-z0-9][a-z0-9_-]{0,55}", output.name)):
        parser.error("output must be a fresh /data/out/fs-eval-<name> directory on Linux")
    try:
        output.mkdir(exist_ok=False)
    except FileExistsError:
        parser.error("output already exists; choose a fresh directory")
    status = {"stage": "startup"}
    try:
        run(args, output, status)
    except Exception as error:
        details = "".join(traceback.format_exception(error))
        details = re.sub(r"\bhf_[A-Za-z0-9]+\b", "[redacted]", details)
        details = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[redacted]", details)
        pilot.save_json(output / "error.json", {
            "status": "failed", **status, "error_type": type(error).__name__, "traceback": details,
        })
        print(f"FSboard evaluation failed at {status['stage']} ({type(error).__name__}); see private error.json",
              file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
