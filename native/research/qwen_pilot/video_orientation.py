"""Read and apply right-angle video display transforms without mirroring."""

import struct


class VideoOrientationError(ValueError):
    """Unsupported video orientation metadata."""


def display_rotation(frame):
    matrix = frame.side_data.get("DISPLAYMATRIX")
    if matrix is not None:
        # FFmpeg stores native-endian fixed-point coefficients. Allow translation
        # because the standalone rotated raster is rebased to its own origin.
        try:
            a, b, u, c, d, v, _, _, w = struct.unpack("=9i", matrix)
        except struct.error as exc:
            raise VideoOrientationError("invalid video display matrix") from exc
        unit = 1 << 16
        matrix_rotation = {
            (unit, 0, 0, unit): 0,
            (0, -unit, unit, 0): 90,
            (-unit, 0, 0, -unit): 180,
            (0, unit, -unit, 0): 270,
        }.get((a, b, c, d))
        if matrix_rotation is None or u != 0 or v != 0 or w != (1 << 30):
            raise VideoOrientationError("video display matrix is not a nonmirrored right-angle rotation")

    rotation = frame.rotation
    if type(rotation) not in (int, float) or rotation not in (0, 90, -90, 180, -180):
        raise VideoOrientationError("unsupported video display rotation")
    if matrix is not None and matrix_rotation != rotation % 360:
        raise VideoOrientationError("video display rotation disagrees with display matrix")
    return int(rotation)


def apply_display_rotation(image, rotation):
    """Apply a counterclockwise display rotation to a Pillow image."""
    from PIL import Image

    transpose = {
        90: Image.Transpose.ROTATE_90,
        180: Image.Transpose.ROTATE_180,
        270: Image.Transpose.ROTATE_270,
    }.get(rotation % 360)
    return image if transpose is None else image.transpose(transpose)
