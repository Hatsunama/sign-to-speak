package com.xmilo_at_your_side.sign_to_speak;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

public final class ReplyAsrRequestHostTest {
  private static int checks;
  private static void check(boolean value, String label) {
    checks++;
    if (!value) throw new AssertionError(label);
  }
  private static void wiped(short[] values) {
    if (values != null) for (short value : values) check(value == 0, "PCM wiped");
  }

  private static final class Fake implements ReplyAsrRequest.NativeApi {
    volatile Thread owner;
    volatile int status = ReplyAsrRequest.STATUS_OK;
    volatile boolean closeFails;
    volatile int closeCalls;
    volatile boolean cancelCalled;
    volatile Thread cancelThread;
    volatile boolean cancelThrows;
    volatile int closeThrowsRemaining;
    volatile byte[] result = "hello".getBytes(StandardCharsets.UTF_8);
    volatile byte[] observedOutput;
    volatile float[] observedNormalized;
    final CountDownLatch decoding = new CountDownLatch(1);
    final CountDownLatch release = new CountDownLatch(1);
    volatile boolean blockDecode;
    volatile long openResult = 41;
    volatile boolean blockOpen;
    final CountDownLatch opening = new CountDownLatch(1);
    final CountDownLatch openRelease = new CountDownLatch(1);

    private void sameOwner() {
      if (owner == null) owner = Thread.currentThread();
      check(owner == Thread.currentThread(), "open/decode/close share owner worker");
    }
    @Override public long open(int modelFd, long modelOffset, long modelLength,
                               int vadFd, long vadOffset, long vadLength) {
      sameOwner();
      opening.countDown();
      if (blockOpen) try { openRelease.await(5, TimeUnit.SECONDS); }
      catch (InterruptedException interrupted) { Thread.currentThread().interrupt(); return 0; }
      check(modelFd == 11 && modelOffset == 7 && modelLength == 101, "model span forwarded");
      check(vadFd == 12 && vadOffset == 9 && vadLength == 202, "VAD span forwarded");
      return openResult;
    }
    @Override public int decode(long id, float[] pcm, byte[] output) {
      sameOwner();
      check(id == 41, "handle forwarded");
      observedNormalized = pcm;
      observedOutput = output;
      decoding.countDown();
      if (blockDecode) try { release.await(5, TimeUnit.SECONDS); }
      catch (InterruptedException interrupted) { Thread.currentThread().interrupt(); return ReplyAsrRequest.STATUS_DECODE_FAILED; }
      if (status == ReplyAsrRequest.STATUS_OK) {
        System.arraycopy(result, 0, output, 0, result.length);
        output[result.length] = 0;
      }
      return status;
    }
    @Override public void cancel(long id) {
      check(id == 41, "cancel handle"); cancelCalled = true; cancelThread = Thread.currentThread();
      if (cancelThrows) throw new IllegalStateException("injected native cancel failure");
    }
    @Override public int close(long id) {
      sameOwner(); closeCalls++;
      if (closeThrowsRemaining > 0) {
        closeThrowsRemaining--;
        throw new IllegalStateException("injected native close failure");
      }
      return closeFails ? ReplyAsrRequest.STATUS_DECODE_FAILED : ReplyAsrRequest.STATUS_OK;
    }
  }

  private static final class FakeDescriptor implements ReplyModelSpan.Owner {
    volatile int closeCalls;
    final boolean fails;
    private FakeDescriptor(boolean fails) { this.fails = fails; }
    @Override public void close() throws IOException {
      closeCalls++;
      if (fails) throw new IOException("injected descriptor close failure");
    }
  }

  private static ReplyModelSpan ownedSpan(int fd, long offset, long length,
                                           ReplyModelSpan.Owner owner) {
    return new ReplyModelSpan(fd, offset, length, owner);
  }

  private static ReplyAsrRequest request(Fake fake) {
    return new ReplyAsrRequest(new ReplyModelSpan.Pair(
        new ReplyModelSpan(11, 7, 101), new ReplyModelSpan(12, 9, 202)), () -> false, fake);
  }

  public static void main(String[] args) throws Exception {
    Fake normal = new Fake();
    ReplyAsrRequest first = request(normal);
    short[] pcm = {8192, -16384};
    check("hello".equals(first.decode(pcm, pcm.length).text), "UTF-8 result");
    wiped(pcm);
    for (float value : normal.observedNormalized)
      check(Float.floatToRawIntBits(value) == 0, "normalized PCM wiped");
    for (byte value : normal.observedOutput) check(value == 0, "native text output wiped");
    first.close();
    first.close();

    Fake invalidUtf8 = new Fake();
    invalidUtf8.result = new byte[]{(byte) 0xc3, 0x28};
    short[] invalidPcm = {1};
    boolean invalidRejected = false;
    try (ReplyAsrRequest invalid = request(invalidUtf8)) { invalid.decode(invalidPcm, 1); }
    catch (ReplyAsrRequest.Failure expected) {
      invalidRejected = expected.code == ReplyAsrRequest.FailureCode.DECODE_FAILED;
    }
    check(invalidRejected, "malformed native UTF-8 rejected");
    wiped(invalidPcm);

    Fake opening = new Fake();
    opening.blockOpen = true;
    AtomicBoolean stale = new AtomicBoolean();
    AtomicBoolean openRejected = new AtomicBoolean();
    Thread opener = new Thread(() -> {
      try { new ReplyAsrRequest(new ReplyModelSpan.Pair(
          new ReplyModelSpan(11, 7, 101), new ReplyModelSpan(12, 9, 202)), stale::get, opening); }
      catch (IllegalStateException expected) { openRejected.set(true); }
    });
    opener.start();
    check(opening.opening.await(2, TimeUnit.SECONDS), "native open entered");
    stale.set(true);
    opening.openRelease.countDown();
    opener.join(2000);
    check(openRejected.get() && !opener.isAlive(), "open cancellation prevents publication");
    check(opening.cancelCalled, "opened native turn cancelled before cleanup");
    check(opening.closeCalls == 1, "successful cancelled open closes exactly once");

    Fake throwingCancel = new Fake();
    throwingCancel.cancelThrows = true;
    AtomicInteger cancelPolls = new AtomicInteger();
    boolean throwingCancelCleanupReported = false;
    try {
      new ReplyAsrRequest(new ReplyModelSpan.Pair(
          new ReplyModelSpan(11, 7, 101), new ReplyModelSpan(12, 9, 202)),
          () -> cancelPolls.incrementAndGet() > 1, throwingCancel);
    } catch (ReplyAsrRequest.Failure expected) {
      throwingCancelCleanupReported = expected.code == ReplyAsrRequest.FailureCode.CLEANUP_FAILED;
    }
    check(throwingCancel.closeCalls == 1,
        "thrown post-open cancel still rolls back native handle");
    check(throwingCancelCleanupReported, "failed native rollback remains cleanup failure");

    Fake throwingClose = new Fake();
    throwingClose.closeThrowsRemaining = 1;
    AtomicInteger closePolls = new AtomicInteger();
    try {
      new ReplyAsrRequest(new ReplyModelSpan.Pair(
          new ReplyModelSpan(11, 7, 101), new ReplyModelSpan(12, 9, 202)),
          () -> closePolls.incrementAndGet() > 1, throwingClose);
    } catch (IllegalStateException expected) { }
    check(throwingClose.closeCalls == 2,
        "thrown post-open close is retried by native rollback");

    int[] nativeOpenStatuses = {ReplyAsrRequest.STATUS_MODEL_UNAVAILABLE,
        ReplyAsrRequest.STATUS_INVALID_INPUT, ReplyAsrRequest.STATUS_DECODE_FAILED};
    ReplyAsrRequest.FailureCode[] openCodes = {ReplyAsrRequest.FailureCode.MODEL_UNAVAILABLE,
        ReplyAsrRequest.FailureCode.INVALID_INPUT, ReplyAsrRequest.FailureCode.DECODE_FAILED};
    for (int i = 0; i < nativeOpenStatuses.length; i++) {
      Fake failedOpen = new Fake();
      failedOpen.openResult = -nativeOpenStatuses[i] - 1L;
      boolean distinguished = false;
      try { request(failedOpen); }
      catch (ReplyAsrRequest.Failure expected) { distinguished = expected.code == openCodes[i]; }
      check(distinguished, "open failure status preserved");
    }

    Fake cancellation = new Fake();
    cancellation.blockDecode = true;
    short[] held = {24576};
    AtomicReference<ReplyAsrRequest> cancelling = new AtomicReference<>();
    AtomicBoolean cancellationResult = new AtomicBoolean();
    Thread decoder = new Thread(() -> {
      ReplyAsrRequest owned = request(cancellation);
      cancelling.set(owned);
      cancellationResult.set(owned.decode(held, 1).status == ReplyAsrRequest.Status.CANCELLED);
      owned.close();
    });
    decoder.start();
    check(cancellation.decoding.await(2, TimeUnit.SECONDS), "decode entered");
    cancelling.get().cancel();
    check(cancellation.cancelCalled, "native atomic cancel invoked concurrently");
    cancellation.status = ReplyAsrRequest.STATUS_CANCELLED;
    cancellation.release.countDown();
    decoder.join(2000);
    check(!decoder.isAlive(), "decode returned after cancel");
    check(cancellationResult.get(), "cancelled decode unpublished");
    wiped(held);
    cancelling.get().cancel();
    check(true, "cancel after close is harmless");

    Fake affinity = new Fake();
    ReplyAsrRequest threadBound = request(affinity);
    AtomicBoolean foreignCloseRejected = new AtomicBoolean();
    Thread foreignCloser = new Thread(() -> {
      try { threadBound.close(); }
      catch (ReplyAsrRequest.Failure expected) {
        foreignCloseRejected.set(expected.code == ReplyAsrRequest.FailureCode.INVALID_INPUT);
      }
    });
    foreignCloser.start(); foreignCloser.join(2000);
    check(foreignCloseRejected.get(), "close cannot leave owner thread");
    threadBound.close();

    Fake noSpeech = new Fake();
    noSpeech.status = ReplyAsrRequest.STATUS_NO_SPEECH;
    short[] silence = {0};
    try (ReplyAsrRequest quiet = request(noSpeech)) {
      check(quiet.decode(silence, 1).status == ReplyAsrRequest.Status.NO_SPEECH, "no speech typed result");
    }
    wiped(silence);

    int[] nativeDecodeStatuses = {ReplyAsrRequest.STATUS_INVALID_INPUT,
        ReplyAsrRequest.STATUS_MODEL_UNAVAILABLE, ReplyAsrRequest.STATUS_DECODE_FAILED};
    ReplyAsrRequest.FailureCode[] decodeCodes = {ReplyAsrRequest.FailureCode.INVALID_INPUT,
        ReplyAsrRequest.FailureCode.MODEL_UNAVAILABLE, ReplyAsrRequest.FailureCode.DECODE_FAILED};
    for (int i = 0; i < nativeDecodeStatuses.length; i++) {
      Fake failedDecode = new Fake();
      failedDecode.status = nativeDecodeStatuses[i];
      short[] sample = {1};
      boolean distinguished = false;
      try (ReplyAsrRequest failed = request(failedDecode)) { failed.decode(sample, 1); }
      catch (ReplyAsrRequest.Failure expected) { distinguished = expected.code == decodeCodes[i]; }
      check(distinguished, "decode failure status preserved");
      wiped(sample);
    }

    Fake badClose = new Fake();
    badClose.closeFails = true;
    ReplyAsrRequest cleanup = request(badClose);
    boolean cleanupReported = false;
    try { cleanup.close(); } catch (ReplyAsrRequest.Failure expected) {
      cleanupReported = expected.code == ReplyAsrRequest.FailureCode.CLEANUP_FAILED;
    }
    check(cleanupReported, "cleanup failure reported");

    Fake throwingCloseCancel = new Fake();
    throwingCloseCancel.cancelThrows = true;
    ReplyAsrRequest closeAfterCancelThrow = request(throwingCloseCancel);
    boolean throwingCloseCancelReported = false;
    try { closeAfterCancelThrow.close(); }
    catch (ReplyAsrRequest.Failure expected) {
      throwingCloseCancelReported = expected.code == ReplyAsrRequest.FailureCode.CLEANUP_FAILED;
    }
    check(throwingCloseCancel.closeCalls == 1,
        "terminal close still releases handle after cancel throws");
    check(throwingCloseCancelReported, "terminal cancel failure reported as cleanup failure");

    Fake descriptorCleanup = new Fake();
    descriptorCleanup.closeFails = true;
    FakeDescriptor modelDescriptor = new FakeDescriptor(false);
    FakeDescriptor vadDescriptor = new FakeDescriptor(true);
    boolean descriptorCleanupReported = false;
    try {
      new ReplyAsrRequest(new ReplyModelSpan.Pair(
          ownedSpan(11, 7, 101, modelDescriptor),
          ownedSpan(12, 9, 202, vadDescriptor)), () -> false, descriptorCleanup);
    } catch (ReplyAsrRequest.Failure expected) {
      descriptorCleanupReported = expected.code == ReplyAsrRequest.FailureCode.CLEANUP_FAILED;
    }
    check(descriptorCleanupReported, "descriptor cleanup failure reported");
    check(vadDescriptor.closeCalls == 1 && modelDescriptor.closeCalls == 1,
        "all admitted descriptors closed after one close failure");
    check(descriptorCleanup.cancelCalled && descriptorCleanup.closeCalls == 1,
        "native handle rolled back after descriptor close failure");
    check(descriptorCleanup.cancelThread == descriptorCleanup.owner,
        "descriptor rollback stays on native owner worker");

    Fake bounds = new Fake();
    try (ReplyAsrRequest bounded = request(bounds)) {
      short[][] rejected = {null, new short[0], new short[ReplyAsrRequest.MAX_SAMPLES + 1]};
      for (short[] input : rejected) {
        boolean failed = false;
        try { bounded.decode(input, input == null ? 0 : input.length); }
        catch (ReplyAsrRequest.Failure expected) {
          failed = expected.code == ReplyAsrRequest.FailureCode.INVALID_INPUT;
        }
        check(failed, "invalid PCM rejected");
        wiped(input);
      }
    }
    System.out.println("ReplyAsrRequest host: " + checks + " checks passed");
  }
}
