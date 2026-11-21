#include "reply_asr.h"
#include "ggml-backend.h"

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
std::atomic<bool> injection_armed{false};
std::atomic<int> wrapped_calls{0};
std::atomic<int> injected_failures{0};

std::uint16_t u16(const unsigned char* bytes) {
    return std::uint16_t(bytes[0]) | (std::uint16_t(bytes[1]) << 8);
}

std::uint32_t u32(const unsigned char* bytes) {
    return std::uint32_t(bytes[0]) | (std::uint32_t(bytes[1]) << 8) |
           (std::uint32_t(bytes[2]) << 16) | (std::uint32_t(bytes[3]) << 24);
}

int floor_divide_by_three(int value) {
    int result = value / 3;
    if (value < 0 && value % 3) --result;
    return result;
}

std::vector<float> read_spoken_fixture(const char* path) {
    std::ifstream input(path, std::ios::binary | std::ios::ate);
    const auto file_size = input.tellg();
    if (file_size < 44 || file_size > 30 * 24000 * 2 + 4096) {
        throw std::runtime_error("invalid WAV fixture size");
    }
    std::vector<unsigned char> bytes(static_cast<std::size_t>(file_size));
    input.seekg(0);
    if (!input.read(reinterpret_cast<char*>(bytes.data()), file_size) ||
        std::memcmp(bytes.data(), "RIFF", 4) != 0 ||
        std::memcmp(bytes.data() + 8, "WAVE", 4) != 0) {
        throw std::runtime_error("invalid WAV fixture header");
    }

    const unsigned char* format = nullptr;
    const unsigned char* pcm = nullptr;
    std::size_t pcm_bytes = 0;
    for (std::size_t offset = 12; offset + 8 <= bytes.size();) {
        const auto chunk_bytes = static_cast<std::size_t>(u32(bytes.data() + offset + 4));
        const auto payload = offset + 8;
        if (payload + chunk_bytes > bytes.size()) {
            throw std::runtime_error("truncated WAV fixture chunk");
        }
        if (std::memcmp(bytes.data() + offset, "fmt ", 4) == 0 && chunk_bytes >= 16) {
            format = bytes.data() + payload;
        } else if (std::memcmp(bytes.data() + offset, "data", 4) == 0) {
            pcm = bytes.data() + payload;
            pcm_bytes = chunk_bytes;
        }
        offset = payload + chunk_bytes + (chunk_bytes & 1);
    }
    if (!format || !pcm || pcm_bytes == 0 || pcm_bytes % 2 ||
        u16(format) != 1 || u16(format + 2) != 1 || u32(format + 4) != 24000 ||
        u16(format + 14) != 16) {
        throw std::runtime_error("unexpected WAV fixture format");
    }

    std::vector<std::int16_t> source;
    source.reserve(pcm_bytes / 2);
    for (std::size_t i = 0; i < pcm_bytes; i += 2) {
        source.push_back(static_cast<std::int16_t>(u16(pcm + i)));
    }
    std::vector<float> result;
    result.reserve(source.size() * 2 / 3);
    for (std::size_t i = 0; i + 2 < source.size(); i += 3) {
        const int left = source[i];
        const int middle = source[i + 1];
        const int right = source[i + 2];
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
}

extern "C" enum ggml_status __real_ggml_backend_sched_graph_compute(
    ggml_backend_sched_t sched, struct ggml_cgraph* graph);

extern "C" enum ggml_status __wrap_ggml_backend_sched_graph_compute(
    ggml_backend_sched_t sched, struct ggml_cgraph* graph) {
    if (injection_armed.load(std::memory_order_acquire)) {
        wrapped_calls.fetch_add(1, std::memory_order_relaxed);
        int expected = 0;
        if (injected_failures.compare_exchange_strong(expected, 1)) {
            return GGML_STATUS_FAILED;
        }
    }
    return __real_ggml_backend_sched_graph_compute(sched, graph);
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
        auto fixture = read_spoken_fixture(argv[7]);
        const std::string expected_text = argv[8];

        const off_t model_position = 4;
        const off_t vad_position = 7;
        if (lseek(model_fd, model_position, SEEK_SET) != model_position ||
            lseek(vad_fd, vad_position, SEEK_SET) != vad_position) {
            return 65;
        }

        sts_reply_turn turn;
        sts_reply_engine* engine = nullptr;
        const auto open_status = sts_reply_open(
            model_fd, model_offset, model_length, vad_fd, vad_offset, vad_length,
            &turn, &engine);
        if (open_status != STS_REPLY_OK || !engine ||
            !position_is(model_fd, model_position) || !position_is(vad_fd, vad_position)) {
            std::cout << "STS_WRAPPER_VAD_COMPUTE_OPEN_FAIL status=" << open_status << std::endl;
            if (engine) sts_reply_close(engine);
            return 2;
        }
        std::cout << "STS_WRAPPER_VAD_COMPUTE_OPEN_PASS borrowed_spans=true" << std::endl;

        std::array<char, STS_REPLY_TEXT_CAPACITY> output;
        output.fill('x');
        injection_armed.store(true, std::memory_order_release);
        const auto failure_status = sts_reply_decode(
            engine, fixture.data(), fixture.size(), &turn, output.data(), output.size());
        injection_armed.store(false, std::memory_order_release);
        const bool output_cleared =
            std::all_of(output.begin(), output.end(), [](char value) { return value == 0; });
        const int calls = wrapped_calls.load();
        const int failures = injected_failures.load();
        std::cout << "STS_WRAPPER_VAD_COMPUTE_INJECTION wrapper_calls=" << calls
                  << " injected_failures=" << failures << " status=" << failure_status
                  << " output_cleared=" << output_cleared << std::endl;
        if (calls != 1 || failures != 1 || failure_status != STS_REPLY_DECODE_FAILED ||
            !output_cleared || !position_is(model_fd, model_position) ||
            !position_is(vad_fd, vad_position)) {
            sts_reply_close(engine);
            std::cout << "STS_WRAPPER_VAD_COMPUTE_MAPPING_RED" << std::endl;
            return 1;
        }

        output.fill('x');
        const auto retry_status = sts_reply_decode(
            engine, fixture.data(), fixture.size(), &turn, output.data(), output.size());
        const auto retry_bytes = strnlen(output.data(), output.size());
        const std::string retry_normalized =
            retry_bytes < output.size() ? normalize(output.data()) : "<unterminated>";
        const bool retry_text = retry_bytes < output.size() &&
                                retry_normalized == expected_text;
        const bool borrowed = position_is(model_fd, model_position) &&
                              position_is(vad_fd, vad_position);
        const auto close_status = sts_reply_close(engine);
        if (retry_status != STS_REPLY_OK || !retry_text || !borrowed ||
            close_status != STS_REPLY_OK || !position_is(model_fd, model_position) ||
            !position_is(vad_fd, vad_position)) {
            std::cout << "STS_WRAPPER_VAD_COMPUTE_RETRY_FAIL status=" << retry_status
                      << " text=" << retry_normalized << " borrowed=" << borrowed
                      << " close_status=" << close_status << std::endl;
            return 3;
        }
        std::cout << "STS_WRAPPER_VAD_COMPUTE_MAPPING_PASS status=" << failure_status
                  << " retry_status=" << retry_status << " text=" << expected_text
                  << " borrowed_spans=true" << std::endl;
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "wrapper VAD compute regression setup failure: " << error.what() << std::endl;
        return 66;
    }
}
