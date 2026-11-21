/** Owns only a volatile native reply token. Caption text is returned to its caller, never retained. */
const REPLY_RESULTS = new Set([
  'CANCELLED',
  'NO_SPEECH',
  'INVALID_INPUT',
  'MODEL_UNAVAILABLE',
  'DECODE_FAILED',
  'BUSY',
  'UNAVAILABLE',
  'FAILED',
  'CLEANUP_FAILED',
]);

export function createLocalReply(bridge, onCleanupFailure = () => {}) {
  let generation = 0;
  let token = null;
  let phase = 'idle';

  function notifyCleanup() {
    try { onCleanupFailure('CLEANUP_FAILED'); } catch { /* Reporting must not break cleanup. */ }
  }

  function cancel(value) {
    if (typeof value !== 'string' || !value) return;
    try { bridge?.cancel?.(value); } catch { /* Native lifecycle also owns cancellation. */ }
  }

  function retire() {
    const previous = token;
    token = null;
    phase = 'idle';
    cancel(previous);
  }

  return Object.freeze({
    async start() {
      const request = ++generation;
      retire();
      phase = 'starting';
      if (typeof bridge?.begin !== 'function') {
        phase = 'idle';
        return null;
      }
      try {
        const received = await bridge.begin();
        if (typeof received !== 'string' || !received) {
          if (request === generation) phase = 'idle';
          return null;
        }
        if (request !== generation) {
          cancel(received);
          return null;
        }
        token = received;
        phase = 'listening';
        return received;
      } catch (error) {
        if (error?.code === 'CLEANUP_FAILED') notifyCleanup();
        if (request === generation) phase = 'idle';
        return null;
      }
    },

    async finish() {
      const request = generation;
      const active = token;
      if (active === null || phase !== 'listening') return null;
      phase = 'finishing';
      if (typeof bridge?.finish !== 'function') {
        if (request === generation && token === active) retire();
        return {status: 'UNAVAILABLE'};
      }
      try {
        const result = await bridge.finish(active);
        if (result?.status === 'CLEANUP_FAILED') notifyCleanup();
        if (request !== generation || token !== active || phase !== 'finishing') return null;

        token = null;
        phase = 'idle';
        if (result === null || typeof result !== 'object' || Array.isArray(result)) {
          cancel(active);
          return {status: 'FAILED'};
        }
        if (result.status === 'COMPLETED') {
          if (typeof result.text !== 'string' || result.text.trim() === '') {
            cancel(active);
            return {status: 'FAILED'};
          }
          return {status: 'COMPLETED', text: result.text};
        }
        if (!REPLY_RESULTS.has(result.status) || result.text !== undefined) {
          cancel(active);
          return {status: 'FAILED'};
        }
        return {status: result.status};
      } catch (error) {
        if (error?.code === 'CLEANUP_FAILED') notifyCleanup();
        if (request !== generation || token !== active || phase !== 'finishing') return null;
        retire();
        return {status: 'FAILED'};
      }
    },

    stop() {
      ++generation;
      retire();
    },

    state() { return phase; },
  });
}
