package com.xmilo_at_your_side.sign_to_speak;

import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.security.MessageDigest;

public final class RecognitionInputStageTest {
    private static final String TOKEN = "0123456789abcdef0123456789abcdef";

    public static void main(String[] args) throws Exception {
        File cache = Files.createTempDirectory("recognition-stage-test").toFile();
        try {
            check(RecognitionInputStage.shellPath(TOKEN, "manifest.json").equals(
                "/data/local/tmp/sign-recognition-" + TOKEN + "/manifest.json"));
            fails(() -> RecognitionInputStage.shellPath("../bad", "manifest.json"));
            fails(() -> RecognitionInputStage.shellPath(TOKEN, "../manifest.json"));
            fails(() -> RecognitionInputStage.shellPath(TOKEN, "result.json"));
            byte[] good = "private fixture".getBytes("UTF-8");
            String hash = hex(MessageDigest.getInstance("SHA-256").digest(good));
            File dir = RecognitionInputStage.create(cache, TOKEN);
            Tracked goodInput = new Tracked(good);
            File staged = RecognitionInputStage.copy(dir, "manifest.json", goodInput, hash, 100);
            check(goodInput.closed);
            check(Files.readAllBytes(staged.toPath()).length == good.length);
            Tracked badHash = new Tracked(good);
            fails(() -> RecognitionInputStage.copy(dir, "labels.json", badHash, "0".repeat(64), 100));
            check(badHash.closed);
            check(!new File(dir, "labels.json").exists());
            Tracked tooLarge = new Tracked(good);
            fails(() -> RecognitionInputStage.copy(dir, "labels.json", tooLarge, hash, 3));
            check(tooLarge.closed);
            check(!new File(dir, "labels.json").exists());
            fails(() -> RecognitionInputStage.copy(dir, "labels.json", new ByteArrayInputStream(new byte[0]), hash, 100));
            check(!new File(dir, "labels.json").exists());
            try {
                RecognitionInputStage.copy(dir, "labels.json", new InputStream() {
                    private int reads;
                    @Override public int read() throws IOException {
                        if (reads++ == 0) return 17;
                        throw new IOException("fixture transfer failure");
                    }
                }, hash, 100);
                throw new AssertionError("expected stream failure");
            } catch (IOException expected) {
                check(!new File(dir, "labels.json.partial").exists());
            }
            RecognitionInputStage.copy(dir, "labels.json", new ByteArrayInputStream(good), hash, 100);
            File dangling = new File(dir, "PLEASE.mp4.partial");
            try {
                Files.createSymbolicLink(dangling.toPath(), new File(dir, "missing").toPath());
                fails(() -> RecognitionInputStage.copy(dir, "PLEASE.mp4", new ByteArrayInputStream(good), hash, 100));
                Files.delete(dangling.toPath());
            } catch (UnsupportedOperationException | java.nio.file.FileSystemException unavailable) {
                // Windows hosts without symlink privilege cannot exercise this case.
            }
            RecognitionInputStage.cleanup(dir);
            check(!dir.exists());
            File retry = RecognitionInputStage.create(cache, TOKEN);
            check(retry.isDirectory());
            RecognitionInputStage.cleanupExisting(cache, TOKEN);
            check(!retry.exists());
            exerciseHolisticRgbaTransfer(cache);
        } finally {
            cache.delete();
        }
    }

    private static void exerciseHolisticRgbaTransfer(File cache) throws Exception {
        String name = "holistic-parity.rgba";
        byte[] rgba = { 17, 34, 51, (byte) 255, 68, 85, 102, (byte) 255 };
        String hash = hex(MessageDigest.getInstance("SHA-256").digest(rgba));
        File stage = RecognitionInputStage.create(cache, TOKEN);
        try {
            check(RecognitionInputStage.shellPath(TOKEN, name).equals(
                "/data/local/tmp/sign-recognition-" + TOKEN + "/" + name));
            Tracked wrongBytes = new Tracked(new byte[rgba.length]);
            fails(() -> RecognitionInputStage.copy(stage, name, wrongBytes, hash, rgba.length));
            check(wrongBytes.closed);
            check(!new File(stage, name).exists());
            check(!new File(stage, name + ".partial").exists());

            Tracked input = new Tracked(rgba);
            File staged = RecognitionInputStage.copy(stage, name, input, hash, rgba.length);
            check(input.closed);
            check(staged.equals(new File(stage, name)));
            check(java.util.Arrays.equals(Files.readAllBytes(staged.toPath()), rgba));

            for (String rejected : new String[] { "../" + name, "Holistic-parity.rgba", name + ".bak", "other.rgba" }) {
                fails(() -> RecognitionInputStage.shellPath(TOKEN, rejected));
                fails(() -> RecognitionInputStage.copy(
                    stage, rejected, new ByteArrayInputStream(rgba), hash, rgba.length));
            }
        } finally {
            RecognitionInputStage.cleanup(stage);
        }
        check(!stage.exists());
    }

    private static void check(boolean condition) { if (!condition) throw new AssertionError(); }
    private static void fails(Throwing action) throws Exception {
        try { action.run(); } catch (IllegalArgumentException expected) { return; }
        throw new AssertionError("expected rejection");
    }
    private static String hex(byte[] bytes) {
        StringBuilder out = new StringBuilder();
        for (byte b : bytes) out.append(String.format("%02x", b & 255));
        return out.toString();
    }
    private static final class Tracked extends ByteArrayInputStream {
        boolean closed;
        Tracked(byte[] data) { super(data); }
        @Override public void close() throws IOException { closed = true; super.close(); }
    }
    private interface Throwing { void run() throws Exception; }
}
