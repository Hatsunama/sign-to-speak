import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import test from 'node:test';
const native = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = p => readFileSync(path.join(native, p), 'utf8');

test('every app variant has an owned, verified Holistic asset producer', () => {
  const build = read('android/app/build.gradle');
  const source = read('android/app/offline-landmark-runtime.gradle');
  assert.match(build, /apply from: "offline-landmark-runtime\.gradle"/);
  assert.match(source, /onVariants\(selector\(\)\.all\(\)\)/);
  assert.match(source, /variant\.sources\.assets\.addGeneratedSourceDirectory\(stageHolisticAssets\)/);
  assert.match(source, /@OutputDirectory abstract DirectoryProperty getOutputDirectory/);
  assert.match(source, /prepare-holistic-assets\.py/);
  assert.match(source, /outputs\.upToDateWhen \{ false \}/);
  assert.match(source, /dependsOn\(verify\)/);
  assert.doesNotMatch(source, /abiFilters|pickFirst|tools:node/);
  assert.match(source, /keepDebugSymbols\.add\("\*\*\/libmediapipe_tasks_jni\.so"\)/);
});

test('offline source substitution covers app and instrumentation without telemetry exclusions', () => {
  const source = read('android/app/offline-landmark-runtime.gradle');
  assert.match(source, /configurations\.configureEach/);
  assert.match(source, /substitute\(module\("com\.google\.mediapipe:tasks-core"\)\)/);
  assert.match(source, /implementation\("com\.google\.mediapipe:tasks-vision:1\.1\.1"\)/);
  assert.match(source, /com\.google\.android\.datatransport/);
  assert.match(source, /selected_coordinates: coordinates/);
  assert.doesNotMatch(source, /exclude\s*\(?group|transitive\s*=\s*false/);
  const qa = read('android/app/phone-qa.gradle');
  assert.doesNotMatch(qa, /androidTestImplementation\("com\.google\.mediapipe:tasks-vision/);
  assert.match(qa, /androidTestImplementation\("com\.google\.ai\.edge\.litert:litert:1\.4\.2"\)/);
});

test('model and all SDK grant texts have explicit pinned app destinations', () => {
  const receipt = JSON.parse(read('vendor/mediapipe-offline/runtime-admission.json'));
  assert.equal(receipt.model.asset, 'holistic/holistic_landmarker.task');
  assert.equal(receipt.model.size, 13683609);
  assert.equal(receipt.model.sha256, 'e2dab61191e2dcd0a15f943d8e3ed1dce13c82dfa597b9dd39f562975a50c3f8');
  assert.equal(receipt.model.members.length, 7);
  assert.equal(receipt.runtime.usage_logging, false);
  assert.equal(receipt.runtime.jni.abi, 'arm64-v8a');
  for (const row of receipt.notices) {
    const bytes = readFileSync(path.join(native, 'vendor/mediapipe-offline', row.source));
    assert.equal(bytes.length, row.size);
    assert.equal(createHash('sha256').update(bytes).digest('hex'), row.sha256);
    assert.ok(row.asset.startsWith('holistic-licenses/'));
  }
  assert.equal(receipt.notices.length, 5);
});
