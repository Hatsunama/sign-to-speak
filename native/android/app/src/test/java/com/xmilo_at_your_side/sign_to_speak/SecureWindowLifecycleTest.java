package com.xmilo_at_your_side.sign_to_speak;

import static org.junit.Assert.*;
import static org.mockito.Mockito.*;

import android.app.Application;
import android.content.ContextWrapper;
import android.content.Intent;
import android.os.Bundle;
import android.os.Looper;
import org.robolectric.Shadows;
import android.view.WindowManager;
import com.facebook.react.bridge.ReactApplicationContext;
import com.facebook.react.bridge.queue.MessageQueueThread;
import com.facebook.react.bridge.queue.ReactQueueConfiguration;
import com.facebook.react.defaults.DefaultReactActivityDelegate;
import com.facebook.react.uimanager.ThemedReactContext;
import com.facebook.react.views.modal.ReactModalHostView;
import com.facebook.react.uimanager.DisplayMetricsHolder;
import com.facebook.react.internal.featureflags.ReactNativeFeatureFlags;
import org.mockito.MockedStatic;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.mockito.MockedConstruction;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.android.controller.ActivityController;
import org.robolectric.annotation.Config;
import org.robolectric.annotation.LooperMode;

/** Window flags only. This is deliberately not a compositor/screenshot or audio-loopback test. */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = {28, 29, 36}, application = MainApplication.class, shadows = {ReactBootstrapShadow.class, CaptureAudioManagerShadow.class})
@LooperMode(LooperMode.Mode.PAUSED)
public class SecureWindowLifecycleTest {
    private MockedConstruction<DefaultReactActivityDelegate> delegates;
    private ActivityController<MainActivity> controller;
    private MainActivity activity;

    @Before public void setUp() {
        delegates = mockConstruction(DefaultReactActivityDelegate.class, (delegate, context) -> {
            MainActivity owner = (MainActivity) context.arguments().get(0);
            doAnswer(call -> { assertSecure(owner); return null; }).when(delegate).onCreate(any());
        });
        controller = Robolectric.buildActivity(MainActivity.class).setup().visible();
        activity = controller.get();
    }

    @After public void tearDown() {
        if (controller != null) controller.pause().stop().destroy();
        if (delegates != null) delegates.close();
    }

    @Test public void secureBeforeReactContentAndAcrossLifecycle() {
        assertSecure(activity);
        controller.pause().stop();
        assertSecure(activity);
        controller.restart().start().resume();
        assertSecure(activity);
        activity.onNewIntent(new Intent(activity, MainActivity.class));
        assertSecure(activity);
        controller.recreate();
        activity = controller.get();
        assertSecure(activity);
    }

    @Test public void actualPinnedReactModalCopiesSecureFlagBeforeShowAndAfterRecreation() {
        ReactApplicationContext react = mock(ReactApplicationContext.class);
        when(react.getCurrentActivity()).thenReturn(activity);
        ThemedReactContext themed = new ThemedReactContext(react, new ContextWrapper(activity), "PrivacyModal", 1);
        // Supply only the RN scheduling boundary; keep the real modal, layout and windows.
        // The modal's layout callback otherwise needs an unstarted Hermes/native-module queue.
        MessageQueueThread queue = mock(MessageQueueThread.class);
        doAnswer(call -> { ((Runnable) call.getArgument(0)).run(); return true; })
            .when(queue).runOnQueue(any(Runnable.class));
        ReactQueueConfiguration queues = mock(ReactQueueConfiguration.class);
        when(queues.getUIQueueThread()).thenReturn(queue);
        when(queues.getNativeModulesQueueThread()).thenReturn(queue);
        when(queues.getJSQueueThread()).thenReturn(queue);
        themed.initializeMessageQueueThreads(queues);
        DisplayMetricsHolder.initDisplayMetricsIfNotInitialized(activity);
        // Replace unrelated JNI feature-flag access, not Dialog/window security behavior.
        try (MockedStatic<ReactNativeFeatureFlags> flags = mockStatic(ReactNativeFeatureFlags.class)) {
        ReactModalHostView modal = new ReactModalHostView(themed);
        final int[] shown = {0};
        modal.setOnShowListener(dialog -> {
            assertTrue((((android.app.Dialog) dialog).getWindow().getAttributes().flags & WindowManager.LayoutParams.FLAG_SECURE) != 0);
            shown[0]++;
        });
        try {
            modal.showOrUpdate();
            assertNotNull(modal.getDialog());
            assertTrue(modal.getDialog().isShowing());
            assertTrue((modal.getDialog().getWindow().getAttributes().flags & WindowManager.LayoutParams.FLAG_SECURE) != 0);
            modal.setAnimationType("fade");
            modal.showOrUpdate();
            assertTrue(modal.getDialog().isShowing());
            assertTrue((modal.getDialog().getWindow().getAttributes().flags & WindowManager.LayoutParams.FLAG_SECURE) != 0);
            Shadows.shadowOf(Looper.getMainLooper()).idle();
            assertEquals("Both dialog creations must deliver the secured show event", 2, shown[0]);
        } finally { modal.onDropInstance(); }
        }
    }

    private static void assertSecure(MainActivity activity) {
        assertTrue("Every app content window must be secure", (activity.getWindow().getAttributes().flags & WindowManager.LayoutParams.FLAG_SECURE) != 0);
    }
}
