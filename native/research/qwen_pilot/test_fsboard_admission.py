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
            {"signingType": "random"}, {"phrase": "NotLower"}, {"phrase": "abcdéf"},
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

    def test_adaptive_samples_short_annotation_windows(self):
        scenarios = [
            (30, 31, (.15, .85), 21),
            (23.94, 61, (.2, 1.4), 29),
        ]
        for rate, total, window, expected_count in scenarios:
            with self.subTest(rate=rate, window=window):
                pts = [index / rate for index in range(total)]
                options = {"origin": 0, "window": window, "period": 1 / rate}
                with self.assertRaisesRegex(AdmissionError, "fewer than 32 distinct"):
                    select_samples(pts, **options)

                samples = select_samples(pts, min_frames=8, max_frames=32, **options)
                eligible = [timestamp for timestamp in pts if window[0] <= timestamp <= window[1]]
                self.assertEqual(len(samples), expected_count)
                self.assertEqual([sample["source_pts_seconds"] for sample in samples], eligible)
                for slot, sample in enumerate(samples):
                    expected_time = window[0] + (window[1] - window[0]) * slot / (expected_count - 1)
                    self.assertAlmostEqual(sample["requested_seconds"], expected_time)
                    self.assertLessEqual(abs(sample["actual_seconds"] - expected_time), 1 / rate)
                    self.assertFalse(sample["duplicate"])

    def test_adaptive_samples_require_eight_unique_frames(self):
        options = {"origin": 0, "window": (0, .07), "period": .01, "min_frames": 8, "max_frames": 32}
        pts = [index / 100 for index in range(8) for _ in range(2)]
        samples = select_samples(pts, **options)
        self.assertEqual([sample["frame_index"] for sample in samples], list(range(0, 16, 2)))
        with self.assertRaisesRegex(AdmissionError, "fewer than 8 distinct"):
            select_samples(pts[:-2], **options)

    def test_adaptive_samples_cap_count_and_preserve_fixed_slots(self):
        pts = [index / 60 for index in range(121)]
        options = {"origin": 0, "window": (0, 2), "period": 1 / 60}
        samples = select_samples(pts, min_frames=8, max_frames=32, **options)
        self.assertEqual(samples, select_samples(pts, **options))
        self.assertEqual(len(samples), FRAME_COUNT)
        self.assertEqual(samples[0]["frame_index"], 0)
        self.assertEqual(samples[-1]["frame_index"], 120)

    def test_adaptive_samples_reject_unmatchable_timing_gaps(self):
        pts = [index / 30 for index in range(20) if index not in range(7, 13)]
        with self.assertRaisesRegex(AdmissionError, "cannot match 14 timed slots"):
            select_samples(pts, origin=0, window=(0, 19 / 30), period=1 / 30,
                           min_frames=8, max_frames=32)

    def test_frame_bounds_are_integers_in_order_and_within_model_cap(self):
        for minimum, maximum in [(0, 8), (9, 8), (8, 33), (True, 8), (8, 8.0)]:
            with self.subTest(minimum=minimum, maximum=maximum):
                with self.assertRaisesRegex(AdmissionError, "invalid frame count bounds"):
                    select_samples([0], origin=0, window=(0, 1), period=1,
                                   min_frames=minimum, max_frames=maximum)

    def test_single_frame_slot_uses_annotation_midpoint(self):
        samples = select_samples([0, .1, .2, .3, .4], origin=0, window=(.1, .4), period=.1,
                                 min_frames=1, max_frames=1)
        self.assertEqual(len(samples), 1)
        self.assertAlmostEqual(samples[0]["requested_seconds"], .25)
        self.assertLessEqual(abs(samples[0]["actual_seconds"] - .25), .1)

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
        terminal_duration=None, terminal_time_base=None, **frame_bounds,
    ):
        class Frame:
            width, height = self.image.size
            side_data = {"DISPLAYMATRIX": matrix} if matrix is not None else {}

            def __init__(self, time, rotation, duration=None, time_base=None):
                self.time = time
                self.rotation = rotation
                self.duration = duration
                self.time_base = time_base

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
            frames = [Frame(time, angle) for time, angle in zip(times[:-1], rotations[:-1])]
            frames.append(Frame(times[-1], rotations[-1], terminal_duration, terminal_time_base))
            result.__enter__.return_value.decode.return_value = iter(frames)
            return result

        av = SimpleNamespace(open=MagicMock(side_effect=[
            container(first_rotation), container(first_rotation if second_rotation is None else second_rotation),
        ]))
        self.decode_count += 1
        clip_id = f"fsboard-{self.decode_count:02d}"
        with patch.dict(sys.modules, {"av": av}):
            return decode_frames(
                self.output / "source.mp4", self.output, clip_id, clip or candidates(row())[0],
                **frame_bounds,
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

    def test_adaptive_decode_writes_only_selected_rotated_real_frames(self):
        from PIL import Image

        clip = {
            "clip_start_full_video_seconds": 0,
            "clip_end_full_video_seconds": 31 / 30,
            "annotation_start_full_video_seconds": .15,
            "annotation_end_full_video_seconds": .85,
        }
        times = [index / 30 for index in range(31)]
        options = {"duration": 31, "start_time": 0, "times": times, "clip": clip}
        with self.assertRaisesRegex(AdmissionError, "missing timed video frames"):
            self.decode(90, **options)
        self.assertEqual(list(self.frames.iterdir()), [])

        receipt = self.decode(90, min_frames=8, max_frames=32, **options)
        frames = receipt["frames"]
        self.assertEqual(len(frames), 21)
        self.assertEqual([frame["frame_index"] for frame in frames], list(range(5, 26)))
        self.assertEqual([frame["source_pts_seconds"] for frame in frames], times[5:26])
        self.assertEqual(receipt["applied_display_rotation_counterclockwise_degrees"], 90)
        directory = self.frames / "fsboard-02"
        self.assertEqual(sorted(path.name for path in directory.iterdir()),
                         [f"{slot:02d}.png" for slot in range(21)])
        for frame in frames:
            path = self.output / frame["path"]
            self.assertEqual(frame["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual((frame["width"], frame["height"]), (256, MAX_EDGE))
            with Image.open(path) as image:
                self.assertEqual(image.size, (256, MAX_EDGE))
                self.assertEqual(image.getpixel((0, 0)), self.colors[1])

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

    def terminal_endpoint_fixture(self):
        time_base = Fraction(1, 90_000)
        # Preserve the measured median gap, frame count, and terminal PTSs.
        ticks = [index * 2999 - min(index, 37) for index in range(119)]
        ticks.extend((356_837, 359_845))
        return {
            "duration": 362_853, "time_base": time_base, "start_time": 0,
            "times": [float(tick * time_base) for tick in ticks],
            "terminal_duration": 3008, "terminal_time_base": time_base,
            "clip": candidates(row(
                clipStartTimeS=808.422878, clipEndTimeS=812.421157,
                annotationStartTimeS=809.0, annotationEndTimeS=811.533,
            ))[0],
            "min_frames": 8, "max_frames": 32,
        }

    def test_terminal_pts_endpoint_requires_explicit_opt_in(self):
        options = self.terminal_endpoint_fixture()
        with self.assertRaisesRegex(
            AdmissionError, r"duration=4[.]031700000, expected_duration=3[.]998279000"
        ):
            self.decode(0, **options)
        self.assertEqual(list(self.frames.iterdir()), [])

        receipt = self.decode(0, allow_terminal_pts_endpoint=True, **options)
        self.assertAlmostEqual(receipt["frame_period_seconds"], 2999 / 90_000)
        self.assertAlmostEqual(receipt["decoded_duration_seconds"], 362_853 / 90_000)
        self.assertAlmostEqual(receipt["decoded_timestamp_tick_seconds"], 1 / 90_000)
        corroboration = receipt["terminal_endpoint_corroboration"]
        self.assertAlmostEqual(corroboration["publisher_clip_duration_seconds"], 3.998279)
        self.assertAlmostEqual(corroboration["last_relative_pts_seconds"], 359_845 / 90_000)
        self.assertEqual(corroboration["last_frame_duration_ticks"], 3008)
        self.assertAlmostEqual(corroboration["last_frame_time_base_seconds"], 1 / 90_000)
        self.assertAlmostEqual(corroboration["last_frame_duration_seconds"], 3008 / 90_000)
        self.assertAlmostEqual(corroboration["preceding_distinct_pts_gap_seconds"], 3008 / 90_000)
        self.assertAlmostEqual(corroboration["last_frame_end_seconds"], 362_853 / 90_000)
        self.assertAlmostEqual(corroboration["tolerance_seconds"], 2 / 90_000)
        sampled = receipt["frames"]
        self.assertEqual(len(sampled), 32)
        pts = [frame["actual_seconds"] for frame in sampled]
        self.assertEqual(pts, sorted(set(pts)))
        self.assertTrue(all(
            receipt["annotation_start_clip_seconds"] <= value <= receipt["annotation_end_clip_seconds"]
            for value in pts
        ))
        for frame in sampled:
            self.assertFalse(frame["duplicate"])
            self.assertEqual(frame["source_pts_seconds"], options["times"][frame["frame_index"]])
            self.assertTrue((self.output / frame["path"]).is_file())
        self.assertEqual(len(list(self.frames.rglob("*.png"))), 32)

    def test_terminal_pts_endpoint_admits_measured_variable_tail_durations(self):
        time_base = Fraction(1, 90_000)
        # Exact presentation ticks from holdout-040 in corpus timing job 6aff33f44fdc5ab3c55a1034.
        holdout_ticks = [
            0, 3_020, 6_040, 9_049, 12_058, 15_079, 18_100, 21_109,
            24_118, 27_138, 30_158, 33_178, 36_188, 39_198, 42_219, 45_240,
            48_249, 51_258, 54_279, 57_300, 60_310, 63_320, 66_341, 69_362,
            72_374, 75_386, 78_398, 81_410, 84_434, 87_458, 90_466, 93_474,
            96_496, 99_518, 102_526, 105_534, 108_555, 111_576, 114_586, 117_596,
            120_617, 123_638, 126_647, 129_656, 132_678, 135_700, 138_709, 141_718,
            144_739, 147_760, 150_768, 153_776, 156_797, 159_818, 162_827, 165_836,
            168_856, 171_876, 174_885, 177_894, 180_914, 183_934, 186_942, 189_950,
            192_970, 195_990, 198_999, 202_008, 205_027, 208_046, 211_055, 214_064,
            217_083, 220_102, 223_112, 226_122, 229_132, 232_153, 235_174, 238_181,
            241_188, 244_207, 247_226, 250_235, 253_244, 256_263, 259_282, 262_291,
            265_300, 268_319, 271_338, 274_347, 277_356, 280_375, 283_394, 286_403,
            289_412, 292_431, 295_450, 298_459, 301_468, 304_488, 307_508, 310_518,
            313_528, 316_553, 319_578, 322_590, 325_602, 328_626, 331_650, 334_663,
            337_676, 340_700, 343_724, 346_738, 349_752, 352_776, 355_800, 358_807,
            361_814,
        ]
        cases = (
            ("holdout-040", holdout_ticks, 364_838, 3024, 3007, {
                "clip_start_full_video_seconds": 14.057611,
                "clip_end_full_video_seconds": 18.077768,
                "annotation_start_full_video_seconds": 14.656,
                "annotation_end_full_video_seconds": 17.444,
            }),
            # Every observed PTS interval is 2992 ticks in training-116.
            ("training-116", range(0, 91 * 2992, 2992), 272_282, 3002, 2992, {
                "clip_start_full_video_seconds": 791.172956,
                "clip_end_full_video_seconds": 794.164957,
                "annotation_start_full_video_seconds": 791.411,
                "annotation_end_full_video_seconds": 793.54,
            }),
        )
        for clip_id, ticks, stream_duration, frame_duration, preceding_gap, clip in cases:
            with self.subTest(clip=clip_id):
                receipt = self.decode(
                    0, duration=stream_duration, time_base=time_base, start_time=0,
                    times=[float(tick * time_base) for tick in ticks], clip=clip,
                    terminal_duration=frame_duration, terminal_time_base=time_base,
                    min_frames=8, max_frames=32, allow_terminal_pts_endpoint=True,
                )
                corroboration = receipt["terminal_endpoint_corroboration"]
                self.assertEqual(corroboration["last_frame_duration_ticks"], frame_duration)
                self.assertAlmostEqual(
                    corroboration["preceding_distinct_pts_gap_seconds"], preceding_gap / 90_000,
                )
                self.assertAlmostEqual(corroboration["last_frame_end_seconds"], stream_duration / 90_000)
                self.assertEqual(len(receipt["frames"]), 32)

    def test_terminal_pts_endpoint_accepts_a_shorter_duration_after_a_wide_pts_gap(self):
        options = self.terminal_endpoint_fixture()
        options["times"][-2] = 353_847 / 90_000
        receipt = self.decode(0, allow_terminal_pts_endpoint=True, **options)
        corroboration = receipt["terminal_endpoint_corroboration"]
        self.assertAlmostEqual(corroboration["preceding_distinct_pts_gap_seconds"], 5998 / 90_000)
        self.assertEqual(corroboration["last_frame_duration_ticks"], 3008)

    def test_terminal_pts_endpoint_rejects_uncorroborated_overruns_before_publication(self):
        options = self.terminal_endpoint_fixture()
        shifted_end = dict(options["clip"])
        shifted_end["clip_end_full_video_seconds"] -= 0.000050

        for description, change in (
            ("missing duration", {"terminal_duration": None}),
            ("missing time base", {"terminal_time_base": None}),
            ("wrong time base", {"terminal_time_base": Fraction(1, 45_000)}),
            ("non-finite duration", {"terminal_duration": math.nan}),
            ("non-finite time base", {"terminal_time_base": math.nan}),
            ("zero duration", {"terminal_duration": 0}),
            ("duration reported as seconds instead of ticks", {"terminal_duration": 3008 / 90_000}),
            ("publisher end moved 50 microseconds", {"clip": shifted_end}),
            ("terminal duration extended by eight ticks", {"terminal_duration": 3016}),
            ("stream overrun without matching frame end", {"duration": 362_861}),
            ("aligned terminal frame exceeds twice the median period", {
                "duration": 365_852, "terminal_duration": 6007,
            }),
        ):
            with self.subTest(description=description):
                with self.assertRaisesRegex(AdmissionError, "decoded clip timing disagrees with metadata"):
                    self.decode(0, allow_terminal_pts_endpoint=True, **{**options, **change})
                self.assertEqual(list(self.frames.iterdir()), [])

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
