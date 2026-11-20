"""Exercise the Holistic probe without media SDK dependencies."""

from contextlib import nullcontext, redirect_stderr
from hashlib import sha256
import io
import json
import os
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

import holistic_video_probe

from holistic_video_probe import (
    ProbeError, TimestampLedger, landmark_payload, make_success_receipt,
    nearest_center_indices, write_private_json,
)


def points(count, x=0.25):
    return [SimpleNamespace(x=x, y=0.5, z=-0.125) for _ in range(count)]


def result(left=(), right=(), pose=(), face=()):
    return SimpleNamespace(
        left_hand_landmarks=left, right_hand_landmarks=right,
        pose_landmarks=pose, face_landmarks=face,
        left_hand_world_landmarks=points(21, 99),
    )


class TimestampTests(unittest.TestCase):
    def test_preserves_rational_source_time_and_floors_relative_milliseconds(self):
        ledger = TimestampLedger()
        frame = ledger.record(-2, Fraction(1, 90_000))
        self.assertEqual(frame, {
            "frame_index": 0, "source_pts": -2,
            "source_time_base": [1, 90_000], "timestamp_ms": 0,
            "status": "processed",
        })
        self.assertEqual(ledger.record(91, Fraction(1, 90_000))["timestamp_ms"], 1)
        self.assertEqual(ledger.record(3, Fraction(1, 1000))["timestamp_ms"], 3)

    def test_records_drops_in_decode_order_without_inventing_times(self):
        ledger = TimestampLedger()
        base = Fraction(1, 90_000)
        observations = [(None, base), (100, base), (100, base), (102, base),
                        (101, base), (190, None), (197, base)]
        frames = [ledger.record(pts, tb) for pts, tb in observations]
        self.assertEqual([f["status"] for f in frames], [
            "missing_pts", "processed", "duplicate_pts", "duplicate_timestamp_ms",
            "nonmonotonic_pts", "missing_time_base", "processed",
        ])
        self.assertEqual([f["frame_index"] for f in frames], list(range(7)))
        self.assertIsNone(frames[0]["timestamp_ms"])
        self.assertEqual(frames[0]["source_time_base"], [1, 90_000])
        self.assertEqual(frames[5]["source_pts"], 190)
        self.assertIsNone(frames[5]["timestamp_ms"])
        self.assertEqual(frames[-1]["timestamp_ms"], 1)

    def test_enforces_frame_and_duration_limits(self):
        ledger = TimestampLedger()
        for _ in range(600):
            ledger.record(None, None)
        with self.assertRaisesRegex(ProbeError, "frame limit"):
            ledger.record(None, None)
        ledger = TimestampLedger()
        ledger.record(0, Fraction(1, 90_000))
        with self.assertRaisesRegex(ProbeError, "duration limit"):
            ledger.record(20 * 90_000 + 1, Fraction(1, 90_000))

    def test_nearest_center_selection(self):
        self.assertEqual(nearest_center_indices(7, 3), [1, 3, 5])
        self.assertEqual(nearest_center_indices(4, 2), [1, 3])
        self.assertEqual(nearest_center_indices(3, 3), [0, 1, 2])


class PayloadTests(unittest.TestCase):
    def test_anatomical_slots_shape_and_explicit_missingness(self):
        payload = landmark_payload(result(right=points(21), pose=points(33), face=points(478)))
        self.assertIsNone(payload["left_hand"])
        for name, length in (("right_hand", 21), ("pose", 33), ("face", 478)):
            self.assertEqual(payload[name], [[0.25, 0.5, -0.125]] * length)
        self.assertEqual(set(payload), {"left_hand", "right_hand", "pose", "face"})

    def test_bad_shape_or_nonfinite_coordinate_fails(self):
        with self.assertRaisesRegex(ProbeError, "left_hand.*21"):
            landmark_payload(result(left=points(20)))
        with self.assertRaisesRegex(ProbeError, "face.*nonfinite"):
            landmark_payload(result(face=points(478, float("nan"))))
        with self.assertRaisesRegex(ProbeError, "pose.*nonfinite"):
            landmark_payload(result(pose=points(33, float("inf"))))

    def test_motion_counts_only_adjacent_observed_frames_in_anatomical_slot(self):
        ledger = TimestampLedger()
        base = Fraction(1, 100)
        source = [
            (0, result(left=points(21))),
            (1, result(left=points(21, 0.5), right=points(21))),
            (1, None),
            (2, result(left=points(21), right=points(21))),
            (3, result(right=points(21))),
        ]
        frames = []
        for pts, landmarks in source:
            frame = ledger.record(pts, base)
            if landmarks is not None:
                frame["landmarks"] = landmark_payload(landmarks)
            frames.append(frame)
        receipt = make_success_receipt(frames, {}, {})
        self.assertEqual(receipt["type"], "holistic_video_extraction")
        self.assertEqual(receipt["status"], "SUCCESS")
        self.assertEqual(receipt["coverage"]["left_hand"], {
            "present_frames": 3, "adjacent_joint_pairs": 21, "changed_xyz_pairs": 21,
        })
        self.assertEqual(receipt["coverage"]["right_hand"], {
            "present_frames": 3, "adjacent_joint_pairs": 21, "changed_xyz_pairs": 0,
        })
        self.assertEqual(receipt["frame_counts"], {"processed": 4, "duplicate_pts": 1})

    def test_cannot_serialize_zero_processed_frames_as_success(self):
        ledger = TimestampLedger()
        with self.assertRaisesRegex(ProbeError, "no frames processed"):
            make_success_receipt([ledger.record(None, None)], {}, {})

    def test_write_requires_unused_name_and_rejects_nonfinite_json(self):
        with TemporaryDirectory() as temporary:
            folder = Path(temporary)
            write_private_json(folder, "receipt.json", {"answer": [1, None]})
            with self.assertRaises(FileExistsError):
                write_private_json(folder, "receipt.json", {"answer": "replacement"})
            path = folder / "receipt.json"
            self.assertEqual(json.loads(path.read_text()), {"answer": [1, None]})
            with self.assertRaises(ValueError):
                write_private_json(folder, "invalid.json", {"value": float("nan")})
            self.assertEqual(list(folder.iterdir()), [path])


class VerifiedClipTests(unittest.TestCase):
    def test_decoder_reads_verified_bytes_after_source_mutates(self):
        original = b"original clip"
        replacement = b"replaced clip"
        model = b"model bytes"
        validate_copy = holistic_video_probe.validate_copy
        decoded_bytes = []
        decoder_inputs = []
        frame = SimpleNamespace(pts=0, time_base=Fraction(1, 1000), width=1, height=1)
        stream = SimpleNamespace(
            width=1, height=1, duration=None, time_base=None, codec_context=SimpleNamespace(),
        )

        def open_video(source, **_):
            decoder_inputs.append(source)
            decoded_bytes.append(source.read())
            return nullcontext(SimpleNamespace(
                streams=SimpleNamespace(video=[stream]), duration=None, decode=lambda _: [frame],
            ))

        def validate_and_mutate(source, target, declared_bytes, cap, digest):
            validate_copy(source, target, declared_bytes, cap, digest)
            if source.name == str(clip_path):
                with clip_path.open("r+b") as mutable:
                    mutable.write(replacement)

        detector = SimpleNamespace(detect_for_video=lambda *_: result())
        base_options = Mock(Delegate=SimpleNamespace(CPU="CPU"))
        vision = SimpleNamespace(
            HolisticLandmarkerOptions=Mock(),
            RunningMode=SimpleNamespace(VIDEO="VIDEO"),
            HolisticLandmarker=SimpleNamespace(create_from_options=lambda _: nullcontext(detector)),
        )
        modules = {
            "av": SimpleNamespace(__version__=holistic_video_probe.AV_VERSION, open=open_video),
            "mediapipe": SimpleNamespace(
                __version__=holistic_video_probe.MEDIAPIPE_VERSION,
                tasks=SimpleNamespace(BaseOptions=base_options, vision=vision),
                Image=Mock(), ImageFormat=SimpleNamespace(SRGBA="SRGBA"),
            ),
            "numpy": SimpleNamespace(__version__="fake"),
            "PIL": SimpleNamespace(__version__="fake"),
        }
        with TemporaryDirectory() as temporary:
            clip_path = Path(temporary) / "clip.mp4"
            clip_path.write_bytes(original)
            model_path = Path(temporary) / "model.task"
            model_path.write_bytes(model)
            args = SimpleNamespace(
                clip=clip_path, clip_bytes=len(original), clip_sha256=sha256(original).hexdigest(),
                model=model_path,
            )
            rgba = SimpleNamespace(shape=(1, 1, 4))
            with (
                patch.dict("sys.modules", modules),
                patch.object(holistic_video_probe, "MODEL_BYTES", len(model)),
                patch.object(holistic_video_probe, "MODEL_SHA256", sha256(model).hexdigest()),
                patch.object(holistic_video_probe, "open_local_file", lambda path, _: path.open("rb")),
                patch.object(holistic_video_probe, "validate_copy", validate_and_mutate),
                patch.object(holistic_video_probe, "upright_pixels", return_value=(rgba, 0)),
            ):
                receipt = holistic_video_probe.run(args, [])

            self.assertEqual(clip_path.read_bytes(), replacement)
            self.assertEqual(receipt["status"], "SUCCESS")
            self.assertEqual(receipt["input"]["clip"]["sha256"], args.clip_sha256)
            self.assertEqual(decoded_bytes, [original])
            self.assertTrue(decoder_inputs[0].closed)



class FixtureTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        model = b"model bytes"
        clip = b"clip bytes"
        self.args = SimpleNamespace(
            clip=root / "clip.mp4", clip_bytes=len(clip), clip_sha256=sha256(clip).hexdigest(),
            model=root / "model.task", output=root / "output", export_rgba_fixture=True,
        )
        self.args.clip.write_bytes(clip)
        self.args.model.write_bytes(model)
        self.args.output.mkdir()
        self.rgba = SimpleNamespace(shape=(1, 2, 4))
        self.first = bytes([1, 2, 3, 255, 4, 5, 6, 255])
        self.second = bytes([7, 8, 9, 255, 10, 11, 12, 255])
        self.view = SimpleNamespace(
            shape=self.rgba.shape, dtype=SimpleNamespace(name="uint8"),
            tobytes=Mock(side_effect=[self.first, self.second]),
        )
        self.image = SimpleNamespace(numpy_view=Mock(return_value=self.view))
        self.detector = Mock(detect_for_video=Mock(return_value=result(left=points(21))))
        self.decoded = [
            SimpleNamespace(pts=pts, time_base=Fraction(1, 90_000), width=2, height=1)
            for pts in (None, -2, -2, 91)
        ]
        stream = SimpleNamespace(
            width=2, height=1, duration=None, time_base=None, codec_context=SimpleNamespace(),
        )
        container = SimpleNamespace(
            streams=SimpleNamespace(video=[stream]), duration=None, decode=lambda _: self.decoded,
        )
        vision = SimpleNamespace(
            HolisticLandmarkerOptions=Mock(),
            RunningMode=SimpleNamespace(VIDEO="VIDEO"),
            HolisticLandmarker=SimpleNamespace(create_from_options=lambda _: nullcontext(self.detector)),
        )
        self.mp_image = Mock(return_value=self.image)
        self.enterContext(patch.dict("sys.modules", {
            "av": SimpleNamespace(
                __version__=holistic_video_probe.AV_VERSION, open=lambda *_a, **_k: nullcontext(container),
            ),
            "mediapipe": SimpleNamespace(
                __version__=holistic_video_probe.MEDIAPIPE_VERSION,
                tasks=SimpleNamespace(BaseOptions=Mock(Delegate=SimpleNamespace(CPU="CPU")), vision=vision),
                Image=self.mp_image, ImageFormat=SimpleNamespace(SRGBA="SRGBA"),
            ),
            "numpy": SimpleNamespace(__version__="fake"),
            "PIL": SimpleNamespace(__version__="fake"),
        }))
        for name, value in {
            "MODEL_BYTES": len(model), "MODEL_SHA256": sha256(model).hexdigest(),
            "open_local_file": lambda path, _: path.open("rb"),
        }.items():
            self.enterContext(patch.object(holistic_video_probe, name, value))
        self.enterContext(patch.object(holistic_video_probe, "upright_pixels", return_value=(self.rgba, 0)))

    def test_exports_sdk_image_bytes_as_reference_free_fixture(self):
        timeline = Mock()
        timeline.attach_mock(self.image.numpy_view, "sdk_pixels")
        timeline.attach_mock(self.view.tobytes, "snapshot")
        timeline.attach_mock(self.detector.detect_for_video, "detect")
        receipt = holistic_video_probe.run(self.args, [])
        expected_blob = self.first + self.second
        output = self.args.output
        self.assertTrue((output / "holistic-parity.rgba").is_file(), "SDK pixel fixture was not exported")
        self.assertEqual((output / "holistic-parity.rgba").read_bytes(), expected_blob)
        manifest = (output / "parity-manifest.json").read_bytes()
        expected_frames = [
            {
                "frame_index": index, "width": 2, "height": 1, "offset_bytes": offset, "length_bytes": 8,
                "rgba_sha256": sha256(pixels).hexdigest(), "source_pts": pts,
                "source_time_base": [1, 90_000], "timestamp_ms": timestamp,
            }
            for index, offset, pixels, pts, timestamp in [(1, 0, self.first, -2, 0), (3, 8, self.second, 91, 1)]
        ]
        frame_digest = sha256(expected_blob).hexdigest()
        self.assertEqual(json.loads(manifest), {
            "type": "holistic_rgba_replay_fixture", "schema_version": 1,
            "pixel_format": "RGBA_8888", "byte_order": "row_major_RGBA",
            "frames_file": {"name": "holistic-parity.rgba", "bytes": len(expected_blob), "sha256": frame_digest},
            "source_clip": receipt["input"]["clip"], "source_model": receipt["input"]["model"],
            "frame_count": 2, "frames": expected_frames,
        })
        self.assertEqual(receipt["rgba_fixture"], {
            "manifest_sha256": sha256(manifest).hexdigest(), "frames_sha256": frame_digest, "frame_count": 2,
        })
        self.assertEqual(json.loads((output / "receipt.json").read_bytes()), receipt)
        for manifest_frame in expected_frames:
            reference = receipt["frames"][manifest_frame["frame_index"]]
            self.assertEqual(reference["rgba_sha256"], manifest_frame["rgba_sha256"])
            self.assertEqual(reference["landmarks"]["left_hand"], [[0.25, 0.5, -0.125]] * 21)
        self.assertNotIn("rgba_sha256", receipt["frames"][2])
        self.mp_image.assert_called_with(image_format="SRGBA", data=self.rgba)
        self.assertEqual(timeline.mock_calls, [
            call.sdk_pixels(), call.snapshot(order="C"), call.detect(self.image, 0),
            call.sdk_pixels(), call.snapshot(order="C"), call.detect(self.image, 1),
        ])

    def test_cli_exports_only_the_complete_fixture_and_reference_receipt(self):
        output = self.args.output / "cli"
        arguments = [
            "holistic_video_probe.py", "--clip", str(self.args.clip),
            "--clip-sha256", self.args.clip_sha256, "--clip-bytes", str(self.args.clip_bytes),
            "--model", str(self.args.model), "--output", str(output), "--export-rgba-fixture",
        ]
        with patch("sys.argv", arguments), patch.object(holistic_video_probe, "enforce_offline"):
            holistic_video_probe.main()
        self.assertEqual({path.name for path in output.iterdir()}, {
            "receipt.json", "holistic-parity.rgba", "parity-manifest.json",
        })
        receipt = json.loads((output / "receipt.json").read_bytes())
        self.assertEqual(receipt["status"], "SUCCESS")
        self.assertEqual(receipt["rgba_fixture"]["frame_count"], 2)

    def test_default_does_not_read_sdk_pixels_or_emit_fixture(self):
        self.args.export_rgba_fixture = False
        receipt = holistic_video_probe.run(self.args, [])
        self.image.numpy_view.assert_not_called()
        self.assertNotIn("rgba_fixture", receipt)
        self.assertNotIn("rgba_sha256", receipt["frames"][1])
        self.assertEqual(list(self.args.output.iterdir()), [])

    def test_rejects_nonopaque_pixels_before_detection(self):
        self.view.tobytes.side_effect = None
        self.view.tobytes.return_value = self.first[:-1] + bytes([254])
        with self.assertRaisesRegex(ProbeError, "opaque"):
            holistic_video_probe.run(self.args, [])
        self.detector.detect_for_video.assert_not_called()
        self.assertEqual(list(self.args.output.iterdir()), [])

    def test_rejects_invalid_sdk_shape_before_copying_pixels(self):
        for shape in [(1, 641, 4), (641, 1, 4), (0, 2, 4), (1, 2, 3)]:
            with self.subTest(shape=shape):
                self.view.shape = shape
                with self.assertRaisesRegex(ProbeError, "dimensions"):
                    holistic_video_probe.run(self.args, [])
        self.view.tobytes.assert_not_called()
        self.assertEqual(list(self.args.output.iterdir()), [])

    def test_rejects_fixture_limits_without_publishing_prefix(self):
        for limit, cap, message in [
            ("MAX_FIXTURE_FRAMES", 1, "frame limit"),
            ("MAX_FIXTURE_BYTES", 8, "byte limit"),
        ]:
            with self.subTest(limit=limit), patch.object(holistic_video_probe, limit, cap):
                self.view.tobytes.side_effect = [self.first, self.second]
                with self.assertRaisesRegex(ProbeError, message):
                    holistic_video_probe.run(self.args, [])
                self.assertEqual(list(self.args.output.iterdir()), [])

    def test_detector_error_does_not_publish_fixture(self):
        self.detector.detect_for_video.side_effect = [result(), RuntimeError("detector failed")]
        with self.assertRaisesRegex(RuntimeError, "detector failed") as outcome:
            holistic_video_probe.run(self.args, [])
        self.assertIn("decoded frame 3", outcome.exception.__notes__[0])
        self.assertEqual(list(self.args.output.iterdir()), [])

    def test_cancel_preserves_primary_if_blob_cleanup_fails(self):
        unlink = Path.unlink

        def deny_blob_cleanup(path, *args, **kwargs):
            if path.name == "holistic-parity.tmp":
                raise PermissionError("staged blob cleanup denied")
            return unlink(path, *args, **kwargs)

        self.detector.detect_for_video.side_effect = [result(), KeyboardInterrupt("cancel extraction")]
        with patch.object(Path, "unlink", deny_blob_cleanup):
            with self.assertRaisesRegex(KeyboardInterrupt, "cancel extraction") as outcome:
                holistic_video_probe.run(self.args, [])
        self.assertIn("staged blob cleanup denied", outcome.exception.__notes__[0])
        self.assertEqual({path.name for path in self.args.output.iterdir()}, {"holistic-parity.tmp"})

    def test_receipt_publish_failure_keeps_manifest_unpublished(self):
        link = os.link

        def link_output(source, target):
            if target.name == "receipt.json":
                raise OSError("receipt publish failed")
            link(source, target)

        with patch.object(holistic_video_probe.os, "link", side_effect=link_output):
            with self.assertRaisesRegex(OSError, "receipt publish failed"):
                holistic_video_probe.run(self.args, [])
        self.assertFalse((self.args.output / "parity-manifest.json").exists())

    def test_manifest_link_commit_then_error_keeps_committed_fixture(self):
        link = os.link

        def link_output(source, target):
            link(source, target)
            if target.name == "parity-manifest.json":
                raise OSError("manifest link result interrupted")

        stderr = io.StringIO()
        with patch.object(holistic_video_probe.os, "link", side_effect=link_output), redirect_stderr(stderr):
            receipt = holistic_video_probe.run(self.args, [])
        manifest = (self.args.output / "parity-manifest.json").read_bytes()
        self.assertEqual(sha256(manifest).hexdigest(), receipt["rgba_fixture"]["manifest_sha256"])
        self.assertIn("manifest link result interrupted", stderr.getvalue())
        self.assertFalse((self.args.output / "parity-manifest.tmp").exists())


class ReceiptCommitTests(unittest.TestCase):
    def test_cli_keeps_success_when_link_commits_before_error(self):
        link = os.link

        def commit_then_error(source, target):
            link(source, target)
            if target.name == "receipt.json":
                raise OSError("link result interrupted")

        with patch("holistic_video_probe.os.link", side_effect=commit_then_error):
            self.assert_committed_receipt("link result interrupted", staging_remains=False)

    def test_cli_keeps_success_when_staging_cleanup_fails(self):
        unlink = Path.unlink

        def deny_receipt_cleanup(path, *args, **kwargs):
            if path.name == "receipt.tmp":
                raise PermissionError("staged receipt cleanup denied")
            return unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", deny_receipt_cleanup):
            self.assert_committed_receipt("staged receipt cleanup denied", staging_remains=True)

    def assert_committed_receipt(self, warning_text, *, staging_remains):
        with TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            document = {"status": "SUCCESS", "frames": [1]}
            stderr = io.StringIO()
            arguments = [
                "holistic_video_probe.py", "--clip", "unused.mp4",
                "--clip-sha256", "0" * 64, "--clip-bytes", "1",
                "--model", "unused.task", "--output", str(output),
            ]
            with (
                patch("sys.argv", arguments),
                patch.object(holistic_video_probe, "enforce_offline"),
                patch.object(holistic_video_probe, "run", return_value=document),
                redirect_stderr(stderr),
            ):
                holistic_video_probe.main()
            self.assertEqual(json.loads((output / "receipt.json").read_bytes()), document)
            expected = {"receipt.json", "receipt.tmp"} if staging_remains else {"receipt.json"}
            self.assertEqual({path.name for path in output.iterdir()}, expected)
            self.assertIn(warning_text, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()

