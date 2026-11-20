cmake_minimum_required(VERSION 3.31.5)
project(sign_reply_asr_component LANGUAGES C CXX)

add_subdirectory(../../vendor/reply-asr reply-asr)

if(NOT ANDROID)
    find_package(Threads REQUIRED)
    add_executable(reply_asr_smoke smoke.cpp)
    target_link_libraries(reply_asr_smoke PRIVATE sign_reply_asr Threads::Threads)

    # Hosted-only fail-before regression. The executable links the pinned
    # upstream library directly so ASan/LSan can inspect its model-load cleanup.
    add_executable(reply_asr_model_load_leak_regression model_load_leak_regression.cpp)
    target_link_libraries(reply_asr_model_load_leak_regression PRIVATE whisper)
endif()
