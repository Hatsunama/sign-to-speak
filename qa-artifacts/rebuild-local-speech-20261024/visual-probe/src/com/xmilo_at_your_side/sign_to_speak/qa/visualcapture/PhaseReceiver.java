package com.xmilo_at_your_side.sign_to_speak.qa.visualcapture;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;

/** Exported only to shell/holders of signature permission DUMP. */
public final class PhaseReceiver extends BroadcastReceiver {
  @Override public void onReceive(Context context, Intent intent) {
    if (intent == null) return;
    String phase = intent.getStringExtra("phase");
    if (!"CONTROL".equals(phase) && !"PROTECTED".equals(phase)
        && !"CONTROL_AFTER".equals(phase) && !"STOP".equals(phase)) {
      Log.i(CaptureService.TAG, "phase rejected reason=invalid-name"); return;
    }
    Intent service = new Intent(context, CaptureService.class)
        .setAction("STOP".equals(phase) ? CaptureService.STOP : CaptureService.PHASE)
        .putExtra("phase", phase);
    if (!"STOP".equals(phase)) {
      for (String key : new String[]{"left", "top", "right", "bottom"}) {
        if (!intent.hasExtra(key) || !(intent.getExtras().get(key) instanceof Integer)) {
          Log.i(CaptureService.TAG, "phase rejected reason=missing-or-noninteger-crop"); return;
        }
        service.putExtra(key, intent.getIntExtra(key, -1));
      }
    }
    context.startService(service);
  }
}
