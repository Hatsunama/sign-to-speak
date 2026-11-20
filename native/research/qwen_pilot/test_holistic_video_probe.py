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
from unittest.mock import Mock, patch

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

