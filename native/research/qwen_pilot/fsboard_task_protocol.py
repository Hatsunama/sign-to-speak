"""Freeze a metadata-only FSboard whole-prompt A/B proposal before model scoring."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sys
import tempfile

import fsboard_evaluate as evaluation
import fsboard_training_intake as intake
import paired_experiment as paired
import train_qwen_pilot as pilot
from fsboard_admission import (
    AdmissionError, CLIP_BYTES, FRAME_COUNT, MAX_EDGE, safe_error,
    validated_fingerspelling_clip, write_json,
)

EXPERIMENT_ID = "sts-fsboard-whole-prompt-static-ablation-20261119-v2"
ROLE_SOURCES = {"holdout": "validation", "development": "train", "training": "train"}
QUOTAS = {
    "holdout": intake.QUOTAS["test"],
    "development": intake.QUOTAS["validation"],
    "training": intake.Quota(192, 20, 800 * intake.MIB),
}
TARGET_PATTERN = r"(?=[a-z ]{3,12}\Z)[a-z]+(?: [a-z]+)*"
VIDEO_EPOCHS = 2
SCORER_SHA256 = "d3899f88c2c59280b58e4861321d433e8883d1c626445b8ebd53f32411d7c266"
STATIC_MANIFEST_SHA256 = "55d6034693b3118c4e7fc87f6191effd831410d22d9cb96225c35eb7dc1b0d2e"
PREVIOUS_PROTOCOL_SHA256 = "b106018ee2f9191b202e48bbe01e90da7326e3d1738be6bc2a2959d9e0b03714"
PREVIOUS_SCHEDULE_SHA256 = "6b1980c513f627f3b4bad9cec92c6cda951f0e1fe77fececeb0b319d3f8f27fe"
PREVIOUS_STATIC_ROW_COUNT = 80


def video_directory(split):
    return f"daun_v3/video_clips/daun_v3-{split}"


def read_release_split(handle, split):
    if split not in intake.ROLES:
        raise AdmissionError("unsupported publisher split")
    rows = 0
    by_path, groups = {}, set()
    for row in intake.metadata_rows(handle):
        rows += 1
        signer, recording = row.get("signerId"), row.get("fullVideoFilename")
        if not all(isinstance(value, str) and value.strip() for value in (signer, recording)):
            raise AdmissionError("publisher row lacks signer or recording identity")
        groups.add((signer.strip().casefold(), recording.strip().casefold()))
        if split == "test":
            continue
        clip = validated_fingerspelling_clip(
            row, video_dir=video_directory(split), target_pattern=TARGET_PATTERN,
        )
        if clip is not None:
            clip["split"] = f"daun_v3-{split}"
            previous = by_path.setdefault(clip["source_path"].casefold(), clip)
            if previous != clip:
                raise AdmissionError("conflicting metadata for a clip path")
    clips = list(by_path.values())
    return clips, {
        "rows": rows,
        "official_signers": len({signer for signer, _ in groups}),
        "official_recordings": len({recording for _, recording in groups}),
        "excluded_from_all_experiment_roles": split == "test",
        "candidates": len(clips),
        "candidate_signers": len({clip["signer_id"].strip().casefold() for clip in clips}),
        "candidate_categories": intake.coverage(clips),
    }, groups


def group_keys(groups):
    signers = {("signer_id", signer) for signer, _ in groups}
    recordings = {("full_video_filename", recording) for _, recording in groups}
    return signers | recordings


def development_groups(official_train, selected):
    """Reserve each connected signer and recording group represented in development."""
    neighbors = defaultdict(set)
    for signer, recording in official_train:
        signer_key = ("signer_id", signer)
        recording_key = ("full_video_filename", recording)
        neighbors[signer_key].add(recording_key)
        neighbors[recording_key].add(signer_key)
    seeds = intake.exclusion_keys(selected) & neighbors.keys()
    pending = list(seeds)
    reserved = set(seeds)
    while pending:
        key = pending.pop()
        fresh = neighbors[key] - reserved
        reserved.update(fresh)
        pending.extend(fresh)
    return reserved


def propose(candidates, groups, previous, quotas=QUOTAS):
    publisher_groups = {split: group_keys(groups[split]) for split in intake.ROLES}
    checked = set()
    for split, identities in publisher_groups.items():
        if checked & identities:
            raise AdmissionError("publisher split overlap in signer or recording identities")
        checked.update(identities)
        for clip in candidates[split]:
            if (split == "test" or clip["split"] != f"daun_v3-{split}"
                    or clip["source_path"] != f"{video_directory(split)}/{clip['clip_filename']}"):
                raise AdmissionError("candidate lacks required publisher provenance")

    excluded = intake.exclusion_keys(previous)
    chosen, reports = {}, {}
    for role, split in ROLE_SOURCES.items():
        chosen[role], reports[role] = intake.select_role(candidates[split], quotas[role], excluded)
        reports[role]["publisher_split"] = f"daun_v3-{split}"
        excluded.update(intake.exclusion_keys(chosen[role]))
        if role == "development":
            excluded.update(development_groups(groups["train"], chosen[role]))
    return chosen, reports


def planned_optimizer_steps(admitted_video_count):
    required = QUOTAS["training"].clips
    if type(admitted_video_count) is not int or admitted_video_count != required:
        raise AdmissionError(f"training schedule requires exactly {required} admitted video clips")
    return VIDEO_EPOCHS * (required // pilot.GRAD_ACCUMULATION)


def frozen_protocol():
    scorer_path = Path(evaluation.__file__)
    scorer_source = scorer_path.read_text(encoding="utf-8").encode("utf-8")
    if hashlib.sha256(scorer_source).hexdigest() != SCORER_SHA256:
        raise AdmissionError("frozen evaluation scorer integrity check failed")
    required_video_count = QUOTAS["training"].clips
    steps = planned_optimizer_steps(required_video_count)
    return {
        "experiment_id": EXPERIMENT_ID,
        "status": "frozen_metadata_protocol",
        "role_sources": {role: f"daun_v3-{split}" for role, split in ROLE_SOURCES.items()},
        "excluded_publisher_split": "daun_v3-test",
        "selection": {
            "priority": list(ROLE_SOURCES),
            "algorithm": "Deterministic greedy category scarcity, signer balance, byte size and source path.",
            "development_partition": "Reserve entire connected signer and recording groups from publisher train.",
            "cross_role_and_prior_separation": list(intake.SEPARATION_FIELDS),
            "target_pattern": TARGET_PATTERN,
            "target": "Whole publisher fingerspelling prompt, 3 to 12 lowercase ASCII characters including single interword spaces.",
            "annotation_duration_seconds": [0.5, 3.2],
            "max_clip_bytes": CLIP_BYTES,
            "ordinary_category": "Target with neither j, z nor identical adjacent letters.",
        },
        "model": {"id": pilot.MODEL_ID, "revision": intake.MODEL_REVISION},
        "input": {
            "native_video": False,
            "ordered_rgb_stills": FRAME_COUNT,
            "max_frame_edge": MAX_EDGE,
            "dense_max_sequence_tokens": evaluation.PROFILE.max_sequence_tokens,
        },
        "training": {
            "runtime_hook_implemented": False,
            "shared_adapter_initialization_seed": pilot.SEED,
            "lora": {"scope": "language_model", "rank": 8, "alpha": 16, "dropout": 0.0, "bias": "none"},
            "optimizer": {"type": "AdamW", "learning_rate": pilot.LEARNING_RATE, "weight_decay": 0.0},
            "max_gradient_norm": paired.CLIP_NORM,
            "required_admitted_video_clips": required_video_count,
            "video_epochs": VIDEO_EPOCHS,
            "optimizer_steps_per_arm": steps,
            "matching_video_microbatches_per_update": pilot.GRAD_ACCUMULATION,
            "matching_video_microbatches_per_arm": steps * pilot.GRAD_ACCUMULATION,
            "examples_per_microbatch": 1,
            "video_order": "Two complete shared seeded shuffled passes over the frozen training manifest.",
            "video_sampler_seed": pilot.SEED,
            "video_prompt": evaluation.PROMPT,
            "video_objective": "Target-only loss with the complete literal prompt reference as the target.",
            "video_loss_weight": 1.0,
            "A": {"static_loss_weight": 0.0, "static_microbatches_per_update": 0},
            "B": {
                "static_loss_weight": paired.AUXILIARY_WEIGHT,
                "static_microbatches_per_update": 1,
                "prior_schedule_sha256": PREVIOUS_SCHEDULE_SHA256,
                "prior_static_row_count": PREVIOUS_STATIC_ROW_COUNT,
                "static_order": "Cycle the 80 distinct B static IDs in order from the pinned prior schedule.",
                "static_manifest_sha256": STATIC_MANIFEST_SHA256,
                "input_binding": "Verify each selected row against the pinned admission and its source bytes before training.",
                "static_prompt": paired.STATIC_PROMPT,
            },
        },
        "evaluation": {
            "scorer_file": scorer_path.name,
            "scorer_sha256": SCORER_SHA256,
            "scorer_source_encoding": "UTF-8 with LF line endings",
            "scorer_functions": ["score", "aggregate"],
            "prompt": evaluation.PROMPT,
            "candidates": list(evaluation.CANDIDATES),
            "runtime_hook_implemented": False,
            "conditions": {
                "matched": {"source": "same_clip", "frame_indices": list(range(FRAME_COUNT))},
                "reordered": {"source": "same_clip", "frame_indices": list(reversed(range(FRAME_COUNT)))},
                "repeated_middle_frame": {"source": "same_clip", "frame_indices": [FRAME_COUNT // 2] * FRAME_COUNT},
                "black": {"source": "black_frames_with_matched_geometry"},
                "wrong_video": {
                    "source": "different_clip",
                    "donor_policy": "Preassign a distinct frozen holdout clip with a different signer ID, recording and reference.",
                },
            },
            "condition_pairing": "Keep target, visual slot count, prompt and generation settings fixed within each example.",
            "report_per_category": list(intake.CATEGORIES),
            "max_new_tokens": evaluation.MAX_NEW_TOKENS,
            "do_sample": False,
            "normalization": "NFKC, casefold, collapse whitespace to single spaces, retain punctuation.",
            "metrics": [
                "exact match", "weighted character error rate including normalized spaces",
                "EOS versus cap", "paired wrong-video minus matched reference NLL",
            ],
        },
        "prior_evidence": {
            "protocol_file": "comparison-protocol-v1.json",
            "protocol_sha256": PREVIOUS_PROTOCOL_SHA256,
            "results_file": "PAIRED_RESEARCH_RESULTS.md",
            "evaluation_receipt_sha256": intake.PREVIOUS_RECEIPT_SHA256,
        },
        "shared_hf_budget_usd": 14,
        "compute_ceiling_usd": 12,
        "storage_startup_reserve_usd": 2,
    }


def check_output(output):
    if output.exists() or output.is_symlink():
        raise AdmissionError("preflight output already exists")


def publish_proposal(output, summary, clips):
    check_output(output)
    with tempfile.TemporaryDirectory(prefix=".fsboard-proposal-", dir=output.parent) as staging:
        directory = Path(staging)
        private = directory / "private-proposal.json"
        write_json(private, {
            **summary, "contains_private_signer_ids_recordings_and_targets": True, "clips": clips,
        })
        public = {**summary, "private_proposal_sha256": hashlib.sha256(private.read_bytes()).hexdigest()}
        write_json(directory / "summary.json", public)
        check_output(output)
        directory.rename(output)
    return public


def preflight(output, previous_path):
    check_output(output)
    protocol = frozen_protocol()
    previous = intake.read_previous(previous_path)
    identities, candidates, stats, groups = {}, {}, {}, {}
    with tempfile.TemporaryDirectory(prefix=".fsboard-meta-", dir=output.parent) as staging:
        for name in intake.METADATA_PINS:
            path = Path(staging) / name.rsplit("/", 1)[-1]
            identities[name] = intake.fetch_metadata(name, path)
            if name != "LICENSE":
                split = path.stem.removeprefix("daun_v3-")
                with path.open(encoding="utf-8-sig") as handle:
                    candidates[split], stats[split], groups[split] = read_release_split(handle, split)
            path.unlink()
    clips, reports = propose(candidates, groups, previous)
    shortfalls = any(any(report["shortfalls"].values()) for report in reports.values())
    summary = {
        "schema_version": 1,
        "status": "metadata_proposal_with_shortfalls" if shortfalls else "metadata_proposal",
        "protocol": protocol,
        "dataset": {
            "id": intake.DATASET, "publisher": "Google AI", "version": intake.VERSION,
            "url": intake.DATASET_URL, "license": "CC-BY-4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "citation": "https://arxiv.org/abs/2407.15806", "source_pins": identities,
        },
        "previous_evaluation_excluded_clips": len(previous),
        "publisher_signer_and_recording_separation_verified": True,
        "source_stats": stats,
        "splits": reports,
        "scope": {
            "task": "FSboard fingerspelling prompt transcription.",
            "holdout": "Experimental holdout drawn from publisher validation.",
            "shortfalls": "Reported shortfalls describe the greedy proposal.",
            "media_downloaded_bytes": 0,
            "source_video_verified": False,
            "frame_sampling_verified": False,
            "processor_token_contract_verified": False,
            "physical_signer_identity_verified": False,
            "independent_asl_fluent_review": False,
            "independent_static_holdout_verified": False,
            "training_started": False,
            "model_scoring_started": False,
        },
    }
    return publish_proposal(output, summary, clips)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new directory on a private mount")
    parser.add_argument("--previous-receipt", type=Path, required=True,
                        help="immutable fsboard-eval-v3-approved.json")
    args = parser.parse_args()
    try:
        summary = preflight(args.output, args.previous_receipt)
    except Exception as exc:
        print(f"FSboard task preflight failed ({safe_error(exc)}).", file=sys.stderr)
        return 1
    print(json.dumps({
        "experiment_id": EXPERIMENT_ID,
        "status": summary["status"],
        "splits": {role: {"selected_clips": report["selected_clips"],
                          "selected_signers": report["selected_signers"],
                          "shortfalls": report["shortfalls"]}
                   for role, report in summary["splits"].items()},
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
