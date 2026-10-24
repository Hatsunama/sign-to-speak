import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root = new URL('../', import.meta.url);
const read = p => fs.readFileSync(new URL(p, root), 'utf8');
const main = 'android/app/src/main/';
const code = main + 'java/com/xmilo_at_your_side/sign_to_speak/';

test('playback capture is denied independently of secure-window video', () => {
  assert.match(read(main + 'AndroidManifest.xml'), /android:allowAudioPlaybackCapture="false"/);
  const app = read(code + 'MainApplication.kt');
  assert.match(app, /Build\.VERSION\.SDK_INT < Build\.VERSION_CODES\.Q/);
  assert.match(app, /setAllowedCapturePolicy\(AudioAttributes\.ALLOW_CAPTURE_BY_NONE\)/);
  assert.match(app, /allowedCapturePolicy == AudioAttributes\.ALLOW_CAPTURE_BY_NONE/);
  assert.ok(app.indexOf('setAllowedCapturePolicy(') < app.indexOf('loadReactNative(this)'));
  assert.match(app, /catch \(_: RuntimeException\) \{[\s\S]*?false/);
  assert.match(app, /@Volatile var capturePrivacyReady: Boolean = false/);
  assert.match(read(code + 'PrivateCameraView.kt'), /cameraActivity\?\.capturePrivacyReady != true/);
});

test('launcher is static and secure flag precedes React content', () => {
  const activity = read(code + 'MainActivity.kt');
  assert.ok(activity.indexOf('FLAG_SECURE') < activity.indexOf('super.onCreate(savedInstanceState)'));
  const styles = read(main + 'res/values/styles.xml');
  assert.match(styles, /<item name="android:windowBackground">#0A0B0E<\/item>/);
  assert.doesNotMatch(activity, /clearFlags\(/);
  const manifest = read(main + 'AndroidManifest.xml');
  assert.deepEqual([...manifest.matchAll(/<activity\b[^>]*android:name="([^"]+)"/g)].map(m => m[1]), ['.MainActivity']);
});

function files(dir) {
  return fs.readdirSync(dir, {withFileTypes: true}).flatMap(entry => {
    const full = path.join(dir, entry.name);
    return entry.isDirectory() ? files(full) : [full];
  });
}

test('only the reviewed app-owned protected PCM sink is permitted', () => {
  // Source scanning is an alarm; the protected sink also has Android policy tests.
  const sources = [fileURLToPath(new URL('App.tsx', root)), ...files(fileURLToPath(new URL('src', root))),
    ...files(fileURLToPath(new URL(main + 'java', root)))].filter(p => /\.(kt|java|[cm]?js|tsx?)$/.test(p));
  for (const file of sources) {
    const source = fs.readFileSync(file, 'utf8').replace(/\/\*[\s\S]*?\*\/|\/\/[^\n]*/g, '');
    if (path.basename(file) === 'ProtectedPcmPlayer.java') {
      assert.match(source, /setAllowedCapturePolicy\(AudioAttributes\.ALLOW_CAPTURE_BY_NONE\)/);
      assert.match(source, /getAllowedCapturePolicy\(\) != AudioAttributes\.ALLOW_CAPTURE_BY_NONE/);
      assert.match(source, /Build\.VERSION\.SDK_INT < 29/);
    } else {
      assert.doesNotMatch(source,
        /\b(TextToSpeech|AudioTrack|MediaPlayer|SoundPool|ToneGenerator|ExoPlayer|AAudio|OpenSLES|WebView|MediaCodec|SpeechSynthesisUtterance)\b|speechSynthesis|createAudioTrack|createMediaPlayer|\.playSoundEffect\s*\(/,
        `Audio output needs explicit privacy review: ${file}`);
    }
    assert.doesNotMatch(source, /\b(AlertDialog|ComponentDialog|Dialog|PopupWindow|Modal)\b|Alert\.alert/,
      `Separate window needs explicit secure-window review: ${file}`);
  }
});


test('all merged variants enforce explicit audio opt-out with adversarial fixtures', () => {
  const build = read('android/app/build.gradle');
  assert.match(build, /applications\.length != 1/);
  assert.match(build, /getAttributeNS\(ANDROID_NS, "allowAudioPlaybackCapture"\) != "false"/);
  for (const fixture of ['missingCaptureOptOut', 'enabledCapture', 'wrongCaptureNamespace', 'missingApplication', 'duplicateApplication']) {
    assert.match(build, new RegExp(`${fixture}:`));
  }
  assert.match(build, /onVariants\(selector\(\)\.all\(\)\)/);
});


test('camera lifecycle rejection precedes registration and privacy eligibility', () => {
  const source = read(code + 'PrivateCameraView.kt');
  const active = source.slice(source.indexOf('fun setActive(active: Boolean)'), source.indexOf('private fun start()'));
  const inactive = active.indexOf('if (!active)');
  const retired = active.indexOf('if (disposed || requested) return');
  const register = active.indexOf('registerWithActivity()');
  const privacy = active.indexOf('cameraActivity?.capturePrivacyReady != true');
  assert.ok(inactive >= 0 && retired > inactive && register > retired && privacy > register,
    'Late active props must not re-register or emit events from a disposed view');
  assert.equal([...active.matchAll(/registerWithActivity\(\)/g)].length, 1);
});
