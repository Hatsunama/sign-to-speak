package com.xmilo_at_your_side.sign_to_speak

import android.os.Build
import android.content.Context
import android.media.AudioAttributes
import android.media.AudioManager
import androidx.lifecycle.Lifecycle
import java.util.concurrent.atomic.AtomicLong

/** Activity-scoped speech authority. Native lifecycle revokes it without waiting for JS. */
internal class SpeechOwner(private val activity: MainActivity) {
  companion object { private val nextId = AtomicLong() }
  private val identity = nextId.incrementAndGet().toString()
  private val app get() = activity.application as MainApplication
  @Volatile private var resumed = false
  private var session: SpeechSession? = null
  private var token: String? = null
  private var internalToken = 0L
  @Volatile private var closed = false

  @Synchronized fun resume() { if (!closed) resumed = true }

  private fun eligible(): Boolean {
    if (closed || !resumed || Build.VERSION.SDK_INT < Build.VERSION_CODES.Q ||
        !activity.capturePrivacyReady || app.speechCleanupFaulted ||
        !activity.lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)) return false
    return try {
      val audio = activity.getSystemService(Context.AUDIO_SERVICE) as? AudioManager
      audio?.allowedCapturePolicy == AudioAttributes.ALLOW_CAPTURE_BY_NONE
    } catch (_: RuntimeException) { false }
  }

  /** Called on the UI thread, after the Activity identity and RESUMED check. */
  @Synchronized fun begin(): String? {
    if (!eligible()) return null
    if (session == null) {
      session = SpeechSession(object : SpeechSession.Factory {
        override fun newRequest(): SpeechSession.Request = FliteRequest()
        override fun newPlayer(): SpeechSession.Player = ProtectedPcmPlayer(
            activity.applicationContext, { eligible() }, { cancelAll() })
      })
    }
    val local = session!!.begin(true)
    if (session!!.isFaulted) app.recordSpeechCleanupFault()
    if (local == 0L || app.speechCleanupFaulted) return null
    internalToken = local
    token = "$identity:$local"
    return token
  }

  @Synchronized fun speak(requestToken: String, text: String, done: (String) -> Unit) {
    if (requestToken != token || !eligible()) { done("UNAVAILABLE"); return }
    val bytes = ascii(text)
    if (bytes == null) { done("INVALID_INPUT"); return }
    val target = session ?: run { done("UNAVAILABLE"); return }
    try {
      target.speak(internalToken, bytes) { result ->
        if (result == SpeechSession.Result.CLEANUP_FAILED || target.isFaulted)
          app.recordSpeechCleanupFault()
        done(result.name)
      }
    } finally {
      // SpeechSession clones valid input before dispatch; the owner's temporary
      // buffer has no reason to survive an asynchronous completion.
      bytes.fill(0)
    }
  }

  private fun ascii(text: String): ByteArray? {
    if (text.isEmpty() || text.length > 256 || text.all { it == ' ' } ||
        text.any { it.code < 32 || it.code > 126 }) return null
    return text.toByteArray(Charsets.US_ASCII)
  }

  @Synchronized fun cancel(requestToken: String) {
    if (requestToken == token) cancelAll()
  }

  @Synchronized fun cancelAll() {
    token = null
    internalToken = 0L
    try { session?.stop() } catch (_: RuntimeException) { app.recordSpeechCleanupFault() }
    if (session?.isFaulted == true) app.recordSpeechCleanupFault()
  }

  @Synchronized fun pause() {
    resumed = false
    cancelAll()
  }

  @Synchronized fun close() {
    if (closed) return
    closed = true
    cancelAll()
    try { session?.close() } catch (_: RuntimeException) { app.recordSpeechCleanupFault() }
    if (session?.isFaulted == true) app.recordSpeechCleanupFault()
    session = null
  }
}
