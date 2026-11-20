"""Exercise the frozen variable-frame evaluation controls with synthetic clips."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import fsboard_task_controls as controls
from fsboard_admission import AdmissionError


def fixture(index, count, *, root=None):
    clip_id = f"holdout-{index:03d}"
    reference = "".join("abcdef"[digit] for digit in (
        (index // 36) % 6, (index // 6) % 6, index % 6,
    ))
    clip = {
        "id": clip_id, "role": "holdout", "signer_id": f"synthetic-signer-{index}",
        "full_video_filename": f"synthetic-recording-{index}.mp4", "reference": reference,
        "original_sha256": hashlib.sha256(clip_id.encode("utf-8")).hexdigest(),
        "sampled_frame_count": count, "frames": [],
        "clip_start_full_video_seconds": 0, "annotation_start_full_video_seconds": 0.2,
        "annotation_end_full_video_seconds": 1.8, "clip_pts_origin_seconds": 0,
        "annotation_start_clip_seconds": 0.2, "annotation_end_clip_seconds": 1.8,
        "frame_period_seconds": 0.1,
    }
    if root is not None:
        (root / "frames" / clip_id).mkdir(parents=True)
    for slot in range(count):
        relative = f"frames/{clip_id}/{slot:02d}.png"
        size = (index + 1, 3 + slot % 2)
        timestamp = 0.2 + 1.6 * slot / (count - 1)
        payload = relative.encode("utf-8")
        if root is not None:
            path = root / relative
            with Image.new("RGB", size, (slot + 1, index, 9)) as image:
                image.save(path)
            payload = path.read_bytes()
        clip["frames"].append({
            "path": relative, "width": size[0], "height": size[1],
            "sha256": hashlib.sha256(payload).hexdigest(), "frame_index": slot,
            "duplicate": False, "requested_seconds": timestamp, "actual_seconds": timestamp,
            "source_pts_seconds": timestamp, "annotation_offset_seconds": timestamp - 0.2,
        })
    return clip


class PlanningTests(unittest.TestCase):
    def test_selects_each_actual_count_and_reuses_donors(self):
        clips = [fixture(1, 8), fixture(2, 17), fixture(3, 32), fixture(4, 32)]
        before = deepcopy(clips)
        plan = controls.plan_controls(clips)
        self.assertEqual(clips, before)
        self.assertEqual(controls.plan_controls(reversed(clips)), plan)
        self.assertEqual(plan["donor_usage"], {
            "holdout-001": 0, "holdout-002": 1, "holdout-003": 2, "holdout-004": 1,
        })
        for clip in clips:
            count = clip["sampled_frame_count"]
            with self.subTest(count=count):
                conditions = plan["examples"][clip["id"]]["conditions"]
                self.assertEqual(conditions["matched"]["frame_indices"], list(range(count)))
                self.assertEqual(conditions["reordered"]["frame_indices"], list(reversed(range(count))))
                self.assertEqual(conditions["repeated_middle_frame"]["frame_indices"], [count // 2] * count)
                self.assertEqual(conditions["black"]["frame_indices"], list(range(count)))
                wrong = conditions["wrong_video"]
                donor = next(row for row in clips if row["id"] == wrong["input_clip_id"])
                last = donor["sampled_frame_count"] - 1
                self.assertEqual(len(set(wrong["frame_indices"])), count)
                self.assertEqual(wrong["frame_indices"], sorted(wrong["frame_indices"]))
                self.assertEqual((wrong["frame_indices"][0], wrong["frame_indices"][-1]), (0, last))
                for index, slot in enumerate(wrong["frame_indices"]):
                    self.assertLessEqual(abs(slot - index * last / (count - 1)), 0.5)
                self.assertEqual(wrong["ordered_png_sha256"],
                                 [donor["frames"][slot]["sha256"] for slot in wrong["frame_indices"]])

    def test_excludes_normalized_collisions_and_short_donors(self):
        for field in ("signer_id", "full_video_filename", "reference"):
            target, alias, short, donor, alternate = (
                fixture(1, 17), fixture(2, 32), fixture(3, 8), fixture(4, 32), fixture(5, 32),
            )
            alias[field] = f" {target[field].upper()} "
            clips = [target, alias, short, donor, alternate]
            with self.subTest(field=field):
                plan = controls.plan_controls(clips)
                wrong = plan["examples"][target["id"]]["conditions"]["wrong_video"]
                self.assertEqual(wrong["input_clip_id"], donor["id"])

    def test_rejects_missing_donor_and_invalid_clips(self):
        sample = fixture(1, 32)
        for alias in (fixture(2, 8), {**fixture(2, 32), "original_sha256": sample["original_sha256"]}):
            with self.assertRaisesRegex(AdmissionError, "no eligible wrong-video donor"):
                controls.plan_controls([sample, alias])
        with self.assertRaisesRegex(AdmissionError, "duplicate"):
            controls.plan_controls([sample, deepcopy(sample)])
        for changes in (
            {"sampled_frame_count": True}, {"sampled_frame_count": 31}, {"role": "development"},
            {"signer_id": " "}, {"reference": 1}, {"id": "holdout-../001"},
            {"full_video_filename": None}, {"original_sha256": "invalid"},
            {"frames": [None] * 32},
        ):
            with self.subTest(changes=changes), self.assertRaises(AdmissionError):
                controls.plan_controls([{**sample, **changes}])
        for count in (7, 33):
            with self.subTest(count=count), self.assertRaisesRegex(AdmissionError, "frame count"):
                controls.plan_controls([fixture(1, count)])

    def test_full_holdout_with_reuse_only_donors(self):
        clips = [fixture(index, 8 + index % 24) for index in range(1, 63)]
        clips += [fixture(63, 32), fixture(64, 32)]
        for clip in clips[:-2]:
            clip["signer_id"] = "synthetic-shared-signer"
        plan = controls.plan_controls(clips)
        self.assertEqual(len(plan["examples"]), 64)
        self.assertEqual(sum(plan["donor_usage"].values()), 64)
        self.assertGreaterEqual(min(plan["donor_usage"][clip["id"]] for clip in clips[-2:]), 31)
        self.assertEqual(controls.plan_controls(reversed(clips)), plan)


class MaterializationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.target = fixture(1, 8, root=self.root)
        self.donor = fixture(2, 32, root=self.root)
        self.alternate = fixture(3, 32, root=self.root)
        self.clips = {clip["id"]: clip for clip in (self.target, self.donor, self.alternate)}
        self.plan = controls.plan_controls(self.clips.values())
        self.clip_id = self.target["id"]

    def test_concrete_rgb_conditions_preserve_donor_and_target_geometry_without_writing(self):
        files_before = {path: path.read_bytes() for path in self.root.rglob("*.png")}
        with controls.condition_images(self.root, self.clips, self.plan, self.clip_id) as images:
            self.assertEqual(set(images), {"matched", "reordered", "repeated_middle_frame", "black", "wrong_video"})
            def red(name):
                return [image.getpixel((0, 0))[0] for image in images[name]]

            self.assertEqual(red("matched"), list(range(1, 9)))
            self.assertEqual(red("reordered"), list(range(8, 0, -1)))
            self.assertEqual(red("repeated_middle_frame"), [5] * 8)
            self.assertEqual(red("wrong_video"), [1, 5, 10, 14, 19, 23, 28, 32])
            for matched, black in zip(images["matched"], images["black"], strict=True):
                self.assertEqual(black.size, matched.size)
                self.assertEqual(black.mode, "RGB")
                self.assertEqual(black.getextrema(), ((0, 0),) * 3)
            for condition, frames in images.items():
                with self.subTest(condition=condition):
                    details = self.plan["examples"][self.clip_id]["conditions"][condition]
                    self.assertEqual(details["rgb_sizes"], [list(frame.size) for frame in frames])
                    self.assertEqual(details["geometry_differs_from_matched"], condition not in ("matched", "black"))
        self.assertEqual({path: path.read_bytes() for path in self.root.rglob("*.png")}, files_before)

    def test_changed_plan_metadata_and_media_fail_before_yield(self):
        changed = deepcopy(self.plan)
        changed["examples"][self.clip_id]["conditions"]["wrong_video"]["frame_indices"][0] = 1
        cases = [
            (changed, self.clips, "plan"),
            (self.plan, {**self.clips, self.clip_id: {**self.target, "signer_id": "changed"}}, "binding"),
        ]
        for plan, clips, diagnostic in cases:
            with self.subTest(diagnostic=diagnostic), self.assertRaisesRegex(AdmissionError, diagnostic):
                with controls.condition_images(self.root, clips, plan, self.clip_id):
                    self.fail("stale plan yielded images")
        wrong = self.plan["examples"][self.clip_id]["conditions"]["wrong_video"]
        path = self.root / self.clips[wrong["input_clip_id"]]["frames"][0]["path"]
        path.write_bytes(path.read_bytes() + b"tampered")
        with self.assertRaisesRegex(AdmissionError, "SHA-256"):
            with controls.condition_images(self.root, self.clips, self.plan, self.clip_id):
                self.fail("tampered media yielded images")

    def test_verify_plan_catches_an_unselected_clip_change_after_json_round_trip(self):
        plan = json.loads(json.dumps(self.plan, sort_keys=True))
        controls.verify_plan(self.clips.values(), plan)
        self.alternate["frames"][0]["sha256"] = "c" * 64
        with self.assertRaisesRegex(AdmissionError, "frozen holdout"):
            controls.verify_plan(self.clips.values(), plan)

    def test_closes_each_owned_image_once_on_cancellation(self):
        retained = []
        closes = []
        original_close = Image.Image.close
        original_load = controls.load_frames

        def record_frames(*args, **kwargs):
            frames = original_load(*args, **kwargs)
            retained.extend(frames)
            return frames

        def record_close(image):
            closes.append(id(image))
            original_close(image)

        with patch.object(Image.Image, "close", record_close), patch.object(
            controls, "load_frames", side_effect=record_frames,
        ):
            with self.assertRaises(KeyboardInterrupt):
                with controls.condition_images(self.root, self.clips, self.plan, self.clip_id) as images:
                    retained.extend(images["black"])
                    raise KeyboardInterrupt
        self.assertEqual(len(retained), 48)
        for image in retained:
            self.assertEqual(closes.count(id(image)), 1)

    def test_closes_target_images_when_donor_loading_fails(self):
        matched = controls.load_frames(self.root, self.target)
        with patch.object(controls, "load_frames", side_effect=[matched, AdmissionError("donor failure")]):
            with self.assertRaisesRegex(AdmissionError, "donor failure"):
                with controls.condition_images(self.root, self.clips, self.plan, self.clip_id):
                    self.fail("failed donor yielded images")
        for image in matched:
            with self.assertRaises(ValueError):
                image.getpixel((0, 0))
