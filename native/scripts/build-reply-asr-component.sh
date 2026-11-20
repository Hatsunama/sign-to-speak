#!/usr/bin/env bash
set -euo pipefail

role="${1:?expected host, host-leak-regression, host-vad-leak-red, arm64-v8a, or x86_64}"
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
    host-leak-regression)
        flags=(-DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++
               -DCMAKE_C_FLAGS=-fsanitize=address
               -DCMAKE_CXX_FLAGS=-fsanitize=address
               -DCMAKE_EXE_LINKER_FLAGS=-fsanitize=address)
        targets=(reply_asr_model_load_leak_regression)
        ;;
    host-vad-leak-red)
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

if [[ "$role" == host-leak-regression ]]; then
    # The producer stages the pinned repair in place. Freeze that boundary so
    # this existing green regression can never silently move to pristine input.
    printf '%s  %s\n' b1641a842cc9ec6edc3e8236d53335d52c5fc1eca9a8426d8c416b442214e4a9 \
        "$whisper_source/src/whisper.cpp" | sha256sum --check --strict
    sha256sum "$whisper_source/src/whisper.cpp" > \
        "$evidence/model-load-leak-source-sha256.txt"
elif [[ "$role" == host-vad-leak-red ]]; then
    # prepare verifies the pinned commit/tree before applying the production
    # cleanup repair. Materialize and independently verify HEAD so this VAD test
    # remains a true fail-before regression against untouched v1.9.5.
    whisper_source="$work/whisper.cpp-v1.9.5-pristine"
    git -C "$stage/whisper.cpp" worktree add --quiet --detach "$whisper_source" HEAD
    test "$(git -C "$whisper_source" rev-parse HEAD)" = d1be6fde11ac6e0407606b4e42fe72d34add8037
    test "$(git -C "$whisper_source" rev-parse 'HEAD^{tree}')" = 6fe2755682242a5c01aba65a22b3c0305d747841
    printf '%s  %s\n' 891168a838301bd2baa5e8b87422a854dafeeb7da914d40a2e4c6bf33ff448ed \
        "$whisper_source/src/whisper.cpp" | sha256sum --check --strict
    git -C "$whisper_source" status --porcelain --untracked-files=all > \
        "$evidence/vad-leak-source-status.txt"
    test ! -s "$evidence/vad-leak-source-status.txt"
    git -C "$whisper_source" rev-parse HEAD 'HEAD^{tree}' > \
        "$evidence/vad-leak-source-revisions.txt"
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
elif [[ "$role" == host-vad-leak-red ]]; then
    log="$work/vad-model-load-leak-regression.full.txt"
    vad_model="$stage/assets/reply-asr/ggml-silero-v6.2.0.bin"
    test "$(stat -c %s "$vad_model")" = 885098
    printf '%s  %s\n' 2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987 \
        "$vad_model" | sha256sum --check --strict
    sha256sum "$vad_model" > "$evidence/vad-leak-model-sha256.txt"
    set +e
    ASAN_OPTIONS=detect_leaks=1:halt_on_error=1 \
        LSAN_OPTIONS=exitcode=23:max_leaks=20:report_objects=0 \
        "$build/reply_asr_vad_model_load_leak_regression" \
        "$vad_model" > "$log" 2>&1
    status=$?
    set -e
    tail -c 131072 "$log" > "$evidence/vad-model-load-leak-regression.txt"
    if ! grep -Fq 'STS_EXPECTED_RED: VAD_LATE_READ_LEAK_TRIGGERED' "$log"; then
        printf 'STS_VAD_LEAK_TRIGGER_UNTRIGGERED status=%s\n' "$status" | \
            tee -a "$evidence/vad-model-load-leak-regression.txt"
        rm -f "$log"
        exit 90
    fi
    if ! grep -Fq 'ERROR: LeakSanitizer: detected memory leaks' "$log"; then
        printf 'STS_VAD_LEAK_NOT_DETECTED status=%s\n' "$status" | \
            tee -a "$evidence/vad-model-load-leak-regression.txt"
        rm -f "$log"
        exit 91
    fi
    if [[ "$status" -ne 23 ]]; then
        printf 'STS_VAD_LEAK_UNEXPECTED_SANITIZER_STATUS status=%s\n' "$status" | \
            tee -a "$evidence/vad-model-load-leak-regression.txt"
        rm -f "$log"
        exit 92
    fi
    printf 'STS_EXPECTED_RED: PINNED_V1_9_5_VAD_LOAD_LEAK status=23\n' | \
        tee -a "$evidence/vad-model-load-leak-regression.txt"
    rm -f "$log"
    cat "$evidence/vad-model-load-leak-regression.txt"
    exit 23
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
