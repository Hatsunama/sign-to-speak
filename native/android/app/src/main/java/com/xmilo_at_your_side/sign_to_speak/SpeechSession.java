package com.xmilo_at_your_side.sign_to_speak;

import java.nio.ByteBuffer;
import java.nio.CharBuffer;
import java.nio.charset.CoderResult;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.Objects;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.function.BooleanSupplier;

/** Owns one local synthesis and playback job at a time. It retains no speech history. */
public final class SpeechSession implements AutoCloseable {
  public interface Request {
    /** Rejects text at the engine boundary with {@link InvalidInput}. */
    short[] synthesize(byte[] utf8);
    void cancel();
    void close();
  }
  public interface Player {
    /** Returns after playback drains or cancellation stops it. */
    void play(short[] pcm, BooleanSupplier cancelled);
    void cancel();
    void close();
  }
  public interface Factory {
    /** Preparation must poll cancelled during work it can safely abandon. */
    Request newRequest(BooleanSupplier cancelled);
    Player newPlayer();
  }
  public interface Completion { void complete(Result result); }
  public enum Result { COMPLETED, CANCELLED, BUSY, UNAVAILABLE, INVALID_INPUT, FAILED, CLEANUP_FAILED }
  public static final class InvalidInput extends IllegalArgumentException {
    public InvalidInput(String message) { super(message); }
  }

  private static final class Job {
    final long generation;
    final byte[] ascii;
    final Completion done;
    volatile boolean cancelled, cancellationFailed;
    boolean started, abandoned;
    Request request;
    Player player;
    short[] pcm;
    Job(long generation, byte[] utf8, Completion done) {
      this.generation = generation; this.ascii = utf8; this.done = done;
    }
  }

  private final Object gate = new Object();
  private final Factory factory;
  private final ExecutorService executor;
  private long generation;
  private boolean eligible, closed, faulted;
  private Job current;

  public SpeechSession(Factory factory) {
    this(factory, Executors.newSingleThreadExecutor(r -> {
      Thread t = new Thread(r, "sign-to-speak-audio");
      t.setDaemon(true);
      return t;
    }));
  }
  public SpeechSession(Factory factory, ExecutorService executor) {
    this.factory = Objects.requireNonNull(factory);
    this.executor = Objects.requireNonNull(executor);
  }

  /** Opens a new eligible generation, or returns zero when speech cannot start. */
  public long begin(boolean allowed) {
    stop();
    synchronized (gate) {
      if (closed || faulted || !allowed) return 0;
      eligible = true;
      return generation;
    }
  }

  public boolean isFaulted() {
    synchronized (gate) { return faulted; }
  }

  public void speak(long token, byte[] utf8, Completion done) {
    Objects.requireNonNull(done);
    Result immediate = null;
    Job job = null;
    synchronized (gate) {
      if (closed || faulted || !eligible || token == 0 || token != generation) {
        immediate = Result.UNAVAILABLE;
      } else if (current != null) {
        immediate = Result.BUSY;
      } else if (!valid(utf8)) {
        immediate = Result.INVALID_INPUT;
      } else {
        job = new Job(token, utf8.clone(), done);
        current = job;
      }
    }
    if (immediate != null) { notifySafely(done, immediate); return; }
    final Job submitted = job;
    try {
      executor.execute(() -> {
        synchronized (gate) {
          if (submitted.abandoned) return;
          submitted.started = true;
        }
        run(submitted);
      });
    } catch (RuntimeException | Error dispatchFailure) {
      // A direct executor may propagate a worker failure. In that case, the
      // worker owns cleanup. Otherwise abandon a command retained by executor.
      boolean abandoned;
      synchronized (gate) {
        abandoned = !submitted.started;
        if (abandoned) {
          submitted.abandoned = true;
          Arrays.fill(submitted.ascii, (byte) 0);
          if (current == submitted) current = null;
        }
      }
      if (abandoned) notifySafely(done, Result.FAILED);
      if (dispatchFailure instanceof Error) throw (Error) dispatchFailure;
      if (!abandoned) throw (RuntimeException) dispatchFailure;
    }
  }

  private static boolean valid(byte[] utf8) {
    if (utf8 == null || utf8.length == 0 || utf8.length > 512) return false;
    CharBuffer decoded = CharBuffer.allocate(256);
    try {
      var decoder = StandardCharsets.UTF_8.newDecoder()
          .onMalformedInput(CodingErrorAction.REPORT)
          .onUnmappableCharacter(CodingErrorAction.REPORT);
      CoderResult status = decoder.decode(ByteBuffer.wrap(utf8), decoded, true);
      if (status.isError() || status.isOverflow()) return false;
      status = decoder.flush(decoded);
      if (status.isError() || status.isOverflow()) return false;
      int length = decoded.position();
      if (length == 0) return false;
      char[] chars = decoded.array();
      boolean nonblank = false;
      for (int i = 0; i < length;) {
        int codePoint = Character.codePointAt(chars, i, length);
        if (Character.getType(codePoint) == Character.CONTROL) return false;
        if (!Character.isWhitespace(codePoint) && !Character.isSpaceChar(codePoint)) nonblank = true;
        i += Character.charCount(codePoint);
      }
      return nonblank;
    } finally {
      Arrays.fill(decoded.array(), '\0');
    }
  }

  /** Strictly encodes reviewed text for the shared native boundary. The returned bytes belong to the caller. */
  public static byte[] encodeText(String text) {
    if (text == null || text.isEmpty() || text.length() > 256) return null;
    ByteBuffer encoded = ByteBuffer.allocate(512);
    byte[] result = null;
    boolean accepted = false;
    try {
      var encoder = StandardCharsets.UTF_8.newEncoder()
          .onMalformedInput(CodingErrorAction.REPORT)
          .onUnmappableCharacter(CodingErrorAction.REPORT);
      CoderResult status = encoder.encode(CharBuffer.wrap(text), encoded, true);
      if (status.isError() || status.isOverflow()) return null;
      status = encoder.flush(encoded);
      if (status.isError() || status.isOverflow()) return null;
      result = Arrays.copyOf(encoded.array(), encoded.position());
      accepted = valid(result);
      if (accepted) return result;
      return null;
    } finally {
      if (result != null && !accepted) Arrays.fill(result, (byte) 0);
      Arrays.fill(encoded.array(), (byte) 0);
    }
  }

  private boolean cancelled(Job job) {
    synchronized (gate) {
      return job.cancelled || current != job || closed || faulted ||
          !eligible || generation != job.generation;
    }
  }

  private boolean installRequest(Job job, Request request) {
    synchronized (gate) {
      if (cancelled(job)) return false;
      job.request = request;
      return true;
    }
  }

  private boolean installPlayer(Job job, Player player) {
    synchronized (gate) {
      if (cancelled(job)) return false;
      job.player = player;
      return true;
    }
  }

  private void run(Job job) {
    Result result = Result.FAILED;
    Request looseRequest = null;
    Player loosePlayer = null;
    try {
      if (cancelled(job)) { result = Result.CANCELLED; return; }
      looseRequest = Objects.requireNonNull(factory.newRequest(() -> cancelled(job)));
      if (!installRequest(job, looseRequest)) { result = Result.CANCELLED; return; }
      looseRequest = null;
      job.pcm = Objects.requireNonNull(job.request.synthesize(job.ascii));
      if (cancelled(job)) { result = Result.CANCELLED; return; }
      loosePlayer = Objects.requireNonNull(factory.newPlayer());
      if (!installPlayer(job, loosePlayer)) { result = Result.CANCELLED; return; }
      loosePlayer = null;
      if (cancelled(job)) { result = Result.CANCELLED; return; }
      job.player.play(job.pcm, () -> cancelled(job));
      result = cancelled(job) ? Result.CANCELLED : Result.COMPLETED;
    } catch (InvalidInput invalid) {
      result = cancelled(job) ? Result.CANCELLED : Result.INVALID_INPUT;
    } catch (RuntimeException | LinkageError failure) {
      result = cancelled(job) ? Result.CANCELLED : Result.FAILED;
    } finally {
      boolean cleanupFailed = false;
      Arrays.fill(job.ascii, (byte) 0);
      if (job.pcm != null) Arrays.fill(job.pcm, (short) 0);
      cleanupFailed |= closePlayer(job.player);
      cleanupFailed |= closePlayer(loosePlayer);
      cleanupFailed |= closeRequest(job.request);
      cleanupFailed |= closeRequest(looseRequest);
      synchronized (gate) {
        if (cleanupFailed) faulted = true;
        if (current == job) current = null;
      }
      notifySafely(job.done, cleanupFailed || job.cancellationFailed ? Result.CLEANUP_FAILED : result);
    }
  }

  private static boolean closeRequest(Request request) {
    if (request == null) return false;
    try { request.close(); return false; }
    catch (RuntimeException | LinkageError failure) { return true; }
  }
  private static boolean closePlayer(Player player) {
    if (player == null) return false;
    try { player.close(); return false; }
    catch (RuntimeException | LinkageError failure) { return true; }
  }
  private static void notifySafely(Completion done, Result result) {
    try { done.complete(result); }
    catch (RuntimeException | LinkageError ignored) { /* An application callback cannot strand the owner. */ }
  }

  /** Invalidates in-flight work and calls cancellation without waiting for its worker. */
  public void stop() {
    Job job;
    Request request;
    Player player;
    synchronized (gate) {
      generation++;
      eligible = false;
      job = current;
      if (job == null) return;
      job.cancelled = true;
      request = job.request;
      player = job.player;
    }
    boolean failed = false;
    if (request != null) {
      try { request.cancel(); } catch (RuntimeException | LinkageError failure) { failed = true; }
    }
    if (player != null) {
      try { player.cancel(); } catch (RuntimeException | LinkageError failure) { failed = true; }
    }
    if (failed) synchronized (gate) { job.cancellationFailed = true; faulted = true; }
  }

  @Override public void close() {
    synchronized (gate) {
      if (closed) return;
      closed = true;
    }
    stop();
    executor.shutdown();
  }
}
