#!/usr/bin/env bash
set -euo pipefail

role="${1:?expected host, arm64-v8a, or x86_64}"
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
        target=reply_asr_smoke
        ;;
    arm64-v8a|x86_64)
        flags=(-DCMAKE_TOOLCHAIN_FILE="$ndk/build/cmake/android.toolchain.cmake"
               -DANDROID_ABI="$role" -DANDROID_PLATFORM=android-26
               -DANDROID_STL=c++_shared -DANDROID_SUPPORT_FLEXIBLE_PAGE_SIZES=ON)
        target=sign_reply_asr
        ;;
    *) exit 2 ;;
esac

"$cmake" --version > "$evidence/cmake-version.txt"
"$cmake" -S "$root/research/reply_asr_component" -B "$build" -G Ninja \
    -DCMAKE_MAKE_PROGRAM="$ninja" -DCMAKE_BUILD_TYPE=Release -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
    -DREPLY_WHISPER_SOURCE="$stage/whisper.cpp" "${flags[@]}" \
    2>&1 | tee "$evidence/$role-configure.txt"
"$cmake" --build "$build" --target "$target" --parallel 2 --verbose \
    2>&1 | tee "$evidence/$role-build.txt"

if [[ "$role" == host ]]; then
    python3 "$root/scripts/reply-asr-component.py" smoke "$build/reply_asr_smoke" "$stage" "$evidence"
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
