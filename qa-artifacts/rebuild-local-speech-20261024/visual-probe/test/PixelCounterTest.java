import java.nio.ByteBuffer;
import com.xmilo_at_your_side.sign_to_speak.qa.visualcapture.PixelCounter;

public final class PixelCounterTest {
  static void eq(long expected, long actual) { if (expected != actual) throw new AssertionError(expected + " != " + actual); }
  static void bad(Runnable r) { try { r.run(); throw new AssertionError("accepted invalid input"); } catch (IllegalArgumentException expected) {} }
  public static void main(String[] args) {
    ByteBuffer b = ByteBuffer.allocate(32); // 2 rows, 12 bytes of pixels + 4 padding per row
    b.put(0, (byte)255); b.put(2, (byte)255); b.put(3, (byte)255); // magenta outside crop
    b.put(4, (byte)0); b.put(5, (byte)0); b.put(6, (byte)0); b.put(7, (byte)255);
    b.put(16 + 4, (byte)0); b.put(16 + 7, (byte)0); // transparent black inside crop
    PixelCounter.Counts c = PixelCounter.count(b, 2, 2, 16, 4, 1, 0, 2, 2);
    eq(2, c.pixels); eq(1, c.opaqueBlack); eq(0, c.magenta);
    b.put(16 + 4, (byte)255); b.put(16 + 6, (byte)255); b.put(16 + 7, (byte)255);
    c = PixelCounter.count(b, 2, 2, 16, 4, 1, 0, 2, 2);
    eq(2, c.pixels); eq(1, c.magenta);
    ByteBuffer spaced = ByteBuffer.allocate(20);
    spaced.put(8, (byte)255); spaced.put(10, (byte)255); spaced.put(11, (byte)255);
    c = PixelCounter.count(spaced, 2, 1, 20, 8, 1, 0, 2, 1);
    eq(1, c.pixels); eq(1, c.magenta);
    bad(() -> PixelCounter.count(b, 2, 2, 16, 4, -1, 0, 1, 1));
    bad(() -> PixelCounter.count(b, 2, 2, 16, 4, 0, 0, 3, 1));
    bad(() -> PixelCounter.count(b, 2, 2, 7, 4, 0, 0, 2, 2));
    bad(() -> PixelCounter.count(ByteBuffer.allocate(19), 2, 2, 16, 4, 0, 0, 2, 2));
    bad(() -> PixelCounter.count(b, 2, 2, 16, 3, 0, 0, 2, 2));
    System.out.println("PixelCounterTest PASS");
  }
}
