package com.xmilo_at_your_side.sign_to_speak

import android.graphics.Bitmap
import com.google.mediapipe.framework.image.BitmapImageBuilder
import com.google.mediapipe.framework.image.MPImage

internal class HandTrackingCleanupException : RuntimeException()

/** The SDK owns/recycles the SAME Bitmap, not a copied container. */
internal object HolisticBitmapInput {
    fun <T> consume(bitmap: Bitmap, detect: (MPImage) -> T): T {
        val image = BitmapImageBuilder(bitmap).build()
        try {
            return detect(image)
        } finally {
            var failed = false
            try { bitmap.eraseColor(0) }
            catch (_: RuntimeException) { failed = true }
            catch (_: LinkageError) { failed = true }
            // Synchronous detectForVideo has returned; clear while still mutable,
            // then release the SDK wrapper exactly once (which recycles bitmap).
            try { image.close() }
            catch (_: RuntimeException) { failed = true }
            catch (_: LinkageError) { failed = true }
            if (failed) throw HandTrackingCleanupException()
        }
    }
}
