#include "ggml-backend.h"
#include "whisper.h"

#include <array>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {
void quiet_log(enum ggml_log_level, const char*, void*) {}

std::vector<float> read_pcm(const char* path) {
    std::ifstream input(path, std::ios::binary | std::ios::ate);
    const auto length = input.tellg();
    if (length <= 0 || length > 30 * 16000 * 2 ||
        static_cast<std::streamoff>(length) % 2) {
        throw std::runtime_error("invalid public fixture size");
    }
    std::vector<unsigned char> bytes(static_cast<std::size_t>(length));
    input.seekg(0);
    if (!input.read(reinterpret_cast<char*>(bytes.data()), length)) {
        throw std::runtime_error("invalid public fixture read");
    }
    std::vector<float> pcm(bytes.size() / 2);
    for (std::size_t i = 0; i < bytes.size(); i += 2) {
        const int raw = int(bytes[i]) | (int(bytes[i + 1]) << 8);
        pcm[i / 2] = float(raw < 32768 ? raw : raw - 65536) / 32768.f;
    }
    return pcm;
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

enum class Result { pass, semantic_failure, inference_error, segments_error };

Result probe(whisper_vad_context* context, const char* label,
             const std::vector<float>& pcm, bool expect_speech) {
    if (!whisper_vad_detect_speech(context, pcm.data(), static_cast<int>(pcm.size()))) {
        std::cout << label << " ERROR inference" << std::endl;
        return Result::inference_error;
    }
    auto* segments = whisper_vad_segments_from_probs(context, whisper_vad_default_params());
    if (!segments) {
        std::cout << label << " ERROR segments" << std::endl;
        return Result::segments_error;
    }
    const int count = whisper_vad_segments_n_segments(segments);
    whisper_vad_free_segments(segments);
    const bool passed = expect_speech ? count >= 1 : count == 0;
    std::cout << label << " " << (passed ? "pass" : "FAIL")
              << " segments=" << count
              << " expected=" << (expect_speech ? "speech" : "no-speech") << std::endl;
    return passed ? Result::pass : Result::semantic_failure;
}
}

int main(int argc, char** argv) {
    if (argc != 4) return 64;
    try {
        ggml_backend_load_all();
        whisper_log_set(quiet_log, nullptr);
        auto params = whisper_vad_default_context_params();
        params.use_gpu = false;
        auto* context = whisper_vad_init_from_file_with_params(argv[1], params);
        if (!context) {
            std::cout << "vad-model-load ERROR" << std::endl;
            return 2;
        }
        const auto a = read_pcm(argv[2]);
        const auto b = read_pcm(argv[3]);
        bool semantic_failure = false;
        const auto run = [&](const char* label, const std::vector<float>& pcm, bool speech) {
            const auto result = probe(context, label, pcm, speech);
            semantic_failure |= result == Result::semantic_failure;
            return result;
        };
        for (const auto& item : std::array<std::pair<const char*, std::uint32_t>, 4>{{
                 {"noise-seed-9021df15", 0x9021df15},
                 {"noise-seed-243f6a88", 0x243f6a88},
                 {"noise-seed-85a308d3", 0x85a308d3},
                 {"noise-seed-13198a2e", 0x13198a2e}}}) {
            const auto result = run(item.first, seeded_noise(item.second), false);
            if (result == Result::inference_error || result == Result::segments_error) {
                whisper_vad_free(context);
                return result == Result::inference_error ? 3 : 4;
            }
        }
        const auto loud_noise = run("noise-seed-9021df15-gain-4",
                                    seeded_noise(0x9021df15, 4.f), false);
        if (loud_noise == Result::inference_error || loud_noise == Result::segments_error) {
            whisper_vad_free(context);
            return loud_noise == Result::inference_error ? 3 : 4;
        }
        for (const auto& gain : std::array<std::pair<const char*, float>, 4>{{
                 {"original", 1.f}, {"gain-0.10", 0.10f},
                 {"gain-0.05", 0.05f}, {"gain-0.03", 0.03f}}}) {
            for (const auto& fixture : std::array<std::pair<const char*, const std::vector<float>*>, 2>{{
                     {"fixture-a", &a}, {"fixture-b", &b}}}) {
                const std::string label = std::string(fixture.first) + "-" + gain.first;
                const auto result = run(label.c_str(), scaled(*fixture.second, gain.second), true);
                if (result == Result::inference_error || result == Result::segments_error) {
                    whisper_vad_free(context);
                    return result == Result::inference_error ? 3 : 4;
                }
            }
        }
        whisper_vad_free(context);
        return semantic_failure ? 1 : 0;
    } catch (const std::exception&) {
        std::cout << "vad-probe-input ERROR" << std::endl;
        return 5;
    }
}
