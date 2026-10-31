#!/usr/bin/env python3
"""Dependency-light behavioral checks for the bounded Pocket TTS audition."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).with_name("pocket-tts-audition.py")
spec = importlib.util.spec_from_file_location("pocket_tts_audition", SCRIPT)
audition = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audition)
BASE = audition._manifest()


class ManifestPinTests(unittest.TestCase):
    def test_exact_publisher_lfs_metadata_and_source_config_pin(self):
        self.assertEqual(BASE["source"]["commit"], "a922b912dfa366975294afb568d815b305acde5d")
        self.assertEqual(BASE["source"]["config_sha256"], "3ec91f2f298d3fd283b98093d432f236c30523262f74e7e820a60d9625426700")
        self.assertEqual(BASE["source"]["archive_sha256"], "08dd2e7405609e788a9811f2786cdfc3f8f21f5e083a321108923fcae8f44b06")
        self.assertEqual(BASE["model"]["revision"], "a7056f505a4a8ca72d8b5fb1440071d6f1a35739")
        actual = {item["path"]: (item["size"], item["sha256"]) for item in BASE["model"]["assets"]}
        self.assertEqual(actual, {
            "languages/english_18m_2026-10/model.safetensors": (111728528, "599329dcea2f2a8d17b575e1c6e11b78822453c9073040d55c26faadf4883188"),
            "languages/english_18m_2026-10/tokenizer.model": (59339, "d461765ae179566678c93091c5fa6f2984c31bbe990bf1aa62d92c64d91bc3f6"),
            "languages/english_18m_2026-10/embeddings/alba.safetensors": (4163992, "11554cbe9f3d3a3e10722932df9952bb44928e534f7af3200524659166b5627a"),
        })


class AuditionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = json.loads(json.dumps(BASE))
        for item in self.manifest["model"]["assets"]:
            path = self.root / item["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = (item["path"] + "\n").encode()
            path.write_bytes(payload)
            item["size"] = len(payload)
            item["sha256"] = hashlib.sha256(payload).hexdigest()
        self.config_text = (
            "weights_path: hf://gated/model\n"
            "weights_path_without_voice_cloning: hf://ungated/model\n"
            "flow_lm:\n  lookup_table:\n    tokenizer_path: hf://ungated/tokenizer\n"
            "mimi:\n  sample_rate: 24000\n  channels: 1\n  n_bins: 4000\n"
        )
        config = self.root / audition.CONFIG_NAME
        config.write_text(self.config_text)
        self.manifest["source"]["config_size"] = config.stat().st_size
        self.manifest["source"]["config_sha256"] = audition._digest(config)

    def test_only_pinned_18m_alba_selection_is_admitted(self):
        files = audition.verify_assets(self.root, self.manifest)
        self.assertEqual(len(files), 4)
        for update in ({"language": "english_7m_2026-10"}, {"voice": "cosette"}, {"revision": "main"}):
            altered = json.loads(json.dumps(self.manifest))
            altered["model"].update(update)
            with self.assertRaises(ValueError):
                audition.verify_assets(self.root, altered)
        altered = json.loads(json.dumps(self.manifest))
        altered["model"]["assets"].append(dict(altered["model"]["assets"][0]))
        with self.assertRaises(ValueError):
            audition.verify_assets(self.root, altered)

    def test_missing_corrupt_and_wrong_size_rejected_before_inference(self):
        item = self.manifest["model"]["assets"][0]
        path = self.root / item["path"]
        with mock.patch.object(audition, "_source_checkout") as source:
            path.unlink()
            with self.assertRaisesRegex(ValueError, "Missing"):
                audition.audition(self.root, self.root / "out", self.root, self.root / "source.tar.gz", self.manifest)
            source.assert_not_called()
        path.write_bytes(b"x" * item["size"])
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            audition.verify_assets(self.root, self.manifest)
        path.write_bytes(b"short")
        with self.assertRaisesRegex(ValueError, "size mismatch"):
            audition.verify_assets(self.root, self.manifest)

    def test_local_config_selects_only_verified_ungated_weights(self):
        files = audition.verify_assets(self.root, self.manifest)
        path = audition._local_config(files, self.root)
        data = path.read_text()
        self.assertNotIn("hf://", data)
        self.assertIn("weights_path_without_voice_cloning: " + files[self.manifest["model"]["assets"][0]["path"]].as_posix(), data)
        self.assertIn("tokenizer_path: " + files[self.manifest["model"]["assets"][1]["path"]].as_posix(), data)
        self.assertIn("voice-cloning-disabled.safetensors", data)
        (self.root / "voice-cloning-disabled.safetensors").touch()
        with self.assertRaisesRegex(ValueError, "sentinel"):
            audition._local_config(files, self.root)

    def test_unexpected_config_or_extra_remote_reference_rejected(self):
        files = audition.verify_assets(self.root, self.manifest)
        (self.root / audition.CONFIG_NAME).write_text(self.config_text.replace("24000", "22050"))
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            audition.verify_assets(self.root, self.manifest)
        (self.root / audition.CONFIG_NAME).write_text(self.config_text + "other: hf://unreviewed/asset\n")
        self.manifest["source"]["config_size"] = (self.root / audition.CONFIG_NAME).stat().st_size
        self.manifest["source"]["config_sha256"] = audition._digest(self.root / audition.CONFIG_NAME)
        files = audition.verify_assets(self.root, self.manifest)
        with self.assertRaisesRegex(ValueError, "Remote reference"):
            audition._local_config(files, self.root)

    def test_resolver_forces_ungated_fallback_and_denies_remote_inputs(self):
        files = audition.verify_assets(self.root, self.manifest)
        config = audition._local_config(files, self.root)
        resolver = audition._guarded_local_resolver(files, config)
        weights = files["languages/english_18m_2026-10/model.safetensors"]
        self.assertEqual(resolver(weights), weights)
        with self.assertRaises(FileNotFoundError):
            resolver(self.root / "voice-cloning-disabled.safetensors")
        with self.assertRaisesRegex(ValueError, "Remote"):
            resolver("hf://kyutai/pocket-tts/model.safetensors")
        with self.assertRaisesRegex(ValueError, "Unapproved"):
            resolver(self.root / "other.safetensors")
        weights.unlink()
        with self.assertRaisesRegex(ValueError, "Unapproved"):
            resolver(weights)

    def test_source_archive_and_extracted_file_hashes_required(self):
        archive = self.root / "source.tar.gz"
        archive.write_bytes(b"pinned archive fixture")
        self.manifest["source"]["archive_size"] = archive.stat().st_size
        self.manifest["source"]["archive_sha256"] = audition._digest(archive)
        source = self.root / "source"
        source.mkdir()
        self.manifest["source"]["file_sha256"] = {"pyproject.toml": hashlib.sha256(b"expected").hexdigest()}
        (source / "pyproject.toml").write_bytes(b"expected")
        audition._source_checkout(source, archive, self.manifest)
        archive.write_bytes(b"corrupt archive fixture")
        with self.assertRaises(ValueError):
            audition._source_checkout(source, archive, self.manifest)
        archive.write_bytes(b"pinned archive fixture")
        (source / "pyproject.toml").write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "source file differs"):
            audition._source_checkout(source, archive, self.manifest)

    def test_pcm_rejects_nonfinite_silence_clipping_stereo_and_long(self):
        import numpy as np
        for invalid in (
            np.array([], dtype=np.float32),
            np.zeros(24000, dtype=np.float32),
            np.array([0.2, np.nan], dtype=np.float32),
            np.array([0.2, np.inf], dtype=np.float32),
            np.array([0.2, 1.0], dtype=np.float32),
            np.ones((2, 240), dtype=np.float32) * 0.2,
            np.ones(24000 * 17, dtype=np.float32) * 0.2,
        ):
            with self.subTest(shape=invalid.shape, value=invalid.reshape(-1)[:2]):
                with self.assertRaises(ValueError):
                    audition.inspect_pcm(invalid)
        pcm, stats = audition.inspect_pcm(np.array([0.25, -0.25], dtype=np.float32))
        self.assertEqual(len(pcm), 4)
        self.assertEqual(stats["samples"], 2)
        self.assertAlmostEqual(stats["peak"], 0.25)


if __name__ == "__main__":
    unittest.main()
