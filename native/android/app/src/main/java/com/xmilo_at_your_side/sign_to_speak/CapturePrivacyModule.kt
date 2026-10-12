package com.xmilo_at_your_side.sign_to_speak

import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule

/** Startup capability only; no content, permission request, player, or policy-changing method. */
class CapturePrivacyModule(context: ReactApplicationContext) : ReactContextBaseJavaModule(context) {
    override fun getName() = "CapturePrivacy"
    override fun getConstants(): Map<String, Any> = mapOf(
        "ready" to ((reactApplicationContext.applicationContext as? MainApplication)
            ?.capturePrivacyReady == true),
    )
}
