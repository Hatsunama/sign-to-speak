# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["av>=14,<18", "Pillow>=11,<13", "huggingface_hub>=1,<2"]
# ///
"""Prepare bounded, independently grouped public ASL clips in a private HF Jobs bucket."""

import bisect
import csv
import hashlib
import io
import json
import math
import re
import sys
import tempfile
import unicodedata
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import Request, urlopen


HUB = "https://huggingface.co"
DATASET = "facebook/2M-Flores-ASL"
DATA_REVISION = "b450c1a427738e78f06362fc4619674f5d74f774"
MODEL = "Qwen/Qwen3.5-0.8B"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/legalcode.txt"
MIB = 1024 * 1024
METADATA_CAP = 2 * MIB
TOTAL_BYTES = 1024 * MIB
CLIP_BYTES = 150 * MIB
FRAME_COUNT = 8
MAX_EDGE = 384
MIN_TRAIN = 4
TARGET_TRAIN = 16
TARGET_HOLDOUT = 4
MAX_DECODED_FRAMES = 12_000
PROMPT = "Translate the American Sign Language signing into one English sentence."


class AdmissionError(ValueError):
    """A validation failure whose message is safe to include in job logs."""


def bounded_copy(source, target, cap, declared=None):
    if declared is not None and (declared <= 0 or declared > cap):
        raise AdmissionError("declared length outside cap")
    limit = cap if declared is None else declared
    digest = hashlib.sha256()
    total = 0
    while True:
        block = source.read(min(MIB, limit - total + 1))
        if not block:
            break
        total += len(block)
        if total > limit:
            raise AdmissionError("response longer than permitted length")
        digest.update(block)
        target.write(block)
    if declared is not None and total != declared:
        raise AdmissionError("truncated response")
    if total == 0:
        raise AdmissionError("empty response")
    return total, digest.hexdigest()


def fetch(url, target, cap, declared=None):
    request = Request(url, headers={"Accept-Encoding": "identity", "User-Agent": "sign-to-speak-admission/1"})
    with urlopen(request, timeout=45) as response:
        if response.status != 200:
            raise AdmissionError("unexpected response status")
        header_size = response.headers.get("Content-Length")
        if header_size is not None:
            if not re.fullmatch(r"[0-9]{1,12}", header_size):
                raise AdmissionError("malformed Content-Length")
            header_size = int(header_size)
            if declared is not None and header_size != declared:
                raise AdmissionError("response size disagrees with Hub tree")
            declared = header_size
        return bounded_copy(response, target, cap, declared)


def fetch_bytes(url):
    result = io.BytesIO()
    fetch(url, result, METADATA_CAP)
    return result.getvalue()


def dataset_url(path):
    return f"{HUB}/datasets/{DATASET}/resolve/{DATA_REVISION}/{quote(path, safe='/')}"


def safe_video_path(split, filename):
    if split not in {"dev", "devtest"} or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.(?:mov|mp4)", filename, re.I):
        raise AdmissionError("invalid individual video path")
    return f"data/{split}/{filename}"


def canonical_url(url):
    value = urlsplit(url.strip())
    if value.scheme not in {"http", "https"} or not value.hostname or value.username or value.password:
        raise AdmissionError("invalid article URL")
    host = value.hostname.lower().removeprefix("www.").rstrip(".")
    path = unicodedata.normalize("NFKC", unquote(value.path)).replace("_", " ")
    path = "/".join(" ".join(part.split()) for part in path.split("/") if part)
    return f"{host}/{path.casefold()}"


def normalized_prompt(sentence):
    value = unicodedata.normalize("NFKC", sentence).casefold()
    return " ".join(re.sub(r"[\W_]+", " ", value).split())


def parse_rows(content, split):
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""))
    required = {"file_name", "URL", "sentence"}
    if not required.issubset(reader.fieldnames or []):
        raise AdmissionError("unexpected metadata columns")
    rows = []
    paths = set()
    for row in reader:
        if any(not row[key] for key in required):
            raise AdmissionError("missing required metadata value")
        path = safe_video_path(split, row["file_name"])
        reference = row["sentence"].strip()
        prompt_key = normalized_prompt(reference)
        if path in paths or not prompt_key:
            raise AdmissionError("duplicate video path or empty normalized sentence")
        paths.add(path)
        rows.append({
            "source_path": path, "reference": reference, "source_url": row["URL"],
            "group": canonical_url(row["URL"]), "prompt_key": prompt_key,
        })
    if not rows:
        raise AdmissionError("empty split metadata")
    return rows


def independent_rows(train_rows, holdout_rows):
    train_groups = {row["group"] for row in train_rows}
    train_prompts = {row["prompt_key"] for row in train_rows}
    return [
        row for row in holdout_rows
        if row["group"] not in train_groups and row["prompt_key"] not in train_prompts
    ]


def eligible_rows(api, split, rows):
    from huggingface_hub.hf_api import RepoFile

    files = {}
    for entry in api.list_repo_tree(
        DATASET, path_in_repo=f"data/{split}", recursive=False,
        revision=DATA_REVISION, repo_type="dataset",
    ):
        if isinstance(entry, RepoFile):
            files[entry.path] = entry
    if not files:
        raise AdmissionError("empty Hub split tree")
    eligible = []
    for row in rows:
        entry = files.get(row["source_path"])
        if entry is None or not isinstance(entry.size, int) or entry.size <= 0:
            raise AdmissionError("metadata video absent from Hub tree or invalid size")
        if entry.size <= CLIP_BYTES:
            eligible.append({
                **row, "original_bytes": entry.size,
                "upstream_sha256": getattr(entry.lfs, "sha256", None),
            })
    return sorted(eligible, key=lambda row: (row["original_bytes"], row["source_path"]))


def pick_rows(candidates, count, budget):
    chosen = []
    groups = set()
    prompts = set()
    for row in candidates:
        size = row["original_bytes"]
        if size > budget or row["group"] in groups or row["prompt_key"] in prompts:
            continue
        chosen.append(row)
        groups.add(row["group"])
        prompts.add(row["prompt_key"])
        budget -= size
        if len(chosen) == count:
            break
    return chosen, budget


def select_frame_indices(actual_seconds, requests):
    if not actual_seconds or any(not math.isfinite(value) for value in actual_seconds):
        raise AdmissionError("missing or invalid presentation timestamps")
    if any(a > b for a, b in zip(actual_seconds, actual_seconds[1:])):
        raise AdmissionError("presentation timestamps must be ordered")
    selected = []
    seen = set()
    for requested in requests:
        right = bisect.bisect_left(actual_seconds, requested)
        left = max(0, right - 1)
        if right == len(actual_seconds) or requested - actual_seconds[left] <= actual_seconds[right] - requested:
            index = left
        else:
            index = right
        timestamp = actual_seconds[index]
        selected.append((index, timestamp in seen))
        seen.add(timestamp)
    return selected


def decode_frames(video, root, clip_id):
    import av

    timestamps = []
    with av.open(str(video), mode="r") as container:
        if len(container.streams.video) != 1:
            raise AdmissionError("expected one video stream")
        stream = container.streams.video[0]
        if not (1 <= stream.width <= 4096 and 1 <= stream.height <= 4096):
            raise AdmissionError("video dimensions out of bounds")
        for frame in container.decode(stream):
            if len(timestamps) == MAX_DECODED_FRAMES or frame.time is None:
                raise AdmissionError("video frame count or timestamp out of bounds")
            timestamps.append(float(frame.time))
        if len(timestamps) < 2:
            raise AdmissionError("video has fewer than two timed frames")
        origin = timestamps[0]
        actual_seconds = [timestamp - origin for timestamp in timestamps]
        reported_duration = float(stream.duration * stream.time_base) if stream.duration and stream.time_base else 0.0
        duration = max(actual_seconds[-1], reported_duration)
    if not math.isfinite(duration) or not 0 < duration <= 180:
        raise AdmissionError("video duration out of bounds")
    requests = [duration * number / (FRAME_COUNT - 1) for number in range(FRAME_COUNT)]
    indices = select_frame_indices(actual_seconds, requests)
    directory = root / "frames" / clip_id
    directory.mkdir(parents=True, exist_ok=True)
    images = {}
    with av.open(str(video), mode="r") as container:
        needed = {index for index, _ in indices}
        for index, frame in enumerate(container.decode(video=0)):
            if index not in needed:
                continue
            if frame.time is None or float(frame.time) != timestamps[index]:
                raise AdmissionError("video timestamps changed between decodes")
            scale = min(1.0, MAX_EDGE / max(frame.width, frame.height))
            width = max(1, round(frame.width * scale))
            height = max(1, round(frame.height * scale))
            images[index] = frame.reformat(width=width, height=height, format="rgb24").to_image()
            if len(images) == len(needed):
                break
    if len(images) != len(needed):
        raise AdmissionError("video frames missing on second decode")
    frames = []
    for slot, (index, duplicate) in enumerate(indices):
        filename = directory / f"{slot:02d}.png"
        images[index].save(filename, format="PNG")
        frames.append({
            "path": filename.relative_to(root).as_posix(),
            "requested_seconds": requests[slot], "actual_seconds": actual_seconds[index],
            "source_pts_seconds": timestamps[index], "duplicate": duplicate,
        })
    return duration, frames


def get_model(api):
    info = api.model_info(MODEL)
    if not info.sha or not re.fullmatch(r"[0-9a-f]{40}", info.sha):
        raise AdmissionError("model revision is not an immutable SHA")
    config = json.loads(fetch_bytes(f"{HUB}/{MODEL}/resolve/{info.sha}/config.json"))
    if (
        info.pipeline_tag != "image-text-to-text"
        or config.get("model_type") != "qwen3_5"
        or "Qwen3_5ForConditionalGeneration" not in config.get("architectures", [])
        or not config.get("vision_config")
    ):
        raise AdmissionError("unexpected Qwen multimodal config")
    return {
        "id": MODEL, "revision": info.sha, "pipeline_tag": info.pipeline_tag,
        "model_type": config["model_type"], "architectures": config["architectures"],
    }


def write_receipt(root, receipt):
    pending = root / "admission.json.pending"
    pending.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pending.replace(root / "admission.json")


def main():
    from huggingface_hub import HfApi

    root = Path("/data/pilot")
    stage = "checking mounted output"
    try:
        if sys.platform != "linux" or not root.parent.is_dir():
            raise AdmissionError("expected Linux cloud job with /data mounted")
        root.mkdir(exist_ok=True)
        if (root / "admission.json").exists():
            raise AdmissionError("admission receipt already exists")
        api = HfApi(endpoint=HUB, token=False)

        stage = "reading pinned metadata"
        metadata = {
            split: fetch_bytes(dataset_url(f"data/{split}/metadata.csv"))
            for split in ("dev", "devtest")
        }
        train_source = parse_rows(metadata["dev"], "dev")
        holdout_source = parse_rows(metadata["devtest"], "devtest")
        independent = independent_rows(train_source, holdout_source)
        stage = "matching videos to Hub tree sizes"
        train_eligible = eligible_rows(api, "dev", train_source)
        holdout_eligible = eligible_rows(api, "devtest", independent)
        holdout, remaining = pick_rows(holdout_eligible, TARGET_HOLDOUT, TOTAL_BYTES)
        train, _ = pick_rows(train_eligible, TARGET_TRAIN, remaining)
        ready_to_decode = len(holdout) == TARGET_HOLDOUT and len(train) >= MIN_TRAIN
        print(
            f"Metadata rows {len(train_source)} train / {len(holdout_source)} held-out. "
            f"Independent held-out rows {len(independent)}. "
            f"Selected {len(train)} train / {len(holdout)} held-out.",
            flush=True,
        )

        stage = "checking model metadata and license"
        model = get_model(api)
        readme = fetch_bytes(dataset_url("README.md"))
        legalcode = fetch_bytes(LICENSE_URL)
        if b"cc-by-sa-4.0" not in readme.lower() or b"Attribution-ShareAlike 4.0 International" not in legalcode:
            raise AdmissionError("unexpected license metadata")
        (root / "UPSTREAM_README.md").write_bytes(readme)
        (root / "CC-BY-SA-4.0.txt").write_bytes(legalcode)

        clips = []
        seen_hashes = set()
        original_bytes = 0
        if ready_to_decode:
            scratch = Path("/tmp/sign-to-speak")
            scratch.mkdir(mode=0o700, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="qwen-admission-", dir=scratch) as tmp:
                for split, rows in (("train", train), ("holdout", holdout)):
                    for row in rows:
                        stage = f"checking selected {split} video bytes"
                        video = Path(tmp) / Path(row["source_path"]).name
                        cap = min(CLIP_BYTES, TOTAL_BYTES - original_bytes)
                        with video.open("wb") as stream:
                            size, sha = fetch(dataset_url(row["source_path"]), stream, cap, row["original_bytes"])
                        if row["upstream_sha256"] and row["upstream_sha256"] != sha:
                            raise AdmissionError("video digest disagrees with Hub tree")
                        if sha in seen_hashes:
                            raise AdmissionError("duplicate original video across selected clips")
                        seen_hashes.add(sha)
                        original_bytes += size
                        stage = f"decoding selected {split} video"
                        duration, frames = decode_frames(video, root, sha)
                        video.unlink()
                        clips.append({
                            "split": split, "id": sha, "sha256": sha, "original_bytes": size,
                            "source_path": row["source_path"], "source_url": row["source_url"],
                            "source_group": row["group"], "reference": row["reference"],
                            "duration_seconds": duration, "hand_label": None, "frames": frames,
                        })
                        print(f"Decoded {len(clips)} videos, {original_bytes} original bytes.", flush=True)

        stage = "publishing admission receipt"
        counts = {"train": len(train) if ready_to_decode else 0, "holdout": len(holdout) if ready_to_decode else 0}
        receipt = {
            "schema_version": 1,
            "status": "ready" if ready_to_decode else "insufficient_independent_clips",
            "ready": ready_to_decode,
            "dataset": {
                "id": DATASET, "revision": DATA_REVISION, "license": "CC-BY-SA-4.0",
                "license_url": LICENSE_URL, "license_path": "CC-BY-SA-4.0.txt",
                "attribution_path": "UPSTREAM_README.md", "attribution": "Meta 2M-Flores-ASL and Flores200",
                "citation": "https://arxiv.org/abs/2412.08274",
                "modifications": "temporal sampling and RGB frame resizing",
                "metadata": {
                    f"data/{split}/metadata.csv": hashlib.sha256(content).hexdigest()
                    for split, content in metadata.items()
                },
            },
            "model": model,
            "prompt": PROMPT,
            "counts": counts,
            "frames_per_clip": FRAME_COUNT,
            "max_frame_edge": MAX_EDGE,
            "max_original_bytes": TOTAL_BYTES,
            "original_bytes": original_bytes,
            "clips": clips,
        }
        write_receipt(root, receipt)
        print(
            f"Admission {receipt['status']}. {counts['train']} train / {counts['holdout']} held-out. "
            f"{len(clips) * FRAME_COUNT} frames, {sum(frame['duplicate'] for clip in clips for frame in clip['frames'])} "
            f"duplicated timestamps, {original_bytes} original bytes.",
            flush=True,
        )
    except AdmissionError as exc:
        print(f"Admission stopped while {stage} ({exc}).", file=sys.stderr)
        return 1
    except HTTPError as exc:
        print(f"Admission stopped while {stage} (HTTP {exc.code}).", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Admission stopped while {stage} ({type(exc).__name__}).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
