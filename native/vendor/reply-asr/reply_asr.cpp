#include "reply_asr.h"
#include "whisper.h"

#include <cerrno>
#include <cmath>
#include <cstring>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string_view>
#include <sys/stat.h>
#include <thread>
#include <utility>
#include <unistd.h>

namespace {
constexpr std::int64_t kModelBytes = 77704715;
constexpr unsigned char kModelHeader[] = {
    0x6c, 0x6d, 0x67, 0x67, 0x98, 0xca, 0x00, 0x00, 0xdc, 0x05, 0x00, 0x00,
    0x80, 0x01, 0x00, 0x00, 0x06, 0x00, 0x00, 0x00, 0x04, 0x00, 0x00, 0x00,
    0xc0, 0x01, 0x00, 0x00, 0x80, 0x01, 0x00, 0x00, 0x06, 0x00, 0x00, 0x00,
    0x04, 0x00, 0x00, 0x00, 0x50, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00
};
static_assert(WHISPER_SAMPLE_RATE == 16000);
static_assert(sizeof(off_t) >= sizeof(std::int64_t));

struct Span {
    int fd;
    std::int64_t offset;
    std::int64_t length;
    std::int64_t cursor = 0;
};

std::size_t read_span(void* user, void* output, std::size_t count) {
    auto& span = *static_cast<Span*>(user);
    auto* bytes = static_cast<unsigned char*>(output);
    // The pinned loader probes for another tensor header at the exact EOF and
    // checks eof() after reading its three fields. Initialize that probe's fields.
    if (span.cursor == span.length) {
        if (count) std::memset(bytes, 0, count);
        return 0;
    }
    if (count > static_cast<std::uint64_t>(span.length - span.cursor)) {
        throw std::runtime_error("invalid model read");
    }
    std::size_t done = 0;
    while (done < count) {
        const auto result = pread(span.fd, bytes + done, count - done,
                                  static_cast<off_t>(span.offset + span.cursor));
        if (result < 0 && errno == EINTR) continue;
        if (result <= 0) throw std::runtime_error("incomplete model read");
        done += static_cast<std::size_t>(result);
        span.cursor += result;
    }
    return done;
}

bool span_eof(void* user) {
    auto& span = *static_cast<Span*>(user);
    return span.cursor == span.length;
}

void ignore_close(void*) {}
void ignore_log(ggml_log_level, const char*, void*) {}

bool cancelled(const sts_reply_turn* turn) {
    return turn->cancelled.load(std::memory_order_acquire);
}

bool begin_encoder(whisper_context*, whisper_state*, void* user) {
    return !cancelled(static_cast<sts_reply_turn*>(user));
}

bool abort_decode(void* user) {
    auto* turn = static_cast<sts_reply_turn*>(user);
    if (turn->on_abort_poll) turn->on_abort_poll(turn->observer);
    return cancelled(turn);
}

bool valid_utf8(std::string_view input) {
    auto cursor = input.begin();
    while (cursor != input.end()) {
        const auto lead = static_cast<unsigned char>(*cursor++);
        if (lead <= 0x7f) continue;
        unsigned remaining;
        std::uint32_t codepoint;
        std::uint32_t minimum;
        if (lead >= 0xc2 && lead <= 0xdf) {
            remaining = 1;
            codepoint = lead & 0x1f;
            minimum = 0x80;
        } else if (lead >= 0xe0 && lead <= 0xef) {
            remaining = 2;
            codepoint = lead & 0x0f;
            minimum = 0x800;
        } else if (lead >= 0xf0 && lead <= 0xf4) {
            remaining = 3;
            codepoint = lead & 0x07;
            minimum = 0x10000;
        } else {
            return false;
        }
        while (remaining--) {
            if (cursor == input.end()) return false;
            const auto continuation = static_cast<unsigned char>(*cursor++);
            if ((continuation & 0xc0) != 0x80) return false;
            codepoint = (codepoint << 6) | (continuation & 0x3f);
        }
        if (codepoint < minimum || codepoint > 0x10ffff ||
            (codepoint >= 0xd800 && codepoint <= 0xdfff)) {
            return false;
        }
    }
    return true;
}

bool ascii_space(char ch) {
    return ch == ' ' || ch == '\t' || ch == '\n' || ch == '\r' || ch == '\f' || ch == '\v';
}

void clear_output(char* output, std::size_t capacity) {
    volatile char* bytes = output;
    for (std::size_t i = 0; i < capacity; ++i) bytes[i] = 0;
}

using Weights = std::unique_ptr<whisper_context, decltype(&whisper_free)>;
using State = std::unique_ptr<whisper_state, decltype(&whisper_free_state)>;
}

struct sts_reply_engine {
    Weights weights;
    std::thread::id owner = std::this_thread::get_id();
    bool decoding = false;
};

sts_reply_status sts_reply_open(int fd, std::int64_t offset, std::int64_t length,
                               sts_reply_turn* turn, sts_reply_engine** result) noexcept {
    if (!result) return STS_REPLY_INVALID_INPUT;
    *result = nullptr;
    if (!turn) return STS_REPLY_INVALID_INPUT;
    if (cancelled(turn)) return STS_REPLY_CANCELLED;
    struct stat info{};
    if (fd < 0 || offset < 0 || length != kModelBytes ||
        fstat(fd, &info) != 0 || !S_ISREG(info.st_mode) ||
        offset > info.st_size || length > info.st_size - offset) {
        return STS_REPLY_MODEL_UNAVAILABLE;
    }
    try {
        Span span{fd, offset, length};
        unsigned char header[sizeof(kModelHeader)]{};
        read_span(&span, header, sizeof(header));
        if (std::memcmp(header, kModelHeader, sizeof(header)) != 0) {
            return STS_REPLY_MODEL_UNAVAILABLE;
        }
        span.cursor = 0;
        whisper_model_loader loader{&span, read_span, span_eof, ignore_close};
        static std::once_flag logging;
        std::call_once(logging, [] { whisper_log_set(ignore_log, nullptr); });
        auto params = whisper_context_default_params();
        params.use_gpu = false;
        params.flash_attn = false;
        params.dtw_token_timestamps = false;
        Weights weights(whisper_init_with_params_no_state(&loader, params), whisper_free);
        if (cancelled(turn)) return STS_REPLY_CANCELLED;
        if (!weights || span.cursor != length) return STS_REPLY_MODEL_UNAVAILABLE;
        *result = new sts_reply_engine{std::move(weights)};
        return STS_REPLY_OK;
    } catch (...) {
        return cancelled(turn) ? STS_REPLY_CANCELLED : STS_REPLY_MODEL_UNAVAILABLE;
    }
}

sts_reply_status sts_reply_decode(sts_reply_engine* engine, const float* samples,
                                 std::size_t count, sts_reply_turn* turn,
                                 char* output, std::size_t capacity) noexcept {
    if (!output || capacity == 0 || capacity > STS_REPLY_TEXT_CAPACITY) {
        return STS_REPLY_INVALID_INPUT;
    }
    clear_output(output, capacity);
    if (!engine || engine->owner != std::this_thread::get_id() || !samples || !turn ||
        engine->decoding || count == 0 || count > STS_REPLY_MAX_SAMPLES) {
        return STS_REPLY_INVALID_INPUT;
    }
    if (cancelled(turn)) return STS_REPLY_CANCELLED;
    bool audible = false;
    for (std::size_t i = 0; i < count; ++i) {
        if (!std::isfinite(samples[i]) || samples[i] < -1.f || samples[i] >= 1.f) {
            return STS_REPLY_INVALID_INPUT;
        }
        audible |= samples[i] != 0.f;
    }
    if (!audible || count < WHISPER_HOP_LENGTH * 10) return STS_REPLY_NO_SPEECH;

    engine->decoding = true;
    struct ClearDecoding {
        bool& decoding;
        ~ClearDecoding() { decoding = false; }
    } clear_decoding{engine->decoding};
    try {
        auto* context = engine->weights.get();
        State state(whisper_init_state(context), whisper_free_state);
        if (cancelled(turn)) return STS_REPLY_CANCELLED;
        if (!state) return STS_REPLY_DECODE_FAILED;
        auto params = whisper_full_default_params(WHISPER_SAMPLING_GREEDY);
        params.n_threads = 2;
        params.greedy.best_of = 1;
        params.temperature_inc = 0.f;
        params.language = "en";
        params.detect_language = false;
        params.translate = false;
        params.no_context = true;
        params.initial_prompt = nullptr;
        params.no_timestamps = true;
        params.print_special = false;
        params.print_progress = false;
        params.print_realtime = false;
        params.print_timestamps = false;
        params.vad = false;
        params.encoder_begin_callback = begin_encoder;
        params.encoder_begin_callback_user_data = turn;
        params.abort_callback = abort_decode;
        params.abort_callback_user_data = turn;
        const int status = whisper_full_with_state(context, state.get(), params, samples,
                                                   static_cast<int>(count));
        if (cancelled(turn)) return STS_REPLY_CANCELLED;
        if (status != 0) return STS_REPLY_DECODE_FAILED;

        // Hold output privately until every segment passes byte and UTF-8 checks.
        char text[STS_REPLY_TEXT_CAPACITY]{};
        struct ClearText {
            char* bytes;
            ~ClearText() { clear_output(bytes, STS_REPLY_TEXT_CAPACITY); }
        } clear_text{text};
        std::size_t length = 0;
        const int segments = whisper_full_n_segments_from_state(state.get());
        for (int i = 0; i < segments; ++i) {
            const char* segment = whisper_full_get_segment_text_from_state(state.get(), i);
            if (!segment) return STS_REPLY_DECODE_FAILED;
            const auto remaining = sizeof(text) - length;
            const auto bytes = strnlen(segment, remaining);
            if (bytes == remaining) return STS_REPLY_DECODE_FAILED;
            std::memcpy(text + length, segment, bytes);
            length += bytes;
        }
        std::string_view caption(text, length);
        if (!valid_utf8(caption)) return STS_REPLY_DECODE_FAILED;
        while (!caption.empty() && ascii_space(caption.front())) caption.remove_prefix(1);
        while (!caption.empty() && ascii_space(caption.back())) caption.remove_suffix(1);
        if (caption.size() >= capacity) return STS_REPLY_DECODE_FAILED;
        if (cancelled(turn)) return STS_REPLY_CANCELLED;
        if (caption.empty()) return STS_REPLY_NO_SPEECH;
        std::memcpy(output, caption.data(), caption.size());
        if (cancelled(turn)) {
            clear_output(output, capacity);
            return STS_REPLY_CANCELLED;
        }
        return STS_REPLY_OK;
    } catch (...) {
        return cancelled(turn) ? STS_REPLY_CANCELLED : STS_REPLY_DECODE_FAILED;
    }
}

sts_reply_status sts_reply_close(sts_reply_engine* engine) noexcept {
    if (!engine || engine->owner != std::this_thread::get_id() || engine->decoding) {
        return STS_REPLY_INVALID_INPUT;
    }
    delete engine;
    return STS_REPLY_OK;
}
