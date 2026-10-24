# Android development CI

## Verified checkpoint (2026-10-12)

At exact commit `1472323c62fe21fb69620f36b45aa0eba1d94cf6`, native CI passed 84 Android-unit tests with zero failures/errors/skips, actual APK compilation and manifest audits; the separate unsigned emulator-app/test build also passed. See the [canonical checkpoint and immutable run/artifact links](../README.md#verified-checkpoint-2026-10-12). Native Back is repaired. These are hosted Robolectric/JVM and build results, not real emulator/device, compositor or audio-loopback acceptance. ASL inference and TTS remain unavailable. No later documentation/code revision inherits this verification.

## Scope and use

`.github/workflows/sign-android.yml` is a development-only workflow triggered manually or by a same-repository PR whose head branch is exactly `fix/android-native-foundation`. Fork PRs and other branches do not run the job. Candidate publication requires coordinating review; opening or updating the approved PR then triggers CI. It runs on the standard `ubuntu-24.04` hosted runner, one job bounded to 30 minutes, without persistent caches. Artifacts expire after three days. The source repository is public; logs and artifacts must contain only repository/build/test data, never user media or keys.

The checkout is the exact PR head SHA (not a synthetic merge commit), or the dispatch `GITHUB_SHA`, verified against `git rev-parse HEAD` and recorded alongside lockfile/wrapper hashes, run ID, runner image version, test reports and APK checksums. Do not describe a different or newer commit as tested. Every change needs a fresh run; rerunning an old workflow does not validate new code.

## Gates

1. Locked dependency install without lifecycle scripts; all Node host tests, TypeScript check, actual Java cancellation-class tests, and CI configuration/SDK-helper tests.
2. Preinstalled SDK36/build-tools36.0.0/CMake3.31.5, and exact NDK27.1.12297006. If missing, only that NDK may be installed, bounded to 180 seconds, using existing runner license records and standard input closed. There is no license-acceptance command or injected license hash. A new terms prompt, missing license, or download problem fails with an explicit blocker. Gradle automatic SDK downloading is disabled.
3. Preinstalled JDK21 is required by Robolectric4.16 for SDK36 (JDK17 remains sufficient for the standalone Java class check). Full `:app:testDebugUnitTest`, compiling production Kotlin and running Android-class tests with Robolectric's simulated framework. This is not an Android emulator, actual CameraX binding, or React Native bridge proof.
4. `:app:verifyDebugMergedPermissions` and `:app:verifyReleaseMergedPermissions`, checking the actual merged manifests, including transitive permissions. `--continue` lets both independent variant checks report even if the first fails. No release APK/AAB is built.
5. `:app:assembleDebug`, x86_64 only, with bundled JavaScript and an unsigned APK. Build success does not prove functional camera/UI behavior. Failed unit or manifest gates remain failures while other native evidence steps still run, provided host checks and SDK setup passed. APK upload requires overall success.

6. Opt-in `-PunsignedPhoneQa=true -PreactNativeArchitectures=arm64-v8a`: `:app:verifyUnsignedPhoneQaIsolation :app:verifyQaMergedPermissions :app:assembleQa`, followed by `scripts/ci-phone-qa-audit.sh`. This produces an unsigned isolated test-only ARM64 APK, not a physical-device test. The separate [emulator build workflow](../../.github/workflows/sign-emulator-build.yml) produces/audits an unsigned x86_64 app/test pair without booting or installing.

No signing configuration is changed by the workflow. No AAB, release publishing, production keys or app-store action is included.

## Remaining emulator gate and signing precondition

An unsigned APK cannot be installed. Emulator instrumentation remains a required pending gate, not a skipped or simulated success. The isolated `.qa` app and `.qa.test` instrumentation pair already build unsigned. Before runtime work, obtain authorization for compatible app/test signing using the existing development key on C, approved signed-artifact transfer and a pinned emulator system image with already accepted terms. Keys stay on C: never generate, import, download or export a signing key in cloud CI. Use emulator-generated frames only; no personal camera feed, real phones, network user data or media artifacts.

## Workflow actions

Immutable commits verified through each upstream repository on 2026-10-12:

- [actions/checkout](https://github.com/actions/checkout/commit/11d5960a326750d5838078e36cf38b85af677262)
- [actions/setup-node](https://github.com/actions/setup-node/commit/49933ea5288caeca8642d1e84afbd3f7d6820020)
- [actions/upload-artifact](https://github.com/actions/upload-artifact/commit/ea165f8d65b6e75b540449e92b4886f43607fa02)

Node 24.19.0 was verified against the [official Node release commit](https://github.com/nodejs/node/commit/cdc1b38d40cb567b7ad0b39c86addf830a0af0ae), dated 2026-08-03.

The [official Ubuntu runner inventory](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md) was checked on 2026-10-12: SDK36, build-tools36.0.0 and CMake3.31.5 were present; NDK27.1 was absent. Runner images evolve, so runtime preflight is authoritative.

## Historical implementation and run evidence

The following checkpoints preserve the original preparation and repair sequence. Their test counts and “not performed”/“next candidate” language refer to those earlier revisions, superseded by the verified checkpoint above. They are not current blockers or authority to execute a build.

## Local regression checks

From `native`, run `node --test scripts/ci-policy.test.mjs scripts/ci-sdk.test.mjs` and `bash -n scripts/ci-sdk.sh`. The policy suite parses YAML using the locked dependency tree. It mutation-tests cost, action pinning, permissions, mandatory gates, failure propagation and retention constraints. The SDK suite uses temporary fixture directories and fake installers; it downloads nothing and checks that a terms prompt receives EOF. These are focused guards, not a complete shell/workflow security audit.

Both suites were introduced before their implementations and observed failing. Those initial failures were missing-module/missing-script failures, not a demonstrated Android behavioral regression. Then all 16 focused tests passed. Host tests (13), TypeScript and the actual Java cancellation-class test (8 checks, JDK17) also passed. Gradle/native compilation, actual SDK install and a hosted workflow run have not been performed while preparing this workflow.

## First hosted run and harness correction

[Run 38251763501](https://github.com/Hatsunama/sign-to-speak/actions/runs/38251763501) checked head `a76554770e777bf009c7b069bd52fc33691b36bc` on 2026-10-12. Host checks and SDK setup passed. Actual Kotlin compilation and unsigned APK assembly succeeded, but native behavioral tests did not run: the XML reported `Android SDK 36 requires Java 21 (have Java 17)`. The SDK requirement is also explicit in [Robolectric4.16's provider](https://github.com/robolectric/robolectric/blob/robolectric-4.16/robolectric/src/main/java/org/robolectric/plugins/DefaultSdkProvider.java). CI now selects the runner's preinstalled JDK21, retaining both SDK28 and SDK36 coverage.

The actual debug merged-manifest gate failed on `SYSTEM_ALERT_WINDOW` and the app-specific `DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION`; the release task was not reached. The former is an unwanted debug capability; the latter requires inspection of its AndroidX signature-protected declaration and use before any policy decision. CI now runs the independent variant checks with `--continue`; neither failure is suppressed. Focused policy tests demonstrated the old JDK and stop-on-first-manifest-failure configuration failing before these changes. All 18 focused CI tests pass locally afterward. A new hosted run is required to validate the harness repair. No Back behavioral red or green is claimed from the first run.

[Run 38253275574](https://github.com/Hatsunama/sign-to-speak/actions/runs/38253275574) checked head `928f87713c96fcaa5dbdec5230151016d62a7fdc`. Both actual merged-manifest gates, their policy fixtures, and unsigned APK assembly passed. The Robolectric class still failed before any test method: although the job ran with `JAVA_HOME` pointing at JDK21, React Native's `JdkConfiguratorUtils.kt` explicitly configures the Kotlin JVM toolchain to 17, which also selects the unit-test launcher. The next harness correction must set the Android unit-test `javaLauncher` to 21 explicitly and assert the selected runtime, while keeping application compilation and SDK28/36 test coverage unchanged. This second failure is still not a meaningful Back regression result.

[Run 38254499136](https://github.com/Hatsunama/sign-to-speak/actions/runs/38254499136) checked head `1daf2df1efd03516871201a8198d428c7be9e56b`. The explicit launcher guard and separate plain-JUnit worker-runtime test both confirmed Java21. Both manifest gates and unsigned APK assembly passed. Of 16 Robolectric cases, six passed and ten failed: on both API28 and API36, the pending-provider Back test passed its setup preconditions and then failed the production-boundary assertion, `Native Back must cancel before RN receives Back, even when JS stalls`. These two failures are meaningful fail-before evidence for native Back cancellation. The other eight failures occurred earlier at the bound-preview fixture precondition (`actual start() must bind a Preview`), so they cannot yet prove a Back/unbind defect. The bound-preview fixture needs separate diagnosis. This establishes neither emulator coverage nor actual camera binding.

## Bounded cleanup-fault counterfactual

The next candidate contains an intentional, behavior-changing privacy repair: Activity-owned native Back cancellation and per-view cleanup-fault isolation. Cleanup that throws is never reported as confirmed hardware release; the UI explains the failure and requires a full close/reopen before starting again.

CI runs a 12-minute-bounded counterfactual before the full candidate suite, within the same 30-minute standard job. The pinned `pre-isolation.patch` constructs a cleanup-fault counterfactual while retaining native Back cancellation, the corrected provider fixture and current synchronous acquisition error handling. Its context and pinned hash must track production source edits. Restore integration tests must prove the fake Gradle command was actually reached as well as byte-exact restoration; restoration after an apply failure is insufficient. The verifier accepts exactly the two named isolation assertions on API28/36, their exact injected-exception messages and source locations. Setup, compilation, missing reports, wrong assertions and unexpected green all fail this gate.

Baseline reports and both source hash sets are retained separately. An EXIT/signal trap restores the original sources, checks their hashes and checks against HEAD before reporting restoration. Candidate tests and build gates run independently of the baseline result, but only after verified restoration. The job remains failed if either baseline or candidate fails. The eight verifier/restore-fixture tests, 20 CI policy/SDK tests, 13 host tests, TypeScript and eight Java checks pass locally. These checks do not claim either hosted counterfactual red or candidate native green.

[Run 38258095288](https://github.com/Hatsunama/sign-to-speak/actions/runs/38258095288) checked head `f50236f6e480400eb38887e3e812178e3e6ba342`. The restored candidate passed all 26 native Robolectric cases and the independent worker-runtime test, both permission gates/policy fixtures and unsigned APK assembly. The counterfactual hit all four intended isolation assertions at the exact source lines after setup succeeded, but its verifier rejected Gradle's `java.lang.AssertionError: ` XML-message prefix, which the synthetic fixture had omitted. Two counterfactual Back cases also emitted the deliberately injected cleanup exception again during teardown. The overall job correctly failed and did not publish an APK artifact.

The verifier correction is test-first against sanitized authentic run4 XML stored at `scripts/fixtures/run4-isolation-baseline.xml`. Only the exact standard AssertionError prefix may be removed; message, type, API case and source-line requirements remain exact. Multiple failures in one case now explicitly reject baseline evidence. The retained real XML with additional teardown exceptions is rejected; its unmodified primary failure records validate the authentic Gradle serialization shape. The test fixture teardown removes only the injected mock fault after assertions so the next hosted baseline must produce four clean primary assertion failures. Full candidate code is unchanged by this verifier repair. Native hardware and emulator coverage remain pending.
