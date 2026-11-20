"""Bind frozen FSboard evaluation candidates to verified paired-training byte artifacts."""
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re


EXPERIMENT_ID = "sts-fsboard-whole-prompt-static-ablation-20261119-v4"
MODEL = {"id": "Qwen/Qwen3.5-0.8B", "revision": "2fc06364715b967f1860aea9cf38778875588b17"}
PAIR_PINS = {
    "source_manifest_sha256": "5b2df533e3d36fc29a7df820e4e9e069cbd1e739b48e44e69fb4db7c052e95aa",
    "admission_sha256": "780c78b8b3b9f43aa4919815394784f8b5d3cb1432574e87644f39ed4c03d075",
    "proposal_sha256": "6f04da3b218f664d8ab4097ab0212fdd135220f89dc8019e9b3b1fb04213e6d9",
    "protocol_sha256": "9e482b474b1250422e571179ee217a6bf2ad2a223ec28b8fc2f9a288b88a90f1",
    "previous_receipt_sha256": "5be4dd8a5d5225c8131eab855f521a83382a4da3f18804170e917ff381ebd8ac",
    "processor_receipt_sha256": "551c2e637dfe5a4132c537c3dd8398e0a58cc76750573b2882e30afa5a14db61",
    "prior_schedule_sha256": "6b1980c513f627f3b4bad9cec92c6cda951f0e1fe77fececeb0b319d3f8f27fe",
    "static_manifest_sha256": "55d6034693b3118c4e7fc87f6191effd831410d22d9cb96225c35eb7dc1b0d2e",
}
ARMS = ("A", "B")
FINAL_STEP = 192
ADAPTER_FILES = ("adapter_config.json", "adapter_model.safetensors")
JSON_LIMIT = 4 * 1024**2
WEIGHTS_LIMIT = 512 * 1024**2
OPTIMIZER_LIMIT = 1024**3
EMPTY_SHA256 = sha256(b"").hexdigest()


class BindingError(ValueError):
    pass


@dataclass(frozen=True)
class Candidate:
    checkpoint: Path
    adapter_sha256: str
    parameter_sha256: str
    checkpoint_receipt_sha256: str
    initialization_sha256: str


def require(condition, message):
    if not condition:
        raise BindingError(message)


def valid_digest(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _unique(pairs):
    document = {}
    for key, value in pairs:
        require(key not in document, "Duplicate JSON key")
        document[key] = value
    return document


def _reject_constant(value):
    raise BindingError("Non-finite JSON constant")


def read_object(payload, label):
    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_reject_constant)
    except (ValueError, RecursionError) as error:
        raise BindingError(f"Invalid JSON in {label}") from error
    require(type(document) is dict, f"Expected JSON object in {label}")
    return document


def _json_key(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


def _matches(document, expected, label):
    require(all(key in document and _json_key(document[key]) == _json_key(value)
                for key, value in expected.items()), f"Invalid {label}")


def _path(root, relative):
    path = root / relative
    require(path.resolve() == root.resolve() / relative and path.is_file() and not path.is_symlink(),
            f"Missing or non-regular captured artifact: {relative}")
    return path


def _read_bytes(path, limit):
    with path.open("rb") as stream:
        contents = stream.read(limit + 1)
    require(0 < len(contents) <= limit, f"Unexpected captured artifact size: {path.name}")
    return contents


def _read_pinned(root, relative, pin):
    require(valid_digest(pin), f"Missing trusted SHA-256 for {relative}")
    payload = _read_bytes(_path(root, relative), JSON_LIMIT)
    require(sha256(payload).hexdigest() == pin, f"SHA-256 mismatch for {relative}")
    return read_object(payload, relative), len(payload)


class _Capture:
    def __init__(self, root, entries):
        require(type(entries) is dict, "Missing strict capture index")
        self.root = root
        self.entries = entries

    def entry(self, relative):
        value = self.entries.get(relative)
        require(type(value) is dict and set(value) == {"bytes", "sha256"}
                and type(value["bytes"]) is int and value["bytes"] > 0 and valid_digest(value["sha256"]),
                f"Invalid or missing strict capture entry: {relative}")
        return value

    def document(self, relative):
        entry = self.entry(relative)
        contents, size = _read_pinned(self.root, relative, entry["sha256"])
        require(size == entry["bytes"], f"Captured JSON size mismatch: {relative}")
        return contents

    def binary(self, relative, limit, local_copy=None):
        entry = self.entry(relative)
        require(entry["bytes"] <= limit, f"Captured file exceeds its limit: {relative}")
        path = _path(self.root, relative)
        require(path.stat().st_size == entry["bytes"], f"Captured file size mismatch: {relative}")
        digest = sha256()
        size = 0
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024**2), b""):
                size += len(block)
                require(size <= entry["bytes"], f"Captured file grew during verification: {relative}")
                digest.update(block)
                if local_copy is not None:
                    local_copy.write(block)
        require(size == entry["bytes"] and digest.hexdigest() == entry["sha256"],
                f"Captured file bytes do not match: {relative}")
        return entry["sha256"]


def _adapter(capture, directory, destination=None):
    receipt = capture.document(f"{directory}/receipt.json")
    config = capture.document(f"{directory}/adapter_config.json")
    _matches(config, {"base_model_name_or_path": MODEL["id"], "revision": MODEL["revision"],
                      "peft_type": "LORA", "r": 8, "lora_alpha": 16, "lora_dropout": 0.0, "bias": "none"},
             f"{directory} adapter config")
    digests = {}
    if destination is not None:
        destination.mkdir(mode=0o700, parents=True)
    for filename in ADAPTER_FILES:
        limit = JSON_LIMIT if filename == ADAPTER_FILES[0] else WEIGHTS_LIMIT
        relative = f"{directory}/{filename}"
        if destination is None:
            digests[filename] = capture.binary(relative, limit)
        else:
            with (destination / filename).open("xb") as stream:
                digests[filename] = capture.binary(relative, limit, stream)
    adapter_sha256 = sha256(_json_key(digests).encode()).hexdigest()
    require(receipt.get("adapter_sha256") == adapter_sha256, f"Captured adapter mismatch: {directory}")
    parameter = receipt.get("parameter_sha256")
    require(valid_digest(parameter) and parameter != EMPTY_SHA256, f"Invalid parameter digest: {directory}")
    return receipt


def _verify_schedule(schedule, training_ids):
    require(schedule.keys() == set(ARMS), "Invalid pair schedule arms")
    video_orders = {}
    for arm in ARMS:
        steps = schedule[arm]
        require(type(steps) is list and len(steps) == FINAL_STEP, f"Invalid schedule length for arm {arm}")
        videos, static = [], []
        for step in steps:
            require(type(step) is dict and set(step) == {"video_ids", "static_id"}, f"Invalid schedule step for arm {arm}")
            ids, static_id = step["video_ids"], step["static_id"]
            require(type(ids) is list and len(ids) == 2 and all(type(item) is str and item for item in ids),
                    f"Invalid video microbatches for arm {arm}")
            videos.extend(ids)
            static.append(static_id)
        first, second = videos[:FINAL_STEP], videos[FINAL_STEP:]
        require(len(set(first)) == FINAL_STEP and Counter(first) == Counter(second)
                and (training_ids is None or set(first) == training_ids), f"Invalid shared video coverage for arm {arm}")
        if arm == "A":
            require(all(item is None for item in static), "Invalid static schedule for arm A")
        else:
            require(all(type(item) is str and item for item in static) and len(set(static[:80])) == 80
                    and static == (static[:80] * 3)[:FINAL_STEP], "Invalid static schedule for arm B")
        video_orders[arm] = videos
    require(video_orders["A"] == video_orders["B"], "Arms do not have identical video orders")


def verify_pair(pair_root, *, success_sha256, exit_sha256, control_plan_sha256, training_ids=None, local_adapters=None):
    """Verify terminal artifacts and optionally stage the same adapter bytes for local model loading."""
    root = Path(pair_root)
    artifacts = root / "artifacts"
    require(artifacts.is_dir() and not artifacts.is_symlink(), "Missing pair artifacts directory")
    require(valid_digest(control_plan_sha256), "Missing trusted control plan SHA-256")
    pins = {**PAIR_PINS, "control_plan_sha256": control_plan_sha256}

    exit_document, _ = _read_pinned(root, "child-exit.json", exit_sha256)
    _matches(exit_document, {
        "returncode": 0, "status": "completed_training_pair", "strict_capture_complete": True,
        "cancellation_requested": False, "orchestration_error_type": None, "fit_adapter_reused": False,
        "rejected_child_success": None,
    }, "pair exit")
    capture = _Capture(artifacts, exit_document.get("saved_artifacts"))
    for directory, name in ((artifacts, "failure.json"), (root, "rejected-child-success.json")):
        path = directory / name
        require(name not in capture.entries and not path.exists() and not path.is_symlink(),
                f"Conflicting pair failure evidence: {name}")
    require(capture.entry("success.json")["sha256"] == success_sha256, "Pair exit does not attest pinned success")
    success = capture.document("success.json")
    shared = {"schema_version": 1, "experiment_id": EXPERIMENT_ID, "model": MODEL, "pins": pins}
    _matches(success, {
        **shared, "status": "completed_training_pair", "processor_token_contract_verified": True,
        "training_started": True, "model_scoring_started": False,
    }, "pair success")
    require(type(success.get("arms")) is dict and success["arms"].keys() == set(ARMS), "Invalid pair success arms")
    _matches(capture.document("experiment.json"), shared, "pair experiment")
    capture.document("runtime_receipt.json")
    require(capture.entry("schedule.json")["sha256"] == success.get("schedule_sha256"), "Mismatched schedule reference")
    _verify_schedule(capture.document("schedule.json"), training_ids)

    initial = _adapter(capture, "initialization")
    require(success.get("initialization_sha256") == initial["adapter_sha256"], "Mismatched shared initialization")
    bindings = {}
    for arm in ARMS:
        _matches(capture.document(f"{arm}/initialization.json"), {
            "shared_adapter_sha256": initial["adapter_sha256"],
            "trainable_parameter_sha256": initial["parameter_sha256"],
        }, f"arm {arm} initialization")
        for step in range(1, FINAL_STEP + 1):
            capture.entry(f"{arm}/steps/{step:03d}.json")
        directory = f"{arm}/checkpoint-{FINAL_STEP}"
        summary = capture.document(f"{arm}/summary.json")
        require(_json_key(summary) == _json_key(success["arms"][arm]), f"Invalid arm {arm} summary reference")
        receipt_ref = capture.entry(f"{directory}/receipt.json")["sha256"]
        destination = None if local_adapters is None else Path(local_adapters) / arm
        final = _adapter(capture, directory, destination)
        optimizer_digest = capture.binary(f"{directory}/optimizer.pt", OPTIMIZER_LIMIT)
        _matches(final, {"optimizer_steps": FINAL_STEP, "optimizer_sha256": optimizer_digest},
                 f"arm {arm} optimizer checkpoint")
        _matches(summary, {
            "optimizer_steps": FINAL_STEP, "static_microbatches": 0 if arm == "A" else FINAL_STEP,
            "video_microbatches": 2 * FINAL_STEP, "final_checkpoint": directory,
            "final_checkpoint_receipt_sha256": receipt_ref, "adapter_sha256": final["adapter_sha256"],
            "parameter_sha256": final["parameter_sha256"],
        }, f"arm {arm} summary")
        require(final["adapter_sha256"] != initial["adapter_sha256"]
                and final["parameter_sha256"] != initial["parameter_sha256"], f"Arm {arm} reused initialization")
        bindings[arm] = Candidate(
            artifacts / directory if destination is None else destination,
            final["adapter_sha256"], final["parameter_sha256"], receipt_ref, initial["adapter_sha256"],
        )
    return bindings
