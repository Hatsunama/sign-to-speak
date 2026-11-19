import hashlib
from io import StringIO
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fsboard_training_intake as intake


def row(tag, phrase="maple", signer=None, recording=None, sensitive=False):
    return {
        "signingType": "fs",
        "sensitiveContent": sensitive,
        "clipFilename": f"{tag}.mp4",
        "clipFileSize": 72000,
        "clipFileMd5": hashlib.md5(tag.encode(), usedforsecurity=False).hexdigest(),
        "signerId": signer or f"signer-{tag}",
        "fullVideoFilename": recording or f"recording-{tag}.mp4",
        "phrase": phrase,
        "clipStartTimeS": 10.0,
        "clipEndTimeS": 12.25,
        "annotationStartTimeS": 10.1,
        "annotationEndTimeS": 12.1,
    }


def candidate(role, tag, **kwargs):
    return intake.candidate_for_role(row(tag, **kwargs), role)


class Metadata(unittest.TestCase):
    def test_release_shape_filters_and_uses_the_official_split_path(self):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "source.json"
            path.write_text(json.dumps([row("valid"), row("hidden", sensitive=True),
                                        row("not-a-short-word", phrase="the hills")]),
                            encoding="utf-8")
            candidates, stats, _ = intake.read_split(path, "validation")
        self.assertEqual(stats["rows"], 3)
        self.assertEqual(stats["candidates"], 1)
        self.assertEqual(candidates[0]["source_path"],
                         "daun_v3/video_clips/daun_v3-validation/valid.mp4")

    def test_streamed_array_delimiters_across_chunk_boundaries(self):
        cases = (
            (' \n[ {"name":"maple"}, {"name":"piano"} ] \t', None),
            ('[{"name":"maple"},]', "expected a metadata row object"),
            ('[{"name":"maple"}] {}', "unexpected trailing metadata"),
            ('[{"name":"maple"}', "missing metadata array delimiter"),
            ('[{"name":"maple"', "invalid or oversized metadata row"),
        )
        for chunk_size in (16, 17, 18, 19, 30, 64):
            for content, error in cases:
                with self.subTest(chunk_size=chunk_size, content=content):
                    with patch.object(intake, "JSON_CHUNK", chunk_size):
                        if error is None:
                            self.assertEqual(list(intake.metadata_rows(StringIO(content))),
                                             [{"name": "maple"}, {"name": "piano"}])
                        else:
                            with self.assertRaisesRegex(intake.AdmissionError, error):
                                list(intake.metadata_rows(StringIO(content)))

    def test_rejects_conflicting_clip_metadata(self):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "source.json"
            path.write_text(json.dumps([row("reused"), row("reused", phrase="piano")]),
                            encoding="utf-8")
            with self.assertRaisesRegex(intake.AdmissionError, "conflicting metadata"):
                intake.read_split(path, "train")

    def test_metadata_network_allowlist_and_digest_pin(self):
        with tempfile.TemporaryDirectory() as scratch, patch.object(intake, "build_opener") as opener:
            target = Path(scratch) / "file"
            with self.assertRaises(intake.AdmissionError):
                intake.fetch_metadata("daun_v3/video_clips/daun_v3-train/never.mp4", target)
            opener.assert_not_called()
            response = opener.return_value.open.return_value.__enter__.return_value
            response.status = 200
            response.headers = {"Content-Length": "129"}
            response.read.side_effect = [b"x" * 129, b""]
            with self.assertRaisesRegex(intake.AdmissionError, "integrity"):
                intake.fetch_metadata("LICENSE", target)


class Selection(unittest.TestCase):
    def test_excludes_previous_signers_targets_recordings_paths_and_bytes(self):
        prior = candidate("test", "prior")
        same_bytes = candidate("test", "other", phrase="chair")
        same_bytes["metadata_md5"] = prior["metadata_md5"].upper()
        options = [
            candidate("test", "prior", phrase="class", signer="other", recording="other.mp4"),
            candidate("test", "person", phrase="checks", signer="SIGNER-PRIOR"),
            candidate("test", "recording", phrase="plane", recording="RECORDING-PRIOR.mp4"),
            candidate("test", "target", phrase="maple"),
            same_bytes,
            candidate("test", "good", phrase="music"),
        ]
        chosen, report = intake.select_role(options, intake.Quota(2, 2, 500000),
                                            intake.exclusion_keys([prior]))
        self.assertEqual([entry["clip_filename"] for entry in chosen], ["good.mp4"])
        self.assertEqual(report["shortfalls"]["clips"], 1)
        self.assertEqual(report["shortfalls"]["signers"], 1)

    def test_reserves_distinct_test_provenance_before_other_splits(self):
        test = [candidate("test", "a", phrase="jazz", signer="s1"),
                candidate("test", "b", phrase="apple", signer="s2"),
                candidate("test", "c", phrase="piano", signer="s3")]
        validation = [candidate("validation", "d", phrase="stone", signer="s1"),
                      candidate("validation", "e", phrase="piano", signer="s4"),
                      candidate("validation", "f", phrase="chair", signer="s5")]
        train = [candidate("train", "g", phrase="music", signer="s5"),
                 candidate("train", "h", phrase="wheel", recording="recording-f.mp4"),
                 candidate("train", "i", phrase="smile", signer="s6")]
        quotas = {"test": intake.Quota(3, 3, 300000, 1),
                  "validation": intake.Quota(1, 1, 100000),
                  "train": intake.Quota(1, 1, 100000)}
        inputs = dict(test=test, validation=validation, train=train)
        chosen, reports = intake.propose(inputs, [], quotas)
        reversed_inputs = {key: list(reversed(value)) for key, value in inputs.items()}
        self.assertEqual(chosen, intake.propose(reversed_inputs, [], quotas)[0])
        self.assertEqual([entry["clip_filename"] for entry in chosen["validation"]], ["f.mp4"])
        self.assertEqual([entry["clip_filename"] for entry in chosen["train"]], ["i.mp4"])
        self.assertTrue(all(value == 0 for value in reports["test"]["shortfalls"].values()))

    def test_reports_missing_category_and_respects_cap(self):
        possible = [candidate("test", "a", phrase="jet"), candidate("test", "b", phrase="zoo")]
        chosen, report = intake.select_role(possible, intake.Quota(2, 2, 72000, 1), set())
        self.assertEqual(len(chosen), 1)
        self.assertEqual(report["proposed_media_bytes"], 72000)
        self.assertEqual(report["shortfalls"]["ordinary"], 1)

    def test_greedy_signer_shortfall_can_have_a_feasible_alternative(self):
        options = [
            candidate("test", "a", phrase="maple", signer="s1"),
            candidate("test", "b", phrase="maple", signer="s2"),
            candidate("test", "c", phrase="stone", signer="s1"),
        ]
        quota = intake.Quota(2, 2, 200000)
        _, report = intake.select_role(options, quota, set())
        _, alternative = intake.select_role(options[1:], quota, set())
        self.assertEqual(report["shortfalls"]["signers"], 1)
        self.assertEqual(alternative["shortfalls"]["signers"], 0)

    def test_pinned_old_receipt_contains_exactly_eight_references(self):
        path = Path(__file__).with_name("fsboard-eval-v3-approved.json")
        previous = intake.read_previous(path)
        self.assertEqual(len(previous), 8)
        self.assertEqual(len({entry["signer_id"] for entry in previous}), 8)
        with tempfile.TemporaryDirectory() as scratch:
            tampered = Path(scratch) / "receipt.json"
            tampered.write_bytes(path.read_bytes() + b" ")
            with self.assertRaisesRegex(intake.AdmissionError, "integrity"):
                intake.read_previous(tampered)


class Preflight(unittest.TestCase):
    def setUp(self):
        release = {
            "test": [row("unseen-test", phrase="jazz", signer="fixture-test")],
            "validation": [row("unseen-validation", phrase="apple", signer="fixture-val")],
            "train": [row("unseen-train", phrase="piano", signer="fixture-train")],
        }

        def fetch(name, target):
            if name == "LICENSE":
                payload = b"fixture"
            else:
                role = Path(name).stem.removeprefix("daun_v3-")
                payload = json.dumps(release[role]).encode()
            target.write_bytes(payload)
            return {"path": name, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}

        self.prior = Path(__file__).with_name("fsboard-eval-v3-approved.json")
        self.scratch = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.output = self.scratch / "output"
        self.enterContext(patch.object(intake, "fetch_metadata", fetch))

    def test_writes_private_proposal_and_only_aggregate_summary(self):
        write_json = intake.write_json

        def write_before_publication(path, document):
            self.assertFalse(self.output.exists())
            write_json(path, document)

        with patch.object(intake, "write_json", write_before_publication):
            result = intake.preflight(self.output, self.prior)
        private = (self.output / "private-proposal.json").read_bytes()
        summary = (self.output / "summary.json").read_bytes()
        self.assertEqual(set(self.scratch.iterdir()), {self.output})
        self.assertEqual(json.loads(summary), result)
        self.assertEqual(hashlib.sha256(private).hexdigest(), result["private_proposal_sha256"])
        self.assertIn(b"fixture-test", private)
        self.assertNotIn(b"fixture-test", summary)
        self.assertNotIn(b"unseen-test.mp4", summary)
        self.assertNotIn(b"jazz", summary)
        self.assertEqual(result["scope"]["media_downloaded_bytes"], 0)
        self.assertIn("greedy", result["scope"]["shortfalls"])
        self.assertIn("different valid selection", result["scope"]["shortfalls"])
        self.assertEqual(result["official_split_overlap"]["test/validation"],
                         {"signers": 0, "recordings": 0})
        self.assertEqual(result["splits"]["train"]["selected_clips"], 1)

    def assert_second_write_failure_leaves_no_output(self, error):
        write_json = intake.write_json
        unrelated = self.scratch / ".fsboard-proposal-unrelated"
        unrelated.mkdir()
        sentinel = unrelated / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")

        def fail_second_write(path, document):
            if path.name == "summary.json":
                self.assertTrue((path.parent / "private-proposal.json").is_file())
                path.write_text("{", encoding="utf-8")
                raise error("interrupted write")
            write_json(path, document)

        with patch.object(intake, "write_json", fail_second_write):
            with self.assertRaisesRegex(error, "interrupted write"):
                intake.preflight(self.output, self.prior)
        self.assertEqual(set(self.scratch.iterdir()), {unrelated})
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

        intake.preflight(self.output, self.prior)
        self.assertEqual(set(self.output.iterdir()), {
            self.output / "private-proposal.json", self.output / "summary.json",
        })

    def test_cleans_up_only_its_own_stage_on_second_write_error(self):
        self.assert_second_write_failure_leaves_no_output(OSError)

    def test_cleans_up_only_its_own_stage_on_second_write_interrupt(self):
        self.assert_second_write_failure_leaves_no_output(KeyboardInterrupt)

    def test_existing_destination_is_preserved(self):
        self.output.mkdir()
        sentinel = self.output / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        with patch.object(intake, "fetch_metadata") as fetch:
            with self.assertRaisesRegex(intake.AdmissionError, "already exists"):
                intake.preflight(self.output, self.prior)
        fetch.assert_not_called()
        self.assertEqual(list(self.output.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_empty_destination_created_during_write_is_preserved(self):
        write_json = intake.write_json

        def create_destination(path, document):
            write_json(path, document)
            if path.name == "summary.json":
                self.output.mkdir()

        with patch.object(intake, "write_json", create_destination):
            with self.assertRaisesRegex(intake.AdmissionError, "already exists"):
                intake.preflight(self.output, self.prior)
        self.assertEqual(set(self.scratch.iterdir()), {self.output})
        self.assertEqual(list(self.output.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
