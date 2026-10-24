# Local speech reconstruction and cascading audit — 24 October 2026

## Decision

The approved local speech/session backend has been reconstructed on verified foundation `10bc6fa8d4d5baa4e44c3fcebd75b2d675ba3b07` (original foundation `78c71bd36bbd0f04c86bd85e4b795da35bcd56f7`). The exact tested **code commit is `4ec7c8a38d9444db4d6ef2b3506faa27b0b5a292`**. Later documentation/evidence commits do not identify a newly built APK.

**Shipping release blocked.** The verified artifact is an isolated, `testOnly` ARM64 QA APK signed locally with the existing Android debug certificate. No production APK/AAB has been signed, published, or recommended for installation. The user confirmed production signing is not configured. Separate store builds are deliberately deferred until commercial readiness testing. The future Play AAB and Solana APK must use different private keys, stored outside the Git checkout and never uploaded to GitHub. After production signing is configured, each final shipping artifact needs its own two-phone cold-start and affected-feature checks. Passwords or key files should not be posted to a conversation or uploaded.

The application remains a camera-preview prototype. No ASL recognizer, continuous recognition pipeline, translation, user text/speech input, or automatic speech was added. Synthetic test text exercises the production native speech bridge; the visible UI has no speech caller. This is the requested backend reconstruction, not completion of a sign-language translator.

## Exact source and artifact evidence

- [Draft PR 3](https://github.com/Hatsunama/sign-to-speak/pull/3).
- [Successful required check run 40119903979](https://github.com/Hatsunama/sign-to-speak/actions/runs/40119903979), independently matched to code commit `4ec7c8a...`.
- Unsigned app SHA-256: `0aa364f263b7a6d27f1bd5466654adc0e83dc11b4ff602401f1b486593d9f5f4`.
- Locally signed, physically tested app SHA-256: `6f5d30e481781e5b7cc5dca13f47550143277ac159e27f84b5a01846afa3f7e1`.
- Signed instrumentation APK SHA-256: `77c5eda4fa351cca9fd4ca286d2969212e1efbea5452b9383a54eaaa56dfe0fd`.
- Certificate SHA-256: `2a0d37dc62eebed39bd08a9238c9255e26eb52a00143a02e0c6c473489985a21`. Existing key bytes were checked unchanged before/after signing; no key was generated, exported, copied, or uploaded. Signed/unsigned ZIP payloads match.
- Device-installed `base.apk` hashes were checked against the signed artifacts, rather than inferred from installation success.
- [Machine-readable artifact audit](evidence/final/phones/artifact-verification.json), [signing receipt](evidence/final/phones/signing-receipt.json), [phone verification](evidence/final/phones/phone-final-verification.json).

The unpublished 477-file candidate was **not recovered**. Its inventory `d38aa2515a33447893328680274665ae76ceb181016f11b67e2ed871855a8a5e` and old APK receipt do not establish reproducibility. This source tree does not purport to reproduce that candidate or explain every failure of the old installed binary.

## Original issue status

| Original issue / requirement | Status on this reconstruction |
| --- | --- |
| Missing native speech/session integration and empty speech-stop connection | Rebuilt; production bridge, native session ownership and lifecycle cancellation exercised on both phones. |
| Start preview failed after camera permission | Reproduced on reconstruction; CameraX/Fabric layout root cause repaired; actual streaming and camera closure checked in portrait and landscape on both phones. The old unpublished candidate's root cause remains unverified. |
| Seeker screenshot exclusion inconclusive | Accepted for the measured, camera-off Home regions on both phones; positive controls before/after, verified drawn secure window, fresh frames. Historical candidate remains unverified. |
| Screen-recording exclusion lacked acceptance evidence | Accepted for the measured, camera-off Home regions on both phones; positive controls before/after, verified drawn secure window, fresh frames. Historical candidate remains unverified. |
| Independent internal-audio capture exclusion lacked a positive control | Accepted within the tested scope on both phones: positive control captured, protected synthetic speech not captured, no read errors. |
| Full-app speech cancellation / lifecycle / recovery unaccepted | Both phones exercised real JNI synthesis, PCM, AudioTrack progress/drain, invalid input, stale tokens, Home, Back, destruction and explicit recovery. |
| Appearance and existing navigation | Home return JSX/styles byte-identical to foundation; renderer unchanged; Home/Thank-you/return text, bounds and hierarchy matched the pre-rebuild installed baseline on both phones. |
| Continuous ASL/translation absent | Still absent, accurately disclosed by existing UI; outside the approved missing-backend scope. |
| API 26–28 speech | Deliberately rejected; preview support retained. Required playback-capture policy is available only from API 29. |
| Full-app 16 KiB runtime | Not established. Every packaged ARM64 ELF and APK alignment was checked statically; both tested physical phones use 4 KiB pages. |
| Exact old candidate source | Still unrecovered; user authorized reconstruction on the verified foundation. |
| Shipping APK/AAB | Blocked as described above. QA evidence does not certify a different shipping artifact. |

## Deduplicated product failures and smallest repairs

### P1 — Active speech did not recheck privacy eligibility (proven; fixed)

**Path:** `native/android/app/src/main/java/com/xmilo_at_your_side/sign_to_speak/ProtectedPcmPlayer.java`, `play`, lines 105–138; `requireReady`, lines 170–173. Related authority: `SpeechOwner.kt`, `eligible`, lines 23–30, and its Activity lifecycle paths.

**What broke:** admission checked eligibility, but partial-write and playback-drain loops only checked cancellation. A later loss of native privacy eligibility could leave the existing job speaking. This violated the same fail-closed contract enforced for new jobs. No external audio leak was demonstrated by this regression.

**Evidence:** two regression tests were committed and run before the repair: privacy loss after a partial write failed to stop the next write, and privacy loss during drain continued polling the playback head (402 calls versus the expected first call). Raw RED XML is in `evidence/red/playback-privacy/`. The other two failures in that interim XML were test-fixture defects, described below; they are not additional product findings.

**Smallest repair:** call the existing complete eligibility guard on every write/drain iteration, retaining the cancellation check inside the output lock. Existing finally cleanup, session invalidation, input/PCM wiping and process fault propagation retain their responsibilities.

**Cascading verification:** partial writes, stalls, drain completion, cancellation/focus loss, cleanup failures, lifecycle authority, stale callbacks and explicit recovery were inspected and tested. The final suite and real production speech journeys passed. Independent capture checks used the actual tested APK, not an imitation player.

### P1 — A late CameraX TextureView could stay unmeasured under Fabric (proven; fixed)

**Path:** `native/android/app/src/main/java/com/xmilo_at_your_side/sign_to_speak/PrivateCameraView.kt`, `layoutPreview`, lines 39–53; `requestLayout`/`cancelPreviewLayout`, lines 223–234; attach/detach and disposal paths. Callers/consumers: Fabric native view manager, CameraX `PreviewView`/surface provider, React preview status, native Back/Home teardown.

**What broke:** Fabric assigned the host's bounds before CameraX asynchronously inserted its texture child. React's parent layout path did not perform a new Android child layout. A correctly sized outer preview could therefore contain a zero-size or stale-size texture and never produce a working camera surface. A plain FrameLayout fixture with manually injected STREAMING status had missed this runtime dependency.

**Evidence:** physical native-view dumps and CameraX surface timeouts established the broken child geometry. A regression using the actual React parent failed before repair on API 28 and 36: measured width 0, expected 320. The RED hosted run had exactly those two new failures (`evidence/red/camera-layout/`).

**Smallest repair:** coalesce a posted measurement/layout of `PreviewView` using the host's current positive bounds, including late descendants. Reject detached/disposed work, clear pending state on completion/post failure, and remove pending callbacks during teardown. Camera ownership, acquisition, capture policy and visible layout were retained.

**Cascading verification:** queued resize, detach/reattach, disposal, native status truthfulness, actual surface streaming, Start/Stop/Back/Home, camera closure and no automatic restart. Both phones passed the exact final APK in portrait and landscape.

## Build and test evidence failures corrected separately

These failures did not establish more product defects:

- Windows/Linux generated-source drift and host Flite test linking initially failed CI. Canonical UTF-8/LF generation and the intended no-I/O link boundary were repaired; read-only prepared-source checks, native execution and Android builds then passed.
- The actual instrumentation APK initially lacked its required `testOnly` marker. Its QA manifest was corrected and the packaged manifest/signature/isolation gate rerun.
- API 28's playback test setup called a newer Android accessor while creating a mock. The setup is now API guarded. A focus-cancellation cleanup assertion also raced the worker; explicit drain barriers now prove cancellation while the track is actually owned. Neither failure required a new playback product change.
- The intentional cleanup counterfactual fixture lost context after the camera layout edit. Its source/marker integrity was refreshed without weakening its four expected fail-before assertions. The final candidate suite is green; the deliberately broken counterfactual still fails precisely those assertions and restores the exact candidate bytes.
- Physical QA initially selected an off-screen Start label and later conflated off-screen Stop visibility with acceptance. It now requires a fully visible, settled clickable button for input, then checks rendered state and native streaming/cleanup independently. An earlier automated S24 Stop no-op could not be reproduced manually on the same APK; its product root cause is **unproven**, and it is not reported as another app bug.
- The first visual control included an Android ActionBar shadow; the temporary opaque control now uses a NoActionBar theme. Later nonblack pixels were independently identified as a Samsung Edge handle and a SystemUI bubble. Geometry from visible windows/input regions excludes those overlays without changing phone settings or relaxing pixel thresholds. MediaProjection mirrors input windows onto a virtual display; a RED regression now ensures physical display 0 is selected and CLONE input rejected. The helper's pure geometry tests pass (14 cases). These failures occurred in the control/measurement fixture and did not demonstrate app content leakage.

## Final verification boundary

The required hosted run completed the full host suite, TypeScript, Java session checks, workflow/SDK/unsigned-artifact policy, generated-source validation, C/JNI engine execution, Android compilation and **118 Android unit tests with zero failures/errors/skips**. The C suite passed both tests; actual JNI execution passed 344 checks. Raw final JUnit XML and native receipts are under `evidence/final/ci/`. Passing checks are evidence for their exercised behavior, not proof that the whole app is correct.

On Samsung S24 and Seeker (physical ARM64, Android 16/API 36), the exact final app was cold-started. Each phone passed four portrait tests and a landscape camera journey. Tests used the real registered React module, production Flite JNI and AudioTrack, not substituted speech factories. Observed nonzero PCM, PLAYING state, playback-head advance and drain completion establish runtime playback; human audibility was not separately certified. User audio volume was not changed.

Independent audio capture used fresh normal Android MediaProjection consent, exact owned UID filters, an isolated capturable tone control and the production synthetic-speech test wholly inside the protected capture window. S24 captured 74,378 nonzero control samples; Seeker 73,359. Each protected phase read 319,488 samples with **zero nonzero samples, zero peak and zero read errors**. No microphone, raw PCM, camera frames or video files were recorded. Source, timing, positive control and overlap receipts are preserved.

Independent visual checks passed on the same signed app hash. OS screenshots contained only opaque black in S24 ROI `[2,105,998,2194]` and Seeker ROI `[210,112,1198,2596]`. A normal-consent MediaProjection probe measured 89 fresh protected frames on S24 and 3 on Seeker, every measured pixel opaque black, with zero errors. Before/after controls were 100% magenta in screenshots and captured frames. Parent validation checked frame timestamps after arming and before phase end, pixel/frame accounting, identical ROI, actual visible/drawn secure app window, ordered phases and clean explicit shutdown. Camera remained OFF; private camera images and raw video were not persisted. The tested ROI excludes known system overlays, so this is not a claim about every screen pixel or active-preview recording. Raw counts, source, timestamps and window receipts are under `evidence/final/visual/`.

The Home return JSX/styles SHA-256 is `43bd39eb4d896411a1f432b22105314ff669669ca92797727206ea57f2525819`, matching the verified foundation. The native Hatsu renderer is unchanged. Hierarchy/text/bounds comparison passed both phones. A pixel-perfect baseline visual comparison was not performed by disabling secure-window protection.

The actual QA APK contains nonempty bundled Hermes JavaScript, the native speech library, bundled voice and byte-identical notices. Its packaged manifest has no INTERNET permission. Flite synthesis has no enabled network/file/audio-device I/O path; playback is supplied by the protected Android adapter. Runtime needs no service API or model download. Build dependency downloads are separate from runtime behavior.

## Related paths inspected and remaining gaps

The audit followed React mount/start/stop/unmount and promise generations through native Activity identity, registered module, session executor/input ownership, JNI handles and cancellation, native arena cleanup, PCM ownership/wiping, audio focus/output/drain, and process-level cleanup fault propagation. Camera tracing covered Fabric host measurement, late CameraX texture creation, leases/status observers, provider acquisition/errors, native Back/pause/destroy, owned unbind and reattachment. APK contents, notices, generated source provenance, unsigned QA task graphs and counterfactual restoration were checked.

No additional product issue was found in that inspected scope. This does not establish universal correctness.

Remaining evidence gaps: production signing/identity and exact shipping artifact verification; original candidate source; continuous ASL/translation (not implemented); physical API 26–35 behavior; full-app 16 KiB-page runtime; physical OS/audio cleanup fault injection (covered with controlled dependencies rather than damaged hardware); captures outside the tested owned Home-region/synthetic-audio scope. Secure-window tests do not certify privileged capture mechanisms or an external microphone/camera.

No production app/build/test source changed after the verified code commit. The follow-up changes only documentation/evidence, standalone QA-probe material and repository key-file ignore rules; application/build/runtime-test sources remain the tested code. A filename-only regression first demonstrated 12 missing root/nested key exclusions; all 16 secret-path and 3 ordinary-path expectations now pass. Ignore rules reduce accidental inclusion but cannot prevent a deliberate force-add. No private key bytes are staged. Temporary capture helpers are removed after evidence collection, projections stopped, and rotation restored. Signing keys and credentials are retained untouched. Local untracked build caches and the unrecovered-candidate recovery material are not part of this evidence commit.

## Unresolved commercial-readiness blockers — no separate store builds yet

1. **Core product functionality is missing (proven).** `native/App.tsx:131–132` discloses that ASL recognition and spoken translation are unavailable. `PrivateCameraView.kt:99–105` binds only a Preview use case; no frame-analysis/recognizer-to-translation-to-speech connection exists. `LocalSpeechModule.kt:22–27` can speak test text, while `App.tsx:43,102` only starts/stops session ownership. Smallest next product increment: implement a real, fully on-device recognition pipeline and connect validated output to the existing speech authority. Required acceptance: actual supported signs, meaningful recognition accuracy/latency criteria, wrong/uncertain/no-hand input, cancellation and offline recovery. The backend QA does not substitute for this missing feature.
2. **Production signing is unconfigured (confirmed by user and code).** `native/android/app/build.gradle:63–76` has no production signing config. Before store packaging, establish distinct Play-upload and Solana-APK private keys outside Git, record public certificate identities, and verify the identities are different. Google Play's upload key and Play-managed distribution signing key have different responsibilities; both must remain distinct from the Solana signing identity. No new private key or separate store variant was generated during this mission. [Google Play signing concepts](https://support.google.com/googleplay/android-developer/answer/9842756?hl=en).
3. **There is no verified shipping artifact (proven gap).** All physical evidence identifies the isolated `.qa`/`testOnly` app and debug certificate, not a Play-delivered install or production Solana APK. Once feature readiness and signing are established, build each intended channel, audit the packaged policies/content and cold-start/test the exact delivered artifact on both phones. Any source/build change invalidates the old artifact's release acceptance.
4. **Full-app 16 KiB runtime remains unverified (evidence gap, not a demonstrated alignment defect).** Static ELF/ZIP alignment passed for all packaged ARM64 libraries; the final app has not run in a verified 16 KiB-page environment. Run the complete production runtime there, including native synthesis, camera and teardown. Google's native-app guidance treats alignment checks and runtime testing as separate steps. [Android 16 KiB guidance](https://developer.android.com/guide/practices/page-sizes).
5. **Supported-device and customer privacy acceptance remain incomplete (release decisions/evidence gaps).** The minimum SDK remains 26 (`native/android/build.gradle:4`), but speech intentionally rejects API 26–28 (`ProtectedPcmPlayer.java:170–173`). Decide the commercial support contract before release, then test the promised OS/device range. Current physical evidence covers API 36 on two phones, synthetic backend speech, and camera-off Home capture regions. It does not certify a real customer's sign-to-speech flow or active-preview recording exclusion; those acceptance tests must accompany the completed feature without persisting private frames.

No other unresolved product defect was demonstrated in this inspected scope. Store accounts/listings, accessibility acceptance, target-user validation and commercial policy review have not been established by this technical audit; their absence from the report is not certification. The original candidate's missing source is historical provenance debt, not proof that this newly tracked reconstruction is irreproducible.

## Storage handoff

The user approved using C: temporarily and moving project work to `C:\sign_to_speak`, outside `C:\xMilo`. The repository and historical app-specific evidence are retained separately there; all 18,617 file hashes matched after transfer. Existing Android signing/ADB credentials stay in their existing external locations; the xMilo project itself is unrelated and is not moved. No separate store builds are performed.

### Storage consolidation blocker

The canonical working repository is `C:\sign_to_speak\repository`; historical app-specific evidence is `C:\sign_to_speak\historical-evidence\sign-arm64-60d255d0`. Every transferred file hash matched. The old app-specific xMilo directory is gone; the unrelated xMilo project is untouched.

**The duplicate at `C:\Users\1thea\OneDrive\Documents\ChatGPT\Sign to Speak\verification` still remains.** Windows rejects moving that directory because another process holds it open. Automatic approval review separately rejected removing this verified duplicate with only “blocked by policy”; deletion never executed. No alternate deletion method was attempted. The safe remaining step is to release the old workspace lock, respecting other agents, then retire that duplicate. No private keys/credentials were moved or deleted. This is an operational storage blocker, separate from commercial readiness.
