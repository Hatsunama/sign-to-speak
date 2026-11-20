cmake_minimum_required(VERSION 3.31.5)
project(sign_reply_asr_component LANGUAGES C CXX)

add_subdirectory(../../vendor/reply-asr reply-asr)

if(NOT ANDROID)
    find_package(Threads REQUIRED)
    add_executable(reply_asr_smoke smoke.cpp)
    target_link_libraries(reply_asr_smoke PRIVATE sign_reply_asr Threads::Threads)

    add_executable(reply_asr_vad_semantic_probe vad_semantic_probe.cpp)
    target_link_libraries(reply_asr_vad_semantic_probe PRIVATE whisper)

    # Hosted-only fault injection: production sources remain unmodified.
    add_executable(reply_asr_vad_compute_failure_regression vad_compute_failure_regression.cpp)
    target_link_libraries(reply_asr_vad_compute_failure_regression PRIVATE whisper)
    target_link_options(reply_asr_vad_compute_failure_regression PRIVATE
        -Wl,--wrap=ggml_backend_sched_graph_compute)

    # Hosted-only repaired-source regression for the general Whisper loader.
    add_executable(reply_asr_model_load_leak_regression model_load_leak_regression.cpp)
    target_link_libraries(reply_asr_model_load_leak_regression PRIVATE whisper)

    # Hosted-only repaired-source VAD initialization lifetime regression.
    add_executable(reply_asr_vad_model_load_leak_regression vad_model_load_leak_regression.cpp)
    target_link_libraries(reply_asr_vad_model_load_leak_regression PRIVATE whisper)
endif()
