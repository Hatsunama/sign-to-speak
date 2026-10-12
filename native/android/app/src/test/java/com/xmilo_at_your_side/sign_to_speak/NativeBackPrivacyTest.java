package com.xmilo_at_your_side.sign_to_speak;

import static org.junit.Assert.*;
import static org.mockito.Mockito.*;

import android.Manifest;
import android.app.Application;
import android.os.Looper;
import android.view.View;
import android.widget.FrameLayout;
import androidx.camera.core.CameraSelector;
import androidx.camera.core.Preview;
import androidx.camera.lifecycle.ProcessCameraProvider;
import androidx.camera.view.PreviewView;
import androidx.lifecycle.Lifecycle;
import com.facebook.react.bridge.ReactApplicationContext;
import com.facebook.react.defaults.DefaultReactActivityDelegate;
import com.facebook.react.uimanager.ThemedReactContext;
import com.google.common.util.concurrent.SettableFuture;
import java.lang.reflect.Field;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.mockito.MockedConstruction;
import org.mockito.MockedStatic;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.Shadows;
import org.robolectric.android.controller.ActivityController;
import org.robolectric.annotation.Config;
import org.robolectric.annotation.LooperMode;

/**
 * Runs the production Activity and camera View on Android's Robolectric runtime.
 * Only React's delegate/JS boundary and the hardware camera provider are replaced.
 * The delegate consumes Back without navigating or pausing: this models stalled JS.
 * This is not a physical-camera, Hermes, or end-to-end predictive-gesture test.
 */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = {28, 36}, application = Application.class)
@LooperMode(LooperMode.Mode.PAUSED)
public class NativeBackPrivacyTest {
    private MockedConstruction<DefaultReactActivityDelegate> delegates;
    private MockedStatic<ProcessCameraProvider> providerFactory;
    private ActivityController<MainActivity> controller;
    private MainActivity activity;
    private ReactApplicationContext reactContext;
    private FrameLayout root;
    private PrivateCameraView view;
    private ProcessCameraProvider provider;
    private SettableFuture<ProcessCameraProvider> future;

    @Before public void setUp() throws Exception {
        // Do not bootstrap Hermes or load the app's native libraries in a JVM test.
        // This mock is below MainActivity's production Back path, not above it.
        delegates = mockConstruction(DefaultReactActivityDelegate.class,
            (delegate, context) -> when(delegate.onBackPressed()).thenReturn(true));
        controller = Robolectric.buildActivity(MainActivity.class).setup().visible();
        activity = controller.get();
        assertEquals(36, activity.getApplicationInfo().targetSdkVersion);
        assertTrue(activity.getLifecycle().getCurrentState().isAtLeast(Lifecycle.State.RESUMED));
        Shadows.shadowOf(RuntimeEnvironment.getApplication()).grantPermissions(Manifest.permission.CAMERA);
        reactContext = mock(ReactApplicationContext.class);
        when(reactContext.getCurrentActivity()).thenReturn(activity);
        root = new FrameLayout(activity);
        activity.setContentView(root);
        view = new PrivateCameraView(new ThemedReactContext(reactContext, activity, "BackRegression", 1));
        root.addView(view, new FrameLayout.LayoutParams(320, 240));
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertTrue("Fixture must attach the actual native view", view.isAttachedToWindow());
        provider = mock(ProcessCameraProvider.class);
        when(provider.hasCamera(CameraSelector.DEFAULT_FRONT_CAMERA)).thenReturn(true);
        future = SettableFuture.create();
        providerFactory = mockStatic(ProcessCameraProvider.class);
        providerFactory.when(() -> ProcessCameraProvider.getInstance(view.getContext())).thenReturn(future);
    }

    @After public void tearDown() {
        if (view != null) view.dispose();
        if (controller != null) controller.pause().stop().destroy();
        if (providerFactory != null) providerFactory.close();
        if (delegates != null) delegates.close();
    }

    @Test public void backInvalidatesPendingProviderBeforeReactNavigation() throws Exception {
        view.setActive(true);
        assertTrue("Precondition: the provider request really is pending", requested());
        assertFalse(future.isDone());
        assertNull(ownedPreview());
        doAnswer(call -> {
            assertFalse("Native Back must cancel before RN receives Back, even when JS stalls", requested());
            return true;
        }).when(delegate()).onBackPressed();
        committedBack();
        future.set(provider);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertStopped();
        verifyNoInteractions(provider);
        assertTrue("RN consumed Back; passing must not rely on Activity pause",
            activity.getLifecycle().getCurrentState().isAtLeast(Lifecycle.State.RESUMED));
    }

    @Test public void backUnbindsOwnedPreviewBeforeReactNavigation() throws Exception {
        Preview owned = startBoundPreview();
        doAnswer(call -> {
            assertStopped();
            verify(provider).unbind(owned);
            return true;
        }).when(delegate()).onBackPressed();
        committedBack();
        verify(delegate()).onBackPressed();
        verify(provider, never()).unbindAll();
        assertTrue(activity.getLifecycle().getCurrentState().isAtLeast(Lifecycle.State.RESUMED));
    }

    @Test public void repeatedBackIsIdempotentAndResumeDoesNotRestart() throws Exception {
        Preview owned = startBoundPreview();
        committedBack();
        committedBack();
        view.onHostResume();
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertStopped();
        verify(provider, times(1)).unbind(owned);
        providerFactory.verify(() -> ProcessCameraProvider.getInstance(view.getContext()), times(1));
    }

    @Test public void backWithNoCameraStillReachesReact() {
        committedBack();
        committedBack();
        verify(delegate(), times(2)).onBackPressed();
        verifyNoInteractions(provider);
    }

    @Test public void hostPauseCancelsPendingAcquisitionAndResumeDoesNotRestart() throws Exception {
        view.setActive(true);
        view.onHostPause();
        future.set(provider);
        view.onHostResume();
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertStopped();
        verifyNoInteractions(provider);
    }

    @Test public void hostDestroyCancelsPendingAcquisitionAndRejectsRestart() throws Exception {
        view.setActive(true);
        view.onHostDestroy();
        future.set(provider);
        view.setActive(true);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertStopped();
        verifyNoInteractions(provider);
        verify(reactContext, times(1)).removeLifecycleEventListener(view);
    }

    @Test public void detachUnbindsAndReattachDoesNotRestart() throws Exception {
        Preview owned = startBoundPreview();
        root.removeView(view);
        root.addView(view);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertStopped();
        verify(provider, times(1)).unbind(owned);
        providerFactory.verify(() -> ProcessCameraProvider.getInstance(view.getContext()), times(1));
    }

    @Test public void disposalIsIdempotentAndRejectsFutureStarts() throws Exception {
        Preview owned = startBoundPreview();
        view.dispose();
        view.dispose();
        view.setActive(true);
        view.onHostResume();
        assertStopped();
        verify(provider, times(1)).unbind(owned);
        verify(reactContext, times(1)).removeLifecycleEventListener(view);
    }

    private void committedBack() {
        if (android.os.Build.VERSION.SDK_INT >= 36) {
            // RN 0.86.3 registers its real OnBackPressedCallback on targetSdk 36.
            // This exercises the committed predictive-Back dispatcher route.
            activity.getOnBackPressedDispatcher().onBackPressed();
        } else {
            activity.onBackPressed();
        }
    }

    private DefaultReactActivityDelegate delegate() {
        assertEquals("Fixture must use the real Activity's constructed delegate", 1, delegates.constructed().size());
        return delegates.constructed().get(0);
    }

    private Preview startBoundPreview() throws Exception {
        view.setActive(true);
        future.set(provider);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        Preview owned = ownedPreview();
        assertNotNull("Precondition: actual start() must bind a Preview", owned);
        verify(provider).bindToLifecycle(activity, CameraSelector.DEFAULT_FRONT_CAMERA, owned);
        assertEquals(View.VISIBLE, previewView().getVisibility());
        return owned;
    }

    private void assertStopped() throws Exception {
        assertFalse("Native camera request must be canceled synchronously", requested());
        assertNull("Owned preview reference must be cleared", ownedPreview());
        assertEquals("Last preview must immediately be hidden", View.INVISIBLE, previewView().getVisibility());
    }

    // Observe the production view without adding test-only APIs to shipping code.
    private Object field(String name) throws Exception {
        Field field = PrivateCameraView.class.getDeclaredField(name);
        field.setAccessible(true);
        return field.get(view);
    }
    private boolean requested() throws Exception { return (Boolean) field("requested"); }
    private Preview ownedPreview() throws Exception { return (Preview) field("preview"); }
    private PreviewView previewView() throws Exception { return (PreviewView) field("previewView"); }
}
