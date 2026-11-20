"""Plan and load the five FSboard v4 controls from the verified private holdout."""
from collections import Counter
from contextlib import ExitStack, contextmanager
import re

from PIL import Image

from fsboard_admission import AdmissionError, MAX_EDGE
from fsboard_task_admission import canonical_sha256
from fsboard_task_inputs import load_frames, sampled_frame_count, valid_sha256
from fsboard_task_protocol import EXPERIMENT_ID, MAX_FRAMES, MIN_FRAMES, TARGET_PATTERN


def metadata_digest(clip):
    try:
        return canonical_sha256(clip)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise AdmissionError("invalid FSboard holdout clip metadata") from exc


def donor_identity(clip):
    identities = [clip.get(field) for field in ("signer_id", "full_video_filename", "reference")]
    if not all(isinstance(value, str) and value.strip() for value in identities):
        raise AdmissionError("missing signer, recording or reference in FSboard holdout")
    signer, recording, reference = (value.strip().casefold() for value in identities)
    if re.fullmatch(TARGET_PATTERN, reference) is None or not valid_sha256(clip.get("original_sha256")):
        raise AdmissionError("invalid reference or original video digest in FSboard holdout")
    return signer, recording, reference, clip["original_sha256"]


def validate_clip(clip):
    if not isinstance(clip, dict) or clip.get("role") != "holdout":
        raise AdmissionError("FSboard control planning requires holdout clips")
    clip_id = clip.get("id")
    if not isinstance(clip_id, str) or re.fullmatch(r"holdout-[0-9]{3}", clip_id) is None:
        raise AdmissionError("invalid FSboard holdout clip ID")
    count = sampled_frame_count(clip, MIN_FRAMES, MAX_FRAMES)
    for slot, frame in enumerate(clip["frames"]):
        if (
            not isinstance(frame, dict) or frame.get("path") != f"frames/{clip_id}/{slot:02d}.png"
            or not valid_sha256(frame.get("sha256"))
            or any(type(frame.get(edge)) is not int or not 1 <= frame[edge] <= MAX_EDGE
                   for edge in ("width", "height"))
        ):
            raise AdmissionError("invalid FSboard holdout frame path, digest or geometry")
    return clip_id, count, donor_identity(clip)


def describe_example(clip, donor, count):
    matched = list(range(count))
    last_donor_slot = donor["sampled_frame_count"] - 1
    intervals = count - 1
    # Round evenly spaced slots to the nearest integer, including both endpoints.
    donor_slots = [(2 * i * last_donor_slot + intervals) // (2 * intervals) for i in matched]
    selections = {
        "matched": (clip, matched),
        "reordered": (clip, matched[::-1]),
        "repeated_middle_frame": (clip, [count // 2] * count),
        "black": (None, matched),
        "wrong_video": (donor, donor_slots),
    }
    conditions = {}
    for name, (source, slots) in selections.items():
        frames = [(clip if source is None else source)["frames"][slot] for slot in slots]
        sizes = [[frame["width"], frame["height"]] for frame in frames]
        conditions[name] = {
            "input_clip_id": None if source is None else source["id"],
            "frame_indices": slots,
            "rgb_sizes": sizes,
            "ordered_png_sha256": None if source is None else [frame["sha256"] for frame in frames],
            "geometry_differs_from_matched": name != "matched" and sizes != conditions["matched"]["rgb_sizes"],
        }
    return {"sampled_frame_count": count, "conditions": conditions}


def plan_controls(clips):
    """Return a JSON-ready plan bound to the exact holdout metadata and ordered PNG digests."""
    by_id, counts, identities, digests = {}, {}, {}, {}
    for clip in clips:
        clip_id, count, identity = validate_clip(clip)
        if clip_id in by_id:
            raise AdmissionError("duplicate FSboard holdout clip ID")
        by_id[clip_id], counts[clip_id], identities[clip_id] = clip, count, identity
        digests[clip_id] = metadata_digest(clip)
    if not by_id:
        raise AdmissionError("FSboard control planning requires a nonempty holdout")

    clip_ids = sorted(by_id)
    usage = Counter({clip_id: 0 for clip_id in clip_ids})
    examples = {}
    for clip_id in clip_ids:
        count = counts[clip_id]
        candidates = (
            other for other in clip_ids
            if other != clip_id and counts[other] >= count
            and all(a != b for a, b in zip(identities[clip_id], identities[other], strict=True))
        )
        donor_id = min(candidates, key=lambda other: (usage[other], other), default=None)
        if donor_id is None:
            raise AdmissionError(f"{clip_id} has no eligible wrong-video donor")
        usage[donor_id] += 1
        examples[clip_id] = describe_example(by_id[clip_id], by_id[donor_id], count)

    plan = {
        "schema_version": 1, "experiment_id": EXPERIMENT_ID,
        "clip_metadata_sha256": {clip_id: digests[clip_id] for clip_id in clip_ids},
        "examples": examples, "donor_usage": dict(usage),
    }
    return {**plan, "plan_sha256": canonical_sha256(plan)}


def verify_plan(clips, plan):
    """Check a persisted plan against all supplied clips before scoring any of them."""
    if plan != plan_controls(clips):
        raise AdmissionError("FSboard control plan disagrees with the frozen holdout")


@contextmanager
def condition_images(root, clips_by_id, plan, clip_id):
    """Yield the five condition image lists. Their borrowed images are closed on exit."""
    if not isinstance(plan, dict):
        raise AdmissionError("invalid FSboard control plan")
    contents = {key: value for key, value in plan.items() if key != "plan_sha256"}
    if plan.get("plan_sha256") != metadata_digest(contents):
        raise AdmissionError("FSboard control plan integrity check failed")

    try:
        example = plan["examples"][clip_id]
        donor_id = example["conditions"]["wrong_video"]["input_clip_id"]
        clip, donor = clips_by_id[clip_id], clips_by_id[donor_id]
        bindings = plan["clip_metadata_sha256"]
        if metadata_digest(clip) != bindings[clip_id] or metadata_digest(donor) != bindings[donor_id]:
            raise AdmissionError("FSboard control clip metadata binding check failed")
    except (KeyError, TypeError) as exc:
        raise AdmissionError("FSboard control plan is missing the target or donor binding") from exc

    with ExitStack() as owned:
        matched = load_frames(root, clip, min_frames=MIN_FRAMES, max_frames=MAX_FRAMES)
        for image in matched:
            owned.callback(image.close)
        donor_frames = load_frames(root, donor, min_frames=MIN_FRAMES, max_frames=MAX_FRAMES)
        for image in donor_frames:
            owned.callback(image.close)
        black = []
        for image in matched:
            frame = Image.new("RGB", image.size, (0, 0, 0))
            owned.callback(frame.close)
            black.append(frame)
        images = {}
        for name, details in example["conditions"].items():
            source = donor_frames if name == "wrong_video" else matched
            images[name] = black if name == "black" else [source[index] for index in details["frame_indices"]]
        yield images
