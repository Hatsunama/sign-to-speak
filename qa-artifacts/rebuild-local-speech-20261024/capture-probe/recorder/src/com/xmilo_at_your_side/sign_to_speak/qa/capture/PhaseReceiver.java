package com.xmilo_at_your_side.sign_to_speak.qa.capture;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;

public final class PhaseReceiver extends BroadcastReceiver {
  @Override public void onReceive(Context context, Intent intent) {
    String phase = intent.getStringExtra("phase");
    if (!"CONTROL".equals(phase) && !"PROTECTED".equals(phase) && !"STOP".equals(phase)) {
      Log.i("CaptureQA", "phase rejected: invalid value");
      return;
    }
    context.startService(new Intent(context, RecorderService.class)
        .setAction("STOP".equals(phase) ? RecorderService.STOP : RecorderService.PHASE)
        .putExtra("phase", phase));
  }
}
