import com.xmilo_at_your_side.sign_to_speak.FliteRequest;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;

/** Synthetic host JNI checks against the actual source-built libprivate_tts.so. */
public final class FliteRequestHostTest {
  private static int checks;
  private static void check(boolean condition, String label) {
    checks++;
    if (!condition) throw new AssertionError(label);
  }
  private static byte[] ascii(String text) { return text.getBytes(StandardCharsets.US_ASCII); }
  private static void wiped(byte[] bytes, String label) {
    for (byte value : bytes) check(value == 0, label);
  }
  private static void invalid(FliteRequest request, byte[] input, String label) {
    boolean rejected = false;
    try { request.synthesize(input); }
    catch (IllegalArgumentException expected) {
      check("Invalid speech text".equals(expected.getMessage()), label + " sanitized");
      rejected = true;
    }
    check(rejected, label + " rejected");
    if (input != null) wiped(input, label + " wiped");
  }
  public static void main(String[] args) {
    FliteRequest request = new FliteRequest();
    try {
      byte[] first = ascii("Hello.");
      short[] pcm = request.synthesize(first);
      check(pcm != null && pcm.length > 0 && pcm.length <= 16000 * 30, "bounded PCM");
      boolean nonzero = false;
      for (short sample : pcm) if (sample != 0) { nonzero = true; break; }
      check(nonzero, "nonzero PCM");
      wiped(first, "successful input wiped");
      Arrays.fill(pcm, (short) 0);
      byte[] second = ascii("Second request.");
      short[] repeated = request.synthesize(second);
      check(repeated != null && repeated.length > 0, "repeat request");
      wiped(second, "repeat input wiped");
      Arrays.fill(repeated, (short) 0);
      invalid(request, null, "null");
      invalid(request, new byte[0], "empty");
      invalid(request, new byte[257], "oversized");
      invalid(request, new byte[]{'A', 0, 'B'}, "NUL");
      invalid(request, new byte[]{(byte) 0xc3, (byte) 0xa9}, "non-ASCII");
      invalid(request, ascii("   "), "whitespace");
    } finally { request.close(); }
    boolean closed = false;
    byte[] afterClose = ascii("Never spoken.");
    try { request.synthesize(afterClose); }
    catch (IllegalStateException expected) {
      check("Local speech unavailable".equals(expected.getMessage()), "closed message sanitized");
      closed = true;
    }
    check(closed, "closed handle denied");
    wiped(afterClose, "closed input wiped");
    request.cancel(); request.close();
    FliteRequest cancelled = new FliteRequest();
    try {
      cancelled.cancel();
      byte[] text = ascii("Cancelled before synthesis.");
      check(cancelled.synthesize(text) == null, "pre-cancel returns no PCM");
      wiped(text, "pre-cancel input wiped");
    } finally { cancelled.close(); }
    System.out.println("FliteRequest JNI: " + checks + " checks passed");
  }
}