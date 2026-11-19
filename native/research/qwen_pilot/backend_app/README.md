# Sign To Speak Android backend probe

This test-only APK runs the pinned llama.cpp synthetic backend tests from a real Android app process. Android loads the probe through the app class loader. The APK declares no permissions and exposes no activities or launcher entry. The probe uses no model or input assets.

Compilation and device execution are pending. This diagnostic APK does not qualify the main application or a release.

## Build inputs

Build the Android `arm64-v8a` CMake target `sign_backend_probe` with `LLAMA_SOURCE_DIR` pointing to llama.cpp at `b1b770d90d94cad9e9ea5b993bdbc383a522fb28`. Use the CPU and Vulkan backends and the static C++ runtime. The shared library is at `build/lib/libsign_backend_probe.so`. The probe uses NDK `27.3.13750724` and API 28 to match the prior Vulkan CLI build. Its compile and target SDK are 36, matching the main app’s target SDK. The standalone `ProbeContractTest` under `tests` uses only the JDK.

## Instrumentation

Run the self-instrumentation with three arguments. Use a fresh runid containing exactly 32 lowercase hex characters, backend CPU or Vulkan0, and case q4_n1, q4_n2, or q6.

```sh
adb shell am instrument -w \
    -e runid 0123456789abcdef0123456789abcdef \
    -e backend Vulkan0 -e case q4_n1 \
    com.hatsunama.signtospeak.backendprobe/.ProbeInstrumentation
```

The app creates an exclusive child directory under its private files directory at files/qa-<runid>. The start receipt is run.json. The native adapter creates stdout.log exclusively and captures both stdout and stderr. The result.json receipt records the native exit code, any adapter or loader failure returned to Java, and the end time. Both receipts include the request, process ID, runtime Android API, app target SDK, and start time. The Android instrumentation returns `RESULT_OK` (`-1`) after a native exit of zero and a successful result write. A fatal native process exit may leave a start receipt and a partial log.

Require a matching result receipt with nativeExitCode equal to zero and nativeFailure null. Verify that the requested backend actually ran and that stdout.log shows the expected successful MUL_MAT cases on it. The upstream executable can return zero when its filters match no backend or case.

| Case | Operands | Matrix dimensions (m, n, k) | Expected successful tests |
| --- | --- | --- | --- |
| q4_n1 | Q4_K × F32 | 4096, 1, 1024 | 1 |
| q4_n2 | Q4_K × F32 | 4096, 2, 1024 | 1 |
| q6 | Q6_K × F32 | 1000, 21, 4608 | 2 (default buffer and weights buffer) |

## Attribution

Original Sign To Speak source and build files in this directory are licensed under Apache-2.0. The probe builds and links [llama.cpp's backend operator test source](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/tests/test-backend-ops.cpp) and ggml from the pinned checkout. CMake renames the upstream entry point at compile time for JNI. The upstream MIT notice is included in `COPYING.llama.cpp`.
