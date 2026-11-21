import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const root = new URL('../', import.meta.url);
const read = path => fs.readFileSync(new URL(path, root), 'utf8');

function section(source, heading, nextHeading) {
  const start = source.indexOf(heading);
  assert.notEqual(start, -1, `Missing ${heading}`);
  const end = nextHeading ? source.indexOf(nextHeading, start + heading.length) : source.length;
  assert.notEqual(end, -1, `Missing ${nextHeading}`);
  return source.slice(start, end);
}

test('current README describes the reply candidate without inheriting APK or phone verification', () => {
  const readme = read('README.md');
  const manifest = read('android/app/src/main/AndroidManifest.xml');
  const app = read('App.tsx');
  const nativePackage = read('android/app/src/main/java/com/xmilo_at_your_side/sign_to_speak/SignFoundationPackage.kt');

  assert.match(manifest, /android\.permission\.RECORD_AUDIO/);
  assert.match(app, /NativeModules\.LocalReply/);
  assert.match(app, /replyCaption/);
  assert.match(nativePackage, /LocalReplyModule\(context\)/);

  const currentSummary = readme.slice(0, readme.indexOf('## Live hand-tracking candidate'));
  assert.match(currentSummary, /candidate source/i);
  assert.match(currentSummary, /microphone/i);
  assert.match(currentSummary, /reply caption/i);
  assert.match(currentSummary, /not[^.]{0,80}(?:verified[^.]{0,80}(?:APK|phone|device|runtime)|(?:APK|phone|device|runtime)[^.]{0,80}(?:verified|evidence))/i);
  assert.doesNotMatch(currentSummary, /microphone captions remain absent/i);

  const privacy = section(readme, '## Privacy boundary', '## Reproduce host checks');
  assert.match(privacy, /microphone permission/i);
  assert.match(privacy, /Start listening/i);
  assert.match(privacy, /volatile/i);
  assert.match(privacy, /local reply/i);
  assert.match(privacy, /candidate source/i);
  assert.match(privacy, /not[^.]{0,80}(?:verified[^.]{0,80}(?:APK|phone|device|runtime)|(?:APK|phone|device|runtime)[^.]{0,80}(?:verified|evidence))/i);
  assert.doesNotMatch(privacy, /No microphone[^.]*permission is declared/i);
  assert.doesNotMatch(privacy, /reply model integration needs/i);
  assert.doesNotMatch(privacy, /\b(?:ships?|shipping|released|production-ready)\b/i);
});
