"""Test whether the paired Qwen q/v LoRA recipe can fit four admitted photos."""

import argparse
from itertools import islice
from pathlib import Path
import re
import sys
import traceback
import unicodedata

import fsboard_evaluate as fsboard
import paired_experiment as paired
import static_diagnostic as diagnostic
import train_qwen_pilot as pilot


LETTERS = "ABCL"
SOURCE_PATHS = {letter: f"{letter}/{letter}0007_test.jpg" for letter in LETTERS}
STEPS = 200
CHECKPOINT_STEPS = (0, 50, 100, STEPS)
SAMPLER_SEED = 78311
IMAGE_SIZE = (200, 200)
STATIC_WEIGHT = 1.0
TRAINABLE_PARAMETERS = 319488
SCOPE = "Visual learnability on four training photos only. ASL quality and generalization were not assessed."
SIGNAL = "Four exact photo predictions and four distinct normalized photo outputs versus one output for a black image."


def select_rows(samples):
    selected = []
    for label, source_path in SOURCE_PATHS.items():
        rows = [row for row in samples.values() if row.get("source_path") == source_path]
        if len(rows) != 1:
            raise ValueError(f"Expected one approved manifest row for {source_path}")
        row = rows[0]
        if (row["label"] != label or row["role"] != "training_eligible"
                or row.get("split", "train") != "train" or (row["width"], row["height"]) != IMAGE_SIZE):
            raise ValueError(f"Selected photo identity or admission differs from the protocol for {source_path}")
        selected.append(row)
    return selected


def build_schedule(ids):
    if len(ids) != len(LETTERS) or len(set(ids)) != len(LETTERS):
        raise ValueError("Exactly four distinct photo IDs are required")
    return list(islice(paired.shuffled_epochs(sorted(ids), SAMPLER_SEED), STEPS))


def load_initial_adapter(root, expected_digest, base_factory, load_lora):
    initial_path = fsboard.pair_path(root, "initialization")
    for name in ("adapter_config.json", "adapter_model.safetensors"):
        fsboard.pair_path(root, f"initialization/{name}")
    return paired.load_initialized_adapter(base_factory, load_lora, initial_path, expected_digest)


def load_inputs(args):
    experiment, training, development, _, digests = fsboard.load_pair(args.pair_root, args.dense_admission)
    requirements = Path(__file__).with_name("requirements-train-cloud.txt")
    shared_sources = {path.name: paired.sha256(path.read_bytes()) for path in (
        Path(paired.__file__), Path(pilot.__file__), requirements,
    )}
    if (experiment.get("source_sha256") != shared_sources or experiment.get("static_prompt") != paired.STATIC_PROMPT
            or experiment.get("planned_optimizer_steps_per_arm") != 80):
        raise ValueError("Completed pair differs from the pinned 80-step training recipe")
    manifest, manifest_sha = fsboard.read_json(args.static_manifest, experiment["static_manifest_sha256"])
    samples = paired.validate_static_manifest(manifest, args.static_manifest.parent, training, development)
    if len(samples) != 116:
        raise ValueError("Expected the completed pair's 116 approved static photos")
    rows = select_rows(samples)
    root = args.pair_root.resolve()
    success, _ = fsboard.read_json(fsboard.pair_path(root, "success.json"), digests["success.json"])
    initial, initial_sha = fsboard.read_json(fsboard.pair_path(root, "initialization/receipt.json"))
    if (not fsboard.digest_value(success.get("initialization_sha256"))
            or success["initialization_sha256"] != initial.get("adapter_sha256")
            or not fsboard.digest_value(initial.get("parameter_sha256"))
            or initial.get("trainable_parameters") != TRAINABLE_PARAMETERS):
        raise ValueError("Shared initialization receipt disagrees with the completed pair")
    for arm in ("A", "B"):
        arm_initial, _ = fsboard.read_json(fsboard.pair_path(root, f"{arm}/initialization.json"))
        if (arm_initial.get("shared_adapter_sha256") != initial["adapter_sha256"]
                or arm_initial.get("trainable_parameter_sha256") != initial["parameter_sha256"]):
            raise ValueError(f"Shared initialization receipt disagrees with arm {arm}")
    adapter_path = fsboard.pair_path(root, "initialization")
    config_path = fsboard.pair_path(root, "initialization/adapter_config.json")
    fsboard.pair_path(root, "initialization/adapter_model.safetensors")
    if paired.adapter_digest(adapter_path) != initial["adapter_sha256"]:
        raise ValueError("Shared initialization adapter digest disagrees with the completed pair")
    config, _ = fsboard.read_json(config_path)
    targets, config_targets = initial.get("targets"), config.get("target_modules")
    if (any(type(initial.get(receipt_key)) not in (int, float)
            or initial[receipt_key] != config.get(config_key)
            for receipt_key, config_key in (("rank", "r"), ("alpha", "lora_alpha"), ("dropout", "lora_dropout")))
            or not isinstance(targets, list) or not isinstance(config_targets, list)
            or not all(isinstance(target, str) for target in targets + config_targets)
            or sorted(targets) != sorted(config_targets)):
        raise ValueError("Shared initialization receipt LoRA settings disagree with the verified adapter")
    return experiment, rows, initial, {
        **digests, "static_manifest": manifest_sha, "dense_admission": experiment["dense_admission_sha256"],
        "initialization/receipt.json": initial_sha, "initialization/adapter": initial["adapter_sha256"],
    }, {
        **shared_sources, **{path.name: paired.sha256(path.read_bytes()) for path in (
            Path(__file__), Path(fsboard.__file__), Path(diagnostic.__file__),
        )},
    }


def update_step(torch, model, optimizer, trainable, example):
    batch = {name: value.to("cuda") for name, value in example.items()}
    loss = paired.backward_loss(pilot.completion_loss(torch, model, batch), STATIC_WEIGHT)
    norm = torch.nn.utils.clip_grad_norm_(trainable, paired.CLIP_NORM, error_if_nonfinite=True).item()
    optimizer.step()
    optimizer.zero_grad(set_to_none=True)
    return {"target_ce": loss, "gradient_norm_before_clip": norm}


def encode_prompt(processor, image):
    messages = [{"role": "user", "content": [
        {"type": "image", "image": image}, {"type": "text", "text": paired.STATIC_PROMPT},
    ]}]
    encoded = pilot.tokenize_messages(processor, messages, generation=True, profile=paired.STATIC_PROFILE)
    if encoded["input_ids"].shape[1] + diagnostic.MAX_NEW_TOKENS > paired.STATIC_PROFILE.max_sequence_tokens:
        raise ValueError("Static generation exceeds the sequence budget")
    return encoded


def predict(model, processor, encoded):
    max_tokens = diagnostic.MAX_NEW_TOKENS
    eos = model.generation_config.eos_token_id
    if eos is None:
        raise RuntimeError("The model lacks an EOS token")
    generated = model.generate(
        **{name: value.to("cuda") for name, value in encoded.items()},
        max_new_tokens=max_tokens, do_sample=False, num_beams=1, use_cache=True, return_dict_in_generate=False,
    )
    tokens = generated[0, encoded["input_ids"].shape[1]:].tolist()
    return {
        "decoded": processor.tokenizer.decode(tokens, skip_special_tokens=True, clean_up_tokenization_spaces=False),
        "generated_token_ids": tokens,
        "stop": paired.generation_status(tokens, eos, max_tokens),
    }


def evaluate(torch, model, processor, rows, photo_inputs, black_input):
    model.eval()
    predictions = []
    with torch.inference_mode():
        for row in rows:
            sample_id, label = row["id"], row["label"]
            result = predict(model, processor, photo_inputs[sample_id])
            predictions.append({
                **result, "sample_id": sample_id, "label": label,
                "exact": diagnostic.exact_letter(result["decoded"], label),
            })
        black = predict(model, processor, black_input)
    exact = sum(row["exact"] for row in predictions)
    distinct = len({unicodedata.normalize("NFKC", row["decoded"]).strip().upper() for row in predictions})
    return {
        "predictions": predictions, "exact_matches": exact, "distinct_photo_outputs": distinct, "black": black,
        "visual_learnability_signal": exact == distinct == len(LETTERS),
    }


def run(args, output, status):
    status["stage"] = "pair_verification"
    experiment, rows, initial, digests, source_sha = load_inputs(args)

    from PIL import Image
    import torch
    from peft import PeftModel
    from transformers import AutoProcessor, Qwen3_5ForConditionalGeneration

    status["stage"] = "runtime"
    paired.validate_runtime(torch, output)
    status["stage"] = "input_processing"
    model_id, revision = (experiment["model"][key] for key in ("id", "revision"))
    processor = AutoProcessor.from_pretrained(model_id, revision=revision, token=False, trust_remote_code=False)
    examples, photo_inputs = {}, {}
    for row in rows:
        image = paired.read_static_image(Image, args.static_manifest.parent, row)
        examples[row["id"]] = paired.static_training_example(torch, processor, image, row["label"])
        photo_inputs[row["id"]] = encode_prompt(processor, image)
    black_input = encode_prompt(processor, Image.new("RGB", IMAGE_SIZE, color=(0, 0, 0)))
    visual_sha = {name: pilot.tensor_digest(encoded["pixel_values"])
                  for name, encoded in {**photo_inputs, "black": black_input}.items()}
    if len(set(visual_sha.values())) != len(LETTERS) + 1:
        raise ValueError("Photos and black control must encode distinct visual inputs")
    schedule = build_schedule(list(examples))
    pilot.save_json(output / "probe.json", {
        "scope": SCOPE, "predeclared_signal": SIGNAL, "model": experiment["model"], "initialization": initial,
        "input_sha256": digests, "source_sha256": source_sha, "samples": rows, "pixel_values_sha256": visual_sha,
        "prompt": paired.STATIC_PROMPT, "schedule": schedule, "sampler_seed": SAMPLER_SEED, "model_seed": pilot.SEED,
        "optimizer_steps": STEPS, "checkpoint_steps": CHECKPOINT_STEPS, "optimizer": "AdamW",
        "learning_rate": pilot.LEARNING_RATE, "weight_decay": 0.0, "gradient_clip_norm": paired.CLIP_NORM,
        "static_microbatches_per_step": 1, "video_microbatches_per_step": 0, "objective_weight": STATIC_WEIGHT,
        "black_control_size": IMAGE_SIZE, "black_predictions_per_checkpoint": 1,
        "max_new_tokens": diagnostic.MAX_NEW_TOKENS, "do_sample": False, "num_beams": 1,
    })

    status["stage"] = "initialization_loading"

    def get_base():
        model = Qwen3_5ForConditionalGeneration.from_pretrained(
            model_id, revision=revision, token=False, use_safetensors=True,
            dtype=torch.bfloat16, attn_implementation="sdpa",
        ).to("cuda")
        if model.config.model_type != "qwen3_5":
            raise RuntimeError("Loaded model is not Qwen 3.5 multimodal")
        return model.requires_grad_(False)

    torch.manual_seed(pilot.SEED)
    model = load_initial_adapter(args.pair_root.resolve(), initial["adapter_sha256"], get_base,
                               lambda base, path: PeftModel.from_pretrained(base, str(path), is_trainable=True))
    trainable = [parameter for parameter in model.parameters() if parameter.requires_grad]
    initial_sha = paired.parameter_digest(model)
    if sum(parameter.numel() for parameter in trainable) != TRAINABLE_PARAMETERS or initial_sha != initial["parameter_sha256"]:
        raise ValueError("Loaded trainable parameters differ from the saved shared initialization")
    optimizer = torch.optim.AdamW(trainable, lr=pilot.LEARNING_RATE, weight_decay=0.0)
    evaluations = {}

    def evaluate_step(step):
        status.update(stage="evaluation", step=step)
        result = evaluate(torch, model, processor, rows, photo_inputs, black_input)
        pilot.save_json(output / "evaluations" / f"{step:03d}.json", {"optimizer_steps": step, **result})
        evaluations[step] = {key: value for key, value in result.items() if key != "predictions"}

    evaluate_step(0)
    torch.manual_seed(pilot.SEED)
    model.train()
    for step, sample_id in enumerate(schedule, 1):
        status.update(stage="training", step=step)
        receipt = update_step(torch, model, optimizer, trainable, examples[sample_id])
        pilot.save_json(output / "steps" / f"{step:03d}.json", {
            "optimizer_steps": step, "sample_id": sample_id, **receipt,
        })
        if step in CHECKPOINT_STEPS:
            status["stage"] = "checkpoint"
            checkpoint = output / f"checkpoint-{step:03d}"
            checkpoint.mkdir(exist_ok=False)
            model.save_pretrained(checkpoint, safe_serialization=True, save_embedding_layers=False)
            torch.save(optimizer.state_dict(), checkpoint / "optimizer.pt")
            pilot.save_json(checkpoint / "receipt.json", {
                "optimizer_steps": step, "parameter_sha256": paired.parameter_digest(model),
                "adapter_sha256": paired.adapter_digest(checkpoint),
                "optimizer_sha256": paired.sha256((checkpoint / "optimizer.pt").read_bytes()),
            })
            evaluate_step(step)
            model.train()

    status["stage"] = "completion"
    pilot.save_json(output / "success.json", {
        "status": "completed_static_learnability_probe", "scope": SCOPE, "predeclared_signal": SIGNAL,
        "examples": len(rows), "optimizer_steps": len(schedule), "evaluations": evaluations,
        "initial_parameter_sha256": initial_sha, "final_parameter_sha256": paired.parameter_digest(model),
        "peak_gpu_allocated_bytes": torch.cuda.max_memory_allocated(),
        "probe_sha256": paired.sha256((output / "probe.json").read_bytes()),
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("pair-root", "static-manifest", "dense-admission", "output"):
        parser.add_argument(f"--{flag}", type=Path, required=True)
    args = parser.parse_args()
    output = args.output
    if (sys.platform != "linux" or output.parent != paired.OUTPUT_ROOT or not paired.OUTPUT_ROOT.is_dir()
            or not re.fullmatch(r"static-learnability-[a-z0-9][a-z0-9_-]{0,45}", output.name)):
        parser.error("output must be a fresh /data/out/static-learnability-<name> directory on Linux")
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
        print(f"Static probe failed at {status['stage']} ({type(error).__name__}); see private error.json",
              file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())