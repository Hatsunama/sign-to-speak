import React, {useCallback, useEffect, useRef, useState} from 'react';
import {
  AppState, BackHandler, NativeModules, PermissionsAndroid, Pressable, requireNativeComponent,
  ScrollView, StyleSheet, Text, TextInput, useWindowDimensions, View,
  type NativeSyntheticEvent, type ViewProps,
} from 'react-native';
import {SafeAreaProvider, SafeAreaView} from 'react-native-safe-area-context';
import {createSession} from './src/session.mjs';
import {createLocalSpeech} from './src/local-speech.mjs';
import {createLocalReply} from './src/local-reply.mjs';

const HatsuBackground = requireNativeComponent<ViewProps & {active: boolean}>('HatsuBackground');
const CameraPreview = requireNativeComponent<ViewProps & {
  active: boolean;
  lensFacing: 'back' | 'front';
  onStatus: (event: NativeSyntheticEvent<{
    status: string; measuredHands?: number; confirmedHands?: number; continuousHands?: number;
  }>) => void;
}>('PrivateCameraPreview');

function localSpeechBridge() {
  try { return NativeModules.LocalSpeech; } catch { return null; }
}

function localReplyBridge() {
  try { return NativeModules.LocalReply; } catch { return null; }
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
  const [lensFacing, setLensFacing] = useState<'back' | 'front'>('back');
  const [requesting, setRequesting] = useState(false);
  const [cleanupFailed, setCleanupFailed] = useState(false);
  const [notice, setNotice] = useState('Camera is off.');
  const [draft, setDraft] = useState('');
  const [speechStatus, setSpeechStatus] = useState('Type text, review it, then tap Speak typed text.');
  const [speechBusy, setSpeechBusy] = useState(false);
  const [speechFault, setSpeechFault] = useState(false);
  const [replyCaption, setReplyCaption] = useState('');
  const [replyStatus, setReplyStatus] = useState('Tap Start listening when the other person is ready to speak.');
  const [replyPhase, setReplyPhase] = useState<'idle' | 'requesting' | 'starting' | 'listening' | 'finishing'>('idle');
  const [replyFault, setReplyFault] = useState(false);
  const speechFaultRef = useRef(false);
  const speechAttempt = useRef(false);
  const speechEpoch = useRef(0);
  const request = useRef<{canStart: boolean} | null>(null);
  const cameraViewRevision = useRef(0);
  const mounted = useRef(true);
  const replyFaultRef = useRef(false);
  const replyEpoch = useRef(0);
  const replyRequest = useRef<{canStart: boolean} | null>(null);
  const replyInterval = useRef<ReturnType<typeof setInterval> | null>(null);
  const replyTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const speech = useRef(createLocalSpeech(localSpeechBridge(), () => {
    if (!mounted.current || speechFaultRef.current) return;
    speechFaultRef.current = true;
    speechEpoch.current++;
    speechAttempt.current = false;
    speech.stop();
    setSpeechBusy(false);
    setSpeechFault(true);
  })).current;
  const reply = useRef(createLocalReply(localReplyBridge(), () => {
    if (!mounted.current || replyFaultRef.current) return;
    replyFaultRef.current = true;
    replyEpoch.current++;
    reply.stop();
    setReplyFault(true);
    setReplyPhase('idle');
    setReplyStatus('Microphone cleanup could not be confirmed. Fully close and reopen the app before listening again.');
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

  const clearReplyTimers = useCallback(() => {
    if (replyInterval.current !== null) clearInterval(replyInterval.current);
    if (replyTimeout.current !== null) clearTimeout(replyTimeout.current);
    replyInterval.current = null;
    replyTimeout.current = null;
  }, []);

  const stopReply = useCallback((clearCaption: boolean, message: string) => {
    replyEpoch.current++;
    if (replyRequest.current) replyRequest.current.canStart = false;
    replyRequest.current = null;
    clearReplyTimers();
    reply.stop();
    setReplyPhase('idle');
    if (clearCaption) setReplyCaption('');
    setReplyStatus(replyFaultRef.current
      ? 'Microphone cleanup could not be confirmed. Fully close and reopen the app before listening again.'
      : message);
  }, [clearReplyTimers, reply]);

  const stop = useCallback(() => {
    request.current = null;
    gate.stop();
    stopSpeech(false, 'Speech stopped.');
    stopReply(true, 'Listening cancelled and reply caption cleared.');
    setRequesting(false);
    setNotice('Camera is off.');
  }, [gate, stopReply, stopSpeech]);

  useEffect(() => {
    mounted.current = true;
    const subscription = AppState.addEventListener('change', state => {
      setForeground(state === 'active');
      if (state !== 'active') {
        // Every pause revokes capture eligibility, but the latest permission
        // outcome can still explain why the camera or microphone is off when
        // it resolves. A reply request that has reached native startup is no
        // longer only a permission request and must be cancelled normally.
        if (request.current) request.current.canStart = false;
        const replyPermissionPending = replyRequest.current !== null && reply.state() === 'idle';
        if (replyRequest.current) replyRequest.current.canStart = false;
        const wasPreviewing = gate.state().phase === 'preview';
        gate.stop();
        stopSpeech(true, 'Speech stopped. Type text to speak again.');
        if (!replyPermissionPending) {
          stopReply(true, 'Listening stopped in the background. Tap Start listening to begin again.');
        }
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
      clearReplyTimers();
      replyRequest.current = null;
      replyEpoch.current++;
      reply.stop();
      setDraft('');
      setReplyCaption('');
    };
  }, [clearReplyTimers, gate, reply, speech, stopReply, stopSpeech]);

  useEffect(() => {
    const subscription = BackHandler.addEventListener('hardwareBackPress', () => {
      if (replyPhase !== 'idle' || replyCaption) { stopReply(true, 'Listening cancelled and reply caption cleared.'); return true; }
      if (preview || requesting || request.current) { stop(); setDraft(''); return true; }
      if (route === 'thanks') { stopSpeech(true, 'Speech stopped.'); stopReply(true, 'Listening cancelled and reply caption cleared.'); setRoute('home'); return true; }
      if (draft || speechBusy || speechAttempt.current) { stopSpeech(true, 'Speech stopped.'); return true; }
      return false;
    });
    return () => subscription.remove();
  }, [preview, requesting, route, draft, speechBusy, replyCaption, replyPhase, stop, stopReply, stopSpeech]);

  function editDraft(value: string) {
    if (speechAttempt.current) stopSpeech(false, 'Text changed. Review it and tap Speak typed text again.');
    else if (!speechFaultRef.current) setSpeechStatus('Review the text, then tap Speak typed text.');
    setDraft(value);
  }

  function editReplyCaption(value: string) {
    if (replyRequest.current || reply.state() !== 'idle' || replyPhase !== 'idle') return;
    setReplyCaption(value);
  }

  async function speakDraft() {
    if (speechAttempt.current || speechFaultRef.current || replyFaultRef.current || replyRequest.current || reply.state() !== 'idle' || replyPhase !== 'idle' || AppState.currentState !== 'active') return;
    if (!draft.trim()) {
      setSpeechStatus('Enter text before speaking.');
      return;
    }
    if (draft.length > 256) {
      setSpeechStatus('Use 1 to 256 characters. Edit the text and try again.');
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
        case 'INVALID_INPUT': setSpeechStatus('This text is unsupported by the local English voice. Edit it and try again.'); break;
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

  async function finishReply() {
    if (reply.state() !== 'listening') return;
    const epoch = replyEpoch.current;
    clearReplyTimers();
    setReplyPhase('finishing');
    setReplyStatus('Creating a local caption...');
    const result = await reply.finish();
    if (!mounted.current || epoch !== replyEpoch.current || result === null) return;
    setReplyPhase('idle');
    switch (result.status) {
      case 'COMPLETED':
        setReplyCaption(result.text);
        setReplyStatus('Reply caption ready. Review or edit it below.');
        break;
      case 'NO_SPEECH': setReplyStatus('No speech was detected. Tap Start listening to try again.'); break;
      case 'MODEL_UNAVAILABLE': setReplyStatus('The local reply model is unavailable. Try again after reopening the app.'); break;
      case 'DECODE_FAILED': setReplyStatus('The reply could not be captioned. Tap Start listening to try again.'); break;
      case 'BUSY': setReplyStatus('The microphone is busy. Stop other audio and try again.'); break;
      case 'CLEANUP_FAILED':
        replyFaultRef.current = true;
        setReplyFault(true);
        setReplyStatus('Microphone cleanup could not be confirmed. Fully close and reopen the app before listening again.');
        break;
      case 'CANCELLED': setReplyStatus('Listening was cancelled. Tap Start listening to try again.'); break;
      default: setReplyStatus('A local reply caption could not be created. Tap Start listening to try again.');
    }
  }

  async function startReply() {
    if (replyRequest.current || reply.state() !== 'idle' || replyPhase !== 'idle' || replyFaultRef.current || speechFaultRef.current || speechBusy || speechAttempt.current || AppState.currentState !== 'active') return;
    const attempt = {canStart: true};
    const epoch = ++replyEpoch.current;
    replyRequest.current = attempt;
    setReplyPhase('requesting');
    setReplyStatus('Waiting for microphone permission...');
    try {
      const granted = await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.RECORD_AUDIO);
      if (attempt !== replyRequest.current || epoch !== replyEpoch.current) return;
      if (granted !== PermissionsAndroid.RESULTS.GRANTED) {
        setReplyStatus('Microphone permission was not granted. No audio was captured.');
        return;
      }
      if (!attempt.canStart || AppState.currentState !== 'active') {
        setReplyStatus('Microphone is off. Permission was granted; tap Start listening to begin.');
        return;
      }
      if (speechAttempt.current || speechFaultRef.current || replyFaultRef.current) {
        setReplyStatus('Typed speech is active. Stop it, then tap Start listening again.');
        return;
      }
      setReplyPhase('starting');
      setReplyStatus('Starting local listening...');
      const token = await reply.start();
      if (!mounted.current || epoch !== replyEpoch.current || attempt !== replyRequest.current) return;
      if (token === null) {
        setReplyStatus('Local reply captions are unavailable or the microphone is busy. Try again.');
        return;
      }
      replyRequest.current = null;
      setReplyCaption('');
      setReplyPhase('listening');
      setReplyStatus('Listening locally… 30 seconds remaining.');
      const started = Date.now();
      replyInterval.current = setInterval(() => {
        const remaining = Math.max(0, 30 - Math.floor((Date.now() - started) / 1000));
        if (mounted.current) {
          setReplyStatus(`Listening locally… ${remaining} seconds remaining.`);
        }
      }, 1000);
      replyTimeout.current = setTimeout(() => { void finishReply(); }, 30000);
    } catch {
      if (attempt === replyRequest.current && epoch === replyEpoch.current) {
        setReplyStatus('Microphone permission could not be requested.');
      }
    } finally {
      if (attempt === replyRequest.current && reply.state() !== 'listening') {
        replyRequest.current = null;
        setReplyPhase('idle');
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

  function flipCamera() {
    if (requesting || request.current || cleanupFailed || AppState.currentState !== 'active') return;
    cameraViewRevision.current++;
    setLensFacing(current => current === 'back' ? 'front' : 'back');
    if (preview) setNotice('Starting local preview. Translation is unavailable.');
  }

  const renderedCameraViewRevision = cameraViewRevision.current;

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
              <Text style={styles.body}>Local hand tracking is experimental and available on supported devices. It does not recognize ASL or speak a translation. You can type text and speak it locally below.</Text>
            </View>
            <Text style={styles.body}>No saved recordings or signing sessions. Camera frames stay on this device. Reply audio stays on this device and is not saved.</Text>
            <Button onPress={() => { stop(); stopSpeech(true, 'Speech stopped.'); setRoute('thanks'); }}>Hatsu Is Here For You</Button>
          </View>
          <View style={styles.card}>
            <Text accessibilityRole="header" style={styles.sectionTitle}>Live camera</Text>
            {preview ? <CameraPreview key={`${preview}:${renderedCameraViewRevision}`} active={true}
              lensFacing={lensFacing} style={styles.preview}
              onStatus={({nativeEvent}) => {
                if (!mounted.current) return;
                // Cleanup uncertainty is Activity-wide and must latch even for
                // a retired view. Ordinary events belong only to this view.
                if (nativeEvent.status !== 'camera_cleanup_failed' &&
                  (!gate.isCurrent(preview) || renderedCameraViewRevision !== cameraViewRevision.current)) return;
                if (nativeEvent.status === 'preview_only') {
                  setNotice('Live preview only. Recognition: unsupported.');
                } else if (nativeEvent.status === 'tracking_starting') {
                  setNotice('Starting local hand tracking. Recognition: unsupported.');
                } else if (nativeEvent.status === 'tracking_unavailable') {
                  setNotice('Local hand tracking is unavailable. Live preview only. Recognition: unsupported.');
                } else if (nativeEvent.status === 'tracking_active') {
                  // Only bounded, current-frame aggregate counts cross the bridge.
                  // A tracking result must never change reviewed text or trigger speech.
                  const count = nativeEvent.measuredHands;
                  const validCount = Number.isInteger(count) && count! >= 0 && count! <= 2;
                  setNotice(validCount
                    ? `Local hand tracking: ${count} ${count === 1 ? 'hand' : 'hands'} detected. Recognition: unsupported.`
                    : 'Local hand tracking active. Recognition: unsupported.');
                } else {
                  stop();
                  if (nativeEvent.status === 'camera_cleanup_failed') setCleanupFailed(true);
                  setNotice(nativeEvent.status === 'camera_cleanup_failed'
                    ? 'Camera cleanup could not be confirmed. Fully close and reopen the app before starting again.'
                    : nativeEvent.status === 'rear_camera_unavailable'
                      ? 'A rear camera is not available on this device.'
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
            <Button testID="camera-flip" onPress={flipCamera}
              disabled={requesting || cleanupFailed || !foreground}>
              {lensFacing === 'back' ? 'Switch to front camera' : 'Switch to rear camera'}
            </Button>
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
            <Text style={styles.small}>Use 1 to 256 characters with the local English voice. Text stays on this screen until you clear it or leave.</Text>
            <Text testID="typed-speech-status" accessibilityLiveRegion="polite" style={styles.body}>{speechFault ? 'Speech cleanup could not be confirmed. Fully close and reopen the app before speaking again.' : speechStatus}</Text>
            <Button testID="typed-speech-speak" onPress={speakDraft} disabled={speechBusy || speechFault || replyFault || replyPhase !== 'idle'}>Speak typed text</Button>
            <Button testID="typed-speech-stop" onPress={() => stopSpeech(false, 'Speech stopped.')}>Stop speech</Button>
            <Button testID="typed-speech-clear" onPress={() => stopSpeech(true, 'Typed text cleared.')}>Clear typed text</Button>
          </View>
          <View style={styles.card}>
            <Text accessibilityRole="header" style={styles.sectionTitle}>Caption a spoken reply</Text>
            <Text style={styles.body}>The other person can speak for up to 30 seconds. Audio is processed locally, stays on this device, and is not saved.</Text>
            <Text testID="reply-caption-status" accessibilityLiveRegion="polite" style={styles.body}>{replyFault
              ? 'Microphone cleanup could not be confirmed. Fully close and reopen the app before listening again.'
              : replyStatus}</Text>
            {replyPhase === 'listening'
              ? <Button testID="reply-stop" onPress={() => { void finishReply(); }}>Stop and caption</Button>
              : <Button testID="reply-start" onPress={() => { void startReply(); }}
                disabled={replyPhase !== 'idle' || replyFault || speechFault || speechBusy}>Start listening</Button>}
            <Button testID="reply-clear" onPress={() => stopReply(true, 'Listening cancelled and reply caption cleared.')}>Cancel / Clear</Button>
            <TextInput testID="reply-caption-input" accessibilityLabel="Editable reply caption" value={replyCaption}
              onChangeText={editReplyCaption} editable={replyPhase === 'idle'} multiline
              autoCorrect={false} spellCheck={false} autoComplete="off" importantForAutofill="no"
              style={styles.input} placeholder="Spoken reply caption" placeholderTextColor="#A99BB8" />
            <Text style={styles.small}>Listening never resumes automatically. Starting again is always your choice.</Text>
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
