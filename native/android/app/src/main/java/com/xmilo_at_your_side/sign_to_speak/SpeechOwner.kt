package com.xmilo_at_your_side.sign_to_speak

import android.os.Build
import android.content.Context
import android.media.AudioAttributes
import android.media.AudioManager
import android.os.Handler
import android.os.Looper
import androidx.lifecycle.Lifecycle
import java.io.IOException
import java.util.function.BooleanSupplier
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicBoolean

/** Activity-scoped speech authority. Native lifecycle revokes it without waiting for JS. */
internal class SpeechOwner(private val activity: MainActivity) {
  companion object {
    private val nextId = AtomicLong()
    private const val SPEAK_DEADLINE_MS = 180_000L
  }
  private val identity = nextId.incrementAndGet().toString()
  private val app get() = activity.application as MainApplication
  private val deadlineHandler = Handler(Looper.getMainLooper())
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
      val appContext = activity.applicationContext
      session = SpeechSession(object : SpeechSession.Factory {
        override fun newRequest(cancelled: BooleanSupplier): SpeechSession.Request {
          try {
            val parent = appContext.noBackupFilesDir.canonicalFile
            val root = PocketModelStore().prepare(parent,
                { name -> appContext.assets.open("pocket-alba/$name") }, cancelled)
            if (cancelled.asBoolean) throw IOException("Speech request cancelled")
            return PocketTtsRequest(root.absolutePath)
          } catch (_: IOException) {
            throw IllegalStateException("Local speech unavailable")
          }
        }
        override fun newPlayer(): SpeechSession.Player = ProtectedPcmPlayer(
            appContext, { eligible() }, { cancelAll() })
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
    val bytes = SpeechSession.encodeText(text)
    if (bytes == null) { done("INVALID_INPUT"); return }
    val target = session ?: run { done("UNAVAILABLE"); return }
    val lease = app.localAudioLease.tryAcquire(Any())
        ?: run { bytes.fill(0); done("BUSY"); return }
    val settled = AtomicBoolean()
    val deadline = Runnable {
      if (settled.compareAndSet(false, true)) {
        app.recordLocalAudioCleanupFault()
        done(SpeechSession.Result.CLEANUP_FAILED.name)
      }
    }
    deadlineHandler.postDelayed(deadline, SPEAK_DEADLINE_MS)
    try {
      target.speak(internalToken, bytes) { result ->
        try {
          deadlineHandler.removeCallbacks(deadline)
          if (result == SpeechSession.Result.CLEANUP_FAILED || target.isFaulted)
            app.recordSpeechCleanupFault()
          if (settled.compareAndSet(false, true)) done(result.name)
        } finally {
          // SpeechSession invokes completion only after request/player cleanup.
          lease.close()
        }
      }
    } catch (failure: Throwable) {
      deadlineHandler.removeCallbacks(deadline)
      val deadlineAlreadySettled = settled.getAndSet(true)
      lease.close()
      // A RuntimeException arriving after the deadline must not make the bridge
      // settle a second time through LocalSpeechModule's submission catch.
      // Fatal VM errors still propagate.
      if (!deadlineAlreadySettled || failure !is RuntimeException) throw failure
    } finally {
      // SpeechSession clones valid input before dispatch; the owner's temporary
      // buffer has no reason to survive an asynchronous completion.
      bytes.fill(0)
    }
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
