/** Holds only a native generation token. No utterance or transcript is retained. */
const SPEECH_RESULTS = new Set(['COMPLETED', 'CANCELLED', 'BUSY', 'UNAVAILABLE', 'INVALID_INPUT', 'FAILED', 'CLEANUP_FAILED']);

export function createLocalSpeech(bridge, onCleanupFailed = () => {}) {
  let generation = 0;
  let token = null;
  function cancel(value) {
    if (typeof value !== 'string' || !value) return;
    try { bridge?.cancel?.(value); } catch { /* Native lifecycle also cancels. */ }
  }
  return Object.freeze({
    async start() {
      const request = ++generation;
      if (token !== null) { cancel(token); token = null; }
      if (typeof bridge?.begin !== 'function') return null;
      try {
        const received = await bridge.begin();
        if (typeof received !== 'string' || !received) return null;
        if (request !== generation) { cancel(received); return null; }
        token = received;
        return received;
      } catch { return null; }
    },
    async speak(text) {
      const request = generation;
      const active = token;
      if (active === null) return null;
      if (typeof text !== 'string') return 'INVALID_INPUT';
      if (typeof bridge?.speak !== 'function') return 'UNAVAILABLE';
      try {
        const result = await bridge.speak(active, text);
        if (result === 'CLEANUP_FAILED') onCleanupFailed(result);
        if (request !== generation || token !== active) return null;
        return SPEECH_RESULTS.has(result) ? result : 'FAILED';
      } catch { return request === generation && token === active ? 'FAILED' : null; }
    },
    stop() {
      ++generation;
      const previous = token;
      token = null;
      cancel(previous);
    },
  });
}
