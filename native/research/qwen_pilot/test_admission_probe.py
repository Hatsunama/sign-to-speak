import hashlib
import io
import unittest

from admission_probe import bounded_copy, canonical_url, independent_rows, normalized_prompt, safe_video_path, select_frame_indices


class AdmissionProbeTests(unittest.TestCase):
    def test_bounded_copy_checks_hub_length_and_cap(self):
        for data, declared, cap, message in [
            (b"12345", 4, 5, "longer"),
            (b"123", 4, 5, "truncated"),
            (b"", 6, 5, "cap"),
        ]:
            with self.subTest(data=data, declared=declared, cap=cap):
                with self.assertRaisesRegex(ValueError, message):
                    bounded_copy(io.BytesIO(data), io.BytesIO(), cap, declared)
        copied = io.BytesIO()
        self.assertEqual(
            bounded_copy(io.BytesIO(b"1234"), copied, 4, 4),
            (4, hashlib.sha256(b"1234").hexdigest()),
        )
        self.assertEqual(copied.getvalue(), b"1234")

    def test_holdout_excludes_canonical_source_and_prompt_overlap(self):
        def row(url, sentence):
            return {"group": canonical_url(url), "prompt_key": normalized_prompt(sentence)}

        training = [row("http://example.org/wiki/A_B?tracking=1#top", "Good morning!")]
        unique = row("https://example.org/wiki/D", "Another sentence")
        holdout = [
            row("https://EXAMPLE.org/wiki/A%20B/", "Different sentence"),
            row("https://example.org/wiki/C", " ＧＯＯＤ   morning. "),
            unique,
        ]
        self.assertEqual(independent_rows(training, holdout), [unique])

    def test_video_path_rejects_traversal_and_non_video(self):
        self.assertEqual(safe_video_path("dev", "clip-1.mp4"), "data/dev/clip-1.mp4")
        for name in ("../clip.mp4", "/clip.mp4", r"C:\\clip.mp4", "a/../clip.mp4", "%2e%2e/clip.mp4", "clip.zip"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                safe_video_path("dev", name)

    def test_nearest_actual_timestamps_and_duplicates(self):
        self.assertEqual(
            select_frame_indices([0.0, 1.0, 3.0], [0.0, 0.2, 1.0, 2.0, 3.0]),
            [(0, False), (0, True), (1, False), (1, True), (2, False)],
        )
        with self.assertRaisesRegex(ValueError, "ordered"):
            select_frame_indices([0.0, 2.0, 1.0], [1.0])


if __name__ == "__main__":
    unittest.main()
