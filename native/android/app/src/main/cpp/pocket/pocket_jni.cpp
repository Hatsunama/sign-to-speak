#include "sign_pocket_tts.h"
#include <jni.h>
#include <cstdint>
#include <cstring>
#include <limits>
#include <vector>

namespace {
constexpr size_t kMaxRootBytes = 4096;
constexpr size_t kMaxTextBytes = 512;
constexpr size_t kMaxPcmSamples = 30 * 24000;
constexpr size_t kMaxChunkSamples = 1920;

void wipe(void* ptr, size_t size) noexcept {
    volatile unsigned char* bytes = static_cast<volatile unsigned char*>(ptr);
    for (size_t i = 0; i < size; ++i) bytes[i] = 0;
}
struct Bytes {
    std::vector<uint8_t> data;
    ~Bytes() { if (!data.empty()) wipe(data.data(), data.size()); }
};
struct Pcm {
    uint64_t id;
    bool bad = false;
    std::vector<int16_t> data;
    explicit Pcm(uint64_t requestId) : id(requestId) {}
    ~Pcm() { if (!data.empty()) wipe(data.data(), data.size() * sizeof(int16_t)); }
};
void fail(JNIEnv* env, const char* type, const char* message) {
    if (env->ExceptionCheck()) return;
    jclass cls = env->FindClass(type);
    if (cls) { env->ThrowNew(cls, message); env->DeleteLocalRef(cls); }
}
void unavailable(JNIEnv* env) { fail(env, "java/lang/IllegalStateException", "Local speech unavailable"); }
void invalidText(JNIEnv* env) {
    fail(env, "com/xmilo_at_your_side/sign_to_speak/SpeechSession$InvalidInput",
         "Invalid speech text");
}
bool copyArray(JNIEnv* env, jbyteArray input, size_t maximum, Bytes& bytes) {
    if (!input) return false;
    const jsize size = env->GetArrayLength(input);
    if (env->ExceptionCheck() || size <= 0 || static_cast<size_t>(size) > maximum) return false;
    bytes.data.resize(static_cast<size_t>(size));
    env->GetByteArrayRegion(input, 0, size, reinterpret_cast<jbyte*>(bytes.data.data()));
    return !env->ExceptionCheck();
}
extern "C" bool collect(uint64_t requestId, const int16_t* samples,
                          size_t count, void* user) noexcept {
    auto* pcm = static_cast<Pcm*>(user);
    if (!pcm || requestId != pcm->id || !samples || count == 0 ||
        count > kMaxChunkSamples || pcm->data.size() > kMaxPcmSamples - count) {
        if (pcm) pcm->bad = true;
        return false;
    }
    try { pcm->data.insert(pcm->data.end(), samples, samples + count); }
    catch (...) { pcm->bad = true; return false; }
    return true;
}
}

extern "C" JNIEXPORT jlong JNICALL
Java_com_xmilo_1at_1your_1side_sign_1to_1speak_PocketTtsRequest_nativeCreate(
    JNIEnv* env, jclass, jbyteArray root) {
    try {
        Bytes path;
        if (!copyArray(env, root, kMaxRootBytes, path)) {
            if (!env->ExceptionCheck()) fail(env, "java/lang/IllegalArgumentException", "Invalid model root");
            return 0;
        }
        uint64_t id = 0;
        const sts_pocket_status status = sts_pocket_create(path.data.data(), path.data.size(), &id);
        if (status == STS_POCKET_INVALID_INPUT) {
            fail(env, "java/lang/IllegalArgumentException", "Invalid model root");
            return 0;
        }
        if (status != STS_POCKET_OK || id == 0 ||
            id > static_cast<uint64_t>(std::numeric_limits<jlong>::max())) {
            if (status == STS_POCKET_OK && id != 0) sts_pocket_close(id);
            unavailable(env);
            return 0;
        }
        return static_cast<jlong>(id);
    } catch (...) { unavailable(env); return 0; }
}

extern "C" JNIEXPORT jshortArray JNICALL
Java_com_xmilo_1at_1your_1side_sign_1to_1speak_PocketTtsRequest_nativeSynthesize(
    JNIEnv* env, jclass, jlong id, jbyteArray utf8) {
    try {
        if (id <= 0) { unavailable(env); return nullptr; }
        Bytes input;
        if (!copyArray(env, utf8, kMaxTextBytes, input)) {
            if (!env->ExceptionCheck()) invalidText(env);
            return nullptr;
        }
        Pcm pcm(static_cast<uint64_t>(id));
        pcm.data.reserve(kMaxPcmSamples);
        const sts_pocket_status status = sts_pocket_synthesize(
            pcm.id, input.data.data(), input.data.size(), collect, &pcm);
        if (status == STS_POCKET_CANCELLED) return nullptr;
        if (status == STS_POCKET_INVALID_INPUT) { invalidText(env); return nullptr; }
        if (status != STS_POCKET_OK || pcm.bad || pcm.data.empty() ||
            pcm.data.size() > kMaxPcmSamples) {
            unavailable(env);
            return nullptr;
        }
        jshortArray result = env->NewShortArray(static_cast<jsize>(pcm.data.size()));
        if (!result) return nullptr;
        env->SetShortArrayRegion(result, 0, static_cast<jsize>(pcm.data.size()),
                                 reinterpret_cast<const jshort*>(pcm.data.data()));
        if (env->ExceptionCheck()) return nullptr;
        return result;
    } catch (...) { unavailable(env); return nullptr; }
}

extern "C" JNIEXPORT void JNICALL
Java_com_xmilo_1at_1your_1side_sign_1to_1speak_PocketTtsRequest_nativeCancel(
    JNIEnv* env, jclass, jlong id) {
    try {
        if (id <= 0 || sts_pocket_cancel(static_cast<uint64_t>(id)) != STS_POCKET_OK)
            unavailable(env);
    } catch (...) { unavailable(env); }
}

extern "C" JNIEXPORT void JNICALL
Java_com_xmilo_1at_1your_1side_sign_1to_1speak_PocketTtsRequest_nativeClose(
    JNIEnv* env, jclass, jlong id) {
    try {
        if (id <= 0 || sts_pocket_close(static_cast<uint64_t>(id)) != STS_POCKET_OK)
            unavailable(env);
    } catch (...) { unavailable(env); }
}
