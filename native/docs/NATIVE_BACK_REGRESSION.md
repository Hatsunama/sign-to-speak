# Native Back regression: test-first candidate

Status: initial hosted Kotlin/APK compilation passed; the native test harness failed before assertions because SDK 36 requires JDK 21. There is no verified Back runtime red or green yet. The merged debug permission gate genuinely failed, and a scoped permission repair is now prepared below. Production Back/camera code and the existing Back regression remain unchanged.

## Defect and required behavior

The existing JavaScript BackHandler stops its session, but JavaScript may be stalled. MainActivity currently inherits ReactActivity.onBackPressed. React Native may consume Back by emitting a JavaScript event without pausing the Activity. PrivateCameraView therefore retains both an active Preview and a pending provider request in that interval. Existing Activity-pause/view-detach cleanup does not close this route.

The fix must cancel pending/current camera leases and synchronously unbind only owned use cases before React receives Back. Repeated Back and an absent camera must be safe. Resume/reattach must not restart preview without a new explicit request. Preserve the canonical renderer and preview-only boundary.

## Runtime regression suite

`android/app/src/test/java/com/xmilo_at_your_side/sign_to_speak/NativeBackPrivacyTest.java` runs the actual MainActivity and PrivateCameraView under Robolectric 4.16 at API 28 and 36. Only the React delegate/JavaScript boundary and CameraX provider are mocked. The React delegate consumes Back and does not pause or navigate, modeling stalled JavaScript. A controllable provider future exercises the actual asynchronous start callback.

Cases (eight tests at two API levels):
- Pending provider canceled before the React delegate sees Back; completing it cannot bind.
- Bound Preview hidden, cleared and unbound before the React delegate sees Back.
- Repeated Back unbinds once; resume does not restart.
- No active camera still delegates navigation safely.
- Host pause cancels pending acquisition; resume does not restart.
- Host destruction cancels pending acquisition and rejects a later start.
- Detach unbinds; reattach does not restart.
- Repeated disposal is safe and rejects later starts.

API 36 uses the actual OnBackPressedDispatcher path registered by the pinned ReactActivity for targetSdk 36. API 28 calls the Activity's legacy Back entry point. This covers committed predictive-Back dispatch at the AndroidX/RN boundary, not an actual OS gesture animation or gesture cancellation.

The fixture asserts a resumed Activity, attached native view, pending acquisition or actual Preview binding before checking teardown. Failure in setup, dependency resolution, compilation, reflection or a mock is a harness/build failure, not proof of this product bug. Only failures of the teardown assertions after those preconditions establish the intended red. The lifecycle control tests should remain green.

Run only in the approved CI environment, not on a personal computer:

```
cd native/android
./gradlew --no-daemon --max-workers=1 -Pandroid.builder.sdkDownload=false -PreactNativeArchitectures=x86_64 :app:testDebugUnitTest
```

No emulator, signing key, APK installation or Hermes execution is needed for this suite. Android resource processing and Android/Kotlin compilation are still required. Unit tests use JUnit 4.13.2, Robolectric 4.16 (supports API 36), Mockito 5.18.0 and Guava 33.4.8-jre. Actual hardware-camera release, permission dialogs/revocation, RN/Hermes integration and OS predictive gestures remain emulator/device gates.

## Merged permission gate

`VerifyMergedPermissions` in app/build.gradle consumes AGP's `SingleArtifact.MERGED_MANIFEST`, including variant/transitive dependency manifests. It parses all three permission element forms and requires exactly CAMERA. Both variant tasks are wired to `check`:

```
./gradlew --no-daemon --max-workers=1 -Pandroid.builder.sdkDownload=false :app:verifyDebugMergedPermissions :app:verifyReleaseMergedPermissions
```

The pinned React Native debug dependency declares SYSTEM_ALERT_WINDOW. The main source manifest alone cannot establish final permissions. The new gate is expected to expose it and any other merged additions. Do not label that expectation a measured merged-manifest result until Gradle executes. An AndroidX internal signature permission may also appear; review actual merger provenance before deciding how to preserve the strict privacy contract. Never silently expand the allowlist or remove security protections to get green.

The build selects CMake 3.31.5, matching the approved standard Ubuntu runner image. SDK downloads remain disabled by CI. All variants remain unsigned; no product permission is changed.

## Local evidence for this candidate

- Existing Node/React host suite: 13/13 passed after adding the tests/build gate.
- TypeScript no-emit check: passed.
- Byte comparison with the preserved foundation archive: all 24 production UI/Android/manifest/session files unchanged. Git diff for tracked web source: empty.
- Attempted existing Java lease test with `/usr/lib/jvm/java-21-openjdk-amd64`: blocked because that path has no `bin/javac`. This is not a failing product test and no tooling was installed.
- New Gradle test suite, manifest gate and APK compilation: not run locally; reserved for approved CI.
- Production Kotlin/Java, manifest, canonical renderer and existing web source: unchanged.

## Stop-before-repair gate

Publish this test-only candidate for reviewed CI. If the harness fails, repair the harness without claiming a product red. Once cancellation assertions demonstrably fail against unchanged production, record the exact commit/run and results, implement the native repair, then rerun the same suite plus merged-manifest gates and compilation. Preserve both before/after evidence. Do not substitute missing-file or source-pattern checks for runtime red.

References: [Robolectric compatibility](https://robolectric.org/compatibility_table/), [Android manifest merging](https://developer.android.com/build/manage-manifests), and pinned local RN ReactActivity.java.

## Hosted permission failure and scoped repair (2026-10-12)

Run [38251763501](https://github.com/Hatsunama/sign-to-speak/actions/runs/38251763501), commit `a7655477`, compiled Kotlin and assembled the unsigned APK. The test class did not execute its behavioral cases: `DefaultSdkProvider` rejected Java 17 for SDK 36. This is a harness failure, not native Back evidence. CI is being corrected to JDK 21 without changing the Back regression.

The actual debug merged-manifest allowlist failed. Its merger report attributes SYSTEM_ALERT_WINDOW to react-android 0.86.3 debug (lines 291–295), and the internal receiver declaration/use to androidx.core:core:1.13.1 (lines 359–375). The release gate was not reached on that first run.

The candidate repair adds only a `tools:node="remove"` marker for SYSTEM_ALERT_WINDOW to the main manifest. Development support is already disabled and JS is bundled. It does not remove AndroidX's receiver protection.

AndroidX's custom permission is defined as `com.xmilo_at_your_side.sign_to_speak.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION`, protected by `signature`, and requested by the same application. `ContextCompat.registerReceiver` uses this app-scoped permission to enforce the non-exported boundary on Android before API 33 when no explicit broadcast permission is supplied; its permission check can throw if the declaration/use is removed. It grants no internet, microphone, media, recording or storage access. The expected security consumer is AndroidX's compatibility implementation; no claim is made that a current foundation screen actively registers such a receiver.

The revised gate checks the exact package, exactly CAMERA plus this exact internal request, exactly one matching signature declaration, and an unconditional normal `uses-permission` form for the internal permission. It rejects other declarations, grant-expanding flags, other namespaces and unrelated permissions. No wildcard, suffix-only exception or generic signature permission allowance is used.

`:app:verifyMergedPermissionPolicyTests` executes the same validator against one accepted manifest and 30 rejected fixtures: wrong package/XML namespace/permission namespace, missing or expanded protection, missing declaration/use, conditional or duplicate use, an unrelated custom declaration, and overlay/network/microphone/storage/media permissions in all three permission element forms. Both actual merged-manifest gates depend on these regression fixtures. The fixtures have not yet executed; Gradle execution is reserved for CI. The post-edit source host suite passed 13/13.

Security reference: [AndroidX ContextCompat implementation](https://android.googlesource.com/platform/frameworks/support/+/53bce536bce3e861b1ec75d900704c563f84fab9/core/core/src/main/java/androidx/core/content/ContextCompat.java), especially `obtainAndCheckReceiverPermission` and `Api26Impl.registerReceiver`. This is an upstream implementation reference; exact dependency provenance and signature declaration come from this build's merger report.

## Explicit test-worker launcher correction

Hosted run 38253275574 at `928f8771` passed both merged-manifest gates, the permission-policy fixture regression and unsigned APK assembly. Back cases still did not execute: RN's `JdkConfiguratorUtils` explicitly selects `kotlinExtension.jvmToolchain(17)`, which also supplied the unit-test launcher despite CI's JDK 21 environment.

The follow-up sets `javaLauncher` to Java 21 only inside Android `unitTests.all`. Production Java/Kotlin compilation remains under RN's Java 17 configuration. A task-time assertion logs the selected executable and version; a separate plain-JUnit `UnitTestRuntimeTest` asserts and records the actual forked worker's Java specification/runtime/home without depending on Robolectric initialization. The existing SDK 28/36 Back suite is byte-for-byte unchanged. This correction has not been run locally; its launcher and behavior must be verified by the next hosted run.
