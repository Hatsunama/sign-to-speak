package com.xmilo_at_your_side.sign_to_speak

import android.app.Application
import android.content.Context
import android.media.AudioAttributes
import android.media.AudioManager
import android.os.Build
import com.facebook.react.PackageList
import com.facebook.react.ReactApplication
import com.facebook.react.ReactHost
import com.facebook.react.ReactNativeApplicationEntryPoint.loadReactNative
import com.facebook.react.defaults.DefaultReactHost.getDefaultReactHost

class MainApplication : Application(), ReactApplication {
  // False until native startup has completed the policy check. Never persisted or JS-writable.
  @Volatile var capturePrivacyReady: Boolean = false
    private set


  override val reactHost: ReactHost by lazy {
    getDefaultReactHost(
      context = applicationContext,
      useDevSupport = false,
      packageList =
        PackageList(this).packages.apply {
          // Packages that cannot be autolinked yet can be added manually here, for example:
          add(SignFoundationPackage())
        },
    )
  }

  override fun onCreate() {
    super.onCreate()
    capturePrivacyReady = if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
      // Public playback capture and its opt-out policy were added in API 29.
      // Legacy devices remain preview-only; this does not certify future speech.
      true
    } else {
      try {
        val audio = getSystemService(Context.AUDIO_SERVICE) as? AudioManager
        if (audio == null) false else {
          audio.setAllowedCapturePolicy(AudioAttributes.ALLOW_CAPTURE_BY_NONE)
          audio.allowedCapturePolicy == AudioAttributes.ALLOW_CAPTURE_BY_NONE
        }
      } catch (_: RuntimeException) {
        // No exception/payload logging, unsafe fallback, or automatic policy retry.
        false
      }
    }
    // This foundation has no audio sink. RN renders only the unavailable screen on
    // policy failure; native camera eligibility is also denied independently of JS.
    loadReactNative(this)
  }
}
