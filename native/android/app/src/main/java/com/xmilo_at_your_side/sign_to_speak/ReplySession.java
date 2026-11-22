package com.xmilo_at_your_side.sign_to_speak;

import java.util.Arrays;
import java.util.Objects;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.BooleanSupplier;

/** One bounded, volatile microphone capture and local decode. Retains no transcript history. */
public final class ReplySession implements AutoCloseable {
  public interface Recorder {
    void start();
    /** A stopped recorder may return zero or a negative platform error. */
    int read(short[] target, int offset, int count);
    void stop();
    void close();
  }
  public interface Request {
    Outcome decode(short[] pcm, int count);
    void cancel();
    void close();
  }
  public interface Factory {
    Recorder newRecorder();
    Request newRequest(BooleanSupplier cancelled);
  }
  public interface Started { void complete(boolean recording); }
  public interface Completion { void complete(Outcome outcome); }
  public static final class ModelUnavailable extends IllegalStateException {
    public ModelUnavailable() { super("Local reply model unavailable"); }
  }
  public static final class DecodeFailed extends IllegalStateException {
    public DecodeFailed() { super("Local reply decode failed"); }
  }
  public static final class CleanupFailed extends IllegalStateException {
    public CleanupFailed() { super("Local reply cleanup failed"); }
  }

  public enum Status {
    COMPLETED, CANCELLED, NO_SPEECH, INVALID_INPUT, MODEL_UNAVAILABLE,
    DECODE_FAILED, BUSY, UNAVAILABLE, FAILED, CLEANUP_FAILED
  }
  public static final class Outcome {
    public final Status status;
    public final String text;
    private Outcome(Status status, String text) {
      this.status = Objects.requireNonNull(status); this.text = text;
    }
    public static Outcome status(Status status) { return new Outcome(status, null); }
    public static Outcome completed(String text) {
      if (text == null || text.trim().isEmpty()) return status(Status.DECODE_FAILED);
      return new Outcome(Status.COMPLETED, text);
    }
  }

  private static final int DEFAULT_MAX_SAMPLES = 16_000 * 30;
  private static final class Job {
    final long generation;
    final Started started;
    final Completion done;
    volatile boolean cancelled;
    boolean finishing;
    boolean startedNotified;
    boolean cancellationFailed;
    Recorder recorder;
    Request request;
    Job(long generation, Started started, Completion done) {
      this.generation = generation; this.started = started; this.done = done;
    }
  }

  private final Object gate = new Object();
  private final Factory factory;
  private final ExecutorService executor;
  private final int maxSamples;
  private final AtomicLong invalidatedThrough = new AtomicLong();
  private long generation;
  private boolean closed, faulted;
  private volatile Job current;

  public ReplySession(Factory factory) {
    this(factory, Executors.newSingleThreadExecutor(r -> {
      Thread thread = new Thread(r, "sign-to-speak-reply");
      thread.setDaemon(true);
      return thread;
    }), DEFAULT_MAX_SAMPLES);
  }

  public ReplySession(Factory factory, ExecutorService executor, int maxSamples) {
    this.factory = Objects.requireNonNull(factory);
    this.executor = Objects.requireNonNull(executor);
    if (maxSamples <= 0) throw new IllegalArgumentException("maxSamples");
    this.maxSamples = maxSamples;
  }

  /** Returns a generation immediately; Started resolves only after recording really began. */
  public long begin(boolean allowed, Started started, Completion done) {
    Objects.requireNonNull(started); Objects.requireNonNull(done);
    Job job;
    synchronized (gate) {
      if (!allowed || closed || faulted || current != null) return 0;
      job = new Job(++generation, started, done);
      current = job;
    }
    try {
      executor.execute(() -> run(job));
      return job.generation;
    } catch (RuntimeException dispatchFailure) {
      synchronized (gate) { if (current == job) current = null; }
      notifyStarted(job, false);
      notifyDone(job, Outcome.status(Status.FAILED));
      return 0;
    }
  }

  public boolean finish(long token) {
    Job job;
    synchronized (gate) {
      job = current;
      if (job == null || token == 0 || job.generation != token || job.cancelled || job.finishing)
        return false;
      job.finishing = true;
    }
    stopRecorder(job);
    return true;
  }

  /**
   * Revokes a generation without touching the recorder or waiting for its platform calls.
   * Lifecycle owners call this synchronously before dispatching the potentially blocking
   * {@link #cancel(long)} cleanup on a platform worker.
   */
  public void invalidate(long token) {
    if (token == 0) return;
    long observed = invalidatedThrough.get();
    while (observed < token &&
        !invalidatedThrough.compareAndSet(observed, token)) {
      observed = invalidatedThrough.get();
    }
    Job job = current;
    if (job != null && job.generation == token) job.cancelled = true;
  }

  public void cancel(long token) {
    invalidate(token);
    Job job;
    synchronized (gate) {
      job = current;
      if (job == null || token == 0 || job.generation != token) return;
      job.cancelled = true;
      generation++;
    }
    cancelInstalled(job);
  }

  public void cancelAll() {
    Job job;
    synchronized (gate) {
      generation++;
      job = current;
      if (job == null) return;
      job.cancelled = true;
    }
    cancelInstalled(job);
  }

  public boolean isFaulted() { synchronized (gate) { return faulted; } }

  private boolean cancelled(Job job) {
    synchronized (gate) {
      return closed || faulted || job.cancelled ||
          invalidatedThrough.get() >= job.generation || current != job ||
          generation != job.generation;
    }
  }

  private void run(Job job) {
    short[] pcm = new short[maxSamples];
    Outcome outcome = null;
    boolean cleanupFailed = false;
    try {
      Recorder recorder = Objects.requireNonNull(factory.newRecorder());
      synchronized (gate) {
        job.recorder = recorder;
        if (cancelled(job)) { outcome = Outcome.status(Status.CANCELLED); return; }
        // Serialize the platform start with cancellation. Otherwise cancel can stop an
        // as-yet-unstarted recorder, return to Back/pause, and then let this start the mic.
        recorder.start();
      }
      if (cancelled(job)) { outcome = Outcome.status(Status.CANCELLED); return; }
      notifyStarted(job, true);

      int count = 0;
      while (true) {
        synchronized (gate) {
          if (job.cancelled) { outcome = Outcome.status(Status.CANCELLED); break; }
          if (job.finishing) break;
        }
        if (count == pcm.length) {
          synchronized (gate) { job.finishing = true; }
          stopRecorder(job);
          break;
        }
        int read = recorder.read(pcm, count, Math.min(2048, pcm.length - count));
        if (read > 0) count += Math.min(read, pcm.length - count);
        else if (cancelled(job)) { outcome = Outcome.status(Status.CANCELLED); break; }
        else synchronized (gate) {
          if (!job.finishing) { outcome = Outcome.status(Status.FAILED); break; }
        }
      }
      if (outcome != null || cancelled(job)) {
        if (cancelled(job)) outcome = Outcome.status(Status.CANCELLED);
        return;
      }
      if (count == 0) { outcome = Outcome.status(Status.NO_SPEECH); return; }
      Request request = Objects.requireNonNull(factory.newRequest(() -> cancelled(job)));
      synchronized (gate) {
        job.request = request;
        if (cancelled(job)) { outcome = Outcome.status(Status.CANCELLED); return; }
      }
      outcome = Objects.requireNonNull(request.decode(pcm, count));
      if (cancelled(job)) outcome = Outcome.status(Status.CANCELLED);
    } catch (ModelUnavailable failure) {
      outcome = cancelled(job) ? Outcome.status(Status.CANCELLED) : Outcome.status(Status.MODEL_UNAVAILABLE);
    } catch (DecodeFailed failure) {
      outcome = cancelled(job) ? Outcome.status(Status.CANCELLED) : Outcome.status(Status.DECODE_FAILED);
    } catch (CleanupFailed failure) {
      outcome = Outcome.status(Status.CLEANUP_FAILED);
    } catch (IllegalArgumentException failure) {
      outcome = cancelled(job) ? Outcome.status(Status.CANCELLED) : Outcome.status(Status.INVALID_INPUT);
    } catch (RuntimeException | LinkageError failure) {
      outcome = cancelled(job) ? Outcome.status(Status.CANCELLED) : Outcome.status(Status.FAILED);
    } finally {
      Arrays.fill(pcm, (short) 0);
      cleanupFailed |= closeRequest(job.request);
      cleanupFailed |= closeRecorder(job.recorder);
      synchronized (gate) {
        cleanupFailed |= job.cancellationFailed;
        if (cleanupFailed || (outcome != null && outcome.status == Status.CLEANUP_FAILED)) faulted = true;
        if (current == job) current = null;
      }
      notifyStarted(job, false);
      notifyDone(job, cleanupFailed ? Outcome.status(Status.CLEANUP_FAILED) :
          (outcome == null ? Outcome.status(Status.FAILED) : outcome));
    }
  }

  private void cancelInstalled(Job job) {
    boolean failed = false;
    Request request = job.request;
    if (request != null) try { request.cancel(); } catch (RuntimeException | LinkageError ignored) { failed = true; }
    failed |= !stopRecorder(job);
    if (failed) synchronized (gate) { job.cancellationFailed = true; faulted = true; }
  }

  private boolean stopRecorder(Job job) {
    Recorder recorder = job.recorder;
    if (recorder == null) return true;
    try { recorder.stop(); return true; }
    catch (RuntimeException | LinkageError ignored) {
      synchronized (gate) { job.cancellationFailed = true; faulted = true; }
      return false;
    }
  }

  private static boolean closeRecorder(Recorder recorder) {
    if (recorder == null) return false;
    try { recorder.close(); return false; } catch (RuntimeException | LinkageError ignored) { return true; }
  }
  private static boolean closeRequest(Request request) {
    if (request == null) return false;
    try { request.close(); return false; } catch (RuntimeException | LinkageError ignored) { return true; }
  }
  private static void notifyStarted(Job job, boolean recording) {
    synchronized (job) {
      if (job.startedNotified) return;
      job.startedNotified = true;
    }
    try { job.started.complete(recording); } catch (RuntimeException | LinkageError ignored) {}
  }
  private static void notifyDone(Job job, Outcome outcome) {
    try { job.done.complete(outcome); } catch (RuntimeException | LinkageError ignored) {}
  }

  @Override public void close() {
    synchronized (gate) { if (closed) return; closed = true; }
    cancelAll();
    executor.shutdown();
  }
}
