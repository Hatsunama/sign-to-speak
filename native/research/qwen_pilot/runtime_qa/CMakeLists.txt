# SPDX-License-Identifier: Apache-2.0
cmake_minimum_required(VERSION 3.16)
project(qwen_backend_probe LANGUAGES C CXX)

set(LLAMA_SOURCE_DIR "" CACHE PATH "Path to the pinned llama.cpp checkout")
set(llama_revision "b1b770d90d94cad9e9ea5b993bdbc383a522fb28")
if(NOT EXISTS "${LLAMA_SOURCE_DIR}/tools/mtmd/mtmd-cli.cpp")
    message(FATAL_ERROR "Set LLAMA_SOURCE_DIR to the pinned llama.cpp checkout")
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

set(LLAMA_BUILD_COMMON ON CACHE BOOL "Build the shared llama.cpp CLI utilities")
set(LLAMA_BUILD_TOOLS ON CACHE BOOL "Build llama.cpp tools, including llama-mtmd-cli and llama-mtmd-debug")
set(LLAMA_BUILD_MTMD ON CACHE BOOL "Build libmtmd even when llama.cpp tools are disabled")
add_subdirectory("${LLAMA_SOURCE_DIR}" llama.cpp)

add_executable(qwen-backend-probe qwen_backend_probe.cpp)
target_compile_features(qwen-backend-probe PRIVATE cxx_std_17)
target_compile_definitions(qwen-backend-probe PRIVATE QWEN_QA_LLAMA_REVISION="${checkout_revision}")
target_link_libraries(qwen-backend-probe PRIVATE llama-common mtmd llama)
set_target_properties(qwen-backend-probe PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
