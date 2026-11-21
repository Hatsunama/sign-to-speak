package com.xmilo_at_your_side.sign_to_speak;

import android.content.res.AssetFileDescriptor;
import android.content.res.AssetManager;
import android.system.ErrnoException;
import android.system.Os;
import java.io.FileDescriptor;
import java.io.IOException;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Arrays;

/** Package-private capability created only after exact same-fd model admission. */
final class ReplyModelSpan {
    private static final String MODEL = "reply-asr/ggml-tiny.en.bin";
    private static final long MODEL_BYTES = 77704715L;
    private static final byte[] MODEL_SHA = hex("921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f");
    private static final String VAD = "reply-asr/ggml-silero-v6.2.0.bin";
    private static final long VAD_BYTES = 885098L;
    private static final byte[] VAD_SHA = hex("2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987");

    final int fd;
    final long offset;
    final long length;
    interface Owner { void close() throws IOException; }
    private final Owner owner;

    ReplyModelSpan(int fd, long offset, long length) {
        this.fd = fd; this.offset = offset; this.length = length; this.owner = null;
    }
    ReplyModelSpan(int fd, long offset, long length, Owner owner) {
        this.fd = fd; this.offset = offset; this.length = length; this.owner = owner;
    }
    private ReplyModelSpan(AssetFileDescriptor owner, long expectedLength, byte[] expectedSha)
            throws IOException, ErrnoException {
        if (owner.getLength() != expectedLength || owner.getStartOffset() < 0) {
            throw new IOException("Reply model unavailable");
        }
        FileDescriptor descriptor = owner.getFileDescriptor();
        verify(descriptor, owner.getStartOffset(), expectedLength, expectedSha);
        this.owner = owner::close;
        this.fd = owner.getParcelFileDescriptor().getFd();
        this.offset = owner.getStartOffset();
        this.length = expectedLength;
    }

    static Pair open(AssetManager assets) {
        AssetFileDescriptor model = null;
        AssetFileDescriptor vad = null;
        try {
            model = assets.openFd(MODEL);
            vad = assets.openFd(VAD);
            ReplyModelSpan admittedModel = new ReplyModelSpan(model, MODEL_BYTES, MODEL_SHA);
            ReplyModelSpan admittedVad = new ReplyModelSpan(vad, VAD_BYTES, VAD_SHA);
            return new Pair(admittedModel, admittedVad);
        } catch (IOException | ErrnoException | RuntimeException failure) {
            IOException cleanup = closeOwners(
                    vad == null ? null : vad::close,
                    model == null ? null : model::close);
            if (cleanup != null) {
                CleanupException result = new CleanupException(cleanup);
                result.addSuppressed(failure);
                throw result;
            }
            throw new IllegalStateException("Local reply unavailable");
        }
    }

    private static void verify(FileDescriptor fd, long offset, long length, byte[] expected)
            throws ErrnoException, IOException {
        final MessageDigest digest;
        try { digest = MessageDigest.getInstance("SHA-256"); }
        catch (NoSuchAlgorithmException impossible) { throw new AssertionError(impossible); }
        byte[] buffer = new byte[64 * 1024];
        long read = 0;
        try {
            while (read < length) {
                int wanted = (int) Math.min(buffer.length, length - read);
                int count = Os.pread(fd, buffer, 0, wanted, offset + read);
                if (count <= 0) throw new IOException("Reply model unavailable");
                digest.update(buffer, 0, count);
                read += count;
            }
            if (!MessageDigest.isEqual(digest.digest(), expected)) {
                throw new IOException("Reply model unavailable");
            }
        } finally { Arrays.fill(buffer, (byte) 0); }
    }

    static final class Pair implements AutoCloseable {
        final ReplyModelSpan model;
        final ReplyModelSpan vad;
        Pair(ReplyModelSpan model, ReplyModelSpan vad) { this.model = model; this.vad = vad; }
        @Override public void close() {
            IOException failure = closeOwners(vad.owner, model.owner);
            if (failure != null) throw new CleanupException(failure);
        }
    }

    static final class CleanupException extends IllegalStateException {
        CleanupException(IOException cause) { super("Reply model descriptor cleanup failed", cause); }
    }

    private static IOException closeOwners(Owner first, Owner second) {
        IOException failure = closeOwner(first);
        IOException secondFailure = closeOwner(second);
        if (failure == null) return secondFailure;
        if (secondFailure != null) failure.addSuppressed(secondFailure);
        return failure;
    }

    private static IOException closeOwner(Owner owner) {
        if (owner == null) return null;
        try { owner.close(); return null; }
        catch (IOException failure) { return failure; }
    }
    private static byte[] hex(String value) {
        byte[] result = new byte[value.length() / 2];
        for (int i = 0; i < result.length; i++) {
            int high = Character.digit(value.charAt(i * 2), 16);
            int low = Character.digit(value.charAt(i * 2 + 1), 16);
            result[i] = (byte) ((high << 4) | low);
        }
        return result;
    }
}
