package com.xmilo_at_your_side.sign_to_speak

import com.facebook.react.ReactActivity
import com.facebook.react.ReactActivityDelegate
import com.facebook.react.defaults.DefaultNewArchitectureEntryPoint.fabricEnabled
import com.facebook.react.defaults.DefaultReactActivityDelegate

class MainActivity : ReactActivity() {
  // Register views before attachment too: a queued active prop must be canceled by Back.
  private val cameraViews = java.util.Collections.newSetFromMap(
      java.util.WeakHashMap<PrivateCameraView, Boolean>())

  internal var cameraCleanupFailures = 0
    private set

  private fun cancelCameraViews(dispose: Boolean) {
    cameraViews.toList().forEach { view ->
      try {
        if (dispose) view.dispose() else view.cancelFromNativeBack()
      } catch (_: RuntimeException) {
        // Payload-free, in-memory accounting. Release is not confirmed; fail closed.
        cameraCleanupFailures++
        try { view.reportCleanupFailure() } catch (_: RuntimeException) {
          // A stalled/broken JS notification must not skip another native cleanup.
          // The failure remains recorded and new starts remain blocked.
        }
      }
    }
  }

  internal fun registerCameraView(view: PrivateCameraView) { cameraViews.add(view) }
  internal fun unregisterCameraView(view: PrivateCameraView) { cameraViews.remove(view) }

  @Suppress("DEPRECATION")
  override fun onBackPressed() {
    // RN 0.86.3 also routes committed predictive Back here for targetSdk 36.
    // Do this before RN can emit a JS event; JS may never process that event.
    cancelCameraViews(dispose = false)
    super.onBackPressed()
  }

  override fun onDestroy() {
    try { cancelCameraViews(dispose = true) }
    finally {
      cameraViews.clear()
      super.onDestroy()
    }
  }

  override fun onCreate(savedInstanceState: android.os.Bundle?) {
    window.addFlags(android.view.WindowManager.LayoutParams.FLAG_SECURE)
    super.onCreate(savedInstanceState)
  }

  /**
   * Returns the name of the main component registered from JavaScript. This is used to schedule
   * rendering of the component.
   */
  override fun getMainComponentName(): String = "SignToSpeak"

  /**
   * Returns the instance of the [ReactActivityDelegate]. We use [DefaultReactActivityDelegate]
   * which allows you to enable New Architecture with a single boolean flags [fabricEnabled]
   */
  override fun createReactActivityDelegate(): ReactActivityDelegate =
      DefaultReactActivityDelegate(this, mainComponentName, fabricEnabled)
}
