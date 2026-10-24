# Playback cleanup follow-up - 24 October 2026

## Verification status

Implementation code commit: `d6ff1cb3e994cf0accf55fec2844f897a87fd08a`.

Final required CI run: [40132971778](https://github.com/Hatsunama/sign-to-speak/actions/runs/40132971778) passed. Independently downloaded raw XML contains **122 Android tests, zero failures/errors/skips**. The full host suite, TypeScript, Java checks, two native tests, 344 JNI checks, permission/isolation gates, and unsigned APK builds passed. The deliberate cleanup counterfactual still produced its four expected failures and restored exact candidate bytes.

**Required two-phone verification is complete for this QA artifact:** Samsung S24 and Seeker both cold-started the exact signed app/test hashes and passed five portrait integration tests plus the landscape camera test. The user reconnected Seeker and Home readiness was independently verified before use. Both phones returned Home after their tests and their original rotation settings were restored. Production release remains blocked by the commercial-readiness items below; no shipping release or installation recommendation is made. Store builds remain deferred.

## Exact final artifact and physical evidence

- Unsigned app SHA-256: `d29ca04a4873d14c881b117d8c5f6e92662a70fbc53c17afe8dc8b16a80ae2f7`.
- Signed QA app SHA-256: `4e1b6c26b3f8fe6ca445133fbe2bf980711371685497124073330c9979ffb27b`.
- Signed instrumentation SHA-256: `5053f46d60ee3fda77be3285a89169842444bca2afce1669fca24ebb9b20cd23`.
- Existing debug certificate SHA-256: `2a0d37dc62eebed39bd08a9238c9255e26eb52a00143a02e0c6c473489985a21`. Key bytes were checked unchanged; no key was generated, copied, exported or uploaded. Signed ZIP payloads match unsigned payloads, signatures verify, and APK/ELF alignment checks pass. This does not establish full-app 16 KiB runtime.
- Packaged manifest is `.qa`, testOnly, non-debuggable, with no INTERNET permission. Bundled JavaScript, voice/native library and notices are present. The native speech library is byte-identical to the previously inspected library; its diagnostics documentation change did not alter native behavior.
- Samsung S24 (API 36, 4096-byte pages) installed app/test hashes match the signed receipts. A COLD start passed; **five portrait integration tests and the landscape camera test passed**. These exercise real registered bridge/JNI synthesis, protected AudioTrack progress/drain, immediate track state after cancellation, stale/input/lifecycle cancellation, and explicit recovery. No audio volume was changed; human audibility was not separately certified. Rotation was restored and the phone returned Home.
- Seeker (API 36, 4096-byte pages) was independently checked connected and at Home, then installed the same exact signed app/test hashes. Its COLD start, **five portrait integration tests and landscape camera test passed**. This includes the new real AudioTrack cancellation-return check and recovery. Original rotation was restored and final foreground was Home. `evidence/final/phones/seeker-verification.json` and raw instrumentation output supersede the earlier disconnection blocker; `seeker-readiness.json` is retained as a historical observation of that interruption.
- Raw receipts are under `evidence/final/`; CI metadata is `evidence/ci-run.json`. No application/build/runtime-test source changed after the tested code commit. Later report/evidence commits do not create a different app artifact.

The packaged JS bundle, manifest and ordinary appearance assets match the old QA APK. The comparison found two generated entries changed: ART baseline profile and the resource table. A decoded resource-table comparison shows only React Native's generated developer-server IP changed; development support is disabled and the package has no INTERNET permission. This is build metadata, not a new active network path or UI change. `resources-diff.txt` records the comparison. A Windows default-encoding error in the ad hoc comparison was corrected by decoding/writing UTF-8; no application or artifact was modified.

## What broke and what changed

1. **Abort teardown dropped ownership before silence (P2, proven schedule).** `ProtectedPcmPlayer.play` cleared `activeTrack` before stop/release, so concurrent cancellation could return while queued audio remained active. Aborted started playback now attempts pause/flush while holding the ownership lock, then detaches the track. Stop/release remain outside the lock so cancellation does not wait for slow teardown. Natural playback that has fully drained skips pause/flush.
2. **The first repair still disabled focus callbacks too early (same P2).** A follow-up audit found `focusActive=false` before the lock. The worker could be suspended there and ignore focus loss while queued audio remained active. The final flag move occurs inside the lock after the silence attempt. This is a continuation of the original ownership defect, not another unrelated finding.
3. **Failed focus-abandonment return was ignored (P3, conditional contract defect).** A failed return after granted focus could produce `COMPLETED` without poisoning the owner. Granted focus now requires successful abandonment; failure uses the existing cleanup-fault latch, session `CLEANUP_FAILED`, and process fault propagation. Denied, never-granted focus is an admission failure and does not fault merely because abandonment returns FAILED. No real phone occurrence of the FAILED return has been established.
4. **Native documentation promised absolute silence (P3 cleanup).** The README now distinguishes suppressed Flite text diagnostics and no intentional input/PCM logging from retained Android NDK unwind diagnostics. No speech-payload logging leak was established, and no exception/unwind dependency was removed.

No application UI, camera, renderer, dependency, permission or build configuration changed. The only production code file changed is `ProtectedPcmPlayer.java`. `evidence/preserved-source.json` checks exact Git bytes for the UI, renderer and camera. Required behavior changes are confined to abort cancellation and error reporting.

## Fail-before evidence and cascading verification

- Initial behavioral RED: [run 40131286650](https://github.com/Hatsunama/sign-to-speak/actions/runs/40131286650), source `5421f477756b5f2add198a1826497653c0722d23`: 121 Android tests, exactly three assertion failures, zero errors/skips. They observed queued audio at stop, cancellation returning with queued audio active, and `COMPLETED` rather than `CLEANUP_FAILED` for failed focus abandonment. Production code was unchanged from the pre-repair source. Raw XML and summary: `evidence/red/`.
- Follow-up RED: [run 40132182876](https://github.com/Hatsunama/sign-to-speak/actions/runs/40132182876), draft repair `00f7df1c247ef8e7945d48b1be28cfb9cd93d120`: 122 tests, exactly one assertion failure, zero errors/skips. Focus loss returned with queued PCM active while the worker waited for the teardown lock. The original three regressions passed. Raw XML and summary: `evidence/red-focus/`.
- All four regression bodies remain byte-identical to their observed RED versions (`evidence/regression-provenance.json`). Nominal completion and denied-focus assertions were strengthened separately. The phone integration test additionally retains the real playing AudioTrack and checks its state immediately after production cancellation returns, then checks cleanup and explicit recovery.
- Impact audits traced the JS/native session adapter, registered bridge, SpeechOwner eligibility, Activity Back/pause/destroy, SpeechSession install/stop/run/close, real request/JNI registry lifetime, audio focus callbacks, PCM wiping, and process fault latching. They checked success, partial writes, drain, cancellation, teardown failures, denied focus and recovery. The incomplete focus handoff was repaired before final verification. No additional concrete regression was found in the final inspected change.
- Standalone key-path exclusion and 14 evidence-geometry tests were rerun without failure. No paging changes or local Gradle/NDK build was performed.

Mocked queued-audio state and actual AudioTrack state are evidence about the exercised paths; neither establishes universal acoustic silence. If Android pause/flush/stop/release fails, cleanup is reported as unconfirmed and further speech is blocked. Damaged-device/OS cleanup faults were not physically injected.

## Unresolved commercial readiness

- Core ASL recognition, translation and customer speech flow remain absent; the existing UI discloses this. This mission repairs the reconstructed backend, not a recognizer.
- Production identity/signing is unconfigured. Future Play AAB and Solana APK signing identities must differ; private keys must remain outside Git. No production key or separate store build was created.
- No exact final production/store-delivered artifact has been verified. QA is isolated, testOnly and debug-signed; it is not a shipping release.
- Full-app 16 KiB runtime is unverified; static alignment checks do not establish runtime compatibility.
- Supported-device and customer privacy acceptance remain incomplete. Speech rejects API 26â€“28. Physical coverage is limited to available phones, and prior capture evidence applies only to its exact old artifact and tested Home-region/synthetic-audio scope. Active-preview/customer-flow privacy acceptance is not certified here.

The unpublished 477-file historical candidate remains unrecovered. This reconstruction is tracked independently and does not claim to reproduce it. Historical storage-retirement blockers and unrelated projects are untouched.

Signing keys/credentials are never committed. Device work requires Home readiness; other foreground apps are left alone. Any claimed phone use must include exact installed artifact hashes and return-to-Home evidence. No additional issue found in the inspected final scope means exactly that; it is not proof that the entire application is correct.
