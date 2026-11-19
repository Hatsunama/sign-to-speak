# Qwen3.5 Android export preflight

**Status.** Source review, GitHub compilation, cloud F16 conversion and Q4_K_M quantization completed. Android execution is pending. See RUNTIME_PREFLIGHT_RESULTS.md for exact measured hashes and limits. This checks fully local static-image inference on Android CPU. The prior ASL evaluation scored 0/8 for the original model and both tuned candidates. This procedure uses the original model for runtime feasibility. It provides no ASL accuracy or release result and does not exercise native video or change the existing decoder.

## Inputs and provenance

- HF model `Qwen/Qwen3.5-0.8B` at `2fc06364715b967f1860aea9cf38778875588b17`, Apache-2.0. The snapshot is about 1.77 GB. Its one 1.75 GB checkpoint shard has publisher SHA-256 `04b1c301231dd422b8860db31311ab2721511346a32cb1e079c4c4e5f1fe4696`. Take the model config, text tokenizer/chat template, vision preprocessor config, model card, and license from that same snapshot. [Pinned snapshot](https://huggingface.co/Qwen/Qwen3.5-0.8B/tree/2fc06364715b967f1860aea9cf38778875588b17) · [Published shard digest](https://huggingface.co/Qwen/Qwen3.5-0.8B/blob/2fc06364715b967f1860aea9cf38778875588b17/model.safetensors-00001-of-00001.safetensors)
- llama.cpp `v1.0.0` at `b1b770d90d94cad9e9ea5b993bdbc383a522fb28`. Use the successful artifacts from GitHub Actions run `44443603415` at build-repo commit `6655aa419ce1235dec77f1be2c1ed6d3631373ec`, after checking the run conclusion, upload IDs/digests, upstream pin, platform, CMake options, and binary/library SHA-256. Linux must have a runnable x86-64 `llama-quantize`. Android needs the CPU-only arm64 `llama-mtmd-cli` and any shared libraries from its build package, with `MTMD_VIDEO=OFF`. [Upstream release](https://github.com/ggml-org/llama.cpp/releases/tag/v1.0.0) · [MTMD build](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/tools/mtmd/CMakeLists.txt)
- For the smoke test, the local `static-asl-reference/danrasband-A0001-test.jpg` hashes to `4c22cf21532007e15036d69e6d511ed050cea28eeb6bbb65488fe19dd70bba71`. The matching original bytes `A/A0001_test.jpg` appear in `static-v1-review/manifest-approved.json` under the reviewed CC0 v1 set. This was a training-eligible image and is suitable here as a technical fixture. Use the photo locally with its hash and do not include it in the export job.

The model config has 24 layers, with Gated DeltaNet linear attention and full attention every fourth layer, plus a 12-layer vision encoder. The pinned converter resolves `Qwen3_5ForConditionalGeneration` to `Qwen3_5TextModel` / `qwen35` for text and `Qwen3VLVisionModel` / `qwen3vl_merger` for the vision GGUF. The pinned runtime has a recurrent/full-attention Qwen3.5 graph and builds the Qwen3-VL image graph independently of `MTMD_VIDEO`. The text GGUF and vision GGUF are a matched pair from one checkpoint. [Model config](https://huggingface.co/Qwen/Qwen3.5-0.8B/blob/2fc06364715b967f1860aea9cf38778875588b17/config.json) · [Converter dispatch](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/conversion/__init__.py#L408-L418) · [Qwen3.5 converter](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/conversion/qwen.py#L662) · [Vision converter](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/conversion/qwen3vl.py#L16-L50) · [Text runtime](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/src/models/qwen35.cpp) · [Image runtime](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/tools/mtmd/models/qwen3vl.cpp)

## Cloud resources

Select an x86-64 Linux CPU runner with 16 GB RAM, at least 12 GB free disk, Python 3.11, Git, GNU time, and a glibc compatible with the CI quantizer. Use one sequential run with a 30-minute outer timeout. HF Jobs CPU Basic documents 2 vCPU, 16 GB RAM, 50 GB ephemeral storage, and $0.01/hour, or approximately $0.005 compute for 30 minutes before any other charges. The complete 19-job execution estimate is $0.611818 of the shared $14, with invoice and storage/startup totals still unverified. Use an existing authorized artifact destination to persist output before an ephemeral runner exits. [HF Jobs pricing](https://huggingface.co/docs/hub/jobs-pricing)

Plan for ~1.56 GB text F16, ~0.55–0.65 GB text Q4_K_M, and ~0.20 GB vision F16. These are estimates, so record actual byte counts. The phone needs space for about 0.75–0.85 GB of model files plus the executable, libraries, and logs. Runtime RAM also depends on image, context, and graph buffers. [Upstream text size comparison](https://huggingface.co/ggml-org/Qwen3.5-0.8B-GGUF/tree/main)

The converter indexes the safetensors, uses lazy tensors by default, and can spool materialized output to disk. This avoids requiring two eager copies of the full model. A tensor conversion can still overlap its input and output allocations, and imports, tokenization, and page cache also consume memory. Eight GB is plausible with sequential lazy conversion and a disk-backed spool, but no peak memory result at this pin supports an 8 GB guarantee. Record each process peak and the job cgroup's memory peak/events where available. Skip `--no-lazy`. Do not combine `--use-temp-file` with split output. [Conversion base](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/conversion/base.py#L229-L274) · [GGUF writer](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/gguf-py/gguf/gguf_writer.py#L390-L409) · [Converter options](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/convert_hf_to_gguf.py#L54-L96)

## Cloud Linux procedure

Run conversion in a fresh disk-backed Linux working directory. For quantization, provision the successful Linux CI artifact by its verified receipt and set `QUANT` to its absolute executable path. Apply its packaged shared-library path if needed. Run `file` and inspect `ldd` output for resolved dependencies where the package is dynamic.

```bash
set -euo pipefail
mkdir -p source out logs spool
export TMPDIR="$PWD/spool"
export OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 NO_COLOR=1
unset NO_LOCAL_GGUF
pin=b1b770d90d94cad9e9ea5b993bdbc383a522fb28
repo=Qwen/Qwen3.5-0.8B
rev=2fc06364715b967f1860aea9cf38778875588b17

git -C source init -q
git -C source fetch --depth=1 https://github.com/ggml-org/llama.cpp.git "$pin"
git -C source checkout -q --detach FETCH_HEAD
test "$(git -C source rev-parse HEAD)" = "$pin"
printf '%s\n' "$repo@$rev" "$pin" > logs/source-pins.txt

python3.11 -m venv .venv
py="$PWD/.venv/bin/python"
"$py" -m pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu 'torch==2.11.0+cpu'
"$py" -m pip install --no-cache-dir --only-binary=:all: \
  -r source/requirements/requirements-convert_hf_to_gguf.txt
"$py" -m pip check
"$py" -m pip freeze --all > logs/python-dependencies.txt
"$py" --version > logs/python-version.txt
"$py" -c 'import torch; assert torch.version.cuda is None; print(torch.__version__)'

convert="$PWD/source/convert_hf_to_gguf.py"
"$py" "$convert" --help > logs/convert-help.txt
"$py" "$convert" --print-supported-models > logs/convert-models.txt 2>&1
grep -F -- '--mmproj' logs/convert-help.txt
grep -F 'Qwen3_5ForConditionalGeneration' logs/convert-models.txt
hf="$PWD/.venv/bin/hf"
"$hf" download --help > logs/download-help.txt
"$hf" download "$repo" --revision "$rev" --local-dir model --max-workers 2 2>&1 | tee logs/download.txt
printf '%s  %s\n' \
  04b1c301231dd422b8860db31311ab2721511346a32cb1e079c4c4e5f1fe4696 \
  model/model.safetensors-00001-of-00001.safetensors | sha256sum -c -
find model -type f ! -path 'model/.cache/*' -print0 | sort -z | xargs -0 sha256sum > logs/model-inputs.sha256

text_f16="$PWD/out/Qwen3.5-0.8B-F16.gguf"
vision_f16="$PWD/out/mmproj-Qwen3.5-0.8B-F16.gguf"

/usr/bin/time -v -o logs/text-f16.time "$py" "$convert" model \
  --outtype f16 --use-temp-file --outfile "$text_f16" 2>&1 | tee logs/text-f16.log
/usr/bin/time -v -o logs/vision-f16.time "$py" "$convert" model \
  --mmproj --outtype f16 --use-temp-file --outfile "$vision_f16" 2>&1 | tee logs/vision-f16.log
test -s "$text_f16"
test -s "$vision_f16"
cp model/LICENSE out/Qwen-BASE-LICENSE.txt
(cd out && sha256sum -- *.gguf Qwen-BASE-LICENSE.txt) > logs/f16-outputs.sha256
```

The F16 export can finish before the Linux quantizer artifact is available. In that case, persist the two F16 files, license, receipts, and logs. Run the next block in the same working directory or restore those files and the pinned source into that layout first. The metadata dump needs the Python environment installed above.

```bash
set -euo pipefail
test -x "$QUANT"
(cd out && sha256sum -c ../logs/f16-outputs.sha256)
sha256sum "$QUANT" > logs/quantize-binary.sha256
# At this pin the quantizer exits 1 after printing its usage.
quant_help_status=0
"$QUANT" --help > logs/quantize-help.txt 2>&1 || quant_help_status=$?
test "$quant_help_status" -le 1
grep -F -- '--dry-run' logs/quantize-help.txt
grep -F 'Q4_K_M' logs/quantize-help.txt

py="$PWD/.venv/bin/python"
text_f16="$PWD/out/Qwen3.5-0.8B-F16.gguf"
vision_f16="$PWD/out/mmproj-Qwen3.5-0.8B-F16.gguf"
text_q4="$PWD/out/Qwen3.5-0.8B-Q4_K_M.gguf"

"$QUANT" --dry-run "$text_f16" "$text_q4" Q4_K_M 2 2>&1 | tee logs/text-q4-plan.log
/usr/bin/time -v -o logs/text-q4.time "$QUANT" "$text_f16" "$text_q4" Q4_K_M 2 \
  2>&1 | tee logs/text-q4.log
test -s "$text_q4"

for gguf in "$text_f16" "$vision_f16" "$text_q4"; do
  PYTHONPATH="$PWD/source/gguf-py" "$py" -m gguf.scripts.gguf_dump \
    --json "$gguf" > "logs/$(basename "$gguf").json"
done
wc -c out/* > logs/output-bytes.txt
(cd out && sha256sum -- *.gguf Qwen-BASE-LICENSE.txt) > logs/outputs.sha256
```

The requirement file pins Torch 2.11.0 and inherits Transformers 4.57.6, NumPy, SentencePiece, GGUF and protobuf constraints. The explicit CPU-wheel install selects the PyTorch build that satisfies the upstream pin. Vendored `gguf-py` takes precedence for the converter and metadata dump. The converter uses a local, pinned snapshot directory. Its separate `--mmproj` pass selects vision tensors and preserves the explicitly supplied output basename. The Python `--outtype` selector has no Q4_K_M choice. Perform that step with the Linux quantizer on text F16 and keep the vision GGUF at F16. [Requirements](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/requirements/requirements-convert_hf_to_gguf.txt) · [Inherited requirements](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/requirements/requirements-convert_legacy_llama.txt) · [CPU wheels](https://download.pytorch.org/whl/cpu/torch/) · [Projector basename behavior](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/conversion/base.py#L2966-L2974) · [Quantizer usage](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/tools/quantize/quantize.cpp#L113-L172)

Before transferring, check both text dumps for architecture `qwen35`, tokenizer/chat template, recurrent/GDN and rotary metadata, and matching model dimensions. Confirm quantizer output labels the text type Q4_K_M with 4-bit mixed K tensor types. Confirm the projector dump has `general.type=mmproj`, `clip.projector_type=qwen3vl_merger`, `clip.has_vision_encoder=true`, and `clip.vision.projection_dim=1024` matching the language embedding dimension. Preserve the job/run identity, container image digest, exact command line, dependency versions, converter/source pin, build package receipts, file sizes and SHA-256, and timing/header logs. Publish to the authorized artifact destination only upon successful verification. Android requires the Q4 text GGUF and matching F16 vision GGUF. Keep text F16 as the export intermediate for direct comparison or reproducing quantization. [GGUF dump](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/gguf-py/gguf/scripts/gguf_dump.py#L445)

## Android CPU image smoke test

Stage the arm64 package, two final GGUFs, and the verified JPEG as `image.jpg` in a dedicated directory under `/data/local/tmp` on an authorized arm64 Android test device. Verify device API against the CI build's minimum API and verify hashes after transfer. Preserve any package libraries alongside the executable and set `LD_LIBRARY_PATH` as needed. Use USB ADB and turn device networking off for the measured smoke test. Run the following in the device shell. [Upstream Android workflow](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/docs/android.md)

```sh
cd /data/local/tmp/sign-to-speak-qwen-smoke || exit 1
chmod 755 llama-mtmd-cli
export LD_LIBRARY_PATH="$PWD:$PWD/lib"
./llama-mtmd-cli --version
./llama-mtmd-cli --help
time ./llama-mtmd-cli \
  -m Qwen3.5-0.8B-Q4_K_M.gguf \
  --mmproj mmproj-Qwen3.5-0.8B-F16.gguf --no-mmproj-offload \
  --image image.jpg -p 'Describe the visible hand in one short sentence.' \
  -c 1024 -n 32 -t 2 --temp 0
```

Check the built help for the flags above and capture the version and complete output locally. The CLI runs one turn when given both prompt and image and inserts the media marker itself. The pinned common parser's `--reasoning` and `--chat-template-kwargs` options are scoped to other executables, so leave them off this invocation. The 32-token cap is for the smoke test and may cut off default reasoning. Native `--video` requires the excluded video build path. [MTMD single-turn path](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/tools/mtmd/mtmd-cli.cpp#L492-L574) · [Parser scopes](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/common/arg.cpp#L2432-L2515) · [Reasoning flag scopes](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/common/arg.cpp#L3612-L3641)

Pass on an offline exit 0, successful CPU text and vision model initialization, an image encode/decode trace, and generated tokens. Confirm the trace shows an image chunk with nonzero tokens consumed. Save OS/API and ABI, device class, model/image/binary hashes, wall time, image time, prompt evaluation and generation rates, and peak RSS if the device exposes it. A load failure, missing image trace, unsupported operator, timeout, crash, or OOM is a runtime failure. [MTMD image evaluation and logging](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/tools/mtmd/mtmd-helper.cpp#L204-L292)

