# SPDX-License-Identifier: Apache-2.0
cmake_minimum_required(VERSION 3.16)
project(sign_backend_probe LANGUAGES C CXX)

set(LLAMA_SOURCE_DIR "" CACHE PATH "Path to the pinned llama.cpp checkout")
set(llama_revision "b1b770d90d94cad9e9ea5b993bdbc383a522fb28")
if(NOT LLAMA_SOURCE_DIR)
    message(FATAL_ERROR "Set LLAMA_SOURCE_DIR to the pinned llama.cpp checkout")
endif()
get_filename_component(LLAMA_SOURCE_DIR "${LLAMA_SOURCE_DIR}" ABSOLUTE)
set(backend_test_source "${LLAMA_SOURCE_DIR}/tests/test-backend-ops.cpp")
if(NOT EXISTS "${backend_test_source}")
    message(FATAL_ERROR "The llama.cpp checkout is missing tests/test-backend-ops.cpp")
endif()
find_package(Git REQUIRED)
execute_process(
    COMMAND "${GIT_EXECUTABLE}" rev-parse HEAD
    WORKING_DIRECTORY "${LLAMA_SOURCE_DIR}"
    RESULT_VARIABLE revision_status
    OUTPUT_VARIABLE checkout_revision
    OUTPUT_STRIP_TRAILING_WHITESPACE
)
if(NOT revision_status EQUAL 0 OR NOT checkout_revision STREQUAL llama_revision)
    message(FATAL_ERROR "LLAMA_SOURCE_DIR must be at ${llama_revision}, found '${checkout_revision}'")
endif()

set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(BUILD_SHARED_LIBS OFF CACHE BOOL "Link static ggml into the probe" FORCE)
set(LLAMA_BUILD_COMMON OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_TOOLS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_SERVER OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_MTMD OFF CACHE BOOL "" FORCE)
add_subdirectory("${LLAMA_SOURCE_DIR}" llama.cpp EXCLUDE_FROM_ALL)

find_package(Threads REQUIRED)
add_library(sign_backend_probe SHARED probe_jni.cpp "${backend_test_source}")
set_property(SOURCE "${backend_test_source}" APPEND PROPERTY COMPILE_DEFINITIONS main=sign_backend_ops_main)
target_compile_features(sign_backend_probe PRIVATE cxx_std_17)
target_link_libraries(sign_backend_probe PRIVATE ggml Threads::Threads log)
set_target_properties(sign_backend_probe PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/lib"
)
