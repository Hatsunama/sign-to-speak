# Qwen Android runtime preflight results

GitHub build and cloud conversion/quantization completed on November 18, 2026. Android inference is **unverified and blocked by missing connected phones**. This is a technical feasibility probe of the original base, not a new app release or a successful signing model. The failed A/B adapters remain separate.

## Exact artifact chain

- Base: Qwen/Qwen3.5-0.8B, revision `2fc06364715b967f1860aea9cf38778875588b17`; original shard SHA-256 `04b1c301231dd422b8860db31311ab2721511346a32cb1e079c4c4e5f1fe4696` checked before conversion.
- Converter/runtime: llama.cpp `b1b770d90d94cad9e9ea5b993bdbc383a522fb28` (v1.0.0). GitHub source commit `6655aa419ce1235dec77f1be2c1ed6d3631373ec`, [successful run 44443603415](https://github.com/Hatsunama/sign-to-speak/actions/runs/44443603415), artifact `14492872256`. Package SHA-256 `f6c6d0cf3ee78fb4022419edb757d15495b450d9c46b0101fa96c202b020ff58`, 37,525,579 compressed bytes. Local readback checked the digest, all nine manifest files, both source pins and bounded extraction paths. The original package and receipt were preserved privately beyond the seven-day Actions retention window.
- CPU-only Linux quantizer and Android arm64 MTMD CLI were built with NDK 27.3.13750724, API 28 and explicit 16 KiB ELF alignment. The hosted gates checked architecture, direct library allowlists, symbol smoke checks and PT_LOAD alignments. These checks do not demonstrate Android process execution or a network sandbox.
- Text F16: 1,557,660,640 bytes, SHA-256 `828d9f28d840f2a9612a0fc94f906e9e9eea7550048889c52b4f23435de4529b`.
- Text Q4_K_M: 547,308,512 bytes, SHA-256 `7245b80c81495eb999b520aefe933e76f7a5d17ef8e78bdaa353d54f5a19ea2c`.
- Vision F16: 204,987,008 bytes, SHA-256 `c6634f2207ece2cc948089217717c5c6e898924e7b55207270c98c62d049060e`.

The text metadata reports `qwen35`, 1024-wide embeddings, 25 total blocks and one NextN/MTP layer. The upstream config has 24 decoder layers plus that head. At this pin, normal MTMD inference excludes the optional head; quantization preserves it. The vision metadata reports `qwen3vl_merger`, a vision encoder and 1024-wide projection. The dimensions agree; numerical output parity remains unmeasured. [Converter](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/conversion/qwen.py) · [Layer handling](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/src/llama-hparams.cpp) · [Runtime](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/src/models/qwen35.cpp)

## Measured scope and remaining checks

F16 conversion used the pinned upstream requirements with a CPU-only Torch wheel. Text conversion took 11.68 seconds at 2,289,140 KiB peak process RSS; vision conversion took 4.13 seconds at 399,832 KiB. The complete conversion job cgroup peaked at 6,804,795,392 bytes with no OOM events. Quantization used the exact hosted Linux binary on a matching Ubuntu 24.04 container and took about 27.6 seconds. These are cloud CPU measurements, not phone performance.

The two final GGUF files total 752,295,520 bytes before the executable and runtime buffers. They remain in private project storage; they were not downloaded to this low-memory Windows PC. No local application/native build or model load was performed. Complete estimated execution for 19 project jobs is US$0.611818, excluding unverified invoice/storage/startup charges, within the shared US$14 authorization.

The host ADB check listed no devices. Neither phone was opened, changed, installed, sounded or taken from another agent. Reconnect and unlock them at Home when free before continuing. Recheck Home, lock state and competing instrumentation immediately before use. Verify hashes after USB transfer, then prove CPU text/vision initialization, actual nonzero image tokens, generated output, offline behavior, resource use and scoped cancellation/recovery. Return owned free phones to Home. Never treat a CLI smoke pass as final app acceptance.

The original model and both research adapters failed the small ASL task evaluation. A technically loadable Q4 file cannot remedy that quality failure. Model-format support, conversion, ELF alignment and successful installation are not translation, live-camera, full-app lifecycle, 16 KiB runtime or commercial-readiness evidence. No app appearance, behavior, signing identity or main-branch content changed.
