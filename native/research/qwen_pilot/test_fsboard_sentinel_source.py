import codecs
import hashlib
import io
import os
from pathlib import Path
import stat
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import fsboard_sentinel_source as package


class SourcePackageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "source"
        self.root.mkdir()

    def test_strict_utf8_with_optional_bom_and_lf_preserves_both_glyphs(self):
        path = self.root / "test_fsboard_evaluate.py"
        text = 'fixture = "\uff21 Stra\u00dfe"\n'
        original = text.replace("\n", "\r\n").encode("utf-8")
        path.write_bytes(codecs.BOM_UTF8 + original)
        normalized = package.read_source(self.root, path.name)
        self.assertEqual(normalized, text)
        package.check_unicode_fixture({path.name: normalized.encode("utf-8")})

        # The legacy Windows decode could succeed and still corrupt the fixture.
        corrupted = original.decode("cp1252").encode("utf-8")
        with self.assertRaisesRegex(ValueError, "Unicode fixture"):
            package.check_unicode_fixture({path.name: corrupted})
        path.write_bytes(text.encode("cp1252", errors="replace"))
        with self.assertRaises(UnicodeDecodeError):
            package.read_source(self.root, path.name)
        path.write_bytes(b"#!/bin/python\r# coding: latin-1\r" + original)
        with self.assertRaisesRegex(ValueError, "non-UTF-8 encoding"):
            package.read_source(self.root, path.name)

    def test_follows_transitive_fixture_imports_and_fails_if_one_is_missing(self):
        for name in package.SEED_SOURCES:
            (self.root / name).touch()
        (self.root / "test_fsboard_evaluate.py").write_bytes(
            b"from test_train_qwen_temporal import dense_admission\n"
        )
        (self.root / "test_train_qwen_temporal.py").write_bytes(
            b"def dense_admission():\n    from test_train_qwen_pilot import admission\n"
        )
        with self.assertRaisesRegex(ValueError, "test_train_qwen_pilot.py"):
            package.collect_sources(self.root)
        (self.root / "test_train_qwen_pilot.py").write_bytes(b"import unittest\n")
        (self.root / "credentials.py").write_bytes(b"bystander = True\n")
        (self.root / "__pycache__").mkdir()
        (self.root / ".git").mkdir()
        self.assertEqual(set(package.collect_sources(self.root)), set(package.RELEASE_SOURCES))
        (self.root / "test_train_qwen_pilot.py").write_bytes(b"import credentials\n")
        with self.assertRaisesRegex(ValueError, "Unexpected local dependency"):
            package.collect_sources(self.root)

    def test_private_byte_digest_and_path_boundary(self):
        data = codecs.BOM_UTF8 + b'{"private": true}\r\n'
        path = self.root / "private-proposal.json"
        path.write_bytes(data)
        sha256 = hashlib.sha256(data).hexdigest()
        with patch.dict(package.PRIVATE_SHA256, {path.name: sha256}):
            self.assertEqual(package.read_private(self.root, path, path.name), data)
            path.write_bytes(data.replace(b"\r\n", b"\n"))
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                package.read_private(self.root, path, path.name)
            outside = self.root.parent / path.name
            outside.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "outside the source root"):
                package.read_private(self.root, outside, path.name)

    @unittest.skipIf(os.name == "nt", "The Windows host may restrict creating a real symlink")
    def test_rejects_directory_link_even_when_target_is_inside_root(self):
        target = self.root / "real"
        target.mkdir()
        (target / "test_fsboard_evaluate.py").write_bytes(b"pass\n")
        alias = self.root / "link"
        alias.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Symlink or reparse point"):
            package.read_source(self.root, alias / "test_fsboard_evaluate.py")

    def test_rejects_windows_reparse_attribute_on_input_ancestor(self):
        folder = self.root / "folder"
        folder.mkdir()
        fixture = folder / "test_fsboard_evaluate.py"
        fixture.write_bytes(b"pass\n")
        actual_lstat = Path.lstat

        def lstat(path):
            if path == folder:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
            return actual_lstat(path)

        with patch.object(Path, "lstat", lstat), self.assertRaisesRegex(ValueError, "Symlink or reparse point"):
            package.read_source(self.root, fixture)

    def test_archive_and_manifest_are_deterministic_with_fixed_metadata(self):
        first = {"b.py": b"b = 1\n", "a.py": b"a = 2\n"}
        second = dict(reversed(list(first.items())))
        archive, manifest = package.archive_source(first)
        self.assertEqual((archive, manifest), package.archive_source(second))
        self.assertEqual([row["file"] for row in manifest], ["a.py", "b.py"])
        self.assertEqual(manifest[0]["sha256"], hashlib.sha256(first["a.py"]).hexdigest())
        self.assertEqual(archive[4:8], bytes(4))  # Gzip mtime.
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as contents:
            self.assertEqual(contents.getnames(), ["SOURCE-MANIFEST.json", "a.py", "b.py"])
            for member in contents.getmembers():
                self.assertEqual((member.mtime, member.uid, member.gid, member.mode), (0, 0, 0, 0o644))


if __name__ == "__main__":
    unittest.main()
