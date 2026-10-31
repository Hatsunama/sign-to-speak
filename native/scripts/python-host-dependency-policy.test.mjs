import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';

const yaml = createRequire(import.meta.url)('yaml');
const load = name => yaml.parse(readFileSync(new URL(`../../.github/workflows/${name}`, import.meta.url), 'utf8'));
const requirements = readFileSync(new URL('../requirements-host-tests.txt', import.meta.url), 'utf8');
const wheelHash = '8145dd6d10df13c559d1e4314df29695613575183fa2e2d11fac4c208c8a1f73';

function auditRequirements(source) {
  const declarations = source.split('\n').map(line => line.trim()).filter(line => line && !line.startsWith('#'));
  assert.deepEqual(declarations, [`numpy==2.3.2 --hash=sha256:${wheelHash}`]);
  assert.match(source, /https:\/\/pypi\.org\/pypi\/numpy\/2\.3\.2\/json/);
  assert.match(source, /Ubuntu 24\.04 x64 \/ CPython 3\.12/);
}

function auditWorkflow(workflow, jobName, suiteName, path) {
  const steps = workflow.jobs[jobName].steps;
  const provision = steps.find(step => step.name === 'Provision pinned Python host-test wheel');
  const suite = steps.find(step => step.name === suiteName);
  assert.ok(provision && suite);
  assert.ok(steps.indexOf(provision) < steps.indexOf(suite));
  assert.equal(provision['timeout-minutes'], 2);
  assert.equal(provision['continue-on-error'], undefined);
  assert.match(provision.run, /set -euo pipefail/);
  assert.match(provision.run, /target="\$RUNNER_TEMP\/sign-python-host-tests"/);
  assert.ok(provision.run.includes('python3 -m pip install --disable-pip-version-check --require-hashes --only-binary=:all: --no-deps --no-cache-dir --target "$target" -r ' + path));
  assert.match(provision.run, /PYTHONPATH="\$target" python3 -c 'import numpy; assert numpy\.__version__ == "2\.3\.2"'/);
  assert.match(provision.run, /echo "PYTHONPATH=\$target" >> "\$GITHUB_ENV"/);
  assert.match(suite.run, /for test_file in scripts\/\*\.test\.py; do python3 "\$test_file"; done/);
  assert.equal(steps.filter(step => /pip install/.test(step.run ?? '')).length, 1);
}

test('host-test NumPy is a single verified Ubuntu wheel', () => auditRequirements(requirements));

for (const [file, job, suite, path] of [
  ['sign-android.yml', 'android', 'Full host suite, TypeScript and workflow policy', 'requirements-host-tests.txt'],
  ['sign-speech-component-probe.yml', 'probe', 'Run complete bounded host checks', 'native/requirements-host-tests.txt'],
]) {
  test(`${file} provisions NumPy before full Python host tests`, () => {
    const workflow = load(file);
    auditWorkflow(workflow, job, suite, path);
    const noProvision = structuredClone(workflow);
    noProvision.jobs[job].steps = noProvision.jobs[job].steps.filter(step => step.name !== 'Provision pinned Python host-test wheel');
    assert.throws(() => auditWorkflow(noProvision, job, suite, path));
    const noHashes = structuredClone(workflow);
    noHashes.jobs[job].steps.find(step => step.name === 'Provision pinned Python host-test wheel').run = noHashes.jobs[job].steps.find(step => step.name === 'Provision pinned Python host-test wheel').run.replace('--require-hashes ', '');
    assert.throws(() => auditWorkflow(noHashes, job, suite, path));
    const wrongOrder = structuredClone(workflow);
    const steps = wrongOrder.jobs[job].steps;
    const index = steps.findIndex(step => step.name === 'Provision pinned Python host-test wheel');
    steps.splice(index, 1);
    steps.push({name: 'Provision pinned Python host-test wheel', ...workflow.jobs[job].steps[index]});
    assert.throws(() => auditWorkflow(wrongOrder, job, suite, path));
  });
}

test('rejects a changed NumPy digest or extra package', () => {
  assert.throws(() => auditRequirements(requirements.replace(wheelHash, '0'.repeat(64))));
  assert.throws(() => auditRequirements(`${requirements}\ntorch==2.0.0\n`));
});
