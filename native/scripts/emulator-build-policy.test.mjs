import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url), yaml=require('yaml');
const read=p=>readFileSync(new URL(p,import.meta.url),'utf8');
const workflow=yaml.parse(read('../../.github/workflows/sign-emulator-build.yml'));
const build=read('../android/app/emulator-qa.gradle');
function policy(w,b) {
 assert.deepEqual(Object.keys(w.jobs),['unsigned']);
 const job=w.jobs.unsigned;
 assert.equal(job['runs-on'],'ubuntu-24.04');assert.equal(job['timeout-minutes'],30);
 assert.match(job.if,/github.event.repository.private == false/);
 assert.match(job.if,/github.event.pull_request.head.repo.full_name == github.repository/);
 assert.match(job.if,/github.head_ref == 'fix\/android-native-foundation'/);
 assert.deepEqual(w.permissions,{contents:'read'});
 assert.deepEqual(Object.keys(w.on),['workflow_dispatch','pull_request']);
 assert.equal(w.concurrency['cancel-in-progress'],true);
 for(const s of job.steps) {
  assert.equal(Boolean(s['continue-on-error']),false);
  if(s.uses) assert.match(s.uses,/^[\w-]+\/[\w-]+@[a-f0-9]{40}$/);
  if(s.uses?.startsWith('actions/upload-artifact')) assert.equal(s.with['retention-days'],3);
  assert.equal(Boolean(s.with?.cache),false);
 }
 assert.ok(job.steps.some(s=>s.uses?.startsWith('actions/checkout')&&s.with['persist-credentials']===false));
 const runs=job.steps.map(s=>s.run??'').join('\n');
 for(const gate of ['ci-emulator-diagnostics.sh','npm test','npm run typecheck','--dry-run','check-emulator-task-graph.py','ci-emulator-unsigned-audit.sh','-PunsignedEmulatorQa=true','android.builder.sdkDownload=false','-Pandroid.experimental.useDefaultDebugSigningConfigForProfileableBuildtypes=false']) assert.ok(runs.includes(gate),gate);
 assert.doesNotMatch(runs,/keytool|keystore|storePassword|--licenses|adb |emulator -avd|chmod|usermod|udev|assembleRelease|bundleRelease/);
 assert.ok(runs.indexOf('--dry-run')<runs.indexOf('ci-emulator-unsigned-audit.sh'));
 assert.match(b,/gradleProperty\("unsignedEmulatorQa"\)\.orElse\("false"\)/);
 assert.match(b,/if \(emulatorQaEnabled\)/);
 assert.match(b,/gradleProperty\("unsignedPhoneQa"\).* != "false"/);
 assert.match(b,/gradleProperty\("reactNativeArchitectures"\).* != "x86_64"/);
 for(const required of [/initWith release/,/applicationIdSuffix "\.qa"/,/versionNameSuffix "-emulator-qa"/,/debuggable false/,/signingConfig = null/,/abiFilters "x86_64"/,/testBuildType "emulatorQa"/,/gradle.taskGraph.whenReady/,/getAttributeNS\(ns, "testOnly"\) != "true"/]) assert.match(b,required);
 assert.doesNotMatch(b,/System\.getenv|storeFile|storePassword|keyPassword|signingConfigs\.debug/);
 assert.match(b,/android\.testVariants\.all/);
 assert.equal([...b.matchAll(/variant\.isSigningReady\(\)/g)].length,2);
 assert.match(b,/task\.class\.name\.contains\("ValidateSigningTask"\)/);
 assert.match(b,/android\.injected\.signing\./);
 assert.match(b,/projectProperties\[fallbackOption\] != "false"/);
}
test('bounded public unsigned build requires audited dry-run and preserves key boundary',()=>policy(workflow,build));
for(const [label,change] of [
 ['private job',w=>w.jobs.unsigned.if='true'],['paid runner',w=>w.jobs.unsigned['runs-on']='ubuntu-16-core'],
 ['write token',w=>w.permissions.contents='write'],['unsigned graph bypass',w=>{for(const s of w.jobs.unsigned.steps)if(s.run)s.run=s.run.replaceAll('--dry-run','');}],
 ['cloud signer',w=>w.jobs.unsigned.steps.push({run:'keytool -genkeypair'})],
 ['unbounded artifacts',w=>w.jobs.unsigned.steps.find(s=>s.uses?.startsWith('actions/upload-artifact')).with['retention-days']=90],
 ['permission change',w=>w.jobs.unsigned.steps.push({run:'sudo chmod 666 /dev/kvm'})],
]) test('rejects '+label,()=>{const w=structuredClone(workflow);change(w);assert.throws(()=>policy(w,build));});
for(const [label,b] of [
 ['production package',build.replace('applicationIdSuffix ".qa"','applicationIdSuffix ""')],
 ['debug target',build.replace('debuggable false','debuggable true')],
 ['signing inherited',build.replace('signingConfig = null','')],
 ['implicit opt-in',build.replace('if (emulatorQaEnabled)','if (true)')],
 ['secret input',build+'\nSystem.getenv("SECRET")'],
 ['resolved signer guard removed',build.replaceAll('variant.isSigningReady()', 'false')],
 ['signing task class guard removed',build.replace('task.class.name.contains("ValidateSigningTask")', 'false')],
]) test('rejects '+label,()=>assert.throws(()=>policy(workflow,b)));
test('test harness proves runtime pages, loaded libraries and both orientations without media capture',()=>{
 const source=read('../android/app/src/androidTestEmulatorQa/java/com/xmilo_at_your_side/sign_to_speak/EmulatorPrivacyLifecycleTest.java');
 for(const token of ['Os.sysconf(OsConstants._SC_PAGESIZE)','Build.SUPPORTED_ABIS[0]','/proc/self/maps','libhermesvm.so','libreactnative.so','libappmodules.so','startingOrientation','cycle < 2','repeat < 3','permission_deny_button','STREAMING'])assert.ok(source.includes(token),token);
 assert.doesNotMatch(source,/takeScreenshot|screenrecord|screencap|Bitmap\.compress|ImageCapture|VideoCapture/);
});
