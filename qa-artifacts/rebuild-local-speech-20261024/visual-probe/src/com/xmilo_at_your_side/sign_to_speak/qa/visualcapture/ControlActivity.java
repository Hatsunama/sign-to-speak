package com.xmilo_at_your_side.sign_to_speak.qa.visualcapture;

import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.view.View;

/** Visible, opaque control surface; no secure flag. */
public final class ControlActivity extends Activity {
  @Override public void onCreate(Bundle state) { super.onCreate(state); setContentView(new Surface()); }
  private final class Surface extends View {
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Paint paint = new Paint();
    private boolean tick;
    private final Runnable redraw = new Runnable() {
      @Override public void run() { tick = !tick; invalidate(); handler.postDelayed(this, 200); }
    };
    Surface() { super(ControlActivity.this); setBackgroundColor(Color.MAGENTA); }
    @Override protected void onAttachedToWindow() { super.onAttachedToWindow(); handler.post(redraw); }
    @Override protected void onDetachedFromWindow() { handler.removeCallbacks(redraw); super.onDetachedFromWindow(); }
    @Override protected void onDraw(Canvas canvas) {
      canvas.drawColor(Color.MAGENTA);
      paint.setColor(tick ? Color.BLACK : Color.WHITE);
      canvas.drawRect(0, 0, 12, 12, paint);
    }
  }
}
