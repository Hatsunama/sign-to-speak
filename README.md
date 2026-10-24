# Sign To Speak

## Android-native development

The current development destination is the isolated [React Native Android app in `native/`](native/README.md). It implements home, the canonical Hatsu animation, a local thank-you page and camera **preview only**. The current development candidate adds explicit typed text review and local Flite speech; exact final-artifact verification is still required. ASL recognition, translation, hand-mesh inference and customer microphone captions remain absent. It does not claim interpreter capability or HIPAA certification.

Android application ID: `com.xmilo_at_your_side.sign_to_speak`. QA builds use a separate `.qa` identity.

See [native build and verification status](native/README.md), [capture protections and remaining capture gates](native/docs/CAPTURE_PRIVACY.md), and [development CI](native/scripts/CI.md). Native builds and hosted Android-unit tests passed at the exact checkpoint linked there; limited physical non-camera navigation and app-only screenshot exclusion were later observed on two phones. The full screen-recording, camera-runtime and audio-loopback gates remain pending. No verified installable release is recommended here.

## Preserved browser prototype

The root `app/`, `components/`, `lib/`, `public/` and `scripts/` contain the historical Next.js/browser prototype. Its installer, browser model downloads, hand mapping and browser speech paths are preserved as source history; they have not migrated into Android and are not evidence of native capabilities or model-license rights.

The [original prototype README at base `e52813a3`](https://github.com/Hatsunama/sign-to-speak/blob/e52813a3ccf34cb5e2df40548dfc68e9587e2225/README.md) preserves the earlier setup and capability descriptions for provenance. Those descriptions are not a current installation recommendation or verified Android behavior. Use the native documentation above for Android development.

## Recognition work and licensing

See the [approved native recognition/video-evaluation plan](native/docs/RECOGNITION_PLAN.md) for implementation boundaries and required gates. The application is free and open source; original application code is licensed under [Apache 2.0](LICENSE). Third-party code, voices, models and datasets retain their separate terms. Private research datasets are not part of the application or this repository.
