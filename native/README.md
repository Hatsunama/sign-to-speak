# Sign To Speak — current Android work

The current candidate adds reviewed typed text to the existing local Flite speech backend while preserving the Hatsu renderer and original intro/camera arrangement. Camera recognition, continuous ASL translation and customer microphone captions are still absent. The recorded-video tracker/model probe is opt-in instrumentation research, not an application feature.

See the [commercial readiness plan](docs/COMMERCIAL_READINESS_PLAN.md) for the full phase sequence, dependencies and exit gates, and the [recognition research plan](docs/RECOGNITION_PLAN.md) for model contracts. Rear-default camera switching and typed-speech QA passed on both phones at the [current exact-artifact checkpoint](../qa-artifacts/rear-camera-20261025/evidence.json). Historical results below apply only to their named artifacts. Production release remains blocked.

## Historical foundation checkpoint

An isolated **React Native Android app**, not a wrapper around the existing web prototype. This milestone implements home, the canonical Hatsu background, a local thank-you page and camera **preview only**. ASL recognition, landmark inference and speech are unavailable. It does not claim interpreter capability or HIPAA certification.

## Privacy boundary

- Camera permission is requested only when Start is tapped. No microphone, network, storage or recording permission is declared.
- The native camera view stops on Activity pause, detach, disposal and explicit Stop. Late camera-provider callbacks are invalidated. Returning to foreground does not restart it.
- No signing frames, landmarks, transcripts or session history are saved or uploaded. This build has no analyzer/model and creates no app-owned image buffers. It also has no speech engine or system-voice fallback.
- The Activity sets Android's secure-window flag before content. Audio capture is controlled separately: the manifest opts out and API 29+ requests `ALLOW_CAPTURE_BY_NONE` before React startup. If policy setup/readback fails, the first render shows only an unavailable state and native camera start is denied. API 26–28 remain preview-only with documented platform limits. See [capture protections and the required independent audio-loopback matrix](docs/CAPTURE_PRIVACY.md). No real screen/audio capture acceptance or future TTS protection is claimed.
- The canonical Hatsu renderer stores only its existing local visual seed. Its static animation diagnostic is not signing/session content. Neither is transmitted.
- Future model and speech integration is blocked on licensing, owner-hosted pinned artifacts and offline/privacy validation. A voice's “offline” flag alone is not enough.

## Reproduce host checks

Use Node 24 and an official JDK 17 or newer. From this directory:

```sh
npm ci --ignore-scripts
npm test
npm run typecheck
JAVA_HOME=/path/to/jdk ./tests/run-java-tests.sh
```

The React test exercises the actual App component with mocked Android/RN hosts; it is not an emulator test. The Java test executes the actual native cancellation-token class independently of Android. Structural tests are guards, not runtime proof.

## Native build prerequisites and verification

The pinned official React Native 0.86.3 template specifies Gradle 9.3.1, Android SDK/build-tools 36, NDK 27.1.12297006 and Kotlin 2.1.20. Android API 26 is the app minimum because the canonical motion-policy host uses `ValueAnimator.areAnimatorsEnabled()`.

## Verified checkpoint (2026-10-12)

At exact [commit `1472323c62fe21fb69620f36b45aa0eba1d94cf6`](https://github.com/Hatsunama/sign-to-speak/commit/1472323c62fe21fb69620f36b45aa0eba1d94cf6) (tree `9b8d74169d85bcd8125f44ba7dc09b8ceb5c7d3d`):

- [Native CI 38306178707](https://github.com/Hatsunama/sign-to-speak/actions/runs/38306178707) passed actual Android/Kotlin compilation, unsigned APK builds and merged-permission gates. Its Android-unit XML reports **84 tests, 0 failures, 0 errors, 0 skipped**: 60 native Back cases, 11 capture-policy startup cases, 6 capability-module cases, 6 secure-window lifecycle cases and 1 worker-runtime check. These are hosted Robolectric/JVM tests, not emulator or physical-device execution.
- [Unsigned emulator-artifact CI 38306178700](https://github.com/Hatsunama/sign-to-speak/actions/runs/38306178700) passed building/auditing the isolated x86_64 app/instrumentation pair. It did not boot an emulator. The actual ARM64 phone-QA and x86_64 emulator-app binary manifests both opt out of audio playback capture.
- Evidence artifacts: [native 11772393858](https://github.com/Hatsunama/sign-to-speak/actions/runs/38306178707/artifacts/11772393858) and [emulator build 11772233933](https://github.com/Hatsunama/sign-to-speak/actions/runs/38306178700/artifacts/11772233933). Artifact retention is three days; run/commit links identify the immutable checkpoint even after downloads expire.

Native Back cancellation is implemented and covered by the passing suite. That checkpoint did not include real emulator/device camera, screen-compositor or playback-loopback acceptance. Limited later physical evidence is recorded below. ASL inference and TTS remain unavailable. These results apply only to the exact commit above, not to a later documentation or code revision. Unsigned artifacts are test inputs, not an install recommendation or release.

## Limited physical QA checkpoint (2026-10-12)

Exact tested source: [482605debdbd7fa465105ea6edbe07b2bdcb01a7](https://github.com/Hatsunama/sign-to-speak/commit/482605debdbd7fa465105ea6edbe07b2bdcb01a7), tree `7b0a4eed8551c4dc06f16ae2c5f7080210a74a53`. Its [native CI 38310944788](https://github.com/Hatsunama/sign-to-speak/actions/runs/38310944788) and [unsigned emulator-artifact CI 38310944780](https://github.com/Hatsunama/sign-to-speak/actions/runs/38310944780) passed; the native XML again reports 84 tests with zero failures/errors/skips. Neither workflow ran an emulator.

The isolated ARM64 `.qa` APK was subsequently signed locally with the existing approved development identity and its installed bytes matched on Seeker and Galaxy S24:
- Signed APK SHA-256: `c53ec92f4e5e3ef9b2aa009315dbbaa7577fc2a9217aec93c27f166ca36576ff`
- Signing certificate SHA-256: `2a0d37dc62eebed39bd08a9238c9255e26eb52a00143a02e0c6c473489985a21`

Cold starts were 253 ms on Seeker and 152 ms on S24 in this bounded check. Home/Hatsu-page navigation, page Back, system Back, portrait/landscape and background/return were responsive. Live hierarchies changed Home → Thank you/Back to Home → Home; background/return retained the same process. Bounded QA logs contained no fatal crash or ANR. CAMERA remained denied; neither camera nor microphone was invoked.

Six app-only screenshots across those devices were entirely black while independent live hierarchies and ready secure windows established active app content. This supports **screenshot capture exclusion for those sampled states**, not visual appearance or animation correctness. See [physical screenshot evidence and limits](docs/CAPTURE_PRIVACY.md#limited-physical-screenshot-checkpoint-2026-10-12).

This limited physical result belongs only to the exact tested source and signed APK above. A later documentation commit is not itself device-tested. Actual visual animation, camera runtime, emulator runtime matrix, screen-recording matrix, playback loopback, ASL/TTS and release acceptance remain unverified or unavailable. Black screenshots do not establish audio protection.

Host/structural tests alone do not prove APK compilation or native camera/rendering behavior. Obtain the coordinated build slot and resource approval before downloading tooling or running Gradle. Approved hosted CI uses JDK 21 for Robolectric API 36; the standalone Java check can use JDK 17.

After prerequisites and resource approval, the supported unsigned x86_64 debug-build command (not emulator execution) is:

```sh
cd android
./gradlew --no-daemon --max-workers=1 -Pandroid.builder.sdkDownload=false \
  -PreactNativeArchitectures=x86_64 :app:assembleDebug
```

All variants bundle JavaScript and have null signing configuration. No key is included or generated by this project configuration. Keys stay on the authorized local C computer; do not create, import or export signing keys in cloud CI. An unsigned APK cannot be installed until the separately authorized signing step. Do not add a new key or build an AAB during this foundation iteration.

## Provenance

- Existing web source retained unchanged at base `e52813a3ccf34cb5e2df40548dfc68e9587e2225`.
- Android shell adapted from official npm `@react-native-community/template@0.86.3`; dependencies pinned by `package-lock.json`.
- Canonical `Hatsunama/Hatsu-Animation` commit `0f4bb7512a074eabd704481fb06725e1b84ac928`. Both upstream files are exact copies, checked by SHA-256 tests. No license is invented; owner-directed reuse is the authority.
- Hatsu lifecycle host adapted from the verified Caption Action workspace implementation. Limited physical background/return behavior is recorded above; full lifecycle and visual-animation acceptance remains pending.
- Local thank-you/button pattern verified against Caption Action commit `6ecc3377d10b5ce32aa41c1fa7ca4735bd513cb5`. App name and one app-specific body sentence are adapted to Sign To Speak. Patreon text is local, non-clickable text; no payment flow is created.

See [the foundation design](docs/FOUNDATION.md) for the remaining test gates and model requirements.

## Native Back regression

Native Back synchronously cancels owned camera requests before React delegation, including stalled JavaScript. Hosted Robolectric coverage passes at the exact checkpoint above; camera hardware release and predictive-gesture edge cases still need emulator/device validation. Limited non-camera system-Back navigation is recorded above. See [current status and preserved test-first history](docs/NATIVE_BACK_REGRESSION.md).
