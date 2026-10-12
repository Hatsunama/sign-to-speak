import React, {useCallback, useEffect, useRef, useState} from 'react';
import {
  AppState, BackHandler, PermissionsAndroid, Pressable, requireNativeComponent,
  ScrollView, StyleSheet, Text, useWindowDimensions, View,
  type NativeSyntheticEvent, type ViewProps,
} from 'react-native';
import {SafeAreaProvider, SafeAreaView} from 'react-native-safe-area-context';
import {createSession} from './src/session.mjs';

const HatsuBackground = requireNativeComponent<ViewProps & {active: boolean}>('HatsuBackground');
const CameraPreview = requireNativeComponent<ViewProps & {
  active: boolean;
  onStatus: (event: NativeSyntheticEvent<{status: string}>) => void;
}>('PrivateCameraPreview');

function Button({children, onPress, disabled = false}: {
  children: string; onPress: () => void; disabled?: boolean;
}) {
  return <Pressable accessibilityRole="button" disabled={disabled} onPress={onPress}
    style={({pressed}) => [styles.button, (pressed || disabled) && styles.dimmed]}>
    <Text style={styles.buttonText}>{children}</Text>
  </Pressable>;
}

function Home() {
  const {width, height} = useWindowDimensions();
  const [route, setRoute] = useState<'home' | 'thanks'>('home');
  const [foreground, setForeground] = useState(AppState.currentState === 'active');
  const [preview, setPreview] = useState(false);
  const [requesting, setRequesting] = useState(false);
  const [notice, setNotice] = useState('Camera is off.');
  const request = useRef(0);
  const gate = useRef(createSession({
    stopCamera: () => setPreview(false),
    // No speech engine or app-owned camera buffers are present in this build.
    stopSpeech: () => {}, clearNativeBuffers: () => {},
  })).current;

  const stop = useCallback(() => {
    ++request.current;
    gate.stop();
    setRequesting(false);
    setNotice('Camera is off.');
  }, [gate]);

  useEffect(() => {
    const subscription = AppState.addEventListener('change', state => {
      setForeground(state === 'active');
      if (state !== 'active') stop();
    });
    return () => { subscription.remove(); ++request.current; gate.stop(); };
  }, [gate, stop]);

  useEffect(() => {
    const subscription = BackHandler.addEventListener('hardwareBackPress', () => {
      if (preview || requesting) { stop(); return true; }
      if (route === 'thanks') { setRoute('home'); return true; }
      return false;
    });
    return () => subscription.remove();
  }, [preview, requesting, route, stop]);

  async function startPreview() {
    if (requesting || preview || AppState.currentState !== 'active') return;
    const token = ++request.current;
    setRequesting(true);
    try {
      const granted = await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.CAMERA);
      if (token !== request.current || AppState.currentState !== 'active') return;
      if (granted !== PermissionsAndroid.RESULTS.GRANTED) {
        setNotice('Camera permission was not granted. You can use the home screen without it.');
        return;
      }
      if (gate.start({permission: true, foreground: true}) !== null) {
        setNotice('Starting local preview. Translation is unavailable.');
        setPreview(true);
      }
    } catch {
      if (token === request.current) setNotice('Camera permission could not be requested.');
    } finally {
      if (token === request.current) setRequesting(false);
    }
  }

  return <View style={styles.screen}>
    <HatsuBackground pointerEvents="none" active={foreground} style={StyleSheet.absoluteFill} />
    <SafeAreaView style={styles.safe}>
      <ScrollView contentContainerStyle={styles.scroll}>
        {route === 'thanks' ? <View style={styles.card}>
          <Text accessibilityRole="header" style={styles.title}>Thank you</Text>
          <Text style={styles.body}>Thanks for downloading Sign To Speak and spending time with it. Hope it makes your world a little brighter.</Text>
          <Text style={styles.body}>If you want to support Hatsu please subscribe to the Patreon @Hatsunama</Text>
          <Button onPress={() => setRoute('home')}>Back to Home</Button>
        </View> : <View style={[styles.columns, width > height && styles.landscape]}>
          <View style={styles.card}>
            <Text style={styles.eyebrow}>SIGN TO SPEAK · ANDROID FOUNDATION</Text>
            <Text accessibilityRole="header" style={styles.title}>Your hands. Your words.</Text>
            <Text style={styles.body}>A private, on-device signing companion in development.</Text>
            <View style={styles.warning}>
              <Text style={styles.warningTitle}>Translation is not available yet</Text>
              <Text style={styles.body}>This build can show a live camera preview. It does not recognize ASL or speak a translation.</Text>
            </View>
            <Text style={styles.body}>No recording. No saved signing sessions. Camera frames stay on this device.</Text>
            <Button onPress={() => { stop(); setRoute('thanks'); }}>Hatsu Is Here For You</Button>
          </View>
          <View style={styles.card}>
            <Text accessibilityRole="header" style={styles.sectionTitle}>Live camera</Text>
            {preview ? <CameraPreview active={preview} style={styles.preview}
              onStatus={({nativeEvent}) => {
                if (nativeEvent.status === 'preview_only') {
                  setNotice('Live preview only. Recognition: unsupported.');
                } else {
                  stop();
                  setNotice(nativeEvent.status === 'front_camera_unavailable'
                    ? 'A front camera is not available on this device.'
                    : 'Camera stopped. Tap Start to try again.');
                }
              }} /> : <View style={styles.previewOff}><Text style={styles.body}>Camera off</Text></View>}
            <Text accessibilityLiveRegion="polite" style={styles.body}>{notice}</Text>
            {preview || requesting
              ? <Button onPress={stop}>Stop camera</Button>
              : <Button onPress={startPreview}>Start local preview</Button>}
            <Text style={styles.small}>Leaving this screen or putting the app in the background stops the camera. Starting again is always your choice.</Text>
          </View>
        </View>}
      </ScrollView>
    </SafeAreaView>
  </View>;
}

export default function App() {
  return <SafeAreaProvider><Home /></SafeAreaProvider>;
}

const styles = StyleSheet.create({
  screen: {flex: 1, backgroundColor: '#0a0b0e'}, safe: {flex: 1},
  scroll: {flexGrow: 1, padding: 20, justifyContent: 'center'},
  columns: {gap: 18, width: '100%', maxWidth: 1120, alignSelf: 'center'},
  landscape: {flexDirection: 'row', alignItems: 'flex-start'},
  card: {flex: 1, gap: 18, padding: 24, backgroundColor: '#171321F2', borderRadius: 24,
    borderWidth: 1, borderColor: '#594474', minWidth: 0},
  eyebrow: {fontSize: 12, fontWeight: '700', color: '#DCC7FF', letterSpacing: 1},
  title: {fontSize: 36, fontWeight: '700', color: '#FAF6FF'},
  sectionTitle: {fontSize: 24, fontWeight: '700', color: '#FAF6FF'},
  body: {fontSize: 17, lineHeight: 25, color: '#E2DBEC'},
  small: {fontSize: 14, lineHeight: 21, color: '#C5BBD0'},
  warning: {gap: 8, padding: 16, backgroundColor: '#31233E', borderRadius: 14},
  warningTitle: {fontSize: 18, fontWeight: '700', color: '#F0DAFF'},
  button: {minHeight: 52, justifyContent: 'center', padding: 14, backgroundColor: '#D7B7FF', borderRadius: 14},
  buttonText: {fontSize: 17, fontWeight: '700', color: '#231331', textAlign: 'center'},
  dimmed: {opacity: 0.65}, preview: {height: 280, width: '100%', backgroundColor: '#08090C'},
  previewOff: {height: 160, alignItems: 'center', justifyContent: 'center', backgroundColor: '#08090C', borderRadius: 14},
});
