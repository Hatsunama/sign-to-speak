# Release contract and Phase 0 acceptance baseline

Version 0.1, October 25, 2026. Status: evidence baseline and proposed gates. Commercial release is blocked. This contract supplements the [commercial readiness roadmap](COMMERCIAL_READINESS_PLAN.md); scope and thresholds have not been approved by the owner.

## Intended product and fixed constraints

A free Android app for ASL-to-English communication: continuous signing becomes reviewable English text; the signer explicitly starts local English speech; a partner's spoken English reply becomes reviewable local captions. Typed input remains a fallback. Original app code is Apache 2.0; third-party engines, models, voices, datasets and notices require separate admission. No service API for inference, speech, analytics, crash upload or conversation data. Keep user frames, audio, landmarks and drafts volatile. Preserve the React Native shell, canonical Hatsu renderer, existing visual language and intro/camera layout. Rear camera is default; front/rear selection works before and during preview. A lens change must retire stale work. Continue one QA path; no separate store builds until Phase 11.

Licensed annotated signing videos are the evaluation medium. Phone optical recapture is a separate functional test. Recruiting Deaf testers is not a prerequisite. Recorded videos do not prove live conversational accuracy or user acceptance.

Unresolved product decisions: approved vocabulary and conversational domains, signer population, regions, devices beyond current Samsung S24 and Seeker QA phones, accent/dialect handling, commercial minimum OS, model delivery and first-run offline behavior. English is the intended output and reply language; breadth is not approved. The roadmap recommends API 29+ as the full-feature floor because current speech privacy enforcement disables speech on API 26–28. Gradle minSdkVersion 26 is only the present build setting. Retaining older OS versions needs an approved limited mode.

## Exact evidence and reproducibility

| Evidence | Verified result and limit |
| --- | --- |
| Source | Phone app/test source 46e78059da8ba59d75cf29a0dc74eb38fc23f9d8. Later 74866a6c1cb576aee074fe0ceb01dbea6bc3e997 changes emulator test/policy only. Current documentation HEAD 8546c23438342301f7ddabd59e0fef76acd9ca33 is not itself device tested. |
| CI | [Android run 40236437136](https://github.com/Hatsunama/sign-to-speak/actions/runs/40236437136), attempt 1, runner image 20261023.334.3: 151 normal Android tests, zero failures/errors/skips; four expected deliberate counterfactual failures checked separately. Actual APK audits passed. See the local unsigned receipt at `C:/sign_to_speak/phone-acceptance-20261025/final-46e7805/unsigned-receipt.json`. |
| Test APK pair | Unsigned app SHA-256 a3fafbbcb32da0d78b94f79636c23ad7551c8342c5ea142ae3b9c2d0367efb59; unsigned test 65883af403fd54aefb1415f19babd4bbb10615526b6dbdae91d0672a4565ea2b. Signed app c8b198e1584692f885bbd6dcd049b23d2a035798387745a095764a7ddbea93dc; signed test ab61d7c3a72b6a4bab54d4e4cca5fe093833375f1cfcc6641e561d4ae41746bf. The local `signed-receipt.json` alongside it confirms identical payloads, unchanged existing development key and certificate SHA-256 2a0d37dc62eebed39bd08a9238c9255e26eb52a00143a02e0c6c473489985a21. These are testOnly QA packages. |
| Phones | The local `physical-receipt.json` alongside those receipts: installed hashes matched, 12 cases per phone, zero failures, covering portrait/landscape/repeat preview, typed and native speech. Cold starts 136 ms S24 and 371 ms Seeker in this bounded check. Permission/rotation restored and phones returned Home. [Reviewed summary](../../qa-artifacts/rear-camera-20261025/evidence.json) reports rear default/flip, camera ownership and Stop/Back/Home. Pixel-perfect appearance and broad reliability are not established. |
| Emulator | [Build run 40237504240](https://github.com/Hatsunama/sign-to-speak/actions/runs/40237504240) compiled/audited x86_64 pair at 74866a6. Runtime NOT_RUN because existing runner KVM access was blocked. |

Committed-source SHA-256 hashes match the CI source-checksums receipt and Git blobs at phone source: native/package-lock.json 68b10e85a8fdb3b1f72d77c78d2aabe3b33d5752e35b620af338afe5d5b68875; Gradle wrapper JAR b3a875ddc1f044746e1b1a55f645584505f4a10438c1afea9f15e92a7c42ec13; wrapper properties a180163249c7252412ddaee1c7adcf59f6494b1183136f690fbd9a3b8aaed00e. Windows working-file hashes can differ due to line endings; compare Git blob bytes. Pinned inputs: React Native 0.86.3, React 19.2.3, Node 24 (CI guide verifies 24.19.0), Gradle 9.3.1, build tools 36.0.0, compile/target SDK 36, NDK 27.1.12297006, Kotlin 2.1.20, CMake 3.31.5. Production Java compilation uses 17; Robolectric API 36 uses Java 21. Follow locked-install and native CI procedure in [CI guide](../scripts/CI.md). Implementation/build-input changes need a fresh run; use existing hosted CI for heavy builds. Do not run local Gradle/NDK/CMake builds or alter paging on this low-RAM PC.

## Code and rights inventory

| Component | Current boundary and admission need |
| --- | --- |
| Camera | App.tsx, PrivateCameraView.kt and PreviewLease.java own preview, lens choice and cancellation. No production ImageAnalysis, tracker or recognition bridge. Reuse existing ownership. |
| Speech | SpeechOwner.kt, SpeechSession.java, Flite JNI and ProtectedPcmPlayer.java drive owned PCM/AudioTrack from reviewed typed text. Input accepts 1–256 printable ASCII characters; API 26–28 speech is disabled. Owner rejects current CMU US SLT voice; PCM versus playback cause is undiagnosed. Flite source pinned to e9e2e37c329dbe98bfeb27a1828ef9a71fa84f88; license audit, notices and modifications are in native/licenses/flite, app Apache files in native/licenses/sign-to-speak. |
| Tracking SDK | Offline MediaPipe Tasks Core 1.1.1 QA-only source build from 50505c8312c7eacb1bd411cecdc4cc9fd08d53cb, AAR SHA-256 e0e9365d753b990f9b0d68e61274dfa5736d8812dbde5679095aec5ca28bd6b2. [Vendor inventory](../vendor/mediapipe-offline/README.md) has build and QA license records. Source rebuild/static admission succeeded; actual tracker execution, ABI coverage and production admission have not. |
| Recognition research | scripts/recognition-benchmark.py validates manifests/scores supplied predictions, but does not decode or infer. PhoneRecognitionProbeTest.kt is opt-in QA research. TheoViel isolated-sign candidate at 148ccb9b35b321149ac716ed69966dd8e7efeb09, SHA-256 25531b695e07b21467ef91bea5164c96e36ff86a1fff84182613f39ac4724bed, has synthetic desktop operator evidence only. Legacy 543-point input differs from current 553-point Holistic output. Weight/training-media rights and parity unresolved. Private ASL Citizen clips are noncommercial diagnostics, excluded from Git/app and not a release benchmark. See [recognition plan](RECOGNITION_PLAN.md). |
| Missing paths | No customer microphone/ASR or continuous ASL-to-English customer path. No voice, sentence or reply model admitted. Each engine, weight, voice, phonemizer, dataset and native dependency needs origin, hash, redistribution terms and notices. |

## Acceptance matrix

All numbers are proposed roadmap targets, not owner-approved gates or measured results. Freeze independent final data only after approval. Publish source/build/model/clip hashes, counts, conditions, coverage and uncertainty; calibrate on development data only.

| Gate | Proposed target and proof | Current status |
| --- | --- | --- |
| Voice | Owner accepts at least 50 fixed utterances; no clipping, missing endings or meaning-changing normalization. Actual phone audio, Unicode/input bounds, Stop/edit/background/recovery. | Blocked: rejected voice, ASCII/256 limit. Bounded typed path passed. |
| Tracking | At least 95% usable tracking on visible required anatomy; loss, handedness swaps and recovery; named 543/553 mapping and direct RGB/real camera parity. | NOT_RUN: static source build only. |
| Isolated signs/unknowns | At least 95% precision among accepted at at least 80% coverage; unknown false acceptance at most 1% with initially at least 500 independent negatives; per-class intervals. | NOT_RUN: no production path or lawful final benchmark. |
| Fingerspelling | CER at most 10%, exact-word accuracy at least 85%; J/Z, repeated letters, names separately. | Absent. |
| Sentence meaning | At least 95% meaning-correct among accepted in scope at at least 80% coverage; zero unsupported critical substitutions in fixed negation/name/number suite; reliable aligned references and per-domain results. | Absent; feasibility and rights block release. |
| Reply ASR | Actual local phone microphone; WER at most 15% specified quiet / 25% specified noise; critical words, silence hallucinations, permissions, endpoint/cancellation separately. | Absent. |
| Latency/resources | Provisional warm first-audio/caption-finalization p95 at most 1.5 s; audible Stop p95 at most 250 ms; peak PSS 1 GiB per target phone; no OOM/unbounded queue/growth over 100 cycles and 30-minute soak. | Unmeasured for target pipeline. |
| Privacy/compatibility | No unauthorized capture, persistence, transmission or stale output in defined tests; real screenshot/recording/playback-loopback during speech, full-app 16 KiB execution, accessibility/failure matrix. | Incomplete; emulator runtime KVM blocked. |
| Complete journey | Fresh-install then airplane-mode signing → review → actual speech → actual microphone reply → captions on both phones with model provisioning and recovery. | Absent. |
| Release | Frozen code/models/artifacts; all required host/native/phone gates green, zero release blockers; later independently audited channel artifacts. | Blocked: no commercial candidate or production identities. |

## Owners and dependencies

| Owner | Required decision or evidence |
| --- | --- |
| Product owner | Approve vocabulary/domains, regions, devices, OS floor/limited mode, voice, model delivery/first-run behavior, targets and honest claims. Set compute/storage budget; decide heavy training or paid cloud work. |
| ML contributor | Establish lawful model/data route, per-candidate origin/revision/hash, labels/tokenizer/preprocessing, training overlap, independent signer/source/content splits, trusted sentence references, direct-video results and phone feasibility. |
| Mobile contributor | Integrate admitted camera/PCM/microphone ownership with bounded buffers, cancellation, privacy/local processing; measure both phones and preserve visuals. |
| Independent reviewer | Verify rights/notices, exact receipts, splits, regressions, APK permissions/network behavior, privacy/16 KiB execution and claim/result alignment. |
| Owner/publisher | Check Play account type and conditional general-tester requirement early, identity/regions, package IDs, distinct Play/Solana signing custody/recovery, listings and disclosures. Store testers, if required, need not be Deaf. No new key or store build belongs to this contract. |

Dependency chain: scope and rights → lawful annotated data/models and frozen evaluation → tracker parity and sentence feasibility → local voice/reply captions → reviewed conversation → privacy/accessibility/performance and exact QA candidate → channel identities/artifacts → store testing and approval. Missing core features or rights fail readiness; optional probes remain NOT_RUN.
