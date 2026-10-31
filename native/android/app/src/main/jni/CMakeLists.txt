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
