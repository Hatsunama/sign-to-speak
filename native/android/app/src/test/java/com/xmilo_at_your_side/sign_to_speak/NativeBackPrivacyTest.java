package com.xmilo_at_your_side.sign_to_speak;

import static org.junit.Assert.*;
import static org.mockito.Mockito.*;

import android.Manifest;
import android.app.Application;
import android.content.Context;
import android.content.ContextWrapper;
import java.util.Set;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Executor;
import androidx.camera.core.Camera;
import androidx.camera.core.CameraInfo;
import androidx.camera.core.CameraState;
import androidx.lifecycle.MutableLiveData;
import androidx.lifecycle.Observer;
import com.facebook.react.uimanager.UIManagerHelper;
import com.facebook.react.uimanager.events.EventDispatcher;
import org.mockito.MockedStatic;
import android.os.Looper;
import android.view.View;
import android.view.ViewGroup;
import android.view.TextureView;
import com.facebook.react.views.view.ReactViewGroup;
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
import java.lang.reflect.Method;
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
@Config(sdk = {28, 36}, application = MainApplication.class,
    shadows = {NativeBackPrivacyTest.ProviderCompanionShadow.class, ReactBootstrapShadow.class, CaptureAudioManagerShadow.class})
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
    private Preview[] injectedUnbindFaults;
    private MutableLiveData<CameraState> cameraStates;
    private final List<String> statuses = new ArrayList<>();
    private MockedStatic<UIManagerHelper> uiManager;


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
        when(provider.hasCamera(any(CameraSelector.class))).thenReturn(true);
        Camera camera = mock(Camera.class);
        CameraInfo cameraInfo = mock(CameraInfo.class);
        cameraStates = new MutableLiveData<>(CameraState.create(CameraState.Type.OPENING));
        when(camera.getCameraInfo()).thenReturn(cameraInfo);
        when(cameraInfo.getCameraState()).thenReturn(cameraStates);
        when(provider.bindToLifecycle(any(androidx.lifecycle.LifecycleOwner.class),
            any(CameraSelector.class), any(Preview.class))).thenReturn(camera);
        when(reactContext.hasActiveCatalystInstance()).thenReturn(true); // ThemedReactContext delegates here in RN 0.86.3.
        view.setId(101);
        EventDispatcher dispatcher = mock(EventDispatcher.class);
        uiManager = mockStatic(UIManagerHelper.class);
        uiManager.when(() -> UIManagerHelper.getEventDispatcherForReactTag(any(), anyInt()))
            .thenReturn(dispatcher);
        doAnswer(call -> {
            Object event = call.getArgument(0);
            Field value = event.getClass().getDeclaredField("value");
            value.setAccessible(true);
            statuses.add((String) value.get(event));
            return null;
        }).when(dispatcher).dispatchEvent(any());
        future = SettableFuture.create();
        ProviderCompanionShadow.nextFuture = future;
        ProviderCompanionShadow.acquisitionCount = 0;
        ProviderCompanionShadow.nextFailure = null;
        ProviderCompanionShadow.beforeFailure = null;
    }

    @After public void tearDown() {
        // Assertions have already completed. Remove only this test's deliberate faults,
        // so a counterfactual assertion failure is not duplicated by fixture disposal.
        if (provider != null && injectedUnbindFaults != null) {
            for (Preview target : injectedUnbindFaults) doNothing().when(provider).unbind(target);
        }
        if (view != null) view.dispose();
        if (controller != null) controller.pause().stop().destroy();
        if (delegates != null) delegates.close();
        if (uiManager != null) uiManager.close();
    }

    @Test public void latePreviewTextureGetsLaidOutWithoutAnotherFabricLayout() throws Exception {
        // Fabric lays out native views once. ReactViewGroup deliberately ignores a
        // child's later requestLayout, just as it does in the physical QA screen.
        root.removeView(view);
        ReactViewGroup reactParent = new ReactViewGroup(activity);
        root.addView(reactParent, new FrameLayout.LayoutParams(320, 240));
        reactParent.addView(view, new ViewGroup.LayoutParams(320, 240));
        int width = View.MeasureSpec.makeMeasureSpec(320, View.MeasureSpec.EXACTLY);
        int height = View.MeasureSpec.makeMeasureSpec(240, View.MeasureSpec.EXACTLY);
        reactParent.measure(width, height);
        reactParent.layout(0, 0, 320, 240);
        view.measure(width, height);
        view.layout(0, 0, 320, 240);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        PreviewView surface = previewView();
        assertEquals(320, surface.getWidth());
        assertEquals(240, surface.getHeight());
        assertTrue(view.isAttachedToWindow());

        // CameraX's compatible preview installs a TextureView after the Fabric pass.
        TextureView lateTexture = new TextureView(activity);
        surface.addView(lateTexture, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));
        assertTrue("Precondition: the late TextureView requested a layout", lateTexture.isLayoutRequested());
        Shadows.shadowOf(Looper.getMainLooper()).idle();

        assertEquals("Late CameraX TextureView must be measured without another Fabric layout",
                surface.getWidth(), lateTexture.getMeasuredWidth());
        assertEquals("Late CameraX TextureView must be measured vertically",
                surface.getHeight(), lateTexture.getMeasuredHeight());
        assertEquals("Late CameraX TextureView must receive actual horizontal bounds",
                surface.getWidth(), lateTexture.getWidth());
        assertEquals("Late CameraX TextureView must receive actual vertical bounds",
                surface.getHeight(), lateTexture.getHeight());
    }
    @Test public void queuedLatePreviewLayoutUsesCurrentHostBounds() throws Exception {
        root.removeView(view);
        ReactViewGroup reactParent = new ReactViewGroup(activity);
        root.addView(reactParent, new FrameLayout.LayoutParams(320, 240));
        reactParent.addView(view, new ViewGroup.LayoutParams(320, 240));
        int width = View.MeasureSpec.makeMeasureSpec(320, View.MeasureSpec.EXACTLY);
        int height = View.MeasureSpec.makeMeasureSpec(240, View.MeasureSpec.EXACTLY);
        reactParent.measure(width, height);
        reactParent.layout(0, 0, 320, 240);
        view.measure(width, height);
        view.layout(0, 0, 320, 240);
        Shadows.shadowOf(Looper.getMainLooper()).idle();

        PreviewView surface = previewView();
        TextureView lateTexture = new TextureView(activity);
        surface.addView(lateTexture, new FrameLayout.LayoutParams(-1, -1));
        assertTrue(lateTexture.isLayoutRequested());
        // The host gets new bounds while the CameraX child request is still queued.
        // A direct host layout leaves the child measured at the old size until the callback.
        view.layout(0, 0, 400, 200);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertEquals(400, surface.getMeasuredWidth());
        assertEquals(200, surface.getMeasuredHeight());
        assertEquals(400, lateTexture.getMeasuredWidth());
        assertEquals(200, lateTexture.getMeasuredHeight());
        assertEquals(400, lateTexture.getWidth());
        assertEquals(200, lateTexture.getHeight());
        assertEquals(0, ProviderCompanionShadow.acquisitionCount);
    }

    @Test public void pendingLatePreviewLayoutCancelsOnDetachAndWorksAfterReattach() throws Exception {
        root.removeView(view);
        ReactViewGroup reactParent = new ReactViewGroup(activity);
        root.addView(reactParent, new FrameLayout.LayoutParams(320, 240));
        reactParent.addView(view, new ViewGroup.LayoutParams(320, 240));
        int width = View.MeasureSpec.makeMeasureSpec(320, View.MeasureSpec.EXACTLY);
        int height = View.MeasureSpec.makeMeasureSpec(240, View.MeasureSpec.EXACTLY);
        reactParent.measure(width, height);
        reactParent.layout(0, 0, 320, 240);
        view.measure(width, height);
        view.layout(0, 0, 320, 240);
        Shadows.shadowOf(Looper.getMainLooper()).idle();

        PreviewView surface = previewView();
        TextureView pendingTexture = new TextureView(activity);
        surface.addView(pendingTexture, new FrameLayout.LayoutParams(-1, -1));
        assertTrue(pendingTexture.isLayoutRequested());
        reactParent.removeView(view);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertFalse(view.isAttachedToWindow());
        assertEquals(0, pendingTexture.getMeasuredWidth());
        assertEquals(0, pendingTexture.getWidth());
        surface.removeView(pendingTexture);

        reactParent.addView(view, new ViewGroup.LayoutParams(320, 240));
        view.measure(width, height);
        view.layout(0, 0, 320, 240);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertTrue(view.isAttachedToWindow());
        TextureView resumedTexture = new TextureView(activity);
        surface.addView(resumedTexture, new FrameLayout.LayoutParams(-1, -1));
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertEquals(320, resumedTexture.getMeasuredWidth());
        assertEquals(240, resumedTexture.getMeasuredHeight());
        assertEquals(320, resumedTexture.getWidth());
        assertEquals(240, resumedTexture.getHeight());
        assertEquals(0, ProviderCompanionShadow.acquisitionCount);
    }
    @Test public void lateActiveOnDisposedViewNeverReregisters() throws Exception {
        view.dispose();
        assertFalse(registeredViews(activity).contains(view));
        int events = statuses.size();
        view.setActive(true);
        assertFalse("Late active prop must not re-register a retired view", registeredViews(activity).contains(view));
        assertStopped();
        assertEquals(events, statuses.size());
        assertEquals(0, ProviderCompanionShadow.acquisitionCount);
        verifyNoInteractions(provider);
    }

    @Test public void lateActiveOnDisposedViewNeverEmitsPrivacyFailure() throws Exception {
        view.dispose();
        Field readiness = MainApplication.class.getDeclaredField("capturePrivacyReady");
        readiness.setAccessible(true);
        readiness.setBoolean(activity.getApplication(), false);
        int events = statuses.size();
        view.setActive(true);
        assertEquals("Retired view must stay silent even when privacy is unavailable", events, statuses.size());
        assertFalse(registeredViews(activity).contains(view));
        assertStopped();
        assertEquals(0, ProviderCompanionShadow.acquisitionCount);
        verifyNoInteractions(provider);
    }

    @Test public void nativeCameraRefusesPolicyFailureEvenIfJavaScriptRequestsStart() throws Exception {
        Field readiness = MainApplication.class.getDeclaredField("capturePrivacyReady");
        readiness.setAccessible(true);
        readiness.setBoolean(activity.getApplication(), false);
        view.setActive(true);
        assertStopped();
        assertEquals(0, ProviderCompanionShadow.acquisitionCount);
        assertTrue(statuses.contains("capture_privacy_unavailable"));
        view.onHostResume();
        root.removeView(view);
        root.addView(view);
        view.setActive(true);
        assertStopped();
        assertEquals(0, ProviderCompanionShadow.acquisitionCount);
        verifyNoInteractions(provider);
    }

    @Test public void synchronousProviderAcquisitionFailureCancelsAndAllowsExplicitRetry() throws Exception {
        ProviderCompanionShadow.nextFailure = new IllegalStateException("Injected synchronous acquisition failure");
        view.setActive(true);
        assertStopped();
        assertEquals(1, ProviderCompanionShadow.acquisitionCount);
        assertEquals("camera_unavailable", statuses.get(statuses.size() - 1));
        verifyNoInteractions(provider);

        ProviderCompanionShadow.nextFailure = null;
        statuses.clear();
        view.setActive(true);
        assertTrue(requested());
        assertEquals(2, ProviderCompanionShadow.acquisitionCount);
        future.set(provider);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertNotNull("Explicit retry must bind after the acquisition fault clears", ownedPreview());
        assertFalse(statuses.contains("camera_unavailable"));
    }

    @SuppressWarnings("unchecked")
    @Test public void synchronousProviderListenerFailureCancelsAndAllowsExplicitRetry() throws Exception {
        ListenableFuture<ProcessCameraProvider> badFuture = mock(ListenableFuture.class);
        doThrow(new IllegalStateException("Injected listener registration failure"))
            .when(badFuture).addListener(any(Runnable.class), any(Executor.class));
        ProviderCompanionShadow.nextFuture = badFuture;
        view.setActive(true);
        assertStopped();
        assertEquals(1, ProviderCompanionShadow.acquisitionCount);
        assertEquals("camera_unavailable", statuses.get(statuses.size() - 1));
        verifyNoInteractions(provider);

        ProviderCompanionShadow.nextFuture = future;
        statuses.clear();
        view.setActive(true);
        assertEquals(2, ProviderCompanionShadow.acquisitionCount);
        future.set(provider);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertNotNull("Explicit retry must bind after listener registration recovers", ownedPreview());
        assertFalse(statuses.contains("camera_unavailable"));
    }

    @Test public void acquisitionFailureAfterStopCannotPublishStaleFailure() throws Exception {
        ProviderCompanionShadow.beforeFailure = () -> view.setActive(false);
        ProviderCompanionShadow.nextFailure = new IllegalStateException("Injected late acquisition failure");
        view.setActive(true);
        assertStopped();
        assertEquals(1, ProviderCompanionShadow.acquisitionCount);
        assertTrue("A canceled lease must stay silent after a late failure", statuses.isEmpty());
        verifyNoInteractions(provider);
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
        injectedUnbindFaults = new Preview[] {first, second};
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
        injectedUnbindFaults = new Preview[] {first, second};
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

    // These exercise real production cleanup entry points, not a parallel policy helper.
    @Test public void sharedCleanupFailureOnHostPauseIsAccountedAndBlocksFreshView() throws Exception {
        assertCleanupBoundary("pause");
    }
    @Test public void sharedCleanupFailureOnActualDetachFinalizesAndBlocksFreshView() throws Exception {
        assertCleanupBoundary("detach");
    }
    @Test public void sharedCleanupFailureOnManagerDropFinalizesAndBlocksFreshView() throws Exception {
        // React unmount uses this same native boundary for both Stop and Hatsu navigation.
        assertCleanupBoundary("drop");
    }
    @Test public void sharedCleanupFailureOnDirectDisposeIsAccountedAndBlocksFreshView() throws Exception {
        assertCleanupBoundary("dispose");
    }
    @Test public void sharedCleanupFailureOnInactivePropIsAccountedAndBlocksFreshView() throws Exception {
        assertCleanupBoundary("inactive");
    }
    @Test public void sharedCleanupFailureOnHostDestroyIsAccountedAndBlocksFreshView() throws Exception {
        assertCleanupBoundary("hostDestroy");
    }

    private void assertCleanupBoundary(String entry) throws Exception {
        Preview owned = startBoundPreview();
        long token = leaseToken(view);
        doThrow(new UnsupportedOperationException("Injected owned cleanup failure")).when(provider).unbind(owned);
        injectedUnbindFaults = new Preview[] {owned};
        statuses.clear();
        RuntimeException escaped = null;
        try {
            switch (entry) {
                case "pause": view.onHostPause(); break;
                case "detach": root.removeView(view); break;
                case "drop": new CameraViewManager().onDropViewInstance(view); break;
                case "dispose": view.dispose(); break;
                case "inactive": view.setActive(false); break;
                case "hostDestroy": view.onHostDestroy(); break;
                default: throw new AssertionError(entry);
            }
        } catch (RuntimeException error) { escaped = error; }
        assertNull(entry + " must contain cleanup failure", escaped);
        assertNativeCanceled(view, token);
        verify(provider).unbind(owned);
        assertEquals(entry + " must record the Activity-wide fault", 1, cleanupFailures());
        assertFalse("Never publish ordinary stopped after uncertain release", statuses.contains("stopped"));
        if (!entry.equals("pause") && !entry.equals("inactive")) {
            assertFalse("Listener finalization must run", (Boolean) field("observing"));
            verify(reactContext).removeLifecycleEventListener(view);
        }
        if (entry.equals("drop") || entry.equals("dispose") || entry.equals("hostDestroy")) {
            assertFalse(registeredViews(activity).contains(view));
        }
        if (entry.equals("detach")) assertFalse(view.isAttachedToWindow());
        // A second stop cannot clear the fault or hide it from a newly created view.
        view.setActive(false);
        PrivateCameraView fresh = new PrivateCameraView(
            new ThemedReactContext(reactContext, new ContextWrapper(activity), "FreshAfterFault", 4));
        fresh.setId(102);
        root.addView(fresh);
        fresh.setActive(true);
        assertFalse((Boolean) field(fresh, "requested"));
        assertEquals(1, ProviderCompanionShadow.acquisitionCount);
        assertEquals("camera_cleanup_failed", statuses.get(statuses.size() - 1));
        fresh.dispose();
    }

    @Test public void pendingNewViewCannotBindAfterRetiredViewCleanupFails() throws Exception {
        Preview retired = startBoundPreview();
        SettableFuture<ProcessCameraProvider> pending = SettableFuture.create();
        ProviderCompanionShadow.nextFuture = pending;
        PrivateCameraView next = new PrivateCameraView(
            new ThemedReactContext(reactContext, new ContextWrapper(activity), "PendingAfterFault", 5));
        next.setId(102);
        root.addView(next, new FrameLayout.LayoutParams(320, 240));
        try {
            next.setActive(true);
            assertEquals("New view must actually be waiting for its provider", 2,
                ProviderCompanionShadow.acquisitionCount);
            assertFalse(pending.isDone());
            assertTrue((Boolean) field(next, "requested"));
            assertNull(field(next, "preview"));

            doThrow(new UnsupportedOperationException("Injected retired-view cleanup failure"))
                .when(provider).unbind(retired);
            injectedUnbindFaults = new Preview[] {retired};
            view.setActive(false); // Actual owned cleanup records the shared Activity fault.
            verify(provider).unbind(retired);
            assertEquals(1, cleanupFailures());
            statuses.clear();

            pending.set(provider);
            Shadows.shadowOf(Looper.getMainLooper()).idle();
            verify(provider, times(1)).bindToLifecycle(any(androidx.lifecycle.LifecycleOwner.class),
                any(CameraSelector.class), any(Preview.class));
            assertFalse("Late provider must cancel the pending request after shared cleanup failure",
                (Boolean) field(next, "requested"));
            assertNull(field(next, "preview"));
            assertEquals(View.INVISIBLE, ((PreviewView) field(next, "previewView")).getVisibility());
            assertEquals("camera_cleanup_failed", statuses.get(statuses.size() - 1));
            assertEquals("Late callback must preserve the Activity fault latch", 1, cleanupFailures());
        } finally {
            next.dispose();
        }
    }

    @Test public void sharedCleanupFailureDuringBindErrorRetainsFaultStatus() throws Exception {
        doAnswer(call -> {
            Preview owned = call.getArgument(2);
            assertNotNull(owned);
            leaseToken(view);
            doThrow(new UnsupportedOperationException("Injected cleanup failure")).when(provider).unbind(owned);
            injectedUnbindFaults = new Preview[] {owned};
            throw new IllegalArgumentException("Injected bind failure");
        }).when(provider).bindToLifecycle(eq(activity), any(CameraSelector.class), any(Preview.class));
        view.setActive(true);
        future.set(provider);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertStopped();
        assertEquals(1, cleanupFailures());
        assertFalse(statuses.contains("camera_unavailable"));
        assertTrue(statuses.contains("camera_cleanup_failed"));
    }

    @Test public void surfaceDetachFailureStillAttemptsOwnedUnbind() throws Exception {
        Preview original = startBoundPreview();
        long token = leaseToken(view);
        // Only the exceptional surface-detach operation is replaced. The real start
        // and bound Preview preconditions above must succeed before injection.
        Preview faulty = spy(original);
        doThrow(new IllegalStateException("Injected surface failure")).when(faulty)
            .setSurfaceProvider((Preview.SurfaceProvider) null);
        Field previewField = PrivateCameraView.class.getDeclaredField("preview");
        previewField.setAccessible(true);
        previewField.set(view, faulty);
        RuntimeException escaped = null;
        try { view.onHostPause(); } catch (RuntimeException error) { escaped = error; }
        assertNull("Surface failure must be accounted at shared cleanup boundary", escaped);
        assertNativeCanceled(view, token);
        verify(provider).unbind(faulty);
        assertEquals(1, cleanupFailures());
        assertFalse(statuses.contains("stopped"));
    }

    @SuppressWarnings("unchecked")
    private MutableLiveData<PreviewView.StreamState> streamStates() throws Exception {
        return (MutableLiveData<PreviewView.StreamState>) previewView().getPreviewStreamState();
    }

    @Test public void bindingIdleDoesNotClaimStreamingAndCurrentStreamingDoes() throws Exception {
        startBoundPreview();
        assertEquals(PreviewView.StreamState.IDLE, streamStates().getValue());
        assertFalse("Bind success and View.VISIBLE do not prove streaming", statuses.contains("preview_only"));
        assertTrue(cameraStates.hasObservers());
        streamStates().setValue(PreviewView.StreamState.STREAMING);
        assertEquals("preview_only", statuses.get(statuses.size() - 1));
    }

    @Test public void defaultCameraBindsRearAfterActualProviderAcquisition() throws Exception {
        Preview owned = startBoundPreview();
        // startBoundPreview proves the real Kotlin start path acquired and bound a use case.
        verify(provider).hasCamera(CameraSelector.DEFAULT_BACK_CAMERA);
        verify(provider).bindToLifecycle(activity, CameraSelector.DEFAULT_BACK_CAMERA, owned);
    }

    @Test public void explicitFrontSelectionBeforeStartBindsFront() throws Exception {
        selectLens("front");
        view.setActive(true);
        assertEquals(1, ProviderCompanionShadow.acquisitionCount);
        future.set(provider);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        Preview owned = ownedPreview();
        assertNotNull("Explicit front Start must bind a preview", owned);
        verify(provider).hasCamera(CameraSelector.DEFAULT_FRONT_CAMERA);
        verify(provider).bindToLifecycle(activity, CameraSelector.DEFAULT_FRONT_CAMERA, owned);
    }

    @Test public void activeThenFrontLensBeforeAttachmentKeepsQueuedStart() throws Exception {
        root.removeView(view);
        assertFalse(view.isAttachedToWindow());
        view.setActive(true);
        assertTrue("Detached Start must remain queued", requested());
        selectLens("front");
        assertTrue("Changing the lens must preserve a detached Start request", requested());
        assertEquals("A detached view cannot acquire a provider", 0,
            ProviderCompanionShadow.acquisitionCount);
        root.addView(view);
        assertEquals("Attachment must acquire the selected lens exactly once", 1,
            ProviderCompanionShadow.acquisitionCount);
        future.set(provider);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        Preview owned = ownedPreview();
        assertNotNull("Queued front request must bind after attachment", owned);
        verify(provider).hasCamera(CameraSelector.DEFAULT_FRONT_CAMERA);
        verify(provider).bindToLifecycle(activity, CameraSelector.DEFAULT_FRONT_CAMERA, owned);
    }

    @Test public void frontLensThenActiveBeforeAttachmentBindsOnAttachment() throws Exception {
        root.removeView(view);
        assertFalse(view.isAttachedToWindow());
        selectLens("front");
        assertFalse("Lens selection alone cannot request a camera", requested());
        view.setActive(true);
        assertTrue("Detached Start must remain queued after lens selection", requested());
        assertEquals("A detached view cannot acquire a provider", 0,
            ProviderCompanionShadow.acquisitionCount);
        root.addView(view);
        assertEquals("Attachment must acquire the selected lens exactly once", 1,
            ProviderCompanionShadow.acquisitionCount);
        future.set(provider);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        Preview owned = ownedPreview();
        assertNotNull("Queued front request must bind after attachment", owned);
        verify(provider).hasCamera(CameraSelector.DEFAULT_FRONT_CAMERA);
        verify(provider).bindToLifecycle(activity, CameraSelector.DEFAULT_FRONT_CAMERA, owned);
    }

    @Test public void pendingLensChangeInvalidatesOldProviderCallback() throws Exception {
        view.setActive(true);
        assertEquals(1, ProviderCompanionShadow.acquisitionCount);
        SettableFuture<ProcessCameraProvider> retired = future;
        future = SettableFuture.create();
        ProviderCompanionShadow.nextFuture = future;
        selectLens("front");
        assertEquals("Changing a pending lens starts a fresh acquisition", 2,
            ProviderCompanionShadow.acquisitionCount);
        retired.set(provider);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        verifyNoInteractions(provider);
        future.set(provider);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        Preview owned = ownedPreview();
        assertNotNull("New front request must bind", owned);
        verify(provider).bindToLifecycle(activity, CameraSelector.DEFAULT_FRONT_CAMERA, owned);
        verify(provider, never()).bindToLifecycle(eq(activity), eq(CameraSelector.DEFAULT_BACK_CAMERA), any(Preview.class));
    }

    @Test public void activeLensChangeReleasesOldPreviewAndIgnoresRetiredObservers() throws Exception {
        Preview old = startBoundPreview();
        Observer<PreviewView.StreamState> oldStream = observer(streamStates());
        Observer<CameraState> oldError = observer(cameraStates);
        long retiredToken = leaseToken(view);
        statuses.clear();
        selectLens("front");
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertFalse(((PreviewLease) field("lease")).isCurrent(retiredToken));
        verify(provider).unbind(old);
        Preview current = ownedPreview();
        assertNotNull("Lens change must bind a new preview", current);
        assertNotSame(old, current);
        verify(provider).bindToLifecycle(activity, CameraSelector.DEFAULT_FRONT_CAMERA, current);
        oldStream.onChanged(PreviewView.StreamState.STREAMING);
        oldError.onChanged(CameraState.create(CameraState.Type.PENDING_OPEN,
            CameraState.StateError.create(CameraState.ERROR_CAMERA_IN_USE)));
        assertSame(current, ownedPreview());
        assertTrue(requested());
        assertFalse("Retired callbacks cannot claim new frames", statuses.contains("preview_only"));
        assertFalse("Retired errors cannot stop the new binding", statuses.contains("camera_unavailable"));
    }

    @Test public void activeFrontPreviewCanSwitchBack() throws Exception {
        selectLens("front");
        Preview old = startBoundPreview();
        verify(provider).bindToLifecycle(activity, CameraSelector.DEFAULT_FRONT_CAMERA, old);
        selectLens("back");
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        verify(provider).unbind(old);
        Preview current = ownedPreview();
        assertNotNull("Switching back must bind a new preview", current);
        assertNotSame(old, current);
        verify(provider).bindToLifecycle(activity, CameraSelector.DEFAULT_BACK_CAMERA, current);
    }

    @Test public void selectedLensUnavailableNeverFallsBackToOtherLens() throws Exception {
        when(provider.hasCamera(CameraSelector.DEFAULT_FRONT_CAMERA)).thenReturn(false);
        selectLens("front");
        view.setActive(true);
        future.set(provider);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertStopped();
        verify(provider).hasCamera(CameraSelector.DEFAULT_FRONT_CAMERA);
        verify(provider, never()).bindToLifecycle(any(androidx.lifecycle.LifecycleOwner.class),
            any(CameraSelector.class), any(Preview.class));
        assertEquals("front_camera_unavailable", statuses.get(statuses.size() - 1));
    }

    @Test public void defaultRearUnavailableNeverFallsBackToFront() throws Exception {
        when(provider.hasCamera(CameraSelector.DEFAULT_BACK_CAMERA)).thenReturn(false);
        view.setActive(true);
        assertEquals(1, ProviderCompanionShadow.acquisitionCount);
        future.set(provider);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertStopped();
        verify(provider).hasCamera(CameraSelector.DEFAULT_BACK_CAMERA);
        verify(provider, never()).hasCamera(CameraSelector.DEFAULT_FRONT_CAMERA);
        verify(provider, never()).bindToLifecycle(any(androidx.lifecycle.LifecycleOwner.class),
            any(CameraSelector.class), any(Preview.class));
        assertEquals("rear_camera_unavailable", statuses.get(statuses.size() - 1));
    }

    @Test public void failedCleanupDuringLensChangeBlocksRebind() throws Exception {
        Preview old = startBoundPreview();
        doThrow(new UnsupportedOperationException("Injected lens-switch cleanup failure"))
            .when(provider).unbind(old);
        injectedUnbindFaults = new Preview[] {old};
        selectLens("front");
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        verify(provider).unbind(old);
        verify(provider, times(1)).bindToLifecycle(any(androidx.lifecycle.LifecycleOwner.class),
            any(CameraSelector.class), any(Preview.class));
        assertEquals(1, cleanupFailures());
        assertEquals("camera_cleanup_failed", statuses.get(statuses.size() - 1));
        assertStopped();
    }

    @Test public void lensChangeAfterStopDoesNotRestart() throws Exception {
        view.setActive(true);
        assertEquals(1, ProviderCompanionShadow.acquisitionCount);
        view.setActive(false);
        selectLens("front");
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertFalse(requested());
        assertEquals("A lens preference cannot restart a stopped session", 1,
            ProviderCompanionShadow.acquisitionCount);
        future.set(provider);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        verifyNoInteractions(provider);
    }

    @Test public void lensChangeAfterBackDoesNotRestart() throws Exception {
        view.setActive(true);
        assertEquals(1, ProviderCompanionShadow.acquisitionCount);
        committedBack();
        selectLens("front");
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertFalse(requested());
        assertEquals(1, ProviderCompanionShadow.acquisitionCount);
        future.set(provider);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        verifyNoInteractions(provider);
    }

    @Test public void lensChangeAfterPauseAndResumeDoesNotRestart() throws Exception {
        view.setActive(true);
        assertEquals(1, ProviderCompanionShadow.acquisitionCount);
        view.onHostPause();
        view.onHostResume();
        selectLens("front");
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertFalse(requested());
        assertEquals(1, ProviderCompanionShadow.acquisitionCount);
        future.set(provider);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        verifyNoInteractions(provider);
    }

    @Test public void asynchronousCameraErrorCancelsCurrentLeaseAndRemovesObservers() throws Exception {
        Preview owned = startBoundPreview();
        long token = leaseToken(view);
        streamStates().setValue(PreviewView.StreamState.STREAMING);
        statuses.clear();
        cameraStates.setValue(CameraState.create(CameraState.Type.PENDING_OPEN,
            CameraState.StateError.create(CameraState.ERROR_CAMERA_IN_USE)));
        assertNativeCanceled(view, token);
        verify(provider).unbind(owned);
        assertEquals("camera_unavailable", statuses.get(statuses.size() - 1));
        assertFalse(cameraStates.hasObservers());
        assertFalse(streamStates().hasObservers());
        view.onHostResume();
        assertFalse(requested());
    }

    @Test public void initialIdleCanWaitButIdleAfterStreamingStopsTruthfully() throws Exception {
        Preview owned = startBoundPreview();
        cameraStates.setValue(CameraState.create(CameraState.Type.PENDING_OPEN));
        streamStates().setValue(PreviewView.StreamState.IDLE);
        assertTrue(requested());
        assertFalse(statuses.contains("preview_only"));
        streamStates().setValue(PreviewView.StreamState.STREAMING);
        long token = leaseToken(view);
        streamStates().setValue(PreviewView.StreamState.IDLE);
        assertNativeCanceled(view, token);
        verify(provider).unbind(owned);
        assertEquals("stopped", statuses.get(statuses.size() - 1));
    }

    @Test public void lateOldStreamAndErrorCallbacksCannotAffectNewLease() throws Exception {
        startBoundPreview();
        // Keep the registered observers to simulate callbacks already queued before Stop.
        Observer<PreviewView.StreamState> oldStream = observer(streamStates());
        Observer<CameraState> oldError = observer(cameraStates);
        view.setActive(false);
        assertFalse(cameraStates.hasObservers());
        assertFalse(streamStates().hasObservers());
        statuses.clear();
        view.setActive(true);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        long token = leaseToken(view);
        oldStream.onChanged(PreviewView.StreamState.STREAMING);
        oldError.onChanged(CameraState.create(CameraState.Type.PENDING_OPEN,
            CameraState.StateError.create(CameraState.ERROR_CAMERA_IN_USE)));
        assertTrue(((PreviewLease) field("lease")).isCurrent(token));
        assertTrue(requested());
        assertTrue("Old callbacks cannot publish or stop a new session", statuses.isEmpty());
        streamStates().setValue(PreviewView.StreamState.STREAMING);
        assertEquals("preview_only", statuses.get(statuses.size() - 1));
        view.dispose();
        assertFalse(cameraStates.hasObservers());
        assertFalse(streamStates().hasObservers());
    }

    @Test public void retiredCachedStreamingCannotClaimNewBindingIsLive() throws Exception {
        startBoundPreview();
        streamStates().setValue(PreviewView.StreamState.STREAMING);
        view.setActive(false);
        assertFalse(streamStates().hasObservers());
        statuses.clear();
        view.setActive(true);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertTrue(requested());
        assertTrue("Cached old STREAMING cannot publish a new live claim", statuses.isEmpty());
        streamStates().setValue(PreviewView.StreamState.IDLE);
        assertTrue("Initial idle for the new binding may wait", requested());
        streamStates().setValue(PreviewView.StreamState.STREAMING);
        assertEquals("preview_only", statuses.get(statuses.size() - 1));
    }

    @Test public void cameraErrorCleanupFailureHasPriorityAndIsCountedOnce() throws Exception {
        Preview owned = startBoundPreview();
        long token = leaseToken(view);
        streamStates().setValue(PreviewView.StreamState.STREAMING);
        statuses.clear();
        doThrow(new UnsupportedOperationException("Injected async-error cleanup failure"))
            .when(provider).unbind(owned);
        injectedUnbindFaults = new Preview[] {owned};
        cameraStates.setValue(CameraState.create(CameraState.Type.PENDING_OPEN,
            CameraState.StateError.create(CameraState.ERROR_CAMERA_IN_USE)));
        assertNativeCanceled(view, token);
        assertEquals(1, cleanupFailures());
        assertFalse(statuses.contains("camera_unavailable"));
        assertFalse(statuses.contains("stopped"));
        assertEquals("camera_cleanup_failed", statuses.get(statuses.size() - 1));
        view.onHostPause();
        view.dispose();
        assertEquals("Repeated exits do not double-account the same failed cleanup", 1, cleanupFailures());
        verify(provider, times(1)).unbind(owned);
        assertFalse(cameraStates.hasObservers());
        assertFalse(streamStates().hasObservers());
    }

    @SuppressWarnings("unchecked")
    private <T> Observer<T> observer(androidx.lifecycle.LiveData<T> data) throws Exception {
        Field observers = androidx.lifecycle.LiveData.class.getDeclaredField("mObservers");
        observers.setAccessible(true);
        Iterable<?> entries = (Iterable<?>) observers.get(data);
        java.util.Iterator<?> iterator = entries.iterator();
        assertTrue("Production must register a current-lease observer", iterator.hasNext());
        return (Observer<T>) ((java.util.Map.Entry<?, ?>) iterator.next()).getKey();
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
        verify(provider).bindToLifecycle(eq(activity), any(CameraSelector.class), eq(owned));
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

    private void selectLens(String lensFacing) throws Exception {
        Method setter;
        try {
            setter = PrivateCameraView.class.getMethod("setLensFacing", String.class);
        } catch (NoSuchMethodException missing) {
            throw new AssertionError("Native view must expose setLensFacing(String) for front/rear selection", missing);
        }
        setter.invoke(view, lensFacing);
    }

    private Preview startBoundPreview() throws Exception {
        view.setActive(true);
        assertEquals("Actual Kotlin acquisition must use the controlled future", 1,
            ProviderCompanionShadow.acquisitionCount);
        future.set(provider);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        Preview owned = ownedPreview();
        assertNotNull("Precondition: actual start() must bind a Preview", owned);
        verify(provider).bindToLifecycle(eq(activity), any(CameraSelector.class), eq(owned));
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
        static ListenableFuture<ProcessCameraProvider> nextFuture;
        static int acquisitionCount;
        static RuntimeException nextFailure;
        static Runnable beforeFailure;

        @Implementation protected ListenableFuture<ProcessCameraProvider> getInstance(Context context) {
            acquisitionCount++;
            if (nextFailure != null) {
                if (beforeFailure != null) beforeFailure.run();
                throw nextFailure;
            }
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
