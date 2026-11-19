"""Run the exploratory paired static-ASL auxiliary experiment on one cloud L4."""

import argparse
from collections import Counter
from dataclasses import asdict, dataclass, replace
import gc
import hashlib
from importlib.metadata import version
from io import BytesIO
from itertools import permutations
import json
import math
from pathlib import Path
import random
import re
import string
import sys
import time
import traceback
from types import SimpleNamespace
import unicodedata

import train_qwen_pilot as pilot


OUTPUT_ROOT = Path("/data/out")
ORIENTATION_POLICY = "validated-displaymatrix-right-angle-v1"
RIGHT_ANGLE_ROTATIONS = frozenset({-180, -90, 0, 90, 180})
STATIC_LETTERS = frozenset(string.ascii_uppercase) - {"J", "Z"}
STATIC_PROMPT = "Identify the static American Sign Language fingerspelling handshape. Reply with only the uppercase letter."
STATIC_PROFILE = SimpleNamespace(name="static", matched=(0,), max_sequence_tokens=1024)
STATIC_SEED = 54921
AUXILIARY_WEIGHT = 0.05
MAX_STEPS = 400
CLIP_NORM = 1.0
CONDITIONS = ("matched", "shuffled", "black", "wrong_video")
SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")
SHA256 = re.compile(r"[0-9a-f]{64}")
PATH_COMPONENT = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*")


def sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def read_pinned_json(path, expected_sha256):
    payload = path.read_bytes()
    if sha256(payload) != expected_sha256:
        raise ValueError(f"Admission digest mismatch for {path.name}")
    return json.loads(payload)


def load_dense_admission(path, expected_sha256):
    document = read_pinned_json(path, expected_sha256)
    if document.get("orientation_policy") != ORIENTATION_POLICY:
        raise ValueError("Dense admission lacks the required display orientation policy")
    profile = replace(pilot.DENSE, root=path.parent)
    training, development = pilot.validate_admission(document, profile)
    for clip in [*training, *development]:
        for frame in clip["frames"]:
            display = frame.get("display_rotation_degrees")
            applied = frame.get("applied_rotation_degrees_ccw")
            if (type(display) is not int or display not in RIGHT_ANGLE_ROTATIONS
                    or type(applied) is not int or applied != display):
                raise ValueError(f"Invalid display orientation for admitted frame {frame['path']}")
    return document, training, development, profile


def group_key(group):
    return unicodedata.normalize("NFKC", group).strip().casefold()


def sample_path(root, relative):
    if not isinstance(relative, str) or any(
        not PATH_COMPONENT.fullmatch(component) or component.endswith(".")
        for component in relative.split("/")
    ):
        raise ValueError("Static image path must be a safe relative path")
    if Path(relative).suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        raise ValueError("Static image path must identify a PNG or JPEG")
    root = root.resolve()
    path = root / relative
    if path.resolve() != path:
        raise ValueError("Static image path may not traverse symlinks")
    return path


def validate_static_manifest(document, root, training, development):
    if (type(document.get("schema_version")) is not int or document["schema_version"] != 1
            or document.get("role") != "training_eligible"
            or not isinstance(document.get("samples"), list)):
        raise ValueError("Static manifest must have schema version 1 and role training_eligible")
    development_groups = {group_key(clip["source_group"]) for clip in development}
    clips = [*training, *development]
    used_ids = {clip["id"].casefold() for clip in clips}
    used_paths = {clip["source_path"].casefold() for clip in clips}
    used_digests = {clip["sha256"] for clip in clips}
    admitted = {}
    for row in document["samples"]:
        if row.get("role") != "training_eligible" or row.get("split", "train") != "train":
            raise ValueError("Static samples must be training_eligible and in the train split")
        item_id, label, digest, relative = (row[key] for key in ("id", "label", "sha256", "path"))
        if not isinstance(item_id, str) or not SAFE_ID.fullmatch(item_id):
            raise ValueError("Invalid static sample ID")
        if not isinstance(label, str) or label not in STATIC_LETTERS:
            raise ValueError("Invalid static ASL letter")
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise ValueError("Invalid static sample digest")
        sample_path(root, relative)
        group = row.get("source_group")
        if not isinstance(group, str) or not group_key(group) or group_key(group) in development_groups:
            raise ValueError("Static source group is missing or overlaps the development split")
        if any(type(row.get(key)) is not int or not 1 <= row[key] <= 384 for key in ("width", "height")):
            raise ValueError("Manifest must declare bounded static image dimensions")
        if item_id.casefold() in used_ids or relative.casefold() in used_paths or digest in used_digests:
            raise ValueError("Duplicate sample identity, path or bytes")
        used_ids.add(item_id.casefold())
        used_paths.add(relative.casefold())
        used_digests.add(digest)
        admitted[item_id] = row
    if {row["label"] for row in admitted.values()} != STATIC_LETTERS:
        raise ValueError("Static admission must cover all 24 eligible letters")
    return admitted


def verified_sample_bytes(root, row):
    payload = sample_path(root, row["path"]).read_bytes()
    if sha256(payload) != row["sha256"]:
        raise ValueError(f"Static sample digest mismatch for {row['id']}")
    return payload


def read_static_image(image_module, root, row):
    expected_format = "PNG" if Path(row["path"]).suffix.lower() == ".png" else "JPEG"
    with image_module.open(BytesIO(verified_sample_bytes(root, row))) as image:
        if (image.format != expected_format or getattr(image, "n_frames", 1) != 1
                or image.size != (row["width"], row["height"])):
            raise ValueError(f"Static image format or dimensions differ from admission for {row['id']}")
        return image.convert("RGB")


@dataclass(frozen=True)
class Step:
    video_ids: tuple[str, ...]
    static_id: str | None = None


def shuffled_epochs(ids, seed):
    rng = random.Random(seed)
    order = list(ids)
    while True:
        rng.shuffle(order)
        yield from order


def paired_schedule(video_ids, static_ids, steps):
    if not 1 <= steps <= MAX_STEPS or not video_ids or not static_ids:
        raise ValueError(f"Provide both cohorts and between 1 and {MAX_STEPS} steps")
    video_stream = shuffled_epochs(video_ids, pilot.SEED)
    static_stream = shuffled_epochs(sorted(static_ids), STATIC_SEED)
    schedules = {"A": [], "B": []}
    for _ in range(steps):
        video = tuple(next(video_stream) for _ in range(pilot.GRAD_ACCUMULATION))
        schedules["A"].append(Step(video))
        schedules["B"].append(Step(video, next(static_stream)))
    return schedules


def adapter_digest(path):
    return sha256(json.dumps({
        name: sha256((path / name).read_bytes())
        for name in ("adapter_config.json", "adapter_model.safetensors")
    }, sort_keys=True).encode())


def load_initialized_adapter(base_factory, load_lora, path, expected_digest):
    if adapter_digest(path) != expected_digest:
        raise ValueError("Saved initialization changed before loading an arm")
    return load_lora(base_factory(), path)


def parameter_digest(model):
    digest = hashlib.sha256()
    for name, parameter in sorted(model.named_parameters()):
        if parameter.requires_grad:
            tensor = parameter.detach().cpu().float()
            digest.update(f"{name}:{tuple(tensor.shape)}:".encode("utf-8"))
            digest.update(bytes.fromhex(pilot.tensor_digest(tensor)))
    return digest.hexdigest()


def backward_loss(loss, weight):
    value = loss.detach().item()
    if not math.isfinite(value):
        raise RuntimeError("Encountered nonfinite training loss")
    (loss * weight).backward()
    return value


def update_step(optimizer, video_ids, static_id, loss_for, clip_grad, sync=lambda: None):
    started = time.monotonic()
    losses = [backward_loss(loss_for("video", item), 1.0 / len(video_ids)) for item in video_ids]
    video_loss = sum(losses) / len(losses)
    static_loss, static_seconds = None, 0.0
    if static_id is not None:
        sync()
        started_static = time.monotonic()
        static_loss = backward_loss(loss_for("static", static_id), AUXILIARY_WEIGHT)
        sync()
        static_seconds = time.monotonic() - started_static
    gradient_norm = clip_grad()
    optimizer.step()
    optimizer.zero_grad(set_to_none=True)
    sync()
    return {
        "video_microbatch_losses": losses,
        "video_loss": video_loss,
        "static_loss": static_loss,
        "objective": video_loss + AUXILIARY_WEIGHT * (static_loss or 0.0),
        "gradient_norm_before_clip": gradient_norm,
        "gradient_clip_factor": min(1.0, CLIP_NORM / (gradient_norm + 1e-6)),
        "optimizer_step_seconds": time.monotonic() - started,
        "static_forward_backward_seconds": static_seconds,
    }


def static_training_example(torch, processor, image, label):
    messages = [{"role": "user", "content": [
        {"type": "image", "image": image},
        {"type": "text", "text": STATIC_PROMPT},
    ]}]
    prefix = pilot.tokenize_messages(processor, messages, generation=True, profile=STATIC_PROFILE)
    full = pilot.tokenize_messages(processor, messages + [
        {"role": "assistant", "content": label},
    ], generation=False, profile=STATIC_PROFILE)
    if not torch.equal(prefix["pixel_values"], full["pixel_values"]):
        raise ValueError("Static teacher forcing changed the visual input")
    labels = pilot.completion_labels(
        prefix["input_ids"][0].tolist(), full["input_ids"][0].tolist(), processor.tokenizer.eos_token_id,
    )
    target = [token for token in labels if token != -100]
    if (len(target) > pilot.MAX_TARGET_TOKENS
            or processor.tokenizer.decode(target[:-1], clean_up_tokenization_spaces=False) != label):
        raise ValueError("Static completion tokens do not reproduce the admitted letter")
    full["labels"] = torch.tensor([labels], dtype=torch.long)
    return full


def train_arm(torch, model, video_examples, static_examples, schedule, output, initial_parameter_sha256):
    model.train()
    trainable = [parameter for parameter in model.parameters() if parameter.requires_grad]
    optimizer = torch.optim.AdamW(trainable, lr=pilot.LEARNING_RATE, weight_decay=0.0)
    totals = Counter()
    started = time.monotonic()

    def loss_for(task, item):
        examples = static_examples if task == "static" else video_examples
        batch = {name: value.to("cuda") for name, value in examples[item].items()}
        return pilot.completion_loss(torch, model, batch)

    def clip_grad():
        return torch.nn.utils.clip_grad_norm_(trainable, CLIP_NORM, error_if_nonfinite=True).item()

    for step_number, step in enumerate(schedule, 1):
        receipt = update_step(
            optimizer, step.video_ids, step.static_id, loss_for, clip_grad, torch.cuda.synchronize,
        )
        totals.update({key: receipt[key] for key in (
            "optimizer_step_seconds", "static_forward_backward_seconds",
        )})
        totals["video_microbatches"] += len(step.video_ids)
        totals["static_microbatches"] += step.static_id is not None
        totals["clipped_steps"] += receipt["gradient_clip_factor"] < 1.0
        pilot.save_json(output / "steps" / f"{step_number:03d}.json", {
            "optimizer_steps": step_number, **asdict(step), **receipt,
        })
        if step_number % pilot.CHECKPOINT_INTERVAL == 0 or step_number == len(schedule):
            checkpoint = output / f"checkpoint-{step_number:03d}"
            model.save_pretrained(checkpoint, safe_serialization=True, save_embedding_layers=False)
            torch.save(optimizer.state_dict(), checkpoint / "optimizer.pt")
            pilot.save_json(checkpoint / "receipt.json", {
                "optimizer_steps": step_number, "adapter_sha256": adapter_digest(checkpoint),
                "parameter_sha256": parameter_digest(model),
                "optimizer_sha256": sha256((checkpoint / "optimizer.pt").read_bytes()),
            })
            print(f"{output.name}: saved optimizer step {step_number}", flush=True)

    updated_digest = parameter_digest(model)
    if updated_digest == initial_parameter_sha256:
        raise RuntimeError("Trainable LoRA parameters did not change")
    return {
        **totals, "optimizer_steps": len(schedule), "parameter_sha256": updated_digest,
        "train_wall_seconds": time.monotonic() - started,
    }


def wrong_video_donors(clips):
    for candidates in permutations(clips):
        if all(
            target["id"] != donor["id"]
            and group_key(target["source_group"]) != group_key(donor["source_group"])
            and pilot.words(target["reference"]) != pilot.words(donor["reference"])
            for target, donor in zip(clips, candidates)
        ):
            return {target["id"]: donor for target, donor in zip(clips, candidates)}
    raise ValueError("Development controls lack unrelated wrong-video donors")


def wrong_video_input(image_module, processor, donor, profile):
    frames = [donor["frames"][index] for index in profile.matched]
    images = [pilot.frame_image(image_module, frame["path"], profile) for frame in frames]
    encoded = pilot.tokenize_messages(processor, pilot.inference_messages(images), True, profile)
    return encoded, {
        "donor_clip_id": donor["id"], "donor_reference": donor["reference"],
        "donor_source_group": donor["source_group"], "frame_indices": profile.matched,
        "frames": frames, "rgb_sizes": [image.size for image in images],
        "ordered_rgb_sha256": [sha256(image.tobytes()) for image in images],
        "pixel_values_sha256": pilot.tensor_digest(encoded["pixel_values"]),
        "image_grid_thw": encoded["image_grid_thw"].tolist(),
        "prompt_tokens": encoded["input_ids"].shape[1],
    }


def generation_status(tokens, eos_token_ids, max_tokens):
    eos = eos_token_ids if isinstance(eos_token_ids, (tuple, list)) else (eos_token_ids,)
    if tokens and tokens[-1] in eos:
        return "eos"
    if len(tokens) == max_tokens:
        return "max_tokens"
    raise RuntimeError("Unexpected generation stop without EOS or the token cap")


def evaluate_development(torch, image_module, processor, model, clips, donors, output, profile):
    model.eval()
    results = {condition: [] for condition in CONDITIONS}
    eos_token_ids = model.generation_config.eos_token_id
    with torch.inference_mode():
        for clip in clips:
            inputs = pilot.condition_inputs(image_module, processor, clip, profile)
            inputs["black"] = inputs.pop("blank")
            inputs["wrong_video"] = wrong_video_input(image_module, processor, donors[clip["id"]], profile)
            if len({details["pixel_values_sha256"] for _, details in inputs.values()}) != len(CONDITIONS):
                raise ValueError("Two development controls encode indistinguishable visual inputs")
            matched_words = None
            for condition in CONDITIONS:
                encoded, visual_receipt = inputs[condition]
                prompt_length = encoded["input_ids"].shape[1]
                generated = model.generate(
                    **{name: tensor.to("cuda") for name, tensor in encoded.items()},
                    max_new_tokens=pilot.MAX_NEW_TOKENS, do_sample=False, use_cache=True,
                    return_dict_in_generate=False,
                )
                tokens = generated[0, prompt_length:].tolist()
                stop = generation_status(tokens, eos_token_ids, pilot.MAX_NEW_TOKENS)
                decoded = processor.tokenizer.decode(
                    tokens, skip_special_tokens=True, clean_up_tokenization_spaces=False,
                ).strip()
                decoded_words = pilot.words(decoded)
                if condition == "matched":
                    matched_words = decoded_words
                scores = pilot.score(decoded, clip["reference"])
                matches = decoded_words == matched_words
                results[condition].append((scores, matches, stop))
                pilot.save_json(output / clip["id"] / f"{condition}.json", {
                    "clip_id": clip["id"], "condition": condition, "model_input": visual_receipt,
                    "generated_token_ids": tokens, "stop": stop, "generated_tokens": len(tokens),
                    "decoded": decoded, "reference": clip["reference"], "scores": scores,
                })
    summary = {
        condition: {
            "examples": len(rows),
            "normalized_exact_matches": sum(score["normalized_exact_match"] for score, _, _ in rows),
            "mean_token_f1": sum(score["token_f1"] for score, _, _ in rows) / len(rows),
            "normalized_outputs_equal_to_matched": sum(matches for _, matches, _ in rows),
            "stop_counts": {stop: sum(actual == stop for _, _, actual in rows)
                            for stop in ("eos", "max_tokens")},
        }
        for condition, rows in results.items()
    }
    pilot.save_json(output / "summary.json", summary)
    return summary


def validate_runtime(torch, output):
    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise RuntimeError("One cloud CUDA GPU is required")
    device = torch.cuda.get_device_properties(0)
    packages = {name: version(name) for name in pilot.DEPS}
    requirements = Path(__file__).with_name("requirements-train-cloud.txt")
    pinned = dict(line.strip().split("==", 1) for line in requirements.read_text().splitlines()
                  if line.strip() and not line.lstrip().startswith("#"))
    pinned.update({"torch": "2.8.0+cu126", "torchvision": "0.23.0+cu126"})
    pilot.save_json(output / "runtime_receipt.json", {
        "python": sys.version.split()[0], "packages": packages,
        "device": device.name, "total_gpu_bytes": device.total_memory, "torch_cuda": torch.version.cuda,
        "dtype": "bfloat16", "attention_implementation": "sdpa",
    })
    if packages != pinned or torch.version.cuda != "12.6":
        raise RuntimeError("Cloud libraries do not match the pinned CUDA 12.6 training runtime")
    if device.name not in {"NVIDIA L4", "NVIDIA L40S"} or not torch.cuda.is_bf16_supported():
        raise RuntimeError("A bf16-capable NVIDIA L4 or NVIDIA L40S GPU is required")


def create_initial_adapter(torch, base, lora_config, wrap, revision, path):
    targets = [name for name, module in base.named_modules()
               if name.startswith("model.language_model.")
               and name.rsplit(".", 1)[-1] in {"q_proj", "v_proj"}
               and isinstance(module, torch.nn.Linear)]
    if {name.rsplit(".", 1)[-1] for name in targets} != {"q_proj", "v_proj"}:
        raise RuntimeError("Language-model q_proj/v_proj targets were not found")
    adapter = wrap(base, lora_config(
        r=8, lora_alpha=16, lora_dropout=0.0, target_modules=targets, bias="none",
    ), revision=revision)
    trainable = [(name, parameter.numel()) for name, parameter in adapter.named_parameters()
                 if parameter.requires_grad]
    if not trainable or any(".language_model." not in name or ".lora_" not in name for name, _ in trainable):
        raise RuntimeError("Unexpected trainable parameters")
    adapter.save_pretrained(path, safe_serialization=True, save_embedding_layers=False)
    return {
        "adapter_sha256": adapter_digest(path), "parameter_sha256": parameter_digest(adapter),
        "rank": 8, "alpha": 16, "dropout": 0.0, "targets": targets,
        "trainable_parameters": sum(size for _, size in trainable),
        "loss": "Mean FP32 causal cross-entropy over supervised predictor positions",
    }


def run(args, output, status):
    started = time.monotonic()
    status["stage"] = "admission"
    dense, training, development, profile = load_dense_admission(
        args.dense_admission, args.dense_admission_sha256,
    )
    donors = wrong_video_donors(development)
    manifest = read_pinned_json(args.static_manifest, args.static_manifest_sha256)
    static_root = args.static_manifest.parent
    samples = validate_static_manifest(manifest, static_root, training, development)
    schedules = paired_schedule([clip["id"] for clip in training], samples, args.steps)
    schedule_path = output / "schedule.json"
    pilot.save_json(schedule_path, {arm: [asdict(step) for step in steps] for arm, steps in schedules.items()})
    pilot.save_json(output / "experiment.json", {
        "model": dense["model"], "dataset": dense["dataset"],
        "dense_admission_sha256": args.dense_admission_sha256,
        "static_manifest_sha256": args.static_manifest_sha256,
        "schedule_sha256": sha256(schedule_path.read_bytes()),
        "source_sha256": {path.name: sha256(path.read_bytes()) for path in (
            Path(__file__), Path(pilot.__file__), Path(__file__).with_name("requirements-train-cloud.txt"),
        )},
        "training_clips": [clip["id"] for clip in training],
        "development_controls": [clip["id"] for clip in development],
        "wrong_video_donors": {clip_id: donor["id"] for clip_id, donor in donors.items()},
        "static_samples_by_letter": Counter(row["label"] for row in samples.values()),
        "static_prompt": STATIC_PROMPT, "video_prompt": pilot.INSTRUCTION,
        "evaluation_role": profile.evaluation_role, "evaluation_conditions": CONDITIONS,
        "seed": pilot.SEED, "static_sampler_seed": STATIC_SEED, "learning_rate": pilot.LEARNING_RATE,
        "video_microbatches_per_step": pilot.GRAD_ACCUMULATION, "auxiliary_weight": AUXILIARY_WEIGHT,
        "clip_norm": CLIP_NORM, "planned_optimizer_steps_per_arm": args.steps,
        "scope": "Exploratory comparison on four reused development clips. No independent fingerspelling assessment.",
    })
    status["stage"] = "runtime"
    import torch
    from PIL import Image
    from peft import LoraConfig, PeftModel, get_peft_model
    from transformers import AutoProcessor, Qwen3_5ForConditionalGeneration

    validate_runtime(torch, output)
    revision = dense["model"]["revision"]

    def get_base():
        model = Qwen3_5ForConditionalGeneration.from_pretrained(
            pilot.MODEL_ID, revision=revision, token=False, use_safetensors=True,
            dtype=torch.bfloat16, attn_implementation="sdpa",
        ).to("cuda")
        if model.config.model_type != "qwen3_5":
            raise RuntimeError("Loaded model is not Qwen 3.5 multimodal")
        return model.requires_grad_(False)

    status["stage"] = "input_processing"
    processor = AutoProcessor.from_pretrained(
        pilot.MODEL_ID, revision=revision, token=False, trust_remote_code=False,
    )
    video = {
        clip["id"]: pilot.training_example(torch, Image, processor, clip, output, profile)
        for clip in training
    }
    sampled = {step.static_id for step in schedules["B"]}
    static = {}
    for item_id, row in samples.items():
        image = read_static_image(Image, static_root, row)
        if item_id in sampled:
            example = static_training_example(torch, processor, image, row["label"])
            static[item_id] = example
            pilot.save_json(output / "static_training_inputs" / f"{item_id}.json", {
                "admission": row, "pixel_values_sha256": pilot.tensor_digest(example["pixel_values"]),
                "sequence_tokens": example["input_ids"].shape[1],
                "supervised_tokens_including_eos": (example["labels"] != -100).sum().item(),
            })

    status["stage"] = "shared_initialization"
    torch.manual_seed(pilot.SEED)
    initial_path = output / "initialization"
    initialization = create_initial_adapter(torch, get_base(), LoraConfig, get_peft_model, revision, initial_path)
    pilot.save_json(initial_path / "receipt.json", initialization)
    gc.collect()
    torch.cuda.empty_cache()

    summaries = {}
    for arm, schedule in schedules.items():
        status["arm"] = arm
        status["stage"] = "arm_initialization"
        arm_path = output / arm
        arm_path.mkdir(exist_ok=False)
        torch.manual_seed(pilot.SEED)
        torch.cuda.reset_peak_memory_stats()
        model = load_initialized_adapter(get_base, lambda base, path: PeftModel.from_pretrained(
            base, str(path), is_trainable=True,
        ), initial_path, initialization["adapter_sha256"])
        parameter_sha256 = parameter_digest(model)
        if parameter_sha256 != initialization["parameter_sha256"]:
            raise RuntimeError("Trainable parameters differ from the saved shared initialization")
        pilot.save_json(arm_path / "initialization.json", {
            "shared_adapter_sha256": initialization["adapter_sha256"],
            "trainable_parameter_sha256": parameter_sha256,
        })

        status["stage"] = "training"
        torch.manual_seed(pilot.SEED)
        summary = train_arm(torch, model, video, static if arm == "B" else {}, schedule, arm_path, parameter_sha256)
        adapter_path = arm_path / "adapter"
        model.save_pretrained(adapter_path, safe_serialization=True, save_embedding_layers=False)
        summary["adapter_sha256"] = adapter_digest(adapter_path)

        status["stage"] = "development_evaluation"
        summary["development"] = evaluate_development(
            torch, Image, processor, model, development, donors, arm_path / "development", profile,
        )
        summary["peak_gpu_allocated_bytes"] = torch.cuda.max_memory_allocated()
        pilot.save_json(arm_path / "summary.json", summary)
        summaries[arm] = summary
        del model
        gc.collect()
        torch.cuda.empty_cache()
    status["stage"] = "completion"
    status.pop("arm")
    pilot.save_json(output / "success.json", {
        "status": "completed_exploratory_pair", "evaluation_role": profile.evaluation_role,
        "initialization_sha256": initialization["adapter_sha256"], "arms": summaries,
        "elapsed_seconds": time.monotonic() - started,
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="Unique directory under /data/out")
    parser.add_argument("--dense-admission", required=True, type=Path,
                        help="Orientation-validated dense manifest beside its frame tree")
    parser.add_argument("--dense-admission-sha256", required=True)
    parser.add_argument("--static-manifest", required=True, type=Path)
    parser.add_argument("--static-manifest-sha256", required=True)
    parser.add_argument("--steps", type=int, default=pilot.MAX_OPTIMIZER_STEPS)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", args.run):
        parser.error("run must be a lowercase directory name")
    if not SHA256.fullmatch(args.dense_admission_sha256):
        parser.error("dense admission digest must be a lowercase SHA-256")
    if not SHA256.fullmatch(args.static_manifest_sha256):
        parser.error("static manifest digest must be a lowercase SHA-256")
    if not 1 <= args.steps <= MAX_STEPS:
        parser.error(f"steps must be between 1 and {MAX_STEPS}")
    if sys.platform != "linux" or not OUTPUT_ROOT.is_dir():
        parser.error("The cloud job must mount output at /data/out on Linux")
    if not args.dense_admission.is_file():
        parser.error("The selected dense admission must be a readable file")
    output = OUTPUT_ROOT / args.run
    try:
        output.mkdir(exist_ok=False)
    except FileExistsError:
        parser.error("run already exists; supply a fresh name")
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
        print(f"Paired experiment failed at {status['stage']} ({type(error).__name__}); see private error.json",
              file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

