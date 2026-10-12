package com.xmilo_at_your_side.sign_to_speak;

import static org.junit.Assert.*;

import android.app.Application;
import com.facebook.react.bridge.ReactApplicationContext;
import com.facebook.react.bridge.ReactMethod;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.Collections;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = {28, 29, 36}, application = MainApplication.class,
    shadows = {ReactBootstrapShadow.class, CaptureAudioManagerShadow.class})
public class CaptureCapabilityModuleTest {
    @Test public void moduleReportsOnlyNativeCapabilityAndHasNoWritableBridge() throws Exception {
        MainApplication app = (MainApplication) RuntimeEnvironment.getApplication();
        assertTrue(app.getCapturePrivacyReady());
        CapturePrivacyModule module = new CapturePrivacyModule(new ReactApplicationContext(app));
        assertEquals("CapturePrivacy", module.getName());
        assertEquals(Collections.singletonMap("ready", true), module.getConstants());
        for (Method method : module.getClass().getDeclaredMethods()) {
            assertNull("No JS-callable method may change native privacy readiness", method.getAnnotation(ReactMethod.class));
        }
        Field readiness = MainApplication.class.getDeclaredField("capturePrivacyReady");
        readiness.setAccessible(true);
        readiness.setBoolean(app, false);
        assertEquals(Collections.singletonMap("ready", false), module.getConstants());
    }

    @Test @Config(application = Application.class) public void unknownApplicationFailsClosed() {
        CapturePrivacyModule module = new CapturePrivacyModule(
            new ReactApplicationContext(RuntimeEnvironment.getApplication()));
        assertEquals(Collections.singletonMap("ready", false), module.getConstants());
    }
}
