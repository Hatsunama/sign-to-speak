import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

const owner = fs.readFileSync(new URL(
  '../android/app/src/main/java/com/xmilo_at_your_side/sign_to_speak/SpeechOwner.kt',
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

test('speech deadline invalidates its session before returning to the main queue', () => {
  const speak = body('@Synchronized fun speak(');
  const deadlineStart = speak.indexOf('val deadline = Runnable');
  assert.ok(deadlineStart >= 0, 'speech deadline is missing');
  const deadline = speak.slice(deadlineStart, speak.indexOf('deadlineHandler.postDelayed', deadlineStart));
  assert.match(deadline, /target\.stop\(\)/,
    'deadline must synchronously invalidate SpeechSession; its stop dispatches platform cancellation off-main');
  assert.ok(deadline.indexOf('target.stop()') < deadline.indexOf('done('),
    'deadline must invalidate the generation before notifying code that can re-enter the owner');
  assert.doesNotMatch(deadline, /requestStop\(/,
    'deadline must not queue an unscoped stop that can cancel a later generation');
});

test('lifecycle cancellation cannot be suppressed by a pending deadline stop', () => {
  const cancelAll = body('@Synchronized fun cancelAll(');
  assert.match(cancelAll, /session\?\.stop\(\)/,
    'every lifecycle cancellation must directly invalidate the current session generation');
  assert.doesNotMatch(cancelAll, /activeStop|compareAndSet/,
    'deadline bookkeeping must not suppress lifecycle invalidation');
  assert.doesNotMatch(owner, /speech-owner-stop|cancellationExecutor|private fun requestStop/,
    'SpeechSession alone owns deferred platform cancellation');
});
