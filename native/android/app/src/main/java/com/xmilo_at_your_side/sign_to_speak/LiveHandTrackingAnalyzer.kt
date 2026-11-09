package com.xmilo_at_your_side.sign_to_speak

import android.graphics.Bitmap
import android.graphics.PixelFormat
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import java.util.concurrent.atomic.AtomicBoolean

/** Counts only. Neither coordinates, IDs, timestamps nor recognized text cross the bridge. */
internal data class HandTrackingSummary(
    val status: String,
    val measuredHands: Int = 0,
    val confirmedHands: Int = 0,
    val continuousHands: Int = 0,
)

internal class DetectedHands(val left: FloatArray?, val right: FloatArray?) : AutoCloseable {
    override fun close() { left?.fill(0f); right?.fill(0f) }
}

/** Mock seam covers ownership/errors without loading JNI or claiming detector quality. */
internal interface LiveHandDetector : AutoCloseable {
    fun detect(bitmap: Bitmap, timestampMs: Long): DetectedHands
}

internal class LiveHandTrackingAnalyzer(
    private val worker: LiveHandTrackingWorker,
    private val factory: () -> LiveHandDetector,
    private val publish: (HandTrackingSummary) -> Unit,
    private val cleanupFailure: () -> Unit,
) : ImageAnalysis.Analyzer {
    private val active = AtomicBoolean(true)
    private val faultReported = AtomicBoolean(false)
    private val tracker = RoboflowHandTracker()
    private val timeline = HandTrackingTimeline()
    private var detector: LiveHandDetector? = null // Worker only, including destruction.

    fun isActive() = active.get()

    /** Immediate cancellation, with native destruction deferred behind in-flight inference. */
    fun cancel() {
        active.set(false)
        tracker.close()
        worker.requestCleanup()
    }

    override fun analyze(image: ImageProxy) {
        var imageClosed = false
        var pixels: CameraRgbaPixels.Pixels? = null
        var bitmap: Bitmap? = null
        var hands: DetectedHands? = null
        try {
            if (!active.get() || !worker.mayAnalyze()) return
            if (!worker.claim(this)) { unavailable(); return }
            require(image.format == PixelFormat.RGBA_8888 && image.planes.size == 1)
            val crop = image.cropRect
            val geometry = CameraRgbaPixels.Geometry(image.width, image.height,
                crop.left, crop.top, crop.right, crop.bottom, image.imageInfo.rotationDegrees)
            val stamp = timeline.next(image.imageInfo.timestamp, geometry) ?: return
            val plane = image.planes[0]
            try {
                pixels = CameraRgbaPixels.copy(plane.buffer, plane.rowStride, plane.pixelStride, geometry)
            } finally {
                imageClosed = true
                release { image.close() }
            }
            if (!active.get()) return
            if (stamp.reset) {
                if (!releaseDetector()) { unavailable(); return }
                tracker.reset()
            }
            if (!active.get()) return
            if (detector == null) detector = factory()
            if (!active.get()) return
            val copied = requireNotNull(pixels)
            bitmap = Bitmap.createBitmap(copied.width, copied.height, Bitmap.Config.ARGB_8888)
            bitmap.setPixels(copied.argb, 0, copied.width, 0, 0, copied.width, copied.height)
            copied.close()
            pixels = null
            hands = requireNotNull(detector).detect(bitmap, stamp.milliseconds)
            if (!active.get()) return
            val result = tracker.update(hands.left, hands.right, stamp.milliseconds.toDouble(),
                copied.width, copied.height, tracker.generation)
            if (active.get()) publish(HandTrackingSummary("tracking_active", result.size,
                result.count { it.trackerId != null }, result.count { it.continuous }))
        } catch (_: HandTrackingCleanupException) {
            fault()
        } catch (_: Exception) {
            unavailable()
        } catch (_: LinkageError) {
            // Unsupported/missing JNI or graph registration is a truthful unavailable state.
            unavailable()
        } finally {
            hands?.close()
            pixels?.close()
            bitmap?.let { owned ->
                if (!owned.isRecycled) {
                    release { owned.eraseColor(0) }
                    release { owned.recycle() }
                }
            }
            if (!imageClosed) release { image.close() }
        }
    }

    private fun unavailable() {
        if (active.getAndSet(false)) {
            tracker.close()
            publish(HandTrackingSummary("tracking_unavailable"))
        }
    }

    private fun release(action: () -> Unit): Boolean = try {
        action(); true
    } catch (_: Exception) {
        fault(); false
    } catch (_: LinkageError) {
        fault(); false
    }

    private fun fault() {
        active.set(false)
        tracker.close()
        worker.recordCleanupFailure()
        if (faultReported.compareAndSet(false, true)) cleanupFailure()
    }

    private fun releaseDetector(): Boolean {
        val owned = detector
        detector = null
        return owned == null || release { owned.close() }
    }

    internal fun releaseOnWorker(): Boolean {
        check(worker.isWorkerThread())
        tracker.close()
        val released = releaseDetector()
        return released && !faultReported.get()
    }
}
