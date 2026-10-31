package com.xmilo_at_your_side.sign_to_speak;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Locale;
import java.nio.file.Files;

/** Bounded, pinned research input transfer; never used by the app build. */
public final class RecognitionInputStage {
    private RecognitionInputStage() {}

    public static String shellPath(String transferId, String name) {
        checkTransferId(transferId);
        checkName(name);
        return "/data/local/tmp/sign-recognition-" + transferId + "/" + name;
    }

    public static File create(File cache, String transferId) throws IOException {
        checkTransferId(transferId);
        if (!cache.isDirectory() || Files.isSymbolicLink(cache.toPath())) {
            throw new IllegalArgumentException("Private cache unavailable");
        }
        File stage = new File(cache, "recognition-probe-" + transferId);
        if (!stage.mkdir()) throw new IllegalArgumentException("Fresh input stage unavailable");
        return stage;
    }

    public static File copy(File stage, String name, InputStream input, String expectedSha, long maxBytes)
            throws IOException {
        checkName(name);
        if (expectedSha == null || !expectedSha.matches("[0-9a-f]{64}") || maxBytes <= 0) {
            throw new IllegalArgumentException("Invalid input pin or bound");
        }
        if (!stage.isDirectory() || Files.isSymbolicLink(stage.toPath())) {
            throw new IllegalArgumentException("Input stage unavailable");
        }
        File target = new File(stage, name);
        File partial = new File(stage, name + ".partial");
        if (Files.exists(target.toPath(), java.nio.file.LinkOption.NOFOLLOW_LINKS)
                || Files.exists(partial.toPath(), java.nio.file.LinkOption.NOFOLLOW_LINKS)) {
            throw new IllegalArgumentException("Input already staged");
        }
        boolean published = false;
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] buffer = new byte[64 * 1024];
            long total = 0;
            int count;
            try (InputStream source = input; FileOutputStream output = new FileOutputStream(partial)) {
                while ((count = source.read(buffer)) != -1) {
                    if (count == 0) continue;
                    total += count;
                    if (total > maxBytes) throw new IllegalArgumentException("Input exceeds bound");
                    output.write(buffer, 0, count);
                    digest.update(buffer, 0, count);
                }
                if (total == 0 || !hex(digest.digest()).equals(expectedSha)) {
                    throw new IllegalArgumentException("Input SHA-256 or size mismatch");
                }
                output.getFD().sync();
            }
            if (!partial.renameTo(target)) throw new IOException("Could not publish staged input");
            published = true;
            return target;
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException(impossible);
        } finally {
            if (!published && partial.exists() && !partial.delete()) {
                throw new IOException("Could not clear partial input");
            }
        }
    }

    public static void cleanup(File stage) {
        if (stage == null || !stage.exists()) return;
        if (!stage.isDirectory() || Files.isSymbolicLink(stage.toPath())) {
            throw new IllegalArgumentException("Invalid input stage");
        }
        File[] children = stage.listFiles();
        if (children == null) throw new IllegalArgumentException("Cannot inspect input stage");
        for (File child : children) {
            String name = child.getName();
            if (name.endsWith(".partial")) name = name.substring(0, name.length() - 8);
            checkName(name);
            if (!child.isFile() || Files.isSymbolicLink(child.toPath()) || !child.delete()) {
                throw new IllegalArgumentException("Could not clear staged input");
            }
        }
        if (!stage.delete()) throw new IllegalArgumentException("Could not remove input stage");
    }

    public static void cleanupExisting(File cache, String transferId) {
        checkTransferId(transferId);
        if (!cache.isDirectory() || Files.isSymbolicLink(cache.toPath())) {
            throw new IllegalArgumentException("Private cache unavailable");
        }
        cleanup(new File(cache, "recognition-probe-" + transferId));
    }

    private static void checkTransferId(String id) {
        if (id == null || !id.matches("[0-9a-f]{32}")) {
            throw new IllegalArgumentException("Invalid transfer id");
        }
    }

    private static void checkName(String name) {
        if (name == null || !(name.equals("manifest.json") || name.equals("model.tflite")
                || name.equals("holistic.task") || name.equals("labels.json")
                || name.matches("[a-zA-Z0-9_-]{1,40}\\.mp4"))) {
            throw new IllegalArgumentException("Unapproved input name");
        }
    }

    private static String hex(byte[] bytes) {
        StringBuilder result = new StringBuilder();
        for (byte b : bytes) result.append(String.format(Locale.ROOT, "%02x", b & 255));
        return result.toString();
    }
}
