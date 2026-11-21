package com.xmilo_at_your_side.sign_to_speak;

import static org.junit.Assert.*;
import static org.mockito.Mockito.*;

import com.facebook.react.bridge.Promise;
import com.facebook.react.bridge.ReactApplicationContext;
import com.facebook.react.defaults.DefaultReactActivityDelegate;
import android.content.Context;
import android.media.AudioAttributes;
import android.media.AudioManager;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
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
    @Test public void markUncertainFaultsProcessAndBlocksBothAudioBridgesAcrossRecreation()
            throws Exception {
        try (MockedConstruction<DefaultReactActivityDelegate> delegates = mockConstruction(DefaultReactActivityDelegate.class);
             MockedConstruction<ReplySession> replies = mockConstruction(ReplySession.class);
             MockedConstruction<SpeechSession> speeches = mockConstruction(SpeechSession.class)) {
            ActivityController<MainActivity> first = Robolectric.buildActivity(MainActivity.class).setup();
            ReactApplicationContext context = mock(ReactApplicationContext.class);
            when(context.getCurrentActivity()).thenReturn(first.get());
            LocalSpeechModule speech = new LocalSpeechModule(context);
            invokeNoArg(speech, "markUncertain");
            MainApplication app = (MainApplication) first.get().getApplication();
            assertTrue(app.getReplyCleanupFaulted());
            assertTrue(app.getSpeechCleanupFaulted());
            assertBothAudioBeginsRejected(new LocalReplyModule(context), speech);

            first.pause().stop().destroy();
            ActivityController<MainActivity> recreated = Robolectric.buildActivity(MainActivity.class).setup();
            when(context.getCurrentActivity()).thenReturn(recreated.get());
            assertBothAudioBeginsRejected(new LocalReplyModule(context), new LocalSpeechModule(context));
            assertTrue(replies.constructed().isEmpty());
            assertTrue(speeches.constructed().isEmpty());
            recreated.pause().stop().destroy();
        }
    }

    @Test public void ordinarySpeechCancellationDoesNotFaultEitherAudioPath() {
        try (MockedConstruction<DefaultReactActivityDelegate> delegates = mockConstruction(DefaultReactActivityDelegate.class);
             MockedConstruction<SpeechSession> sessions = mockConstruction(SpeechSession.class,
                     (session, ignored) -> when(session.begin(true)).thenReturn(47L))) {
            ActivityController<MainActivity> controller = Robolectric.buildActivity(MainActivity.class).setup();
            ReactApplicationContext context = mock(ReactApplicationContext.class);
            when(context.getCurrentActivity()).thenReturn(controller.get());
            LocalSpeechModule bridge = new LocalSpeechModule(context);
            Promise begun = mock(Promise.class);
            bridge.begin(begun);
            Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
            ArgumentCaptor<Object> token = ArgumentCaptor.forClass(Object.class);
            verify(begun).resolve(token.capture());
            bridge.cancel((String) token.getValue());
            MainApplication app = (MainApplication) controller.get().getApplication();
            assertFalse(app.getReplyCleanupFaulted());
            assertFalse(app.getSpeechCleanupFaulted());
            assertFalse(app.isLocalAudioLeaseHeld());
            controller.pause().stop().destroy();
        }
    }

    @Test public void stalledSpeechCallbackFaultsByNativeDeadlineButKeepsLeaseUntilWorkerCompletes()
            throws Exception {
        AtomicReference<SpeechSession.Completion> completion = new AtomicReference<>();
        AtomicReference<Thread> cancellationThread = new AtomicReference<>();
        CountDownLatch cancellationRequested = new CountDownLatch(1);
        try (MockedConstruction<DefaultReactActivityDelegate> delegates = mockConstruction(DefaultReactActivityDelegate.class);
             MockedConstruction<SpeechSession> sessions = mockConstruction(SpeechSession.class,
                     (session, ignored) -> {
                         when(session.begin(true)).thenReturn(53L);
                         doAnswer(call -> {
                             completion.set(call.getArgument(2));
                             return null; // The worker accepts speech but never completes it.
                         }).when(session).speak(anyLong(), any(byte[].class), any());
                         doAnswer(call -> {
                             cancellationThread.set(Thread.currentThread());
                             cancellationRequested.countDown();
                             return null;
                         }).when(session).stop();
                     })) {
            ActivityController<MainActivity> controller = Robolectric.buildActivity(MainActivity.class).setup();
            ReactApplicationContext context = mock(ReactApplicationContext.class);
            when(context.getCurrentActivity()).thenReturn(controller.get());
            LocalSpeechModule bridge = new LocalSpeechModule(context);
            Promise begun = mock(Promise.class);
            bridge.begin(begun);
            Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
            ArgumentCaptor<Object> token = ArgumentCaptor.forClass(Object.class);
            verify(begun).resolve(token.capture());
            Promise speaking = mock(Promise.class);
            bridge.speak((String) token.getValue(), "HELLO", speaking);
            MainApplication app = (MainApplication) controller.get().getApplication();
            assertTrue(app.isLocalAudioLeaseHeld());

            Shadows.shadowOf(android.os.Looper.getMainLooper()).idleFor(179, TimeUnit.SECONDS);
            assertFalse(app.getReplyCleanupFaulted());
            assertFalse(app.getSpeechCleanupFaulted());
            assertTrue(app.isLocalAudioLeaseHeld());
            verifyNoInteractions(speaking);

            Shadows.shadowOf(android.os.Looper.getMainLooper()).idleFor(2, TimeUnit.SECONDS);
            assertTrue(app.getReplyCleanupFaulted());
            assertTrue(app.getSpeechCleanupFaulted());
            assertTrue("deadline must not claim worker cleanup", app.isLocalAudioLeaseHeld());
            verify(speaking).resolve("CLEANUP_FAILED");
            assertTrue("speech deadline must request stop",
                    cancellationRequested.await(1, TimeUnit.SECONDS));
            assertNotSame("SpeechSession.stop must not run on the main looper",
                    Thread.currentThread(), cancellationThread.get());
            verify(sessions.constructed().get(0), timeout(1000).times(1)).stop();
            assertTrue("lease remains held until worker completion", app.isLocalAudioLeaseHeld());

            completion.get().complete(SpeechSession.Result.CANCELLED);
            assertFalse(app.isLocalAudioLeaseHeld());
            verifyNoMoreInteractions(speaking);
            controller.pause().stop().destroy();
        }
    }

    @Test public void speechCompletionBeforeDeadlineCancelsTimerAndReleasesLease() {
        try (MockedConstruction<DefaultReactActivityDelegate> delegates = mockConstruction(DefaultReactActivityDelegate.class);
             MockedConstruction<SpeechSession> sessions = mockConstruction(SpeechSession.class,
                     (session, ignored) -> {
                         when(session.begin(true)).thenReturn(59L);
                         doAnswer(call -> {
                             ((SpeechSession.Completion) call.getArgument(2))
                                     .complete(SpeechSession.Result.COMPLETED);
                             return null;
                         }).when(session).speak(anyLong(), any(byte[].class), any());
                     })) {
            ActivityController<MainActivity> controller = Robolectric.buildActivity(MainActivity.class).setup();
            ReactApplicationContext context = mock(ReactApplicationContext.class);
            when(context.getCurrentActivity()).thenReturn(controller.get());
            LocalSpeechModule bridge = new LocalSpeechModule(context);
            Promise begun = mock(Promise.class);
            bridge.begin(begun);
            Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
            ArgumentCaptor<Object> token = ArgumentCaptor.forClass(Object.class);
            verify(begun).resolve(token.capture());
            Promise speaking = mock(Promise.class);
            bridge.speak((String) token.getValue(), "HELLO", speaking);
            verify(speaking).resolve("COMPLETED");
            MainApplication app = (MainApplication) controller.get().getApplication();
            assertFalse(app.isLocalAudioLeaseHeld());

            Shadows.shadowOf(android.os.Looper.getMainLooper()).idleFor(181, TimeUnit.SECONDS);
            assertFalse(app.getReplyCleanupFaulted());
            assertFalse(app.getSpeechCleanupFaulted());
            assertFalse(app.isLocalAudioLeaseHeld());
            verifyNoMoreInteractions(begun);
            verifyNoMoreInteractions(speaking);
            controller.pause().stop().destroy();
        }
    }

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
            verify(denied).reject(eq("CLEANUP_FAILED"), anyString());
            verify(denied, never()).resolve(any());
            assertEquals(1, sessions.constructed().size());
            recreated.pause().stop().destroy();
        }
    }

    @Test public void beginRejectsLatchedCleanupFaultWithoutPendingSpeechCallback() {
        try (MockedConstruction<DefaultReactActivityDelegate> delegates = mockConstruction(DefaultReactActivityDelegate.class);
             MockedConstruction<SpeechSession> sessions = mockConstruction(SpeechSession.class,
                (session, context) -> {
                    when(session.begin(true)).thenReturn(23L);
                    when(session.isFaulted()).thenReturn(false, true);
                })) {
            ActivityController<MainActivity> controller = Robolectric.buildActivity(MainActivity.class).setup();
            MainActivity activity = controller.get();
            MainApplication app = (MainApplication) activity.getApplication();
            assertTrue(app.getCapturePrivacyReady());
            ReactApplicationContext context = mock(ReactApplicationContext.class);
            when(context.getCurrentActivity()).thenReturn(activity);
            LocalSpeechModule bridge = new LocalSpeechModule(context);
            Promise begun = mock(Promise.class);
            bridge.begin(begun);
            Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
            ArgumentCaptor<Object> begunToken = ArgumentCaptor.forClass(Object.class);
            verify(begun).resolve(begunToken.capture());
            String token = (String) begunToken.getValue();
            assertTrue(token.endsWith(":23"));
            assertFalse(app.getSpeechCleanupFaulted());
            bridge.cancel(token);
            assertTrue(app.getSpeechCleanupFaulted());
            Promise denied = mock(Promise.class);
            bridge.begin(denied);
            Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
            verify(denied).reject(eq("CLEANUP_FAILED"), anyString());
            verify(denied, never()).resolve(any());
            assertEquals(1, sessions.constructed().size());
            controller.pause().stop().destroy();
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

    private static void invokeNoArg(Object target, String method) throws Exception {
        target.getClass().getMethod(method).invoke(target);
    }

    private static void assertBothAudioBeginsRejected(LocalReplyModule reply, LocalSpeechModule speech) {
        Promise deniedReply = mock(Promise.class);
        Promise deniedSpeech = mock(Promise.class);
        reply.begin(deniedReply);
        speech.begin(deniedSpeech);
        Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
        verify(deniedReply).reject(eq("CLEANUP_FAILED"), anyString());
        verify(deniedReply, never()).resolve(any());
        verify(deniedSpeech).reject(eq("CLEANUP_FAILED"), anyString());
        verify(deniedSpeech, never()).resolve(any());
    }
}
