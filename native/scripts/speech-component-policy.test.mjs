import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';

const yaml = createRequire(import.meta.url)('yaml');
const workflow = yaml.parse(readFileSync(new URL('../../.github/workflows/sign-speech-component-probe.yml', import.meta.url), 'utf8'));
const manifest = JSON.parse(readFileSync(new URL('../vendor/pocket-tts-audition/manifest.json', import.meta.url), 'utf8'));
const head = 'codex/native-recognition-flow-20261024';
const repo = 'Hatsunama/sign-to-speak';

function evaluateJobIf(expression, github) {
  const tokens = expression.match(/\s*(?:&&|\|\||==|\(|\)|'(?:[^']*)'|[A-Za-z_][\w.]*)/g)?.map(x => x.trim()) ?? [];
  assert.equal(tokens.join(''), expression.replace(/\s+/g, ''), 'unsupported condition syntax');
  let i = 0;
  function atom() {
    const token = tokens[i++];
    if (token === '(') { const value = or(); assert.equal(tokens[i++], ')'); return value; }
    assert.ok(token, 'incomplete condition');
    if (token.startsWith("'")) return token.slice(1, -1);
    if (token === 'true') return true;
    if (token === 'false') return false;
    assert.match(token, /^github(?:\.[A-Za-z_][\w]*)+$/);
    return token.split('.').slice(1).reduce((value, key) => value?.[key], github);
  }
  function comparison() { const left = atom(); if (tokens[i] !== '==') return left; i++; return left === atom(); }
  function and() { let value = comparison(); while (tokens[i] === '&&') { i++; const right = comparison(); value = Boolean(value && right); } return value; }
  function or() { let value = and(); while (tokens[i] === '||') { i++; const right = and(); value = Boolean(value || right); } return value; }
  const result = or();
  assert.equal(i, tokens.length);
  return Boolean(result);
}

function event(event_name, {repository = repo, privateRepo = false, ref = `refs/heads/${head}`,
  headRepo = repo, headRef = head} = {}) {
  return {event_name, repository, ref, event: {repository: {private: privateRepo},
    pull_request: {head: {repo: {full_name: headRepo}, ref: headRef}}}};
}

const allowed = [event('workflow_dispatch'), event('pull_request', {ref: 'refs/pull/8/merge'})];
const rejected = [event('push'), event('workflow_dispatch', {ref: 'refs/heads/main'}),
  event('workflow_dispatch', {privateRepo: true}), event('pull_request', {headRepo: 'fork/repo'}),
  event('pull_request', {headRef: 'main'}), event('pull_request', {privateRepo: true}),
  event('pull_request', {repository: 'other/repo'})];

function cargoLines(run) { return run.split('\n').map(x => x.trim()).filter(x => /^cargo\s+/.test(x)); }
function step(w, name) { const found = w.jobs.probe.steps.find(x => x.name === name); assert.ok(found, name); return found; }

function audit(w) {
  assert.deepEqual(Object.keys(w.on), ['workflow_dispatch', 'pull_request']);
  assert.deepEqual(w.on.pull_request.types, ['opened', 'synchronize', 'reopened']);
  assert.deepEqual(w.on.pull_request.paths, [
    '.github/workflows/sign-speech-component-probe.yml', 'native/scripts/speech-component-policy.test.mjs',
    'native/scripts/pocket-license-audit.py', 'native/scripts/pocket-license-audit.test.py',
    'native/scripts/pocket-tts-component-probe.py', 'native/scripts/pocket-tts-component-probe.test.py',
    'native/scripts/pocket-tts-audition.py', 'native/scripts/pocket-tts-audition.test.py',
    'native/scripts/speech-component-android-policy.test.mjs',
    'native/scripts/speech-native-probe-policy.test.mjs',
    'native/android/app/src/main/cpp/pocket/**',
    'native/android/app/src/main/java/com/xmilo_at_your_side/sign_to_speak/PocketTtsRequest.java',
    'native/android/app/src/main/java/com/xmilo_at_your_side/sign_to_speak/PocketModelStore.java',
    'native/android/app/src/main/java/com/xmilo_at_your_side/sign_to_speak/SpeechSession.java',
    'native/tests/java/PocketTtsRequestHostTest.java', 'native/tests/java/PocketModelStoreTest.java',
    'native/tests/java/SpeechTextContractTest.java', 'native/tests/java/SpeechSessionTest.java',
    'native/tests/run-java-tests.sh', 'native/vendor/pocket-tts-runtime/**',
    'native/vendor/pocket-tts-audition/manifest.json']);
  assert.deepEqual(w.permissions, {contents: 'read'});
  assert.equal(w.concurrency['cancel-in-progress'], true);
  assert.deepEqual(Object.keys(w.jobs), ['probe']);
  const job = w.jobs.probe;
  assert.equal(job['runs-on'], 'ubuntu-24.04');
  assert.equal(job['timeout-minutes'], 30);
  for (const sample of allowed) assert.equal(evaluateJobIf(job.if, sample), true);
  for (const sample of rejected) assert.equal(evaluateJobIf(job.if, sample), false);
  assert.equal(job.env.POCKET_SOURCE_COMMIT, manifest.source.commit);
  assert.equal(job.env.POCKET_SOURCE_ARCHIVE_SHA256, manifest.source.archive_sha256);
  assert.equal(job.env.POCKET_CARGO_LOCK_SHA256, '3f2c5396eaaad4c64258ae724e012fdaa0c7b3e71165bcf0ef2c6b0641d18d27');
  assert.equal(job.env.RUSTFLAGS, '-C target-cpu=x86-64-v3');
  assert.equal(job.env.CARGO_BUILD_JOBS, '2');
  assert.equal(job.env.RAYON_NUM_THREADS, '2');
  assert.equal(job.env.EXPECTED_SOURCE_SHA, "${{ github.event_name == 'pull_request' && github.event.pull_request.head.sha || github.sha }}");
  const checkout = step(w, 'Checkout exact component source');
  assert.equal(checkout.uses, 'actions/checkout@11d5960a326750d5838078e36cf38b85af677262');
  assert.deepEqual(checkout.with, {'persist-credentials': false, ref: job.env.EXPECTED_SOURCE_SHA});
  const preflight = step(w, 'Verify source and preinstalled CPU toolchain');
  assert.match(preflight.run, /git rev-parse HEAD.*EXPECTED_SOURCE_SHA/);
  for (const flag of ['avx2', 'fma', 'bmi1', 'bmi2']) assert.ok(preflight.run.includes(flag));
  for (const tool of ['rustc', 'cargo', 'cmake', 'c++', 'python3']) assert.ok(preflight.run.includes(tool));
  assert.match(preflight.run, /JAVA_HOME_17_X64:\?Preinstalled JDK 17 is required/);
  assert.match(preflight.run, /\$JAVA_HOME_17_X64\/bin\/javac.*-version/s);
  const node = step(w, 'Select pinned Node runtime');
  assert.equal(node.uses, 'actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020');
  assert.deepEqual(node.with, {'node-version': '24.19.0', 'check-latest': false});
  const local = step(w, 'Run complete bounded host checks');
  assert.match(local.run, /npm ci --ignore-scripts --no-audit --no-fund/);
  assert.match(local.run, /npm test/);
  assert.match(local.run, /npm run typecheck/);
  assert.match(local.run, /node --test scripts\/\*\.test\.mjs/);
  assert.match(local.run, /JAVA_HOME="\$JAVA_HOME_17_X64" JAVA_TEST_OUT_PARENT="\$RUNNER_TEMP" \.\/tests\/run-java-tests\.sh/);
  assert.match(local.run, /for test_file in scripts\/\*\.test\.py; do python3 "\$test_file"; done/);
  assert.match(local.run, /unittest discover -s vendor\/pocket-tts-runtime\/tests/);
  const seed = step(w, 'Verify upstream lock seed for first-run bootstrap');
  assert.match(seed.if, /hashFiles\('native\/vendor\/pocket-tts-runtime\/Cargo\.lock'\) == ''/);
  assert.match(seed.run, /codeload\.github\.com\/kyutai-labs\/pocket-tts\/tar\.gz\/\$POCKET_SOURCE_COMMIT/);
  assert.match(seed.run, /-eq 908066/);
  assert.equal((seed.run.match(/sha256sum --check --status/g) ?? []).length, 2);
  assert.match(seed.run, /tar -xOzf.*Cargo\.lock/s);
  const resolve = step(w, 'Resolve complete Cargo graph before compiling');
  assert.match(resolve.run, /if test -f Cargo\.lock; then\s+test -s Cargo\.lock\s+mode=locked-admission/s);
  assert.match(resolve.run, /cp .*upstream-Cargo\.lock.*Cargo\.lock\s+cargo generate-lockfile/s);
  const resolveCargo = cargoLines(resolve.run);
  assert.deepEqual(resolveCargo, ['cargo generate-lockfile', 'cargo fetch --locked',
    'cargo metadata --locked --offline --format-version 1 > "$RUNNER_TEMP/pocket-cargo-metadata.json"']);
  assert.doesNotMatch(resolve.run, /cargo fetch.*--target/);
  assert.match(resolve.run, /bootstrap-unadmitted/);
  assert.match(resolve.run, /resolved_lock_sha256/);
  assert.match(resolve.run, /echo 'bootstrap=true' >> "\$GITHUB_OUTPUT"/);
  const build = step(w, 'Compile cdylib and execute component unit tests');
  assert.deepEqual(cargoLines(build.run), ['cargo test --release --locked --offline --jobs 2',
    'cargo build --release --locked --offline --jobs 2 --lib']);
  assert.match(build.run, /libsign_pocket_tts_runtime\.so/);
  const jniBuild = step(w, 'Build actual host JNI bridge against compiled runtime');
  assert.match(jniBuild.run, /cmake -S native\/android\/app\/src\/main\/cpp\/pocket -B "\$build" -DCMAKE_BUILD_TYPE=Release/);
  assert.match(jniBuild.run, /-DPOCKET_RUNTIME_LIBRARY="\$runtime"/);
  assert.match(jniBuild.run, /(?:^|\n)\s*cmake --build "\$build" --config Release --parallel 2/);
  assert.match(jniBuild.run, /test -s "\$build\/libprivate_pocket_tts\.so"/);
  const download = step(w, 'Download pinned English 18M and Alba assets');
  assert.match(download.run, /pocket-tts-audition\.py download --asset-dir/);
  const diagnostic = step(w, 'Diagnose tokenizer against verified pinned asset');
  assert.match(diagnostic.run, /TOKENIZER_MODEL="\$RUNNER_TEMP\/pocket-component-assets\/languages\/english_18m_2026-10\/tokenizer\.model"/);
  assert.match(diagnostic.run, /cargo test --release --locked --offline --jobs 2 diagnostic_tokenizer_contract -- --ignored --nocapture/);
  assert.match(diagnostic.run, /\}\s*cat "\$RUNNER_TEMP\/pocket-component-evidence\/tokenizer-diagnostic\.txt"\s*$/);
  const provenance = step(w, 'Record component provenance and dependency declarations');
  for (const field of ['source_files_sha256', 'cargo_lock_sha256', 'component_cdylib_sha256',
    'host_jni_sha256', 'model_manifest_sha256', 'tool_versions', 'java_version', 'javac_version',
    'runner_image', 'dependency-license-report.json',
    'pocket-license-audit.py']) assert.ok(provenance.run.includes(field));
  assert.match(provenance.run, /--metadata "\$RUNNER_TEMP\/pocket-cargo-metadata\.json"/);
  assert.doesNotMatch(provenance.run, /re\.findall|identifiers\s*=/);
  assert.doesNotMatch(provenance.run, /cargo metadata/);
  const inference = step(w, 'Execute real component in network namespace');
  assert.match(inference.run, /sudo unshare --net -- runuser/);
  assert.match(inference.run, /pocket-tts-component-probe\.py/);
  for (const argument of ['--library', '--asset-dir', '--output-dir']) assert.ok(inference.run.includes(argument));
  assert.match(inference.run, /result\.json/);
  assert.match(inference.run, /native_component_probe_passed/);
  assert.match(inference.run, /real_C_ABI/);
  assert.match(inference.run, /-eq 5/);
  assert.match(inference.run, /cp .*execution\/result\.json.*evidence\//);
  const jni = step(w, 'Execute actual JNI request host test offline');
  assert.match(jni.run, /javac" -J-Xmx128m -d "\$classes"/);
  for (const file of ['SpeechSession.java', 'PocketTtsRequest.java', 'PocketTtsRequestHostTest.java']) assert.ok(jni.run.includes(file));
  assert.match(jni.run, /sudo unshare --net -- runuser -u/);
  assert.match(jni.run, /env LD_LIBRARY_PATH=.*pocket-component-host-jni.*target\/release/);
  assert.match(jni.run, /"\$JAVA_HOME\/bin\/java" -Xmx128m -Djava\.library\.path=/);
  assert.match(jni.run, /-cp "\$classes" PocketTtsRequestHostTest/);
  assert.match(jni.run, /pocket-component-execution\/component-assets/);
  const success = step(w, 'Upload successful WAVs and sanitized records only');
  assert.equal(success.if, 'success()');
  assert.equal(success.uses, 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02');
  assert.deepEqual(success.with.path.trim().split('\n').map(x => x.trim()), [
    '${{ runner.temp }}/pocket-component-evidence/*.wav',
    '${{ runner.temp }}/pocket-component-evidence/*.json',
    '${{ runner.temp }}/pocket-component-evidence/*.txt']);
  const diagnostics = step(w, 'Upload component diagnostics on failure');
  assert.equal(diagnostics.if, 'failure()');
  assert.deepEqual(diagnostics.with.path.trim().split('\n').map(x => x.trim()), [
    '${{ runner.temp }}/pocket-component-evidence/tokenizer-diagnostic.txt',
    '${{ runner.temp }}/pocket-component-evidence/jni-configure.txt',
    '${{ runner.temp }}/pocket-component-evidence/jni-build.txt',
    '${{ runner.temp }}/pocket-component-evidence/jni-host-test.txt',
    '${{ runner.temp }}/pocket-component-evidence/dependency-license-report.json']);
  assert.match(diagnostics.with.name, /component-diagnostics/);
  assert.equal(diagnostics.with['if-no-files-found'], 'ignore');
  assert.equal(diagnostics.with['retention-days'], 3);
  assert.equal(diagnostics.uses, 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02');
  const bootstrap = step(w, 'Upload bootstrap lock and resolution receipt separately');
  assert.equal(bootstrap.if, "${{ always() && steps.resolve.outputs.bootstrap == 'true' }}");
  assert.deepEqual(bootstrap.with.path.trim().split('\n').map(x => x.trim()), [
    '${{ runner.temp }}/pocket-component-bootstrap/Cargo.lock',
    '${{ runner.temp }}/pocket-component-bootstrap/resolution-receipt.json']);
  for (const upload of [success, bootstrap]) {
    assert.equal(upload.uses, 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02');
    assert.equal(upload.with['retention-days'], 3);
    assert.equal(upload.with['if-no-files-found'], 'error');
  }
  for (const s of job.steps) {
    assert.notEqual(s['continue-on-error'], true);
    assert.equal(s.with?.cache, undefined);
    assert.doesNotMatch(s.run ?? '', /\|\|\s*true|cargo install|rustup (?:update|install)|apt(?:-get)? install|pip install|torch|cuda|--allow-network/);
  }
  assert.ok(job.steps.indexOf(resolve) < job.steps.indexOf(build));
  assert.ok(job.steps.indexOf(local) < job.steps.indexOf(resolve));
  assert.ok(job.steps.indexOf(build) < job.steps.indexOf(jniBuild));
  assert.ok(job.steps.indexOf(jniBuild) < job.steps.indexOf(download));
  assert.ok(job.steps.indexOf(download) < job.steps.indexOf(diagnostic));
  assert.ok(job.steps.indexOf(diagnostic) < job.steps.indexOf(inference));
  assert.ok(job.steps.indexOf(inference) < job.steps.indexOf(jni));
  assert.ok(job.steps.indexOf(jni) < job.steps.indexOf(success));
  assert.ok(job.steps.indexOf(build) < job.steps.indexOf(download));
  assert.ok(job.steps.indexOf(provenance) < job.steps.indexOf(inference));
}

test('component probe is bounded, pinned, offline at inference and separates bootstrap evidence', () => audit(workflow));

test('standalone Java helper executes model store and text contract behavior', () => {
  const helper = readFileSync(new URL('../tests/run-java-tests.sh', import.meta.url), 'utf8');
  for (const source of ['PocketModelStore.java', 'PocketModelStoreTest.java', 'SpeechSession.java',
    'SpeechSessionTest.java', 'SpeechTextContractTest.java']) assert.ok(helper.includes(source));
  assert.match(helper, /(?:^|\n)"\$JAVA_HOME\/bin\/java" -Xmx128m -cp "\$OUT" com\.xmilo_at_your_side\.sign_to_speak\.PocketModelStoreTest "\$OUT"/);
  assert.match(helper, /(?:^|\n)"\$JAVA_HOME\/bin\/java" -Xmx64m -cp "\$OUT" SpeechTextContractTest/);
});

for (const [name, mutate] of [
  ['push trigger', w => {w.on.push = {}; }],
  ['private repository', w => {w.jobs.probe.if = 'true'; }],
  ['fork PR', w => {w.jobs.probe.if = w.jobs.probe.if.replace("github.event.pull_request.head.repo.full_name == 'Hatsunama/sign-to-speak'", 'true'); }],
  ['wrong branch', w => {w.jobs.probe.if = w.jobs.probe.if.replaceAll(head, 'main'); }],
  ['broad paths', w => {w.on.pull_request.paths.push('native/**'); }],
  ['write permission', w => {w.permissions.contents = 'write'; }],
  ['paid runner', w => {w.jobs.probe['runs-on'] = 'ubuntu-24.04-16core'; }],
  ['long job', w => {w.jobs.probe['timeout-minutes'] = 60; }],
  ['mutable checkout', w => {step(w, 'Checkout exact component source').uses = 'actions/checkout@v4'; }],
  ['merge checkout', w => {step(w, 'Checkout exact component source').with.ref = '${{ github.sha }}'; }],
  ['unverified lock seed', w => {step(w, 'Verify upstream lock seed for first-run bootstrap').run = step(w, 'Verify upstream lock seed for first-run bootstrap').run.replaceAll('sha256sum --check --status', 'cat'); }],
  ['unlocked fetch', w => {step(w, 'Resolve complete Cargo graph before compiling').run = step(w, 'Resolve complete Cargo graph before compiling').run.replace('cargo fetch --locked', 'cargo fetch'); }],
  ['target-limited fetch', w => {step(w, 'Resolve complete Cargo graph before compiling').run = step(w, 'Resolve complete Cargo graph before compiling').run.replace('cargo fetch --locked', 'cargo fetch --locked --target x86_64-unknown-linux-gnu'); }],
  ['offline metadata after build', w => {const a = step(w, 'Resolve complete Cargo graph before compiling'), b = step(w, 'Compile cdylib and execute component unit tests'); a.run = a.run.replace('cargo metadata --locked --offline --format-version 1 > "$RUNNER_TEMP/pocket-cargo-metadata.json"', ''); b.run += '\ncargo metadata --locked --offline --format-version 1 > "$RUNNER_TEMP/pocket-cargo-metadata.json"'; }],
  ['unlocked build', w => {step(w, 'Compile cdylib and execute component unit tests').run = step(w, 'Compile cdylib and execute component unit tests').run.replaceAll('--locked', ''); }],
  ['missing full Node suite', w => {step(w, 'Run complete bounded host checks').run = step(w, 'Run complete bounded host checks').run.replace('npm test', ':'); }],
  ['missing TypeScript check', w => {step(w, 'Run complete bounded host checks').run = step(w, 'Run complete bounded host checks').run.replace('npm run typecheck', ':'); }],
  ['missing Java helper execution', w => {step(w, 'Run complete bounded host checks').run = step(w, 'Run complete bounded host checks').run.replace('./tests/run-java-tests.sh', 'echo tests/run-java-tests.sh'); }],
  ['missing Python host suite', w => {step(w, 'Run complete bounded host checks').run = step(w, 'Run complete bounded host checks').run.replace('python3 "$test_file"', ': "$test_file"'); }],
  ['JNI bridge source only', w => {step(w, 'Build actual host JNI bridge against compiled runtime').run = step(w, 'Build actual host JNI bridge against compiled runtime').run.replace('cmake --build "$build" --config Release --parallel 2', 'echo cmake --build "$build" --config Release --parallel 2'); }],
  ['JNI class source only', w => {step(w, 'Execute actual JNI request host test offline').run = step(w, 'Execute actual JNI request host test offline').run.replace('-cp "$classes" PocketTtsRequestHostTest', '-cp "$classes" java.lang.Object'); }],
  ['JNI test outside network namespace', w => {step(w, 'Execute actual JNI request host test offline').run = step(w, 'Execute actual JNI request host test offline').run.replace('sudo unshare --net -- runuser', 'runuser'); }],
  ['JNI test before C ABI probe', w => {const steps = w.jobs.probe.steps, a = step(w, 'Execute actual JNI request host test offline'); steps.splice(steps.indexOf(a), 1); steps.splice(steps.indexOf(step(w, 'Execute real component in network namespace')), 0, a); }],
  ['online inference', w => {step(w, 'Execute real component in network namespace').run = step(w, 'Execute real component in network namespace').run.replace('sudo unshare --net -- runuser', 'runuser'); }],
  ['four WAVs', w => {step(w, 'Execute real component in network namespace').run = step(w, 'Execute real component in network namespace').run.replace('-eq 5', '-eq 4'); }],
  ['failure success upload', w => {step(w, 'Upload successful WAVs and sanitized records only').if = 'always()'; }],
  ['missing declaration helper', w => {step(w, 'Record component provenance and dependency declarations').run = step(w, 'Record component provenance and dependency declarations').run.replace('pocket-license-audit.py', 'missing.py'); }],
  ['missing successful tokenizer log', w => {step(w, 'Diagnose tokenizer against verified pinned asset').run = step(w, 'Diagnose tokenizer against verified pinned asset').run.replace(/\ncat "\$RUNNER_TEMP\/pocket-component-evidence\/tokenizer-diagnostic\.txt"\s*$/, ''); }],
  ['diagnostics mislabeled success', w => {step(w, 'Upload component diagnostics on failure').with.name = 'successful-wavs'; }],
  ['model upload', w => {step(w, 'Upload successful WAVs and sanitized records only').with.path += '\n${{ runner.temp }}/pocket-component-assets/'; }],
  ['unconditional lock upload', w => {step(w, 'Upload bootstrap lock and resolution receipt separately').if = 'always()'; }],
]) test(`rejects ${name}`, () => { const copy = structuredClone(workflow); mutate(copy); assert.throws(() => audit(copy)); });
