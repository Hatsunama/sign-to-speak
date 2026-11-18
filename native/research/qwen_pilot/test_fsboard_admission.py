"""Stdlib regression tests for the FSboard evaluation admission."""
import hashlib
import io
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request

from fsboard_admission import (
    AdmissionError,
    CLIP_BYTES,
    FRAME_COUNT,
    HTTPSRedirects,
    LICENSE_BYTES,
    admit,
    annotation_window,
    copy_verified,
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
