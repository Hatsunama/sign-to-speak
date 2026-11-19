"""Emit the frozen FSboard pair command after verifying the private six-clip fit evidence."""

import argparse
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import re
import sys

import fsboard_task_fit as fit
from fsboard_training_intake import PREVIOUS_RECEIPT_SHA256


SOURCE_MANIFEST_SHA256 = "5b2df533e3d36fc29a7df820e4e9e069cbd1e739b48e44e69fb4db7c052e95aa"
FIT_PROTOCOL_SHA256 = "714523f181dc6a01b6b4666bc7da62483bb18504dc822c5d39ac13ae10a384e9"
ADMISSION_SHA256 = "780c78b8b3b9f43aa4919815394784f8b5d3cb1432574e87644f39ed4c03d075"
PROPOSAL_SHA256 = "6f04da3b218f664d8ab4097ab0212fdd135220f89dc8019e9b3b1fb04213e6d9"
PROTOCOL_SHA256 = "9e482b474b1250422e571179ee217a6bf2ad2a223ec28b8fc2f9a288b88a90f1"
EOS_IDS = [248046, 248044]
RECEIPT_LIMIT = 2_000_000

PAIR_PATH_FLAGS = (
    "source-manifest", "admission", "proposal", "previous-receipt",
    "processor-receipt", "control-plan", "prior-schedule", "static-manifest", "output",
)


class LaunchError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise LaunchError(message)


def valid_pin(value):
    return isinstance(value, str) and fit.cloud.SHA256.fullmatch(value) is not None


def read_receipt(path, expected):
    require(valid_pin(expected), f"Missing or invalid SHA-256 pin for {path.name}")
    require(not path.is_symlink() and path.is_file(), f"Missing regular {path.name}")
    with path.open("rb") as source:
        payload = source.read(RECEIPT_LIMIT)
    require(len(payload) < RECEIPT_LIMIT and sha256(payload).hexdigest() == expected,
            f"SHA-256 or size mismatch for {path.name}")
    document = json.loads(payload)
    require(isinstance(document, dict), f"Expected a JSON object in {path.name}")
    return document


def finite_number(value):
    return type(value) in (int, float) and isfinite(value)


def same_json(left, right):
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True, allow_nan=False)


def verify_checkpoint(document, update, clip_ids, baseline):
    require(type(document.get("optimizer_updates")) is int and document["optimizer_updates"] == update
            and valid_pin(document.get("parameter_sha256")) and document.get("eos_token_ids") == EOS_IDS,
            "Fit checkpoint step, parameter digest or EOS does not match")
    examples = document.get("examples")
    require(isinstance(examples, list) and len(examples) == len(clip_ids),
            "Fit checkpoint must contain six training examples")
    losses, references = [], []
    for row, clip_id in zip(examples, clip_ids, strict=True):
        require(isinstance(row, dict) and row.get("clip_id") == clip_id and row.get("eos_token_ids") == EOS_IDS
                and row.get("output_equals_matched") is True, "Fit checkpoint example identity or EOS does not match")
        tokens = row.get("generated_token_ids")
        cap = fit.FIT_POLICY["generation"]["max_new_tokens"]
        require(isinstance(tokens, list) and 1 <= len(tokens) <= cap
                and all(type(token) is int and token >= 0 for token in tokens)
                and type(row.get("generated_tokens")) is int and row["generated_tokens"] == len(tokens)
                and not any(token in EOS_IDS for token in tokens[:-1]),
                "Fit checkpoint generation violates the effective EOS or token cap")
        require((tokens[-1] in EOS_IDS and row.get("stop") == "eos")
                or (tokens[-1] not in EOS_IDS and len(tokens) == cap and row.get("stop") == "max_tokens"),
                "Fit checkpoint generation stop does not match the token sequence")
        decoded, reference = row.get("decoded"), row.get("reference")
        require(isinstance(decoded, str) and isinstance(reference, str)
                and re.fullmatch(fit.protocol.TARGET_PATTERN, reference) is not None
                and same_json(row.get("scores"), fit.scorer.score(decoded, reference)),
                "Fit checkpoint scores do not match the decoded output and reference")
        references.append(reference)
        loss, model_input = row.get("reference_nll"), row.get("model_input")
        require(isinstance(loss, dict) and isinstance(model_input, dict), "Missing fit checkpoint loss or input")
        mean, count, total = (loss.get(key) for key in ("mean_per_token", "tokens_including_eos", "total"))
        require(finite_number(mean) and mean >= 0 and type(count) is int and count >= 2
                and finite_number(total) and total == mean * count
                and model_input.get("eos_token_id") == EOS_IDS[0]
                and type(model_input.get("supervised_tokens_including_eos")) is int
                and model_input["supervised_tokens_including_eos"] == count,
                "Fit checkpoint loss does not match the EOS-inclusive reference input")
        losses.append(mean)

    aggregate = fit.scorer.aggregate(examples)
    mean = sum(losses) / len(losses)
    require(len(set(references)) == len(clip_ids) and same_json(document.get("aggregate"), aggregate)
            and finite_number(document.get("mean_matched_teacher_nll"))
            and document["mean_matched_teacher_nll"] == mean and (baseline is not None or mean > 0),
            "Fit checkpoint aggregate or mean NLL does not match its six distinct training examples")
    reduction = None if baseline is None else 1 - mean / baseline
    passed = baseline is not None and (
        aggregate["exact_matches"] == fit.FIT_POLICY["minimum_exact_matches"]
        and reduction >= fit.FIT_POLICY["minimum_relative_teacher_nll_reduction"]
    )
    require(same_json(document.get("relative_teacher_nll_reduction"), reduction)
            and document.get("fit_guard_passed") is passed, "Fit checkpoint guard does not match scores and NLL")
    return mean, passed, references


def verify_fit(args):
    records = args.fit_records
    failure = records / "failure.json"
    require(not failure.exists() and not failure.is_symlink(), "A fit failure receipt is present")
    exit_document = read_receipt(args.fit_child_exit, args.fit_child_exit_sha256)
    saved = exit_document.get("saved_json_sha256")
    require(type(exit_document.get("returncode")) is int and exit_document["returncode"] == 0
            and exit_document.get("fit_adapter_reused") is False and isinstance(saved, dict)
            and "failure.json" not in saved and saved.get("success.json") == args.fit_success_sha256,
            "Fit child did not report a successful exit with a discarded adapter and matching success digest")
    success = read_receipt(records / "success.json", args.fit_success_sha256)
    update = success.get("optimizer_updates")
    steps = fit.FIT_POLICY["checkpoints"]
    require(type(update) is int and update in steps[1:], "Fit must pass at 50 or 100 updates")
    steps = [step for step in steps if step <= update]
    sources = fit.verify_source(args.source_manifest, SOURCE_MANIFEST_SHA256)
    pins = {
        "fit_protocol_sha256": FIT_PROTOCOL_SHA256,
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "admission_sha256": ADMISSION_SHA256,
        "proposal_sha256": PROPOSAL_SHA256,
        "protocol_sha256": PROTOCOL_SHA256,
        "previous_receipt_sha256": PREVIOUS_RECEIPT_SHA256,
        "processor_receipt_sha256": args.processor_receipt_sha256,
        "prior_schedule_sha256": fit.protocol.PREVIOUS_SCHEDULE_SHA256,
        "static_manifest_sha256": fit.protocol.STATIC_MANIFEST_SHA256,
        "scorer_sha256": fit.protocol.SCORER_SHA256,
    }
    require(type(success.get("schema_version")) is int and success["schema_version"] == 1
            and success.get("fit_id") == fit.FIT_ID and success.get("scope") == fit.FIT_POLICY["scope"]
            and success.get("status") == "fit_guard_passed" and success.get("fit_adapter_reusable") is False
            and success.get("pins") == pins and success.get("source_sha256") == sources
            and success.get("eos_token_ids") == EOS_IDS,
            "Fit success receipt disagrees with the pinned v2 fit, source, data or processor")
    counts = {"video_microbatches": update * 2, "static_microbatches": 0, "holdout_or_development_model_calls": 0}
    require(all(type(success.get(key)) is int and success[key] == value for key, value in counts.items()),
            "Fit success receipt has invalid video, static or holdout/development counts")
    clip_ids = success.get("training_clip_ids")
    require(isinstance(clip_ids, list) and len(clip_ids) == fit.FIT_POLICY["training_examples"]
            and all(isinstance(item, str) and re.fullmatch(r"training-[0-9]{3}", item)
                    and 1 <= int(item[-3:]) <= 192 for item in clip_ids)
            and clip_ids == sorted(set(clip_ids)), "Fit success receipt must list six distinct training clip IDs in order")
    filenames = [f"checkpoint-{step:03d}.json" for step in steps]
    require(set(filenames) == {name for name in saved if name.startswith("checkpoint-")}
            == {path.name for path in records.glob("checkpoint-*.json")}, "Fit checkpoint history is incomplete or unexpected")
    summaries = success.get("checkpoints")
    require(isinstance(summaries, list) and len(summaries) == len(steps),
            "Fit success receipt has invalid checkpoint summaries")
    baseline, baseline_references, digests = None, None, []
    for step, filename, summary in zip(steps, filenames, summaries, strict=True):
        checkpoint = read_receipt(records / filename, saved.get(filename))
        require(same_json(summary, {key: value for key, value in checkpoint.items() if key != "examples"}),
                "Fit success summary disagrees with the checkpoint")
        mean, passed, references = verify_checkpoint(checkpoint, step, clip_ids, baseline)
        require(passed == (step == update), "Fit did not stop at the first passing trained checkpoint")
        if step == 0:
            baseline, baseline_references = mean, references
        require(references == baseline_references, "Fit reference changed between checkpoints")
        digests.append(checkpoint["parameter_sha256"])
    require(len(set(digests)) == len(digests) and success.get("initial_parameter_sha256") == digests[0]
            and success.get("final_parameter_sha256") == digests[-1], "Fit parameter digests disagree with checkpoints")


def build_pair_argv(args):
    require(valid_pin(args.processor_receipt_sha256) and valid_pin(args.control_plan_sha256),
            "Pair requires trusted processor receipt and control plan SHA-256 pins")
    output = args.output.resolve()
    require(not args.output.exists() and not args.output.is_symlink() and not output.exists()
            and not output.is_relative_to(args.fit_records.resolve().parent),
            "Pair output must be fresh and outside the fit run")
    verify_fit(args)
    argv = [sys.executable, "-B", "-m", "fsboard_task_cloud_training"]
    for name in PAIR_PATH_FLAGS:
        argv.extend((f"--{name}", str(getattr(args, name.replace("-", "_")))))
    pins = {
        "source-manifest": SOURCE_MANIFEST_SHA256, "admission": ADMISSION_SHA256, "proposal": PROPOSAL_SHA256,
        "protocol": PROTOCOL_SHA256, "processor-receipt": args.processor_receipt_sha256,
        "control-plan": args.control_plan_sha256,
    }
    for name, digest in pins.items():
        argv.extend((f"--{name}-sha256", digest))
    return argv


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ("fit-records", "fit-child-exit", *PAIR_PATH_FLAGS):
        parser.add_argument(f"--{name}", type=Path, required=True)
    for name in ("fit-child-exit", "fit-success", "processor-receipt", "control-plan"):
        parser.add_argument(f"--{name}-sha256", required=True)
    return parser.parse_args(argv)


def main(argv=None):
    try:
        command = build_pair_argv(parse_args(argv))
    except (OSError, ValueError, OverflowError) as error:
        print(f"Pair launch refused ({type(error).__name__}): {error}", file=sys.stderr)
        return 1
    print(json.dumps(command))
    return 0


if __name__ == "__main__":
    sys.exit(main())

