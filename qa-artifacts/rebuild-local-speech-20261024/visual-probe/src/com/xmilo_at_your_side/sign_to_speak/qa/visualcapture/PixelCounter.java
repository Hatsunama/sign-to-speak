package com.xmilo_at_your_side.sign_to_speak.qa.visualcapture;

import java.nio.ByteBuffer;

/** Pure RGBA_8888 crop counter. Reads neither padding nor pixels outside crop. */
public final class PixelCounter {
  private PixelCounter() {}
  public static final class Counts {
    public long pixels, opaqueBlack, magenta;
  }

  public static Counts count(ByteBuffer bytes, int width, int height, int rowStride,
      int pixelStride, int left, int top, int right, int bottom) {
    if (bytes == null || width <= 0 || height <= 0 || pixelStride < 4 || rowStride <= 0
        || left < 0 || top < 0 || right > width || bottom > height
        || right <= left || bottom <= top) throw new IllegalArgumentException("bounds");
    long rowBytes = (long)(width - 1) * pixelStride + 4;
    long last = (long)(height - 1) * rowStride + rowBytes;
    if (rowBytes > rowStride || last > bytes.limit()) throw new IllegalArgumentException("stride-or-buffer");
    Counts result = new Counts();
    for (int y = top; y < bottom; y++) {
      for (int x = left; x < right; x++) {
        int i = y * rowStride + x * pixelStride;
        int r = bytes.get(i) & 255, g = bytes.get(i + 1) & 255;
        int b = bytes.get(i + 2) & 255, a = bytes.get(i + 3) & 255;
        result.pixels++;
        if (a == 255 && r == 0 && g == 0 && b == 0) result.opaqueBlack++;
        if (a == 255 && r == 255 && g == 0 && b == 255) result.magenta++;
      }
    }
    return result;
  }
}
