#!/usr/bin/env bash
set -euo pipefail

role="${1:?expected host, host-leak-regression, arm64-v8a, or x86_64}"
sdk="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work="${RUNNER_TEMP:?missing hosted runner scratch}/sign-reply-component"
stage="$work/inputs"
evidence="$work/evidence"
build="$work/build-$role"
cmake="$sdk/cmake/3.31.5/bin/cmake"
ninja="$sdk/cmake/3.31.5/bin/ninja"
ndk="$sdk/ndk/27.1.12297006"
bin="$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin"
export CMAKE_BUILD_PARALLEL_LEVEL=2

case "$role" in
    host)
        flags=(-DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++)
        targets=(reply_asr_smoke reply_asr_vad_semantic_probe)
        ;;
    host-leak-regression)
        flags=(-DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++
               -DCMAKE_C_FLAGS=-fsanitize=address
               -DCMAKE_CXX_FLAGS=-fsanitize=address
               -DCMAKE_EXE_LINKER_FLAGS=-fsanitize=address)
        targets=(reply_asr_model_load_leak_regression)
        ;;
    arm64-v8a|x86_64)
        flags=(-DCMAKE_TOOLCHAIN_FILE="$ndk/build/cmake/android.toolchain.cmake"
               -DANDROID_ABI="$role" -DANDROID_PLATFORM=android-26
               -DANDROID_STL=c++_shared -DANDROID_SUPPORT_FLEXIBLE_PAGE_SIZES=ON)
        targets=(sign_reply_asr)
        ;;
    *) exit 2 ;;
esac

"$cmake" --version > "$evidence/cmake-version.txt"
"$cmake" -S "$root/research/reply_asr_component" -B "$build" -G Ninja \
    -DCMAKE_MAKE_PROGRAM="$ninja" -DCMAKE_BUILD_TYPE=Release -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
    -DREPLY_WHISPER_SOURCE="$stage/whisper.cpp" "${flags[@]}" \
    2>&1 | tee "$evidence/$role-configure.txt"
"$cmake" --build "$build" --target "${targets[@]}" --parallel 2 --verbose \
    2>&1 | tee "$evidence/$role-build.txt"

if [[ "$role" == host ]]; then
    python3 "$root/scripts/reply-asr-component.py" smoke \
        "$build/reply_asr_smoke" "$build/reply_asr_vad_semantic_probe" "$stage" "$evidence"
elif [[ "$role" == host-leak-regression ]]; then
    log="$work/model-load-leak-regression.full.txt"
    set +e
    ASAN_OPTIONS=detect_leaks=1:halt_on_error=1:exitcode=23 \
        "$build/reply_asr_model_load_leak_regression" \
        "$stage/assets/reply-asr/ggml-tiny.en.bin" > "$log" 2>&1
    status=$?
    set -e
    tail -c 131072 "$log" > "$evidence/model-load-leak-regression.txt"
    rm -f "$log"
    cat "$evidence/model-load-leak-regression.txt"
    exit "$status"
else
    so="$build/reply-asr/libsign_reply_asr.so"
    prefix="$evidence/$role"
    cp "$build/compile_commands.json" "$prefix-compile-commands.json"
    cp "$build/reply-asr/sign_reply_asr.map" "$prefix-link-map.txt"
    "$ninja" -C "$build" -t deps > "$prefix-includes.txt"
    "$bin/llvm-readelf" -h "$so" > "$prefix-header.txt"
    "$bin/llvm-readelf" -lW "$so" > "$prefix-segments.txt"
    "$bin/llvm-readelf" -dW "$so" > "$prefix-dynamic.txt"
    "$bin/llvm-nm" --defined-only --dynamic --extern-only "$so" > "$prefix-symbols.txt"
    "$bin/llvm-nm" --undefined-only --dynamic "$so" > "$prefix-imports.txt"
    sha256sum "$so" > "$prefix-sha256.txt"
    "$bin/clang" --version > "$prefix-clang-version.txt"
    python3 "$root/scripts/reply-asr-component.py" audit "$role" "$build" "$stage" "$evidence"
fi
