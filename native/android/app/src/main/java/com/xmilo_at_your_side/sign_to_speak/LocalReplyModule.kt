package com.xmilo_at_your_side.sign_to_speak

import com.facebook.react.bridge.Arguments
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.bridge.UiThreadUtil

/** Token-only local bridge; Activity lifecycle remains authoritative over JavaScript. */
class LocalReplyModule(context: ReactApplicationContext) : ReactContextBaseJavaModule(context) {
  override fun getName() = "LocalReply"

  @ReactMethod fun begin(promise: Promise) {
    val expected = reactApplicationContext.currentActivity as? MainActivity
    UiThreadUtil.runOnUiThread {
      val activity = (reactApplicationContext.currentActivity as? MainActivity)
        ?.takeIf { it === expected }
      if (activity == null) { promise.resolve(null); return@runOnUiThread }
      try {
        activity.replyOwner.begin { token ->
          if ((activity.application as MainApplication).replyCleanupFaulted)
            promise.reject("CLEANUP_FAILED", "Microphone cleanup failed; close and reopen the app.")
          else promise.resolve(token)
        }
      } catch (_: RuntimeException) { promise.resolve(null) }
    }
  }

  @ReactMethod fun finish(token: String, promise: Promise) {
    val activity = reactApplicationContext.currentActivity as? MainActivity
    if (activity == null) { promise.resolve(result("UNAVAILABLE", null)); return }
    try {
      activity.replyOwner.finish(token) { outcome ->
        promise.resolve(result(outcome.status.name, outcome.text))
      }
    } catch (_: RuntimeException) { promise.resolve(result("FAILED", null)) }
  }

  @ReactMethod fun cancel(token: String) {
    try { (reactApplicationContext.currentActivity as? MainActivity)?.replyOwner?.cancel(token) }
    catch (_: RuntimeException) { /* Activity teardown remains authoritative. */ }
  }

  private fun result(status: String, text: String?) = Arguments.createMap().apply {
    putString("status", status)
    if (status == "COMPLETED" && text != null) putString("text", text)
  }
}
