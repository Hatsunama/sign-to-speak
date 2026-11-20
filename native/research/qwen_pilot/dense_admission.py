# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["av>=17,<18", "Pillow>=11,<13", "huggingface_hub>=1,<2"]
# ///
"""Resample the original ASL pilot cohort to 32 presentation-timed frames."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys
import tempfile
from urllib.error import HTTPError

import admission_probe as probe
from train_qwen_pilot import DENSE, SPARSE, SPARSE_ADMISSION_SHA256, validate_admission


SCRATCH = Path("/tmp/sign-to-speak")


def original_receipt(root):
    receipt_bytes = (root / "admission.json").read_bytes()
    if hashlib.sha256(receipt_bytes).hexdigest() != SPARSE_ADMISSION_SHA256:
        raise probe.AdmissionError("sparse admission receipt differs from the verified original")
    receipt = json.loads(receipt_bytes)
    validate_admission(receipt)
    if (receipt["counts"] != {"train": 16, "holdout": 4}
            or receipt["max_frame_edge"] != probe.MAX_EDGE
            or receipt["original_bytes"] != sum(clip["original_bytes"] for clip in receipt["clips"])
            or not 0 < receipt["original_bytes"] <= probe.TOTAL_BYTES):
        raise probe.AdmissionError("original cohort or resource bounds are invalid")
    return receipt


def resample(receipt, root, scratch):
    clip_receipts = []
    original_bytes = 0
    frame_bytes = 0
    with tempfile.TemporaryDirectory(prefix="qwen-dense-", dir=scratch) as directory:
        for clip in receipt["clips"]:
            source_path = clip["source_path"]
            source_split = {"train": "dev", "holdout": "devtest"}[clip["split"]]
            if source_path != probe.safe_video_path(source_split, Path(source_path).name):
                raise probe.AdmissionError("invalid pinned video path")
            video = Path(directory) / Path(source_path).name
            immutable_url = probe.dataset_url(source_path)
            cap = min(probe.CLIP_BYTES, probe.TOTAL_BYTES - original_bytes - frame_bytes)
            with video.open("xb") as output:
                byte_count, digest = probe.fetch(immutable_url, output, cap, clip["original_bytes"])
            if digest != clip["sha256"]:
                raise probe.AdmissionError("original video digest differs from sparse admission")
            original_bytes += byte_count
            duration, frames = probe.decode_frames(
                video, root, clip["id"], frame_count=DENSE.admitted_frames,
            )
            video.unlink()
            if not math.isclose(duration, clip["duration_seconds"], abs_tol=1e-8):
                raise probe.AdmissionError("decoded duration differs from sparse admission")
            frame_bytes += sum((root / frame["path"]).stat().st_size for frame in frames)
            if original_bytes + frame_bytes > probe.TOTAL_BYTES:
                raise probe.AdmissionError("original and derived bytes exceeded 1 GiB")
            clip_receipts.append({**clip, "frames": frames, "immutable_video_url": immutable_url})
            print(f"Resampled {len(clip_receipts)}/{len(receipt['clips'])} clips.", flush=True)
    if original_bytes != receipt["original_bytes"]:
        raise probe.AdmissionError("downloaded bytes differ from sparse admission")
    return clip_receipts, frame_bytes


def main(argv=()):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=DENSE.root,
        help=f"New dense admission directory (default: {DENSE.root})",
    )
    root = parser.parse_args(argv).output.resolve()
    if sys.platform != "linux" or not SPARSE.root.is_dir():
        print("Dense admission requires the mounted cloud pilot directory.", file=sys.stderr)
        return 1
    stage = "validating original cohort"
    incomplete_output = False
    try:
        receipt = original_receipt(SPARSE.root)
        stage = "creating dense output directory"
        root.mkdir(exist_ok=False)
        incomplete_output = True
        stage = "resampling original clips"
        SCRATCH.mkdir(mode=0o700, exist_ok=True)
        clips, frame_bytes = resample(receipt, root, SCRATCH)
        stage = "validating and publishing dense admission"
        attribution_bytes = 0
        for key in ("license_path", "attribution_path"):
            name = receipt["dataset"][key]
            content = (SPARSE.root / name).read_bytes()
            (root / name).write_bytes(content)
            attribution_bytes += len(content)
        document = {
            **receipt,
            "source_admission_sha256": SPARSE_ADMISSION_SHA256,
            "frames_per_clip": DENSE.admitted_frames,
            "evaluation_role": DENSE.evaluation_role,
            "orientation_policy": probe.ORIENTATION_POLICY,
            "derived_frame_bytes": frame_bytes,
            "attribution_bytes": attribution_bytes,
            "max_total_bytes": probe.TOTAL_BYTES,
            "clips": clips,
        }
        validate_admission(document, DENSE)
        receipt_size = len((json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        if receipt["original_bytes"] + frame_bytes + attribution_bytes + receipt_size > probe.TOTAL_BYTES:
            raise probe.AdmissionError("original and derived bytes exceeded 1 GiB")
        probe.write_receipt(root, document)
        incomplete_output = False
        duplicate_count = sum(frame["duplicate"] for clip in clips for frame in clip["frames"])
        print(
            f"Dense admission ready. {len(clips) * DENSE.admitted_frames} frames, "
            f"{duplicate_count} repeated presentation timestamps, {frame_bytes} derived frame bytes.",
            flush=True,
        )
    except Exception as error:
        if isinstance(error, HTTPError):
            detail = f"HTTP {error.code}"
        elif isinstance(error, ValueError):
            detail = str(error)
        else:
            detail = type(error).__name__
        print(f"Dense admission failed while {stage} ({detail}).", file=sys.stderr)
        if incomplete_output:
            try:
                shutil.rmtree(root)
            except OSError as cleanup_error:
                print(
                    f"Dense admission failed while removing incomplete output at {root} ({cleanup_error}).",
                    file=sys.stderr,
                )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
