import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const native = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = relative => readFileSync(path.join(native, relative));

test('Android bundles retained application notices and omits retired Flite notices', () => {
  const build = read('android/app/build.gradle').toString();
  const mapping = read('android/app/license-assets.gradle').toString();
  assert.match(build, /apply from: "license-assets\.gradle"/);
  assert.match(mapping, /sign-to-speak\/LICENSE/);
  assert.match(mapping, /sign-to-speak\/NOTICE/);
  assert.match(mapping, /react-native-template-MIT\.txt/);
  assert.match(mapping, /selectedFiles\.from\(project\.fileTree\(licenseRoot\) \{ include\(licenseFiles\) \}\)/);
  assert.match(mapping, /includedPaths\.set\(licenseFiles\)/);
  assert.match(mapping, /variant\.sources\.assets\.addGeneratedSourceDirectory\(stageReviewedLicenses\)/);
  assert.doesNotMatch(mapping, /include\([^\n]*flite/);
});

test('Pocket model bundling is guarded by exact pinned hashes and Cargo notices', () => {
  const build = read('android/app/build.gradle').toString();
  const mapping = read('android/app/pocket-model-assets.gradle').toString();
  const manifest = JSON.parse(read('vendor/pocket-tts-audition/manifest.json'));
  assert.match(build, /apply from: "pocket-model-assets\.gradle"/);
  assert.match(mapping, /generated\/pocket-assets/);
  assert.match(mapping, /generated\/pocket-jniLibs/);
  assert.match(mapping, /merge.*Assets/);
  assert.match(mapping, /package/);
  for (const entry of manifest.model.assets) {
    assert.match(mapping, new RegExp(entry.sha256));
    assert.match(mapping, new RegExp(String(entry.size)));
  }
  assert.match(mapping, /cargo-declaration-routes\.json/);
  assert.match(mapping, /KYUTAI-MIT-LICENSE/);
});
