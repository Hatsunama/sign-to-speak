package com.xmilo_at_your_side.sign_to_speak;

import android.content.res.AssetManager;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.Objects;
import java.util.function.BooleanSupplier;

/** A single, finite customer-reply utterance. This class does not own microphone capture. */
public final class ReplyAsrRequest implements AutoCloseable {
    public static final int MAX_SAMPLES = 30 * 16000;
    static final int OUTPUT_CAPACITY = 8193;
    static final int STATUS_OK = 0;
    static final int STATUS_NO_SPEECH = 1;
    static final int STATUS_INVALID_INPUT = 2;
    static final int STATUS_CANCELLED = 3;
    static final int STATUS_MODEL_UNAVAILABLE = 4;
    static final int STATUS_DECODE_FAILED = 5;

    public enum Status { TRANSCRIPT, NO_SPEECH, CANCELLED }
    public enum FailureCode { MODEL_UNAVAILABLE, INVALID_INPUT, DECODE_FAILED, CLEANUP_FAILED }

    public static final class Failure extends IllegalStateException {
        public final FailureCode code;
        private Failure(FailureCode code, String message) { super(message); this.code = code; }
    }

    public static final class Result {
        public final Status status;
        public final String text;
        private Result(Status status, String text) { this.status = status; this.text = text; }
        static Result transcript(String text) { return new Result(Status.TRANSCRIPT, text); }
        static Result noSpeech() { return new Result(Status.NO_SPEECH, null); }
        static Result cancelled() { return new Result(Status.CANCELLED, null); }
    }

    interface NativeApi {
        long open(int modelFd, long modelOffset, long modelLength,
                  int vadFd, long vadOffset, long vadLength);
        int decode(long id, float[] pcm, byte[] output);
        void cancel(long id);
        int close(long id);
    }

    private static final class JniApi implements NativeApi {
        static final JniApi INSTANCE = new JniApi();
        static { System.loadLibrary("private_reply_asr"); }
        @Override public long open(int modelFd, long modelOffset, long modelLength,
                                   int vadFd, long vadOffset, long vadLength) {
            return nativeOpen(modelFd, modelOffset, modelLength, vadFd, vadOffset, vadLength);
        }
        @Override public int decode(long id, float[] pcm, byte[] output) { return nativeDecode(id, pcm, output); }
        @Override public void cancel(long id) { nativeCancel(id); }
        @Override public int close(long id) { return nativeClose(id); }
    }

    private final Thread ownerThread = Thread.currentThread();
    private final NativeApi nativeApi;
    private final BooleanSupplier externallyCancelled;
    private final Object stateLock = new Object();
    private long id;
    private boolean cancelled;
    private boolean closed;

    /** Opens and authenticates the two uncompressed bundled model spans on the owner worker. */
    public ReplyAsrRequest(AssetManager assets, BooleanSupplier cancelled) {
        this(assets, cancelled, JniApi.INSTANCE);
    }

    private ReplyAsrRequest(AssetManager assets, BooleanSupplier cancelled, NativeApi nativeApi) {
        this.nativeApi = Objects.requireNonNull(nativeApi);
        this.externallyCancelled = Objects.requireNonNull(cancelled);
        Objects.requireNonNull(assets);
        final ReplyModelSpan.Pair pair;
        try { pair = ReplyModelSpan.open(assets); }
        catch (ReplyModelSpan.CleanupException failure) { throw cleanupFailed(); }
        initialize(pair);
    }

    // Pure-Java seam for the contract test. Production callers cannot create verified spans.
    ReplyAsrRequest(ReplyModelSpan.Pair pair, BooleanSupplier cancelled, NativeApi nativeApi) {
        this.nativeApi = Objects.requireNonNull(nativeApi);
        this.externallyCancelled = Objects.requireNonNull(cancelled);
        Objects.requireNonNull(pair);
        initialize(pair);
    }

    private void initialize(ReplyModelSpan.Pair pair) {
        long opened = 0;
        RuntimeException pending = null;
        try {
            if (externalCancellationRequested()) throw new CancelledOpen();
            opened = nativeApi.open(pair.model.fd, pair.model.offset,
                    pair.model.length, pair.vad.fd, pair.vad.offset, pair.vad.length);
            if (opened <= 0) {
                long failure = opened;
                opened = 0;
                throw openFailure(failure);
            }
            if (externalCancellationRequested()) {
                nativeApi.cancel(opened);
                int closeStatus = nativeApi.close(opened);
                opened = 0;
                if (closeStatus != STATUS_OK) throw cleanupFailed();
                throw new CancelledOpen();
            }
        } catch (RuntimeException failure) {
            pending = failure;
        }
        try { pair.close(); }
        catch (ReplyModelSpan.CleanupException failure) {
            if (opened != 0) rollbackOpened(opened);
            throw cleanupFailed();
        }
        if (pending != null) {
            if (opened != 0 && !rollbackOpened(opened)) throw cleanupFailed();
            throw pending;
        }
        synchronized (stateLock) { id = opened; }
    }

    private boolean rollbackOpened(long opened) {
        boolean cleanupFailed = false;
        try { nativeApi.cancel(opened); }
        catch (RuntimeException failure) { cleanupFailed = true; }
        try {
            if (nativeApi.close(opened) != STATUS_OK) cleanupFailed = true;
        } catch (RuntimeException failure) { cleanupFailed = true; }
        return !cleanupFailed;
    }

    /** Consumes and clears signed 16-bit mono PCM; count must contain at most 30 seconds at 16 kHz. */
    public Result decode(short[] pcm, int count) {
        requireOwnerThread();
        if (pcm == null || count <= 0 || count > MAX_SAMPLES || count > pcm.length) {
            if (pcm != null) Arrays.fill(pcm, (short) 0);
            throw failure(FailureCode.INVALID_INPUT, "Invalid reply audio");
        }
        float[] normalized = new float[count];
        try {
            for (int i = 0; i < count; i++) normalized[i] = pcm[i] / 32768.0f;
            Arrays.fill(pcm, (short) 0);
            final long handle;
            synchronized (stateLock) {
                if (closed || id == 0) throw unavailable();
                handle = id;
                if (cancelled || externalCancellationRequested()) return Result.cancelled();
            }
            return decodeOnOwner(handle, normalized);
        } finally {
            Arrays.fill(pcm, (short) 0);
            Arrays.fill(normalized, 0.0f);
        }
    }

    private Result decodeOnOwner(long handle, float[] normalized) {
        byte[] output = new byte[OUTPUT_CAPACITY];
        try {
            int status = nativeApi.decode(handle, normalized, output);
            if (status == STATUS_CANCELLED || isCancelled()) return Result.cancelled();
            if (status == STATUS_NO_SPEECH) return Result.noSpeech();
            if (status == STATUS_INVALID_INPUT) throw failure(FailureCode.INVALID_INPUT, "Invalid reply audio");
            if (status == STATUS_MODEL_UNAVAILABLE) throw failure(FailureCode.MODEL_UNAVAILABLE, "Local reply model unavailable");
            if (status != STATUS_OK) throw failure(FailureCode.DECODE_FAILED, "Local reply decode failed");
            int end = 0;
            while (end < output.length && output[end] != 0) end++;
            if (end == 0 || end == output.length) throw failure(FailureCode.DECODE_FAILED, "Local reply decode failed");
            ByteBuffer bytes = ByteBuffer.wrap(output, 0, end);
            String text;
            try {
                text = StandardCharsets.UTF_8.newDecoder()
                        .onMalformedInput(CodingErrorAction.REPORT)
                        .onUnmappableCharacter(CodingErrorAction.REPORT).decode(bytes).toString();
            } catch (CharacterCodingException invalid) {
                throw failure(FailureCode.DECODE_FAILED, "Local reply decode failed");
            }
            if (text.trim().isEmpty()) throw failure(FailureCode.DECODE_FAILED, "Local reply decode failed");
            return isCancelled() ? Result.cancelled() : Result.transcript(text);
        } finally {
            Arrays.fill(output, (byte) 0);
        }
    }

    private boolean isCancelled() {
        synchronized (stateLock) { return cancelled || closed || externalCancellationRequested(); }
    }

    private boolean externalCancellationRequested() {
        try { return externallyCancelled.getAsBoolean(); }
        catch (RuntimeException invalidOwnerState) { return true; }
    }

    /** The only cross-thread native operation: it atomically marks the native turn cancelled. */
    public void cancel() {
        final long handle;
        synchronized (stateLock) {
            cancelled = true;
            handle = id;
        }
        if (handle != 0) nativeApi.cancel(handle);
    }

    @Override public void close() {
        requireOwnerThread();
        final long handle;
        synchronized (stateLock) {
            if (closed) return;
            closed = true;
            cancelled = true;
            handle = id;
            id = 0;
        }
        boolean cleanupFailed = false;
        if (handle != 0) try { nativeApi.cancel(handle); }
        catch (RuntimeException failure) { cleanupFailed = true; }
        if (handle != 0) try {
            if (nativeApi.close(handle) != STATUS_OK) cleanupFailed = true;
        } catch (RuntimeException failure) { cleanupFailed = true; }
        if (cleanupFailed) throw cleanupFailed();
    }

    private void requireOwnerThread() {
        if (Thread.currentThread() != ownerThread) {
            throw failure(FailureCode.INVALID_INPUT, "Local reply called from wrong owner thread");
        }
    }

    private static IllegalStateException unavailable() {
        return new IllegalStateException("Local reply unavailable");
    }
    private static Failure cleanupFailed() {
        return failure(FailureCode.CLEANUP_FAILED, "Local reply cleanup failed");
    }
    private static Failure openFailure(long encoded) {
        if (encoded == -1000) return cleanupFailed();
        int status = encoded < 0 ? (int) (-encoded - 1) : STATUS_MODEL_UNAVAILABLE;
        if (status == STATUS_INVALID_INPUT)
            return failure(FailureCode.INVALID_INPUT, "Invalid reply model span");
        if (status == STATUS_DECODE_FAILED)
            return failure(FailureCode.DECODE_FAILED, "Local reply initialization failed");
        return failure(FailureCode.MODEL_UNAVAILABLE, "Local reply model unavailable");
    }
    private static Failure failure(FailureCode code, String message) {
        return new Failure(code, message);
    }
    private static final class CancelledOpen extends IllegalStateException {
        CancelledOpen() { super("Local reply cancelled"); }
    }

    private static native long nativeOpen(int modelFd, long modelOffset, long modelLength,
                                          int vadFd, long vadOffset, long vadLength);
    private static native int nativeDecode(long id, float[] pcm, byte[] output);
    private static native void nativeCancel(long id);
    private static native int nativeClose(long id);
}
