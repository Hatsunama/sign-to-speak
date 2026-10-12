# Android development CI

## Scope and use

`.github/workflows/sign-android.yml` is a development-only workflow triggered manually or by a same-repository PR whose head branch is exactly `fix/android-native-foundation`. Fork PRs and other branches do not run the job. Candidate publication requires coordinating review; opening or updating the approved PR then triggers CI. It runs on the standard `ubuntu-24.04` hosted runner, one job bounded to 30 minutes, without persistent caches. Artifacts expire after three days. The source repository is public; logs and artifacts must contain only repository/build/test data, never user media or keys.

The checkout is the exact PR head SHA (not a synthetic merge commit), or the dispatch `GITHUB_SHA`, verified against `git rev-parse HEAD` and recorded alongside lockfile/wrapper hashes, run ID, runner image version, test reports and APK checksums. Do not describe a different or newer commit as tested. Every change needs a fresh run; rerunning an old workflow does not validate new code.

## Gates

1. Locked dependency install without lifecycle scripts; all Node host tests, TypeScript check, actual Java cancellation-class tests, and CI configuration/SDK-helper tests.
2. Preinstalled SDK36/build-tools36.0.0/CMake3.31.5, and exact NDK27.1.12297006. If missing, only that NDK may be installed, bounded to 180 seconds, using existing runner license records and standard input closed. There is no license-acceptance command or injected license hash. A new terms prompt, missing license, or download problem fails with an explicit blocker. Gradle automatic SDK downloading is disabled.
3. Preinstalled JDK21 is required by Robolectric4.16 for SDK36 (JDK17 remains sufficient for the standalone Java class check). Full `:app:testDebugUnitTest`, compiling production Kotlin and running Android-class tests with Robolectric's simulated framework. This is not an Android emulator, actual CameraX binding, or React Native bridge proof.
4. `:app:verifyDebugMergedPermissions` and `:app:verifyReleaseMergedPermissions`, checking the actual merged manifests, including transitive permissions. `--continue` lets both independent variant checks report even if the first fails. No release APK/AAB is built.
5. `:app:assembleDebug`, x86_64 only, with bundled JavaScript and an unsigned APK. Build success does not prove functional camera/UI behavior. Failed unit or manifest gates remain failures while other native evidence steps still run, provided host checks and SDK setup passed. APK upload requires overall success.

No signing configuration is changed by the workflow. No AAB, release publishing, production keys or app-store action is included.

## Remaining emulator gate and signing precondition

An unsigned APK cannot be installed. Emulator instrumentation remains a required pending gate, not a skipped or simulated success. Before adding that stage, review an isolated test application ID distinct from `com.xmilo_at_your_side.sign_to_speak`, an ephemeral CI-only development signing key, compatible app/test APK signing, instrumentation runner/dependencies and a pinned emulator system image with already accepted terms. Never import user/private production keys or create a production signing identity. Use emulator-generated frames only; no personal camera feed, real phones, network user data or media artifacts.

## Workflow actions

Immutable commits verified through each upstream repository on 2026-10-12:

- [actions/checkout](https://github.com/actions/checkout/commit/11d5960a326750d5838078e36cf38b85af677262)
- [actions/setup-node](https://github.com/actions/setup-node/commit/49933ea5288caeca8642d1e84afbd3f7d6820020)
- [actions/upload-artifact](https://github.com/actions/upload-artifact/commit/ea165f8d65b6e75b540449e92b4886f43607fa02)

Node 24.19.0 was verified against the [official Node release commit](https://github.com/nodejs/node/commit/cdc1b38d40cb567b7ad0b39c86addf830a0af0ae), dated 2026-08-03.

The [official Ubuntu runner inventory](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md) was checked on 2026-10-12: SDK36, build-tools36.0.0 and CMake3.31.5 were present; NDK27.1 was absent. Runner images evolve, so runtime preflight is authoritative.

## Local regression checks

From `native`, run `node --test scripts/ci-policy.test.mjs scripts/ci-sdk.test.mjs` and `bash -n scripts/ci-sdk.sh`. The policy suite parses YAML using the locked dependency tree. It mutation-tests cost, action pinning, permissions, mandatory gates, failure propagation and retention constraints. The SDK suite uses temporary fixture directories and fake installers; it downloads nothing and checks that a terms prompt receives EOF. These are focused guards, not a complete shell/workflow security audit.

Both suites were introduced before their implementations and observed failing. Those initial failures were missing-module/missing-script failures, not a demonstrated Android behavioral regression. Then all 16 focused tests passed. Host tests (13), TypeScript and the actual Java cancellation-class test (8 checks, JDK17) also passed. Gradle/native compilation, actual SDK install and a hosted workflow run have not been performed while preparing this workflow.

## First hosted run and harness correction

[Run 38251763501](https://github.com/Hatsunama/sign-to-speak/actions/runs/38251763501) checked head `a76554770e777bf009c7b069bd52fc33691b36bc` on 2026-10-12. Host checks and SDK setup passed. Actual Kotlin compilation and unsigned APK assembly succeeded, but native behavioral tests did not run: the XML reported `Android SDK 36 requires Java 21 (have Java 17)`. The SDK requirement is also explicit in [Robolectric4.16's provider](https://github.com/robolectric/robolectric/blob/robolectric-4.16/robolectric/src/main/java/org/robolectric/plugins/DefaultSdkProvider.java). CI now selects the runner's preinstalled JDK21, retaining both SDK28 and SDK36 coverage.

The actual debug merged-manifest gate failed on `SYSTEM_ALERT_WINDOW` and the app-specific `DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION`; the release task was not reached. The former is an unwanted debug capability; the latter requires inspection of its AndroidX signature-protected declaration and use before any policy decision. CI now runs the independent variant checks with `--continue`; neither failure is suppressed. Focused policy tests demonstrated the old JDK and stop-on-first-manifest-failure configuration failing before these changes. All 18 focused CI tests pass locally afterward. A new hosted run is required to validate the harness repair. No Back behavioral red or green is claimed from the first run.

[Run 38253275574](https://github.com/Hatsunama/sign-to-speak/actions/runs/38253275574) checked head `928f87713c96fcaa5dbdec5230151016d62a7fdc`. Both actual merged-manifest gates, their policy fixtures, and unsigned APK assembly passed. The Robolectric class still failed before any test method: although the job ran with `JAVA_HOME` pointing at JDK21, React Native's `JdkConfiguratorUtils.kt` explicitly configures the Kotlin JVM toolchain to 17, which also selects the unit-test launcher. The next harness correction must set the Android unit-test `javaLauncher` to 21 explicitly and assert the selected runtime, while keeping application compilation and SDK28/36 test coverage unchanged. This second failure is still not a meaningful Back regression result.
