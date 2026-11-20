"""Run the offline Holistic worker and preserve its evidence on the job output mount."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


def _open_private(path: Path):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return os.fdopen(descriptor, "wb")


def run_worker(command, *, cwd, env, result_dir: Path, durable_dir: Path, timeout):
    """Return the worker outcome and persist it as worker-exit.json.

    The caller supplies a private output directory, an unused result path, and
    the complete child environment. Only an exited worker with returncode zero,
    intact capture, and a verified receipt may qualify.
    """
    marker = durable_dir / "worker-exit.json"
    if os.path.lexists(marker):
        raise FileExistsError(marker)

    outcome = {
        "status": "launch_failed",
        "returncode": None,
        "artifacts": {},
        "capture_errors": {},
    }
    started = time.monotonic()
    with _open_private(durable_dir / "worker.stdout") as stdout:
        try:
            if os.path.lexists(result_dir):
                raise FileExistsError(result_dir)
            with _open_private(durable_dir / "worker.stderr") as stderr:
                worker = subprocess.run(
                    command, cwd=cwd, env=env, timeout=timeout, close_fds=True,
                    stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                )
            outcome.update(status="exited", returncode=worker.returncode)
        except subprocess.TimeoutExpired:
            outcome["status"] = "timed_out"
        except OSError as error:
            outcome["error_type"] = type(error).__name__
    outcome["wall_seconds"] = time.monotonic() - started

    if outcome["status"] != "launch_failed":
        for name in ("receipt.json", "failure.json"):
            destination = durable_dir / f"worker-{name}"
            try:
                try:
                    payload = (result_dir / name).read_bytes()
                except FileNotFoundError:
                    continue
                with _open_private(destination) as stream:
                    stream.write(payload)
            except OSError as error:
                outcome["capture_errors"][destination.name] = type(error).__name__
                continue
            outcome["artifacts"][destination.name] = {
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }

    try:
        with _open_private(marker) as stream:
            stream.write((json.dumps(outcome, allow_nan=False, indent=2) + "\n").encode("utf-8"))
    except OSError as error:
        outcome["capture_errors"][marker.name] = type(error).__name__
    return outcome
