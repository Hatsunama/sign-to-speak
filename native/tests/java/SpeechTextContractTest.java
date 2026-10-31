import com.xmilo_at_your_side.sign_to_speak.SpeechSession;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.concurrent.AbstractExecutorService;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.function.BooleanSupplier;

/** Standalone checks of the reviewed-text boundary using the real SpeechSession. */
public final class SpeechTextContractTest {
  private static int checks;

  private static void check(boolean condition, String message) {
    checks++;
    if (!condition) throw new AssertionError(message);
  }

  private static SpeechSession.Result take(BlockingQueue<SpeechSession.Result> results) {
    try {
      SpeechSession.Result result = results.poll(3, TimeUnit.SECONDS);
      if (result == null) throw new AssertionError("speech did not complete");
      return result;
    } catch (InterruptedException failure) {
      Thread.currentThread().interrupt();
      throw new AssertionError(failure);
    }
  }

  private static void await(CountDownLatch latch) {
    try {
      if (!latch.await(3, TimeUnit.SECONDS)) throw new AssertionError("synthesis did not reach barrier");
    } catch (InterruptedException failure) {
      Thread.currentThread().interrupt();
      throw new AssertionError(failure);
    }
  }

  private static final class DirectExecutor extends AbstractExecutorService {
    private boolean shutdown;
    @Override public void shutdown() { shutdown = true; }
    @Override public java.util.List<Runnable> shutdownNow() {
      shutdown = true;
      return java.util.Collections.emptyList();
    }
    @Override public boolean isShutdown() { return shutdown; }
    @Override public boolean isTerminated() { return shutdown; }
    @Override public boolean awaitTermination(long timeout, TimeUnit unit) { return shutdown; }
    @Override public void execute(Runnable command) {
      if (shutdown) throw new RejectedExecutionException();
      command.run();
    }
  }

  private static class RecordingFactory implements SpeechSession.Factory {
    int requests, players, plays, closes, cancels;
    byte[] received, ownedText;
    short[] ownedPcm;
    BooleanSupplier cancellationGuard;
    CountDownLatch synthesisEntered, releaseSynthesis;

    @Override public SpeechSession.Request newRequest(BooleanSupplier cancelled) {
      requests++;
      return new SpeechSession.Request() {
        @Override public short[] synthesize(byte[] utf8) {
          received = utf8.clone();
          ownedText = utf8;
          ownedPcm = new short[] { 11, -12, 13 };
          if (synthesisEntered != null) {
            synthesisEntered.countDown();
            await(releaseSynthesis);
          }
          return ownedPcm;
        }
        @Override public void cancel() { cancels++; }
        @Override public void close() { closes++; }
      };
    }
    @Override public SpeechSession.Player newPlayer() {
      players++;
      return new SpeechSession.Player() {
        @Override public void play(short[] pcm, BooleanSupplier cancelled) {
          plays++;
          cancellationGuard = cancelled;
          check(pcm == ownedPcm, "player receives the synthesized PCM");
        }
        @Override public void cancel() { }
        @Override public void close() { closes++; }
      };
    }
  }

  private static void accepted(String text) {
    RecordingFactory factory = new RecordingFactory();
    SpeechSession session = new SpeechSession(factory, new DirectExecutor());
    try {
      byte[] caller = text.getBytes(StandardCharsets.UTF_8);
      byte[] expected = caller.clone();
      BlockingQueue<SpeechSession.Result> results = new LinkedBlockingQueue<>();
      session.speak(session.begin(true), caller, results::add);
      check(take(results) == SpeechSession.Result.COMPLETED, "valid reviewed text completes: " + text);
      check(Arrays.equals(factory.received, expected), "UTF-8 bytes reach synthesis unchanged");
      check(Arrays.equals(caller, expected), "session does not erase caller-owned input");
      check(factory.requests == 1 && factory.players == 1 && factory.plays == 1,
          "one request and protected playback for accepted text");
      check(factory.closes == 2, "both resources close after accepted text");
      check(Arrays.equals(factory.ownedText, new byte[expected.length]), "owned text is erased");
      check(Arrays.equals(factory.ownedPcm, new short[3]), "owned PCM is erased");
    } finally {
      session.close();
    }
  }

  private static void rejected(byte[] candidate, String reason) {
    RecordingFactory factory = new RecordingFactory();
    SpeechSession session = new SpeechSession(factory, new DirectExecutor());
    try {
      BlockingQueue<SpeechSession.Result> results = new LinkedBlockingQueue<>();
      long token = session.begin(true);
      session.speak(token, candidate, results::add);
      check(take(results) == SpeechSession.Result.INVALID_INPUT, reason);
      check(factory.requests == 0 && factory.players == 0, "invalid text never reaches engine");
      session.speak(token, "Ready".getBytes(StandardCharsets.UTF_8), results::add);
      check(take(results) == SpeechSession.Result.COMPLETED, "invalid input leaves session usable");
      check(factory.requests == 1 && factory.plays == 1, "recovery request plays once");
    } finally {
      session.close();
    }
  }

  private static void reviewedTextPassesAndIsErased() {
    accepted("Caf\u00e9 \u2014 na\u00efve, \u6771\u4eac! ");
    accepted("\u00e9".repeat(256)); // 256 UTF-16 units and exactly 512 UTF-8 bytes.
    accepted("\ud83d\ude42".repeat(128)); // 256 UTF-16 units and exactly 512 UTF-8 bytes.
  }

  private static void malformedAndUnsafeTextIsRejected() {
    rejected(null, "null input");
    rejected(new byte[0], "empty input");
    rejected("   ".getBytes(StandardCharsets.UTF_8), "ASCII whitespace only");
    rejected("\u2003\u3000".getBytes(StandardCharsets.UTF_8), "Unicode whitespace only");
    rejected("x\n".getBytes(StandardCharsets.UTF_8), "newline control");
    rejected("x\u007f".getBytes(StandardCharsets.UTF_8), "DEL control");
    rejected("x\u0085".getBytes(StandardCharsets.UTF_8), "C1 control");
    rejected(new byte[] {'x', 0}, "NUL control");
    rejected(new byte[] {(byte) 0xc3, (byte) 0x28}, "malformed continuation");
    rejected(new byte[] {(byte) 0xc3}, "truncated sequence");
    rejected(new byte[] {(byte) 0xc0, (byte) 0xaf}, "overlong sequence");
    rejected(new byte[] {(byte) 0xed, (byte) 0xa0, (byte) 0x80}, "encoded surrogate");
    rejected(new byte[] {(byte) 0xf4, (byte) 0x90, (byte) 0x80, (byte) 0x80},
        "code point above Unicode maximum");
    rejected("a".repeat(257).getBytes(StandardCharsets.UTF_8), "UI 256 UTF-16 unit limit");
    rejected("\u00e9".repeat(257).getBytes(StandardCharsets.UTF_8), "257 decoded UTF-16 units");
    rejected("\u20ac".repeat(171).getBytes(StandardCharsets.UTF_8), "513 UTF-8 bytes");
  }

  private static void encoderUsesTheSameBoundary() {
    String reviewed = "Caf\u00e9 \u2014 \u6771\u4eac \ud83d\ude42";
    byte[] expected = reviewed.getBytes(StandardCharsets.UTF_8);
    byte[] encoded = SpeechSession.encodeText(reviewed);
    check(encoded != null && Arrays.equals(encoded, expected),
        "encoder preserves valid UTF-8 bytes without replacement");
    check(SpeechSession.encodeText("\ud800") == null, "unpaired high surrogate rejected");
    check(SpeechSession.encodeText("\udc00") == null, "unpaired low surrogate rejected");
    check(SpeechSession.encodeText("x\ud800y") == null, "embedded unpaired surrogate rejected");
    check(SpeechSession.encodeText("\u00e9".repeat(256)) != null,
        "512 UTF-8 bytes and 256 UTF-16 units accepted");
    check(SpeechSession.encodeText("\u00e9".repeat(257)) == null,
        "257 UTF-16 units rejected by encoder");
    check(SpeechSession.encodeText("\u20ac".repeat(171)) == null,
        "513 UTF-8 bytes rejected by encoder");
    check(SpeechSession.encodeText("\u2003\u3000") == null,
        "Unicode blank text rejected by encoder");
    check(SpeechSession.encodeText("x\u0085") == null,
        "Unicode control rejected by encoder");
  }

  private static void cancelledUnicodeJobIsErasedAndCannotPlay() {
    RecordingFactory factory = new RecordingFactory();
    factory.synthesisEntered = new CountDownLatch(1);
    factory.releaseSynthesis = new CountDownLatch(1);
    SpeechSession session = new SpeechSession(factory);
    BlockingQueue<SpeechSession.Result> results = new LinkedBlockingQueue<>();
    try {
      long token = session.begin(true);
      byte[] caller = "Caf\u00e9".getBytes(StandardCharsets.UTF_8);
      session.speak(token, caller, results::add);
      await(factory.synthesisEntered);
      check(Arrays.equals(factory.received, caller), "unicode reaches active synthesis unchanged");
      session.stop();
      check(factory.cancels == 1, "stop cancels active synthesis");
      factory.releaseSynthesis.countDown();
      check(take(results) == SpeechSession.Result.CANCELLED, "stale unicode job is cancelled");
      check(factory.players == 0 && factory.plays == 0, "stale job never reaches playback");
      check(Arrays.equals(factory.ownedText, new byte[caller.length]), "cancelled text is erased");
      check(Arrays.equals(factory.ownedPcm, new short[3]), "cancelled PCM is erased");
      check(Arrays.equals(caller, "Caf\u00e9".getBytes(StandardCharsets.UTF_8)),
          "cancel does not erase caller-owned input");
      factory.synthesisEntered = null;
      session.speak(session.begin(true), "Ready".getBytes(StandardCharsets.UTF_8), results::add);
      check(take(results) == SpeechSession.Result.COMPLETED, "new generation recovers after cancellation");
    } finally {
      factory.releaseSynthesis.countDown();
      session.close();
    }
  }

  private static void nativeInputMarkerIsSpecific() {
    RecordingFactory factory = new RecordingFactory() {
      @Override public SpeechSession.Request newRequest(BooleanSupplier cancelled) {
        return new SpeechSession.Request() {
          @Override public short[] synthesize(byte[] utf8) {
            throw new SpeechSession.InvalidInput("native token limit");
          }
          @Override public void cancel() { }
          @Override public void close() { closes++; }
        };
      }
    };
    SpeechSession session = new SpeechSession(factory, new DirectExecutor());
    try {
      BlockingQueue<SpeechSession.Result> results = new LinkedBlockingQueue<>();
      session.speak(session.begin(true), "Ready".getBytes(StandardCharsets.UTF_8), results::add);
      check(take(results) == SpeechSession.Result.INVALID_INPUT,
          "only native input marker maps to invalid input");
    } finally {
      session.close();
    }

    SpeechSession.Factory badPlayer = new SpeechSession.Factory() {
      @Override public SpeechSession.Request newRequest(BooleanSupplier cancelled) {
        return new SpeechSession.Request() {
          @Override public short[] synthesize(byte[] utf8) { return new short[] {1}; }
          @Override public void cancel() { }
          @Override public void close() { }
        };
      }
      @Override public SpeechSession.Player newPlayer() {
        throw new IllegalArgumentException("bad player");
      }
    };
    SpeechSession other = new SpeechSession(badPlayer, new DirectExecutor());
    try {
      BlockingQueue<SpeechSession.Result> results = new LinkedBlockingQueue<>();
      other.speak(other.begin(true), "Ready".getBytes(StandardCharsets.UTF_8), results::add);
      check(take(results) == SpeechSession.Result.FAILED,
          "unrelated illegal argument remains a failure");
    } finally {
      other.close();
    }
  }

  public static void main(String[] args) {
    nativeInputMarkerIsSpecific();
    encoderUsesTheSameBoundary();
    reviewedTextPassesAndIsErased();
    malformedAndUnsafeTextIsRejected();
    cancelledUnicodeJobIsErasedAndCannotPlay();
    System.out.println("SpeechTextContract: " + checks + " checks passed");
  }
}
