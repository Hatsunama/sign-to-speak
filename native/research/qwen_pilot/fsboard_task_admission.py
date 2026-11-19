# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["av>=17,<18", "Pillow>=11,<13"]
# ///
"""Verify the frozen FSboard task proposal and admit selected media on a private mount."""
import argparse
import hashlib
import io
import json
import math
from pathlib import Path
import re
import signal
import sys
import tempfile
from urllib.request import Request, build_opener

from PIL import Image

import fsboard_task_protocol as protocol
import fsboard_training_intake as intake
from fsboard_admission import (
    AdmissionError, CLIP_BYTES, ENDPOINT, HTTPSRedirects, MAX_EDGE, MIB,
    SAFE_BASENAME, copy_verified, decode_frames, safe_error, write_json,
)

PROPOSAL_BYTES = 2 * MIB
# A 384 x 384 RGB PNG has under 512 KiB even before compression.
FRAME_BYTES = MIB // 2
SHA256 = re.compile(r"[a-f0-9]{64}")
MODES = ("sentinel", "full")

# Normalized proposal fields map back to the publisher fields validated in preflight.
CLIP_FIELDS = {
    "reference": "phrase",
    "signer_id": "signerId",
    "full_video_filename": "fullVideoFilename",
    "clip_filename": "clipFilename",
    "original_bytes": "clipFileSize",
    "metadata_md5": "clipFileMd5",
    "clip_start_full_video_seconds": "clipStartTimeS",
    "clip_end_full_video_seconds": "clipEndTimeS",
    "annotation_start_full_video_seconds": "annotationStartTimeS",
    "annotation_end_full_video_seconds": "annotationEndTimeS",
}
UNIQUE_FIELDS = set(intake.SEPARATION_FIELDS) - {"signer_id", "full_video_filename"}


def canonical_sha256(document):
    payload = json.dumps(document, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_clip(clip, split):
    if not isinstance(clip, dict) or clip.keys() != CLIP_FIELDS.keys() | {"source_path", "split"}:
        raise AdmissionError("proposal clip schema disagrees with metadata preflight")
    # The digest binds the normalized preflight records, which only emit fs rows
    # marked non-sensitive. Those two implicit predicates are not fields in the proposal.
    row = {publisher: clip[normalized] for normalized, publisher in CLIP_FIELDS.items()}
    row.update(signingType="fs", sensitiveContent=False)
    normalized = protocol.task_candidate(row, split)
    if normalized is None or clip != normalized:
        raise AdmissionError("invalid clip metadata or publisher split")


def validate_role(clips, report, split, quota, excluded):
    if not isinstance(clips, list) or len(clips) != quota.clips:
        raise AdmissionError("proposal does not meet role clip quota")
    role_keys = set()
    for clip in clips:
        validate_clip(clip, split)
        keys = intake.exclusion_keys([clip])
        if keys & excluded:
            raise AdmissionError("clip overlaps a prior evaluation or experiment role")
        if any(key in role_keys for key in keys if key[0] in UNIQUE_FIELDS):
            raise AdmissionError("duplicate target, file or payload in role")
        role_keys.update(keys)

    size = sum(clip["original_bytes"] for clip in clips)
    signers = sum(field == "signer_id" for field, _ in role_keys)
    recordings = sum(field == "full_video_filename" for field, _ in role_keys)
    coverage = intake.coverage(clips)
    if size > quota.media_bytes or signers < quota.signers or min(coverage.values()) < quota.per_category:
        raise AdmissionError("proposal does not meet role byte, signer or category quota")
    expected = {
        "publisher_split": f"daun_v3-{split}",
        "requested_clips": quota.clips,
        "requested_min_signers": quota.signers,
        "requested_min_per_category": quota.per_category,
        "selected_clips": len(clips),
        "selected_signers": signers,
        "selected_recordings": recordings,
        "selected_categories": coverage,
        "proposed_media_budget_bytes": quota.media_bytes,
        "proposed_media_bytes": size,
        "shortfalls": dict.fromkeys(("clips", "signers", *intake.CATEGORIES), 0),
    }
    if not isinstance(report, dict) or any(report.get(key) != value for key, value in expected.items()):
        raise AdmissionError("role summary disagrees with verified proposal quotas")
    excluded.update(role_keys)


def read_proposal(path, proposal_sha256, protocol_sha256, previous_receipt):
    if not all(isinstance(value, str) and SHA256.fullmatch(value)
               for value in (proposal_sha256, protocol_sha256)):
        raise AdmissionError("expected lowercase SHA-256 digests")
    with path.open("rb") as handle:
        payload = handle.read(PROPOSAL_BYTES + 1)
    if len(payload) > PROPOSAL_BYTES or hashlib.sha256(payload).hexdigest() != proposal_sha256:
        raise AdmissionError("private proposal integrity check failed")
    document = json.loads(payload.decode("utf-8"))
    if (
        not isinstance(document, dict)
        or type(document.get("schema_version")) is not int
        or document["schema_version"] != 1
        or document.get("status") != "metadata_proposal"
        or document.get("contains_private_signer_ids_recordings_and_targets") is not True
        or document.get("publisher_signer_and_recording_separation_verified") is not True
        or document.get("previous_evaluation_excluded_clips") != 8
    ):
        raise AdmissionError("proposal is not a complete private metadata proposal")
    if (canonical_sha256(document.get("protocol")) != protocol_sha256
            or canonical_sha256(protocol.frozen_protocol()) != protocol_sha256):
        raise AdmissionError("proposal protocol does not match frozen experiment")
    dataset = document["dataset"]
    pins = {name: {"path": name, "bytes": size, "sha256": sha256}
            for name, (size, sha256) in intake.METADATA_PINS.items()}
    if ((dataset["id"], dataset["version"], dataset["license"]) != (intake.DATASET, intake.VERSION, "CC-BY-4.0")
            or dataset["source_pins"] != pins
            or document["source_stats"]["test"]["excluded_from_all_experiment_roles"] is not True):
        raise AdmissionError("proposal publisher provenance disagrees with FSboard v13 pins")
    scope = document["scope"]
    if scope["media_downloaded_bytes"] != 0 or any(
        scope[key] is not False for key in (
            "source_video_verified", "frame_sampling_verified", "training_started", "model_scoring_started",
        )
    ):
        raise AdmissionError("proposal does not have metadata-only scope")

    clips, reports = document["clips"], document["splits"]
    if (not isinstance(clips, dict) or not isinstance(reports, dict)
            or clips.keys() != protocol.ROLE_SOURCES.keys() or reports.keys() != clips.keys()):
        raise AdmissionError("proposal role keys do not match frozen experiment")
    excluded = intake.exclusion_keys(intake.read_previous(previous_receipt))
    for role, split in protocol.ROLE_SOURCES.items():
        validate_role(clips[role], reports[role], split, protocol.QUOTAS[role], excluded)
    return document


def fetch_clip(clip, role, target):
    """Transfer only the individual MP4 named in a validated role record."""
    prefix, _, basename = clip["source_path"].rpartition("/")
    if (prefix != protocol.video_directory(protocol.ROLE_SOURCES[role])
            or not SAFE_BASENAME.fullmatch(basename) or basename != clip["clip_filename"]):
        raise AdmissionError("video path is outside role download scope")
    size = clip["original_bytes"]
    owner, dataset = intake.DATASET.split("/")
    payload = json.dumps({
        "ownerSlug": owner, "datasetSlug": dataset, "datasetVersionNumber": intake.VERSION,
        "fileName": clip["source_path"],
    }).encode("utf-8")
    request = Request(ENDPOINT, method="POST", data=payload, headers={
        "Accept-Encoding": "identity",
        "Content-Type": "application/json",
        "User-Agent": "sign-to-speak-fsboard-task-admission/1",
    })
    with build_opener(HTTPSRedirects()).open(request, timeout=45) as response:
        if response.status != 200 or response.headers.get("Content-Encoding", "identity") != "identity":
            raise AdmissionError("unexpected dataset response status or encoding")
        declared = response.headers.get("Content-Length")
        if declared is not None and declared != str(size):
            raise AdmissionError("response size disagrees with metadata")
        with target.open("xb") as destination:
            return copy_verified(response, destination, CLIP_BYTES, size, clip["metadata_md5"])


def verify_frames(staging, clip_id, frames):
    if not isinstance(frames, list) or not protocol.MIN_FRAMES <= len(frames) <= protocol.MAX_FRAMES:
        raise AdmissionError(f"expected {protocol.MIN_FRAMES} to {protocol.MAX_FRAMES} decoded frames")
    directory = staging / "frames" / clip_id
    if {path.name for path in directory.iterdir()} != {f"{slot:02d}.png" for slot in range(len(frames))}:
        raise AdmissionError("decoded frame files do not match the manifest")
    previous_index, previous_pts = -1, -math.inf
    for slot, sample in enumerate(frames):
        path = directory / f"{slot:02d}.png"
        index, pts = sample["frame_index"], sample["source_pts_seconds"]
        if (
            type(index) is not int or index <= previous_index
            or type(pts) not in (float, int) or not math.isfinite(pts) or pts <= previous_pts
            or sample["duplicate"] is not False
            or sample["path"] != path.relative_to(staging).as_posix()
            or any(type(sample[key]) is not int or not 1 <= sample[key] <= MAX_EDGE
                   for key in ("width", "height"))
            or path.is_symlink()
        ):
            raise AdmissionError("decoded frame sample is out of order or out of scope")
        with path.open("rb") as handle:
            payload = handle.read(FRAME_BYTES + 1)
        if len(payload) > FRAME_BYTES or hashlib.sha256(payload).hexdigest() != sample["sha256"]:
            raise AdmissionError("decoded PNG disagrees with frame integrity contract")
        try:
            with Image.open(io.BytesIO(payload), formats=("PNG",)) as image:
                if (image.mode != "RGB" or image.size != (sample["width"], sample["height"])
                        or image.n_frames != 1):
                    raise AdmissionError("decoded PNG disagrees with RGB frame geometry")
                image.verify()
            with Image.open(io.BytesIO(payload), formats=("PNG",)) as image:
                image.load()
        except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
            raise AdmissionError("decoded PNG failed integrity or decoding checks") from exc
        previous_index, previous_pts = index, pts


def failure_path(output):
    return output.with_name(f"{output.name}.failed.json")


def check_output(output):
    if any(path.exists() or path.is_symlink() for path in (output, failure_path(output))):
        raise AdmissionError("admission output or failure receipt already exists")


def write_failure(output, progress, exc):
    path = failure_path(output)
    with tempfile.TemporaryDirectory(prefix=".fsboard-failure-", dir=output.parent) as temporary:
        pending = Path(temporary) / "failure.json"
        write_json(pending, {
            **progress,
            "status": "cancelled" if isinstance(exc, KeyboardInterrupt) else "failed",
            "error": safe_error(exc),
        })
        pending.chmod(0o600)
        # Link the complete receipt atomically without replacing earlier evidence.
        path.hardlink_to(pending)


def admit(*, proposal, proposal_sha256, protocol_sha256, previous_receipt, output, mode):
    if mode not in MODES:
        raise AdmissionError("expected an explicit sentinel or full admission mode")
    check_output(output)
    progress = {
        "schema_version": 1,
        "experiment_id": protocol.EXPERIMENT_ID,
        "mode": mode,
        "stage": "proposal_validation",
        "clips_verified": dict.fromkeys(protocol.ROLE_SOURCES, 0),
        "attempted_original_bytes": 0,
    }
    try:
        document = read_proposal(proposal, proposal_sha256, protocol_sha256, previous_receipt)
        plan = {role: clips[:1] if mode == "sentinel" else clips
                for role, clips in document["clips"].items()}
        declared_bytes = sum(clip["original_bytes"] for clips in plan.values() for clip in clips)
        admitted = {role: [] for role in protocol.ROLE_SOURCES}
        with tempfile.TemporaryDirectory(prefix=".fsboard-media-", dir=output.parent) as temporary:
            staging = Path(temporary)
            (staging / "source").mkdir()
            (staging / "frames").mkdir()
            for role in protocol.ROLE_SOURCES:
                for position, clip in enumerate(plan[role], start=1):
                    clip_id = f"{role}-{position:03d}"
                    original = staging / "source" / f"{clip_id}.mp4"
                    progress.update(stage="download", role=role, role_position=position)
                    progress["attempted_original_bytes"] += clip["original_bytes"]
                    size, sha256 = fetch_clip(clip, role, original)
                    progress["stage"] = "decode"
                    decoded = decode_frames(
                        original, staging, clip_id, clip,
                        min_frames=protocol.MIN_FRAMES, max_frames=protocol.MAX_FRAMES,
                        allow_terminal_pts_endpoint=True,
                    )
                    progress["stage"] = "frame_verification"
                    frames = decoded["frames"]
                    verify_frames(staging, clip_id, frames)
                    admitted[role].append({
                        **clip, **decoded, "id": clip_id, "role": role,
                        "original_path": original.relative_to(staging).as_posix(),
                        "original_bytes": size, "original_sha256": sha256,
                        "sampled_frame_count": len(frames),
                    })
                    progress["clips_verified"][role] += 1

            progress.update(stage="publication", role=None, role_position=None)
            summary = {
                "schema_version": 1,
                "experiment_id": protocol.EXPERIMENT_ID,
                "mode": mode,
                "status": "full_media_verified" if mode == "full" else "sentinel_verified",
                "proposal_sha256": proposal_sha256,
                "protocol_sha256": protocol_sha256,
                "previous_receipt_sha256": intake.PREVIOUS_RECEIPT_SHA256,
                "clips_verified": progress["clips_verified"],
                "original_bytes": declared_bytes,
                "min_frames_per_clip": protocol.MIN_FRAMES,
                "max_frames_per_clip": protocol.MAX_FRAMES,
                "sampled_frame_counts": {
                    role: [clip["sampled_frame_count"] for clip in clips]
                    for role, clips in admitted.items()
                },
                "max_frame_edge": MAX_EDGE,
                "training_media_complete": mode == "full",
                "training_eligible": False,
                "processor_token_contract_verified": False,
                "training_started": False,
                "model_scoring_started": False,
            }
            receipt = staging / "admission.json"
            write_json(receipt, {
                **summary, "contains_private_signer_ids_recordings_and_targets": True,
                "dataset": document["dataset"], "clips": admitted,
            })
            summary["admission_sha256"] = hashlib.sha256(receipt.read_bytes()).hexdigest()
            write_json(staging / "summary.json", summary)
            check_output(output)
            staging.rename(output)
        return summary
    except (Exception, KeyboardInterrupt) as exc:
        try:
            write_failure(output, progress, exc)
        except (OSError, KeyboardInterrupt):
            # Keep the original error if publishing its diagnostic fails.
            pass
        raise


def handle_sigterm(signum, frame):
    raise KeyboardInterrupt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposal", type=Path, required=True, help="frozen private-proposal.json")
    parser.add_argument("--proposal-sha256", required=True, help="expected SHA-256 of the proposal bytes")
    parser.add_argument("--protocol-sha256", required=True, help="expected SHA-256 of the canonical protocol JSON")
    parser.add_argument("--previous-receipt", type=Path, required=True, help="immutable fsboard-eval-v3-approved.json")
    parser.add_argument(
        "--output", type=Path, required=True,
        help="fresh directory on private storage with hard-link support for failure receipts",
    )
    parser.add_argument("--mode", choices=MODES, required=True)
    args = parser.parse_args(argv)
    previous_handler = signal.signal(signal.SIGTERM, handle_sigterm)
    try:
        summary = admit(**vars(args))
    except KeyboardInterrupt:
        print("FSboard media admission cancelled.", file=sys.stderr)
        return 130
    except Exception:
        print("FSboard media admission failed. Check the diagnostic on the configured mount.", file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())