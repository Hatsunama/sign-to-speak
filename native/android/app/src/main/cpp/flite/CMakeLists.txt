cmake_minimum_required(VERSION 3.22)
project(sign_to_speak_private_tts LANGUAGES C CXX)
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED YES)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED YES)
set(CMAKE_POSITION_INDEPENDENT_CODE YES)
find_package(Python3 REQUIRED COMPONENTS Interpreter)
execute_process(
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/prepare_sources.py" --check
    RESULT_VARIABLE FLITE_SOURCES_CHECK_RESULT
    OUTPUT_VARIABLE FLITE_SOURCES_CHECK_OUTPUT
    ERROR_VARIABLE FLITE_SOURCES_CHECK_ERROR)
if(NOT FLITE_SOURCES_CHECK_RESULT EQUAL 0)
    message(FATAL_ERROR "Flite prepared sources do not match approved upstream:\n${FLITE_SOURCES_CHECK_ERROR}")
endif()
include(sources.cmake)

add_library(flite_objects OBJECT ${FLITE_SOURCES})
target_compile_definitions(flite_objects PRIVATE CST_NO_SOCKETS CST_AUDIO_NONE)
target_include_directories(flite_objects PRIVATE generated/include . upstream/lang/usenglish
    upstream/lang/cmulex upstream/src/cg upstream/src/regex upstream/src/speech)
target_compile_options(flite_objects PRIVATE -include "${CMAKE_CURRENT_SOURCE_DIR}/private_runtime.h"
    -ffunction-sections -fdata-sections -fvisibility=hidden
    "-ffile-prefix-map=${CMAKE_CURRENT_SOURCE_DIR}=flite")
if(ANDROID_ABI STREQUAL "arm64-v8a")
    target_compile_options(flite_objects PRIVATE -mno-outline-atomics)
endif()

add_library(private_tts SHARED $<TARGET_OBJECTS:flite_objects>
    private_runtime.c private_no_io.c ../flite_jni.cpp)
target_include_directories(private_tts PRIVATE generated/include . ../
    upstream/lang/usenglish upstream/lang/cmulex upstream/src/cg upstream/src/regex upstream/src/speech)
if(NOT ANDROID)
    find_package(JNI REQUIRED)
    target_include_directories(private_tts PRIVATE ${JNI_INCLUDE_DIRS})
endif()
target_compile_definitions(private_tts PRIVATE CST_NO_SOCKETS CST_AUDIO_NONE)
target_compile_options(private_tts PRIVATE -ffunction-sections -fdata-sections -fvisibility=hidden)
target_link_libraries(private_tts PRIVATE m)
target_link_options(private_tts PRIVATE
    "-Wl,--version-script=${CMAKE_CURRENT_SOURCE_DIR}/exports.map"
    -Wl,--gc-sections -Wl,-z,defs -Wl,-z,max-page-size=16384)

if(NOT ANDROID)
    enable_testing()
    add_executable(test_private_tts $<TARGET_OBJECTS:flite_objects>
        private_runtime.c private_no_io.c
        "${CMAKE_CURRENT_SOURCE_DIR}/../../../../../../tests/flite/test_private_tts.c")
    target_include_directories(test_private_tts PRIVATE . generated/include upstream/lang/usenglish
        upstream/lang/cmulex upstream/src/cg upstream/src/regex upstream/src/speech)
    target_compile_definitions(test_private_tts PRIVATE STTS_TESTING CST_NO_SOCKETS CST_AUDIO_NONE)
    target_compile_options(test_private_tts PRIVATE -UNDEBUG)
    target_link_libraries(test_private_tts PRIVATE m pthread)
    add_test(NAME private_tts_engine COMMAND test_private_tts)
    add_executable(test_request_registry
        "${CMAKE_CURRENT_SOURCE_DIR}/../../../../../../tests/flite/test_request_registry.cpp")
    target_include_directories(test_request_registry PRIVATE .. . generated/include)
    target_link_libraries(test_request_registry PRIVATE pthread)
    add_test(NAME request_registry COMMAND test_request_registry)
endif()
