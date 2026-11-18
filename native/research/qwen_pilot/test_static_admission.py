import hashlib
import io
import json
from pathlib import Path
import stat
from string import ascii_uppercase
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import warnings
import zipfile

from PIL import Image

import static_admission as admission


CLASSES = (*ascii_uppercase, "del", "nothing", "space")
FILES = [f"{label}/{label}{number:04d}_test.jpg" for label in CLASSES for number in range(1, 31)]
ANCHOR = "A/A0001_test.jpg"


def jpeg(number, *, size=(200, 200), mode="RGB", image_format="JPEG"):
    rgb = (number % 32 * 8, number // 32 * 8, 127)
    with Image.new(mode, size, rgb if mode == "RGB" else 127) as image:
        buffer = io.BytesIO()
        image.save(buffer, format=image_format)
        return buffer.getvalue()


class StaticAdmissionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.members = {path: jpeg(index) for index, path in enumerate(FILES)}
        cls.original = cls.archive()

    @classmethod
    def archive(cls, *, omit=(), extras=(), replace=None, dirs=()):
        result = io.BytesIO()
        replacements = replace or {}
        with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in dirs:
                archive.writestr(name, b"")
            for path, payload in reversed(cls.members.items()):
                if path not in omit:
                    archive.writestr(path, replacements.get(path, payload))
            for name, payload in extras:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    archive.writestr(name, payload)
        return result.getvalue()

    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="static-admission-test-", dir=Path(__file__).resolve().parent)
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.destination = self.root / "static-v1"
        anchor = self.members[ANCHOR]
        for name, value in (("ANCHOR_BYTES", len(anchor)), ("ANCHOR_SHA256", hashlib.sha256(anchor).hexdigest())):
            replacement = patch.object(admission, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)

    def prepare(self, payload=None):
        data = self.original if payload is None else payload
        with patch.object(admission, "download_archive", side_effect=lambda target: admission.bounded_copy(
                io.BytesIO(data), target, admission.ARCHIVE_CAP)):
            return admission.prepare(self.destination)

    def reject(self, payload, message):
        with self.assertRaisesRegex(admission.AdmissionError, message):
            self.prepare(payload)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_original_bytes_and_review_only_provenance(self):
        self.prepare(self.archive(dirs=[f"{label}/" for label in CLASSES]))
        manifest = json.loads((self.destination / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["role"], "reference_candidate_not_admitted")
        self.assertEqual(manifest["source"]["license"], "CC0-1.0")
        self.assertEqual(manifest["source"]["creator"], "Dan Rasband")
        self.assertEqual(manifest["source"]["version"], 1)
        self.assertEqual(manifest["source"]["archive"]["sha256"], hashlib.sha256(
            self.archive(dirs=[f"{label}/" for label in CLASSES])).hexdigest())
        self.assertEqual(manifest["source"]["anchor"]["sha256"], hashlib.sha256(self.members[ANCHOR]).hexdigest())
        self.assertEqual(manifest["source"]["signer_session_independence"], "unverified")
        samples = manifest["samples"]
        self.assertEqual(len(samples), 120)
        self.assertEqual(
            [(row["label"], row["source_path"]) for row in samples],
            [
                (label, f"{label}/{label}{index:04d}_test.jpg")
                for label in ascii_uppercase if label not in "JZ"
                for index in (1, 7, 13, 19, 25)
            ],
        )
        self.assertEqual({row["role"] for row in samples}, {"reference_candidate_not_admitted"})
        self.assertEqual({row["source_group"] for row in samples}, {"danrasband-asl-alphabet-test-v1"})
        self.assertEqual({row["split"] for row in samples}, {"train"})
        self.assertEqual({(row["width"], row["height"]) for row in samples}, {(200, 200)})
        for row in samples:
            original = self.members[row["source_path"]]
            self.assertEqual(row["sha256"], hashlib.sha256(original).hexdigest())
            self.assertEqual((self.destination / row["path"]).read_bytes(), original)
        self.assertEqual(len(list((self.destination / "contact-sheets").glob("*.png"))), 6)

    def test_wrong_classes_and_missing_files(self):
        self.reject(self.archive(omit=[FILES[-1]], extras=[("QX/QX0030_test.jpg", b"1")]), "unexpected archive path")
        self.reject(self.archive(omit=[FILES[-1]]), "missing expected archive files")

    def test_traversal_absolute_and_windows_paths(self):
        for path in ("../outside.jpg", "/A/A0001_test.jpg", "A/../A/A0001_test.jpg", "C:/A.jpg"):
            with self.subTest(path=path):
                self.reject(self.archive(omit=[FILES[-1]], extras=[(path, b"1")]), "unexpected archive path")
        windows_path = self.original.replace(b"A/A0001_test.jpg", b"A\\A0001_test.jpg")
        self.reject(windows_path, "unexpected archive path")
        nul_path = self.original.replace(b"A/A0001_test.jpg", b"A/A0001_test\0jpg")
        self.reject(nul_path, "unexpected archive path")

    def test_symlink_members_are_rejected(self):
        link = zipfile.ZipInfo(FILES[-1])
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.reject(self.archive(omit=[FILES[-1]], extras=[(link, b"../elsewhere")]), "regular file")

    def test_duplicate_members_and_duplicate_image_bytes(self):
        self.reject(self.archive(extras=[(FILES[-1], b"1")]), "duplicate archive path")
        self.reject(self.archive(replace={FILES[-1]: self.members[FILES[-2]]}), "duplicate image bytes")

    def test_archive_caps_bound_wire_and_expanded_data(self):
        with patch.object(admission, "ARCHIVE_CAP", len(self.original) - 1):
            self.reject(self.original, "permitted length")
        with patch.object(admission, "EXPANDED_CAP", 10_000):
            self.reject(self.original, "expanded archive")
        self.reject(self.archive(replace={FILES[-1]: b"a" * (admission.IMAGE_CAP + 1)}), "image byte cap")

    def test_image_checks_apply_even_to_unused_members(self):
        for bad_image in (jpeg(0, size=(201, 200)), jpeg(0, mode="L"), jpeg(0, image_format="PNG"), b"not an image"):
            with self.subTest(header=bad_image[:16]):
                self.reject(self.archive(replace={FILES[-1]: bad_image}), "expected single-frame 200x200 RGB JPEG")

    def test_anchor_hash_and_byte_count_must_match(self):
        with patch.object(admission, "ANCHOR_BYTES", 1):
            self.reject(self.original, "pinned source photo")
        with patch.object(admission, "ANCHOR_SHA256", "0" * 64):
            self.reject(self.original, "pinned source photo")

    def test_existing_output_is_never_overwritten(self):
        self.destination.mkdir()
        marker = self.destination / "previous"
        marker.write_bytes(b"keep me")
        with patch.object(admission, "download_archive") as download:
            with self.assertRaises(FileExistsError):
                admission.prepare(self.destination)
            download.assert_not_called()
        self.assertEqual(marker.read_bytes(), b"keep me")

    def test_failure_cleans_only_its_partial_output_and_can_retry(self):
        other = self.root / "other"
        other.write_bytes(b"unrelated")
        with patch.object(admission, "write_sheets", side_effect=RuntimeError("test recovery")):
            with self.assertRaisesRegex(RuntimeError, "test recovery"):
                self.prepare()
        self.assertEqual(list(self.root.iterdir()), [other])
        self.prepare()
        self.assertEqual(other.read_bytes(), b"unrelated")
        self.assertTrue((self.destination / "manifest.json").is_file())

    def test_failure_after_manifest_rename_preserves_published_output(self):
        rename = Path.rename

        def rename_then_fail(staged, target):
            rename(staged, target)
            raise OSError("test error after publication")

        with patch.object(Path, "rename", rename_then_fail):
            with self.assertRaisesRegex(OSError, "test error after publication"):
                self.prepare()

        manifest = json.loads((self.destination / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["samples"]), 120)
        for sample in manifest["samples"]:
            self.assertEqual((self.destination / sample["path"]).read_bytes(), self.members[sample["source_path"]])
        self.assertEqual(len(list((self.destination / "contact-sheets").glob("*.png"))), 6)
        with self.assertRaises(FileExistsError):
            self.prepare()

    def test_failed_manifest_rename_cleans_unpublished_output(self):
        with patch.object(Path, "rename", side_effect=OSError("test error before publication")):
            with self.assertRaisesRegex(OSError, "test error before publication"):
                self.prepare()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_cleanup_failure_preserves_primary_error_and_reports_cleanup(self):
        primary_error = admission.AdmissionError("test preparation failed")
        primary_error.__cause__ = zipfile.BadZipFile("test cause")
        cleanup_error = PermissionError("test cleanup denied")
        with (
            patch.object(admission, "write_sheets", side_effect=primary_error),
            patch.object(admission.shutil, "rmtree", side_effect=cleanup_error) as cleanup,
        ):
            with self.assertRaisesRegex(admission.AdmissionError, "test preparation failed") as failure:
                self.prepare()

        self.assertIs(failure.exception, primary_error)
        self.assertIsInstance(failure.exception.__cause__, zipfile.BadZipFile)
        cleanup.assert_called_once_with(self.destination)
        stderr = io.StringIO()
        with (
            patch.object(admission, "prepare", side_effect=failure.exception),
            patch.object(admission.sys, "argv", ["static_admission.py", "--output", str(self.destination)]),
            patch.object(admission.sys, "stderr", stderr),
        ):
            self.assertEqual(admission.main(), 1)
        self.assertIn("test preparation failed", stderr.getvalue())
        self.assertIn(f"Could not clean up {self.destination} (PermissionError: test cleanup denied)", stderr.getvalue())
        self.assertTrue((self.destination / "images").is_dir())
        self.assertFalse((self.destination / "manifest.json").exists())
        with self.assertRaises(FileExistsError):
            self.prepare()

    def test_failure_leaves_a_replacement_output_alone(self):
        displaced = self.root / "displaced"

        def replace_output(output, _samples):
            output.rename(displaced)
            output.mkdir()
            (output / "replacement").write_bytes(b"new owner")
            raise RuntimeError("test concurrent replacement")

        with patch.object(admission, "write_sheets", side_effect=replace_output):
            with self.assertRaisesRegex(RuntimeError, "test concurrent replacement"):
                self.prepare()
        self.assertEqual((self.destination / "replacement").read_bytes(), b"new owner")
        self.assertTrue((displaced / "images").is_dir())

    def test_declared_download_cap_rejects_before_reading_body(self):
        response = MagicMock()
        response.status = 200
        response.headers = {"Content-Length": str(admission.ARCHIVE_CAP + 1)}
        with patch.object(admission, "urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = response
            with self.assertRaisesRegex(admission.AdmissionError, "declared length outside cap"):
                admission.download_archive(io.BytesIO())
        response.read.assert_not_called()

    def test_anonymous_version_pinned_archive_request(self):
        response = MagicMock()
        response.status = 200
        response.headers = {"Content-Length": "3"}
        response.read.side_effect = [b"ZIP", b""]
        with patch.object(admission, "urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = response
            target = io.BytesIO()
            count, digest = admission.download_archive(target)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, admission.API_URL)
        self.assertEqual(json.loads(request.data), {
            "ownerSlug": "danrasband", "datasetSlug": "asl-alphabet-test", "datasetVersionNumber": 1,
        })
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual((count, digest), (3, hashlib.sha256(b"ZIP").hexdigest()))


if __name__ == "__main__":
    unittest.main()
