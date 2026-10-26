import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url), yaml=require('yaml');
const read=p=>readFileSync(new URL(p,import.meta.url),'utf8');
const workflow=yaml.parse(read('../../.github/workflows/sign-emulator-build.yml'));
const build=read('../android/app/emulator-qa.gradle');
const phone=read('../android/app/phone-qa.gradle');
const guard=read('../android/app/unsigned-qa-task-guard.gradle');
const main=read('../android/app/build.gradle');
function verifySharedTaskGuard(m,g,b,p) {
 const apply='apply from: \"unsigned-qa-task-guard.gradle\"';
 assert.equal(m.split(apply).length,2);
 for(const qa of ['emulator-qa.gradle','phone-qa.gradle']) {
  const position=m.indexOf(`apply from: \"${qa}\"`);
  assert.ok(position>m.indexOf(apply),`${qa} must follow the shared guard`);
 }
 assert.match(g,/def forbiddenUnsignedQaTask = \{ String path, String name, String type ->/);
 assert.match(g,/type\.contains\(\"ValidateSigningTask\"\)/);
 assert.match(g,/\[\":app:harmless\", \"com\.android\.build\.gradle\.internal\.tasks\.ValidateSigningTask\"\]/);
 assert.match(g,/ext\.forbiddenUnsignedQaTask = forbiddenUnsignedQaTask/);
 assert.match(g,/ext\.verifyUnsignedQaTaskFixtures = verifyUnsignedQaTaskFixtures/);
 for(const script of [b,p]) {
  assert.match(script,/gradle\.taskGraph\.whenReady \{ graph ->[\s\S]*?project\.ext\.verifyUnsignedQaTaskFixtures\(\)/);
  assert.match(script,/def forbidden = graph\.allTasks\.findAll \{ task ->\s*project\.ext\.forbiddenUnsignedQaTask\(task\.path, task\.name, task\.class\.name\)\s*\}/);
  assert.match(script,/if \(\!forbidden\.isEmpty\(\)\)\s*(?:\{|throw)/);
 }
}
function policy(w,b,g=guard,m=main,p=phone) {
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
 for(const gate of ['ci-emulator-diagnostics.sh','npm test','npm run typecheck','--dry-run','check-emulator-task-graph.py','ci-emulator-unsigned-audit.sh','build-pocket-runtime.sh x86_64','prepare-pocket-assets.py','check-pocket-apk-elf.py','-PunsignedEmulatorQa=true','android.builder.sdkDownload=false','-Pandroid.experimental.useDefaultDebugSigningConfigForProfileableBuildtypes=false']) assert.ok(runs.includes(gate),gate);
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
 verifySharedTaskGuard(m,g,b,p);
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
]) test('rejects '+label,()=>assert.throws(()=>policy(workflow,b)));
test('rejects shared signing task class guard removal',()=>{
 assert.throws(()=>policy(workflow,build,guard.replace('type.contains("ValidateSigningTask")','false')));
});
for(const [label,emulator,phoneScript] of [
 ['emulator task guard invocation removed',build.replace('project.ext.forbiddenUnsignedQaTask(task.path, task.name, task.class.name)','false'),phone],
 ['phone task guard invocation removed',build,phone.replace('project.ext.forbiddenUnsignedQaTask(task.path, task.name, task.class.name)','false')],
 ['emulator fixture verification removed',build.replace('project.ext.verifyUnsignedQaTaskFixtures()',''),phone],
 ['phone fixture verification removed',build,phone.replace('project.ext.verifyUnsignedQaTaskFixtures()','')],
]) test('rejects '+label,()=>assert.throws(()=>policy(workflow,emulator,guard,main,phoneScript)));
test('rejects shared task guard applied after QA scripts',()=>{
 const reordered=main.replace(/apply from: "unsigned-qa-task-guard\.gradle"\r?\n/,'').replace('apply from: "phone-qa.gradle"','apply from: "phone-qa.gradle"\napply from: "unsigned-qa-task-guard.gradle"');
 assert.notEqual(reordered,main);
 assert.equal(reordered.split('apply from: "unsigned-qa-task-guard.gradle"').length,2);
 assert.throws(()=>policy(workflow,build,guard,reordered));
});
test('test harness proves runtime pages, loaded libraries and both orientations without media capture',()=>{
 const source=read('../android/app/src/androidTestEmulatorQa/java/com/xmilo_at_your_side/sign_to_speak/EmulatorPrivacyLifecycleTest.java');
 for(const token of ['Os.sysconf(OsConstants._SC_PAGESIZE)','Build.SUPPORTED_ABIS[0]','/proc/self/maps','libhermesvm.so','libreactnative.so','libappmodules.so','startingOrientation','cycle < 2','repeat < 3','permission_deny_button','STREAMING'])assert.ok(source.includes(token),token);
 assert.doesNotMatch(source,/takeScreenshot|screenrecord|screencap|Bitmap\.compress|ImageCapture|VideoCapture/);
});
test('front-camera emulator journey selects through the rendered UI before each off-state Start',()=>{
 const source=read('../android/app/src/androidTestEmulatorQa/java/com/xmilo_at_your_side/sign_to_speak/EmulatorPrivacyLifecycleTest.java');
 const journey=source.slice(source.indexOf('realUiPermissionCameraAndLifecycleJourney()'),source.indexOf('private void setOrientation('));
 const startAgain=source.slice(source.indexOf('private CameraObservation startAgain()'),source.indexOf('private CameraObservation awaitStreamingCamera()'));
 const selection=source.slice(source.indexOf('private void selectFrontCameraWhileOff()'),source.indexOf('private CameraObservation awaitStreamingCamera()'));
 assert.match(journey,/stage\("01_launch_real_ui_camera_off"\);\s*selectFrontCameraWhileOff\(\);\s*clickUiLabel\(START\);/);
 assert.match(journey,/stage\("02_os_permission_denied"\);\s*selectFrontCameraWhileOff\(\);\s*clickUiLabel\(START\);/);
 assert.match(journey,/if \(device\.hasObject\(By\.pkg\(QA_PACKAGE\)\.text\(grantedButOff\)\)\) \{\s*assertNoCurrentCamera\(\);\s*selectFrontCameraWhileOff\(\);\s*clickUiLabel\(START\);/);
 assert.match(startAgain,/assertNoCurrentCamera\(\);\s*selectFrontCameraWhileOff\(\);\s*clickUiLabel\(START\);/);
 assert.match(journey,/launchRealActivity\(\);\s*assertNotSame\("Reopen must create a new Activity"[\s\S]*?CameraObservation afterReopen = startAgain\(\);/);
 assert.match(selection,/assertNoCurrentCamera\(\)/);
 assert.match(selection,/int permissionBefore = target\.checkSelfPermission\(Manifest\.permission\.CAMERA\)/);
 assert.match(selection,/assertEquals\("Camera selection must preserve permission state", permissionBefore,\s*target\.checkSelfPermission\(Manifest\.permission\.CAMERA\)\)/);
 assert.match(selection,/findViews\([^;]*TextView\.class\)/s);
 assert.match(selection,/"Switch to front camera"/);
 assert.match(selection,/"Switch to rear camera"/);
 assert.match(selection,/clickUiLabel\("Switch to front camera"\)/);
 assert.doesNotMatch(selection,/setLensFacing|setCameraFacing|setFacing/);
});
test('applied script receives typed provider and artifact from the compiled main build script',()=>{
 const main=read('../android/app/build.gradle');
 const verifyCapture=(m,b)=>{
  assert.match(m,/ext\.emulatorQaBuildInputs = \[bundledVariants: reactDebuggableVariants,\s*mergedManifest: SingleArtifact\.MERGED_MANIFEST\.INSTANCE\]/);
  assert.ok(m.indexOf('ext.emulatorQaBuildInputs')<m.indexOf('apply from: "emulator-qa.gradle"'));
  assert.match(b,/def qaBuildInputs = project\.extensions\.extraProperties\.get\("emulatorQaBuildInputs"\)/);
  assert.match(b,/def bundledVariants = qaBuildInputs\.bundledVariants/);
  assert.match(b,/variant\.artifacts\.get\(qaBuildInputs\.mergedManifest\)/);
  assert.doesNotMatch(b,/com\.facebook\.react\.ReactExtension|com\.android\.build\.api\.artifact\.SingleArtifact/);
 };
 verifyCapture(main,build);
 assert.throws(()=>verifyCapture(main.replace('bundledVariants: reactDebuggableVariants','bundledVariants: react.debuggableVariants'),build));
 assert.throws(()=>verifyCapture(main,build.replace('qaBuildInputs.bundledVariants','react.debuggableVariants')));
});

test('runner diagnostics only inspect access and ACL-tool availability',()=>{
 const diagnostics=read('./ci-emulator-diagnostics.sh');
 assert.match(diagnostics,/command -v setfacl/);
 assert.match(diagnostics,/getfacl -p \/dev\/kvm/);
 assert.doesNotMatch(diagnostics.split("\n").filter(line=>!line.trim().startsWith("#")).join("\n"),/sudo|chmod|usermod|udev|setfacl\s+-[mbx]/);
});

test('loaded-library evidence binds executable mappings to the current APK or native directory',()=>{
 const source=read('../android/app/src/androidTestEmulatorQa/java/com/xmilo_at_your_side/sign_to_speak/EmulatorPrivacyLifecycleTest.java');
 assert.match(source,/NativeLibraryMapping\.executableMapping\(library, maps,/);
 assert.match(source,/installed\.sourceDir, installed\.nativeLibraryDir/);
 assert.doesNotMatch(source,/maps\.contains\(library\)/);
 assert.ok(workflow.jobs.unsigned.steps.some(s=>s.run?.includes('check-native-library-mapping.sh')));
});
