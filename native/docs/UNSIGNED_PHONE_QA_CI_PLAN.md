# Unsigned arm64 phone QA: build contract

The unsigned ARM64 phone-QA build and binary-manifest audit passed at the [exact verified checkpoint](../README.md#verified-checkpoint-2026-10-12). This confirms artifact compilation and isolation, not installation or physical-device behavior.

Historical proposal base: `0ad27c73817fc8414f9a72785d5e85669005b866` / tree `99c144029cc6ccaca0dde539f7d89b9fea7dd58c`. Its PC memory snapshot is not a current resource check. Keep builds in the approved hosted workflow; any later local signing/device work requires current authorization and a fresh resource check. No cloud signing keys, credential uploads, AAB or release publication are permitted by this build contract.

The existing one-job/30-minute standard public workflow includes a ten-minute-bounded ARM64 build step. Branch publication requires review before CI is triggered.

`-PunsignedPhoneQa=true -PreactNativeArchitectures=arm64-v8a` opts into a release-like `qa` variant, application ID `com.xmilo_at_your_side.sign_to_speak.qa`, `android:testOnly=true`, bundled React/Hermes and null signing. Normal debug/release remain unsigned with their original identities. App Kotlin/React/canonical renderer files are unchanged.

CI builds only APK, verifies QA isolation and the actual merged manifest, then extracts the binary APK manifest. The audit requires the exact QA identity, SDK26/target36, test-only/nondebuggable flags, disabled backup/cleartext, CAMERA plus the exact QA-owned signature receiver permission, arm64-only native libraries, valid ZIP structure, and no v1 or v2/v3 signing material. `apksigner verify` must reject it as unsigned. It records SHA256 and marks physical testing NOT_RUN. The artifact expires after three days.

The local PC child later transfers only this APK and minimal verification metadata, signs a copy with the existing approved development identity under its safe resource budget, and records a new signed hash/certificate. No local key generation, credential upload, source compilation or emulated camera assumptions are involved. Install is only the distinct `.qa` package on each exact authorized Seeker/S24 serial. Signature conflicts or insufficient signing memory stop progress; neither justifies data deletion or an unapproved key/tool/resource change.

This is a verified unsigned build, not a device-tested phone artifact. Physical camera/UI/lifecycle checks and signed installed-byte provenance remain separate gates. The emulator runtime matrix also stays unrun; an x86_64 artifact cannot substitute for the ARM64 phone APK.
