# Android foundation plan and verification

Base: `Hatsunama/sign-to-speak`, main commit `e52813a3ccf34cb5e2df40548dfc68e9587e2225` (verified 2026-10-12). The existing Next.js application is preserved in place. This directory is isolated and must not import browser camera, browser speech, or browser model-download code.

## Scope and honest capability

The first Android milestone is a React Native home and local camera/privacy foundation. It is **not a continuous ASL translator**. No recognized phrase or speech may be fabricated from an unavailable model. Show “Continuous ASL translation is not available in this build” and recognition `unsupported`. When a real model is integrated later, absence of reliable evidence must be `unknown`, not a guessed phrase.

The source currently contains an InstantHMR WASM loader and hand-coded fingerspelling/gesture rules. These are not a trained continuous-language model. Existing model downloads use a moving external Hugging Face `main` URL. Those URLs and browser fallback speech are not inherited by Android.

## Smallest complete vertical slice

1. React Native Android shell, application ID `com.xmilo_at_your_side.sign_to_speak`. Home and local thank-you route support portrait and landscape, large type and Android Back. Render the owner’s canonical Hatsu animation through its Android View, retaining pinned source provenance; avoid a WebView or recreated approximation.
2. Request camera permission only after an explicit live-preview action. Use CameraX `Preview` and `ImageAnalysis`, never still capture or video recording. Native Activity/View lifecycle is the authority: JS lifecycle is defense in depth only.
3. `ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST`, a single analyzer executor, and `ImageProxy.close()` in `finally` for every path. Do not copy camera pixels into JavaScript. With no approved recognizer, disable analysis entirely; camera preview can work without pretending to translate.
4. Stop clears analyzer, unbinds this app’s use cases, invalidates generation tokens, stops speech and destroys app-owned ephemeral buffers. Native `onPause`, view detach, permission revocation, Android Back, JS cancellation and Activity destruction must each enforce teardown. Resume must not silently restart the camera.
5. Native-only, bounded temporal landmark memory is added only with a licensed, validated recognizer. No frames, landmarks, transcripts, session logs or signing telemetry are persisted, exported or uploaded. Camera-system-owned buffers are released; claiming cryptographic erasure of platform-owned memory would be inaccurate.
6. Speech remains unavailable until an offline route is verified. Android `Voice.isNetworkConnectionRequired=false` is a useful selection filter, but a third-party TTS engine runs outside this app and can have its own permissions. It is not by itself proof that signer-derived text cannot leave the device. Strict privacy favors an audited in-process offline engine with approved assets. Never fall back to an arbitrary system/network voice.
7. First release has no microphone, storage, media-recording or network permission. A later explicit model download feature requires a separate review. Approved AI assets must resolve only through a new app-labeled repository owned by Hatsunama, immutable revision and verified SHA-256, with fail-closed license/manifest checks. No model is selected, downloaded or mirrored here.

## Tests, in order

- Zero-dependency session tests: denied permission/background starts; repeated starts; stop invalidates pending work; old generation rejected after restart; cleanup failures block restart; unavailable recognition has no transcript/speech API.
- Native analyzer tests: image closed on success/error/cancellation, stale callback ignored, one in-flight frame maximum, owned buffers cleared, no retained result after stop.
- Native lifecycle tests: background while permission or provider acquisition is pending; pause/stop/detach/repeated stop; no automatic resume; teardown even with stalled JS.
- UI tests: canonical Hatsu visible and responsive; reduced-motion/background animation policy; local Hatsu button and Back; portrait and landscape; permission denied/revoked; truthful unsupported state.
- Manifest/source/dependency checks: no recording APIs, recording/export UI, camera-content logs, file/database writes, network fallback, unpinned AI asset paths or content telemetry.
- Real Android emulator checks after a resource-approved APK build: camera lifecycle, permission transitions, actual rotation and screenshots. Host-only tests do not prove device behavior.

## Current evidence

- React Native 0.86.3 shell, canonical Hatsu native renderer, local thank-you route, CameraX preview-only native view, secure Activity and native lifecycle cancellation are implemented but not Android-built.
- `npm test`: 13/13 host tests pass, including actual React App interruption/Back/local-route checks with mocked RN/Android hosts. Initial session implementation was missing; structural checks initially caught a missing immediate preview hide and API-26 minimum. A later source check caught and corrected the template launcher label and explicitly disabled development-server support. Component test initially failed because of a circular-JSON test selector, corrected in the harness; that was not a product regression.
- `npm run typecheck`: passes. Initial inherited Jest types were absent; the isolated TypeScript config now specifies React types.
- `JAVA_HOME=... ./tests/run-java-tests.sh`: 8 checks pass against the actual native Java cancellation-token class after an initial missing-class compile failure.
- Exact upstream Hatsu hashes pass. Existing tracked web files remain unchanged.
- No APK, Android/Kotlin compilation, emulator camera/rotation verification, model integration, translation evaluation or speech test has run.
- The foundation deliberately binds only CameraX Preview. ImageAnalysis and temporal landmark buffers are not added speculatively before model/license approval. No fake landmark bridge, model download, token or training job is present.

## Official API references

- React Native existing Android integration: https://reactnative.dev/docs/integration-with-existing-apps
- CameraX analysis and frame release: https://developer.android.com/media/camera/camerax/analyze
- Android offline-voice metadata: https://developer.android.com/reference/android/speech/tts/Voice#isNetworkConnectionRequired()
