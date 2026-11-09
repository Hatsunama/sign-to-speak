import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const source = name => readFileSync(new URL(`../android/app/src/${name}`, import.meta.url), 'utf8');
const phone = source('androidTestQa/java/com/xmilo_at_your_side/sign_to_speak/PhonePreviewIntegrationTest.java');
const emulator = source('androidTestEmulatorQa/java/com/xmilo_at_your_side/sign_to_speak/EmulatorPrivacyLifecycleTest.java');

test('physical preview checks accept bounded settled tracking notices, never starting or arbitrary text', () => {
  const literal = phone.match(/LIVE_NOTICE = Pattern\.compile\(("[^\n]+")\);/);
  assert.ok(literal, 'The exact Java notice pattern must remain inspectable');
  const pattern = new RegExp(JSON.parse(literal[1]));
  for (const value of [
    'Live preview only. Recognition: unsupported.',
    'Local hand tracking is unavailable. Live preview only. Recognition: unsupported.',
    'Local hand tracking active. Recognition: unsupported.',
    'Local hand tracking: 0 hands detected. Recognition: unsupported.',
    'Local hand tracking: 1 hand detected. Recognition: unsupported.',
    'Local hand tracking: 2 hands detected. Recognition: unsupported.',
  ]) assert.equal(pattern.test(value), true, value);
  for (const value of [
    'Starting local hand tracking. Recognition: unsupported.',
    'Local hand tracking: 3 hands detected. Recognition: unsupported.',
    'Local hand tracking: -1 hands detected. Recognition: unsupported.',
    'Local hand tracking: 1 hands detected. Recognition: unsupported.',
    'Recognition: unsupported.',
    'Local hand tracking active. Recognition: supported.',
  ]) assert.equal(pattern.test(value), false, value);
  assert.doesNotMatch(phone, /findLabel\(LIVE\)/);
  assert.match(phone, /if \(!state\.streaming\(\)\) return false/);
  assert.match(phone, /findLiveNotice\(\);\s*assertStreaming\("After scrolling to live notice"/);
  assert.match(phone, /findLiveNotice\(\);\s*assertStreaming\("After scrolling to switched-camera notice"/);
  assert.match(phone, /stream == PreviewView\.StreamState\.STREAMING && resumed/);
});

test('x86_64 emulator requires honest detector unavailability after real streaming checks', () => {
  assert.match(emulator, /"x86_64", Build\.SUPPORTED_ABIS\[0\]/);
  assert.match(emulator, /TRACKING_UNAVAILABLE =\s*"Local hand tracking is unavailable\. Live preview only\. Recognition: unsupported\."/);
  assert.doesNotMatch(emulator, /\.text\("Live preview only\. Recognition: unsupported\."\)|findUiLabel\("Live preview only\./);
  const start = emulator.indexOf('private CameraObservation awaitStreamingCamera()');
  const end = emulator.indexOf('private void assertStillStreaming(', start);
  const helper = emulator.slice(start, end);
  assert.match(helper, /lease\.isCurrent\(token\)/);
  assert.match(helper, /provider\.isBound\(preview\)/);
  assert.match(helper, /getPreviewStreamState\(\)\.getValue\(\) == PreviewView\.StreamState\.STREAMING/);
  assert.match(helper, /findUiLabel\(TRACKING_UNAVAILABLE\);\s*assertStillStreaming\(found\.get\(\)\);/);
  assert.match(emulator, /stage\("03a_x86_64_tracking_unavailable_no_detector_runtime_claim"\)/);
});
