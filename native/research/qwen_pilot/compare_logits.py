# SPDX-License-Identifier: Apache-2.0

"""Compare a CPU reference against a forced replay of qwen-backend-probe.

The defaults are a fixed engineering guard for this probe, independent of
model accuracy: per row, raw sum of squared error / CPU sum of squares <=
1e-4, raw cosine >= 0.999, and exact top-1 agreement. Both vectors must have
nonzero finite norms. The probe's lowest-token-ID tie rule is used for top-1.

Usage: python compare_logits.py CPU_DIR REPLAY_DIR [--output NEW_REPORT.json]
Without --output, the JSON report goes to stdout. Exit codes are 0 for pass,
1 for a guard failure, and 2 for invalid input or an output error.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import sys


LLAMA_REVISION = "b1b770d90d94cad9e9ea5b993bdbc383a522fb28"
ROW_COUNT = 5
NORMALIZED_MSE_MAX = 1e-4
COSINE_MIN = 0.999
INPUT_FIELDS = (
    "model", "mmproj", "image", "image_sha256", "image_width", "image_height",
    "prompt", "formatted_prompt", "use_jinja", "n_vocab", "dtype", "byte_order",
    "n_ctx", "n_batch", "n_ubatch", "n_threads", "prefill_n_tokens", "prefill_n_past",
    "chunks", "image_chunk_count", "prefix_text_token_ids",
)
SOURCE_FIELDS = ("llama_revision", "ggml_commit", "ggml_version")


class ComparisonError(ValueError):
    """The capture does not match the probe output contract."""


def require(condition, message):
    if not condition:
        raise ComparisonError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_bytes(path, label):
    try:
        return path.read_bytes()
    except OSError:
        raise ComparisonError(f"{label}: cannot read artifact") from None


def unique_json_object(pairs):
    obj = dict(pairs)
    if len(obj) != len(pairs):
        raise ValueError("duplicate JSON object key")
    return obj


def is_token(token, n_vocab):
    return type(token) is int and 0 <= token < n_vocab


def validate_prefix(meta, role):
    chunks = meta.get("chunks")
    require(isinstance(chunks, list) and chunks, f"{role}: invalid chunks")
    for chunk in chunks:
        require(isinstance(chunk, dict) and chunk.get("type") in ("text", "image"),
                f"{role}: invalid chunk type")
        for field in ("n_tokens", "n_pos"):
            count = chunk.get(field)
            require(type(count) is int and count >= 0, f"{role}: invalid chunks {field}")
    require(chunks[-1]["type"] == "text" and chunks[-1]["n_tokens"] > 0,
            f"{role}: chunks must end in nonempty text")
    images = sum(chunk["type"] == "image" for chunk in chunks)
    require(images > 0 and meta["image_chunk_count"] == images, f"{role}: invalid image_chunk_count")
    require(sum(chunk["n_tokens"] for chunk in chunks) == meta["prefill_n_tokens"],
            f"{role}: chunks do not match prefill_n_tokens")
    require(sum(chunk["n_pos"] for chunk in chunks) == meta["prefill_n_past"],
            f"{role}: chunks do not match prefill_n_past")
    require(max(meta["prefill_n_tokens"], meta["prefill_n_past"]) + ROW_COUNT - 1 <= meta["n_ctx"],
            f"{role}: prefix and continuation exceed n_ctx")
    ids = meta.get("prefix_text_token_ids")
    text_count = sum(chunk["n_tokens"] for chunk in chunks if chunk["type"] == "text")
    require(isinstance(ids, list) and len(ids) == text_count
            and all(is_token(token, meta["n_vocab"]) for token in ids),
            f"{role}: invalid prefix_text_token_ids")


def validate_steps(meta, role):
    n_vocab = meta["n_vocab"]
    tokens = meta.get("continuation_tokens")
    require(isinstance(tokens, list) and len(tokens) == ROW_COUNT - 1
            and all(is_token(token, n_vocab) for token in tokens), f"{role}: invalid continuation_tokens")
    steps = meta.get("steps")
    require(isinstance(steps, list) and len(steps) == ROW_COUNT, f"{role}: expected exactly five steps")
    for index, step in enumerate(steps):
        label = f"{role} step {index}"
        require(isinstance(step, dict), f"{label}: invalid step metadata")
        for field, value in (("index", index), ("n_past", meta["prefill_n_past"] + index),
                             ("finite_count", n_vocab), ("nonfinite_count", 0)):
            require(type(step.get(field)) is int and step[field] == value, f"{label}: invalid {field}")
        require(step.get("file") == f"logits-{index}.f32", f"{label}: invalid row filename")
        shape = step.get("shape")
        require(isinstance(shape, list) and len(shape) == 1
                and type(shape[0]) is int and shape[0] == n_vocab, f"{label}: invalid shape")
        require(is_token(step.get("argmax"), n_vocab), f"{label}: invalid argmax")
        if index:
            decoded = step.get("decoded_token")
            require(is_token(decoded, n_vocab) and decoded == tokens[index - 1],
                    f"{label}: decoded_token differs from continuation_tokens")
        else:
            require("decoded_token" not in step, f"{label}: prefill must not have a decoded_token")


def read_capture(directory, role, forced):
    raw = read_bytes(directory / "metadata.json", f"{role} metadata.json")
    try:
        meta = json.loads(raw, object_pairs_hook=unique_json_object)
    except (ValueError, RecursionError):
        raise ComparisonError(f"{role}: invalid metadata.json") from None
    require(isinstance(meta, dict), f"{role}: invalid metadata.json object")
    require(type(meta.get("schema_version")) is int and meta["schema_version"] == 1,
            f"{role}: expected schema_version 1")
    require(meta.get("status") == "finite", f"{role}: expected finite status")
    require(meta.get("llama_revision") == LLAMA_REVISION, f"{role}: unexpected llama_revision")
    require(meta.get("forced") is forced, f"{role}: invalid forced flag")
    require(meta.get("dtype") == "float32" and meta.get("byte_order") == "little",
            f"{role}: expected little-endian float32")
    for field in ("model", "mmproj", "image", "prompt", "formatted_prompt", "ggml_commit", "ggml_version"):
        require(isinstance(meta.get(field), str) and meta[field], f"{role}: invalid {field}")
    image_hash = meta.get("image_sha256")
    require(isinstance(image_hash, str) and re.fullmatch(r"[0-9a-fA-F]{64}", image_hash),
            f"{role}: invalid image_sha256")
    for field in ("n_vocab", "n_ctx", "n_batch", "n_ubatch", "image_width", "image_height",
                  "prefill_n_tokens", "prefill_n_past", "image_chunk_count"):
        require(type(meta.get(field)) is int and meta[field] > 0, f"{role}: invalid {field}")
    require(type(meta.get("n_threads")) is int, f"{role}: invalid n_threads")
    require(type(meta.get("use_jinja")) is bool, f"{role}: invalid use_jinja")
    if not forced:
        require(type(meta.get("n_gpu_layers")) is int and meta["n_gpu_layers"] == 0
                and meta.get("mmproj_use_gpu") is False,
                "reference: expected zero GPU layers and CPU projector")
    validate_prefix(meta, role)
    validate_steps(meta, role)
    raw_tokens = read_bytes(directory / "tokens.txt", f"{role} tokens.txt")
    expected_tokens = "".join(f"{token}\n" for token in meta["continuation_tokens"]).encode("ascii")
    require(raw_tokens.replace(b"\r\n", b"\n") == expected_tokens,
            f"{role}: tokens.txt differs from continuation_tokens")
    return meta, {"metadata_sha256": sha256(raw), "tokens_sha256": sha256(raw_tokens)}


def read_logits(directory, step, n_vocab, role):
    label = f"{role} step {step['index']}"
    expected_size = n_vocab * 4
    try:
        with (directory / step["file"]).open("rb") as stream:
            require(stream.seek(0, 2) == expected_size, f"{label}: unexpected row size")
            stream.seek(0)
            raw = stream.read(expected_size + 1)
    except OSError:
        raise ComparisonError(f"{label}: cannot read row") from None
    require(len(raw) == expected_size, f"{label}: unexpected row size")
    values = struct.unpack(f"<{n_vocab}f", raw)
    require(all(map(math.isfinite, values)), f"{label}: nonfinite binary logits")
    norm_squared = math.fsum(value * value for value in values)
    require(math.isfinite(norm_squared) and norm_squared > 0, f"{label}: zero or nonfinite squared norm")
    argmax = max(range(n_vocab), key=values.__getitem__)
    require(argmax == step["argmax"], f"{label}: binary argmax differs from metadata")
    return values, norm_squared, argmax, sha256(raw)


def compare(reference_dir, replay_dir):
    """Return a JSON-ready report for two validated, aligned captures."""
    reference_dir, replay_dir = Path(reference_dir), Path(replay_dir)
    reference, reference_hashes = read_capture(reference_dir, "reference", forced=False)
    replay, replay_hashes = read_capture(replay_dir, "replay", forced=True)
    for field in (*SOURCE_FIELDS, *INPUT_FIELDS, "continuation_tokens"):
        require(reference[field] == replay[field], f"reference and replay differ in {field}")
    n_vocab = reference["n_vocab"]
    rows = []
    for index, (reference_step, replay_step) in enumerate(zip(reference["steps"], replay["steps"])):
        cpu, cpu_norm, cpu_top, cpu_hash = read_logits(reference_dir, reference_step, n_vocab, "reference")
        accelerated, replay_norm, replay_top, replay_hash = read_logits(replay_dir, replay_step, n_vocab, "replay")
        if index < ROW_COUNT - 1:
            require(reference["continuation_tokens"][index] == cpu_top,
                    f"reference step {index}: continuation token differs from binary argmax")
        differences = [abs(y - x) for x, y in zip(cpu, accelerated)]
        normalized_mse = math.fsum(delta * delta for delta in differences) / cpu_norm
        dot_product = math.fsum(x * y for x, y in zip(cpu, accelerated))
        cosine = dot_product / (math.sqrt(cpu_norm) * math.sqrt(replay_norm))
        runner_up = max((value for token, value in enumerate(cpu) if token != cpu_top), default=None)
        rows.append({
            "index": index,
            "n_past": reference_step["n_past"],
            "reference_logits_sha256": cpu_hash,
            "replay_logits_sha256": replay_hash,
            "normalized_mse": normalized_mse,
            "cosine_similarity": max(-1.0, min(1.0, cosine)),
            "reference_argmax": cpu_top,
            "replay_argmax": replay_top,
            "top1_agreement": cpu_top == replay_top,
            "cpu_margin": cpu[cpu_top] - runner_up if runner_up is not None else None,
            "max_absolute_difference": max(differences),
        })
    aggregate = {
        "rows_compared": len(rows),
        "logits_compared": len(rows) * n_vocab,
        "top1_agreement_rows": sum(row["top1_agreement"] for row in rows),
        "normalized_mse_pass_rows": sum(row["normalized_mse"] <= NORMALIZED_MSE_MAX for row in rows),
        "cosine_pass_rows": sum(row["cosine_similarity"] >= COSINE_MIN for row in rows),
        "max_normalized_mse": max(row["normalized_mse"] for row in rows),
        "min_cosine_similarity": min(row["cosine_similarity"] for row in rows),
        "max_absolute_difference": max(row["max_absolute_difference"] for row in rows),
    }
    passed = all(aggregate[field] == ROW_COUNT for field in (
        "top1_agreement_rows", "normalized_mse_pass_rows", "cosine_pass_rows",
    ))
    inputs = {field: reference[field] for field in (*INPUT_FIELDS, "continuation_tokens")}
    input_digest = sha256(json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode("ascii"))
    return {
        "schema_version": 1,
        "status": "pass" if passed else "fail",
        "source": {field: reference[field] for field in (*SOURCE_FIELDS, "image_sha256")},
        "input_metadata_sha256": input_digest,
        "n_vocab": n_vocab,
        "artifacts": {"reference": reference_hashes, "replay": replay_hashes},
        "engineering_guard": {"normalized_mse_max": NORMALIZED_MSE_MAX, "cosine_similarity_min": COSINE_MIN,
                              "top1_agreement_required_rows": ROW_COUNT},
        "aggregate": aggregate,
        "rows": rows,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("reference", type=Path, help="CPU capture directory")
    parser.add_argument("replay", type=Path, help="forced replay capture directory")
    parser.add_argument("--output", type=Path, help="new JSON report file (default: stdout)")
    args = parser.parse_args(argv)
    try:
        report = compare(args.reference, args.replay)
    except ComparisonError as error:
        report = {"schema_version": 1, "status": "invalid", "error": str(error)}
    encoded = json.dumps(report, indent=2, allow_nan=False) + "\n"
    try:
        if args.output is None:
            sys.stdout.write(encoded)
        else:
            with args.output.open("x", encoding="utf-8", newline="\n") as output:
                output.write(encoded)
    except OSError:
        print("Cannot write report. --output requires a fresh file in an existing directory.", file=sys.stderr)
        return 2
    return {"pass": 0, "fail": 1, "invalid": 2}[report["status"]]


if __name__ == "__main__":
    sys.exit(main())
