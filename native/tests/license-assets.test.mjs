import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const native = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = relative => readFileSync(path.join(native, relative));
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');

test('the reviewed Flite source receipt still matches all 171 vendored source bodies', () => {
  const manifestBytes = read('android/app/src/main/cpp/flite/source-manifest.json');
  assert.equal(sha256(manifestBytes), 'e0969bcb5a34628e66f93517e9cbdb092252988c5920007e18c46f595dc54cd8');
  const manifest = JSON.parse(manifestBytes);
  const receipt = JSON.parse(read('android/app/src/main/cpp/flite/source-receipt.json'));
  assert.equal(receipt.manifest_sha256, sha256(manifestBytes));
  assert.deepEqual(receipt.files.map(({ sha256: _hash, ...metadata }) => metadata), manifest.files);
  assert.equal(receipt.commit, 'e9e2e37c329dbe98bfeb27a1828ef9a71fa84f88');
  assert.equal(receipt.file_count, 171);
  assert.equal(receipt.files.length, 171);
  for (const file of receipt.files) {
    const body = read(`android/app/src/main/cpp/flite/upstream/${file.path}`);
    assert.equal(body.length, file.bytes, file.path);
    assert.equal(sha256(body), file.sha256, file.path);
    const gitBlob = createHash('sha1').update(`blob ${body.length}\0`).update(body).digest('hex');
    assert.equal(gitBlob, file.git_blob_sha1, file.path);
  }
});

test('Flite notices retain exact upstream COPYING and substantive local modification record', () => {
  const copying = read('android/app/src/main/cpp/flite/upstream/COPYING');
  const notices = read('licenses/flite/THIRD_PARTY_NOTICES.txt');
  const modifications = read('licenses/flite/MODIFICATIONS.txt');
  assert.ok(copying.length > 0);
  assert.ok(notices.includes(copying), 'full upstream COPYING must be present byte for byte');
  assert.ok(modifications.length > 0);
  assert.match(modifications.toString(), /modifications\.patch/);
});

test('Android app maps the existing licenses directory into APK assets', () => {
  const build = read('android/app/build.gradle').toString();
  const mapping = read('android/app/license-assets.gradle').toString();
  assert.match(build, /apply from: "license-assets\.gradle"/);
  assert.match(mapping, /assets\.srcDir\(file\("\.\.\/\.\.\/licenses"\)\)/);
  assert.match(mapping, /THIRD_PARTY_NOTICES\.txt/);
  assert.match(mapping, /MODIFICATIONS\.txt/);
});
