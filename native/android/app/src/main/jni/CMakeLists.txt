cmake_minimum_required(VERSION 3.22)
project(appmodules)

# Preserve React Native's default OnLoad, codegen, Hermes/Fabric and autolinking.
# Keep this directory free of .cpp files: React Native uses any such file as
# an OnLoad override instead of its default fallback.
include(${REACT_ANDROID_DIR}/cmake-utils/ReactNative-application.cmake)

# The speech JNI bridge is a separate shared library, never an appmodules override.
add_subdirectory(../cpp/flite flite_build)