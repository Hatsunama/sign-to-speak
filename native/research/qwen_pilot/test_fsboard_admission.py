"""Regression tests for the FSboard evaluation admission."""
import hashlib
import io
import json
import math
import struct
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.request import Request

from fsboard_admission import (
    AdmissionError,
    CLIP_BYTES,
    FRAME_COUNT,
    HTTPSRedirects,
    LICENSE_BYTES,
    MAX_EDGE,
    admit,
    annotation_window,
    copy_verified,
    decode_frames,
    parse_candidates,
    select_candidates,
    select_samples,
)


def row(index=1, **changes):
    value = {
        "signingType": "fs",
        "sensitiveContent": False,
        "phrase": "paper",
        "signerId": "signer-a",
        "fullVideoFilename": "source.mp4",
        "clipFilename": f"clip-{index}.mp4",
        "clipStartTimeS": 24.0,
        "clipEndTimeS": 28.0,
        "annotationStartTimeS": 24.7,
        "annotationEndTimeS": 27.5,
        "clipFileSize": 150,
        "clipFileMd5": "1" * 32,
    }
    return {**value, **changes}


def candidates(*rows):
    return parse_candidates(json.dumps(rows).encode())


class MetadataTests(unittest.TestCase):
    def test_invalid_rows_filtered_and_safe_source_path(self):
        good = row()
        invalid_cases = [
            {"sensitiveContent": 0}, {"sensitiveContent": True}, {"sensitiveContent": None},
            {"signingType": "random"}, {"phrase": "NotLower"}, {"phrase": "abcdÃ©f"},
            {"phrase": "abc\n"}, {"phrase": "ab"}, {"phrase": "abcdefghi"},
            {"clipFileMd5": "g" * 32}, {"clipFileMd5": "a" * 31},
            {"clipFilename": "../video.mp4"}, {"clipFilename": "sub\\video.mp4"},
            {"clipFilename": "video.mp4\n"}, {"fullVideoFilename": "../source.mp4"},
            {"clipFileSize": True}, {"clipFileSize": 0}, {"clipFileSize": CLIP_BYTES + 1},
            {"signerId": ""}, {"annotationStartTimeS": math.nan},
            {"clipStartTimeS": -1}, {"clipEndTimeS": math.inf},
            {"annotationStartTimeS": 23.0}, {"annotationEndTimeS": 29.0},
            {"annotationEndTimeS": 24.8}, {"annotationEndTimeS": 27.99},
            {"annotationStartTimeS": "24.7"}, {"annotationEndTimeS": True},
        ]
        for changes in invalid_cases:
            with self.subTest(changes=changes):
                self.assertEqual(candidates({**good, **changes}), [])
        self.assertEqual(candidates(good)[0]["source_path"],
                         "daun_v3/video_clips/daun_v3-test/clip-1.mp4")

    def test_requires_metadata_array_of_objects_and_consistent_paths(self):
        for content in (b'{}', b'[false]', b'', b'[]'):
            with self.subTest(content=content), self.assertRaises(AdmissionError):
                parse_candidates(content)
        with self.assertRaises(AdmissionError):
            candidates(row(), row(phrase="other"))

    def test_group_constraints_determinism_and_byte_cap(self):
        rows = [
            row(1, clipFileSize=100),
            row(2, clipFileSize=101, phrase="other", fullVideoFilename="two.mp4"),
            row(3, clipFileSize=100, signerId="signer-b", phrase="other"),
            row(4, clipFileSize=102, signerId="signer-b", phrase="paper", fullVideoFilename="two.mp4"),
            row(5, clipFileSize=103, signerId="signer-b", phrase="other", fullVideoFilename="two.mp4"),
            row(6, clipFileSize=104, signerId="signer-c", phrase="third", fullVideoFilename="three.mp4"),
        ]
        first = select_candidates(candidates(*rows), count=8, budget=303)
        self.assertEqual(first, select_candidates(candidates(*reversed(rows)), count=8, budget=303))
        self.assertEqual([value["clip_filename"] for value in first], ["clip-1.mp4", "clip-5.mp4"])
        self.assertEqual(len(select_candidates(candidates(*rows), count=1)), 1)
        with self.assertRaises(AdmissionError):
            select_candidates(candidates(*rows), count=16)


class TimelineTests(unittest.TestCase):
    def test_annotation_uses_clip_offset_and_nonzero_pts_origin(self):
        start, end = annotation_window(candidates(row())[0])
        self.assertAlmostEqual(start, 0.7)
        self.assertAlmostEqual(end, 3.5)
        pts = [11.0 + i / 30 for i in range(120)]
        samples = select_samples(pts, origin=11.0, window=(start, end), period=1 / 30)
        self.assertEqual(len(samples), FRAME_COUNT)
        self.assertEqual(len({sample["source_pts_seconds"] for sample in samples}), FRAME_COUNT)
        for sample in samples:
            self.assertGreaterEqual(sample["actual_seconds"], start)
            self.assertLessEqual(sample["actual_seconds"], end)
            self.assertAlmostEqual(sample["source_pts_seconds"] - 11, sample["actual_seconds"])
            self.assertLessEqual(abs(sample["actual_seconds"] - sample["requested_seconds"]), 1 / 30)

    def test_repeated_pts_and_timeline_gaps_fail(self):
        with self.assertRaises(AdmissionError):
            select_samples([i / 100 for i in range(31) for _ in range(2)],
                           origin=0, window=(0, .31), period=.01)
        with self.assertRaises(AdmissionError):
            select_samples([i / 60 for i in range(60)], origin=0, window=(0, 2), period=1 / 60)

    def test_unique_match_when_two_requests_have_same_nearest_frame(self):
        pts = [i / 30 for i in range(32)]
        pts[15] += .012
        pts[16] -= .012
        samples = select_samples(pts, origin=0, window=(0, 31 / 30), period=1 / 30)
        self.assertEqual([sample["source_pts_seconds"] for sample in samples], pts)



class DecodeTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image

        self.image = Image.new("RGB", (600, 400))
        self.colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
        for color, box in zip(self.colors, [
            (0, 0, 300, 200), (300, 0, 600, 200), (0, 200, 300, 400), (300, 200, 600, 400),
        ]):
            self.image.paste(color, box)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.output = Path(temp.name)
        self.frames = self.output / "frames"
        self.frames.mkdir()
        self.decode_count = 0

    def decode(
        self, first_rotation, second_rotation=None, *, matrix=None, duration=120,
        time_base=Fraction(1, 30), start_time=330, times=None, clip=None,
    ):
        class Frame:
            width, height = self.image.size
            side_data = {"DISPLAYMATRIX": matrix} if matrix is not None else {}

            def __init__(self, time, rotation):
                self.time = time
                self.rotation = rotation

            def reformat(self, *, width, height, format):
                return SimpleNamespace(to_image=lambda: image.resize((width, height)))

        image = self.image
        stream = SimpleNamespace(
            width=600, height=400, start_time=start_time, duration=duration, time_base=time_base,
        )
        if times is None:
            times = [11 + index / 30 for index in range(120)]

        def container(rotation):
            rotations = rotation if isinstance(rotation, list) else [rotation] * len(times)
            result = MagicMock()
            result.__enter__.return_value.streams.video = [stream]
            result.__enter__.return_value.decode.return_value = iter(
                Frame(time, angle) for time, angle in zip(times, rotations)
            )
            return result

        av = SimpleNamespace(open=MagicMock(side_effect=[
            container(first_rotation), container(first_rotation if second_rotation is None else second_rotation),
        ]))
        self.decode_count += 1
        clip_id = f"fsboard-{self.decode_count:02d}"
        with patch.dict(sys.modules, {"av": av}):
            return decode_frames(
                self.output / "source.mp4", self.output, clip_id, clip or candidates(row())[0],
            )

    def test_applies_counterclockwise_rotation_to_pixels_and_receipts(self):
        from PIL import Image

        for angle, corner_order, dimensions in [
            (0, (0, 1, 2, 3), (MAX_EDGE, 256)),
            (90, (1, 3, 0, 2), (256, MAX_EDGE)),
            (-90, (2, 0, 3, 1), (256, MAX_EDGE)),
            (180, (3, 2, 1, 0), (MAX_EDGE, 256)),
            (-180, (3, 2, 1, 0), (MAX_EDGE, 256)),
        ]:
            with self.subTest(angle=angle):
                receipt = self.decode(angle)
                self.assertEqual(receipt["clip_pts_origin_seconds"], 11)
                self.assertEqual(len(receipt["frames"]), FRAME_COUNT)
                self.assertEqual(len({frame["source_pts_seconds"] for frame in receipt["frames"]}), FRAME_COUNT)
                for frame in receipt["frames"]:
                    self.assertAlmostEqual(frame["source_pts_seconds"] - 11, frame["actual_seconds"])
                    path = self.output / frame["path"]
                    with Image.open(path) as image:
                        width, height = dimensions
                        self.assertEqual(image.size, dimensions)
                        self.assertEqual((frame["width"], frame["height"]), dimensions)
                        corners = [(0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)]
                        self.assertEqual([image.getpixel(corner) for corner in corners],
                                         [self.colors[index] for index in corner_order])
                    self.assertEqual(frame["source_display_rotation_degrees"], angle)
                    self.assertEqual(frame["applied_display_rotation_counterclockwise_degrees"], angle)
                self.assertEqual(receipt["source_display_rotation_degrees"], angle)
                self.assertEqual(receipt["applied_display_rotation_counterclockwise_degrees"], angle)

    def test_applies_real_mp4_track_display_matrix(self):
        import av
        from PIL import Image

        source = io.BytesIO()
        with av.open(source, "w", format="mp4") as container:
            stream = container.add_stream("mpeg4", rate=30)
            stream.width, stream.height, stream.pix_fmt = 64, 32, "yuv420p"
            image = self.image.resize((64, 32), Image.Resampling.NEAREST)
            for _ in range(120):
                for packet in stream.encode(av.VideoFrame.from_image(image)):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)

        # Supply a track display matrix to the recorded pixels without re-encoding.
        # The matrix starts 40 bytes into a version-0 MP4 track header's payload.
        data = bytearray(source.getvalue())
        track_header = data.index(b"tkhd") + 4
        self.assertEqual(data[track_header], 0)
        unit = 1 << 16
        offset = track_header + 40
        self.assertEqual(struct.unpack_from(">9i", data, offset),
                         (unit, 0, 0, 0, unit, 0, 0, 0, 1 << 30))
        struct.pack_into(">9i", data, offset, 0, -unit, 0, unit, 0, 0, 0, 64 * unit, 1 << 30)
        video = self.output / "source.mp4"
        video.write_bytes(data)

        receipt = decode_frames(video, self.output, "fsboard-01", candidates(row())[0])
        with Image.open(self.output / receipt["frames"][0]["path"]) as frame:
            self.assertEqual(frame.size, (32, 64))
            for point, expected in zip([(4, 4), (24, 4), (4, 54), (24, 54)], [1, 3, 0, 2]):
                for actual, channel in zip(frame.getpixel(point), self.colors[expected]):
                    self.assertLessEqual(abs(actual - channel), 12)
        self.assertEqual(receipt["source_display_rotation_degrees"], 90)

    def test_rejects_invalid_or_changing_angles_without_publishing_frames(self):
        for first, second in [
            (37, None), (89.5, None), (math.nan, None), (math.inf, None),
            ([0] * 119 + [90], None), (0, 90), (0, [0] * 119 + [90]),
        ]:
            with self.subTest(first=first, second=second):
                with self.assertRaisesRegex(AdmissionError, "display rotation"):
                    self.decode(first, second)
                self.assertEqual(list(self.frames.iterdir()), [])

    def test_rejects_reflection_shear_and_perspective_even_with_zero_angle(self):
        unit = 1 << 16
        for matrix in [
            (unit, 0, 0, 0, -unit, 0, 0, 0, 1 << 30),
            (unit, 0, 0, 1, unit, 0, 0, 0, 1 << 30),
            (unit, 0, 1, 0, unit, 0, 0, 0, 1 << 30),
        ]:
            with self.subTest(matrix=matrix):
                with self.assertRaisesRegex(AdmissionError, "display matrix"):
                    self.decode(0, matrix=struct.pack("=9i", *matrix))
                self.assertEqual(list(self.frames.iterdir()), [])

    def test_duration_allows_two_ticks_of_endpoint_quantization(self):
        time_base = Fraction(1, 90_000)
        times = [float(index * 3000 * time_base) for index in range(120)]
        times.append(float(360_068 * time_base))
        clip = candidates(row(
            clipEndTimeS=28.000757, annotationStartTimeS=24.2619, annotationEndTimeS=27.0289,
        ))[0]
        options = {"time_base": time_base, "start_time": 0, "times": times, "clip": clip}

        receipt = self.decode(0, duration=363_070, **options)
        self.assertEqual(receipt["decoded_timestamp_tick_seconds"], float(time_base))
        self.assertAlmostEqual(receipt["decoded_duration_seconds"], 4.034111111)
        self.assertEqual(len(receipt["frames"]), FRAME_COUNT)

        with self.assertRaisesRegex(AdmissionError, "decoded clip timing disagrees with metadata"):
            self.decode(0, duration=363_130, **options)

    def test_invalid_timing_reports_numeric_context(self):
        with self.assertRaisesRegex(
            AdmissionError, r"duration=3[.]7.*expected_duration=4[.]0.*first_relative_pts=0[.]0"
            r".*last_relative_pts_plus_period=4[.]0.*period=0[.]03.*annotation_end=3[.]5",
        ):
            self.decode(0, duration=111)


class OutputTests(unittest.TestCase):
    def test_kaggle_post_redirect_becomes_https_get(self):
        request = Request("https://www.kaggle.com/api/download", data=b"original POST body")
        redirects = HTTPSRedirects()
        redirected = redirects.redirect_request(
            request, None, 302, "Found", {}, "https://storage.googleapis.com/signed-download",
        )
        self.assertEqual(redirected.get_method(), "GET")
        self.assertIsNone(redirected.data)
        with self.assertRaises(AdmissionError):
            redirects.redirect_request(request, None, 302, "Found", {}, "http://example.org")

    def test_pins_source_license_before_fetching_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "evaluation"

            def different_license(_name, target, _cap, **_kwargs):
                body = b"x" * LICENSE_BYTES
                target.write_bytes(body)
                return len(body), hashlib.sha256(body).hexdigest()

            with patch("fsboard_admission.fetch_file", side_effect=different_license) as fetch:
                with self.assertRaisesRegex(AdmissionError, "source license integrity"):
                    admit(output)
            self.assertEqual(fetch.call_count, 1)
            self.assertFalse((output / "admission.json").exists())


    def test_download_checks_real_size_and_both_hashes(self):
        data = b"known bytes"
        expected_md5 = hashlib.md5(data, usedforsecurity=False).hexdigest()
        result = copy_verified(io.BytesIO(data), io.BytesIO(), len(data), len(data), expected_md5)
        self.assertEqual(result, (len(data), hashlib.sha256(data).hexdigest()))
        for body, md5 in ((data + b"!", expected_md5), (data[:-1], expected_md5), (data, "0" * 32)):
            with self.subTest(body=body, md5=md5), self.assertRaises(AdmissionError):
                copy_verified(io.BytesIO(body), io.BytesIO(), len(data), len(data), md5)

    def test_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "evaluation"
            output.mkdir()
            marker = output / "admission.json"
            marker.write_bytes(b"previous attempt")
            with patch("fsboard_admission.fetch_file") as fetch:
                with self.assertRaises(FileExistsError):
                    admit(output)
            fetch.assert_not_called()
            self.assertEqual(list(output.iterdir()), [marker])
            self.assertEqual(marker.read_bytes(), b"previous attempt")

    def test_failed_attempt_keeps_partial_and_diagnostic_without_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "evaluation"

            def partial_fetch(_name, target, _cap, **_kwargs):
                target.write_bytes(b"partial")
                raise AdmissionError("invalid download")

            with patch("fsboard_admission.fetch_file", side_effect=partial_fetch):
                with self.assertRaises(AdmissionError):
                    admit(output)
            self.assertFalse((output / "admission.json").exists())
            self.assertTrue((output / "source" / "LICENSE").is_file())
            failure = json.loads((output / "admission-error.json").read_text())
            self.assertEqual(failure["status"], "failed")
            self.assertEqual(failure["stage"], "source_license")


if __name__ == "__main__":
    unittest.main()
