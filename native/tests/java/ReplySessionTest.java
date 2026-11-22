import com.xmilo_at_your_side.sign_to_speak.ReplySession;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

public final class ReplySessionTest {
  private static void check(boolean value, String message) {
    if (!value) throw new AssertionError(message);
  }

  public static void main(String[] args) throws Exception {
    cancellationCannotReturnBeforePendingStartIsSerialized();
    logicalCancellationInvalidatesWithoutWaitingForPendingStart();
    finishPublishesOnlyAfterCleanup();
    cancelUnblocksReadAndSuppressesTranscript();
    exactSampleLimitAutoStopsAndDecodes();
    overflowIsBoundedAndFaultedCleanupFailsClosed();
    System.out.println("ReplySessionTest passed");
  }

  private static void cancellationCannotReturnBeforePendingStartIsSerialized() throws Exception {
    ExecutorService worker = Executors.newSingleThreadExecutor();
    CountDownLatch startEntered = new CountDownLatch(1), allowStart = new CountDownLatch(1);
    CountDownLatch cancelReturned = new CountDownLatch(1), completed = new CountDownLatch(1);
    AtomicBoolean recording = new AtomicBoolean();
    ReplySession session = new ReplySession(new ReplySession.Factory() {
      public ReplySession.Recorder newRecorder() {
        return new ReplySession.Recorder() {
          public void start() {
            startEntered.countDown();
            try { allowStart.await(); } catch (InterruptedException ignored) { Thread.currentThread().interrupt(); }
            recording.set(true);
          }
          public int read(short[] target, int offset, int count) { return recording.get() ? 1 : 0; }
          // Models AudioRecord.stop(): stopping before startRecording completed is a no-op.
          public void stop() { if (recording.get()) recording.set(false); }
          public void close() { recording.set(false); }
        };
      }
      public ReplySession.Request newRequest(java.util.function.BooleanSupplier cancelled) {
        throw new AssertionError("cancelled startup must not decode");
      }
    }, worker, 8);
    long token = session.begin(true, ignored -> {}, ignored -> completed.countDown());
    check(startEntered.await(2, TimeUnit.SECONDS), "start was not reached");
    Thread canceller = new Thread(() -> { session.cancel(token); cancelReturned.countDown(); });
    canceller.start();
    boolean returnedWhileStartPending = cancelReturned.await(100, TimeUnit.MILLISECONDS);
    allowStart.countDown();
    canceller.join(2_000);
    check(completed.await(2, TimeUnit.SECONDS), "cancelled startup did not complete");
    check(!returnedWhileStartPending, "cancel returned before the pending recorder start was serialized");
    session.close(); worker.awaitTermination(1, TimeUnit.SECONDS);
  }

  private static void logicalCancellationInvalidatesWithoutWaitingForPendingStart() throws Exception {
    ExecutorService worker = Executors.newSingleThreadExecutor();
    CountDownLatch startEntered = new CountDownLatch(1), allowStart = new CountDownLatch(1);
    CountDownLatch invalidationReturned = new CountDownLatch(1), completed = new CountDownLatch(1);
    AtomicBoolean recording = new AtomicBoolean(), decoded = new AtomicBoolean();
    AtomicReference<Throwable> invalidationFailure = new AtomicReference<>();
    ReplySession session = new ReplySession(new ReplySession.Factory() {
      public ReplySession.Recorder newRecorder() {
        return new ReplySession.Recorder() {
          public void start() {
            startEntered.countDown();
            try { allowStart.await(); } catch (InterruptedException ignored) { Thread.currentThread().interrupt(); }
            recording.set(true);
          }
          public int read(short[] target, int offset, int count) { return recording.get() ? 1 : 0; }
          public void stop() { recording.set(false); }
          public void close() { recording.set(false); }
        };
      }
      public ReplySession.Request newRequest(java.util.function.BooleanSupplier cancelled) {
        return new ReplySession.Request() {
          public ReplySession.Outcome decode(short[] pcm, int count) {
            decoded.set(true);
            return ReplySession.Outcome.completed("unexpected");
          }
          public void cancel() {}
          public void close() {}
        };
      }
    }, worker, 8);
    long token = session.begin(true, ignored -> {}, ignored -> completed.countDown());
    check(startEntered.await(2, TimeUnit.SECONDS), "start was not reached");
    Thread invalidator = new Thread(() -> {
      try {
        ReplySession.class.getMethod("invalidate", long.class).invoke(session, token);
      } catch (ReflectiveOperationException failure) {
        invalidationFailure.set(failure);
      } finally {
        invalidationReturned.countDown();
      }
    });
    invalidator.setUncaughtExceptionHandler((thread, failure) -> {});
    invalidator.start();
    boolean returnedWhileStartPending = invalidationReturned.await(1, TimeUnit.SECONDS);
    allowStart.countDown();
    invalidator.join(2_000);
    check(completed.await(2, TimeUnit.SECONDS), "invalidated startup did not complete");
    check(returnedWhileStartPending,
        "logical invalidation must not wait for a pending platform recorder start");
    check(invalidationFailure.get() == null,
        "ReplySession must expose immediate logical invalidation: " + invalidationFailure.get());
    check(!decoded.get(), "an invalidated startup must not decode or publish text");
    check(!recording.get(), "an in-flight recorder start must be cleaned after invalidation");
    session.close(); worker.awaitTermination(1, TimeUnit.SECONDS);
  }

  private static void finishPublishesOnlyAfterCleanup() throws Exception {
    ExecutorService worker = Executors.newSingleThreadExecutor();
    AtomicBoolean recording = new AtomicBoolean(true), requestClosed = new AtomicBoolean();
    AtomicInteger started = new AtomicInteger(), completed = new AtomicInteger();
    ReplySession session = new ReplySession(new ReplySession.Factory() {
      public ReplySession.Recorder newRecorder() { return recorder(recording, false); }
      public ReplySession.Request newRequest(java.util.function.BooleanSupplier cancelled) {
        return new ReplySession.Request() {
          public ReplySession.Outcome decode(short[] pcm, int count) {
            check(count > 0 && !cancelled.getAsBoolean(), "decode must receive live bounded PCM");
            return ReplySession.Outcome.completed("hello");
          }
          public void cancel() {}
          public void close() { requestClosed.set(true); }
        };
      }
    }, worker, 32);
    long token = session.begin(true, ok -> { if (ok) started.incrementAndGet(); }, outcome -> {
      check(requestClosed.get(), "completion must follow native cleanup");
      check(outcome.status == ReplySession.Status.COMPLETED && "hello".equals(outcome.text), "typed transcript");
      completed.incrementAndGet();
    });
    await(started, 1);
    session.finish(token);
    await(completed, 1);
    session.close(); worker.awaitTermination(1, TimeUnit.SECONDS);
  }

  private static void cancelUnblocksReadAndSuppressesTranscript() throws Exception {
    ExecutorService worker = Executors.newSingleThreadExecutor();
    AtomicBoolean recording = new AtomicBoolean(true);
    AtomicInteger started = new AtomicInteger(), decoded = new AtomicInteger(), completed = new AtomicInteger();
    ReplySession session = new ReplySession(new ReplySession.Factory() {
      public ReplySession.Recorder newRecorder() { return recorder(recording, false); }
      public ReplySession.Request newRequest(java.util.function.BooleanSupplier cancelled) {
        return new ReplySession.Request() {
          public ReplySession.Outcome decode(short[] pcm, int count) { decoded.incrementAndGet(); return ReplySession.Outcome.completed("stale"); }
          public void cancel() {}
          public void close() {}
        };
      }
    }, worker, 32);
    long token = session.begin(true, ok -> { if (ok) started.incrementAndGet(); }, ignored -> completed.incrementAndGet());
    await(started, 1);
    session.cancel(token);
    await(completed, 1);
    check(decoded.get() == 0, "cancelled capture must not decode or publish stale text");
    session.close(); worker.awaitTermination(1, TimeUnit.SECONDS);
  }

  private static void exactSampleLimitAutoStopsAndDecodes() throws Exception {
    ExecutorService worker = Executors.newSingleThreadExecutor();
    AtomicInteger decodedCount = new AtomicInteger(), completed = new AtomicInteger();
    AtomicBoolean stopped = new AtomicBoolean();
    ReplySession session = new ReplySession(new ReplySession.Factory() {
      public ReplySession.Recorder newRecorder() {
        return new ReplySession.Recorder() {
          public void start() {}
          public int read(short[] target, int offset, int count) {
            for (int i = 0; i < count; i++) target[offset + i] = 3;
            return count;
          }
          public void stop() { stopped.set(true); }
          public void close() {}
        };
      }
      public ReplySession.Request newRequest(java.util.function.BooleanSupplier cancelled) {
        return new ReplySession.Request() {
          public ReplySession.Outcome decode(short[] pcm, int count) {
            check(stopped.get(), "capture must auto-stop before decoding the exact cap");
            decodedCount.set(count);
            return ReplySession.Outcome.completed("at the cap");
          }
          public void cancel() {}
          public void close() {}
        };
      }
    }, worker, 480_000);
    session.begin(true, ignored -> {}, outcome -> {
      check(outcome.status == ReplySession.Status.COMPLETED, "exact cap must remain valid input");
      completed.incrementAndGet();
    });
    await(completed, 1);
    check(decodedCount.get() == 480_000, "decoder must receive the inclusive 480000-sample cap");
    session.close(); worker.awaitTermination(1, TimeUnit.SECONDS);
  }

  private static void overflowIsBoundedAndFaultedCleanupFailsClosed() throws Exception {
    ExecutorService worker = Executors.newSingleThreadExecutor();
    AtomicBoolean recording = new AtomicBoolean(true);
    AtomicInteger completed = new AtomicInteger();
    ReplySession session = new ReplySession(new ReplySession.Factory() {
      public ReplySession.Recorder newRecorder() { return recorder(recording, true); }
      public ReplySession.Request newRequest(java.util.function.BooleanSupplier cancelled) {
        return new ReplySession.Request() {
          public ReplySession.Outcome decode(short[] pcm, int count) {
            check(count == 4, "capture remains bounded at the inclusive cap");
            return ReplySession.Outcome.completed("bounded");
          }
          public void cancel() {}
          public void close() {}
        };
      }
    }, worker, 4);
    long token = session.begin(true, ok -> {}, outcome -> {
      check(outcome.status == ReplySession.Status.CLEANUP_FAILED, "cleanup fault overrides capture result");
      completed.incrementAndGet();
    });
    Thread.sleep(20);
    session.finish(token);
    await(completed, 1);
    check(session.isFaulted(), "cleanup failure must latch");
    check(session.begin(true, ok -> {}, ignored -> {}) == 0, "faulted session fails closed");
    session.close(); worker.awaitTermination(1, TimeUnit.SECONDS);
  }

  private static ReplySession.Recorder recorder(AtomicBoolean recording, boolean failClose) {
    return new ReplySession.Recorder() {
      public void start() { recording.set(true); }
      public int read(short[] target, int offset, int count) {
        if (!recording.get()) return 0;
        int written = Math.min(count, 2);
        for (int i = 0; i < written; i++) target[offset + i] = 7;
        try { Thread.sleep(1); } catch (InterruptedException ignored) { Thread.currentThread().interrupt(); }
        return written;
      }
      public void stop() { recording.set(false); }
      public void close() { if (failClose) throw new IllegalStateException("cleanup"); }
    };
  }

  private static void await(AtomicInteger value, int expected) throws Exception {
    long until = System.nanoTime() + TimeUnit.SECONDS.toNanos(2);
    while (value.get() != expected && System.nanoTime() < until) Thread.sleep(2);
    check(value.get() == expected, "timed out");
  }
}
