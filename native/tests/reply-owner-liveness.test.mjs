import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

const owner = fs.readFileSync(new URL(
  '../android/app/src/main/java/com/xmilo_at_your_side/sign_to_speak/ReplyOwner.kt',
  import.meta.url), 'utf8');

function body(signature) {
  const start = owner.indexOf(signature);
  assert.notEqual(start, -1, `missing ${signature}`);
  const open = owner.indexOf('{', start);
  let depth = 0;
  for (let index = open; index < owner.length; index += 1) {
    if (owner[index] === '{') depth += 1;
    if (owner[index] === '}' && --depth === 0) return owner.slice(start, index + 1);
  }
  assert.fail(`unterminated ${signature}`);
}

test('reply owner dispatches finish, cancellation and close away from lifecycle callers', () => {
  assert.match(owner, /private fun requestFinish\(/);
  assert.match(owner, /private fun requestCancellation\(/);
  assert.match(owner, /private fun requestClose\(/);
  assert.doesNotMatch(body('@Synchronized fun finish('), /session\?\.finish|target\.finish/);
  assert.doesNotMatch(body('@Synchronized fun cancelAll('), /session\?\.cancel|target\.cancel/);
  assert.doesNotMatch(body('@Synchronized fun close('), /session\?\.close|target\.close/);
});

test('finish watchdog is installed before native stop is dispatched', () => {
  const finish = body('@Synchronized fun finish(');
  const deadline = finish.indexOf('deadlineHandler.postDelayed');
  const dispatch = finish.indexOf('requestFinish(');
  assert.ok(deadline >= 0, 'finish watchdog is missing');
  assert.ok(dispatch > deadline, 'finish must arm its watchdog before dispatching native stop');
});

test('blocked finish cannot queue lifecycle cancellation behind the same worker', () => {
  assert.match(owner, /private val finishExecutor = ThreadPoolExecutor\(/);
  assert.match(owner, /private val cleanupExecutor = ThreadPoolExecutor\(/);
  assert.doesNotMatch(owner, /platformExecutor/);
  assert.match(body('private fun requestFinish('), /finishExecutor\.execute/);
  assert.match(body('private fun requestCancellation('), /cleanupExecutor\.execute/);
  assert.match(body('private fun requestClose('), /cleanupExecutor\.execute/);
});

test('logical cancellation precedes queued platform cleanup', () => {
  const cancellation = body('private fun requestCancellation(');
  const invalidate = cancellation.indexOf('target.invalidate(generation)');
  const dispatch = cancellation.indexOf('cleanupExecutor.execute');
  assert.ok(invalidate >= 0, 'cancellation must synchronously invalidate the generation');
  assert.ok(dispatch > invalidate, 'logical invalidation must precede platform cleanup dispatch');

  const close = body('@Synchronized fun close(');
  assert.ok(close.indexOf('target.invalidate(local)') < close.indexOf('requestClose(target, local)'),
    'close must invalidate the active generation before dispatching platform close');
});
