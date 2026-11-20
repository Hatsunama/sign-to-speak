"""Copy completed artifacts from a local FSboard v4 pair to private storage."""

from hashlib import sha256
import json
import os
from pathlib import Path
import stat


MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
FILE_LIMITS = {
    "adapter_config.json": MAX_JSON_BYTES,
    "adapter_model.safetensors": 8 * 1024 * 1024,
    "optimizer.pt": 16 * 1024 * 1024,
    "README.md": 1024 * 1024,
}
ADAPTER_FILES = ("adapter_config.json", "adapter_model.safetensors")
FINAL_STEP = 192
CHECKPOINT_STEPS = (*range(10, FINAL_STEP, 10), FINAL_STEP)
CHUNK_BYTES = 1024 * 1024


class CaptureError(RuntimeError):
    """A paired artifact could not be validated or captured."""


class _Incomplete(CaptureError):
    """The child may still be writing this artifact."""


def _path_info(path, check_type):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (not check_type(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise CaptureError(f"Unexpected file type for {path}")
    return info


def _directory(path, create=False):
    if path.parent != path and not _directory(path.parent, create):
        return False
    if _path_info(path, stat.S_ISDIR) is None:
        if not create:
            return False
        path.mkdir(exist_ok=True)
        if _path_info(path, stat.S_ISDIR) is None:
            raise CaptureError(f"Directory disappeared during capture: {path}")
    return True


def _file_info(path, limit):
    info = _path_info(path, stat.S_ISREG)
    if info is not None and info.st_size > limit:
        raise CaptureError(f"Artifact exceeds its size limit: {path}")
    return info


def _fingerprint(path, limit):
    if _file_info(path, limit) is None:
        raise CaptureError(f"Missing artifact: {path}")
    digest = sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK_BYTES):
            size += len(chunk)
            if size > limit:
                raise CaptureError(f"Artifact exceeds its size limit: {path}")
            digest.update(chunk)
    return digest.hexdigest(), size


def _read_json(path):
    if _file_info(path, MAX_JSON_BYTES) is None:
        return None
    with path.open("rb") as stream:
        payload = stream.read(MAX_JSON_BYTES + 1)
    if len(payload) > MAX_JSON_BYTES:
        raise CaptureError(f"Artifact exceeds its size limit: {path}")
    try:
        document = json.loads(payload)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise _Incomplete(f"Incomplete or invalid JSON: {path}") from error
    # Both writers append a newline after the JSON object.
    if not payload.endswith(b"\n") or not isinstance(document, dict):
        raise _Incomplete(f"Incomplete or invalid JSON: {path}")
    return document, (sha256(payload).hexdigest(), len(payload))


def _verify_copied_entry(source, destination, relative, expected):
    for root in (source, destination):
        path = root / relative
        if not _directory(path.parent) or _fingerprint(path, expected[1]) != expected:
            raise CaptureError(f"Captured artifact changed: {path}")


def _save_entries(source, destination, entries, copied):
    pending = {relative: expected for relative, expected in entries.items() if relative not in copied}
    total_size = sum(size for _, size in copied.values()) + sum(size for _, size in pending.values())
    if total_size > MAX_TOTAL_BYTES:
        raise CaptureError("Paired output exceeds the capture size limit")
    for relative, expected in entries.items():
        if relative in copied:
            if copied[relative] != expected:
                raise CaptureError(f"Captured artifact changed: {source / relative}")
            _verify_copied_entry(source, destination, relative, expected)
            continue
        target = destination / relative
        _directory(target.parent, create=True)
        if _path_info(target, stat.S_ISREG) is not None:
            raise CaptureError(f"Artifact already exists: {target}")

    for relative, expected in pending.items():
        target = destination / relative
        created = False
        try:
            with (source / relative).open("rb") as original, target.open("xb") as saved:
                created = True
                remaining = expected[1]
                while remaining and (chunk := original.read(min(CHUNK_BYTES, remaining))):
                    saved.write(chunk)
                    remaining -= len(chunk)
                if remaining or original.read(1):
                    raise CaptureError(f"Artifact changed size during capture: {relative}")
            if _fingerprint(target, expected[1]) != expected:
                raise CaptureError(f"Artifact hash mismatch after capture: {relative}")
        except (OSError, CaptureError) as error:
            if created:
                try:
                    target.unlink()
                except OSError as cleanup_error:
                    error.add_note(f"Could not remove partial capture {target}: {cleanup_error}")
            raise
        copied[relative] = expected


def _capture_json(source, destination, relative, copied, step=None):
    if relative in copied:
        return
    result = _read_json(source / relative)
    if result is None:
        return
    document, expected = result
    if step is not None and (type(document.get("optimizer_steps")) is not int or document["optimizer_steps"] != step):
        raise CaptureError(f"Wrong optimizer step in {relative}")
    _save_entries(source, destination, {relative: expected}, copied)


def _capture_model(source, destination, directory, copied, step=None):
    receipt = directory / "receipt.json"
    if receipt in copied or not _directory(source / directory):
        return

    names = ADAPTER_FILES if step is None else (*ADAPTER_FILES, "optimizer.pt")
    files = {
        name: _file_info(source / directory / name, FILE_LIMITS[name])
        for name in (*names, "README.md")
    }
    result = _read_json(source / receipt)
    if result is None or any(files[name] is None for name in names):
        raise _Incomplete(f"Incomplete adapter output: {directory}")

    document, expected = result
    if step is not None and (type(document.get("optimizer_steps")) is not int or document["optimizer_steps"] != step):
        raise CaptureError(f"Wrong optimizer step in {receipt}")
    digests = {
        name: _fingerprint(source / directory / name, FILE_LIMITS[name])
        for name, info in files.items() if info is not None
    }
    # Use the same canonical input as paired_experiment.adapter_digest.
    adapter_hashes = {name: digests[name][0] for name in ADAPTER_FILES}
    if sha256(json.dumps(adapter_hashes, sort_keys=True).encode()).hexdigest() != document.get("adapter_sha256"):
        raise CaptureError(f"adapter hash mismatch: {directory}")
    if step is not None and digests["optimizer.pt"][0] != document.get("optimizer_sha256"):
        raise CaptureError(f"optimizer hash mismatch: {directory}")

    entries = {directory / name: digest for name, digest in digests.items()}
    entries[receipt] = expected
    _save_entries(source, destination, entries, copied)


def _validate_success(destination, success, copied):
    arms = success.get("arms")
    if success.get("status") != "completed_training_pair" or not isinstance(arms, dict) or set(arms) != {"A", "B"}:
        raise CaptureError("Invalid paired-training success receipt")

    def require(relative):
        if relative not in copied:
            raise CaptureError(f"Missing completed artifact: {relative}")
        return copied[relative]

    def saved_json(relative):
        expected = require(relative)
        result = _read_json(destination / relative)
        if result is None or result[1] != expected:
            raise CaptureError(f"Captured artifact changed: {destination / relative}")
        return result[0]

    require(Path("experiment.json"))
    require(Path("runtime_receipt.json"))
    schedule_hash, _ = require(Path("schedule.json"))
    if success.get("schedule_sha256") != schedule_hash:
        raise CaptureError("Success receipt schedule hash does not match captured schedule")

    initialization = Path("initialization")
    for name in ADAPTER_FILES:
        require(initialization / name)
    initial_receipt = saved_json(initialization / "receipt.json")
    if success.get("initialization_sha256") != initial_receipt["adapter_sha256"]:
        raise CaptureError("Success receipt initialization hash does not match captured adapter")

    for arm, claimed_summary in arms.items():
        arm_path = Path(arm)
        arm_initialization = saved_json(arm_path / "initialization.json")
        if arm_initialization.get("shared_adapter_sha256") != initial_receipt["adapter_sha256"]:
            raise CaptureError(f"Initialization hash for arm {arm} does not match captured adapter")
        for step in range(1, FINAL_STEP + 1):
            require(arm_path / "steps" / f"{step:03d}.json")
        checkpoint = arm_path / f"checkpoint-{FINAL_STEP:03d}"
        for name in (*ADAPTER_FILES, "optimizer.pt"):
            require(checkpoint / name)
        receipt_path = checkpoint / "receipt.json"
        receipt = saved_json(receipt_path)
        summary = saved_json(arm_path / "summary.json")
        if json.dumps(claimed_summary, sort_keys=True) != json.dumps(summary, sort_keys=True):
            raise CaptureError(f"Success receipt does not match captured summary for arm {arm}")
        if (
            type(summary.get("optimizer_steps")) is not int
            or summary["optimizer_steps"] != FINAL_STEP
            or summary.get("final_checkpoint") != checkpoint.as_posix()
            or summary.get("final_checkpoint_receipt_sha256") != copied[receipt_path][0]
            or summary.get("adapter_sha256") != receipt["adapter_sha256"]
        ):
            raise CaptureError(f"Captured summary does not match final checkpoint for arm {arm}")


def capture_completed(pair_root, destination, copied, strict=False):
    """Preserve completed files, keeping the same digest map between polls.

    The final pass checks previously saved bytes and publishes a child success
    receipt only after capture verification succeeds.
    """
    source = Path(os.path.abspath(pair_root))
    destination = Path(os.path.abspath(destination))
    if source == destination or source in destination.parents or destination in source.parents:
        raise CaptureError("Source and destination must be separate directories")
    if not _directory(destination):
        raise CaptureError(f"Missing capture destination: {destination}")
    if not _directory(source):
        if copied:
            raise CaptureError(f"Missing paired output directory: {source}")
        return

    errors = []

    def attempt(capture, relative, step=None):
        try:
            capture(source, destination, relative, copied, step)
        except (OSError, CaptureError) as error:
            if strict:
                errors.append(error)
            elif not isinstance(error, _Incomplete):
                raise

    for name in ("experiment.json", "runtime_receipt.json", "schedule.json", "failure.json"):
        attempt(_capture_json, Path(name))
    attempt(_capture_model, Path("initialization"))
    for arm in (Path("A"), Path("B")):
        if not _directory(source / arm):
            continue
        attempt(_capture_json, arm / "initialization.json")
        steps = arm / "steps"
        if _directory(source / steps):
            for step in range(1, FINAL_STEP + 1):
                attempt(_capture_json, steps / f"{step:03d}.json", step)
        for step in CHECKPOINT_STEPS:
            attempt(_capture_model, arm / f"checkpoint-{step:03d}", step)
        attempt(_capture_json, arm / "summary.json")

    if strict:
        for relative, expected in copied.items():
            _verify_copied_entry(source, destination, relative, expected)
        if errors:
            raise errors[0]
        success = Path("success.json")
        result = _read_json(source / success)
        if result is not None:
            if _path_info(source / "failure.json", stat.S_ISREG) is not None:
                raise CaptureError("Conflicting paired-training terminal receipts")
            document, expected = result
            _validate_success(destination, document, copied)
            if success not in copied:
                _save_entries(source, destination, {success: expected}, copied)

