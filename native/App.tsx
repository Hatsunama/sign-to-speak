import React, {useCallback, useEffect, useRef, useState} from 'react';
import {
  AppState, BackHandler, NativeModules, PermissionsAndroid, Pressable, requireNativeComponent,
  ScrollView, StyleSheet, Text, TextInput, useWindowDimensions, View,
  type NativeSyntheticEvent, type ViewProps,
} from 'react-native';
import {SafeAreaProvider, SafeAreaView} from 'react-native-safe-area-context';
import {createSession} from './src/session.mjs';
import {createLocalSpeech} from './src/local-speech.mjs';

const HatsuBackground = requireNativeComponent<ViewProps & {active: boolean}>('HatsuBackground');
const CameraPreview = requireNativeComponent<ViewProps & {
  active: boolean;
  onStatus: (event: NativeSyntheticEvent<{status: string}>) => void;
}>('PrivateCameraPreview');

function localSpeechBridge() {
  try { return NativeModules.LocalSpeech; } catch { return null; }
}

function Button({children, onPress, disabled = false, testID}: {
  children: string; onPress: () => void; disabled?: boolean; testID?: string;
}) {
  return <Pressable accessibilityRole="button" testID={testID} disabled={disabled} onPress={onPress}
    style={({pressed}) => [styles.button, (pressed || disabled) && styles.dimmed]}>
    <Text style={styles.buttonText}>{children}</Text>
  </Pressable>;
}

function Home() {
  const {width, height} = useWindowDimensions();
  const [route, setRoute] = useState<'home' | 'thanks'>('home');
  const [foreground, setForeground] = useState(AppState.currentState === 'active');
  const [preview, setPreview] = useState<number | null>(null);
  const [requesting, setRequesting] = useState(false);
  const [cleanupFailed, setCleanupFailed] = useState(false);
  const [notice, setNotice] = useState('Camera is off.');
  const [draft, setDraft] = useState('');
  const [speechStatus, setSpeechStatus] = useState('Type text, review it, then tap Speak typed text.');
  const [speechBusy, setSpeechBusy] = useState(false);
  const [speechFault, setSpeechFault] = useState(false);
  const speechFaultRef = useRef(false);
  const speechAttempt = useRef(false);
  const speechEpoch = useRef(0);
  const request = useRef<{canStart: boolean} | null>(null);
  const mounted = useRef(true);
  const speech = useRef(createLocalSpeech(localSpeechBridge(), () => {
    if (!mounted.current || speechFaultRef.current) return;
    speechFaultRef.current = true;
    speechEpoch.current++;
    speechAttempt.current = false;
    speech.stop();
    setSpeechBusy(false);
    setSpeechFault(true);
  })).current;
  const gate = useRef(createSession({
    stopCamera: () => setPreview(null),
    stopSpeech: () => speech.stop(), clearNativeBuffers: () => {},
  })).current;

  const stopSpeech = useCallback((clearDraft: boolean, message: string) => {
    speechEpoch.current++;
    speechAttempt.current = false;
    speech.stop();
    setSpeechBusy(false);
    if (clearDraft) setDraft('');
    setSpeechStatus(speechFaultRef.current
      ? 'Speech cleanup could not be confirmed. Fully close and reopen the app before speaking again.'
      : message);
  }, [speech]);

  const stop = useCallback(() => {
    request.current = null;
    gate.stop();
    stopSpeech(false, 'Speech stopped.');
    setRequesting(false);
    setNotice('Camera is off.');
  }, [gate, stopSpeech]);

  useEffect(() => {
    mounted.current = true;
    const subscription = AppState.addEventListener('change', state => {
      setForeground(state === 'active');
      if (state !== 'active') {
        // Every pause revokes capture eligibility, but the latest permission
        // outcome can still explain why the camera is off when it resolves.
        if (request.current) request.current.canStart = false;
        const wasPreviewing = gate.state().phase === 'preview';
        gate.stop();
        stopSpeech(true, 'Speech stopped. Type text to speak again.');
        setRequesting(false);
        if (wasPreviewing) setNotice('Camera is off.');
      }
    });
    return () => {
      mounted.current = false;
      subscription.remove();
      request.current = null;
      gate.stop();
      speechEpoch.current++;
      speechAttempt.current = false;
      speech.stop();
      setDraft('');
    };
  }, [gate, speech, stopSpeech]);

  useEffect(() => {
    const subscription = BackHandler.addEventListener('hardwareBackPress', () => {
      if (preview || requesting || request.current) { stop(); setDraft(''); return true; }
      if (route === 'thanks') { stopSpeech(true, 'Speech stopped.'); setRoute('home'); return true; }
      if (draft || speechBusy || speechAttempt.current) { stopSpeech(true, 'Speech stopped.'); return true; }
      return false;
    });
    return () => subscription.remove();
  }, [preview, requesting, route, draft, speechBusy, stop, stopSpeech]);

  function editDraft(value: string) {
    if (speechAttempt.current) stopSpeech(false, 'Text changed. Review it and tap Speak typed text again.');
    else if (!speechFaultRef.current) setSpeechStatus('Review the text, then tap Speak typed text.');
    setDraft(value);
  }

  async function speakDraft() {
    if (speechAttempt.current || speechFaultRef.current || AppState.currentState !== 'active') return;
    if (!draft.trim()) {
      setSpeechStatus('Enter text before speaking.');
      return;
    }
    if (draft.length > 256 || /[^\x20-\x7E]/.test(draft)) {
      setSpeechStatus('Use 1 to 256 printable ASCII characters. Edit the text and try again.');
      return;
    }
    const text = draft;
    const epoch = ++speechEpoch.current;
    speechAttempt.current = true;
    setSpeechBusy(true);
    setSpeechStatus('Preparing local speech...');
    try {
      const token = await speech.start();
      if (!mounted.current || epoch !== speechEpoch.current) return;
      if (token === null) {
        setSpeechStatus('Local speech is unavailable. Try again when the app is active.');
        return;
      }
      setSpeechStatus('Speaking typed text...');
      const result = await speech.speak(text);
      if (!mounted.current || epoch !== speechEpoch.current || result === null) return;
      switch (result) {
        case 'COMPLETED': setSpeechStatus('Typed text spoken.'); break;
        case 'CANCELLED': setSpeechStatus('Speech was cancelled.'); break;
        case 'BUSY': setSpeechStatus('Speech is busy. Try again.'); break;
        case 'UNAVAILABLE': setSpeechStatus('Local speech is unavailable. Try again when the app is active.'); break;
        case 'INVALID_INPUT': setSpeechStatus('Text could not be spoken. Use 1 to 256 printable ASCII characters.'); break;
        case 'CLEANUP_FAILED':
          speechFaultRef.current = true;
          setSpeechFault(true);
          speech.stop();
          setSpeechStatus('Speech cleanup could not be confirmed. Fully close and reopen the app before speaking again.');
          break;
        default: setSpeechStatus('Speech could not be completed. Try again.');
      }
    } finally {
      if (mounted.current && epoch === speechEpoch.current) {
        speechAttempt.current = false;
        setSpeechBusy(false);
      }
    }
  }

  async function startPreview() {
    if (cleanupFailed || requesting || preview || AppState.currentState !== 'active') return;
    const attempt = {canStart: true};
    request.current = attempt;
    setRequesting(true);
    try {
      const granted = await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.CAMERA);
      if (attempt !== request.current) return;
      if (granted !== PermissionsAndroid.RESULTS.GRANTED) {
        setNotice('Camera permission was not granted. You can use the home screen without it.');
        return;
      }
      if (!attempt.canStart || AppState.currentState !== 'active') {
        setNotice('Camera is off. Permission was granted; tap Start local preview to begin.');
        return;
      }
      const previewToken = gate.start({permission: true, foreground: true});
      if (previewToken !== null) {
        setNotice('Starting local preview. Translation is unavailable.');
        setPreview(previewToken);
      }
    } catch {
      if (attempt === request.current) setNotice('Camera permission could not be requested.');
    } finally {
      if (attempt === request.current) {
        request.current = null;
        setRequesting(false);
      }
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
        </View> : <View style={styles.columns}>
          <View style={[styles.columns, width > height && styles.landscape]}>
          <View style={styles.card}>
            <Text style={styles.eyebrow}>SIGN TO SPEAK · ANDROID FOUNDATION</Text>
            <Text accessibilityRole="header" style={styles.title}>Your hands. Your words.</Text>
            <Text style={styles.body}>A private, on-device signing companion in development.</Text>
            <View style={styles.warning}>
              <Text style={styles.warningTitle}>Translation is not available yet</Text>
              <Text style={styles.body}>This build can show a live camera preview. It does not recognize ASL or speak a translation. You can type text and speak it locally below.</Text>
            </View>
            <Text style={styles.body}>No recording. No saved signing sessions. Camera frames stay on this device.</Text>
            <Button onPress={() => { stop(); stopSpeech(true, 'Speech stopped.'); setRoute('thanks'); }}>Hatsu Is Here For You</Button>
          </View>
          <View style={styles.card}>
            <Text accessibilityRole="header" style={styles.sectionTitle}>Live camera</Text>
            {preview ? <CameraPreview active={true} style={styles.preview}
              onStatus={({nativeEvent}) => {
                if (!mounted.current) return;
                // Cleanup uncertainty is Activity-wide and must latch even for
                // a retired view. Ordinary events belong only to this preview.
                if (nativeEvent.status !== 'camera_cleanup_failed' && !gate.isCurrent(preview)) return;
                if (nativeEvent.status === 'preview_only') {
                  setNotice('Live preview only. Recognition: unsupported.');
                } else {
                  stop();
                  if (nativeEvent.status === 'camera_cleanup_failed') setCleanupFailed(true);
                  setNotice(nativeEvent.status === 'camera_cleanup_failed'
                    ? 'Camera cleanup could not be confirmed. Fully close and reopen the app before starting again.'
                    : nativeEvent.status === 'front_camera_unavailable'
                      ? 'A front camera is not available on this device.'
                      : 'Camera stopped. Tap Start to try again.');
                }
              }} /> : <View style={styles.previewOff}><Text style={styles.body}>{cleanupFailed ? 'Preview hidden' : 'Camera off'}</Text></View>}
            <Text accessibilityLiveRegion="polite" style={styles.body}>{cleanupFailed
              ? 'Camera cleanup could not be confirmed. Fully close and reopen the app before starting again.'
              : notice}</Text>
            {preview || requesting
              ? <Button onPress={stop}>Stop camera</Button>
              : <Button onPress={startPreview} disabled={cleanupFailed}>Start local preview</Button>}
            <Text style={styles.small}>Leaving this screen or putting the app in the background stops the camera. Starting again is always your choice.</Text>
          </View>
          </View>
          <View style={styles.card}>
            <Text accessibilityRole="header" style={styles.sectionTitle}>Speak typed text</Text>
            <Text style={styles.body}>Type and review your own words. This is not ASL recognition or a camera translation.</Text>
            <TextInput testID="typed-speech-input" accessibilityLabel="Text to speak" value={draft}
              onChangeText={editDraft} multiline autoCorrect={false} spellCheck={false}
              autoComplete="off" importantForAutofill="no"
              style={styles.input} placeholder="Type text here" placeholderTextColor="#A99BB8" />
            <Text style={styles.small}>1-256 printable English ASCII characters. Text stays on this screen until you clear it or leave.</Text>
            <Text testID="typed-speech-status" accessibilityLiveRegion="polite" style={styles.body}>{speechFault ? 'Speech cleanup could not be confirmed. Fully close and reopen the app before speaking again.' : speechStatus}</Text>
            <Button testID="typed-speech-speak" onPress={speakDraft} disabled={speechBusy || speechFault}>Speak typed text</Button>
            <Button testID="typed-speech-stop" onPress={() => stopSpeech(false, 'Speech stopped.')}>Stop speech</Button>
            <Button testID="typed-speech-clear" onPress={() => stopSpeech(true, 'Typed text cleared.')}>Clear typed text</Button>
          </View>
        </View>}
      </ScrollView>
    </SafeAreaView>
  </View>;
}

function capturePrivacyReady(): boolean {
  // Read the synchronous native constant before Home is ever mounted. Missing,
  // failed or throwing bridges fail closed; no optimistic async/loading frame.
  try { return NativeModules.CapturePrivacy?.ready === true; }
  catch { return false; }
}

export default function App() {
  return <SafeAreaProvider>{capturePrivacyReady() ? <Home /> :
    <View style={styles.screen}><SafeAreaView style={styles.safe}>
      <View style={styles.card}>
        <Text accessibilityRole="header" style={styles.sectionTitle}>Privacy protection is unavailable</Text>
        <Text style={styles.body}>The camera is off. Close and reopen the app to try again.</Text>
      </View>
    </SafeAreaView></View>
  }</SafeAreaProvider>;
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
  dimmed: {opacity: 0.65},
  input: {minHeight: 100, padding: 14, color: '#FAF6FF', backgroundColor: '#08090C',
    borderWidth: 1, borderColor: '#594474', borderRadius: 14, fontSize: 17, textAlignVertical: 'top'},
  preview: {height: 280, width: '100%', backgroundColor: '#08090C'},
  previewOff: {height: 160, alignItems: 'center', justifyContent: 'center', backgroundColor: '#08090C', borderRadius: 14},
});
