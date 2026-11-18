# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["av>=14,<18", "Pillow>=11,<13"]
# ///
"""Extract a bounded FSboard test sample for private evaluation review."""
import argparse
import hashlib
import json
import math
import re
import statistics
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

DATASET = "googleai/fsboard"
DATASET_URL = "https://www.kaggle.com/datasets/googleai/fsboard/versions/13"
ENDPOINT = "https://www.kaggle.com/api/v1/datasets.DatasetApiService/DownloadDataset"
VERSION = 13
SPLIT = "daun_v3-test"
LICENSE_PATH = "LICENSE"
METADATA_PATH = f"daun_v3/metadata/{SPLIT}.json"
VIDEO_DIR = f"daun_v3/video_clips/{SPLIT}"
MIB = 1024 * 1024
CLIP_BYTES = 20 * MIB
TOTAL_BYTES = 150 * MIB
METADATA_BYTES = 9_227_832
METADATA_SHA256 = "94f53f6c9d9199cbc50c5207a31a96d400734e49eac60d4a157fc515a587423b"
LICENSE_BYTES = 129
LICENSE_SHA256 = "96218feb836f9005261b0346501f349d11e4019deb6bbfce899483d94639c7dd"
MIN_COUNT = 4
TARGET_COUNT = 8
MAX_COUNT = 15
FRAME_COUNT = 32
MAX_EDGE = 384
MAX_DIMENSION = 4096
MAX_DECODED_FRAMES = 12_000
SAFE_BASENAME = re.compile(r"[A-Za-z0-9_.-]+\.mp4")


class AdmissionError(ValueError):
    """Validation error whose message contains no private source identifiers."""


class HTTPSRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise AdmissionError("download redirect is not a valid HTTPS URL")
        # urllib converts the Kaggle 302 response from POST to GET.
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def copy_verified(source, target, cap, declared=None, expected_md5=None):
    limit = cap if declared is None else declared
    if not 0 < limit <= cap:
        raise AdmissionError("declared length exceeds download cap")
    sha256 = hashlib.sha256()
    md5 = hashlib.md5(usedforsecurity=False) if expected_md5 is not None else None
    total = 0
    while block := source.read(min(MIB, limit - total + 1)):
        total += len(block)
        if total > limit:
            raise AdmissionError("response exceeds allowed download length")
        sha256.update(block)
        if md5 is not None:
            md5.update(block)
        target.write(block)
    if total == 0 or (declared is not None and total != declared):
        raise AdmissionError("empty or truncated response")
    if md5 is not None and md5.hexdigest() != expected_md5:
        raise AdmissionError("download MD5 disagrees with metadata")
    return total, sha256.hexdigest()


def fetch_file(name, target, cap, *, size=None, md5=None):
    if name not in {LICENSE_PATH, METADATA_PATH}:
        prefix, _, filename = name.rpartition("/")
        if prefix != VIDEO_DIR or not SAFE_BASENAME.fullmatch(filename):
            raise AdmissionError("invalid dataset file path")
    owner, dataset = DATASET.split("/")
    payload = json.dumps({
        "ownerSlug": owner,
        "datasetSlug": dataset,
        "datasetVersionNumber": VERSION,
        "fileName": name,
    }).encode("utf-8")
    request = Request(ENDPOINT, method="POST", data=payload, headers={
        "Accept-Encoding": "identity",
        "Content-Type": "application/json",
        "User-Agent": "sign-to-speak-fsboard-admission/1",
    })
    with build_opener(HTTPSRedirects()).open(request, timeout=45) as response:
        if response.status != 200:
            raise AdmissionError("unexpected dataset response status")
        declared = response.headers.get("Content-Length")
        if declared is not None:
            if not re.fullmatch(r"[0-9]{1,12}", declared):
                raise AdmissionError("malformed Content-Length")
            declared = int(declared)
            if size is not None and size != declared:
                raise AdmissionError("response size disagrees with metadata")
        with target.open("xb") as destination:
            return copy_verified(response, destination, cap, size if size is not None else declared, md5)


def candidate_from_row(row):
    phrase = row.get("phrase")
    signer = row.get("signerId")
    full_video = row.get("fullVideoFilename")
    clip = row.get("clipFilename")
    size = row.get("clipFileSize")
    md5 = row.get("clipFileMd5")
    if (
        row.get("signingType") != "fs"
        or row.get("sensitiveContent") is not False
        or not isinstance(phrase, str)
        or not re.fullmatch(r"[a-z]{3,8}", phrase)
        or not isinstance(signer, str)
        or not signer.strip()
        or not isinstance(full_video, str)
        or not SAFE_BASENAME.fullmatch(full_video)
        or not isinstance(clip, str)
        or not SAFE_BASENAME.fullmatch(clip)
        or type(size) is not int
        or not 0 < size <= CLIP_BYTES
        or not isinstance(md5, str)
        or not re.fullmatch(r"[A-Fa-f0-9]{32}", md5)
    ):
        return None
    timestamp_keys = ("clipStartTimeS", "clipEndTimeS", "annotationStartTimeS", "annotationEndTimeS")
    bounds = [row.get(key) for key in timestamp_keys]
    try:
        valid_bounds = all(type(value) in (int, float) and math.isfinite(value) for value in bounds)
    except OverflowError:
        valid_bounds = False
    if not valid_bounds:
        return None
    clip_start, clip_end, annotation_start, annotation_end = bounds
    if not (0 <= clip_start <= annotation_start < annotation_end <= clip_end):
        return None
    if not 0.5 <= annotation_end - annotation_start <= 3.2 + 1e-9:
        return None
    return {
        "reference": phrase,
        "signer_id": signer,
        "full_video_filename": full_video,
        "clip_filename": clip,
        "source_path": f"{VIDEO_DIR}/{clip}",
        "original_bytes": size,
        "metadata_md5": md5.lower(),
        "clip_start_full_video_seconds": clip_start,
        "clip_end_full_video_seconds": clip_end,
        "annotation_start_full_video_seconds": annotation_start,
        "annotation_end_full_video_seconds": annotation_end,
    }


def parse_candidates(content):
    try:
        rows = json.loads(content)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise AdmissionError("invalid dataset metadata JSON") from exc
    if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
        raise AdmissionError("expected a nonempty metadata array of objects")
    candidates = {}
    for row in rows:
        clip = candidate_from_row(row)
        if clip is None:
            continue
        path = clip["source_path"]
        if path in candidates and candidates[path] != clip:
            raise AdmissionError("conflicting metadata for a clip path")
        candidates[path] = clip
    return list(candidates.values())


def select_candidates(candidates, count, budget=TOTAL_BYTES):
    if not 1 <= count <= MAX_COUNT or not 0 <= budget <= TOTAL_BYTES:
        raise AdmissionError("invalid sample count or byte budget")
    chosen = []
    signers, videos, phrases = set(), set(), set()
    for clip in sorted(candidates, key=lambda item: (item["original_bytes"], item["source_path"])):
        signer, video, phrase = clip["signer_id"], clip["full_video_filename"], clip["reference"]
        if (clip["original_bytes"] > budget or signer in signers
                or video in videos or phrase in phrases):
            continue
        chosen.append(clip)
        signers.add(signer)
        videos.add(video)
        phrases.add(phrase)
        budget -= clip["original_bytes"]
        if len(chosen) == count:
            break
    return chosen


def annotation_window(clip):
    start = clip["clip_start_full_video_seconds"]
    return (
        clip["annotation_start_full_video_seconds"] - start,
        clip["annotation_end_full_video_seconds"] - start,
    )


def select_samples(pts, *, origin, window, period):
    start, end = window
    if (
        not all(math.isfinite(value) for value in (origin, start, end, period))
        or not 0 <= start < end
        or period <= 0
        or not pts
        or any(not math.isfinite(value) for value in pts)
        or any(left > right for left, right in zip(pts, pts[1:]))
    ):
        raise AdmissionError("missing, invalid or unordered presentation timestamps")
    available = []
    for index, timestamp in enumerate(pts):
        relative = timestamp - origin
        if start <= relative <= end and (not available or timestamp != available[-1][1]):
            available.append((index, timestamp, relative))
    if len(available) < FRAME_COUNT:
        raise AdmissionError("fewer than 32 distinct presentation timestamps in the annotation")

    requested = [start + (end - start) * slot / (FRAME_COUNT - 1) for slot in range(FRAME_COUNT)]
    # Minimize total time error over increasing, unique frame indices. Each slot
    # must be within a frame period, even if two slots share the nearest PTS.
    previous = [0.0] * len(available)
    predecessors = []
    for slot, seconds in enumerate(requested):
        costs = [math.inf] * len(available)
        parents = [-1] * len(available)
        best_cost, best_index = math.inf, -1
        for index, (_, _, relative) in enumerate(available):
            if index > 0 and previous[index - 1] < best_cost:
                best_cost, best_index = previous[index - 1], index - 1
            error = abs(relative - seconds)
            if error <= period + 1e-9 and (slot == 0 or best_index >= 0):
                costs[index] = error if slot == 0 else best_cost + error
                parents[index] = best_index
        predecessors.append(parents)
        previous = costs
    index = min(range(len(previous)), key=previous.__getitem__)
    if not math.isfinite(previous[index]):
        raise AdmissionError("cannot match 32 timed slots within one frame period")
    samples = []
    for slot in reversed(range(FRAME_COUNT)):
        frame_index, source_pts, relative = available[index]
        samples.append({
            "frame_index": frame_index,
            "requested_seconds": requested[slot],
            "actual_seconds": relative,
            "source_pts_seconds": source_pts,
            "annotation_offset_seconds": relative - start,
            "duplicate": False,
        })
        index = predecessors[slot][index]
    return list(reversed(samples))


def check_dimensions(image):
    if not (1 <= image.width <= MAX_DIMENSION and 1 <= image.height <= MAX_DIMENSION):
        raise AdmissionError("video dimensions out of bounds")


def decode_frames(video, output, clip_id, clip):
    import av

    timestamps = []
    with av.open(str(video), mode="r") as container:
        if len(container.streams.video) != 1:
            raise AdmissionError("expected a single video stream")
        stream = container.streams.video[0]
        check_dimensions(stream)
        for frame in container.decode(stream):
            check_dimensions(frame)
            if len(timestamps) == MAX_DECODED_FRAMES or frame.time is None:
                raise AdmissionError("video frame count or timestamp out of bounds")
            timestamps.append(float(frame.time))
        if len(timestamps) < FRAME_COUNT or any(not math.isfinite(value) for value in timestamps):
            raise AdmissionError("missing timed video frames")
        gaps = [after - before for before, after in zip(timestamps, timestamps[1:]) if after > before]
        if not gaps:
            raise AdmissionError("no positive presentation timestamp gaps")
        period = statistics.median(gaps)
        origin = (float(stream.start_time * stream.time_base)
                  if stream.start_time is not None and stream.time_base else timestamps[0])
        duration = (float(stream.duration * stream.time_base)
                    if stream.duration is not None and stream.time_base else timestamps[-1] - origin + period)

    expected_duration = clip["clip_end_full_video_seconds"] - clip["clip_start_full_video_seconds"]
    window = annotation_window(clip)
    if (
        not all(math.isfinite(value) for value in (origin, duration, period))
        or duration <= 0
        or period <= 0
        or abs(duration - expected_duration) > period + 1e-9
        or abs(timestamps[0] - origin) > period + 1e-9
        or abs(timestamps[-1] - origin + period - duration) > period + 1e-9
        or window[1] > duration + period + 1e-9
    ):
        raise AdmissionError("decoded clip timing disagrees with metadata")

    samples = select_samples(timestamps, origin=origin, window=window, period=period)
    directory = output / "frames" / clip_id
    directory.mkdir()
    by_index = {sample["frame_index"]: (slot, sample) for slot, sample in enumerate(samples)}
    with av.open(str(video), mode="r") as container:
        for index, frame in enumerate(container.decode(video=0)):
            if index not in by_index:
                continue
            slot, sample = by_index.pop(index)
            if frame.time is None or float(frame.time) != sample["source_pts_seconds"]:
                raise AdmissionError("presentation timestamp changed between decodes")
            check_dimensions(frame)
            scale = min(1.0, MAX_EDGE / max(frame.width, frame.height))
            width = max(1, round(frame.width * scale))
            height = max(1, round(frame.height * scale))
            path = directory / f"{slot:02d}.png"
            image = frame.reformat(width=width, height=height, format="rgb24").to_image()
            with path.open("xb") as handle:
                image.save(handle, format="PNG")
            sample.update({
                "path": path.relative_to(output).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "width": width,
                "height": height,
            })
            if not by_index:
                break
    if by_index:
        raise AdmissionError("selected frames missing on second decode")
    return {
        "decoded_duration_seconds": duration,
        "clip_pts_origin_seconds": origin,
        "frame_period_seconds": period,
        "annotation_start_clip_seconds": window[0],
        "annotation_end_clip_seconds": window[1],
        "frames": samples,
    }


def artifact_info(path, output, identity):
    size, sha256 = identity
    return {"path": path.relative_to(output).as_posix(), "bytes": size, "sha256": sha256}


def write_json(path, value):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")


def safe_error(exc):
    if isinstance(exc, HTTPError):
        return f"HTTP error {exc.code}"
    return str(exc) if isinstance(exc, AdmissionError) else type(exc).__name__


def admit(output, count=TARGET_COUNT):
    if not MIN_COUNT <= count <= MAX_COUNT:
        raise AdmissionError(f"target count must be between {MIN_COUNT} and {MAX_COUNT}")
    output.mkdir()
    stage = "source_license"
    received = []
    failures = []
    charged_bytes = 0
    try:
        source = output / "source"
        source.mkdir()
        (output / "frames").mkdir()
        license_file = source / "LICENSE"
        license_identity = fetch_file(LICENSE_PATH, license_file, LICENSE_BYTES, size=LICENSE_BYTES)
        if license_identity != (LICENSE_BYTES, LICENSE_SHA256):
            raise AdmissionError("source license integrity check failed")
        license_text = license_file.read_text(encoding="utf-8-sig")
        if not all(value in license_text.casefold() for value in ("daun_v3", "dmk_v3")):
            raise AdmissionError("source license omits an expected FSboard component")
        if not re.search(r"(?:cc\s*by\s*4\.0|creativecommons\.org/licenses/by/4\.0|attribution\s+4\.0)",
                         license_text, re.IGNORECASE):
            raise AdmissionError("source license lacks expected CC BY 4.0 attribution terms")
        stage = "test_metadata"
        metadata_file = source / f"{SPLIT}.json"
        metadata_identity = fetch_file(METADATA_PATH, metadata_file, METADATA_BYTES, size=METADATA_BYTES)
        if metadata_identity != (METADATA_BYTES, METADATA_SHA256):
            raise AdmissionError("test metadata integrity check failed")
        candidates = parse_candidates(metadata_file.read_bytes())
        chosen = select_candidates(candidates, MAX_COUNT)
        if len(chosen) < MIN_COUNT:
            raise AdmissionError("too few independent test candidates under byte caps")
        for number, clip in enumerate(chosen, start=1):
            if len(received) == count:
                break
            clip_id = f"fsboard-{number:02d}"
            stage = f"{clip_id}_download"
            charged_bytes += clip["original_bytes"]
            try:
                with tempfile.TemporaryDirectory(prefix="fsboard-admission-") as scratch:
                    video = Path(scratch) / "source.mp4"
                    size, sha256 = fetch_file(
                        clip["source_path"], video, CLIP_BYTES,
                        size=clip["original_bytes"], md5=clip["metadata_md5"],
                    )
                    stage = f"{clip_id}_decode"
                    decoded = decode_frames(video, output, clip_id, clip)
                received.append({
                    **clip,
                    **decoded,
                    "id": clip_id,
                    "split": SPLIT,
                    "role": "evaluation_only",
                    "status": "review_required",
                    "original_bytes": size,
                    "original_sha256": sha256,
                })
            except Exception as exc:
                failures.append({"id": clip_id, "stage": stage, "error": safe_error(exc), **clip})
        stage = "review_receipt"
        if len(received) < MIN_COUNT:
            raise AdmissionError("fewer than four verified test clips with 32 unique frames")
        receipt = {
            "schema_version": 1,
            "status": "review_required",
            "role": "evaluation_only",
            "contains_private_evaluation_references": True,
            "dataset": {
                "id": DATASET,
                "publisher": "Google AI",
                "version": VERSION,
                "split": SPLIT,
                "url": DATASET_URL,
                "license": "CC-BY-4.0",
                "license_url": "https://creativecommons.org/licenses/by/4.0/",
                "license_file": artifact_info(license_file, output, license_identity),
                "metadata": artifact_info(metadata_file, output, metadata_identity),
            },
            "requested_count": count,
            "verified_count": len(received),
            "media_download_budget_bytes": TOTAL_BYTES,
            "attempted_original_bytes": charged_bytes,
            "frame_count": FRAME_COUNT,
            "max_frame_edge": MAX_EDGE,
            "clips": received,
            "failed_clips": failures,
        }
        pending = output / "admission.json.pending"
        write_json(pending, receipt)
        pending.rename(output / "admission.json")
        return receipt
    except Exception as exc:
        write_json(output / "admission-error.json", {
            "status": "failed",
            "role": "evaluation_only",
            "stage": stage,
            "error": safe_error(exc),
            "attempted_original_bytes": charged_bytes,
            "partial_clips": received,
            "failed_clips": failures,
        })
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new directory in the private evaluation mount")
    parser.add_argument("--count", type=int, default=TARGET_COUNT, help="target verified clips (4 to 15, default 8)")
    args = parser.parse_args()
    try:
        receipt = admit(args.output, args.count)
    except FileExistsError:
        print("FSboard output already exists", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"FSboard admission failed ({safe_error(exc)}). Check the private diagnostic.", file=sys.stderr)
        return 1
    print(f"FSboard review required for {receipt['verified_count']} evaluation-only clips.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
