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

The current two-thread CPU route took 55.35 and 101.78 seconds for this low-resolution sequence workload. Memory fit, but interactive conversation latency is unqualified. A standard Vulkan companion was built in GitHub run 44458051361. Its default vision-offload output then failed the predeclared engineering comparison on both phones; a supported precision-setting probe subsequently met that same embedding guard on both phones. Full text-logit parity and application integration remain unverified. Driver capability declarations alone do not establish working offload, finite multimodal logits, numerical parity, speed or ASL quality. Preserve the CPU result as the comparator.

## Verification repairs and remaining limits

The first Samsung checker rejected an exit-zero inference because default logging hid image decode/performance traces and its RSS parser expected GNU output. The rerun used the documented `--perf --log-verbosity 4` flags, recognized Toybox KiB output, and required actual image-token batches, completed decode, positive generation elapsed time and uncancelled exit 0. The original failed verification receipt remains preserved.

Seeker initially disconnected before inference, then a prelaunch check rejected its retained legacy mobile-data preference. A restoration guard initially skipped its unexpected marker combination; the original values were explicitly restored before further work. A real route diagnostic showed airplane mode on, Wi-Fi off, legacy data preference still enabled and only loopback/dummy routes. The repaired verifier checks successful route output and rejects external default routes. The subsequent test changed only airplane mode and Wi-Fi, and restored the original settings. Regression tests cover these observed cases and reject online/occupied devices and mismatched PID identities.

Cancellation used SIGINT only after an actual image decode began, with executable path and process-start identity rechecked before signalling. Its exit 0 is a cancellation outcome, not an inference pass. Recovery ran in a new process. Peak RSS is the Android timing tool result for uncancelled runs; cancellation has sampled VmHWM only. Both phones were verified at Home with settings restored after testing; no other app was closed or interrupted.

The upstream recurrent-memory position warnings appeared here; the source warns and continues for M-RoPE image positions. Successful caption output is not general recurrent-state correctness or export-parity proof. The separate 1,024-image-token warning concerns spatial grounding; the workload budget was not changed to hide it. Both devices use 4 KiB pages, so this is not 16 KiB OS runtime verification.

The image/model/binary SHA-256 chain is unchanged from [export evidence](RUNTIME_PREFLIGHT_RESULTS.md). The app, its appearance/behavior, signing keys and main branch were unchanged. No customer inputs or trained adapters were used. The two research adapters still scored 0/8 on the earlier assessment; this runtime result does not repair that quality failure. Raw logs, private operational scripts, hardware identifiers and fixture files are backed up privately; aggregates and reproduction instructions are public.

## Vulkan numerical diagnosis

The initial GPU projector output failed the predeclared normalized-MSE ≤ 1e-4 and cosine ≥ 0.999 guard on both phones. With only `GGML_VK_DISABLE_F16=1` changed, normalized MSE was 1.035e-6 on Samsung and 1.084e-6 on Seeker, with cosine above 0.9999994 on both. All 287 observed vision graph nodes remained on Vulkan0; there was no CPU fallback. This disables FP16 shader arithmetic; it does not make the F16 projector weights or the whole model FP32. These are one-fixture engineering comparisons, not general numerical or ASL qualification.

Samsung's separate text-GPU run aborted during warmup with `compute pipeline creation failed for mul_mat_vec_q4_k_f32_f32` and `vk::Device::createComputePipeline: ErrorUnknown`. The selected pipeline is already F32 and is independent of the FP16-disable setting. It has not been repaired. Seeker's text-GPU path completed both default and FP16-disabled profiles, but successful captions and finite embeddings do not establish text-logit parity. A native QA tool now captures full-vocabulary logits after multimodal prefill and four CPU-forced continuation tokens so that the actual numerical behavior can be compared. GitHub run 44464130811 built that exact tool and the companion CLI. The downloaded archive digest and all 33 file digests were independently verified before Samsung execution.

The failed pipeline family is being retained as all three generated SPIR-V variants for validation and driver diagnosis. A valid SPIR-V module would not prove that a vendor driver can create its specialized pipeline. Shell-process results also require an intended-APK-context check before adoption. Both phones remained at Home and their changed radio settings were restored after these probes. No app or signing key was changed.

## Full-vocabulary Samsung follow-up

The exact QA binary recorded five rows of 248,320 logits after still-image prefill and four CPU-forced continuation tokens. CPU replay reproduced all 1,241,600 values bit-for-bit. Vision-only Vulkan offload with `GGML_VK_DISABLE_F16=1` retained all five argmax choices but failed the frozen raw-logit guard in all five rows: maximum normalized MSE 0.0192062 and minimum cosine 0.9909258. Centering the rows did not remove the difference. At the fifth row, total variation between the CPU and accelerated token distributions was 0.06234. The improved projector comparison therefore does not qualify end-to-end numerical behavior. No guard was weakened after observing these results. This is an engineering rejection for this fixture, not proof that the backend can never be used.

All three retained Q4_K F32 SPIR-V variants passed `spirv-val --target-env vulkan1.2 --scalar-block-layout` and disassembly in that build. Samsung's earlier driver pipeline-creation failure remains unresolved. Seeker was occupied by another app at the fresh staging check, so its new logit captures were deferred without touching the app. Both source and device evidence distinguish completed CPU checks from pending GPU qualification.
