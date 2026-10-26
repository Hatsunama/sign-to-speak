package com.xmilo_at_your_side.sign_to_speak;

import static org.junit.Assert.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;

import android.app.Application;
import android.content.Context;
import android.media.AudioAttributes;
import android.media.AudioFocusRequest;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioTrack;
import android.os.Build;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.annotation.Config;

/** The admitted voice emits 24 kHz mono signed 16-bit PCM. */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 29, application = Application.class)
public final class PocketPcmFormatTest {
    private Application context;
    private AudioManager audio;
    private AudioTrack track;
    private AtomicReference<AudioFormat> createdFormat;
    private AtomicReference<AudioAttributes> createdAttributes;
    private AtomicInteger creations;

    @Before public void setUp() {
        context = spy(new Application());
        audio = mock(AudioManager.class);
        track = mock(AudioTrack.class);
        createdFormat = new AtomicReference<>();
        createdAttributes = new AtomicReference<>();
        creations = new AtomicInteger();
        doReturn(audio).when(context).getSystemService(Context.AUDIO_SERVICE);
        if (Build.VERSION.SDK_INT >= 29)
            when(audio.getAllowedCapturePolicy()).thenReturn(AudioAttributes.ALLOW_CAPTURE_BY_NONE);
        when(audio.requestAudioFocus(any(AudioFocusRequest.class)))
            .thenReturn(AudioManager.AUDIOFOCUS_REQUEST_GRANTED);
        when(audio.abandonAudioFocusRequest(any(AudioFocusRequest.class)))
            .thenReturn(AudioManager.AUDIOFOCUS_REQUEST_GRANTED);
        when(track.getState()).thenReturn(AudioTrack.STATE_INITIALIZED);
        if (Build.VERSION.SDK_INT >= 29)
            when(track.getAudioAttributes()).thenAnswer(call -> createdAttributes.get());
        when(track.write(any(short[].class), anyInt(), anyInt(), eq(AudioTrack.WRITE_NON_BLOCKING)))
            .thenAnswer(call -> call.getArgument(2));
        when(track.getPlaybackHeadPosition()).thenReturn(240);
    }

    private ProtectedPcmPlayer player() {
        return new ProtectedPcmPlayer(context, () -> true, () -> { },
            (attributes, format, bufferBytes) -> {
                creations.incrementAndGet();
                createdAttributes.set(attributes);
                createdFormat.set(format);
                assertTrue("stream buffer must hold PCM", bufferBytes > 0);
                return track;
            });
    }

    @Test public void protectedOutputUsesNativeVoicePcmFormat() {
        player().play(new short[240], () -> false);
        AudioFormat format = createdFormat.get();
        assertNotNull("player must create a track", format);
        assertEquals("native voice samples are 24 kHz", 24000, format.getSampleRate());
        assertEquals(AudioFormat.CHANNEL_OUT_MONO, format.getChannelMask());
        assertEquals(AudioFormat.ENCODING_PCM_16BIT, format.getEncoding());
        assertEquals(AudioAttributes.ALLOW_CAPTURE_BY_NONE,
            createdAttributes.get().getAllowedCapturePolicy());
        assertEquals(1, creations.get());
        verify(track).write(any(short[].class), eq(0), eq(240), eq(AudioTrack.WRITE_NON_BLOCKING));
        verify(track).play();
        verify(track).stop();
        verify(track).release();
        verify(audio).abandonAudioFocusRequest(any(AudioFocusRequest.class));
    }

    @Test @Config(sdk = 28) public void oldAndroidCannotOpenSpeechTrack() {
        assertThrows(IllegalStateException.class, () -> player().play(new short[240], () -> false));
        assertEquals(0, creations.get());
        verifyNoInteractions(audio, track);
    }
}
