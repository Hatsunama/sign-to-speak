package com.xmilo_at_your_side.sign_to_speak;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;

public final class PocketModelStoreTest {
  private static int checks;
  private static final String[] NAMES = {"model.safetensors", "tokenizer.model", "alba.safetensors"};
  private static final byte[][] BYTES = {"model".getBytes(StandardCharsets.UTF_8), "tokens".getBytes(StandardCharsets.UTF_8), "voice".getBytes(StandardCharsets.UTF_8)};
  private static void check(boolean value, String label) { checks++; if (!value) throw new AssertionError(label); }
  private static String hex(byte[] bytes) throws Exception {
    byte[] digest = MessageDigest.getInstance("SHA-256").digest(bytes);
    StringBuilder out = new StringBuilder(); for (byte b : digest) out.append(String.format("%02x", b & 255)); return out.toString();
  }
  private static PocketModelStore fixture() throws Exception {
    PocketModelStore.ModelFile[] files = new PocketModelStore.ModelFile[3];
    for (int i = 0; i < 3; i++) files[i] = new PocketModelStore.ModelFile(NAMES[i], BYTES[i].length, hex(BYTES[i]));
    return new PocketModelStore(files);
  }
  private static PocketModelStore.AssetSource source() {
    return name -> { for (int i = 0; i < 3; i++) if (NAMES[i].equals(name)) return new ByteArrayInputStream(BYTES[i]); throw new FileNotFoundException(name); };
  }
  private static void failed(RunnableIO action, String label) throws Exception {
    boolean yes = false; try { action.run(); } catch (IOException expected) { yes = true; }
    check(yes, label);
  }
  private interface RunnableIO { void run() throws Exception; }
  private static void checkRoot(File root) throws Exception {
    check(root.isAbsolute() && root.isDirectory(), "absolute published root");
    for (int i = 0; i < 3; i++) check(Arrays.equals(Files.readAllBytes(new File(root, NAMES[i]).toPath()), BYTES[i]), "verified " + NAMES[i]);
    check(Objects.requireNonNull(root.list()).length == 3, "only three files");
  }
  private static void corrupt(File root, int index) throws Exception {
    File file = new File(root, NAMES[index]);
    if (!file.setWritable(true)) throw new IOException("Cannot set fixture writable");
    Files.write(file.toPath(), new byte[]{0});
  }
  private static Path orphan(Path parent, String prefix, boolean complete) throws Exception {
    Path directory = Files.createDirectory(parent.resolve(prefix + UUID.randomUUID()));
    for (int i = 0; i < (complete ? NAMES.length : 1); i++)
      Files.write(directory.resolve(NAMES[i]), BYTES[i]);
    return directory;
  }
  public static void main(String[] args) throws Exception {
    if (args.length != 1) throw new IllegalArgumentException("Expected temporary parent directory");
    File base = new File(args[0]);
    Files.createDirectories(base.toPath());
    PocketModelStore store = fixture();
    File first = Files.createTempDirectory(base.toPath(), "first-copy-").toFile();
    failed(() -> store.prepare(first, name -> {
      if (name.equals(NAMES[1])) throw new FileNotFoundException(name);
      return source().open(name);
    }), "first copy failure rejected");
    check(Objects.requireNonNull(first.list()).length == 0, "first failure publishes no root or staging directory");
    checkRoot(store.prepare(first, source()));
    Path firstOrphan = orphan(base.toPath(), ".pocket-stage-", false);
    Path displacedOrphan = orphan(base.toPath(), ".pocket-displaced-", true);
    Path unrelated = Files.createDirectory(base.toPath().resolve(".pocket-stage-not-a-uuid"));
    Files.write(unrelated.resolve("keep.txt"), new byte[]{1});
    File root = store.prepare(base, source()); checkRoot(root);
    check(!Files.exists(firstOrphan) && !Files.exists(displacedOrphan), "first install removes owned crash leftovers");
    check(Files.exists(unrelated.resolve("keep.txt")), "unrelated lookalike retained");
    Path reuseOrphan = orphan(base.toPath(), ".pocket-stage-", true);
    Path unsafeOrphan = orphan(base.toPath(), ".pocket-displaced-", false);
    Files.write(unsafeOrphan.resolve("keep.txt"), new byte[]{1});
    File reused = store.prepare(base, name -> { throw new AssertionError("cached install opened bundled asset"); });
    check(root.equals(reused), "valid cache reused without source");
    check(!Files.exists(reuseOrphan), "cache reuse removes owned crash leftover");
    check(Files.exists(unsafeOrphan.resolve("keep.txt")), "unknown orphan content retained");
    corrupt(root, 1);
    failed(() -> store.prepare(base, name -> { throw new FileNotFoundException(name); }), "bad cache cannot be used without known bytes");
    checkRoot(store.prepare(base, source()));
    check(root.equals(store.prepare(base, source())), "corrupt cache repaired at version root");

    for (int i = 0; i < 3; i++) {
      corrupt(root, i);
      final int bad = i;
      failed(() -> store.prepare(base, name -> name.equals(NAMES[bad]) ? new ByteArrayInputStream(new byte[]{9}) : source().open(name)), "wrong bundled bytes rejected");
      check(!root.exists() || !Arrays.equals(Files.readAllBytes(new File(root, NAMES[bad]).toPath()), BYTES[bad]), "no false valid root");
      checkRoot(store.prepare(base, source()));
    }
    corrupt(root, 0);
    failed(() -> store.prepare(base, name -> { if (name.equals(NAMES[1])) throw new FileNotFoundException(name); return source().open(name); }), "missing asset rejected");
    checkRoot(store.prepare(base, source()));
    corrupt(root, 0);
    failed(() -> store.prepare(base, name -> name.equals(NAMES[0]) ? new ByteArrayInputStream(new byte[BYTES[0].length + 1]) : source().open(name)), "oversize rejected");
    checkRoot(store.prepare(base, source()));
    corrupt(root, 0);
    failed(() -> store.prepare(base, name -> name.equals(NAMES[0]) ? new InputStream() { int n; public int read() throws IOException { if (n++ == 0) return 1; throw new IOException("interrupted"); } } : source().open(name)), "source interruption rejected");
    checkRoot(store.prepare(base, source()));

    // A platform may disallow symlink creation for unprivileged Windows users.
    Path link = base.toPath().resolve("symlink-parent");
    try {
      Files.createSymbolicLink(link, base.toPath());
      failed(() -> store.prepare(link.toFile(), source()), "symlink parent rejected");
    } catch (UnsupportedOperationException | SecurityException e) { System.out.println("Symlink case skipped: unsupported"); }
    catch (FileSystemException e) { System.out.println("Symlink case skipped: host privilege unavailable"); }
    checkRoot(store.prepare(base, source()));
    System.out.println("PocketModelStore: " + checks + " checks passed");
  }
}
