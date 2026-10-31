import com.xmilo_at_your_side.sign_to_speak.SpeechSession;
import java.util.Arrays;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import java.util.function.BooleanSupplier;

public final class SpeechSessionTest {
  static int checks;
  static void check(boolean ok, String message) { checks++; if (!ok) throw new AssertionError(message); }
  static void await(CountDownLatch latch) {
    try { if (!latch.await(3, TimeUnit.SECONDS)) throw new AssertionError("timed out"); }
    catch (InterruptedException e) { throw new AssertionError(e); }
  }
  static class StubRequest implements SpeechSession.Request {
    final AtomicInteger cancels = new AtomicInteger(), closes = new AtomicInteger();
    byte[] seen;
    short[] pcm = {1, 2, 3};
    CountDownLatch entered, release;
    public short[] synthesize(byte[] ascii) {
      seen = ascii;
      if (entered != null) { entered.countDown(); await(release); }
      return pcm;
    }
    public void cancel() { cancels.incrementAndGet(); }
    public void close() { closes.incrementAndGet(); }
  }
  static class StubPlayer implements SpeechSession.Player {
    final AtomicInteger cancels = new AtomicInteger(), closes = new AtomicInteger(), plays = new AtomicInteger();
    java.util.function.BooleanSupplier guard;
    CountDownLatch entered, release;
    public void play(short[] pcm, java.util.function.BooleanSupplier cancelled) {
      plays.incrementAndGet(); guard = cancelled;
      if (entered != null) { entered.countDown(); await(release); }
    }
    public void cancel() { cancels.incrementAndGet(); }
    public void close() { closes.incrementAndGet(); }
  }
  static class StubFactory implements SpeechSession.Factory {
    StubRequest request = new StubRequest();
    StubPlayer player = new StubPlayer();
    public SpeechSession.Request newRequest(BooleanSupplier cancelled) { return request; }
    public SpeechSession.Player newPlayer() { return player; }
  }
  static SpeechSession.Result take(BlockingQueue<SpeechSession.Result> q) {
    try { SpeechSession.Result r = q.poll(3, TimeUnit.SECONDS); if (r == null) throw new AssertionError("no completion"); return r; }
    catch (InterruptedException e) { throw new AssertionError(e); }
  }
  static void completedAndWiped() {
    StubFactory f = new StubFactory(); SpeechSession s = new SpeechSession(f);
    BlockingQueue<SpeechSession.Result> q = new LinkedBlockingQueue<>();
    long t = s.begin(true); byte[] caller = {'H','i'};
    s.speak(t, caller, q::add);
    check(take(q) == SpeechSession.Result.COMPLETED, "completed");
    check(Arrays.equals(caller, new byte[]{'H','i'}), "caller owns input");
    check(Arrays.equals(f.request.seen, new byte[2]), "internal text erased");
    check(Arrays.equals(f.request.pcm, new short[3]), "PCM erased");
    check(f.request.closes.get() == 1 && f.player.closes.get() == 1, "resources closed");
    s.close(); check(s.begin(true) == 0, "closed denies begin");
  }
  static void busyAndCancelDuringSynthesis() {
    StubFactory f = new StubFactory(); f.request.entered = new CountDownLatch(1); f.request.release = new CountDownLatch(1);
    SpeechSession s = new SpeechSession(f); long t = s.begin(true);
    BlockingQueue<SpeechSession.Result> q = new LinkedBlockingQueue<>();
    s.speak(t, new byte[]{'A'}, q::add); await(f.request.entered);
    s.speak(t, new byte[]{'B'}, q::add);
    check(take(q) == SpeechSession.Result.BUSY, "one job only");
    s.stop(); check(f.request.cancels.get() > 0, "native cancelled");
    f.request.release.countDown();
    check(take(q) == SpeechSession.Result.CANCELLED, "old synthesis cancelled");
    check(f.player.plays.get() == 0, "no stale playback");
    check(s.begin(true) != t, "new generation");
    s.close();
  }
  static void cancelDuringPlayback() {
    StubFactory f = new StubFactory(); f.player.entered = new CountDownLatch(1); f.player.release = new CountDownLatch(1);
    SpeechSession s = new SpeechSession(f); BlockingQueue<SpeechSession.Result> q = new LinkedBlockingQueue<>();
    s.speak(s.begin(true), new byte[]{'A'}, q::add); await(f.player.entered);
    s.stop(); check(f.player.cancels.get() > 0 && f.player.guard.getAsBoolean(), "player cancelled and guarded");
    f.player.release.countDown(); check(take(q) == SpeechSession.Result.CANCELLED, "playback cancelled");
    s.close();
  }
  static void cancellationFaultReported() {
    StubFactory f = new StubFactory();
    f.request = new StubRequest() {
      @Override public void cancel() { super.cancel(); throw new IllegalStateException("request cancellation failed"); }
    };
    f.player = new StubPlayer() {
      @Override public void cancel() { super.cancel(); throw new IllegalStateException("player cancellation failed"); }
    };
    f.player.entered = new CountDownLatch(1); f.player.release = new CountDownLatch(1);
    SpeechSession s = new SpeechSession(f); BlockingQueue<SpeechSession.Result> q = new LinkedBlockingQueue<>();
    s.speak(s.begin(true), new byte[]{'A'}, q::add); await(f.player.entered);
    s.stop();
    check(f.request.cancels.get() == 1 && f.player.cancels.get() == 1, "both cancellation actions attempted");
    check(s.isFaulted(), "cancellation fault latched");
    f.player.release.countDown();
    check(take(q) == SpeechSession.Result.CLEANUP_FAILED, "cancellation cleanup failure reported");
    check(f.request.closes.get() == 1 && f.player.closes.get() == 1, "resources closed after cancel failure");
    check(Arrays.equals(f.request.seen, new byte[1]) && Arrays.equals(f.request.pcm, new short[3]), "buffers erased after cancel failure");
    check(s.begin(true) == 0, "fault denies new generation");
    s.close();
  }  static class DirectExecutor extends AbstractExecutorService {
    boolean shutdown;
    public void shutdown() { shutdown = true; }
    public java.util.List<Runnable> shutdownNow() { shutdown = true; return java.util.Collections.emptyList(); }
    public boolean isShutdown() { return shutdown; }
    public boolean isTerminated() { return shutdown; }
    public boolean awaitTermination(long timeout, TimeUnit unit) { return shutdown; }
    public void execute(Runnable command) {
      if (shutdown) throw new RejectedExecutionException();
      command.run();
    }
  }
  static void fatalErrorPropagatesAfterCleanup() {
    StubFactory f = new StubFactory();
    f.request = new StubRequest() {
      @Override public short[] synthesize(byte[] ascii) { seen = ascii; throw new OutOfMemoryError("simulated"); }
    };
    SpeechSession s = new SpeechSession(f, new DirectExecutor());
    BlockingQueue<SpeechSession.Result> q = new LinkedBlockingQueue<>();
    boolean propagated = false;
    try { s.speak(s.begin(true), new byte[]{'A'}, q::add); }
    catch (OutOfMemoryError expected) { propagated = true; }
    check(propagated, "fatal VM error propagates");
    check(take(q) == SpeechSession.Result.FAILED && q.isEmpty(), "fatal job completes exactly once");
    check(f.request.closes.get() == 1 && Arrays.equals(f.request.seen, new byte[1]), "fatal job cleans up");
    s.close();
  }  static class FatalDispatchExecutor extends DirectExecutor {
    Runnable captured;
    boolean fail = true;
    @Override public void execute(Runnable command) {
      if (fail) {
        fail = false;
        captured = command;
        throw new OutOfMemoryError("dispatch failed before worker start");
      }
      super.execute(command);
    }
  }
  static byte[] capturedJobText(Runnable runnable) {
    try {
      for (java.lang.reflect.Field field : runnable.getClass().getDeclaredFields()) {
        field.setAccessible(true);
        Object value = field.get(runnable);
        if (value != null && value.getClass().getSimpleName().equals("Job")) {
          java.lang.reflect.Field ascii = value.getClass().getDeclaredField("ascii");
          ascii.setAccessible(true);
          return (byte[]) ascii.get(value);
        }
      }
      throw new AssertionError("submitted job was not captured");
    } catch (ReflectiveOperationException e) { throw new AssertionError(e); }
  }
  static void fatalDispatchFailureWipesAndRecovers() {
    FatalDispatchExecutor executor = new FatalDispatchExecutor();
    AtomicInteger factoryStarts = new AtomicInteger();
    StubFactory f = new StubFactory() {
      @Override public SpeechSession.Request newRequest(BooleanSupplier cancelled) { factoryStarts.incrementAndGet(); return super.newRequest(cancelled); }
    };
    SpeechSession s = new SpeechSession(f, executor);
    BlockingQueue<SpeechSession.Result> q = new LinkedBlockingQueue<>();
    long token = s.begin(true);
    boolean propagated = false;
    try { s.speak(token, new byte[]{'S','E','C','R','E','T'}, q::add); }
    catch (OutOfMemoryError expected) { propagated = true; }
    check(propagated, "fatal dispatch error propagates");
    check(Arrays.equals(capturedJobText(executor.captured), new byte[6]), "owned text erased after failed dispatch");
    check(take(q) == SpeechSession.Result.FAILED && q.isEmpty(), "failed dispatch completes exactly once");
    executor.captured.run();
    check(factoryStarts.get() == 0, "abandoned queued command never starts factory");
    check(q.isEmpty(), "abandoned queued command never completes twice");
    s.speak(token, new byte[]{'B'}, q::add);
    check(take(q) == SpeechSession.Result.COMPLETED, "failed dispatch does not strand current job");
    s.close();
  }  static void nativeLinkageFailureIsSanitized() {
    SpeechSession.Factory missing = new SpeechSession.Factory() {
      public SpeechSession.Request newRequest(BooleanSupplier cancelled) { throw new UnsatisfiedLinkError("private native path"); }
      public SpeechSession.Player newPlayer() { throw new AssertionError("player should not be created"); }
    };
    SpeechSession s = new SpeechSession(missing, new DirectExecutor());
    BlockingQueue<SpeechSession.Result> q = new LinkedBlockingQueue<>();
    s.speak(s.begin(true), new byte[]{'A'}, q::add);
    check(take(q) == SpeechSession.Result.FAILED, "native linkage failure sanitized");
    s.close();
  }  static void invalidInput() {
    StubFactory f = new StubFactory(); SpeechSession s = new SpeechSession(f);
    BlockingQueue<SpeechSession.Result> q = new LinkedBlockingQueue<>(); long t = s.begin(true);
    s.speak(t, new byte[]{0}, q::add); check(take(q) == SpeechSession.Result.INVALID_INPUT, "NUL invalid");
    s.speak(t, new byte[257], q::add); check(take(q) == SpeechSession.Result.INVALID_INPUT, "bounded");
    check(f.request.seen == null, "invalid input never reaches engine");
    s.close();
  }
  static void pendingAndRejectedExecutor() {
    ExecutorService worker = Executors.newSingleThreadExecutor();
    CountDownLatch occupied = new CountDownLatch(1), release = new CountDownLatch(1);
    worker.execute(() -> { occupied.countDown(); await(release); }); await(occupied);
    StubFactory f = new StubFactory(); SpeechSession s = new SpeechSession(f, worker);
    BlockingQueue<SpeechSession.Result> q = new LinkedBlockingQueue<>();
    s.speak(s.begin(true), new byte[]{'A'}, q::add);
    s.stop(); release.countDown();
    check(take(q) == SpeechSession.Result.CANCELLED, "queued work invalidated");
    check(f.request.seen == null, "queued work never enters native");
    s.close();

    ExecutorService rejected = Executors.newSingleThreadExecutor(); rejected.shutdown();
    SpeechSession denied = new SpeechSession(new StubFactory(), rejected);
    BlockingQueue<SpeechSession.Result> refused = new LinkedBlockingQueue<>();
    long token = denied.begin(true);
    denied.speak(token, new byte[]{'A'}, refused::add);
    check(take(refused) == SpeechSession.Result.FAILED, "submission rejection completes");
    denied.close();
  }
  static void cancelBeforeFactoryReturns() {
    StubFactory f = new StubFactory();
    CountDownLatch entered = new CountDownLatch(1), release = new CountDownLatch(1);
    SpeechSession.Factory slow = new SpeechSession.Factory() {
      public SpeechSession.Request newRequest(BooleanSupplier cancelled) { entered.countDown(); await(release); return f.request; }
      public SpeechSession.Player newPlayer() { return f.player; }
    };
    SpeechSession s = new SpeechSession(slow);
    BlockingQueue<SpeechSession.Result> q = new LinkedBlockingQueue<>();
    s.speak(s.begin(true), new byte[]{'A'}, q::add); await(entered);
    s.stop();
    long retry = s.begin(true);
    s.speak(retry, new byte[]{'B'}, q::add);
    check(take(q) == SpeechSession.Result.BUSY, "new generation waits for uncancellable factory to finish");
    release.countDown();
    check(take(q) == SpeechSession.Result.CANCELLED, "factory result cancelled");
    check(f.request.closes.get() == 1 && f.request.seen == null, "loose request closed without synthesis");
    s.speak(retry, new byte[]{'B'}, q::add);
    check(take(q) == SpeechSession.Result.COMPLETED, "fresh job starts after old factory closes");
    s.close();
  }
  static void stopCancelsFactoryPreparationPromptly() {
    StubFactory f = new StubFactory();
    CountDownLatch entered = new CountDownLatch(1);
    AtomicInteger attempts = new AtomicInteger();
    SpeechSession.Factory preparing = new SpeechSession.Factory() {
      public SpeechSession.Request newRequest(BooleanSupplier cancelled) {
        if (attempts.incrementAndGet() > 1) return f.request;
        entered.countDown();
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(2);
        while (!cancelled.getAsBoolean() && System.nanoTime() < deadline) {
          try { Thread.sleep(5); } catch (InterruptedException e) { Thread.currentThread().interrupt(); break; }
        }
        if (!cancelled.getAsBoolean()) throw new AssertionError("factory cancellation never arrived");
        return f.request;
      }
      public SpeechSession.Player newPlayer() { return f.player; }
    };
    SpeechSession s = new SpeechSession(preparing);
    BlockingQueue<SpeechSession.Result> q = new LinkedBlockingQueue<>();
    s.speak(s.begin(true), new byte[]{'A'}, q::add);
    await(entered);
    try {
      s.stop();
      SpeechSession.Result result = q.poll(750, TimeUnit.MILLISECONDS);
      check(result == SpeechSession.Result.CANCELLED, "Stop completes blocked factory preparation promptly");
      check(f.request.seen == null && f.player.plays.get() == 0,
          "cancelled preparation cannot synthesize or play stale audio");
      long retry = s.begin(true);
      s.speak(retry, new byte[]{'B'}, q::add);
      check(take(q) == SpeechSession.Result.COMPLETED && f.player.plays.get() == 1,
          "explicit retry can play only fresh speech");
    } catch (InterruptedException e) { throw new AssertionError(e); }
    finally { s.close(); }
  }
  static void cleanupFaultAndCallbackFailure() {
    StubFactory f = new StubFactory();
    f.request = new StubRequest() { @Override public void close() { super.close(); throw new IllegalStateException(); } };
    SpeechSession s = new SpeechSession(f);
    BlockingQueue<SpeechSession.Result> q = new LinkedBlockingQueue<>();
    long token = s.begin(true);
    s.speak(token, new byte[]{'A'}, q::add);
    check(take(q) == SpeechSession.Result.CLEANUP_FAILED, "cleanup failure reported");
    check(f.player.closes.get() == 1, "other cleanup still attempted");
    check(s.isFaulted() && s.begin(true) == 0, "fault latched across begin");
    s.close();

    StubFactory normal = new StubFactory(); SpeechSession callback = new SpeechSession(normal);
    long t = callback.begin(true);
    CountDownLatch callbackRan = new CountDownLatch(1);
    callback.speak(t, new byte[]{'A'}, result -> { callbackRan.countDown(); throw new RuntimeException(); });
    await(callbackRan);
    BlockingQueue<SpeechSession.Result> completed = new LinkedBlockingQueue<>();
    // A throwing completion must not strand a job or poison a later generation.
    callback.speak(t, new byte[]{'B'}, completed::add);
    SpeechSession.Result second = take(completed);
    check(second == SpeechSession.Result.COMPLETED || second == SpeechSession.Result.BUSY,
        "callback failure contained");
    callback.close();
  }
  public static void main(String[] args) {
    completedAndWiped(); busyAndCancelDuringSynthesis(); cancelDuringPlayback(); cancellationFaultReported(); fatalErrorPropagatesAfterCleanup(); fatalDispatchFailureWipesAndRecovers(); nativeLinkageFailureIsSanitized(); invalidInput(); pendingAndRejectedExecutor(); cancelBeforeFactoryReturns(); stopCancelsFactoryPreparationPromptly(); cleanupFaultAndCallbackFailure();
    System.out.println("SpeechSession: " + checks + " checks passed");
  }
}
