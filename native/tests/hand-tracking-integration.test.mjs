import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
const source = name => readFileSync(new URL(`../android/app/${name}`, import.meta.url), 'utf8');
const probe = source('src/androidTestQa/java/com/xmilo_at_your_side/sign_to_speak/PhoneRecognitionProbeTest.kt');
const tracker = source('src/main/java/com/xmilo_at_your_side/sign_to_speak/RoboflowHandTracker.kt');

test('native recorded-video path consumes local tracking with per-clip cleanup', () => {
  assert.match(probe, /val handTracker = RoboflowHandTracker\(\)/);
  assert.match(probe, /val face = result\.faceLandmarks\(\)\s+val leftHand = result\.leftHandLandmarks\(\)\s+val pose = result\.poseLandmarks\(\)\s+val rightHand = result\.rightHandLandmarks\(\)\s+val tracked = consumeFrame\(features, handTracker, timestampMs, bitmap\.width, bitmap\.height,\s+face, leftHand, pose, rightHand\)/);
  assert.match(probe, /handTracker\.updateDiagnostics\(left, right, timestampMs\.toDouble\(\), width, height\)/);
  assert.match(probe, /handTracker\.close\(\)/);
  assert.match(probe, /var left: FloatArray\? = null\s+var right: FloatArray\? = null\s+val tracked = try \{\s+left = trackingHand\(leftHand\)\s+right = trackingHand\(rightHand\)/);
  assert.match(probe, /left\?\.fill\(0f\)/);
  assert.match(probe, /right\?\.fill\(0f\)/);
  assert.match(probe, /appendFeatures\(features, face, leftHand, pose, rightHand\)/);
  assert.match(probe, /appendLandmarks\(buffer, face, 468\)\s+appendLandmarks\(buffer, leftHand, 21\)\s+appendLandmarks\(buffer, pose, 33\)\s+appendLandmarks\(buffer, rightHand, 21\)/);
  assert.match(probe, /classifier_features_unchanged/);
  assert.doesNotMatch(probe, /put\("(?:tracking_landmarks|tracking_boxes|tracker_ids)"/);
});

test('Kotlin tracking has no network, Android, file or model dependency', () => {
  assert.doesNotMatch(tracker, /^import (?:android\.|java\.io|java\.net|kotlin\.io|com\.google\.)/m);
  assert.doesNotMatch(tracker, /println\(|Log\./);
  assert.match(tracker, /Kotlin adaptation/);
  assert.match(tracker, /class RoboflowHandTracker : AutoCloseable/);
  assert.match(tracker, /expectedGeneration == generation/);
});
