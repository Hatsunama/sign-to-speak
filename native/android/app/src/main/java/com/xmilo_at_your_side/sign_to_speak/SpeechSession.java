package com.xmilo_at_your_side.sign_to_speak;

import java.util.Arrays;
import java.util.Objects;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.function.BooleanSupplier;

/** Owns one local synthesis and playback job at a time. It retains no speech history. */
public final class SpeechSession implements AutoCloseable {
  public interface Request {
    short[] synthesize(byte[] ascii);
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
    Request newRequest();
    Player newPlayer();
  }
  public interface Completion { void complete(Result result); }
  public enum Result { COMPLETED, CANCELLED, BUSY, UNAVAILABLE, INVALID_INPUT, FAILED, CLEANUP_FAILED }

  private static final class Job {
    final long generation;
    final byte[] ascii;
    final Completion done;
    volatile boolean cancelled, cancellationFailed;
    boolean started, abandoned;
    Request request;
    Player player;
    short[] pcm;
    Job(long generation, byte[] ascii, Completion done) {
      this.generation = generation; this.ascii = ascii; this.done = done;
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

  public void speak(long token, byte[] ascii, Completion done) {
    Objects.requireNonNull(done);
    Result immediate = null;
    Job job = null;
    synchronized (gate) {
      if (closed || faulted || !eligible || token == 0 || token != generation) {
        immediate = Result.UNAVAILABLE;
      } else if (current != null) {
        immediate = Result.BUSY;
      } else if (!valid(ascii)) {
        immediate = Result.INVALID_INPUT;
      } else {
        job = new Job(token, ascii.clone(), done);
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

  private static boolean valid(byte[] ascii) {
    if (ascii == null || ascii.length == 0 || ascii.length > 256) return false;
    boolean nonblank = false;
    for (byte value : ascii) {
      int c = value & 0xff;
      if (c < 32 || c > 126) return false;
      if (c != 32) nonblank = true;
    }
    return nonblank;
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
      looseRequest = Objects.requireNonNull(factory.newRequest());
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
