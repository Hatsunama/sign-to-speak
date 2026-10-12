/** UI gate only. Native lifecycle teardown must also run without the JS runtime. */
export function createSession({stopCamera, stopSpeech, clearNativeBuffers}) {
  let generation = 0;
  let phase = 'idle';
  return Object.freeze({
    start({permission, foreground}) {
      if (!permission || !foreground || phase === 'cleanup_failed') return null;
      if (phase === 'preview') return generation;
      phase = 'preview';
      return ++generation;
    },
    isCurrent(token) { return phase === 'preview' && token === generation; },
    state() { return Object.freeze({phase, recognition: 'unsupported'}); },
    stop() {
      ++generation;
      phase = 'idle';
      for (const cleanup of [stopCamera, stopSpeech, clearNativeBuffers]) {
        try { cleanup(); } catch { phase = 'cleanup_failed'; }
      }
    },
  });
}
