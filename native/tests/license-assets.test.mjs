import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { createHash } from 'node:crypto';

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

test('Android stages the four pinned React Native native-dependency notices', () => {
  const mapping = read('android/app/license-assets.gradle').toString();
  for (const name of [
    'double-conversion-LICENSE.txt',
    'glog-COPYING.txt',
    'fast_float-LICENSE-MIT.txt',
    'folly-LICENSE.txt',
  ]) {
    assert.match(mapping, new RegExp(`framework/${name.replaceAll('.', '\\.')}\\b`));
    assert.ok(read(`licenses/framework/${name}`).length > 0, `${name} must contain upstream text`);
  }
});

test('Android stages the resolved Maven grants with the reviewed upstream bytes', () => {
  const mapping = read('android/app/license-assets.gradle').toString();
  for (const [name, hash, grant] of [
    ['bolts-tasks-LICENSE.txt', '9e69750c616877af2dec86f961a53e39324c3655e25b1d6ceed06e9661ec072b', /BSD License[\s\S]*Copyright \(c\) 2013-present, Facebook, Inc\./],
    ['old-facebook-MIT.txt', '52412d7bc7ce4157ea628bbaacb8829e0a9cb3c58f57f99176126bc8cf2bfc85', /MIT License[\s\S]*Copyright \(c\) Facebook, Inc\. and its affiliates\./],
    ['camera-libyuv-LICENSE.txt', '2b2cc1180c7e6988328ad2033b04b80117419db9c4c584918bbb3cfec7e9364f', /Copyright 2011 The LibYuv Project Authors\.[\s\S]*Redistribution and use in source and binary forms/],
  ]) {
    assert.ok(mapping.includes(`framework/${name}`), `${name} must be staged`);
    const bytes = read(`licenses/framework/${name}`);
    assert.equal(createHash('sha256').update(bytes).digest('hex'), hash);
    assert.match(bytes.toString(), grant);
  }
  const provenance = read('licenses/framework/README.md').toString();
  for (const coordinate of [
    'com.parse.bolts:bolts-tasks:1.4.0',
    'com.facebook.infer.annotation:infer-annotation:0.18.0',
    'com.facebook.yoga:proguard-annotations:1.19.0',
    'androidx.camera:camera-core:1.4.2',
  ]) assert.ok(provenance.includes(coordinate), `${coordinate} must have a grant mapping`);
  assert.match(provenance, /hermes-v250829098\.0\.17/);
  assert.match(provenance, /\.hermesv1version/);
});

test('Android stages Hermes dtoa notices and the Fresco IJG terms and acknowledgment', () => {
  const mapping = read('android/app/license-assets.gradle').toString();
  for (const name of [
    'hermes-dtoa.c-NOTICE.txt',
    'hermes-g_fmt.c-NOTICE.txt',
    'fresco-ijg-README.txt',
    'fresco-ijg-ACKNOWLEDGMENT.txt',
  ]) {
    assert.match(mapping, new RegExp(`framework/${name.replaceAll('.', '\\.')}\\b`));
    assert.ok(read(`licenses/framework/${name}`).length > 0);
  }
  assert.match(read('licenses/framework/fresco-ijg-ACKNOWLEDGMENT.txt').toString(),
    /This software is based in part on the work of the Independent JPEG Group\./);
});

test('Hermes headers retain distinct full upstream notice bytes and IJG attribution is exact', () => {
  const digest = bytes => createHash('sha256').update(bytes).digest('hex');
  const sources = [
    ['hermes-dtoa.c-NOTICE.txt', '1991, 2000, 2001', '45a0b19676f406b6aaeb9ebc04961ba711d38b13003a15b38ff7174b2568b0c4'],
    ['hermes-g_fmt.c-NOTICE.txt', '1991, 1996', 'be5981b1b47d165581f00f9915679ecd19a4fa44ba259f5b60ee1fd1be1df31f'],
  ];
  for (const [name, years, hash] of sources) {
    const notice = read(`licenses/framework/${name}`);
    assert.equal(digest(notice), hash);
    assert.match(notice.toString(), new RegExp(`Copyright \\(c\\) ${years} by Lucent Technologies\\.`));
    assert.match(notice.toString(), /this entire notice\s+\* is included in all copies/);
    assert.match(notice.toString(), /THIS SOFTWARE IS BEING PROVIDED "AS IS"/);
  }
  assert.equal(digest(read('licenses/framework/fresco-ijg-README.txt')),
    '4b7b9f8c03bb8d60270dfd12684e70ab21e4abfd27e73905cd1a7c4cae6f5cdb');
  assert.equal(read('licenses/framework/fresco-ijg-ACKNOWLEDGMENT.txt').toString(),
    'This software is based in part on the work of the Independent JPEG Group.\n');
  const provenance = read('licenses/framework/README.md').toString();
  for (const sourceHash of [
    '081a79092e4c239dd51e7c0e759df4dd46926162131dc27f54db219ca26ed821',
    '0a1f2438e3ff2c270653a3fc51b0448570fd0ea2791590678ba54500fa349f16',
  ]) assert.ok(provenance.includes(sourceHash));
  assert.match(provenance, /\[0, 861\)/);
  assert.match(provenance, /\[0, 855\)/);
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
