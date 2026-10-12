import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import crypto from 'node:crypto';
const root = new URL('../', import.meta.url);
const read = p => fs.readFileSync(new URL(p, root), 'utf8');
const java = 'android/app/src/main/java/com/xmilo_at_your_side/sign_to_speak/';

test('manifest asks for camera only, disables backup and cleartext', () => {
  const manifest = read('android/app/src/main/AndroidManifest.xml');
  assert.deepEqual([...manifest.matchAll(/<uses-permission android:name="([^"]+)"/g)].map(m => m[1]), ['android.permission.CAMERA']);
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
  assert.equal([...build.matchAll(/signingConfig = null/g)].length, 2);
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
