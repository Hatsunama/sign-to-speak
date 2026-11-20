"""Offline research probe for measured-timestamp Holistic extraction on one clip."""

import argparse
from collections import Counter
from fractions import Fraction
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
from tempfile import TemporaryFile
from typing import NotRequired, TypedDict

from admission_probe import CLIP_BYTES, bounded_copy
from local_network_guard import enforce_offline
from video_orientation import apply_display_rotation, display_rotation


MODEL_SHA256 = "e2dab61191e2dcd0a15f943d8e3ed1dce13c82dfa597b9dd39f562975a50c3f8"
MODEL_BYTES = 13_683_609
MEDIAPIPE_VERSION = "1.1.1"
AV_VERSION = "17.0.0"
MAX_DECODED_FRAMES = 600
MAX_DURATION_SECONDS = 20
MAX_SOURCE_EDGE = 4096
MAX_INPUT_EDGE = 640
LANDMARK_FIELDS = {
    "left_hand": ("left_hand_landmarks", 21),
    "right_hand": ("right_hand_landmarks", 21),
    "pose": ("pose_landmarks", 33),
    "face": ("face_landmarks", 478),
}
HOLISTIC_OPTIONS = {
    "min_face_detection_confidence": 0.5,
    "min_face_suppression_threshold": 0.3,
    "min_face_landmarks_confidence": 0.5,
    "min_pose_detection_confidence": 0.5,
    "min_pose_suppression_threshold": 0.3,
    "min_pose_landmarks_confidence": 0.5,
    "min_hand_landmarks_confidence": 0.5,
    "output_face_blendshapes": False,
    "output_segmentation_mask": False,
}


class ProbeError(ValueError):
    """The worker could not complete bounded extraction."""


class FrameRecord(TypedDict):
    frame_index: int
    source_pts: int | None
    source_time_base: list[int] | None
    timestamp_ms: int | None
    status: str
    display_rotation_degrees_ccw: NotRequired[int]
    input_size: NotRequired[list[int]]
    landmarks: NotRequired[dict[str, list[list[float]] | None]]


class TimestampLedger:
    """Convert measured frame PTS to strictly increasing MediaPipe milliseconds."""

    def __init__(self):
        self.decoded = 0
        self.origin = None
        self.latest_time = None
        self.last_ms = -1

    def record(self, pts: int | None, time_base: Fraction | None) -> FrameRecord:
        if self.decoded >= MAX_DECODED_FRAMES:
            raise ProbeError("decoded frame limit exceeded")
        if time_base is not None and time_base <= 0:
            raise ProbeError("nonpositive source time base")
        frame = FrameRecord(
            frame_index=self.decoded,
            source_pts=pts,
            source_time_base=[time_base.numerator, time_base.denominator] if time_base is not None else None,
            timestamp_ms=None,
            status="processed",
        )
        self.decoded += 1
        if pts is None:
            frame["status"] = "missing_pts"
            return frame
        if time_base is None:
            frame["status"] = "missing_time_base"
            return frame

        source_time = pts * time_base
        if self.origin is None:
            self.origin = source_time
        elapsed = source_time - self.origin
        if abs(elapsed) > MAX_DURATION_SECONDS:
            raise ProbeError("measured duration limit exceeded")
        if self.latest_time is not None and source_time <= self.latest_time:
            frame["status"] = "duplicate_pts" if source_time == self.latest_time else "nonmonotonic_pts"
            return frame

        self.latest_time = source_time
        timestamp_ms = elapsed * 1000 // 1
        if timestamp_ms <= self.last_ms:
            frame["status"] = "duplicate_timestamp_ms"
            return frame
        self.last_ms = timestamp_ms
        frame["timestamp_ms"] = timestamp_ms
        return frame


def nearest_center_indices(source_size: int, output_size: int) -> list[int]:
    return [(2 * index + 1) * source_size // (2 * output_size) for index in range(output_size)]


def landmark_payload(result) -> dict[str, list[list[float]] | None]:
    payload = {}
    for name, (attribute, count) in LANDMARK_FIELDS.items():
        points = getattr(result, attribute)
        if len(points) == 0:
            payload[name] = None
            continue
        if len(points) != count:
            raise ProbeError(f"{name} must have {count} normalized XYZ landmarks")
        coordinates = [[point.x, point.y, point.z] for point in points]
        if any(not math.isfinite(value) for triple in coordinates for value in triple):
            raise ProbeError(f"{name} contains a nonfinite coordinate")
        payload[name] = coordinates
    return payload


def hand_coverage(frames: list[FrameRecord], hand: str) -> dict[str, int]:
    present_frames = adjacent_joint_pairs = changed_xyz_pairs = 0
    previous = None
    for frame in frames:
        current = frame["landmarks"][hand] if frame["status"] == "processed" else None
        if current is not None:
            present_frames += 1
            if previous is not None:
                adjacent_joint_pairs += len(current)
                changed_xyz_pairs += sum(before != after for before, after in zip(previous, current, strict=True))
        previous = current
    return {
        "present_frames": present_frames,
        "adjacent_joint_pairs": adjacent_joint_pairs,
        "changed_xyz_pairs": changed_xyz_pairs,
    }


class SuccessReceipt(TypedDict):
    type: str
    schema_version: int
    status: str
    input: dict
    runtime: dict
    method: dict
    frame_counts: dict[str, int]
    coverage: dict[str, dict[str, int]]
    frames: list[FrameRecord]


def make_success_receipt(frames: list[FrameRecord], inputs: dict, runtime: dict) -> SuccessReceipt:
    counts = Counter(frame["status"] for frame in frames)
    if not counts.get("processed"):
        raise ProbeError("no frames processed")
    return {
        "type": "holistic_video_extraction",
        "schema_version": 1,
        "status": "SUCCESS",
        "input": inputs,
        "runtime": runtime,
        "method": {
            "decoding": "Full video stream in decode order from a staged local MP4.",
            "timing": "PTS times frame time base, relative to first measured frame, floored to milliseconds.",
            "pixels": "Full decoded RGBA raster with validated display rotation and nearest-center downsampling.",
            "mirrored": False,
            "max_input_edge": MAX_INPUT_EDGE,
            "max_decoded_frames": MAX_DECODED_FRAMES,
            "max_duration_seconds": MAX_DURATION_SECONDS,
            "coordinate_space": "Holistic normalized XYZ in anatomical slots, null for missing landmarks.",
            "motion": "Exact XYZ changes within each hand slot across adjacent decoded frames with observations.",
            "android_reference": "CameraRgbaPixels and LiveHolisticHandDetector (Tasks Vision 1.1.1).",
            "android_aar_option_defaults_verified": False,
            "android_parity_evaluated": False,
            "recognition_evaluated": False,
        },
        "frame_counts": dict(counts),
        "coverage": {
            "left_hand": hand_coverage(frames, "left_hand"),
            "right_hand": hand_coverage(frames, "right_hand"),
            "pose": {"present_frames": sum(frame.get("landmarks", {}).get("pose") is not None for frame in frames)},
            "face": {"present_frames": sum(frame.get("landmarks", {}).get("face") is not None for frame in frames)},
        },
        "frames": frames,
    }


def write_private_json(directory: Path, name: str, document: dict) -> None:
    target = directory / name
    temporary = target.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    staged = None
    primary_error = None
    warnings = []
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            staged = os.fstat(stream.fileno())
            json.dump(document, stream, allow_nan=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except (Exception, KeyboardInterrupt) as error:
            # The link can commit before the caller observes an error.
            try:
                committed = staged.st_ino != 0 and os.path.samestat(staged, target.lstat())
            except FileNotFoundError:
                committed = False
            except (OSError, KeyboardInterrupt) as stat_error:
                error.add_note(f"Could not verify receipt publication at {target}: {stat_error}")
                committed = False
            if not committed:
                raise
            warnings.append(f"Receipt committed at {target} despite link error: {error}")
    except (Exception, KeyboardInterrupt) as error:
        primary_error = error
        raise
    finally:
        try:
            if staged is None or staged.st_ino == 0 or not os.path.samestat(staged, temporary.lstat()):
                raise OSError("staged file identity could not be verified")
            temporary.unlink()
        except FileNotFoundError:
            pass
        except (OSError, KeyboardInterrupt) as cleanup_error:
            message = f"Could not remove staged output {temporary}: {cleanup_error}"
            if primary_error is None:
                warnings.append(message)
            else:
                primary_error.add_note(message)
        if warnings:
            try:
                print("\n".join(f"Warning: {message}" for message in warnings), file=sys.stderr)
            except (OSError, ValueError):
                # Diagnostic output cannot change the committed receipt.
                pass


def open_local_file(path: Path, expected_bytes: int):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size != expected_bytes:
            raise ProbeError("input must be a regular file with the pinned byte length")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def validate_copy(source, target, declared_bytes: int, cap: int, sha256: str) -> None:
    _, digest = bounded_copy(source, target, cap, declared=declared_bytes)
    if digest != sha256:
        raise ProbeError("input did not match its SHA-256 pin")


def upright_pixels(frame, np):
    from PIL import Image

    rotation = display_rotation(frame)
    rgba = frame.to_ndarray(format="rgba")
    upright = np.asarray(apply_display_rotation(Image.fromarray(rgba), rotation))
    height, width = upright.shape[:2]
    scale = min(1.0, MAX_INPUT_EDGE / max(width, height))
    output_width = max(1, int(width * scale))
    output_height = max(1, int(height * scale))
    if (output_width, output_height) != (width, height):
        rows = nearest_center_indices(height, output_height)
        cols = nearest_center_indices(width, output_width)
        upright = upright[np.ix_(rows, cols)]
    return np.ascontiguousarray(upright), rotation


def decode_video(clip, detector, av, mp, np, frames: list[FrameRecord]) -> None:
    ledger = TimestampLedger()
    with av.open(clip, mode="r", format="mov", options={"enable_drefs": "0", "use_absolute_path": "0"}) as container:
        if len(container.streams.video) != 1:
            raise ProbeError("expected one video stream")
        stream = container.streams.video[0]
        if not (1 <= stream.width <= MAX_SOURCE_EDGE and 1 <= stream.height <= MAX_SOURCE_EDGE):
            raise ProbeError("video dimensions out of bounds")
        durations = (
            Fraction(container.duration, av.time_base) if container.duration is not None else None,
            stream.duration * stream.time_base if stream.duration is not None and stream.time_base is not None else None,
        )
        if any(duration is not None and not 0 < duration <= MAX_DURATION_SECONDS for duration in durations):
            raise ProbeError("reported video duration out of bounds")
        stream.codec_context.thread_count = 1
        stream.codec_context.options = {"max_pixels": str(MAX_SOURCE_EDGE * MAX_SOURCE_EDGE)}
        for frame in container.decode(stream):
            record = ledger.record(frame.pts, frame.time_base)
            if record["status"] == "processed":
                if not (1 <= frame.width <= MAX_SOURCE_EDGE and 1 <= frame.height <= MAX_SOURCE_EDGE):
                    raise ProbeError(f"source frame {record['frame_index']} dimensions out of bounds")
                try:
                    rgba, rotation = upright_pixels(frame, np)
                    image = mp.Image(image_format=mp.ImageFormat.SRGBA, data=rgba)
                    result = detector.detect_for_video(image, record["timestamp_ms"])
                    record["landmarks"] = landmark_payload(result)
                except Exception as error:
                    error.add_note(f"Failed at decoded frame {record['frame_index']}, source PTS {record['source_pts']}")
                    raise
                record["display_rotation_degrees_ccw"] = rotation
                record["input_size"] = [int(rgba.shape[1]), int(rgba.shape[0])]
                del image, rgba, result
            frames.append(record)


def run(args, frames: list[FrameRecord]) -> SuccessReceipt:
    import av
    import mediapipe as mp
    import numpy as np
    from PIL import __version__ as pillow_version

    if (av.__version__, mp.__version__) != (AV_VERSION, MEDIAPIPE_VERSION):
        raise ProbeError(f"expected PyAV {AV_VERSION} and MediaPipe {MEDIAPIPE_VERSION}")
    with open_local_file(args.model, MODEL_BYTES) as source, io.BytesIO() as model:
        validate_copy(source, model, MODEL_BYTES, MODEL_BYTES, MODEL_SHA256)
        base = mp.tasks.BaseOptions(model_asset_buffer=model.getvalue(), delegate=mp.tasks.BaseOptions.Delegate.CPU)
    vision = mp.tasks.vision
    options = vision.HolisticLandmarkerOptions(
        base_options=base, running_mode=vision.RunningMode.VIDEO, **HOLISTIC_OPTIONS,
    )
    with TemporaryFile() as clip:
        with open_local_file(args.clip, args.clip_bytes) as source:
            validate_copy(source, clip, args.clip_bytes, CLIP_BYTES, args.clip_sha256)
        clip.seek(0)
        with vision.HolisticLandmarker.create_from_options(options) as detector:
            decode_video(clip, detector, av, mp, np, frames)

    return make_success_receipt(
        frames,
        {
            "clip": {"sha256": args.clip_sha256, "bytes": args.clip_bytes},
            "model": {"sha256": MODEL_SHA256, "bytes": MODEL_BYTES},
        },
        {
            "mediapipe": mp.__version__, "pyav": av.__version__,
            "numpy": np.__version__, "pillow": pillow_version,
            "delegate": "CPU", "running_mode": "VIDEO",
            "holistic_options": HOLISTIC_OPTIONS,
            "offline_guard": "seccomp_direct_socket_barrier",
        },
    )


def main() -> None:
    enforce_offline()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clip", type=Path, required=True)
    parser.add_argument("--clip-sha256", required=True)
    parser.add_argument("--clip-bytes", type=int, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{64}", args.clip_sha256):
        parser.error("--clip-sha256 must be a lowercase 64-character SHA-256")
    if not 0 < args.clip_bytes <= CLIP_BYTES:
        parser.error("--clip-bytes outside permitted range")
    args.output.mkdir(mode=0o700)
    frames: list[FrameRecord] = []
    try:
        write_private_json(args.output, "receipt.json", run(args, frames))
    except Exception as error:
        try:
            write_private_json(args.output, "failure.json", {
                "type": "holistic_video_extraction",
                "schema_version": 1,
                "status": "FAILURE",
                "error_type": type(error).__name__,
                "error": str(error),
                "notes": getattr(error, "__notes__", []),
                "frame_counts_before_failure": dict(Counter(frame["status"] for frame in frames)),
            })
        except Exception as receipt_error:
            error.add_note(f"Could not write failure receipt: {receipt_error}")
        raise


if __name__ == "__main__":
    main()

