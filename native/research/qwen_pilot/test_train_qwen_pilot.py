import hashlib
import unittest

from train_qwen_pilot import (
    DATASET_ID,
    DATASET_REVISION,
    INSTRUCTION,
    MODEL_ID,
    completion_labels,
    inference_messages,
    validate_admission,
    visual_conditions,
)


def admission():
    clips = []
    for index, split in enumerate(("train", "holdout")):
        clip_id = f"clip{index}"
        clips.append({
            "split": split, "id": clip_id,
            "sha256": hashlib.sha256(clip_id.encode()).hexdigest(),
            "source_group": f"group{index}", "source_path": f"dir/{clip_id}.mp4",
            "reference": f"English sentence {index}.",
            "frames": [
                {"path": f"frames/{clip_id}/{i:02d}.png",
                 "requested_seconds": i / 10, "actual_seconds": i / 10,
                 "duplicate": False}
                for i in range(8)
            ],
        })
    return {
        "schema_version": 1, "ready": True, "status": "ready",
        "model": {"id": MODEL_ID, "revision": "a" * 40,
                  "model_type": "qwen3_5", "pipeline_tag": "image-text-to-text"},
        "dataset": {"id": DATASET_ID, "revision": DATASET_REVISION,
                    "license": "cc-by-sa-4.0"},
        "counts": {"train": 1, "holdout": 1},
        "clips": clips,
    }


class TrainingHelpersTest(unittest.TestCase):
    def test_masks_prompt_and_images_through_assistant_header(self):
        prefix = [1, 70, 71, 71, 72, 15, 18]
        full = prefix + [80, 81, 3, 12]
        self.assertEqual(completion_labels(prefix, full, 3),
                         [-100] * len(prefix) + [80, 81, 3, -100])

    def test_rejects_mismatched_prefix_missing_eos_and_empty_target(self):
        for full in ([9, 2, 5, 3], [1, 2, 5], [1, 2, 3]):
            with self.subTest(full=full), self.assertRaises(ValueError):
                completion_labels([1, 2], full, 3)

    def test_inference_uses_only_ordered_images_and_fixed_instruction(self):
        images = [object() for _ in range(4)]
        messages = inference_messages(images)
        self.assertEqual([message["role"] for message in messages], ["user"])
        content = messages[0]["content"]
        self.assertEqual(content[:-1],
                         [{"type": "image", "image": image} for image in images])
        self.assertEqual(content[-1], {"type": "text", "text": INSTRUCTION})

    def test_accepts_ready_admission_with_disjoint_splits(self):
        train, holdout = validate_admission(admission())
        self.assertEqual([clip["id"] for clip in train], ["clip0"])
        self.assertEqual([clip["id"] for clip in holdout], ["clip1"])

    def test_rejects_empty_holdout_and_nonready_admission(self):
        empty = admission()
        empty["clips"].pop()
        empty["counts"]["holdout"] = 0
        not_ready = admission()
        not_ready["ready"] = False
        for document in (empty, not_ready):
            with self.assertRaises(ValueError):
                validate_admission(document)

    def test_rejects_leakage_and_unordered_or_external_frames(self):
        for field in ("id", "sha256", "source_group", "source_path"):
            document = admission()
            document["clips"][1][field] = document["clips"][0][field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_admission(document)
        document = admission()
        document["clips"][1]["frames"].reverse()
        with self.assertRaises(ValueError):
            validate_admission(document)
        document = admission()
        document["clips"][1]["frames"][0]["path"] = "../../private.png"
        with self.assertRaises(ValueError):
            validate_admission(document)

    def test_visual_controls_use_same_frames_reordered_or_blank(self):
        conditions = visual_conditions()
        self.assertEqual(conditions["matched"], (0, 2, 5, 7))
        self.assertCountEqual(conditions["shuffled"], conditions["matched"])
        self.assertTrue(all(a != b for a, b in zip(
            conditions["matched"], conditions["shuffled"])))
        self.assertIsNone(conditions["blank"])


if __name__ == "__main__":
    unittest.main()
