# Qwen multimodal logit probe

The probe runs the same still-image prefill as the multimodal CLI at llama.cpp revision `b1b770d90d94cad9e9ea5b993bdbc383a522fb28`. It records the raw text logits at the end of prefill and after four single-token decodes through the same context. The reference run feeds its raw argmax at each step. The replay run feeds the exact four reference IDs and records its own argmax for comparison.

## Build

Point CMake at a Git checkout of the pinned revision and reuse the Android smoke build's toolchain and backend flags. Build `qwen-backend-probe` and, when building the CLI tools, `llama-mtmd-cli` and `llama-mtmd-debug` from the same build directory.

```sh
cmake -S native/research/qwen_pilot/runtime_qa -B "$build_dir" -DLLAMA_SOURCE_DIR="$llama_checkout" \
  "${android_options[@]}"
cmake --build "$build_dir" --target qwen-backend-probe llama-mtmd-cli llama-mtmd-debug
```

The executable is in `$build_dir/bin/`. Use the same pinned model, projector, image, prompt, thread count, context size, and attention settings in both runs. The example assumes a Vulkan build with device `Vulkan0`, plus local files in the current directory.

```sh
QWEN_QA_OUT=cpu ./qwen-backend-probe --offline \
  -m "$model" --mmproj "$projector" --image "$image" -p "$prompt" -t 2 -c 1024 \
  --device none -ngl 0 --no-mmproj-offload

GGML_VK_DISABLE_F16=1 QWEN_QA_OUT=vulkan QWEN_QA_FORCE_TOKENS=cpu/tokens.txt \
  ./qwen-backend-probe --offline \
  -m "$model" --mmproj "$projector" --image "$image" -p "$prompt" -t 2 -c 1024 \
  --device Vulkan0 -ngl all --mmproj-device Vulkan0
```

The output name must be a fresh child directory. Pass local files and explicit CLI settings because upstream can resolve remote models while parsing arguments. The probe rejects remote selectors and implicit llama.cpp INI files. `-n` has no effect on the four continuation steps.

## Output

- `logits-0.f32` through `logits-4.f32` each contain `n_vocab` little-endian IEEE 754 floats in token ID order. Row zero follows prefill.
- `metadata.json` records the prompt, input chunks and text token IDs, build and device settings, and per-row positions, timing, finite counts, and argmax. In the replay, `steps[i].decoded_token` is the reference ID fed to produce row `i`.
- `tokens.txt` contains four token IDs, one per line. Use the reference file for `QWEN_QA_FORCE_TOKENS` on each replay.

A successful run prints `QA_LOGITS status=finite steps=5`. A nonfinite row is saved with its counts and the process exits nonzero. Decode errors also exit nonzero and preserve any earlier rows. The external comparison should check that both runs used identical text token IDs, chunk layouts, and continuation IDs before measuring logit differences.

## Attribution

Original Sign To Speak probe code and build files are licensed under Apache-2.0. Adapted upstream portions retain the following MIT notice.

The multimodal prefill sequence follows [llama.cpp's multimodal CLI](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/tools/mtmd/mtmd-cli.cpp). The upstream license applies to adapted code.

```text
MIT License

Copyright (c) 2023-2026 The ggml authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
