package com.xmilo_at_your_side.sign_to_speak.qa.capture;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioPlaybackCaptureConfiguration;
import android.media.AudioRecord;
import android.media.projection.MediaProjection;
import android.media.projection.MediaProjectionManager;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;
import java.util.Arrays;

public final class RecorderService extends Service {
  static final String START = "START", STOP = "STOP", PHASE = "PHASE";
  static final String RESULT_CODE = "resultCode", RESULT_DATA = "resultData";
  private static final String TAG = "CaptureQA";
  private static final String PRODUCT = "com.xmilo_at_your_side.sign_to_speak.qa";
  private static final String TONE = "com.xmilo_at_your_side.sign_to_speak.qa.tone";
  private static final int HZ = 16000, PHASE_MS = 20000, SESSION_MS = 60000;
  private final Handler handler = new Handler(Looper.getMainLooper());
  private MediaProjection projection;
  private MediaProjection.Callback callback;
  private long sessionStart;
  private Phase active;
  private boolean finishing;

  @Override public IBinder onBind(Intent intent) { return null; }

  @Override public int onStartCommand(Intent intent, int flags, int startId) {
    if (intent == null) { finishSession("null-intent"); return START_NOT_STICKY; }
    String action = intent.getAction();
    if (START.equals(action)) startSession(intent);
    else if (STOP.equals(action)) finishSession("explicit-stop");
    else if (PHASE.equals(action)) startPhase(intent.getStringExtra("phase"));
    return START_NOT_STICKY;
  }

  private void startSession(Intent intent) {
    if (projection != null) { Log.i(TAG, "session start rejected reason=already-active"); return; }
    finishing = false;
    if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
      Log.i(TAG, "session start failed reason=record-audio-permission"); stopSelf(); return;
    }
    try {
      getSystemService(NotificationManager.class).createNotificationChannel(
          new NotificationChannel("capture_qa", "Capture QA", NotificationManager.IMPORTANCE_LOW));
      Notification notification = new Notification.Builder(this, "capture_qa")
          .setContentTitle("Playback capture QA active")
          .setContentText("UID-limited audio statistics only; ends within 60 seconds")
          .setSmallIcon(android.R.drawable.ic_btn_speak_now).setOngoing(true).build();
      startForeground(11, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION);
      Intent data = (Intent) intent.getParcelableExtra(RESULT_DATA);
      projection = getSystemService(MediaProjectionManager.class).getMediaProjection(
          intent.getIntExtra(RESULT_CODE, 0), data);
      if (projection == null) { finishSession("projection-null"); return; }
      callback = new MediaProjection.Callback() {
        @Override public void onStop() { handler.post(() -> finishSession("projection-revoked")); }
      };
      projection.registerCallback(callback, handler);
      sessionStart = SystemClock.elapsedRealtime();
      handler.postDelayed(() -> finishSession("session-timeout"), SESSION_MS);
      Log.i(TAG, "session start elapsedMs=" + sessionStart + " maxMs=" + SESSION_MS + " audioOnly=true");
    } catch (Exception e) {
      Log.i(TAG, "session start failed reason=" + e.getClass().getSimpleName());
      finishSession("start-error");
    }
  }

  private void startPhase(String name) {
    if (!"CONTROL".equals(name) && !"PROTECTED".equals(name)) return;
    if (projection == null || finishing) { Log.i(TAG, "phase start failed name=" + name + " reason=no-projection"); return; }
    if (SystemClock.elapsedRealtime() - sessionStart >= SESSION_MS) { finishSession("session-timeout"); return; }
    endPhase("replaced");
    try {
      PackageManager pm = getPackageManager();
      int productUid = pm.getApplicationInfo(PRODUCT, 0).uid;
      int toneUid = pm.getApplicationInfo(TONE, 0).uid;
      if (productUid == toneUid) throw new IllegalStateException("shared-uid");
      AudioPlaybackCaptureConfiguration config = new AudioPlaybackCaptureConfiguration.Builder(projection)
          .addMatchingUid(productUid).addMatchingUid(toneUid)
          .addMatchingUsage(AudioAttributes.USAGE_MEDIA).build();
      AudioFormat format = new AudioFormat.Builder().setSampleRate(HZ)
          .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
          .setChannelMask(AudioFormat.CHANNEL_IN_MONO).build();
      int minBytes = AudioRecord.getMinBufferSize(HZ, AudioFormat.CHANNEL_IN_MONO,
          AudioFormat.ENCODING_PCM_16BIT);
      if (minBytes <= 0) throw new IllegalStateException("buffer-size");
      AudioRecord record = new AudioRecord.Builder().setAudioFormat(format)
          .setBufferSizeInBytes(Math.max(minBytes, 8192))
          .setAudioPlaybackCaptureConfig(config).build();
      if (record.getState() != AudioRecord.STATE_INITIALIZED) { record.release(); throw new IllegalStateException("record-state"); }
      Phase phase = new Phase(name, record);
      active = phase;
      record.startRecording();
      phase.started = SystemClock.elapsedRealtime();
      phase.thread = new Thread(() -> readPhase(phase), "qa-capture-reader");
      phase.thread.start();
      Log.i(TAG, "phase start name=" + name + " elapsedMs=" + phase.started + " maxMs=" + PHASE_MS + " productUid=" + productUid + " toneUid=" + toneUid);
      handler.postDelayed(() -> { if (active == phase) endPhase("phase-timeout"); }, PHASE_MS);
    } catch (Exception e) {
      Log.i(TAG, "phase start failed name=" + name + " reason=" + e.getClass().getSimpleName());
      endPhase("start-error");
    }
  }

  private void readPhase(Phase phase) {
    short[] buffer = new short[1024];
    try {
      while (phase.running && SystemClock.elapsedRealtime() - phase.started < PHASE_MS) {
        int count = phase.record.read(buffer, 0, buffer.length, AudioRecord.READ_NON_BLOCKING);
        if (count < 0) { if (phase.running) phase.readErrors++; break; }
        if (count == 0) { Thread.sleep(10); continue; }
        phase.samples += count;
        for (int i = 0; i < count; i++) {
          int value = Math.abs((int)buffer[i]);
          if (value != 0) phase.nonzero++;
          if (value > phase.peak) phase.peak = value;
        }
        Arrays.fill(buffer, (short)0);
      }
    } catch (Exception e) {
      if (phase.running) phase.readErrors++;
    } finally {
      Arrays.fill(buffer, (short)0);
      handler.post(() -> { if (active == phase) endPhase(phase.readErrors > 0 ? "read-error" : "reader-ended"); });
    }
  }

  private void endPhase(String reason) {
    Phase phase = active;
    if (phase == null) return;
    active = null;
    phase.running = false;
    boolean cleanupFailed = false;
    try { phase.record.stop(); }
    catch (Exception e) {
      cleanupFailed = true;
      Log.i(TAG, "phase cleanup failed name=" + phase.name + " step=stop reason=" + e.getClass().getSimpleName());
    }
    try { phase.record.release(); }
    catch (Exception e) {
      cleanupFailed = true;
      Log.i(TAG, "phase cleanup failed name=" + phase.name + " step=release reason=" + e.getClass().getSimpleName());
    }
    if (phase.thread != null && phase.thread != Thread.currentThread()) {
      try { phase.thread.join(500); }
      catch (InterruptedException e) {
        Thread.currentThread().interrupt();
        cleanupFailed = true;
        Log.i(TAG, "phase cleanup failed name=" + phase.name + " step=join reason=InterruptedException");
      }
      if (phase.thread.isAlive()) {
        cleanupFailed = true;
        Log.i(TAG, "phase cleanup failed name=" + phase.name + " step=join reason=reader-still-alive");
      }
    }
    if (cleanupFailed) {
      Log.i(TAG, "phase end failed name=" + phase.name + " reason=cleanup-failed");
      return;
    }
    Log.i(TAG, "phase end name=" + phase.name + " elapsedMs=" + SystemClock.elapsedRealtime()
        + " durationMs=" + (SystemClock.elapsedRealtime() - phase.started)
        + " samples=" + phase.samples + " nonzero=" + phase.nonzero
        + " peak=" + phase.peak + " readErrors=" + phase.readErrors + " reason=" + reason);
  }

  private void finishSession(String reason) {
    if (finishing) return;
    finishing = true;
    endPhase(reason);
    MediaProjection old = projection;
    projection = null;
    if (old != null) {
      if (callback != null) old.unregisterCallback(callback);
      callback = null;
      old.stop();
    }
    Log.i(TAG, "session end reason=" + reason + " durationMs=" + (sessionStart == 0 ? 0 : SystemClock.elapsedRealtime() - sessionStart));
    stopForeground(STOP_FOREGROUND_REMOVE);
    stopSelf();
  }

  @Override public void onDestroy() { finishSession("service-destroyed"); super.onDestroy(); }

  private static final class Phase {
    final String name;
    final AudioRecord record;
    volatile boolean running = true;
    volatile long started, samples, nonzero;
    volatile int peak, readErrors;
    Thread thread;
    Phase(String name, AudioRecord record) { this.name = name; this.record = record; }
  }
}

