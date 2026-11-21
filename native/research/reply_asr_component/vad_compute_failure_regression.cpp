#include "ggml-backend.h"
#include "whisper.h"

#include <atomic>
#include <iostream>
#include <vector>

namespace {
std::atomic<bool> injection_armed{false};
std::atomic<int> wrapped_calls{0};
std::atomic<int> injected_failures{0};

void quiet_log(enum ggml_log_level, const char*, void*) {}
}

extern "C" enum ggml_status __real_ggml_backend_sched_graph_compute(
    ggml_backend_sched_t sched, struct ggml_cgraph* graph);

extern "C" enum ggml_status __wrap_ggml_backend_sched_graph_compute(
    ggml_backend_sched_t sched, struct ggml_cgraph* graph) {
    if (injection_armed.load()) {
        wrapped_calls.fetch_add(1);
        int expected = 0;
        if (injected_failures.compare_exchange_strong(expected, 1)) {
            return GGML_STATUS_FAILED;
        }
    }
    return __real_ggml_backend_sched_graph_compute(sched, graph);
}

int main(int argc, char** argv) {
    if (argc != 2) return 64;

    ggml_backend_load_all();
    whisper_log_set(quiet_log, nullptr);
    auto params = whisper_vad_default_context_params();
    params.use_gpu = false;
    auto* context = whisper_vad_init_from_file_with_params(argv[1], params);
    if (!context) {
        std::cout << "STS_VAD_COMPUTE_MODEL_LOAD_FAIL" << std::endl;
        return 2;
    }
    std::cout << "STS_VAD_COMPUTE_MODEL_LOAD_PASS" << std::endl;

    std::vector<float> silence(32000, 0.0f);
    injection_armed.store(true);
    const bool detected =
        whisper_vad_detect_speech(context, silence.data(), static_cast<int>(silence.size()));
    injection_armed.store(false);
    whisper_vad_free(context);

    const int calls = wrapped_calls.load();
    const int failures = injected_failures.load();
    std::cout << "STS_VAD_COMPUTE_INJECTION wrapper_calls=" << calls
              << " injected_failures=" << failures << std::endl;
    if (calls != 1 || failures != 1) {
        std::cout << "STS_VAD_COMPUTE_INJECTION_INVALID" << std::endl;
        return 3;
    }
    if (detected) {
        std::cout << "STS_VAD_COMPUTE_FAILURE_PROPAGATION_RED detect_return=true"
                  << std::endl;
        return 1;
    }

    std::cout << "STS_VAD_COMPUTE_FAILURE_PROPAGATION_PASS detect_return=false"
              << std::endl;
    return 0;
}
