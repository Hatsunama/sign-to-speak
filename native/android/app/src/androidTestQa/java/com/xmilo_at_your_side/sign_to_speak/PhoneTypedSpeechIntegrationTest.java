package com.xmilo_at_your_side.sign_to_speak;

import static org.junit.Assert.*;

import android.Manifest;
import android.app.Instrumentation;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.media.AudioTrack;
import android.os.Build;
import android.os.SystemClock;
import android.view.View;
import android.view.ViewGroup;
import android.widget.TextView;
import androidx.lifecycle.Lifecycle;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import androidx.test.uiautomator.By;
import androidx.test.uiautomator.Direction;
import androidx.test.uiautomator.UiDevice;
import androidx.test.uiautomator.UiObject2;
import com.facebook.react.ReactHost;
import com.facebook.react.bridge.ReactApplicationContext;
import java.lang.reflect.Field;
import java.util.Arrays;
import java.util.concurrent.Callable;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;

/** UI taps through the installed QA React screen into its registered bridge and real phone audio. */
@RunWith(AndroidJUnit4.class)
public final class PhoneTypedSpeechIntegrationTest {
    private static final String QA_PACKAGE = "com.xmilo_at_your_side.sign_to_speak.qa";
    private static final long WAIT_MS = 45000;
    private static final String LONG_TEXT =
            "This is synthetic local speech from the typed text screen. It must reach the " +
            "real phone speaker through the registered bridge and the protected audio track. " +
            "The playback head must advance before the local speech journey ends.";
    private final Instrumentation instrumentation = InstrumentationRegistry.getInstrumentation();
    private final Context target = instrumentation.getTargetContext();
    private final UiDevice device = UiDevice.getInstance(instrumentation);
    private PhoneActivityDriver driver;
    private MainActivity activity;

    @Before public void launch() throws Exception {
        assertEquals(QA_PACKAGE, target.getPackageName());
        assertEquals(QA_PACKAGE + ".test", instrumentation.getContext().getPackageName());
        assertTrue("Physical ARM64 phone required", Arrays.asList(Build.SUPPORTED_ABIS).contains("arm64-v8a"));
        assertFalse("Emulator audio cannot certify this journey", Build.FINGERPRINT.contains("generic")
                || Build.MODEL.toLowerCase().contains("emulator"));
        assertCameraDenied();
        driver = new PhoneActivityDriver(instrumentation);
        activity = driver.launch();
        await("real ReactContext with registered LocalSpeech bridge", WAIT_MS, () -> {
            ReactHost host = ((MainApplication) activity.getApplication()).getReactHost();
            if (!(host.getCurrentReactContext() instanceof ReactApplicationContext)) return false;
            ReactApplicationContext react = (ReactApplicationContext) host.getCurrentReactContext();
            return react.hasActiveReactInstance() && react.getCurrentActivity() == activity
                    && react.getNativeModule("LocalSpeech") instanceof LocalSpeechModule;
        });
        assertTrue(((MainApplication) activity.getApplication()).getCapturePrivacyReady());
        findInput();
    }

    @After public void close() throws Exception {
        if (driver != null) driver.close();
    }

    @Test(timeout = 150000) public void typedInputValidatesPlaysCompletesAndBackgroundClearsDraft() throws Exception {
        input(repeat('x', 257));
        tap("typed-speech-speak", "Speak typed text");
        awaitStatus("Use 1 to 256 printable ASCII characters. Edit the text and try again.");
        assertNull("Invalid UI input never creates a native job", currentJob());
        assertCameraDenied();

        input(LONG_TEXT);
        tap("typed-speech-speak", "Speak typed text");
        Playback first = awaitPlaying();
        awaitStatus("Typed text spoken.");
        assertPlaybackCleaned(first);
        assertNull("Completed worker releases its job", currentJob());
        assertCameraDenied();

        assertTrue(device.pressHome());
        await("Home pauses real Activity", 10000, () -> !activity.getLifecycle()
                .getCurrentState().isAtLeast(Lifecycle.State.RESUMED));
        Intent reopen = target.getPackageManager().getLaunchIntentForPackage(QA_PACKAGE);
        assertNotNull(reopen);
        target.startActivity(reopen.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_REORDER_TO_FRONT));
        await("Activity resumes after Home", 10000, () -> activity.getLifecycle()
                .getCurrentState().isAtLeast(Lifecycle.State.RESUMED));
        await("background clears typed draft", 10000, () -> emptyInput());
        assertNull("Background never resumes native speech", currentJob());
        assertCameraDenied();

        input(LONG_TEXT);
        tap("typed-speech-speak", "Speak typed text");
        Playback retry = awaitPlaying();
        awaitStatus("Typed text spoken.");
        assertPlaybackCleaned(retry);
        tap("typed-speech-clear", "Clear typed text");
        await("explicit Clear empties the input", 5000, () -> emptyInput());
        assertCameraDenied();
    }

    @Test(timeout = 120000) public void stopTapCancelsOwnedTrackAndExplicitRetryPlays() throws Exception {
        input(LONG_TEXT);
        tap("typed-speech-speak", "Speak typed text");
        Playback playing = awaitPlaying();
        tap("typed-speech-stop", "Stop speech");
        awaitStatus("Speech stopped.");
        await("Stop UI promptly revokes the owned AudioTrack", 2000,
                () -> trackStopped(playing.player));
        assertPlaybackCleaned(playing);
        assertNull("Cancelled worker releases its job", currentJob());
        assertCameraDenied();

        tap("typed-speech-speak", "Speak typed text");
        Playback retry = awaitPlaying();
        awaitStatus("Typed text spoken.");
        assertPlaybackCleaned(retry);
        assertCameraDenied();
    }

    private static String repeat(char character, int count) {
        char[] chars = new char[count];
        Arrays.fill(chars, character);
        return new String(chars);
    }

    private void assertCameraDenied() {
        assertEquals("Speech must work with CAMERA denied; configure this before launching QA",
                PackageManager.PERMISSION_DENIED, target.checkSelfPermission(Manifest.permission.CAMERA));
    }

    private UiObject2 findInput() throws Exception {
        UiObject2 found = findNode("typed-speech-input");
        if (found != null) return found;
        scrollFor("typed-speech-input", "Text to speak");
        found = findNode("typed-speech-input");
        assertNotNull("Actual React TextInput must be accessible", found);
        return found;
    }

    private boolean emptyInput() throws Exception {
        String visible = findInput().getText();
        return visible == null || visible.isEmpty() || "Type text here".equals(visible);
    }

    private void input(String text) throws Exception {
        UiObject2 field = findInput();
        field.setText(text);
        await("TextInput receives synthetic text", 5000,
                () -> text.equals(findInput().getText()));
        instrumentation.runOnMainSync(() -> {
            android.view.inputmethod.InputMethodManager keyboard =
                    (android.view.inputmethod.InputMethodManager) activity.getSystemService(Context.INPUT_METHOD_SERVICE);
            keyboard.hideSoftInputFromWindow(activity.getWindow().getDecorView().getWindowToken(), 0);
        });
    }

    private void tap(String id, String label) throws Exception {
        scrollFor(id, label);
        UiObject2 node = findNode(id);
        assertNotNull("Actual React control: " + label, node);
        assertTrue("Clickable actual React control: " + label, node.isClickable());
        assertTrue("Enabled actual React control: " + label, node.isEnabled());
        node.click(100L);
    }

    private UiObject2 findNode(String id) {
        return device.findObject(By.res(id).pkg(QA_PACKAGE));
    }

    private void scrollFor(String id, String label) throws Exception {
        long until = SystemClock.elapsedRealtime() + 15000;
        boolean down = true;
        while (SystemClock.elapsedRealtime() < until) {
            UiObject2 node = findNode(id);
            UiObject2 scroll = device.findObject(By.pkg(QA_PACKAGE).scrollable(true));
            if (node != null && node.isClickable() && !node.getVisibleBounds().isEmpty()
                    && node.getVisibleBounds().top >= 0
                    && node.getVisibleBounds().bottom <= device.getDisplayHeight()
                    && (scroll == null || scroll.getVisibleBounds().contains(node.getVisibleBounds()))) return;
            if (scroll == null) SystemClock.sleep(100);
            else if (!scroll.scroll(down ? Direction.DOWN : Direction.UP, 0.65f)) down = !down;
        }
        throw new AssertionError("Cannot reach actual React control: " + label);
    }

    private void awaitStatus(String expected) throws Exception {
        await("UI status: " + expected, WAIT_MS,
                () -> onMain(() -> hasText(activity.getWindow().getDecorView(), expected)));
    }

    private static boolean hasText(View view, String text) {
        if (view instanceof TextView && text.contentEquals(((TextView) view).getText())) return true;
        if (view instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) view;
            for (int i = 0; i < group.getChildCount(); i++)
                if (hasText(group.getChildAt(i), text)) return true;
        }
        return false;
    }

    private Playback awaitPlaying() throws Exception {
        final Playback[] observed = new Playback[1];
        await("button reached real Flite PCM and advancing protected AudioTrack", WAIT_MS, () -> {
            Object job = currentJob();
            if (job == null) return false;
            short[] pcm = (short[]) value(job, "pcm");
            Object player = value(job, "player");
            if (pcm == null || player == null) return false;
            boolean nonzero = false;
            for (short sample : pcm) if (sample != 0) { nonzero = true; break; }
            if (!nonzero) return false;
            synchronized (value(player, "lock")) {
                AudioTrack track = (AudioTrack) value(player, "activeTrack");
                if (track == null || track.getPlayState() != AudioTrack.PLAYSTATE_PLAYING
                        || (track.getPlaybackHeadPosition() & 0xffffffffL) == 0) return false;
                observed[0] = new Playback(pcm, player);
                return true;
            }
        });
        return observed[0];
    }

    private boolean trackStopped(Object player) throws Exception {
        synchronized (value(player, "lock")) {
            AudioTrack track = (AudioTrack) value(player, "activeTrack");
            return track == null || track.getPlayState() != AudioTrack.PLAYSTATE_PLAYING;
        }
    }

    private void assertPlaybackCleaned(Playback playback) throws Exception {
        await("native worker releases AudioTrack", WAIT_MS, () -> currentJob() == null);
        for (short sample : playback.pcm) assertEquals("PCM zeroized after playback", 0, sample);
        synchronized (value(playback.player, "lock")) {
            assertNull("AudioTrack released after playback", value(playback.player, "activeTrack"));
        }
    }

    private Object currentJob() throws Exception {
        Object owner = ((kotlin.Lazy<?>) value(activity, "speechOwner$delegate")).getValue();
        Object session = value(owner, "session");
        if (session == null) return null;
        Object gate = value(session, "gate");
        synchronized (gate) { return value(session, "current"); }
    }

    private static Object value(Object owner, String name) throws Exception {
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
    private static void await(String label, long timeoutMs, Condition condition) throws Exception {
        long until = SystemClock.elapsedRealtime() + timeoutMs;
        while (SystemClock.elapsedRealtime() < until) {
            if (condition.ready()) return;
            SystemClock.sleep(25);
        }
        throw new AssertionError("Timed out: " + label);
    }

    private static final class Playback {
        final short[] pcm;
        final Object player;
        Playback(short[] pcm, Object player) { this.pcm = pcm; this.player = player; }
    }
}





