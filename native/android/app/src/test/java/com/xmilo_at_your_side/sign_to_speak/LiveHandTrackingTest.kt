package com.xmilo_at_your_side.sign_to_speak

import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.graphics.Rect
import androidx.camera.core.ImageInfo
import androidx.camera.core.ImageProxy
import com.google.mediapipe.framework.image.BitmapExtractor
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.mockito.Mockito.*
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.nio.ByteBuffer
import java.util.Collections
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/** Synthetic pixels and mock inference only; no hardware, private media or quality claims. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28, 36], application = android.app.Application::class)
class LiveHandTrackingTest {
    @Test fun actualSdkWrapperOwnsBitmapAndClosesAfterWipe() {
        val bitmap = spy(Bitmap.createBitmap(2, 2, Bitmap.Config.ARGB_8888))
        bitmap.eraseColor(0xff123456.toInt())
        assertEquals("done", HolisticBitmapInput.consume(bitmap) { image ->
            assertSame(bitmap, BitmapExtractor.extract(image))
            assertFalse(bitmap.isRecycled)
            "done"
        })
        assertTrue(bitmap.isRecycled)
        val releaseOrder = inOrder(bitmap)
        releaseOrder.verify(bitmap).eraseColor(0)
        releaseOrder.verify(bitmap).recycle()
    }

    @Test fun actualSdkWrapperStillClosesAfterWipeLinkageFailure() {
        val bitmap = spy(Bitmap.createBitmap(2, 2, Bitmap.Config.ARGB_8888))
        doThrow(UnsatisfiedLinkError("Synthetic wipe failure")).`when`(bitmap).eraseColor(0)
        try {
            HolisticBitmapInput.consume(bitmap) { Unit }
            fail("Expected cleanup fault")
        } catch (_: HandTrackingCleanupException) { }
        verify(bitmap).recycle()
        assertTrue(bitmap.isRecycled)
    }

    @Test fun actualSdkWrapperClosesOnInferenceFailure() {
        val bitmap = Bitmap.createBitmap(2, 2, Bitmap.Config.ARGB_8888)
        try {
            HolisticBitmapInput.consume(bitmap) { error("Synthetic detector failure") }
            fail("Expected failure")
        } catch (_: IllegalStateException) { }
        assertTrue(bitmap.isRecycled)
    }

    @Test fun copiesFramesTracksHandsAndErasesAllOwnedBuffers() = withWorker { worker ->
        val summaries = Collections.synchronizedList(mutableListOf<HandTrackingSummary>())
        val arrays = mutableListOf<FloatArray>()
        val bitmaps = mutableListOf<Bitmap>()
        val detector = fakeDetector { bitmap, _ ->
            bitmaps.add(bitmap)
            val left = hand()
            arrays.add(left)
            DetectedHands(left, null)
        }
        val analyzer = LiveHandTrackingAnalyzer(worker, { detector }, summaries::add, { fail("Cleanup failed") })
        val frames = listOf(frame(0), frame(33_000_000), frame(67_000_000))
        frames.forEach { image -> run(worker) { analyzer.analyze(image) } }
        assertEquals(listOf(1, 1, 1), summaries.map { it.measuredHands })
        assertEquals(listOf(0, 1, 1), summaries.map { it.confirmedHands })
        assertEquals(listOf(0, 0, 1), summaries.map { it.continuousHands })
        assertTrue(arrays.all { values -> values.all { it == 0f } })
        assertTrue(bitmaps.all { it.isRecycled })
        frames.forEach { verify(it, times(1)).close() }
        analyzer.cancel()
        run(worker) { }
    }

    @Test fun realSdkBitmapOwnershipDoesNotLatchCleanupFailure() = withWorker { worker ->
        val faults = AtomicInteger()
        val statuses = mutableListOf<String>()
        val detector = fakeDetector { bitmap, _ ->
            HolisticBitmapInput.consume(bitmap) { DetectedHands(hand(), null) }
        }
        val analyzer = LiveHandTrackingAnalyzer(worker, { detector }, { statuses.add(it.status) }, { faults.incrementAndGet() })
        run(worker) { analyzer.analyze(frame(0)) }
        run(worker) { analyzer.analyze(frame(33_000_000)) }
        assertEquals(listOf("tracking_active", "tracking_active"), statuses)
        assertEquals(0, faults.get())
        analyzer.cancel()
        run(worker) { }
        assertFalse(worker.hasCleanupFailure())
    }

    @Test fun slowCadenceKeepsOneGraphAndMonotonicVideoTime() = withWorker { worker ->
        val creates = AtomicInteger()
        val closes = AtomicInteger()
        val times = mutableListOf<Long>()
        val analyzer = LiveHandTrackingAnalyzer(worker, {
            creates.incrementAndGet()
            fakeDetector({ closes.incrementAndGet() }) { _, timestamp ->
                times.add(timestamp); DetectedHands(hand(), null)
            }
        }, {}, { fail("Cleanup failed") })
        listOf(0L, 300_000_000L, 600_000_000L).forEach { ns -> run(worker) { analyzer.analyze(frame(ns)) } }
        assertEquals(listOf(0L, 300L, 600L), times)
        assertEquals(1, creates.get())
        assertEquals(0, closes.get())
        analyzer.cancel()
        run(worker) { }
        assertEquals(1, closes.get())
    }

    @Test fun geometryChangeClosesGraphBeforeRestartingTimestamp() = withWorker { worker ->
        val order = mutableListOf<String>()
        val analyzer = LiveHandTrackingAnalyzer(worker, {
            order.add("create")
            fakeDetector({ order.add("close") }) { _, timestamp ->
                order.add("detect:$timestamp"); DetectedHands(null, null)
            }
        }, {}, { fail("Cleanup failed") })
        run(worker) { analyzer.analyze(frame(0)) }
        run(worker) { analyzer.analyze(frame(33_000_000, rotation = 90)) }
        assertEquals(listOf("create", "detect:0", "close", "create", "detect:0"), order)
        analyzer.cancel()
        run(worker) { }
    }

    @Test fun duplicateTimestampDropsAndClosesWithoutSecondInference() = withWorker { worker ->
        val calls = AtomicInteger()
        val analyzer = LiveHandTrackingAnalyzer(worker, {
            fakeDetector { _, _ -> calls.incrementAndGet(); DetectedHands(null, null) }
        }, {}, {})
        val images = listOf(frame(0), frame(0))
        images.forEach { run(worker) { analyzer.analyze(it) } }
        assertEquals(1, calls.get())
        images.forEach { verify(it).close() }
        analyzer.cancel()
        run(worker) { }
    }

    @Test fun malformedFrameClosesAndReportsUnavailableWithoutCreatingDetector() = withWorker { worker ->
        val statuses = mutableListOf<String>()
        val analyzer = LiveHandTrackingAnalyzer(worker, { error("Must not create") }, { statuses.add(it.status) }, {})
        val image = frame(0)
        `when`(image.format).thenReturn(35)
        run(worker) { analyzer.analyze(image) }
        verify(image).close()
        assertEquals(listOf("tracking_unavailable"), statuses)
    }

    @Test fun modelOrNativeLoadFailureClosesFrameAndFailsOnce() = withWorker { worker ->
        val statuses = mutableListOf<String>()
        val calls = AtomicInteger()
        val analyzer = LiveHandTrackingAnalyzer(worker, {
            calls.incrementAndGet(); throw UnsatisfiedLinkError("Synthetic ABI failure")
        }, { statuses.add(it.status) }, {})
        val images = listOf(frame(0), frame(33_000_000))
        images.forEach { run(worker) { analyzer.analyze(it) } }
        assertEquals(listOf("tracking_unavailable"), statuses)
        assertEquals(1, calls.get())
        images.forEach { verify(it).close() }
    }

    @Test fun cancelDuringInferenceIsImmediateSuppressesResultAndClosesBeforeNextSession() = withWorker { worker ->
        val entered = CountDownLatch(1)
        val release = CountDownLatch(1)
        val order = Collections.synchronizedList(mutableListOf<String>())
        val statuses = Collections.synchronizedList(mutableListOf<HandTrackingSummary>())
        val old = LiveHandTrackingAnalyzer(worker, {
            fakeDetector({ order.add("old-close") }) { _, _ ->
                entered.countDown()
                check(release.await(5, TimeUnit.SECONDS))
                DetectedHands(hand(), null)
            }
        }, statuses::add, {})
        val image = frame(0)
        worker.execute { old.analyze(image) }
        assertTrue(entered.await(5, TimeUnit.SECONDS))
        try {
            old.cancel()
            assertFalse(old.isActive())
            val next = LiveHandTrackingAnalyzer(worker, {
                order.add("new-create"); fakeDetector { _, _ -> DetectedHands(null, null) }
            }, {}, {})
            val nextFinished = CountDownLatch(1)
            worker.execute { try { next.analyze(frame(0)) } finally { nextFinished.countDown() } }
            assertTrue(order.isEmpty())
            release.countDown()
            assertTrue(nextFinished.await(5, TimeUnit.SECONDS))
            run(worker) { }
            assertEquals(listOf("old-close", "new-create"), order)
            assertTrue(statuses.isEmpty())
            verify(image).close()
            next.cancel()
            run(worker) { }
        } finally { release.countDown() }
    }

    @Test fun queueOverflowDropsAndClosesOffWorkerWithoutInference() = withWorker { worker ->
        val entered = CountDownLatch(1)
        val release = CountDownLatch(1)
        worker.execute { entered.countDown(); check(release.await(5, TimeUnit.SECONDS)) }
        assertTrue(entered.await(5, TimeUnit.SECONDS))
        val drained = CountDownLatch(2)
        try {
            worker.execute { drained.countDown() }
            worker.execute { drained.countDown() }
            val image = frame(0)
            val analyzer = LiveHandTrackingAnalyzer(worker, { error("Must not infer off worker") }, {}, {})
            worker.execute { analyzer.analyze(image) }
            verify(image).close()
            assertTrue(analyzer.isActive())
            analyzer.cancel()
        } finally { release.countDown() }
        assertTrue(drained.await(5, TimeUnit.SECONDS))
        run(worker) { }
    }

    @Test fun processFaultListenersNotifyWithoutFramesAndHonorUnregister() = withWorker { worker ->
        val firstCalls = AtomicInteger()
        val removedCalls = AtomicInteger()
        val laterCalls = AtomicInteger()
        val first = Runnable { firstCalls.incrementAndGet() }
        val removed = Runnable { removedCalls.incrementAndGet() }
        worker.registerCleanupFaultListener(first)
        worker.registerCleanupFaultListener(first) // Duplicate attach cannot duplicate ownership.
        worker.registerCleanupFaultListener(removed)
        worker.unregisterCleanupFaultListener(removed)
        run(worker) { worker.recordCleanupFailure() }
        assertEquals(1, firstCalls.get())
        assertEquals(0, removedCalls.get())
        worker.registerCleanupFaultListener(Runnable { laterCalls.incrementAndGet() })
        assertEquals(1, laterCalls.get())
        run(worker) { worker.recordCleanupFailure() }
        assertEquals(1, firstCalls.get())
        worker.unregisterCleanupFaultListener(first)
    }

    @Test fun closeFailureLatchesAndPreventsNewDetector() = withWorker { worker ->
        val faults = AtomicInteger()
        val old = LiveHandTrackingAnalyzer(worker, {
            fakeDetector({ error("Synthetic close failure") }) { _, _ -> DetectedHands(null, null) }
        }, {}, { faults.incrementAndGet() })
        run(worker) { old.analyze(frame(0)) }
        old.cancel()
        run(worker) { }
        assertEquals(1, faults.get())
        assertTrue(worker.hasCleanupFailure())
        val states = mutableListOf<String>()
        val next = LiveHandTrackingAnalyzer(worker, { error("Must not reopen after fault") }, { states.add(it.status) }, {})
        run(worker) { next.analyze(frame(0)) }
        assertEquals(listOf("tracking_unavailable"), states)
    }

    private fun frame(timestampNs: Long, rotation: Int = 0): ImageProxy {
        val image = mock(ImageProxy::class.java)
        val info = mock(ImageInfo::class.java)
        val plane = mock(ImageProxy.PlaneProxy::class.java)
        `when`(image.width).thenReturn(2)
        `when`(image.height).thenReturn(2)
        `when`(image.format).thenReturn(PixelFormat.RGBA_8888)
        `when`(image.cropRect).thenReturn(Rect(0, 0, 2, 2))
        `when`(image.imageInfo).thenReturn(info)
        `when`(info.timestamp).thenReturn(timestampNs)
        `when`(info.rotationDegrees).thenReturn(rotation)
        `when`(image.planes).thenReturn(arrayOf(plane))
        `when`(plane.pixelStride).thenReturn(4)
        `when`(plane.rowStride).thenReturn(8)
        `when`(plane.buffer).thenReturn(ByteBuffer.wrap(ByteArray(16) { 127 }))
        return image
    }

    private fun hand() = FloatArray(63) { index -> when (index % 3) {
        0 -> .1f + (index / 3 % 5) * .2f
        1 -> .1f + (index / 15) * .2f
        else -> 0f
    } }

    private fun fakeDetector(onClose: () -> Unit = {}, detect: (Bitmap, Long) -> DetectedHands) =
        object : LiveHandDetector {
            override fun detect(bitmap: Bitmap, timestampMs: Long) = detect(bitmap, timestampMs)
            override fun close() = onClose()
        }

    private fun run(worker: LiveHandTrackingWorker, action: () -> Unit) {
        worker.executeAndAwaitForTest(Runnable(action))
    }

    private fun withWorker(test: (LiveHandTrackingWorker) -> Unit) {
        val worker = LiveHandTrackingWorker()
        try { test(worker) } finally { worker.shutdownForTest() }
    }
}
