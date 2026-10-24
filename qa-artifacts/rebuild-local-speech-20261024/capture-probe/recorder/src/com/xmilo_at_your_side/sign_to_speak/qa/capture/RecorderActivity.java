package com.xmilo_at_your_side.sign_to_speak.qa.capture;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.media.projection.MediaProjectionManager;
import android.os.Bundle;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

public final class RecorderActivity extends Activity {
  private static final int AUDIO_PERMISSION = 1;
  private static final int PROJECTION_PERMISSION = 2;
  private MediaProjectionManager manager;
  private TextView status;

  @Override public void onCreate(Bundle state) {
    super.onCreate(state);
    manager = getSystemService(MediaProjectionManager.class);
    LinearLayout layout = new LinearLayout(this);
    layout.setOrientation(LinearLayout.VERTICAL);
    int pad = (int)(20 * getResources().getDisplayMetrics().density);
    layout.setPadding(pad, pad, pad, pad);
    status = new TextView(this);
    status.setText("Local playback-audio QA only. Consent allows a short, UID-limited audio capture session. This app never opens screen pixels, the microphone, files, or network. Use adb shell phase commands after consent. A foreground notification stays visible until capture stops. Maximum session: 60 seconds.");
    layout.addView(status);
    Button start = new Button(this);
    start.setText("Grant capture consent");
    start.setOnClickListener((View v) -> begin());
    layout.addView(start);
    Button stop = new Button(this);
    stop.setText("Stop capture");
    stop.setOnClickListener((View v) -> stopService(new Intent(this, RecorderService.class).setAction(RecorderService.STOP)));
    layout.addView(stop);
    setContentView(layout);
  }

  private void begin() {
    if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
      requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, AUDIO_PERMISSION);
    } else {
      startActivityForResult(manager.createScreenCaptureIntent(), PROJECTION_PERMISSION);
    }
  }

  @Override public void onRequestPermissionsResult(int request, String[] permissions, int[] grants) {
    super.onRequestPermissionsResult(request, permissions, grants);
    if (request == AUDIO_PERMISSION && grants.length == 1 && grants[0] == PackageManager.PERMISSION_GRANTED) begin();
    else status.setText("RECORD_AUDIO denied; capture did not start.");
  }

  @Override protected void onActivityResult(int request, int result, Intent data) {
    super.onActivityResult(request, result, data);
    if (request != PROJECTION_PERMISSION) return;
    if (result != RESULT_OK || data == null) { status.setText("Projection denied; capture did not start."); return; }
    Intent service = new Intent(this, RecorderService.class).setAction(RecorderService.START);
    service.putExtra(RecorderService.RESULT_CODE, result);
    service.putExtra(RecorderService.RESULT_DATA, data);
    startForegroundService(service);
    status.setText("Consent sent to foreground capture service. Arm CONTROL or PROTECTED by adb shell; session ends in 60 seconds.");
  }
}
