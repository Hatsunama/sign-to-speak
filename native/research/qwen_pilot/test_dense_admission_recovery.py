import dataclasses
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import dense_admission as admission


class DenseAdmissionRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory(prefix="dense-recovery-test-"))
        workspace = Path(temporary)
        self.sparse = workspace / "sparse"
        self.dense = workspace / "dense"
        self.sparse.mkdir()
        for name in ("LICENSE.txt", "attribution.txt"):
            (self.sparse / name).write_text(name, encoding="utf-8")
        self.stderr = io.StringIO()
        self.enterContext(patch.multiple(
            admission,
            SPARSE=dataclasses.replace(admission.SPARSE, root=self.sparse),
            DENSE=dataclasses.replace(admission.DENSE, root=self.dense),
            sys=SimpleNamespace(platform="linux", stderr=self.stderr),
        ))
        self.enterContext(patch.object(admission, "Path", return_value=workspace / "scratch"))
        self.enterContext(patch.object(admission, "original_receipt", return_value={
            "dataset": {"license_path": "LICENSE.txt", "attribution_path": "attribution.txt"},
            "original_bytes": 5,
        }))
        self.resample = self.enterContext(patch.object(admission, "resample"))
        self.enterContext(patch.object(admission, "validate_admission"))
        self.success_message = self.enterContext(patch.object(admission, "print", create=True))
        self.success_message.side_effect = self.record_diagnostics

    def record_diagnostics(self, *args, **kwargs):
        if kwargs.get("file") is self.stderr:
            print(*args, **kwargs)

    def fetch_fails_after_first_clip(self, receipt, root, scratch):
        (root / "partial-frame.jpg").write_bytes(b"partial")
        raise HTTPError("https://example.test/video.mp4", 503, "Unavailable", None, None)

    def resample_succeeds(self, receipt, root, scratch):
        (root / "frame.jpg").write_bytes(b"frame")
        return [{"frames": [{"duplicate": False}]}], 5

    def test_handled_fetch_failure_allows_retry_at_same_path(self):
        self.resample.side_effect = self.fetch_fails_after_first_clip
        self.assertEqual(admission.main(), 1)
        self.assertIn("resampling original clips (HTTP 503)", self.stderr.getvalue())

        self.resample.side_effect = self.resample_succeeds
        self.assertEqual(admission.main(), 0, self.stderr.getvalue())
        self.assertEqual(self.resample.call_count, 2)
        self.assertFalse((self.dense / "partial-frame.jpg").exists())
        result = json.loads((self.dense / "admission.json").read_text(encoding="utf-8"))
        self.assertEqual(result["source_admission_sha256"], admission.SPARSE_ADMISSION_SHA256)
        self.assertEqual(result["frames_per_clip"], admission.DENSE.admitted_frames)
        self.assertEqual(result["derived_frame_bytes"], 5)

    def test_preexisting_output_is_preserved(self):
        self.dense.mkdir()
        sentinel = self.dense / "partial-frame.jpg"
        sentinel.write_bytes(b"from a previous run")
        self.assertEqual(admission.main(), 1)
        self.assertIn("FileExistsError", self.stderr.getvalue())
        self.assertEqual(sentinel.read_bytes(), b"from a previous run")
        self.resample.assert_not_called()

    def test_cleanup_failure_reports_both_errors_and_leaves_retry_blocked(self):
        self.resample.side_effect = self.fetch_fails_after_first_clip
        with patch("shutil.rmtree", side_effect=PermissionError("fixture cleanup denied")):
            self.assertEqual(admission.main(), 1)
        diagnostics = self.stderr.getvalue()
        self.assertIn("resampling original clips (HTTP 503)", diagnostics)
        self.assertIn("removing incomplete output", diagnostics)
        self.assertIn("fixture cleanup denied", diagnostics)
        self.assertEqual((self.dense / "partial-frame.jpg").read_bytes(), b"partial")

        self.assertEqual(admission.main(), 1)
        self.assertIn("FileExistsError", self.stderr.getvalue())
        self.resample.assert_called_once()

    def test_published_output_is_preserved_if_success_reporting_fails(self):
        self.resample.side_effect = self.resample_succeeds

        def fail_success_message(*args, **kwargs):
            if kwargs.get("flush"):
                raise BrokenPipeError
            self.record_diagnostics(*args, **kwargs)

        self.success_message.side_effect = fail_success_message
        self.assertEqual(admission.main(), 1)
        self.assertIn("BrokenPipeError", self.stderr.getvalue())
        self.assertTrue((self.dense / "admission.json").is_file())
        self.assertEqual((self.dense / "frame.jpg").read_bytes(), b"frame")


if __name__ == "__main__":
    unittest.main()
