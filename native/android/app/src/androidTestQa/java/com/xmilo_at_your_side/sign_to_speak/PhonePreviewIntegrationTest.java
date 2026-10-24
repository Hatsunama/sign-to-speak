package com.xmilo_at_your_side.sign_to_speak;

import static org.junit.Assert.*;

import android.Manifest;
import android.app.Instrumentation;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.SystemClock;
import android.view.View;
import android.view.ViewGroup;
import android.view.TextureView;
import androidx.camera.core.Preview;
import androidx.camera.lifecycle.ProcessCameraProvider;
import androidx.camera.view.PreviewView;
import androidx.lifecycle.Lifecycle;
import androidx.test.core.app.ActivityScenario;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import androidx.test.uiautomator.By;
import androidx.test.uiautomator.Direction;
import androidx.test.uiautomator.UiDevice;
import androidx.test.uiautomator.UiObject2;
import java.lang.reflect.Field;
import java.util.Arrays;
import java.util.Set;
import java.util.concurrent.Callable;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.After;
import org.junit.Test;
import org.junit.runner.RunWith;

/** Real QA React UI and CameraX on a physical phone. Never reads camera frames. */
@RunWith(AndroidJUnit4.class)
public final class PhonePreviewIntegrationTest {
    private static final String QA_PACKAGE = "com.xmilo_at_your_side.sign_to_speak.qa";
    private static final String START = "Start local preview";
    private static final String STOP = "Stop camera";
    private static final String LIVE = "Live preview only. Recognition: unsupported.";
    private static final long STREAM_WAIT_MS = 20000;
    private final Instrumentation instrumentation = InstrumentationRegistry.getInstrumentation();
    private final Context target = instrumentation.getTargetContext();
    private final UiDevice device = UiDevice.getInstance(instrumentation);
    private ActivityScenario<MainActivity> scenario;
    private MainActivity activity;

    @After public void close() {
        if (scenario != null) scenario.close();
    }

    @Test(timeout = 150000) public void uiStartStreamsAndEveryExitReleasesPreview() throws Exception {
        assertEquals("Test only the installed QA application", QA_PACKAGE, target.getPackageName());
        assertEquals(QA_PACKAGE + ".test", instrumentation.getContext().getPackageName());
        assertTrue("Run on an ARM64 physical phone", Arrays.asList(Build.SUPPORTED_ABIS).contains("arm64-v8a"));
        assertFalse("An emulator cannot certify a physical camera", Build.FINGERPRINT.contains("generic")
                || Build.MODEL.toLowerCase().contains("emulator"));
        assertEquals("Grant CAMERA before the run; this test never changes permissions",
                PackageManager.PERMISSION_GRANTED, target.checkSelfPermission(Manifest.permission.CAMERA));
        scenario = ActivityScenario.launch(MainActivity.class);
        scenario.onActivity(a -> activity = a);
        assertTrue("Capture privacy must be ready before mounting Home",
                ((MainApplication) activity.getApplication()).getCapturePrivacyReady());
        findLabel(START);
        assertCameraOff("Launch");

        CameraSnapshot first = startAndAwaitStreaming();
        UiObject2 stop = findLabel(STOP);
        assertStreaming("Before Stop", first);
        stop.click();
        assertReleased("Stop", first);
        findLabel(START);
        assertNoAutomaticRestart();

        CameraSnapshot back = startAndAwaitStreaming();
        assertTrue("OS Back injection succeeds", device.pressBack());
        assertReleased("Back", back);
        findLabel(START);
        assertNoAutomaticRestart();

        CameraSnapshot home = startAndAwaitStreaming();
        assertTrue("OS Home injection succeeds", device.pressHome());
        await("Home pauses Activity", 10000, () -> !snapshot().resumed);
        assertReleased("Home", home);
        Intent reopen = target.getPackageManager().getLaunchIntentForPackage(QA_PACKAGE);
        assertNotNull("QA launcher exists", reopen);
        target.startActivity(reopen.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_REORDER_TO_FRONT));
        await("Launcher resumes Activity", 10000, () -> snapshot().resumed);
        findLabel(START);
        assertNoAutomaticRestart();
    }

    private CameraSnapshot startAndAwaitStreaming() throws Exception {
        assertCameraOff("Before Start");
        findLabel(START).click();
        final CameraSnapshot[] streamed = new CameraSnapshot[1];
        final CameraSnapshot[] lastMounted = new CameraSnapshot[1];
        try {
            await("physical camera STREAMING", STREAM_WAIT_MS, () -> {
                CameraSnapshot state = snapshot();
                if (state.mounted != null) lastMounted[0] = state;
                if (!state.streaming()) return false;
                streamed[0] = state;
                return true;
            });
        } catch (AssertionError timeout) {
            throw new AssertionError(timeout.getMessage()
                    + "; last mounted camera before unmount=" + lastMounted[0], timeout);
        }
        findLabel(LIVE);
        assertStreaming("After scrolling to live notice", streamed[0]);
        return streamed[0];
    }

    private void assertStreaming(String step, CameraSnapshot expected) throws Exception {
        CameraSnapshot now = snapshot(expected.view, expected.preview, expected.provider);
        assertTrue(step + ": " + now, now.streaming()
                && now.view == expected.view && now.preview == expected.preview);
    }

    private void assertReleased(String step, CameraSnapshot previous) throws Exception {
        long end = SystemClock.elapsedRealtime() + 10000;
        while (SystemClock.elapsedRealtime() < end) {
            CameraSnapshot now = snapshot(previous.view, previous.preview, previous.provider);
            if (!now.requested && now.preview == null && now.provider == null
                    && !now.bound && now.stream == PreviewView.StreamState.IDLE) {
                assertEquals("No native cleanup failure after " + step, 0,
                        ((Number) onMain(() -> field(activity, "cameraCleanupFailures"))).intValue());
                return;
            }
            SystemClock.sleep(100);
        }
        throw new AssertionError(step + " did not release owned Preview; "
                + snapshot(previous.view, previous.preview, previous.provider));
    }

    private void assertNoAutomaticRestart() throws Exception {
        long end = SystemClock.elapsedRealtime() + 1200;
        while (SystemClock.elapsedRealtime() < end) {
            assertCameraOff("After exit");
            SystemClock.sleep(100);
        }
    }

    private void assertCameraOff(String step) throws Exception {
        CameraSnapshot now = snapshot();
        assertNull(step + ": camera remounted: " + now, now.mounted);
        assertFalse(step + ": registered camera still owns a session: " + now,
                now.registeredActive);
    }

    private CameraSnapshot snapshot() throws Exception { return snapshot(null, null, null); }

    private CameraSnapshot snapshot(PrivateCameraView previousView, Preview previousPreview,
            ProcessCameraProvider previousProvider) throws Exception {
        return onMain(() -> {
            PrivateCameraView mounted = findCamera(activity.getWindow().getDecorView());
            Set<?> registered = (Set<?>) field(activity, "cameraViews");
            PrivateCameraView fallback = null;
            boolean registeredActive = false;
            StringBuilder registration = new StringBuilder();
            for (Object entry : registered) {
                PrivateCameraView candidate = (PrivateCameraView) entry;
                Preview ownedPreview = (Preview) field(candidate, "preview");
                ProcessCameraProvider ownedProvider = (ProcessCameraProvider) field(candidate, "provider");
                boolean requested = Boolean.TRUE.equals(field(candidate, "requested"));
                boolean active = requested || ownedPreview != null || ownedProvider != null;
                registeredActive |= active;
                if (fallback == null || active) fallback = candidate;
                PreviewView surface = (PreviewView) field(candidate, "previewView");
                if (registration.length() > 0) registration.append("; ");
                registration.append("#").append(System.identityHashCode(candidate))
                        .append("{attached=").append(candidate.isAttachedToWindow())
                        .append(",requested=").append(requested)
                        .append(",disposed=").append(field(candidate, "disposed"))
                        .append(",size=").append(candidate.getWidth()).append("x").append(candidate.getHeight())
                        .append(",surface=").append(surface.getWidth()).append("x").append(surface.getHeight())
                        .append(",stream=").append(surface.getPreviewStreamState().getValue())
                        .append(",preview=").append(ownedPreview != null)
                        .append(",provider=").append(ownedProvider != null)
                        .append("}");
            }
            PrivateCameraView view = mounted != null ? mounted
                    : previousView != null ? previousView : fallback;
            Preview preview = view == null ? null : (Preview) field(view, "preview");
            ProcessCameraProvider provider = view == null ? null : (ProcessCameraProvider) field(view, "provider");
            PreviewView surface = view == null ? null : (PreviewView) field(view, "previewView");
            String surfaceChildren = previewChildren(surface);
            Preview check = previousPreview != null ? previousPreview : preview;
            ProcessCameraProvider checkProvider = previousProvider != null ? previousProvider : provider;
            return new CameraSnapshot(mounted, view, preview, provider,
                    view != null && Boolean.TRUE.equals(field(view, "requested")),
                    view != null && Boolean.TRUE.equals(field(view, "disposed")),
                    view != null && view.isAttachedToWindow(),
                    view == null ? 0 : view.getWidth(), view == null ? 0 : view.getHeight(),
                    surface == null ? 0 : surface.getWidth(), surface == null ? 0 : surface.getHeight(),
                    surface == null ? null : surface.getPreviewStreamState().getValue(),
                    check != null && checkProvider != null && checkProvider.isBound(check),
                    activity.getLifecycle().getCurrentState().isAtLeast(Lifecycle.State.RESUMED),
                    registeredActive, registration.toString(), surfaceChildren);
        });
    }

    private static String previewChildren(PreviewView surface) {
        if (surface == null) return "none";
        StringBuilder children = new StringBuilder();
        for (int i = 0; i < surface.getChildCount(); i++) {
            View child = surface.getChildAt(i);
            if (i > 0) children.append("; ");
            children.append(child.getClass().getName())
                    .append("{actual=").append(child.getWidth()).append("x").append(child.getHeight())
                    .append(",measured=").append(child.getMeasuredWidth()).append("x")
                    .append(child.getMeasuredHeight())
                    .append(",attached=").append(child.isAttachedToWindow());
            if (child instanceof TextureView) {
                children.append(",available=").append(((TextureView) child).isAvailable());
            }
            children.append("}");
        }
        return children.length() == 0 ? "none" : children.toString();
    }
    private static PrivateCameraView findCamera(View root) {
        if (root instanceof PrivateCameraView) return (PrivateCameraView) root;
        if (root instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) root;
            for (int i = 0; i < group.getChildCount(); i++) {
                PrivateCameraView found = findCamera(group.getChildAt(i));
                if (found != null) return found;
            }
        }
        return null;
    }

    private UiObject2 findLabel(String label) throws Exception {
        long end = SystemClock.elapsedRealtime() + 15000;
        boolean downward = true;
        while (SystemClock.elapsedRealtime() < end) {
            UiObject2 node = device.findObject(By.pkg(QA_PACKAGE).text(label));
            if (node == null) node = device.findObject(By.pkg(QA_PACKAGE).desc(label));
            if (node != null) return node;
            UiObject2 scroll = device.findObject(By.pkg(QA_PACKAGE).scrollable(true));
            if (scroll == null) SystemClock.sleep(100);
            else if (!scroll.scroll(downward ? Direction.DOWN : Direction.UP, 0.65f)) downward = !downward;
        }
        throw new AssertionError("QA UI missing '" + label + "'; " + snapshot());
    }

    private static Object field(Object owner, String name) throws Exception {
        Field field = owner.getClass().getDeclaredField(name);
        field.setAccessible(true);
        return field.get(owner);
    }

    private <T> T onMain(Callable<T> work) throws Exception {
        AtomicReference<T> result = new AtomicReference<>();
        AtomicReference<Throwable> error = new AtomicReference<>();
        instrumentation.runOnMainSync(() -> {
            try { result.set(work.call()); } catch (Throwable failure) { error.set(failure); }
        });
        if (error.get() instanceof Error) throw (Error) error.get();
        if (error.get() instanceof Exception) throw (Exception) error.get();
        return result.get();
    }

    private interface Condition { boolean ready() throws Exception; }
    private void await(String label, long timeoutMs, Condition condition) throws Exception {
        long end = SystemClock.elapsedRealtime() + timeoutMs;
        while (SystemClock.elapsedRealtime() < end) {
            if (condition.ready()) return;
            SystemClock.sleep(100);
        }
        throw new AssertionError("Timed out: " + label + "; " + snapshot());
    }

    private static final class CameraSnapshot {
        final PrivateCameraView mounted, view;
        final Preview preview;
        final ProcessCameraProvider provider;
        final boolean requested, disposed, attached, bound, resumed, registeredActive;
        final int width, height, surfaceWidth, surfaceHeight;
        final PreviewView.StreamState stream;
        final String registration, surfaceChildren;
        CameraSnapshot(PrivateCameraView mounted, PrivateCameraView view, Preview preview,
                ProcessCameraProvider provider, boolean requested, boolean disposed, boolean attached,
                int width, int height, int surfaceWidth, int surfaceHeight,
                PreviewView.StreamState stream, boolean bound, boolean resumed,
                boolean registeredActive, String registration, String surfaceChildren) {
            this.mounted = mounted; this.view = view; this.preview = preview; this.provider = provider;
            this.requested = requested; this.disposed = disposed; this.attached = attached;
            this.width = width; this.height = height; this.surfaceWidth = surfaceWidth;
            this.surfaceHeight = surfaceHeight; this.stream = stream; this.bound = bound;
            this.resumed = resumed; this.registeredActive = registeredActive;
            this.registration = registration; this.surfaceChildren = surfaceChildren;
        }
        boolean streaming() {
            return mounted == view && view != null && requested && !disposed && attached
                    && width > 0 && height > 0 && surfaceWidth > 0 && surfaceHeight > 0
                    && preview != null && provider != null && bound
                    && stream == PreviewView.StreamState.STREAMING && resumed;
        }
        @Override public String toString() {
            return "native{view=" + (view != null) + ",mounted=" + (mounted == view && view != null)
                    + ",requested=" + requested + ",disposed=" + disposed + ",attached=" + attached
                    + ",size=" + width + "x" + height + ",surface=" + surfaceWidth + "x" + surfaceHeight
                    + ",stream=" + stream + ",preview=" + (preview != null) + ",provider="
                    + (provider != null) + ",bound=" + bound + ",resumed=" + resumed
                    + ",registeredActive=" + registeredActive + ",children=[" + surfaceChildren
                    + "],registry=[" + registration + "]}";
        }
    }
}
