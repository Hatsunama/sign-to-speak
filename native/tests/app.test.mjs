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
async function renderHome(t, privacy = {ready: true}) {
  const stateListeners = new Set();
  const backListeners = new Set();
  const permissions = [];
  const starts = [];
  let homeRenders = 0;
  const native = {
    NativeModules: privacy === null ? {} : {CapturePrivacy: privacy},
    AppState: {currentState: 'active', addEventListener(_, fn) {stateListeners.add(fn); return {remove: () => stateListeners.delete(fn)};}},
    BackHandler: {addEventListener(_, fn) {backListeners.add(fn); return {remove: () => backListeners.delete(fn)};}},
    PermissionsAndroid: {
      PERMISSIONS: {CAMERA: 'camera'},
      RESULTS: {GRANTED: 'granted', DENIED: 'denied', NEVER_ASK_AGAIN: 'never_ask_again'},
      request: permission => new Promise((resolve, reject) => {permissions.push({permission, resolve, reject});}),
    },
    Pressable: 'Pressable', ScrollView: 'ScrollView', Text: 'Text', View: 'View',
    requireNativeComponent: name => name,
    StyleSheet: {create: styles => styles, absoluteFill: {}},
    useWindowDimensions: () => { homeRenders++; return {width: 1000, height: 600}; },
  };
  const exports = {};
  vm.runInNewContext(compiled, {exports, require(name) {
    if (name === 'react-native') return native;
    if (name === 'react-native-safe-area-context') return {SafeAreaProvider: 'SafeAreaProvider', SafeAreaView: 'SafeAreaView'};
    if (name === './src/session.mjs') return {createSession(options) {
      const session = createSession(options);
      return {...session, start(options) {starts.push(options); return session.start(options);}};
    }};
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
  const notice = () => app.root.findAllByType('Text').find(node => node.props.accessibilityLiveRegion === 'polite').props.children;
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
  return {app, native, homeRenders: () => homeRenders, stateListeners, backListeners, permissions, starts, text, button, press, cameras, notice, grant, resolve, reject, state, back, status};
}

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
