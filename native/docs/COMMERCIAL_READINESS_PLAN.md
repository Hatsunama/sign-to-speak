# Sign to Speak Commercial Readiness Plan

Updated October 27, 2026. Status: roadmap for the full product; commercial release remains blocked.

The target is a free, open-source Android app that supports an ASL signer and a hearing conversation partner using local recognition, reviewed English text, natural local speech and local reply captions. Commercial readiness here means a useful, distributable and supportable product, even though the app will be free.

Continuous ASL-to-English quality and lawful model/data use are the largest uncertainties. Run their feasibility gate early. A hand tracker, a 250-label classifier or a typed-speech demonstration does not complete that product.

Current input/notice and two-phone evidence is in the [October 27 follow-up](RECOGNITION_FOLLOWUP_20261026.md); the [Pocket checkpoint](POCKET_SPEECH_CHECKPOINT_20261026.md) preserves earlier speech repairs. The [October 25 checkpoint](READINESS_CHECKPOINT_20261025.md) preserves the preceding baseline. The [release contract](RELEASE_CONTRACT.md) and [model admission record](MODEL_ADMISSION.md) distinguish verified baselines, proposed gates and remaining decisions.

## Verified starting point

| Area | Current evidence | Remaining work |
| --- | --- | --- |
| Source | Exact tested app/build source `a21faf347247f0bbd9fb44fda8266de8e72e16b6`; later documentation does not change those inputs. | Approve release scope and preserve reproducible inputs. |
| Camera | The same final pair passed rear default, front/rear selection before/during preview, Stop/Back/Home and repeated landscape on Samsung and Seeker. | Add analysis with the same ownership/cancellation protections. |
| Speech | Bundled Pocket TTS/Alba, reviewed Unicode text and protected 24 kHz PCM passed bounded UI/native playback, rejection, cancellation and recovery on both phones. Sound stayed zero. The renderer and speech implementation are unchanged from c4f7222. | Owner fixed listening/naturalness, long text, opaque native-load cancellation timing, performance/soak/privacy and supported OS. API 26–28 speech remains outside the protected contract. |
| Checks | Full Android CI 40465926012 passed 154 normal tests with zero failures/errors/skips; full host/TypeScript/policy checks passed. Exact pair: 15 bounded app cases per phone plus handoff/rejection/recovery and four diagnostic video clips each. Signed payload equality, models, 15 framework texts, 11 additional JavaScript grants/20-package receipt, all 15 native ELF/ZIP alignment passed. | Actual 16 KiB execution, sustained resources, accessibility and independent capture matrix. Both phones use 4 KiB pages. Bounded notice coverage is not complete independent source reconstruction. |
| Tracking SDK | Pinned offline MediaPipe core source build is admitted to QA instrumentation; actual Holistic video execution passed on both phones. | No production tracker/frame analyzer. Independent numeric/handedness/cadence parity, ABI/support scope and production admission remain. |
| Recognition | The repaired app-owned input transfer ran four private RGB diagnostic clips through Holistic and the pinned classifier on each phone, with four expected top-1 matches each. | Not a release benchmark. Admitted weights/labels, independent feature parity, disjoint licensed accuracy/unknown rejection, fingerspelling and sentence translation remain. |
| Reply captions | A standalone shell-UID Vosk/JNA decoder passed silence and one synthetic phrase on both phones. No app microphone/ASR flow exists. | Exact static dependency/source/notices, human audio comparison, local app capture, editable captions and turn ownership. |
| Distribution | Existing local development key; isolated testOnly QA packages. | Production identities, keys, AAB/APK delivery and store acceptance. |

The source-build result is [40146406354](https://github.com/Hatsunama/sign-to-speak/actions/runs/40146406354); static admission does not prove tracker execution. Some older research notes still describe that run as pending. Use the current [camera evidence](../../qa-artifacts/rear-camera-20261025/evidence.json), [research plan](RECOGNITION_PLAN.md) and [offline SDK inventory](../vendor/mediapipe-offline/README.md) together. This project rebuilds from the verified foundation; it does not reproduce the unrecovered historical 477-file candidate.

## Product constraints and working decisions

- Keep original app code Apache 2.0 and the app free. Check engine, weights, voices, phonemizers, datasets and redistribution terms separately.
- Keep inference and conversation processing entirely on-device. No translation, recognition, speech, analytics or crash-upload service APIs.
- Prefer bundled baseline models and optional verified local import. Hugging Face model acquisition is allowed, but never permits hosted inference or uploading conversation content. An in-app downloader would need a separately reviewed network exception; it must not silently weaken today's no-INTERNET admission policy.
- Preserve the canonical Hatsu renderer, styling and existing behavior. Add only the controls and states required by the new features.
- Keep the React Native shell. Implement processing in Kotlin with existing Java/JNI ownership where useful. A Kotlin UI rewrite is not a prerequisite.
- Rear camera is the initial choice; explicit front selection remains available. Lens changes must retire all old analysis and recognition results.
- Evaluate using licensed annotated signing videos. Recruiting Deaf testers is not a prerequisite. Recorded-video results must not be presented as proof of live conversational accuracy or broad user acceptance.
- Keep user frames, audio, landmarks and drafts volatile. Models and the existing visual seed are separate from conversation content.
- Keep work under C:/sign_to_speak for now. Protect signing material and credentials outside the Git worktree. Do not change paging or close other agents' apps.
- Use a phone only when it is free at Home with no active instrumentation. Restore its permissions/rotation and return it Home afterward.
- Continue one QA delivery path. Do not create separate store builds until Phase 11, when testing genuine commercial readiness.
- Use fresh-context subagents with a medium reasoning ceiling for bounded implementation tasks. Give each a clear file scope; independently review its work and evidence afterward. Avoid simultaneous heavy builds on the low-RAM PC.
- Phase exits are acceptance gates, not routine confirmation prompts. Continue when the gate passes. Pause only for a genuine missing decision, rights/resource blocker or required publishing action. If device checks are next and both phones are busy, end the turn and report. Give an estimate and pause while simply waiting.

Working first-release scope is ASL to English, English spoken replies to captions, and reviewed speech output. Phase 0 must fix the supported vocabulary/domains, devices, regions and OS floor. API 29+ is the recommended full-feature starting floor because current speech privacy enforcement is unavailable on API 26–28. Retaining those older versions requires an explicitly accepted limited mode; do not silently re-enable speech there.

## Phase sequence and dependencies

Effort bands are planning estimates for experienced mobile and ML contributors, not delivery promises. They exclude unresolved licensing, training and store review delays.

| Phase | Deliverable | Depends on | Initial effort band |
| --- | --- | --- | --- |
| 0 | Release contract and reproducible baseline | Current checkpoint | 3–5 days |
| 1 | Model/data feasibility and admission decisions | 0 | 1–2 weeks; sentence-model spike may take 2–4 |
| 2 | Natural local speech and robust text input | 0; model admission from 1 | 1–3 weeks |
| 3 | Real video tracking and native frame pipeline | 1 | 2–3 weeks |
| 4 | Isolated-sign recognition with rejection and review | 3; suitable classifier | 2–4 weeks |
| 5 | Temporal fingerspelling | 3; suitable sequence model/data | 3–6 weeks |
| 6 | Continuous ASL-to-English translation | 1 and required video/model inputs | Research-dependent; first spike 4–6 weeks |
| 7 | Local customer reply captions | 1 | 2–4 weeks |
| 8 | Complete conversation flow | 2, 4, 5, 6, 7 | 1–2 weeks |
| 9 | Privacy, accessibility, performance and compatibility | Starts at 0; final pass after 8 | 2–4 weeks |
| 10 | Frozen commercial test candidate | 8 and 9 | 1–2 weeks |
| 11 | Production identities and exact channel artifacts | 10 | About 1 week plus owner/account inputs |
| 12 | Store beta, launch and maintenance | 11 | At least applicable closed-test period plus reviews |

Start speech work and the sentence-model feasibility spike immediately after Phase 0. Tracking, voice and reply-caption work can overlap. Isolated-sign and fingerspelling milestones are valuable subsystems; they do not substitute for Phase 6. A sentence model may use RGB rather than landmarks, so its actual input contract determines its dependency on Phase 3 outputs.

The critical path is lawful data/model access → reliable input/feature contract → measured sentence quality → complete conversation flow → hardening → exact shipping artifacts → store testing.

## Phase 0 Establish the release contract

1. Preserve the reviewed source, lockfiles, SDK/NDK versions, build procedure, notices, artifact hashes and current test receipts. Keep expiring CI evidence in a durable, sanitized location.
2. Record supported ASL scope, English output, minimum OS, target phones, supported regions and offline setup behavior.
3. Approve quality targets before freezing evaluation data. Separate tracking, isolated labels, spelling, sentence meaning, ASR and voice quality.
4. Inventory existing code before adding a second implementation. Reuse the current benchmark scorer, recorded-video probe, CameraX owner, cancellation lease, speech ownership and protected PCM player.
5. Record acceptance responsibilities: owner for scope/voice/store decisions; mobile contributor for native integration; ML contributor for feature/model validation; independent review for regression and evidence checks.
6. Set bounded compute/storage budgets. Heavy training or new paid cloud jobs require a resource decision; the current low-RAM PC is not the default training machine.
7. Check Play account type, eligibility and publisher identity prerequisites now. If the conditional closed test applies, arrange the general tester pool early; their installation starts only when the store candidate is ready. No Deaf-tester recruitment is required.

**Exit:** a versioned release contract, acceptance matrix and artifact inventory. No unknown feature may be described as implemented.

## Phase 1 Resolve model and data feasibility

Create one admission record per candidate: exact origin/revision, SHA-256, size, engine version, input/output shapes, preprocessing, labels/tokenizer, training provenance, licenses/notices, permitted redistribution and conversion/quantization history.

Choose model delivery using measured model sizes and current store limits. Bundled baseline weights preserve today's no-INTERNET policy; install-time asset delivery or verified local import may be needed for larger models. An approved HF-only setup path is also possible, with a narrow reviewed network policy and honest setup disclosure. Inference must work in airplane mode once provisioning is complete. Test first-run offline failure separately.

Use platform/library integrity and parsing mechanisms: validate pinned revision, hash, length, model format, labels/tokenizer/voice dependencies and app compatibility before activation. Install updates atomically, preserve a previous valid model on failure and reject truncated, corrupt or mismatched assets visibly. Exercise canceled downloads/imports, full storage and model replacement while work is pending. Never use remote executable model code, dummy predictions or an online fallback.

Run a small, lawful pilot before extensive integration. Measure actual video/audio inference on both phones, including cold load, peak memory and cancellation. Finite synthetic output proves operator execution only.

For signing evaluation, freeze separate development, tuning and final test sets by signer, source and content. Hash clips and record annotations and permitted uses privately. Report unknown pretrained training overlap; it prevents claiming independently unseen signers.

The current GitHub TheoViel candidate is an isolated-sign research candidate, not a general translator. A repository license alone does not establish the rights for every weight or source recording. Do not assume that its GitHub name is a valid Hugging Face model ID.

Existing private ASL Citizen clips are restricted diagnostic material. Its terms limit use to noncommercial, non-revenue research and prohibit distributing data or modifications. How2Sign is research/CC BY-NC, and OpenASL states CC BY-NC-ND. None is automatically approved for a shipping training or evaluation pipeline. Obtain suitable rights or select another corpus; a free app does not settle this question. See [ASL Citizen terms](https://www.microsoft.com/en-us/research/project/asl-citizen/dataset-license/), [How2Sign](https://how2sign.github.io/) and [OpenASL](https://github.com/chevalierNoir/OpenASL).

Begin the continuous-translation feasibility spike here. Require a suitable aligned corpus, trustworthy references, deployable weights and a plausible on-device resource budget.

**Exit:** admitted candidates and lawful benchmark material, or a specific blocked decision. If no acceptable continuous model/data route exists, the full translator release stays blocked. A narrower beta needs an explicit scope decision and honest naming.

## Phase 2 Replace unacceptable speech quality

The earlier Flite comparison found matching 16 kHz generation/playback but did not establish acceptable voice quality. The current Pocket candidate requires fresh actual-playback checks at its 24 kHz contract, including format, channels, clipping, gain, underruns, pronunciation and sentence prosody.

Use a fixed listening set covering short replies, questions, negation, dates, times, names, numbers, abbreviations, punctuation and long text. Let the owner compare equivalent samples at matched loudness on both phones.

Candidate investigation:

| Candidate | Reason to evaluate | Admission condition |
| --- | --- | --- |
| Kokoro-82M | Natural-speech candidate with Apache-licensed weights | Audit exact Android conversion, voices and phonemizer. Its common eSpeak path is not automatically an Apache-only distribution. |
| Supertonic | Local ONNX candidate with an official model release | Weights have OpenRAIL terms; code licensing does not replace those terms. Resolve suitability before integration. |
| Retired Flite | Earlier functional baseline for diagnosis and comparisons | Production source has been removed from the current candidate; it must not return as a hidden fallback. |

The table records earlier alternatives; the current implementation candidate is pinned Pocket TTS/Alba. The official [Kokoro card](https://huggingface.co/hexgrad/Kokoro-82M), [sherpa-onnx Android/runtime project](https://github.com/k2-fsa/sherpa-onnx), [Supertonic card](https://huggingface.co/Supertone/supertonic) and [eSpeak license](https://github.com/espeak-ng/espeak-ng/blob/master/COPYING) are starting evidence, not a completed license review. Current [Piper development is GPL](https://github.com/OHF-Voice/piper1-gpl); do not describe it generically as an MIT dependency. Copyleft obligations require a deliberate distribution decision.

Integrate the accepted engine behind the existing owned PCM playback path. Match its actual sample rate rather than reusing Flite's 16 kHz setting. Preserve cancellation generations, per-player capture policy, bounded output and zeroization.

Replace the ASCII-only restriction with an explicit supported-text contract: Unicode normalization, punctuation, numbers, visible rejection of unsupported text and bounded chunking. Preserve the original reviewed text; never silently truncate or alter its meaning. Chunk limits must follow the selected model's token/phoneme limits.

**Exit:** owner accepts the voice; text boundaries, real audio, long input, Stop, editing, background, failure and retry pass on both phones. Remove the superseded production engine/assets only after the replacement supplies every required responsibility.

## Phase 3 Prove tracking and native frame ownership

Use the admitted official SDK architecture and existing opt-in PhoneRecognitionProbeTest. Reuse its lawful video inputs for diagnosis, then make QA exercise the production processor rather than maintain a separate imitation.

The offline MediaPipe source build already exists in QA instrumentation. Check its ABI coverage and admit any additional supported ABI builds before relying on them in the runtime matrix. Promote only the admitted configuration after actual tracker execution, artifact/component/network inspection and notices are complete. Do not restore telemetry by swapping back to the published default core, or merely hide its manifest permissions.

Add CameraX ImageAnalysis beside Preview under the existing native owner. Use a bounded executor and latest-frame backpressure; close every ImageProxy on success, failure and cancellation. Keep native images/landmarks out of React Native. Deliver only bounded text/status updates. An async consumer must finish or own a safe copy before its input is released. LIVE_STREAM may drop submissions without a callback; correlate actual returned timestamps and expire incomplete work rather than waiting forever.

Validate rear/front, rotation, aspect ratio, anatomical handedness, preview mirroring, missing face/hands, crossings, occlusion, motion blur, timestamp order and recovery. A lens switch invalidates queued frames, temporal state and every old result.

The existing model expects the legacy 543-point contract; current Holistic reports 553. Prove named/index mappings and coordinate conventions with independent reference fixtures. Do not treat a dimension match, dropping points or filling missing features with zeros as parity. See the [Holistic guide](https://developers.google.com/edge/mediapipe/solutions/vision/holistic_landmarker).

Use direct RGB-video decoding for accuracy evaluation. Camera recapture of those videos can exercise the optical path, but its display artifacts and domain differ and must be reported separately.

**Exit:** actual video and camera execution, input-contract evidence, bounded resources, loss/recovery and Stop/Back/Home/lens-switch tests pass. Tracking remains a prerequisite, not proof of sign understanding.

## Phase 4 Deliver isolated sign recognition honestly

Connect an admitted classifier to the verified feature pipeline. Validate label order, normalization, timing, padding/missing values, output interpretation and Android/reference parity.

Evaluate all declared classes with signer/source-disjoint clips, plus unknown signs, no signer, ordinary gestures, partial signing and transitions. Report per-class precision/recall, macro-F1, accepted accuracy, rejection and coverage. Calibrate thresholds on development data rather than the final test set.

Show tentative recognized text in a reviewable draft. Speaking requires an explicit user action. Low confidence, invalid input or tracking loss must produce an understandable uncertain/unavailable state rather than a plausible word.

**Exit:** frozen benchmark targets pass with sufficient samples and uncertainty estimates; the real UI-to-model-to-review-to-speech path works on both phones. Keep the supported vocabulary explicit.

## Phase 5 Add temporal fingerspelling

Use a sequence model appropriate for fingerspelling. Test J/Z motion, repeated letters, holds, pauses, names, numbers, transitions and spelling outside the vocabulary.

Measure character error rate and exact-word accuracy against annotated recordings. Do not let dictionary correction invent a name or collapse repeated letters. Distinguish recognized characters from suggested corrections.

**Exit:** agreed spelling metrics and critical fixtures pass; users can review/correct text; cancellation or a lens change cannot append stale characters.

## Phase 6 Establish continuous ASL to English

Build a separate sentence-level acceptance path. Recognizing isolated labels and concatenating them is not translation. Model selection must account for movement, facial/nonmanual signals, body context and temporal segmentation.

Validate sign/utterance start and end, pauses, repeated signs, signer changes, unknown content and long sequences. Test negation, questions, pronouns, names, quantities and numbers explicitly. Preserve adequate face/body input when required by the model.

Measure sentence meaning against trusted aligned references. Report coverage and abstention, critical semantic errors and per-domain results alongside BLEU/chrF or similar text metrics. A fluent English rewrite or an LLM judge alone cannot establish correct ASL meaning.

Recorded videos with reliable annotations are the required evaluation medium. If those references cannot support semantic scoring, fix the benchmark or report the gate blocked; do not infer broad conversational accuracy from a few clips.

**Exit:** lawful assets, real phone performance and the locked semantic-quality gate pass. Otherwise retain an explicitly limited research state. Timebox initial exploration, then estimate further work from measured results; do not promise a release date from repository demos.

## Phase 7 Add local customer reply captions

Compare an admitted Whisper.cpp tiny/base configuration with an exact small Vosk model using actual noisy phone audio and transcribed reference clips. Include accents, short replies, numbers, names, silence, music, nearby speech and low-volume speech. The [Whisper.cpp project](https://github.com/ggml-org/whisper.cpp) provides Android work; [Vosk's model list](https://alphacephei.com/vosk/models) publishes per-model sizes/licenses. Published benchmark accuracy is not expected field accuracy.

Capture only after an explicit microphone action and permission. Keep PCM volatile, stop input promptly and make captions reviewable/editable. Provide clear denied/revoked permission, no-speech, unsupported and engine-failure states.

Use explicit turn-taking initially: speaking and reply listening should not run together and transcribe the app's own voice. Avoid a cloud/system-recognizer fallback and background microphone services.

**Exit:** approved WER/critical-word targets, silence/noise rejection, endpoint latency, memory, cancellation, buffer cleanup and phone microphone tests pass.

## Phase 8 Complete the conversation interaction

Connect signing → tentative English → review/edit → Speak, followed by customer reply → local captions. Retain typed input as a deliberate fallback.

Use the existing visual language for necessary controls. Make selected lens, camera/microphone activity, uncertainty, loading, model availability and Stop clear. No hidden automatic speaking or automatic capture resumption.

Test interleaved actions: edit during synthesis, flip during inference, Stop during permission/model loading, switch to reply mode, rapid repeated actions, Back/Home/lock/unlock, calls/audio interruptions and process recreation. Clear drafts and private session state according to the current privacy behavior.

Run the entire path through each phone's actual camera: signing input → recognition/translation → reviewed text → actual speech → an actual microphone reply → visible captions. Do not satisfy this journey with injected predictions, a mocked tracker or prerecorded audio substituted for actual speech generation. Licensed signing videos played on another display can provide the signing performance without recruiting Deaf participants; label the optical recapture conditions and keep this functional test separate from direct-video accuracy scoring. Optional owner-performed smoke signs do not prove broad signer accuracy.

**Exit:** complete journeys succeed from a fresh install with admitted models provisioned, then in airplane mode on both phones. First-run missing-model behavior and failure/recovery are also tested. User-facing claims match the measured feature scope.

## Phase 9 Harden privacy and product quality

Run this work throughout implementation, then perform a final dedicated pass:

- Audit actual production manifests, dependencies, native libraries, assets, license notices and runtime network behavior. No accounts or telemetry SDKs are needed for the stated product.
- Check files, logs, backups, caches and failure reports for camera/audio/text/landmark leakage. Test that stale results and queued audio cannot escape cancellation.
- Verify actual screenshot/recording and playback-loopback behavior while synthetic speech really plays. FLAG_SECURE, alignment and silent output alone do not prove capture protection.
- Exercise audio routing, interruptions, permission revocation, camera/mic contention, unsupported devices, corrupt/missing models, low storage and memory pressure.
- Test TalkBack, font scaling, contrast, touch targets, keyboard focus, landscape and reduced motion. Preserve the existing capture-policy tradeoffs rather than weakening them silently.
- Inspect every shipped native library for ELF and ZIP alignment, then execute the whole app in a real 16 KiB environment. Two 4 KiB phones and zipalign output cannot certify it. Current emulator runner KVM access is blocked; obtain an approved capable runner/device rather than changing permissions or paging covertly.
- Soak-test repeated conversations and lens changes, queue bounds, memory retention, thermal behavior, battery use and latency. Optimize only measured bottlenecks.
- Remove superseded prototype engines, duplicate processors and dead fallbacks after their replacements pass. Preserve provenance, legal notices and useful independent tests.

Use [Android's page-size guidance](https://developer.android.com/guide/practices/page-sizes) and [core quality guidance](https://developer.android.com/docs/quality-guidelines/core-app-quality). Store policy dates can change; full 16 KiB execution remains this project's gate.

**Exit:** no unresolved high-severity correctness, privacy, security or lifecycle defects; the supported-device matrix and limitations are accurate.

## Initial acceptance targets

These are proposed project targets, not established industry guarantees or results. Phase 0 approves or revises them before final evaluation is frozen.

| Area | Proposed initial gate |
| --- | --- |
| Voice | Owner accepts naturalness/intelligibility on a fixed set of at least 50 utterances; no clipping, missing endings or meaning-changing normalization. |
| Tracking | At least 95% usable tracking on annotated frames where the required hands/body are actually visible; separately report occlusion, handedness swaps, losses and recovery. |
| Isolated signs | At least 95% precision among accepted outputs at at least 80% coverage in declared scope; macro/per-class results and confidence intervals published. |
| Unknown input | False acceptance at most 1% at the selected operating point; use enough independent negative clips to support the estimate, initially at least 500. |
| Fingerspelling | CER at most 10% and exact-word accuracy at least 85% on the declared benchmark; motion/repeated-letter and critical-name cases reported separately. |
| Sentence meaning | At least 95% meaning-correct among accepted in-scope outputs at at least 80% coverage, scored against reliable annotated references; zero unsupported critical substitutions in the fixed negation/name/number suite. |
| Reply ASR | WER at most 15% in specified quiet conditions and 25% in specified noise; critical words and silence hallucinations scored separately. |
| Responsiveness | Provisional warm first-audio/caption-finalization p95 at most 1.5 seconds; recognition cadence matches its trained contract; show loading for cold work. |
| Cancellation | Revoke input/result eligibility immediately; target audible Stop p95 at most 250 ms. Measure worker completion separately; never free buffers still in use or report uncertain cleanup as confirmed. |
| Resources | Start with a provisional peak PSS budget of 1 GiB per target phone; no OOM, unbounded queue or sustained memory growth across 100 start/stop cycles and a 30-minute soak. |
| Privacy | No unauthorized capture, stale speech/result acceptance, conversation persistence or transmission in the defined tests. |
| Regression | All required checks pass; zero unresolved release-blocking defects. |

Always publish sample counts, conditions, model/code hashes, coverage and uncertainty. Do not tune against the final test set, report only successful classes, or lower targets after a failed final run without a documented scope change.

## Phase 10 Freeze a commercial test candidate

Require all promised features and prior gates, then freeze app source, models, preprocessing/tokenizers, build inputs and supported claims.

For every coherent implementation change: trace callers and lifecycle/failure paths, reproduce bugs with meaningful failing regressions, review impact and the final diff, run relevant integration tests and the full required suite. Compilation and another agent's report are not runtime evidence.

A required model, recognition probe or feature may not be skipped and counted as accepted. Required missing assets fail readiness; optional research probes remain explicitly NOT_RUN.

Build one QA candidate, independently audit it, sign locally and verify installed bytes. Cold-start it on both free phones and exercise all affected success, failure, cancellation and recovery paths. Record exact hashes and restore each phone's state.

If code or build inputs change, test the resulting exact artifact again. Failed checks remain failures until repaired. Do not transfer acceptance from an older package.

**Exit:** complete commercial-test readiness dossier, with no missing core feature or unresolved rights issue. Only then create separate store artifacts.

## Phase 11 Configure identities and test shipping artifacts

Choose final package IDs and per-channel update policy. Different signing certificates under the same package ID create cross-store install/update constraints; distinct Play/Solana IDs are the recommended starting decision if independent installation is wanted.

Keep the local Play upload key and Solana APK signing key different. Also ensure the Play-delivered app-signing certificate differs from Solana's. Play App Signing has a Google-held delivery key distinct from the owner-held upload key; confirm custody/setup with the owner. Keep owner-held keystores in protected C: locations outside Git and cloud CI. Agree a recoverability plan before generation; never put passwords or private keys in chat/logs.

Create the Play Console app record, complete identity/signing prerequisites and use an internal-testing release to obtain Play-delivered packages for Phase 11 validation. Internal testing is available before production access; Phase 12 completes public listing and any required closed test.

Build the Play AAB and Solana APK from the frozen source/model inventory. Remove QA/testOnly settings, use production identities, increment versions and audit the actual binaries. Test Play-delivered APKs/splits on both phones, not just the AAB archive. Test the exact Solana APK separately, including fresh install, upgrade and emergency roll-forward. Preserve signing continuity within each channel. See [Android signing](https://developer.android.com/studio/publish/app-signing) and [Solana APK preparation](https://docs.solanamobile.com/dapp-store/build-and-sign-an-apk).

**Exit:** distinct protected identities and hashes, actual delivered-artifact runtime acceptance, notices, model availability and update/recovery evidence. No production keys or separate store builds are created by this planning task.

## Phase 12 Complete store testing and launch controls

Prepare accurate store descriptions, privacy policy, data-safety disclosures, content rating, support contact and screenshots using permitted methods. Camera/mic access and SDK practices must match the actual binary and [Play User Data policy](https://support.google.com/googleplay/android-developer/answer/10144311?hl=en). Do not advertise general ASL translation before its gate passes.

As checked October 25, 2026, new ordinary Android Play submissions must target API 36 or later. Check the live [target API policy](https://support.google.com/googleplay/android-developer/answer/11926878?hl=en) again at submission.

If the owner's Play account is a personal account created after November 13, 2023, current policy requires at least 12 opted-in closed testers continuously for 14 days before applying for production access. These need not be Deaf testers. Two connected QA phones and recorded-video evaluation do not replace that store process. Check account applicability and the current [closed-testing policy](https://support.google.com/googleplay/android-developer/answer/14151465?hl=en).

Complete applicable [Android developer verification](https://developer.android.com/developer-verification) for the actual channels and regions. Use current Solana publisher guidance; its [active publishing CLI](https://github.com/solana-mobile/dapp-publishing) differs from older config-driven examples. Store submission credentials/transactions are outside the runtime app; do not add blockchain or service APIs to the app merely to publish it.

Publish only after the owner reviews the concrete release dossier and store requirements are satisfied. Start with a controlled rollout and a tested roll-forward route. Maintain dependency/license review, public source/notices, model-version benchmarks and a way to submit redacted issues without uploading conversation content. Store fees, transaction costs and publisher account eligibility must be checked with the actual accounts.

**Exit:** approved listings, required beta evidence, exact accepted artifacts and an operated support/update process.

## First execution batch

1. Freeze Phase 0 scope, OS/model delivery choices and measurable gates.
2. Diagnose the existing PCM/playback path and prepare the voice listening comparison.
3. Run a small admitted tracker/model probe on lawful annotated RGB clips using the existing QA harness and both free phones.
4. Produce the continuous-model/data rights and feasibility decision before major sentence-model integration.
5. Record measured results, next blockers and a revised estimate. Continue independent work while a device/resource decision is pending.

## Timeline and remaining decisions

With experienced mobile and ML contributors working in parallel, a better speech/reply-caption and recorded-video prototype may fit roughly 4–8 focused weeks if suitable assets are admitted. That milestone is not a general translator release.

A full product may require 4–9 months or longer. Existing models could shorten engineering; unsuitable models, data rights or sentence quality can block it entirely. There is no defensible committed release date until the Phase 1 and Phase 6 feasibility evidence exists. Current RAM/device availability and store review/testing time also affect the calendar.

Decisions to record during their phases: full supported ASL scope; minimum OS; bundled/imported versus approved HF-only acquisition; natural voice and licensing path; lawful sentence data/model; Play account/tester requirements; package identities; Play signing custody; and protected key recovery. None requires generating keys, starting a store build or recruiting Deaf testers now.
