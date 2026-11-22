#include "reply_asr.h"
#include <jni.h>
#include <atomic>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>
#include <memory>
#include <mutex>
#include <unordered_map>
#include <vector>

namespace {
constexpr std::size_t kOutputCapacity = STS_REPLY_TEXT_CAPACITY;
constexpr jlong kCleanupFailure = -1000;
constexpr jlong encode_failure(sts_reply_status status) noexcept {
    return -static_cast<jlong>(status) - 1;
}

void wipe(void* pointer, std::size_t bytes) noexcept {
    volatile unsigned char* data = static_cast<volatile unsigned char*>(pointer);
    for (std::size_t i = 0; i < bytes; ++i) data[i] = 0;
}

struct Request {
    sts_reply_turn turn;
    sts_reply_engine* engine = nullptr;
};

std::mutex registry_mutex;
std::unordered_map<std::uint64_t, std::shared_ptr<Request>> registry;
std::atomic<std::uint64_t> next_id{1};

std::shared_ptr<Request> find_request(jlong id) {
    if (id <= 0) return {};
    std::lock_guard<std::mutex> lock(registry_mutex);
    auto found = registry.find(static_cast<std::uint64_t>(id));
    return found == registry.end() ? std::shared_ptr<Request>() : found->second;
}

std::shared_ptr<Request> remove_request(jlong id) {
    if (id <= 0) return {};
    std::lock_guard<std::mutex> lock(registry_mutex);
    auto found = registry.find(static_cast<std::uint64_t>(id));
    if (found == registry.end()) return {};
    auto request = found->second;
    registry.erase(found);
    return request;
}

jlong publish(const std::shared_ptr<Request>& request) {
    for (;;) {
        const std::uint64_t id = next_id.fetch_add(1, std::memory_order_relaxed);
        if (id == 0 || id > static_cast<std::uint64_t>(std::numeric_limits<jlong>::max())) continue;
        std::lock_guard<std::mutex> lock(registry_mutex);
        if (registry.emplace(id, request).second) return static_cast<jlong>(id);
    }
}

struct FloatBuffer {
    std::vector<float> values;
    ~FloatBuffer() { if (!values.empty()) wipe(values.data(), values.size() * sizeof(float)); }
};
struct TextBuffer {
    char values[kOutputCapacity]{};
    ~TextBuffer() { wipe(values, sizeof(values)); }
};
}

extern "C" JNIEXPORT jlong JNICALL
Java_com_xmilo_1at_1your_1side_sign_1to_1speak_ReplyAsrRequest_nativeOpen(
    JNIEnv*, jclass, jint model_fd, jlong model_offset, jlong model_length,
    jint vad_fd, jlong vad_offset, jlong vad_length) {
    if (model_fd < 0 || vad_fd < 0 || model_offset < 0 || vad_offset < 0 ||
        model_length <= 0 || vad_length <= 0) return encode_failure(STS_REPLY_INVALID_INPUT);
    std::shared_ptr<Request> request;
    try {
        request = std::make_shared<Request>();
        const sts_reply_status status = sts_reply_open(
            model_fd, model_offset, model_length, vad_fd, vad_offset, vad_length,
            &request->turn, &request->engine);
        if (status != STS_REPLY_OK || request->engine == nullptr) {
            if (request->engine != nullptr && sts_reply_close(request->engine) != STS_REPLY_OK) {
                request->engine = nullptr;
                return kCleanupFailure;
            }
            request->engine = nullptr;
            return encode_failure(status == STS_REPLY_OK ? STS_REPLY_MODEL_UNAVAILABLE : status);
        }
        return publish(request);
    } catch (...) {
        if (request && request->engine != nullptr) {
            const sts_reply_status cleanup = sts_reply_close(request->engine);
            request->engine = nullptr;
            if (cleanup != STS_REPLY_OK) return kCleanupFailure;
        }
        return encode_failure(STS_REPLY_DECODE_FAILED);
    }
}

extern "C" JNIEXPORT jint JNICALL
Java_com_xmilo_1at_1your_1side_sign_1to_1speak_ReplyAsrRequest_nativeDecode(
    JNIEnv* env, jclass, jlong id, jfloatArray pcm, jbyteArray output) {
    auto request = find_request(id);
    if (!request || !pcm || !output || request->engine == nullptr) return STS_REPLY_INVALID_INPUT;
    const jsize count = env->GetArrayLength(pcm);
    const jsize capacity = env->GetArrayLength(output);
    if (env->ExceptionCheck() || count <= 0 || static_cast<std::size_t>(count) > STS_REPLY_MAX_SAMPLES ||
        capacity != static_cast<jsize>(kOutputCapacity)) return STS_REPLY_INVALID_INPUT;
    try {
        FloatBuffer input;
        input.values.resize(static_cast<std::size_t>(count));
        env->GetFloatArrayRegion(pcm, 0, count, input.values.data());
        if (env->ExceptionCheck()) return STS_REPLY_INVALID_INPUT;
        for (float sample : input.values) if (!std::isfinite(sample) || sample < -1.0f || sample > 1.0f)
            return STS_REPLY_INVALID_INPUT;
        TextBuffer text;
        const sts_reply_status status = sts_reply_decode(request->engine, input.values.data(),
            input.values.size(), &request->turn, text.values, sizeof(text.values));
        if (status == STS_REPLY_OK) {
            if (std::memchr(text.values, '\0', sizeof(text.values)) == nullptr) return STS_REPLY_DECODE_FAILED;
            env->SetByteArrayRegion(output, 0, capacity, reinterpret_cast<const jbyte*>(text.values));
            if (env->ExceptionCheck()) return STS_REPLY_DECODE_FAILED;
        }
        return status;
    } catch (...) { return STS_REPLY_DECODE_FAILED; }
}

extern "C" JNIEXPORT void JNICALL
Java_com_xmilo_1at_1your_1side_sign_1to_1speak_ReplyAsrRequest_nativeCancel(
    JNIEnv*, jclass, jlong id) {
    auto request = find_request(id);
    if (request) request->turn.cancelled.store(true, std::memory_order_release);
}

extern "C" JNIEXPORT jint JNICALL
Java_com_xmilo_1at_1your_1side_sign_1to_1speak_ReplyAsrRequest_nativeClose(
    JNIEnv*, jclass, jlong id) {
    auto request = remove_request(id);
    if (!request || request->engine == nullptr) return STS_REPLY_INVALID_INPUT;
    request->turn.cancelled.store(true, std::memory_order_release);
    const sts_reply_status status = sts_reply_close(request->engine);
    request->engine = nullptr;
    return status;
}
