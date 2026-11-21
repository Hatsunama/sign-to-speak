package com.xmilo_at_your_side.sign_to_speak;

import static org.junit.Assert.*;
import static org.mockito.Mockito.*;

import android.Manifest;
import com.facebook.react.bridge.Arguments;
import com.facebook.react.bridge.JavaOnlyMap;
import com.facebook.react.bridge.Promise;
import com.facebook.react.bridge.ReactApplicationContext;
import com.facebook.react.bridge.ReadableMap;
import com.facebook.react.defaults.DefaultReactActivityDelegate;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.mockito.ArgumentCaptor;
import org.mockito.MockedConstruction;
import org.mockito.MockedStatic;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.Shadows;
import org.robolectric.android.controller.ActivityController;
import org.robolectric.annotation.Config;

/** Native reply authority tests; the actual worker is covered by ReplySessionTest. */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 36, application = MainApplication.class,
    shadows = {ReactBootstrapShadow.class, CaptureAudioManagerShadow.class})
public class ReplyLifecycleTest {
  @Test @Config(sdk = 29, application = MainApplication.class,
      shadows = {ReactBootstrapShadow.class, CaptureAudioManagerShadow.class})
  public void api29FailsClosedWithoutPrivacySensitiveAudioRecord() {
    Shadows.shadowOf(RuntimeEnvironment.getApplication()).grantPermissions(Manifest.permission.RECORD_AUDIO);
    try (MockedConstruction<DefaultReactActivityDelegate> delegates = mockConstruction(DefaultReactActivityDelegate.class);
         MockedConstruction<ReplySession> sessions = mockConstruction(ReplySession.class)) {
      ActivityController<MainActivity> controller = Robolectric.buildActivity(MainActivity.class).setup();
      ReactApplicationContext context = mock(ReactApplicationContext.class);
      when(context.getCurrentActivity()).thenReturn(controller.get());
      Promise denied = mock(Promise.class);
      new LocalReplyModule(context).begin(denied);
      Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
      verify(denied).resolve(null);
      assertTrue(sessions.constructed().isEmpty());
      controller.pause().stop().destroy();
    }
  }

  @Test public void permissionIsRequiredBeforeWorkerConstruction() {
    try (MockedConstruction<DefaultReactActivityDelegate> delegates = mockConstruction(DefaultReactActivityDelegate.class);
         MockedConstruction<ReplySession> sessions = mockConstruction(ReplySession.class)) {
      ActivityController<MainActivity> controller = Robolectric.buildActivity(MainActivity.class).setup();
      ReactApplicationContext context = mock(ReactApplicationContext.class);
      when(context.getCurrentActivity()).thenReturn(controller.get());
      Promise denied = mock(Promise.class);
      new LocalReplyModule(context).begin(denied);
      Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
      verify(denied).resolve(null);
      assertTrue(sessions.constructed().isEmpty());
      controller.pause().stop().destroy();
    }
  }

  @Test public void backPauseAndDestroyCancelBeforeReactAndNeverAutoResume() {
    Shadows.shadowOf(RuntimeEnvironment.getApplication()).grantPermissions(Manifest.permission.RECORD_AUDIO);
    AtomicReference<ReplySession.Completion> completion = new AtomicReference<>();
    try (MockedConstruction<DefaultReactActivityDelegate> delegates = mockConstruction(
             DefaultReactActivityDelegate.class,
             (delegate, ignored) -> when(delegate.onBackPressed()).thenReturn(true));
         MockedConstruction<ReplySession> sessions = mockConstruction(ReplySession.class,
             (session, ignored) -> when(session.begin(eq(true), any(), any())).thenAnswer(call -> {
               completion.set(call.getArgument(2));
               ((ReplySession.Started) call.getArgument(1)).complete(true);
               return 17L;
             }))) {
      ActivityController<MainActivity> controller = Robolectric.buildActivity(MainActivity.class).setup();
      MainActivity activity = controller.get();
      ReactApplicationContext context = mock(ReactApplicationContext.class);
      when(context.getCurrentActivity()).thenReturn(activity);
      LocalReplyModule bridge = new LocalReplyModule(context);
      Promise begun = mock(Promise.class);
      bridge.begin(begun);
      Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
      ArgumentCaptor<Object> token = ArgumentCaptor.forClass(Object.class);
      verify(begun).resolve(token.capture());
      assertTrue(((String) token.getValue()).endsWith(":17"));
      ReplySession session = sessions.constructed().get(0);
      activity.onBackPressed();
      verify(session).cancel(17L);
      // Cancellation alone cannot release the process audio lease; worker cleanup does.
      assertTrue(((MainApplication) activity.getApplication()).isLocalAudioLeaseHeld());
      completion.get().complete(ReplySession.Outcome.status(ReplySession.Status.CANCELLED));
      assertFalse(((MainApplication) activity.getApplication()).isLocalAudioLeaseHeld());
      clearInvocations(session);
      controller.pause().resume();
      verify(session, never()).begin(eq(true), any(), any());
      controller.pause().stop().destroy();
      verify(session).close();
    }
  }

  @Test public void backSettlesAnInflightFinishAsCancelled() {
    Shadows.shadowOf(RuntimeEnvironment.getApplication()).grantPermissions(Manifest.permission.RECORD_AUDIO);
    AtomicReference<ReplySession.Completion> completion = new AtomicReference<>();
    try (MockedConstruction<DefaultReactActivityDelegate> delegates = mockConstruction(
             DefaultReactActivityDelegate.class,
             (delegate, ignored) -> when(delegate.onBackPressed()).thenReturn(true));
         MockedConstruction<ReplySession> sessions = mockConstruction(ReplySession.class,
             (session, ignored) -> {
               when(session.begin(eq(true), any(), any())).thenAnswer(call -> {
                 completion.set(call.getArgument(2));
                 ((ReplySession.Started) call.getArgument(1)).complete(true);
                 return 23L;
               });
               when(session.finish(23L)).thenReturn(true);
             })) {
      ActivityController<MainActivity> controller = Robolectric.buildActivity(MainActivity.class).setup();
      MainActivity activity = controller.get();
      ReactApplicationContext context = mock(ReactApplicationContext.class);
      when(context.getCurrentActivity()).thenReturn(activity);
      LocalReplyModule bridge = new LocalReplyModule(context);
      Promise begun = mock(Promise.class);
      bridge.begin(begun);
      Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
      ArgumentCaptor<Object> token = ArgumentCaptor.forClass(Object.class);
      verify(begun).resolve(token.capture());
      Promise finished = mock(Promise.class);
      try (MockedStatic<Arguments> arguments = mockArgumentsMaps()) {
        bridge.finish((String) token.getValue(), finished);
        activity.onBackPressed();
        verify(finished, never()).resolve(any());
        verify(sessions.constructed().get(0)).cancel(23L);
        completion.get().complete(ReplySession.Outcome.status(ReplySession.Status.CANCELLED));
        assertResolvedResult(finished, "CANCELLED", null);
      }
      controller.pause().stop().destroy();
    }
  }

  @Test public void finishAfterEligibilityLossCancelsActiveCapture() {
    Shadows.shadowOf(RuntimeEnvironment.getApplication()).grantPermissions(Manifest.permission.RECORD_AUDIO);
    try (MockedConstruction<DefaultReactActivityDelegate> delegates = mockConstruction(DefaultReactActivityDelegate.class);
         MockedConstruction<ReplySession> sessions = mockConstruction(ReplySession.class,
             (session, ignored) -> when(session.begin(eq(true), any(), any())).thenAnswer(call -> {
               ((ReplySession.Started) call.getArgument(1)).complete(true);
               return 29L;
             }))) {
      ActivityController<MainActivity> controller = Robolectric.buildActivity(MainActivity.class).setup();
      MainActivity activity = controller.get();
      ReactApplicationContext context = mock(ReactApplicationContext.class);
      when(context.getCurrentActivity()).thenReturn(activity);
      LocalReplyModule bridge = new LocalReplyModule(context);
      Promise begun = mock(Promise.class);
      bridge.begin(begun);
      Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
      ArgumentCaptor<Object> token = ArgumentCaptor.forClass(Object.class);
      verify(begun).resolve(token.capture());

      Shadows.shadowOf(RuntimeEnvironment.getApplication()).denyPermissions(Manifest.permission.RECORD_AUDIO);
      Promise finished = mock(Promise.class);
      try (MockedStatic<Arguments> arguments = mockArgumentsMaps()) {
        bridge.finish((String) token.getValue(), finished);
        verify(sessions.constructed().get(0)).cancel(29L);
        assertResolvedResult(finished, "UNAVAILABLE", null);
      }
      controller.pause().stop().destroy();
    }
  }

  @Test public void finishSurfacesLatchedCleanupFailure() {
    Shadows.shadowOf(RuntimeEnvironment.getApplication()).grantPermissions(Manifest.permission.RECORD_AUDIO);
    AtomicReference<ReplySession.Completion> completion = new AtomicReference<>();
    try (MockedConstruction<DefaultReactActivityDelegate> delegates = mockConstruction(DefaultReactActivityDelegate.class);
         MockedConstruction<ReplySession> sessions = mockConstruction(ReplySession.class,
             (session, ignored) -> when(session.begin(eq(true), any(), any())).thenAnswer(call -> {
               completion.set(call.getArgument(2));
               ((ReplySession.Started) call.getArgument(1)).complete(true);
               return 31L;
             }))) {
      ActivityController<MainActivity> controller = Robolectric.buildActivity(MainActivity.class).setup();
      MainActivity activity = controller.get();
      ReactApplicationContext context = mock(ReactApplicationContext.class);
      when(context.getCurrentActivity()).thenReturn(activity);
      LocalReplyModule bridge = new LocalReplyModule(context);
      Promise begun = mock(Promise.class);
      bridge.begin(begun);
      Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
      ArgumentCaptor<Object> token = ArgumentCaptor.forClass(Object.class);
      verify(begun).resolve(token.capture());
      completion.get().complete(ReplySession.Outcome.status(ReplySession.Status.CLEANUP_FAILED));

      Promise finished = mock(Promise.class);
      try (MockedStatic<Arguments> arguments = mockArgumentsMaps()) {
        bridge.finish((String) token.getValue(), finished);
        assertResolvedResult(finished, "CLEANUP_FAILED", null);
      }
      verify(sessions.constructed().get(0), never()).finish(anyLong());
      controller.pause().stop().destroy();
    }
  }

  @Test public void staleActivityIdentityCannotStartReply() {
    Shadows.shadowOf(RuntimeEnvironment.getApplication()).grantPermissions(Manifest.permission.RECORD_AUDIO);
    try (MockedConstruction<DefaultReactActivityDelegate> delegates = mockConstruction(DefaultReactActivityDelegate.class);
         MockedConstruction<ReplySession> sessions = mockConstruction(ReplySession.class)) {
      ActivityController<MainActivity> first = Robolectric.buildActivity(MainActivity.class).setup();
      ActivityController<MainActivity> second = Robolectric.buildActivity(MainActivity.class).setup();
      ReactApplicationContext context = mock(ReactApplicationContext.class);
      when(context.getCurrentActivity()).thenReturn(first.get(), second.get());
      Promise promise = mock(Promise.class);
      new LocalReplyModule(context).begin(promise);
      Shadows.shadowOf(android.os.Looper.getMainLooper()).idle();
      verify(promise).resolve(null);
      assertTrue(sessions.constructed().isEmpty());
      first.pause().stop().destroy(); second.pause().stop().destroy();
    }
  }

  /** Robolectric has no React Native JNI runtime, so keep result assertions on a Java test map. */
  private static MockedStatic<Arguments> mockArgumentsMaps() {
    MockedStatic<Arguments> arguments = mockStatic(Arguments.class);
    arguments.when(Arguments::createMap).thenAnswer(ignored -> new JavaOnlyMap());
    return arguments;
  }

  private static void assertResolvedResult(Promise promise, String status, String text) {
    ArgumentCaptor<Object> result = ArgumentCaptor.forClass(Object.class);
    verify(promise).resolve(result.capture());
    ReadableMap map = (ReadableMap) result.getValue();
    assertEquals(status, map.getString("status"));
    assertEquals(text != null, map.hasKey("text"));
    if (text != null) assertEquals(text, map.getString("text"));
  }
}
