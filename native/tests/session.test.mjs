import test from 'node:test';
import assert from 'node:assert/strict';
import {createSession} from '../src/session.mjs';

function setup() {
  const calls = [];
  const session = createSession({stopCamera: () => calls.push('camera'), stopSpeech: () => calls.push('speech'), clearNativeBuffers: () => calls.push('buffers')});
  return {session, calls};
}

test('starts only after explicit permission and foreground confirmation', () => {
  const {session} = setup();
  assert.equal(session.start({permission: false, foreground: true}), null);
  assert.equal(session.start({permission: true, foreground: false}), null);
  assert.equal(session.state().phase, 'idle');
  assert.equal(typeof session.start({permission: true, foreground: true}), 'number');
  assert.equal(session.state().recognition, 'unsupported');
});

test('stop invalidates pending camera starts and results without retaining content', () => {
  const {session, calls} = setup();
  const token = session.start({permission: true, foreground: true});
  session.stop();
  assert.equal(session.isCurrent(token), false);
  assert.deepEqual(calls, ['camera', 'speech', 'buffers']);
  assert.deepEqual(session.state(), {phase: 'idle', recognition: 'unsupported'});
});

test('new session rejects old asynchronous callbacks', () => {
  const {session} = setup();
  const old = session.start({permission: true, foreground: true});
  session.stop();
  const current = session.start({permission: true, foreground: true});
  assert.equal(session.isCurrent(old), false);
  assert.equal(session.isCurrent(current), true);
});

test('repeated start does not resurrect or create parallel sessions', () => {
  const {session} = setup();
  const token = session.start({permission: true, foreground: true});
  assert.equal(session.start({permission: true, foreground: true}), token);
  session.stop(); session.stop();
  assert.equal(session.isCurrent(token), false);
});

test('one cleanup failure cannot skip other actions or allow restart', () => {
  const calls = [];
  const session = createSession({stopCamera() {throw new Error('camera');}, stopSpeech() {calls.push('speech');}, clearNativeBuffers() {calls.push('buffers');}});
  session.start({permission: true, foreground: true});
  assert.doesNotThrow(() => session.stop());
  assert.deepEqual(calls, ['speech', 'buffers']);
  assert.equal(session.state().phase, 'cleanup_failed');
  assert.equal(session.start({permission: true, foreground: true}), null);
});

test('foundation never publishes transcripts or recognized speech', () => {
  const {session} = setup();
  session.start({permission: true, foreground: true});
  assert.deepEqual(Object.keys(session).sort(), ['isCurrent', 'start', 'state', 'stop']);
  assert.equal(session.state().recognition, 'unsupported');
});
