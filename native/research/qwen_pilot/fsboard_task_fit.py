"""Check whether the frozen FSboard training graph can fit six admitted training clips."""

import argparse
from functools import partial
import gc
from hashlib import sha256
from itertools import cycle, islice
import json
from math import isfinite
from pathlib import Path
import signal
import sys
from tempfile import TemporaryDirectory
import time
import traceback

import fsboard_task_cloud_training as cloud
import fsboard_task_inputs as gate
import fsboard_task_protocol as protocol
import fsboard_task_training as training
import fsboard_evaluate as scorer
import paired_experiment as paired
import train_qwen_pilot as pilot


FIT_ID = "sts-fsboard-v4-six-clip-fit-smoke-20261119-v1"
FIT_SOURCE = Path(__file__).name
RUNTIME_SOURCES = cloud.RUNTIME_SOURCES | {FIT_SOURCE}
FIT_POLICY = {
    "schema_version": 1,
    "fit_id": FIT_ID,
    "parent_experiment_id": protocol.EXPERIMENT_ID,
    "scope": "engineering_training_fit_only",
    "selection": "first_six_distinct_literal_references_in_frozen_training_order",
    "training_examples": 6,
    "shared_adapter_initialization_seed": 17631,
    "video_microbatches_per_update": 2,
    "static_microbatches_per_update": 0,
    "schedule": "cycle_selected_clips_in_frozen_training_order",
    "checkpoints": [0, 50, 100],
    "generation": {"max_new_tokens": 32, "do_sample": False, "use_cache": True},
    "teacher_nll": "arithmetic_mean_of_per_clip_eos_inclusive_token_mean_completion_loss",
    "minimum_exact_matches": 6,
    "minimum_relative_teacher_nll_reduction": 0.25,
    "early_stop_on_first_passing_trained_checkpoint": True,
    "fit_adapter_reusable": False,
}


def verify_source(path, expected):
    hashes = cloud.verify_source(path, expected)
    payload = gate.read_bounded(path, gate.ADMISSION_LIMIT)
    if sha256(payload).hexdigest() != expected:
        raise cloud.RunError("Source manifest changed before fit source verification")
    rows = [row for row in json.loads(payload) if row["file"] == FIT_SOURCE]
    source = Path(__file__)
    if len(rows) != 1 or source.is_symlink() or not source.is_file():
        raise cloud.RunError("Source manifest must bind the regular fit entry point exactly once")
    data = source.read_bytes()
    digest = sha256(data).hexdigest()
    if rows[0].get("sha256") != digest or rows[0].get("bytes") != len(data):
        raise cloud.RunError("Source digest or size mismatch for fit entry point")
    return {**hashes, FIT_SOURCE: digest}


def verify_fit_protocol(path, expected):
    payload = gate.read_bounded(path, gate.ADMISSION_LIMIT)
    if sha256(payload).hexdigest() != expected:
        raise cloud.RunError("Fit protocol SHA-256 mismatch")
    if json.dumps(gate.read_json(payload), sort_keys=True) != json.dumps(FIT_POLICY, sort_keys=True):
        raise cloud.RunError("Fit protocol differs from the implemented smoke policy")


def select_training_clips(clips):
    selected, references = [], set()
    for clip in clips:
        if clip["role"] != "training" or not clip["id"].startswith("training-"):
            raise cloud.RunError("Only admitted training clips may enter the fit smoke")
        reference = clip["reference"]
        if reference in references:
            continue
        selected.append(clip)
        references.add(reference)
        if len(selected) == FIT_POLICY["training_examples"]:
            return tuple(selected)
    raise cloud.RunError("The training manifest has fewer than six distinct literal references")


def encode_checkpoint_clip(verified, attested, torch, processor, clip):
    limits = verified.frozen_protocol["input"]
    frame_bounds = {
        "min_frames": limits["min_ordered_rgb_stills"],
        "max_frames": limits["max_ordered_rgb_stills"],
    }
    images = gate.load_frames(verified.root, clip, **frame_bounds)
    try:
        prefix, teacher, receipt = gate.encode_example(
            processor, images, clip["reference"], context=limits["dense_max_sequence_tokens"],
            generation_cap=FIT_POLICY["generation"]["max_new_tokens"], **frame_bounds,
        )
        if receipt != attested[clip["id"]]:
            raise cloud.RunError("Fit checkpoint input differs from the attested processor encoding")
        return (
            {key: torch.from_numpy(array) for key, array in prefix.items()},
            {key: torch.from_numpy(array) for key, array in teacher.items()},
            receipt,
        )
    finally:
        for image in images:
            image.close()


def assess(torch, processor, model, clips, encode, update, digest):
    model.eval()
    generation = FIT_POLICY["generation"]
    eos = model.generation_config.eos_token_id
    rows = []
    with torch.inference_mode():
        for clip in clips:
            prefix, teacher, encoding = encode(clip)
            if prefix.keys() != gate.MODEL_FIELDS or teacher.keys() != gate.MODEL_FIELDS | {"labels"}:
                raise cloud.RunError("Unexpected fields in fit inference or teacher-forcing inputs")
            prompt_length = prefix["input_ids"].shape[1]
            generated = model.generate(
                **{key: tensor.to("cuda") for key, tensor in prefix.items()},
                **generation, return_dict_in_generate=False,
            )
            tokens = generated[0, prompt_length:].tolist()
            if len(tokens) > generation["max_new_tokens"]:
                raise cloud.RunError("Fit generation exceeded its token cap")
            stop = paired.generation_status(tokens, eos, generation["max_new_tokens"])
            decoded = processor.tokenizer.decode(
                tokens, skip_special_tokens=True, clean_up_tokenization_spaces=False,
            ).strip()
            loss = pilot.completion_loss(
                torch, model, {key: tensor.to("cuda") for key, tensor in teacher.items()},
            ).item()
            count = encoding["supervised_tokens_including_eos"]
            if not isfinite(loss) or loss < 0:
                raise cloud.RunError("Invalid or nonfinite fit checkpoint teacher NLL")
            rows.append({
                "clip_id": clip["id"], "decoded": decoded, "reference": clip["reference"],
                "generated_token_ids": tokens, "generated_tokens": len(tokens), "stop": stop,
                "scores": scorer.score(decoded, clip["reference"]),
                "output_equals_matched": True, "model_input": encoding,
                "reference_nll": {"mean_per_token": loss, "tokens_including_eos": count, "total": loss * count},
            })
            del prefix, teacher, generated
    return {
        "optimizer_updates": update, "parameter_sha256": digest, "examples": rows,
        "aggregate": scorer.aggregate(rows),
        "mean_matched_teacher_nll": sum(row["reference_nll"]["mean_per_token"] for row in rows) / len(rows),
    }


def run_updates(torch, model, video, ids, assess_checkpoint, initial_digest, output, progress):
    checkpoints = []

    def checkpoint(step, digest):
        progress["stage"] = "fit_checkpoint"
        result = assess_checkpoint(step, digest)
        nll = result["mean_matched_teacher_nll"]
        if not isfinite(nll) or nll < 0 or (step == 0 and nll == 0):
            raise cloud.RunError("Fit checkpoint cannot establish a finite relative NLL reduction")
        reduction = None if step == 0 else 1 - nll / checkpoints[0]["mean_matched_teacher_nll"]
        result.update(
            relative_teacher_nll_reduction=reduction,
            fit_guard_passed=step != 0 and (
                result["aggregate"]["exact_matches"] == FIT_POLICY["minimum_exact_matches"]
                and reduction >= FIT_POLICY["minimum_relative_teacher_nll_reduction"]
            ),
        )
        cloud.publish_receipt(output / f"checkpoint-{step:03d}.json", result)
        checkpoints.append(result)
        progress["completed_checkpoints"].append(step)
        return result["fit_guard_passed"]

    checkpoint(0, initial_digest)
    trainable = [parameter for parameter in model.parameters() if parameter.requires_grad]
    optimizer = torch.optim.AdamW(trainable, lr=pilot.LEARNING_RATE, weight_decay=0.0)

    def loss_for(task, item):
        if task != "video" or item not in ids:
            raise cloud.RunError("Unexpected microbatch requested for the fit smoke")
        return pilot.completion_loss(torch, model, {
            key: value.to("cuda") for key, value in video[item].items()
        })

    def clip_grad():
        return torch.nn.utils.clip_grad_norm_(trainable, paired.CLIP_NORM, error_if_nonfinite=True).item()

    _, first, last = FIT_POLICY["checkpoints"]
    stream = cycle(ids)
    model.train()
    progress["stage"] = "training"
    previous_digest = initial_digest
    for step in range(1, last + 1):
        batch = tuple(islice(stream, FIT_POLICY["video_microbatches_per_update"]))
        progress["training_started"] = True
        receipt = paired.update_step(optimizer, batch, None, loss_for, clip_grad, torch.cuda.synchronize)
        if not all(isfinite(receipt[key]) for key in ("video_loss", "gradient_norm_before_clip")):
            raise cloud.RunError("Encountered a nonfinite fit update")
        progress["optimizer_updates"] = step
        if step % 5 == 0:
            print(f"Fit smoke finished optimizer update {step}", flush=True)
        if step in (first, last):
            digest = paired.parameter_digest(model)
            if digest in (initial_digest, previous_digest):
                raise cloud.RunError("Trainable LoRA parameters did not change between fit checkpoints")
            if checkpoint(step, digest):
                break
            previous_digest = digest
            model.train()
            progress["stage"] = "training"
    return checkpoints


def run_fit(args, progress):
    started = time.monotonic()
    progress["stage"] = "source"
    sources = verify_source(args.source_manifest, args.source_manifest_sha256)
    progress["stage"] = "fit_protocol"
    verify_fit_protocol(args.fit_protocol, args.fit_protocol_sha256)
    progress["stage"] = "admission"
    verified = gate.load_admission(
        admission=args.admission, admission_sha256=args.admission_sha256,
        proposal=args.proposal, proposal_sha256=args.proposal_sha256,
        protocol_sha256=args.protocol_sha256, previous_receipt=args.previous_receipt,
    )
    progress["stage"] = "training_inputs"
    prepared = training.prepare_training(
        verified, processor_receipt=args.processor_receipt, processor_receipt_sha256=args.processor_receipt_sha256,
        prior_schedule=args.prior_schedule, static_manifest=args.static_manifest,
    )
    clips = select_training_clips(verified.clips["training"])
    ids = tuple(clip["id"] for clip in clips)
    model_spec = verified.frozen_protocol["model"]
    model_id, revision = model_spec["id"], model_spec["revision"]
    settings = verified.frozen_protocol["training"]
    seed = settings["shared_adapter_initialization_seed"]
    if (
        seed != FIT_POLICY["shared_adapter_initialization_seed"]
        or settings["matching_video_microbatches_per_update"] != FIT_POLICY["video_microbatches_per_update"]
        or verified.frozen_protocol["evaluation"]["max_new_tokens"] != FIT_POLICY["generation"]["max_new_tokens"]
    ):
        raise cloud.RunError("Fit smoke and frozen parent protocol disagree")
    pins = {
        "fit_protocol_sha256": args.fit_protocol_sha256,
        "source_manifest_sha256": args.source_manifest_sha256,
        "admission_sha256": args.admission_sha256,
        "proposal_sha256": args.proposal_sha256,
        "protocol_sha256": args.protocol_sha256,
        "previous_receipt_sha256": verified.summary["previous_receipt_sha256"],
        "processor_receipt_sha256": args.processor_receipt_sha256,
        "prior_schedule_sha256": protocol.PREVIOUS_SCHEDULE_SHA256,
        "static_manifest_sha256": protocol.STATIC_MANIFEST_SHA256,
        "scorer_sha256": protocol.SCORER_SHA256,
    }

    progress["stage"] = "model_path"
    cloud.check_model_path(model_id)
    cloud.publish_receipt(args.output / "experiment.json", {
        "schema_version": 1, "fit_id": FIT_ID, "policy": FIT_POLICY, "model": model_spec, "pins": pins,
        "source_sha256": sources, "training_clip_ids": ids, "processor_token_contract_verified": True,
    })
    progress["stage"] = "runtime"
    torch, processor_type, model_type, lora_config, wrap, peft_model = cloud.load_runtime()
    paired.validate_runtime(torch, args.output)

    def get_base():
        cloud.check_model_path(model_id)
        model = model_type.from_pretrained(
            model_id, revision=revision, token=False, use_safetensors=True,
            dtype=torch.bfloat16, attn_implementation="sdpa",
        ).to("cuda")
        if model.config.model_type != "qwen3_5":
            raise cloud.RunError("Loaded model is not Qwen3.5 multimodal")
        return model.requires_grad_(False)

    progress["stage"] = "processor"
    processor = processor_type.from_pretrained(model_id, revision=revision, token=False, trust_remote_code=False)
    video, static, _ = prepared.arm_inputs("A", torch=torch, processor=processor)
    if static or any(item not in video for item in ids):
        raise cloud.RunError("Fit smoke requires the six admitted video inputs and no static batches")
    encode = partial(encode_checkpoint_clip, verified, prepared._attested, torch, processor)

    progress["stage"] = "shared_initialization"
    with TemporaryDirectory(prefix="fsboard-fit-initialization-") as temporary:
        initial_path = Path(temporary)
        torch.manual_seed(seed)
        initialization = paired.create_initial_adapter(torch, get_base(), lora_config, wrap, revision, initial_path)
        gc.collect()
        torch.cuda.empty_cache()
        torch.manual_seed(seed)
        model = paired.load_initialized_adapter(
            get_base, lambda base, path: peft_model.from_pretrained(base, str(path), is_trainable=True),
            initial_path, initialization["adapter_sha256"],
        )
    digest = paired.parameter_digest(model)
    if digest != initialization["parameter_sha256"]:
        raise cloud.RunError("Fit adapter differs from the frozen common initialization")
    cloud.publish_receipt(args.output / "initialization.json", initialization)

    torch.manual_seed(seed)
    checkpoints = run_updates(
        torch, model, video, ids, partial(assess, torch, processor, model, clips, encode),
        digest, args.output, progress,
    )
    final = checkpoints[-1]
    progress["stage"] = "fit_guard"
    if not final["fit_guard_passed"]:
        raise cloud.RunError("The six training clips did not meet the engineering fit guard at 100 updates")

    summaries = [
        {key: value for key, value in row.items() if key != "examples"} for row in checkpoints
    ]
    cloud.publish_receipt(args.output / "success.json", {
        "schema_version": 1, "fit_id": FIT_ID, "status": "fit_guard_passed", "scope": FIT_POLICY["scope"],
        "pins": pins, "source_sha256": sources, "training_clip_ids": ids,
        "initial_parameter_sha256": digest, "final_parameter_sha256": final["parameter_sha256"],
        "optimizer_updates": final["optimizer_updates"], "checkpoints": summaries,
        "video_microbatches": final["optimizer_updates"] * FIT_POLICY["video_microbatches_per_update"],
        "static_microbatches": 0, "fit_adapter_reusable": False, "holdout_or_development_model_calls": 0,
        "peak_gpu_allocated_bytes": torch.cuda.max_memory_allocated(),
        "peak_gpu_reserved_bytes": torch.cuda.max_memory_reserved(),
        "elapsed_seconds": time.monotonic() - started,
    })


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in (
        "admission", "proposal", "previous-receipt", "processor-receipt",
        "prior-schedule", "static-manifest", "source-manifest", "fit-protocol",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
    for name in ("admission", "proposal", "protocol", "processor-receipt", "source-manifest", "fit-protocol"):
        parser.add_argument(f"--{name}-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True, help="Fresh directory on private cloud storage")
    args = parser.parse_args(argv)
    if any(not cloud.SHA256.fullmatch(value) for name, value in vars(args).items() if name.endswith("_sha256")):
        parser.error("SHA-256 pins must contain 64 lowercase hexadecimal digits")
    return args


def main(argv=None):
    args = parse_args(argv)
    if sys.platform != "linux":
        print("FSboard fit smoke requires the Linux cloud runtime.", file=sys.stderr)
        return 1
    try:
        args.output.mkdir(mode=0o700, exist_ok=False)
    except OSError as error:
        print(f"Could not create the fresh private output directory ({type(error).__name__}).", file=sys.stderr)
        return 1

    progress = {"stage": "startup", "optimizer_updates": 0, "completed_checkpoints": [], "training_started": False}
    prior_handler = signal.signal(signal.SIGTERM, cloud.handle_sigterm)
    try:
        run_fit(args, progress)
    except (Exception, KeyboardInterrupt) as error:
        if (args.output / "success.json").is_file():
            return 0
        cancelled = isinstance(error, KeyboardInterrupt)
        trace = traceback.extract_tb(error.__traceback__)
        report = {
            "schema_version": 1, "fit_id": FIT_ID, **progress, "status": "cancelled" if cancelled else "failed",
            "error_type": type(error).__name__,
            "error_location": f"{Path(trace[-1].filename).name}:{trace[-1].lineno}" if trace else None,
            "fit_adapter_reusable": False,
        }
        if isinstance(error, cloud.RunError):
            report["error"] = str(error)
        try:
            cloud.publish_receipt(args.output / "failure.json", report)
        except OSError as receipt_error:
            print(f"Could not publish failure.json ({type(receipt_error).__name__}).", file=sys.stderr)
        print(f"FSboard fit smoke {report['status']} during {progress['stage']} ({type(error).__name__}).",
              file=sys.stderr)
        return 130 if cancelled else 1
    finally:
        signal.signal(signal.SIGTERM, prior_handler)
    print('{"status":"fit_guard_passed"}')
    return 0


if __name__ == "__main__":
    sys.exit(main())
