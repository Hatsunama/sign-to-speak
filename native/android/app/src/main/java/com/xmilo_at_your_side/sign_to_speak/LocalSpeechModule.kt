package com.xmilo_at_your_side.sign_to_speak

import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.bridge.UiThreadUtil

/** Token-only bridge. The Activity owns lifecycle and native cancellation. */
class LocalSpeechModule(context: ReactApplicationContext) : ReactContextBaseJavaModule(context) {
  override fun getName() = "LocalSpeech"

  @ReactMethod fun begin(promise: Promise) {
    val expected = reactApplicationContext.currentActivity as? MainActivity
    UiThreadUtil.runOnUiThread {
      val owner = (reactApplicationContext.currentActivity as? MainActivity)
      try { promise.resolve(if (owner === expected) owner?.speechOwner?.begin() else null) }
      catch (_: RuntimeException) { promise.resolve(null) }
    }
  }

  @ReactMethod fun speak(token: String, text: String, promise: Promise) {
    val owner = reactApplicationContext.currentActivity as? MainActivity
    try {
      if (owner == null) promise.resolve("UNAVAILABLE")
      else owner.speechOwner.speak(token, text) { result -> promise.resolve(result) }
    } catch (_: RuntimeException) { promise.resolve("FAILED") }
  }

  @ReactMethod fun cancel(token: String) {
    // Cancellation is synchronous and token-conditional, even while a new begin is queued.
    try { (reactApplicationContext.currentActivity as? MainActivity)?.speechOwner?.cancel(token) }
    catch (_: RuntimeException) { /* Native Activity teardown remains authoritative. */ }
  }
}
