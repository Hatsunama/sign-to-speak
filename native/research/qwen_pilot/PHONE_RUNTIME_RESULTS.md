# Android CPU and Vulkan runtime checkpoint — November 19, 2026

The exact pinned original Qwen base ran offline on Samsung Galaxy S24 (API 37) and Seeker (API 36). Both passed single-image inference, PID-verified cancellation, fresh-process recovery and a bounded 32-still workload. This is a native CLI engineering result, not an app release or ASL recognition result.

| CPU test, two threads | Samsung | Seeker |
| --- | ---: | ---: |
| Fresh-process image, seconds | 4.001 | 8.075 |
| Recovery image, seconds | 3.826 | 7.220 |
| 32 synthetic stills, seconds | 55.347 | 101.779 |
| Image peak RSS, KiB | 884880 | 885272 |
| 32-still peak RSS, KiB | 946728 | 947024 |
| Confirmed SIGINT to exit, seconds | 0.625 | 1.656 |

The 32 inputs were distinct 200×200 PNG variants of the same permitted hand photograph, with small synthetic background patches. Each run consumed all 32 positive image batches and 1,152 image tokens, with positive generation timing and exit 0. Context was 6,144 tokens; the single-image profile used 1,024. These are still images, not native video or representative motion/accuracy tests. Filesystem caches were warm from staging and hash verification. Each measured process started fresh; neither phone was rebooted or cache-flushed.

The current two-thread CPU route took 55.35 and 101.78 seconds for this low-resolution sequence workload. Memory fit, but interactive conversation latency is unqualified. A standard Vulkan companion was built in GitHub run 44458051361. Its default vision-offload output then failed the predeclared engineering comparison on both phones; a supported precision-setting probe subsequently met that same embedding guard on both phones. Subsequent full-model raw-logit comparisons failed the frozen guard. Production app integration remains unqualified. Driver capability declarations alone do not establish working offload, finite multimodal logits, numerical parity, speed or ASL quality. Preserve the CPU result as the comparator.

## Verification repairs and remaining limits

The first Samsung checker rejected an exit-zero inference because default logging hid image decode/performance traces and its RSS parser expected GNU output. The rerun used the documented `--perf --log-verbosity 4` flags, recognized Toybox KiB output, and required actual image-token batches, completed decode, positive generation elapsed time and uncancelled exit 0. The original failed verification receipt remains preserved.

Seeker initially disconnected before inference, then a prelaunch check rejected its retained legacy mobile-data preference. A restoration guard initially skipped its unexpected marker combination; the original values were explicitly restored before further work. A real route diagnostic showed airplane mode on, Wi-Fi off, legacy data preference still enabled and only loopback/dummy routes. The repaired verifier checks successful route output and rejects external default routes. The subsequent test changed only airplane mode and Wi-Fi, and restored the original settings. Regression tests cover these observed cases and reject online/occupied devices and mismatched PID identities.

Cancellation used SIGINT only after an actual image decode began, with executable path and process-start identity rechecked before signalling. Its exit 0 is a cancellation outcome, not an inference pass. Recovery ran in a new process. Peak RSS is the Android timing tool result for uncancelled runs; cancellation has sampled VmHWM only. Both phones were verified at Home with settings restored after testing; no other app was closed or interrupted.

The upstream recurrent-memory position warnings appeared here; the source warns and continues for M-RoPE image positions. Successful caption output is not general recurrent-state correctness or export-parity proof. The separate 1,024-image-token warning concerns spatial grounding; the workload budget was not changed to hide it. Both devices use 4 KiB pages, so this is not 16 KiB OS runtime verification.

The image/model/binary SHA-256 chain is unchanged from [export evidence](RUNTIME_PREFLIGHT_RESULTS.md). The CPU checkpoint used the native CLI. The later diagnostic APK signing is recorded below. No customer inputs or trained adapters were used. The two research adapters still scored 0/8 on the earlier assessment; this runtime result does not repair that quality failure. Raw logs, private operational scripts, hardware identifiers and fixture files are backed up privately; aggregates and reproduction instructions are public.

## Vulkan numerical diagnosis

The initial GPU projector output failed the predeclared normalized-MSE ≤ 1e-4 and cosine ≥ 0.999 guard on both phones. With only `GGML_VK_DISABLE_F16=1` changed, normalized MSE was 1.035e-6 on Samsung and 1.084e-6 on Seeker, with cosine above 0.9999994 on both. All 287 observed vision graph nodes remained on Vulkan0; there was no CPU fallback. This disables FP16 shader arithmetic; it does not make the F16 projector weights or the whole model FP32. These are one-fixture engineering comparisons, not general numerical or ASL qualification.

Samsung's separate text-GPU run aborted during warmup with `compute pipeline creation failed for mul_mat_vec_q4_k_f32_f32` and `vk::Device::createComputePipeline: ErrorUnknown`. The selected pipeline is already F32 and is independent of the FP16-disable setting. It has not been repaired. Seeker's text-GPU path completed both default and FP16-disabled profiles, but successful captions and finite embeddings do not establish text-logit parity. A native QA tool now captures full-vocabulary logits after multimodal prefill and four CPU-forced continuation tokens so that the actual numerical behavior can be compared. GitHub run 44464130811 built that exact tool and the companion CLI. The downloaded archive digest and all 33 file digests were independently verified before Samsung execution.

The three generated SPIR-V variants for the failed pipeline family were retained for validation and driver diagnosis. A valid SPIR-V module would not prove that a vendor driver can create its specialized pipeline. Shell-process results also require an intended-APK-context check before adoption. Both phones remained at Home and their changed radio settings were restored after these probes. These Vulkan CLI probes ran from a shell process.

## Full-vocabulary Samsung follow-up

The exact QA binary recorded five rows of 248,320 logits after still-image prefill and four CPU-forced continuation tokens. CPU replay reproduced all 1,241,600 values bit-for-bit. Vision-only Vulkan offload with `GGML_VK_DISABLE_F16=1` retained all five argmax choices but failed the frozen raw-logit guard in all five rows: maximum normalized MSE 0.0192062 and minimum cosine 0.9909258. Centering the rows did not remove the difference. At the fifth row, total variation between the CPU and accelerated token distributions was 0.06234. The improved projector comparison therefore does not qualify end-to-end numerical behavior. No guard was weakened after observing these results. This is an engineering rejection for this fixture, not proof that the backend can never be used.

All three retained Q4_K F32 SPIR-V variants passed `spirv-val --target-env vulkan1.2 --scalar-block-layout` and disassembly in that build. Samsung's earlier driver pipeline-creation failure remains unresolved. Seeker was initially occupied by another app and was left untouched. After it returned to Home, the new captures were completed as described below. Both source and device evidence distinguish completed CPU checks from pending GPU qualification.

## OpenCL alternative — verified failure before adoption

The supported OpenCL alternative built in GitHub run 44466748756 from source c0613186f52b40f604c6fd96770cb2ea44be3266. The exact archive SHA-256 is `27f3beb7cce2617a7b55325948f3a5ba9af2179631c9b20e44ff3c5f9f73a216`; all 18 manifest files were verified. It contains the CLI, native logit probe and pinned upstream operation tester, with direct OEM `libOpenCL.so` linkage and no packaged loader. Samsung enumerated the expected Adreno 750. CPU forced replay again reproduced all 1,241,600 logits exactly.

Text OpenCL offload terminated after image prefill with timing-tool exit 9. A self-SIGKILL control produced that same timer status; targeted available logs did not identify a sender or cause. The new result is a failed run, not a successful acceleration result. The narrow upstream Q4_K/F32 matrix case (`m=4096,n=1,k=1024`) then reported error 327.837468124 against its 0.0005 limit before the four-case process also ended with exit 9. Peak RSS was 98,604 KiB, available memory was about 3 GiB, and the global kernel OOM-kill counter stayed at zero. That rejects a simple large-model-memory explanation for the numerical mismatch; it does not identify the sender of SIGKILL or prove a particular vendor-driver defect. The test framework normally continues after a numerical failure, so the termination could have happened during a later case.

The pinned source exposes `GGML_OPENCL_Q4K_GEMV_WIDE=0`, a supported way to change the single-column kernel's launch geometry without changing its shader or packing. The controlled single-case comparison below isolates that setting. No backend setting has been adopted in the app.

Two verifier issues were repaired before runtime claims: the OpenCL device is named `GPUOpenCL` by the pinned implementation, and the explicit disabled proprietary-kernel flag needed a Boolean CMake cache type for the strict CI gate. Regression cases failed before each repair. Neither repair is evidence that OpenCL computation is correct.

## Seeker completed comparison

Seeker's CPU replay reproduced all 1,241,600 values exactly. Its Vulkan text profile completed with 5/5 matching argmaxes, but maximum raw-logit NMSE was 0.0211627 (default) and 0.0159169 (FP16 shader arithmetic disabled). Minimum cosine was 0.9898729 and 0.9923168, respectively. The disabled-FP16 vision-only profile also completed with 5/5 matching argmaxes, NMSE 0.0320071 and cosine 0.9852110. All failed the frozen raw-logit guard. The predeclared qualification rule therefore skipped full offload. Seeker returned to Home with the changed radio settings restored.

The configured fast CPU path is a comparator, not an exact-arithmetic oracle. Source inspection confirms Q4_K/Q6_K CPU matmuls can quantize F32 activations to Q8_K; even the upstream generic `use_ref` path retains that packing. OpenCL's sampled Q4 kernel uses F32 activations, and Vulkan selects different arithmetic by device and shape. These facts make numerical drift plausible but do not attribute the measured differences or prove that they are acceptable. The frozen guard remains failed. A later accuracy-qualified experiment needs predeclared held-out task measures and paired target-token likelihood or probability-distribution measures. Five matching greedy choices do not establish ASL quality. [CPU implementation](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/ggml/src/ggml-cpu/ggml-cpu.c#L1287)

## Samsung OpenCL workgroup follow-up

The isolated default Q4_K `m=4096,n=1,k=1024` case reproduced a numerical failure and returned the normal test failure code 1. With only `GGML_OPENCL_Q4K_GEMV_WIDE=0` changed, that same case passed at the unchanged upstream tolerance. The four Q4_K cases at n=1–4 then passed, and both selected Q6_K cases passed. This validates those sampled operations for the supported workgroup setting; it does not identify every failing launch or qualify a model.

The Q6 verifier originally expected one case although its prefix filter selects two distinct cases, with and without `weights=1`. Recorded hardware output showed both numeric OK and 2/2. Tests reproduced rejection of the valid capture and acceptance of a misleading one-case capture before the verifier was repaired to require both. The original log/result were preserved; a separate revalidation receipt binds the accepted capture and new checker hashes.

Full Qwen text offload with the narrow-workgroup setting subsequently completed and emitted finite logits, but only 3/5 argmaxes matched CPU. Maximum raw-logit NMSE was 1.295818, minimum cosine 0.155632, and maximum absolute difference 32.1132. The setting fixes the sampled operator failure; it is not a complete model repair and was not adopted in the application.

With `GGML_OPENCL_Q6K_GEMV_O4_GLOBAL=0` added to the Q4 narrow-workgroup setting, a second Samsung full-model OpenCL run completed. The SHA-256 of each of its five captured logit rows matched the corresponding Q4-narrow row. The result again failed the original guard with 3/5 matching argmaxes, maximum normalized MSE 1.295818, and minimum cosine 0.155632.

## Android process-context control

GitHub run 44474477241 built the test-only JNI probe from source `7ba384d5efae7ab4ab273a34bce24e762e8dce58`. The probe uses the CLI's pinned NDK/API settings and target SDK 36 to run the same synthetic upstream Q4_K and Q6_K matrix operations inside an Android app process. The existing protected QA key was used only for local APK signing. No private key was exported, and production signing keys were unused. The signed APK SHA-256 was `0c0e5c5ad9e998573a0e6eededf35f05ab178d69132de29ec967fd8b05ec7ab2`. All seven runs matched that APK digest.

| Operator | Samsung (API 37) | Seeker (API 36) |
| --- | --- | --- |
| CPU Q4_K N=1 | Passed 1/1 | Passed 1/1 |
| Vulkan Q4_K N=1 | Pipeline creation exception in `mul_mat_vec_q4_k_f32_f32` (`ErrorUnknown`), native exit 70 | Passed 1/1 |
| Vulkan Q4_K N=2 | Numerical failure, 1.032083371 > 0.0005, native exit 1 | Passed 1/1 |
| Vulkan Q6_K | No capture | Passed both cases, with and without `weights=1` |

The Samsung N=1 pipeline failure therefore reproduced inside the test APK. The N=2 test reached the numerical comparison and failed it. Seeker's synthetic operator passes and its earlier full-model Vulkan parity failures both stand.

## Shaderc v2026.4 follow-up

GitHub build 44480464845 failed during host CMake generation before compilation, with source `fc1ce54d6c05a4ee163b293d36acc329beb3439a`. The failure exposed inconsistent dependency install-export options. A configure-only regression reproduced the optimizer export errors and passed after the dependency export sets were enabled together.

The corrected GitHub build 44481814847 completed at source `7838d6947770aa3b046120759b0004552dcced65`. It used Shaderc v2026.4 buildable source `b64fd42c4a2a9d501c47aca5d3571f7673a5e9cc`, pinned by release manifest commit `75137cca3b41a59b8b3e5c42177da8a9490c042f`. The archive SHA-256 was `6fc7cabd30e679256b756018f030d274ca8bb58a7ec32c7f9d8b2d2ffa4d3370`. Verification covered 65 manifest files, matched the APK's native payload to the standalone library, and found all six expected baseline F32 and packed-dot shader modules in that library. Native compilation ran on GitHub. The existing QA debug key was used only for local APK signing. The key stayed in its protected keystore, with no export or production key use. Each of the 13 captured runs matched signed APK SHA-256 `7e59902bc7fb22fd42846c5c106d4cfe09e1ad20abfbd135ef79a84c537c9843`. [Shader compiler preflight](SHADER_COMPILER_PREFLIGHT.md) · [Probe process modes](backend_app/README.md)

| App-process operator and mode | Samsung (API 37) | Seeker (API 36) |
| --- | --- | --- |
| CPU Q4_K N=1, baseline | Passed 1/1 | Passed 1/1 |
| Vulkan Q4_K N=1, baseline | Pipeline creation `ErrorUnknown`, native exit 70 | Passed 1/1 |
| Vulkan Q4_K N=1, `f32` | Pipeline creation `ErrorUnknown`, native exit 70 | No capture |
| Vulkan Q4_K N=1, `mmvq` | Numerical error 15.681660930, native exit 1 | Passed 1/1 |
| Vulkan Q4_K N=1, `f32_mmvq` | Numerical error 2.843326668, native exit 1 | Passed 1/1 |
| Vulkan Q4_K N=2, baseline | Numerical error 1.170434364, native exit 1 | Passed 1/1 |
| Vulkan Q6_K, baseline | Numerical errors 264518.813343281 and 280064.594685293, native exit 1 | Passed 2/2 |

The upstream numeric limit was 0.0005. Both Samsung Q4_K N=1 pipeline failures named `mul_mat_vec_q4_k_f32_f32`. The `f32` profile sets `GGML_VK_DISABLE_F16=1`, and `mmvq` sets `GGML_VK_FORCE_MMVQ=1`. The `f32_mmvq` profile sets both, and baseline clears both. The forced modes reached numeric comparison on Samsung and failed. The Q6_K rows are the two selected cases without and with `weights=1`, respectively.

The new APK exercised synthetic operators. The earlier full-model raw-logit guard failures still govern GPU qualification, and the controls provide no new full-model parity or ASL quality evidence. Production app UI and main were untouched. At the recorded 08:01 UTC readback on November 19, both phones were at Home, media volume was 0 and the diagnostic processes were absent. The readback recorded no radio or volume changes.
