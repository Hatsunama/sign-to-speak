import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import crypto from 'node:crypto';
const root = new URL('../', import.meta.url);
const read = p => fs.readFileSync(new URL(p, root), 'utf8');
const java = 'android/app/src/main/java/com/xmilo_at_your_side/sign_to_speak/';

test('manifest asks for camera only, disables backup and cleartext', () => {
  const manifest = read('android/app/src/main/AndroidManifest.xml');
  const requested = [...manifest.matchAll(/<uses-permission\b[^>]*\/>/g)]
    .map(m => m[0]).filter(tag => !tag.includes('tools:node="remove"'))
    .map(tag => tag.match(/android:name="([^"]+)"/)[1]);
  assert.deepEqual(requested, ['android.permission.CAMERA']);
  assert.match(manifest, /<uses-permission android:name="android.permission.SYSTEM_ALERT_WINDOW" tools:node="remove" \/>/);
  assert.match(manifest, /android:allowBackup="false"/);
  assert.match(manifest, /android:usesCleartextTraffic="false"/);
  assert.doesNotMatch(manifest, /screenOrientation=/);
});

test('camera foundation has no recording, frame export, network, logging or speech fallback', () => {
  const source = read(java + 'PrivateCameraView.kt');
  assert.doesNotMatch(source, /MediaRecorder|VideoCapture|ImageCapture|FileOutputStream|Bitmap\.compress|Log\.|TextToSpeech|https?:|OkHttp/);
  assert.match(source, /onHostPause\(\).*stop\(\)/);
  assert.match(source, /onHostResume\(\) = Unit/);
  assert.match(source, /previewView.visibility = INVISIBLE/);
  assert.match(read(java + 'MainActivity.kt'), /FLAG_SECURE/);
});

test('canonical owner renderer and seed are exact pinned copies', () => {
  for (const [file, expected] of [
    ['RefractBackgroundView.kt', '3ae35fd6e40bb419ef4d5a6cddc8baea7ac498e3d91f61b3b19ccfe502c9452b'],
    ['RefractSeed.kt', '4790b0f5976406755f9f2b72c5a390aad5746441f95591074382f28710e8f66d'],
  ]) {
    const content = fs.readFileSync(new URL('android/app/src/main/java/com/hatsunama/refract/' + file, root));
    assert.equal(crypto.createHash('sha256').update(content).digest('hex'), expected);
  }
});

test('all build variants are bundled and unsigned with bounded Gradle resources', () => {
  const build = read('android/app/build.gradle');
  assert.match(build, /debuggableVariants = \[\]/);
  assert.equal([...build.matchAll(/signingConfig = null/g)].length, 3);
  assert.doesNotMatch(build, /storePassword|keyPassword|signingConfigs\.debug/);
  assert.match(read('android/gradle.properties'), /org.gradle.workers.max=1/);
  assert.match(read('android/build.gradle'), /minSdkVersion = 26/);
});

test('native launcher uses the real app name', () => {
  assert.match(read('android/app/src/main/res/values/strings.xml'), /<string name="app_name">Sign To Speak<\/string>/);
});

test('all builds disable React Native development-server support', () => {
  assert.match(read(java + 'MainApplication.kt'), /useDevSupport = false/);
});


function block(source, marker) {
  const start = source.indexOf(marker);
  assert.notEqual(start, -1, `Missing ${marker}`);
  const open = source.indexOf('{', start);
  let depth = 1;
  for (let i = open + 1; i < source.length; i++) {
    if (source[i] === '{') depth++;
    if (source[i] === '}' && --depth === 0) return source.slice(open + 1, i);
  }
  assert.fail(`Unclosed ${marker}`);
}

function verifyUnsignedPhoneQaStructure(build, manifest) {
  assert.match(build, /providers\.gradleProperty\("unsignedPhoneQa"\)\.orElse\("false"\)\.get\(\)/);
  assert.match(build, /unsignedPhoneQaProperty == "true"/);
  assert.match(build, /\["false", "true"\]\.contains\(unsignedPhoneQaProperty\)/);
  assert.match(build, /unsignedPhoneQa && providers\.gradleProperty\("reactNativeArchitectures"\)\.orElse\(""\)\.get\(\) != "arm64-v8a"/);
  const optIn = block(build, 'if (unsignedPhoneQa)');
  const qa = block(optIn, 'qa {');
  for (const required of [
    /initWith release/, /matchingFallbacks = \["release"\]/,
    /applicationIdSuffix "\.qa"/, /versionNameSuffix "-phone-qa"/,
    /debuggable false/, /signingConfig = null/, /ndk \{ abiFilters "arm64-v8a" \}/,
  ]) assert.match(qa, required);
  assert.doesNotMatch(build, /System\.getenv|environmentVariable|System\.getProperty|storeFile|storePassword|keyAlias|keyPassword|keystore|keytool|signingConfigs\.debug|androidTestImplementation|testInstrumentationRunner/);
  assert.match(block(build, 'debug {'), /signingConfig = null/);
  const release = block(build, 'release {');
  assert.match(release, /signingConfig = null/);
  assert.doesNotMatch(release, /applicationIdSuffix|versionNameSuffix|testOnly/);
  assert.match(build, /debuggableVariants = \[\]/);
  assert.match(manifest, /<application android:testOnly="true"\s*\/>/);
  assert.doesNotMatch(manifest, /uses-permission|<permission|activity|receiver|provider|debuggable|allowBackup|usesCleartextTraffic|tools:replace/);
}

test('unsigned phone QA is an explicit arm64 opt-in with no signing or production changes', () => {
  verifyUnsignedPhoneQaStructure(read('android/app/build.gradle'), read('android/app/src/qa/AndroidManifest.xml'));
  assert.doesNotMatch(read('android/app/src/main/AndroidManifest.xml'), /testOnly/);
});

test('unsigned phone QA structural guard rejects identity, signing, bundling and manifest mutations', () => {
  const build = read('android/app/build.gradle');
  const manifest = read('android/app/src/qa/AndroidManifest.xml');
  const mutations = [
    ['implicit opt-in', build.replace('unsignedPhoneQaProperty == "true"', 'true'), manifest],
    ['production identity', build.replace('applicationIdSuffix ".qa"', 'applicationIdSuffix ""'), manifest],
    ['missing version suffix', build.replace('versionNameSuffix "-phone-qa"', ''), manifest],
    ['debuggable QA', build.replace('debuggable false', 'debuggable true'), manifest],
    ['debug inheritance', build.replace('initWith release', 'initWith debug'), manifest],
    ['extra architecture', build.replace('ndk { abiFilters "arm64-v8a" }', 'ndk { abiFilters "arm64-v8a", "x86_64" }'), manifest],
    ['inherited signer', build.replace(/debuggable false\r?\n\s*signingConfig = null/, 'debuggable false'), manifest],
    ['Metro dependency', build.replace('debuggableVariants = []', 'debuggableVariants = ["qa"]'), manifest],
    ['secret environment read', build + '\nSystem.getenv("SIGNING_SECRET")', manifest],
    ['non-test APK', build, manifest.replace('testOnly="true"', 'testOnly="false"')],
    ['QA network permission', build, manifest.replace('</manifest>', '<uses-permission android:name="android.permission.INTERNET" /></manifest>')],
  ];
  for (const [label, mutatedBuild, mutatedManifest] of mutations) {
    assert.ok(mutatedBuild !== build || mutatedManifest !== manifest, `Mutation had no effect: ${label}`);
    assert.throws(() => verifyUnsignedPhoneQaStructure(mutatedBuild, mutatedManifest), {name: 'AssertionError'}, label);
  }
});

test('merged permission guards bind production and QA to separate exact application identities', () => {
  const build = read('android/app/build.gradle');
  assert.match(block(build, 'static void verify(org.w3c.dom.Document document)'), /verifyForApplication\(document, APPLICATION_ID\)/);
  assert.match(block(build, 'static void verifyQa(org.w3c.dom.Document document)'), /verifyForApplication\(document, APPLICATION_ID \+ "\.qa"\)/);
  assert.match(build, /private static void verifyForApplication/);
  assert.match(build, /def receiverPermission = applicationId \+ "\.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION"/);
  assert.match(build, /phoneQa\.set\(variant\.buildType == "qa"\)/);
  assert.match(build, /if \(phoneQa\.get\(\)\) MergedPermissionPolicy\.verifyQa\(document\)/);
  assert.match(build, /else MergedPermissionPolicy\.verify\(document\)/);
  for (const fixture of ['crossVariantIdentity', 'productionReceiverInQa', 'normalProtection', 'dangerousProtection', 'widenedProtection', 'boundedInternalUse', 'duplicateUse', 'extraDeclaration']) {
    assert.match(build, new RegExp(`${fixture}:`));
  }
});

test('unsigned QA isolation checks actual merged output and gates packaging', () => {
  const build = read('android/app/build.gradle');
  const guard = block(build, 'tasks.register("verifyUnsignedPhoneQaIsolation")');
  for (const required of [
    /qa\.signingConfig != null/, /qa\.debuggable/,
    /qa\.applicationIdSuffix != "\.qa"/, /qa\.versionNameSuffix != "-phone-qa"/,
    /qa\.ndk\.abiFilters\.toSet\(\) != \["arm64-v8a"\]\.toSet\(\)/,
    /reactDebuggableVariants\.get\(\)\.contains\(variant\.name\)/,
    /variant\.applicationId\.get\(\) != expectedApp/,
    /android\.defaultConfig\.applicationId != MergedPermissionPolicy\.APPLICATION_ID/,
    /android\.buildTypes\.debug\.signingConfig != null/,
    /android\.buildTypes\.release\.signingConfig != null/,
    /getAttributeNS\(MergedPermissionPolicy\.ANDROID_NS, "testOnly"\) != "true"/,
    /getAttributeNS\(MergedPermissionPolicy\.ANDROID_NS, "debuggable"\)/,
  ]) assert.match(guard, required);
  assert.match(build, /it\.name == "packageQa" \|\| it\.name == "assembleQa"/);
  assert.match(build, /dependsOn\(isolationTask\)/);
});


test('QA bundling guard captures the typed project React provider outside task closures', () => {
  const build = read('android/app/build.gradle');
  const capture = 'def reactDebuggableVariants = project.extensions.getByType(ReactExtension).getDebuggableVariants()';
  const verifyCapture = source => {
    assert.match(source, /^import com\.facebook\.react\.ReactExtension$/m);
    assert.match(source, /^def reactDebuggableVariants = project\.extensions\.getByType\(ReactExtension\)\.getDebuggableVariants\(\)$/m);
    assert.ok(source.indexOf(capture) < source.indexOf('androidComponents {'));
    const guard = block(source, 'tasks.register("verifyUnsignedPhoneQaIsolation")');
    assert.match(guard, /reactDebuggableVariants\.get\(\)\.contains\(variant\.name\)/);
    assert.doesNotMatch(guard, /\breact\.|\bproject\.extensions/);
  };
  verifyCapture(build);
  for (const mutation of [
    build.replace('reactDebuggableVariants.get()', 'react.debuggableVariants.get()'),
    build.replace(capture, capture + '.get()'),
    build.replace('project.extensions.getByType(ReactExtension)', 'react'),
  ]) assert.throws(() => verifyCapture(mutation), {name: 'AssertionError'});
});
