# Sign To Speak emulator matrix proposal

Status: prepared test design only; no emulator run, Gradle run, source publication, or workflow dispatch has occurred.

## Source and representative matrix

Production candidate: ac75886c449902f14019964425f24f7da877250e (tree e6d9fc86b9d32f5f60d1a74878ea45b3d1ec7a12). Preserve Kotlin/Java/React app implementation; additions restricted to test source, opt-in test variant, scripts and workflow.

- API26 Google APIs x86_64, compact phone profile, 4 KB expected. This is the application's actual minimum SDK.
- API35 Google APIs x86_64, phone profile, 4 KB expected.
- API36 Google APIs x86_64, tablet profile, 4 KB expected. This covers the app's target SDK, not a claim about the latest Android release.
- API35 Google APIs experimental 16 KB x86_64, phone profile, 16 KB expected.

Each profile must run the complete journey separately from a fresh install in portrait and landscape. Rotation loops also visit both orientations while actively streaming generated emulator camera frames. Profiles must retain their normal density; do not reduce density merely to make controls visible. Scroll to controls, fail on inaccessible controls. This is representative coverage, not every physical device.

## Hard gates

Public repository only; standard ubuntu-24.04 only; bounded jobs with at most two concurrent cells; no larger or private runners. Existing writable KVM must be checked before emulator downloads. No chmod, usermod, access changes, or unapproved software-emulation fallback. Earlier Caption run 38257767682 software fallback did not boot within 10 minutes. Do not repeat that as a viable result.

Exact SDK package candidates must exist in current official sdkmanager listing before installation; record installed revisions and source.properties. Package identifiers: system-images;android-26;google_apis;x86_64, system-images;android-35;google_apis;x86_64, system-images;android-36;google_apis;x86_64, system-images;android-35;google_apis_ps16k;x86_64. Installation stdin is closed; never accept new license terms.

Fresh emulator-5554 only, ro.kernel.qemu=1, no additional adb devices. Record SDK, fingerprint, ABI, uname, getconf PAGE_SIZE and PAGESIZE when supported. The 16 KB cell requires PAGE_SIZE=16384 and the same result from Os.sysconf(_SC_PAGESIZE) within the running app process. x86_64 16 KB is simulated application page-size coverage, not native ARM64 hardware proof. Confirm libhermesvm.so, libreactnative.so and libappmodules.so actually mapped in the tested app process; alignment and image labels alone are not runtime proof.

## Signing contract

Keys remain on C. Build unsigned x86_64 release-like, bundled, non-debuggable .qa app with testOnly=true, and unsigned .qa.test instrumentation APK targeting .qa. Disable AGP default-debug signing fallback explicitly; require both resolved app and instrumentation variants to have null signing configuration and signingReady=false before any build task executes. Block ValidateSigningTask/key generation/device tasks, while permitting harmless write*SigningConfigVersions metadata. Verify binary manifests, native ABI, no APK signatures before transfer. Existing unsigned debug artifact 11766324377 has production package and does not meet this isolated contract; existing isolated artifact 11766254410 is ARM64 only.

The existing C development key signs both exact APKs; return SHA256 for unsigned/signed bytes and matching certificate fingerprint. No key generation, key download, cloud signer, or key export. An explicitly marked public test-only artifact branch may carry signed test artifacts only after explicit review. No release, store deployment or installation recommendation.

## Runtime journey and evidence

Actual bundled React/Hermes UI and canonical Hatsu host, no replacement camera provider/React host. OS camera denial then grant, generated front camera STREAMING, Stop, OS Back, Home/background and launcher resume, two round-trip rotations, thank-you navigation and repeated Back transitions, Activity destroy/reopen, no implicit camera restart. Camera observations read lease/provider/binding/stream state and OS camera availability. No physical camera, ASL, speech, accuracy or HIPAA claims.

Only fixed assertion stage labels and bounded failure diagnostics are artifacts; no screenshots/frame capture or guest data images. Preserve FLAG_SECURE. Runtime test must report complete expected stages with zero skipped/failed tests. Pull installed app and test APKs and byte-compare to signed input. Capture linker/load/crash classification without claiming a visual design audit.

## Current blockers

Unsigned pair generation, C signing and signed artifact input. Build/workflow publication is approved; signed artifact transfer and runtime execution remain separately gated. Current public-runner KVM access unverified; no successful Sign guest boot. No test result should be described as passed until an actual runtime produces it.

Sources checked 2026-10-12:
- https://developer.android.com/guide/practices/page-sizes
- https://developer.android.com/studio/run/emulator-commandline
- https://github.com/google/android-emulator-container-scripts/blob/master/emu/emu_downloads_menu.py
- https://docs.github.com/en/actions/reference/runners/github-hosted-runners
