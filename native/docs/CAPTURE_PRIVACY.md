# Native screen and playback capture privacy

## Scope and current output boundary

This Android foundation has a camera preview and local UI. It has **no ASL recognition, translation, TTS engine, PCM playback, player, earcon or speech fallback**. Nothing in this change enables them. The existing web prototype is a separate application and is not covered by these native Android controls.

Screen protection and audio protection are separate acceptance criteria. A black recording with audible app content is a failure. Silence from a build that never played audio is not evidence that playback capture was blocked.

## Native controls

- `MainActivity` sets `FLAG_SECURE` before `super.onCreate`, before React can attach content. It is never cleared. The same window hosts home, preview, thank-you and the unavailable state. CameraX uses its `COMPATIBLE` TextureView path inside this composition.
- The starting window uses an opaque constant color. Android 12+ can show the static launcher icon and this background. No session state, camera frame or transcript is put in the starting window. This does not claim the system-owned splash itself is flagged secure before the Activity exists.
- The application manifest explicitly disables `allowAudioPlaybackCapture`. On API 29+, `MainApplication` sets `AudioManager` capture policy to `ALLOW_CAPTURE_BY_NONE` and checks its readback before loading React Native. Android's manifest opt-out alone still permits system capture; `NONE` is the stronger request. This can also prevent system live-caption/accessibility playback processing.
- A missing service, thrown policy/readback call or rejected readback leaves a native capability false. The synchronous, read-only `CapturePrivacy` module exposes only that boolean. The first React render chooses a generic unavailable screen without mounting Home. There is no optimistic loading frame or in-process retry control. Native `PrivateCameraView` independently refuses a start while this capability is false. No exception detail or user content is logged or displayed.
- API 26–28 keep the preview usable. The public playback-capture API and these audio-policy calls start at API 29; no unsupported call runs on older Android. Older-OS/OEM audio restrictions are not certified, and future speech remains unavailable there until its own reviewed policy exists.

Sources: [secure windows](https://developer.android.com/reference/android/view/WindowManager.LayoutParams#FLAG_SECURE), [screen-protection limits](https://developer.android.com/security/fraud-prevention/activities), [static system splash behavior](https://developer.android.com/develop/ui/views/launch/splash-screen/migrate), [playback capture](https://developer.android.com/media/platform/av-capture), [app-wide capture policy](https://developer.android.com/reference/android/media/AudioManager#setAllowedCapturePolicy(int)), [NONE policy and accessibility tradeoff](https://developer.android.com/reference/android/media/AudioAttributes#ALLOW_CAPTURE_BY_NONE).

## Separate windows and lifecycle

The production foundation declares one Activity and creates no app dialog, popup or React Modal. Android's camera permission prompt belongs to the system; it receives no private signing/transcript content from this build. New app-owned windows must set secure flags **before showing**, rather than assuming every dialog automatically inherits an Activity's flags.

Pinned React Native 0.86.3's `ReactModalHostView.showOrUpdate` explicitly copies the current Activity's secure flag to its `ComponentDialog` before `show()`. A regression exercises that actual dependency path, including dialog recreation. This establishes only that pinned implementation, not a guarantee for arbitrary future native dialogs. Activity tests cover creation before the React delegate, pause, restart, new intents and recreation. Keep the static startup boundary, unavailable state, process recreation and recents in real compositor tests too.

## Future audio integration gate

`tests/capture-privacy.test.mjs` rejects known audio-output APIs and new private secondary windows in current shipping sources. This is a review alarm, **not** a sandbox or proof that every possible future library cannot play audio. The app-wide policy protects playback owned by this app; it does not assign this app's UID or manifest to another service.

Android TTS commonly runs in an engine service. The framework's `TextToSpeechService` receives audio attributes and its `BlockingAudioTrack` constructs the playback track in that service. Consequently, app manifest/runtime flags alone cannot certify a selected engine's output. Before any real TTS/player is enabled:

1. Identify the actual producer, its UID, package/version and every output path, including prerecorded speech, earcons and fallback.
2. Set `AudioAttributes.Builder.setAllowedCapturePolicy(ALLOW_CAPTURE_BY_NONE)` **on each player before creation/playback**. For TTS, apply those attributes with `TextToSpeech.setAudioAttributes`, check `SUCCESS`, and reject error/unknown engine behavior. Use a truthful usage/content type; do not disguise media as calls to evade capture rules.
3. Do not reuse a player created before policy installation. Android documents that changing the app policy does not retrofit an already playing track.
4. Require per-engine end-to-end screen plus playback-loopback acceptance and lifecycle stop/cancellation tests. Reject unvalidated output, engine changes, initialization failure, unsupported devices or fallback. A successful setter is configuration evidence only.
5. If output must be app-owned, design the synthesis/playback boundary explicitly without quietly writing speech to disk or exposing synthesis data. This change does not implement such a path.

Sources: [TTS audio attributes and return status](https://developer.android.com/reference/android/speech/tts/TextToSpeech#setAudioAttributes(android.media.AudioAttributes)), [framework engine audio parameters](https://android.googlesource.com/platform/frameworks/base/+/HEAD/core/java/android/speech/tts/TextToSpeechService.java), [framework engine track creation](https://android.googlesource.com/platform/frameworks/base/+/master/core/java/android/speech/tts/BlockingAudioTrack.java).

## Required real capture matrix (not executed by JVM/host tests)

Use a disposable emulator with only synthetic content. Installation/signing and MediaProjection consent must be separately authorized. A playback-capture recorder may require `RECORD_AUDIO`, but must use **AudioPlaybackCapture**, never a microphone input. No physical camera, microphone or user's speech is needed. Camera-preview imagery, if later authorized, must come from emulator synthetic frames.

Run API 29, 30, 31/32, 34 and 36; retain API 28 as a screen-only/legacy compatibility row. Record the exact app/test SHA, APK hashes, Android image/build, recorder/version, selected display/window scope and audio routing. A later physical/OEM check needs its own approval; emulator success does not certify all vendor recorders.

1. **Recorder-positive control:** an isolated unprotected fixture displays a moving synthetic marker and plays a known synthetic tone with media usage and `ALLOW_CAPTURE_BY_ALL`. Record using the same projection, audio configuration and volume. Both marker and expected waveform must be detectable. A silent or black positive control invalidates the run.
2. **Protected screen:** record before launch, during cold/warm launch, home, unavailable state, thank-you, synthetic preview, permission prompt, dialog, rotation, background/recents, return, Activity recreation and process restart. Start projection both before and after opening the app; test full-display and app-window capture where available. Private app pixels must never appear. Static system/launcher chrome is not private app content.
3. **Protected playback independently:** a test-only synthetic probe executes under the target app's UID with app-owned playback. Confirm actual sample production and playback progress; collect separate loopback PCM while the secure window is present. Analyze tone correlation/energy against the validated positive control. Silence with no active playback, a stalled recorder, an incorrect UID filter or a muted source is inconclusive.
4. **Combined failure sentinel:** evaluate video and PCM separately. Black video plus a recoverable synthetic tone fails the run. Never let a screenshot assertion alone mark privacy passed.
5. **System recorder:** where the image supplies a recorder with internal-audio mode, repeat without microphone mode. A third-party MediaProjection recorder cannot demonstrate the system-only branch of `NONE` by itself. If unavailable, report this row untested.
6. **Future TTS:** after a real reviewed engine exists, repeat using synthetic text and the **engine/actual producer UID**, not only the Sign UID. Include queue/cancel, pause/Back, engine failure/change, reinitialization and every fallback. This row is currently not applicable because no engine is implemented; it is not a pass.
7. **Teardown:** revoke projection and verify recorder cleanup. Keep only bounded synthetic artifacts. No monitoring, microphone recording or screen/audio recording of the user is authorized by this test plan.

Expected evidence includes compositor frames/video, positive-control PCM, protected-probe PCM, objective comparison output and a per-row pass/fail/inconclusive record. None exists yet for this change.

## Verification labels and limits

Host/component tests establish first-render gating and source boundaries. Robolectric tests establish production startup ordering, native failure handling and window/lifecycle flag behavior using controlled service/React boundaries. They do not exercise Android's compositor, real AudioFlinger mixing, a real TTS engine, screen recording or playback loopback. Exact CI red/green receipts must be recorded before claiming those tests passed.

These controls request Android's platform capture protections. They do not prevent a separate physical microphone/camera recording sound or light, a compromised OS, or a vendor path that ignores the policy. The app does not try to disable another app's microphone or change device-wide recording settings. Do not describe this as absolute prevention or a privacy/compliance certification.

## Recorded test-first checkpoint

[Native CI run 38301427079](https://github.com/Hatsunama/sign-to-speak/actions/runs/38301427079), exact tests-only commit `d1b95ce77fd320fa7bf4190d006d16bd12ef1011`, compiled unchanged production and executed 61 tests. Exactly two failed: API 29/36 startup never called `setAllowedCapturePolicy(3)` before React bootstrap. The API 28 control and all three secure-window lifecycle cases passed. The separate unsigned emulator build passed; it did not run an emulator. The evidence artifact is `11771795911`, SHA-256 `d8cfc61b31c998be00a110f1b5c54c844eb0aff23cfc931e28d19b65c6130c11`.

The repaired candidate's additional service-failure, capability, disposed-view and dialog cases are awaiting their own hosted run. No real screen/audio-loopback row has been executed.
