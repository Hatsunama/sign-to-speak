# Android OpenCL preflight

The Samsung Adreno 750 Vulkan run aborted while creating a Q4_K/F32 text pipeline. The vision-only Vulkan run with FP16 disabled produced comparable embeddings (NMSE about 1e-6), but its full logits failed the frozen raw guard (maximum NMSE 0.0192). CPU replay of the forced continuation was bit-identical. Samsung exposed an OEM `libOpenCL.so` on its public vendor list. The same llama.cpp revision documents Adreno 750 support and Q4_K and Q6_K support in the OpenCL backend. [Pinned llama.cpp OpenCL guide](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/docs/backend/OPENCL.md)

## GitHub build

The workflow builds Android arm64-v8a tools at API 28 with NDK 27.3.13750724 and static C++. It enables CPU, OpenCL, embedded source kernels and Adreno optimizations. It disables Vulkan, precompiled Adreno kernels, the server, RPC, and the same online features as the prior smoke workflow. It builds three explicit targets through the existing QA wrapper. The third, `test-backend-ops`, links GGML and threads. [Pinned test CMake target](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/tests/CMakeLists.txt#L367)

CI cross-builds Khronos's ICD Loader solely as a link input. Both Khronos sources use `v2025.07.22`. The staged headers and library live under the runner's temporary directory. The artifact carries only the three executables, provenance, source and license notices. Each executable has a direct `DT_NEEDED` for the device's `libOpenCL.so`. CI verifies the dependency allowlist, 16 KiB ELF load alignment, OpenCL imports, and absence of a `listen` import or embedded runtime search path. The diagnostic artifact preserves build output and readelf reports. [Khronos headers commit](https://github.com/KhronosGroup/OpenCL-Headers/commit/8a97ebc88daa3495d6f57ec10bb515224400186f) · [Khronos ICD Loader commit](https://github.com/KhronosGroup/OpenCL-ICD-Loader/commit/ad770a1b64c6b8d5f2ed4e153f22e4f45939f27f)

## Device evidence required

Use the device name reported by `llama-mtmd-cli --list-devices` as `opencl_device`. From a device shell, the upstream synthetic tests can check short-column Q4_K/F32 multiplication against the CPU reference and one Q6_K/F32 shape. Set `GGML_OPENCL_KERNEL_CACHE_DIR=0` to exercise kernel compilation from source. Retain stdout, stderr, selected backend and individual case statuses. A zero exit with all devices skipped, no matching cases, or unsupported cases does not establish a pass. [Pinned test switches](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/tests/test-backend-ops.cpp#L14473-L14489) · [Q4_K test shapes](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/tests/test-backend-ops.cpp#L11834-L11839) · [Q6_K test shape](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/tests/test-backend-ops.cpp#L11946)

```sh
GGML_OPENCL_KERNEL_CACHE_DIR=0 ./test-backend-ops test -b "$opencl_device" -o MUL_MAT \
  -p '^type_a=q4_K,type_b=f32,m=4096,n=[1-4],k=1024,'
GGML_OPENCL_KERNEL_CACHE_DIR=0 ./test-backend-ops test -b "$opencl_device" -o MUL_MAT \
  -p '^type_a=q6_K,type_b=f32,m=1000,n=21,k=4608,'
```

Run the same Qwen model, projector, image, prompt and frozen settings used in the Vulkan comparison. Verify text-only inference, then use the existing QA probe and CPU reference continuation IDs to compare the multimodal prefill and all four continuation rows under the frozen raw-logit guard. Capture the actual text and projector placements from device logs. OpenCL hardware enumeration and the synthetic kernel checks alone leave full-model parity and performance unverified.

The device shell and a target APK have different native-library visibility. For a future APK targeting Android 12 (API 31) or later, declare this inside `<application>` when testing optional OpenCL on supported devices. [Android manifest documentation](https://developer.android.com/guide/topics/manifest/uses-native-library-element)

```xml
<uses-native-library android:name="libOpenCL.so" android:required="false" />
```

Optional installation requires the app to handle missing or unusable OpenCL at runtime. Keep its CPU path loadable independently of OpenCL, and use a future probe inside the actual app process to verify OEM library resolution, device enumeration, a small kernel, and full Qwen parity. This build workflow covers shell tools. APK namespace behavior remains unverified.
