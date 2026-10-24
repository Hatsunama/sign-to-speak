package com.xmilo_at_your_side.sign_to_speak.qa.visualcapture;

import android.app.Activity;
import android.content.Intent;
import android.media.projection.MediaProjectionManager;
import android.os.Bundle;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

public final class ConsentActivity extends Activity {
  private static final int REQUEST = 1;
  private TextView status;
  @Override public void onCreate(Bundle state) {
    super.onCreate(state);
    LinearLayout layout = new LinearLayout(this);
    layout.setOrientation(LinearLayout.VERTICAL);
    status = new TextView(this);
    status.setText("Temporary local visual QA. Android will ask for screen capture consent. Choose entire screen. No image is saved. Session ends within 60 seconds.");
    layout.addView(status);
    Button start = new Button(this); start.setText("Grant screen capture consent");
    start.setOnClickListener((View v) -> startActivityForResult(
        getSystemService(MediaProjectionManager.class).createScreenCaptureIntent(), REQUEST));
    layout.addView(start);
    Button stop = new Button(this); stop.setText("Stop capture");
    stop.setOnClickListener((View v) -> startService(new Intent(this, CaptureService.class).setAction(CaptureService.STOP)));
    layout.addView(stop);
    setContentView(layout);
  }
  @Override protected void onActivityResult(int request, int result, Intent data) {
    super.onActivityResult(request, result, data);
    if (request != REQUEST) return;
    if (result != RESULT_OK || data == null) { status.setText("Consent denied. No capture started."); return; }
    startForegroundService(new Intent(this, CaptureService.class).setAction(CaptureService.START)
        .putExtra(CaptureService.RESULT_CODE, result).putExtra(CaptureService.RESULT_DATA, data));
    status.setText("Consent sent. Arm phases from adb shell; session ends within 60 seconds.");
  }
}
