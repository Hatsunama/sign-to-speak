package com.xmilo_at_your_side.sign_to_speak;

import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.Objects;

/** One local Alba request. Returned 24 kHz mono PCM belongs to the caller. */
public final class PocketTtsRequest implements SpeechSession.Request, AutoCloseable {
    static { System.loadLibrary("private_pocket_tts"); }

    private long id;
    private volatile boolean cancelled;

    public PocketTtsRequest(String modelRoot) {
        Objects.requireNonNull(modelRoot);
        byte[] root;
        try {
            ByteBuffer encoded = StandardCharsets.UTF_8.newEncoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT)
                    .encode(java.nio.CharBuffer.wrap(modelRoot));
            root = new byte[encoded.remaining()];
            encoded.get(root);
            encoded.clear();
            while (encoded.hasRemaining()) encoded.put((byte) 0);
        } catch (CharacterCodingException invalid) {
            throw new IllegalArgumentException("Invalid model root");
        }
        try {
            id = nativeCreate(root);
            if (id <= 0) throw new IllegalStateException("Local speech unavailable");
        } finally {
            Arrays.fill(root, (byte) 0);
        }
    }

    @Override public short[] synthesize(byte[] utf8) {
        try {
            final long requestId;
            synchronized (this) { requestId = id; }
            if (requestId == 0) throw new IllegalStateException("Local speech unavailable");
            if (cancelled) return null;
            short[] pcm = nativeSynthesize(requestId, utf8);
            if (cancelled && pcm != null) {
                Arrays.fill(pcm, (short) 0);
                return null;
            }
            return pcm;
        } finally {
            if (utf8 != null) Arrays.fill(utf8, (byte) 0);
        }
    }

    @Override public synchronized void cancel() {
        if (id != 0) {
            cancelled = true;
            nativeCancel(id);
        }
    }

    @Override public synchronized void close() {
        if (id != 0) {
            cancelled = true;
            nativeClose(id);
            id = 0;
        }
    }

    private static native long nativeCreate(byte[] root);
    private static native short[] nativeSynthesize(long id, byte[] utf8);
    private static native void nativeCancel(long id);
    private static native void nativeClose(long id);
}
