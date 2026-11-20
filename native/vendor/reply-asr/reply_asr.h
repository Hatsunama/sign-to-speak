#ifndef SIGN_REPLY_ASR_H
#define SIGN_REPLY_ASR_H

#include <atomic>
#include <cstddef>
#include <cstdint>

constexpr std::size_t STS_REPLY_MAX_SAMPLES = 30 * 16000;
constexpr std::size_t STS_REPLY_TEXT_CAPACITY = 8193;

enum sts_reply_status {
    STS_REPLY_OK,
    STS_REPLY_NO_SPEECH,
    STS_REPLY_INVALID_INPUT,
    STS_REPLY_CANCELLED,
    STS_REPLY_MODEL_UNAVAILABLE,
    STS_REPLY_DECODE_FAILED
};

struct sts_reply_engine;

struct sts_reply_turn {
    std::atomic<bool> cancelled{false};
    // Optional synchronous observation of Whisper's compute-boundary abort polls.
    // Production callers leave these null.
    void (*on_abort_poll)(void*) noexcept = nullptr;
    void* observer = nullptr;
};

// Open, decode and close are synchronous and must run on the same serialized owner
// thread. A caller can only set turn.cancelled concurrently. Retain the turn and
// its observer until the worker returns. The consumer must recheck turn eligibility
// at publication because cancellation can race native return.
// Borrow immutable regular-file spans for the Whisper and VAD models. The fds are
// required only until open returns. No call changes their positions or closes them.
// The caller verifies each bundled SHA-256 on the same fd before calling open.
// Open itself may not be interruptible.
#if defined(__GNUC__)
#define STS_REPLY_EXPORT __attribute__((visibility("default")))
#else
#define STS_REPLY_EXPORT
#endif

extern "C" {
STS_REPLY_EXPORT sts_reply_status sts_reply_open(
    int model_fd, std::int64_t model_offset, std::int64_t model_length,
    int vad_fd, std::int64_t vad_offset, std::int64_t vad_length,
    sts_reply_turn* turn, sts_reply_engine** result) noexcept;

// Input is finite 16 kHz mono normalized PCM. On success, output is NUL-terminated
// standard UTF-8. The owner provides and clears the caller-owned PCM and text buffers.
STS_REPLY_EXPORT sts_reply_status sts_reply_decode(
    sts_reply_engine* engine, const float* samples, std::size_t count,
    sts_reply_turn* turn, char* output, std::size_t capacity) noexcept;

STS_REPLY_EXPORT sts_reply_status sts_reply_close(sts_reply_engine* engine) noexcept;
}
#endif
