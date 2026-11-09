package com.xmilo_at_your_side.sign_to_speak

import android.content.Context
import android.graphics.Bitmap
import android.os.Build
import android.os.Process
import com.google.mediapipe.tasks.components.containers.NormalizedLandmark
import com.google.mediapipe.tasks.core.BaseOptions
import com.google.mediapipe.tasks.vision.core.RunningMode
import com.google.mediapipe.tasks.vision.holisticlandmarker.HolisticLandmarker
import java.security.MessageDigest

/** Official bundled offline detector. No classifier, recording, or network fallback. */
internal class LiveHolisticHandDetector private constructor(
    private val detector: HolisticLandmarker,
) : LiveHandDetector {
    override fun detect(bitmap: Bitmap, timestampMs: Long): DetectedHands {
        var left: FloatArray? = null
        var right: FloatArray? = null
        try {
          return HolisticBitmapInput.consume(bitmap) { image ->
            val result = detector.detectForVideo(image, timestampMs)
            left = copyHand(result.leftHandLandmarks())
            right = copyHand(result.rightHandLandmarks())
            // Keep Holistic anatomical slots. SORT association must not reorder them.
            DetectedHands(left, right)
          }
        } catch (failure: Throwable) {
            left?.fill(0f)
            right?.fill(0f)
            throw failure
        }
    }

    override fun close() { detector.close() }

    private fun copyHand(points: List<NormalizedLandmark>): FloatArray? {
        if (points.isEmpty()) return null
        require(points.size == 21)
        return FloatArray(63) { index ->
            val point = points[index / 3]
            when (index % 3) { 0 -> point.x(); 1 -> point.y(); else -> point.z() }
        }
    }

    companion object {
        const val ASSET = "holistic/holistic_landmarker.task"
        const val MODEL_BYTES = 13_683_609L
        const val MODEL_SHA256 = "e2dab61191e2dcd0a15f943d8e3ed1dce13c82dfa597b9dd39f562975a50c3f8"

        // Check before any reference to HolisticLandmarker's native-loading methods.
        fun isSupportedProcess(): Boolean = Process.is64Bit() &&
            Build.SUPPORTED_ABIS.firstOrNull() == "arm64-v8a"

        fun create(context: Context): LiveHandDetector {
            check(isSupportedProcess())
            val digest = MessageDigest.getInstance("SHA-256")
            val scratch = ByteArray(16 * 1024)
            var count = 0L
            try {
                context.assets.open(ASSET).use { source ->
                    while (true) {
                        val read = source.read(scratch)
                        if (read < 0) break
                        count += read
                        check(count <= MODEL_BYTES)
                        digest.update(scratch, 0, read)
                    }
                }
            } finally { scratch.fill(0) }
            check(count == MODEL_BYTES && digest.digest().joinToString("") { "%02x".format(it) } == MODEL_SHA256)
            val base = BaseOptions.builder().setModelAssetPath(ASSET).build()
            val options = HolisticLandmarker.HolisticLandmarkerOptions.builder()
                .setBaseOptions(base).setRunningMode(RunningMode.VIDEO).build()
            return LiveHolisticHandDetector(HolisticLandmarker.createFromOptions(context, options))
        }
    }
}
