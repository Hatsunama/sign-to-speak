import hashlib
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fsboard_admission as admission
import fsboard_evaluate as scorer
import fsboard_training_intake as intake
import fsboard_task_protocol as task


def row(index, reference="abc", *, signer=None, recording=None, **extra):
    return {
        "phrase": reference, "signerId": signer or f"signer-{index}",
        "fullVideoFilename": recording or f"recording-{index}.mp4",
        "clipFilename": f"clip-{index}.mp4", "clipFileSize": 1024 + index,
        "clipFileMd5": f"{index:032x}",
        "signingType": "fs", "sensitiveContent": False,
        "clipStartTimeS": 1, "clipEndTimeS": 3,
        "annotationStartTimeS": 1.25, "annotationEndTimeS": 2.75,
        **extra,
    }


def read_split(split, *rows):
    return task.read_release_split(StringIO(json.dumps(rows)), split)


class CandidatePolicyTest(unittest.TestCase):
    def test_video_row_policy_is_versioned_and_target_spaces_stay_literal(self):
        compact = row(1, "abc")
        spaced = row(2, "ab cdef ghij")
        self.assertEqual(admission.candidate_from_row(compact), {
            "reference": "abc", "signer_id": "signer-1",
            "full_video_filename": "recording-1.mp4", "clip_filename": "clip-1.mp4",
            "source_path": "daun_v3/video_clips/daun_v3-test/clip-1.mp4",
            "original_bytes": 1025, "metadata_md5": f"{1:032x}",
            "clip_start_full_video_seconds": 1, "clip_end_full_video_seconds": 3,
            "annotation_start_full_video_seconds": 1.25,
            "annotation_end_full_video_seconds": 2.75,
        })
        self.assertIsNone(admission.candidate_from_row(spaced))
        candidates, stats, _ = read_split("validation", spaced)
        self.assertEqual(stats["candidates"], 1)
        self.assertEqual(candidates[0]["reference"], spaced["phrase"])
        self.assertEqual(candidates[0]["split"], "daun_v3-validation")
        self.assertEqual(candidates[0]["source_path"], "daun_v3/video_clips/daun_v3-validation/clip-2.mp4")
        self.assertEqual(scorer.score("AB CDEF\tGHIJ", spaced["phrase"])["edits"], 0)
        self.assertEqual(scorer.score("abcdefghij", spaced["phrase"])["edits"], 2)

    def test_v4_uses_shared_metadata_duration_bounds(self):
        durations = (0.499, 0.5, 0.7, 0.917, 1.199, 1.2, 3.2, 3.201)
        rows = [
            row(index, annotationStartTimeS=10, annotationEndTimeS=10 + duration,
                clipStartTimeS=9, clipEndTimeS=14)
            for index, duration in enumerate(durations, start=1)
        ]
        candidates, stats, groups = read_split("validation", *rows)
        self.assertEqual([clip["clip_filename"] for clip in candidates], [
            f"clip-{index}.mp4" for index in range(2, 8)
        ])
        self.assertEqual((stats["rows"], stats["candidates"], len(groups)), (8, 6, 8))

    def test_v4_rejects_invalid_targets_and_preserves_shared_clip_checks(self):
        targets = ("ab", "ab cdef ghijk", "ab  c", " ab", "ab ", "ab\tc", "abc\n", "abC", "ab-c", "?b?")
        rows = [row(index, target) for index, target in enumerate(targets, start=1)]
        for index, bad in enumerate((
            {"clipFileMd5": "broken"}, {"clipFilename": "../bad.mp4"}, {"sensitiveContent": 0},
            {"signingType": "phrase"}, {"clipFileSize": 20 * intake.MIB + 1},
            {"annotationEndTimeS": 1.74}, {"annotationEndTimeS": 3.01},
            {"annotationStartTimeS": float("inf")}, {"clipStartTimeS": True},
        ), start=100):
            rows.append(row(index, **bad))
        candidates, stats, _ = read_split("train", *rows)
        self.assertEqual(candidates, [])
        self.assertEqual(stats["rows"], len(rows))

    def test_publisher_test_has_no_candidate_path_and_split_is_validated(self):
        candidates, stats, groups = read_split("test", row(1, "usage"))
        self.assertEqual(candidates, [])
        self.assertEqual(stats["rows"], 1)
        self.assertEqual(groups, {("signer-1", "recording-1.mp4")})
        with self.assertRaisesRegex(admission.AdmissionError, "publisher split"):
            read_split("holdout", row(1))

    def test_conflicting_normalized_source_path_fails_closed(self):
        with self.assertRaisesRegex(admission.AdmissionError, "conflicting metadata"):
            read_split("train", row(1), row(2, clipFilename="Clip-1.mp4"))


class ProtocolSelectionTest(unittest.TestCase):
    def setUp(self):
        self.quotas = {
            "holdout": intake.Quota(1, 1, 1_000_000),
            "development": intake.Quota(1, 1, 1_000_000),
            "training": intake.Quota(1, 1, 1_000_000),
        }
        prior, _, _ = read_split("train", row(900, "held out"))
        self.prior = prior
        sources = {
            "test": (row(99, "off limits"),),
            "validation": (
                row(1, "held out"), row(2, "hold out"),
                row(3, "target", signer=" SIGNER-900 "), row(4, "past", clipFileMd5=f"{900:032x}"),
            ),
            "train": (
                row(10, "develop"), row(11, "held out"), row(12, "hold out"),
                row(13, "same target", signer="SIGNER-10 "),
                row(14, "linked clip", signer="signer-14", recording="RECORDING-10.mp4"),
                row(15, "linked media", signer="SIGNER-14 ", recording="recording-15.mp4"),
                row(16, "valid", clipFileMd5=f"{2:032x}"),
                row(17, "train now"),
            ),
        }
        self.candidates = {}
        self.groups = {}
        for split, rows in sources.items():
            self.candidates[split], _, self.groups[split] = read_split(split, *rows)

    def test_uses_two_official_pools_with_transitive_development_group_isolation(self):
        chosen, reports = task.propose(self.candidates, self.groups, self.prior, self.quotas)
        self.assertEqual({role: clips[0]["reference"] for role, clips in chosen.items()}, {
            "holdout": "hold out", "development": "develop", "training": "train now",
        })
        self.assertTrue(all(sum(report["shortfalls"].values()) == 0 for report in reports.values()))
        reordered = {split: list(reversed(clips)) for split, clips in self.candidates.items()}
        self.assertEqual(task.propose(reordered, self.groups, self.prior, self.quotas), (chosen, reports))

    def test_explicit_role_map_and_source_roots_fail_closed(self):
        self.candidates["validation"].append(self.candidates["train"][0])
        with self.assertRaisesRegex(admission.AdmissionError, "publisher provenance"):
            task.propose(self.candidates, self.groups, self.prior, self.quotas)

    def test_official_test_signer_overlap_fails_closed(self):
        self.groups["test"].add(("signer-17", "new-recording.mp4"))
        with self.assertRaisesRegex(admission.AdmissionError, "publisher split overlap"):
            task.propose(self.candidates, self.groups, self.prior, self.quotas)

    def test_reports_shortfalls_without_reassigning_official_test(self):
        self.candidates["validation"] = []
        _, reports = task.propose(self.candidates, self.groups, self.prior, self.quotas)
        self.assertEqual(reports["holdout"]["shortfalls"]["clips"], 1)
        self.assertEqual(reports["holdout"]["shortfalls"]["signers"], 1)


class FrozenProtocolTest(unittest.TestCase):
    def test_v4_freezes_the_variable_still_policy_under_a_new_experiment_id(self):
        protocol = task.frozen_protocol()
        self.assertEqual(protocol["experiment_id"], "sts-fsboard-whole-prompt-static-ablation-20261119-v4")
        self.assertEqual(protocol["selection"]["annotation_duration_seconds"], [0.5, 3.2])
        self.assertEqual(protocol["input"], {
            "native_video": False,
            "min_ordered_rgb_stills": 8,
            "max_ordered_rgb_stills": 32,
            "sampling": "distinct_pts_uniform_no_padding",
            "max_frame_edge": 384,
            "dense_max_sequence_tokens": 6144,
        })

    def test_two_full_shared_video_passes_and_fixed_static_cycle(self):
        protocol = task.frozen_protocol()
        training = protocol["training"]
        self.assertEqual(training["required_admitted_video_clips"], 192)
        self.assertEqual(training["video_epochs"], 2)
        self.assertEqual(training["optimizer_steps_per_arm"], 192)
        self.assertEqual(training["matching_video_microbatches_per_update"], 2)
        self.assertEqual(training["matching_video_microbatches_per_arm"], 384)
        self.assertEqual(training["A"]["static_microbatches_per_update"], 0)
        self.assertEqual(training["B"]["static_microbatches_per_update"], 1)
        self.assertEqual(training["B"]["static_loss_weight"], 0.05)
        self.assertEqual(training["B"]["prior_static_row_count"], 80)
        self.assertEqual(training["B"]["prior_schedule_sha256"],
                         "6b1980c513f627f3b4bad9cec92c6cda951f0e1fe77fececeb0b319d3f8f27fe")
        self.assertEqual(protocol["input"]["dense_max_sequence_tokens"], 6144)
        self.assertEqual(protocol["evaluation"]["max_new_tokens"], 32)

    def test_freezes_distinct_temporal_controls_and_keeps_runtime_pending(self):
        protocol = task.frozen_protocol()
        evaluation = protocol["evaluation"]
        controls = evaluation["conditions"]
        self.assertEqual(list(controls), ["matched", "reordered", "repeated_middle_frame", "black", "wrong_video"])
        self.assertEqual(controls["matched"], {"source": "same_clip", "frame_indices": "range(N)"})
        self.assertEqual(controls["reordered"], {
            "source": "same_clip", "frame_indices": "reversed(range(N))",
        })
        self.assertEqual(controls["repeated_middle_frame"], {
            "source": "same_clip", "frame_indices": "[N // 2] * N",
        })
        self.assertEqual(controls["wrong_video"]["source"], "different_clip")
        self.assertFalse(protocol["input"]["native_video"])
        self.assertFalse(protocol["training"]["runtime_hook_implemented"])
        self.assertFalse(evaluation["runtime_hook_implemented"])

    def test_training_count_guard_requires_the_exact_admitted_cohort(self):
        self.assertEqual(task.planned_optimizer_steps(192), 192)
        for count in (191, 193, 192.0):
            with self.subTest(count=count):
                with self.assertRaisesRegex(admission.AdmissionError, "exactly 192 admitted"):
                    task.planned_optimizer_steps(count)


class PublicationTest(unittest.TestCase):
    def setUp(self):
        staging = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(staging.cleanup)
        self.output = Path(staging.name) / "proposal"
        self.summary = {"status": "metadata_proposal"}
        self.records = {"holdout": [{"reference": "private target", "signer_id": "private signer"}]}

    def test_publishes_only_aggregate_and_digest_to_summary(self):
        result = task.publish_proposal(self.output, self.summary, self.records)
        public = (self.output / "summary.json").read_text(encoding="utf-8")
        private = (self.output / "private-proposal.json").read_bytes()
        self.assertEqual(json.loads(public), result)
        self.assertEqual(json.loads(private)["clips"], self.records)
        self.assertEqual(result["private_proposal_sha256"], hashlib.sha256(private).hexdigest())
        self.assertNotIn("private target", public)
        self.assertNotIn("private signer", public)

    def test_failure_and_cancellation_leave_no_output_and_allow_retry(self):
        for interruption in (OSError, KeyboardInterrupt):
            with self.subTest(interruption=interruption):
                with patch.object(task, "write_json", side_effect=[None, interruption]):
                    with patch.object(Path, "read_bytes", return_value=b"private"):
                        with self.assertRaises(interruption):
                            task.publish_proposal(self.output, self.summary, self.records)
                self.assertEqual(list(self.output.parent.iterdir()), [])
        task.publish_proposal(self.output, self.summary, self.records)

    def test_concurrent_publication_preserves_existing_destination(self):
        original = task.write_json

        def another_writer(path, payload):
            original(path, payload)
            if path.name == "summary.json":
                self.output.mkdir()
                (self.output / "keep").write_text("existing", encoding="utf-8")

        with patch.object(task, "write_json", side_effect=another_writer):
            with self.assertRaisesRegex(admission.AdmissionError, "output already exists"):
                task.publish_proposal(self.output, self.summary, self.records)
        self.assertEqual((self.output / "keep").read_text(encoding="utf-8"), "existing")
        self.assertEqual(list(self.output.parent.iterdir()), [self.output])


if __name__ == "__main__":
    unittest.main()
