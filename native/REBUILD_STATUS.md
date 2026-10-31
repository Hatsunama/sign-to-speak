# Historical local speech integration reconstruction

Current follow-up work adds a typed speech screen and a research-only video probe. See [the current phase plan](docs/RECOGNITION_PLAN.md). The source and artifact results below document the earlier backend reconstruction and do not verify the new customer flow.

This work starts from verified follow-up foundation `10bc6fa8d4d5baa4e44c3fcebd75b2d675ba3b07` (foundation `78c71bd36bbd0f04c86bd85e4b795da35bcd56f7`). It reconstructs the missing native speech/session integration. It is not a recovery of the unpublished 477-file candidate, and does not reproduce that candidate's source inventory or APK.

## Scope

The existing Home/Thank you UI, text, styles and renderer are preserved. The preview layout repair restores the intended Start/Stop behavior. No ASL recognizer, translation pipeline, transcript, speech input screen, or automatic speech is added. Speech is a native backend capability tested with synthetic text through its production React module. The existing visible statement that translation is unavailable remains accurate.

Flite and the English SLT voice are compiled from the reviewed public component source at `6999e43b16c41f574b9ad3914554c7ea71fa84f88`, pinned to upstream `e9e2e37c329dbe98bfeb27a1828ef9a71fa84f88`. Synthesis and playback use device resources only, without service APIs or runtime model downloads. API 26–28 keep preview support and reject speech because the required playback-capture policy is unavailable. API 29+ speech additionally requires a resumed Activity and verified capture policies.

## Repairs and impact checks

- Replaced the empty speech stop connection with generation tokens and native Back/pause/destroy cancellation. Tests cover mounted React cancellation, stale begin/token callbacks, native lifecycle, cleanup faults and recovery.
- Bounded native requests, mutable input and PCM ownership. Dispatch allocation failure and delayed rejected work are regression tested; fatal errors propagate after cleanup. Cleanup uncertainty blocks subsequent speech for the process lifetime.
- Added a capture-protected PCM player with audio focus, bounded nonblocking writes and playback-head drain. Failure/cancellation and persistent cleanup-fault tests exercise the actual Android adapter with controlled AudioTrack dependencies.
- Preserved React Native default OnLoad/autolinking while adding a separate `private_tts` library. Read-only source preparation checks verify upstream and generated bodies during CMake configuration. APK audits require the actual speech library, bundled JS and byte-identical notices.

## Verified code and runtime boundary

Tested code: `4ec7c8a38d9444db4d6ef2b3506faa27b0b5a292`; required hosted run [40119903979](https://github.com/Hatsunama/sign-to-speak/actions/runs/40119903979) succeeded. The full required checks and 118 Android unit tests passed. The exact locally signed QA APK SHA-256 is `6f5d30e481781e5b7cc5dca13f47550143277ac159e27f84b5a01846afa3f7e1`.

That exact artifact was cold-started on both physical phones (S24 and Seeker, API 36). Production speech/JNI/output, cancellation/recovery and portrait/landscape preview journeys passed. Independent internal-audio capture, OS screenshot and fresh screen-recording-frame checks passed with positive controls. Visual captures measured camera-off Home regions excluding independently identified system overlays; active-preview recording and full-app 16 KiB runtime are not certified by those results.

Two product regressions found by cascading checks were repaired: active playback failed to recheck privacy eligibility, and late CameraX texture children were not laid out under Fabric. Test/CI fixture failures and their corrections are separately documented in the [complete report](../qa-artifacts/rebuild-local-speech-20261024/FINAL_REPORT.md).

The Home return JSX/styles and native renderer remain unchanged. The app still has no ASL recognition or translated-speech customer flow. All speech evidence uses synthetic text through the real production module.

**Commercial release remains blocked.** Production signing is unconfigured, and the verified APK is isolated `.qa`, `testOnly`, signed with the existing debug certificate. Separate Google Play AAB/Solana APK builds are deliberately deferred until commercial readiness testing. Their future private keys must differ and remain outside GitHub. No production key was created, exported or uploaded. Each final shipping artifact requires its own exact-artifact verification.

The report lists all demonstrated unresolved commercial blockers and evidence gaps. Later documentation, probe and Git-ignore updates do not identify a newly built app artifact. Existing keys/credentials are protected; the verified working copy and historical evidence are at `C:\sign_to_speak`. A locked duplicate remains in the old OneDrive workspace; see the report's storage blocker.
