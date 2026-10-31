package com.xmilo_at_your_side.sign_to_speak;

import static org.junit.Assert.*;

import android.app.Instrumentation;
import android.content.Context;
import android.content.Intent;
import android.media.AudioTrack;
import android.os.Build;
import android.os.SystemClock;
import androidx.lifecycle.Lifecycle;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import androidx.test.uiautomator.UiDevice;
import com.facebook.react.ReactHost;
import com.facebook.react.bridge.Promise;
import com.facebook.react.bridge.ReactApplicationContext;
import java.io.File;
import java.io.FilterInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.lang.reflect.Field;
import java.lang.reflect.Proxy;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.util.Arrays;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;

/**
 * Physical ARM64 integration journey. Uses the installed QA Activity, its real ReactContext,
 * production LocalSpeechModule, Pocket JNI, and protected AudioTrack. Synthetic reviewed text only.
 * Reflection reads state under production locks. Five bridge cases use the production factory;
 * the staging I/O case uses a separate session and a test-owned asset stream gate.
 */
@RunWith(AndroidJUnit4.class)
public final class PhoneSpeechIntegrationTest {
    private static final long WAIT_MS = 45000;
    private static final String LONG_TEXT =
            "This is a private local speech test. The audio should be heard from this phone. " +
            "The playback head must advance while these synthetic words are spoken aloud. " +
            "Native cancellation must stop this sound when the activity leaves the foreground.";
    private final Instrumentation instrumentation = InstrumentationRegistry.getInstrumentation();
    private final Context target = instrumentation.getTargetContext();
    private final UiDevice device = UiDevice.getInstance(instrumentation);
    private PhoneActivityDriver driver;
    private MainActivity activity;
    private LocalSpeechModule module;

    @Before public void launch() throws Exception {
        assertTrue("Run on a physical ARM64 phone", Arrays.asList(Build.SUPPORTED_ABIS).contains("arm64-v8a"));
        assertFalse("Emulators cannot certify phone audio", Build.FINGERPRINT.contains("generic")
                || Build.MODEL.toLowerCase().contains("emulator"));
        driver = new PhoneActivityDriver(instrumentation);
        activity = driver.launch();
        await("real ReactContext and production module", () -> {
            ReactHost host = ((MainApplication) activity.getApplication()).getReactHost();
            if (!(host.getCurrentReactContext() instanceof ReactApplicationContext)) return false;
            ReactApplicationContext react = (ReactApplicationContext) host.getCurrentReactContext();
            if (!react.hasActiveReactInstance() || react.getCurrentActivity() != activity) return false;
            Object registered = react.getNativeModule("LocalSpeech");
            if (!(registered instanceof LocalSpeechModule)) return false;
            module = (LocalSpeechModule) registered;
            return true;
        });
        assertTrue(((MainApplication) activity.getApplication()).getCapturePrivacyReady());
    }

    @After public void close() throws Exception {
        if (driver != null) driver.close();
    }

    @Test public void realPocketPcmProtectedPlaybackAndInputBoundaries() throws Exception {
        String token = begin();
        assertNotNull("Explicit begin must work on resumed real Activity", token);
        for (String invalid : new String[]{"", " ", "line\nfeed", repeat('x', 257)}) {
            assertEquals("INVALID_INPUT", speak(token, invalid).get(5, TimeUnit.SECONDS));
        }
        assertNull("Rejected input must never start a job", currentJob());

        CompletableFuture<Object> result = speak(token, LONG_TEXT);
        AudioEvidence evidence = observeRealPlayback(result);
        assertTrue("Pocket returned nonzero PCM", evidence.nonzeroPcm);
        assertTrue("Protected AudioTrack entered PLAYING", evidence.playing);
        assertTrue("AudioTrack playback head advanced", evidence.maxHead > 0);
        assertEquals("COMPLETED", result.get(WAIT_MS, TimeUnit.MILLISECONDS));
        assertTrue("24 kHz PCM must be bounded to 30 seconds", evidence.pcmFrames > 0
                && evidence.pcmFrames <= 720_000);
        assertTrue("Observe playback timing before completion", evidence.firstHeadAt > 0);
        long remainingMs = Math.max(0, evidence.pcmFrames - evidence.firstHead) / 24;
        assertTrue("Completion must wait for real AudioTrack drain",
                evidence.completionAt - evidence.firstHeadAt + 200 >= remainingMs);
        assertCleaned(evidence.pcm, evidence.player);
        assertNull("Worker releases completed job", currentJob());
    }

    @Test public void accentedUtf8PlaysAndUnsupportedUnicodeRejectsWithoutSubstitution() throws Exception {
        String token = begin();
        assertNotNull("Explicit begin must work on resumed real Activity", token);

        // Each string is valid Unicode within the bridge's length limits. A permissive
        // tokenizer must not drop its unsupported code point and speak the ASCII prefix.
        for (String unsupported : new String[]{
                "Ready \u6771\u4eac.", "Ready \ud83d\ude42.", "Ready \udbff\udfff."}) {
            assertEquals("Unsupported Unicode must reach Pocket and report INVALID_INPUT: " + unsupported,
                    "INVALID_INPUT", speak(token, unsupported).get(WAIT_MS, TimeUnit.MILLISECONDS));
            assertNull("Rejected text must not leave an audio job", currentJob());
        }

        CompletableFuture<Object> recovered = speak(token, "Caf\u00e9 is ready.");
        AudioEvidence audio = observeRealPlayback(recovered);
        assertTrue("Supported accented UTF-8 produces nonzero Pocket PCM", audio.nonzeroPcm);
        assertTrue("Supported accented UTF-8 reaches protected AudioTrack", audio.playing && audio.maxHead > 0);
        assertTrue("Pocket PCM stays within the 30 second bound", audio.pcmFrames > 0
                && audio.pcmFrames <= 720_000);
        assertEquals("COMPLETED", recovered.get(WAIT_MS, TimeUnit.MILLISECONDS));
        assertCleaned(audio.pcm, audio.player);
        assertNull("Recovered worker releases its job", currentJob());
    }

    @Test public void cancelStopsOwnedRealTrackBeforeReturning() throws Exception {
        String token = begin();
        assertNotNull("Explicit begin must work on resumed real Activity", token);
        CompletableFuture<Object> result = speak(token, LONG_TEXT);
        final AudioTrack[] ownedTrack = new AudioTrack[1];
        final short[][] pcm = new short[1][];
        final Object[] player = new Object[1];
        await("real AudioTrack playing with advancing playback head", () -> {
            assertFalse("Speech finished before cancellation precondition", result.isDone());
            Object job = currentJob();
            if (job == null) return false;
            Object candidatePlayer = value(job, "player");
            short[] candidatePcm = (short[]) value(job, "pcm");
            if (candidatePlayer == null || candidatePcm == null) return false;
            synchronized (value(candidatePlayer, "lock")) {
                AudioTrack candidate = (AudioTrack) value(candidatePlayer, "activeTrack");
                if (candidate == null || candidate.getPlayState() != AudioTrack.PLAYSTATE_PLAYING
                        || (candidate.getPlaybackHeadPosition() & 0xffffffffL) == 0) return false;
                assertEquals("Pocket playback sample rate", 24_000, candidate.getSampleRate());
                ownedTrack[0] = candidate;
                pcm[0] = candidatePcm;
                player[0] = candidatePlayer;
                return true;
            }
        });
        module.cancel(token);
        assertTrue("Owned AudioTrack must stop before cancel returns",
                ownedTrack[0].getState() == AudioTrack.STATE_UNINITIALIZED
                        || ownedTrack[0].getPlayState() != AudioTrack.PLAYSTATE_PLAYING);
        assertEquals("CANCELLED", result.get(WAIT_MS, TimeUnit.MILLISECONDS));
        assertCleaned(pcm[0], player[0]);
        assertNull("Worker releases cancelled job", currentJob());
        assertEquals("UNAVAILABLE", speak(token, "Cancelled session.").get(5, TimeUnit.SECONDS));

        String recovered = begin();
        assertNotNull("Recovery requires explicit new begin", recovered);
        CompletableFuture<Object> recoveredSpeech = speak(recovered, "Recovery is explicit.");
        AudioEvidence recoveredAudio = observeRealPlayback(recoveredSpeech);
        assertTrue(recoveredAudio.nonzeroPcm && recoveredAudio.maxHead > 0);
        assertEquals("COMPLETED", recoveredSpeech.get(WAIT_MS, TimeUnit.MILLISECONDS));
        assertCleaned(recoveredAudio.pcm, recoveredAudio.player);
    }

    @Test public void staleTokenCannotCancelNewSession() throws Exception {
        String stale = begin();
        String current = begin();
        assertNotEquals(stale, current);
        module.cancel(stale);
        assertEquals("UNAVAILABLE", speak(stale, "Old session.").get(5, TimeUnit.SECONDS));
        CompletableFuture<Object> result = speak(current, LONG_TEXT);
        awaitPlaybackStarted(result);
        module.cancel(stale); // An old JS callback must not revoke current production audio.
        AudioEvidence evidence = observeRealPlayback(result);
        assertTrue(evidence.playing && evidence.nonzeroPcm && evidence.maxHead > 0);
        assertEquals("COMPLETED", result.get(WAIT_MS, TimeUnit.MILLISECONDS));
        assertCleaned(evidence.pcm, evidence.player);
    }

    @Test public void homeBackAndDestroyRevokeWithoutAutomaticResume() throws Exception {
        String homeToken = begin();
        CompletableFuture<Object> homeSpeech = speak(homeToken, LONG_TEXT);
        PlaybackObservation homeAudio = awaitPlaybackStarted(homeSpeech);
        assertTrue(device.pressHome());
        await("Home pauses real Activity", () -> !activity.getLifecycle().getCurrentState()
                .isAtLeast(Lifecycle.State.RESUMED));
        assertEquals("CANCELLED", homeSpeech.get(WAIT_MS, TimeUnit.MILLISECONDS));
        assertCleaned(homeAudio.pcm, homeAudio.player);
        bringToFront();
        assertEquals("UNAVAILABLE", speak(homeToken, "Never resume.").get(5, TimeUnit.SECONDS));
        assertNull("No automatic playback after Home", currentJob());

        String backToken = begin();
        CompletableFuture<Object> backSpeech = speak(backToken, LONG_TEXT);
        PlaybackObservation backAudio = awaitPlaybackStarted(backSpeech);
        assertTrue(device.pressBack());
        assertEquals("CANCELLED", backSpeech.get(WAIT_MS, TimeUnit.MILLISECONDS));
        assertCleaned(backAudio.pcm, backAudio.player);
        // Back may finish this Activity depending on the JS navigator.
        if (activity.isDestroyed() || activity.isFinishing()) relaunch();
        else if (!activity.getLifecycle().getCurrentState().isAtLeast(Lifecycle.State.RESUMED))
            bringToFront();
        assertEquals("UNAVAILABLE", speak(backToken, "Never resume.").get(5, TimeUnit.SECONDS));

        String destroyToken = begin();
        CompletableFuture<Object> destroySpeech = speak(destroyToken, LONG_TEXT);
        PlaybackObservation destroyAudio = awaitPlaybackStarted(destroySpeech);
        MainActivity old = activity;
        driver.close();
        driver = null;
        await("real Activity destruction", old::isDestroyed);
        assertEquals("CANCELLED", destroySpeech.get(WAIT_MS, TimeUnit.MILLISECONDS));
        assertCleaned(destroyAudio.pcm, destroyAudio.player);
        relaunch();
        assertEquals("UNAVAILABLE", speak(destroyToken, "Never resume.").get(5, TimeUnit.SECONDS));
        assertNull("Reopen never starts playback", currentJob());
        String recovered = begin();
        assertNotNull("Recovery requires explicit new begin", recovered);
        CompletableFuture<Object> recoveredSpeech = speak(recovered, "Recovery is explicit.");
        AudioEvidence recoveredAudio = observeRealPlayback(recoveredSpeech);
        assertTrue(recoveredAudio.nonzeroPcm && recoveredAudio.maxHead > 0);
        assertEquals("COMPLETED", recoveredSpeech.get(WAIT_MS, TimeUnit.MILLISECONDS));
        assertCleaned(recoveredAudio.pcm, recoveredAudio.player);
    }

    @Test public void stopDuringBundledModelReadCancelsBeforeNativeRequest() throws Exception {
        File qaParent = Files.createTempDirectory(target.getNoBackupFilesDir().getCanonicalFile()
                .toPath(), "qa-pocket-cancel-").toFile();
        CountDownLatch readEntered = new CountDownLatch(1);
        CountDownLatch releaseRead = new CountDownLatch(1);
        AtomicBoolean firstRead = new AtomicBoolean();
        AtomicInteger readCalls = new AtomicInteger();
        AtomicInteger nativeRequests = new AtomicInteger();
        AtomicInteger syntheses = new AtomicInteger();
        AtomicInteger players = new AtomicInteger();
        CompletableFuture<SpeechSession.Result> result = new CompletableFuture<>();
        PocketModelStore store = new PocketModelStore();
        PocketModelStore.AssetSource assets = name -> {
            InputStream bundled = target.getAssets().open("pocket-alba/" + name);
            if (!name.equals("model.safetensors")) return bundled;
            return new FilterInputStream(bundled) {
                @Override public int read(byte[] buffer, int offset, int length) throws IOException {
                    if (readCalls.incrementAndGet() != 1)
                        throw new IOException("Cancelled model read continued");
                    int count = super.read(buffer, offset, length);
                    if (count > 0 && firstRead.compareAndSet(false, true)) {
                        readEntered.countDown();
                        try {
                            if (!releaseRead.await(10, TimeUnit.SECONDS))
                                throw new IOException("Timed out holding first bundled model read");
                        } catch (InterruptedException interrupted) {
                            Thread.currentThread().interrupt();
                            throw new IOException("Bundled model read interrupted", interrupted);
                        }
                    }
                    return count;
                }
            };
        };
        SpeechSession session = new SpeechSession(new SpeechSession.Factory() {
            @Override public SpeechSession.Request newRequest(java.util.function.BooleanSupplier cancelled) {
                try {
                    File root = store.prepare(qaParent, assets, cancelled);
                    if (cancelled.getAsBoolean()) throw new IOException("Speech request cancelled");
                    nativeRequests.incrementAndGet();
                    PocketTtsRequest request = new PocketTtsRequest(root.getAbsolutePath());
                    return new SpeechSession.Request() {
                        @Override public short[] synthesize(byte[] utf8) {
                            syntheses.incrementAndGet();
                            return request.synthesize(utf8);
                        }
                        @Override public void cancel() { request.cancel(); }
                        @Override public void close() { request.close(); }
                    };
                } catch (IOException unavailable) {
                    throw new IllegalStateException("Local speech unavailable", unavailable);
                }
            }
            @Override public SpeechSession.Player newPlayer() {
                players.incrementAndGet();
                return new ProtectedPcmPlayer(target, () -> true, () -> {});
            }
        });
        boolean speechRequested = false;
        Throwable testFailure = null;
        try {
            long token = session.begin(true);
            assertTrue(token != 0);
            speechRequested = true;
            session.speak(token, SpeechSession.encodeText("Cancel model preparation."), result::complete);
            assertTrue("Real bundled asset read must start", readEntered.await(10, TimeUnit.SECONDS));
            assertFalse("Preparation must still be in flight", result.isDone());
            session.stop();
            releaseRead.countDown();
            assertEquals("CANCELLED", SpeechSession.Result.CANCELLED,
                    result.get(5, TimeUnit.SECONDS));
            assertEquals("Cancellation stops before a second bundled model read", 1, readCalls.get());
            assertEquals("Cancellation must precede Pocket native creation", 0, nativeRequests.get());
            assertEquals("No synthesis after cancelled preparation", 0, syntheses.get());
            assertEquals("No player after cancelled preparation", 0, players.get());
            assertArrayEquals("Cancelled QA staging parent must be empty", new String[0], qaParent.list());

            // Recovery uses the registered production bridge; this isolated factory only probes staging I/O.
            String recovered = begin();
            assertNotNull("Retry requires explicit begin", recovered);
            CompletableFuture<Object> recoveredSpeech = speak(recovered, "Recovery is explicit.");
            AudioEvidence audio = observeRealPlayback(recoveredSpeech);
            assertTrue(audio.nonzeroPcm && audio.maxHead > 0);
            assertEquals("COMPLETED", recoveredSpeech.get(WAIT_MS, TimeUnit.MILLISECONDS));
            assertCleaned(audio.pcm, audio.player);
        } catch (Throwable failure) {
            testFailure = failure;
            throw failure;
        } finally {
            Throwable cleanupFailure = null;
            try { session.stop(); }
            catch (Throwable failure) { cleanupFailure = failure; }
            releaseRead.countDown();
            try { session.close(); }
            catch (Throwable failure) { cleanupFailure = addFailure(cleanupFailure, failure); }
            boolean workerFinished = !speechRequested;
            if (speechRequested) {
                try {
                    result.get(5, TimeUnit.SECONDS);
                    workerFinished = true;
                } catch (Throwable failure) {
                    workerFinished = result.isDone();
                    cleanupFailure = addFailure(cleanupFailure, failure);
                    if (failure instanceof InterruptedException) Thread.currentThread().interrupt();
                }
            }
            if (workerFinished) {
                try { removeQaModelParent(qaParent); }
                catch (Throwable failure) { cleanupFailure = addFailure(cleanupFailure, failure); }
            }
            if (cleanupFailure != null) {
                if (testFailure != null) testFailure.addSuppressed(cleanupFailure);
                else rethrowCleanupFailure(cleanupFailure);
            }
        }
    }

    private static Throwable addFailure(Throwable first, Throwable additional) {
        if (first == null) return additional;
        first.addSuppressed(additional);
        return first;
    }

    private static void rethrowCleanupFailure(Throwable failure) throws Exception {
        if (failure instanceof Error) throw (Error) failure;
        if (failure instanceof Exception) throw (Exception) failure;
        throw new AssertionError(failure);
    }

    private static void removeQaModelParent(File parent) throws IOException {
        // This directory was created by this test. Never traverse the production no-backup model root.
        File[] children = parent.listFiles();
        if (children == null) throw new IOException("QA staging parent disappeared");
        for (File child : children) {
            String name = child.getName();
            if (!name.matches("\\.pocket-stage-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}|alba-18m-[0-9a-f]{24}")
                    || !Files.isDirectory(child.toPath(), LinkOption.NOFOLLOW_LINKS))
                throw new IOException("Unexpected QA staging path: " + name);
            File[] files = child.listFiles();
            if (files == null) throw new IOException("Cannot inspect QA staging directory");
            for (File file : files) {
                String fileName = file.getName();
                if (!(fileName.equals("model.safetensors") || fileName.equals("tokenizer.model")
                        || fileName.equals("alba.safetensors"))
                        || !Files.isRegularFile(file.toPath(), LinkOption.NOFOLLOW_LINKS))
                    throw new IOException("Unexpected QA staging file: " + fileName);
            }
            for (File file : files) Files.delete(file.toPath());
            Files.delete(child.toPath());
        }
        Files.delete(parent.toPath());
    }

    private void relaunch() throws Exception {
        if (driver != null) driver.close();
        driver = new PhoneActivityDriver(instrumentation);
        activity = driver.launch();
        await("new real ReactContext current Activity", () -> {
            ReactHost host = ((MainApplication) activity.getApplication()).getReactHost();
            if (!(host.getCurrentReactContext() instanceof ReactApplicationContext)) return false;
            ReactApplicationContext react = (ReactApplicationContext) host.getCurrentReactContext();
            if (!react.hasActiveReactInstance() || react.getCurrentActivity() != activity) return false;
            Object registered = react.getNativeModule("LocalSpeech");
            if (!(registered instanceof LocalSpeechModule)) return false;
            module = (LocalSpeechModule) registered;
            return true;
        });
    }

    private void bringToFront() throws Exception {
        Intent intent = new Intent(target, MainActivity.class);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        target.startActivity(intent);
        await("Home returns existing Activity to resumed state", () -> activity.getLifecycle()
                .getCurrentState().isAtLeast(Lifecycle.State.RESUMED));
    }

    private String begin() throws Exception {
        CompletableFuture<Object> result = new CompletableFuture<>();
        module.begin(promise(result));
        return (String) result.get(10, TimeUnit.SECONDS);
    }

    private CompletableFuture<Object> speak(String token, String text) {
        CompletableFuture<Object> result = new CompletableFuture<>();
        module.speak(token, text, promise(result));
        return result;
    }

    private static Promise promise(CompletableFuture<Object> result) {
        return (Promise) Proxy.newProxyInstance(Promise.class.getClassLoader(),
                new Class<?>[]{Promise.class}, (proxy, method, args) -> {
                    if (method.getName().equals("resolve")) result.complete(args[0]);
                    else if (method.getName().equals("reject"))
                        result.completeExceptionally(new AssertionError("Native promise rejected: " + args[0]));
                    return null;
                });
    }

    private PlaybackObservation awaitPlaybackStarted(CompletableFuture<Object> result) throws Exception {
        final PlaybackObservation[] found = new PlaybackObservation[1];
        await("actual production AudioTrack playing", () -> {
            assertFalse("Speech finished before lifecycle cancellation precondition", result.isDone());
            Object job = currentJob();
            if (job == null) return false;
            Object player = value(job, "player");
            short[] pcm = (short[]) value(job, "pcm");
            if (player == null || pcm == null) return false;
            TrackSnapshot snapshot = trackSnapshot(player);
            if (!snapshot.playing) return false;
            found[0] = new PlaybackObservation(pcm, player);
            return true;
        });
        return found[0];
    }

    private AudioEvidence observeRealPlayback(CompletableFuture<Object> result) throws Exception {
        AudioEvidence evidence = new AudioEvidence();
        long until = SystemClock.elapsedRealtime() + WAIT_MS;
        while (SystemClock.elapsedRealtime() < until && !result.isDone()) {
            Object job = currentJob();
            if (job != null) {
                short[] pcm = (short[]) value(job, "pcm");
                if (pcm != null) {
                    evidence.pcm = pcm;
                    evidence.pcmFrames = pcm.length;
                    for (short sample : pcm) if (sample != 0) { evidence.nonzeroPcm = true; break; }
                }
                Object player = value(job, "player");
                if (player != null) {
                    evidence.player = player;
                    TrackSnapshot snapshot = trackSnapshot(player);
                    if (snapshot.playing) {
                        evidence.playing = true;
                        if (evidence.firstHeadAt == 0 && evidence.pcmFrames > 0) {
                            evidence.firstHeadAt = SystemClock.elapsedRealtime();
                            evidence.firstHead = snapshot.head;
                        }
                    }
                    evidence.maxHead = Math.max(evidence.maxHead, snapshot.head);
                }
            }
            SystemClock.sleep(5);
        }
        evidence.completionAt = SystemClock.elapsedRealtime();
        assertTrue("Production speech must finish within deadline", result.isDone());
        return evidence;
    }

    private static void assertCleaned(short[] pcm, Object player) throws Exception {
        assertNotNull("PCM must have been allocated", pcm);
        assertNotNull("Production player must have been installed", player);
        for (short sample : pcm) assertEquals("PCM zeroized after job", 0, sample);
        synchronized (value(player, "lock")) {
            assertNull("AudioTrack released after job", value(player, "activeTrack"));
        }
    }

    private static TrackSnapshot trackSnapshot(Object player) throws Exception {
        Object lock = value(player, "lock");
        synchronized (lock) {
            AudioTrack track = (AudioTrack) value(player, "activeTrack");
            if (track == null) return new TrackSnapshot(false, 0);
            assertEquals("Pocket playback sample rate", 24_000, track.getSampleRate());
            return new TrackSnapshot(track.getPlayState() == AudioTrack.PLAYSTATE_PLAYING,
                    track.getPlaybackHeadPosition() & 0xffffffffL);
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

    private static String repeat(char character, int count) {
        char[] chars = new char[count];
        Arrays.fill(chars, character);
        return new String(chars);
    }

    private interface Condition { boolean trueNow() throws Exception; }
    private static void await(String label, Condition condition) throws Exception {
        long until = SystemClock.elapsedRealtime() + WAIT_MS;
        while (SystemClock.elapsedRealtime() < until) {
            if (condition.trueNow()) return;
            SystemClock.sleep(25);
        }
        throw new AssertionError("Timed out: " + label);
    }

    private static final class AudioEvidence {
        boolean nonzeroPcm, playing;
        long maxHead, firstHead, firstHeadAt, completionAt;
        int pcmFrames;
        short[] pcm;
        Object player;
    }

    private static final class PlaybackObservation {
        final short[] pcm;
        final Object player;
        PlaybackObservation(short[] pcm, Object player) {
            this.pcm = pcm;
            this.player = player;
        }
    }

    private static final class TrackSnapshot {
        final boolean playing;
        final long head;
        TrackSnapshot(boolean playing, long head) {
            this.playing = playing;
            this.head = head;
        }
    }
}
