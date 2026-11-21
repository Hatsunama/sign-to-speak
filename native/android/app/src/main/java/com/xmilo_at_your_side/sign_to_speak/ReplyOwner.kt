package com.xmilo_at_your_side.sign_to_speak

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import androidx.lifecycle.Lifecycle
import com.facebook.react.bridge.UiThreadUtil
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/** Activity-scoped, foreground-only microphone authority. It never automatically resumes. */
internal class ReplyOwner(private val activity: MainActivity) {
  companion object {
    private val nextId = AtomicLong()
    private const val BEGIN_DEADLINE_MS = 30_000L
    private const val FINISH_DEADLINE_MS = 120_000L
  }
  private val identity = nextId.incrementAndGet().toString()
  private val app get() = activity.application as MainApplication
  private val deadlineHandler = Handler(Looper.getMainLooper())
  @Volatile private var resumed = false
  @Volatile private var closed = false
  private var session: ReplySession? = null
  private var internalToken = 0L
  private var token: String? = null
  private var lease: LocalAudioLease.Lease? = null
  private var pending: ReplySession.Outcome? = null
  private var finishDone: ((ReplySession.Outcome) -> Unit)? = null
  private var finishSettled: AtomicBoolean? = null
  private var beginDeadline: Runnable? = null
  private var beginDeadlineGeneration = 0L
  private var finishDeadline: Runnable? = null
  private var finishDeadlineGeneration = 0L

  @Synchronized fun resume() { if (!closed) resumed = true }

  private fun eligible(): Boolean =
    !closed && resumed && Build.VERSION.SDK_INT >= Build.VERSION_CODES.R &&
      activity.capturePrivacyReady && !app.replyCleanupFaulted &&
      activity.lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED) &&
      activity.checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

  /** Resolves only after the platform confirms that privacy-sensitive recording started. */
  @Synchronized fun begin(done: (String?) -> Unit) {
    if (!eligible() || token != null) { done(null); return }
    val held = app.localAudioLease.tryAcquire(Any()) ?: run { done(null); return }
    lease = held
    if (session == null) session = ReplySession(object : ReplySession.Factory {
      override fun newRecorder(): ReplySession.Recorder = ReplyAudioRecorder()
      override fun newRequest(cancelled: java.util.function.BooleanSupplier): ReplySession.Request {
        val request = try { ReplyAsrRequest(activity.assets, cancelled) }
        catch (failure: ReplyAsrRequest.Failure) {
          throw when (failure.code.name) {
            "CLEANUP_FAILED" -> ReplySession.CleanupFailed()
            "INVALID_INPUT" -> IllegalArgumentException("Invalid reply model input")
            "DECODE_FAILED" -> ReplySession.DecodeFailed()
            else -> ReplySession.ModelUnavailable()
          }
        }
        catch (_: IllegalStateException) { throw ReplySession.ModelUnavailable() }
        catch (_: LinkageError) { throw ReplySession.ModelUnavailable() }
        return object : ReplySession.Request {
          override fun decode(pcm: ShortArray, count: Int): ReplySession.Outcome {
            val result = try { request.decode(pcm, count) }
            catch (failure: ReplyAsrRequest.Failure) {
              throw when (failure.code.name) {
                "CLEANUP_FAILED" -> ReplySession.CleanupFailed()
                "MODEL_UNAVAILABLE" -> ReplySession.ModelUnavailable()
                "INVALID_INPUT" -> IllegalArgumentException("Invalid reply audio")
                else -> ReplySession.DecodeFailed()
              }
            }
            catch (_: IllegalArgumentException) { throw IllegalArgumentException("Invalid reply audio") }
            catch (_: IllegalStateException) { throw ReplySession.DecodeFailed() }
            return when (result.status.name) {
              "TRANSCRIPT" -> ReplySession.Outcome.completed(result.text)
              "NO_SPEECH" -> ReplySession.Outcome.status(ReplySession.Status.NO_SPEECH)
              "CANCELLED" -> ReplySession.Outcome.status(ReplySession.Status.CANCELLED)
              "MODEL_UNAVAILABLE" -> ReplySession.Outcome.status(ReplySession.Status.MODEL_UNAVAILABLE)
              "INVALID_INPUT" -> ReplySession.Outcome.status(ReplySession.Status.INVALID_INPUT)
              else -> ReplySession.Outcome.status(ReplySession.Status.DECODE_FAILED)
            }
          }
          override fun cancel() = request.cancel()
          override fun close() = request.close()
        }
      }
    })
    pending = null
    finishDone = null
    val localRef = AtomicLong()
    val beginSettled = AtomicBoolean()
    val earlyStarted = AtomicReference<Boolean>()
    val earlyOutcome = AtomicReference<ReplySession.Outcome>()
    fun resolveBegin(value: String?) {
      if (beginSettled.compareAndSet(false, true)) {
        cancelBeginDeadline(localRef.get())
        done(value)
      }
    }
    fun handleStarted(recording: Boolean) {
      UiThreadUtil.runOnUiThread {
        synchronized(this) {
          var current = localRef.get()
          if (current == 0L) {
            earlyStarted.set(recording)
            current = localRef.get()
            if (current == 0L || !earlyStarted.compareAndSet(recording, null)) return@synchronized
          }
          if (recording && token == "$identity:$current" && eligible()) resolveBegin(token)
          else {
            if (token == "$identity:$current") { token = null; internalToken = 0L }
            // A deadline is only a fail-closed fence. JavaScript requests
            // cancellation separately, and a parked recorder start must not block
            // the main looper after the fence has fired.
            if (recording && !app.replyCleanupFaulted) session?.cancel(current)
            resolveBegin(null)
          }
        }
      }
    }
    val local = session!!.begin(true, { recording -> handleStarted(recording) }, { result ->
      var current = localRef.get()
      if (current == 0L) {
        earlyOutcome.set(result)
        current = localRef.get()
        if (current != 0L && earlyOutcome.compareAndSet(result, null)) complete(current, result)
      } else complete(current, result)
    })
    if (local == 0L) { releaseLease(); resolveBegin(null); return }
    internalToken = local
    token = "$identity:$local"
    localRef.set(local)
    if (!beginSettled.get()) {
      val deadline = Runnable {
        if (beginSettled.compareAndSet(false, true)) {
          app.recordLocalAudioCleanupFault()
          done(null)
        }
      }
      beginDeadline = deadline
      beginDeadlineGeneration = local
      deadlineHandler.postDelayed(deadline, BEGIN_DEADLINE_MS)
    }
    earlyStarted.getAndSet(null)?.let { handleStarted(it) }
    earlyOutcome.getAndSet(null)?.let { complete(local, it) }
  }

  @Synchronized fun finish(requestToken: String, done: (ReplySession.Outcome) -> Unit) {
    if (requestToken != token || finishDone != null) {
      done(ReplySession.Outcome.status(ReplySession.Status.UNAVAILABLE)); return
    }
    if (app.replyCleanupFaulted) {
      cancelAll()
      done(ReplySession.Outcome.status(ReplySession.Status.CLEANUP_FAILED)); return
    }
    if (!eligible()) {
      // Eligibility can disappear between begin and finish. Revoke the active mic
      // authority instead of merely rejecting the finish request.
      cancelAll()
      done(ReplySession.Outcome.status(ReplySession.Status.UNAVAILABLE)); return
    }
    val settlement = AtomicBoolean()
    finishSettled = settlement
    finishDone = done
    val ready = pending
    if (ready != null) deliver(ready)
    else if (session?.finish(internalToken) != true) {
      finishDone = null; finishSettled = null
      done(ReplySession.Outcome.status(ReplySession.Status.FAILED))
    } else if (finishDone != null && internalToken != 0L) {
      val local = internalToken
      val deadline = Runnable {
        if (settlement.compareAndSet(false, true)) {
          app.recordLocalAudioCleanupFault()
          // Keep the generation/token until cleanup completes. This both retains
          // the lease and lets the JS watchdog's separate cancel still reach the
          // worker after this callback has been settled.
          done(ReplySession.Outcome.status(ReplySession.Status.CLEANUP_FAILED))
        }
      }
      finishDeadline = deadline
      finishDeadlineGeneration = local
      deadlineHandler.postDelayed(deadline, FINISH_DEADLINE_MS)
    }
  }

  @Synchronized private fun complete(generation: Long, result: ReplySession.Outcome) {
    cancelBeginDeadline(generation)
    cancelFinishDeadline(generation)
    if (result.status == ReplySession.Status.CLEANUP_FAILED || session?.isFaulted == true)
      app.recordReplyCleanupFault()
    releaseLease()
    if (generation != internalToken) return
    if (app.replyCleanupFaulted && finishDone == null) {
      pending = null; token = null; internalToken = 0L
      return
    }
    pending = result
    if (finishDone != null) deliver(result)
  }

  @Synchronized private fun deliver(result: ReplySession.Outcome) {
    val callback = finishDone ?: return
    cancelFinishDeadline(internalToken)
    val publish = finishSettled?.compareAndSet(false, true) != false
    finishDone = null; finishSettled = null; pending = null; token = null; internalToken = 0L
    if (publish) callback(result)
  }

  private fun cancelBeginDeadline(generation: Long) {
    if (generation == 0L || beginDeadlineGeneration != generation) return
    beginDeadline?.let { deadlineHandler.removeCallbacks(it) }
    beginDeadline = null
    beginDeadlineGeneration = 0L
  }

  private fun cancelFinishDeadline(generation: Long) {
    if (generation == 0L || finishDeadlineGeneration != generation) return
    finishDeadline?.let { deadlineHandler.removeCallbacks(it) }
    finishDeadline = null
    finishDeadlineGeneration = 0L
  }

  @Synchronized fun cancel(requestToken: String) { if (requestToken == token) cancelAll() }

  @Synchronized fun cancelAll() {
    token = null; pending = null
    val local = internalToken
    // An accepted finish remains attached to its generation so worker completion
    // settles it only after recorder/request cleanup. With no finish waiter, stale
    // completion needs only release the lease and may be ignored.
    if (finishDone == null) internalToken = 0L
    try { if (local != 0L) session?.cancel(local) }
    catch (_: RuntimeException) {
      app.recordReplyCleanupFault()
      if (finishDone != null) deliver(ReplySession.Outcome.status(ReplySession.Status.CLEANUP_FAILED))
      else internalToken = 0L
    }
    if (session?.isFaulted == true) app.recordReplyCleanupFault()
    // The worker completion owns lease release after recorder/request cleanup.
  }

  @Synchronized fun pause() { resumed = false; cancelAll() }

  @Synchronized fun close() {
    if (closed) return
    closed = true; cancelAll()
    try { session?.close() } catch (_: RuntimeException) { app.recordReplyCleanupFault() }
    if (session?.isFaulted == true) app.recordReplyCleanupFault()
    session = null
  }

  private fun releaseLease() { lease?.close(); lease = null }
}
