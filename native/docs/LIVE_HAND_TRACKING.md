# Live hand tracking: implementation and change audit

## Scope

The candidate connects the existing native camera owner to a real bundled
MediaPipe Holistic detector, then feeds its measured hand landmarks into
`RoboflowHandTracker`, the attributed Kotlin adaptation of Roboflow SORT.
The actual pinned Roboflow Python package remains the host reference, not an
Android dependency. No web service or runtime download is involved.

This is geometric hand tracking, not sign recognition. The UI keeps the explicit
unsupported-recognition notice. Neither detected hands nor confirmed track IDs
modify reviewed text, call speech or constitute a recognized word. Existing
recorded-video classifier preprocessing remains unchanged.

The detector produces anatomical left/right groups of 21 xyz points. Missing
hands remain missing, not invented zeros or predicted output. Detector coordinates
are passed to the tracker's documented validation contract. IDs remain local and
session-scoped; even two geometrically continuous tracks are not proof of correct
handedness, signing, signer identity or linguistic understanding.

## Runtime and privacy contract

- Analysis is owned alongside the existing Preview use case. Stop, Back, pause,
  detach, disposal and lens replacement invalidate the old lease before release.
- CameraX uses latest-frame backpressure. Native inference and cleanup run on one
  bounded process-owned worker, not the UI thread or one new thread per lens flip.
- Every image proxy is closed, including dropped, invalid, canceled and failed
  submissions. Processing owns a bounded image copy before releasing a frame.
- Rotation and crop are explicit. Preview mirroring never reverses anatomical
  detector inputs. A changed coordinate space resets temporal identity.
- Only current-session status and bounded counts reach React Native. Frames,
  landmarks, boxes and IDs are never bridged, logged, persisted or uploaded.
  Temporary owned buffers are cleared; SDK-internal memory is released by closing
  its graph, not claimed to be cryptographically erased.
- Detector failure leaves preview available and reports tracking unavailable.
  Camera/graph cleanup uncertainty is different: it latches and blocks restart,
  including late failure after the old view has been disposed.
- Returning to the foreground never restarts the camera. Existing camera consent,
  secure-window, audio-capture, speech ownership and reviewed-text rules remain.

## SDK, model and ABI admission

The official `tasks-vision:1.1.1` Java API resolves only the pinned vendored
`tasks-core:1.1.1-offline-50505c8`. That core was built from upstream source with
its dummy logger configuration and retains the exact published ARM64 JNI. No
manifest permission stripping, binary patch or reflective logger replacement is
used. The app still has no network permission or telemetry transport dependency.

The official versioned Holistic model is bundled at build time with exact size
and SHA-256 checks, and verified before native initialization. The build records
resolved dependency inventory, checks expected SDK artifact bytes, and packages
its reviewed source/model notices. See the [offline runtime inventory](../vendor/mediapipe-offline/README.md).

Only the existing ARM64 detector JNI is admitted. x86_64 builds are retained for
other app functionality and explicitly report tracking unavailable without
loading an incompatible detector. This is not an x86_64 detector implementation.
No new production signing, store build, account, paid service or user-device
operation is part of this change.

## Cascading change audit

| Changed surface | Dependent behavior and check |
| --- | --- |
| CameraX binding | Owned Preview and ImageAnalysis release; genuine STREAMING state remains the preview liveness signal. |
| Frame processing | Crop/rotation/stride bounds, proxy closure, invalid frames, detector failure and stale timestamps. |
| Native worker | Queue bounds, dropped callbacks, cancellation, rapid replacement, close failure and Activity-wide fault latch. |
| UI event contract | Starting/active/unavailable tracking keeps preview; counts are bounded; old lens/session events are ignored. |
| Speech/text | Tracking cannot edit text or trigger speech; typed speech and explicit review remain unchanged. |
| Runtime promotion | Resolved app graph, byte-pinned offline core/vision, absence of transport classes/components, JNI ABI and alignment. |
| Bundled model | Fixed public origin, size/hash, malformed/missing files, fail-closed startup and no app downloads. |
| Notices/assets | Every variant's asset producer; exact new license/model bytes checked inside the unsigned APK. |
| Existing QA | Phone/emulator assertions accept settled tracking status while still requiring actual native/OS preview streaming. These tests are updated, not device-executed. |

The dedicated exact-head workflow runs the real-library reference/parity suite,
complete host/tooling checks, actual Android compilation and Robolectric unit
tests, then builds and audits unsigned ARM64 QA app/test artifacts. Its run result
and exact commit, not this document alone, establish what passed. A failed or
skipped stage must be reported as such.

## Remaining evidence and release boundary

Cloud tests cannot establish real front/rear camera orientation, image quality,
tracking accuracy, thermal/battery behavior, live latency or cancellation under
physical camera/native-driver faults. No live detector execution on a user phone
is claimed. The current feature needs that coordinated exact-artifact device
validation before release; this does not block writing and testing the integration
in a draft PR.

A sign classifier would additionally require the legacy 543-point numerical
feature contract, admitted weights/labels, lawful evaluation data, calibrated
rejection and measured sign accuracy. Continuous ASL translation has further
model/data gates. This hand-tracking change does not bypass those gates or claim
a recognition improvement.
