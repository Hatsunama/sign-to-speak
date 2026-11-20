import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import fsboard_task_inputs as input_gate
from fsboard_v4_resume_inputs import journal_completed_encodings


class EncodingJournalTests(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.directory = self.root / "journal"
        self.model = {"id": "test/pinned", "revision": "b" * 40}
        self.admission = SimpleNamespace(
            summary={"admission_sha256": "a" * 64},
            frozen_protocol={"model": self.model},
            clips={
                "development": [{"id": "development-001", "reference": "cat"}],
                "holdout": [{"id": "holdout-001", "reference": "dog"}],
            },
        )
        self.images = [object()]
        self.encoding = (b"prefix\x00", b"full\xff", {"prompt_tokens": 42})
        self.gate = SimpleNamespace(
            encode_example=self.encode,
            publish_private=input_gate.publish_private,
        )

    def encode(self, processor, images, reference, **kwargs):
        self.assertIs(processor, self.gate)
        self.assertIs(images, self.images)
        self.assertEqual(kwargs, {"context": 6144})
        if reference == "dog":
            self.assertTrue((self.directory / "0001.json").is_file())
        return self.encoding

    def call(self, reference):
        return self.gate.encode_example(self.gate, self.images, reference, context=6144)

    def test_records_delegated_results_immediately_in_gate_order(self):
        original = self.gate.encode_example
        with journal_completed_encodings(self.gate, self.admission, self.directory):
            self.assertIs(self.call("cat"), self.encoding)
            self.assertIs(self.call("dog"), self.encoding)
        self.assertIs(self.gate.encode_example, original)
        self.assertEqual(
            sorted(path.name for path in self.directory.iterdir()), ["0001.json", "0002.json"]
        )
        for ordinal, role in enumerate(("development", "holdout"), 1):
            row = json.loads((self.directory / f"{ordinal:04d}.json").read_bytes())
            self.assertGreaterEqual(row.pop("elapsed_seconds"), 0)
            self.assertEqual(row, {
                "schema_version": 1,
                "status": "encoding_completed",
                "ordinal": ordinal,
                "role": role,
                "clip_id": f"{role}-001",
                "admission_sha256": "a" * 64,
                "model": self.model,
                "encoding": self.encoding[2],
            })

    def test_interruption_retains_only_completed_calls_and_restores_delegate(self):
        def interrupt(processor, images, reference, **kwargs):
            if reference == "dog":
                raise KeyboardInterrupt("interrupted during encoding")
            return self.encode(processor, images, reference, **kwargs)

        self.gate.encode_example = interrupt
        with self.assertRaisesRegex(KeyboardInterrupt, "interrupted"):
            with journal_completed_encodings(self.gate, self.admission, self.directory):
                self.call("cat")
                self.call("dog")
        self.assertIs(self.gate.encode_example, interrupt)
        self.assertEqual([path.name for path in self.directory.iterdir()], ["0001.json"])

    def test_existing_record_is_never_overwritten(self):
        with self.assertRaises(FileExistsError):
            with journal_completed_encodings(self.gate, self.admission, self.directory):
                existing = self.directory / "0001.json"
                existing.write_bytes(b"original evidence")
                self.call("cat")
        self.assertEqual(existing.read_bytes(), b"original evidence")

    def test_existing_directory_is_never_reused(self):
        self.directory.mkdir()
        with self.assertRaises(FileExistsError):
            with journal_completed_encodings(self.gate, self.admission, self.directory):
                self.fail("existing directory was accepted")

    def test_symlink_ancestor_is_rejected(self):
        actual = self.root / "actual"
        actual.mkdir()
        link = self.root / "link"
        try:
            link.symlink_to(actual, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlinks are unavailable: {exc}")
        (actual / "nested").mkdir()
        with self.assertRaisesRegex(ValueError, "link"):
            with journal_completed_encodings(
                self.gate, self.admission, link / "nested" / "journal"
            ):
                self.fail("symlink ancestor was accepted")
        self.assertEqual(list((actual / "nested").iterdir()), [])

    def test_rejects_calls_out_of_gate_order(self):
        with self.assertRaisesRegex(ValueError, "order"):
            with journal_completed_encodings(self.gate, self.admission, self.directory):
                self.call("dog")
        self.assertEqual(list(self.directory.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
