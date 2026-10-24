package com.xmilo_at_your_side.sign_to_speak.qa.visualcapture;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.graphics.PixelFormat;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.media.Image;
import android.media.ImageReader;
import android.media.projection.MediaProjection;
import android.media.projection.MediaProjectionManager;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.util.DisplayMetrics;
import android.util.Log;
import android.view.Display;
import android.view.WindowManager;
import java.nio.ByteBuffer;

/** Bounded, in-memory visual statistics. No pixel buffer is retained or written. */
public final class CaptureService extends Service {
  static final String TAG = "VisualCaptureQA";
  static final String START = "START", STOP = "STOP", PHASE = "PHASE";
  static final String RESULT_CODE = "resultCode", RESULT_DATA = "resultData";
  private static final int PHASE_MS = 3000, SESSION_MS = 60000;
  private final Handler handler = new Handler(Looper.getMainLooper());
  private MediaProjection projection;
  private MediaProjection.Callback callback;
  private ImageReader reader;
  private VirtualDisplay virtualDisplay;
  private int width, height, density;
  private long sessionStartedNs, lastImageTimestampNs;
  private Phase active;
  private boolean finishing, started;

  @Override public IBinder onBind(Intent intent) { return null; }
  @Override public int onStartCommand(Intent intent, int flags, int startId) {
    if (intent == null) { finish("null-intent"); return START_NOT_STICKY; }
    if (START.equals(intent.getAction())) begin(intent);
    else if (STOP.equals(intent.getAction())) finish("explicit-stop");
    else if (PHASE.equals(intent.getAction())) phase(intent);
    return START_NOT_STICKY;
  }

  private void begin(Intent intent) {
    if (started || finishing) { Log.i(TAG, "session start rejected reason=already-started"); return; }
    started = true;
    try {
      getSystemService(NotificationManager.class).createNotificationChannel(
          new NotificationChannel("visual_capture_qa", "Visual Capture QA", NotificationManager.IMPORTANCE_LOW));
      Notification notice = new Notification.Builder(this, "visual_capture_qa")
          .setContentTitle("Visual capture QA active")
          .setContentText("In-memory pixel counts; ends within 60 seconds")
          .setSmallIcon(android.R.drawable.ic_menu_view).setOngoing(true).build();
      startForeground(12, notice, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION);
      Intent data = (Intent)intent.getParcelableExtra(RESULT_DATA);
      if (data == null) throw new IllegalArgumentException("missing-consent-data");
      projection = getSystemService(MediaProjectionManager.class)
          .getMediaProjection(intent.getIntExtra(RESULT_CODE, 0), data);
      if (projection == null) throw new IllegalStateException("projection-null");
      callback = new MediaProjection.Callback() {
        @Override public void onStop() { handler.post(() -> finish("projection-revoked")); }
      };
      projection.registerCallback(callback, handler);
      Display display = getSystemService(WindowManager.class).getDefaultDisplay();
      DisplayMetrics metrics = new DisplayMetrics(); display.getRealMetrics(metrics);
      width = metrics.widthPixels; height = metrics.heightPixels; density = metrics.densityDpi;
      if (width <= 0 || height <= 0 || width >= height) throw new IllegalStateException("portrait-required");
      reader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 2);
      reader.setOnImageAvailableListener(source -> consume(), handler);
      virtualDisplay = projection.createVirtualDisplay("visual-capture-qa", width, height, density,
          DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR, reader.getSurface(), null, handler);
      if (virtualDisplay == null) throw new IllegalStateException("virtual-display-null");
      sessionStartedNs = System.nanoTime();
      handler.postDelayed(() -> finish("session-timeout"), SESSION_MS);
      Log.i(TAG, "session start width=" + width + " height=" + height
          + " density=" + density + " startNs=" + sessionStartedNs + " maxMs=" + SESSION_MS);
    } catch (Exception e) {
      Log.i(TAG, "session start failed reason=" + e.getClass().getSimpleName()); finish("start-error");
    }
  }

  private void phase(Intent intent) {
    String name = intent.getStringExtra("phase");
    if (projection == null || reader == null || finishing) {
      Log.i(TAG, "phase rejected reason=no-session"); return;
    }
    if (!"CONTROL".equals(name) && !"PROTECTED".equals(name) && !"CONTROL_AFTER".equals(name)) {
      Log.i(TAG, "phase rejected reason=invalid-name"); return;
    }
    if (System.nanoTime() - sessionStartedNs >= SESSION_MS * 1000000L) { finish("session-timeout"); return; }
    int left = intent.getIntExtra("left", -1), top = intent.getIntExtra("top", -1);
    int right = intent.getIntExtra("right", -1), bottom = intent.getIntExtra("bottom", -1);
    if (left < 0 || top < 0 || right > width || bottom > height || right <= left || bottom <= top) {
      Log.i(TAG, "phase rejected reason=invalid-crop"); return;
    }
    endPhase("replaced");
    drain();
    Phase p = new Phase(name, left, top, right, bottom, System.nanoTime());
    active = p;
    Log.i(TAG, "phase requested name=" + name + " crop=" + left + "," + top + "," + right + "," + bottom
        + " requestedNs=" + p.requestedNs);
    handler.postDelayed(() -> {
      if (active != p || finishing) return;
      drain(); // discard queued frames before arming; only later timestamps count
      p.armedNs = System.nanoTime();
      p.armed = true;
      Log.i(TAG, "phase armed name=" + p.name + " armedNs=" + p.armedNs);
    }, 250);
    handler.postDelayed(() -> { if (active == p) endPhase("phase-timeout"); }, PHASE_MS);
  }

  private void consume() {
    if (reader == null || finishing) return;
    Image image = null;
    try {
      image = reader.acquireLatestImage();
      if (image == null) return;
      long timestamp = image.getTimestamp();
      if (timestamp > lastImageTimestampNs) lastImageTimestampNs = timestamp;
      Phase p = active;
      if (p == null || !p.armed) return; // no pixel-plane access outside armed phase
      if (timestamp <= 0 || timestamp <= p.armedNs || timestamp <= p.lastTimestampNs) return;
      DisplayMetrics now = new DisplayMetrics();
      getSystemService(WindowManager.class).getDefaultDisplay().getRealMetrics(now);
      if (now.widthPixels != width || now.heightPixels != height
          || image.getWidth() != width || image.getHeight() != height) {
        p.errors++; finish("dimensions-changed"); return;
      }
      Image.Plane[] planes = image.getPlanes();
      if (planes.length != 1) throw new IllegalStateException("planes");
      ByteBuffer bytes = planes[0].getBuffer();
      PixelCounter.Counts counts = PixelCounter.count(bytes, image.getWidth(), image.getHeight(),
          planes[0].getRowStride(), planes[0].getPixelStride(), p.left, p.top, p.right, p.bottom);
      p.frames++; p.pixels += counts.pixels; p.opaqueBlack += counts.opaqueBlack; p.magenta += counts.magenta;
      if (p.firstTimestampNs == 0) p.firstTimestampNs = timestamp;
      p.lastTimestampNs = timestamp;
    } catch (Exception e) {
      if (active != null) active.errors++;
      Log.i(TAG, "frame error reason=" + e.getClass().getSimpleName());
      finish("frame-error");
    } finally { if (image != null) image.close(); }
  }

  private void drain() {
    if (reader == null) return;
    Image image = null;
    try {
      while ((image = reader.acquireLatestImage()) != null) {
        long timestamp = image.getTimestamp();
        if (timestamp > lastImageTimestampNs) lastImageTimestampNs = timestamp;
        image.close(); image = null;
      }
    } catch (Exception e) {
      if (active != null) active.errors++;
      Log.i(TAG, "drain error reason=" + e.getClass().getSimpleName()); finish("drain-error");
    } finally { if (image != null) image.close(); }
  }

  private void endPhase(String reason) {
    Phase p = active;
    if (p == null) return;
    active = null;
    long endNs = System.nanoTime();
    boolean valid = "phase-timeout".equals(reason) && p.armed && p.frames > 0 && p.pixels > 0 && p.errors == 0
        && p.firstTimestampNs > p.armedNs && p.lastTimestampNs >= p.firstTimestampNs;
    Log.i(TAG, "phase end name=" + p.name + " reason=" + reason + " valid=" + valid
        + " frames=" + p.frames + " pixels=" + p.pixels + " opaqueBlack=" + p.opaqueBlack
        + " magenta=" + p.magenta + " errors=" + p.errors
        + " firstImageTimestampNs=" + p.firstTimestampNs + " lastImageTimestampNs=" + p.lastTimestampNs
        + " requestedNs=" + p.requestedNs + " armedNs=" + p.armedNs + " endNs=" + endNs
        + " width=" + width + " height=" + height + " crop=" + p.left + "," + p.top + "," + p.right + "," + p.bottom);
  }

  private void finish(String reason) {
    if (finishing) return;
    finishing = true;
    endPhase(reason);
    boolean cleanupFailed = false;
    if (reader != null) {
      try { reader.setOnImageAvailableListener(null, null); } catch (Exception e) { cleanupFailed = true; }
    }
    if (virtualDisplay != null) {
      try { virtualDisplay.release(); } catch (Exception e) { cleanupFailed = true; }
      virtualDisplay = null;
    }
    if (reader != null) {
      try { reader.close(); } catch (Exception e) { cleanupFailed = true; }
      reader = null;
    }
    MediaProjection old = projection; projection = null;
    if (old != null) {
      try { if (callback != null) old.unregisterCallback(callback); } catch (Exception e) { cleanupFailed = true; }
      try { old.stop(); } catch (Exception e) { cleanupFailed = true; }
    }
    callback = null;
    Log.i(TAG, "session end reason=" + reason + " cleanupFailed=" + cleanupFailed
        + " endNs=" + System.nanoTime());
    stopForeground(STOP_FOREGROUND_REMOVE); stopSelf();
  }
  @Override public void onDestroy() { finish("service-destroyed"); super.onDestroy(); }

  private static final class Phase {
    final String name;
    final int left, top, right, bottom;
    final long requestedNs;
    long armedNs, firstTimestampNs, lastTimestampNs, frames, pixels, opaqueBlack, magenta;
    int errors;
    boolean armed;
    Phase(String name, int left, int top, int right, int bottom, long requestedNs) {
      this.name = name; this.left = left; this.top = top; this.right = right; this.bottom = bottom;
      this.requestedNs = requestedNs;
    }
  }
}
