# Roboflow tracking integration: native adaptation and host reference

Sign To Speak now includes two explicitly different implementations:

- `hand_tracking.HolisticHandTracker` uses the **actual `trackers==2.7.0` Python
  package** for offline host reference checks.
- `RoboflowHandTracker.kt` is an attributed **Kotlin adaptation** in the Android
  app source. Both the live CameraX/Holistic consumer and the native recorded-video
  probe invoke it for measured frames and expose only aggregate diagnostics. The Python
  runtime and its dependencies are not shipped in the APK.

Neither implementation is a hand detector, sign recognizer or ASL translator.
The native tracker is connected to live CameraX through a bundled offline
Holistic detector. See [live runtime admission and change audit](../docs/LIVE_HAND_TRACKING.md).
No server, permission change or Python-in-APK shim is added.
The classifier retains exactly the same landmark values and anatomical ordering.

The active Android source is the stacked recognition branch, not the retired
browser prototype on `main`. The base of this work is
`01b9c9befab1c19b31c934c28aa6461866836b6b`. Existing layout, camera ownership and reviewed speech
behavior are preserved; the UI gains honest tracking/unavailable status text. New code and notice assets are compiled into the app;
no modified APK has been installed or exercised on a device.

## Kotlin implementation and native pipeline

`RoboflowHandTracker` accepts two nullable 63-float xyz arrays, timestamp in
milliseconds and decoded image dimensions. It ports the upstream XYXY SORT
Kalman state, timing, expiry, assignment-before-threshold and maturity behavior.
Four independent scalar 2×2 Kalman filters are equivalent to the upstream
independent XYXY axes. At most two hand measurements are accepted. Assignment
is exhaustively optimized over up to 64 retained hypotheses rather than
introducing SciPy or another native dependency. A hypothesis overflow fails and
resets instead of silently discarding identities.

This is a bounded adaptation, not a general MOT replacement. Floating-point
results are not bit-exact, and arbitrary equal-cost assignment ties need not
match SciPy's choices. Seeded reference checks include identical-box ties but
cannot establish every possible tie. Do not infer recognition or quality benefit
from matching geometric IDs.

The synchronized API has explicit `reset()` and permanent, idempotent `close()`.
Capture `generation` before scheduling asynchronous work, pass it as
`expectedGeneration`, and reject a result if the generation changed before
consumption. Stale work is rejected before state mutation. The recorded-video
caller is synchronous and creates one tracker per clip, closes it in `finally`,
and clears temporary hand arrays after each update. The live caller uses the same
tracker contract with session generations, bounded worker ownership and explicit
cancellation; it never exports the IDs or coordinates. No input arrays are retained
or modified by the tracker; state is only bounded geometric hypotheses.

`PhoneRecognitionProbeTest.runClip` now consumes the native tracker between
Holistic detection and unchanged classifier preprocessing. Its output adds only
measurement/confirmation/continuity/reset counts and implementation identity,
not per-frame landmarks, boxes or track IDs. Those counts are diagnostic. No
automatic speech or classifier acceptance decision is made from tracking.

The Apache-2.0 upstream license and modified-source attribution are retained in
`native/licenses/roboflow-trackers/` and registered in the app's existing reviewed
license asset producer. The expanded CI audits the exact unsigned APK's new
model, native library and notices. Packaging success is not device execution.

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

`PhoneRecognitionProbeTest.kt` exports aggregate counts and model predictions,
not landmarks. Its result JSON is not input to the Python reference CLI. The
Kotlin adapter consumes the actual in-memory Holistic hand arrays before the
unchanged 543-point classifier feature assembly. The host reference consumes
separately generated or authorized fixtures matching that contract.
`recognition-benchmark.py` still evaluates sign predictions independently;
tracking IDs must never be substituted for labels or treated as accuracy evidence.

Live customer-camera use remains blocked on admitting a production landmark
runtime/model, establishing feature parity and frame ownership/cancellation,
and measuring real-video/device behavior. The current QA detector/model remain
in the instrumentation app. This PR does not move them into production, change
permissions or add a service to avoid those gates.

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

For native parity and contract checks, use the official Kotlin 2.1.20 compiler:

```sh
python native/research/native_tracking_parity.py --kotlinc /path/to/kotlinc
```

The runner compiles the actual Android-source class, not a test copy, and compares
its outputs against the pinned real Python package. Current deterministic coverage
matches 931 protocol steps across 907 synthetic measured frames, including the
IoU and time boundaries. Native contract checks cover stale work, close/reset,
input ownership, transactional validation and bounded-hypothesis failure/recovery.
These are JVM tests, not device/Android runtime evidence.

The CI workflow runs the Python and Kotlin reference checks on the exact head,
then runs host and Android unit checks and builds an unsigned ARM64 app/test
pair for exact manifest, model, dependency, notice and native-library audits.
Model downloads occur only in build preparation, never in the app. CI does not
install APKs, use devices or alter signing. No ASL accuracy, performance improvement or
release certification is claimed. Tests were written
before the adapter and initially failed because its module did not exist; that
is a missing-feature baseline, not a reproduced bug in the old app.
