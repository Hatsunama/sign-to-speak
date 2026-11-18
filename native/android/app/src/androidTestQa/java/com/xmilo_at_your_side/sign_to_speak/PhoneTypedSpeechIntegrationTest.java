package com.xmilo_at_your_side.sign_to_speak;

import static org.junit.Assert.*;

import android.Manifest;
import android.app.Instrumentation;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Rect;
import android.media.AudioTrack;
import android.os.Build;
import android.os.SystemClock;
import android.view.View;
import android.view.ViewGroup;
import android.view.ViewParent;
import android.view.WindowManager;
import android.widget.ScrollView;
import android.widget.TextView;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowInsetsCompat;
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
        awaitStatus("Use 1 to 256 characters. Edit the text and try again.");
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

    @Test(timeout = 150000) public void accentedDraftPlaysAndUnsupportedUnicodeKeepsReviewedDraft() throws Exception {
        String accented = "Caf\u00e9 is ready.";
        input(accented);
        tap("typed-speech-speak", "Speak typed text");
        Playback supported = awaitPlaying();
        awaitStatus("Typed text spoken.");
        assertPlaybackCleaned(supported);
        assertEquals("Completed speech keeps reviewed text visible", accented, findInput().getText());

        for (String unsupported : new String[]{"Ready \u6771\u4eac.", "Ready \ud83d\ude42."}) {
            input(unsupported);
            tap("typed-speech-speak", "Speak typed text");
            awaitStatus("This text is unsupported by the local English voice. Edit it and try again.");
            assertEquals("Unsupported draft remains available for editing", unsupported, findInput().getText());
            assertNull("Unsupported Unicode never leaves a native audio job", currentJob());
        }

        input("Caf\u00e9 is ready again.");
        tap("typed-speech-speak", "Speak typed text");
        Playback recovered = awaitPlaying();
        awaitStatus("Typed text spoken.");
        assertPlaybackCleaned(recovered);
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
        try {
            await("UI status: " + expected, WAIT_MS,
                    () -> onMain(() -> hasText(activity.getWindow().getDecorView(), expected)));
        } catch (AssertionError failure) {
            // Snapshot after the unchanged wait so diagnostics cannot affect the tap or status polling.
            String diagnostics;
            try {
                diagnostics = onMain(this::nativeStatusDiagnostics);
            } catch (Exception | AssertionError | LinkageError unavailable) {
                diagnostics = "native=diagnostic_unavailable";
            }
            try {
                UiObject2 speak = findNode("typed-speech-speak");
                diagnostics += speak == null ? " uiSpeak=missing" :
                        " uiSpeakVisibleScreenPx=" + speak.getVisibleBounds().toShortString()
                                + " uiSpeakClickable=" + speak.isClickable()
                                + " uiSpeakEnabled=" + speak.isEnabled();
            } catch (RuntimeException | AssertionError | LinkageError unavailable) {
                diagnostics += " uiSpeak=diagnostic_unavailable";
            }
            throw new AssertionError(failure.getMessage() + " [at failure: " + diagnostics + "]", failure);
        }
    }

    private String nativeStatusDiagnostics() {
        View decor = activity.getWindow().getDecorView();
        View status = findReactView(decor, "typed-speech-status");
        View input = findReactView(decor, "typed-speech-input");
        View speak = findReactView(decor, "typed-speech-speak");
        WindowInsetsCompat insets = ViewCompat.getRootWindowInsets(decor);
        String diagnostics = "status=" + statusCode(status)
                + " resumed=" + activity.getLifecycle().getCurrentState().isAtLeast(Lifecycle.State.RESUMED)
                + " windowFocused=" + decor.hasWindowFocus()
                + " capturePrivacyReady=" + ((MainApplication) activity.getApplication()).getCapturePrivacyReady()
                + " secureWindow=" + ((activity.getWindow().getAttributes().flags
                        & WindowManager.LayoutParams.FLAG_SECURE) != 0)
                + " imeVisible=" + (insets == null ? "unknown" : insets.isVisible(WindowInsetsCompat.Type.ime()));
        if (input instanceof TextView) {
            CharSequence draft = ((TextView) input).getText();
            int length = draft.length();
            diagnostics += " draftLength=" + length + " draftLengthInRange=" + (length >= 1 && length <= 256)
                    + " draftMatchesOverlengthFixture=" + repeat('x', 257).contentEquals(draft)
                    + " inputFocused=" + input.hasFocus();
        } else {
            diagnostics += " input=missing";
        }
        diagnostics += speak == null ? " nativeSpeak=missing" :
                " nativeSpeak=" + bounds(speak) + " nativeSpeakEnabled=" + speak.isEnabled();
        View control = speak == null ? input : speak;
        ViewParent firstParent = control == null ? null : control.getParent();
        for (ViewParent parent = firstParent; parent instanceof View; parent = parent.getParent()) {
            if (parent instanceof ScrollView) {
                ScrollView scroll = (ScrollView) parent;
                return diagnostics + " scrollViewport=" + bounds(scroll) + " scrollY=" + scroll.getScrollY();
            }
        }
        return diagnostics + " scrollViewport=missing";
    }

    private static View findReactView(View view, String id) {
        if (id.equals(view.getTag(com.facebook.react.R.id.react_test_id))) return view;
        if (view instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) view;
            for (int i = 0; i < group.getChildCount(); i++) {
                View match = findReactView(group.getChildAt(i), id);
                if (match != null) return match;
            }
        }
        return null;
    }

    private static String bounds(View view) {
        int[] origin = new int[2];
        view.getLocationOnScreen(origin);
        Rect full = new Rect(origin[0], origin[1], origin[0] + view.getWidth(), origin[1] + view.getHeight());
        Rect visible = new Rect();
        boolean hasVisibleBounds = view.isShown() && view.getLocalVisibleRect(visible);
        visible.offset(origin[0], origin[1]);
        return "{fullScreenPx=" + full.toShortString()
                + " visibleScreenPx=" + (hasVisibleBounds ? visible.toShortString() : "none") + "}";
    }

    private static String statusCode(View status) {
        // Only fixed codes can reach the failure message, even if the UI text changes.
        if (!(status instanceof TextView)) return "missing";
        switch (((TextView) status).getText().toString()) {
            case "Type text, review it, then tap Speak typed text.": return "initial";
            case "Review the text, then tap Speak typed text.": return "review";
            case "Text changed. Review it and tap Speak typed text again.": return "edited_during_speech";
            case "Enter text before speaking.": return "empty_input";
            case "Use 1 to 256 characters. Edit the text and try again.": return "overlength";
            case "Preparing local speech...": return "preparing";
            case "Speaking typed text...": return "speaking";
            case "Typed text spoken.": return "completed";
            case "Speech stopped.": return "stopped";
            case "Speech stopped. Type text to speak again.": return "stopped_and_cleared";
            case "Speech was cancelled.": return "cancelled";
            case "Typed text cleared.": return "cleared";
            case "Speech is busy. Try again.": return "busy";
            case "Local speech is unavailable. Try again when the app is active.": return "unavailable";
            case "This text is unsupported by the local English voice. Edit it and try again.": return "unsupported";
            case "Speech cleanup could not be confirmed. Fully close and reopen the app before speaking again.":
                return "cleanup_failed";
            case "Speech could not be completed. Try again.": return "failed";
            default: return "unrecognized";
        }
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
        await("button reached real Pocket PCM and advancing protected AudioTrack", WAIT_MS, () -> {
            Object job = currentJob();
            if (job == null) return false;
            short[] pcm = (short[]) value(job, "pcm");
            Object player = value(job, "player");
            if (pcm == null || player == null) return false;
            assertTrue("24 kHz PCM must be bounded to 30 seconds", pcm.length > 0
                    && pcm.length <= 720_000);
            boolean nonzero = false;
            for (short sample : pcm) if (sample != 0) { nonzero = true; break; }
            if (!nonzero) return false;
            synchronized (value(player, "lock")) {
                AudioTrack track = (AudioTrack) value(player, "activeTrack");
                if (track == null || track.getPlayState() != AudioTrack.PLAYSTATE_PLAYING
                        || (track.getPlaybackHeadPosition() & 0xffffffffL) == 0) return false;
                assertEquals("Pocket playback sample rate", 24_000, track.getSampleRate());
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





