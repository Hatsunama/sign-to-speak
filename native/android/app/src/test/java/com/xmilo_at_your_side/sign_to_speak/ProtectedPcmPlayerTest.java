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

    @Test public void exactCapturePolicyAndSpeechAttributesAreRequired() {
        ProtectedPcmPlayer player = player();
        player.play(new short[160], () -> false);
        AudioAttributes attributes = createdAttributes.get();
        assertNotNull(attributes);
        assertEquals(AudioAttributes.ALLOW_CAPTURE_BY_NONE, attributes.getAllowedCapturePolicy());
        assertEquals(AudioAttributes.USAGE_MEDIA, attributes.getUsage());
        assertEquals(AudioAttributes.CONTENT_TYPE_SPEECH, attributes.getContentType());
        verify(track).play();
        verify(track).release();
        verify(audio).abandonAudioFocusRequest(focusRequest.get());
    }

    @Test public void deniedFocusNeverCreatesTrack() {
        when(audio.requestAudioFocus(any(AudioFocusRequest.class)))
            .thenReturn(AudioManager.AUDIOFOCUS_REQUEST_FAILED);
        ProtectedPcmPlayer player = player();
        assertThrows(IllegalStateException.class, () -> player.play(new short[160], () -> false));
        assertNull(createdAttributes.get());
        verify(track, never()).play();
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
        CountDownLatch wrote = new CountDownLatch(1);
        when(track.write(any(short[].class), anyInt(), anyInt(), eq(AudioTrack.WRITE_NON_BLOCKING)))
            .thenAnswer(call -> { wrote.countDown(); return call.getArgument(2); });
        AtomicReference<Throwable> failure = new AtomicReference<>();
        Thread worker = new Thread(() -> {
            try { player.play(new short[160], () -> false); }
            catch (Throwable t) { failure.set(t); }
        });
        worker.start();
        assertTrue(wrote.await(2, TimeUnit.SECONDS));
        assertTrue(worker.isAlive());
        player.cancel();
        worker.join(2000);
        assertFalse(worker.isAlive());
        verify(track).pause();
        verify(track).flush();
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

    @Test public void cancellationAttemptsPauseAndFlushAndReportsFailure() {
        when(track.getPlaybackHeadPosition()).thenReturn(0);
        doThrow(new IllegalStateException("test pause failure")).when(track).pause();
        doThrow(new IllegalStateException("test flush failure")).when(track).flush();
        ProtectedPcmPlayer player = player();
        CountDownLatch wrote = new CountDownLatch(1);
        when(track.write(any(short[].class), anyInt(), anyInt(), eq(AudioTrack.WRITE_NON_BLOCKING)))
            .thenAnswer(call -> { wrote.countDown(); return call.getArgument(2); });
        Thread worker = new Thread(() -> {
            try { player.play(new short[160], () -> false); }
            catch (IllegalStateException expected) { /* cancellation */ }
        });
        worker.start();
        try { assertTrue(wrote.await(2, TimeUnit.SECONDS)); }
        catch (InterruptedException failure) { throw new AssertionError(failure); }
        assertThrows(IllegalStateException.class, player::cancel);
        verify(track).pause();
        verify(track).flush();
        try { worker.join(2000); }
        catch (InterruptedException failure) { throw new AssertionError(failure); }
        assertFalse(worker.isAlive());
    }
    @Test public void focusLossStillInvalidatesWhenPauseFails() throws Exception {
        when(track.getPlaybackHeadPosition()).thenReturn(0);
        doThrow(new IllegalStateException("test pause failure")).when(track).pause();
        CountDownLatch wrote = new CountDownLatch(1);
        when(track.write(any(short[].class), anyInt(), anyInt(), eq(AudioTrack.WRITE_NON_BLOCKING)))
            .thenAnswer(call -> { wrote.countDown(); return call.getArgument(2); });
        ProtectedPcmPlayer player = player();
        Thread worker = new Thread(() -> {
            try { player.play(new short[160], () -> false); }
            catch (IllegalStateException expected) { /* focus loss */ }
        });
        worker.start();
        assertTrue(wrote.await(2, TimeUnit.SECONDS));
        player.onAudioFocusChange(AudioManager.AUDIOFOCUS_LOSS);
        assertEquals(1, invalidations.get());
        worker.join(2000);
        assertFalse(worker.isAlive());
    }
    @Test public void focusLossInvalidatesSessionWithoutAutoResume() throws Exception {
        when(track.getPlaybackHeadPosition()).thenReturn(0);
        CountDownLatch wrote = new CountDownLatch(1);
        when(track.write(any(short[].class), anyInt(), anyInt(), eq(AudioTrack.WRITE_NON_BLOCKING)))
            .thenAnswer(call -> { wrote.countDown(); return call.getArgument(2); });
        ProtectedPcmPlayer player = player();
        Thread worker = new Thread(() -> {
            try { player.play(new short[160], () -> false); }
            catch (IllegalStateException expected) { /* focus loss stops playback */ }
        });
        worker.start();
        assertTrue(wrote.await(2, TimeUnit.SECONDS));
        player.onAudioFocusChange(AudioManager.AUDIOFOCUS_LOSS_TRANSIENT_CAN_DUCK);
        worker.join(2000);
        assertFalse(worker.isAlive());
        assertEquals(1, invalidations.get());
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
        CountDownLatch wrote = new CountDownLatch(1);
        CountDownLatch atDrain = new CountDownLatch(1);
        CountDownLatch resumeDrain = new CountDownLatch(1);
        when(track.write(any(short[].class), anyInt(), anyInt(), eq(AudioTrack.WRITE_NON_BLOCKING)))
            .thenAnswer(call -> { wrote.countDown(); return call.getArgument(2); });
        ProtectedPcmPlayer player = player();
        Thread worker = new Thread(() -> {
            try {
                player.play(new short[160], () -> {
                    if (wrote.getCount() == 0) {
                        atDrain.countDown();
                        try { resumeDrain.await(); }
                        catch (InterruptedException failure) {
                            Thread.currentThread().interrupt();
                            throw new AssertionError(failure);
                        }
                    }
                    return false;
                });
            } catch (IllegalStateException expected) { /* focus loss stops playback */ }
        });
        worker.start();
        try {
            assertTrue(atDrain.await(2, TimeUnit.SECONDS));
            player.onAudioFocusChange(AudioManager.AUDIOFOCUS_LOSS);
            assertEquals(1, invalidations.get());
            verify(track, times(1)).pause();
            verify(track, times(1)).flush();
        } finally {
            resumeDrain.countDown();
        }
        worker.join(2000);
        assertFalse(worker.isAlive());
        assertThrows(IllegalStateException.class, player::close);
        assertThrows(IllegalStateException.class, player::close);
        verify(track, times(1)).stop();
        verify(track, times(1)).release();
        verify(audio, times(1)).abandonAudioFocusRequest(focusRequest.get());
    }
}
