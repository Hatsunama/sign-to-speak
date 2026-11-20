import {spawnSync} from 'node:child_process';
import {mkdirSync, mkdtempSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname, join} from 'node:path';

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

function runPocketToolchainFixture(t, abi, overrides = {}) {
  const scratch = mkdtempSync(join(process.env.RUNNER_TEMP || tmpdir(), 'pocket-toolchain-'));
  t.after(() => rmSync(scratch, {recursive: true, force: true}));

  const ndk = 'sdk/ndk/27.1.12297006';
  const llvm = ndk + '/toolchains/llvm/prebuilt/linux-x86_64/bin';
  const target = abi === 'arm64-v8a' ? 'aarch64-linux-android' : 'x86_64-linux-android';
  function write(path, content) {
    const absolute = join(scratch, path);
    mkdirSync(dirname(absolute), {recursive: true});
    writeFileSync(absolute, content, {mode: 0o755});
  }
  write('native/scripts/build-pocket-runtime.sh', shell.replace(/\r\n/g, '\n'));
  write('native/vendor/pocket-tts-runtime/Cargo.lock', '# Fixture lockfile\n');
  for (const file of [
    llvm + '/' + target + '29-clang', llvm + '/' + target + '29-clang++',
    llvm + '/llvm-ar', llvm + '/llvm-readelf', ndk + '/build/cmake/android.toolchain.cmake',
    'sdk/cmake/3.31.5/bin/cmake', 'trace.txt', 'runner-temp/.keep',
  ]) write(file, '');

  write('bin/rustup', '#!/usr/bin/env bash\n' + [
    'set -eu',
    'printf "rustup %s\\n" "$*" >> "$TEST_TRACE"',
    'case "$1 $2" in',
    '  "show active-toolchain") printf "%s (default)\\n" "$RUSTUP_TOOLCHAIN" ;;',
    '  "toolchain install")',
    '    [[ "$TEST_FAIL_INSTALL" != 1 ]] || exit 76',
    '    touch "$TEST_TOOLCHAIN_INSTALLED" ;;',
    '  "target add") ;;',
    '  "target list") printf "%s\\n" "$TEST_TARGET" ;;',
    '  "run "*)',
    '    [[ "$2" == "$RUSTUP_TOOLCHAIN" || -f "$TEST_TOOLCHAIN_INSTALLED" ]] || exit 1',
    '    export TEST_RUNNING_TOOLCHAIN="$2"',
    '    shift 2',
    '    exec "$@" ;;',
    '  *) exit 99 ;;',
    'esac',
  ].join('\n') + '\n');
  write('bin/rustc', '#!/usr/bin/env bash\n' + [
    '[[ "$*" == "-Vv" ]] || exit 99',
    'printf "rustc %s (fixture)\\nrelease: %s\\n" "$TEST_RUNNING_TOOLCHAIN" "$TEST_RUNNING_TOOLCHAIN"',
  ].join('\n') + '\n');
  write('bin/cargo', '#!/usr/bin/env bash\n' + [
    'toolchain="${TEST_RUNNING_TOOLCHAIN:-$RUSTUP_TOOLCHAIN}"',
    'printf "cargo %s %s\\n" "$toolchain" "$*" >> "$TEST_TRACE"',
    'case "$1" in',
    '  -V) printf "cargo %s (fixture)\\n" "$toolchain" ;;',
    '  fetch) ;;',
    '  metadata) printf "{}\\n" ;;',
    '  build) exit 77 ;;',
    '  *) exit 99 ;;',
    'esac',
  ].join('\n') + '\n');

  const bash = process.platform === 'win32'
    ? join(process.env.ProgramFiles || 'C:/Program Files', 'Git', 'bin', 'bash.exe')
    : 'bash';
  const result = spawnSync(bash, ['--noprofile', '--norc', '-c', [
    'export PATH="$PWD/bin:$PATH" ANDROID_HOME="$PWD/sdk" RUNNER_TEMP="$PWD/runner-temp"',
    'export TEST_TRACE="$PWD/trace.txt" TEST_TOOLCHAIN_INSTALLED="$PWD/toolchain-installed"',
    'exec bash native/scripts/build-pocket-runtime.sh "$1"',
  ].join('\n'), 'pocket-toolchain-fixture', abi], {
    cwd: scratch,
    env: {
      ...process.env,
      BASH_ENV: '',
      RUSTFLAGS: '',
      RUSTC: '',
      RUSTC_WRAPPER: '',
      RUSTC_WORKSPACE_WRAPPER: '',
      RUSTUP_TOOLCHAIN: '1.100.0',
      TEST_TARGET: target,
      TEST_FAIL_INSTALL: '0',
      ...overrides,
    },
    encoding: 'utf8',
    timeout: 15_000,
  });
  assert.ifError(result.error);
  const trace = readFileSync(join(scratch, 'trace.txt'), 'utf8').trim().split('\n').filter(Boolean);
  return {...result, trace, target, receipt: join(scratch, 'runner-temp', 'pocket-' + abi + '-toolchain.txt')};
}

for (const abi of ['arm64-v8a', 'x86_64']) {
  test('Pocket ' + abi + ' uses Rust 1.99.1 with ambient Rust 1.100.0', t => {
    const result = runPocketToolchainFixture(t, abi);
    assert.equal(result.status, 77, result.stderr);
    assert.deepEqual(result.trace.filter(call => /^cargo \S+ (fetch|metadata|build) /.test(call)), [
      'cargo 1.99.1 fetch --locked',
      'cargo 1.99.1 metadata --locked --offline --format-version 1',
      'cargo 1.99.1 build --release --locked --offline --jobs 2 --target ' + result.target + ' --lib',
    ]);
    assert.ok(result.trace.includes('rustup toolchain install 1.99.1 --profile minimal --no-self-update'), result.trace.join('\n'));
    assert.ok(result.trace.includes('rustup target add ' + result.target + ' --toolchain 1.99.1'), result.trace.join('\n'));
    assert.ok(result.trace.some(call => call.startsWith('rustup target list ') && call.includes('--installed') && call.includes('--toolchain 1.99.1')), result.trace.join('\n'));
    const receipt = readFileSync(result.receipt, 'utf8');
    assert.match(receipt, /^release: 1\.99\.1$/m);
    assert.match(receipt, /^cargo 1\.99\.1 /m);
    assert.ok(result.stdout.includes(receipt));
  });
}

test('failed Pocket toolchain install stops before fetching dependencies', t => {
  const result = runPocketToolchainFixture(t, 'arm64-v8a', {TEST_FAIL_INSTALL: '1'});
  assert.equal(result.status, 76, result.stderr);
  assert.ok(result.trace.includes('rustup toolchain install 1.99.1 --profile minimal --no-self-update'));
  assert.ok(result.trace.every(call => !/^(cargo|rustup target) /.test(call)));
});

for (const variable of ['RUSTC', 'RUSTC_WRAPPER', 'RUSTC_WORKSPACE_WRAPPER']) {
  test(variable + ' cannot override the pinned Pocket compiler', t => {
    const result = runPocketToolchainFixture(t, 'arm64-v8a', {[variable]: '/compiler-shadow'});
    assert.equal(result.status, 1, result.stderr);
    assert.match(result.stderr, new RegExp(variable));
    assert.deepEqual(result.trace, []);
  });
}
