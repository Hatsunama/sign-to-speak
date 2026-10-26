#!/usr/bin/env python3
"""Behavior tests for the bounded official-Rust Pocket TTS probe."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).with_name("pocket-tts-native-probe.py")
spec = importlib.util.spec_from_file_location("native_probe", SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def wav(samples, *, rate=24000, channels=1, format_code=1, bits=16):
    if format_code == 1 and bits == 16:
        data = struct.pack("<" + "h" * len(samples), *samples)
    else:
        data = struct.pack("<" + "f" * len(samples), *samples)
    width = bits // 8
    fmt = struct.pack("<HHIIHH", format_code, channels, rate, rate * channels * width, channels * width, bits)
    return b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", len(data)) + data


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_wave_accepts_bounded_pcm_and_float_without_gain_change(self):
        for payload in (wav([8192, -8192]), wav([0.25, -0.25], format_code=3, bits=32)):
            p = self.root / "voice.wav"
            p.write_bytes(payload)
            pcm, stats = probe.inspect_wav(p)
            self.assertEqual(len(pcm), 4)
            self.assertAlmostEqual(stats["peak"], 0.25, places=3)
            self.assertEqual(stats["samples"], 2)

    def test_wave_rejects_malformed_stereo_silent_clipped_nonfinite_and_long(self):
        bad = [b"not wav", wav([0, 0]), wav([32767]), wav([1000, 1000], channels=2),
               wav([1000], rate=22050), wav([float("nan")], format_code=3, bits=32),
               wav([1.0], format_code=3, bits=32), wav([1000] * (24000 * 17))]
        for payload in bad:
            with self.subTest(size=len(payload)):
                p = self.root / "bad.wav"
                p.write_bytes(payload)
                with self.assertRaises(ValueError):
                    probe.inspect_wav(p)

    def test_config_rewrites_only_expected_refs_and_rejects_remote_remainder(self):
        source = ("weights_path: hf://cloning\nweights_path_without_voice_cloning: hf://ungated\n"
                  "flow_lm:\n  lookup_table:\n    tokenizer_path: hf://tokenizer\n"
                  "mimi:\n  sample_rate: 24000\n  channels: 1\n  n_bins: 4000\n")
        output = probe.render_local_config(source, Path("/tmp/model.safetensors"), Path("/tmp/tokenizer.model"))
        self.assertIn("weights_path: /tmp/model.safetensors", output)
        self.assertIn("weights_path_without_voice_cloning: null", output)
        self.assertNotIn("hf://", output)
        with self.assertRaisesRegex(ValueError, "remote"):
            probe.render_local_config(source + "other: https://unreviewed\n", Path("/tmp/w"), Path("/tmp/t"))
        with self.assertRaises(ValueError):
            probe.render_local_config(source.replace("24000", "22050"), Path("/tmp/w"), Path("/tmp/t"))

    def test_failure_and_timeout_stop_before_result_and_do_not_accept_stale_wave(self):
        out = self.root / "out"
        out.mkdir()
        (out / "01-introduction.wav").write_bytes(wav([8192]))
        fake = mock.Mock(returncode=1, stdout="", stderr="bad weights")
        with mock.patch.object(probe.subprocess, "run", return_value=fake):
            with self.assertRaisesRegex(RuntimeError, "bad weights"):
                probe.run_generation(Path("/bin/probe"), Path("/tmp/c.yaml"), Path("/tmp/a.safetensors"), out, [("01-introduction", "Hi.")])
        self.assertFalse((out / "01-introduction.wav").exists())
        self.assertFalse((out / "result.json").exists())
        with mock.patch.object(probe.subprocess, "run", side_effect=probe.subprocess.TimeoutExpired("probe", 1)):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                probe.run_generation(Path("/bin/probe"), Path("/tmp/c.yaml"), Path("/tmp/a.safetensors"), out, [("01-introduction", "Hi.")], timeout=1)
        with mock.patch.object(probe.subprocess, "run", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                probe.run_generation(Path("/bin/probe"), Path("/tmp/c.yaml"), Path("/tmp/a.safetensors"), out, [("01-introduction", "Hi.")])
        self.assertFalse((out / "01-introduction.wav").exists())

    def test_success_invokes_exact_local_cpu_command_and_reports_outputs(self):
        out = self.root / "out"
        def command(args, **kwargs):
            self.assertEqual(args[1:3], ["generate", "--config"])
            self.assertIn("--device", args)
            self.assertEqual(args[args.index("--device") + 1], "cpu")
            self.assertEqual(args[args.index("--voice") + 1], str(Path("/tmp/alba.safetensors")))
            self.assertEqual(args[args.index("--max-tokens") + 1], "160")
            self.assertEqual(kwargs["env"]["POCKET_TTS_SEED"], "42")
            Path(args[args.index("--output-path") + 1]).write_bytes(wav([8192, -8192]))
            return mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(probe.subprocess, "run", side_effect=command):
            outputs = probe.run_generation(Path("/bin/probe"), Path("/tmp/c.yaml"), Path("/tmp/alba.safetensors"), out, [("01-introduction", "Hi.")])
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0]["text"], "Hi.")
        self.assertEqual(outputs[0]["sha256"], hashlib.sha256((out / "01-introduction.wav").read_bytes()).hexdigest())
        self.assertIn("cli_startup_model_load_and_generation_seconds", outputs[0])
        self.assertNotIn("synthesis_seconds", outputs[0])

    def test_token_ids_require_actual_diagnostic_and_exact_python_match(self):
        model = self.root / "tokenizer.model"
        model.write_bytes(b"fixture")
        diagnostic = self.root / "sign-token-probe"
        diagnostic.write_bytes(b"binary")
        expected = [[1, 2] for _ in probe.TOKEN_PARITY_TEXTS]
        fake_sp = mock.Mock()
        fake_sp.encode.side_effect = lambda text, out_type: [1, 2]
        fake_module = mock.Mock(SentencePieceProcessor=mock.Mock(return_value=fake_sp))
        with mock.patch.dict('sys.modules', {'sentencepiece': fake_module}):
            with mock.patch.object(probe.subprocess, 'run', return_value=mock.Mock(returncode=0, stdout=json.dumps(expected), stderr='')):
                record = probe.compare_token_ids(model, diagnostic)
            self.assertEqual(record['status'], 'exact_raw_sentencepiece_ids_match')
            self.assertEqual(len(record['cases']), len(expected))
            with mock.patch.object(probe.subprocess, 'run', return_value=mock.Mock(returncode=0, stdout=json.dumps([[9]] + expected[1:]), stderr='')):
                with self.assertRaisesRegex(ValueError, 'Token ID mismatch'):
                    probe.compare_token_ids(model, diagnostic)
            with mock.patch.object(probe.subprocess, 'run', return_value=mock.Mock(returncode=0, stdout='', stderr='')):
                with self.assertRaisesRegex(ValueError, 'diagnostic output'):
                    probe.compare_token_ids(model, diagnostic)
        with self.assertRaisesRegex(ValueError, 'diagnostic'):
            probe.compare_token_ids(model, self.root / 'missing')

    def test_extracted_rust_source_must_match_pinned_archive(self):
        source = self.root / "source"
        cargo = source / "rust"
        cargo.mkdir(parents=True)
        files = {"rust/Cargo.toml": probe.PTTS_FORK_COMMIT.encode(),
                 "rust/Cargo.lock": probe.PTTS_FORK_COMMIT.encode(),
                 "rust/.cargo/config.toml": b'rustflags = ["-C", "target-cpu=x86-64-v3"]',
                 "rust/src/main.rs": b"fn main() {}\n"}
        archive = self.root / "source.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            for name, data in files.items():
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                info = tarfile.TarInfo(f"pocket-tts-{probe.RUST_COMMIT}/{name}")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        manifest = {"source": {"archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}}
        with mock.patch.object(probe.reference, "_source_checkout"):
            identity = probe.rust_source_identity(source, archive, manifest)
            self.assertEqual(identity["ptts_fork_commit"], probe.PTTS_FORK_COMMIT)
            diagnostic = source / "rust/src/bin/sign_token_probe.rs"
            diagnostic.parent.mkdir(parents=True)
            diagnostic.write_bytes(probe.DIAGNOSTIC_SOURCE.read_bytes())
            probe.rust_source_identity(source, archive, manifest)
            diagnostic.write_text("tampered")
            with self.assertRaisesRegex(ValueError, "diagnostic differs"):
                probe.rust_source_identity(source, archive, manifest)
            diagnostic.write_bytes(probe.DIAGNOSTIC_SOURCE.read_bytes())
            (source / "rust/src/main.rs").write_text("tampered")
            with self.assertRaisesRegex(ValueError, "differs"):
                probe.rust_source_identity(source, archive, manifest)


if __name__ == "__main__":
    unittest.main()
