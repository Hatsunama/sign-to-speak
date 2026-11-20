#include "whisper.h"

#include <cstdio>
#include <cstring>

namespace {
constexpr std::size_t kPinnedVadBytes = 885098;

struct Probe {
    std::FILE* model = nullptr;
    bool backend_allocated = false;
    bool failure_injected = false;
    unsigned close_calls = 0;
    unsigned backend_read_calls = 0;
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
    // A VAD tensor record uses three header reads, three dimension reads, one
    // name read and one data read. Let that first record complete, then model a
    // real short read at the next record boundary. v1.9.5 ignores the returned
    // byte count, observes EOF, and takes its partial-model failure branch.
    if (probe.failure_injected ||
        (probe.backend_allocated && probe.backend_read_calls >= 8)) {
        probe.failure_injected = true;
        std::memset(output, 0, count);
        return 0;
    }
    const auto read = std::fread(output, 1, count, probe.model);
    probe.bytes_read += read;
    if (probe.backend_allocated) {
        ++probe.backend_read_calls;
    }
    return read;
}

bool model_eof(void* user_data) {
    const auto& probe = *static_cast<Probe*>(user_data);
    return probe.failure_injected || std::feof(probe.model) != 0;
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
    if (std::fseek(probe.model, 0, SEEK_END) != 0 ||
        std::ftell(probe.model) != static_cast<long>(kPinnedVadBytes) ||
        std::fseek(probe.model, 0, SEEK_SET) != 0) {
        std::fprintf(stderr, "STS_VAD_LEAK_TRIGGER_UNTRIGGERED reason=model-size\n");
        close_model(&probe);
        return 90;
    }

    whisper_log_set(observe_log, &probe);
    whisper_model_loader loader{&probe, read_model, model_eof, close_model};
    auto params = whisper_vad_default_context_params();
    params.use_gpu = false;
    whisper_vad_context* context = whisper_vad_init_with_params(&loader, params);

    if (probe.model) {
        close_model(&probe);
    }

    if (context) {
        std::fprintf(stderr, "STS_VAD_LATE_READ_MISREPORTED_SUCCESS reads=%u bytes=%zu\n",
                     probe.backend_read_calls, probe.bytes_read);
        whisper_vad_free(context);
        return 91;
    }
    if (!probe.backend_allocated || !probe.failure_injected || probe.backend_read_calls != 8) {
        std::fprintf(stderr,
                     "STS_VAD_LEAK_TRIGGER_UNTRIGGERED backend=%d injected=%d reads=%u bytes=%zu\n",
                     probe.backend_allocated, probe.failure_injected,
                     probe.backend_read_calls, probe.bytes_read);
        return 90;
    }

    // Returning normally is intentional: LeakSanitizer performs its process-exit
    // scan and must turn this otherwise-successful trigger into the expected red.
    std::fprintf(stderr,
                 "STS_EXPECTED_RED: VAD_LATE_READ_LEAK_TRIGGERED reads=%u bytes=%zu close_calls=%u\n",
                 probe.backend_read_calls, probe.bytes_read, probe.close_calls);
    return 0;
}
