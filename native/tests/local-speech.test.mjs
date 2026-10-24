import test from 'node:test';
import assert from 'node:assert/strict';
import {createLocalSpeech} from '../src/local-speech.mjs';

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

test('stop revokes an unresolved begin and cancels its late token', async () => {
  const first = deferred();
  const cancelled = [];
  const speech = createLocalSpeech({begin: () => first.promise, cancel: token => cancelled.push(token)});
  const starting = speech.start();
  speech.stop();
  first.resolve('old');
  assert.equal(await starting, null);
  assert.deepEqual(cancelled, ['old']);
});

test('late old begin cannot replace or stop a newer token', async () => {
  const first = deferred(), second = deferred();
  const cancelled = [];
  let calls = 0;
  const speech = createLocalSpeech({begin: () => ++calls === 1 ? first.promise : second.promise,
    cancel: token => cancelled.push(token)});
  const old = speech.start();
  speech.stop();
  const fresh = speech.start();
  second.resolve('new');
  assert.equal(await fresh, 'new');
  first.resolve('old');
  assert.equal(await old, null);
  assert.deepEqual(cancelled, ['old']);
  speech.stop();
  assert.deepEqual(cancelled, ['old', 'new']);
});

test('missing or rejecting bridge fails closed and allows a later explicit retry', async () => {
  const missing = createLocalSpeech(null);
  assert.equal(await missing.start(), null);
  missing.stop();
  let calls = 0;
  const speech = createLocalSpeech({begin: () => ++calls === 1 ? Promise.reject(Error('unavailable')) : Promise.resolve('recovered'),
    cancel() {}});
  assert.equal(await speech.start(), null);
  assert.equal(await speech.start(), 'recovered');
});

test('begin cleanup rejection notifies even when no token was admitted', async () => {
  const faults = [];
  const speech = createLocalSpeech({
    begin: async () => {throw {code: 'CLEANUP_FAILED', message: 'Native admission cleanup failed'};},
    cancel() {},
  }, value => faults.push(value));
  assert.equal(await speech.start(), null);
  assert.deepEqual(faults, ['CLEANUP_FAILED']);
});

test('retired begin cleanup rejection notifies after stop and newer admission', async () => {
  const first = deferred(), second = deferred(), faults = [], cancelled = [];
  let calls = 0;
  const speech = createLocalSpeech({
    begin: () => ++calls === 1 ? first.promise : second.promise,
    cancel: token => cancelled.push(token),
  }, value => faults.push(value));
  const stale = speech.start();
  speech.stop();
  const fresh = speech.start();
  second.resolve('new');
  assert.equal(await fresh, 'new');
  first.reject({code: 'CLEANUP_FAILED', message: 'Native admission cleanup failed'});
  assert.equal(await stale, null);
  assert.deepEqual(faults, ['CLEANUP_FAILED']);
  assert.deepEqual(cancelled, []);
});

test('ordinary and unrelated begin rejections stay retryable and never signal cleanup', async () => {
  const faults = [];
  let calls = 0;
  const speech = createLocalSpeech({
    begin: () => ++calls === 1 ? Promise.reject({code: 'UNAVAILABLE'})
      : calls === 2 ? Promise.reject(new Error('CLEANUP_FAILED'))
        : Promise.resolve('recovered'),
    cancel() {},
  }, value => faults.push(value));
  assert.equal(await speech.start(), null);
  assert.equal(await speech.start(), null);
  assert.equal(await speech.start(), 'recovered');
  assert.deepEqual(faults, []);
});

test('speak forwards confirmed text and the live native token, preserving native status', async () => {
  const calls = [];
  const speech = createLocalSpeech({begin: async () => 'activity:1', speak: async (token, text) => {
    calls.push([token, text]);
    return 'COMPLETED';
  }, cancel() {}});
  assert.equal(await speech.start(), 'activity:1');
  assert.equal(await speech.speak('HELLO'), 'COMPLETED');
  assert.deepEqual(calls, [['activity:1', 'HELLO']]);
});

test('speak fails closed without a live token or for non-string input', async () => {
  const calls = [];
  const speech = createLocalSpeech({begin: async () => 'activity:1', speak: (...args) => {
    calls.push(args);
    return Promise.resolve('COMPLETED');
  }, cancel() {}});
  assert.equal(await speech.speak('BEFORE START'), null);
  await speech.start();
  assert.equal(await speech.speak(null), 'INVALID_INPUT');
  assert.equal(await speech.speak({toString: () => 'SECRET'}), 'INVALID_INPUT');
  speech.stop();
  assert.equal(await speech.speak('AFTER STOP'), null);
  assert.deepEqual(calls, []);
});

test('native input rejection and native failure statuses reach the caller', async () => {
  const calls = [];
  const speech = createLocalSpeech({begin: async () => 'activity:1', speak: async (token, text) => {
    calls.push([token, text]);
    return text === '' ? 'INVALID_INPUT' : 'FAILED';
  }, cancel() {}});
  await speech.start();
  assert.equal(await speech.speak(''), 'INVALID_INPUT');
  assert.equal(await speech.speak('HELLO'), 'FAILED');
  assert.deepEqual(calls, [['activity:1', ''], ['activity:1', 'HELLO']]);
});

test('missing, throwing and rejecting native speak fail closed without losing the session', async () => {
  const missing = createLocalSpeech({begin: async () => 'activity:1', cancel() {}});
  await missing.start();
  assert.equal(await missing.speak('HELLO'), 'UNAVAILABLE');
  let calls = 0;
  const speech = createLocalSpeech({begin: async () => 'activity:2', speak: () => {
    if (++calls === 1) throw Error('native throw');
    if (calls === 2) return Promise.reject(Error('native reject'));
    return Promise.resolve('COMPLETED');
  }, cancel() {}});
  await speech.start();
  assert.equal(await speech.speak('HELLO'), 'FAILED');
  assert.equal(await speech.speak('HELLO'), 'FAILED');
  assert.equal(await speech.speak('HELLO'), 'COMPLETED');
});

test('pending begin and cancelled playback cannot expose a valid result', async () => {
  const beginning = deferred(), playing = deferred();
  const calls = [], cancelled = [];
  const speech = createLocalSpeech({begin: () => beginning.promise, speak: (token, text) => {
    calls.push([token, text]);
    return playing.promise;
  }, cancel: token => cancelled.push(token)});
  const start = speech.start();
  assert.equal(await speech.speak('TOO EARLY'), null);
  beginning.resolve('activity:1');
  assert.equal(await start, 'activity:1');
  const result = speech.speak('CONFIRMED');
  speech.stop();
  playing.resolve('COMPLETED');
  assert.equal(await result, null);
  assert.deepEqual(calls, [['activity:1', 'CONFIRMED']]);
  assert.deepEqual(cancelled, ['activity:1']);
});

test('stale playback completion cannot claim success after restart', async () => {
  const old = deferred();
  const calls = [], cancelled = [];
  let next = 0;
  const speech = createLocalSpeech({begin: async () => `activity:${++next}`,
    speak: (token, text) => { calls.push([token, text]); return token === 'activity:1' ? old.promise : Promise.resolve('COMPLETED'); },
    cancel: token => cancelled.push(token)});
  await speech.start();
  const stale = speech.speak('FIRST');
  assert.equal(await speech.start(), 'activity:2');
  assert.equal(await speech.speak('SECOND'), 'COMPLETED');
  old.resolve('COMPLETED');
  assert.equal(await stale, null);
  assert.deepEqual(cancelled, ['activity:1']);
  assert.deepEqual(calls, [['activity:1', 'FIRST'], ['activity:2', 'SECOND']]);
});

test('an explicit restart recovers and does not replay previous text', async () => {
  const calls = [], cancelled = [];
  let next = 0;
  const speech = createLocalSpeech({begin: async () => `activity:${++next}`,
    speak: (token, text) => { calls.push([token, text]); return Promise.resolve('COMPLETED'); },
    cancel: token => cancelled.push(token)});
  await speech.start();
  assert.equal(await speech.speak('ONE TIME ONLY'), 'COMPLETED');
  speech.stop();
  assert.equal(await speech.speak('ONE TIME ONLY'), null);
  assert.equal(await speech.start(), 'activity:2');
  assert.deepEqual(calls, [['activity:1', 'ONE TIME ONLY']]);
  assert.equal(await speech.speak('NEW CONFIRMED TEXT'), 'COMPLETED');
  assert.deepEqual(calls, [['activity:1', 'ONE TIME ONLY'], ['activity:2', 'NEW CONFIRMED TEXT']]);
  assert.deepEqual(cancelled, ['activity:1']);
});


test('malformed native result cannot be mistaken for completed playback', async () => {
  const speech = createLocalSpeech({begin: async () => 'activity:1', speak: async () => 'unexpected', cancel() {}});
  await speech.start();
  assert.equal(await speech.speak('HELLO'), 'FAILED');
});

test('late native rejection after stop cannot claim a current failure', async () => {
  const playing = deferred();
  const speech = createLocalSpeech({begin: async () => 'activity:1', speak: () => playing.promise, cancel() {}});
  await speech.start();
  const result = speech.speak('HELLO');
  speech.stop();
  playing.reject(Error('cancelled by native lifecycle'));
  assert.equal(await result, null);
});



test('retired cleanup failure is notified while ordinary stale results stay discarded', async () => {
  const old = deferred(), fresh = deferred(), faults = [];
  let next = 0;
  const speech = createLocalSpeech({begin: async () => `t${++next}`,
    speak: token => token === 't1' ? old.promise : fresh.promise, cancel() {}},
    result => faults.push(result));
  await speech.start();
  const first = speech.speak('OLD');
  speech.stop();
  await speech.start();
  const second = speech.speak('NEW');
  old.resolve('CLEANUP_FAILED');
  assert.equal(await first, null);
  assert.deepEqual(faults, ['CLEANUP_FAILED']);
  fresh.resolve('COMPLETED');
  assert.equal(await second, 'COMPLETED');
});
