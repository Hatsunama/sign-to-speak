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
