# Roboflow tracking integration: offline research only

This integrates the **actual `trackers==2.7.0` Python package** into Sign To
Speak's host research tooling. `hand_tracking.HolisticHandTracker` feeds measured
hand boxes to Roboflow `SORTTracker` and returns geometric continuity diagnostics.
It is not a Kotlin port, a hand detector, a sign recognizer or an ASL translator.

The active Android source is the stacked recognition branch, not the retired
browser prototype on `main`. The base of this work is
`01b9c9befab1c19b31c934c28aa6461866836b6b`. Android app, UI, camera ownership,
permissions, speech and model feature inputs are unchanged. Nothing is enabled
in the production app. No Python service, network permission or Android runtime
shim is introduced.

## Why SORT, and what it provides

The existing Holistic research contract provides 21 points per hand but no
calibrated box detection confidence. SORT associates their boxes across measured
frames without inventing confidence scores. ByteTrack's low-confidence recovery
cannot be meaningfully claimed from landmark presence alone. The library handles
motion prediction and geometric assignment; our adapter handles input validation,
timestamps, original detection-row mapping, clip lifetime and missing hands.

Both hands share a class-agnostic tracker. The returned `side` always comes from
the **current input slot**, not from its tracker ID. A detector handedness swap or
hand crossing can cause identity switches; tracking never relabels the left/right
feature slots. This is intentionally diagnostic, not a feature-repair heuristic.

Outputs:
- `tracker_id`: confirmed geometric ID, or `null` while unconfirmed (`-1` upstream).
  Zero is a valid ID. There are no output hands for missing measurements, even
  when the internal tracker retains a predicted box.
- `generation`: increments when this adapter resets. An identity is scoped to
  **the clip, generation and tracker_id**, never just the integer ID. A new clip
  needs a new instance or explicit `reset()`.
- `continuous`: true only when the same slot has the same confirmed ID in the
  immediately preceding measured frame. It becomes false on missing hands,
  unconfirmed detections, slot/ID changes or reset. It is not confidence or proof
  of anatomical/signer identity and must not trigger automatic speech.

Defaults are explicit: 2 consecutive frames to confirm, IoU threshold 0.3,
`lost_track_buffer=15` (500 ms at the library's 30 FPS reference), `frame_rate=30`,
and actual timestamp seconds passed on every update. A submitted-frame gap over
250 ms resets all tracking state rather than joining a sparse clip into a false
continuous sequence. These are conservative research defaults, **not tuned or
validated signing-performance thresholds**. At low sampling rates, frequent resets
are expected. Empty frames must still be submitted.

## Python API and feature contract

After installing the [separate locked host environment](DEPENDENCIES.md):

```python
from hand_tracking import HolisticHandTracker

tracker = HolisticHandTracker()
tracks = tracker.update(
    features,             # numeric array, shape (543, 3)
    timestamp_ms=100,      # actual monotonically increasing decoded timestamp
    width=640, height=480 # decoded image dimensions before preview mirroring
)
```

Import from `native/research` (for example with `PYTHONPATH=native/research`).
The layout matches the existing probe: 468 face, 21 left hand, 33 pose, 21 right
hand. Only the two hand slices are used. The input array is never modified or
returned, and face/pose coordinates are not inferred or synthesized. A missing
hand must be all NaNs. Partial missing hands, infinities and nonnumeric arrays
are rejected. Normalized xyz must be within -4..4; off-image points are allowed
within that bound, and are not clipped. Boxes must span at least one pixel on
both axes. These are adapter admission limits, not statements of model validity.

Dimensions must be integers in 1..16384 and fixed for the clip. Timestamps must
be finite, strictly increasing, within 0..86,400,000 ms. Reset for a new clip,
camera, orientation, crop, mirror convention or resolution. Invalid input is
rejected before tracker state advances. A third-party update/mapping failure
clears state and raises an error; restart the clip rather than silently continuing.

Matching this 543-point layout does not establish Holistic numerical parity or
correct handedness, normalized z, aspect ratio or model preprocessing. Existing
recognition admission gates remain open.

## Explicit local fixture CLI

```sh
python native/research/hand_tracking.py input.json new-diagnostics.json
```

Input schema (each present hand is exactly 21 `[x, y, z]` arrays):

```json
{
  "schema_version": 1,
  "width": 640,
  "height": 480,
  "frames": [
    {"timestamp_ms": 0, "left": null, "right": null}
  ]
}
```

`null` is an absent hand, not a zero pose. Input is limited to 4 MiB and 1–1800
frames. Duplicate/extra fields, nonstandard JSON NaN/Infinity and malformed
frames fail. Output contains timestamps, slots, IDs, generation/continuity,
algorithm settings, dependency versions, input SHA-256 and adapter SHA-256.
It never contains raw landmarks, boxes, video, labels or transcripts. Output
creation is exclusive and atomically publishes only a complete file, without
replacing an existing file or symlink. The CLI writes only to the explicitly
chosen local path and does not upload anything.

Even these derived diagnostics can be sensitive when generated from private
material. Keep private inputs and results outside Git and CI. This work uses
only generated synthetic landmarks; no private recordings or device data were
read, copied, exported or tested. Do not use the CLI as permission to export
production landmarks.

## Relationship to existing evaluation

`PhoneRecognitionProbeTest.kt` currently exports aggregate counts and model
predictions, **not landmarks**. Its result JSON is not input to this tool. The
adapter consumes the same in-memory layout or separately authorized host
fixtures, and is not connected to the phone probe or production CameraX path.
`recognition-benchmark.py` still evaluates supplied sign predictions independently;
tracking IDs must never be substituted for labels or treated as accuracy evidence.

Actual Android integration remains blocked on selecting/admitting a native
tracking runtime or a separately reviewed native implementation, establishing its
input/output parity and notice/dependency inventory, native cancellation/resource
ownership, and measured real-video/device evaluation. A future native port must
be labeled as a port, not as this Python package running in the APK. Adding a
server to run Python would violate the current local-only product design.

## Verification and limits

Run the actual-library synthetic suite:

```sh
python -m unittest discover -s native/research -p 'test_*.py' -v
```

It covers two hands, confirmation, lost/expired tracks, sparse timestamps,
handedness swaps, detection output reordering, reset isolation, input immutability,
malformed and bounded input, dependency failure/mapping cleanup, exclusive atomic
output and a runtime socket-connect guard. The guard covers the exercised adapter
path after imports; it is not a comprehensive network/security audit of all
transitive packages.

The separate CI workflow runs those synthetic tests on the exact candidate head.
No device test, Android build, ASL accuracy benchmark, performance improvement,
release certification or production integration is claimed. Tests were written
before the adapter and initially failed because its module did not exist; that
is a missing-feature baseline, not a reproduced bug in the old app.
