import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const shell = read('build-pocket-runtime.sh');
const cmake = read('../android/app/src/main/jni/CMakeLists.txt');
const elf = read('check-pocket-elf.py');
const apk = read('check-pocket-apk-elf.py');

test('runtime builds both requested ABIs from the complete locked graph', () => {
  for (const token of ['aarch64-linux-android', 'x86_64-linux-android',
    'aarch64-linux-android29-clang', 'x86_64-linux-android29-clang',
    'ANDROID_PLATFORM android-29', 'ANDROID_STL c++_shared',
    'cargo fetch --locked', 'cargo metadata --locked --offline',
    'cargo build --release --locked --offline', 'libsign_pocket_tts_runtime.so',
    '27.1.12297006', 'max-page-size=16384']) assert.ok(shell.includes(token), token);
  assert.ok(shell.indexOf('cargo fetch --locked') < shell.indexOf('cargo build --release'));
  assert.match(shell, /target-cpu=x86-64/);
  assert.doesNotMatch(shell, /bootstrap|curl|wget|--target x86_64-unknown-linux-gnu/);
});

test('CMake links only the matching source-built runtime through the Pocket JNI target', () => {
  for (const token of ['ANDROID_ABI STREQUAL "arm64-v8a"', 'ANDROID_ABI STREQUAL "x86_64"',
    'POCKET_RUNTIME_ROOT', 'libsign_pocket_tts_runtime.so',
    'add_subdirectory(../cpp/pocket pocket_build)']) assert.ok(cmake.includes(token), token);
  assert.doesNotMatch(cmake, /flite|private_tts\.so|add_subdirectory\(\.\.\/cpp\/flite/);
});

test('final APK audit checks linked binaries and 16 KiB ELF segments', () => {
  for (const token of ['libsign_pocket_tts_runtime.so', 'libprivate_pocket_tts.so',
    'libc++_shared.so', 'check(abi, prefix, role)', 'verify_pocket_assets(archive, names)']) assert.ok(apk.includes(token), token);
  for (const token of ['SONAME', 'NEEDED', 'RPATH', 'RUNPATH', '16384',
    'sts_pocket_synthesize', 'PocketTtsRequest_native']) assert.ok(elf.includes(token), token);
});
