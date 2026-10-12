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
  check(!/bundleRelease|assembleRelease|--licenses|continue-on-error|\|\| true/.test(runs), 'No release bundle or weakened gates');
  check(steps.some(s => s.uses?.startsWith('actions/checkout@') && s.with?.['persist-credentials'] === false), 'Do not persist credentials');
  check(!steps.some(s => /cache@/.test(s.uses ?? '') || s.with?.cache), 'No persistent caches');
  return errors;
}
