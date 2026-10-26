#!/usr/bin/env python3
"""Exercise the real local Pocket TTS C ABI with pinned Alba assets and fixed text.

This is a component probe, not Android or listening acceptance. The controller
never loads the shared library; a bounded child contains all native calls.
"""
from __future__ import annotations

import argparse
import array
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import wave

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "pocket_tts_audition", Path(__file__).with_name("pocket-tts-audition.py")
)
assert _spec and _spec.loader
audition = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audition)

OK, CANCELLED, INVALID, ASSETS, SINK, UNKNOWN, BUSY = 0, 1, 2, 3, 5, 7, 8
SAMPLE_RATE = 24_000
MAX_SAMPLES = SAMPLE_RATE * 30
MAX_CHUNK = 1920
SINK_FN = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_uint64,
                           ctypes.POINTER(ctypes.c_int16), ctypes.c_size_t,
                           ctypes.c_void_p)


class ProbeError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ProbeError(message)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def prepare_flat_assets(nested: Path, output: Path) -> Path:
    """Copy only verified source bytes into the runtime's required flat layout."""
    files = audition.verify_assets(nested, audition._manifest())
    flat = output / "component-assets"
    require(not flat.exists(), "Private flat asset directory already exists")
    flat.mkdir(parents=True)
    mapping = {
        "model.safetensors": "languages/english_18m_2026-10/model.safetensors",
        "tokenizer.model": "languages/english_18m_2026-10/tokenizer.model",
        "alba.safetensors": "languages/english_18m_2026-10/embeddings/alba.safetensors",
    }
    try:
        for name, key in mapping.items():
            dest = flat / name
            with files[key].open("rb") as source, dest.open("xb") as target:
                shutil.copyfileobj(source, target, 1024 * 1024)
            require(digest(dest) == digest(files[key]), f"Flat copy changed {name}")
            dest.chmod(0o444)
        flat.chmod(0o555)
    except BaseException:
        shutil.rmtree(flat)
        raise
    return flat


class NativeABI:
    def __init__(self, library: Path):
        lib = ctypes.CDLL(str(library.resolve(strict=True)))
        lib.sts_pocket_create.argtypes = [ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
                                          ctypes.POINTER(ctypes.c_uint64)]
        lib.sts_pocket_create.restype = ctypes.c_int
        lib.sts_pocket_synthesize.argtypes = [ctypes.c_uint64, ctypes.POINTER(ctypes.c_uint8),
                                              ctypes.c_size_t, SINK_FN, ctypes.c_void_p]
        lib.sts_pocket_synthesize.restype = ctypes.c_int
        lib.sts_pocket_cancel.argtypes = [ctypes.c_uint64]
        lib.sts_pocket_cancel.restype = ctypes.c_int
        lib.sts_pocket_close.argtypes = [ctypes.c_uint64]
        lib.sts_pocket_close.restype = ctypes.c_int
        self.lib = lib

    def create(self, root: Path) -> int:
        data = os.fsencode(str(root.resolve(strict=True)))
        buf = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
        request_id = ctypes.c_uint64()
        status = self.lib.sts_pocket_create(buf, len(data), ctypes.byref(request_id))
        require(status == OK and request_id.value != 0, f"create status {status}")
        return request_id.value

    def synth(self, request_id: int, data: bytes, callback: SINK_FN) -> int:
        buf = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
        return self.lib.sts_pocket_synthesize(request_id, buf, len(data), callback, None)

    def cancel(self, request_id: int) -> int:
        return self.lib.sts_pocket_cancel(request_id)

    def close(self, request_id: int) -> int:
        return self.lib.sts_pocket_close(request_id)


class Capture:
    def __init__(self, request_id: int, action: str = "accept", abi=None):
        self.request_id = request_id
        self.action = action
        self.abi = abi
        self.thread = threading.get_ident()
        self.started = time.monotonic()
        self.first_chunk_seconds = None
        self.calls = 0
        self.samples = array.array("h")
        self.errors: list[str] = []
        self.action_status = None
        self.callback = SINK_FN(self._receive)

    def _receive(self, request_id, samples, count, _user) -> bool:
        try:
            require(threading.get_ident() == self.thread, "Callback used a different OS thread")
            require(request_id == self.request_id, "Callback request ID mismatch")
            require(bool(samples) and 1 <= count <= MAX_CHUNK, "Invalid PCM chunk")
            require(len(self.samples) + count <= MAX_SAMPLES, "PCM exceeds 30 seconds")
            self.calls += 1
            if self.first_chunk_seconds is None:
                self.first_chunk_seconds = round(time.monotonic() - self.started, 3)
            self.samples.extend(samples[index] for index in range(count))
            if self.calls == 1 and self.action == "cancel":
                self.action_status = self.abi.cancel(self.request_id)
                require(self.action_status == OK, "Cancel inside callback failed")
            elif self.calls == 1 and self.action == "close":
                self.action_status = self.abi.close(self.request_id)
                require(self.action_status == OK, "Close inside callback failed")
            elif self.calls == 1 and self.action == "reentrant":
                self.action_status = self.abi.synth(self.request_id, b"Hello.",
                                                    SINK_FN(lambda *_args: False))
                require(self.action_status == BUSY, "Reentrant synthesis was not busy")
            return self.action != "reject"
        except BaseException as exc:  # no Python exception may cross the C callback boundary
            self.errors.append(f"{type(exc).__name__}: {exc}")
            return False

    def assert_clean(self) -> None:
        require(not self.errors, "; ".join(self.errors))


def stats_and_wav(capture: Capture, path: Path) -> dict:
    capture.assert_clean()
    require(capture.calls > 0 and len(capture.samples) > 0, "No PCM returned")
    values = capture.samples
    peak = max(abs(int(sample)) for sample in values)
    require(peak >= 33 and peak < 32767, "PCM is silent or clipped")
    clipped = sum(abs(int(sample)) >= 32767 for sample in values)
    require(clipped == 0, "PCM contains clipped samples")
    rms = (sum(int(sample) ** 2 for sample in values) / len(values)) ** 0.5 / 32768
    require(rms >= 0.001, "PCM RMS is silent")
    if sys.byteorder != "little":
        values.byteswap()
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(values.tobytes())
    return {"file": path.name, "sha256": digest(path), "samples": len(values),
            "duration_seconds": round(len(values) / SAMPLE_RATE, 4),
            "peak": round(peak / 32768, 6), "rms": round(rms, 6),
            "chunks": capture.calls, "time_to_first_chunk_seconds": capture.first_chunk_seconds}


def checked_synth(abi, request_id: int, payload: bytes, expected: int,
                  action: str = "accept", require_audio: bool = False) -> Capture:
    capture = Capture(request_id, action, abi)
    capture.started = time.monotonic()
    status = abi.synth(request_id, payload, capture.callback)
    capture.total_seconds = round(time.monotonic() - capture.started, 3)
    capture.assert_clean()
    require(status == expected, f"synthesize status {status}, expected {expected}")
    require((capture.calls > 0) == require_audio,
            "Unexpected callback count for status")
    if action in ("cancel", "close", "reject"):
        require(capture.calls == 1, "Synthesis continued after first callback stopped it")
    return capture


def run_probe(abi, flat: Path, output: Path) -> dict:
    empty = SINK_FN(lambda *_args: False)
    request_id = abi.create(flat)
    try:
        # Validation must precede any PCM callback. Unicode unknown reaches token preflight.
        for label, data in (("invalid UTF-8", b"\xff"), ("NUL", b"a\x00b"),
                            ("control", b"a\nb"), ("too long", b"a" * 513)):
            checked_synth(abi, request_id, data, INVALID)
        checked_synth(abi, request_id, "Hello \U0010ffff.".encode(), INVALID)

        outputs = []
        for index, (stem, utterance) in enumerate(audition.UTTERANCES[:4]):
            capture = checked_synth(abi, request_id, utterance.encode("utf-8"), OK,
                                    action="reentrant" if index == 0 else "accept",
                                    require_audio=True)
            record = stats_and_wav(capture, output / f"{stem}.wav")
            record.update({"text": utterance, "synthesis_seconds": capture.total_seconds})
            outputs.append(record)
    finally:
        require(abi.close(request_id) == OK, "Initial request close failed")

    stale = request_id
    require(abi.cancel(stale) == UNKNOWN, "Stale cancellation did not report unknown request")
    checked_synth(abi, stale, b"Hello.", UNKNOWN)
    require(abi.close(stale) == OK, "Close was not idempotent")

    missing_root = output / "wrong-assets"
    missing_root.mkdir(exist_ok=False)
    wrong = abi.create(missing_root)
    try:
        checked_synth(abi, wrong, b"Hello.", ASSETS)
    finally:
        require(abi.close(wrong) == OK, "Wrong-assets request close failed")

    pre = abi.create(flat)
    require(abi.cancel(pre) == OK, "Pre-synthesis cancellation failed")
    checked_synth(abi, pre, b"Hello.", CANCELLED)
    require(abi.close(pre) == OK, "Cancelled request close failed")

    for action, expected in (("cancel", CANCELLED), ("close", CANCELLED),
                             ("reject", SINK)):
        request_id = abi.create(flat)
        try:
            checked_synth(abi, request_id, b"Hello, I can speak for you.", expected,
                          action=action, require_audio=True)
        finally:
            require(abi.close(request_id) == OK, f"{action} request close failed")

    # Proves native state can recover after aborts using a new ID and real PCM.
    recovery = abi.create(flat)
    try:
        stem, utterance = audition.UTTERANCES[4]
        capture = checked_synth(abi, recovery, utterance.encode("utf-8"), OK,
                                require_audio=True)
        record = stats_and_wav(capture, output / f"{stem}.wav")
        record.update({"text": utterance, "synthesis_seconds": capture.total_seconds})
        outputs.append(record)
    finally:
        require(abi.close(recovery) == OK, "Recovery request close failed")
    return {"status": "native_component_probe_passed", "runtime": "real_C_ABI",
            "sample_rate": SAMPLE_RATE, "channels": 1,
            "pcm_format": "signed_16_bit_little_endian", "outputs": outputs,
            "negative_cases": ["invalid_utf8", "nul", "control", "too_long",
                               "unknown_token", "wrong_asset_root", "cancel_before_synthesis",
                               "cancel_in_callback", "close_in_callback", "sink_rejection",
                               "reentrant_busy", "stale_request",
                               "recovery_new_request"],
            "model_revision": audition._manifest()["model"]["revision"],
            "voice": "alba"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", required=True, type=Path)
    parser.add_argument("--asset-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--_child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args._child:
        flat = output / "component-assets"
        result = run_probe(NativeABI(args.library), flat, output)
        result["library_sha256"] = digest(args.library)
        result["asset_sha256"] = {
            item["path"]: item["sha256"]
            for item in audition._manifest()["model"]["assets"]
        }
        (output / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                                             encoding="utf-8")
        return
    require(not any(output.iterdir()), "Output directory must be empty")
    prepare_flat_assets(args.asset_dir, output)
    env = os.environ.copy()
    env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                "CUDA_VISIBLE_DEVICES": "", "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2"})
    command = [sys.executable, str(Path(__file__).resolve()), "--_child", "--library",
               str(args.library.resolve(strict=True)), "--asset-dir", str(args.asset_dir),
               "--output-dir", str(output)]
    subprocess.run(command, env=env, timeout=1020, check=True)
    require((output / "result.json").is_file(), "Child produced no success result")
    print(output / "result.json")


if __name__ == "__main__":
    main()
