#include "request_registry.h"
#include <jni.h>
#include <array>
#include <cstdint>
#include <cstring>
#include <new>

namespace {
RequestRegistry requests;
constexpr const char* kOwner = "com/xmilo_at_your_side/sign_to_speak/FliteRequest";

void throwSanitized(JNIEnv* env, const char* type, const char* message) {
    if (env->ExceptionCheck()) return;
    jclass klass = env->FindClass(type);
    if (klass) {
        env->ThrowNew(klass, message);
        env->DeleteLocalRef(klass);
    }
}
void throwUnavailable(JNIEnv* env) {
    throwSanitized(env, "java/lang/IllegalStateException", "Local speech unavailable");
}
void wipe(void* ptr, size_t size) {
    volatile unsigned char* bytes = static_cast<volatile unsigned char*>(ptr);
    for (size_t i = 0; i < size; ++i) bytes[i] = 0;
}
struct Input final {
    std::array<uint8_t, STTS_MAX_TEXT_BYTES> bytes{};
    ~Input() { wipe(bytes.data(), bytes.size()); }
};
struct Pcm final {
    stts_pcm value{};
    ~Pcm() { stts_pcm_release(&value); }
};
struct Active final {
    explicit Active(std::shared_ptr<RequestRecord> owned) : request(std::move(owned)) {}
    ~Active() { if (started) request->finish(); }
    std::shared_ptr<RequestRecord> request;
    bool started = false;
};
}

extern "C" JNIEXPORT jlong JNICALL
Java_com_xmilo_1at_1your_1side_sign_1to_1speak_FliteRequest_nativeCreate(JNIEnv* env, jclass) {
    try {
        const int64_t id = requests.create();
        if (id == 0) throwUnavailable(env);
        return static_cast<jlong>(id);
    } catch (...) {
        throwUnavailable(env);
        return 0;
    }
}

extern "C" JNIEXPORT jshortArray JNICALL
Java_com_xmilo_1at_1your_1side_sign_1to_1speak_FliteRequest_nativeSynthesize(
    JNIEnv* env, jclass, jlong id, jbyteArray ascii) {
    try {
        auto request = requests.acquire(static_cast<int64_t>(id));
        if (!request) { throwUnavailable(env); return nullptr; }
        Active active(std::move(request));
        if (!active.request->begin()) { throwUnavailable(env); return nullptr; }
        active.started = true;
        if (!ascii) {
            throwSanitized(env, "java/lang/IllegalArgumentException", "Invalid speech text");
            return nullptr;
        }
        const jsize length = env->GetArrayLength(ascii);
        if (length <= 0 || length > STTS_MAX_TEXT_BYTES) {
            throwSanitized(env, "java/lang/IllegalArgumentException", "Invalid speech text");
            return nullptr;
        }
        Input input;
        env->GetByteArrayRegion(ascii, 0, length, reinterpret_cast<jbyte*>(input.bytes.data()));
        if (env->ExceptionCheck()) return nullptr;
        Pcm pcm;
        const stts_status result = stts_synthesize(input.bytes.data(), static_cast<size_t>(length),
                                                    &active.request->token, &pcm.value);
        if (result == STTS_CANCELLED || active.request->isClosed() || active.request->isCancelled()) return nullptr;
        if (result == STTS_INPUT) {
            throwSanitized(env, "java/lang/IllegalArgumentException", "Invalid speech text");
            return nullptr;
        }
        if (result != STTS_OK || !pcm.value.samples || pcm.value.count == 0 ||
            pcm.value.count > STTS_MAX_PCM_SAMPLES || pcm.value.sample_rate != 16000 ||
            pcm.value.channels != 1) {
            throwUnavailable(env);
            return nullptr;
        }
        jshortArray output = env->NewShortArray(static_cast<jsize>(pcm.value.count));
        if (!output) return nullptr;
        env->SetShortArrayRegion(output, 0, static_cast<jsize>(pcm.value.count),
                                 reinterpret_cast<const jshort*>(pcm.value.samples));
        if (env->ExceptionCheck()) return nullptr;
        if (active.request->isClosed() || active.request->isCancelled()) {
            std::array<jshort, 1024> zeros{};
            for (jsize position = 0; position < static_cast<jsize>(pcm.value.count); ) {
                const jsize chunk = static_cast<jsize>(pcm.value.count) - position < 1024
                    ? static_cast<jsize>(pcm.value.count) - position : 1024;
                env->SetShortArrayRegion(output, position, chunk, zeros.data());
                if (env->ExceptionCheck()) return nullptr;
                position += chunk;
            }
            return nullptr;
        }
        return output;
    } catch (...) {
        throwUnavailable(env);
        return nullptr;
    }
}

extern "C" JNIEXPORT void JNICALL
Java_com_xmilo_1at_1your_1side_sign_1to_1speak_FliteRequest_nativeCancel(JNIEnv*, jclass, jlong id) {
    requests.cancel(static_cast<int64_t>(id));
}
extern "C" JNIEXPORT void JNICALL
Java_com_xmilo_1at_1your_1side_sign_1to_1speak_FliteRequest_nativeClose(JNIEnv*, jclass, jlong id) {
    requests.close(static_cast<int64_t>(id));
}