/** Holds only a native generation token. No utterance or transcript is retained. */
export function createLocalSpeech(bridge) {
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
    stop() {
      ++generation;
      const previous = token;
      token = null;
      cancel(previous);
    },
  });
}
