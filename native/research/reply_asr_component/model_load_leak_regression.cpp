#include "whisper.h"

#include <cstdio>
#include <cstring>
#include <stdexcept>

namespace {
struct Probe {
    std::FILE* model = nullptr;
    bool backend_allocated = false;
    bool failure_injected = false;
    unsigned close_calls = 0;
    std::size_t bytes_read = 0;
};

void observe_log(enum ggml_log_level, const char* text, void* user_data) {
    auto& probe = *static_cast<Probe*>(user_data);
    // v1.9.5 emits this only after a non-null backend buffer is installed in
    // model.buffers and immediately before it starts reading tensor records.
    if (text && std::strstr(text, " total size = ")) {
        probe.backend_allocated = true;
    }
}

std::size_t read_model(void* user_data, void* output, std::size_t count) {
    auto& probe = *static_cast<Probe*>(user_data);
    if (probe.backend_allocated && !probe.failure_injected) {
        probe.failure_injected = true;
        throw std::runtime_error("injected late model read failure");
    }
    const auto read = std::fread(output, 1, count, probe.model);
    probe.bytes_read += read;
    return read;
}

bool model_eof(void* user_data) {
    return std::feof(static_cast<Probe*>(user_data)->model) != 0;
}

void close_model(void* user_data) {
    auto& probe = *static_cast<Probe*>(user_data);
    ++probe.close_calls;
    if (probe.model) {
        std::fclose(probe.model);
        probe.model = nullptr;
    }
}
} // namespace

int main(int argc, char** argv) {
    if (argc != 2) {
        std::fprintf(stderr, "usage: %s MODEL\n", argv[0]);
        return 2;
    }

    Probe probe;
    probe.model = std::fopen(argv[1], "rb");
    if (!probe.model) {
        std::perror("open pinned model");
        return 2;
    }

    whisper_log_set(observe_log, &probe);
    whisper_model_loader loader{&probe, read_model, model_eof, close_model};
    auto params = whisper_context_default_params();
    params.use_gpu = false;
    whisper_context* context = whisper_init_with_params_no_state(&loader, params);

    const bool passed = context == nullptr && probe.backend_allocated &&
                        probe.failure_injected && probe.close_calls == 1;
    std::fprintf(stderr, "late-model-read-failure %s context_null=%d backend_allocated=%d "
                "failure_injected=%d close_calls=%u bytes_read=%zu\n",
                passed ? "pass" : "FAIL", context == nullptr, probe.backend_allocated,
                probe.failure_injected, probe.close_calls, probe.bytes_read);

    if (context) {
        whisper_free(context);
    }
    if (probe.model) {
        std::fclose(probe.model);
    }
    return passed ? 0 : 1;
}
