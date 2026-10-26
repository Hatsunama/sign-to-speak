import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';

const require = createRequire(import.meta.url);
const yaml = require('yaml');
const workflow = yaml.parse(readFileSync(new URL('../../.github/workflows/sign-speech-native-probe.yml', import.meta.url), 'utf8'));
const manifest = JSON.parse(readFileSync(new URL('../vendor/pocket-tts-audition/manifest.json', import.meta.url), 'utf8'));
const head = 'codex/native-recognition-flow-20261024';
const repo = 'Hatsunama/sign-to-speak';

// Evaluate the entire supported expression, including both sides of each OR.
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
  const value = or();
  assert.equal(i, tokens.length, 'trailing condition syntax');
  return Boolean(value);
}

function event(event_name, {repository = repo, privateRepo = false, ref = `refs/heads/${head}`,
  headRepo = repo, headRef = head} = {}) {
  return {event_name, repository, ref, event: {repository: {private: privateRepo},
    pull_request: {head: {repo: {full_name: headRepo}, ref: headRef}}}};
}

const allowed = [event('workflow_dispatch'), event('pull_request', {ref: 'refs/pull/8/merge'})];
const rejected = [
  event('workflow_dispatch', {ref: 'refs/heads/main'}),
  event('workflow_dispatch', {privateRepo: true}),
  event('pull_request', {ref: 'refs/pull/8/merge', headRepo: 'untrusted/fork'}),
  event('pull_request', {ref: 'refs/pull/8/merge', headRef: 'main'}),
  event('pull_request', {ref: 'refs/pull/8/merge', privateRepo: true}),
  event('pull_request', {ref: 'refs/pull/8/merge', repository: 'other/repo'}),
  event('push'),
];

function audit(w) {
  assert.deepEqual(Object.keys(w.on), ['workflow_dispatch', 'pull_request']);
  assert.deepEqual(w.on.pull_request.types, ['opened', 'synchronize', 'reopened']);
  assert.deepEqual(w.on.pull_request.paths, [
    '.github/workflows/sign-speech-native-probe.yml',
    'native/scripts/pocket-tts-native-probe.py',
    'native/scripts/pocket-tts-native-probe.test.py',
    'native/scripts/speech-native-probe-policy.test.mjs',
    'native/vendor/pocket-tts-native-probe/sign_token_probe.rs',
    'native/vendor/pocket-tts-audition/manifest.json',
    'native/scripts/pocket-tts-audition.py',
  ]);
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
  assert.equal(job.env.POCKET_PTTS_FORK_COMMIT, '7efa26973f81bba42cf2ace8da2c1eb9b605d54d');
  assert.equal(job.env.RAYON_NUM_THREADS, '2');
  assert.equal(job.env.CARGO_BUILD_JOBS, '2');
  assert.equal(job.env.POCKET_TTS_SEED, '42');
  assert.equal(manifest.model.language, 'english_18m_2026-10');
  assert.equal(manifest.model.voice, 'alba');
  assert.equal(manifest.model.assets.reduce((sum, asset) => sum + asset.size, 0), 115951859);
  assert.deepEqual(manifest.model.assets.map(x => x.path).sort(), [
    'languages/english_18m_2026-10/embeddings/alba.safetensors',
    'languages/english_18m_2026-10/model.safetensors',
    'languages/english_18m_2026-10/tokenizer.model',
  ]);
  const steps = job.steps;
  assert.equal(steps.length, 11);
  const [checkout, preflight, nodeSetup, install, unit, source, build, download, synthesis, notices, upload] = steps;
  const sourceSha = "${{ github.event_name == 'pull_request' && github.event.pull_request.head.sha || github.sha }}";
  assert.equal(checkout.uses, 'actions/checkout@11d5960a326750d5838078e36cf38b85af677262');
  assert.deepEqual(checkout.with, {'persist-credentials': false, ref: sourceSha});
  assert.equal(job.env.EXPECTED_SOURCE_SHA, sourceSha);
  assert.match(preflight.run, /git rev-parse HEAD.*EXPECTED_SOURCE_SHA/);
  for (const tool of ['rustc', 'cargo', 'cmake', 'c++']) assert.ok(preflight.run.includes(`command -v ${tool}`));
  assert.match(preflight.run, /avx2/);
  assert.equal(nodeSetup.uses, 'actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020');
  assert.deepEqual(nodeSetup.with, {'node-version': '24.19.0', 'check-latest': false});
  assert.match(install.run, /cd native\s+npm ci --ignore-scripts --no-audit --no-fund/);
  assert.match(install.run, /sentencepiece-0\.2\.1-cp312/);
  assert.match(install.run, /sha256sum --check --status/);
  assert.ok(steps.indexOf(install) < steps.indexOf(unit));
  assert.match(unit.run, /node --test native\/scripts\/speech-native-probe-policy\.test\.mjs/);
  assert.match(unit.run, /python3 native\/scripts\/pocket-tts-native-probe\.test\.py/);
  assert.ok(steps.indexOf(unit) < steps.indexOf(download));
  assert.match(source.run, /codeload\.github\.com\/kyutai-labs\/pocket-tts\/tar\.gz\/\$POCKET_SOURCE_COMMIT/);
  assert.match(source.run, /-eq 908066/);
  assert.equal((source.run.match(/sha256sum --check --status/g) ?? []).length, 2);
  assert.match(source.run, /test -f.*rust\/Cargo\.lock/);
  assert.match(source.run, /lock\.count\(expected\) != 1/);
  assert.match(source.run, /Original Rust source mismatch/);
  assert.match(build.run, /cd "\$RUST_SOURCE_DIR\/rust"/);
  assert.match(build.run, /cargo build --release --locked --jobs 2/);
  assert.match(build.run, /cargo test --release --locked --jobs 2/);
  assert.match(build.run, /cp .*sign_token_probe\.rs" src\/bin\/sign_token_probe\.rs/);
  assert.match(build.run, /cargo build --release --locked --jobs 2 --bin sign_token_probe/);
  assert.match(build.run, /sha256sum --check --status/);
  assert.ok(build['timeout-minutes'] <= 14);
  assert.match(download.run, /pocket-tts-audition\.py download --asset-dir/);
  assert.ok(download['timeout-minutes'] <= 3);
  assert.match(synthesis.run, /sudo unshare --net -- runuser/);
  assert.match(synthesis.run, /env RAYON_NUM_THREADS=2 POCKET_TTS_SEED=42/);
  assert.match(synthesis.run, /pocket-tts-native-probe\.py audition/);
  assert.match(synthesis.run, /--binary.*rust\/target\/release\/pocket-tts/);
  assert.match(synthesis.run, /--source-archive.*pocket-tts-rust-source\.tar\.gz/);
  assert.match(synthesis.run, /--diagnostic.*rust\/target\/release\/sign_token_probe/);
  assert.match(synthesis.run, /--asset-dir/);
  assert.match(synthesis.run, /--output-dir/);
  assert.match(synthesis.run, /-eq 5/);
  assert.ok(synthesis['timeout-minutes'] <= 7);
  assert.match(notices.run, /'binary_sha256'/);
  assert.match(notices.run, /'cargo_lock_sha256'/);
  assert.match(notices.run, /'app_source_sha'/);
  assert.match(notices.run, /'tool_versions'/);
  assert.match(notices.run, /cargo', 'metadata'[\s\S]*--locked', '--offline/);
  assert.match(notices.run, /dependency-licenses\.json/);
  assert.match(notices.run, /MIT/);
  assert.match(notices.run, /CC BY 4\.0/);
  assert.equal(upload.if, 'success()');
  assert.equal(upload.uses, 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02');
  assert.equal(upload.with['retention-days'], 3);
  assert.equal(upload.with['if-no-files-found'], 'error');
  assert.deepEqual(upload.with.path.trim().split('\n').map(x => x.trim()), [
    '${{ runner.temp }}/pocket-tts-native-probe/*.wav',
    '${{ runner.temp }}/pocket-tts-native-probe/*.json',
    '${{ runner.temp }}/pocket-tts-native-probe/*.txt',
  ]);
  for (const step of steps) {
    assert.equal(Boolean(step['continue-on-error']), false);
    assert.equal(Boolean(step.with?.cache), false);
    assert.doesNotMatch(step.run ?? '', /\|\|\s*true|cargo install|rustup (?:update|install)|apt(?:-get)? install|torch|cuda|--allow-network/);
    if (step !== install) assert.doesNotMatch(step.run ?? '', /pip install/);
  }
}

test('native probe is bounded, exact-source, CPU-only and offline for inference', () => audit(workflow));

for (const [name, mutate] of [
  ['push trigger', w => {w.on.push = {branches: ['main']};}],
  ['private repo', w => {w.jobs.probe.if = 'true';}],
  ['fork PR', w => {w.jobs.probe.if = w.jobs.probe.if.replace("github.event.pull_request.head.repo.full_name == 'Hatsunama/sign-to-speak' &&", 'true &&');}],
  ['wrong branch', w => {w.jobs.probe.if = w.jobs.probe.if.replaceAll(head, 'main');}],
  ['broad PR paths', w => {w.on.pull_request.paths.push('native/**');}],
  ['write token', w => {w.permissions.contents = 'write';}],
  ['paid runner', w => {w.jobs.probe['runs-on'] = 'ubuntu-24.04-16core';}],
  ['long job', w => {w.jobs.probe['timeout-minutes'] = 60;}],
  ['mutable checkout', w => {w.jobs.probe.steps[0].uses = 'actions/checkout@v4';}],
  ['merge SHA checkout', w => {w.jobs.probe.steps[0].with.ref = '${{ github.sha }}';}],
  ['missing AVX2 check', w => {w.jobs.probe.steps[1].run = w.jobs.probe.steps[1].run.replace('avx2', 'sse2');}],
  ['mutable Node setup', w => {w.jobs.probe.steps[2].uses = 'actions/setup-node@v4';}],
  ['missing locked Node install', w => {w.jobs.probe.steps[3].run = w.jobs.probe.steps[3].run.replace('npm ci --ignore-scripts --no-audit --no-fund', 'npm install');}],
  ['install after policy tests', w => {[w.jobs.probe.steps[3], w.jobs.probe.steps[4]] = [w.jobs.probe.steps[4], w.jobs.probe.steps[3]];}],
  ['missing tokenizer wheel hash', w => {w.jobs.probe.steps[3].run = w.jobs.probe.steps[3].run.replace('sha256sum --check --status', 'cat');}],
  ['missing lock check', w => {w.jobs.probe.steps[5].run = w.jobs.probe.steps[5].run.replace('sha256sum --check --status', 'cat');}],
  ['unlocked build', w => {w.jobs.probe.steps[6].run = w.jobs.probe.steps[6].run.replaceAll('--locked', '');}],
  ['wrong build cwd', w => {w.jobs.probe.steps[6].run = w.jobs.probe.steps[6].run.replace('cd "$RUST_SOURCE_DIR/rust"', 'cd "$GITHUB_WORKSPACE"');}],
  ['missing diagnostic build', w => {w.jobs.probe.steps[6].run = w.jobs.probe.steps[6].run.replace('--bin sign_token_probe', '--bin pocket-tts');}],
  ['more build jobs', w => {w.jobs.probe.steps[6].run = w.jobs.probe.steps[6].run.replaceAll('--jobs 2', '--jobs 8');}],
  ['online inference', w => {w.jobs.probe.steps[8].run = w.jobs.probe.steps[8].run.replace('sudo unshare --net -- runuser', 'runuser');}],
  ['missing diagnostic execution', w => {w.jobs.probe.steps[8].run = w.jobs.probe.steps[8].run.replace('--diagnostic', '--ignored');}],
  ['four WAVs', w => {w.jobs.probe.steps[8].run = w.jobs.probe.steps[8].run.replace('-eq 5', '-eq 4');}],
  ['failure upload', w => {w.jobs.probe.steps[10].if = 'always()';}],
  ['upload model assets', w => {w.jobs.probe.steps[10].with.path += '\n${{ runner.temp }}/pocket-tts-native-assets/';}],
  ['long retention', w => {w.jobs.probe.steps[10].with['retention-days'] = 30;}],
]) test(`rejects ${name}`, () => { const copy = structuredClone(workflow); mutate(copy); assert.throws(() => audit(copy)); });
