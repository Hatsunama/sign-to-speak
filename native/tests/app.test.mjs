import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {createRequire} from 'node:module';
import React from 'react';
import TestRenderer, {act} from 'react-test-renderer';
import ts from 'typescript';
import {createSession} from '../src/session.mjs';

const require = createRequire(import.meta.url);
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const DENIED = 'Camera permission was not granted. You can use the home screen without it.';
const PAUSED_GRANT = 'Camera is off. Permission was granted; tap Start local preview to begin.';

const source = fs.readFileSync(new URL('../App.tsx', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions: {
  jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, esModuleInterop: true,
}}).outputText;

// Execute the actual App.tsx and session gate, mocking only React Native boundaries.

async function renderHome(t, privacy = {ready: true}, speechBridge, dimensions = {width: 1000, height: 600}) {
  const stateListeners = new Set();
  const backListeners = new Set();
  const permissions = [];
  const starts = [];
  let homeRenders = 0;
  const native = {
    NativeModules: privacy === null ? {} : {CapturePrivacy: privacy, LocalSpeech: speechBridge},
    AppState: {currentState: 'active', addEventListener(_, fn) {stateListeners.add(fn); return {remove: () => stateListeners.delete(fn)};}},
    BackHandler: {addEventListener(_, fn) {backListeners.add(fn); return {remove: () => backListeners.delete(fn)};}},
    PermissionsAndroid: {
      PERMISSIONS: {CAMERA: 'camera'},
      RESULTS: {GRANTED: 'granted', DENIED: 'denied', NEVER_ASK_AGAIN: 'never_ask_again'},
      request: permission => new Promise((resolve, reject) => {permissions.push({permission, resolve, reject});}),
    },
    Pressable: 'Pressable', ScrollView: 'ScrollView', Text: 'Text', TextInput: 'TextInput', View: 'View',
    requireNativeComponent: name => name,
    StyleSheet: {create: styles => styles, absoluteFill: {}},
    useWindowDimensions: () => { homeRenders++; return dimensions; },
  };
  const exports = {};
  vm.runInNewContext(compiled, {exports, require(name) {
    if (name === 'react-native') return native;
    if (name === 'react-native-safe-area-context') return {SafeAreaProvider: 'SafeAreaProvider', SafeAreaView: 'SafeAreaView'};
    if (name === './src/session.mjs') return {createSession(options) {
      const session = createSession(options);
      return {...session, start(options) {starts.push(options); return session.start(options);}};
    }};
    if (name === './src/local-speech.mjs') return require('../src/local-speech.mjs');
    return require(name);
  }});
  let app;
  await act(() => { app = TestRenderer.create(React.createElement(exports.default)); });
  t.after(async () => {await act(() => app.unmount());});
  const text = () => JSON.stringify(app.toJSON());
  const button = label => app.root.findAllByType('Pressable').find(node => node.findAllByType('Text').some(text => text.props.children === label));
  const press = async label => {
    const target = button(label);
    assert.ok(target, `button exists: ${label}`);
    assert.equal(target.props.disabled, false, `button is enabled: ${label}`);
    await act(() => {target.props.onPress();});
  };
  const cameras = () => app.root.findAllByType('PrivateCameraPreview');
  const input = () => app.root.findAllByType('TextInput').find(node => node.props.testID === 'typed-speech-input');
  const edit = async value => { assert.ok(input()); await act(() => input().props.onChangeText(value)); };
  const speechStatus = () => app.root.findAllByType('Text').find(node => node.props.testID === 'typed-speech-status')?.props.children;
  const notice = () => app.root.findAllByType('Text').find(node => node.props.accessibilityLiveRegion === 'polite' && !node.props.testID).props.children;
  const grant = value => permissions.at(-1).resolve(value);
  const resolve = async (value, index = permissions.length - 1) => {await act(() => {permissions[index].resolve(value);});};
  const reject = async (index = permissions.length - 1) => {await act(() => {permissions[index].reject(new Error('permission request failed'));});};
  const state = async value => {await act(() => {native.AppState.currentState = value; stateListeners.forEach(fn => fn(value));});};
  const back = async () => {
    const results = [];
    await act(() => {backListeners.forEach(fn => results.push(fn()));});
    return results;
  };
  const status = async (handler, value) => {await act(() => handler({nativeEvent: {status: value}}));};
  return {app, native, homeRenders: () => homeRenders, stateListeners, backListeners, permissions, starts, text, button, press, cameras, input, edit, speechStatus, notice, grant, resolve, reject, state, back, status};
}

test('mounted React Stop, navigation, Back, background and unmount cancel the real native token', async t => {
  const cancelled = [];
  let next = 0;
  const h = await renderHome(t, {ready: true}, {
    begin: async () => String(++next),
    cancel: token => cancelled.push(token),
  });
  async function start() {
    await h.edit('HELLO');
    await h.press('Speak typed text');
    await h.press('Start local preview');
    await h.resolve('granted');
  }
  await start();
  await h.press('Stop camera');
  await start();
  await h.press('Hatsu Is Here For You');
  await h.press('Back to Home');
  await start();
  assert.deepEqual(await h.back(), [true]);
  await start();
  await h.state('background');
  await h.state('active');
  await start();
  await act(() => h.app.unmount());
  assert.deepEqual(cancelled, ['1', '2', '3', '4', '5']);
});

test('actual React home handles permission cancellation, native stop, local thanks and Back', async t => {
  const {app, native, stateListeners, backListeners, text, button, press, cameras, grant, resolve} = await renderHome(t);
  assert.match(text(), /Translation is not available yet/);
  assert.equal(cameras().length, 0);
  assert.equal(app.root.findByType('HatsuBackground').props.active, true);
  await press('Start local preview');
  assert.equal(cameras().length, 0);
  await act(() => {
    native.AppState.currentState = 'background';
    stateListeners.forEach(fn => fn('background'));
    grant('granted');
  });
  assert.equal(cameras().length, 0, 'late permission never opens camera in background');
  assert.equal(app.root.findByType('HatsuBackground').props.active, false);
  await act(() => {native.AppState.currentState = 'active'; stateListeners.forEach(fn => fn('active'));});
  assert.equal(cameras().length, 0, 'foreground never restarts automatically');
  await press('Start local preview');
  await resolve('granted');
  assert.equal(cameras().length, 1);
  await act(() => cameras()[0].props.onStatus({nativeEvent: {status: 'preview_only'}}));
  assert.match(text(), /Recognition: unsupported/);
  await press('Hatsu Is Here For You');
  assert.equal(cameras().length, 0);
  assert.match(text(), /If you want to support Hatsu please subscribe to the Patreon @Hatsunama/);
  assert.ok(button('Back to Home'));
  await act(() => {for (const fn of backListeners) assert.equal(fn(), true);});
  assert.match(text(), /Start local preview/);
  await press('Start local preview');
  await resolve('granted');
  await act(() => {for (const fn of backListeners) assert.equal(fn(), true);});
  assert.equal(cameras().length, 0, 'Android Back stops preview before leaving');
  await press('Start local preview');
  await resolve('denied');
  assert.equal(cameras().length, 0);
  assert.match(text(), /Camera permission was not granted/);
  await press('Start local preview');
  await resolve('granted');
  await act(() => cameras()[0].props.onStatus({nativeEvent: {status: 'stopped'}}));
  assert.equal(cameras().length, 0, 'native pause reaches UI');
  await press('Start local preview');
  await resolve('granted');
  await act(() => cameras()[0].props.onStatus({nativeEvent: {status: 'camera_cleanup_failed'}}));
  assert.equal(cameras().length, 0, 'failed cleanup hides preview without claiming camera release');
  assert.match(text(), /Preview hidden/);
  assert.match(text(), /Fully close and reopen the app/);
  assert.equal(button('Start local preview').props.disabled, true);
  await act(() => {native.AppState.currentState = 'background'; stateListeners.forEach(fn => fn('background'));});
  await act(() => {native.AppState.currentState = 'active'; stateListeners.forEach(fn => fn('active'));});
  assert.match(text(), /Camera cleanup could not be confirmed/);
  assert.equal(button('Start local preview').props.disabled, true, 'resume cannot clear cleanup failure');
  await act(() => app.unmount());
  assert.equal(stateListeners.size, 0);
  assert.equal(backListeners.size, 0);
});

for (const result of ['denied', 'never_ask_again']) {
  for (const ordering of ['resume before result', 'result before resume', 'result before pause']) {
    test(`actual React retains ${result}: ${ordering}`, async t => {
      const h = await renderHome(t);
      await h.press('Start local preview');
      if (ordering === 'result before pause') await h.resolve(result);
      await h.state('background');
      assert.equal(h.cameras().length, 0);
      if (ordering === 'result before resume') await h.resolve(result);
      await h.state('active');
      if (ordering === 'resume before result') await h.resolve(result);
      assert.equal(h.notice(), DENIED);
      assert.equal(h.cameras().length, 0, 'a denial never mounts the camera');
      assert.equal(h.starts.length, 0);
      assert.ok(h.button('Start local preview'));
      await h.state('background');
      await h.state('active');
      assert.equal(h.notice(), DENIED, 'later lifecycle changes preserve the latest explanation');
    });
  }
}

for (const resumeFirst of [false, true]) {
  test(`actual React paused grant requires another Start (resume first: ${resumeFirst})`, async t => {
    const h = await renderHome(t);
    await h.press('Start local preview');
    await h.state('background');
    if (resumeFirst) await h.state('active');
    await h.resolve('granted');
    assert.equal(h.cameras().length, 0, 'even a resumed app cannot use an interrupted Start');
    assert.equal(h.starts.length, 0);
    assert.equal(h.notice(), PAUSED_GRANT);
    if (!resumeFirst) await h.state('active');
    assert.equal(h.cameras().length, 0, 'resume is never consent to restart');
    await h.press('Start local preview');
    await h.resolve('granted');
    assert.equal(h.cameras().length, 1, 'a fresh explicit Start can open the camera');
  });
}

for (const result of ['denied', 'never_ask_again', 'granted']) {
  test(`actual React batched inactive/resume and ${result} cannot restore capture eligibility`, async t => {
    const h = await renderHome(t);
    await h.press('Start local preview');
    await act(() => {
      h.native.AppState.currentState = 'inactive';
      h.stateListeners.forEach(fn => fn('inactive'));
      h.native.AppState.currentState = 'active';
      h.stateListeners.forEach(fn => fn('active'));
      h.permissions[0].resolve(result);
    });
    assert.equal(h.notice(), result === 'granted' ? PAUSED_GRANT : DENIED);
    assert.equal(h.cameras().length, 0);
    assert.equal(h.starts.length, 0);
    assert.equal(h.app.root.findByType('HatsuBackground').props.active, true);
  });
}

test('actual React permission error after pause stays specific and allows an explicit retry', async t => {
  const h = await renderHome(t);
  await h.press('Start local preview');
  await h.state('background');
  await h.state('active');
  await h.reject();
  assert.equal(h.notice(), 'Camera permission could not be requested.');
  await h.state('background');
  await h.state('active');
  assert.equal(h.notice(), 'Camera permission could not be requested.');
  await h.press('Start local preview');
  await h.resolve('granted');
  assert.equal(h.cameras().length, 1);
});

test('actual React background Start cannot request permission or start capture', async t => {
  const h = await renderHome(t);
  await h.state('background');
  await h.press('Start local preview');
  assert.equal(h.permissions.length, 0);
  assert.equal(h.starts.length, 0);
  await h.state('active');
  assert.equal(h.cameras().length, 0);
});

for (const action of ['Stop', 'Back', 'Back after pause', 'navigation', 'navigation after pause']) {
  for (const result of ['denied', 'granted', 'error']) {
    test(`actual React ${action} cancels a pending ${result} outcome`, async t => {
      const h = await renderHome(t);
      await h.press('Start local preview');
      if (action.endsWith('after pause')) {
        await h.state('background');
        await h.state('active');
      }
      if (action === 'Stop') await h.press('Stop camera');
      else if (action.startsWith('Back')) assert.deepEqual(await h.back(), [true]);
      else {
        await h.press('Hatsu Is Here For You');
        await h.press('Back to Home');
      }
      if (result === 'error') await h.reject();
      else await h.resolve(result);
      assert.equal(h.notice(), 'Camera is off.', 'cancelled outcomes cannot replace explicit cancellation');
      assert.equal(h.cameras().length, 0);
      assert.equal(h.starts.length, 0);
      assert.ok(h.button('Start local preview'));
    });
  }
}

for (const staleResult of ['denied', 'granted', 'error']) {
  test(`actual React newer Start owns its pending and denied state over old ${staleResult}`, async t => {
    const h = await renderHome(t);
    await h.press('Start local preview');
    await h.state('background');
    await h.state('active');
    await h.press('Start local preview');
    assert.equal(h.permissions.length, 2);
    if (staleResult === 'error') await h.reject(0);
    else await h.resolve(staleResult, 0);
    assert.ok(h.button('Stop camera'), 'old finally cannot clear the newer pending request');
    assert.equal(h.cameras().length, 0);
    await h.resolve('denied', 1);
    assert.equal(h.notice(), DENIED);
    assert.equal(h.cameras().length, 0);
  });
  test(`actual React latest denial survives older late ${staleResult}`, async t => {
    const h = await renderHome(t);
    await h.press('Start local preview');
    await h.state('background');
    await h.state('active');
    await h.press('Start local preview');
    await h.resolve('never_ask_again', 1);
    if (staleResult === 'error') await h.reject(0);
    else await h.resolve(staleResult, 0);
    assert.equal(h.notice(), DENIED);
    assert.equal(h.cameras().length, 0);
    assert.equal(h.starts.length, 0);
    assert.ok(h.button('Start local preview'));
  });
}

test('actual React ignores inactive preview status after denial and after a newer preview', async t => {
  const h = await renderHome(t);
  await h.press('Start local preview');
  await h.resolve('granted');
  const oldStatus = h.cameras()[0].props.onStatus;
  await h.state('background');
  assert.equal(h.notice(), 'Camera is off.');
  assert.equal(h.cameras().length, 0);
  await h.state('active');
  await h.press('Start local preview');
  await h.resolve('denied');
  for (const value of ['stopped', 'preview_only', 'front_camera_unavailable']) {
    await h.status(oldStatus, value);
    assert.equal(h.notice(), DENIED, `stale ${value} cannot replace denial`);
  }
  await h.press('Start local preview');
  await h.resolve('granted');
  for (const value of ['stopped', 'preview_only', 'front_camera_unavailable']) {
    await h.status(oldStatus, value);
    assert.equal(h.cameras().length, 1, `stale ${value} cannot stop a newer preview`);
    assert.equal(h.notice(), 'Starting local preview. Translation is unavailable.');
  }
  await h.status(h.cameras()[0].props.onStatus, 'preview_only');
  assert.equal(h.notice(), 'Live preview only. Recognition: unsupported.');
});

test('actual React stale native cleanup failure still fails closed over newer permission', async t => {
  const h = await renderHome(t);
  await h.press('Start local preview');
  await h.resolve('granted');
  const oldStatus = h.cameras()[0].props.onStatus;
  await h.state('background');
  await h.state('active');
  await h.press('Start local preview');
  await h.status(oldStatus, 'camera_cleanup_failed');
  await h.resolve('granted');
  assert.equal(h.cameras().length, 0);
  assert.match(h.notice(), /Camera cleanup could not be confirmed/);
  assert.equal(h.button('Start local preview').props.disabled, true);
  await h.status(oldStatus, 'stopped');
  await h.state('background');
  await h.state('active');
  assert.match(h.notice(), /Camera cleanup could not be confirmed/);
  assert.equal(h.button('Start local preview').props.disabled, true);
});

test('actual React stale native cleanup failure stops a newer live preview too', async t => {
  const h = await renderHome(t);
  await h.press('Start local preview');
  await h.resolve('granted');
  const oldStatus = h.cameras()[0].props.onStatus;
  await h.press('Stop camera');
  await h.press('Start local preview');
  await h.resolve('granted');
  assert.equal(h.cameras().length, 1);
  await h.status(oldStatus, 'camera_cleanup_failed');
  assert.equal(h.cameras().length, 0);
  assert.match(h.notice(), /Camera cleanup could not be confirmed/);
  assert.equal(h.button('Start local preview').props.disabled, true);
});

for (const result of ['denied', 'granted', 'error']) {
  test(`actual React unmount cancels pending ${result} after pause`, async t => {
    const h = await renderHome(t);
    await h.press('Start local preview');
    await h.state('background');
    await h.state('active');
    await act(() => h.app.unmount());
    if (result === 'error') await h.reject();
    else await h.resolve(result);
    assert.equal(h.starts.length, 0, 'unmount invalidates the request before it can start a session');
    assert.equal(h.stateListeners.size, 0);
    assert.equal(h.backListeners.size, 0);
  });
}

for (const [label, capability] of [
  ['missing', undefined], ['uninitialized', {}], ['failed', {ready: false}],
  ['invalid truthy', {ready: 'true'}],
  ['native error', {get ready() { throw new Error('native capability unavailable'); }}],
]) {
  test(`privacy ${label} blocks first render and lifecycle without permission requests`, async t => {
    const app = await renderHome(t, capability === undefined ? null : capability);
    assert.equal(app.homeRenders(), 0, "Private Home was never rendered, even transiently");
    assert.match(app.text(), /Privacy protection is unavailable/);
    assert.equal(app.app.root.findAllByType('Pressable').length, 0);
    assert.equal(app.cameras().length, 0);
    assert.equal(app.permissions.length, 0);
    assert.equal(app.starts.length, 0);
    assert.doesNotMatch(app.text(), /Your hands|Start local preview|Translation is not available yet/);
    await app.state('background');
    await app.state('active');
    await app.back();
    assert.equal(app.homeRenders(), 0, "Private Home was never rendered, even transiently");
    assert.match(app.text(), /Privacy protection is unavailable/);
    assert.equal(app.app.root.findAllByType('Pressable').length, 0);
    assert.equal(app.permissions.length, 0);
    assert.equal(app.starts.length, 0);
  });
}

test('typed speech speaks exact reviewed text without camera permission and retains it for retry', async t => {
  const calls = [];
  const h = await renderHome(t, {ready: true}, {
    begin: async () => {calls.push(['begin']); return 'token';},
    speak: async (token, value) => {calls.push(['speak', token, value]); return 'COMPLETED';},
    cancel: token => calls.push(['cancel', token]),
  });
  assert.equal(h.input().props.testID, 'typed-speech-input');
  await h.edit('Hello, world!');
  await h.press('Speak typed text');
  assert.deepEqual(calls.slice(0, 2), [['begin'], ['speak', 'token', 'Hello, world!']]);
  assert.equal(h.permissions.length, 0);
  assert.equal(h.cameras().length, 0);
  assert.match(h.speechStatus(), /spoken|finished|complete/i);
  assert.equal(h.input().props.value, 'Hello, world!');
  await h.press('Stop speech');
  assert.deepEqual(calls.at(-1), ['cancel', 'token']);
  assert.equal(h.input().props.value, 'Hello, world!');
  await h.press('Clear typed text');
  assert.equal(h.input().props.value, '');
});

test('typed speech rejects unsupported characters and length visibly without changing text', async t => {
  const calls = [];
  const h = await renderHome(t, {ready: true}, {begin: async () => {calls.push('begin'); return 't';}, speak: async () => calls.push('speak')});
  for (const value of ['', '   ', 'Hello\nworld', 'café', 'A'.repeat(257)]) {
    await h.edit(value);
    await h.press('Speak typed text');
    assert.equal(h.input().props.value, value);
    assert.match(h.speechStatus(), /printable|256|enter/i);
  }
  assert.deepEqual(calls, []);
});

test('double Speak in one React batch starts only one native utterance', async t => {
  const calls = [];
  let finish;
  const h = await renderHome(t, {ready: true}, {
    begin: async () => {calls.push('begin'); return 't';},
    speak: () => {calls.push('speak'); return new Promise(resolve => {finish = resolve;});},
    cancel: () => {},
  });
  await h.edit('HELLO');
  const button = h.button('Speak typed text');
  await act(() => {button.props.onPress(); button.props.onPress();});
  assert.deepEqual(calls, ['begin', 'speak']);
  await act(() => finish('COMPLETED'));
});

test('pending begin and speech results cannot revive stopped or cleared private draft', async t => {
  let resolveBegin;
  let resolveSpeak;
  const calls = [];
  const h = await renderHome(t, {ready: true}, {
    begin: () => new Promise(resolve => {resolveBegin = resolve;}),
    speak: (token, value) => {calls.push([token, value]); return new Promise(resolve => {resolveSpeak = resolve;});},
    cancel: token => calls.push(['cancel', token]),
  });
  await h.edit('PRIVATE');
  await h.press('Speak typed text');
  await h.press('Stop speech');
  await act(() => resolveBegin('late'));
  assert.deepEqual(calls, [['cancel', 'late']]);
  assert.equal(h.input().props.value, 'PRIVATE');
  assert.match(h.speechStatus(), /stopped/i);
  await h.press('Speak typed text');
  await act(() => resolveBegin('fresh'));
  assert.deepEqual(calls.at(-1), ['fresh', 'PRIVATE']);
  await h.state('background');
  assert.equal(h.input().props.value, '');
  await act(() => resolveSpeak('COMPLETED'));
  assert.equal(h.input().props.value, '');
  assert.doesNotMatch(h.speechStatus(), /complete|spoken|finished/i);
  await h.state('active');
  assert.equal(h.input().props.value, '');
});

test('Back and route navigation clear typed draft and cancel native speech', async t => {
  const cancelled = [];
  const h = await renderHome(t, {ready: true}, {begin: async () => 't', speak: () => new Promise(() => {}), cancel: token => cancelled.push(token)});
  await h.edit('PRIVATE');
  await h.press('Speak typed text');
  assert.deepEqual(await h.back(), [true]);
  assert.equal(h.input().props.value, '');
  assert.deepEqual(cancelled, ['t']);
  await h.edit('AGAIN');
  await h.press('Hatsu Is Here For You');
  await h.press('Back to Home');
  assert.equal(h.input().props.value, '');
});

test('native speech statuses are understandable and cleanup failure requires fresh app session', async t => {
  let result = 'BUSY';
  const h = await renderHome(t, {ready: true}, {begin: async () => 't', speak: async () => result, cancel: () => {}});
  await h.edit('HELLO');
  for (const [native, expected] of [
    ['BUSY', /busy|another/i], ['UNAVAILABLE', /unavailable/i],
    ['INVALID_INPUT', /input|text/i], ['FAILED', /could not|failed/i],
    ['CANCELLED', /cancel/i],
  ]) {
    result = native;
    await h.press('Speak typed text');
    assert.match(h.speechStatus(), expected);
  }
  result = 'CLEANUP_FAILED';
  await h.press('Speak typed text');
  assert.match(h.speechStatus(), /close and reopen/i);
  assert.equal(h.button('Speak typed text').props.disabled, true);
  await h.press('Stop speech');
  assert.match(h.speechStatus(), /close and reopen/i);
});

test('editing during playback cancels old result and only a fresh tap speaks edited text', async t => {
  let resolveSpeech;
  const calls = [];
  const h = await renderHome(t, {ready: true}, {
    begin: async () => 'token',
    speak: (token, text) => {calls.push(['speak', token, text]); return new Promise(resolve => {resolveSpeech = resolve;});},
    cancel: token => calls.push(['cancel', token]),
  });
  await h.edit('OLD');
  await h.press('Speak typed text');
  await h.edit('NEW');
  await act(() => resolveSpeech('COMPLETED'));
  assert.equal(h.input().props.value, 'NEW');
  assert.doesNotMatch(h.speechStatus(), /spoken|complete/i);
  assert.deepEqual(calls.slice(0, 2), [['speak', 'token', 'OLD'], ['cancel', 'token']]);
  await h.press('Speak typed text');
  assert.deepEqual(calls.at(-1), ['speak', 'token', 'NEW']);
  await h.press('Stop speech');
});

test('route change and unmount invalidate pending begin without speech or leaked draft', async t => {
  const pending = [];
  const calls = [];
  const h = await renderHome(t, {ready: true}, {
    begin: () => new Promise(resolve => pending.push(resolve)),
    speak: () => calls.push('speak'),
    cancel: token => calls.push(['cancel', token]),
  });
  await h.edit('SECRET');
  await h.press('Speak typed text');
  await h.press('Hatsu Is Here For You');
  await act(() => pending[0]('late-route'));
  await h.press('Back to Home');
  assert.equal(h.input().props.value, '');
  await h.edit('SECOND');
  await h.press('Speak typed text');
  await act(() => h.app.unmount());
  await act(() => pending[1]('late-unmount'));
  assert.deepEqual(calls, [['cancel', 'late-route'], ['cancel', 'late-unmount']]);
});

test('unavailable bridge and rejected native speech report retryable status without camera', async t => {
  const h = await renderHome(t, {ready: true}, {begin: async () => 't', speak: async () => {throw new Error('native failure');}, cancel: () => {}});
  await h.edit('HELLO');
  await h.press('Speak typed text');
  assert.match(h.speechStatus(), /could not|failed/i);
  assert.equal(h.button('Speak typed text').props.disabled, false);
  assert.equal(h.permissions.length, 0);
  const noBridge = await renderHome(t, {ready: true}, null);
  await noBridge.edit('HELLO');
  await noBridge.press('Speak typed text');
  assert.match(noBridge.speechStatus(), /unavailable/i);
  assert.equal(noBridge.button('Speak typed text').props.disabled, false);
});

test('begin cleanup rejection latches React speech warning and disables Speak', async t => {
  const h = await renderHome(t, {ready: true}, {
    begin: async () => {throw {code: 'CLEANUP_FAILED', message: 'Native admission cleanup failed'};},
    cancel() {},
  });
  await h.edit('HELLO');
  await h.press('Speak typed text');
  assert.match(h.speechStatus(), /close and reopen/i);
  assert.equal(h.button('Speak typed text').props.disabled, true);
  await h.press('Stop speech');
  assert.match(h.speechStatus(), /close and reopen/i);
});

test('late begin cleanup rejection latches over a newer speech attempt', async t => {
  const pending = [];
  const spoken = [];
  const h = await renderHome(t, {ready: true}, {
    begin: () => new Promise((resolve, reject) => pending.push({resolve, reject})),
    speak: async (token, text) => {spoken.push([token, text]); return 'COMPLETED';},
    cancel() {},
  });
  await h.edit('OLD');
  await h.press('Speak typed text');
  await h.press('Stop speech');
  await h.edit('NEW');
  await h.press('Speak typed text');
  await act(async () => {pending[1].resolve('new-token'); await Promise.resolve();});
  assert.deepEqual(spoken, [['new-token', 'NEW']]);
  await act(async () => {pending[0].reject({code: 'CLEANUP_FAILED', message: 'Native admission cleanup failed'}); await Promise.resolve();});
  assert.match(h.speechStatus(), /close and reopen/i);
  assert.equal(h.button('Speak typed text').props.disabled, true);
  assert.equal(h.input().props.value, 'NEW');
});

test('ordinary begin rejection remains retryable in React', async t => {
  let calls = 0;
  const h = await renderHome(t, {ready: true}, {
    begin: async () => ++calls === 1 ? Promise.reject(new Error('temporarily unavailable')) : 'recovered',
    speak: async () => 'COMPLETED',
    cancel() {},
  });
  await h.edit('HELLO');
  await h.press('Speak typed text');
  assert.match(h.speechStatus(), /unavailable/i);
  assert.equal(h.button('Speak typed text').props.disabled, false);
  await h.press('Speak typed text');
  assert.match(h.speechStatus(), /spoken/i);
});

test('retired cleanup failure latches across Stop and newer normal result', async t => {
  const pending = [];
  const h = await renderHome(t, {ready: true}, {
    begin: async () => `token-${pending.length + 1}`,
    speak: () => new Promise(resolve => pending.push(resolve)),
    cancel() {},
  });
  await h.edit('OLD');
  await h.press('Speak typed text');
  await h.press('Stop speech');
  await h.edit('NEW');
  await h.press('Speak typed text');
  assert.equal(pending.length, 2);
  await act(async () => {pending[0]('CLEANUP_FAILED'); await Promise.resolve();});
  assert.match(h.speechStatus(), /close and reopen/i);
  assert.equal(h.button('Speak typed text').props.disabled, true);
  await act(async () => {pending[1]('COMPLETED'); await Promise.resolve();});
  assert.match(h.speechStatus(), /close and reopen/i);
  await h.press('Stop speech');
  assert.match(h.speechStatus(), /close and reopen/i);
});

test('typed input prevents keyboard suggestions and wide layout preserves intro/camera row', async t => {
  const h = await renderHome(t, {ready: true}, null, {width: 1000, height: 600});
  assert.equal(h.input().props.autoComplete, 'off');
  assert.equal(h.input().props.importantForAutofill, 'no');
  assert.equal(h.input().props.autoCorrect, false);
  assert.equal(h.input().props.spellCheck, false);
  const headers = h.app.root.findAllByType('Text').filter(node => node.props.accessibilityRole === 'header' && ['Your hands. Your words.', 'Live camera', 'Speak typed text'].includes(node.props.children));
  assert.deepEqual(headers.map(node => node.props.children), ['Your hands. Your words.', 'Live camera', 'Speak typed text']);
  assert.equal(headers[0].parent.parent, headers[1].parent.parent);
  assert.notEqual(headers[1].parent.parent, headers[2].parent.parent);
  assert.doesNotMatch(h.text(), /\uFFFD/);
});
