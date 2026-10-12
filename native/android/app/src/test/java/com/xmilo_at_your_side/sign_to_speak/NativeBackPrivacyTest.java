package com.xmilo_at_your_side.sign_to_speak;

import static org.junit.Assert.*;
import static org.mockito.Mockito.*;

import android.Manifest;
import android.app.Application;
import android.content.Context;
import android.content.ContextWrapper;
import java.util.Set;
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
import com.google.common.util.concurrent.ListenableFuture;
import java.lang.reflect.Field;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.mockito.MockedConstruction;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.Shadows;
import org.robolectric.android.controller.ActivityController;
import org.robolectric.annotation.Config;
import org.robolectric.annotation.LooperMode;
import org.robolectric.annotation.Implementation;
import org.robolectric.annotation.Implements;

/**
 * Runs the production Activity and camera View on Android's Robolectric runtime.
 * Only React's delegate/JS boundary and the hardware camera provider are replaced.
 * The delegate consumes Back without navigating or pausing: this models stalled JS.
 * This is not a physical-camera, Hermes, or end-to-end predictive-gesture test.
 */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = {28, 36}, application = Application.class,
    shadows = NativeBackPrivacyTest.ProviderCompanionShadow.class)
@LooperMode(LooperMode.Mode.PAUSED)
public class NativeBackPrivacyTest {
    private MockedConstruction<DefaultReactActivityDelegate> delegates;
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
        view = new PrivateCameraView(new ThemedReactContext(reactContext, new ContextWrapper(activity), "BackRegression", 1));
        root.addView(view, new FrameLayout.LayoutParams(320, 240));
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertTrue("Fixture must attach the actual native view", view.isAttachedToWindow());
        provider = mock(ProcessCameraProvider.class);
        when(provider.hasCamera(CameraSelector.DEFAULT_FRONT_CAMERA)).thenReturn(true);
        future = SettableFuture.create();
        ProviderCompanionShadow.nextFuture = future;
        ProviderCompanionShadow.acquisitionCount = 0;
    }

    @After public void tearDown() {
        if (view != null) view.dispose();
        if (controller != null) controller.pause().stop().destroy();
        if (delegates != null) delegates.close();
    }

    @Test public void backInvalidatesPendingProviderBeforeReactNavigation() throws Exception {
        view.setActive(true);
        assertTrue("Precondition: the provider request really is pending", requested());
        assertEquals("Actual Kotlin acquisition must use the controlled future", 1,
            ProviderCompanionShadow.acquisitionCount);
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
        assertEquals("No new provider acquisition without explicit Start", 1,
            ProviderCompanionShadow.acquisitionCount);
    }

    @Test public void backWithNoCameraStillReachesReact() {
        committedBack();
        committedBack();
        verify(delegate(), times(2)).onBackPressed();
        verifyNoInteractions(provider);
    }

    @Test public void backBeforeAttachmentCancelsQueuedStart() throws Exception {
        root.removeView(view);
        view.setActive(true);
        assertTrue(requested());
        assertEquals(0, ProviderCompanionShadow.acquisitionCount);
        committedBack();
        root.addView(view);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertStopped();
        assertEquals("Attachment must not restart the canceled request", 0,
            ProviderCompanionShadow.acquisitionCount);
    }

    @Test public void separateActivitiesCannotCancelEachOthersViews() throws Exception {
        ActivityController<MainActivity> otherController = Robolectric.buildActivity(MainActivity.class).setup().visible();
        MainActivity otherActivity = otherController.get();
        ReactApplicationContext otherContext = mock(ReactApplicationContext.class);
        when(otherContext.getCurrentActivity()).thenReturn(otherActivity);
        PrivateCameraView otherView = new PrivateCameraView(
            new ThemedReactContext(otherContext, new ContextWrapper(otherActivity), "OtherActivity", 2));
        try {
            view.setActive(true);
            otherView.setActive(true); // Intentionally not attached yet.
            assertTrue(requested());
            assertTrue((Boolean) field(otherView, "requested"));
            assertTrue(registeredViews(activity).contains(view));
            assertFalse(registeredViews(activity).contains(otherView));
            assertTrue(registeredViews(otherActivity).contains(otherView));
            assertFalse(registeredViews(otherActivity).contains(view));
            committedBack(otherActivity);
            assertFalse((Boolean) field(otherView, "requested"));
            assertTrue("Another Activity's Back must not cancel this session", requested());
            committedBack();
            assertStopped();
        } finally {
            otherView.dispose();
            otherController.pause().stop().destroy();
        }
    }

    @Test public void actualActivityDestroyCancelsPendingWithoutReactLifecycleDelivery() throws Exception {
        view.setActive(true);
        assertEquals(1, ProviderCompanionShadow.acquisitionCount);
        controller.pause().stop().destroy();
        controller = null;
        future.set(provider);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertStopped();
        verifyNoInteractions(provider);
    }

    @Test public void cleanupFailureOnBackStillCancelsEveryViewAndDelegates() throws Exception {
        Preview first = startBoundPreview();
        PrivateCameraView secondView = secondBoundView();
        Preview second = (Preview) field(secondView, "preview");
        long firstToken = leaseToken(view);
        long secondToken = leaseToken(secondView);
        doThrow(new UnsupportedOperationException("Injected CameraX cleanup failure")).when(provider).unbind(first);
        doThrow(new UnsupportedOperationException("Injected CameraX cleanup failure")).when(provider).unbind(second);
        RuntimeException escaped = null;
        try { committedBack(); } catch (RuntimeException error) { escaped = error; }
        assertNull("Back must isolate each camera cleanup failure before delegating", escaped);
        assertNativeCanceled(view, firstToken);
        assertNativeCanceled(secondView, secondToken);
        verify(provider).unbind(first);
        verify(provider).unbind(second);
        verify(delegate()).onBackPressed();
        assertEquals("Both failures must be accounted for", 2, cleanupFailures());
        view.setActive(true);
        secondView.setActive(true);
        assertFalse(requested());
        assertFalse((Boolean) field(secondView, "requested"));
        assertEquals("Cleanup failure must block new native acquisition", 2,
            ProviderCompanionShadow.acquisitionCount);
        secondView.dispose();
        assertFalse(registeredViews(activity).contains(secondView));
    }

    @Test public void cleanupFailureOnDestroyStillUnregistersEveryViewAndDelegates() throws Exception {
        Preview first = startBoundPreview();
        PrivateCameraView secondView = secondBoundView();
        Preview second = (Preview) field(secondView, "preview");
        long firstToken = leaseToken(view);
        long secondToken = leaseToken(secondView);
        doThrow(new UnsupportedOperationException("Injected CameraX cleanup failure")).when(provider).unbind(first);
        doThrow(new UnsupportedOperationException("Injected CameraX cleanup failure")).when(provider).unbind(second);
        RuntimeException escaped = null;
        try { controller.pause().stop().destroy(); } catch (RuntimeException error) { escaped = error; }
        controller = null;
        assertNull("Activity destruction must isolate each camera cleanup failure", escaped);
        assertNativeCanceled(view, firstToken);
        assertNativeCanceled(secondView, secondToken);
        verify(provider).unbind(first);
        verify(provider).unbind(second);
        verify(delegate()).onDestroy();
        assertEquals(2, cleanupFailures());
        assertTrue("Disposed views must unregister even when their stop threw", registeredViews(activity).isEmpty());
        assertFalse((Boolean) field(view, "observing"));
        assertFalse((Boolean) field(secondView, "observing"));
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
        assertEquals("No new provider acquisition without explicit Start", 1,
            ProviderCompanionShadow.acquisitionCount);
    }

    @Test public void disposalIsIdempotentAndRejectsFutureStarts() throws Exception {
        Preview owned = startBoundPreview();
        assertTrue(registeredViews(activity).contains(view));
        view.dispose();
        view.dispose();
        assertFalse("Disposal unregisters the view from its owning Activity", registeredViews(activity).contains(view));
        view.setActive(true);
        view.onHostResume();
        assertStopped();
        verify(provider, times(1)).unbind(owned);
        verify(reactContext, times(1)).removeLifecycleEventListener(view);
    }

    private void committedBack() { committedBack(activity); }

    private void committedBack(MainActivity activity) {
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

    private PrivateCameraView secondBoundView() throws Exception {
        PrivateCameraView second = new PrivateCameraView(
            new ThemedReactContext(reactContext, new ContextWrapper(activity), "SecondView", 3));
        root.addView(second, new FrameLayout.LayoutParams(320, 240));
        second.setActive(true);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        Preview owned = (Preview) field(second, "preview");
        assertNotNull("Precondition: second view must really bind before fault injection", owned);
        verify(provider).bindToLifecycle(activity, CameraSelector.DEFAULT_FRONT_CAMERA, owned);
        assertEquals(2, ProviderCompanionShadow.acquisitionCount);
        return second;
    }

    private long leaseToken(PrivateCameraView target) throws Exception {
        PreviewLease lease = (PreviewLease) field(target, "lease");
        Field generation = PreviewLease.class.getDeclaredField("generation");
        generation.setAccessible(true);
        long token = generation.getLong(lease);
        assertTrue("Precondition: owned lease must be current before fault injection", lease.isCurrent(token));
        return token;
    }

    private void assertNativeCanceled(PrivateCameraView target, long token) throws Exception {
        assertFalse((Boolean) field(target, "requested"));
        assertEquals(View.INVISIBLE, ((PreviewView) field(target, "previewView")).getVisibility());
        assertFalse(((PreviewLease) field(target, "lease")).isCurrent(token));
        // An unbind attempt that throws is deliberately not claimed as hardware release.
    }

    private int cleanupFailures() throws Exception {
        Field failures = MainActivity.class.getDeclaredField("cameraCleanupFailures");
        failures.setAccessible(true);
        return failures.getInt(activity);
    }

    private Preview startBoundPreview() throws Exception {
        view.setActive(true);
        assertEquals("Actual Kotlin acquisition must use the controlled future", 1,
            ProviderCompanionShadow.acquisitionCount);
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

    /**
     * CameraX 1.4.2 declares getInstance on its Kotlin companion with @JvmStatic.
     * Kotlin callers invoke the companion instance, bypassing a Mockito mock of
     * the Java static bridge. Shadow the actual companion boundary so the real
     * native start callback receives this test's controllable future.
     */
    @Implements(value = ProcessCameraProvider.Companion.class, isInAndroidSdk = false)
    public static class ProviderCompanionShadow {
        static SettableFuture<ProcessCameraProvider> nextFuture;
        static int acquisitionCount;

        @Implementation protected ListenableFuture<ProcessCameraProvider> getInstance(Context context) {
            acquisitionCount++;
            assertNotNull("Controlled provider must be initialized before native Start", nextFuture);
            return nextFuture;
        }
    }

    // Observe the production view without adding test-only APIs to shipping code.
    private Object field(String name) throws Exception { return field(view, name); }
    private Object field(PrivateCameraView target, String name) throws Exception {
        Field field = PrivateCameraView.class.getDeclaredField(name);
        field.setAccessible(true);
        return field.get(target);
    }
    private Set<?> registeredViews(MainActivity owner) throws Exception {
        Field field = MainActivity.class.getDeclaredField("cameraViews");
        field.setAccessible(true);
        return (Set<?>) field.get(owner);
    }
    private boolean requested() throws Exception { return (Boolean) field("requested"); }
    private Preview ownedPreview() throws Exception { return (Preview) field("preview"); }
    private PreviewView previewView() throws Exception { return (PreviewView) field("previewView"); }
}
