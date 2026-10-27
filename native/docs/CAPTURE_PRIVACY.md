# Native screen and playback capture privacy

## Scope and current output boundary

The current Android QA app has camera preview and typed, bundled Pocket TTS/Alba speech. Synthesis and protected 24 kHz PCM playback run in the app process and UID; they do not use an Android TTS engine service. Production ASL recognition, continuous translation and microphone reply captions remain absent. See the [current exact-artifact evidence and remaining acceptance](RECOGNITION_FOLLOWUP_20261026.md). The historical foundation checkpoints below predate speech and apply only to their named artifacts.

Screen protection and audio protection are separate acceptance criteria. A black recording with audible app content is a failure. Silence from a build that never played audio is not evidence that playback capture was blocked.

## Native controls

- `MainActivity` sets `FLAG_SECURE` before `super.onCreate`, before React can attach content. It is never cleared. The same window hosts home, preview, thank-you and the unavailable state. CameraX uses its `COMPATIBLE` TextureView path inside this composition.
- The starting window uses an opaque constant color. Android 12+ can show the static launcher icon and this background. No session state, camera frame or transcript is put in the starting window. This does not claim the system-owned splash itself is flagged secure before the Activity exists.
- The application manifest explicitly disables `allowAudioPlaybackCapture`. On API 29+, `MainApplication` sets `AudioManager` capture policy to `ALLOW_CAPTURE_BY_NONE` and checks its readback before loading React Native. Android's manifest opt-out alone still permits system capture; `NONE` is the stronger request. This can also prevent system live-caption/accessibility playback processing.
- A missing service, thrown policy/readback call or rejected readback leaves a native capability false. The synchronous, read-only `CapturePrivacy` module exposes only that boolean. The first React render chooses a generic unavailable screen without mounting Home. There is no optimistic loading frame or in-process retry control. Native `PrivateCameraView` independently refuses a start while this capability is false. No exception detail or user content is logged or displayed.
- API 26–28 keep the preview usable. The public playback-capture API and these audio-policy calls start at API 29; no unsupported call runs on older Android. Older-OS/OEM audio restrictions are not certified; the current speech capability remains unavailable on API 26–28.

Sources: [secure windows](https://developer.android.com/reference/android/view/WindowManager.LayoutParams#FLAG_SECURE), [screen-protection limits](https://developer.android.com/security/fraud-prevention/activities), [static system splash behavior](https://developer.android.com/develop/ui/views/launch/splash-screen/migrate), [playback capture](https://developer.android.com/media/platform/av-capture), [app-wide capture policy](https://developer.android.com/reference/android/media/AudioManager#setAllowedCapturePolicy(int)), [NONE policy and accessibility tradeoff](https://developer.android.com/reference/android/media/AudioAttributes#ALLOW_CAPTURE_BY_NONE).

## Separate windows and lifecycle

The production foundation declares one Activity and creates no app dialog, popup or React Modal. Android's camera permission prompt belongs to the system; it receives no private signing/transcript content from this build. New app-owned windows must set secure flags **before showing**, rather than assuming every dialog automatically inherits an Activity's flags.

Pinned React Native 0.86.3's `ReactModalHostView.showOrUpdate` explicitly copies the current Activity's secure flag to its `ComponentDialog` before `show()`. A regression exercises that actual dependency path, including dialog recreation. This establishes only that pinned implementation, not a guarantee for arbitrary future native dialogs. Activity tests cover creation before the React delegate, pause, restart, new intents and recreation. Keep the static startup boundary, unavailable state, process recreation and recents in real compositor tests too.

## Current app-owned audio boundary

`tests/capture-privacy.test.mjs` permits the reviewed `ProtectedPcmPlayer` sink and flags other known audio-output APIs or private secondary windows in shipping sources. It is a source-review alarm, not a sandbox. The sink also has Android policy tests. Neither layer proves every possible dependency's behavior or substitutes for capture testing.

Pocket synthesis runs in-process. `SpeechOwner` supplies protected playback with volatile PCM; `ProtectedPcmPlayer` requires API 29+, checks app-wide policy readback and creates each `AudioTrack` with media/speech attributes and `ALLOW_CAPTURE_BY_NONE`. Model files use a separate verified private cache. The current speech path does not persist transcripts or synthesized audio.

1. Identify the actual producer UID and every output path. The current producer is the app; any added prerecorded audio, earcon or fallback needs its own review.
2. Set the capture policy on every player before creation/playback. Keep truthful usage/content types and the app-wide policy check.
3. Do not reuse a player created before policy installation. Changing app policy does not retrofit an already playing track.
4. Complete end-to-end screen plus playback-loopback acceptance with real synthetic sample production, cancellation, pause/Back, failure and recovery. A successful setter or a moving playback head alone is configuration/execution evidence.
5. Preserve app ownership, volatile audio, cancellation eligibility and cleanup when changing the engine. This boundary is implemented, while independent capture acceptance remains open.

Android's `TextToSpeechService` normally constructs playback under an engine-service UID. App flags cannot certify that separate producer. If such an engine is introduced, its audio attributes, producer identity and output paths need separate runtime proof.

Sources: [TTS audio attributes and return status](https://developer.android.com/reference/android/speech/tts/TextToSpeech#setAudioAttributes(android.media.AudioAttributes)), [framework engine audio parameters](https://android.googlesource.com/platform/frameworks/base/+/HEAD/core/java/android/speech/tts/TextToSpeechService.java), [framework engine track creation](https://android.googlesource.com/platform/frameworks/base/+/master/core/java/android/speech/tts/BlockingAudioTrack.java).

## Required real capture matrix (not executed by JVM/host tests)

Use a disposable emulator with only synthetic content. Installation/signing and MediaProjection consent must be separately authorized. A playback-capture recorder may require `RECORD_AUDIO`, but must use **AudioPlaybackCapture**, never a microphone input. No physical camera, microphone or user's speech is needed. Camera-preview imagery, if later authorized, must come from emulator synthetic frames.

Run API 29, 30, 31/32, 34 and 36; retain API 28 as a screen-only/legacy compatibility row. Record the exact app/test SHA, APK hashes, Android image/build, recorder/version, selected display/window scope and audio routing. A later physical/OEM check needs its own approval; emulator success does not certify all vendor recorders.

1. **Recorder-positive control:** an isolated unprotected fixture displays a moving synthetic marker and plays a known synthetic tone with media usage and `ALLOW_CAPTURE_BY_ALL`. Record using the same projection, audio configuration and volume. Both marker and expected waveform must be detectable. A silent or black positive control invalidates the run.
2. **Protected screen:** record before launch, during cold/warm launch, home, unavailable state, thank-you, synthetic preview, permission prompt, dialog, rotation, background/recents, return, Activity recreation and process restart. Start projection both before and after opening the app; test full-display and app-window capture where available. Private app pixels must never appear. Static system/launcher chrome is not private app content.
3. **Protected playback independently:** a test-only synthetic probe executes under the target app's UID with app-owned playback. Confirm actual sample production and playback progress; collect separate loopback PCM while the secure window is present. Analyze tone correlation/energy against the validated positive control. Silence with no active playback, a stalled recorder, an incorrect UID filter or a muted source is inconclusive.
4. **Combined failure sentinel:** evaluate video and PCM separately. Black video plus a recoverable synthetic tone fails the run. Never let a screenshot assertion alone mark privacy passed.
5. **System recorder:** where the image supplies a recorder with internal-audio mode, repeat without microphone mode. A third-party MediaProjection recorder cannot demonstrate the system-only branch of `NONE` by itself. If unavailable, report this row untested.
6. **Current Pocket TTS:** repeat with fixed synthetic text and the actual app producer UID. Include preparation, active playback, queue/cancel, pause/Back, failure and reinitialization. This row is **required and NOT_RUN for independent playback capture**, not inapplicable: a real synthesizer/player is implemented. Any later engine-service integration needs the engine/actual producer UID instead. Muted AudioTrack progress tests cannot satisfy this row.
7. **Teardown:** revoke projection and verify recorder cleanup. Keep only bounded synthetic artifacts. No monitoring, microphone recording or screen/audio recording of the user is authorized by this test plan.

The full matrix still requires compositor video, positive-control PCM, protected-probe PCM, objective comparison output and per-row pass/fail/inconclusive records. It has not executed. The limited physical app-only screenshot observations below do not complete this matrix.

## Verification labels and limits

Host/component tests establish first-render gating and source boundaries. Robolectric tests establish production startup ordering, native failure handling and window/lifecycle flag behavior using controlled service/React boundaries. They do not exercise Android's compositor, real AudioFlinger mixing, a physical speech path, screen recording or playback loopback. The later physical Pocket tests exercise app-owned synthesis and playback progress, but do not complete independent capture acceptance. The exact hosted red/green checkpoints are recorded below; neither establishes real capture acceptance.

These controls request Android's platform capture protections. They do not prevent a separate physical microphone/camera recording sound or light, a compromised OS, or a vendor path that ignores the policy. The app does not try to disable another app's microphone or change device-wide recording settings. Do not describe this as absolute prevention or a privacy/compliance certification.

## Recorded test-first checkpoint

[Native CI run 38301427079](https://github.com/Hatsunama/sign-to-speak/actions/runs/38301427079), exact tests-only commit `d1b95ce77fd320fa7bf4190d006d16bd12ef1011`, compiled unchanged production and executed 61 tests. Exactly two failed: API 29/36 startup never called `setAllowedCapturePolicy(3)` before React bootstrap. The API 28 control and all three secure-window lifecycle cases passed. The separate unsigned emulator build passed; it did not run an emulator. The evidence artifact is `11771795911`, SHA-256 `d8cfc61b31c998be00a110f1b5c54c844eb0aff23cfc931e28d19b65c6130c11`.

## Verified checkpoint (2026-10-12)

At exact commit `1472323c62fe21fb69620f36b45aa0eba1d94cf6`, native CI passed 84 Android-unit tests with zero failures/errors/skips, actual APK compilation and manifest audits; the separate unsigned emulator-app/test build also passed. See the [canonical checkpoint and immutable run/artifact links](../README.md#verified-checkpoint-2026-10-12). Native Back is repaired. These are hosted Robolectric/JVM and build results, not real emulator/device, compositor or audio-loopback acceptance. ASL inference and TTS remain unavailable. No later documentation/code revision inherits this verification.

## Limited physical screenshot checkpoint (2026-10-12)

At exact tested source `482605debdbd7fa465105ea6edbe07b2bdcb01a7`, six app-only screenshots sampled Home portrait, Hatsu/thank-you portrait and Home landscape on Seeker and Galaxy S24. Every RGB channel had minimum and maximum zero: 11,003,904 black pixels in total. Seeker images were 960×2136 or 2136×960; S24 images were 864×1872 or 1872×864.

Black images alone would be inconclusive. Independent live UI hierarchies showed the Home → Thank you/Back to Home → Home transitions, while window diagnostics showed a surface ready for display. Seeker explicitly reported `SECURE`; S24 flags `0x81812100` include `FLAG_SECURE` (`0x2000`), with `mHasSurface=true` and `isReadyForDisplay=true`. Together, this supports app-only screenshot exclusion in these sampled states while the app was responsive. It does not reveal or validate visual design or animation.

The [canonical physical checkpoint](../README.md#limited-physical-qa-checkpoint-2026-10-12) records exact source, public green CI, signed APK/certificate hashes and matching installed-byte provenance. CAMERA stayed denied and no camera or microphone was used. Neither real camera-preview capture exclusion nor the broader screen-recording matrix was tested. No playback-loopback test or TTS output exists; black screenshots say nothing about audio capture. This result does not certify every device, recorder or future output, and does not make a later documentation revision device-tested.
