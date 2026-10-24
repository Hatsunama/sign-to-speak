package com.xmilo_at_your_side.sign_to_speak;

import android.content.Context;
import android.media.AudioAttributes;
import android.media.AudioFocusRequest;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioTrack;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import java.util.Objects;
import java.util.function.BooleanSupplier;

/** Plays only app-owned, capture-protected PCM. One instance owns one playback attempt. */
public final class ProtectedPcmPlayer implements SpeechSession.Player {
    private static final int SAMPLE_RATE = 16000;
    private static final int MAX_FRAMES = SAMPLE_RATE * 30;
    private static final int BUFFER_BYTES = 8192;
    private static final int CHUNK_FRAMES = 2048;
    private static final long STALL_MS = 2000;
    private final Object lock = new Object();
    private final Context context;
    private final BooleanSupplier privacyReady;
    private final Runnable onFocusLoss;
    private final TrackFactory trackFactory;
    private volatile boolean cancelled;
    private volatile boolean focusActive;
    private volatile boolean cleanupFault;
    private boolean used;
    private boolean cancelCleanupAttempted;
    private AudioTrack activeTrack;

    interface TrackFactory {
        AudioTrack create(AudioAttributes attributes, AudioFormat format, int bufferBytes);
    }

    public ProtectedPcmPlayer(Context applicationContext, BooleanSupplier privacyReady, Runnable onFocusLoss) {
        this(applicationContext, privacyReady, onFocusLoss, (attributes, format, bufferBytes) ->
            new AudioTrack.Builder().setAudioAttributes(attributes).setAudioFormat(format)
                .setTransferMode(AudioTrack.MODE_STREAM).setBufferSizeInBytes(bufferBytes).build());
    }

    ProtectedPcmPlayer(Context applicationContext, BooleanSupplier privacyReady,
                       Runnable onFocusLoss, TrackFactory trackFactory) {
        this.context = Objects.requireNonNull(applicationContext, "applicationContext");
        this.privacyReady = Objects.requireNonNull(privacyReady, "privacyReady");
        this.onFocusLoss = Objects.requireNonNull(onFocusLoss, "onFocusLoss");
        this.trackFactory = Objects.requireNonNull(trackFactory, "trackFactory");
    }

    @Override public void play(short[] pcm, BooleanSupplier sessionCancelled) {
        if (pcm == null || pcm.length < 1 || pcm.length > MAX_FRAMES || sessionCancelled == null)
            throw new IllegalArgumentException("Invalid audio buffer");
        synchronized (lock) {
            if (used || cancelled) throw new IllegalStateException("Playback unavailable");
            used = true;
        }
        AudioManager manager = null;
        AudioFocusRequest focus = null;
        try {
            requireReady(sessionCancelled);
            manager = (AudioManager) context.getSystemService(Context.AUDIO_SERVICE);
            if (manager == null || manager.getAllowedCapturePolicy() != AudioAttributes.ALLOW_CAPTURE_BY_NONE)
                throw new IllegalStateException("Audio privacy unavailable");
            AudioAttributes attributes = new AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                .setAllowedCapturePolicy(AudioAttributes.ALLOW_CAPTURE_BY_NONE).build();
            focus = new AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
                .setAudioAttributes(attributes)
                .setAcceptsDelayedFocusGain(false)
                .setOnAudioFocusChangeListener(this::onAudioFocusChange,
                    new Handler(Looper.getMainLooper())).build();
            focusActive = true;
            if (manager.requestAudioFocus(focus) != AudioManager.AUDIOFOCUS_REQUEST_GRANTED)
                throw new IllegalStateException("Audio focus unavailable");
            requireReady(sessionCancelled);
            AudioFormat format = new AudioFormat.Builder()
                .setSampleRate(SAMPLE_RATE)
                .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                .setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build();
            requireReady(sessionCancelled);
            if (manager.getAllowedCapturePolicy() != AudioAttributes.ALLOW_CAPTURE_BY_NONE)
                throw new IllegalStateException("Audio privacy unavailable");
            AudioTrack created = trackFactory.create(attributes, format, BUFFER_BYTES);
            if (created == null) throw new IllegalStateException("Audio output unavailable");
            synchronized (lock) {
                activeTrack = created;
            }
            if (created.getState() != AudioTrack.STATE_INITIALIZED ||
                created.getAudioAttributes() == null ||
                created.getAudioAttributes().getAllowedCapturePolicy() != AudioAttributes.ALLOW_CAPTURE_BY_NONE ||
                created.getAudioAttributes().getUsage() != AudioAttributes.USAGE_MEDIA ||
                created.getAudioAttributes().getContentType() != AudioAttributes.CONTENT_TYPE_SPEECH)
                throw new IllegalStateException("Protected audio output unavailable");
            requireReady(sessionCancelled);
            synchronized (lock) {
                requireNotCancelled(sessionCancelled);
                created.play();
            }
            int offset = 0;
            long lastProgressAt = SystemClock.elapsedRealtime();
            while (offset < pcm.length) {
                requireReady(sessionCancelled);
                int written;
                synchronized (lock) {
                    requireNotCancelled(sessionCancelled);
                    written = created.write(pcm, offset, Math.min(CHUNK_FRAMES, pcm.length - offset),
                        AudioTrack.WRITE_NON_BLOCKING);
                }
                if (written < 0 || written > Math.min(CHUNK_FRAMES, pcm.length - offset))
                    throw new IllegalStateException("Audio output failed");
                if (written == 0) {
                    if (SystemClock.elapsedRealtime() - lastProgressAt > STALL_MS)
                        throw new IllegalStateException("Audio output stalled");
                    SystemClock.sleep(5);
                } else {
                    offset += written;
                    lastProgressAt = SystemClock.elapsedRealtime();
                }
            }
            long drainStart = SystemClock.elapsedRealtime();
            long lastHead = -1;
            long lastHeadAt = drainStart;
            while (true) {
                requireReady(sessionCancelled);
                long head;
                synchronized (lock) {
                    requireNotCancelled(sessionCancelled);
                    head = created.getPlaybackHeadPosition() & 0xffffffffL;
                }
                if (head >= pcm.length) break;
                long now = SystemClock.elapsedRealtime();
                if (head != lastHead) { lastHead = head; lastHeadAt = now; }
                if (now - lastHeadAt > STALL_MS || now - drainStart > pcm.length / 16L + STALL_MS)
                    throw new IllegalStateException("Audio output did not drain");
                SystemClock.sleep(5);
            }
        } catch (RuntimeException failure) {
            throw new IllegalStateException("Protected playback failed");
        } finally {
            focusActive = false;
            AudioTrack releasing;
            synchronized (lock) { releasing = activeTrack; activeTrack = null; }
            boolean cleanupFailed = false;
            if (releasing != null) {
                try { releasing.stop(); } catch (RuntimeException failure) { cleanupFailed = true; }
                try { releasing.release(); } catch (RuntimeException failure) { cleanupFailed = true; }
            }
            if (focus != null && manager != null) {
                try { manager.abandonAudioFocusRequest(focus); }
                catch (RuntimeException failure) { cleanupFailed = true; }
            }
            if (cleanupFailed) cleanupFault = true;
            if (cleanupFault) throw new IllegalStateException("Protected playback cleanup failed");
        }
    }

    void onAudioFocusChange(int change) {
        if (change != AudioManager.AUDIOFOCUS_LOSS &&
            change != AudioManager.AUDIOFOCUS_LOSS_TRANSIENT &&
            change != AudioManager.AUDIOFOCUS_LOSS_TRANSIENT_CAN_DUCK) return;
        if (!focusActive || cancelled) return;
        try { cancel(); }
        catch (RuntimeException failure) { /* worker and session observe cleanup failure */ }
        finally { onFocusLoss.run(); }
    }
    private void requireReady(BooleanSupplier sessionCancelled) {
        if (Build.VERSION.SDK_INT < 29 || !privacyReady.getAsBoolean() || sessionCancelled.getAsBoolean() || cancelled)
            throw new IllegalStateException("Protected playback unavailable");
    }

    private void requireNotCancelled(BooleanSupplier sessionCancelled) {
        if (cancelled || sessionCancelled.getAsBoolean())
            throw new IllegalStateException("Playback cancelled");
    }

    @Override public void cancel() {
        cancelled = true;
        synchronized (lock) {
            if (activeTrack != null && !cancelCleanupAttempted) {
                cancelCleanupAttempted = true;
                try { activeTrack.pause(); } catch (RuntimeException failure) { cleanupFault = true; }
                try { activeTrack.flush(); } catch (RuntimeException failure) { cleanupFault = true; }
            }
        }
        if (cleanupFault) throw new IllegalStateException("Protected playback cancellation failed");
    }

    @Override public void close() {
        boolean failed = false;
        try { cancel(); } catch (RuntimeException failure) { failed = true; }
        AudioTrack releasing;
        synchronized (lock) { releasing = activeTrack; activeTrack = null; }
        if (releasing != null) {
            try { releasing.stop(); } catch (RuntimeException failure) { failed = true; }
            try { releasing.release(); } catch (RuntimeException failure) { failed = true; }
        }
        if (failed) cleanupFault = true;
        if (cleanupFault) throw new IllegalStateException("Protected playback cleanup failed");
    }
}
