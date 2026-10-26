import com.xmilo_at_your_side.sign_to_speak.PocketTtsRequest;
import com.xmilo_at_your_side.sign_to_speak.SpeechSession;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;

/** Real JNI checks; invoke with an approved local Alba model directory. */
public final class PocketTtsRequestHostTest {
  private static int checks;
  private static void check(boolean ok, String label) {
    checks++;
    if (!ok) throw new AssertionError(label);
  }
  private static void wiped(byte[] bytes) {
    for (byte value : bytes) check(value == 0, "input wiped");
  }
  private static void invalid(PocketTtsRequest request, byte[] bytes) {
    boolean rejected = false;
    try { request.synthesize(bytes); }
    catch (SpeechSession.InvalidInput expected) {
      check("Invalid speech text".equals(expected.getMessage()), "sanitized invalid input");
      rejected = true;
    }
    check(rejected, "input rejected");
    if (bytes != null) wiped(bytes);
  }
  public static void main(String[] args) {
    if (args.length != 1) throw new IllegalArgumentException("Expected approved local model root");
    boolean missing = false;
    try { new PocketTtsRequest(args[0] + "/missing-model-root"); }
    catch (IllegalStateException expected) { missing = true; }
    check(missing, "missing model root rejected");
    try (PocketTtsRequest request = new PocketTtsRequest(args[0])) {
      byte[] first = "Hello.".getBytes(StandardCharsets.UTF_8);
      short[] pcm = request.synthesize(first);
      check(pcm != null && pcm.length > 0 && pcm.length <= 720_000, "bounded 24 kHz mono PCM");
      boolean nonzero = false;
      for (short value : pcm) if (value != 0) { nonzero = true; break; }
      check(nonzero, "nonzero PCM");
      wiped(first);
      Arrays.fill(pcm, (short) 0);
      invalid(request, null);
      invalid(request, new byte[0]);
      invalid(request, new byte[513]);
      invalid(request, new byte[]{'A', 0, 'B'});
      invalid(request, new byte[]{(byte) 0xc3, (byte) 0x28});
      invalid(request, "   ".getBytes(StandardCharsets.UTF_8));
      byte[] again = "Again.".getBytes(StandardCharsets.UTF_8);
      short[] repeated = request.synthesize(again);
      check(repeated != null && repeated.length > 0, "repeated synthesis");
      wiped(again);
      Arrays.fill(repeated, (short) 0);
    }
    try (PocketTtsRequest cancelled = new PocketTtsRequest(args[0])) {
      cancelled.cancel();
      byte[] text = "Cancelled.".getBytes(StandardCharsets.UTF_8);
      check(cancelled.synthesize(text) == null, "cancelled request returns no PCM");
      wiped(text);
    }
    PocketTtsRequest closed = new PocketTtsRequest(args[0]);
    closed.close();
    closed.close();
    closed.cancel();
    byte[] text = "Closed.".getBytes(StandardCharsets.UTF_8);
    boolean denied = false;
    try { closed.synthesize(text); }
    catch (IllegalStateException expected) { denied = true; }
    check(denied, "closed request denied");
    wiped(text);
    for (int i = 0; i < 3; i++) {
      try (PocketTtsRequest lifecycle = new PocketTtsRequest(args[0])) {
        lifecycle.cancel();
      }
    }
    System.out.println("PocketTtsRequest JNI: " + checks + " checks passed");
  }
}
