cmake_minimum_required(VERSION 3.22)
project(sign_to_speak_private_pocket_tts LANGUAGES CXX)

if(NOT DEFINED POCKET_RUNTIME_LIBRARY OR NOT EXISTS "${POCKET_RUNTIME_LIBRARY}")
    message(FATAL_ERROR "Set POCKET_RUNTIME_LIBRARY to the approved compiled libsign_pocket_tts.so")
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED YES)
set(CMAKE_POSITION_INDEPENDENT_CODE YES)

add_library(sign_pocket_tts SHARED IMPORTED)
set_target_properties(sign_pocket_tts PROPERTIES IMPORTED_LOCATION "${POCKET_RUNTIME_LIBRARY}")

add_library(private_pocket_tts SHARED pocket_jni.cpp)
target_include_directories(private_pocket_tts PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../../../vendor/pocket-tts-runtime/include")
if(NOT ANDROID)
    find_package(JNI REQUIRED)
    target_include_directories(private_pocket_tts PRIVATE ${JNI_INCLUDE_DIRS})
endif()
target_compile_options(private_pocket_tts PRIVATE -fvisibility=hidden -fno-rtti)
target_link_libraries(private_pocket_tts PRIVATE sign_pocket_tts)
target_link_options(private_pocket_tts PRIVATE
    "-Wl,--version-script=${CMAKE_CURRENT_SOURCE_DIR}/exports.map"
    -Wl,-z,defs -Wl,-z,max-page-size=16384)
if(NOT ANDROID)
    set_target_properties(private_pocket_tts PROPERTIES BUILD_RPATH "$ORIGIN")
endif()
