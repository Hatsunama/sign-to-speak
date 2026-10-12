package com.xmilo_at_your_side.sign_to_speak;

import static org.junit.Assert.*;
import static org.mockito.Mockito.*;

import android.app.Application;
import android.content.Context;
import android.media.AudioAttributes;
import android.media.AudioManager;
import android.os.Build;
import com.facebook.react.ReactNativeApplicationEntryPoint;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.mockito.MockedStatic;
import org.mockito.InOrder;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.annotation.Config;

/** Executes production startup; only Android's audio service and native-library bootstrap are mocked. */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = {28, 29, 36}, application = Application.class)
public class CapturePolicyStartupTest {
    @Test public void denyAllCaptureBeforeAnyReactOrPlayerStartup() {
        MainApplication app = spy(new MainApplication());
        AudioManager audio = mock(AudioManager.class);
        doReturn(audio).when(app).getSystemService(Context.AUDIO_SERVICE);
        if (Build.VERSION.SDK_INT >= 29)
            when(audio.getAllowedCapturePolicy()).thenReturn(AudioAttributes.ALLOW_CAPTURE_BY_NONE);
        try (MockedStatic<ReactNativeApplicationEntryPoint> entry = mockStatic(ReactNativeApplicationEntryPoint.class)) {
            entry.when(() -> ReactNativeApplicationEntryPoint.loadReactNative(app)).thenAnswer(call -> {
                if (Build.VERSION.SDK_INT >= 29) {
                    InOrder order = inOrder(audio);
                    order.verify(audio).setAllowedCapturePolicy(AudioAttributes.ALLOW_CAPTURE_BY_NONE);
                    order.verify(audio).getAllowedCapturePolicy();
                } else verifyNoInteractions(audio);
                assertTrue(app.getCapturePrivacyReady());
                return null;
            });
            app.onCreate();
            entry.verify(() -> ReactNativeApplicationEntryPoint.loadReactNative(app));
        }
    }

    @Test @Config(sdk = {29, 36}) public void serviceFailureLatchesUnavailableBeforeReactStartup() {
        MainApplication app = spy(new MainApplication());
        AudioManager audio = mock(AudioManager.class);
        doReturn(audio).when(app).getSystemService(Context.AUDIO_SERVICE);
        doThrow(new IllegalStateException("test service failure")).when(audio)
            .setAllowedCapturePolicy(AudioAttributes.ALLOW_CAPTURE_BY_NONE);
        try (MockedStatic<ReactNativeApplicationEntryPoint> entry = mockStatic(ReactNativeApplicationEntryPoint.class)) {
            entry.when(() -> ReactNativeApplicationEntryPoint.loadReactNative(app)).thenAnswer(call -> {
                assertFalse(app.getCapturePrivacyReady()); return null;
            });
            app.onCreate();
            assertFalse(app.getCapturePrivacyReady());
            entry.verify(() -> ReactNativeApplicationEntryPoint.loadReactNative(app));
        }
    }

    @Test @Config(sdk = {29, 36}) public void missingServiceLatchesUnavailableBeforeReactStartup() {
        MainApplication app = spy(new MainApplication());
        doReturn(null).when(app).getSystemService(Context.AUDIO_SERVICE);
        try (MockedStatic<ReactNativeApplicationEntryPoint> entry = mockStatic(ReactNativeApplicationEntryPoint.class)) {
            entry.when(() -> ReactNativeApplicationEntryPoint.loadReactNative(app)).thenAnswer(call -> {
                assertFalse(app.getCapturePrivacyReady()); return null;
            });
            app.onCreate();
            assertFalse(app.getCapturePrivacyReady());
            entry.verify(() -> ReactNativeApplicationEntryPoint.loadReactNative(app));
        }
    }

    @Test @Config(sdk = {29, 36}) public void rejectedPolicyLatchesUnavailableBeforeReactStartup() {
        for (int policy : new int[] {AudioAttributes.ALLOW_CAPTURE_BY_ALL, AudioAttributes.ALLOW_CAPTURE_BY_SYSTEM}) {
            MainApplication app = spy(new MainApplication());
            AudioManager audio = mock(AudioManager.class);
            doReturn(audio).when(app).getSystemService(Context.AUDIO_SERVICE);
            when(audio.getAllowedCapturePolicy()).thenReturn(policy);
            try (MockedStatic<ReactNativeApplicationEntryPoint> entry = mockStatic(ReactNativeApplicationEntryPoint.class)) {
                entry.when(() -> ReactNativeApplicationEntryPoint.loadReactNative(app)).thenAnswer(call -> {
                    assertFalse(app.getCapturePrivacyReady()); return null;
                });
                app.onCreate();
                assertFalse(app.getCapturePrivacyReady());
                entry.verify(() -> ReactNativeApplicationEntryPoint.loadReactNative(app));
            }
        }
    }

    @Test @Config(sdk = {29, 36}) public void readbackFailureAlsoLatchesUnavailable() {
        MainApplication app = spy(new MainApplication());
        assertFalse("A fresh Application starts unavailable", app.getCapturePrivacyReady());
        AudioManager audio = mock(AudioManager.class);
        doReturn(audio).when(app).getSystemService(Context.AUDIO_SERVICE);
        when(audio.getAllowedCapturePolicy()).thenThrow(new IllegalStateException("test readback failure"));
        try (MockedStatic<ReactNativeApplicationEntryPoint> entry = mockStatic(ReactNativeApplicationEntryPoint.class)) {
            app.onCreate();
            assertFalse(app.getCapturePrivacyReady());
            verify(audio).setAllowedCapturePolicy(AudioAttributes.ALLOW_CAPTURE_BY_NONE);
            entry.verify(() -> ReactNativeApplicationEntryPoint.loadReactNative(app));
        }
    }
}
