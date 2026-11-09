package com.xmilo_at_your_side.sign_to_speak

import java.nio.ByteBuffer

/** CameraX RGBA plane -> bounded upright ARGB copy. Never mirrors the detector input. */
internal object CameraRgbaPixels {
    data class Geometry(
        val width: Int, val height: Int,
        val left: Int, val top: Int, val right: Int, val bottom: Int,
        val rotationDegrees: Int,
    )
    class Pixels(val width: Int, val height: Int, val argb: IntArray) : AutoCloseable {
        override fun close() { argb.fill(0) }
    }

    fun copy(buffer: ByteBuffer, rowStride: Int, pixelStride: Int, geometry: Geometry): Pixels {
        val g = geometry
        require(g.width in 1..4096 && g.height in 1..4096)
        require(g.left >= 0 && g.top >= 0 && g.right in (g.left + 1)..g.width &&
            g.bottom in (g.top + 1)..g.height)
        require(g.rotationDegrees in listOf(0, 90, 180, 270))
        require(pixelStride == 4 && rowStride.toLong() >= g.width.toLong() * pixelStride)
        val plane = buffer.duplicate() // Do not mutate the platform buffer's position or limit.
        val origin = plane.position()
        val lastByte = origin.toLong() + (g.bottom - 1L) * rowStride + (g.right - 1L) * pixelStride + 3
        require(lastByte < plane.limit())
        val cropWidth = g.right - g.left
        val cropHeight = g.bottom - g.top
        val sideways = g.rotationDegrees == 90 || g.rotationDegrees == 270
        val rotatedWidth = if (sideways) cropHeight else cropWidth
        val rotatedHeight = if (sideways) cropWidth else cropHeight
        val scale = minOf(1.0, 640.0 / maxOf(rotatedWidth, rotatedHeight))
        val width = maxOf(1, (rotatedWidth * scale).toInt())
        val height = maxOf(1, (rotatedHeight * scale).toInt())
        val pixels = IntArray(width * height)
        try {
            for (y in 0 until height) for (x in 0 until width) {
                val rx = minOf(rotatedWidth - 1, ((x + 0.5) * rotatedWidth / width).toInt())
                val ry = minOf(rotatedHeight - 1, ((y + 0.5) * rotatedHeight / height).toInt())
                val sx: Int
                val sy: Int
                when (g.rotationDegrees) {
                    90 -> { sx = ry; sy = cropHeight - 1 - rx }
                    180 -> { sx = cropWidth - 1 - rx; sy = cropHeight - 1 - ry }
                    270 -> { sx = cropWidth - 1 - ry; sy = rx }
                    else -> { sx = rx; sy = ry }
                }
                val offset = origin + (g.top + sy) * rowStride + (g.left + sx) * pixelStride
                // ImageAnalysis.OUTPUT_IMAGE_FORMAT_RGBA_8888 specifies byte order R,G,B,A.
                val r = plane.get(offset).toInt() and 255
                val green = plane.get(offset + 1).toInt() and 255
                val b = plane.get(offset + 2).toInt() and 255
                pixels[y * width + x] = (255 shl 24) or (r shl 16) or (green shl 8) or b
            }
            return Pixels(width, height, pixels)
        } catch (failure: Throwable) {
            pixels.fill(0)
            throw failure
        }
    }
}

/** Sensor timestamps, not wall clock. Repeated/out-of-order frames are dropped. */
internal class HandTrackingTimeline {
    data class Stamp(val milliseconds: Long, val reset: Boolean)
    private var geometry: CameraRgbaPixels.Geometry? = null
    private var originNs = 0L
    private var lastNs = -1L
    private var lastMs = -1L

    fun next(timestampNs: Long, nextGeometry: CameraRgbaPixels.Geometry): Stamp? {
        require(timestampNs >= 0)
        if (lastNs >= 0 && timestampNs <= lastNs) return null
        val reset = geometry != nextGeometry || lastNs < 0 ||
            timestampNs - originNs > 86_400_000_000_000L
        if (reset) {
            originNs = timestampNs
            lastMs = -1L
            geometry = nextGeometry
        }
        val ms = (timestampNs - originNs) / 1_000_000L
        if (ms <= lastMs) return null
        lastNs = timestampNs
        lastMs = ms
        return Stamp(ms, reset)
    }
}
