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

test('actual React home handles permission cancellation, native stop, local thanks and Back', async () => {
  const stateListeners = new Set();
  const backListeners = new Set();
  let grant;
  const native = {
    AppState: {currentState: 'active', addEventListener(_, fn) {stateListeners.add(fn); return {remove: () => stateListeners.delete(fn)};}},
    BackHandler: {addEventListener(_, fn) {backListeners.add(fn); return {remove: () => backListeners.delete(fn)};}},
    PermissionsAndroid: {PERMISSIONS: {CAMERA: 'camera'}, RESULTS: {GRANTED: 'granted'}, request: () => new Promise(resolve => {grant = resolve;})},
    Pressable: 'Pressable', ScrollView: 'ScrollView', Text: 'Text', View: 'View',
    requireNativeComponent: name => name,
    StyleSheet: {create: styles => styles, absoluteFill: {}},
    useWindowDimensions: () => ({width: 1000, height: 600}),
  };
  const source = fs.readFileSync(new URL('../App.tsx', import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, {compilerOptions: {jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, esModuleInterop: true}}).outputText;
  const exports = {};
  vm.runInNewContext(compiled, {exports, require(name) {
    if (name === 'react-native') return native;
    if (name === 'react-native-safe-area-context') return {SafeAreaProvider: 'SafeAreaProvider', SafeAreaView: 'SafeAreaView'};
    if (name === './src/session.mjs') return {createSession};
    return require(name);
  }});
  let app;
  await act(() => { app = TestRenderer.create(React.createElement(exports.default)); });
  const text = () => JSON.stringify(app.toJSON());
  const button = label => app.root.findAllByType('Pressable').find(node => node.findAllByType('Text').some(text => text.props.children === label));
  const press = async label => { await act(() => {button(label).props.onPress();}); };
  const cameras = () => app.root.findAllByType('PrivateCameraPreview');
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
  await act(() => grant('granted'));
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
  await act(() => grant('granted'));
  await act(() => {for (const fn of backListeners) assert.equal(fn(), true);});
  assert.equal(cameras().length, 0, 'Android Back stops preview before leaving');

  await press('Start local preview');
  await act(() => grant('denied'));
  assert.equal(cameras().length, 0);
  assert.match(text(), /Camera permission was not granted/);

  await press('Start local preview');
  await act(() => grant('granted'));
  await act(() => cameras()[0].props.onStatus({nativeEvent: {status: 'stopped'}}));
  assert.equal(cameras().length, 0, 'native pause reaches UI');
  await press('Start local preview');
  await act(() => grant('granted'));
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
