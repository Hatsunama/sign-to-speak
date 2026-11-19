"""CPU regressions for the post hoc static-letter comparison."""
from types import SimpleNamespace
import unittest

import paired_experiment as paired
import static_diagnostic as diagnostic
from test_fsboard_evaluate import PairValidation, write_json


def approved_rows():
    labels = {letter: 5 for letter in sorted(paired.STATIC_LETTERS)}
    for letter in "ABCD":
        labels[letter] -= 1
    rows = {}
    for letter, count in labels.items():
        for index in range(count):
            item_id = f"{letter}-{index}"
            rows[item_id] = {
                "id": item_id, "label": letter, "sha256": paired.sha256(item_id.encode()),
                "path": f"images/{item_id}.png", "role": "training_eligible",
                "source_group": "one-static-photo-family", "width": 64, "height": 64,
            }
    return rows


class StaticSelection(unittest.TestCase):
    def test_one_per_present_letter_deduplicates_exposure_and_excludes_absent_unused_classes(self):
        rows = approved_rows()
        sampled = [item_id for item_id, row in rows.items() if row["label"] in "AB" or item_id.endswith("-0")]
        seen_ids, selected = diagnostic.select_rows(rows, sampled + sampled[:4])
        self.assertEqual(len(seen_ids), len(sampled))
        self.assertEqual(len(selected["seen"]), 24)
        self.assertEqual(len(selected["unused_eligible"]), 22)
        seen_selection = {row["id"] for row in selected["seen"]}
        unused_selection = {row["id"] for row in selected["unused_eligible"]}
        self.assertTrue(seen_selection <= seen_ids)
        self.assertTrue(unused_selection <= rows.keys() - seen_ids)
        self.assertTrue(seen_selection.isdisjoint(unused_selection))
        self.assertEqual({row["label"] for row in selected["unused_eligible"]}, paired.STATIC_LETTERS - {"A", "B"})
        reversed_rows = dict(reversed(list(rows.items())))
        self.assertEqual(diagnostic.select_rows(reversed_rows, reversed(sampled)), (seen_ids, selected))

    def test_unknown_schedule_id_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unapproved"):
            diagnostic.select_rows(approved_rows(), ["unapproved-photo"])

    def test_exact_letter_scoring_retains_additional_text_and_punctuation(self):
        self.assertTrue(diagnostic.exact_letter(" \nａ\t ", "A"))
        for answer in ("A.", "It is A", "A B", "A\nA", ""):
            with self.subTest(answer=answer):
                self.assertFalse(diagnostic.exact_letter(answer, "A"))

    def test_aggregates_keep_absent_groups_and_stop_counts_visible(self):
        summary = diagnostic.summarize([{"group": "seen", "exact": True, "stop": "max_tokens"}])
        self.assertEqual(summary["seen"], {
            "examples": 1, "exact_matches": 1, "exact_match_rate": 1.0,
            "stop_counts": {"eos": 0, "max_tokens": 1},
        })
        self.assertEqual(summary["unused_eligible"]["examples"], 0)
        self.assertIsNone(summary["unused_eligible"]["exact_match_rate"])

    def test_scope_records_same_source_training_pool_and_post_hoc_selection(self):
        self.assertIn("post hoc", diagnostic.SCOPE)
        self.assertIn("training-eligible", diagnostic.SCOPE)
        self.assertIn("same source family", diagnostic.SCOPE)
        self.assertIn("unknown", diagnostic.SCOPE)
        self.assertIn("preregistered real-video", diagnostic.SCOPE)


class DiagnosticAdmission(unittest.TestCase):
    def setUp(self):
        fixture = PairValidation()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.fixture = fixture
        static = fixture.root / "static.json"
        write_json(static, {"schema_version": 1, "role": "training_eligible", "samples": list(approved_rows().values())})
        self.args = SimpleNamespace(pair_root=fixture.pair, dense_admission=fixture.dense_path, static_manifest=static)

    def complete_pair(self):
        experiment = self.fixture.write_complete_pair()
        schedule = {
            "A": [{"video_ids": ["clip"], "static_id": None}] * 3,
            "B": [{"video_ids": ["clip"], "static_id": item} for item in ("A-0", "B-0", "A-0")],
        }
        experiment.update(
            static_manifest_sha256=paired.sha256(self.args.static_manifest.read_bytes()),
            schedule_sha256=write_json(self.fixture.pair / "schedule.json", schedule),
            static_prompt=paired.STATIC_PROMPT,
        )
        write_json(self.fixture.pair / "experiment.json", experiment)

    def test_completion_and_both_input_digests_are_required(self):
        with self.assertRaisesRegex(ValueError, "completed A/B"):
            diagnostic.load_inputs(self.args)
        self.complete_pair()
        _, _, _, _, selected, seen_ids = diagnostic.load_inputs(self.args)
        self.assertEqual(seen_ids, {"A-0", "B-0"})
        self.assertEqual({row["id"] for row in selected["seen"]}, seen_ids)
        for path in (self.args.dense_admission, self.args.static_manifest, self.fixture.pair / "schedule.json"):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b"\n")
                    with self.assertRaisesRegex(ValueError, "digest mismatch"):
                        diagnostic.load_inputs(self.args)
                finally:
                    path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
