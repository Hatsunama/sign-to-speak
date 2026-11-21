#include "reply_asr.h"
#include "ggml-backend.h"
#include "whisper.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <sys/types.h>
#include <unistd.h>
#include <vector>

namespace {
std::atomic<bool> count_scheduler_calls{false};
std::atomic<int> scheduler_calls{0};
std::atomic<bool> cancellation_observed{false};
std::atomic<bool> cleanup_sentinel_injected{false};
std::atomic<int> cleanup_reset_calls{0};
std::atomic<bool> cleanup_probability_count_positive{false};
std::atomic<bool> cleanup_probabilities_zero{false};

std::uint16_t read_u16(const unsigned char* bytes) {
    return std::uint16_t(bytes[0]) | (std::uint16_t(bytes[1]) << 8);
}

std::uint32_t read_u32(const unsigned char* bytes) {
    return std::uint32_t(bytes[0]) | (std::uint32_t(bytes[1]) << 8) |
           (std::uint32_t(bytes[2]) << 16) | (std::uint32_t(bytes[3]) << 24);
}

int floor_divide_by_three(int value) {
    int result = value / 3;
    if (value < 0 && value % 3) --result;
    return result;
}

std::vector<float> read_fixture(const char* path) {
    std::ifstream input(path, std::ios::binary | std::ios::ate);
    const auto size = input.tellg();
    if (size < 44 || size > 30 * 24000 * 2 + 4096) {
        throw std::runtime_error("invalid WAV fixture size");
    }
    std::vector<unsigned char> bytes(static_cast<std::size_t>(size));
    input.seekg(0);
    if (!input.read(reinterpret_cast<char*>(bytes.data()), size) ||
        std::memcmp(bytes.data(), "RIFF", 4) != 0 ||
        std::memcmp(bytes.data() + 8, "WAVE", 4) != 0) {
        throw std::runtime_error("invalid WAV fixture header");
    }

    const unsigned char* format = nullptr;
    const unsigned char* pcm = nullptr;
    std::size_t pcm_bytes = 0;
    for (std::size_t offset = 12; offset + 8 <= bytes.size();) {
        const auto chunk_size = static_cast<std::size_t>(read_u32(bytes.data() + offset + 4));
        const auto payload = offset + 8;
        if (payload + chunk_size > bytes.size()) {
            throw std::runtime_error("truncated WAV fixture chunk");
        }
        if (std::memcmp(bytes.data() + offset, "fmt ", 4) == 0 && chunk_size >= 16) {
            format = bytes.data() + payload;
        } else if (std::memcmp(bytes.data() + offset, "data", 4) == 0) {
            pcm = bytes.data() + payload;
            pcm_bytes = chunk_size;
        }
        offset = payload + chunk_size + (chunk_size & 1);
    }
    if (!format || !pcm || pcm_bytes == 0 || pcm_bytes % 2 ||
        read_u16(format) != 1 || read_u16(format + 2) != 1 ||
        read_u32(format + 4) != 24000 || read_u16(format + 14) != 16) {
        throw std::runtime_error("unexpected WAV fixture format");
    }

    std::vector<float> result;
    result.reserve(pcm_bytes / 3);
    for (std::size_t i = 0; i + 5 < pcm_bytes; i += 6) {
        const int left = static_cast<std::int16_t>(read_u16(pcm + i));
        const int middle = static_cast<std::int16_t>(read_u16(pcm + i + 2));
        const int right = static_cast<std::int16_t>(read_u16(pcm + i + 4));
        result.push_back(float(floor_divide_by_three(2 * left + middle)) / 32768.f);
        result.push_back(float(floor_divide_by_three(middle + 2 * right)) / 32768.f);
    }
    return result;
}

std::string normalize(const char* text) {
    std::string result;
    bool space = false;
    for (unsigned char ch; (ch = *text++);) {
        if (ch == ' ' || ch == '\t' || ch == '\n' || ch == '\r') {
            space = !result.empty();
        } else if ((ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') ||
                   (ch >= '0' && ch <= '9')) {
            if (space) result.push_back(' ');
            space = false;
            result.push_back(ch >= 'A' && ch <= 'Z' ? ch + ('a' - 'A') : ch);
        }
    }
    return result;
}

bool position_is(int fd, off_t expected) {
    return lseek(fd, 0, SEEK_CUR) == expected;
}

struct CancelOnFirstPoll {
    sts_reply_turn* turn = nullptr;
    std::atomic<unsigned> polls{0};

    static void cancel(void* data) noexcept {
        auto& observer = *static_cast<CancelOnFirstPoll*>(data);
        if (observer.polls.fetch_add(1, std::memory_order_relaxed) == 0) {
            observer.turn->cancelled.store(true, std::memory_order_release);
            cancellation_observed.store(true, std::memory_order_release);
        }
    }
};
}

extern "C" enum ggml_status __real_ggml_backend_sched_graph_compute(
    ggml_backend_sched_t sched, struct ggml_cgraph* graph);

extern "C" enum ggml_status __wrap_ggml_backend_sched_graph_compute(
    ggml_backend_sched_t sched, struct ggml_cgraph* graph) {
    if (count_scheduler_calls.load(std::memory_order_acquire)) {
        scheduler_calls.fetch_add(1, std::memory_order_relaxed);
    }
    return __real_ggml_backend_sched_graph_compute(sched, graph);
}

extern "C" void __real_whisper_vad_reset_state(struct whisper_vad_context* context);
extern "C" float* __real_whisper_vad_probs(struct whisper_vad_context* context);

extern "C" float* __wrap_whisper_vad_probs(struct whisper_vad_context* context) {
    float* probabilities = __real_whisper_vad_probs(context);
    bool expected = false;
    if (cancellation_observed.load(std::memory_order_acquire) &&
        cleanup_sentinel_injected.compare_exchange_strong(
            expected, true, std::memory_order_acq_rel)) {
        const int count = whisper_vad_n_probs(context);
        if (count > 0 && probabilities != nullptr) {
            probabilities[0] = 0.5f;
        } else {
            cleanup_sentinel_injected.store(false, std::memory_order_release);
        }
    }
    return probabilities;
}

extern "C" void __wrap_whisper_vad_reset_state(struct whisper_vad_context* context) {
    if (cancellation_observed.load(std::memory_order_acquire)) {
        const int count = whisper_vad_n_probs(context);
        const float* probabilities = __real_whisper_vad_probs(context);
        bool all_zero = count > 0 && probabilities != nullptr;
        for (int i = 0; all_zero && i < count; ++i) {
            all_zero = probabilities[i] == 0.f;
        }
        cleanup_probability_count_positive.store(count > 0, std::memory_order_relaxed);
        cleanup_probabilities_zero.store(all_zero, std::memory_order_relaxed);
        cleanup_reset_calls.fetch_add(1, std::memory_order_relaxed);
    }
    __real_whisper_vad_reset_state(context);
}

int main(int argc, char** argv) {
    if (argc != 9) return 64;
    try {
        const int model_fd = std::stoi(argv[1]);
        const auto model_offset = std::stoll(argv[2]);
        const auto model_length = std::stoll(argv[3]);
        const int vad_fd = std::stoi(argv[4]);
        const auto vad_offset = std::stoll(argv[5]);
        const auto vad_length = std::stoll(argv[6]);
        const auto fixture = read_fixture(argv[7]);
        const auto expected_text = normalize(argv[8]);

        constexpr off_t model_position = 4;
        constexpr off_t vad_position = 7;
        if (lseek(model_fd, model_position, SEEK_SET) != model_position ||
            lseek(vad_fd, vad_position, SEEK_SET) != vad_position) {
            return 65;
        }

        sts_reply_turn open_turn;
        sts_reply_engine* engine = nullptr;
        const auto open_status = sts_reply_open(
            model_fd, model_offset, model_length, vad_fd, vad_offset, vad_length,
            &open_turn, &engine);
        if (open_status != STS_REPLY_OK || !engine ||
            !position_is(model_fd, model_position) || !position_is(vad_fd, vad_position)) {
            std::cout << "STS_VAD_CANCELLATION_OPEN_FAIL status=" << open_status << std::endl;
            if (engine) sts_reply_close(engine);
            return 2;
        }

        sts_reply_turn cancelled_turn;
        CancelOnFirstPoll observer{&cancelled_turn};
        cancelled_turn.on_abort_poll = CancelOnFirstPoll::cancel;
        cancelled_turn.observer = &observer;
        std::array<char, STS_REPLY_TEXT_CAPACITY> output;
        output.fill('x');
        scheduler_calls.store(0, std::memory_order_relaxed);
        cancellation_observed.store(false, std::memory_order_relaxed);
        cleanup_sentinel_injected.store(false, std::memory_order_relaxed);
        cleanup_reset_calls.store(0, std::memory_order_relaxed);
        cleanup_probability_count_positive.store(false, std::memory_order_relaxed);
        cleanup_probabilities_zero.store(false, std::memory_order_relaxed);
        count_scheduler_calls.store(true, std::memory_order_release);
        const auto cancelled_status = sts_reply_decode(
            engine, fixture.data(), fixture.size(), &cancelled_turn,
            output.data(), output.size());
        count_scheduler_calls.store(false, std::memory_order_release);

        const int calls = scheduler_calls.load(std::memory_order_relaxed);
        const unsigned polls = observer.polls.load(std::memory_order_relaxed);
        const bool output_cleared =
            std::all_of(output.begin(), output.end(), [](char value) { return value == 0; });
        const bool borrowed_after_cancel = position_is(model_fd, model_position) &&
                                           position_is(vad_fd, vad_position);
        const int reset_calls = cleanup_reset_calls.load(std::memory_order_relaxed);
        const bool sentinel_injected =
            cleanup_sentinel_injected.load(std::memory_order_relaxed);
        const bool probability_count_positive =
            cleanup_probability_count_positive.load(std::memory_order_relaxed);
        const bool probabilities_zero =
            cleanup_probabilities_zero.load(std::memory_order_relaxed);
        std::cout << "STS_VAD_CANCELLATION_FIRST status=" << cancelled_status
                  << " scheduler_calls=" << calls << " observer_polls=" << polls
                  << " output_cleared=" << output_cleared << std::endl;
        std::cout << "STS_VAD_CANCELLATION_CLEANUP reset_calls=" << reset_calls
                  << " sentinel_injected=" << sentinel_injected
                  << " probability_count_positive=" << probability_count_positive
                  << " probabilities_zero=" << probabilities_zero << std::endl;
        if (cancelled_status != STS_REPLY_CANCELLED || !output_cleared || polls == 0 ||
            calls != 1 || !borrowed_after_cancel || reset_calls < 1 ||
            !sentinel_injected || !probability_count_positive || !probabilities_zero) {
            sts_reply_close(engine);
            std::cout << "STS_VAD_CANCELLATION_RED" << std::endl;
            return 1;
        }

        sts_reply_turn retry_turn;
        output.fill('x');
        const auto retry_status = sts_reply_decode(
            engine, fixture.data(), fixture.size(), &retry_turn, output.data(), output.size());
        const auto text_bytes = strnlen(output.data(), output.size());
        const auto actual_text = text_bytes < output.size()
                                     ? normalize(output.data())
                                     : std::string("<unterminated>");
        const bool borrowed_after_retry = position_is(model_fd, model_position) &&
                                          position_is(vad_fd, vad_position);
        const auto close_status = sts_reply_close(engine);
        if (retry_status != STS_REPLY_OK || actual_text != expected_text ||
            !borrowed_after_retry || close_status != STS_REPLY_OK ||
            !position_is(model_fd, model_position) || !position_is(vad_fd, vad_position)) {
            std::cout << "STS_VAD_CANCELLATION_RETRY_FAIL status=" << retry_status
                      << " text=" << actual_text << " close_status=" << close_status
                      << std::endl;
            return 3;
        }

        std::cout << "STS_VAD_CANCELLATION_PASS scheduler_calls=" << calls
                  << " observer_polls=" << polls << " retry_text=" << expected_text
                  << " borrowed_spans=true cleanup_reset_calls=" << reset_calls
                  << " sentinel_injected=true probabilities_zero=true" << std::endl;
        return 0;
    } catch (const std::exception& error) {
        count_scheduler_calls.store(false, std::memory_order_release);
        std::cerr << "VAD cancellation regression setup failure: " << error.what() << std::endl;
        return 66;
    }
}
