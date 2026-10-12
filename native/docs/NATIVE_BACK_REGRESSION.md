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

## Native Back repair after measured red

[Run 38254499136](https://github.com/Hatsunama/sign-to-speak/actions/runs/38254499136), published head `1daf2df1`, confirmed a Java 21 worker and the same native pending-request cancellation assertion failing before the React delegate on API 28 and 36. The manifest gates/policy fixtures and unsigned APK assembly passed. Six no-camera/pause/destroy cases passed. Eight other cases failed the bound-preview fixture precondition and are not treated as evidence of an active-preview product failure.

The native repair registers camera views in an Activity-owned weak set, including requested views not yet attached. MainActivity cancels each view through the existing native stop routine before delegating Back to RN. That routine clears requested state, invalidates the PreviewLease, hides the preview and unbinds only its owned use case. Disposal unregisters the view; Activity destruction disposes remaining registered views. There is no native resume/start path. The existing RN targetSdk 36 dispatcher still routes committed Back through this override.

The bound-preview fixture boundary was incorrect: CameraX 1.4.2 defines `getInstance` on its Kotlin companion with a Java static bridge (`ProcessCameraProvider.kt` lines 909 and 947–958). Mocking only that Java bridge does not intercept Kotlin's companion call. Source was checked from Google's exact [1.4.2 sources artifact](https://dl.google.com/dl/android/maven2/androidx/camera/camera-lifecycle/1.4.2/camera-lifecycle-1.4.2-sources.jar). The test now shadows the companion entry point and counts actual acquisitions. Both pending and bound cases assert that the real native start reached the controlled provider before continuing. Preview construction and all binding/unbinding/visibility assertions remain unchanged; no product factory hook, injected Preview, loader mock or reduced assertion was added.

Two further cases cover Back before attachment and actual Activity destruction without RN lifecycle delivery. There are now eleven cases at both API levels, plus the independent worker-runtime check. These additions and the corrected bound fixture have not yet run; they do not retroactively turn the earlier fixture failures into product-red evidence.

Local candidate checks: Node/React host suite 13/13, native Java PreviewLease 8 checks, TypeScript no-emit pass. Existing web and canonical renderer are untouched. Native compilation/runtime green and actual hardware/OS-gesture checks remain outstanding for the candidate; no APK signing, device use, model or speech work was performed.

Ownership review: registration resolves the owner from `ThemedReactContext.currentActivity`, the same authority used for lifecycle binding, rather than casting a Context wrapper. The fixture now wraps the base Activity to exercise this. Each Activity owns its own weak-key set; a new two-Activity case asserts separate membership and that Back in one leaves the other's pending request intact. `WeakHashMap` expunges collected keys on ordinary map operations; deterministic GC timing is not claimed or tested. Explicit disposal removes membership immediately, asserted in the disposal case. Registration itself does not start, stop or dispose a view. Detach still stops it, and reattach re-resolves ownership but never sets requested=true; existing reattach/no-restart and disposed/rejected-start assertions remain. Repeated Back and an inactive view still delegate navigation safely.

## Cleanup fault isolation and test-first comparison

Exact CameraX 1.4.2 `ProcessCameraProvider.unbind` documents `IllegalStateException` for an off-main call and `UnsupportedOperationException` in concurrent mode (source lines 649–674). These conditions are not expected in the normal single-camera UI path, but they establish that cleanup is not a no-throw contract. The new multi-view fanout therefore isolates `RuntimeException` per view instead of allowing one failure to skip peers or RN/lifecycle delegation. VM errors are not swallowed.

Before unbinding, the existing stop routine invalidates requested state and the lease and hides preview. The Activity records a payload-free in-memory cleanup-failure count, attempts an explicit `camera_cleanup_failed` notification, and blocks further starts in that Activity. Notification failure cannot interrupt peers. Disposal unregisters in nested finally blocks, including after a prior stop error. No global `unbindAll`, camera-content logging or retained exception payload is added. An attempted unbind that throws is explicitly not proof of hardware release.

The React UI hides preview, says cleanup could not be confirmed, disables Start and directs the user to fully close and reopen the app (a new Activity resets the native fault latch). Background/resume cannot silently reset that warning. The host test verifies this behavior. There is no automatic retry/restart or unexplained dead Start button.

Two fault-injection tests bind two real Preview objects through the controlled provider, assert current leases, then make both owned unbind calls throw. They require both requests/leases/previews to be canceled/hidden, both unbind attempts to occur, RN Back or destruction to delegate, failure accounting to equal two, and teardown registration to be removed. Injecting failure for both use cases avoids dependence on weak-set iteration order. There are now 13 native cases at each of API 28/36, plus the worker-runtime check.

For actual hosted fail-before evidence, `native/tests/fixtures/pre-isolation.patch` restores only MainActivity/PrivateCameraView to the exact candidate preserved before fault isolation. It retains the native Back fix and the corrected provider fixture. `git apply --check` passed. CI must apply this patch, run only `*NativeBackPrivacyTest.cleanupFailure*`, preserve separate baseline XML, restore the patch unconditionally, then run the full candidate suite. Only the explicit isolation assertion failures after the bound/lease preconditions establish fault-regression red; harness or binding failures do not. The comparison and candidate native assertions remain unexecuted locally.

Latest local checks after isolation: Node/React 13/13 (including persistent cleanup warning/disabled Start), Java lease 8 checks, TypeScript pass. All final Android and fault-baseline results are pending hosted execution.
