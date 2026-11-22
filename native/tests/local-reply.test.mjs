import test from 'node:test';
import assert from 'node:assert/strict';
import {createLocalReply} from '../src/local-reply.mjs';

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

test('start admits one native token and exposes only lifecycle state', async () => {
  const reply = createLocalReply({begin: async () => 'reply:1', finish() {}, cancel() {}});
  assert.equal(reply.state(), 'idle');
  const starting = reply.start();
  assert.equal(reply.state(), 'starting');
  assert.equal(await starting, 'reply:1');
  assert.equal(reply.state(), 'listening');
});

test('stop revokes an unresolved begin and cancels its late token', async () => {
  const beginning = deferred(), cancelled = [];
  const reply = createLocalReply({begin: () => beginning.promise, cancel: token => cancelled.push(token)});
  const starting = reply.start();
  reply.stop();
  assert.equal(reply.state(), 'idle');
  beginning.resolve('late');
  assert.equal(await starting, null);
  assert.deepEqual(cancelled, ['late']);
});

test('failClosed marks native ownership uncertain before cancelling, while stop does not', async () => {
  const events = [];
  const reply = createLocalReply({
    begin: async () => 'reply:1',
    markUncertain: () => events.push('uncertain'),
    cancel: token => events.push(`cancel:${token}`),
  });
  await reply.start();
  reply.stop();
  assert.deepEqual(events, ['cancel:reply:1'], 'ordinary cancellation never latches a process fault');

  await reply.start();
  reply.failClosed();
  assert.deepEqual(events, ['cancel:reply:1', 'uncertain', 'cancel:reply:1']);
  assert.equal(reply.state(), 'idle');
});

test('a newer start retires the old token and stale begin cannot replace it', async () => {
  const first = deferred(), second = deferred(), cancelled = [];
  let calls = 0;
  const reply = createLocalReply({
    begin: () => ++calls === 1 ? first.promise : second.promise,
    cancel: token => cancelled.push(token),
  });
  const stale = reply.start();
  const fresh = reply.start();
  second.resolve('new');
  assert.equal(await fresh, 'new');
  first.resolve('old');
  assert.equal(await stale, null);
  assert.deepEqual(cancelled, ['old']);
  reply.stop();
  assert.deepEqual(cancelled, ['old', 'new']);
});

test('missing, malformed, throwing and rejecting begin fail closed and remain retryable', async () => {
  assert.equal(await createLocalReply(null).start(), null);
  let calls = 0;
  const reply = createLocalReply({
    begin: () => ++calls === 1 ? ''
      : calls === 2 ? {token: 'secret'}
        : calls === 3 ? (() => { throw Error('throw'); })()
          : calls === 4 ? Promise.reject(Error('reject')) : Promise.resolve('recovered'),
    cancel() {},
  });
  assert.equal(await reply.start(), null);
  assert.equal(await reply.start(), null);
  assert.equal(await reply.start(), null);
  assert.equal(await reply.start(), null);
  assert.equal(await reply.start(), 'recovered');
});

test('finish forwards the live token once and returns unreviewed caption text', async () => {
  const calls = [];
  const reply = createLocalReply({
    begin: async () => 'reply:1',
    finish: async token => { calls.push(token); return {status: 'COMPLETED', text: '  Hello there  '}; },
    cancel() {},
  });
  await reply.start();
  const finishing = reply.finish();
  assert.equal(reply.state(), 'finishing');
  assert.deepEqual(await finishing, {status: 'COMPLETED', text: '  Hello there  '});
  assert.equal(reply.state(), 'idle');
  assert.equal(await reply.finish(), null);
  assert.deepEqual(calls, ['reply:1']);
});

test('finish requires an eligible token and cannot be invoked twice concurrently', async () => {
  const ending = deferred(), calls = [];
  const reply = createLocalReply({
    begin: async () => 'reply:1',
    finish: token => { calls.push(token); return ending.promise; },
    cancel() {},
  });
  assert.equal(await reply.finish(), null);
  await reply.start();
  const first = reply.finish();
  assert.equal(await reply.finish(), null);
  ending.resolve({status: 'NO_SPEECH'});
  assert.deepEqual(await first, {status: 'NO_SPEECH'});
  assert.deepEqual(calls, ['reply:1']);
});

test('stop cancels pending finalization and suppresses its late output', async () => {
  const ending = deferred(), cancelled = [];
  const reply = createLocalReply({
    begin: async () => 'reply:1',
    finish: () => ending.promise,
    cancel: token => cancelled.push(token),
  });
  await reply.start();
  const finishing = reply.finish();
  reply.stop();
  ending.resolve({status: 'COMPLETED', text: 'must not escape'});
  assert.equal(await finishing, null);
  assert.deepEqual(cancelled, ['reply:1']);
});

test('restart during finalization cancels the old token and preserves the new session', async () => {
  const old = deferred(), cancelled = [];
  let next = 0;
  const reply = createLocalReply({
    begin: async () => `reply:${++next}`,
    finish: token => token === 'reply:1' ? old.promise : Promise.resolve({status: 'NO_SPEECH'}),
    cancel: token => cancelled.push(token),
  });
  await reply.start();
  const stale = reply.finish();
  assert.equal(await reply.start(), 'reply:2');
  old.resolve({status: 'COMPLETED', text: 'stale'});
  assert.equal(await stale, null);
  assert.equal(reply.state(), 'listening');
  assert.deepEqual(cancelled, ['reply:1']);
});

test('malformed completion and captionless completion fail safely without exposing text', async () => {
  const results = [
    null,
    'COMPLETED',
    {status: 'UNKNOWN', text: 'secret'},
    {status: 'COMPLETED'},
    {status: 'COMPLETED', text: ''},
    {status: 'COMPLETED', text: '   '},
    {status: 'NO_SPEECH', text: 'hallucination'},
  ];
  let token = 0;
  const reply = createLocalReply({
    begin: async () => `reply:${++token}`,
    finish: async () => results.shift(),
    cancel() {},
  });
  for (let index = 0; index < 7; index++) {
    await reply.start();
    assert.deepEqual(await reply.finish(), {status: 'FAILED'});
  }
});

test('known non-caption statuses are preserved without manufacturing text', async () => {
  const expectedStatuses = [
    'CANCELLED', 'NO_SPEECH', 'INVALID_INPUT', 'MODEL_UNAVAILABLE', 'DECODE_FAILED',
    'BUSY', 'UNAVAILABLE', 'FAILED', 'CLEANUP_FAILED',
  ];
  const statuses = [...expectedStatuses];
  let token = 0;
  const faults = [];
  const reply = createLocalReply({
    begin: async () => `reply:${++token}`,
    finish: async () => ({status: statuses.shift()}),
    cancel() {},
  }, status => faults.push(status));
  for (const expected of expectedStatuses) {
    await reply.start();
    assert.deepEqual(await reply.finish(), {status: expected});
  }
  assert.deepEqual(faults, ['CLEANUP_FAILED']);
});

test('missing or failed finish returns a safe status and closes the token', async () => {
  const missing = createLocalReply({begin: async () => 'reply:1', cancel() {}});
  await missing.start();
  assert.deepEqual(await missing.finish(), {status: 'UNAVAILABLE'});
  assert.equal(await missing.finish(), null);

  let calls = 0;
  const reply = createLocalReply({
    begin: async () => `reply:${calls + 1}`,
    finish: () => ++calls === 1 ? (() => { throw Error('throw'); })() : Promise.reject(Error('reject')),
    cancel() {},
  });
  await reply.start();
  assert.deepEqual(await reply.finish(), {status: 'FAILED'});
  await reply.start();
  assert.deepEqual(await reply.finish(), {status: 'FAILED'});
});

test('cleanup failures notify even when their request has been retired', async () => {
  const beginning = deferred(), ending = deferred(), faults = [];
  let calls = 0;
  const reply = createLocalReply({
    begin: () => ++calls === 1 ? beginning.promise : Promise.resolve(`reply:${calls}`),
    finish: () => ending.promise,
    cancel() {},
  }, status => faults.push(status));
  const staleBegin = reply.start();
  reply.stop();
  beginning.reject({code: 'CLEANUP_FAILED'});
  assert.equal(await staleBegin, null);
  await reply.start();
  const staleFinish = reply.finish();
  reply.stop();
  ending.resolve({status: 'CLEANUP_FAILED'});
  assert.equal(await staleFinish, null);
  assert.deepEqual(faults, ['CLEANUP_FAILED', 'CLEANUP_FAILED']);
});

test('ordinary stale rejection is discarded without changing the current state', async () => {
  const ending = deferred();
  let next = 0;
  const reply = createLocalReply({
    begin: async () => `reply:${++next}`,
    finish: () => ending.promise,
    cancel() {},
  });
  await reply.start();
  const stale = reply.finish();
  await reply.start();
  ending.reject(Error('cancelled'));
  assert.equal(await stale, null);
  assert.equal(reply.state(), 'listening');
});
