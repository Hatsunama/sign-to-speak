"""Synthetic contract checks for the private host-side comparison."""

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import holistic_phone_parity as parity


MODEL = {
    "bytes": 13683609,
    "sha256": "e2dab61191e2dcd0a15f943d8e3ed1dce13c82dfa597b9dd39f562975a50c3f8",
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return (json.dumps(value) + "\n").encode("utf-8")


def hands(index):
    return {
        "left_hand": [[0.125, 0.25, -0.125] for _ in range(21)],
        "right_hand": [[0.5, 0.75, 0.125] for _ in range(21)] if index % 2 else None,
    }


def inputs():
    raw_frames = [bytes([index, 0, 0, 255]) for index in range(19)]
    blob_hash = sha(b"".join(raw_frames))
    clip = {"bytes": 42, "sha256": sha(b"synthetic clip")}
    fixture = {
        "type": "holistic_rgba_replay_fixture",
        "schema_version": 1,
        "pixel_format": "RGBA_8888",
        "byte_order": "row_major_RGBA",
        "source_clip": clip.copy(),
        "source_model": MODEL.copy(),
        "frames_file": {
            "name": "holistic-parity.rgba",
            "bytes": 4 * len(raw_frames),
            "sha256": blob_hash,
        },
        "frame_count": len(raw_frames),
        "frames": [
            {
                "frame_index": 2 * position,
                "width": 1,
                "height": 1,
                "offset_bytes": 4 * position,
                "length_bytes": 4,
                "rgba_sha256": sha(raw),
                "source_pts": -7 + 3003 * position,
                "source_time_base": [1, 90000],
                "timestamp_ms": 3003 * position // 90,
            }
            for position, raw in enumerate(raw_frames)
        ],
    }
    reference = {
        "type": "holistic_video_extraction",
        "schema_version": 1,
        "status": "SUCCESS",
        "input": {"clip": clip.copy(), "model": MODEL.copy()},
        "runtime": {},
        "method": {},
        "coverage": {},
        "frame_counts": {"processed": len(raw_frames)},
        "rgba_fixture": {
            "manifest_sha256": "",
            "frames_sha256": blob_hash,
            "frame_count": len(raw_frames),
        },
        "frames": [
            {
                "status": "processed",
                "frame_index": frame["frame_index"],
                "source_pts": frame["source_pts"],
                "source_time_base": frame["source_time_base"].copy(),
                "timestamp_ms": frame["timestamp_ms"],
                "rgba_sha256": frame["rgba_sha256"],
                "input_size": [1, 1],
                "landmarks": {**hands(position), "pose": None, "face": None},
                "display_rotation_degrees_ccw": 0,
            }
            for position, frame in enumerate(fixture["frames"])
        ],
    }
    android = {
        "type": "phone_holistic_producer_parity",
        "schema_version": 1,
        "status": "COMPLETED",
        "run_id": "a" * 32,
        "fixture_manifest_sha256": "",
        "frames_sha256": blob_hash,
        "model_sha256": MODEL["sha256"],
        "input_mode": "canonical_rgba",
        "camera_path_tested": False,
        "runs": [],
    }
    for run_number, name in enumerate(("initial", "interrupted", "recovered")):
        replay = fixture["frames"][:17] if name == "interrupted" else fixture["frames"]
        android["runs"].append({
            "name": name,
            "detector_create_elapsed_ns": 100,
            "detector_closed": True,
            "frames": [
                {
                    "frame_index": frame["frame_index"],
                    "timestamp_ms": frame["timestamp_ms"],
                    "rgba_sha256": frame["rgba_sha256"],
                    "landmarks": hands(position),
                    "start_elapsed_ns": 10000 * (run_number + 1) + 100 * position,
                    "end_elapsed_ns": 10000 * (run_number + 1) + 100 * position + 20,
                    "elapsed_ns": 20,
                    "bitmap_consumed": True,
                }
                for position, frame in enumerate(replay)
            ],
        })
    return fixture, reference, android


class ParityTest(unittest.TestCase):
    def invoke(self, mutate=None, json_bytes=None, bad_hash=False, existing=False):
        fixture, reference, android = inputs()
        if mutate:
            mutate(fixture, reference, android)
        fixture_raw = encode(fixture)
        fixture_hash = sha(fixture_raw)
        if reference["rgba_fixture"]["manifest_sha256"] == "":
            reference["rgba_fixture"]["manifest_sha256"] = fixture_hash
        if android["fixture_manifest_sha256"] == "":
            android["fixture_manifest_sha256"] = fixture_hash
        raw_files = {"fixture": fixture_raw, "reference": encode(reference), "android": encode(android)}
        if json_bytes:
            name, transform = json_bytes
            raw_files[name] = transform(raw_files[name])
        errors = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "comparison.json"
            if existing:
                output.write_text("existing", encoding="utf-8")
            arguments = ["--output", str(output)]
            for name, raw in raw_files.items():
                path = Path(directory) / (name + ".json")
                path.write_bytes(raw)
                digest = "0" * 64 if bad_hash and name == "android" else sha(raw)
                arguments.extend(["--" + name, str(path), "--" + name + "-sha256", digest])
            with contextlib.redirect_stderr(errors):
                code = parity.main(arguments)
            if existing:
                self.assertEqual(output.read_text(encoding="utf-8"), "existing")
                return code, None, errors.getvalue()
            report = json.loads(output.read_bytes()) if output.exists() else None
        return code, report, errors.getvalue()

    def assert_invalid(self, mutate=None, message="", **kwargs):
        code, report, error = self.invoke(mutate, **kwargs)
        self.assertNotEqual(code, 0)
        self.assertIsNone(report)
        self.assertIn(message, error)

    def test_complete_replay_and_numeric_report(self):
        def change(fixture, reference, android):
            reference["frames"][0]["landmarks"]["left_hand"][0][0] += 0.0005

        code, report, error = self.invoke(change)
        self.assertEqual(code, 0, error)
        cross = report["coordinate_deltas"]["reference_vs_initial"]
        self.assertEqual(report["status"], "AGREEMENT")
        self.assertEqual(cross["absolute_coordinate_error"]["count"], (19 + 9) * 21 * 3)
        self.assertAlmostEqual(cross["absolute_coordinate_error"]["max"], 0.0005)
        self.assertGreater(cross["absolute_coordinate_error"]["mean"], 0)
        self.assertEqual(cross["absolute_coordinate_error"]["p95"], 0)
        self.assertEqual(report["android_timing_ns"]["interrupted"]["all_calls"]["count"], 17)
        self.assertEqual(report["android_timing_ns"]["initial"]["cold_call"], 20)

    def test_presence_and_threshold_fail_with_report(self):
        mutations = (
            ("absence", lambda f, r, a: r["frames"][0]["landmarks"].update(left_hand=None),
             "reference_vs_initial", 1),
            ("cross platform", lambda f, r, a: r["frames"][0]["landmarks"]["left_hand"][0].__setitem__(0, 0.13),
             "reference_vs_initial", 0),
            ("recovery", lambda f, r, a: a["runs"][2]["frames"][0]["landmarks"]["left_hand"][0].__setitem__(0, 0.12502),
             "initial_vs_recovered", 0),
            ("interruption", lambda f, r, a: a["runs"][1]["frames"][0]["landmarks"]["left_hand"][0].__setitem__(0, 0.12502),
             "initial_vs_interrupted", 0),
        )
        for name, mutate, comparison, missingness in mutations:
            with self.subTest(name=name):
                code, report, _ = self.invoke(mutate)
                self.assertNotEqual(code, 0)
                self.assertEqual(report["status"], "OUT_OF_TOLERANCE")
                self.assertEqual(len(report["coordinate_deltas"][comparison]["missingness_mismatches"]), missingness)
                self.assertFalse(report["coordinate_deltas"][comparison]["agreement"])

    def test_explicit_all_absent_is_inconclusive(self):
        def absent(fixture, reference, android):
            groups = [reference["frames"], *(run["frames"] for run in android["runs"])]
            for group in groups:
                for frame in group:
                    frame["landmarks"].update(left_hand=None, right_hand=None)

        code, report, _ = self.invoke(absent)
        self.assertNotEqual(code, 0)
        self.assertEqual(report["status"], "INCONCLUSIVE_NO_HAND_OBSERVATIONS")
        comparison = report["coordinate_deltas"]["reference_vs_initial"]
        self.assertEqual(comparison["missingness_mismatches"], [])
        self.assertFalse(comparison["agreement"])
        self.assertEqual(comparison["absolute_coordinate_error"], {
            "count": 0, "min": None, "max": None, "mean": None, "p95": None,
        })

    def test_frozen_limits_inclusive(self):
        def boundary(fixture, reference, android):
            reference["frames"][0]["landmarks"]["left_hand"][0][0] = 0.126
            android["runs"][2]["frames"][0]["landmarks"]["left_hand"][0][0] = 0.12501

        code, report, error = self.invoke(boundary)
        self.assertEqual(code, 0, error)
        pairs = report["coordinate_deltas"]
        self.assertEqual(pairs["reference_vs_initial"]["absolute_coordinate_error"]["max"], 0.001)
        self.assertEqual(pairs["initial_vs_recovered"]["absolute_coordinate_error"]["max"], 0.00001)

    def test_large_distinct_integers_cannot_round_to_a_zero_delta(self):
        def change(fixture, reference, android):
            reference["frames"][0]["landmarks"]["left_hand"][0][0] = 2**54 + 1
            for run in android["runs"]:
                run["frames"][0]["landmarks"]["left_hand"][0][0] = 2**54

        code, report, _ = self.invoke(change)
        self.assertEqual(code, 1)
        comparison = report["coordinate_deltas"]["reference_vs_initial"]
        self.assertEqual(comparison["absolute_coordinate_error"]["max"], 1)
        self.assertEqual(comparison["above_coordinate_limit"], 1)

    def test_bound_bytes_still_require_valid_json_and_fresh_output(self):
        self.assert_invalid(bad_hash=True, message="SHA-256")
        self.assert_invalid(json_bytes=("android", lambda data: b"\xff" + data), message="UTF-8")
        self.assert_invalid(json_bytes=("android", lambda data: b'{"type":0,' + data[1:]), message="duplicate")
        self.assert_invalid(json_bytes=("android", lambda data: data + b" " * (512 * 1024)), message="byte limit")
        self.assert_invalid(json_bytes=("reference", lambda data: data + b" " * (8 * 1024 * 1024)), message="byte limit")
        self.assert_invalid(existing=True, message="exists")

    def test_manifest_pts_geometry_and_contract(self):
        mutations = (
            ("PTS", lambda f, r, a: f["frames"][1].update(timestamp_ms=34)),
            ("time_base", lambda f, r, a: f["frames"][2].update(source_time_base=[1, 0])),
            ("offset_bytes", lambda f, r, a: f["frames"][1].update(offset_bytes=5)),
            ("width", lambda f, r, a: f["frames"][0].update(width=641)),
            ("frame_index", lambda f, r, a: f["frames"][1].update(frame_index=0)),
            ("fields", lambda f, r, a: f["frames"][0].update(landmarks={})),
            ("source_model", lambda f, r, a: f["source_model"].update(bytes=1)),
            ("frames_file", lambda f, r, a: f["frames_file"].update(bytes=1)),
        )
        for message, mutate in mutations:
            with self.subTest(message=message):
                self.assert_invalid(mutate, message)

    def test_exact_reference_and_android_bindings(self):
        mutations = (
            ("clip", lambda f, r, a: r["input"]["clip"].update(sha256="0" * 64)),
            ("manifest_sha256", lambda f, r, a: r["rgba_fixture"].update(manifest_sha256="0" * 64)),
            ("frames_sha256", lambda f, r, a: a.update(frames_sha256="0" * 64)),
            ("rgba_sha256", lambda f, r, a: r["frames"][0].update(rgba_sha256="0" * 64)),
            ("source_pts", lambda f, r, a: r["frames"][1].update(source_pts=3000)),
            ("input_size", lambda f, r, a: r["frames"][0].update(input_size=[True, 1])),
            ("fixture_manifest_sha256", lambda f, r, a: a.update(fixture_manifest_sha256="0" * 64)),
            ("model_sha256", lambda f, r, a: a.update(model_sha256="0" * 64)),
            ("timestamp_ms", lambda f, r, a: a["runs"][0]["frames"][0].update(timestamp_ms=False)),
        )
        for message, mutate in mutations:
            with self.subTest(message=message):
                self.assert_invalid(mutate, message)

    def test_missing_extra_or_reordered_work(self):
        def reorder(fixture, reference, android):
            frames = reference["frames"]
            frames[0], frames[1] = frames[1], frames[0]

        mutations = (
            ("status", lambda f, r, a: a.update(status="SKIPPED")),
            ("status", lambda f, r, a: r["frames"][1].update(status="duplicate_pts")),
            ("runs", lambda f, r, a: a["runs"].pop()),
            ("name", lambda f, r, a: a["runs"][2].update(name="initial")),
            ("frames", lambda f, r, a: a["runs"][2].update(frames=[])),
            ("frames", lambda f, r, a: a["runs"][1]["frames"].append(a["runs"][0]["frames"][17])),
            ("frame_index", lambda f, r, a: a["runs"][2]["frames"][1].update(frame_index=0)),
            ("frame_index", reorder),
            ("frames", lambda f, r, a: r["frames"].append(r["frames"][-1])),
        )
        for message, mutate in mutations:
            with self.subTest(message=message):
                self.assert_invalid(mutate, message)

    def test_unconsumed_inputs_nonfinite_and_unordered_timing(self):
        mutations = (
            ("bitmap_consumed", lambda f, r, a: a["runs"][0]["frames"][0].update(bitmap_consumed=False)),
            ("detector_closed", lambda f, r, a: a["runs"][0].update(detector_closed=False)),
            ("detector_create_elapsed_ns", lambda f, r, a: a["runs"][0].update(detector_create_elapsed_ns=0)),
            ("elapsed_ns", lambda f, r, a: a["runs"][0]["frames"][0].update(elapsed_ns=21)),
            ("start_elapsed_ns", lambda f, r, a: a["runs"][0]["frames"][1].update(start_elapsed_ns=10010)),
            ("start_elapsed_ns", lambda f, r, a: a["runs"][2]["frames"][0].update(start_elapsed_ns=20)),
            ("landmarks", lambda f, r, a: r["frames"][0]["landmarks"]["left_hand"].pop()),
            ("landmarks", lambda f, r, a: a["runs"][0]["frames"][0]["landmarks"]["left_hand"][0].__setitem__(0, True)),
            ("finite", lambda f, r, a: a["runs"][0]["frames"][0]["landmarks"]["left_hand"][0].__setitem__(0, float("inf"))),
        )
        for message, mutate in mutations:
            with self.subTest(message=message):
                self.assert_invalid(mutate, message)


if __name__ == "__main__":
    unittest.main()
