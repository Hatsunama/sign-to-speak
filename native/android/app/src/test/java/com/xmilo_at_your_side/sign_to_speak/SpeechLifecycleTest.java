package com.xmilo_at_your_side.sign_to_speak;

import static org.junit.Assert.*;
import static org.mockito.Mockito.*;

import com.facebook.react.bridge.Promise;
import com.facebook.react.bridge.ReactApplicationContext;
import com.facebook.react.defaults.DefaultReactActivityDelegate;
import android.content.Context;
import android.media.AudioAttributes;
import android.media.AudioManager;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.mockito.MockedConstruction;
import org.mockito.ArgumentCaptor;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.Shadows;
import org.robolectric.android.controller.ActivityController;
import org.robolectric.annotation.Config;

/** Exercises the actual Activity, bridge and owner while replacing only the synthesis worker. */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 36, application = MainApplication.class,
    shadows = {ReactBootstrapShadow.class, CaptureAudioManagerShadow.class})
public class SpeechLifecycleTest {
    @Test public void backPauseAndDestroyCancelWithoutJavaScriptAndOldTokenCannotCancelNew() {
        try (MockedConstruction<DefaultReactActivityDelegate> delegates = mockConstruction(
                DefaultReactActivityDelegate.class,
                (delegate, context) -> when(delegate.onBackPressed()).thenReturn(true));
             MockedConstruction<SpeechSession> sessions = mockConstruction(SpeechSession.class,
                (session, context) -> when(session.begin(true)).thenReturn(11L, 12L, 13L))) {
            ActivityController<MainActivity> controller = Robolectric.buildActivity(MainActivity.class).setup();
            MainActivity activity = controller.get();
            assertTrue(((MainApplication) activity.getApplication()).getCapturePrivacyReady());
            ReactApplicationContext context = mock(ReactApplicationContext.class);
            when(context.getCurrentActivity()).thenReturn(activity);
            LocalSpeechModule bridge = new LocalSpeechModule(context);
            Promise first = mock(Promise.class);
            bridge.begin(first);
            Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
            ArgumentCaptor<Object> firstToken = ArgumentCaptor.forClass(Object.class);
            verify(first).resolve(firstToken.capture());
            String oldToken = (String) firstToken.getValue();
            assertTrue(oldToken.endsWith(":11"));
            SpeechSession session = sessions.constructed().get(0);
            activity.onBackPressed();
            verify(session, atLeastOnce()).stop();
            Promise second = mock(Promise.class);
            bridge.begin(second);
            Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
            ArgumentCaptor<Object> secondToken = ArgumentCaptor.forClass(Object.class);
            verify(second).resolve(secondToken.capture());
            String newToken = (String) secondToken.getValue();
            assertTrue(newToken.endsWith(":12"));
            clearInvocations(session);
            bridge.cancel(oldToken);
            verify(session, never()).stop();
            controller.pause();
            verify(session).stop();
            controller.stop().destroy();
            verify(session).close();
        }
    }

    @Test public void cleanupFaultSurvivesActivityRecreationAndBlocksBegin() {
        try (MockedConstruction<DefaultReactActivityDelegate> delegates = mockConstruction(DefaultReactActivityDelegate.class);
             MockedConstruction<SpeechSession> sessions = mockConstruction(SpeechSession.class,
                (session, context) -> {
                    when(session.begin(true)).thenReturn(7L);
                    doAnswer(call -> {
                        ((SpeechSession.Completion) call.getArgument(2)).complete(SpeechSession.Result.CLEANUP_FAILED);
                        return null;
                    }).when(session).speak(anyLong(), any(byte[].class), any());
                })) {
            ActivityController<MainActivity> first = Robolectric.buildActivity(MainActivity.class).setup();
            MainActivity activity = first.get();
            ReactApplicationContext context = mock(ReactApplicationContext.class);
            when(context.getCurrentActivity()).thenReturn(activity);
            LocalSpeechModule bridge = new LocalSpeechModule(context);
            Promise begin = mock(Promise.class);
            bridge.begin(begin);
            Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
            ArgumentCaptor<Object> begunToken = ArgumentCaptor.forClass(Object.class);
            verify(begin).resolve(begunToken.capture());
            String token = (String) begunToken.getValue();
            assertTrue(token.endsWith(":7"));
            Promise speaking = mock(Promise.class);
            bridge.speak(token, "HELLO", speaking);
            verify(speaking).resolve("CLEANUP_FAILED");
            MainApplication app = (MainApplication) activity.getApplication();
            assertTrue(app.getSpeechCleanupFaulted());
            first.pause().stop().destroy();
            ActivityController<MainActivity> recreated = Robolectric.buildActivity(MainActivity.class).setup();
            when(context.getCurrentActivity()).thenReturn(recreated.get());
            Promise denied = mock(Promise.class);
            bridge.begin(denied);
            Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
            verify(denied).resolve(null);
            assertEquals(1, sessions.constructed().size());
            recreated.pause().stop().destroy();
        }
    }

    @Test public void ownerWipesTemporaryTextWhenSessionThrowsBeforeCompletion() {
        final byte[][] submitted = new byte[1][];
        try (MockedConstruction<DefaultReactActivityDelegate> delegates = mockConstruction(DefaultReactActivityDelegate.class);
             MockedConstruction<SpeechSession> sessions = mockConstruction(SpeechSession.class,
                (session, context) -> {
                    when(session.begin(true)).thenReturn(19L);
                    doAnswer(call -> {
                        submitted[0] = call.getArgument(1);
                        throw new OutOfMemoryError("session submission failed");
                    }).when(session).speak(anyLong(), any(byte[].class), any());
                })) {
            ActivityController<MainActivity> controller = Robolectric.buildActivity(MainActivity.class).setup();
            MainActivity activity = controller.get();
            ReactApplicationContext context = mock(ReactApplicationContext.class);
            when(context.getCurrentActivity()).thenReturn(activity);
            LocalSpeechModule bridge = new LocalSpeechModule(context);
            Promise begin = mock(Promise.class);
            bridge.begin(begin);
            Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
            ArgumentCaptor<Object> token = ArgumentCaptor.forClass(Object.class);
            verify(begin).resolve(token.capture());
            boolean propagated = false;
            try { bridge.speak((String) token.getValue(), "SECRET", mock(Promise.class)); }
            catch (OutOfMemoryError expected) { propagated = true; }
            assertTrue("fatal session error propagates", propagated);
            assertNotNull(submitted[0]);
            assertArrayEquals("owner text must be erased without completion",
                    new byte[6], submitted[0]);
            controller.pause().stop().destroy();
        }
    }
    @Test public void changedCapturePolicyBlocksBeginBeforeConstructingSession() {
        try (MockedConstruction<DefaultReactActivityDelegate> delegates = mockConstruction(DefaultReactActivityDelegate.class);
             MockedConstruction<SpeechSession> sessions = mockConstruction(SpeechSession.class)) {
            ActivityController<MainActivity> controller = Robolectric.buildActivity(MainActivity.class).setup();
            MainActivity activity = controller.get();
            AudioManager audio = (AudioManager) activity.getSystemService(Context.AUDIO_SERVICE);
            audio.setAllowedCapturePolicy(AudioAttributes.ALLOW_CAPTURE_BY_ALL);
            ReactApplicationContext context = mock(ReactApplicationContext.class);
            when(context.getCurrentActivity()).thenReturn(activity);
            Promise denied = mock(Promise.class);
            new LocalSpeechModule(context).begin(denied);
            Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
            verify(denied).resolve(null);
            assertTrue(sessions.constructed().isEmpty());
            controller.pause().stop().destroy();
        }
    }
}
