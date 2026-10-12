# Sign To Speak

## Android-native development

The current development destination is the isolated [React Native Android app in `native/`](native/README.md). It implements home, the canonical Hatsu animation, a local thank-you page and camera **preview only**. It has no ASL recognition, translation, hand-mesh inference, TTS or system-voice fallback. It does not claim interpreter capability or HIPAA certification.

Android application ID: `com.xmilo_at_your_side.sign_to_speak`. QA builds use a separate `.qa` identity.

See [native build and verification status](native/README.md), [capture protections and remaining capture gates](native/docs/CAPTURE_PRIVACY.md), and [development CI](native/scripts/CI.md). Native builds and hosted Android-unit tests passed at the exact checkpoint linked there; limited physical non-camera navigation and app-only screenshot exclusion were later observed on two phones. The full screen-recording, camera-runtime and audio-loopback gates remain pending. No verified installable release is recommended here.

## Preserved browser prototype

The root `app/`, `components/`, `lib/`, `public/` and `scripts/` contain the historical Next.js/browser prototype. Its installer, browser model downloads, hand mapping and browser speech paths are preserved as source history; they have not migrated into Android and are not evidence of native capabilities or model-license rights.

The [original prototype README at base `e52813a3`](https://github.com/Hatsunama/sign-to-speak/blob/e52813a3ccf34cb5e2df40548dfc68e9587e2225/README.md) preserves the earlier setup and capability descriptions for provenance. Those descriptions are not a current installation recommendation or verified Android behavior. Use the native documentation above for Android development.
