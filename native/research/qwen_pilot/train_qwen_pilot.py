"""Run a bounded multimodal LoRA pilot inside the cloud GPU job."""

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
from importlib.metadata import version
import json
import math
from pathlib import Path
import random
import re
import sys
import time
import traceback


MODEL_ID = "Qwen/Qwen3.5-0.8B"
DATASET_ID = "facebook/2M-Flores-ASL"
DATASET_REVISION = "b450c1a427738e78f06362fc4619674f5d74f774"
PILOT = Path("/data/pilot")
INSTRUCTION = (
    "These images are frames from one American Sign Language clip. "
    "Read them in the order shown. Reply with only the English translation."
)
SEED = 17631
MAX_SEQUENCE_TOKENS = 1024
MAX_TARGET_TOKENS = 64
MAX_NEW_TOKENS = 64
GRAD_ACCUMULATION = 2
LEARNING_RATE = 2e-4
CHECKPOINT_INTERVAL = 10
MAX_OPTIMIZER_STEPS = 40
DEPS = (
    "torch", "torchvision", "transformers", "peft", "accelerate", "Pillow",
    "huggingface-hub", "tokenizers", "safetensors", "numpy", "Jinja2",
)


@dataclass(frozen=True)
class InputProfile:
    name: str
    root: Path
    admitted_frames: int
    matched: tuple[int, ...]
    shuffled: tuple[int, ...]
    max_sequence_tokens: int
    evaluation_role: str

    def __post_init__(self):
        if (not self.matched or tuple(sorted(set(self.matched))) != self.matched
                or self.matched[0] < 0 or self.matched[-1] >= self.admitted_frames
                or sorted(self.shuffled) != list(self.matched)
                or any(a == b for a, b in zip(self.matched, self.shuffled))):
            raise ValueError("Visual conditions must contain the same distinct slots in different orders")


DENSE_FRAME_COUNT = 32
SPARSE_ADMISSION_SHA256 = "886be06ad069c3401e53d314d4f69a1fbc7ac025f23662202791c0ec62a16101"
SPARSE = InputProfile(
    "sparse", PILOT, 8, (0, 2, 5, 7), (5, 0, 7, 2), MAX_SEQUENCE_TOKENS, "holdout",
)
DENSE = InputProfile(
    "dense", Path("/data/pilot_dense"), DENSE_FRAME_COUNT, tuple(range(DENSE_FRAME_COUNT)),
    tuple((13 * slot + 7) % DENSE_FRAME_COUNT for slot in range(DENSE_FRAME_COUNT)),
    6144, "development_controls",
)
PROFILES = {profile.name: profile for profile in (SPARSE, DENSE)}


def visual_conditions(profile=SPARSE):
    return {"matched": profile.matched, "shuffled": profile.shuffled, "blank": None}


def inference_messages(images):
    content = [{"type": "image", "image": image} for image in images]
    return [{"role": "user", "content": content + [{"type": "text", "text": INSTRUCTION}]}]


def completion_labels(prefix_ids, full_ids, eos_id):
    start = len(prefix_ids)
    if not start or full_ids[:start] != prefix_ids:
        raise ValueError("Teacher-forcing input does not start with the inference prefix")
    try:
        end = full_ids.index(eos_id, start)
    except ValueError:
        raise ValueError("Assistant completion has no end-of-turn token") from None
    if end == start:
        raise ValueError("Assistant completion is empty")
    return [-100] * start + full_ids[start:end + 1] + [-100] * (len(full_ids) - end - 1)


def validate_admission(document, profile=SPARSE):
    if (document.get("schema_version") != 1
            or document.get("ready") is not True or document.get("status") != "ready"):
        raise ValueError("Admission is not ready")
    admitted_frames = document.get("frames_per_clip", SPARSE.admitted_frames)
    if admitted_frames != profile.admitted_frames:
        raise ValueError(f"Admission must declare {profile.admitted_frames} frames per clip")
    if profile.name == "dense" and document.get("evaluation_role") != profile.evaluation_role:
        raise ValueError("Dense evaluation clips must be designated as development controls")
    model = document["model"]
    if (model["id"] != MODEL_ID or model["model_type"] != "qwen3_5"
            or model["pipeline_tag"] != "image-text-to-text"
            or not re.fullmatch(r"[a-f0-9]{40}", model["revision"])):
        raise ValueError("Admission model identity or pinned revision is invalid")
    dataset = document["dataset"]
    if (dataset["revision"] != DATASET_REVISION or dataset["id"] != DATASET_ID
            or dataset["license"].lower() != "cc-by-sa-4.0"):
        raise ValueError("Admission dataset identity, pinned revision or license is invalid")

    splits = {"train": [], "holdout": []}
    seen_ids = set()
    seen_bytes = set()
    seen_paths = set()
    group_splits = {}
    for clip in document["clips"]:
        clip_id, split, digest = clip["id"], clip["split"], clip["sha256"]
        group, source_path = clip["source_group"], clip["source_path"]
        if split not in splits or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", clip_id):
            raise ValueError("Invalid clip ID or split")
        if not re.fullmatch(r"[a-f0-9]{64}", digest) or not group or not source_path:
            raise ValueError("Invalid clip provenance")
        if clip_id in seen_ids or digest in seen_bytes or source_path in seen_paths:
            raise ValueError("Duplicate clip identity, bytes or source path")
        if group_splits.setdefault(group, split) != split:
            raise ValueError("Source group occurs in both splits")
        reference = clip["reference"]
        if not reference.strip() or re.search(r"<\||</?think>", reference):
            raise ValueError("Invalid English reference")
        frames = clip["frames"]
        if len(frames) != profile.admitted_frames:
            raise ValueError(f"Each admitted clip must have {profile.admitted_frames} frames")
        for index, frame in enumerate(frames):
            if frame["path"] != f"frames/{clip_id}/{index:02d}.png":
                raise ValueError("Frame paths must be relative, unique and chronologically numbered")
        for key in ("requested_seconds", "actual_seconds"):
            times = [frame[key] for frame in frames]
            if (any(not math.isfinite(value) or value < 0 for value in times)
                    or times != sorted(times)):
                raise ValueError("Frame timestamps must be nonnegative and chronological")
        if profile.name == "dense":
            source_times = [frame["source_pts_seconds"] for frame in frames]
            if (any(not math.isfinite(value) for value in source_times)
                    or source_times != sorted(source_times)):
                raise ValueError("Source presentation timestamps must be finite and chronological")
            seen_pts = set()
            for frame, pts in zip(frames, source_times):
                if (not math.isclose(frame["actual_seconds"], pts - source_times[0], abs_tol=1e-8)
                        or frame["duplicate"] is not (pts in seen_pts)):
                    raise ValueError("Inconsistent presentation timestamp or duplicate flag")
                seen_pts.add(pts)
        seen_ids.add(clip_id)
        seen_bytes.add(digest)
        seen_paths.add(source_path)
        splits[split].append(clip)

    counts = {split: len(clips) for split, clips in splits.items()}
    if counts != document["counts"] or not (1 <= counts["train"] <= 16 and 1 <= counts["holdout"] <= 4):
        raise ValueError("Admission split counts are missing, inconsistent or out of bounds")
    if profile.name == "dense" and counts != {"train": 16, "holdout": 4}:
        raise ValueError("Dense admission must preserve all 16 training and four development clips")
    return splits["train"], splits["holdout"]


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def frame_image(image_module, path, profile=SPARSE):
    with image_module.open(profile.root / path) as image:
        if image.format != "PNG" or image.mode != "RGB" or max(image.size) > 384:
            raise ValueError("Admitted frame is not a bounded RGB PNG")
        return image.copy()


def tensor_digest(tensor):
    return hashlib.sha256(tensor.contiguous().numpy().tobytes()).hexdigest()


def tokenize_messages(processor, messages, generation, profile=SPARSE):
    encoded = processor.apply_chat_template(
        messages, tokenize=True, return_dict=True, return_tensors="pt",
        add_generation_prompt=generation, enable_thinking=False,
    )
    prompt_tokens = encoded["input_ids"].shape[1]
    reserved_tokens = MAX_NEW_TOKENS if generation and profile.name == "dense" else 0
    if prompt_tokens + reserved_tokens > profile.max_sequence_tokens:
        raise ValueError("Input sequence and reserved generation exceeded the pilot token budget")
    grid = encoded["image_grid_thw"]
    expected_images = len(profile.matched)
    if grid.shape != (expected_images, 3) or not encoded["pixel_values"].numel():
        raise ValueError(f"Processor did not encode {expected_images} image inputs")
    if any(tensor.device.type != "cpu" for tensor in encoded.values()):
        raise ValueError("Processed inputs must remain on CPU until model execution")
    return encoded


def training_example(torch, image_module, processor, clip, output, profile=SPARSE):
    indices = profile.matched
    frames = [clip["frames"][index] for index in indices]
    images = [frame_image(image_module, frame["path"], profile) for frame in frames]
    messages = inference_messages(images)
    prefix = tokenize_messages(processor, messages, generation=True, profile=profile)
    full = tokenize_messages(processor, messages + [
        {"role": "assistant", "content": clip["reference"].strip()},
    ], generation=False, profile=profile)
    if not torch.equal(prefix["pixel_values"], full["pixel_values"]):
        raise ValueError("Teacher-forcing input changed image tensors")
    labels = completion_labels(
        prefix["input_ids"][0].tolist(), full["input_ids"][0].tolist(), processor.tokenizer.eos_token_id,
    )
    target = [token for token in labels if token != -100]
    if len(target) > MAX_TARGET_TOKENS:
        raise ValueError("Assistant target exceeded the pilot token budget")
    teacher_text = processor.tokenizer.decode(target[:-1], clean_up_tokenization_spaces=False)
    if teacher_text != clip["reference"].strip():
        raise ValueError("Supervised tokens do not reproduce the English reference")
    save_json(output / "training_inputs" / f"{clip['id']}.json", {
        "frames": frames, "indices": indices, "pixel_values_sha256": tensor_digest(full["pixel_values"]),
        "prompt_tokens": prefix["input_ids"].shape[1], "supervised_tokens_including_eos": len(target),
    })
    full["labels"] = torch.tensor([labels], dtype=torch.long)
    return full


def condition_inputs(image_module, processor, clip, profile=SPARSE):
    conditions = visual_conditions(profile)
    selected = conditions["matched"]
    images_by_index = {
        index: frame_image(image_module, clip["frames"][index]["path"], profile) for index in selected
    }
    encoded_controls = {}
    for condition, order in conditions.items():
        if order is None:
            images = [image_module.new("RGB", images_by_index[index].size) for index in selected]
        else:
            images = [images_by_index[index] for index in order]
        encoded = tokenize_messages(processor, inference_messages(images), generation=True, profile=profile)
        encoded_controls[condition] = (encoded, {
            "frame_indices": order,
            "frames": [clip["frames"][index] for index in order] if order is not None else [],
            "rgb_sizes": [image.size for image in images],
            "ordered_rgb_sha256": [hashlib.sha256(image.tobytes()).hexdigest() for image in images],
            "pixel_values_sha256": tensor_digest(encoded["pixel_values"]),
            "image_grid_thw": encoded["image_grid_thw"].tolist(),
            "prompt_tokens": encoded["input_ids"].shape[1],
        })
    ordered_hashes = encoded_controls["matched"][1]["ordered_rgb_sha256"]
    shuffled_hashes = encoded_controls["shuffled"][1]["ordered_rgb_sha256"]
    expected_shuffle = [ordered_hashes[selected.index(index)] for index in conditions["shuffled"]]
    if shuffled_hashes != expected_shuffle or shuffled_hashes == ordered_hashes:
        raise ValueError("Temporal shuffle failed to reorder visible inputs")
    if len({details["pixel_values_sha256"] for _, details in encoded_controls.values()}) != len(conditions):
        raise ValueError("Processor produced indistinguishable visual control inputs")
    return encoded_controls


def words(text):
    return re.findall(r"[^\W_]+(?:'[^\W_]+)?", text.casefold().replace("\u2019", "'"))


def score(decoded, reference):
    prediction, expected = words(decoded), words(reference)
    overlap = sum((Counter(prediction) & Counter(expected)).values())
    return {
        "normalized_exact_match": bool(expected) and prediction == expected,
        "token_f1": (2 * overlap / (len(prediction) + len(expected))) if expected else 0.0,
    }


def evaluate(torch, image_module, processor, model, holdout, output, phase, profile=SPARSE):
    model.eval()
    results = {condition: [] for condition in visual_conditions(profile)}
    output_matches = {condition: [] for condition in ("shuffled", "blank")}
    with torch.inference_mode():
        for clip in holdout:
            controls = condition_inputs(image_module, processor, clip, profile)
            predictions = {}
            for condition, (encoded, visual_receipt) in controls.items():
                prompt_length = encoded["input_ids"].shape[1]
                generated = model.generate(
                    **{name: tensor.to("cuda") for name, tensor in encoded.items()},
                    max_new_tokens=MAX_NEW_TOKENS, do_sample=False,
                    use_cache=True,
                )
                tokens = generated[0, prompt_length:].tolist()
                if not tokens:
                    raise RuntimeError("Model generated no continuation tokens")
                decoded = processor.tokenizer.decode(
                    tokens, skip_special_tokens=True, clean_up_tokenization_spaces=False,
                ).strip()
                reference = clip["reference"]
                metrics = score(decoded, reference)
                results[condition].append(metrics)
                predictions[condition] = decoded
                save_json(output / phase / clip["id"] / f"{condition}.json", {
                    "clip_id": clip["id"], "condition": condition, "model_input": visual_receipt,
                    "generated_token_ids": tokens, "decoded": decoded, "reference": reference,
                    "scores": metrics,
                })
            for condition in output_matches:
                output_matches[condition].append(words(predictions[condition]) == words(predictions["matched"]))
    summary = {
        condition: {
            "examples": len(rows),
            "normalized_exact_matches": sum(row["normalized_exact_match"] for row in rows),
            "mean_token_f1": sum(row["token_f1"] for row in rows) / len(rows),
        }
        for condition, rows in results.items()
    }
    for condition, matches in output_matches.items():
        summary[condition]["normalized_outputs_equal_to_matched"] = sum(matches)
    save_json(output / phase / "summary.json", summary)
    return summary


def completion_loss(torch, model, batch):
    labels = batch["labels"]
    if (len(labels.shape) != 2 or labels.shape[0] != 1 or labels.shape[1] < 2
            or labels.shape != batch["input_ids"].shape):
        raise ValueError("Completion loss requires one sequence with aligned input IDs and labels")
    if labels[0, 0].item() != -100:
        raise ValueError("The first token cannot be a completion target")

    # labels[0, 1:][i] is the token predicted by hidden state i.
    next_tokens = labels[0, 1:]
    predictors = (next_tokens != -100).nonzero(as_tuple=True)[0]
    if not predictors.numel():
        raise ValueError("Completion loss requires at least one supervised token")

    inputs = {name: value for name, value in batch.items() if name != "labels"}
    # Qwen indexes hidden states with logits_to_keep before applying lm_head.
    logits = model(**inputs, use_cache=False, logits_to_keep=predictors).logits[0]
    return torch.nn.functional.cross_entropy(logits.float(), next_tokens[predictors])


def train(torch, model, examples, output, steps):
    model.train()
    trainable = [parameter for parameter in model.parameters() if parameter.requires_grad]
    optimizer = torch.optim.AdamW(trainable, lr=LEARNING_RATE, weight_decay=0.0)
    rng = random.Random(SEED)
    order = list(examples)
    rng.shuffle(order)
    index = 0
    for step in range(1, steps + 1):
        losses, clips = [], []
        for _ in range(GRAD_ACCUMULATION):
            if index == len(order):
                rng.shuffle(order)
                index = 0
            clip_id = order[index]
            # Move tensors independently so the cached CPU BatchFeature remains on CPU.
            batch = {name: value.to("cuda") for name, value in examples[clip_id].items()}
            loss = completion_loss(torch, model, batch)
            value = loss.detach().item()
            if not math.isfinite(value):
                raise RuntimeError("Encountered nonfinite training loss")
            (loss / GRAD_ACCUMULATION).backward()
            losses.append(value)
            clips.append(clip_id)
            index += 1
        grad_norm = torch.nn.utils.clip_grad_norm_(trainable, 1.0, error_if_nonfinite=True).item()
        optimizer.step()
        optimizer.zero_grad(set_to_none=True)
        save_json(output / "steps" / f"{step:03d}.json", {
            "optimizer_steps": step, "clips": clips, "microbatch_losses": losses,
            "mean_loss": sum(losses) / len(losses), "gradient_norm": grad_norm,
        })
        if step == 1 or step % CHECKPOINT_INTERVAL == 0 or step == steps:
            checkpoint = output / f"checkpoint-{step:03d}"
            model.save_pretrained(checkpoint, safe_serialization=True, save_embedding_layers=False)
            torch.save(optimizer.state_dict(), checkpoint / "optimizer.pt")
            save_json(checkpoint / "receipt.json", {
                "optimizer_steps": step, "gradient_accumulation": GRAD_ACCUMULATION,
            })
            print(f"Optimizer step {step} checkpoint saved", flush=True)
    if not any(torch.count_nonzero(parameter).item() for name, parameter in model.named_parameters()
               if ".lora_B." in name and parameter.requires_grad):
        raise RuntimeError("LoRA B matrices did not change from zero initialization")


def run(args, output, status, profile=SPARSE):
    started = time.monotonic()
    status["stage"] = "admission"
    admission_bytes = (profile.root / "admission.json").read_bytes()
    admission = json.loads(admission_bytes)
    training, holdout = validate_admission(admission, profile)
    if profile.name == "dense":
        if admission.get("source_admission_sha256") != SPARSE_ADMISSION_SHA256:
            raise ValueError("Dense admission does not identify the pinned sparse cohort")
        sparse_bytes = (SPARSE.root / "admission.json").read_bytes()
        if hashlib.sha256(sparse_bytes).hexdigest() != SPARSE_ADMISSION_SHA256:
            raise ValueError("Original sparse admission bytes changed")
        sparse = json.loads(sparse_bytes)
        cohort = [{key: value for key, value in clip.items() if key not in {"frames", "immutable_video_url"}}
                  for clip in admission["clips"]]
        original_cohort = [{key: value for key, value in clip.items() if key != "frames"}
                           for clip in sparse["clips"]]
        if cohort != original_cohort or admission["model"] != sparse["model"] or admission["dataset"] != sparse["dataset"]:
            raise ValueError("Dense admission changed the sparse cohort, reference text or model/data pins")
    save_json(output / "dataset_receipt.json", {
        "admission_sha256": hashlib.sha256(admission_bytes).hexdigest(),
        "model": admission["model"], "dataset": admission["dataset"], "counts": admission["counts"],
        "training": [clip["id"] for clip in training],
        profile.evaluation_role: [clip["id"] for clip in holdout],
        "evaluation_role": profile.evaluation_role, "input_profile": profile.name,
        "source_admission_sha256": admission.get("source_admission_sha256"),
        "admitted_frames_per_clip": profile.admitted_frames,
        "encoded_images_per_clip": len(profile.matched),
        "frame_conditions": visual_conditions(profile), "instruction": INSTRUCTION, "seed": SEED,
        "batch_size": 1, "gradient_accumulation": GRAD_ACCUMULATION, "optimizer_steps_planned": args.steps,
        "learning_rate": LEARNING_RATE, "max_sequence_tokens": profile.max_sequence_tokens,
        "max_target_tokens_including_eos": MAX_TARGET_TOKENS, "max_new_tokens": MAX_NEW_TOKENS,
        "decode": "greedy", "blank": "all-zero RGB, same dimensions as matched frames",
    })
    status["stage"] = "runtime_imports"
    import torch
    from PIL import Image
    from peft import LoraConfig, get_peft_model
    from transformers import AutoProcessor, Qwen3_5ForConditionalGeneration

    status["stage"] = "hardware_validation"
    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise RuntimeError("One cloud CUDA GPU is required")
    prop = torch.cuda.get_device_properties(0)
    save_json(output / "runtime_receipt.json", {
        "python": sys.version.split()[0], "packages": {name: version(name) for name in DEPS},
        "device": prop.name, "total_gpu_bytes": prop.total_memory, "torch_cuda": torch.version.cuda,
        "dtype": "bfloat16", "attention_implementation": "sdpa",
        "transformers_architecture": Qwen3_5ForConditionalGeneration.__name__,
    })
    if not re.search(r"\bL4\b", prop.name) or not torch.cuda.is_bf16_supported():
        raise RuntimeError("A bf16-capable L4 GPU is required")
    torch.manual_seed(SEED)
    status["stage"] = "model_loading"
    revision = admission["model"]["revision"]
    processor = AutoProcessor.from_pretrained(MODEL_ID, revision=revision, token=False, trust_remote_code=False)
    model = Qwen3_5ForConditionalGeneration.from_pretrained(
        MODEL_ID, revision=revision, token=False, use_safetensors=True,
        dtype=torch.bfloat16, attn_implementation="sdpa",
    ).to("cuda")
    if model.config.model_type != "qwen3_5":
        raise RuntimeError("Loaded checkpoint is not the required Qwen 3.5 multimodal architecture")
    model.requires_grad_(False)
    print("Pinned multimodal checkpoint loaded", flush=True)

    status["stage"] = "training_input_validation"
    examples = {
        clip["id"]: training_example(torch, Image, processor, clip, output, profile)
        for clip in training
    }
    status["stage"] = "frozen_baseline"
    baseline = evaluate(torch, Image, processor, model, holdout, output, "baseline", profile)
    print("Frozen baseline evaluations saved", flush=True)

    status["stage"] = "lora_setup"
    targets = [name for name, module in model.named_modules()
               if name.startswith("model.language_model.")
               and name.rsplit(".", 1)[-1] in {"q_proj", "v_proj"}
               and isinstance(module, torch.nn.Linear)]
    if not targets or {name.rsplit(".", 1)[-1] for name in targets} != {"q_proj", "v_proj"}:
        raise RuntimeError("Language-model q_proj/v_proj targets were not found")
    model = get_peft_model(
        model, LoraConfig(r=8, lora_alpha=16, lora_dropout=0.0, target_modules=targets, bias="none"),
        revision=revision,
    )
    trainable = [(name, parameter.numel()) for name, parameter in model.named_parameters()
                 if parameter.requires_grad]
    if not trainable or any(".language_model." not in name or ".lora_" not in name for name, _ in trainable):
        raise RuntimeError("Unexpected trainable parameters")
    save_json(output / "lora_receipt.json", {
        "rank": 8, "alpha": 16, "dropout": 0.0, "targets": targets,
        "trainable_parameters": sum(size for _, size in trainable),
        "all_parameters": sum(parameter.numel() for parameter in model.parameters()),
        "loss_implementation": "Mean FP32 causal cross-entropy over target predictor positions",
    })

    status["stage"] = "training"
    train(torch, model, examples, output, args.steps)
    status["stage"] = "adapter_evaluation"
    adapted = evaluate(torch, Image, processor, model, holdout, output, "adapter", profile)
    status["stage"] = "completion"
    save_json(output / "success.json", {
        "status": "completed_pilot", "optimizer_steps": args.steps,
        "baseline": baseline, "adapter": adapted,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "peak_gpu_allocated_bytes": torch.cuda.max_memory_allocated(),
        "input_profile": profile.name, "evaluation_role": profile.evaluation_role,
        "scope": ("Previously inspected development controls for visual sensitivity"
                  if profile.name == "dense" else "Small held-out pilot, no translation-readiness claim"),
    })
    print("Adapter evaluations and pilot receipt saved", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="Unique artifact directory name")
    parser.add_argument("--steps", type=int, default=MAX_OPTIMIZER_STEPS)
    parser.add_argument("--profile", choices=PROFILES, default=SPARSE.name)
    args = parser.parse_args()
    profile = PROFILES[args.profile]
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", args.run):
        parser.error("run name must be a lowercase directory name")
    if not 1 <= args.steps <= MAX_OPTIMIZER_STEPS:
        parser.error("steps must be between 1 and 40")
    if sys.platform != "linux" or not profile.root.is_dir():
        parser.error("This script requires the mounted cloud pilot directory")
    output = profile.root / "train" / args.run
    output.mkdir(parents=True, exist_ok=False)
    status = {"stage": "startup"}
    try:
        run(args, output, status, profile)
    except Exception as error:
        details = "".join(traceback.format_exception(error))
        details = re.sub(r"\bhf_[A-Za-z0-9]+\b", "[redacted]", details)
        details = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[redacted]", details)
        save_json(output / "error.json", {
            "status": "failed", "stage": status["stage"],
            "error_type": type(error).__name__, "traceback": details,
        })
        print(f"Pilot failed at {status['stage']} ({type(error).__name__}); see private error.json",
              file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
