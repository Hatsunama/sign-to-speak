"""Deterministic probe-contract tests. Fake ABI cases are not native proof."""
from __future__ import annotations

import ctypes
import importlib.util
from pathlib import Path
import tempfile
import unittest
import wave


SCRIPT = Path(__file__).with_name("pocket-tts-component-probe.py")
spec = importlib.util.spec_from_file_location("component_probe", SCRIPT)
assert spec and spec.loader
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class FakeABI:
    def __init__(self, *, status=probe.OK, pcm=(1000, -1000), callback_id=None,
                 chunk_count=None):
        self.status = status
        self.pcm = pcm
        self.callback_id = callback_id
        self.chunk_count = chunk_count
        self.active = False
        self.cancelled = False

    def synth(self, request_id, _payload, callback):
        if self.active:
            return probe.BUSY
        if self.status != probe.OK:
            return self.status
        values = (ctypes.c_int16 * len(self.pcm))(*self.pcm)
        self.active = True
        try:
            accepted = callback(self.callback_id if self.callback_id is not None else request_id,
                                values, self.chunk_count if self.chunk_count is not None
                                else len(self.pcm), None)
            if self.cancelled:
                return probe.CANCELLED
            return probe.OK if accepted else probe.SINK
        finally:
            self.active = False

    def cancel(self, _request_id):
        self.cancelled = True
        return probe.OK

    def close(self, _request_id):
        self.cancelled = True
        return probe.OK


class ProbeContractTests(unittest.TestCase):
    def test_valid_pcm_writes_actual_mono_24k_wav(self):
        fake = FakeABI(pcm=(1000, -1000, 500, -500))
        capture = probe.checked_synth(fake, 10, b"Hello.", probe.OK, require_audio=True)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.wav"
            record = probe.stats_and_wav(capture, path)
            self.assertEqual(record["samples"], 4)
            self.assertEqual(record["chunks"], 1)
            self.assertEqual(record["sha256"], probe.digest(path))
            with wave.open(str(path), "rb") as wav:
                self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()),
                                 (1, 2, 24_000))
                self.assertEqual(wav.getnframes(), 4)

    def test_invalid_status_must_not_emit_audio(self):
        fake = FakeABI(status=probe.INVALID)
        capture = probe.checked_synth(fake, 10, b"\xff", probe.INVALID)
        self.assertEqual(capture.calls, 0)

    def test_unsupported_text_cases_are_invalid_without_pcm(self):
        self.assertEqual([label for label, _ in probe.UNSUPPORTED_TEXT_CASES],
                         ["noncharacter", "CJK", "emoji", "zero-width"])
        for label, text in probe.UNSUPPORTED_TEXT_CASES:
            with self.subTest(label=label):
                capture = probe.checked_synth(FakeABI(status=probe.INVALID), 10,
                                              text.encode("utf-8"), probe.INVALID)
                self.assertEqual(capture.calls, 0)

    def test_callback_rejects_wrong_id_without_raising_through_ffi(self):
        fake = FakeABI(callback_id=11)
        with self.assertRaisesRegex(probe.ProbeError, "request ID mismatch"):
            probe.checked_synth(fake, 10, b"Hello.", probe.OK, require_audio=True)

    def test_callback_rejects_out_of_bounds_chunk(self):
        fake = FakeABI(chunk_count=probe.MAX_CHUNK + 1)
        with self.assertRaisesRegex(probe.ProbeError, "Invalid PCM chunk"):
            probe.checked_synth(fake, 10, b"Hello.", probe.OK, require_audio=True)

    def test_silent_and_clipped_pcm_are_rejected(self):
        for samples, reason in (((0, 0), "silent"), ((32767, 1000), "clipped")):
            with self.subTest(samples=samples), tempfile.TemporaryDirectory() as temp:
                capture = probe.checked_synth(FakeABI(pcm=samples), 10, b"Hello.",
                                              probe.OK, require_audio=True)
                with self.assertRaisesRegex(probe.ProbeError, reason):
                    probe.stats_and_wav(capture, Path(temp) / "invalid.wav")

    def test_expected_status_and_callback_count_are_enforced(self):
        with self.assertRaisesRegex(probe.ProbeError, "status"):
            probe.checked_synth(FakeABI(status=probe.INVALID), 10, b"Hello.", probe.OK)
        with self.assertRaisesRegex(probe.ProbeError, "callback count"):
            probe.checked_synth(FakeABI(status=probe.OK), 10, b"Hello.", probe.OK)

    def test_callback_abort_and_reentrancy_contract(self):
        for action, status in (("cancel", probe.CANCELLED),
                               ("close", probe.CANCELLED), ("reject", probe.SINK),
                               ("reentrant", probe.OK)):
            with self.subTest(action=action):
                capture = probe.checked_synth(FakeABI(), 10, b"Hello.", status,
                                              action=action, require_audio=True)
                self.assertEqual(capture.calls, 1)


if __name__ == "__main__":
    unittest.main()
