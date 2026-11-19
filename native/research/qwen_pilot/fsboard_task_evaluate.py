"""Score the frozen FSboard v4 holdout with a single pinned model candidate."""
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
import json
import math
from pathlib import Path

import fsboard_evaluate as scorer
import fsboard_task_controls as controls
import fsboard_task_inputs as gate
import fsboard_task_protocol as protocol
from fsboard_task_admission import canonical_sha256
from fsboard_task_training import ENCODING_FIELDS, _validate_encoding_receipt
import fsboard_training_intake as intake
from paired_experiment import generation_status, sha256
import train_qwen_pilot as pilot


CONDITIONS = ("matched", "reordered", "repeated_middle_frame", "black", "wrong_video")
HOLDOUT_COUNT = gate.ROLE_COUNTS["holdout"]
PINS = ("admission_sha256", "proposal_sha256", "protocol_sha256", "previous_receipt_sha256")


@dataclass(frozen=True)
class _ControlInputsReceipt:
    admission_binding: tuple
    plan_sha256: str
    plan_file_sha256: str
    sha256: str
    payload: bytes


def _holdout(verified):
    if (
        not isinstance(verified, gate.Admission) or verified.clips.keys() != gate.ROLE_COUNTS.keys()
        or verified.summary.get("status") != "full_media_verified" or verified.summary.get("mode") != "full"
        or verified.summary.get("experiment_id") != protocol.EXPERIMENT_ID
        or verified.summary.get("training_media_complete") is not True
        or verified.summary.get("clips_verified") != gate.ROLE_COUNTS
        or any(len(verified.clips[role]) != count for role, count in gate.ROLE_COUNTS.items())
    ):
        raise gate.AdmissionError("expected the gate-verified FSboard v4 admission")
    binding = tuple(verified.summary.get(key) for key in PINS)
    frozen = verified.frozen_protocol
    if (
        not all(gate.valid_sha256(value) for value in binding)
        or verified.summary.get("previous_receipt_sha256") != intake.PREVIOUS_RECEIPT_SHA256
        or canonical_sha256(frozen) != verified.summary["protocol_sha256"]
        or frozen != protocol.frozen_protocol()
    ):
        raise gate.AdmissionError("FSboard evaluation admission or frozen protocol binding failed")
    clips = verified.clips["holdout"]
    if any(
        not isinstance(clip, dict) or clip.get("id") != f"holdout-{index:03d}"
        or clip.get("role") != "holdout" or clip.get("split") != frozen["role_sources"]["holdout"]
        for index, clip in enumerate(clips, 1)
    ):
        raise gate.AdmissionError("FSboard evaluation requires the exact ordered publisher holdout")
    return clips, binding


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise gate.AdmissionError("duplicate key in pinned FSboard evaluation JSON")
        result[key] = value
    return result


def _reject_constant(value):
    raise gate.AdmissionError(f"invalid JSON numeric constant {value}")


def _strict_json(payload):
    try:
        result = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        if isinstance(result, dict):
            return result
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise gate.AdmissionError("invalid UTF-8 FSboard evaluation JSON") from exc
    raise gate.AdmissionError("expected a pinned FSboard evaluation JSON object")


def _read_pinned(path, expected):
    if not gate.valid_sha256(expected):
        raise gate.AdmissionError("expected an independently trusted evaluation SHA-256 pin")
    payload = gate.read_bounded(Path(path), gate.ADMISSION_LIMIT)
    if sha256(payload) != expected:
        raise gate.AdmissionError("FSboard evaluation input file SHA-256 mismatch")
    return payload


def load_control_inputs(verified, *, control_plan_path, control_plan_sha256,
                        control_inputs_path, control_inputs_sha256):
    """Read the independently pinned R4 plan and processor evidence for this admission."""
    clips, binding = _holdout(verified)
    plan = _strict_json(_read_pinned(control_plan_path, control_plan_sha256))
    controls.verify_plan(clips, plan)
    payload = _read_pinned(control_inputs_path, control_inputs_sha256)
    report = _strict_json(payload)
    expected = {
        "status": "ALL_HOLDOUT_CONTROL_INPUTS_ENCODED", "conditions_per_clip": len(CONDITIONS),
        "clips": HOLDOUT_COUNT, "control_plan_sha256": control_plan_sha256,
        "model_scoring_started": False, "training_eligible": False,
    }
    if (
        report.keys() != expected.keys() | {"examples", "pixel_collisions"}
        or canonical_sha256({key: report[key] for key in expected}) != canonical_sha256(expected)
    ):
        raise gate.AdmissionError("R4 control processor receipt disagrees with the pinned plan or coverage")
    examples = report["examples"]
    if not isinstance(examples, dict) or examples.keys() != plan["examples"].keys():
        raise gate.AdmissionError("R4 control processor receipt must contain exactly 64 admitted holdout clips")
    frozen = verified.frozen_protocol
    context = frozen["input"]["dense_max_sequence_tokens"]
    cap = frozen["evaluation"]["max_new_tokens"]
    collisions = []
    for clip in clips:
        clip_id, count = clip["id"], clip["sampled_frame_count"]
        rows = examples[clip_id]
        if not isinstance(rows, dict) or rows.keys() != set(CONDITIONS):
            raise gate.AdmissionError("R4 control processor receipt must include all five conditions per clip")
        for row in rows.values():
            if (
                not isinstance(row, dict) or row.keys() != ENCODING_FIELDS
                or type(row["sampled_frame_count"]) is not int or row["sampled_frame_count"] != count
            ):
                raise gate.AdmissionError("invalid R4 control processor encoding receipt")
            _validate_encoding_receipt(row, count, context, cap)
        for first, second in combinations(CONDITIONS, 2):
            if rows[first]["pixel_values_sha256"] == rows[second]["pixel_values_sha256"]:
                collisions.append({"clip": clip_id, "first": first, "second": second})
    if report["pixel_collisions"] != collisions:
        raise gate.AdmissionError("R4 control processor collision evidence disagrees with the encoding receipts")
    return plan, _ControlInputsReceipt(binding, plan["plan_sha256"], control_plan_sha256, control_inputs_sha256, payload)


def _device_batch(torch, arrays):
    return {name: torch.from_numpy(array).to("cuda") for name, array in arrays.items()}


def _generate(torch, model, prefix, eos_ids, cap):
    generated = model.generate(
        **_device_batch(torch, prefix), max_new_tokens=cap, do_sample=False, num_beams=1,
        use_cache=True, return_dict_in_generate=False, eos_token_id=list(eos_ids),
    )
    length = prefix["input_ids"].shape[1]
    if (
        len(generated.shape) != 2 or generated.shape[0] != 1
        or not length < generated.shape[1] <= length + cap
        or generated[0, :length].tolist() != prefix["input_ids"][0].tolist()
    ):
        raise RuntimeError("invalid generation shape, prefix or token cap")
    tokens = generated[0, length:].tolist()
    if any(type(token) is not int or token < 0 for token in tokens) or any(token in eos_ids for token in tokens[:-1]):
        raise RuntimeError("invalid generated tokens or tokens after EOS")
    return tokens, generation_status(tokens, eos_ids, cap)


def _score_condition(torch, processor, model, images, clip, condition, attested, *, context, cap, eos):
    reference = clip["reference"]
    prefix, teacher, actual = gate.encode_example(
        processor, images, reference, context=context, generation_cap=cap,
        min_frames=protocol.MIN_FRAMES, max_frames=protocol.MAX_FRAMES,
    )
    if canonical_sha256(actual) != canonical_sha256(attested):
        raise gate.AdmissionError(f"{clip['id']} {condition} disagrees with R4 processor attestation")
    tokens, stop = _generate(torch, model, prefix, eos, cap)
    decoded = processor.tokenizer.decode(tokens, skip_special_tokens=True, clean_up_tokenization_spaces=False)
    nll = pilot.completion_loss(torch, model, _device_batch(torch, teacher)).item()
    if not math.isfinite(nll) or nll < 0:
        raise RuntimeError("invalid reference completion NLL")
    count = actual["supervised_tokens_including_eos"]
    return {
        "model_input": actual, "generated_token_ids": tokens, "generated_tokens": len(tokens), "stop": stop,
        "eos_token_ids": list(eos), "decoded": decoded, "scores": scorer.score(decoded, reference),
        "reference_nll": {"tokens_including_eos": count, "mean_per_token": nll, "total": nll * count},
    }


def _aggregate(rows, keys):
    groups = defaultdict(list)
    for row in rows:
        for key in keys(row):
            groups[key].append(row)
    return {key: scorer.aggregate(group) for key, group in sorted(groups.items())}


def _summarize(results):
    conditions = {}
    for condition, rows in results.items():
        if len(rows) != HOLDOUT_COUNT:
            raise RuntimeError("incomplete FSboard holdout evaluation")
        conditions[condition] = {
            "aggregate": scorer.aggregate(rows),
            "by_category": _aggregate(rows, lambda row: row["categories"]),
            "by_signer_id": _aggregate(rows, lambda row: (row["signer_id"],)),
        }

    matched = {row["clip_id"]: row["reference_nll"]["mean_per_token"] for row in results["matched"]}
    deltas = {
        row["clip_id"]: row["reference_nll"]["mean_per_token"] - matched[row["clip_id"]]
        for row in results["wrong_video"]
    }
    return {
        "conditions": conditions,
        "paired_reference_nll": {
            "wrong_video_minus_matched_by_clip": deltas,
            "mean_wrong_video_minus_matched": math.fsum(deltas.values()) / len(deltas),
            "clips_with_lower_matched_nll": sum(value > 0 for value in deltas.values()),
        },
    }


def evaluate_model(torch, processor, model, verified, control_plan, control_inputs_receipt, output, candidate):
    """Write completed private condition records and publish the summary after all 320 evaluations."""
    if candidate not in scorer.CANDIDATES:
        raise gate.AdmissionError("expected an FSboard evaluation candidate of base, A, or B")
    clips, binding = _holdout(verified)
    controls.verify_plan(clips, control_plan)
    receipt = control_inputs_receipt
    if (
        not isinstance(receipt, _ControlInputsReceipt) or receipt.admission_binding != binding
        or receipt.plan_sha256 != control_plan["plan_sha256"] or sha256(receipt.payload) != receipt.sha256
    ):
        raise gate.AdmissionError("FSboard evaluation requires the validated R4 control receipt and admission binding")
    report = _strict_json(receipt.payload)
    if report["control_plan_sha256"] != receipt.plan_file_sha256:
        raise gate.AdmissionError("R4 control processor receipt plan binding failed")

    frozen = verified.frozen_protocol
    context, cap = frozen["input"]["dense_max_sequence_tokens"], frozen["evaluation"]["max_new_tokens"]
    tokenizer = processor.tokenizer
    eos = gate.generation_eos_ids(
        tokenizer.eos_token_id, model.generation_config.eos_token_id, tokenizer.all_special_ids,
    )
    results = {condition: [] for condition in CONDITIONS}
    by_id = {clip["id"]: clip for clip in clips}
    output = Path(output)
    output.mkdir(mode=0o700)
    model.eval()
    with torch.inference_mode():
        for clip_id, clip in by_id.items():
            clip_output = output / clip_id
            clip_output.mkdir(mode=0o700)
            reference = clip["reference"]
            details = {
                "candidate": candidate, "clip_id": clip_id, "reference": reference,
                "signer_id": clip["signer_id"], "publisher_split": clip["split"],
                "source": clip["full_video_filename"], "categories": sorted(intake.categories(reference)),
            }
            with controls.condition_images(verified.root, by_id, control_plan, clip_id) as images:
                for condition in CONDITIONS:
                    row = _score_condition(
                        torch, processor, model, images[condition], clip, condition,
                        report["examples"][clip_id][condition], context=context, cap=cap, eos=eos,
                    )
                    normalized = scorer.normalize(row["decoded"])
                    if condition == "matched":
                        matched = normalized
                    row.update(
                        details, condition=condition, output_equals_matched=normalized == matched,
                        control=control_plan["examples"][clip_id]["conditions"][condition],
                    )
                    gate.publish_private(clip_output / f"{condition}.json", row)
                    results[condition].append(row)
    summary = {
        "schema_version": 1, "experiment_id": protocol.EXPERIMENT_ID,
        "status": "HOLDOUT_EVALUATED", "candidate": candidate,
        "pins": {
            **dict(zip(PINS, binding, strict=True)), "control_plan_sha256": receipt.plan_file_sha256,
            "control_plan_content_sha256": receipt.plan_sha256, "control_inputs_sha256": receipt.sha256,
            "scorer_sha256": frozen["evaluation"]["scorer_sha256"],
        },
        "generation": {
            "max_new_tokens": cap, "do_sample": False, "num_beams": 1, "use_cache": True,
            "enable_thinking": False, "eos_token_ids": list(eos),
        },
        "donor_usage": control_plan["donor_usage"], "pixel_collisions": report["pixel_collisions"],
        **_summarize(results),
    }
    gate.publish_private(output / "summary.json", summary)
    return summary
