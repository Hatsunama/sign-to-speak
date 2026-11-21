cmake_minimum_required(VERSION 3.22)
project(appmodules)

# Preserve React Native's default OnLoad, codegen, Hermes/Fabric and autolinking.
# Keep this directory free of .cpp files: React Native uses any such file as
# an OnLoad override instead of its default fallback.
include(${REACT_ANDROID_DIR}/cmake-utils/ReactNative-application.cmake)

# The speech JNI bridge is a separate shared library, never an appmodules override.
# Each ABI must have a source-built Rust runtime before Gradle configures CMake.
if(ANDROID_ABI STREQUAL "arm64-v8a")
    set(POCKET_RUST_TARGET "aarch64-linux-android")
elseif(ANDROID_ABI STREQUAL "x86_64")
    set(POCKET_RUST_TARGET "x86_64-linux-android")
else()
    message(FATAL_ERROR "Unsupported Pocket TTS Android ABI: ${ANDROID_ABI}")
endif()
get_filename_component(POCKET_RUNTIME_ROOT
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../../generated/pocket-jniLibs" ABSOLUTE)
set(POCKET_RUNTIME_LIBRARY
    "${POCKET_RUNTIME_ROOT}/${ANDROID_ABI}/libsign_pocket_tts_runtime.so")
if(NOT EXISTS "${POCKET_RUNTIME_LIBRARY}")
    message(FATAL_ERROR "Missing source-built Pocket TTS runtime for ${ANDROID_ABI} (${POCKET_RUST_TARGET})")
endif()
add_subdirectory(../cpp/pocket pocket_build)

# Reply ASR stays separate from appmodules so React Native retains its default OnLoad.
get_filename_component(REPLY_WHISPER_SOURCE
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../../generated/reply-asr-component/whisper.cpp" ABSOLUTE)
if(NOT IS_DIRECTORY "${REPLY_WHISPER_SOURCE}/ggml")
    message(FATAL_ERROR "Missing verified generated Reply ASR whisper.cpp source")
endif()
add_subdirectory(../../../../../vendor/reply-asr reply_asr_build)

add_library(private_reply_asr SHARED ../cpp/reply_jni.cpp)
target_compile_features(private_reply_asr PRIVATE cxx_std_17)
target_compile_options(private_reply_asr PRIVATE -fvisibility=hidden -fno-rtti)
target_include_directories(private_reply_asr PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../../vendor/reply-asr")
target_link_libraries(private_reply_asr PRIVATE sign_reply_asr)
target_link_options(private_reply_asr PRIVATE
    "-Wl,--version-script=${CMAKE_CURRENT_SOURCE_DIR}/../cpp/reply_exports.map"
    -Wl,-z,defs -Wl,-z,max-page-size=16384 -Wl,-z,common-page-size=16384)
set_property(TARGET private_reply_asr APPEND PROPERTY LINK_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/../cpp/reply_exports.map")
