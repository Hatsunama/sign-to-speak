// SPDX-License-Identifier: Apache-2.0
// Multimodal prefill follows llama.cpp's mtmd-cli.cpp (MIT; see README.md).
#include "arg.h"
#include "chat.h"
#include "common.h"
#include "ggml-backend.h"
#include "log.h"
#include "mtmd-helper.h"

#include <nlohmann/json.hpp>

#include <algorithm>
#include <array>
#include <charconv>
#include <clocale>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <limits>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

namespace fs = std::filesystem;
using nlohmann::json;

constexpr int continuation_steps = 4;
static_assert(sizeof(float) == 4 && std::numeric_limits<float>::is_iec559,
              "The probe emits IEEE 754 binary32 logits");

static void require(bool condition, const std::string & error) {
    if (!condition) {
        throw std::runtime_error(error);
    }
}

static void require_ok(int code, const std::string & operation) {
    require(code == 0, operation + " failed with status " + std::to_string(code));
}

// The shared parser can resolve remote assets before it returns. Reject these
// selectors first, including the upstream parser's environment and INI inputs.
static void check_local_cli(int argc, char ** argv) {
    constexpr std::string_view blocked[] = {
        "-mu", "--model-url", "-mmu", "--mmproj-url", "-dr", "--docker-repo",
        "-hf", "-hfr", "--hf-repo", "-hff", "--hf-file", "--rpc", "--audio", "--video",
    };
    bool offline = false;
    for (int i = 1; i < argc; ++i) {
        std::string arg(argv[i]);
        if (arg.compare(0, 2, "--") == 0) {
            std::replace(arg.begin(), arg.end(), '_', '-');
        }
        offline |= arg == "--offline";
        require(std::find(std::begin(blocked), std::end(blocked), arg) == std::end(blocked),
                "expected --image and local GGUF paths; unsupported option " + arg);
    }
    const bool help = argc == 2 && (std::string_view(argv[1]) == "-h" || std::string_view(argv[1]) == "--help");
    require(offline || help, "pass --offline with -m, --mmproj, --image, and -p");
    for (const char * name : {"LLAMA_ARG_MODEL_URL", "LLAMA_ARG_MMPROJ_URL", "LLAMA_ARG_DOCKER_REPO",
                              "LLAMA_ARG_HF_REPO", "LLAMA_ARG_HF_FILE", "LLAMA_ARG_RPC"}) {
        const auto * value = std::getenv(name);
        require(!value || !*value, std::string("unset ") + name + " for this local probe");
    }
#ifdef _WIN32
    const auto * program_data = std::getenv("PROGRAMDATA");
    if (program_data && *program_data) {
        require(!fs::exists(fs::path(program_data) / "llama.cpp" / "config.ini"),
                "the probe requires CLI options in place of system llama.cpp config.ini");
    }
#else
    require(!fs::exists("/etc/llama.cpp/config.ini"),
            "the probe requires CLI options in place of system llama.cpp config.ini");
#endif
    fs::path user_config_dir;
    try {
        user_config_dir = fs_get_config_directory();
    } catch (const std::runtime_error &) {
        // The upstream parser also skips this file if the directory is unavailable.
    }
    require(user_config_dir.empty() || !fs::exists(user_config_dir / "config.ini"),
            "the probe requires CLI options in place of user llama.cpp config.ini");
}

static std::array<llama_token, continuation_steps> read_tokens(const fs::path & path, int n_vocab) {
    require(fs::is_regular_file(path), "QWEN_QA_FORCE_TOKENS must name a local regular file");
    std::ifstream in(path);
    require(in.is_open(), "cannot read QWEN_QA_FORCE_TOKENS");
    std::array<llama_token, continuation_steps> tokens{};
    std::string line;
    for (auto & token : tokens) {
        require(bool(std::getline(in, line)), "expected four forced token IDs, one per line");
        if (!line.empty() && line.back() == '\r') {
            line.pop_back();
        }
        const auto end = line.data() + line.size();
        const auto parsed = std::from_chars(line.data(), end, token);
        require(parsed.ec == std::errc{} && parsed.ptr == end && token >= 0 && token < n_vocab,
                "invalid forced token ID");
    }
    require(!std::getline(in, line) && in.eof() && !in.bad(), "expected exactly four forced token IDs");
    return tokens;
}

static void write_file(const fs::path & path, const char * data, std::streamsize size) {
    std::ofstream out;
    out.exceptions(std::ios::badbit | std::ios::failbit);
    out.open(path, std::ios::binary);
    out.write(data, size);
    out.close();
}

static void write_metadata(const fs::path & path, const json & meta) {
    const auto bytes = meta.dump(2, ' ', false, json::error_handler_t::replace) + "\n";
    write_file(path, bytes.data(), bytes.size());
}

static void print_usage(int, char ** argv) {
    std::printf("Usage: QWEN_QA_OUT=NAME %s --offline -m MODEL --mmproj PROJECTOR --image IMAGE -p PROMPT [options]\n", argv[0]);
}

static int run(int argc, char ** argv) {
    check_local_cli(argc, argv);
    const uint16_t byte_order = 1;
    require(*reinterpret_cast<const unsigned char *>(&byte_order) == 1, "a little-endian host is required");
    std::setlocale(LC_NUMERIC, "C");
    ggml_time_init();
    common_init();
    common_params params;
    params.offline = true;
    if (!common_params_parse(argc, argv, params, LLAMA_EXAMPLE_MTMD, print_usage)) {
        return 1;
    }
    require(params.offline && fs::is_regular_file(params.model.path) && fs::is_regular_file(params.mmproj.path),
            "pass --offline and two local GGUF files with -m and --mmproj");
    require(params.image.size() == 1 && fs::is_regular_file(params.image.front()) && !params.prompt.empty(),
            "pass one local --image file and a nonempty prompt");
    require(params.system_prompt.empty() && !params.embedding && !params.sampling.backend_sampling,
            "use a single user prompt with raw logits and backend sampling disabled");
    require(params.n_batch > 0, "batch size must be positive");

    const auto * name = std::getenv("QWEN_QA_OUT");
    const std::string_view basename = name ? name : "";
    require(!basename.empty() && basename != "." && basename != ".." &&
                basename.find_first_of("/\\:") == basename.npos,
            "QWEN_QA_OUT must be the basename of a fresh child directory");
    const fs::path output(name);
    require(fs::create_directory(output), "QWEN_QA_OUT already exists: " + output.string());

    mtmd_helper_log_set(common_log_default_callback, nullptr);
    ggml_backend_load_all();
    auto llama = common_init_from_params(params);
    require(llama && llama->model() && llama->context(), "model or context initialization failed");
    auto * model = llama->model();
    auto * ctx = llama->context();
    const int n_vocab = llama_vocab_n_tokens(llama_model_get_vocab(model));
    require(n_vocab > 0, "model vocabulary is empty");

    const auto * force_path = std::getenv("QWEN_QA_FORCE_TOKENS");
    const bool forced = force_path != nullptr;
    auto tokens = forced ? read_tokens(force_path, n_vocab) : std::array<llama_token, continuation_steps>{};

    auto vp = mtmd_context_params_default();
    vp.use_gpu = params.mmproj_use_gpu;
    vp.device = params.mmproj_device;
    vp.print_timings = true;
    vp.n_threads = params.cpuparams.n_threads;
    vp.flash_attn_type = params.flash_attn_type;
    vp.warmup = params.warmup;
    vp.image_min_tokens = params.image_min_tokens;
    vp.image_max_tokens = params.image_max_tokens;
    const auto memory = mtmd_get_memory_usage(params.mmproj.path.c_str(), vp);
    const int ubatch = llama_n_ubatch(ctx);
    if (memory.use_non_causal && memory.image_max_tokens > ubatch) {
        vp.image_max_tokens = ubatch;
        vp.image_min_tokens = std::min(vp.image_min_tokens, ubatch);
    }
    mtmd::context_ptr vision(mtmd_init_from_file(params.mmproj.path.c_str(), model, vp));
    require(vision && mtmd_support_vision(vision.get()), "vision initialization failed");

    const auto media = mtmd_helper_bitmap_init_from_file(vision.get(), params.image.front().c_str(),
                                                         false, mtmd_helper_init_opt_default());
    mtmd_helper::video_ptr video(media.video_ctx);
    mtmd::bitmap_ptr bitmap(media.bitmap);
    require(bitmap && !video && !mtmd_bitmap_is_audio(bitmap.get()), "expected a still image");

    require(llama_model_chat_template(model, nullptr) || !params.chat_template.empty(), "missing chat template");
    auto templates = common_chat_templates_init(model, params.chat_template);
    common_chat_msg user;
    user.role = "user";
    user.content = params.prompt;
    const std::string marker = mtmd_default_marker();
    if (user.content.find(marker) == std::string::npos) {
        user.content.insert(0, marker);
    }
    const auto formatted = common_chat_format_single(templates.get(), {}, user, true, params.use_jinja);
    const size_t split = formatted.find(marker);
    require(split != formatted.npos && formatted.find(marker, split + marker.size()) == formatted.npos,
            "the formatted prompt must have exactly one image marker");
    const mtmd_input_text before{formatted.data(), split, false, true};
    const size_t after_pos = split + marker.size();
    const mtmd_input_text after{formatted.data() + after_pos, formatted.size() - after_pos, false, true};
    const mtmd_input_part text1{&before, nullptr}, image{nullptr, bitmap.get()}, text2{&after, nullptr};
    const mtmd_input_part * parts[] = {&text1, &image, &text2};
    mtmd::input_chunks chunks(mtmd_input_chunks_init());
    require_ok(mtmd_tokenize_from_parts(vision.get(), chunks.ptr.get(), parts, 3, true), "multimodal tokenization");

    const size_t n_tokens = mtmd_helper_get_n_tokens(chunks.ptr.get());
    const auto prefix_positions = mtmd_helper_get_n_pos(chunks.ptr.get());
    require(prefix_positions > 0 && n_tokens + continuation_steps <= llama_n_ctx_seq(ctx) &&
                int64_t(prefix_positions) + continuation_steps <= llama_n_ctx_seq(ctx),
            "prompt plus four continuation tokens exceeds the sequence context");
    require(chunks.size() > 0 && mtmd_input_chunk_get_type(chunks[chunks.size() - 1]) == MTMD_INPUT_CHUNK_TYPE_TEXT &&
                mtmd_input_chunk_get_n_tokens(chunks[chunks.size() - 1]) > 0,
            "prefill must end with a nonempty text chunk to request its final logits");

    const auto * image_sha256 = mtmd_bitmap_get_id(bitmap.get());
    const auto * disable_f16 = std::getenv("GGML_VK_DISABLE_F16");
    json meta = {
        {"schema_version", 1}, {"status", "running"}, {"llama_revision", QWEN_QA_LLAMA_REVISION},
        {"ggml_commit", ggml_commit()},
        {"ggml_version", ggml_version()}, {"model", params.model.path}, {"mmproj", params.mmproj.path},
        {"image", params.image.front()}, {"image_sha256", image_sha256 ? image_sha256 : ""},
        {"image_width", mtmd_bitmap_get_nx(bitmap.get())}, {"image_height", mtmd_bitmap_get_ny(bitmap.get())},
        {"prompt", params.prompt}, {"formatted_prompt", formatted}, {"use_jinja", params.use_jinja},
        {"n_vocab", n_vocab}, {"dtype", "float32"}, {"byte_order", "little"},
        {"n_ctx", llama_n_ctx_seq(ctx)}, {"n_batch", params.n_batch},
        {"n_ubatch", ubatch}, {"n_threads", params.cpuparams.n_threads}, {"n_gpu_layers", params.n_gpu_layers},
        {"mmproj_use_gpu", vp.use_gpu}, {"mmproj_device", vp.device ? ggml_backend_dev_name(vp.device) : ""},
        {"ggml_vk_disable_f16", disable_f16 ? disable_f16 : ""},
        {"prefill_n_tokens", n_tokens}, {"prefill_n_past", prefix_positions}, {"forced", forced},
        {"chunks", json::array()}, {"steps", json::array()}, {"prefix_text_token_ids", json::array()},
    };
    meta["devices"] = json::array();
    for (const auto device : params.devices) {
        if (device) {
            meta["devices"].push_back(ggml_backend_dev_name(device));
        }
    }
    size_t n_images = 0;
    for (size_t i = 0; i < chunks.size(); ++i) {
        const auto * chunk = chunks[i];
        const auto type = mtmd_input_chunk_get_type(chunk);
        require(type == MTMD_INPUT_CHUNK_TYPE_TEXT || type == MTMD_INPUT_CHUNK_TYPE_IMAGE,
                "tokenization produced an unsupported media chunk");
        json info = {{"type", type == MTMD_INPUT_CHUNK_TYPE_TEXT ? "text" : "image"},
                     {"n_tokens", mtmd_input_chunk_get_n_tokens(chunk)}, {"n_pos", mtmd_input_chunk_get_n_pos(chunk)}};
        if (type == MTMD_INPUT_CHUNK_TYPE_TEXT) {
            size_t count;
            const auto * ids = mtmd_input_chunk_get_tokens_text(chunk, &count);
            for (size_t j = 0; j < count; ++j) {
                require(ids[j] >= 0 && ids[j] < n_vocab, "prefill text token is out of vocabulary");
                meta["prefix_text_token_ids"].push_back(ids[j]);
            }
        } else {
            ++n_images;
        }
        meta["chunks"].push_back(std::move(info));
    }
    require(n_images > 0, "tokenization produced no image chunks");
    meta["image_chunk_count"] = n_images;
    write_metadata(output / "metadata.json", meta);

    auto start = ggml_time_us();
    llama_pos n_past = 0;
    mtmd::batch_ptr vision_batch;
    for (size_t i = 0; i < chunks.size(); ++i) {
        const auto * chunk = chunks[i];
        auto next_pos = n_past;
        if (mtmd_input_chunk_get_type(chunk) == MTMD_INPUT_CHUNK_TYPE_TEXT) {
            require_ok(mtmd_helper_eval_chunk_single(vision.get(), ctx, chunk, n_past, 0, params.n_batch,
                                                     i + 1 == chunks.size(), &next_pos), "text prefill");
        } else {
            float * embd = vision_batch ? mtmd_batch_get_output_embd(vision_batch.get(), chunk) : nullptr;
            if (!embd) {
                vision_batch.reset(mtmd_batch_init(vision.get()));
                require(!!vision_batch, "vision batch initialization failed");
                require_ok(mtmd_batch_add_chunk(vision_batch.get(), chunk), "adding image chunk");
                int added = 1;
                for (size_t j = i + 1; j < chunks.size() &&
                        mtmd_input_chunk_get_type(chunks[j]) == MTMD_INPUT_CHUNK_TYPE_IMAGE; ++j) {
                    if (mtmd_batch_add_chunk(vision_batch.get(), chunks[j]) != 0) {
                        break;
                    }
                    ++added;
                }
                const auto image_start = ggml_time_ms();
                LOG_INF("encoding mtmd batch, n_chunks = %d (done = %zu, total = %zu)\n", added, i, chunks.size());
                require_ok(mtmd_batch_encode(vision_batch.get()), "image encoder");
                LOG_INF("mtmd batch encoding done in %d ms\n", int(ggml_time_ms() - image_start));
                embd = mtmd_batch_get_output_embd(vision_batch.get(), chunk);
            }
            require(embd, "image embedding missing from encoded batch");
            require_ok(mtmd_helper_decode_image_chunk(vision.get(), ctx, chunk, embd, n_past, 0,
                                                       params.n_batch, &next_pos, nullptr, nullptr), "image prefill");
        }
        require(next_pos >= n_past && next_pos <= prefix_positions, "prefill position out of range");
        n_past = next_pos;
    }
    require(n_past == prefix_positions, "prefill position count mismatch");

    common_batch batch(ctx);
    for (int step = 0; step <= continuation_steps; ++step) {
        const float * logits = llama_get_logits_ith(ctx, -1);
        const auto elapsed = ggml_time_us() - start;
        require(logits, "missing raw logits at step " + std::to_string(step));
        int finite_count = 0;
        llama_token argmax = LLAMA_TOKEN_NULL;
        for (int i = 0; i < n_vocab; ++i) {
            if (std::isfinite(logits[i])) {
                ++finite_count;
                if (argmax == LLAMA_TOKEN_NULL || logits[i] > logits[argmax]) {
                    argmax = i;
                }
            }
        }
        const std::string file = "logits-" + std::to_string(step) + ".f32";
        write_file(output / file, reinterpret_cast<const char *>(logits), std::streamsize(n_vocab) * sizeof(float));
        const bool finite = finite_count == n_vocab;
        json row = {{"index", step}, {"file", file}, {"shape", {n_vocab}}, {"n_past", n_past},
                    {"elapsed_us", elapsed}, {"finite_count", finite_count}, {"nonfinite_count", n_vocab - finite_count},
                    {"argmax", finite ? json(argmax) : json(nullptr)}};
        if (step > 0) {
            row["decoded_token"] = tokens[step - 1];
        }
        meta["steps"].push_back(std::move(row));
        if (!finite) {
            meta["status"] = "nonfinite";
        }
        write_metadata(output / "metadata.json", meta);
        require(finite, "nonfinite raw logits at step " + std::to_string(step) +
                        " (" + std::to_string(n_vocab - finite_count) + "/" + std::to_string(n_vocab) + ")");
        if (step == continuation_steps) {
            break;
        }
        if (!forced) {
            tokens[step] = argmax;
        }
        batch.clear();
        batch.add(tokens[step], n_past, 0, true);
        start = ggml_time_us();
        require_ok(llama_process(ctx, LLAMA_PROCESS_TYPE_DECODE, batch.get()), "forced continuation decode");
        ++n_past;
    }
    std::string token_lines;
    for (const auto token : tokens) {
        token_lines += std::to_string(token) + "\n";
    }
    write_file(output / "tokens.txt", token_lines.data(), token_lines.size());
    meta["status"] = "finite";
    meta["continuation_tokens"] = tokens;
    write_metadata(output / "metadata.json", meta);
    common_log_flush(common_log_main());
    std::puts("QA_LOGITS status=finite steps=5");
    return 0;
}

int main(int argc, char ** argv) {
    try {
        return run(argc, argv);
    } catch (const std::exception & error) {
        common_log_flush(common_log_main());
        std::fprintf(stderr, "QA_LOGITS status=error: %s\n", error.what());
        return 1;
    }
}
