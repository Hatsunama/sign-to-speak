cmake_minimum_required(VERSION 3.31.5)

if(NOT IS_DIRECTORY "${REPLY_WHISPER_SOURCE}/ggml")
    message(FATAL_ERROR "REPLY_WHISPER_SOURCE must name the verified whisper.cpp source tree")
endif()

# Upstream's legacy cmake_minimum_required calls leave these policies unset.
# Use scoped normal variables to avoid changing sibling app targets or global options.
set(CMAKE_POLICY_DEFAULT_CMP0077 NEW)
set(CMAKE_POLICY_DEFAULT_CMP0126 NEW)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(CMAKE_C_VISIBILITY_PRESET hidden)
set(CMAKE_CXX_VISIBILITY_PRESET hidden)
set(CMAKE_VISIBILITY_INLINES_HIDDEN ON)
# The static registry also contains unused upstream plugin-loading APIs.
# Prune unreachable function sections from the final shared library.
add_compile_options(-ffunction-sections -fdata-sections)

foreach(option IN ITEMS
    BUILD_SHARED_LIBS
    WHISPER_BUILD_IS_DEV WHISPER_USE_SYSTEM_GGML WHISPER_USE_SYSTEM_LLAMA
    WHISPER_BUILD_TESTS WHISPER_BUILD_EXAMPLES WHISPER_BUILD_SERVER
    WHISPER_CURL WHISPER_SDL2 WHISPER_COMMON_FFMPEG
    WHISPER_COREML WHISPER_COREML_ALLOW_FALLBACK WHISPER_OPENVINO WHISPER_VITISAI WHISPER_MKL
    GGML_STATIC GGML_NATIVE GGML_BACKEND_DL GGML_CPU_ALL_VARIANTS GGML_CPU_KLEIDIAI
    GGML_CPU_HBM GGML_CPU_REPACK GGML_OPENMP GGML_OPENMP_FETCH GGML_LLAMAFILE GGML_CCACHE
    GGML_ACCELERATE GGML_BLAS GGML_CUDA GGML_HIP GGML_MUSA GGML_VULKAN GGML_WEBGPU
    GGML_ZDNN GGML_VIRTGPU GGML_VIRTGPU_BACKEND GGML_METAL GGML_RPC GGML_SYCL
    GGML_OPENVINO GGML_ET GGML_OPENCL GGML_HEXAGON GGML_ZENDNN
    GGML_BUILD_TESTS GGML_BUILD_EXAMPLES
    GGML_SSE42 GGML_AVX GGML_AVX_VNNI GGML_AVX2 GGML_BMI2 GGML_FMA GGML_F16C
    GGML_AVX512 GGML_AVX512_VBMI GGML_AVX512_VNNI GGML_AVX512_BF16
    GGML_AMX_TILE GGML_AMX_INT8 GGML_AMX_BF16)
    set(${option} OFF)
endforeach()
set(GGML_CPU ON)
if(ANDROID AND ANDROID_ABI STREQUAL "arm64-v8a")
    set(GGML_CPU_ARM_ARCH armv8-a)
    add_compile_options(-march=armv8-a)
elseif((ANDROID AND ANDROID_ABI STREQUAL "x86_64") OR
       (NOT ANDROID AND CMAKE_SYSTEM_NAME STREQUAL "Linux" AND CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|amd64)$"))
    add_compile_options(-march=x86-64 -mno-sse4.2 -mno-avx -mno-avx2 -mno-fma -mno-f16c -mno-bmi2)
else()
    message(FATAL_ERROR "Unsupported reply ASR component platform")
endif()

add_subdirectory("${REPLY_WHISPER_SOURCE}" upstream EXCLUDE_FROM_ALL)
foreach(target IN ITEMS whisper ggml ggml-base ggml-cpu)
    get_target_property(library_type ${target} TYPE)
    if(NOT library_type STREQUAL "STATIC_LIBRARY")
        message(FATAL_ERROR "Reply ASR requires static ${target}")
    endif()
endforeach()

add_library(sign_reply_asr SHARED reply_asr.cpp)
target_compile_features(sign_reply_asr PUBLIC cxx_std_17)
target_include_directories(sign_reply_asr PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}")
target_link_libraries(sign_reply_asr PRIVATE whisper)
target_link_options(sign_reply_asr PRIVATE
    "-Wl,--version-script=${CMAKE_CURRENT_SOURCE_DIR}/exports.map"
    -Wl,--exclude-libs,ALL -Wl,--gc-sections -Wl,-z,defs
    -Wl,-z,max-page-size=16384 -Wl,-z,common-page-size=16384)
set_property(TARGET sign_reply_asr APPEND PROPERTY LINK_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/exports.map")
if(ANDROID)
    target_link_options(sign_reply_asr PRIVATE "-Wl,-Map,${CMAKE_CURRENT_BINARY_DIR}/sign_reply_asr.map")
endif()
