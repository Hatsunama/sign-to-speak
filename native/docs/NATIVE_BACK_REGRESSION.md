# Native Back regression: test-first candidate

Status: tests prepared, Android runtime execution pending. There is no verified red or green Android run yet. Production Back/camera behavior and permission declarations have not been repaired in this candidate.

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
