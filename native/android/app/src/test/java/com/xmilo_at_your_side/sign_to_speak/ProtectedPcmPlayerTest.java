package com.xmilo_at_your_side.sign_to_speak;

import static org.junit.Assert.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import android.app.Application;
import android.content.Context;
import android.media.AudioAttributes;
import android.media.AudioFocusRequest;
import android.media.AudioManager;
import android.media.AudioTrack;
import android.os.Build;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.annotation.Config;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 29, application = Application.class)
public class ProtectedPcmPlayerTest {
    private Application context;
    private AudioManager audio;
    private AudioTrack track;
    private AtomicInteger invalidations;
    private AtomicReference<AudioAttributes> createdAttributes;
    private AtomicReference<AudioFocusRequest> focusRequest;

    @Before public void setUp() {
        context = spy(new Application());
        audio = mock(AudioManager.class);
        track = mock(AudioTrack.class);
        invalidations = new AtomicInteger();
        createdAttributes = new AtomicReference<>();
        focusRequest = new AtomicReference<>();
        doReturn(audio).when(context).getSystemService(Context.AUDIO_SERVICE);
        if (Build.VERSION.SDK_INT >= 29)
            when(audio.getAllowedCapturePolicy()).thenReturn(AudioAttributes.ALLOW_CAPTURE_BY_NONE);
        when(audio.requestAudioFocus(any(AudioFocusRequest.class))).thenAnswer(call -> {
            focusRequest.set(call.getArgument(0));
            return AudioManager.AUDIOFOCUS_REQUEST_GRANTED;
        });
        when(audio.abandonAudioFocusRequest(any(AudioFocusRequest.class)))
            .thenReturn(AudioManager.AUDIOFOCUS_REQUEST_GRANTED);
        when(track.getState()).thenReturn(AudioTrack.STATE_INITIALIZED);
        if (Build.VERSION.SDK_INT >= 29)
            when(track.getAudioAttributes()).thenAnswer(call -> createdAttributes.get());
        when(track.write(any(short[].class), anyInt(), anyInt(), eq(AudioTrack.WRITE_NON_BLOCKING)))
            .thenAnswer(call -> call.getArgument(2));
        when(track.getPlaybackHeadPosition()).thenReturn(160);
    }

    private ProtectedPcmPlayer player() {
        return new ProtectedPcmPlayer(context, () -> true, invalidations::incrementAndGet,
            (attributes, format, bufferBytes) -> {
                createdAttributes.set(attributes);
                return track;
            });
    }

    private static final class DrainBarrier implements java.util.function.BooleanSupplier {
        private final CountDownLatch written = new CountDownLatch(1);
        private final CountDownLatch atDrain = new CountDownLatch(1);
        private final CountDownLatch resumeDrain = new CountDownLatch(1);
        private final AtomicBoolean parked = new AtomicBoolean();

        void wrote() { written.countDown(); }
        boolean awaitDrain() throws InterruptedException { return atDrain.await(2, TimeUnit.SECONDS); }
        void release() { resumeDrain.countDown(); }

        @Override public boolean getAsBoolean() {
            if (written.getCount() == 0 && parked.compareAndSet(false, true)) {
                atDrain.countDown();
                try { resumeDrain.await(); }
                catch (InterruptedException failure) {
                    Thread.currentThread().interrupt();
                    throw new AssertionError(failure);
                }
            }
            return false;
        }
    }

    @Test public void exactCapturePolicyAndSpeechAttributesAreRequired() {
        ProtectedPcmPlayer player = player();
        player.play(new short[160], () -> false);
        AudioAttributes attributes = createdAttributes.get();
        assertNotNull(attributes);
        assertEquals(AudioAttributes.ALLOW_CAPTURE_BY_NONE, attributes.getAllowedCapturePolicy());
        assertEquals(AudioAttributes.USAGE_MEDIA, attributes.getUsage());
        assertEquals(AudioAttributes.CONTENT_TYPE_SPEECH, attributes.getContentType());
        verify(track).play();
        verify(track, never()).pause();
        verify(track, never()).flush();
        verify(track).release();
        verify(audio).abandonAudioFocusRequest(focusRequest.get());
    }

    @Test public void deniedFocusNeverCreatesTrack() {
        when(audio.requestAudioFocus(any(AudioFocusRequest.class))).thenAnswer(call -> {
            focusRequest.set(call.getArgument(0));
            return AudioManager.AUDIOFOCUS_REQUEST_FAILED;
        });
        when(audio.abandonAudioFocusRequest(any(AudioFocusRequest.class)))
            .thenReturn(AudioManager.AUDIOFOCUS_REQUEST_FAILED);
        ProtectedPcmPlayer player = player();
        assertThrows(IllegalStateException.class, () -> player.play(new short[160], () -> false));
        assertNull(createdAttributes.get());
        verify(track, never()).play();
        verify(audio).abandonAudioFocusRequest(focusRequest.get());
        player.close();
    }

    @Test public void appAndTrackCapturePolicyAreReadBack() {
        when(audio.getAllowedCapturePolicy()).thenReturn(AudioAttributes.ALLOW_CAPTURE_BY_SYSTEM);
        assertThrows(IllegalStateException.class, () -> player().play(new short[160], () -> false));
        assertNull(createdAttributes.get());
        if (Build.VERSION.SDK_INT >= 29)
            when(audio.getAllowedCapturePolicy()).thenReturn(AudioAttributes.ALLOW_CAPTURE_BY_NONE);
        when(track.getAudioAttributes()).thenReturn(new AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
            .setAllowedCapturePolicy(AudioAttributes.ALLOW_CAPTURE_BY_ALL).build());
        assertThrows(IllegalStateException.class, () -> player().play(new short[160], () -> false));
        verify(track).release();
    }

    @Test public void initializationAndWriteFailuresReleaseAndAbandon() {
        when(track.getState()).thenReturn(AudioTrack.STATE_UNINITIALIZED);
        assertThrows(IllegalStateException.class, () -> player().play(new short[160], () -> false));
        verify(track).release();
        verify(audio).abandonAudioFocusRequest(any(AudioFocusRequest.class));
        clearInvocations(track, audio);
        when(track.getState()).thenReturn(AudioTrack.STATE_INITIALIZED);
        when(track.write(any(short[].class), anyInt(), anyInt(), eq(AudioTrack.WRITE_NON_BLOCKING)))
            .thenReturn(AudioTrack.ERROR_BAD_VALUE);
        assertThrows(IllegalStateException.class, () -> player().play(new short[160], () -> false));
        verify(track).release();
        verify(audio).abandonAudioFocusRequest(any(AudioFocusRequest.class));
    }

    @Test public void partialAndZeroWritesEventuallyDrain() {
        AtomicInteger calls = new AtomicInteger();
        when(track.write(any(short[].class), anyInt(), anyInt(), eq(AudioTrack.WRITE_NON_BLOCKING)))
            .thenAnswer(call -> calls.incrementAndGet() == 1 ? 0 : Math.min(80, (int) call.getArgument(2)));
        player().play(new short[160], () -> false);
        assertTrue(calls.get() >= 3);
        verify(track).release();
    }

    @Test public void writeCompletionIsNotPlaybackCompletionAndCancelStopsDrain() throws Exception {
        when(track.getPlaybackHeadPosition()).thenReturn(0);
        ProtectedPcmPlayer player = player();
        DrainBarrier barrier = new DrainBarrier();
        when(track.write(any(short[].class), anyInt(), anyInt(), eq(AudioTrack.WRITE_NON_BLOCKING)))
            .thenAnswer(call -> { barrier.wrote(); return call.getArgument(2); });
        AtomicReference<Throwable> failure = new AtomicReference<>();
        Thread worker = new Thread(() -> {
            try { player.play(new short[160], barrier); }
            catch (Throwable t) { failure.set(t); }
        });
        worker.start();
        try {
            assertTrue(barrier.awaitDrain());
            assertTrue(worker.isAlive());
            player.cancel();
            verify(track).pause();
            verify(track).flush();
        } finally {
            barrier.release();
            worker.join(2000);
        }
        assertFalse(worker.isAlive());
        assertTrue(failure.get() instanceof IllegalStateException);
        verify(track).release();
    }
    @Test public void privacyLossAfterPartialWriteStopsBeforeNextWrite() {
        java.util.concurrent.atomic.AtomicBoolean ready = new java.util.concurrent.atomic.AtomicBoolean(true);
        AtomicInteger writes = new AtomicInteger();
        when(track.write(any(short[].class), anyInt(), anyInt(), eq(AudioTrack.WRITE_NON_BLOCKING)))
            .thenAnswer(call -> {
                writes.incrementAndGet();
                ready.set(false);
                return 80;
            });
        ProtectedPcmPlayer player = new ProtectedPcmPlayer(context, ready::get,
            invalidations::incrementAndGet, (attributes, format, bufferBytes) -> {
                createdAttributes.set(attributes);
                return track;
            });
        assertThrows(IllegalStateException.class, () -> player.play(new short[160], () -> false));
        assertEquals(1, writes.get());
        verify(track).stop();
        verify(track).release();
        verify(audio).abandonAudioFocusRequest(focusRequest.get());
        assertThrows(IllegalStateException.class, () -> player.play(new short[160], () -> false));
    }

    @Test public void privacyLossDuringDrainStopsBeforeNextHeadPoll() {
        java.util.concurrent.atomic.AtomicBoolean ready = new java.util.concurrent.atomic.AtomicBoolean(true);
        AtomicInteger polls = new AtomicInteger();
        when(track.getPlaybackHeadPosition()).thenAnswer(call -> {
            polls.incrementAndGet();
            ready.set(false);
            return 0;
        });
        ProtectedPcmPlayer player = new ProtectedPcmPlayer(context, ready::get,
            invalidations::incrementAndGet, (attributes, format, bufferBytes) -> {
                createdAttributes.set(attributes);
                return track;
            });
        assertThrows(IllegalStateException.class, () -> player.play(new short[160], () -> false));
        assertEquals(1, polls.get());
        verify(track).stop();
        verify(track).release();
        verify(audio).abandonAudioFocusRequest(focusRequest.get());
    }
    @Test @Config(sdk = 28) public void oldAndroidNeverRequestsFocusOrCreatesTrack() {
        assertThrows(IllegalStateException.class, () -> player().play(new short[160], () -> false));
        verifyNoInteractions(audio, track);
        assertNull(createdAttributes.get());
    }

    @Test public void cancelledBeforePlaybackNeverRequestsFocus() {
        ProtectedPcmPlayer player = player();
        player.cancel();
        assertThrows(IllegalStateException.class, () -> player.play(new short[160], () -> false));
        verifyNoInteractions(audio, track);
    }

    @Test public void cancellationDuringTrackCreationCannotStartAudio() {
        AtomicReference<ProtectedPcmPlayer> holder = new AtomicReference<>();
        ProtectedPcmPlayer player = new ProtectedPcmPlayer(context, () -> true,
            invalidations::incrementAndGet, (attributes, format, bufferBytes) -> {
                createdAttributes.set(attributes);
                holder.get().cancel();
                return track;
            });
        holder.set(player);
        assertThrows(IllegalStateException.class, () -> player.play(new short[160], () -> false));
        verify(track, never()).play();
        verify(track).release();
        verify(audio).abandonAudioFocusRequest(any(AudioFocusRequest.class));
    }

    @Test public void releaseFailureStillAbandonsFocus() {
        doThrow(new IllegalStateException("test release failure")).when(track).release();
        assertThrows(IllegalStateException.class, () -> player().play(new short[160], () -> false));
        verify(audio).abandonAudioFocusRequest(any(AudioFocusRequest.class));
    }
    @Test public void cleanupFailuresAreReportedAfterEveryCleanupAttempt() {
        doThrow(new IllegalStateException("test stop failure")).when(track).stop();
        doThrow(new IllegalStateException("test release failure")).when(track).release();
        when(audio.abandonAudioFocusRequest(any(AudioFocusRequest.class)))
            .thenThrow(new IllegalStateException("test abandon failure"));
        assertThrows(IllegalStateException.class, () -> player().play(new short[160], () -> false));
        verify(track).stop();
        verify(track).release();
        verify(audio).abandonAudioFocusRequest(any(AudioFocusRequest.class));
    }

    @Test public void cancellationAttemptsPauseAndFlushAndReportsFailure() throws Exception {
        when(track.getPlaybackHeadPosition()).thenReturn(0);
        doThrow(new IllegalStateException("test pause failure")).when(track).pause();
        doThrow(new IllegalStateException("test flush failure")).when(track).flush();
        ProtectedPcmPlayer player = player();
        DrainBarrier barrier = new DrainBarrier();
        when(track.write(any(short[].class), anyInt(), anyInt(), eq(AudioTrack.WRITE_NON_BLOCKING)))
            .thenAnswer(call -> { barrier.wrote(); return call.getArgument(2); });
        AtomicReference<Throwable> failure = new AtomicReference<>();
        Thread worker = new Thread(() -> {
            try { player.play(new short[160], barrier); }
            catch (Throwable t) { failure.set(t); }
        });
        worker.start();
        try {
            assertTrue(barrier.awaitDrain());
            assertThrows(IllegalStateException.class, player::cancel);
            verify(track).pause();
            verify(track).flush();
        } finally {
            barrier.release();
            worker.join(2000);
        }
        assertFalse(worker.isAlive());
        assertTrue(failure.get() instanceof IllegalStateException);
    }
    @Test public void focusLossStillInvalidatesWhenPauseFails() throws Exception {
        when(track.getPlaybackHeadPosition()).thenReturn(0);
        doThrow(new IllegalStateException("test pause failure")).when(track).pause();
        DrainBarrier barrier = new DrainBarrier();
        when(track.write(any(short[].class), anyInt(), anyInt(), eq(AudioTrack.WRITE_NON_BLOCKING)))
            .thenAnswer(call -> { barrier.wrote(); return call.getArgument(2); });
        ProtectedPcmPlayer player = player();
        AtomicReference<Throwable> failure = new AtomicReference<>();
        Thread worker = new Thread(() -> {
            try { player.play(new short[160], barrier); }
            catch (Throwable t) { failure.set(t); }
        });
        worker.start();
        try {
            assertTrue(barrier.awaitDrain());
            player.onAudioFocusChange(AudioManager.AUDIOFOCUS_LOSS);
            assertEquals(1, invalidations.get());
            verify(track).pause();
        } finally {
            barrier.release();
            worker.join(2000);
        }
        assertFalse(worker.isAlive());
        assertTrue(failure.get() instanceof IllegalStateException);
    }
    @Test public void focusLossInvalidatesSessionWithoutAutoResume() throws Exception {
        when(track.getPlaybackHeadPosition()).thenReturn(0);
        DrainBarrier barrier = new DrainBarrier();
        when(track.write(any(short[].class), anyInt(), anyInt(), eq(AudioTrack.WRITE_NON_BLOCKING)))
            .thenAnswer(call -> { barrier.wrote(); return call.getArgument(2); });
        ProtectedPcmPlayer player = player();
        AtomicReference<Throwable> failure = new AtomicReference<>();
        Thread worker = new Thread(() -> {
            try { player.play(new short[160], barrier); }
            catch (Throwable t) { failure.set(t); }
        });
        worker.start();
        try {
            assertTrue(barrier.awaitDrain());
            player.onAudioFocusChange(AudioManager.AUDIOFOCUS_LOSS_TRANSIENT_CAN_DUCK);
            assertEquals(1, invalidations.get());
            verify(track).pause();
            verify(track).flush();
        } finally {
            barrier.release();
            worker.join(2000);
        }
        assertFalse(worker.isAlive());
        assertTrue(failure.get() instanceof IllegalStateException);
        assertThrows(IllegalStateException.class, () -> player.play(new short[160], () -> false));
        player.onAudioFocusChange(AudioManager.AUDIOFOCUS_GAIN);
        assertEquals(1, invalidations.get());
    }
    @Test public void playbackCleanupFaultPersistsThroughLaterClose() {
        doThrow(new IllegalStateException("test release failure")).when(track).release();
        ProtectedPcmPlayer player = player();
        assertThrows(IllegalStateException.class, () -> player.play(new short[160], () -> false));
        assertThrows(IllegalStateException.class, player::close);
        assertThrows(IllegalStateException.class, player::close);
        verify(track, times(1)).stop();
        verify(track, times(1)).release();
        verify(audio, times(1)).abandonAudioFocusRequest(focusRequest.get());
    }

    @Test public void focusCancellationFaultPersistsThroughLaterClose() throws Exception {
        when(track.getPlaybackHeadPosition()).thenReturn(0);
        doThrow(new IllegalStateException("test pause failure")).when(track).pause();
        doThrow(new IllegalStateException("test flush failure")).when(track).flush();
        DrainBarrier barrier = new DrainBarrier();
        when(track.write(any(short[].class), anyInt(), anyInt(), eq(AudioTrack.WRITE_NON_BLOCKING)))
            .thenAnswer(call -> { barrier.wrote(); return call.getArgument(2); });
        ProtectedPcmPlayer player = player();
        AtomicReference<Throwable> failure = new AtomicReference<>();
        Thread worker = new Thread(() -> {
            try { player.play(new short[160], barrier); }
            catch (Throwable t) { failure.set(t); }
        });
        worker.start();
        try {
            assertTrue(barrier.awaitDrain());
            player.onAudioFocusChange(AudioManager.AUDIOFOCUS_LOSS);
            assertEquals(1, invalidations.get());
            verify(track, times(1)).pause();
            verify(track, times(1)).flush();
        } finally {
            barrier.release();
            worker.join(2000);
        }
        assertFalse(worker.isAlive());
        assertTrue(failure.get() instanceof IllegalStateException);
        assertThrows(IllegalStateException.class, player::close);
        assertThrows(IllegalStateException.class, player::close);
        verify(track, times(1)).stop();
        verify(track, times(1)).release();
        verify(audio, times(1)).abandonAudioFocusRequest(focusRequest.get());
    }

    @Test public void privacyAbortSilencesQueuedAudioBeforeStop() {
        AtomicBoolean ready = new AtomicBoolean(true);
        AtomicBoolean queuedPlaying = new AtomicBoolean();
        AtomicBoolean queuedAtStop = new AtomicBoolean();
        when(track.write(any(short[].class), anyInt(), anyInt(), eq(AudioTrack.WRITE_NON_BLOCKING)))
            .thenAnswer(call -> {
                queuedPlaying.set(true);
                ready.set(false);
                return call.getArgument(2);
            });
        doAnswer(call -> {
            queuedAtStop.set(queuedPlaying.get());
            queuedPlaying.set(false);
            return null;
        }).when(track).stop();
        doAnswer(call -> { queuedPlaying.set(false); return null; }).when(track).pause();
        doAnswer(call -> { queuedPlaying.set(false); return null; }).when(track).flush();
        ProtectedPcmPlayer player = new ProtectedPcmPlayer(context, ready::get,
            invalidations::incrementAndGet, (attributes, format, bufferBytes) -> {
                createdAttributes.set(attributes);
                return track;
            });
        assertThrows(IllegalStateException.class, () -> player.play(new short[160], () -> false));
        verify(track).write(any(short[].class), eq(0), eq(160), eq(AudioTrack.WRITE_NON_BLOCKING));
        verify(track).stop();
        assertFalse("privacy loss must silence queued PCM before stop starts", queuedAtStop.get());
        assertFalse(queuedPlaying.get());
    }

    @Test public void cancelSilencesQueueWhilePrivacyAbortIsInsideStop() throws Exception {
        AtomicBoolean ready = new AtomicBoolean(true);
        AtomicBoolean queuedPlaying = new AtomicBoolean();
        AtomicBoolean playingWhenCancelReturned = new AtomicBoolean(true);
        CountDownLatch stopEntered = new CountDownLatch(1);
        CountDownLatch allowStop = new CountDownLatch(1);
        CountDownLatch cancelReturned = new CountDownLatch(1);
        AtomicReference<Throwable> workerFailure = new AtomicReference<>();
        AtomicReference<Throwable> cancelFailure = new AtomicReference<>();
        when(track.write(any(short[].class), anyInt(), anyInt(), eq(AudioTrack.WRITE_NON_BLOCKING)))
            .thenAnswer(call -> {
                queuedPlaying.set(true);
                ready.set(false);
                return call.getArgument(2);
            });
        doAnswer(call -> {
            stopEntered.countDown();
            if (!allowStop.await(5, TimeUnit.SECONDS))
                throw new IllegalStateException("stop barrier timed out");
            queuedPlaying.set(false);
            return null;
        }).when(track).stop();
        doAnswer(call -> { queuedPlaying.set(false); return null; }).when(track).pause();
        doAnswer(call -> { queuedPlaying.set(false); return null; }).when(track).flush();
        ProtectedPcmPlayer player = new ProtectedPcmPlayer(context, ready::get,
            invalidations::incrementAndGet, (attributes, format, bufferBytes) -> {
                createdAttributes.set(attributes);
                return track;
            });
        Thread worker = new Thread(() -> {
            try { player.play(new short[160], () -> false); }
            catch (Throwable failure) { workerFailure.set(failure); }
        });
        Thread canceller = new Thread(() -> {
            try { player.cancel(); }
            catch (Throwable failure) { cancelFailure.set(failure); }
            finally {
                playingWhenCancelReturned.set(queuedPlaying.get());
                cancelReturned.countDown();
            }
        });
        worker.setDaemon(true);
        canceller.setDaemon(true);
        worker.start();
        try {
            assertTrue("worker never reached stop with queued PCM", stopEntered.await(2, TimeUnit.SECONDS));
            verify(track).write(any(short[].class), eq(0), eq(160), eq(AudioTrack.WRITE_NON_BLOCKING));
            canceller.start();
            assertTrue("cancellation did not return while stop was parked",
                cancelReturned.await(1, TimeUnit.SECONDS));
            assertNull(cancelFailure.get());
            assertFalse("cancel returned while queued audio was still playing",
                playingWhenCancelReturned.get());
        } finally {
            allowStop.countDown();
            worker.join(2000);
            if (canceller.isAlive()) canceller.join(2000);
        }
        assertFalse(worker.isAlive());
        assertFalse(canceller.isAlive());
        assertTrue(workerFailure.get() instanceof IllegalStateException);
        verify(track).release();
    }

    @Test public void focusLossWhilePrivacyAbortWaitsForLockSilencesQueuedAudio() throws Exception {
        AtomicBoolean written = new AtomicBoolean();
        AtomicBoolean queuedPlaying = new AtomicBoolean();
        AtomicBoolean queuedAtStop = new AtomicBoolean();
        CountDownLatch checkEntered = new CountDownLatch(1);
        CountDownLatch allowCheck = new CountDownLatch(1);
        AtomicReference<Throwable> workerFailure = new AtomicReference<>();
        when(track.write(any(short[].class), anyInt(), anyInt(), eq(AudioTrack.WRITE_NON_BLOCKING)))
            .thenAnswer(call -> {
                queuedPlaying.set(true);
                written.set(true);
                return call.getArgument(2);
            });
        doAnswer(call -> { queuedPlaying.set(false); return null; }).when(track).pause();
        doAnswer(call -> { queuedPlaying.set(false); return null; }).when(track).flush();
        doAnswer(call -> {
            queuedAtStop.set(queuedPlaying.get());
            queuedPlaying.set(false);
            return null;
        }).when(track).stop();
        ProtectedPcmPlayer player = new ProtectedPcmPlayer(context, () -> {
            if (written.get()) {
                checkEntered.countDown();
                try {
                    if (!allowCheck.await(5, TimeUnit.SECONDS))
                        throw new AssertionError("privacy check barrier timed out");
                } catch (InterruptedException failure) {
                    Thread.currentThread().interrupt();
                    throw new AssertionError(failure);
                }
            }
            return !written.get();
        }, invalidations::incrementAndGet, (attributes, format, bufferBytes) -> {
            createdAttributes.set(attributes);
            return track;
        });
        Thread worker = new Thread(() -> {
            try { player.play(new short[160], () -> false); }
            catch (Throwable failure) { workerFailure.set(failure); }
        });
        worker.setDaemon(true);
        worker.start();
        try {
            assertTrue("worker never entered the post-write privacy check",
                checkEntered.await(2, TimeUnit.SECONDS));
            java.lang.reflect.Field lockField = ProtectedPcmPlayer.class.getDeclaredField("lock");
            lockField.setAccessible(true);
            Object playerLock = lockField.get(player);
            synchronized (playerLock) {
                allowCheck.countDown();
                long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(2);
                while (worker.getState() != Thread.State.BLOCKED && System.nanoTime() < deadline)
                    Thread.sleep(1);
                assertEquals("worker did not reach cleanup lock", Thread.State.BLOCKED, worker.getState());
                assertTrue("queued PCM was not playing before focus loss", queuedPlaying.get());
                player.onAudioFocusChange(AudioManager.AUDIOFOCUS_LOSS);
                assertFalse("focus loss returned with queued PCM playing", queuedPlaying.get());
                assertEquals(1, invalidations.get());
            }
        } finally {
            allowCheck.countDown();
            worker.join(2000);
        }
        assertFalse(worker.isAlive());
        assertTrue(workerFailure.get() instanceof IllegalStateException);
        assertFalse("stop began before queued PCM was silenced", queuedAtStop.get());
        verify(track, times(1)).release();
    }

    @Test public void failedFocusAbandonFaultsSessionAndBlocksAnotherBegin() throws Exception {
        when(audio.abandonAudioFocusRequest(any(AudioFocusRequest.class)))
            .thenReturn(AudioManager.AUDIOFOCUS_REQUEST_FAILED);
        SpeechSession.Request request = new SpeechSession.Request() {
            @Override public short[] synthesize(byte[] ascii) { return new short[160]; }
            @Override public void cancel() { }
            @Override public void close() { }
        };
        SpeechSession session = new SpeechSession(new SpeechSession.Factory() {
            @Override public SpeechSession.Request newRequest() { return request; }
            @Override public SpeechSession.Player newPlayer() {
                return ProtectedPcmPlayerTest.this.player();
            }
        });
        CountDownLatch completed = new CountDownLatch(1);
        AtomicReference<SpeechSession.Result> result = new AtomicReference<>();
        try {
            long token = session.begin(true);
            assertTrue(token > 0);
            session.speak(token, new byte[] { 'h', 'i' }, value -> {
                result.set(value);
                completed.countDown();
            });
            assertTrue("session did not complete", completed.await(2, TimeUnit.SECONDS));
            verify(track).play();
            verify(audio).abandonAudioFocusRequest(focusRequest.get());
            assertEquals(SpeechSession.Result.CLEANUP_FAILED, result.get());
            assertTrue(session.isFaulted());
            assertEquals(0, session.begin(true));
        } finally {
            session.close();
        }
    }
}
