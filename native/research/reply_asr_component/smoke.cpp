#include "reply_asr.h"

#include <algorithm>
#include <array>
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <limits>
#include <mutex>
#include <stdexcept>
#include <string>
#include <sys/resource.h>
#include <thread>
#include <unistd.h>
#include <utility>
#include <vector>

namespace {
using Clock = std::chrono::steady_clock;
using Text = std::array<char, STS_REPLY_TEXT_CAPACITY>;
bool all_passed = true;

void check(const char* name, bool passed, sts_reply_status status = STS_REPLY_OK,
           std::size_t text_bytes = 0, double elapsed_ms = 0) {
    std::cout << name << " " << (passed ? "pass" : "FAIL") << " status=" << status
              << " text_bytes=" << text_bytes << " elapsed_ms=" << elapsed_ms << std::endl;
    all_passed &= passed;
}

double elapsed(Clock::time_point start) {
    return std::chrono::duration<double, std::milli>(Clock::now() - start).count();
}

std::vector<float> read_pcm(const char* path) {
    std::ifstream input(path, std::ios::binary | std::ios::ate);
    const auto length = input.tellg();
    if (length <= 0 || length > static_cast<std::streamoff>(STS_REPLY_MAX_SAMPLES * 2) ||
        static_cast<std::streamoff>(length) % 2) {
        throw std::runtime_error("invalid PCM fixture size");
    }
    std::vector<unsigned char> bytes(static_cast<std::size_t>(length));
    input.seekg(0);
    if (!input.read(reinterpret_cast<char*>(bytes.data()), length)) {
        throw std::runtime_error("invalid PCM fixture read");
    }
    std::vector<float> pcm;
    pcm.reserve(bytes.size() / 2);
    for (std::size_t i = 0; i < bytes.size(); i += 2) {
        const auto raw = int(bytes[i]) | (int(bytes[i + 1]) << 8);
        pcm.push_back(float(raw < 32768 ? raw : raw - 65536) / 32768.f);
    }
    return pcm;
}

// Normalize only casing, ASCII punctuation and whitespace of public test phrases.
std::string normalize(const char* text) {
    std::string normalized;
    bool space = false;
    for (unsigned char ch; (ch = *text++);) {
        if (ch == ' ' || ch == '\t' || ch == '\n' || ch == '\r') {
            space = !normalized.empty();
        } else if (ch >= 0x80 || (ch >= 'a' && ch <= 'z') ||
                   (ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9')) {
            if (space) normalized.push_back(' ');
            space = false;
            normalized.push_back(ch >= 'A' && ch <= 'Z' ? ch + ('a' - 'A') : ch);
        }
    }
    return normalized;
}

void decode(sts_reply_engine* engine, const char* label, const std::vector<float>& pcm,
            sts_reply_status expected, const char* phrase = nullptr,
            sts_reply_turn* supplied_turn = nullptr) {
    Text text;
    text.fill('x'); // Detect stale or partial output on failure.
    sts_reply_turn local_turn;
    const auto start = Clock::now();
    const auto status = sts_reply_decode(engine, pcm.data(), pcm.size(),
                                        supplied_turn ? supplied_turn : &local_turn,
                                        text.data(), text.size());
    const auto bytes = strnlen(text.data(), text.size());
    const bool matches = phrase ? bytes < text.size() && normalize(text.data()) == phrase
                                : std::all_of(text.begin(), text.end(), [](char c) { return c == 0; });
    check(label, status == expected && matches, status, bytes, elapsed(start));
}

std::vector<float> scaled(const std::vector<float>& pcm, float gain) {
    auto result = pcm;
    for (float& sample : result) sample *= gain;
    return result;
}

std::vector<float> seeded_noise(std::uint32_t seed, float gain = 1.f) {
    std::vector<float> noise(32000);
    for (float& sample : noise) {
        seed = 1664525u * seed + 1013904223u;
        sample = gain * float(int(seed >> 24) - 128) / 32768.f;
    }
    return noise;
}

struct AbortLatch {
    std::mutex mutex;
    std::condition_variable changed;
    std::atomic<unsigned> polls{0};
    bool entered = false;
    bool done = false;
    sts_reply_turn turn;

    static void await_cancel(void* data) noexcept {
        auto& latch = *static_cast<AbortLatch*>(data);
        // Whisper v1.9.5 polls at graph boundaries. Let the completed
        // encoder's first poll pass, then cancel at the first decoder poll.
        if (latch.polls.fetch_add(1) != 1) return;
        std::unique_lock<std::mutex> lock(latch.mutex);
        latch.entered = true;
        latch.changed.notify_all();
        latch.changed.wait(lock, [&] { return latch.turn.cancelled.load(); });
    }
};

void inflight_cancel(sts_reply_engine* engine, const std::vector<float>& fixture) {
    AbortLatch latch;
    latch.turn.on_abort_poll = AbortLatch::await_cancel;
    latch.turn.observer = &latch;
    std::thread canceller([&] {
        std::unique_lock<std::mutex> lock(latch.mutex);
        latch.changed.wait(lock, [&] { return latch.entered || latch.done; });
        if (latch.entered) latch.turn.cancelled.store(true);
        latch.changed.notify_all();
    });
    decode(engine, "inflight-cancel", fixture, STS_REPLY_CANCELLED, nullptr, &latch.turn);
    {
        std::lock_guard<std::mutex> lock(latch.mutex);
        latch.done = true;
        latch.changed.notify_all();
    }
    canceller.join();
    check("cancel-second-compute-boundary-observed", latch.entered && latch.polls == 2);
}

int smoke(int argc, char** argv) {
    if (argc != 9) return 2;
    const int model_fd = std::stoi(argv[1]);
    const auto model_offset = std::stoll(argv[2]);
    const auto model_length = std::stoll(argv[3]);
    const int vad_fd = std::stoi(argv[4]);
    const auto vad_offset = std::stoll(argv[5]);
    const auto vad_length = std::stoll(argv[6]);
    auto a = read_pcm(argv[7]);
    auto b = read_pcm(argv[8]);
    sts_reply_engine* engine = nullptr;
    sts_reply_turn open_turn;
    const auto initial_model_position = lseek(model_fd, 4, SEEK_SET);
    const auto initial_vad_position = lseek(vad_fd, 7, SEEK_SET);
    const auto reject_spans = [&](const char* name,
                                  std::int64_t candidate_model_offset,
                                  std::int64_t candidate_model_length,
                                  std::int64_t candidate_vad_offset,
                                  std::int64_t candidate_vad_length) {
        const auto status = sts_reply_open(
            model_fd, candidate_model_offset, candidate_model_length,
            vad_fd, candidate_vad_offset, candidate_vad_length, &open_turn, &engine);
        check(name, engine == nullptr && status == STS_REPLY_MODEL_UNAVAILABLE, status);
        if (engine) {
            sts_reply_close(engine);
            engine = nullptr;
        }
    };
    reject_spans("negative-model-span", -1, model_length, vad_offset, vad_length);
    reject_spans("short-model-span", model_offset, model_length - 1, vad_offset, vad_length);
    reject_spans("oversize-model-span", model_offset, model_length + 1, vad_offset, vad_length);
    reject_spans("overflow-model-span", std::numeric_limits<std::int64_t>::max(), model_length,
                 vad_offset, vad_length);
    reject_spans("wrong-model-span-header", 0, model_length, vad_offset, vad_length);
    reject_spans("negative-vad-span", model_offset, model_length, -1, vad_length);
    reject_spans("short-vad-span", model_offset, model_length, vad_offset, vad_length - 1);
    reject_spans("oversize-vad-span", model_offset, model_length, vad_offset, vad_length + 1);
    reject_spans("overflow-vad-span", model_offset, model_length,
                 std::numeric_limits<std::int64_t>::max(), vad_length);
    reject_spans("wrong-vad-span-header", model_offset, model_length, 0, vad_length);
    sts_reply_turn cancelled_open;
    cancelled_open.cancelled.store(true);
    auto status = sts_reply_open(model_fd, model_offset, model_length,
                                 vad_fd, vad_offset, vad_length, &cancelled_open, &engine);
    check("pre-cancel-open", engine == nullptr && status == STS_REPLY_CANCELLED, status);
    if (engine) {
        sts_reply_close(engine);
        engine = nullptr;
    }
    const auto start = Clock::now();
    status = sts_reply_open(model_fd, model_offset, model_length,
                            vad_fd, vad_offset, vad_length, &open_turn, &engine);
    check("open-padded-model-and-vad-spans", engine && status == STS_REPLY_OK,
          status, 0, elapsed(start));
    check("borrowed-model-fd-and-position",
          initial_model_position == 4 &&
              lseek(model_fd, 0, SEEK_CUR) == initial_model_position);
    check("borrowed-vad-fd-and-position",
          initial_vad_position == 7 && lseek(vad_fd, 0, SEEK_CUR) == initial_vad_position);
    if (!engine || status != STS_REPLY_OK) return 1;

    decode(engine, "first-a", a, STS_REPLY_OK, "please call my family");
    decode(engine, "silence", std::vector<float>(32000), STS_REPLY_NO_SPEECH);
    const auto noise = seeded_noise(0x9021df15);
    decode(engine, "seeded-noise", noise, STS_REPLY_NO_SPEECH);
    for (const auto& gain : std::array<std::pair<const char*, float>, 3>{{
             {"gain-0.10", 0.10f}, {"gain-0.05", 0.05f}, {"gain-0.03", 0.03f}}}) {
        decode(engine, (std::string("quiet-a-") + gain.first).c_str(), scaled(a, gain.second),
               STS_REPLY_OK, "please call my family");
        decode(engine, (std::string("quiet-b-") + gain.first).c_str(), scaled(b, gain.second),
               STS_REPLY_OK, "i need water please");
    }
    for (const auto& fixture : std::array<std::pair<const char*, std::uint32_t>, 3>{{
             {"noise-seed-243f6a88", 0x243f6a88},
             {"noise-seed-85a308d3", 0x85a308d3},
             {"noise-seed-13198a2e", 0x13198a2e}}}) {
        decode(engine, fixture.first, seeded_noise(fixture.second), STS_REPLY_NO_SPEECH);
    }
    decode(engine, "noise-seed-9021df15-gain-4", seeded_noise(0x9021df15, 4.f),
           STS_REPLY_NO_SPEECH);
    decode(engine, "second-b", b, STS_REPLY_OK, "i need water please");
    decode(engine, "third-a", a, STS_REPLY_OK, "please call my family");
    decode(engine, "empty-turn", {}, STS_REPLY_INVALID_INPUT);
    decode(engine, "cap-silence", std::vector<float>(STS_REPLY_MAX_SAMPLES), STS_REPLY_NO_SPEECH);
    decode(engine, "over-cap", std::vector<float>(STS_REPLY_MAX_SAMPLES + 1), STS_REPLY_INVALID_INPUT);
    decode(engine, "nan-turn", {std::numeric_limits<float>::quiet_NaN()}, STS_REPLY_INVALID_INPUT);
    decode(engine, "infinite-turn", {std::numeric_limits<float>::infinity()}, STS_REPLY_INVALID_INPUT);
    decode(engine, "out-of-range-turn", {1.f}, STS_REPLY_INVALID_INPUT);
    decode(engine, "short-turn", {0.5f}, STS_REPLY_NO_SPEECH);
    sts_reply_turn cancelled_turn;
    cancelled_turn.cancelled.store(true);
    decode(engine, "pre-cancel-decode", a, STS_REPLY_CANCELLED, nullptr, &cancelled_turn);
    inflight_cancel(engine, b);
    decode(engine, "cancel-retry", b, STS_REPLY_OK, "i need water please");
    std::thread foreign([&] {
        decode(engine, "foreign-decode", a, STS_REPLY_INVALID_INPUT);
        const auto foreign_status = sts_reply_close(engine);
        check("foreign-close", foreign_status == STS_REPLY_INVALID_INPUT, foreign_status);
    });
    foreign.join();
    status = sts_reply_close(engine);
    check("owner-close", status == STS_REPLY_OK, status);
    struct rusage usage{};
    if (getrusage(RUSAGE_SELF, &usage) == 0) {
        std::cout << "peak_rss_kib=" << usage.ru_maxrss << std::endl;
    }
    return all_passed ? 0 : 1;
}
}

int main(int argc, char** argv) {
    try {
        return smoke(argc, argv);
    } catch (...) {
        std::cerr << "host smoke input or setup failure" << std::endl;
        return 2;
    }
}
