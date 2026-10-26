package com.xmilo_at_your_side.sign_to_speak;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;
import java.util.UUID;

/** Installs only the pinned, bundled Pocket Alba assets into an app-private directory. */
public final class PocketModelStore {
  private static final int BUFFER_SIZE = 64 * 1024;
  private static final Object INSTALL_LOCK = new Object();
  private static final String[] NAMES = {"model.safetensors", "tokenizer.model", "alba.safetensors"};
  private static final String ORPHAN_NAME = "\\.pocket-(stage|displaced)-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}";

  public interface AssetSource { InputStream open(String name) throws IOException; }

  static final class ModelFile {
    final String name;
    final long size;
    final String sha256;
    ModelFile(String name, long size, String sha256) {
      if (name == null || size < 0 || sha256 == null || !sha256.matches("[0-9a-f]{64}"))
        throw new IllegalArgumentException("Invalid model manifest");
      this.name = name; this.size = size; this.sha256 = sha256;
    }
  }

  private static final ModelFile[] PINNED = {
    new ModelFile(NAMES[0], 111_728_528L, "599329dcea2f2a8d17b575e1c6e11b78822453c9073040d55c26faadf4883188"),
    new ModelFile(NAMES[1], 59_339L, "d461765ae179566678c93091c5fa6f2984c31bbe990bf1aa62d92c64d91bc3f6"),
    new ModelFile(NAMES[2], 4_163_992L, "11554cbe9f3d3a3e10722932df9952bb44928e534f7af3200524659166b5627a")
  };

  private final ModelFile[] manifest;
  private final String version;

  public PocketModelStore() { this(PINNED); }

  // Small fixture manifest for the host test; the public constructor always uses PINNED.
  PocketModelStore(ModelFile[] files) {
    if (files == null || files.length != NAMES.length) throw new IllegalArgumentException("Expected three model files");
    manifest = files.clone();
    MessageDigest digest = sha();
    for (int i = 0; i < NAMES.length; i++) {
      if (manifest[i] == null || !NAMES[i].equals(manifest[i].name)) throw new IllegalArgumentException("Invalid model name");
      update(digest, manifest[i].name + ":" + manifest[i].size + ":" + manifest[i].sha256 + "\n");
    }
    version = "alba-18m-" + hex(digest.digest()).substring(0, 24);
  }

  /** Returns an absolute root only after all three files have passed size and SHA-256 checks. */
  public File prepare(File privateParent, AssetSource source) throws IOException {
    if (privateParent == null || source == null) throw new IllegalArgumentException("Parent and asset source required");
    synchronized (INSTALL_LOCK) {
      Path parent = privateParent.toPath().toAbsolutePath().normalize();
      if (!Files.isDirectory(parent, LinkOption.NOFOLLOW_LINKS) || Files.isSymbolicLink(parent)
          || !parent.toRealPath().equals(parent)) throw new IOException("Private model parent is unsafe");
      removeOrphanedDirectories(parent);
      Path root = parent.resolve(version);
      if (Files.exists(root, LinkOption.NOFOLLOW_LINKS)) {
        if (Files.isSymbolicLink(root) || !Files.isDirectory(root, LinkOption.NOFOLLOW_LINKS))
          throw new IOException("Model root is unsafe");
        if (verified(root)) return root.toFile();
        requireOwnedContents(root);
      }
      Path stage = parent.resolve(".pocket-stage-" + UUID.randomUUID());
      Files.createDirectory(stage);
      boolean published = false;
      try {
        for (ModelFile item : manifest) copyChecked(source, item, stage.resolve(item.name));
        if (!verified(stage)) throw new IOException("Staged model failed verification");
        Path displaced = null;
        if (Files.exists(root, LinkOption.NOFOLLOW_LINKS)) {
          requireOwnedContents(root);
          displaced = parent.resolve(".pocket-displaced-" + UUID.randomUUID());
          Files.move(root, displaced, StandardCopyOption.ATOMIC_MOVE);
        }
        try {
          Files.move(stage, root, StandardCopyOption.ATOMIC_MOVE);
          published = true;
        } catch (IOException failure) {
          if (displaced != null && !Files.exists(root, LinkOption.NOFOLLOW_LINKS)) {
            try { Files.move(displaced, root, StandardCopyOption.ATOMIC_MOVE); }
            catch (IOException restoreFailure) { failure.addSuppressed(restoreFailure); }
          }
          throw failure;
        }
        if (displaced != null) removeOwnedDirectory(displaced);
        return root.toFile();
      } finally {
        if (!published) removeOwnedDirectory(stage);
      }
    }
  }

  private void copyChecked(AssetSource source, ModelFile item, Path destination) throws IOException {
    MessageDigest digest = sha();
    long total = 0;
    try (InputStream input = source.open(item.name)) {
      if (input == null) throw new IOException("Bundled model asset missing");
      try (FileOutputStream output = new FileOutputStream(destination.toFile())) {
        byte[] buffer = new byte[BUFFER_SIZE];
        int count;
        int emptyReads = 0;
        while ((count = input.read(buffer)) != -1) {
          if (count == 0) {
            if (++emptyReads > 8) throw new IOException("Bundled model asset stalled");
            continue;
          }
          emptyReads = 0;
          total += count;
          if (total > item.size) throw new IOException("Bundled model asset is too large");
          output.write(buffer, 0, count);
          digest.update(buffer, 0, count);
        }
        if (total != item.size || !item.sha256.equals(hex(digest.digest())))
          throw new IOException("Bundled model asset differs from pinned manifest");
        output.getFD().sync();
      }
    }
    if (!destination.toFile().setReadOnly()) throw new IOException("Cannot protect bundled model asset");
  }

  private boolean verified(Path root) throws IOException {
    requireOwnedContents(root);
    for (ModelFile item : manifest) {
      Path file = root.resolve(item.name);
      if (!Files.isRegularFile(file, LinkOption.NOFOLLOW_LINKS) || Files.size(file) != item.size
          || !file.toRealPath().getParent().equals(root.toRealPath())) return false;
      MessageDigest digest = sha();
      try (FileInputStream input = new FileInputStream(file.toFile())) {
        byte[] buffer = new byte[BUFFER_SIZE];
        int count;
        while ((count = input.read(buffer)) != -1) if (count > 0) digest.update(buffer, 0, count);
      }
      if (!item.sha256.equals(hex(digest.digest()))) return false;
    }
    return true;
  }

  private void requireOwnedContents(Path directory) throws IOException {
    Set<String> expected = new HashSet<>(Arrays.asList(NAMES));
    try (DirectoryStream<Path> children = Files.newDirectoryStream(directory)) {
      for (Path child : children) {
        if (!expected.remove(child.getFileName().toString()) || Files.isSymbolicLink(child)
            || !Files.isRegularFile(child, LinkOption.NOFOLLOW_LINKS))
          throw new IOException("Unexpected model directory content");
      }
    }
  }

  private void removeOrphanedDirectories(Path parent) throws IOException {
    try (DirectoryStream<Path> children = Files.newDirectoryStream(parent)) {
      for (Path child : children) {
        if (!child.getFileName().toString().matches(ORPHAN_NAME)
            || Files.isSymbolicLink(child) || !Files.isDirectory(child, LinkOption.NOFOLLOW_LINKS)) continue;
        try { requireOwnedContents(child); }
        catch (IOException unsafe) { continue; }
        removeOwnedDirectory(child);
      }
    }
  }

  private void removeOwnedDirectory(Path directory) throws IOException {
    if (!Files.exists(directory, LinkOption.NOFOLLOW_LINKS)) return;
    if (Files.isSymbolicLink(directory) || !Files.isDirectory(directory, LinkOption.NOFOLLOW_LINKS))
      throw new IOException("Unsafe staging directory");
    requireOwnedContents(directory);
    for (String name : NAMES) {
      Path file = directory.resolve(name);
      if (Files.exists(file, LinkOption.NOFOLLOW_LINKS)) {
        if (!file.toFile().setWritable(true)) throw new IOException("Cannot remove owned model staging file");
        Files.delete(file);
      }
    }
    Files.delete(directory);
  }

  private static MessageDigest sha() {
    try { return MessageDigest.getInstance("SHA-256"); }
    catch (NoSuchAlgorithmException impossible) { throw new IllegalStateException(impossible); }
  }
  private static void update(MessageDigest digest, String value) {
    try { digest.update(value.getBytes("UTF-8")); }
    catch (java.io.UnsupportedEncodingException impossible) { throw new IllegalStateException(impossible); }
  }
  private static String hex(byte[] bytes) {
    char[] alphabet = "0123456789abcdef".toCharArray();
    char[] result = new char[bytes.length * 2];
    for (int i = 0; i < bytes.length; i++) {
      result[i * 2] = alphabet[(bytes[i] & 255) >>> 4];
      result[i * 2 + 1] = alphabet[bytes[i] & 15];
    }
    return new String(result);
  }
}
