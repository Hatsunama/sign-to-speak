package com.xmilo_at_your_side.sign_to_speak;

import static org.junit.Assert.*;
import static org.mockito.Mockito.*;

import android.app.Application;
import android.content.ContextWrapper;
import android.content.Intent;
import android.os.Bundle;
import android.view.WindowManager;
import com.facebook.react.bridge.ReactApplicationContext;
import com.facebook.react.defaults.DefaultReactActivityDelegate;
import com.facebook.react.uimanager.ThemedReactContext;
import com.facebook.react.views.modal.ReactModalHostView;
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
@Config(sdk = {28, 29, 36}, application = Application.class)
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

    private static void assertSecure(MainActivity activity) {
        assertTrue("Every app content window must be secure", (activity.getWindow().getAttributes().flags & WindowManager.LayoutParams.FLAG_SECURE) != 0);
    }
}
