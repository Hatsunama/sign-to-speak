import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdirSync, mkdtempSync, readFileSync, rmSync, unlinkSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname, join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';

const attributes = readFileSync(new URL('../../.gitattributes', import.meta.url));
const samples = {
  'Renderer.kt': 'package example\nclass Renderer { val value = 1 }\n',
  'verify.sh': '#!/usr/bin/env bash\nprintf "verified\\n"\n',
  'fixture.patch': 'diff --git a/old b/new\n--- a/old\n+++ b/new\n',
};
const componentFiles = [
  'src/weights.rs',
  'english_18m_2026-10.upstream.yaml',
  'Cargo.lock',
  'KYUTAI-MIT-LICENSE',
];
for (const name of componentFiles) {
  const path = `native/vendor/pocket-tts-runtime/${name}`;
  samples[path] = readFileSync(new URL(`../../${path}`, import.meta.url));
}
const supplementalNoticeFiles = [
  'sentencepiece-0.14.0/LICENSE-MIT',
  'sentencepiece-0.14.0/COPYRIGHT.md',
  'dasp_frame-0.11.0/LICENSE-MIT',
];
for (const name of supplementalNoticeFiles) {
  const path = `native/licenses/pocket-tts/supplemental/${name}`;
  samples[path] = readFileSync(new URL(`../../${path}`, import.meta.url));
}
const hash = bytes => createHash('sha256').update(bytes).digest('hex');

function checkoutWith(attributesContent) {
  const directory = mkdtempSync(join(tmpdir(), 'sign-line-endings-'));
  try {
    const git = (...args) => {
      const result = spawnSync('git', args, {cwd: directory, encoding: 'utf8'});
      assert.equal(result.status, 0, result.stderr || result.stdout);
    };
    git('init', '-q');
    writeFileSync(join(directory, '.gitattributes'), attributesContent);
    for (const [name, body] of Object.entries(samples)) {
      mkdirSync(dirname(join(directory, name)), {recursive: true});
      writeFileSync(join(directory, name), body);
    }
    git('-c', 'core.autocrlf=false', 'add', '--all');
    for (const name of Object.keys(samples)) unlinkSync(join(directory, name));
    git('-c', 'core.autocrlf=true', 'checkout-index', '--force', '--all');
    return Object.fromEntries(Object.keys(samples).map(name => [name, readFileSync(join(directory, name))]));
  } finally {
    rmSync(directory, {recursive: true, force: true});
  }
}

test('LF attributes preserve raw source and license bytes on autocrlf checkouts', () => {
  const broken = checkoutWith(Buffer.alloc(0));
  const actual = checkoutWith(process.env.SIGN_TEST_BROKEN_ATTRIBUTES === '1' ? Buffer.alloc(0) : attributes);
  for (const [name, body] of Object.entries(samples)) {
    const original = Buffer.from(body);
    assert.notEqual(hash(broken[name]), hash(original), `Broken rules should change ${name}`);
    assert.ok(broken[name].includes(Buffer.from('\r\n')), `Broken rules should produce CRLF in ${name}`);
    assert.equal(hash(actual[name]), hash(original), `Checkout changed raw bytes in ${name}`);
    assert.deepEqual(actual[name], original);
  }
});
