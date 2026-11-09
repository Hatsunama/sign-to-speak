# Offline MediaPipe detector runtime

The app now uses `com.google.mediapipe:tasks-vision:1.1.1` with the source-built
`com.google.mediapipe:tasks-core:1.1.1-offline-50505c8`. This is the same vetted
core formerly used only by the ARM64 phone research instrumentation. The shared
`native/android/app/offline-landmark-runtime.gradle` substitutes the audited core
in every configuration, including app and instrumentation compile/runtime paths.
The published telemetry-enabled core is never a fallback. Existing app ABIs are
not narrowed; only ARM64 processes can initialize this detector. On x86_64 the
app retains its other capabilities and reports hand tracking unavailable.

This is an on-device landmark detector feeding the attributed Kotlin adaptation
of Roboflow SORT. It is not a sign classifier, translation model or medical
interpreter. No model or SDK download happens inside Android. Real camera
performance, model accuracy, lifecycle release on hardware and commercial
acceptance remain separate validation gates.

## Source-built core and exact SDK artifact admission

The local 8,325,540-byte AAR was built from Google MediaPipe source commit
`50505c8312c7eacb1bd411cecdc4cc9fd08d53cb` using the official
`//mediapipe/tasks/java/com/google/mediapipe/tasks/core:tasks_core` Bazel rule
with `ENABLE_TASKS_USAGE_LOGGING=0`. SHA-256:
`e0e9365d753b990f9b0d68e61274dfa5736d8812dbde5679095aec5ca28bd6b2`.
The reproducible source-build procedure remains `native/scripts/build-offline-mediapipe.sh`.
The historical independent receipt is
`recognition-evidence-20261024/offline-source-trial-2/independent-artifact-receipt.json`
outside this repository; it is not a build input.

The source build retains Tasks Core Java APIs, detector setup/lifecycle code and
unmodified published ARM64 JNI. `TasksStatsLoggerFactory.create` invokes
upstream `TasksStatsDummyLogger.create`. The preparer checks exact factory/dummy
class hashes, rejects usage-logging class references, and checks the core POM's
four nontelemetry dependencies. There is no class shadowing, binary factory
patch, reflective override or SDK permission stripping.

The official Google Maven Tasks Vision 1.1.1 AAR has size 610,482 bytes and SHA-256
`cf6565d27feb96b411bbb2e5cb38b3db37e0e0d3109f30ec0c9a8178055051e6`.
Its manifest and the offline core manifest contain only a `uses-sdk` child
(minimum API 24), no permissions or components. The app minimum remains API 26.
Vision contains Java code and no native libraries. Its POM's nonoptional
compile dependencies are AndroidX annotation 1.1.0, Guava 27.0.1-android and
Tasks Core 1.1.1. Its optional LiteRT-LM dependency is not adopted and is blocked
by the shared dependency gate.

The core POM retains Flogger 0.6, flogger-system-backend 0.6, Guava 27.0.1-android
and protobuf-javalite 4.26.1. The removed published DataTransport dependencies
belong to the source-disabled usage logger. No standalone LiteRT classifier is
added to the app; the existing `litert:1.4.2` research classifier remains in the
QA instrumentation APK only. All newly introduced transitive libraries are
Java; the only new app native object is `libmediapipe_tasks_jni.so`.

The native object is 14,367,776 bytes, SHA-256
`d80dfdfc6e034b7595137d035a279eddf70b028587510e99ede4a7e634c342a0`.
Independent `readelf -lW` inspection confirms AArch64 ELF and all three LOAD
segments have `0x4000` (16 KiB) alignment. AGP preserves these exact already
stripped bytes. The unchanged final APK ELF/ZIP alignment audit still applies
to every native library, including this one.

## Official detector model admission

`runtime-admission.json` records the complete public model and notice inventory.
The model is Google's Holistic Landmarker float16 **version 1**, not a moving
`latest` URL:

https://storage.googleapis.com/mediapipe-models/holistic_landmarker/holistic_landmarker/float16/1/holistic_landmarker.task

- Android asset: `holistic/holistic_landmarker.task`
- Size: 13,683,609 bytes
- SHA-256: `e2dab61191e2dcd0a15f943d8e3ed1dce13c82dfa597b9dd39f562975a50c3f8`
- Unmodified bundle contains seven pinned TFLite members, including hand ROI,
  face/pose detectors and landmarks, and the disabled optional blendshape model.
- Redistribution basis: Apache License 2.0 declarations in Google's BlazePose,
  FaceMesh V2 and Hand Tracking model cards, linked by the official Holistic
  guide. Exact URLs and verification date (2026-11-09) are in the receipt.
  This is model-specific evidence, not an assumption from the SDK's license.

The cards describe limitations with occlusion, light/motion, distance, multiple
people and orientation. They do not establish ASL recognition quality or
life-critical suitability. The integration uses only hand landmark bounding
boxes for ephemeral track association. Holistic internally computes its other
outputs; their coordinates are not exported, saved, used for identity matching
or sent to JavaScript. Optional face blendshapes and segmentation are disabled.

## Build-time producer and fail-closed gates

Every app variant registers the same `:app:stageHolisticAssets` output through
AGP's `variant.sources.assets.addGeneratedSourceDirectory` producer API. This
prevents an untracked source-directory or merge-order omission. It runs the
bounded Python preparer, validates the exact source core/POM, model and every
notice, then stages only the model and `holistic-licenses/` notice inventory.
Repeated builds reuse only a valid hash-checked staged model. Corrupt existing
models fail rather than silently fetching a replacement. Missing models can
be fetched on the build host from the one HTTPS URL, with redirects refused,
size/read bounds and final cryptographic verification.

An offline or pre-provisioned build can use:

```sh
./gradlew --offline -PholisticModelFile=/absolute/path/holistic_landmarker.task \
  -PreactNativeArchitectures=arm64-v8a :app:stageHolisticAssets
```

Python 3 is needed on the build host. `-PpythonCommand=python` selects an existing
platform-specific executable when `python3` is not its name. No Python code,
network client, user clip or research classifier is added to Android.

Each `verify<Variant>OfflineLandmarkRuntime` task validates exact resolved core
and vision AAR hashes/manifests, rejects unadmitted MediaPipe/telemetry modules,
and writes the full selected Maven inventory plus artifact hashes to
`app/build/reports/offline-landmark/<variant>-runtime.json`. These tasks are
required by app merge/package/assemble and `check`. The earlier 95-coordinate
receipt is historical and is not reused as a count for this new dependency
graph. Review the actual fresh report, including version-conflict selections.

The independent review of exact source `21154b744544508009dbbafa07547555758103b6`
([hosted run 42720053166](https://github.com/Hatsunama/sign-to-speak/actions/runs/42720053166))
found identical QA and emulator graphs: 106 external coordinates, 108 reachable
nodes including both local projects, 444 edges, and 103 artifact rows (100
distinct artifacts; three AndroidX rows repeat identical bytes). Six nodes have
no artifact: five metadata/relocation aliases and the coroutines BOM. Their
selected target modules and constraint edges remain in the report, not omitted.

Removing only the app's MediaPipe edge leaves eleven modules reachable solely
through the detector. `runtime_license_routes` records their exact selected
artifact hashes and existing or supplemental packaged grants, plus Guava's
empty `listenablefuture` conflict-avoidance replacement. The Gradle gate checks
this exact introduced-module set, verifies every route's actual resolved byte
hash and packaged notice destination, and exports the verified routes. A new
transitive module, changed version/bytes or missing notice fails closed.
This is a dependency admission check, not a reconstruction of an old lockfile.

That review found one real omission before final acceptance: the new
`checker-compat-qual:2.5.3` JAR has no embedded grant and the SDK's aggregate
NOTICE has no Checker Framework annotation grant. The exact upstream tagged
license is now added below. This notice-only correction requires a fresh
exact-head APK audit; the earlier green artifact does not include it.

`check-live-tracking-apk.py MANIFEST APK --abi arm64-v8a` checks actual model/JNI
bytes, complete exact notices, the no-network permission/component surface,
required DEX class definitions and absence of telemetry in **every** DEX. It
also supports `--abi x86_64`, requiring no ARM64 detector JNI in that artifact.
It supplements, rather than replaces, unsigned app/instrumentation audits and
the final ELF/ZIP audit. Host fixtures cover corruption, missing/extra files,
symlinks, network redirects/oversize data, unknown components, class references
that are not definitions, and telemetry in double-/triple-digit DEX files.

## Packaged license and attribution mapping

AGP omits AAR `META-INF/NOTICE`, so the generated **app** assets explicitly carry:

| App asset under `holistic-licenses/` | Exact source |
| --- | --- |
| `mediapipe-tasks-1.1.1-NOTICE` | Existing `licenses/sdk-licenses/` copy, identical to the core and vision AAR notices; SHA-256 `8db2ce1a7fb4fa13d2261bcc4cd1f1c3044d94b59fb0e4b35a5c0b4ecb6a56d2` |
| `Apache-2.0-LICENSE` | Existing Apache terms; SHA-256 `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30` |
| `protobuf-v26.1-LICENSE` | Official protobuf tag v26.1 LICENSE (BSD-3-Clause); SHA-256 `6e5e117324afd944dcf67f36cf329843bc1a92229a8cd9bb573d7a83130fea7d` |
| `flogger-0.6-LICENSE` | Official Flogger tag flogger-0.6 LICENSE; SHA-256 `8c6db340475136df3c1201d458fa5755698eace76e510471ecc9d857d6083dac` |
| `checker-framework-2.5.3-LICENSE.txt` | Complete official tag `checker-framework-2.5.3` license at commit `4113cc2b669f4bec7a6775c74f1f3db16b18f807`, 22,320 bytes; SHA-256 `37ac781fd633d592519cc231473b9a25ebd84c3bbe829abb8f588d3355edc63c` |
| `ATTRIBUTION.txt` | Integration changes, upstream ownership and explanation of the unchanged generic telemetry notice versus this dummy-logger build |
| `MODEL-RECEIPT.json` | Exact reviewed `runtime-admission.json`, including source URLs, sizes, hashes and complete model-member inventory |

The entire upstream SDK NOTICE is retained, including its bundled third-party
notices. Existing framework and JavaScript notice assets remain unchanged.
No source grant is invented from an empty JAR: protobuf/Flogger published JARs
contain no license entries, so the exact official tagged texts are packaged.
Checker compatibility annotations use the upstream file's **explicit MIT grant
for annotations**. The full original document also contains GPL/classpath terms
for other Checker Framework code; those terms do not replace its annotation MIT
grant. The admitted JAR contains only ten compatibility annotation classes,
not the Checker Framework compiler or other components.

The **QA test APK** continues to package its existing `sdk-licenses/` assets
independently: the original MediaPipe NOTICE, Apache text and complete
`litert-1.4.2-LICENSE` (SHA-256
`71c6915d04265772a0339bed47276942c678b45cc01534210ebe6984fd1aec65`).
Shared MediaPipe classes/native code may reside in the target app rather than
be duplicated in instrumentation. The standalone research LiteRT dependency
and its notices remain test-only; its package/privacy gate is unchanged.
