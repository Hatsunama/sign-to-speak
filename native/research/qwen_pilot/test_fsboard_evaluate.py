"""Regression tests for the private FSboard evaluator."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

import fsboard_evaluate as evaluation
from test_train_qwen_temporal import dense_admission


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def write_json(path, document):
    path.write_text(json.dumps(document), encoding="utf-8")
    return digest(path.read_bytes())


class Scores(unittest.TestCase):
    def test_character_errors_include_insertion_deletion_substitution_and_punctuation(self):
        self.assertEqual(evaluation.edit_distance("kitten", "sitting"), 3)
        self.assertEqual(evaluation.score("", "word")["edits"], 4)
        self.assertEqual(evaluation.score("answer: word", "word")["edits"], 8)
        self.assertFalse(evaluation.score("word.", "word")["exact"])
        self.assertFalse(evaluation.score("unknown", "word")["exact"])

    def test_unicode_case_whitespace_and_reference_weighting(self):
        self.assertTrue(evaluation.score("  ï¼¡\n StraÃŸe\t", "a STRASSE")["exact"])
        first = evaluation.score("ab", "a")
        second = evaluation.score("bbbbbbbbb", "bbbbbbbbb")
        rows = [{"scores": scores, "stop": "eos", "output_equals_matched": True}
                for scores in (first, second)]
        self.assertEqual(evaluation.aggregate(rows)["cer"], 0.1)
        self.assertEqual(evaluation.aggregate(rows)["exact_matches"], 1)


class AdmittedClips(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.path = self.root / "admission.json"
        self.clips = []
        for number, reference in enumerate(("word", "text", "read", "sign"), 1):
            clip_id = f"fsboard-{number:02d}"
            frames_root = self.root / "frames" / clip_id
            frames_root.mkdir(parents=True)
            frames = []
            for slot in range(32):
                frame_path = frames_root / f"{slot:02d}.png"
                Image.new("RGB", (28, 28), (number, slot, 123)).save(frame_path)
                seconds = slot / 31
                frames.append({
                    "path": frame_path.relative_to(self.root).as_posix(),
                    "sha256": digest(frame_path.read_bytes()),
                    "width": 28, "height": 28, "duplicate": False,
                    "frame_index": slot, "requested_seconds": seconds,
                    "actual_seconds": seconds, "source_pts_seconds": seconds,
                    "annotation_offset_seconds": seconds,
                })
            self.clips.append({
                "id": clip_id, "role": "evaluation_only", "status": "review_required",
                "split": "daun_v3-test", "reference": reference, "signer_id": f"signer-{number}",
                "full_video_filename": f"long-{number}.mp4", "clip_filename": f"clip-{number}.mp4",
                "source_path": f"daun_v3/video_clips/daun_v3-test/clip-{number}.mp4",
                "original_sha256": digest(bytes([number])), "frames": frames,
                "clip_pts_origin_seconds": 0.0, "frame_period_seconds": 1 / 31,
                "decoded_duration_seconds": 1.03,
                "clip_start_full_video_seconds": 0, "clip_end_full_video_seconds": 1.03,
                "annotation_start_full_video_seconds": 0, "annotation_end_full_video_seconds": 1,
                "annotation_start_clip_seconds": 0, "annotation_end_clip_seconds": 1,
            })
        self.document = {
            "schema_version": 1, "role": "evaluation_only", "status": "evaluation_eligible",
            "dataset": {"id": "googleai/fsboard", "version": 13, "split": "daun_v3-test",
                        "license": "CC-BY-4.0"},
            "frame_count": 32, "max_frame_edge": 384,
            "verified_count": len(self.clips), "clips": self.clips,
        }

    def load(self):
        return evaluation.load_admission(self.path, write_json(self.path, self.document))

    def test_recomputes_duration_tolerance_from_admitted_timestamp_tick(self):
        clip = self.clips[0]
        clip.update(
            clip_end_full_video_seconds=4.000757,
            decoded_duration_seconds=363_070 / 90_000,
            frame_period_seconds=1 / 30,
        )
        with self.assertRaisesRegex(ValueError, "Invalid approved annotation or clip timing"):
            self.load()

        clip["decoded_timestamp_tick_seconds"] = 1 / 90_000
        self.load()

        clip["decoded_duration_seconds"] = 363_130 / 90_000
        clip["timing_tolerance_seconds"] = 1
        with self.assertRaisesRegex(ValueError, "Invalid approved annotation or clip timing"):
            self.load()

    def test_rejects_invalid_timestamp_ticks(self):
        clip = self.clips[0]
        for tick in (True, "1/90000", float("nan"), float("inf"), -1e-5, 0, 0.034):
            with self.subTest(tick=tick), self.assertRaisesRegex(ValueError, "timestamp tick"):
                evaluation.validate_timing(
                    {**clip, "decoded_timestamp_tick_seconds": tick}, clip["frames"],
                )

    def test_refuses_review_only_or_unpinned_admission(self):
        self.document["status"] = "review_required"
        with self.assertRaisesRegex(ValueError, "evaluation_eligible"):
            self.load()
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            evaluation.load_admission(self.path, "0" * 64)

    def test_refuses_unsafe_path_and_duplicate_id(self):
        frame = self.clips[0]["frames"][0]
        original = frame["path"]
        frame["path"] = "../00.png"
        with self.assertRaisesRegex(ValueError, "Frame path"):
            self.load()
        frame["path"] = original
        self.clips[1]["id"] = self.clips[0]["id"].upper()
        with self.assertRaisesRegex(ValueError, "Duplicate clip"):
            self.load()

    def test_refuses_extra_repeated_and_corrupt_frames(self):
        frames = self.clips[0]["frames"]
        frames.append(frames[0])
        with self.assertRaisesRegex(ValueError, "32 frames"):
            self.load()
        frames.pop()
        frame = frames[1]
        previous_pts = frame["source_pts_seconds"]
        frame["source_pts_seconds"] = 0
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            self.load()
        frame["source_pts_seconds"] = previous_pts
        frame["duplicate"] = True
        with self.assertRaisesRegex(ValueError, "duplicate flag"):
            self.load()
        frame["duplicate"] = False
        (self.root / frame["path"]).write_bytes(b"corrupted")
        with self.assertRaisesRegex(ValueError, "Frame digest"):
            self.load()

    def test_blocks_known_train_or_development_overlap(self):
        clips = self.load()["clips"]
        for field, value in (
            ("id", clips[0]["id"].upper()), ("sha256", clips[0]["original_sha256"]),
            ("source_path", clips[0]["source_path"]), ("source_group", clips[0]["full_video_filename"]),
            ("sha256", clips[0]["frames"][0]["sha256"]),
        ):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "overlap"):
                evaluation.check_holdout(clips, [{field: value}])

    def test_counterfactuals_keep_common_prompt_and_change_only_images(self):
        clips = self.load()["clips"]
        donors = evaluation.assign_donors(clips)
        self.assertEqual(len({donor["id"] for donor in donors.values()}), len(clips))
        target = clips[0]
        donor = donors[target["id"]]
        inputs = evaluation.condition_images(Image, self.root, target, donor)
        self.assertEqual(tuple(inputs), evaluation.CONDITIONS)
        matched, reordered, black, wrong = [inputs[key][0] for key in inputs]
        self.assertEqual([image.getpixel((0, 0))[1] for image in matched], list(range(32)))
        self.assertCountEqual([image.tobytes() for image in matched], [image.tobytes() for image in reordered])
        self.assertNotEqual([image.tobytes() for image in matched], [image.tobytes() for image in reordered])
        self.assertTrue(all(image.getbbox() is None for image in black))
        self.assertEqual([image.size for image in matched], [image.size for image in black])
        self.assertNotEqual(target["signer_id"], donor["signer_id"])
        self.assertNotEqual(target["full_video_filename"], donor["full_video_filename"])
        self.assertNotEqual(target["reference"], donor["reference"])
        self.assertNotEqual([image.tobytes() for image in matched], [image.tobytes() for image in wrong])
        for images, _ in inputs.values():
            content = evaluation.inference_messages(images)[0]["content"]
            self.assertEqual(content[-1], {"type": "text", "text": evaluation.PROMPT})
            self.assertEqual(len(content), 33)


class PairValidation(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.pair = self.root / "run"
        self.pair.mkdir()
        self.dense_path = self.root / "custom" / "chosen.json"
        self.dense_path.parent.mkdir()
        self.dense = dense_admission()
        self.dense["orientation_policy"] = "validated-displaymatrix-right-angle-v1"
        for clip in self.dense["clips"]:
            for frame in clip["frames"]:
                frame.update(display_rotation_degrees=-90, applied_rotation_degrees_ccw=-90)

    def write_complete_pair(self):
        dense_sha = write_json(self.dense_path, self.dense)
        experiment = {
            "model": self.dense["model"], "dataset": self.dense["dataset"],
            "dense_admission_sha256": dense_sha,
            "static_manifest_sha256": "b" * 64, "planned_optimizer_steps_per_arm": 3,
            "training_clips": [clip["id"] for clip in self.dense["clips"] if clip["split"] == "train"],
            "development_controls": [clip["id"] for clip in self.dense["clips"] if clip["split"] == "holdout"],
        }
        arms = {}
        for name in ("A", "B"):
            adapter = self.pair / name / "adapter"
            adapter.mkdir(parents=True)
            write_json(adapter / "adapter_config.json", {"base_model_name_or_path": self.dense["model"]["id"]})
            (adapter / "adapter_model.safetensors").write_bytes(f"adapter {name}".encode())
            arm = {"optimizer_steps": 3, "adapter_sha256": evaluation.paired.adapter_digest(adapter)}
            write_json(adapter.parent / "summary.json", arm)
            arms[name] = arm
        write_json(self.pair / "success.json", {
            "status": "completed_exploratory_pair", "evaluation_role": "development_controls", "arms": arms,
        })
        write_json(self.pair / "experiment.json", experiment)
        return experiment

    def test_partial_pair_rejected_even_with_experiment_file(self):
        write_json(self.pair / "experiment.json", {})
        with self.assertRaisesRegex(ValueError, "completed A/B"):
            evaluation.load_pair(self.pair, self.dense_path)
        write_json(self.pair / "success.json", {
            "status": "completed_exploratory_pair", "arms": {"A": {}},
        })
        with self.assertRaisesRegex(ValueError, "completed A/B"):
            evaluation.load_pair(self.pair, self.dense_path)

    def test_accepts_selected_dense_manifest_only_when_its_bytes_match_pair_pin(self):
        experiment = self.write_complete_pair()
        receipt, training, development, adapters, _ = evaluation.load_pair(self.pair, self.dense_path)
        self.assertEqual(receipt, experiment)
        self.assertEqual((len(training), len(development)), (16, 4))
        self.assertEqual(set(adapters), {"A", "B"})
        self.assertEqual(len(evaluation.PROFILE.matched), 32)
        alternate = self.dense_path.with_name("other.json")
        alternate.write_bytes(self.dense_path.read_bytes() + b"\\n")
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            evaluation.load_pair(self.pair, alternate)

    def test_rejects_pair_pinned_to_legacy_dense_admission(self):
        self.dense.pop("orientation_policy")
        for clip in self.dense["clips"]:
            for frame in clip["frames"]:
                frame.pop("display_rotation_degrees")
                frame.pop("applied_rotation_degrees_ccw")
        self.write_complete_pair()
        with self.assertRaisesRegex(ValueError, "orientation"):
            evaluation.load_pair(self.pair, self.dense_path)

    def test_corrupt_adapters_rejected(self):
        self.write_complete_pair()
        (self.pair / "A/adapter/adapter_model.safetensors").write_bytes(b"corrupted")
        with self.assertRaisesRegex(ValueError, "Adapter digest mismatch"):
            evaluation.load_pair(self.pair, self.dense_path)


if __name__ == "__main__":
    unittest.main()
