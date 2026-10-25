# MediaPipe Tasks Core for unsigned ARM64 phone QA

The local Maven coordinate `com.google.mediapipe:tasks-core:1.1.1-offline-50505c8` is selected only for the `qaAndroidTest` instrumentation configurations when `-PunsignedPhoneQa=true`. The unchanged `tasks-vision:1.1.1` probe requests `tasks-core:1.1.1`; `phone-qa.gradle` substitutes this pinned coordinate and restricts its resolution to the local repository. The production app dependency declarations remain unchanged.

This 8,325,540-byte AAR was built from Google MediaPipe source commit `50505c8312c7eacb1bd411cecdc4cc9fd08d53cb` using the official `//mediapipe/tasks/java/com/google/mediapipe/tasks/core:tasks_core` Bazel rule with `ENABLE_TASKS_USAGE_LOGGING=0`. Its SHA-256 is `e0e9365d753b990f9b0d68e61274dfa5736d8812dbde5679095aec5ca28bd6b2`. The build procedure is in `native/scripts/build-offline-mediapipe.sh`. The independent receipt is `recognition-evidence-20261024/offline-source-trial-2/independent-artifact-receipt.json` outside this repository.

The source build retains the Tasks Core Java APIs, its detector setup and lifecycle code, and the published ARM64 JNI library (SHA-256 `d80dfdfc6e034b7595137d035a279eddf70b028587510e99ede4a7e634c342a0`). Its `TasksStatsLoggerFactory.create` invokes the upstream `TasksStatsDummyLogger.create`, so usage telemetry code is absent. The Maven POM preserves the four nontelemetry compile dependencies and omits the three DataTransport dependencies associated with the removed logging path. No class shadowing, binary patch, or reflection override is used.

MediaPipe is licensed under Apache License 2.0. The AAR includes its original `META-INF/NOTICE`, byte identical to the published `tasks-core:1.1.1` NOTICE. The local POM preserves upstream author and license metadata. This artifact has been statically audited; physical phone behavior requires the separate phone validation.

## QA instrumentation APK license assets

AGP does not carry AAR `META-INF/NOTICE` files into the instrumentation APK. The `androidTestQa` assets source in `native/android/app/phone-qa.gradle` therefore packages the exact files in `licenses/sdk-licenses/` under `assets/sdk-licenses/` in the **QA test APK only**. `native/scripts/check-phone-instrumentation.py` requires each file to match its pinned source bytes. These assets do not enter the production app APK.

| File | Source | SHA-256 |
| --- | --- | --- |
| `mediapipe-tasks-1.1.1-NOTICE` | `META-INF/NOTICE` in the local offline `tasks-core` AAR; independently identical to the published Google Maven `tasks-core:1.1.1` and `tasks-vision:1.1.1` AAR notices | `8db2ce1a7fb4fa13d2261bcc4cd1f1c3044d94b59fb0e4b35a5c0b4ecb6a56d2` |
| `Apache-2.0-LICENSE` | Root `LICENSE`, the Apache License 2.0 terms declared by the MediaPipe core and vision POMs | `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30` |
| `litert-1.4.2-LICENSE` | `LICENSE` in the published Google Maven `com.google.ai.edge.litert:litert:1.4.2` AAR; identical to `litert-api:1.4.2` | `71c6915d04265772a0339bed47276942c678b45cc01534210ebe6984fd1aec65` |

The published LiteRT AARs contain no separate `NOTICE` entry; their POMs declare Apache License 2.0. The LiteRT `LICENSE` is kept whole because it contains more than the standard Apache text. This list covers the introduced MediaPipe and LiteRT SDK artifacts; it does not claim to inventory every transitive Android test dependency.
