"""Propose a private FSboard v13 fingerspelling intake using publisher metadata."""
import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
from urllib.request import Request, build_opener

from fsboard_admission import (
    AdmissionError, DATASET, DATASET_URL, ENDPOINT, FRAME_COUNT, HTTPSRedirects,
    LICENSE_BYTES, LICENSE_SHA256, MAX_EDGE, METADATA_BYTES, METADATA_SHA256, VERSION,
    candidate_from_row, copy_verified, safe_error, write_json,
)

ROLES = ("test", "validation", "train")
METADATA_DIR = "daun_v3/metadata"
METADATA_PINS = {
    "LICENSE": (LICENSE_BYTES, LICENSE_SHA256),
    f"{METADATA_DIR}/daun_v3-test.json": (METADATA_BYTES, METADATA_SHA256),
    f"{METADATA_DIR}/daun_v3-validation.json": (
        8_850_161, "d306aac4c80e7052492ebffdd01f2e8242d09755ff7243824e866360fb609648",
    ),
    f"{METADATA_DIR}/daun_v3-train.json": (
        52_049_545, "f85cbf264a4bab08924f5c19c565d3e9b32d4161fabe3004adeb40dd1ba56cdc",
    ),
}
PREVIOUS_RECEIPT_SHA256 = "5be4dd8a5d5225c8131eab855f521a83382a4da3f18804170e917ff381ebd8ac"
MODEL_REVISION = "2fc06364715b967f1860aea9cf38778875588b17"
MIB = 1024 * 1024
JSON_CHUNK = 64 * 1024
SEPARATION_FIELDS = ("signer_id", "full_video_filename", "reference", "source_path",
                     "clip_filename", "metadata_md5")
CATEGORIES = ("contains_j", "contains_z", "adjacent_repeat", "ordinary")


@dataclass(frozen=True)
class Quota:
    clips: int
    signers: int
    media_bytes: int
    per_category: int = 0


QUOTAS = {
    "test": Quota(64, 8, 320 * MIB, 8),
    "validation": Quota(32, 6, 160 * MIB),
    "train": Quota(192, 20, 768 * MIB),
}


def fetch_metadata(name, target):
    """Download exactly one pinned license or JSON file, with no credential."""
    if name not in METADATA_PINS:
        raise AdmissionError("file is outside the metadata preflight allowlist")
    size, sha256 = METADATA_PINS[name]
    owner, dataset = DATASET.split("/")
    payload = json.dumps({
        "ownerSlug": owner, "datasetSlug": dataset, "datasetVersionNumber": VERSION, "fileName": name,
    }).encode("utf-8")
    request = Request(ENDPOINT, method="POST", data=payload, headers={
        "Content-Type": "application/json", "Accept-Encoding": "identity",
        "User-Agent": "sign-to-speak-fsboard-training-intake/1",
    })
    with build_opener(HTTPSRedirects()).open(request, timeout=45) as response:
        if response.status != 200:
            raise AdmissionError("unexpected dataset response status")
        declared = response.headers.get("Content-Length")
        if declared is not None and declared != str(size):
            raise AdmissionError("metadata response length disagrees with release pin")
        with target.open("xb") as destination:
            identity = copy_verified(response, destination, size, size)
    if identity != (size, sha256):
        raise AdmissionError("metadata integrity check failed")
    return {"path": name, "bytes": size, "sha256": sha256}


def metadata_rows(handle):
    """Stream a bounded JSON array with one decoded row at a time."""
    decoder = json.JSONDecoder()
    buffer = ""
    offset = 0

    def next_character():
        nonlocal buffer, offset
        while True:
            while offset < len(buffer) and buffer[offset] in " \t\r\n":
                offset += 1
            if offset < len(buffer):
                return buffer[offset]
            buffer = handle.read(JSON_CHUNK)
            offset = 0
            if not buffer:
                return None

    if next_character() != "[":
        raise AdmissionError("expected a metadata array")
    offset += 1
    if next_character() == "]":
        raise AdmissionError("metadata array is empty")
    while True:
        if next_character() != "{":
            raise AdmissionError("expected a metadata row object")
        try:
            try:
                row, offset = decoder.raw_decode(buffer, offset)
            except json.JSONDecodeError:
                buffer = buffer[offset:] + handle.read(JSON_CHUNK)
                row, offset = decoder.raw_decode(buffer)
            if not isinstance(row, dict):
                raise AdmissionError("expected a metadata row object")
        except (json.JSONDecodeError, RecursionError) as exc:
            raise AdmissionError("invalid or oversized metadata row") from exc
        yield row
        delimiter = next_character()
        offset += 1
        if delimiter == "]":
            if next_character() is not None:
                raise AdmissionError("unexpected trailing metadata")
            return
        if delimiter != ",":
            raise AdmissionError("missing metadata array delimiter")


def candidate_for_role(row, role):
    if role not in ROLES:
        raise AdmissionError("unsupported publisher split")
    candidate = candidate_from_row(row)
    if candidate is not None:
        candidate["source_path"] = f"daun_v3/video_clips/daun_v3-{role}/{candidate['clip_filename']}"
        candidate["split"] = f"daun_v3-{role}"
    return candidate


def categories(target):
    labels = set()
    if "j" in target:
        labels.add("contains_j")
    if "z" in target:
        labels.add("contains_z")
    if re.search(r"(.)\1", target):
        labels.add("adjacent_repeat")
    return labels or {"ordinary"}


def coverage(clips):
    counts = Counter(label for clip in clips for label in categories(clip["reference"]))
    return {category: counts[category] for category in CATEGORIES}


def read_split(path, role):
    official = {field: set() for field in ("signerId", "fullVideoFilename")}
    by_path = {}
    rows = 0
    with path.open(encoding="utf-8-sig") as handle:
        for row in metadata_rows(handle):
            rows += 1
            for field, values in official.items():
                value = row.get(field)
                if not isinstance(value, str) or not value.strip():
                    raise AdmissionError("publisher row lacks signer or recording identity")
                values.add(value.strip().casefold())
            clip = candidate_for_role(row, role)
            if clip is not None:
                key = clip["source_path"].casefold()
                previous = by_path.setdefault(key, clip)
                if previous != clip:
                    raise AdmissionError("conflicting metadata for a clip path")
    clips = list(by_path.values())
    return clips, {
        "rows": rows,
        "official_signers": len(official["signerId"]),
        "official_recordings": len(official["fullVideoFilename"]),
        "candidates": len(clips),
        "candidate_signers": len({clip["signer_id"].strip().casefold() for clip in clips}),
        "candidate_categories": coverage(clips),
    }, official


def read_previous(path):
    with path.open("rb") as handle:
        payload = handle.read(MIB + 1)
    if hashlib.sha256(payload).hexdigest() != PREVIOUS_RECEIPT_SHA256:
        raise AdmissionError("previous evaluation receipt integrity check failed")
    document = json.loads(payload)
    if (document["dataset"]["id"], document["dataset"]["version"], document["dataset"]["split"],
            len(document["clips"])) != (DATASET, VERSION, "daun_v3-test", 8):
        raise AdmissionError("previous evaluation receipt has the wrong provenance")
    return document["clips"]


def exclusion_keys(clips):
    return {
        (field, clip[field].strip().casefold()) for clip in clips for field in SEPARATION_FIELDS
    }


def select_role(candidates, quota, excluded):
    available = [clip for clip in candidates if not (exclusion_keys([clip]) & excluded)]
    eligible_count = len(available)
    chosen = []
    signers = Counter()
    labels = Counter()
    remaining = quota.media_bytes
    chosen_content = set()
    # The same clip ID/path, target and payload each occur at most once per split.
    unique_fields = {"reference", "source_path", "clip_filename", "metadata_md5"}
    entries = [
        (clip, clip["signer_id"].strip().casefold(), categories(clip["reference"]),
         {item for item in exclusion_keys([clip]) if item[0] in unique_fields})
        for clip in available
    ]
    available_labels = coverage(available)
    while len(chosen) < quota.clips:
        best = None
        best_order = None
        for entry in entries:
            clip, signer, tags, content = entry
            if clip["original_bytes"] > remaining or content & chosen_content:
                continue
            unmet = [tag for tag in tags if labels[tag] < quota.per_category]
            order = (
                min((available_labels[tag] for tag in unmet), default=eligible_count + 1),
                -len(unmet), signers[signer], clip["original_bytes"], clip["source_path"],
            )
            if best_order is None or order < best_order:
                best, best_order = entry, order
        if best is None:
            break
        clip, signer, tags, content = best
        chosen.append(clip)
        signers[signer] += 1
        labels.update(tags)
        chosen_content.update(content)
        remaining -= clip["original_bytes"]

    return chosen, {
        "eligible_after_cross_split_and_prior_exclusions": eligible_count,
        "requested_clips": quota.clips,
        "requested_min_signers": quota.signers,
        "requested_min_per_category": quota.per_category,
        "selected_clips": len(chosen),
        "selected_signers": len(signers),
        "selected_recordings": len({clip["full_video_filename"].casefold() for clip in chosen}),
        "selected_categories": {tag: labels[tag] for tag in CATEGORIES},
        "uncovered_ascii_letters": sorted(set("abcdefghijklmnopqrstuvwxyz") -
                                          set("".join(clip["reference"] for clip in chosen))),
        "proposed_media_budget_bytes": quota.media_bytes,
        "proposed_media_bytes": quota.media_bytes - remaining,
        "shortfalls": {
            "clips": quota.clips - len(chosen),
            "signers": max(0, quota.signers - len(signers)),
            **{tag: max(0, quota.per_category - labels[tag]) for tag in CATEGORIES},
        },
    }


def propose(split_candidates, previous, quotas=QUOTAS):
    excluded = exclusion_keys(previous)
    proposed, reports = {}, {}
    for role in ROLES:
        proposed[role], reports[role] = select_role(split_candidates[role], quotas[role], excluded)
        excluded.update(exclusion_keys(proposed[role]))
    return proposed, reports


def preflight(output, previous_path):
    if output.exists() or output.is_symlink():
        raise AdmissionError("preflight output already exists")
    previous = read_previous(previous_path)
    identities = {}
    split_candidates, source_stats, official = {}, {}, {}
    with tempfile.TemporaryDirectory(prefix=".fsboard-meta-", dir=output.parent) as staging:
        for name in METADATA_PINS:
            path = Path(staging) / name.rsplit("/", 1)[-1]
            identities[name] = fetch_metadata(name, path)
            if name == "LICENSE":
                continue
            role = path.stem.removeprefix("daun_v3-")
            split_candidates[role], source_stats[role], official[role] = read_split(path, role)
            path.unlink()
    proposed, splits = propose(split_candidates, previous)
    overlap = {}
    for index, left in enumerate(ROLES):
        for right in ROLES[index + 1:]:
            overlap[f"{left}/{right}"] = {
                "signers": len(official[left]["signerId"] & official[right]["signerId"]),
                "recordings": len(official[left]["fullVideoFilename"] & official[right]["fullVideoFilename"]),
            }
    summary = {
        "schema_version": 1,
        "status": "metadata_proposal_with_shortfalls" if any(
            any(split["shortfalls"].values()) for split in splits.values()
        ) else "metadata_proposal",
        "dataset": {
            "id": DATASET, "publisher": "Google AI", "version": VERSION, "url": DATASET_URL,
            "license": "CC-BY-4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "citation": "https://arxiv.org/abs/2407.15806",
            "source_pins": identities,
        },
        "candidate_model": {"id": "Qwen/Qwen3.5-0.8B", "revision": MODEL_REVISION},
        "planned_input": {"ordered_rgb_stills": FRAME_COUNT, "max_frame_edge": MAX_EDGE},
        "previous_evaluation_receipt_sha256": PREVIOUS_RECEIPT_SHA256,
        "previous_evaluation_excluded_clips": len(previous),
        "selection_priority": list(ROLES),
        "cross_split_and_prior_separation": list(SEPARATION_FIELDS),
        "official_split_overlap": overlap,
        "source_stats": source_stats,
        "splits": splits,
        "scope": {
            "media_downloaded_bytes": 0,
            "frame_sampling_verified": False,
            "physical_signer_identity_verified": False,
            "independent_asl_fluent_review": False,
            "clip_path_verified": False,
            "annotation": (
                "Publisher fingerspelling prompts (signingType fs), 3 to 8 lowercase ASCII letters, "
                "sensitiveContent false, annotation duration 0.5 to 3.2 seconds. "
                "The task covers fingerspelling, without sign or speech transcription."
            ),
            "ordinary_category": "Target contains neither j nor z nor identical adjacent letters.",
            "shortfalls": (
                "Shortfalls describe the greedy selection. "
                "A different valid selection may meet quotas that this proposal misses."
            ),
            "rights": "Publisher grant applies to daun_v3 and dmk_v3. Attribute Google and FSboard and follow the card's responsible-use guidance.",
        },
    }
    with tempfile.TemporaryDirectory(prefix=".fsboard-proposal-", dir=output.parent) as staging:
        staged_output = Path(staging)
        private = staged_output / "private-proposal.json"
        write_json(private, {
            **summary, "contains_private_signer_ids_recordings_and_targets": True,
            "clips": proposed,
        })
        summary["private_proposal_sha256"] = hashlib.sha256(private.read_bytes()).hexdigest()
        write_json(staged_output / "summary.json", summary)
        if output.exists() or output.is_symlink():
            raise AdmissionError("preflight output already exists")
        staged_output.rename(output)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new directory on a private mount")
    parser.add_argument("--previous-receipt", type=Path, required=True,
                        help="immutable fsboard-eval-v3-approved.json")
    args = parser.parse_args()
    try:
        summary = preflight(args.output, args.previous_receipt)
    except Exception as exc:
        print(f"FSboard metadata preflight failed ({safe_error(exc)}).", file=sys.stderr)
        return 1
    print(json.dumps({
        "status": summary["status"],
        "splits": {role: {"selected_clips": item["selected_clips"],
                          "selected_signers": item["selected_signers"],
                          "shortfalls": item["shortfalls"]}
                   for role, item in summary["splits"].items()},
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
