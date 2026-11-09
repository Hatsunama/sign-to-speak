package com.xmilo_at_your_side.sign_to_speak

import java.nio.ByteBuffer

fun main() {
    val source = ByteBuffer.allocate(7 + 3 * 20)
    source.position(7)
    for (y in 0..2) for (x in 0..3) {
        val index = 7 + y * 20 + x * 4
        source.put(index, (y * 4 + x + 1).toByte())
        source.put(index + 1, 0x22)
        source.put(index + 2, 0x33)
        source.put(index + 3, 0x77)
    }
    val geometry = CameraRgbaPixels.Geometry(4, 3, 1, 0, 4, 2, 0)
    val expected = mapOf(0 to listOf(2, 3, 4, 6, 7, 8),
        90 to listOf(6, 2, 7, 3, 8, 4), 180 to listOf(8, 7, 6, 4, 3, 2),
        270 to listOf(4, 8, 3, 7, 2, 6))
    expected.forEach { (rotation, red) ->
        val pixels = CameraRgbaPixels.copy(source, 20, 4, geometry.copy(rotationDegrees = rotation))
        check(pixels.argb.map { it ushr 16 and 255 } == red) { "Wrong unmirrored rotation $rotation" }
        check(pixels.argb.all { it and 0xff00ffff.toInt() == 0xff002233.toInt() })
        check(source.position() == 7 && source.limit() == 67)
        pixels.close()
        check(pixels.argb.all { it == 0 })
    }
    val large = CameraRgbaPixels.copy(ByteBuffer.allocate(1280 * 720 * 4), 1280 * 4, 4,
        CameraRgbaPixels.Geometry(1280, 720, 0, 0, 1280, 720, 90))
    check(large.width == 360 && large.height == 640)
    large.close()
    val bad = listOf(geometry.copy(left = -1), geometry.copy(right = 5),
        geometry.copy(rotationDegrees = 45), geometry.copy(width = 4097), geometry.copy(bottom = 0))
    bad.forEach { check(runCatching { CameraRgbaPixels.copy(source, 20, 4, it) }.isFailure) }
    check(runCatching { CameraRgbaPixels.copy(source, 15, 4, geometry) }.isFailure)
    check(runCatching { CameraRgbaPixels.copy(source, 20, 3, geometry) }.isFailure)
    check(runCatching { CameraRgbaPixels.copy(ByteBuffer.allocate(8), 20, 4, geometry) }.isFailure)
    val timeline = HandTrackingTimeline()
    check(timeline.next(1_000_000_000, geometry) == HandTrackingTimeline.Stamp(0, true))
    check(timeline.next(1_000_000_000, geometry) == null)
    check(timeline.next(999_000_000, geometry) == null)
    check(timeline.next(1_000_999_999, geometry) == null)
    check(timeline.next(1_033_000_000, geometry) == HandTrackingTimeline.Stamp(33, false))
    check(timeline.next(1_067_000_000, geometry.copy(rotationDegrees = 90)) == HandTrackingTimeline.Stamp(0, true))
    check(timeline.next(1_400_000_000, geometry.copy(rotationDegrees = 90)) == HandTrackingTimeline.Stamp(333, false))
    check(timeline.next(1_700_000_000, geometry.copy(rotationDegrees = 90)) == HandTrackingTimeline.Stamp(633, false))
    check(timeline.next(86_402_000_000_000, geometry) == HandTrackingTimeline.Stamp(0, true))
    check(runCatching { timeline.next(-1, geometry) }.isFailure)
    println("Camera RGBA/timeline: four rotations, crop/stride/position, bounds, wipe, scale, timestamp/gap/geometry checks passed")
}
