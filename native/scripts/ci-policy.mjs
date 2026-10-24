// Configuration regression guard, not a security sandbox for arbitrary YAML/shell.
export function validateWorkflow(config) {
  const errors = [];
  const check = (ok, message) => { if (!ok) errors.push(message); };
  check(Object.keys(config.on ?? {}).join() === 'workflow_dispatch,pull_request', 'Manual or restricted PR runs only');
  check(JSON.stringify(config.permissions) === JSON.stringify({contents:'read'}), 'Read-only token');
  check(Object.keys(config.jobs ?? {}).join() === 'android', 'Exactly one job');
  const job = config.jobs?.android ?? {};
  check(job.if?.includes("github.event.pull_request.head.repo.full_name == github.repository") && job.if?.includes("github.head_ref == 'fix/android-native-foundation'"), 'Same-repository candidate branch only');
  check(job['runs-on'] === 'ubuntu-24.04', 'Standard Ubuntu runner only');
  check(job['timeout-minutes'] === 30, 'Bounded 30-minute job');
  check(config.concurrency?.['cancel-in-progress'] === true, 'Cancel superseded runs');
  const steps = job.steps ?? [];
  for (const step of steps) {
    check(!step['continue-on-error'], 'Do not suppress test failures');
    if (step.uses) check(/^[\w-]+\/[\w-]+@[a-f0-9]{40}$/.test(step.uses), 'Immutable action SHA');
    if (step.uses?.startsWith('actions/upload-artifact@')) {
      check(step.with?.['retention-days'] === 3, 'Three-day artifact retention');
    }
  }
  const runs = steps.map(s => s.run ?? '').join('\n');
  for (const gate of ['npm test', 'npm run typecheck', 'tests/run-java-tests.sh',
    'scripts/ci-policy.test.mjs', ':app:testDebugUnitTest', ':app:assembleDebug',
    ':app:verifyDebugMergedPermissions', ':app:verifyReleaseMergedPermissions',
    'git rev-parse HEAD', 'android.builder.sdkDownload=false']) {
    check(runs.includes(gate), `Missing gate: ${gate}`);
  }
  const flite = steps.find(s => s.id === 'flite_host');
  check(flite?.['timeout-minutes'] === 8 &&
    flite.if?.includes("steps.host.outcome == 'success'") &&
    flite.if?.includes("steps.sdk.outcome == 'success'") &&
    ['cmake -S android/app/src/main/cpp/flite', 'cmake --build', 'ctest --test-dir',
      'tests/java/FliteRequestHostTest.java', '-Djava.library.path='].every(token => flite.run?.includes(token)),
    'Bounded source-built Flite C/CTest and host JNI checks required');
  check(flite?.run?.includes('prepare_sources.py --check'), 'Read-only generated-source verification required');
  check(steps.filter(s => /:app:(testDebugUnitTest|verifyDebugMergedPermissions|assembleDebug|assembleQa)/.test(s.run ?? ''))
    .every(s => s.if?.includes("steps.flite_host.outcome == 'success'") || s.id === 'phone_qa'),
    'Android candidate gates require Flite host checks');
  check(steps.some(s => s.id === 'baseline' && s.run?.includes('ci-isolation-baseline.sh') && s['timeout-minutes'] === 12), 'Bounded counterfactual baseline required');
  check(steps.filter(s => /:app:(testDebugUnitTest|verifyDebugMergedPermissions|assembleDebug)/.test(s.run ?? '')).every(s => s.if?.includes("steps.baseline.outputs.restored == 'true'") && !s.if?.includes('steps.baseline.outcome')), 'Candidate gates require verified restoration, independently of baseline result');
  const phone = steps.find(s => s.id === 'phone_qa');
  check(phone?.['timeout-minutes'] === 10 && phone?.run?.includes('-PreactNativeArchitectures=arm64-v8a -PunsignedPhoneQa=true') && phone?.run?.includes(':app:verifyUnsignedPhoneQaIsolation :app:verifyQaMergedPermissions :app:assembleQa') && phone?.run?.includes('ci-phone-qa-audit.sh'), 'Bounded unsigned arm64 QA build and binary audit required');
  check(phone?.run?.includes(':app:assembleQaAndroidTest') &&
    phone.run.includes('-Pandroid.experimental.useDefaultDebugSigningConfigForProfileableBuildtypes=false'),
    'Phone instrumentation build must explicitly disable default signing');
  check(steps.some(s => s.uses?.startsWith('actions/upload-artifact@') &&
    s.with?.name?.startsWith('sign-phone-qa-') &&
    s.with.path?.includes('native/android/app/build/outputs/apk/androidTest/qa/*.apk')),
    'Retain the exact unsigned phone instrumentation artifact');
  check(!/keytool|keystore|storePassword|SIGN_QA_/.test(runs), 'No cloud signer or credential generation');
  check(runs.includes('JAVA_HOME_21_X64'), 'Robolectric SDK36 requires JDK21');
  check(steps.some(s => s.run?.includes(':app:verifyDebugMergedPermissions') && s.run.includes(':app:verifyReleaseMergedPermissions') && s.run.includes('--continue')), 'Run both manifest checks even when one fails');
  check(!/bundleRelease|assembleRelease|--licenses|continue-on-error|\|\| true/.test(runs), 'No release bundle or weakened gates');
  check(steps.some(s => s.uses?.startsWith('actions/checkout@') && s.with?.['persist-credentials'] === false), 'Do not persist credentials');
  check(!steps.some(s => /cache@/.test(s.uses ?? '') || s.with?.cache), 'No persistent caches');
  return errors;
}
