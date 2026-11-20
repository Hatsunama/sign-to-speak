"""Record completed matched encodings while the pinned cloud input verifier runs."""

from contextlib import contextmanager
from pathlib import Path
import stat
from time import monotonic


@contextmanager
def journal_completed_encodings(gate, verified, directory):
    """Write per-example diagnostic records for one run of the existing input gate.

    The caller supplies a freshly loaded admission and runs the cloud verifier in
    the same thread. Records describe completed calls to encode_example. Only the
    cloud verifier can produce the full input attestation.
    """
    directory = Path(directory).absolute()
    for parent in reversed(directory.parents):
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(
            info, "st_file_attributes", 0
        ) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("unexpected filesystem link in journal path")
        if not stat.S_ISDIR(info.st_mode):
            raise NotADirectoryError(parent)
    directory.mkdir(mode=0o700)

    # inspect_examples walks the admission's roles and clips in insertion order.
    expected = [
        (role, clip["id"], clip["reference"])
        for role, clips in verified.clips.items()
        for clip in clips
    ]
    provenance = {
        "admission_sha256": verified.summary["admission_sha256"],
        "model": dict(verified.frozen_protocol["model"]),
    }
    encode_example = gate.encode_example
    completed = 0

    def encode_and_record(processor, images, reference, **options):
        nonlocal completed
        if completed >= len(expected) or reference != expected[completed][2]:
            raise ValueError("input gate encoding call is outside the admission order")
        role, clip_id, _ = expected[completed]
        started = monotonic()
        encoded = encode_example(processor, images, reference, **options)
        ordinal = completed + 1
        gate.publish_private(directory / f"{ordinal:04d}.json", {
            "schema_version": 1,
            "status": "encoding_completed",
            "ordinal": ordinal,
            "role": role,
            "clip_id": clip_id,
            "elapsed_seconds": monotonic() - started,
            **provenance,
            "encoding": encoded[2],
        })
        completed = ordinal
        return encoded

    gate.encode_example = encode_and_record
    try:
        yield
    finally:
        gate.encode_example = encode_example
