package com.xmilo_at_your_side.sign_to_speak.qa.tone;

import android.app.Activity;
import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioTrack;
import android.os.Bundle;
import android.os.SystemClock;
import android.util.Log;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import java.util.Arrays;

public final class ToneActivity extends Activity {
  private static final String TAG = "ToneQA";
  private static final int HZ = 16000, MAX_MS = 5000;
  private volatile boolean playing;
  private AudioTrack track;
  private Thread worker;
  private TextView status;

  @Override public void onCreate(Bundle state) {
    super.onCreate(state);
    AudioManager manager = getSystemService(AudioManager.class);
    manager.setAllowedCapturePolicy(AudioAttributes.ALLOW_CAPTURE_BY_ALL);
    LinearLayout layout = new LinearLayout(this);
    layout.setOrientation(LinearLayout.VERTICAL);
    int pad = (int)(20 * getResources().getDisplayMetrics().density);
    layout.setPadding(pad, pad, pad, pad);
    status = new TextView(this);
    status.setText("Synthetic 440 Hz tone, at most 5 seconds. This separate QA app explicitly permits playback capture. Set a comfortable volume before playing.");
    layout.addView(status);
    Button play = new Button(this);
    play.setText("Play capturable 5 second tone");
    play.setOnClickListener((View v) -> playTone());
    layout.addView(play);
    Button stop = new Button(this);
    stop.setText("Stop");
    stop.setOnClickListener((View v) -> stopTone("button"));
    layout.addView(stop);
    setContentView(layout);
  }

  private void playTone() {
    stopTone("replaced");
    try {
      AudioAttributes attrs = new AudioAttributes.Builder()
          .setUsage(AudioAttributes.USAGE_MEDIA)
          .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
          .setAllowedCapturePolicy(AudioAttributes.ALLOW_CAPTURE_BY_ALL).build();
      AudioFormat format = new AudioFormat.Builder().setSampleRate(HZ)
          .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
          .setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build();
      int min = AudioTrack.getMinBufferSize(HZ, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT);
      if (min <= 0) throw new IllegalStateException("buffer-size");
      track = new AudioTrack.Builder().setAudioAttributes(attrs).setAudioFormat(format)
          .setBufferSizeInBytes(Math.max(min, 8192)).setTransferMode(AudioTrack.MODE_STREAM).build();
      if (track.getState() != AudioTrack.STATE_INITIALIZED) throw new IllegalStateException("track-state");
      track.play();
      playing = true;
      AudioTrack current = track;
      worker = new Thread(() -> writeTone(current), "qa-tone-writer");
      worker.start();
      status.setText("Playing synthetic tone for up to 5 seconds.");
    } catch (Exception e) {
      Log.i(TAG, "tone start failed reason=" + e.getClass().getSimpleName());
      stopTone("start-error");
    }
  }

  private void writeTone(AudioTrack current) {
    short[] buffer = new short[512];
    long start = SystemClock.elapsedRealtime();
    int written = 0;
    String reason = "duration-limit";
    try {
      while (playing && written < HZ * 5 && SystemClock.elapsedRealtime() - start < MAX_MS) {
        int wanted = Math.min(buffer.length, HZ * 5 - written);
        for (int i = 0; i < wanted; i++)
          buffer[i] = (short)(Math.sin(2.0 * Math.PI * 440.0 * (written + i) / HZ) * 6000);
        int n = current.write(buffer, 0, wanted, AudioTrack.WRITE_BLOCKING);
        Arrays.fill(buffer, (short)0);
        if (n <= 0) { reason = "write-error"; break; }
        written += n;
      }
      if (!playing) reason = "interrupted";
    } catch (Exception e) { reason = "exception-" + e.getClass().getSimpleName(); }
    finally {
      Arrays.fill(buffer, (short)0);
      int head = 0;
      try { head = current.getPlaybackHeadPosition(); }
      catch (Exception e) { reason = "head-error-" + e.getClass().getSimpleName(); }
      Log.i(TAG, "tone end elapsedMs=" + SystemClock.elapsedRealtime() + " durationMs="
          + (SystemClock.elapsedRealtime() - start) + " writtenFrames=" + written
          + " headFrames=" + head + " reason=" + reason);
      runOnUiThread(() -> { if (track == current) stopTone("complete"); });
    }
  }

  private void stopTone(String reason) {
    playing = false;
    AudioTrack old = track;
    track = null;
    if (old != null) {
      try { old.pause(); old.flush(); old.stop(); } catch (Exception ignored) { }
      old.release();
      Log.i(TAG, "tone stop reason=" + reason);
    }
    if (status != null) status.setText("Tone stopped. Tap Play for another 5 second control.");
  }

  @Override protected void onPause() { stopTone("activity-paused"); super.onPause(); }
  @Override protected void onDestroy() { stopTone("activity-destroyed"); super.onDestroy(); }
}
