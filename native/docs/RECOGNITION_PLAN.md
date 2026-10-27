# Recognition, translation and customer flow

The [commercial readiness plan](COMMERCIAL_READINESS_PLAN.md) is the current delivery roadmap and release gate list. This document retains the detailed recognition research contracts and provenance. The [October 27 follow-up](RECOGNITION_FOLLOWUP_20261026.md) records the repaired input handoff and exact two-phone video execution; it does not establish independent feature parity or release accuracy.

## Approved scope and preserved foundation

The foundation rebuild originally started from `cbdd53df5dd53b9ecaa80c4cdc2d30df2146f918`. Its exact archive is retained locally with SHA256 `674b518dc7b297301f4dfc27539de93b3ab8c1f5ee5022cf6b744ae0cd5c1a25`. Current verified checkpoints are linked below. This source does not reproduce the unrecovered 477-file candidate.

The application remains free and open source. The owner selected Apache 2.0 for original application code; third-party terms remain separate. Android processing is local. No service API, account, production signing change or store build is introduced. Use Kotlin for new Android processing and retain the existing Java/JNI speech ownership. Keep the canonical Hatsu renderer and original home/camera layout. A complete Kotlin UI rewrite is not required to add recognition.

Evaluation uses existing annotated signing videos. Recruiting Deaf testers is not a prerequisite. Results must state the exact recorded-video scope; live conversational performance and user acceptance remain unverified.

## Current implementation boundaries

- CameraX still provides preview only. No analyzer, tracker or sign recognizer is connected to the customer UI.
- Typed text review, Speak, Stop and Clear use bundled local Pocket TTS/Alba. Flite production code has been retired. This is manual text-to-speech, not recognition or translation. The [final candidate checkpoint](RECOGNITION_FOLLOWUP_20261026.md) records bounded muted playback on Samsung and Seeker. The [earlier Pocket checkpoint](POCKET_SPEECH_CHECKPOINT_20261026.md) and older two-phone Flite receipt apply to their named artifacts. Voice quality and full release acceptance remain separate gates.
- Speech accepts nonblank reviewed Unicode text within 256 UTF-16 code units and 512 strict UTF-8 bytes; malformed encodings and control characters are rejected. Native synthesis bounds generated audio. Acceptance at this boundary does not prove pronunciation or multilingual support. No silent truncation or spelling substitution is permitted.
- Speech and camera stop on Back, leaving the screen or backgrounding. Draft text is cleared on leaving/backgrounding; it is not saved or logged. Cleanup failures latch and require reopening rather than silently retrying.
- Customer microphone captions and continuous ASL translation remain absent.

## Reproducible video evaluation

`python scripts/recognition-benchmark.py validate MANIFEST.json` verifies clip bytes and dataset separation. `evaluate MANIFEST.json PREDICTIONS.json` scores supplied predictions against exact labels/text. The CLI help documents the small JSON schema. It rejects missing/extra predictions and signer/source/content leakage. Training provenance may be unknown; self-declared identities never constitute independently verified held-out evaluation.

The harness does not decode videos or execute a model. Its synthetic tests prove validator/metric behavior only. Real RGB decoding, tracking, preprocessing, inference and Android execution require separate evidence. Preserve raw source IDs, labels and timestamps locally, with exact clip/model/code hashes. Keep training/tuning recordings and signers out of final evaluation.

Four small RGB files from ASL Citizen were privately range-extracted for noncommercial, non-revenue-generating research. The validated manifest SHA256 is `76f6e10dd043ba3288599fa3e80e2f4f06c4f429992838312e97757379e438d7`. Videos, dataset records and derived landmarks must not be committed, uploaded to CI or included in the APK. This diagnostic subset is not representative, sign-variant equivalence is not established, and model training identity overlap is unknown. It is not a release-accuracy benchmark.

The earlier private desktop tracking attempt required at least 700 MiB free RAM and stopped at 369 MiB before tracking or dependency installation. The later [Android follow-up](RECOGNITION_FOLLOWUP_20261026.md) executed RGB decoding, Holistic tracking and the local classifier on four private diagnostic clips. That convenience sample is not a validated accuracy benchmark. Do not change paging or close another agent's application to make a desktop probe run.

The [October 26 input-handoff and model research follow-up](RECOGNITION_FOLLOWUP_20261026.md) separates the reproduced Android storage failure, its QA-only repair, the actual Android ASR decoder experiment and unresolved continuous-model input provenance.

## Model selection evidence

The first isolated-sign candidate is pinned to TheoViel/kaggle_islr commit `148ccb9b35b321149ac716ed69966dd8e7efeb09`, file `output/ens_576-80_768-25_norm-soup_model.tflite`, 36,870,396 bytes, SHA256 `25531b695e07b21467ef91bea5164c96e36ff86a1fff84182613f39ac4724bed`.

The actual FlatBuffer has float32 input `[frames,543,3]`, float32 output `[1,250]`, embedded preprocessing and builtin operators. A Windows CPU LiteRT 2.3.0 smoke invocation with seeded synthetic coordinates produced finite outputs. That proves operator execution only, not useful recognition, video accuracy or Android compatibility. The weights remain outside the application until suitability is measured.

The Kaggle dataset was designed for isolated educational signs. Its dataset card explicitly cautions against treating it as ASL-to-English translation or a general natural-language interface. A small successful demonstration would not remove that limitation. The official label-index map, feature layout and training provenance must be verified for any selected artifact.

### Native feature and lifetime contract

The inspected Kaggle preprocessing expects the legacy Holistic layout: 468 face, 21 left-hand, 33 pose and 21 right-hand points. Current Holistic Tasks provides 553 total points. The first-468 face selection and group order have structural support from the pinned preprocessing and MediaPipe's appended iris topology; no index-order defect was established. Numerical feature parity, normalized z, anatomical handedness, preview mirroring, aspect ratio, missing detections and cadence still need paired reference evidence. Hand-only output cannot stand in for missing face/body inputs. Never fill missing features with plausible zeros without matching the trained contract.

Add ImageAnalysis to the existing CameraX owner only after the selected tracker/model contract is established. Release every ImageProxy, bound queued work and temporal history, and invalidate all pending results on session replacement or cancellation. MediaPipe LIVE_STREAM can discard submitted frames while busy; multi-task joins must not wait forever for a callback from every submitted frame. Frames and landmarks stay native and in memory; React Native receives text/status only. No automatic speech from uncertain model output.

## Offline SDK admission gate

The first opt-in phone probe compiled, and all 123 normal Android tests passed at source `2678f22c95691ea5befd25aae85b3101054f8637`, but [CI 40140649088](https://github.com/Hatsunama/sign-to-speak/actions/runs/40140649088) correctly failed the final instrumentation APK audit. Published MediaPipe `tasks-vision:1.1.1` pulls `tasks-core:1.1.1`, whose published logger factory initializes DataTransport. The merged test manifest contained INTERNET, ACCESS_NETWORK_STATE and transport services/receiver. The production app manifest passed its no-network gate. That failed published-SDK checkpoint produced no phone recognition result; the later offline source-built SDK and input-handoff follow-up have separate runtime evidence.

Removing permissions or excluding transport while retaining that factory would hide or break the dependency. The official source at commit `50505c8312c7eacb1bd411cecdc4cc9fd08d53cb` instead generates `TasksStatsDummyLogger` by default. That source-built variant is now pinned for QA instrumentation, retaining the published native library and notices. [Source rebuild 40146406354](https://github.com/Hatsunama/sign-to-speak/actions/runs/40146406354) completed successfully. See the [vendored SDK inventory](../vendor/mediapipe-offline/README.md); production tracker execution and model inference remain unverified. It must pass artifact/class/dependency inspection, the unchanged actual-APK network gate and physical execution before adoption. Source configuration alone is insufficient evidence. No binary factory patch or reflective constructor bypass is acceptable.

The instrumentation audit now also rejects unplanned services, receivers and providers even if they request no additional permission. Three injected-component cases failed before the guard repair. This is an artifact-audit repair; it does not establish that all possible SDK behavior has been inspected.

## Phase gates

| Phase | Deliverable | Required evidence |
| --- | --- | --- |
| 0 | Source/license inventory and fixed video benchmark | Exact source, clip/model hashes, permission records, independent splits and validated annotations |
| 1 | Native frame analysis and tracking | RGB-video and real-camera orientation, two hands, failure/recovery, cancellation, bounded RAM and observed phone performance |
| 2 | Isolated-sign recognition | Exact feature parity, real inference, per-class errors, coverage/rejection, non-signing/unknown inputs and unseen-recording results |
| 3 | Reviewed text to local speech | Actual UI-to-bridge-to-JNI-to-AudioTrack behavior, input boundaries, Stop/Back/Home and explicit recovery on both phones |
| 4 | Temporal fingerspelling | Literal character error and exact-word results, J/Z motion, repeated letters and correction |
| 5 | Continuous ASL-to-English | Suitable aligned data/model, measured sentence performance and failure analysis including negation, names and numbers; generic LLM rewriting is not proof |
| 6 | Local customer reply captions | Explicit microphone permission, offline audio tests, real microphone lifecycle/cancellation and no capture of app speech as customer input |
| 7 | Release verification | Full required checks, exact final artifact on both available phones, offline/privacy/16 KiB/support coverage and separate future store-signing arrangements |

Each coherent change gets a caller/lifetime/failure-path impact audit. Reproduce bug regressions before fixing them, run focused checks and the full required suite, then inspect the final diff. A changed app/build configuration requires retesting the exact final artifact. Do not recommend installation or call the release ready while required evidence is missing. Only use a phone when it is at Home and unused; return it Home and restore its settings afterward.

## Primary references

- [Native MediaPipe samples](https://github.com/google-ai-edge/mediapipe-samples)
- [Hand Landmarker Android lifecycle and frame dropping](https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker/android)
- [Current Holistic outputs](https://developers.google.com/edge/mediapipe/solutions/vision/holistic_landmarker)
- [Pinned isolated-model preprocessing](https://github.com/TheoViel/kaggle_islr/blob/148ccb9b35b321149ac716ed69966dd8e7efeb09/src/tflite/prepro.py)
- [Kaggle isolated-sign dataset](https://www.kaggle.com/competitions/asl-signs)
- [ASL Citizen research terms](https://www.microsoft.com/en-us/research/project/asl-citizen/dataset-license/)
- [Apache 2.0](https://www.apache.org/licenses/LICENSE-2.0)
