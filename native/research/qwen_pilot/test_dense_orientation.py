import io
from pathlib import Path
import struct
import tempfile
import unittest

import av
from PIL import Image, ImageDraw

from admission_probe import AdmissionError, MAX_EDGE, decode_frames


class DenseOrientationTests(unittest.TestCase):
    def video_fixture(self, root, matrix):
        movie = io.BytesIO()
        with av.open(movie, "w", format="mp4") as container:
            stream = container.add_stream("mpeg4", rate=2)
            stream.width, stream.height = 480, 320
            stream.pix_fmt = "yuv420p"
            image = Image.new("RGB", (stream.width, stream.height))
            draw = ImageDraw.Draw(image)
            draw.rectangle((0, 0, 239, 159), fill=(255, 0, 0))
            draw.rectangle((240, 0, 479, 159), fill=(0, 255, 0))
            draw.rectangle((0, 160, 239, 319), fill=(0, 0, 255))
            draw.rectangle((240, 160, 479, 319), fill=(255, 255, 0))
            for _ in range(2):
                for packet in stream.encode(av.VideoFrame.from_image(image)):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)

        data = bytearray(movie.getvalue())
        track_header = data.index(b"tkhd")
        self.assertEqual(data[track_header + 4], 0)
        matrix_offset = track_header + 44
        self.assertEqual(
            struct.unpack_from(">9i", data, matrix_offset),
            (65536, 0, 0, 0, 65536, 0, 0, 0, 1 << 30),
        )
        struct.pack_into(">9i", data, matrix_offset, *matrix)
        video = root / "display-matrix.mp4"
        video.write_bytes(data)
        return video

    def test_decode_applies_track_display_matrix_to_all_dense_slots(self):
        fixtures = [
            (
                (0, -65536, 0, 65536, 0, 0, 0, 0, 1 << 30),
                90,
                (256, 384),
                ((0, 1, 0), (1, 1, 0), (1, 0, 0), (0, 0, 1)),
            ),
            (
                (-65536, 0, 0, 0, -65536, 0, 0, 0, 1 << 30),
                -180,
                (384, 256),
                ((1, 1, 0), (0, 0, 1), (0, 1, 0), (1, 0, 0)),
            ),
            (
                (65536, 0, 0, 0, 65536, 0, 0, 0, 1 << 30),
                0,
                (384, 256),
                ((1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 0)),
            ),
        ]
        for matrix, angle, size, expected_corners in fixtures:
            with self.subTest(angle=angle), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                video = self.video_fixture(root, matrix)
                with av.open(str(video)) as container:
                    self.assertEqual([frame.rotation for frame in container.decode(video=0)], [angle, angle])

                duration, frames = decode_frames(video, root, "clip", frame_count=32)
                self.assertEqual(duration, 1.0)
                self.assertEqual(len(frames), 32)
                self.assertEqual(sum(frame["duplicate"] for frame in frames), 30)
                for slot, frame in enumerate(frames):
                    actual = 0.0 if slot < 8 else 0.5
                    self.assertEqual(frame["requested_seconds"], slot / 31)
                    self.assertEqual(frame["actual_seconds"], actual)
                    self.assertEqual(frame["source_pts_seconds"], actual)
                    self.assertEqual(frame["duplicate"], slot not in (0, 8))
                    with Image.open(root / frame["path"]) as image:
                        self.assertEqual(image.size, size)
                        width, height = image.size
                        self.assertEqual(max(image.size), MAX_EDGE)
                        corners = [
                            image.getpixel((x, y))
                            for x, y in ((16, 16), (width - 16, 16), (16, height - 16), (width - 16, height - 16))
                        ]
                        self.assertEqual(
                            tuple(tuple(int(channel > 128) for channel in rgb) for rgb in corners),
                            expected_corners,
                        )
                    self.assertEqual(frame["display_rotation_degrees"], angle)
                    self.assertEqual(frame["applied_rotation_degrees_ccw"], angle)

    def test_decode_rejects_unsupported_track_display_rotation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = self.video_fixture(root, (46341, -46341, 0, 46341, 46341, 0, 0, 0, 1 << 30))
            with av.open(str(video)) as container:
                self.assertEqual([frame.rotation for frame in container.decode(video=0)], [45, 45])
            with self.assertRaisesRegex(AdmissionError, "(?i)(rotation|matrix)"):
                decode_frames(video, root, "clip", frame_count=32)
            self.assertFalse(any(root.glob("frames/**/*.png")))


if __name__ == "__main__":
    unittest.main()