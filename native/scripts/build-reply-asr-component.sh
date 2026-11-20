#!/usr/bin/env bash
set -euo pipefail

role="${1:?expected host, host-vad-compute-failure-regression, host-leak-regression, host-vad-leak-regression, arm64-v8a, or x86_64}"
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
whisper_source="$stage/whisper.cpp"

case "$role" in
    host)
        flags=(-DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++)
        targets=(reply_asr_smoke reply_asr_vad_semantic_probe)
        ;;
    host-vad-compute-failure-regression)
        flags=(-DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++)
        targets=(reply_asr_vad_compute_failure_regression)
        ;;
    host-leak-regression)
        flags=(-DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++
               -DCMAKE_C_FLAGS=-fsanitize=address
               -DCMAKE_CXX_FLAGS=-fsanitize=address
               -DCMAKE_EXE_LINKER_FLAGS=-fsanitize=address)
        targets=(reply_asr_model_load_leak_regression)
        ;;
    host-vad-leak-regression)
        flags=(-DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++
               -DCMAKE_C_FLAGS=-fsanitize=address
               -DCMAKE_CXX_FLAGS=-fsanitize=address
               -DCMAKE_EXE_LINKER_FLAGS=-fsanitize=address)
        targets=(reply_asr_vad_model_load_leak_regression)
        ;;
    arm64-v8a|x86_64)
        flags=(-DCMAKE_TOOLCHAIN_FILE="$ndk/build/cmake/android.toolchain.cmake"
               -DANDROID_ABI="$role" -DANDROID_PLATFORM=android-26
               -DANDROID_STL=c++_shared -DANDROID_SUPPORT_FLEXIBLE_PAGE_SIZES=ON)
        targets=(sign_reply_asr)
        ;;
    *) exit 2 ;;
esac

if [[ "$role" == host-vad-compute-failure-regression ]]; then
    # The fault-injection regression belongs to the same exact fully repaired
    # input as the two independent lifetime gates, but does not modify it.
    printf '%s  %s\n' 79f21daa732c073967d802725fa78d71100da0cdc34ef2fceae7e28dffa6cbd6 \
        "$whisper_source/src/whisper.cpp" | sha256sum --check --strict
    sha256sum "$whisper_source/src/whisper.cpp" > \
        "$evidence/vad-compute-failure-source-sha256.txt"
elif [[ "$role" == host-leak-regression ]]; then
    # The producer stages the pinned repair in place. Freeze that boundary so
    # this existing green regression can never silently move to pristine input.
    printf '%s  %s\n' 79f21daa732c073967d802725fa78d71100da0cdc34ef2fceae7e28dffa6cbd6 \
        "$whisper_source/src/whisper.cpp" | sha256sum --check --strict
    sha256sum "$whisper_source/src/whisper.cpp" > \
        "$evidence/model-load-leak-source-sha256.txt"
elif [[ "$role" == host-vad-leak-regression ]]; then
    # Freeze the final staged source after all three exact-byte repairs. This gate
    # remains independent from the general Whisper loader regression above.
    printf '%s  %s\n' 79f21daa732c073967d802725fa78d71100da0cdc34ef2fceae7e28dffa6cbd6 \
        "$whisper_source/src/whisper.cpp" | sha256sum --check --strict
    sha256sum "$whisper_source/src/whisper.cpp" > \
        "$evidence/vad-leak-source-sha256.txt"
fi

"$cmake" --version > "$evidence/cmake-version.txt"
"$cmake" -S "$root/research/reply_asr_component" -B "$build" -G Ninja \
    -DCMAKE_MAKE_PROGRAM="$ninja" -DCMAKE_BUILD_TYPE=Release -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
    -DREPLY_WHISPER_SOURCE="$whisper_source" "${flags[@]}" \
    2>&1 | tee "$evidence/$role-configure.txt"
"$cmake" --build "$build" --target "${targets[@]}" --parallel 2 --verbose \
    2>&1 | tee "$evidence/$role-build.txt"

if [[ "$role" == host ]]; then
    python3 "$root/scripts/reply-asr-component.py" smoke \
        "$build/reply_asr_smoke" "$build/reply_asr_vad_semantic_probe" "$stage" "$evidence"
elif [[ "$role" == host-vad-compute-failure-regression ]]; then
    log="$work/vad-compute-failure-regression.full.txt"
    vad_model="$stage/assets/reply-asr/ggml-silero-v6.2.0.bin"
    test "$(stat -c %s "$vad_model")" = 885098
    printf '%s  %s\n' 2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987 \
        "$vad_model" | sha256sum --check --strict
    sha256sum "$vad_model" > "$evidence/vad-compute-failure-model-sha256.txt"
    set +e
    "$build/reply_asr_vad_compute_failure_regression" "$vad_model" > "$log" 2>&1
    status=$?
    set -e
    tail -c 131072 "$log" > "$evidence/vad-compute-failure-regression.txt"
    rm -f "$log"
    cat "$evidence/vad-compute-failure-regression.txt"
    if ! grep -Fq 'STS_VAD_COMPUTE_MODEL_LOAD_PASS' \
        "$evidence/vad-compute-failure-regression.txt"; then
        exit 90
    fi
    if ! grep -Fq 'STS_VAD_COMPUTE_INJECTION wrapper_calls=1 injected_failures=1' \
        "$evidence/vad-compute-failure-regression.txt"; then
        exit 91
    fi
    exit "$status"
elif [[ "$role" == host-leak-regression ]]; then
    log="$work/model-load-leak-regression.full.txt"
    model="$stage/assets/reply-asr/ggml-tiny.en.bin"
    set +e
    ASAN_OPTIONS=detect_leaks=1:halt_on_error=1:exitcode=23 \
        "$build/reply_asr_model_load_leak_regression" \
        "$model" > "$log" 2>&1
    status=$?
    set -e
    tail -c 131072 "$log" > "$evidence/model-load-leak-regression.txt"
    rm -f "$log"
    cat "$evidence/model-load-leak-regression.txt"
    if [[ "$status" -ne 0 ]]; then
        exit "$status"
    fi
    if ! grep -Fq 'late-model-read-failure pass' \
        "$evidence/model-load-leak-regression.txt"; then
        printf 'STS_MODEL_LOAD_LEAK_MARKER_MISSING status=%s\n' "$status" | \
            tee -a "$evidence/model-load-leak-regression.txt"
        exit 93
    fi
elif [[ "$role" == host-vad-leak-regression ]]; then
    log="$work/vad-model-load-leak-regression.full.txt"
    vad_model="$stage/assets/reply-asr/ggml-silero-v6.2.0.bin"
    test "$(stat -c %s "$vad_model")" = 885098
    printf '%s  %s\n' 2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987 \
        "$vad_model" | sha256sum --check --strict
    sha256sum "$vad_model" > "$evidence/vad-leak-model-sha256.txt"
    set +e
    ASAN_OPTIONS=detect_leaks=1:halt_on_error=1:exitcode=23 \
        LSAN_OPTIONS=exitcode=23:max_leaks=20:report_objects=0 \
        "$build/reply_asr_vad_model_load_leak_regression" \
        "$vad_model" > "$log" 2>&1
    status=$?
    set -e
    tail -c 131072 "$log" > "$evidence/vad-model-load-leak-regression.txt"
    if ! grep -Fq 'STS_VAD_EARLY_METADATA_CLEANUP_PASS reads=9 close_calls=1' "$log" || \
       ! grep -Fq 'STS_VAD_LATE_READ_CLEANUP_PASS reads=8 bytes=99238 close_calls=1' "$log"; then
        printf 'STS_VAD_LEAK_TRIGGER_UNTRIGGERED status=%s\n' "$status" | \
            tee -a "$evidence/vad-model-load-leak-regression.txt"
        rm -f "$log"
        exit 90
    fi
    if grep -Eq 'ERROR: (AddressSanitizer|LeakSanitizer)' "$log"; then
        printf 'STS_VAD_SANITIZER_DIAGNOSTIC status=%s\n' "$status" | \
            tee -a "$evidence/vad-model-load-leak-regression.txt"
        rm -f "$log"
        exit 91
    fi
    if [[ "$status" -ne 0 ]]; then
        printf 'STS_VAD_LEAK_UNEXPECTED_SANITIZER_STATUS status=%s\n' "$status" | \
            tee -a "$evidence/vad-model-load-leak-regression.txt"
        rm -f "$log"
        exit 92
    fi
    printf 'STS_VAD_INIT_LIFETIME_REPAIRED sanitizer_status=0\n' | \
        tee -a "$evidence/vad-model-load-leak-regression.txt"
    rm -f "$log"
    cat "$evidence/vad-model-load-leak-regression.txt"
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
