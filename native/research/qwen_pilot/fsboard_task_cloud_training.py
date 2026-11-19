"""Run the frozen 192-update FSboard v4 training pair on a cloud L4."""

import argparse
from dataclasses import asdict
import gc
from hashlib import sha256
import json
from pathlib import Path
import re
import signal
import sys
from tempfile import TemporaryDirectory
import time
import traceback


RUNTIME_SOURCES = frozenset({
    "admission_probe.py", "fsboard_admission.py", "fsboard_evaluate.py",
    "fsboard_task_admission.py", "fsboard_task_cloud_training.py", "fsboard_task_controls.py",
    "fsboard_task_inputs.py", "fsboard_task_protocol.py", "fsboard_task_training.py",
    "fsboard_training_intake.py", "paired_experiment.py", "requirements-train-cloud.txt",
    "static_admission.py", "train_qwen_pilot.py", "video_orientation.py",
})
SHA256 = re.compile(r"[0-9a-f]{64}")


class RunError(ValueError):
    pass


def verify_source(path, expected):
    payload = path.read_bytes()
    if sha256(payload).hexdigest() != expected:
        raise RunError("Source manifest SHA-256 mismatch")
    manifest = json.loads(payload.decode("utf-8"))
    if not isinstance(manifest, list) or any(
        not isinstance(row, dict) or not isinstance(row.get("file"), str) for row in manifest
    ):
        raise RunError("Invalid source manifest")
    rows = {row["file"]: row for row in manifest}
    if len(rows) != len(manifest) or not RUNTIME_SOURCES.issubset(rows):
        raise RunError("Source manifest must bind all training runtime files exactly once")

    hashes = {}
    root = Path(__file__).parent
    for name in sorted(RUNTIME_SOURCES):
        source = root / name
        if source.is_symlink() or not source.is_file():
            raise RunError(f"Missing regular runtime source: {name}")
        data = source.read_bytes()
        digest = sha256(data).hexdigest()
        if rows[name].get("sha256") != digest or rows[name].get("bytes") != len(data):
            raise RunError(f"Runtime source SHA-256 or size mismatch: {name}")
        hashes[name] = digest
    return hashes


def publish_receipt(path, report):
    with TemporaryDirectory(prefix=".receipt-", dir=path.parent) as temporary:
        pending = Path(temporary) / "receipt.json"
        pending.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
        pending.chmod(0o600)
        path.hardlink_to(pending)


def check_model_path(model_id):
    # Transformers can prefer a relative local directory even when given a Hub revision.
    try:
        Path(model_id).lstat()
    except (FileNotFoundError, NotADirectoryError):
        return
    raise RunError("Local path shadows the frozen Hub model")


def load_runtime():
    import torch
    from peft import LoraConfig, PeftModel, get_peft_model
    from transformers import AutoProcessor, Qwen3_5ForConditionalGeneration

    return torch, AutoProcessor, Qwen3_5ForConditionalGeneration, LoraConfig, get_peft_model, PeftModel


def validate_arm_inputs(inputs, training_ids, settings):
    video_a, static_a, schedule_a = inputs["A"]
    video_b, static_b, schedule_b = inputs["B"]
    steps = settings["optimizer_steps_per_arm"]
    batch_size = settings["matching_video_microbatches_per_update"]
    if (
        steps != 192 or len(schedule_a) != steps or len(schedule_b) != steps
        or set(video_a) != training_ids or set(video_b) != training_ids or static_a
    ):
        raise RunError("Arm inputs disagree with the frozen optimizer budget or training roles")
    for a, b in zip(schedule_a, schedule_b, strict=True):
        if (
            len(a.video_ids) != batch_size or a.video_ids != b.video_ids
            or a.static_id is not None or b.static_id is None or b.static_id not in static_b
        ):
            raise RunError("Arm inputs disagree on video order or the arm B static exposure")
    video_ids = [item for step in schedule_a for item in step.video_ids]
    epoch_size = len(training_ids)
    if len(video_ids) != settings["video_epochs"] * epoch_size or any(
        set(video_ids[offset:offset + epoch_size]) != training_ids
        for offset in range(0, len(video_ids), epoch_size)
    ):
        raise RunError("The paired schedule does not visit all training clips exactly once per epoch")


def run_pair(args, progress):
    started = time.monotonic()
    output = args.output
    progress["stage"] = "source"
    sources = verify_source(args.source_manifest, args.source_manifest_sha256)

    import fsboard_task_controls as controls
    import fsboard_task_inputs as gate
    import fsboard_task_protocol as protocol
    import fsboard_task_training as training
    import paired_experiment as paired
    import train_qwen_pilot as pilot

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
    progress["stage"] = "holdout_controls"
    plan_bytes = gate.read_bounded(args.control_plan, gate.ADMISSION_LIMIT)
    if sha256(plan_bytes).hexdigest() != args.control_plan_sha256:
        raise RunError("Control plan SHA-256 mismatch")
    controls.verify_plan(verified.clips["holdout"], gate.read_json(plan_bytes))

    model_spec = verified.frozen_protocol["model"]
    model_id, revision = model_spec["id"], model_spec["revision"]
    settings = verified.frozen_protocol["training"]
    seed = settings["shared_adapter_initialization_seed"]
    pins = {
        "source_manifest_sha256": args.source_manifest_sha256,
        "admission_sha256": args.admission_sha256,
        "proposal_sha256": args.proposal_sha256,
        "protocol_sha256": args.protocol_sha256,
        "previous_receipt_sha256": verified.summary["previous_receipt_sha256"],
        "processor_receipt_sha256": args.processor_receipt_sha256,
        "control_plan_sha256": args.control_plan_sha256,
        "prior_schedule_sha256": protocol.PREVIOUS_SCHEDULE_SHA256,
        "static_manifest_sha256": protocol.STATIC_MANIFEST_SHA256,
    }

    progress["stage"] = "model_path"
    check_model_path(model_id)
    progress["stage"] = "runtime"
    # Atomic publication here also tests the output filesystem before model loading.
    publish_receipt(output / "experiment.json", {
        "schema_version": 1, "experiment_id": protocol.EXPERIMENT_ID, "model": model_spec,
        "pins": pins, "source_sha256": sources, "frozen_training_protocol": settings,
    })
    torch, processor_type, model_type, lora_config, wrap, peft_model = load_runtime()
    paired.validate_runtime(torch, output)

    def get_base():
        check_model_path(model_id)
        model = model_type.from_pretrained(
            model_id, revision=revision, token=False, use_safetensors=True,
            dtype=torch.bfloat16, attn_implementation="sdpa",
        ).to("cuda")
        if model.config.model_type != "qwen3_5":
            raise RunError("Loaded model is not Qwen3.5 multimodal")
        return model.requires_grad_(False)

    progress["stage"] = "processor"
    processor = processor_type.from_pretrained(model_id, revision=revision, token=False, trust_remote_code=False)
    inputs = {arm: prepared.arm_inputs(arm, torch=torch, processor=processor) for arm in ("A", "B")}
    validate_arm_inputs(inputs, {clip["id"] for clip in verified.clips["training"]}, settings)
    schedule_path = output / "schedule.json"
    pilot.save_json(schedule_path, {arm: [asdict(step) for step in data[2]] for arm, data in inputs.items()})
    schedule_sha = sha256(schedule_path.read_bytes()).hexdigest()

    progress["stage"] = "shared_initialization"
    initial_path = output / "initialization"
    torch.manual_seed(seed)
    initialization = paired.create_initial_adapter(torch, get_base(), lora_config, wrap, revision, initial_path)
    pilot.save_json(initial_path / "receipt.json", initialization)
    gc.collect()
    torch.cuda.empty_cache()

    summaries = {}
    for arm, (video, static, schedule) in inputs.items():
        progress.update(arm=arm, stage="arm_initialization")
        arm_path = output / arm
        arm_path.mkdir()
        torch.manual_seed(seed)
        torch.cuda.reset_peak_memory_stats()
        model = paired.load_initialized_adapter(
            get_base, lambda base, path: peft_model.from_pretrained(base, str(path), is_trainable=True),
            initial_path, initialization["adapter_sha256"],
        )
        initial_parameters = paired.parameter_digest(model)
        if initial_parameters != initialization["parameter_sha256"]:
            raise RunError("Trainable parameters differ from the shared initialization")
        pilot.save_json(arm_path / "initialization.json", {
            "shared_adapter_sha256": initialization["adapter_sha256"],
            "trainable_parameter_sha256": initial_parameters,
        })

        progress.update(stage="training", training_started=True)
        torch.manual_seed(seed)
        summary = paired.train_arm(torch, model, video, static, schedule, arm_path, initial_parameters)

        progress["stage"] = "final_checkpoint"
        checkpoint = arm_path / f"checkpoint-{len(schedule):03d}"
        checkpoint_receipt_path = checkpoint / "receipt.json"
        checkpoint_bytes = checkpoint_receipt_path.read_bytes()
        final = gate.read_json(checkpoint_bytes)
        if (
            final.get("optimizer_steps") != len(schedule) or summary["optimizer_steps"] != len(schedule)
            or summary["video_microbatches"] != settings["matching_video_microbatches_per_arm"]
            or summary["static_microbatches"] != settings[arm]["static_microbatches_per_update"] * len(schedule)
            or final.get("parameter_sha256") != summary["parameter_sha256"]
            or summary["parameter_sha256"] == initial_parameters
            or final.get("adapter_sha256") != paired.adapter_digest(checkpoint)
            or final.get("optimizer_sha256") != sha256((checkpoint / "optimizer.pt").read_bytes()).hexdigest()
        ):
            raise RunError("Final checkpoint does not contain the expected updated adapter and optimizer")
        summary.update(
            adapter_sha256=final["adapter_sha256"],
            final_checkpoint=str(checkpoint.relative_to(output).as_posix()),
            final_checkpoint_receipt_sha256=sha256(checkpoint_bytes).hexdigest(),
            peak_gpu_allocated_bytes=torch.cuda.max_memory_allocated(),
            peak_gpu_reserved_bytes=torch.cuda.max_memory_reserved(),
        )
        pilot.save_json(arm_path / "summary.json", summary)
        summaries[arm] = summary
        progress["completed_arms"].append(arm)
        del model
        gc.collect()
        torch.cuda.empty_cache()

    progress.update(stage="completion", arm=None)
    publish_receipt(output / "success.json", {
        "schema_version": 1, "experiment_id": protocol.EXPERIMENT_ID, "status": "completed_training_pair",
        "model": model_spec, "pins": pins, "schedule_sha256": schedule_sha,
        "initialization_sha256": initialization["adapter_sha256"], "arms": summaries,
        "processor_token_contract_verified": True, "training_started": True, "model_scoring_started": False,
        "elapsed_seconds": time.monotonic() - started,
    })


def handle_sigterm(signum, frame):
    raise KeyboardInterrupt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in (
        "admission", "proposal", "previous-receipt", "processor-receipt", "control-plan",
        "prior-schedule", "static-manifest", "source-manifest",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
    for name in ("admission", "proposal", "protocol", "processor-receipt", "control-plan", "source-manifest"):
        parser.add_argument(f"--{name}-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path,
                        help="Fresh directory on private local cloud storage supporting hard links")
    args = parser.parse_args(argv)
    if any(not SHA256.fullmatch(value) for name, value in vars(args).items() if name.endswith("_sha256")):
        parser.error("SHA-256 pins must contain 64 lowercase hexadecimal digits")
    if sys.platform != "linux":
        print("FSboard training requires the Linux cloud runtime.", file=sys.stderr)
        return 1
    try:
        args.output.mkdir(mode=0o700, exist_ok=False)
    except OSError as error:
        print(f"Could not create the fresh private output directory ({type(error).__name__}).", file=sys.stderr)
        return 1

    progress = {"stage": "startup", "arm": None, "completed_arms": [],
                "training_started": False, "model_scoring_started": False}
    prior_handler = signal.signal(signal.SIGTERM, handle_sigterm)
    try:
        run_pair(args, progress)
    except (Exception, KeyboardInterrupt) as error:
        if (args.output / "success.json").is_file():
            return 0
        cancelled = isinstance(error, KeyboardInterrupt)
        trace = traceback.extract_tb(error.__traceback__)
        report = {
            "schema_version": 1, **progress, "status": "cancelled" if cancelled else "failed",
            "error_type": type(error).__name__,
            "error_location": f"{Path(trace[-1].filename).name}:{trace[-1].lineno}" if trace else None,
        }
        if isinstance(error, RunError):
            report["error"] = str(error)
        try:
            publish_receipt(args.output / "failure.json", report)
        except OSError as receipt_error:
            print(f"Could not publish failure.json ({type(receipt_error).__name__}).", file=sys.stderr)
        print(f"FSboard training {report['status']} during {progress['stage']} ({type(error).__name__}).",
              file=sys.stderr)
        return 130 if cancelled else 1
    finally:
        signal.signal(signal.SIGTERM, prior_handler)
    print('{"status":"completed_training_pair"}')
    return 0


if __name__ == "__main__":
    sys.exit(main())

