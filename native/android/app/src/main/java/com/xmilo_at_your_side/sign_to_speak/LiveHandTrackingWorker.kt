package com.xmilo_at_your_side.sign_to_speak

import java.lang.ref.WeakReference
import java.util.concurrent.CountDownLatch
import java.util.concurrent.atomic.AtomicReference
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.Executor
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/** One process-owned worker, including across React view remounts and Activity rotation. */
internal class LiveHandTrackingWorker : Executor {
    @Volatile private var thread: Thread? = null
    private val droppedCallback = ThreadLocal.withInitial { false }
    private val cleanupQueued = AtomicBoolean(false)
    private val failed = AtomicBoolean(false)
    // Views own these callbacks; the process worker never strongly retains a view
    // or Activity. Register/remove on native attach/detach, not React scheduling.
    private val faultListeners = mutableListOf<WeakReference<Runnable>>()
    private var owner: LiveHandTrackingAnalyzer? = null // Worker thread only.
    private val executor = ThreadPoolExecutor(1, 1, 0L, TimeUnit.MILLISECONDS,
        ArrayBlockingQueue<Runnable>(2), { task ->
            Thread(task, "sign-hand-tracking").apply { isDaemon = true; thread = this }
        })

    fun isWorkerThread() = Thread.currentThread() === thread
    fun mayAnalyze() = isWorkerThread() && !droppedCallback.get()
    fun hasCleanupFailure() = failed.get()

    fun registerCleanupFaultListener(listener: Runnable) {
        val alreadyFailed = synchronized(faultListeners) {
            faultListeners.removeAll { it.get() == null || it.get() === listener }
            faultListeners.add(WeakReference(listener))
            failed.get()
        }
        if (alreadyFailed) notifyFault(listener)
    }

    fun unregisterCleanupFaultListener(listener: Runnable) {
        synchronized(faultListeners) {
            faultListeners.removeAll { it.get() == null || it.get() === listener }
        }
    }

    fun recordCleanupFailure() {
        if (!failed.compareAndSet(false, true)) return
        val listeners = synchronized(faultListeners) {
            faultListeners.removeAll { it.get() == null }
            faultListeners.mapNotNull { it.get() }
        }
        // Each callback only latches and posts native stop. No frame/JS event is
        // needed, including for a newly bound view in a different Activity.
        listeners.forEach(::notifyFault)
    }

    private fun notifyFault(listener: Runnable) {
        try { listener.run() }
        catch (_: RuntimeException) { /* The process latch remains authoritative. */ }
        catch (_: LinkageError) { /* Do not skip other owners if one callback fails. */ }
    }

    override fun execute(command: Runnable) {
        try {
            executor.execute {
                try { command.run() } finally { retireCancelled() }
            }
        } catch (_: RejectedExecutionException) {
            // CameraX owns the callback until it invokes analyze. Run that callback
            // so its ImageProxy is closed. analyze refuses inference off this worker.
            // Never use CallerRunsPolicy with a native inference task.
            val wasDropped = droppedCallback.get()
            droppedCallback.set(true)
            try { command.run() } finally { droppedCallback.set(wasDropped) }
        }
    }

    fun claim(next: LiveHandTrackingAnalyzer): Boolean {
        check(isWorkerThread())
        if (owner !== next) {
            val previous = owner
            owner = null
            previous?.cancel()
            if (previous != null && !previous.releaseOnWorker()) recordCleanupFailure()
            if (!failed.get()) owner = next
        }
        return !failed.get()
    }

    fun requestCleanup() {
        if (!cleanupQueued.compareAndSet(false, true)) return
        try {
            executor.execute {
                cleanupQueued.set(false)
                retireCancelled()
            }
        } catch (_: RejectedExecutionException) {
            cleanupQueued.set(false)
            // A full queue already has callbacks whose finally blocks retire it.
            // Never close a graph on the UI thread or alongside an in-flight call.
        }
    }

    private fun retireCancelled() {
        check(isWorkerThread())
        val current = owner
        if (current != null && !current.isActive()) {
            owner = null
            if (!current.releaseOnWorker()) recordCleanupFailure()
        }
    }

    /** Bounded test barrier, bypassing CameraX's drop-and-close rejection path. */
    fun executeAndAwaitForTest(command: Runnable) {
        check(!isWorkerThread())
        val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(5)
        val complete = CountDownLatch(1)
        val failure = AtomicReference<Throwable?>()
        val task = Runnable {
            try { command.run() } catch (error: Throwable) { failure.set(error) }
            finally {
                try { retireCancelled() }
                catch (error: Throwable) { failure.compareAndSet(null, error) }
                finally { complete.countDown() }
            }
        }
        while (true) {
            try { executor.execute(task); break }
            catch (rejected: RejectedExecutionException) {
                check(!executor.isShutdown && System.nanoTime() < deadline) { "Worker barrier admission failed" }
                Thread.sleep(1)
            }
        }
        check(complete.await(maxOf(0L, deadline - System.nanoTime()), TimeUnit.NANOSECONDS)) {
            "Worker barrier timed out"
        }
        failure.get()?.let { throw it }
    }

    /** Test-owned workers only. Production has one idle, payload-free process thread. */
    internal fun shutdownForTest() { executor.shutdown() }

    companion object { val process by lazy { LiveHandTrackingWorker() } }
}
