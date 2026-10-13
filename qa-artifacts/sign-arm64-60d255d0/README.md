# Sign ARM64 unsigned diagnostic candidate

This package preserves a frozen, newly compiled diagnostic app. It is not a production release or a claim that speech/privacy/ASL acceptance passed. Keep the accompanying third-party notices and covered source files with the APK when sharing copies.

- Expected reassembled file: `Sign-phoneQa-unsigned.apk`
- Bytes: **23,682,746**
- SHA256: `60d255d02a3b44520e024796eda455dd0670af5b3b6aab06aa964dbb9aceaefc`
- Package: `com.xmilo_at_your_side.sign_to_speak.qa`
- Version: `0.1.0-foundation-phone-qa`, code 1, minimum API 26, target 36
- ARM64 only, test-only, non-debuggable, bundled JavaScript, unsigned

The three ordered parts are each below the GitHub connector's request-size limit. Download all accompanying files from one exact commit into a dedicated directory. Inspect `Reassemble-Sign.ps1`, then run it there with PowerShell. It verifies each part, refuses an existing destination, writes a new temporary file, verifies the whole-file hash/size, and moves it to the final name. It does not sign or install anything. Independently verify the final hash before any separately authorized local signing.

## Source and provenance

The frozen candidate derives from public baseline `78c71bd36bbd0f04c86bd85e4b795da35bcd56f7` plus previously reviewed candidate changes. It is not identical to the storage branch's parent or the public baseline. All 477 frozen source files stayed unchanged through compilation; source inventory SHA256: `d38aa2515a33447893328680274665ae76ceb181016f11b67e2ed871855a8a5e`.

React Native 0.86.3 was newly compiled from official npm source with all 4575 original files verified against the package. Framework ARM AAR SHA256: `9be58873bee48d191e56efba390fc55d1355047f393dc9999d8613a72eb2c7b5`. This is a newly compiled upstream-source artifact, not a byte-identical vendor binary. Official source: https://registry.npmjs.org/react-native/-/react-native-0.86.3.tgz . Hermes remains 250829098.0.17; CameraX remains 1.4.2. Native archives were checked against official checksums or upstream Git trees. The build was offline; app source and pinned dependency versions were unchanged.

## Completed checks and open QA gates

Unsigned verification, ZIP integrity, 16 KiB ZIP alignment and all 15 native ELF-library alignments passed. Typecheck passes; 73/74 source tests pass. The deliberate no-audio privacy alarm remains failing. API 26–28 playback is deliberately denied. Continuous-ASL recognition is absent; voice naturalness, signed full-app playback, capture exclusion, audio focus, lifecycle and visual phone acceptance are pending. Existing instrumentation was built only for x86; no ARM instrumentation APK is included.

No signing keys, device captures or recordings accompany this package. No signing, installation, paid Actions, release or PR is performed by the delivery files. The notices audit separately identifies exact license texts, covered source availability and any unresolved provenance limitations; runtime QA failures are not represented as license obligations.
