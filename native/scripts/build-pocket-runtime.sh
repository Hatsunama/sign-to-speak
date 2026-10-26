#!/usr/bin/env bash
# Hosted CI only. Build the locked Rust runtime for an explicitly requested Android ABI.
set -euo pipefail

abi="${1:-}"
case "$abi" in
  arm64-v8a) target=aarch64-linux-android; clang=aarch64-linux-android29-clang; clangxx=aarch64-linux-android29-clang++; cargo_linker=CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER; rustflags=CARGO_TARGET_AARCH64_LINUX_ANDROID_RUSTFLAGS ;;
  x86_64) target=x86_64-linux-android; clang=x86_64-linux-android29-clang; clangxx=x86_64-linux-android29-clang++; cargo_linker=CARGO_TARGET_X86_64_LINUX_ANDROID_LINKER; rustflags=CARGO_TARGET_X86_64_LINUX_ANDROID_RUSTFLAGS ;;
  *) echo "Usage: $0 arm64-v8a|x86_64" >&2; exit 2 ;;
esac

native="$(cd "$(dirname "$0")/.." && pwd -P)"
runtime="$native/vendor/pocket-tts-runtime"
sdk="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
: "${RUNNER_TEMP:?Hosted runner scratch directory required}"
[[ -n "$sdk" && -f "$runtime/Cargo.lock" ]] || { echo 'Android SDK and committed Cargo.lock required' >&2; exit 1; }
ndk="$sdk/ndk/27.1.12297006"
bin="$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin"
for file in "$bin/$clang" "$bin/$clangxx" "$bin/llvm-ar" "$bin/llvm-readelf" "$ndk/build/cmake/android.toolchain.cmake" "$sdk/cmake/3.31.5/bin/cmake"; do
  [[ -e "$file" ]] || { echo "Missing pinned Android build input: $file" >&2; exit 1; }
done
for command in rustc cargo rustup; do command -v "$command" >/dev/null; done
[[ -z "${RUSTFLAGS:-}" ]] || { echo 'Global RUSTFLAGS may not override the pinned Android target contract' >&2; exit 1; }
toolchain="$(rustup show active-toolchain | awk '{print $1}')"
[[ -n "$toolchain" ]] || { echo 'Preinstalled active Rust toolchain required' >&2; exit 1; }
rustup target add "$target" --toolchain "$toolchain"
rustup target list --installed | grep -Fx "$target" >/dev/null

wrapper="$RUNNER_TEMP/pocket-$abi.toolchain.cmake"
cat > "$wrapper" <<EOF
set(ANDROID_ABI $abi CACHE STRING "" FORCE)
set(ANDROID_PLATFORM android-29 CACHE STRING "" FORCE)
set(ANDROID_STL c++_shared CACHE STRING "" FORCE)
include("$ndk/build/cmake/android.toolchain.cmake")
EOF
export ANDROID_NDK_HOME="$ndk" CMAKE_TOOLCHAIN_FILE="$wrapper" CMAKE="$sdk/cmake/3.31.5/bin/cmake"
export CC="$bin/$clang" CXX="$bin/$clangxx" AR="$bin/llvm-ar"
export CC_aarch64_linux_android="$CC" CXX_aarch64_linux_android="$CXX" AR_aarch64_linux_android="$AR"
export CC_x86_64_linux_android="$CC" CXX_x86_64_linux_android="$CXX" AR_x86_64_linux_android="$AR"
printf -v "$cargo_linker" '%s' "$CC"
# x86_64 QA is compiled for baseline x86-64. No v3/AVX2 target-cpu contract is
# inferred from the host CPU; actual emulator inference remains a separate gate.
if [[ "$abi" == x86_64 ]]; then
  printf -v "$rustflags" '%s' '-C target-cpu=x86-64 -C link-arg=-Wl,-z,max-page-size=16384'
else
  printf -v "$rustflags" '%s' '-C link-arg=-Wl,-z,max-page-size=16384'
fi
export "$cargo_linker" "$rustflags"
export CARGO_BUILD_JOBS=2 CMAKE_BUILD_PARALLEL_LEVEL=2

cd "$runtime"
test -s Cargo.lock
# Fetch the entire pinned graph before *any* offline compilation, including build dependencies.
cargo fetch --locked
cargo metadata --locked --offline --format-version 1 > "$RUNNER_TEMP/pocket-$abi-metadata.json"
test -s "$RUNNER_TEMP/pocket-$abi-metadata.json"
cargo build --release --locked --offline --jobs 2 --target "$target" --lib

source_so="target/$target/release/libsign_pocket_tts_runtime.so"
test -s "$source_so"
dest="$native/generated/pocket-jniLibs/$abi"
mkdir -p "$dest"
cp "$source_so" "$dest/libsign_pocket_tts_runtime.so"
"$bin/llvm-readelf" -hW "$dest/libsign_pocket_tts_runtime.so" > "$RUNNER_TEMP/pocket-$abi-header.txt"
"$bin/llvm-readelf" -lW "$dest/libsign_pocket_tts_runtime.so" > "$RUNNER_TEMP/pocket-$abi-segments.txt"
"$bin/llvm-readelf" -dW "$dest/libsign_pocket_tts_runtime.so" > "$RUNNER_TEMP/pocket-$abi-dynamic.txt"
"$bin/llvm-readelf" -Ws "$dest/libsign_pocket_tts_runtime.so" > "$RUNNER_TEMP/pocket-$abi-symbols.txt"
python3 "$native/scripts/check-pocket-elf.py" "$abi" "$RUNNER_TEMP/pocket-$abi" runtime
sha256sum "$runtime/Cargo.lock" "$dest/libsign_pocket_tts_runtime.so"
