import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {validateWorkflow} from './ci-policy.mjs';
const require = createRequire(import.meta.url);
const yaml = require('yaml');
const source = readFileSync(new URL('../../.github/workflows/sign-android.yml', import.meta.url), 'utf8');
const baselineSource = readFileSync(new URL('./ci-isolation-baseline.sh', import.meta.url), 'utf8');
const config = yaml.parse(source);
test('development workflow satisfies cost, permission, provenance and test gates', () => {
  assert.deepEqual(validateWorkflow(config), []);
});
test('stages one exact Robolectric native JAR fallback before Gradle and scopes it to both unit suites', () => {
  const steps = config.jobs.android.steps;
  const fallbackIndex = steps.findIndex(step => step.id === 'robolectric_maven_fallback');
  const fallback = steps[fallbackIndex];
  const firstGradleIndex = steps.findIndex(step => step.run?.includes('./gradlew'));
  const pocketIndex = steps.findIndex(step => step.id === 'pocket_runtime');

  assert.ok(fallbackIndex > 0 && fallbackIndex < firstGradleIndex && fallbackIndex < pocketIndex);
  assert.equal(fallback['timeout-minutes'], 5);
  for (const token of [
    'https://dl.cloudsmith.io/public/scandit/maven/maven/org/robolectric/nativeruntime-dist-compat/1.0.18/nativeruntime-dist-compat-1.0.18.jar',
    'a6c34d92c990754e5aee076032a36f8e38fcacb5eb878ebe0a324509f3864eef',
    '159128857',
    '--connect-timeout 15',
    '--max-time 240',
    "--proto-redir '=https'",
    '<artifactId>nativeruntime-dist-compat</artifactId>',
    "includeModule('org.robolectric', 'nativeruntime-dist-compat')",
    'exclusiveContent',
    'allprojects',
    'ROBOLECTRIC_MAVEN_REPO',
    'ROBOLECTRIC_INIT_SCRIPT',
  ]) assert.ok(fallback.run.includes(token), `missing fallback policy token: ${token}`);
  assert.equal((fallback.run.match(/\bcurl\b/g) ?? []).length, 1);
  assert.doesNotMatch(fallback.run, /--retry/);
  assert.match(fallback.run, /stat -c %s[^\n]+159128857|159128857[^\n]+stat -c %s/);
  assert.match(fallback.run, /sha256sum --check/);
  assert.ok(steps[firstGradleIndex].if.includes("steps.robolectric_maven_fallback.outcome == 'success'"));

  const unitStep = steps.find(step => step.run?.includes(':app:testDebugUnitTest'));
  assert.equal((unitStep.run.match(/--init-script "\$ROBOLECTRIC_INIT_SCRIPT"/g) ?? []).length, 1);
  assert.equal((source.match(/--init-script "\$ROBOLECTRIC_INIT_SCRIPT"/g) ?? []).length, 1);
  assert.equal((baselineSource.match(/--init-script "\$ROBOLECTRIC_INIT_SCRIPT"/g) ?? []).length, 1);
  assert.match(baselineSource, /:\s*"\$\{ROBOLECTRIC_INIT_SCRIPT:\?[^}]+\}"/);
});
for (const [name, mutate] of [
  ['ungated pull requests', c => { c.jobs.android.if = ''; }],
  ['missing reply app branch', c => { c.jobs.android.if = c.jobs.android.if.replace(/\s*\|\|\s*github\.head_ref == 'codex\/sign-to-speak-reply-app-20261121'/, ''); }],
  ['arbitrary reply app branches', c => { c.jobs.android.if = c.jobs.android.if.replace("github.head_ref == 'codex/sign-to-speak-reply-app-20261121'", "startsWith(github.head_ref, 'codex/')"); }],
  ['unsupported SDK36 JDK', c => { for (const step of c.jobs.android.steps) if (step.run) step.run = step.run.replaceAll('JAVA_HOME_21_X64', 'JAVA_HOME_17_X64'); }],
  ['manifest failure hiding another variant', c => { for (const step of c.jobs.android.steps) if (step.run?.includes(':app:verifyDebugMergedPermissions')) step.run = step.run.replace('--continue', ''); }],
  ['missing baseline', c => { c.jobs.android.steps = c.jobs.android.steps.filter(s => s.id !== 'baseline'); }],
  ['unverified restoration', c => { c.jobs.android.steps.find(s => s.run?.includes(':app:testDebugUnitTest')).if = 'always()'; }],
  ['missing phone QA', c => { c.jobs.android.steps = c.jobs.android.steps.filter(s => s.id !== 'phone_qa'); }],
  ['missing reply APK packaging gate', c => { c.jobs.android.steps = c.jobs.android.steps.filter(s => s.id !== 'reply_apk_gate'); }],
  ['missing phone reply APK packaging gate', c => { const s = c.jobs.android.steps.find(s => s.id === 'phone_qa'); s.run = s.run.replace('check-reply-apk-integration.py', 'removed-reply-gate.py'); }],
  ['missing phone live tracking APK gate', c => { const s = c.jobs.android.steps.find(s => s.id === 'phone_qa'); s.run = s.run.replace('check-live-tracking-apk.py', 'removed-live-tracking-gate.py'); }],
  ['wrong live tracking APK input', c => { const s = c.jobs.android.steps.find(s => s.id === 'phone_qa'); s.run = s.run.replace('"${apks[0]}" --abi arm64-v8a', 'wrong.apk --abi arm64-v8a'); }],
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
  ['missing reply input preparation', c => { c.jobs.android.steps = c.jobs.android.steps.filter(s => s.id !== 'reply_inputs'); }],
  ['missing Robolectric Maven fallback', c => { c.jobs.android.steps = c.jobs.android.steps.filter(s => s.id !== 'robolectric_maven_fallback'); }],
  ['altered Robolectric native JAR digest', c => { const s = c.jobs.android.steps.find(s => s.id === 'robolectric_maven_fallback'); s.run = s.run.replace('a6c34d92c990754e5aee076032a36f8e38fcacb5eb878ebe0a324509f3864eef', '06c34d92c990754e5aee076032a36f8e38fcacb5eb878ebe0a324509f3864eef'); }],
  ['late reply input preparation', c => { const steps=c.jobs.android.steps; const index=steps.findIndex(s=>s.id==='reply_inputs'); steps.push(...steps.splice(index,1)); }],
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
