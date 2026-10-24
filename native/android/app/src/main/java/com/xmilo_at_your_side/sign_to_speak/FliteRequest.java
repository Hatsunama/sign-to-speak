package com.xmilo_at_your_side.sign_to_speak;

import java.util.Arrays;

/** One local, bounded Flite synthesis request. The caller owns the returned PCM. */
public final class FliteRequest implements SpeechSession.Request {
    static { System.loadLibrary("private_tts"); }

    private long id;

    public FliteRequest() {
        id = nativeCreate();
        if (id <= 0) throw new IllegalStateException("Local speech unavailable");
    }

    @Override
    public short[] synthesize(byte[] ascii) {
        try {
            final long requestId;
            synchronized (this) { requestId = id; }
            if (requestId == 0) throw new IllegalStateException("Local speech unavailable");
            return nativeSynthesize(requestId, ascii);
        } finally {
            if (ascii != null) Arrays.fill(ascii, (byte) 0);
        }
    }

    @Override
    public synchronized void cancel() {
        if (id != 0) nativeCancel(id);
    }

    @Override
    public synchronized void close() {
        if (id != 0) {
            nativeClose(id);
            id = 0;
        }
    }

    private static native long nativeCreate();
    private static native short[] nativeSynthesize(long id, byte[] ascii);
    private static native void nativeCancel(long id);
    private static native void nativeClose(long id);
}