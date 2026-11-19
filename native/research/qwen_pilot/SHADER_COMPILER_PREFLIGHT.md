# Android backend probe shader compiler preflight

The research JNI QA APK previously used Ubuntu Shaderc 2023.8. On the Galaxy S24 Ultra (API 37, Adreno 750), the CPU control passed, Vulkan Q4_K N=1 failed at creation of the `mul_mat_vec_q4_k_f32_f32` pipeline, and Q4_K N=2 reported numeric error 1.032. Seeker (API 36, Mali) passed the Q4_K N=1 and N=2 and Q6_K controls. The old compiler rejected the pinned llama.cpp `GL_EXT_integer_dot_product` feature test. The Android library reported `int dot: 0` on Adreno and lacked the packed-dot Q4_K shader, although it contained the `GGML_VK_FORCE_MMVQ` environment variable name.

## Compiler provenance

LunarG's Vulkan SDK 1.4.363.0 Linux config pins google/shaderc commit `75137cca3b41a59b8b3e5c42177da8a9490c042f`. That is the Shaderc v2026.4 release manifest commit. Its `known_good.json` names the actual buildable Shaderc source commit `b64fd42c4a2a9d501c47aca5d3571f7673a5e9cc`. The workflow checks out both GitHub commits and runs the pinned source's `utils/git-sync-deps` with its `DEPS`. It verifies the resolved source and all seven dependencies against the release manifest. [SDK configuration](https://vulkan.lunarg.com/sdk/config/1.4.363.0/linux/config.json) · [Release manifest](https://github.com/google/shaderc/blob/75137cca3b41a59b8b3e5c42177da8a9490c042f/known_good.json) · [Source DEPS](https://github.com/google/shaderc/blob/b64fd42c4a2a9d501c47aca5d3571f7673a5e9cc/DEPS)

| Repository | Commit |
| --- | --- |
| KhronosGroup/glslang | `e1b562a8bed273a02f30b59b66a5d499793cede5` |
| KhronosGroup/SPIRV-Headers | `04fd3caa1e8267e4d95c806cad901181728e1006` |
| KhronosGroup/SPIRV-Tools | `ef96ed763b43b59b33b31b362f09a02b729fa1c9` |
| abseil/abseil-cpp | `dbf88f932096c7f7714356e919f04749eb87c3e9` |
| google/re2 | `972a15cedd008d846f1a39b2e88ce48d7f166cbd` |
| google/effcee | `f8e8a164822d4f65e757bff66bc00e1567959aa0` |
| google/googletest | `52eb8108c5bdec04579160ae17225d66034bd723` |

The release manifest's SPIRV-Headers subdirectory describes the SDK layout. Shaderc's source build puts the same commit in `third_party/spirv-headers` as directed by `DEPS`. The Ubuntu runner builds the executable target `glslc_exe` in Release mode with tests, examples, and installation disabled and two parallel build jobs. GitHub Actions bounds the complete QA build to 35 minutes. [Build README](https://github.com/google/shaderc/blob/b64fd42c4a2a9d501c47aca5d3571f7673a5e9cc/README.md) · [Executable target](https://github.com/google/shaderc/blob/b64fd42c4a2a9d501c47aca5d3571f7673a5e9cc/glslc/CMakeLists.txt)

The source-built Linux x86-64 `glslc` replaces APT `glslc` for host shader compilation through the explicit `Vulkan_GLSLC_EXECUTABLE` path. Ubuntu still supplies the staged Vulkan and SPIR-V headers, `spirv-val`, and `spirv-dis`. The Android build uses NDK 27.3.13750724, ARM64, API 28, static C++, llama.cpp `b1b770d90d94cad9e9ea5b993bdbc383a522fb28`, and CPU and Vulkan backends. The APK manifest remains gated on SDK 36's `minSdkVersion` and `targetSdkVersion` badging output.

## Artifact evidence

The workflow checks the upstream feature test's compiler exit status and nonempty SPIR-V output, then checks the Android configure output and the host shader generator's cache for enabled integer-dot support. The pinned generator compiles the following Q4_K families from `mul_mat_vec_q4_k.comp` and `mul_mat_vecq.comp`, respectively. It retains the plain, `_subgroup`, and `_subgroup_no_shmem` variants as `.spv` files. [Feature detection and generation](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/ggml/src/ggml-vulkan/CMakeLists.txt) · [Generator](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/ggml/src/ggml-vulkan/vulkan-shaders/vulkan-shaders-gen.cpp)

- `mul_mat_vec_q4_k_f32_f32` is the baseline.
- `mul_mat_vec_q4_k_q8_1_f32` is the packed integer-dot path.

The QA archive includes compiler version, binary SHA-256, release manifest, DEPS, resolved commits, and compiler licenses. It includes the six shader binaries and SHA-256 hashes, Vulkan 1.2 validation using scalar block layout, and disassembly that must show a packed four-by-eight-bit integer dot instruction for each packed-dot variant. The workflow checks for the exact six module byte sequences and the packed-dot pipeline name in the final AArch64 library, then byte-compares the APK's library to that file. The host compiler stays on the CI runner. Failure diagnostics retain the available logs, caches and raw shader binaries.

These are build-time gates. A successful CI run would establish compiler support and shader inclusion. GPU dispatch and numerical behavior require phone runs with the new APK. The main app and the prior QA artifact are outside this workflow change.
