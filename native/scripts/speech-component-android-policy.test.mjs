import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';

const yaml = createRequire(import.meta.url)('yaml');
const workflow = yaml.parse(readFileSync(new URL('../../.github/workflows/sign-speech-component-android.yml', import.meta.url), 'utf8'));
const pocketSource = new URL('../android/app/src/main/cpp/pocket/', import.meta.url);
const cmakeSource = readFileSync(new URL('CMakeLists.txt', pocketSource), 'utf8');
const jniSource = readFileSync(new URL('pocket_jni.cpp', pocketSource), 'utf8');
const exportMap = readFileSync(new URL('exports.map', pocketSource), 'utf8');
const rustBuild = readFileSync(new URL('../vendor/pocket-tts-runtime/build.rs', import.meta.url), 'utf8');
const repo = 'Hatsunama/sign-to-speak';
const branch = 'codex/native-recognition-flow-20261024';
const dynamicPathAudit = String.raw`if re.search(r'\((?:RPATH|RUNPATH)\)', dynamic):`;
const step = (job, name) => {
  const found = job.steps.find(value => value.name === name);
  assert.ok(found, name);
  return found;
};

function evaluate(expression, github) {
  const tokens = expression.match(/\s*(?:&&|\|\||==|\(|\)|'(?:[^']*)'|[A-Za-z_][\w.]*)/g)?.map(x => x.trim()) ?? [];
  assert.equal(tokens.join(''), expression.replace(/\s+/g, ''));
  let i = 0;
  function atom() {
    const value = tokens[i++];
    if (value === '(') { const result = or(); assert.equal(tokens[i++], ')'); return result; }
    if (value?.startsWith("'")) return value.slice(1, -1);
    if (value === 'true') return true;
    if (value === 'false') return false;
    assert.match(value ?? '', /^github(?:\.[A-Za-z_][\w]*)+$/);
    return value.split('.').slice(1).reduce((object, key) => object?.[key], github);
  }
  function compare() { const left = atom(); if (tokens[i] !== '==') return left; i++; return left === atom(); }
  function and() { let result = compare(); while (tokens[i] === '&&') { i++; const right = compare(); result = Boolean(result && right); } return result; }
  function or() { let result = and(); while (tokens[i] === '||') { i++; const right = and(); result = Boolean(result || right); } return result; }
  const result = or(); assert.equal(i, tokens.length); return Boolean(result);
}

function event(event_name, {repository = repo, privateRepo = false, ref = `refs/heads/${branch}`,
  headRepo = repo, headRef = branch} = {}) {
  return {event_name, repository, ref, event: {repository: {private: privateRepo},
    pull_request: {head: {repo: {full_name: headRepo}, ref: headRef}}}};
}

function audit(w) {
  assert.deepEqual(Object.keys(w.on), ['workflow_dispatch', 'pull_request']);
  assert.deepEqual(w.on.pull_request.types, ['opened', 'synchronize', 'reopened']);
  assert.deepEqual(w.on.pull_request.paths, [
    '.github/workflows/sign-speech-component-android.yml',
    'native/scripts/speech-component-android-policy.test.mjs',
    'native/scripts/ci-sdk.sh', 'native/vendor/pocket-tts-runtime/**',
    'native/android/app/src/main/cpp/pocket/**']);
  assert.deepEqual(w.permissions, {contents: 'read'});
  assert.equal(w.concurrency['cancel-in-progress'], true);
  assert.deepEqual(Object.keys(w.jobs), ['component']);
  const job = w.jobs.component;
  assert.equal(job['runs-on'], 'ubuntu-24.04');
  assert.equal(job['timeout-minutes'], 30);
  for (const value of [event('workflow_dispatch'), event('pull_request', {ref: 'refs/pull/1/merge'})])
    assert.equal(evaluate(job.if, value), true);
  for (const value of [event('push'), event('workflow_dispatch', {ref: 'refs/heads/main'}),
    event('workflow_dispatch', {privateRepo: true}), event('pull_request', {headRepo: 'fork/repo'}),
    event('pull_request', {headRef: 'main'}), event('pull_request', {privateRepo: true}),
    event('pull_request', {repository: 'other/repo'})]) assert.equal(evaluate(job.if, value), false);
  assert.equal(job.env.EXPECTED_SOURCE_SHA, "${{ github.event_name == 'pull_request' && github.event.pull_request.head.sha || github.sha }}");
  assert.equal(job.env.CARGO_BUILD_JOBS, '2');
  assert.equal(job.env.CMAKE_BUILD_PARALLEL_LEVEL, '2');
  assert.equal(job.env.RUSTFLAGS, undefined);
  const checkout = step(job, 'Checkout exact candidate head');
  assert.equal(checkout.with.ref, job.env.EXPECTED_SOURCE_SHA);
  assert.equal(checkout.with['persist-credentials'], false);
  const preflight = step(job, 'Verify source, committed lock, and hosted tools');
  assert.match(preflight.run, /git rev-parse HEAD.*EXPECTED_SOURCE_SHA/);
  assert.match(preflight.run, /test -s native\/vendor\/pocket-tts-runtime\/Cargo.lock/);
  assert.match(preflight.run, /sha256sum[^\n]*native\/vendor\/pocket-tts-runtime\/build\.rs/);
  assert.doesNotMatch(preflight.run, /cargo generate-lockfile|curl |wget /);
  for (const file of ['CMakeLists.txt', 'pocket_jni.cpp', 'exports.map'])
    assert.ok(preflight.run.includes(`native/android/app/src/main/cpp/pocket/${file}`), file);
  const sdk = step(job, 'Verify pinned Android SDK and NDK without accepting licenses');
  assert.equal(sdk.run.trim(), 'bash native/scripts/ci-sdk.sh');
  const config = step(job, 'Configure NDK and Rust Android target');
  for (const token of ['27.1.12297006', 'aarch64-linux-android29-clang',
    'aarch64-linux-android29-clang++', 'llvm-ar', 'llvm-readelf', 'llvm-nm',
    'rustup target add aarch64-linux-android', 'ANDROID_ABI arm64-v8a',
    'ANDROID_PLATFORM android-29', 'ANDROID_STL c++_shared', 'CMAKE_TOOLCHAIN_FILE=',
    'CC_aarch64_linux_android=', 'CXX_aarch64_linux_android=', 'AR_aarch64_linux_android=',
    'CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER=',
    'CARGO_TARGET_AARCH64_LINUX_ANDROID_RUSTFLAGS=-C link-arg=-Wl,-z,max-page-size=16384'])
    assert.ok(config.run.includes(token), token);
  const fetch = step(job, 'Fetch complete locked Cargo graph and verify offline metadata');
  assert.match(fetch.run, /cargo fetch --locked\s/);
  assert.doesNotMatch(fetch.run, /cargo fetch[^\n]*--target/);
  assert.match(fetch.run, /cargo metadata --locked --offline --format-version 1/);
  const build = step(job, 'Cross compile actual Android ARM64 C ABI library');
  assert.match(build.run, /cargo build --release --locked --offline --jobs 2 --target aarch64-linux-android --lib/);
  assert.match(build.run, /libsign_pocket_tts_runtime\.so/);
  assert.match(build.run, /sha256sum --check.*cargo-lock-sha256/);
  const elf = step(job, 'Audit ARM64 ELF, 16 KiB segments, dependencies, and C ABI exports');
  for (const value of ['llvm-readelf" -hW', 'llvm-readelf" -lW', 'llvm-readelf" -dW',
    'llvm-nm" -D --defined-only', 'Machine:', 'AArch64', 'Type:', 'DYN',
    'align < 16384', 'offset % 16384 != vaddr % 16384', 'NEEDED',
    'sts_pocket_create', 'sts_pocket_synthesize', 'sts_pocket_cancel', 'sts_pocket_close'])
    assert.ok(elf.run.includes(value), value);
  assert.match(elf.run, /not needed <= allowed/);
  assert.match(elf.run, /soname != \['libsign_pocket_tts_runtime\.so'\]/);
  assert.match(elf.run, /RPATH\|RUNPATH/);
  assert.match(elf.run, /soname=\{soname\[0\]\}/);
  const jni = step(job, 'Cross compile and audit actual Android JNI bridge linkage');
  for (const token of ['-S "$source_dir"', '-B "$build"',
    'native/android/app/src/main/cpp/pocket', '-DCMAKE_TOOLCHAIN_FILE="$CMAKE_TOOLCHAIN_FILE"',
    '-DPOCKET_RUNTIME_LIBRARY="$runtime"', 'libsign_pocket_tts_runtime.so',
    '--target private_pocket_tts', 'libprivate_pocket_tts.so',
    'llvm-readelf" -hW', 'llvm-readelf" -lW', 'llvm-readelf" -dW',
    'llvm-nm" -D --defined-only', 'Machine:', 'AArch64', 'Type:', 'DYN',
    'align < 16384', 'offset % 16384 != vaddr % 16384', 'NEEDED',
    'RPATH|RUNPATH', 'symbols != expected', 'jni-elf-audit.txt'])
    assert.ok(jni.run.includes(token), token);
  assert.match(jni.run, /runtime not in needed/);
  assert.match(jni.run, /'\/' in name/);
  assert.ok(jni.run.includes(dynamicPathAudit));
  for (const name of ['Create', 'Synthesize', 'Cancel', 'Close'])
    assert.ok(jni.run.includes(`'${name}'`), name);
  assert.match(jni.run, /tee "\$evidence\/jni-cmake-configure\.txt"/);
  assert.match(jni.run, /tee "\$evidence\/jni-cmake-build\.txt"/);
  const upload = step(job, 'Retain source and ELF audit evidence');
  assert.equal(upload.if, 'always()');
  assert.equal(upload.with['retention-days'], 3);
  assert.equal(upload.with['if-no-files-found'], 'error');
  assert.deepEqual(upload.with.path.trim().split('\n').map(x => x.trim()), ['${{ runner.temp }}/pocket-android-evidence/*.txt']);
  for (const item of job.steps) {
    assert.ok(item['timeout-minutes'] === undefined || item['timeout-minutes'] <= 14);
    assert.notEqual(item['continue-on-error'], true);
    assert.equal(item.with?.cache, undefined);
    if (item.uses) assert.match(item.uses, /^[\w-]+\/[\w-]+@[a-f0-9]{40}$/);
    assert.doesNotMatch(item.run ?? '', /cargo generate-lockfile|cargo install|apt(?:-get)? install|--licenses|yes \||keytool|keystore|gradlew|adb |emulator |assembleRelease|bundleRelease|curl |wget |\|\| true/);
  }
  assert.ok(job.steps.indexOf(fetch) < job.steps.indexOf(build));
  assert.ok(job.steps.indexOf(build) < job.steps.indexOf(elf));
  assert.equal(job.steps.indexOf(jni), job.steps.indexOf(elf) + 1);
  assert.ok(job.steps.indexOf(jni) < job.steps.indexOf(upload));
}

function auditSourceGraph(cmake, cpp, map) {
  assert.match(cmake, /add_library\(sign_pocket_tts SHARED IMPORTED\)/);
  assert.match(cmake, /IMPORTED_LOCATION "\$\{POCKET_RUNTIME_LIBRARY\}"/);
  assert.match(cmake, /add_library\(private_pocket_tts SHARED pocket_jni\.cpp\)/);
  assert.match(cmake, /target_link_libraries\(private_pocket_tts PRIVATE sign_pocket_tts\)/);
  assert.match(cmake, /vendor\/pocket-tts-runtime\/include/);
  assert.match(cmake, /--version-script=\$\{CMAKE_CURRENT_SOURCE_DIR\}\/exports\.map/);
  assert.match(cmake, /-Wl,-z,defs/);
  assert.match(cmake, /-Wl,-z,max-page-size=16384/);
  assert.match(cpp, /#include "sign_pocket_tts\.h"/);
  assert.match(cpp, /#include <jni\.h>/);
  for (const name of ['Create', 'Synthesize', 'Cancel', 'Close']) {
    const symbol = `Java_com_xmilo_1at_1your_1side_sign_1to_1speak_PocketTtsRequest_native${name}`;
    assert.ok(cpp.includes(symbol), symbol);
    assert.ok(map.includes(`${symbol};`), symbol);
  }
  assert.match(map, /local:\s*\*;/);
  assert.doesNotMatch(cmake, /IMPORTED_NO_SONAME/);
}

function auditRustBuild(source) {
  assert.match(source, /CARGO_CFG_TARGET_OS/);
  assert.match(source, /matches!\(target_os\.as_str\(\), "android" \| "linux"\)/);
  assert.match(source, /cargo:rustc-link-arg-cdylib=-Wl,-soname,libsign_pocket_tts_runtime\.so/);
  assert.match(source, /if target_os == "android"/);
  assert.match(source, /cargo:rustc-link-arg-cdylib=-Wl,-z,max-page-size=16384/);
  assert.doesNotMatch(source, /RPATH|RUNPATH|patchelf|objcopy|IMPORTED_NO_SONAME/);
}

test('hosted component workflow enforces source, cost, lock, cross compiler, ELF, and key boundaries', () => audit(workflow));
test('JNI bridge source graph binds the real Rust runtime and four Java exports', () =>
  auditSourceGraph(cmakeSource, jniSource, exportMap));
test('Rust producer assigns a stable SONAME and Android page alignment', () => auditRustBuild(rustBuild));
for (const [name, mutate] of [
  ['fork PR', w => { w.jobs.component.if = 'true'; }],
  ['private repository', w => { w.jobs.component.if = w.jobs.component.if.replace('github.event.repository.private == false', 'true'); }],
  ['write permission', w => { w.permissions.contents = 'write'; }],
  ['paid runner', w => { w.jobs.component['runs-on'] = 'ubuntu-24.04-16core'; }],
  ['unbounded job', w => { w.jobs.component['timeout-minutes'] = 60; }],
  ['unlocked build', w => { step(w.jobs.component, 'Cross compile actual Android ARM64 C ABI library').run = 'cargo build --target aarch64-linux-android'; }],
  ['missing segment audit', w => { step(w.jobs.component, 'Audit ARM64 ELF, 16 KiB segments, dependencies, and C ABI exports').run = 'echo skip'; }],
  ['missing Rust SONAME audit', w => { step(w.jobs.component, 'Audit ARM64 ELF, 16 KiB segments, dependencies, and C ABI exports').run = step(w.jobs.component, 'Audit ARM64 ELF, 16 KiB segments, dependencies, and C ABI exports').run.replace("soname != ['libsign_pocket_tts_runtime.so']", 'false'); }],
  ['missing JNI probe', w => { w.jobs.component.steps.splice(w.jobs.component.steps.findIndex(x => x.name === 'Cross compile and audit actual Android JNI bridge linkage'), 1); }],
  ['early JNI probe', w => { const steps = w.jobs.component.steps; const index = steps.findIndex(x => x.name === 'Cross compile and audit actual Android JNI bridge linkage'); steps.splice(2, 0, steps.splice(index, 1)[0]); }],
  ['JNI source bypass', w => { step(w.jobs.component, 'Cross compile and audit actual Android JNI bridge linkage').run = 'echo bypass'; }],
  ['JNI runtime mismatch', w => { step(w.jobs.component, 'Cross compile and audit actual Android JNI bridge linkage').run = step(w.jobs.component, 'Cross compile and audit actual Android JNI bridge linkage').run.replace('runtime not in needed', 'false'); }],
  ['JNI path audit bypass', w => { step(w.jobs.component, 'Cross compile and audit actual Android JNI bridge linkage').run = step(w.jobs.component, 'Cross compile and audit actual Android JNI bridge linkage').run.replace("'/' in name", 'false'); }],
  ['JNI dynamic path audit bypass', w => { const jni = step(w.jobs.component, 'Cross compile and audit actual Android JNI bridge linkage'); jni.run = jni.run.replace(dynamicPathAudit, 'if False:'); }],
  ['no offline metadata', w => { step(w.jobs.component, 'Fetch complete locked Cargo graph and verify offline metadata').run = 'cargo fetch --locked'; }],
  ['key generation', w => { w.jobs.component.steps.push({run: 'keytool -genkeypair'}); }],
  ['long artifact retention', w => { step(w.jobs.component, 'Retain source and ELF audit evidence').with['retention-days'] = 90; }],
]) test(`rejects ${name}`, () => { const candidate = structuredClone(workflow); mutate(candidate); assert.throws(() => audit(candidate)); });
for (const [name, cmake, cpp, map] of [
  ['missing imported link', cmakeSource.replace('PRIVATE sign_pocket_tts', 'PRIVATE'), jniSource, exportMap],
  ['missing JNI source', cmakeSource.replace('SHARED pocket_jni.cpp', 'SHARED dummy.cpp'), jniSource, exportMap],
  ['missing export', cmakeSource, jniSource, exportMap.replace('PocketTtsRequest_nativeClose;', 'PocketTtsRequest_other;')],
]) test(`rejects ${name} in JNI source graph`, () => assert.throws(() => auditSourceGraph(cmake, cpp, map)));
for (const [name, source] of [
  ['missing SONAME', rustBuild.replace('-Wl,-soname,libsign_pocket_tts_runtime.so', '-Wl,--no-soname')],
  ['missing Android alignment', rustBuild.replace('-Wl,-z,max-page-size=16384', '-Wl,-z,max-page-size=4096')],
]) test(`rejects ${name} in Rust producer`, () => assert.throws(() => auditRustBuild(source)));
