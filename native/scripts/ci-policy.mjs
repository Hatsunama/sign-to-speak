// Configuration regression guard, not a security sandbox for arbitrary YAML/shell.
export function validateWorkflow(config) {
  const errors = [];
  const check = (ok, message) => { if (!ok) errors.push(message); };
  check(Object.keys(config.on ?? {}).join() === 'workflow_dispatch,pull_request', 'Manual or restricted PR runs only');
  check(JSON.stringify(config.permissions) === JSON.stringify({contents:'read'}), 'Read-only token');
  check(Object.keys(config.jobs ?? {}).join() === 'android', 'Exactly one job');
  const job = config.jobs?.android ?? {};
  const normalizedJobIf = job.if?.replace(/\s+/g, ' ').trim();
  check(normalizedJobIf === "github.event_name == 'workflow_dispatch' || (github.event.pull_request.head.repo.full_name == github.repository && (github.head_ref == 'fix/android-native-foundation' || github.head_ref == 'codex/sign-to-speak-reply-app-20261121'))", 'Exact same-repository candidate branch allowlist only');
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
  const pocket = steps.find(s => s.id === 'pocket_runtime');
  check(pocket?.['timeout-minutes'] === 30 &&
    pocket.if?.includes("steps.host.outcome == 'success'") &&
    pocket.if?.includes("steps.sdk.outcome == 'success'") &&
    ['build-pocket-runtime.sh x86_64', 'build-pocket-runtime.sh arm64-v8a'].every(token => pocket.run?.includes(token)),
    'Both ABI runtimes must be source-built from locked Rust before Gradle');
  const assets = steps.find(s => s.id === 'pocket_assets');
  check(assets?.['timeout-minutes'] === 8 && assets.if?.includes("steps.pocket_runtime.outcome == 'success'") &&
    ['pocket-tts-audition.py download', 'pocket-tts-audition.py verify', 'prepare-pocket-assets.py',
      '--metadata', 'generated/pocket-assets'].every(token => assets.run?.includes(token)),
    'Pinned model and license assets required before Gradle');
  const replyInputs = steps.find(s => s.id === 'reply_inputs');
  check(replyInputs?.['timeout-minutes'] === 8 &&
    replyInputs.if?.includes("steps.host.outcome == 'success'") &&
    replyInputs.if?.includes("steps.sdk.outcome == 'success'") &&
    replyInputs.run?.includes('reply-asr-component.py prepare generated/reply-asr-component') &&
    replyInputs.run?.includes('ggml-tiny.en.bin') &&
    replyInputs.run?.includes('ggml-silero-v6.2.0.bin'),
    'Pinned repaired Reply ASR source and models required before Gradle');
  const fallback = steps.find(s => s.id === 'robolectric_maven_fallback');
  const firstGradle = steps.findIndex(s => s.run?.includes('./gradlew'));
  check(firstGradle > steps.indexOf(replyInputs), 'Reply ASR preparation must precede every Gradle invocation');
  check(fallback?.['timeout-minutes'] === 5 &&
    steps.indexOf(fallback) > steps.indexOf(replyInputs) &&
    steps.indexOf(fallback) < firstGradle &&
    steps.indexOf(fallback) < steps.indexOf(pocket) &&
    ['dl.cloudsmith.io/public/scandit/maven/maven/org/robolectric/nativeruntime-dist-compat/1.0.18/nativeruntime-dist-compat-1.0.18.jar',
      'a6c34d92c990754e5aee076032a36f8e38fcacb5eb878ebe0a324509f3864eef', '159128857',
      "--proto-redir '=https'", 'sha256sum --check', 'exclusiveContent', "includeModule('org.robolectric', 'nativeruntime-dist-compat')",
      'allprojects', 'ROBOLECTRIC_MAVEN_REPO', 'ROBOLECTRIC_INIT_SCRIPT'].every(token => fallback.run?.includes(token)) &&
    !fallback.run.includes('--retry') &&
    steps[firstGradle]?.if?.includes("steps.robolectric_maven_fallback.outcome == 'success'"),
    'Exact, single-attempt Robolectric native fallback required before every Gradle or Pocket build');
  check(steps.filter(s => /:app:(testDebugUnitTest|verifyDebugMergedPermissions|assembleDebug|assembleQa)/.test(s.run ?? ''))
    .every(s => s.if?.includes("steps.pocket_runtime.outcome == 'success'") &&
                 s.if?.includes("steps.pocket_assets.outcome == 'success'")),
    'Android candidate gates require Pocket source and model checks');
  check(steps.some(s => s.id === 'baseline' && s.run?.includes('ci-isolation-baseline.sh') && s['timeout-minutes'] === 12), 'Bounded counterfactual baseline required');
  check(steps.filter(s => /:app:(testDebugUnitTest|verifyDebugMergedPermissions|assembleDebug)/.test(s.run ?? '')).every(s => s.if?.includes("steps.baseline.outputs.restored == 'true'") && !s.if?.includes('steps.baseline.outcome')), 'Candidate gates require verified restoration, independently of baseline result');
  const phone = steps.find(s => s.id === 'phone_qa');
  check(phone?.['timeout-minutes'] === 15 && phone?.run?.includes('-PreactNativeArchitectures=arm64-v8a -PunsignedPhoneQa=true') && phone?.run?.includes(':app:verifyUnsignedPhoneQaIsolation :app:verifyQaMergedPermissions :app:assembleQa') && phone?.run?.includes('ci-phone-qa-audit.sh'), 'Bounded unsigned arm64 QA build and binary audit required');
  check(phone?.run?.includes('check-pocket-apk-elf.py') && runs.includes('x86_64') && runs.includes('arm64-v8a'), 'Final APK ELF audits required');
  check(phone?.run?.includes('python3 scripts/check-live-tracking-apk.py "$RUNNER_TEMP/sign-evidence/phone-qa/manifest.xml" "${apks[0]}" --abi arm64-v8a'),
    'Final arm64 phone-QA APK live tracking audit required');
  const reply = steps.find(s => s.id === 'reply_apk_gate');
  check(reply?.if?.includes("steps.debug_apk.outcome == 'success'") &&
    reply.run?.includes('apkanalyzer') &&
    reply.run?.includes('check-reply-apk-integration.py') &&
    reply.run?.includes('--abi x86_64') &&
    phone?.run?.includes('check-reply-apk-integration.py') &&
    phone.run.includes('--abi arm64-v8a'),
    'Final debug and phone-QA APK local reply packaging audits required');
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
  check(!/bundleRelease|assembleRelease|--licenses|continue-on-error|\|\| true|FliteRequest|prepare_sources\.py/.test(runs), 'No release bundle or weakened gates');
  check(steps.some(s => s.uses?.startsWith('actions/checkout@') && s.with?.['persist-credentials'] === false), 'Do not persist credentials');
  check(!steps.some(s => /cache@/.test(s.uses ?? '') || s.with?.cache), 'No persistent caches');
  return errors;
}
