"""Compare bound cloud and Android hands from the frozen RGBA replay contract."""

import argparse
import hashlib
import json
import math
import re
import sys
from decimal import ROUND_UP, Decimal, InvalidOperation, localcontext
from fractions import Fraction
from pathlib import Path
from statistics import mean


MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_ANDROID_BYTES = 512 * 1024
MAX_RGBA_BYTES = 128 * 1024 * 1024
MAX_LONG = (1 << 63) - 1
MAX_COORDINATE = Decimal.from_float(sys.float_info.max)
HASH = re.compile(r"[0-9a-f]{64}")
RUN_ID = re.compile(r"[0-9a-f]{32}")
MODEL = {
    "bytes": 13683609,
    "sha256": "e2dab61191e2dcd0a15f943d8e3ed1dce13c82dfa597b9dd39f562975a50c3f8",
}
CROSS_PLATFORM_LIMIT = 0.001
REPLAY_LIMIT = 0.00001
RUNS = ("initial", "interrupted", "recovered")
SLOTS = ("left_hand", "right_hand")
ROW_BINDINGS = ("frame_index", "timestamp_ms", "rgba_sha256")


class InvalidParityInput(ValueError):
    """An input violated a replay contract or binding."""


def require(condition, message):
    if not condition:
        raise InvalidParityInput(message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON object key")
        result[key] = value
    return result


def json_float(token):
    require(math.isfinite(float(token)), "JSON numbers must be finite")
    try:
        return Decimal(token)
    except InvalidOperation as exc:
        raise InvalidParityInput("invalid JSON number") from exc


def load_bound_json(path, expected_hash, maximum, label):
    require(HASH.fullmatch(expected_hash), f"{label} SHA-256 must have 64 lowercase hex digits")
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    require(0 < len(raw) <= maximum, f"{label} violates JSON byte limit")
    require(hashlib.sha256(raw).hexdigest() == expected_hash, f"{label} SHA-256 mismatch")
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_float=json_float,
            parse_constant=json_float,
        )
    except UnicodeDecodeError as exc:
        raise InvalidParityInput(f"{label} must be UTF-8") from exc
    except (ValueError, RecursionError) as exc:
        # JSON decode errors and parser bounds must fail even if the raw bytes match their hash.
        message = str(exc) if isinstance(exc, (InvalidParityInput, json.JSONDecodeError)) else "invalid JSON values"
        raise InvalidParityInput(f"{label}: {message}") from exc


def fields(value, expected, label):
    require(type(value) is dict and value.keys() == set(expected.split()), f"{label} fields differ from schema")


def count_list(value, count, label):
    require(type(value) is list and len(value) == count, f"{label} must contain {count} entries")


def integer(value, minimum, maximum, label):
    require(type(value) is int and minimum <= value <= maximum, f"{label} is outside integer bounds")
    return value


def bind(value, expected, label):
    """Require exact typed equality, including nested integer and Boolean values."""
    require(type(value) is type(expected), f"{label} has the wrong type")
    if type(expected) is dict:
        require(value.keys() == expected.keys(), f"{label} fields differ from schema")
        for key in expected:
            bind(value[key], expected[key], f"{label}.{key}")
    elif type(expected) is list:
        count_list(value, len(expected), label)
        for index, (actual, target) in enumerate(zip(value, expected)):
            bind(actual, target, f"{label}[{index}]")
    else:
        require(value == expected, f"{label} does not match its binding")


def hash_field(value, label):
    require(type(value) is str and HASH.fullmatch(value), f"{label} is not a lowercase SHA-256")


def validate_fixture(fixture):
    fields(
        fixture,
        "type schema_version pixel_format byte_order frames_file source_clip source_model frame_count frames",
        "fixture",
    )
    for name, value in {
        "type": "holistic_rgba_replay_fixture",
        "schema_version": 1,
        "pixel_format": "RGBA_8888",
        "byte_order": "row_major_RGBA",
        "source_model": MODEL,
    }.items():
        bind(fixture[name], value, f"fixture.{name}")

    clip = fixture["source_clip"]
    fields(clip, "bytes sha256", "fixture.source_clip")
    integer(clip["bytes"], 1, MAX_LONG, "fixture.source_clip.bytes")
    hash_field(clip["sha256"], "fixture.source_clip.sha256")
    blob = fixture["frames_file"]
    fields(blob, "name bytes sha256", "fixture.frames_file")
    bind(blob["name"], "holistic-parity.rgba", "fixture.frames_file.name")
    blob_bytes = integer(blob["bytes"], 1, MAX_RGBA_BYTES, "fixture.frames_file.bytes")
    hash_field(blob["sha256"], "fixture.frames_file.sha256")
    count = integer(fixture["frame_count"], 1, 64, "fixture.frame_count")
    count_list(fixture["frames"], count, "fixture.frames")

    origin = None
    previous_index = previous_ms = -1
    next_offset = 0
    for position, frame in enumerate(fixture["frames"]):
        label = f"fixture.frames[{position}]"
        fields(
            frame,
            "frame_index width height offset_bytes length_bytes rgba_sha256 source_pts source_time_base timestamp_ms",
            label,
        )
        previous_index = integer(frame["frame_index"], previous_index + 1, MAX_LONG, f"{label}.frame_index")
        width = integer(frame["width"], 1, 640, f"{label}.width")
        height = integer(frame["height"], 1, 640, f"{label}.height")
        bind(frame["offset_bytes"], next_offset, f"{label}.offset_bytes")
        bind(frame["length_bytes"], width * height * 4, f"{label}.length_bytes")
        next_offset += frame["length_bytes"]
        require(next_offset <= blob_bytes, f"{label} exceeds fixture.frames_file.bytes")
        hash_field(frame["rgba_sha256"], f"{label}.rgba_sha256")
        pts = integer(frame["source_pts"], -MAX_LONG - 1, MAX_LONG, f"{label}.source_pts")
        time_base = frame["source_time_base"]
        count_list(time_base, 2, f"{label}.source_time_base")
        numerator = integer(time_base[0], 1, MAX_LONG, f"{label}.source_time_base numerator")
        denominator = integer(time_base[1], 1, MAX_LONG, f"{label}.source_time_base denominator")
        source_time = Fraction(pts * numerator, denominator)
        if origin is None:
            origin = source_time
        timestamp_ms = integer(frame["timestamp_ms"], previous_ms + 1, MAX_LONG, f"{label}.timestamp_ms")
        require(timestamp_ms == (source_time - origin) * 1000 // 1, f"{label}.timestamp_ms differs from measured PTS")
        previous_ms = timestamp_ms
    require(next_offset == blob_bytes, "fixture.frames_file.bytes differs from frame coverage")


def validate_hands(landmarks, extra_fields, label):
    fields(landmarks, "left_hand right_hand" + extra_fields, label)
    for slot in SLOTS:
        hand = landmarks[slot]
        if hand is None:
            continue
        count_list(hand, 21, f"{label}.{slot}")
        for point in hand:
            count_list(point, 3, f"{label}.{slot} point")
            for value in point:
                require(
                    type(value) in (int, Decimal)
                    and Decimal(value).copy_abs() <= MAX_COORDINATE,
                    f"{label}.{slot} coordinates must be finite numbers",
                )


def validate_reference(reference, fixture, manifest_hash):
    fields(
        reference,
        "type schema_version status input runtime method frame_counts coverage frames rgba_fixture",
        "reference",
    )
    for name, value in {
        "type": "holistic_video_extraction",
        "schema_version": 1,
        "status": "SUCCESS",
        "input": {"clip": fixture["source_clip"], "model": fixture["source_model"]},
        "frame_counts": {"processed": fixture["frame_count"]},
        "rgba_fixture": {
            "manifest_sha256": manifest_hash,
            "frames_sha256": fixture["frames_file"]["sha256"],
            "frame_count": fixture["frame_count"],
        },
    }.items():
        bind(reference[name], value, f"reference.{name}")
    for name in ("runtime", "method", "coverage"):
        require(type(reference[name]) is dict, f"reference.{name} must be an object")
    count_list(reference["frames"], fixture["frame_count"], "reference.frames")
    for position, (frame, target) in enumerate(zip(reference["frames"], fixture["frames"])):
        label = f"reference.frames[{position}]"
        fields(
            frame,
            "frame_index source_pts source_time_base timestamp_ms status landmarks display_rotation_degrees_ccw input_size rgba_sha256",
            label,
        )
        for name in (*ROW_BINDINGS, "source_pts", "source_time_base"):
            bind(frame[name], target[name], f"{label}.{name}")
        bind(frame["status"], "processed", f"{label}.status")
        bind(frame["input_size"], [target["width"], target["height"]], f"{label}.input_size")
        validate_hands(frame["landmarks"], " pose face", f"{label}.landmarks")


def validate_android(android, fixture, manifest_hash):
    fields(
        android,
        "type schema_version status run_id fixture_manifest_sha256 frames_sha256 model_sha256 input_mode camera_path_tested runs",
        "android",
    )
    for name, value in {
        "type": "phone_holistic_producer_parity",
        "schema_version": 1,
        "status": "COMPLETED",
        "fixture_manifest_sha256": manifest_hash,
        "frames_sha256": fixture["frames_file"]["sha256"],
        "model_sha256": fixture["source_model"]["sha256"],
        "input_mode": "canonical_rgba",
        "camera_path_tested": False,
    }.items():
        bind(android[name], value, f"android.{name}")
    run_id = android["run_id"]
    require(type(run_id) is str and RUN_ID.fullmatch(run_id), "android.run_id must have 32 lowercase hex digits")
    count_list(android["runs"], len(RUNS), "android.runs")

    previous_end = 0
    for name, run in zip(RUNS, android["runs"]):
        label = f"android.runs.{name}"
        fields(run, "name detector_create_elapsed_ns detector_closed frames", label)
        bind(run["name"], name, f"{label}.name")
        bind(run["detector_closed"], True, f"{label}.detector_closed")
        create = integer(run["detector_create_elapsed_ns"], 1, MAX_LONG, f"{label}.detector_create_elapsed_ns")
        expected = fixture["frames"][:17] if name == "interrupted" else fixture["frames"]
        count_list(run["frames"], len(expected), f"{label}.frames")
        for position, (frame, target) in enumerate(zip(run["frames"], expected)):
            row = f"{label}.frames[{position}]"
            fields(
                frame,
                "frame_index timestamp_ms rgba_sha256 landmarks start_elapsed_ns end_elapsed_ns elapsed_ns bitmap_consumed",
                row,
            )
            for field in ROW_BINDINGS:
                bind(frame[field], target[field], f"{row}.{field}")
            bind(frame["bitmap_consumed"], True, f"{row}.bitmap_consumed")
            earliest_start = previous_end + (create if position == 0 else 0)
            start = integer(frame["start_elapsed_ns"], earliest_start, MAX_LONG, f"{row}.start_elapsed_ns")
            end = integer(frame["end_elapsed_ns"], start + 1, MAX_LONG, f"{row}.end_elapsed_ns")
            bind(frame["elapsed_ns"], end - start, f"{row}.elapsed_ns")
            validate_hands(frame["landmarks"], "", f"{row}.landmarks")
            previous_end = end


def distribution(values):
    ordered = sorted(values)
    count = len(ordered)
    if count == 0:
        return {"count": 0, "min": None, "max": None, "mean": None, "p95": None}
    # Nearest-rank p95.
    return {
        "count": count,
        "min": ordered[0],
        "max": ordered[-1],
        "mean": mean(ordered),
        "p95": ordered[(95 * count + 99) // 100 - 1],
    }


def compare_frames(left, right, limit):
    errors = []
    mismatches = []
    above_limit = 0
    decimal_limit = Decimal(str(limit))
    for left_frame, right_frame in zip(left, right):
        for slot in SLOTS:
            left_hand = left_frame["landmarks"][slot]
            right_hand = right_frame["landmarks"][slot]
            if (left_hand is None) != (right_hand is None):
                mismatches.append({
                    "frame_index": left_frame["frame_index"],
                    "slot": slot,
                    "first_present": left_hand is not None,
                    "second_present": right_hand is not None,
                })
            if left_hand is None or right_hand is None:
                continue
            for left_point, right_point in zip(left_hand, right_hand):
                for a, b in zip(left_point, right_point):
                    # Preserve the JSON values at the limit, including decimal
                    # differences that a binary float would round across it.
                    error = (Decimal(a) - Decimal(b)).copy_abs()
                    above_limit += error > decimal_limit
                    numeric_error = float(error)
                    require(
                        math.isfinite(numeric_error) and (numeric_error > 0 or not error),
                        "coordinate difference exceeds finite numeric range",
                    )
                    errors.append(numeric_error)
    stats = distribution(errors)
    return {
        "coordinate_limit": limit,
        "missingness_agreement": not mismatches,
        "missingness_mismatches": mismatches,
        "absolute_coordinate_error": stats,
        "above_coordinate_limit": above_limit,
        "agreement": bool(errors) and not mismatches and not above_limit,
    }


def compare(fixture, reference, android, manifest_hash, reference_hash, android_hash):
    validate_fixture(fixture)
    validate_reference(reference, fixture, manifest_hash)
    validate_android(android, fixture, manifest_hash)
    runs = {run["name"]: run["frames"] for run in android["runs"]}
    # A rounded decimal difference must never cross below the frozen limit.
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_UP
        deltas = {
            f"reference_vs_{name}": compare_frames(reference["frames"][:len(frames)], frames, CROSS_PLATFORM_LIMIT)
            for name, frames in runs.items()
        }
        for name in ("interrupted", "recovered"):
            frames = runs[name]
            deltas[f"initial_vs_{name}"] = compare_frames(runs["initial"][:len(frames)], frames, REPLAY_LIMIT)

    outcomes = list(deltas.values())
    if any(not result["missingness_agreement"] or result["above_coordinate_limit"] for result in outcomes):
        status = "OUT_OF_TOLERANCE"
    elif not any(result["absolute_coordinate_error"]["count"] for result in outcomes):
        status = "INCONCLUSIVE_NO_HAND_OBSERVATIONS"
    else:
        status = "AGREEMENT"

    timings = {}
    for run in android["runs"]:
        calls = [frame["elapsed_ns"] for frame in run["frames"]]
        timings[run["name"]] = {
            "detector_create": run["detector_create_elapsed_ns"],
            "cold_call": calls[0],
            "all_calls": distribution(calls),
            "after_first_call": distribution(calls[1:]),
        }
    return {
        "type": "holistic_host_parity_comparison",
        "schema_version": 1,
        "status": status,
        "fixture_manifest_sha256": manifest_hash,
        "reference_sha256": reference_hash,
        "android_sha256": android_hash,
        "source_clip": fixture["source_clip"],
        "source_model": fixture["source_model"],
        "frames_sha256": fixture["frames_file"]["sha256"],
        "frame_count": fixture["frame_count"],
        "android_run_id": android["run_id"],
        "input_mode": "canonical_rgba",
        "camera_path_tested": False,
        "recognition_accuracy_measured": False,
        "asl_accuracy_measured": False,
        "coordinate_deltas": deltas,
        "android_timing_ns": timings,
        "android_cold_timing_across_runs_ns": {
            "detector_create": distribution([run["detector_create"] for run in timings.values()]),
            "first_call": distribution([run["cold_call"] for run in timings.values()]),
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("fixture", "reference", "android"):
        parser.add_argument(f"--{name}", type=Path, required=True)
        parser.add_argument(f"--{name}-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        fixture = load_bound_json(args.fixture, args.fixture_sha256, MAX_JSON_BYTES, "fixture")
        reference = load_bound_json(args.reference, args.reference_sha256, MAX_JSON_BYTES, "reference")
        android = load_bound_json(args.android, args.android_sha256, MAX_ANDROID_BYTES, "android")
        report = compare(fixture, reference, android, args.fixture_sha256, args.reference_sha256, args.android_sha256)
        data = json.dumps(report, indent=2, allow_nan=False).encode("utf-8") + b"\n"
        with args.output.open("xb") as output:
            output.write(data)
    except (OSError, ValueError, ArithmeticError) as exc:
        print(f"Parity input or report error: {exc}", file=sys.stderr)
        return 2
    if report["status"] != "AGREEMENT":
        print(f"Parity result: {report['status']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
