import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';

const require = createRequire(import.meta.url);
const yaml = require('yaml');
const workflow = yaml.parse(readFileSync(new URL('../../.github/workflows/sign-speech-audition.yml', import.meta.url), 'utf8'));
const requirements = readFileSync(new URL('../vendor/pocket-tts-audition/requirements-cpu.txt', import.meta.url), 'utf8');
const manifest = JSON.parse(readFileSync(new URL('../vendor/pocket-tts-audition/manifest.json', import.meta.url), 'utf8'));

function audit(w, deps = requirements, assets = manifest) {
  assert.deepEqual(Object.keys(w.on), ['workflow_dispatch']);
  assert.deepEqual(w.permissions, {contents: 'read'});
  assert.equal(w.concurrency['cancel-in-progress'], true);
  assert.deepEqual(Object.keys(w.jobs), ['audition']);
  const job = w.jobs.audition;
  assert.equal(job['runs-on'], 'ubuntu-24.04');
  assert.equal(job['timeout-minutes'], 30);
  for (const term of [
    "github.event_name == 'workflow_dispatch'",
    "github.repository == 'Hatsunama/sign-to-speak'",
    "github.ref == 'refs/heads/codex/native-recognition-flow-20261024'",
    'github.event.repository.private == false',
  ]) assert.ok(job.if.includes(term), term);
  assert.equal(job.env.POCKET_SOURCE_COMMIT, assets.source.commit);
  assert.equal(job.env.POCKET_SOURCE_ARCHIVE_SHA256, assets.source.archive_sha256);
  assert.equal(assets.source.commit, 'a922b912dfa366975294afb568d815b305acde5d');
  assert.equal(assets.source.archive_sha256, '08dd2e7405609e788a9811f2786cdfc3f8f21f5e083a321108923fcae8f44b06');
  assert.equal(assets.source.archive_size, 908066);
  assert.equal(job.env.HF_HUB_OFFLINE, '1');
  assert.equal(job.env.TRANSFORMERS_OFFLINE, '1');
  const steps = job.steps;
  assert.equal(steps.length, 8);
  const [checkout, source, provision, unit, download, synthesis, notices, upload] = steps;
  assert.equal(checkout.uses, 'actions/checkout@11d5960a326750d5838078e36cf38b85af677262');
  assert.deepEqual(checkout.with, {'persist-credentials': false, ref: '${{ github.sha }}'});
  assert.match(source.run, /git rev-parse HEAD.*GITHUB_SHA/);
  assert.ok(unit['timeout-minutes'] <= 2);
  assert.ok(provision['timeout-minutes'] <= 14);
  assert.match(provision.run, /download\.pytorch\.org\/whl\/cpu/);
  assert.match(provision.run, /torch==2\.14\.0\+cpu/);
  assert.match(provision.run, /torch\.version\.cuda is None/g);
  assert.match(provision.run, /sha256sum --check --status/);
  assert.doesNotMatch(provision.run, /pip install.*\$SOURCE_DIR|pip install.*git\+/);
  assert.match(provision.run, /-r native\/vendor\/pocket-tts-audition\/requirements-cpu\.txt/);
  assert.match(provision.run, /-c "\$RUNNER_TEMP\/pocket-torch-constraint\.txt"/);
  assert.match(provision.run, /'torch==2\.14\.0\+cpu' > "\$RUNNER_TEMP\/pocket-torch-constraint\.txt"/);
  assert.match(provision.run, /installed_wheels/);
  assert.match(provision.run, /'sha256': digest/);
  assert.doesNotMatch(provision.run, /uv sync|uv\.lock|git clone/);
  assert.match(unit.run, /pocket-tts-venv\/bin\/python native\/scripts\/pocket-tts-audition\.test\.py/);
  assert.ok(download['timeout-minutes'] <= 4);
  assert.match(download.run, /pocket-tts-audition\.py download --asset-dir/);
  assert.ok(synthesis['timeout-minutes'] <= 8);
  assert.match(synthesis.run, /sudo unshare --net -- runuser/);
  assert.match(synthesis.run, /pocket-tts-audition\.py audition/);
  assert.match(synthesis.run, /--source-dir.*pocket-tts-source/);
  assert.match(synthesis.run, /--source-archive.*pocket-tts-source\.tar\.gz/);
  assert.match(synthesis.run, /--asset-dir/);
  assert.match(synthesis.run, /--output-dir/);
  assert.match(synthesis.run, /-eq 5/);
  assert.doesNotMatch(synthesis.run, /\|\|\s*true|continue-on-error|--allow-network/);
  assert.match(notices.run, /owner listening and pronunciation review/);
  assert.equal(upload.if, 'success()');
  assert.equal(upload.uses, 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02');
  assert.equal(upload.with['retention-days'], 3);
  assert.equal(upload.with['if-no-files-found'], 'error');
  assert.deepEqual(upload.with.path.trim().split('\n').map(s => s.trim()), [
    '${{ runner.temp }}/pocket-tts-audition/*.wav',
    '${{ runner.temp }}/pocket-tts-audition/*.json',
    '${{ runner.temp }}/pocket-tts-audition/*.txt',
  ]);
  for (const step of steps) {
    assert.equal(Boolean(step['continue-on-error']), false);
    assert.equal(Boolean(step.with?.cache), false);
  }
  assert.deepEqual(assets.model.assets.map(x => x.path).sort(), [
    'languages/english_18m_2026-10/embeddings/alba.safetensors',
    'languages/english_18m_2026-10/model.safetensors',
    'languages/english_18m_2026-10/tokenizer.model',
  ]);
  assert.equal(assets.model.revision, 'a7056f505a4a8ca72d8b5fb1440071d6f1a35739');
  assert.equal(assets.model.language, 'english_18m_2026-10');
  assert.equal(assets.model.voice, 'alba');
  assert.equal(assets.model.assets.reduce((n, x) => n + x.size, 0), 115951859);
  assert.deepEqual(Object.fromEntries(assets.model.assets.map(x => [x.path, x.sha256])), {
    'languages/english_18m_2026-10/model.safetensors': '599329dcea2f2a8d17b575e1c6e11b78822453c9073040d55c26faadf4883188',
    'languages/english_18m_2026-10/tokenizer.model': 'd461765ae179566678c93091c5fa6f2984c31bbe990bf1aa62d92c64d91bc3f6',
    'languages/english_18m_2026-10/embeddings/alba.safetensors': '11554cbe9f3d3a3e10722932df9952bb44928e534f7af3200524659166b5627a',
  });
  assert.match(deps, /^numpy==2\.3\.2$/m);
  assert.match(deps, /^PyYAML==6\.0\.2$/m);
  for (const line of deps.split('\n').map(x => x.trim()).filter(x => x && !x.startsWith('#'))) {
    assert.match(line, /^[A-Za-z][\w-]*==[\d.]+$/);
    assert.doesNotMatch(line, /^torch|^nvidia|^cuda/i);
  }
}

test('manual exact-source CPU audition obeys budget, network and evidence policy', () => audit(workflow));

for (const [name, mutation] of [
  ['push trigger', w => {w.on.push = {branches: ['main']};}],
  ['private repository', w => {w.jobs.audition.if = 'true';}],
  ['wrong candidate branch', w => {w.jobs.audition.if = w.jobs.audition.if.replace('codex/native-recognition-flow-20261024', 'main');}],
  ['write token', w => {w.permissions.contents = 'write';}],
  ['paid runner', w => {w.jobs.audition['runs-on'] = 'ubuntu-24.04-16core';}],
  ['long job', w => {w.jobs.audition['timeout-minutes'] = 60;}],
  ['mutable checkout', w => {w.jobs.audition.steps[0].uses = 'actions/checkout@v4';}],
  ['checkout credentials', w => {w.jobs.audition.steps[0].with['persist-credentials'] = true;}],
  ['untested source', w => {w.jobs.audition.steps[0].with.ref = '${{ github.ref }}';}],
  ['GPU Torch index', w => {w.jobs.audition.steps[2].run = w.jobs.audition.steps[2].run.replace('download.pytorch.org/whl/cpu', 'download.pytorch.org/whl/cu130');}],
  ['source archive hash bypass', w => {w.jobs.audition.steps[2].run = w.jobs.audition.steps[2].run.replace('sha256sum --check --status', 'cat');}],
  ['CPU constraint removed', w => {w.jobs.audition.steps[2].run = w.jobs.audition.steps[2].run.replace('-c "$RUNNER_TEMP/pocket-torch-constraint.txt"', '');}],
  ['source dependency resolution', w => {w.jobs.audition.steps[2].run += '\npython -m pip install "$SOURCE_DIR"';}],
  ['missing wheel hashes', w => {w.jobs.audition.steps[2].run = w.jobs.audition.steps[2].run.replace("'sha256': digest", "'sha256': 'unknown'");}],
  ['online synthesis', w => {w.jobs.audition.steps[5].run = w.jobs.audition.steps[5].run.replace('sudo unshare --net -- runuser', 'runuser');}],
  ['namespace failure hidden', w => {w.jobs.audition.steps[5].run += '\n|| true';}],
  ['only four WAVs', w => {w.jobs.audition.steps[5].run = w.jobs.audition.steps[5].run.replace('-eq 5', '-eq 4');}],
  ['failure artifact upload', w => {w.jobs.audition.steps[7].if = 'always()';}],
  ['model archive uploaded', w => {w.jobs.audition.steps[7].with.path += '\n${{ runner.temp }}/pocket-tts-assets/';}],
  ['long retention', w => {w.jobs.audition.steps[7].with['retention-days'] = 90;}],
]) test(`rejects ${name}`, () => {const w = structuredClone(workflow); mutation(w); assert.throws(() => audit(w));});

test('rejects added CUDA dependency', () => assert.throws(() => audit(workflow, requirements + '\nnvidia-cublas==13.0.0\n')));
test('rejects another model or voice asset', () => {
  const m = structuredClone(manifest);
  m.model.assets.push({...m.model.assets[0], path: 'languages/english_7m_2026-10/model.safetensors'});
  assert.throws(() => audit(workflow, requirements, m));
});
