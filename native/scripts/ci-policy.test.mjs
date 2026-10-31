import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {validateWorkflow} from './ci-policy.mjs';
const require = createRequire(import.meta.url);
const yaml = require('yaml');
const source = readFileSync(new URL('../../.github/workflows/sign-android.yml', import.meta.url), 'utf8');
const config = yaml.parse(source);
test('development workflow satisfies cost, permission, provenance and test gates', () => {
  assert.deepEqual(validateWorkflow(config), []);
});
for (const [name, mutate] of [
  ['ungated pull requests', c => { c.jobs.android.if = ''; }],
  ['unsupported SDK36 JDK', c => { for (const step of c.jobs.android.steps) if (step.run) step.run = step.run.replaceAll('JAVA_HOME_21_X64', 'JAVA_HOME_17_X64'); }],
  ['manifest failure hiding another variant', c => { for (const step of c.jobs.android.steps) if (step.run?.includes(':app:verifyDebugMergedPermissions')) step.run = step.run.replace('--continue', ''); }],
  ['missing baseline', c => { c.jobs.android.steps = c.jobs.android.steps.filter(s => s.id !== 'baseline'); }],
  ['unverified restoration', c => { c.jobs.android.steps.find(s => s.run?.includes(':app:testDebugUnitTest')).if = 'always()'; }],
  ['missing phone QA', c => { c.jobs.android.steps = c.jobs.android.steps.filter(s => s.id !== 'phone_qa'); }],
  ['wrong phone ABI', c => { const s = c.jobs.android.steps.find(s => s.id === 'phone_qa'); s.run = s.run.replace('arm64-v8a', 'x86_64'); }],
  ['cloud signer creation', c => { c.jobs.android.steps.push({run:'keytool -genkeypair'}); }],
  ['unbounded job', c => { c.jobs.android['timeout-minutes'] = 360; }],
  ['paid runner', c => { c.jobs.android['runs-on'] = 'ubuntu-latest-16-cores'; }],
  ['write permissions', c => { c.permissions.contents = 'write'; }],
  ['unpinned action', c => { c.jobs.android.steps.find(s => s.uses).uses = 'actions/checkout@v4'; }],
  ['suppressed failure', c => { c.jobs.android.steps[0]['continue-on-error'] = true; }],
  ['missing Pocket runtime build', c => { c.jobs.android.steps = c.jobs.android.steps.filter(s => s.id !== 'pocket_runtime'); }],
  ['missing x86 source compile', c => { c.jobs.android.steps.find(s => s.id === 'pocket_runtime').run = 'bash scripts/build-pocket-runtime.sh arm64-v8a'; }],
  ['missing model preparation', c => { c.jobs.android.steps = c.jobs.android.steps.filter(s => s.id !== 'pocket_assets'); }],
  ['missing phone instrumentation', c => { const s = c.jobs.android.steps.find(s => s.id === 'phone_qa'); s.run = s.run.replace(':app:assembleQaAndroidTest', ''); }],
  ['default phone test signing fallback', c => { const s = c.jobs.android.steps.find(s => s.id === 'phone_qa'); s.run = s.run.replace('-Pandroid.experimental.useDefaultDebugSigningConfigForProfileableBuildtypes=false', ''); }],
  ['missing phone test artifact', c => { const s = c.jobs.android.steps.find(s => s.with?.name?.startsWith('sign-phone-qa-')); s.with.path = 'native/android/app/build/outputs/apk/qa/*.apk'; }],
  ['missing native tests', c => { c.jobs.android.steps = c.jobs.android.steps.filter(s => !s.run?.includes(':app:testDebugUnitTest')); }],
  ['missing merged manifest gate', c => { c.jobs.android.steps = c.jobs.android.steps.filter(s => !s.run?.includes(':app:verifyDebugMergedPermissions')); }],
  ['release bundle', c => { c.jobs.android.steps.push({run:'./gradlew bundleRelease'}); }],
  ['unbounded artifacts', c => { c.jobs.android.steps.find(s => s.uses?.startsWith('actions/upload-artifact')).with['retention-days'] = 90; }],
  ['automatic publication trigger', c => { c.on.push = {}; }],
]) {
  test(`rejects ${name}`, () => {
    const altered = structuredClone(config); mutate(altered);
    assert.notEqual(validateWorkflow(altered).length, 0);
  });
}
