"""Post hoc static ASL photo diagnostic for a completed Qwen pair."""

import argparse
from collections import Counter
import gc
from pathlib import Path
import re
import sys
import time
import traceback
import unicodedata

import fsboard_evaluate as fsboard
import paired_experiment as paired
import train_qwen_pilot as pilot


MAX_NEW_TOKENS = 4
GROUPS = ("seen", "unused_eligible")
SCOPE = (
    "This post hoc diagnostic checks recognition under the static training prompt. Unused photos "
    "come from the same source family and predeclared training-eligible pool as the sampled photos. "
    "Signer and session separation are unknown. Photo labels lack expert certification. "
    "Use the preregistered real-video assessment to judge transfer to video sequences."
)


def select_rows(samples, sampled_ids):
    seen = set(sampled_ids)
    if seen - samples.keys():
        raise ValueError("The B schedule references an unapproved static photo")
    selected = {group: {} for group in GROUPS}
    for item_id, row in sorted(samples.items(), key=lambda item: (paired.sha256(item[0].encode()), item[0])):
        group = "seen" if item_id in seen else "unused_eligible"
        selected[group].setdefault(row["label"], row)
    return seen, {group: [rows[letter] for letter in sorted(rows)] for group, rows in selected.items()}


def load_inputs(args):
    experiment, training, development, adapters, digests = fsboard.load_pair(args.pair_root, args.dense_admission)
    manifest, static_sha = fsboard.read_json(args.static_manifest, experiment["static_manifest_sha256"])
    samples = paired.validate_static_manifest(manifest, args.static_manifest.parent, training, development)
    if experiment.get("static_prompt") != paired.STATIC_PROMPT:
        raise ValueError("The completed pair used a different static prompt")
    schedule_sha = experiment.get("schedule_sha256")
    if not fsboard.digest_value(schedule_sha):
        raise ValueError("The completed pair lacks a pinned schedule")
    schedule, _ = fsboard.read_json(fsboard.pair_path(args.pair_root.resolve(), "schedule.json"), schedule_sha)
    steps = experiment["planned_optimizer_steps_per_arm"]
    if set(schedule) != {"A", "B"} or any(
        not isinstance(arm, list) or len(arm) != steps for arm in schedule.values()
    ):
        raise ValueError("Both schedules must match the completed pair step count")
    sampled = []
    for a, b in zip(schedule["A"], schedule["B"]):
        if (not isinstance(a, dict) or not isinstance(b, dict)
                or "static_id" not in a or a["static_id"] is not None
                or not isinstance(b.get("static_id"), str)
                or not isinstance(a.get("video_ids"), list) or a["video_ids"] != b.get("video_ids")):
            raise ValueError("The scheduled steps must pair the same video IDs with static photos in arm B only")
        sampled.append(b["static_id"])
    seen, selected = select_rows(samples, sampled)
    return experiment, adapters, {**digests, "schedule.json": schedule_sha, "static_manifest": static_sha}, samples, selected, seen


def exact_letter(decoded, label):
    return unicodedata.normalize("NFKC", decoded).strip().upper() == label


def summarize(rows):
    result = {}
    for group in GROUPS:
        examples = [row for row in rows if row["group"] == group]
        exact = sum(row["exact"] for row in examples)
        stops = Counter(row["stop"] for row in examples)
        result[group] = {
            "examples": len(examples), "exact_matches": exact,
            "exact_match_rate": exact / len(examples) if examples else None,
            "stop_counts": {stop: stops[stop] for stop in ("eos", "max_tokens")},
        }
    return result


def run(args, output, status):
    started = time.monotonic()
    status["stage"] = "pair_verification"
    experiment, adapters, digests, samples, selected, seen = load_inputs(args)

    from PIL import Image

    status["stage"] = "image_verification"
    selected_ids = {row["id"] for rows in selected.values() for row in rows}
    images = {}
    for item_id, row in samples.items():
        image = paired.read_static_image(Image, args.static_manifest.parent, row)
        if item_id in selected_ids:
            images[item_id] = image

    status["stage"] = "runtime"
    import torch
    from peft import PeftModel
    from transformers import AutoProcessor, Qwen3_5ForConditionalGeneration

    paired.validate_runtime(torch, output)
    status["stage"] = "preprocessing"
    model_id, revision = (experiment["model"][key] for key in ("id", "revision"))
    processor = AutoProcessor.from_pretrained(model_id, revision=revision, token=False, trust_remote_code=False)
    inputs, input_receipts = {}, {}
    for item_id, image in images.items():
        messages = [{"role": "user", "content": [
            {"type": "image", "image": image}, {"type": "text", "text": paired.STATIC_PROMPT},
        ]}]
        encoded = pilot.tokenize_messages(processor, messages, generation=True, profile=paired.STATIC_PROFILE)
        prompt_tokens = encoded["input_ids"].shape[1]
        if prompt_tokens + MAX_NEW_TOKENS > paired.STATIC_PROFILE.max_sequence_tokens:
            raise ValueError("The static prompt plus generation exceeds the sequence budget")
        inputs[item_id] = encoded
        input_receipts[item_id] = {
            "prompt_tokens": prompt_tokens, "image_grid_thw": encoded["image_grid_thw"].tolist(),
            "image_sha256": samples[item_id]["sha256"],
            "pixel_values_sha256": pilot.tensor_digest(encoded["pixel_values"]),
        }
    requirements = Path(__file__).with_name("requirements-train-cloud.txt")
    pilot.save_json(output / "diagnostic.json", {
        "role": "posthoc_static_diagnostic", "scope": SCOPE, "independent_evaluation": False,
        "model": experiment["model"], "pair_and_input_sha256": digests,
        "dense_admission_sha256": experiment["dense_admission_sha256"],
        "source_sha256": {path.name: paired.sha256(path.read_bytes()) for path in (
            Path(__file__), Path(fsboard.__file__), Path(paired.__file__), Path(pilot.__file__), requirements,
        )},
        "post_hoc_selection": "First by SHA-256 of UTF-8 sample ID within each group and letter",
        "source_groups": sorted({row["source_group"] for row in samples.values()}),
        "signer_and_session_separation": "unknown", "admitted_photos": len(samples),
        "static_schedule_slots": experiment["planned_optimizer_steps_per_arm"],
        "unique_static_photos_in_b_schedule": len(seen), "unused_eligible_photos": len(samples) - len(seen),
        "selected_ids_by_group_and_letter": {
            group: {row["label"]: row["id"] for row in rows} for group, rows in selected.items()
        },
        "private_model_inputs": input_receipts, "candidates": fsboard.CANDIDATES,
        "prompt": paired.STATIC_PROMPT, "max_new_tokens": MAX_NEW_TOKENS,
        "do_sample": False, "num_beams": 1, "normalization": "NFKC, trim outer whitespace, uppercase",
    })

    summaries = {}
    for candidate in fsboard.CANDIDATES:
        status.update(stage="model_loading", candidate=candidate)
        model = Qwen3_5ForConditionalGeneration.from_pretrained(
            model_id, revision=revision, token=False, use_safetensors=True,
            dtype=torch.bfloat16, attn_implementation="sdpa",
        ).to("cuda")
        if model.config.model_type != "qwen3_5":
            raise RuntimeError("Loaded model is not Qwen 3.5 multimodal")
        if candidate in adapters:
            adapter = adapters[candidate]
            if paired.adapter_digest(adapter) != digests[f"{candidate}/adapter"]:
                raise ValueError("Adapter changed before inference loading")
            model = PeftModel.from_pretrained(model, str(adapter), is_trainable=False)
        model.requires_grad_(False).eval()
        eos = model.generation_config.eos_token_id
        if eos is None:
            raise RuntimeError("The model lacks an EOS token")
        status["stage"] = "generation"
        results = []
        with torch.inference_mode():
            for group, rows in selected.items():
                for row in rows:
                    item_id, label = row["id"], row["label"]
                    status["sample_id"] = item_id
                    generated = model.generate(
                        **{key: value.to("cuda") for key, value in inputs[item_id].items()},
                        max_new_tokens=MAX_NEW_TOKENS, do_sample=False, num_beams=1,
                        use_cache=True, return_dict_in_generate=False,
                    )
                    tokens = generated[0, input_receipts[item_id]["prompt_tokens"]:].tolist()
                    decoded = processor.tokenizer.decode(
                        tokens, skip_special_tokens=True, clean_up_tokenization_spaces=False,
                    )
                    results.append({
                        "group": group, "sample_id": item_id, "label": label, "decoded": decoded,
                        "exact": exact_letter(decoded, label), "generated_token_ids": tokens,
                        "generated_tokens": len(tokens), "stop": paired.generation_status(tokens, eos, MAX_NEW_TOKENS),
                    })
                    del generated
        status.pop("sample_id", None)
        pilot.save_json(output / candidate / "private_predictions.json", results)
        summaries[candidate] = summarize(results)
        del model
        gc.collect()
        torch.cuda.empty_cache()

    pilot.save_json(output / "success.json", {
        "status": "completed_posthoc_static_diagnostic", "scope": SCOPE, "results": summaries,
        "diagnostic_sha256": paired.sha256((output / "diagnostic.json").read_bytes()),
        "elapsed_seconds": time.monotonic() - started,
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static-manifest", required=True, type=Path, help="Approved manifest pinned by the pair")
    parser.add_argument("--dense-admission", required=True, type=Path, help="Dense video manifest pinned by the pair")
    parser.add_argument("--pair-root", required=True, type=Path, help="Directory with the completed pair receipts")
    parser.add_argument("--output", required=True, type=Path, help="Fresh private /data/out/static-diagnostic-<name> directory")
    args = parser.parse_args()
    output = args.output
    if (sys.platform != "linux" or output.parent != paired.OUTPUT_ROOT or not paired.OUTPUT_ROOT.is_dir()
            or not re.fullmatch(r"static-diagnostic-[a-z0-9][a-z0-9_-]{0,45}", output.name)):
        parser.error("output must be a fresh /data/out/static-diagnostic-<name> directory on Linux")
    try:
        output.mkdir(mode=0o700, exist_ok=False)
    except FileExistsError:
        parser.error("output already exists")
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
        print(f"Static diagnostic failed at {status['stage']} ({type(error).__name__}); see private error.json",
              file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
