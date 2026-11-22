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
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit

/** Activity-scoped, foreground-only microphone authority. It never automatically resumes. */
internal class ReplyOwner(private val activity: MainActivity) {
  companion object {
    private val nextId = AtomicLong()
    private const val BEGIN_DEADLINE_MS = 30_000L
    private const val FINISH_DEADLINE_MS = 120_000L
    private val finishExecutor = ThreadPoolExecutor(
      1, 1, 0L, TimeUnit.MILLISECONDS, ArrayBlockingQueue(4),
      { task -> Thread(task, "reply-owner-finish").apply { isDaemon = true } },
      ThreadPoolExecutor.AbortPolicy())
    private val cleanupExecutor = ThreadPoolExecutor(
      1, 1, 0L, TimeUnit.MILLISECONDS, ArrayBlockingQueue(4),
      { task -> Thread(task, "reply-owner-cleanup").apply { isDaemon = true } },
      ThreadPoolExecutor.AbortPolicy())
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
  private var cancellationRequestedThrough = 0L

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
    val outcomeEndedBegin = AtomicBoolean()
    val earlyStarted = AtomicReference<Boolean>()
    val earlyOutcome = AtomicReference<ReplySession.Outcome>()
    fun resolveBegin(value: String?): Boolean {
      if (beginSettled.compareAndSet(false, true)) {
        synchronized(this) { cancelBeginDeadline(localRef.get()) }
        done(value)
        return true
      }
      return false
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
            // A parked recorder start must be cancelled without ever blocking the
            // main looper. The generation-keyed helper deduplicates a timeout's
            // earlier request from this late start notification.
            if (recording) requestCancellation(current)
            resolveBegin(null)
          }
        }
      }
    }
    val local = session!!.begin(true, { recording -> handleStarted(recording) }, { result ->
      // Completion is terminal for begin even if the platform's started callback
      // never arrived. Settle before the main-looper deadline can report a timeout.
      val beginEndedHere = resolveBegin(null)
      if (beginEndedHere) outcomeEndedBegin.set(true)
      var current = localRef.get()
      if (current == 0L) {
        earlyOutcome.set(result)
        current = localRef.get()
        if (current != 0L && earlyOutcome.compareAndSet(result, null)) {
          if (outcomeEndedBegin.get()) synchronized(this) {
            if (token == "$identity:$current") token = null
          }
          complete(current, result)
        }
      } else {
        if (outcomeEndedBegin.get()) synchronized(this) {
          if (token == "$identity:$current") token = null
        }
        complete(current, result)
      }
    })
    if (local == 0L) { releaseLease(); resolveBegin(null); return }
    internalToken = local
    token = "$identity:$local"
    localRef.set(local)
    if (!beginSettled.get()) {
      lateinit var deadline: Runnable
      deadline = Runnable {
        val timedOut = synchronized(this) {
          if (beginDeadline !== deadline || beginDeadlineGeneration != local) {
            false
          } else if (internalToken != local || !beginSettled.compareAndSet(false, true)) {
            beginDeadline = null
            beginDeadlineGeneration = 0L
            false
          } else {
            beginDeadline = null
            beginDeadlineGeneration = 0L
            if (token == "$identity:$local") token = null
            true
          }
        }
        if (timedOut) {
          try {
            app.recordLocalAudioCleanupFault()
            done(null)
          } finally {
            requestCancellation(local)
          }
        }
      }
      beginDeadline = deadline
      beginDeadlineGeneration = local
      deadlineHandler.postDelayed(deadline, BEGIN_DEADLINE_MS)
    }
    earlyStarted.getAndSet(null)?.let { handleStarted(it) }
    earlyOutcome.getAndSet(null)?.let {
      if (outcomeEndedBegin.get()) synchronized(this) {
        if (token == "$identity:$local") token = null
      }
      complete(local, it)
    }
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
    if (ready != null) { deliver(ready); return }
    val local = internalToken
    val target = session
    if (local == 0L || target == null) {
      finishDone = null; finishSettled = null
      done(ReplySession.Outcome.status(ReplySession.Status.FAILED)); return
    }
    lateinit var deadline: Runnable
    deadline = Runnable {
      val callback = synchronized(this) {
        if (internalToken != local || finishDeadline !== deadline ||
            finishDeadlineGeneration != local || finishDone !== done ||
            finishSettled !== settlement || !settlement.compareAndSet(false, true)) {
          null
        } else {
          // Detach the settled callback without claiming worker cleanup. The
          // generation, session and lease remain owned until complete() arrives.
          finishDeadline = null
          finishDeadlineGeneration = 0L
          finishDone = null
          finishSettled = null
          pending = null
          token = null
          done
        }
      }
      if (callback != null) {
        try {
          app.recordLocalAudioCleanupFault()
          callback(ReplySession.Outcome.status(ReplySession.Status.CLEANUP_FAILED))
        } finally {
          requestCancellation(local)
        }
      }
    }
    finishDeadline = deadline
    finishDeadlineGeneration = local
    // Arm the watchdog before AudioRecord.stop() can park the platform worker.
    deadlineHandler.postDelayed(deadline, FINISH_DEADLINE_MS)
    requestFinish(local, target, done, settlement)
  }

  @Synchronized private fun complete(generation: Long, result: ReplySession.Outcome) {
    cancelBeginDeadline(generation)
    cancelFinishDeadline(generation)
    if (result.status == ReplySession.Status.CLEANUP_FAILED || session?.isFaulted == true)
      app.recordReplyCleanupFault()
    releaseLease()
    if (closed) session = null
    if (generation != internalToken) return
    if (finishDone == null && token != "$identity:$generation") {
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
    val target = session
    // An accepted finish remains attached to its generation so worker completion
    // settles it only after recorder/request cleanup. With no finish waiter, stale
    // completion needs only release the lease and may be ignored.
    if (finishDone == null) internalToken = 0L
    // Logical authority is revoked immediately. This never touches AudioRecord and
    // therefore cannot park Back/pause behind a platform start/stop call.
    target?.invalidate(local)
    requestCancellation(local)
    // The worker completion owns lease release after recorder/request cleanup.
  }

  @Synchronized fun pause() { resumed = false; cancelAll() }

  @Synchronized fun close() {
    if (closed) return
    closed = true
    token = null; pending = null
    val local = internalToken
    if (finishDone == null) internalToken = 0L
    val target = session ?: return
    target.invalidate(local)
    cancellationRequestedThrough = maxOf(cancellationRequestedThrough, local)
    requestClose(target, local)
  }

  private fun releaseLease() { lease?.close(); lease = null }

  /** Timeout cancellation may touch platform audio and must never run on the main looper. */
  private fun requestCancellation(generation: Long) {
    if (generation == 0L) return
    val target = synchronized(this) {
      if (generation <= cancellationRequestedThrough) return
      cancellationRequestedThrough = generation
      session
    } ?: return
    target.invalidate(generation)
    try {
      cleanupExecutor.execute {
        try { target.cancel(generation) }
        catch (_: RuntimeException) { app.recordReplyCleanupFault() }
        if (target.isFaulted) app.recordReplyCleanupFault()
      }
    } catch (_: RuntimeException) {
      // The queue is deliberately bounded. Failure to submit leaves cleanup uncertain.
      app.recordReplyCleanupFault()
      failFinish(generation, ReplySession.Status.CLEANUP_FAILED)
    }
  }

  /** AudioRecord.stop() is a platform call and may not run on the bridge/main caller. */
  private fun requestFinish(
      generation: Long,
      target: ReplySession,
      done: (ReplySession.Outcome) -> Unit,
      settlement: AtomicBoolean) {
    try {
      finishExecutor.execute {
        val accepted = try { target.finish(generation) }
        catch (_: RuntimeException) {
          app.recordReplyCleanupFault()
          failFinish(generation, ReplySession.Status.CLEANUP_FAILED, done, settlement)
          return@execute
        }
        if (target.isFaulted) app.recordReplyCleanupFault()
        if (!accepted) failFinish(generation, ReplySession.Status.FAILED, done, settlement)
      }
    } catch (_: RuntimeException) {
      // Dispatch failure makes recorder cleanup unknowable and permanently faults audio.
      app.recordReplyCleanupFault()
      failFinish(generation, ReplySession.Status.CLEANUP_FAILED, done, settlement)
      requestCancellation(generation)
    }
  }

  /** ReplySession.close() can serialize behind recorder start and must stay off main. */
  private fun requestClose(target: ReplySession, generation: Long) {
    try {
      cleanupExecutor.execute {
        try { target.close() }
        catch (_: RuntimeException) { app.recordReplyCleanupFault() }
        if (target.isFaulted) app.recordReplyCleanupFault()
        synchronized(this) {
          // An active generation retains the session until its cleanup completion.
          if (session === target && (generation == 0L || lease == null)) session = null
        }
      }
    } catch (_: RuntimeException) {
      app.recordReplyCleanupFault()
      failFinish(generation, ReplySession.Status.CLEANUP_FAILED)
    }
  }

  private fun failFinish(
      generation: Long,
      status: ReplySession.Status,
      expectedDone: ((ReplySession.Outcome) -> Unit)? = null,
      expectedSettlement: AtomicBoolean? = null) {
    val callback = synchronized(this) {
      val currentDone = finishDone
      val currentSettlement = finishSettled
      if (generation == 0L || internalToken != generation || currentDone == null ||
          (expectedDone != null && currentDone !== expectedDone) ||
          (expectedSettlement != null && currentSettlement !== expectedSettlement) ||
          currentSettlement?.compareAndSet(false, true) == false) {
        null
      } else {
        cancelFinishDeadline(generation)
        finishDone = null; finishSettled = null; pending = null; token = null
        currentDone
      }
    }
    callback?.invoke(ReplySession.Outcome.status(status))
  }
}
