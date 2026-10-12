package com.xmilo_at_your_side.sign_to_speak;

import static org.junit.Assert.*;

import android.Manifest;
import android.animation.ValueAnimator;
import android.app.Instrumentation;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.res.Configuration;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.system.Os;
import android.system.OsConstants;
import java.nio.file.Files;
import java.nio.file.Paths;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
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
import com.facebook.react.ReactHost;
import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.Callable;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.After;
import org.junit.Test;
import org.junit.runner.RunWith;

/**
 * Real bundled React/Hermes UI, OS permission dialog, and CameraX on an emulator.
 * No replacement Activity, React host, camera provider, frames, or native callbacks.
 * Test-only reflection READS the pinned production objects; it never changes them.
 * Emulator-generated frames are not physical-camera or ASL/speech validation.
 */
@RunWith(AndroidJUnit4.class)
public final class EmulatorPrivacyLifecycleTest {
    private static final String QA_PACKAGE = "com.xmilo_at_your_side.sign_to_speak.qa";
    private static final String START = "Start local preview";
    private static final String STOP = "Stop camera";
    private static final long TIMEOUT_MS = 25000;
    private final Instrumentation instrumentation = InstrumentationRegistry.getInstrumentation();
    private final Context target = instrumentation.getTargetContext();
    private final UiDevice device = UiDevice.getInstance(instrumentation);
    private ActivityScenario<MainActivity> scenario;
    private MainActivity activity;
    private FrontCameraAvailability availability;
    private int startingOrientation;

    /**
     * One ordered journey deliberately owns first-deny/second-grant permission state.
     * Every stage marker follows its assertions. A missing stage is not a pass.
     */
    @Test(timeout = 300000) public void realUiPermissionCameraAndLifecycleJourney() throws Exception {
        assertEquals("Never run against the shipping application", QA_PACKAGE, target.getPackageName());
        assertEquals(QA_PACKAGE + ".test", instrumentation.getContext().getPackageName());
        Bundle args = InstrumentationRegistry.getArguments();
        int expectedApi = Integer.parseInt(args.getString("expectedApi", "0"));
        long expectedPages = Long.parseLong(args.getString("expectedPages", "0"));
        assertTrue("Only reviewed APIs", expectedApi == 26 || expectedApi == 35 || expectedApi == 36);
        assertTrue("Only reviewed page sizes", expectedPages == 4096 || expectedPages == 16384);
        assertEquals("Guest matches matrix API", expectedApi, Build.VERSION.SDK_INT);
        assertEquals("Actual app-process page size, not an image label", expectedPages,
                Os.sysconf(OsConstants._SC_PAGESIZE));
        assertEquals("This matrix exercises x86_64, not ARM64", "x86_64", Build.SUPPORTED_ABIS[0]);
        startingOrientation = Integer.parseInt(args.getString("orientation", "0"));
        assertTrue("Orientation is required", startingOrientation == Configuration.ORIENTATION_PORTRAIT
                || startingOrientation == Configuration.ORIENTATION_LANDSCAPE);
        assertTrue("Require the disposable Android emulator, never a personal device",
                Build.FINGERPRINT.contains("generic") || Build.MODEL.contains("sdk_gphone"));
        assertEquals("Require a fresh QA install; do not pregrant camera", PackageManager.PERMISSION_DENIED,
                target.checkSelfPermission(Manifest.permission.CAMERA));
        assertInstalledPrivacyContract();
        setOrientation(startingOrientation);
        launchRealActivity();
        assertOrientation(startingOrientation);
        findUiLabel("Your hands. Your words.");
        findUiLabel("Translation is not available yet");
        assertNoCurrentCamera();
        assertFalse("Launch alone must not open a permission dialog", hasPermissionDialog());
        assertRealReactHermesAndHatsu();
        stage("01_launch_real_ui_camera_off");

        clickUiLabel(START);
        clickPermissionButton("permission_deny_button");
        await("OS camera denial", () -> target.checkSelfPermission(Manifest.permission.CAMERA)
                == PackageManager.PERMISSION_DENIED && !hasPermissionDialog());
        findUiLabel("Camera permission was not granted. You can use the home screen without it.");
        findUiLabel(START);
        assertNoCurrentCamera();
        stage("02_os_permission_denied");

        clickUiLabel(START);
        clickPermissionButton(Build.VERSION.SDK_INT >= 30
                ? "permission_allow_foreground_only_button" : "permission_allow_button");
        await("OS foreground camera grant", () -> target.checkSelfPermission(Manifest.permission.CAMERA)
                == PackageManager.PERMISSION_GRANTED && !hasPermissionDialog());
        availability = new FrontCameraAvailability(target);
        // The real OS permission dialog may pause the Activity. That pause revokes
        // start eligibility by design; explicitly start again only for that exact UI state.
        String grantedButOff = "Camera is off. Permission was granted; tap Start local preview to begin.";
        await("Grant resolves to streaming or the explicit-start privacy notice", () ->
                device.hasObject(By.pkg(QA_PACKAGE).text(grantedButOff)) ||
                device.hasObject(By.pkg(QA_PACKAGE).text("Live preview only. Recognition: unsupported.")));
        if (device.hasObject(By.pkg(QA_PACKAGE).text(grantedButOff))) {
            assertNoCurrentCamera();
            clickUiLabel(START);
        }
        CameraObservation first = awaitStreamingCamera();
        findUiLabel("Live preview only. Recognition: unsupported.");
        stage("03_emulated_front_camera_streaming");

        clickWhileStreaming(STOP, first);
        assertReleased(first);
        findUiLabel("Camera is off.");
        assertRemainsOff();
        stage("04_stop_releases_owned_preview");

        CameraObservation backCamera = startAgain();
        assertStillStreaming(backCamera);
        assertTrue("OS Back injection must succeed", device.pressBack());
        assertReleased(backCamera);
        findUiLabel(START);
        assertRemainsOff();
        stage("05_os_back_releases_owned_preview");

        CameraObservation homeCamera = startAgain();
        HatsuBackgroundView hatsu = onMain(() -> onlyView(HatsuBackgroundView.class));
        assertStillStreaming(homeCamera);
        assertTrue("OS Home injection must succeed", device.pressHome());
        await("Activity really leaves RESUMED", () -> onMain(() ->
                !activity.getLifecycle().getCurrentState().isAtLeast(Lifecycle.State.RESUMED)));
        assertReleased(homeCamera);
        await("Hatsu pauses in background", () -> onMain(() -> !hatsu.isAnimating()));
        stage("06_home_releases_camera_and_pauses_hatsu");
        Intent reopen = target.getPackageManager().getLaunchIntentForPackage(QA_PACKAGE);
        assertNotNull("QA package must expose its own launcher", reopen);
        target.startActivity(reopen.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_REORDER_TO_FRONT));
        await("Activity resumes through launcher", () -> onMain(() ->
                activity.getLifecycle().getCurrentState().isAtLeast(Lifecycle.State.RESUMED)));
        findUiLabel(START);
        assertRemainsOff();
        assertRealReactHermesAndHatsu();
        stage("07_launcher_resume_requires_new_start");

        CameraObservation rotated = startAgain();
        MainActivity beforeRotation = activity;
        for (int cycle = 0; cycle < 2; cycle++) {
            int other = startingOrientation == Configuration.ORIENTATION_PORTRAIT
                    ? Configuration.ORIENTATION_LANDSCAPE : Configuration.ORIENTATION_PORTRAIT;
            for (int orientation : new int[]{other, startingOrientation}) {
                setOrientation(orientation);
                assertOrientation(orientation);
                CameraObservation turned = awaitStreamingCamera();
                assertSame("Manifest handles orientation without replacing Activity", beforeRotation, activity);
                assertSame("Rotation preserves the existing owned Preview", rotated.preview, turned.preview);
                assertEquals("Rotation cannot silently create a new lease", rotated.token, turned.token);
                findUiLabel(STOP);
            }
        }
        clickWhileStreaming(STOP, rotated);
        assertReleased(rotated);
        stage("08_orientation_preserves_preview_then_stop_releases");

        CameraObservation thanksCamera = startAgain();
        clickWhileStreaming("Hatsu Is Here For You", thanksCamera);
        findUiLabel("Thank you");
        findUiLabel("If you want to support Hatsu please subscribe to the Patreon @Hatsunama");
        assertReleased(thanksCamera);
        assertNoCurrentCamera();
        clickUiLabel("Back to Home");
        findUiLabel(START);
        assertRemainsOff();
        clickUiLabel("Hatsu Is Here For You");
        findUiLabel("Thank you");
        assertTrue(device.pressBack());
        findUiLabel(START);
        assertRemainsOff();
        for (int repeat = 0; repeat < 3; repeat++) {
            clickUiLabel("Hatsu Is Here For You");
            findUiLabel("Thank you");
            assertTrue(device.pressBack());
            findUiLabel(START);
            assertRemainsOff();
        }
        stage("09_thank_you_button_and_os_back_leave_camera_off");

        CameraObservation closed = startAgain();
        MainActivity oldActivity = activity;
        assertStillStreaming(closed);
        scenario.close();
        scenario = null;
        await("Actual Activity destruction", () -> onMain(oldActivity::isDestroyed));
        assertReleased(closed);
        launchRealActivity();
        assertNotSame("Reopen must create a new Activity", oldActivity, activity);
        findUiLabel(START);
        assertRemainsOff();
        assertRealReactHermesAndHatsu();
        CameraObservation afterReopen = startAgain();
        clickWhileStreaming(STOP, afterReopen);
        assertReleased(afterReopen);
        stage("10_activity_reopen_stays_off_until_explicit_start");
        stage("COMPLETE_emulator_only_no_physical_camera_asl_or_speech_claim");
    }

    private void setOrientation(int orientation) throws Exception {
        if (orientation == Configuration.ORIENTATION_PORTRAIT) device.setOrientationPortrait();
        else device.setOrientationLandscape();
    }

    private void assertOrientation(int orientation) throws Exception {
        await("Requested orientation reaches real Activity", () -> onMain(() ->
                activity.getResources().getConfiguration().orientation == orientation));
    }

    @After public void cleanup() throws Exception {
        try {
            if (scenario != null) scenario.close();
        } finally {
            try {
                if (availability != null) availability.close();
            } finally {
                device.unfreezeRotation();
            }
        }
    }

    @SuppressWarnings("deprecation")
    private void assertInstalledPrivacyContract() throws Exception {
        PackageInfo info = target.getPackageManager().getPackageInfo(QA_PACKAGE, PackageManager.GET_PERMISSIONS);
        Set<String> expected = new HashSet<>(Arrays.asList(Manifest.permission.CAMERA,
                QA_PACKAGE + ".DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION"));
        assertNotNull(info.requestedPermissions);
        assertEquals("Exactly the merged app permissions must survive install", expected,
                new HashSet<>(Arrays.asList(info.requestedPermissions)));
        assertEquals("No duplicate installed permission requests", 2, info.requestedPermissions.length);
        assertEquals(36, info.applicationInfo.targetSdkVersion);
        assertTrue("QA must be test-only", (info.applicationInfo.flags & ApplicationInfo.FLAG_TEST_ONLY) != 0);
        assertEquals("Release-like QA has no debugger", 0,
                info.applicationInfo.flags & ApplicationInfo.FLAG_DEBUGGABLE);
        assertEquals("App backup remains disabled", 0,
                info.applicationInfo.flags & ApplicationInfo.FLAG_ALLOW_BACKUP);
        for (String forbidden : Arrays.asList(Manifest.permission.INTERNET, Manifest.permission.RECORD_AUDIO,
                Manifest.permission.READ_EXTERNAL_STORAGE, Manifest.permission.WRITE_EXTERNAL_STORAGE)) {
            assertEquals("App cannot hold " + forbidden, PackageManager.PERMISSION_DENIED,
                    target.checkSelfPermission(forbidden));
        }
    }

    private void launchRealActivity() throws Exception {
        scenario = ActivityScenario.launch(MainActivity.class);
        scenario.onActivity(value -> activity = value);
        await("Bundled React home becomes interactive", () -> onMain(() ->
                findViews(activity.getWindow().getDecorView(), HatsuBackgroundView.class).size() == 1));
        onMain(() -> {
            assertEquals(MainApplication.class, activity.getApplication().getClass());
            assertEquals(MainActivity.class, activity.getClass());
            assertTrue("Secure window remains set", (activity.getWindow().getAttributes().flags
                    & WindowManager.LayoutParams.FLAG_SECURE) != 0);
            return null;
        });
    }

    private void assertRealReactHermesAndHatsu() throws Exception {
        await("Hatsu host follows system animation policy", () -> onMain(() -> {
            HatsuBackgroundView view = onlyView(HatsuBackgroundView.class);
            return view.isAttachedToWindow() && view.isShown() && view.getWidth() > 0
                    && view.getHeight() > 0 && view.isAnimating() == ValueAnimator.areAnimatorsEnabled();
        }));
        String maps = new String(Files.readAllBytes(Paths.get("/proc/self/maps")), java.nio.charset.StandardCharsets.UTF_8);
        for (String library : new String[]{"libhermesvm.so", "libreactnative.so", "libappmodules.so"}) {
            assertTrue("Native library is actually mapped: " + library, maps.contains(library));
        }
        onMain(() -> {
            ReactHost host = ((MainApplication) activity.getApplication()).getReactHost();
            assertNotNull(host.getCurrentReactContext());
            assertTrue("An actual React instance must have produced the UI",
                    host.getCurrentReactContext().hasActiveReactInstance());
            assertNotNull(field(host, "reactInstance"));
            assertEquals(Boolean.FALSE, field(host, "useDevSupport"));
            Object delegate = field(host, "reactHostDelegate");
            Object engine = field(delegate, "jsRuntimeFactory");
            assertEquals("Real initialized host uses the pinned Hermes factory",
                    "com.facebook.react.runtime.hermes.HermesInstance", engine.getClass().getName());
            HatsuBackgroundView view = onlyView(HatsuBackgroundView.class);
            assertEquals("Canonical renderer is actually hosted",
                    "com.hatsunama.refract.RefractBackgroundView", view.getChildAt(0).getClass().getName());
            return null;
        });
    }

    private CameraObservation startAgain() throws Exception {
        findUiLabel(START);
        assertNoCurrentCamera();
        clickUiLabel(START);
        CameraObservation result = awaitStreamingCamera();
        assertFalse("Granted permission must not require another OS dialog", hasPermissionDialog());
        return result;
    }

    private CameraObservation awaitStreamingCamera() throws Exception {
        AtomicReference<CameraObservation> found = new AtomicReference<>();
        await("Real CameraX front preview reaches STREAMING", () -> onMain(() -> {
            List<PrivateCameraView> views = findViews(activity.getWindow().getDecorView(), PrivateCameraView.class);
            if (views.size() != 1) return false;
            PrivateCameraView view = views.get(0);
            Preview preview = (Preview) field(view, "preview");
            ProcessCameraProvider provider = (ProcessCameraProvider) field(view, "provider");
            PreviewView surface = (PreviewView) field(view, "previewView");
            PreviewLease lease = (PreviewLease) field(view, "lease");
            long token = (Long) field(lease, "generation");
            boolean ready = Boolean.TRUE.equals(field(view, "requested")) && lease.isCurrent(token)
                    && preview != null && provider != null && provider.isBound(preview)
                    && view.isAttachedToWindow() && surface.isShown()
                    && surface.getImplementationMode() == PreviewView.ImplementationMode.COMPATIBLE
                    && surface.getPreviewStreamState().getValue() == PreviewView.StreamState.STREAMING;
            if (ready) found.set(new CameraObservation(view, preview, provider, surface, lease, token));
            return ready;
        }));
        await("OS marks the emulated front camera in use", () -> availability.anyFrontUnavailable());
        return found.get();
    }

    private void assertStillStreaming(CameraObservation expected) throws Exception {
        onMain(() -> {
            assertTrue("Action precondition: attached real camera", expected.view.isAttachedToWindow());
            assertEquals(Boolean.TRUE, field(expected.view, "requested"));
            assertTrue("Action precondition: original lease is still current", expected.lease.isCurrent(expected.token));
            assertSame(expected.preview, field(expected.view, "preview"));
            assertTrue("Action precondition: original Preview is still bound", expected.provider.isBound(expected.preview));
            assertEquals("Action precondition: original Preview is still streaming",
                    PreviewView.StreamState.STREAMING, expected.surface.getPreviewStreamState().getValue());
            assertTrue(expected.surface.isShown());
            return null;
        });
    }

    private void clickWhileStreaming(String label, CameraObservation expected) throws Exception {
        UiObject2 control = findUiLabel(label);
        // Scrolling must not have already torn down the camera and made the action vacuously pass.
        assertStillStreaming(expected);
        control.click();
    }

    private void assertReleased(CameraObservation previous) throws Exception {
        await("Owned preview and lease are released", () -> onMain(() ->
                Boolean.FALSE.equals(field(previous.view, "requested"))
                        && !previous.lease.isCurrent(previous.token)
                        && field(previous.view, "preview") == null
                        && field(previous.view, "provider") == null
                        && previous.surface.getVisibility() != View.VISIBLE
                        && !previous.provider.isBound(previous.preview)
                        && previous.surface.getPreviewStreamState().getValue() == PreviewView.StreamState.IDLE));
        await("OS reports all emulated front cameras available again", () -> availability.allFrontAvailable());
        onMain(() -> {
            assertEquals("No cleanup error may be hidden by the UI", 0, ((Number)
                    field(activity, "cameraCleanupFailures")).intValue());
            return null;
        });
    }

    private void assertNoCurrentCamera() throws Exception {
        await("No native camera view remains mounted", () -> onMain(() ->
                findViews(activity.getWindow().getDecorView(), PrivateCameraView.class).isEmpty()));
    }

    private void assertRemainsOff() throws Exception {
        assertNoCurrentCamera();
        // A bounded no-restart observation window catches delayed host/provider callbacks.
        long until = SystemClock.elapsedRealtime() + 1200;
        while (SystemClock.elapsedRealtime() < until) {
            assertTrue("Camera must not remount without another Start", onMain(() ->
                    findViews(activity.getWindow().getDecorView(), PrivateCameraView.class).isEmpty()));
            assertTrue("Emulated front camera stays available", availability.allFrontAvailable());
            SystemClock.sleep(100);
        }
    }

    private UiObject2 findUiLabel(String label) throws Exception {
        long until = SystemClock.elapsedRealtime() + TIMEOUT_MS;
        boolean scrollDown = true;
        while (SystemClock.elapsedRealtime() < until) {
            UiObject2 node = device.findObject(By.pkg(QA_PACKAGE).text(label));
            if (node == null) node = device.findObject(By.pkg(QA_PACKAGE).desc(label));
            if (node != null) return node;
            UiObject2 scroll = device.findObject(By.pkg(QA_PACKAGE).scrollable(true));
            if (scroll != null) {
                if (!scroll.scroll(scrollDown ? Direction.DOWN : Direction.UP, 0.65f)) scrollDown = !scrollDown;
            } else SystemClock.sleep(100);
        }
        throw new AssertionError("Real UI did not show: " + label);
    }

    private void clickUiLabel(String label) throws Exception { findUiLabel(label).click(); }

    private boolean hasPermissionDialog() {
        return device.hasObject(By.res("com.android.permissioncontroller", "permission_deny_button"))
                || device.hasObject(By.res("com.google.android.permissioncontroller", "permission_deny_button"))
                || device.hasObject(By.res("com.android.packageinstaller", "permission_deny_button"))
                || device.hasObject(By.res("com.google.android.packageinstaller", "permission_deny_button"));
    }

    private void clickPermissionButton(String resource) throws Exception {
        AtomicReference<UiObject2> found = new AtomicReference<>();
        await("Real OS permission control: " + resource, () -> {
            UiObject2 button = device.findObject(By.res("com.android.permissioncontroller", resource));
            if (button == null) button = device.findObject(By.res("com.google.android.permissioncontroller", resource));
            if (button == null) button = device.findObject(By.res("com.android.packageinstaller", resource));
            if (button == null) button = device.findObject(By.res("com.google.android.packageinstaller", resource));
            found.set(button);
            return button != null;
        });
        found.get().click();
    }

    private <T extends View> T onlyView(Class<T> type) {
        List<T> views = findViews(activity.getWindow().getDecorView(), type);
        assertEquals("Exactly one real " + type.getSimpleName(), 1, views.size());
        return views.get(0);
    }

    private static <T extends View> List<T> findViews(View root, Class<T> type) {
        List<T> found = new ArrayList<>();
        if (type.isInstance(root)) found.add(type.cast(root));
        if (root instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) root;
            for (int i = 0; i < group.getChildCount(); ++i) found.addAll(findViews(group.getChildAt(i), type));
        }
        return found;
    }

    private static Object field(Object owner, String name) throws Exception {
        Field value = owner.getClass().getDeclaredField(name);
        value.setAccessible(true);
        return value.get(owner);
    }

    private <T> T onMain(Callable<T> work) throws Exception {
        AtomicReference<T> value = new AtomicReference<>();
        AtomicReference<Throwable> error = new AtomicReference<>();
        instrumentation.runOnMainSync(() -> {
            try { value.set(work.call()); } catch (Throwable failure) { error.set(failure); }
        });
        Throwable failure = error.get();
        if (failure instanceof Error) throw (Error) failure;
        if (failure instanceof Exception) throw (Exception) failure;
        return value.get();
    }

    private interface Condition { boolean fulfilled() throws Exception; }
    private static void await(String label, Condition condition) throws Exception {
        long until = SystemClock.elapsedRealtime() + TIMEOUT_MS;
        while (SystemClock.elapsedRealtime() < until) {
            if (condition.fulfilled()) return;
            SystemClock.sleep(100);
        }
        throw new AssertionError("Timed out: " + label);
    }

    private void stage(String label) {
        Bundle evidence = new Bundle();
        evidence.putString("sign_qa_stage", label);
        // Android instrumentation status 2 means in-progress; 0 would falsely end a test.
        evidence.putString(Instrumentation.REPORT_KEY_STREAMRESULT, "SIGN_QA_STAGE " + label + "\n");
        instrumentation.sendStatus(2, evidence); // Only fixed labels, no image or session payload.
    }

    private static final class CameraObservation {
        final PrivateCameraView view;
        final Preview preview;
        final ProcessCameraProvider provider;
        final PreviewView surface;
        final PreviewLease lease;
        final long token;
        CameraObservation(PrivateCameraView view, Preview preview, ProcessCameraProvider provider,
                PreviewView surface, PreviewLease lease, long token) {
            this.view = view; this.preview = preview; this.provider = provider;
            this.surface = surface; this.lease = lease; this.token = token;
        }
    }

    /** Reads only camera IDs, lens direction and availability; never opens or captures a camera. */
    private static final class FrontCameraAvailability implements AutoCloseable {
        private final CameraManager manager;
        private final Set<String> front = new HashSet<>();
        private final Map<String, Boolean> available = new ConcurrentHashMap<>();
        private final CameraManager.AvailabilityCallback callback = new CameraManager.AvailabilityCallback() {
            @Override public void onCameraAvailable(String cameraId) { available.put(cameraId, true); }
            @Override public void onCameraUnavailable(String cameraId) { available.put(cameraId, false); }
        };
        FrontCameraAvailability(Context context) throws Exception {
            manager = (CameraManager) context.getSystemService(Context.CAMERA_SERVICE);
            assertNotNull(manager);
            for (String cameraId : manager.getCameraIdList()) {
                if (Integer.valueOf(CameraCharacteristics.LENS_FACING_FRONT).equals(
                        manager.getCameraCharacteristics(cameraId).get(CameraCharacteristics.LENS_FACING))) {
                    front.add(cameraId);
                }
            }
            assertFalse("The approved emulator must provide a generated front camera", front.isEmpty());
            manager.registerAvailabilityCallback(callback, new Handler(Looper.getMainLooper()));
        }
        boolean anyFrontUnavailable() {
            for (String cameraId : front) if (Boolean.FALSE.equals(available.get(cameraId))) return true;
            return false;
        }
        boolean allFrontAvailable() {
            for (String cameraId : front) if (!Boolean.TRUE.equals(available.get(cameraId))) return false;
            return true;
        }
        @Override public void close() { manager.unregisterAvailabilityCallback(callback); }
    }
}
