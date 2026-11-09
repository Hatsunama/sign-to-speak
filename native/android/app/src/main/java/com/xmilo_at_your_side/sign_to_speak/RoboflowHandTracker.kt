/*
 * Trackers
 * Copyright (c) 2026 Roboflow. All Rights Reserved.
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy at https://www.apache.org/licenses/LICENSE-2.0
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 *
 * Kotlin adaptation for sign-to-speak, not the Python trackers package.
 * Source: https://github.com/roboflow/trackers/tree/3d1532372b46bae1e31a7f4aa3d7350482dadfb6
 * Adapted from core/sort/{tracker,tracklet,utils}.py and utils/{base_tracklet,
 * state_representations,motion_models,kalman_filter,predict_timing}.py.
 */
package com.xmilo_at_your_side.sign_to_speak

import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

/**
 * Offline geometric association of measured Holistic hands, not hand detection,
 * anatomical identity, recognition confidence, or model-input correction.
 *
 * The default XYXY SORT model has four independent position/velocity pairs;
 * its diagonal noise permits the equivalent four scalar 2x2 Kalman filters
 * below. Thresholding follows maximum-total-IoU assignment, not greedy matching.
 * At most two measurements and 64 retained hypotheses are allowed. Overflow
 * fails closed and resets instead of silently discarding a potentially valid ID.
 * Exact assignment ties use ascending track/detection order, which need not
 * reproduce SciPy's tie selection. Floating-point arithmetic is not bit-exact.
 *
 * No input arrays are retained or modified. Only measured slots are returned,
 * never predicted landmarks/boxes. IDs can cross anatomical slots. Callers must
 * keep the original feature ordering. Use one instance per fixed unmirrored
 * coordinate space. Capture [generation] when scheduling work, pass it as
 * expectedGeneration, and reject results whose generation is no longer current.
 */
class RoboflowHandTracker : AutoCloseable {
    enum class Side { LEFT, RIGHT }
    data class HandTrack(
        val side: Side,
        val generation: Long,
        val trackerId: Int?,
        val continuous: Boolean,
    )

    @get:Synchronized
    var generation: Long = 0L
        private set
    private var closed = false
    private var timestamp: Double? = null
    private var imageWidth = 0
    private var imageHeight = 0
    private var nextId = 0
    private val tracks = mutableListOf<Track>()
    private var previous = emptyMap<Side, Int>()

    /** Clears temporal state and invalidates work captured before this call. */
    @Synchronized
    fun reset() {
        check(!closed) { "Tracker is closed" }
        clearState()
    }

    /** Permanently cancels this instance; reopening requires a new instance. */
    @Synchronized
    override fun close() {
        if (!closed) {
            clearState()
            closed = true
        }
    }

    private fun clearState() {
        generation++
        tracks.clear()
        previous = emptyMap()
        timestamp = null
        imageWidth = 0
        imageHeight = 0
        nextId = 0
    }

    /**
     * Each hand is 21 xyz triples (63 floats), or null/all-NaN when absent.
     * All finite coordinates must lie in -4..4. Dimensions are 1..16384.
     * Timestamp is milliseconds within a clip, strictly increasing in 0..24h.
     * Validation failures leave state unchanged. Internal failures reset state.
     */
    @Synchronized
    fun update(
        left: FloatArray?,
        right: FloatArray?,
        timestampMs: Double,
        width: Int,
        height: Int,
        expectedGeneration: Long = generation,
    ): List<HandTrack> {
        check(!closed) { "Tracker is closed" }
        check(expectedGeneration == generation) { "Stale tracking generation" }
        require(timestampMs.isFinite() && timestampMs in 0.0..86_400_000.0) { "Invalid timestamp" }
        require(width in 1..16384 && height in 1..16384) { "Invalid image dimensions" }
        val oldTimestamp = timestamp
        require(oldTimestamp == null || timestampMs > oldTimestamp) { "Timestamps must strictly increase" }
        require(imageWidth == 0 || (width == imageWidth && height == imageHeight)) {
            "Coordinate space changed; reset first"
        }
        // Validate both complete hands before any state transition.
        val measurements = listOfNotNull(
            measuredBox(left, width, height)?.let { Measurement(Side.LEFT, it) },
            measuredBox(right, width, height)?.let { Measurement(Side.RIGHT, it) },
        )
        if (oldTimestamp != null && timestampMs - oldTimestamp > 250.0) clearState()
        try {
            val seconds = timestampMs / 1000.0
            val elapsed = timestamp?.let { seconds - it / 1000.0 } ?: (1.0 / 30.0)
            tracks.forEach { it.predict(elapsed) }
            tracks.removeAll { it.missedSeconds > 0.5 }
            val matches = associate(measurements)
            val matched = arrayOfNulls<Track>(measurements.size)
            // SciPy returns sorted track rows; preserve that order for ID allocation.
            for ((trackIndex, detectionIndex) in matches.sortedBy { it.first }) {
                val track = tracks[trackIndex]
                track.update(measurements[detectionIndex].box)
                matched[detectionIndex] = track
            }
            tracks.removeAll { it.hits < 2 && !it.updated }
            for (i in measurements.indices) {
                if (matched[i] == null) {
                    check(tracks.size < MAX_TRACKS) { "Tracking hypothesis limit exceeded" }
                    tracks.add(Track(measurements[i].box))
                }
            }
            for ((_, detectionIndex) in matches.sortedBy { it.first }) {
                val track = checkNotNull(matched[detectionIndex])
                if (track.hits >= 2 && track.id == null) {
                    check(nextId < Int.MAX_VALUE) { "Tracking identity limit exceeded" }
                    track.id = nextId++
                }
            }
            val result = measurements.mapIndexed { index, measurement ->
                val id = matched[index]?.id
                HandTrack(measurement.side, generation, id, id != null && previous[measurement.side] == id)
            }
            previous = result.mapNotNull { item -> item.trackerId?.let { item.side to it } }.toMap()
            timestamp = timestampMs
            imageWidth = width
            imageHeight = height
            return result
        } catch (failure: RuntimeException) {
            clearState()
            throw IllegalStateException("Tracking failed; state reset", failure)
        }
    }

    private data class Measurement(val side: Side, val box: DoubleArray)

    private fun measuredBox(points: FloatArray?, width: Int, height: Int): DoubleArray? {
        if (points == null) return null
        require(points.size == 63) { "Hand requires 21 xyz points" }
        if (points.all { it.isNaN() }) return null
        require(points.all { it.isFinite() && it in -4f..4f }) { "Invalid hand coordinates" }
        var x1 = Double.POSITIVE_INFINITY
        var y1 = Double.POSITIVE_INFINITY
        var x2 = Double.NEGATIVE_INFINITY
        var y2 = Double.NEGATIVE_INFINITY
        for (i in 0 until 21) {
            val x = points[i * 3].toDouble() * width
            val y = points[i * 3 + 1].toDouble() * height
            x1 = min(x1, x); y1 = min(y1, y)
            x2 = max(x2, x); y2 = max(y2, y)
        }
        require(x2 - x1 >= 1.0 && y2 - y1 >= 1.0) { "Hand box must span one pixel" }
        // The host adapter rounds detection boxes to float32 before SORT.
        return doubleArrayOf(x1, y1, x2, y2).map { it.toFloat().toDouble() }.toDoubleArray()
    }

    /** Exhaustive rectangular assignment is bounded by 64*63 pairs. */
    private fun associate(measurements: List<Measurement>): List<Pair<Int, Int>> {
        if (tracks.isEmpty() || measurements.isEmpty()) return emptyList()
        val scores = Array(tracks.size) { row ->
            DoubleArray(measurements.size) { col -> iou(tracks[row].box(), measurements[col].box) }
        }
        var best = Double.NEGATIVE_INFINITY
        var assignment = emptyList<Pair<Int, Int>>()
        if (measurements.size == 1 || tracks.size == 1) {
            for (row in tracks.indices) for (col in measurements.indices) {
                if (scores[row][col] > best) {
                    best = scores[row][col]
                    assignment = listOf(row to col)
                }
            }
        } else {
            for (row0 in tracks.indices) for (row1 in tracks.indices) {
                if (row0 == row1) continue
                val score = scores[row0][0] + scores[row1][1]
                if (score > best) {
                    best = score
                    assignment = listOf(row0 to 0, row1 to 1)
                }
            }
        }
        return assignment.filter { (row, col) -> scores[row][col] >= 0.3 }
    }

    private fun iou(a: DoubleArray, b: DoubleArray): Double {
        check(a.all { it.isFinite() }) { "Invalid predicted box" }
        val intersection = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) *
            max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
        val union = (a[2] - a[0]) * (a[3] - a[1]) +
            (b[2] - b[0]) * (b[3] - b[1]) - intersection
        // Pinned supervision 0.31.5: float64 calculation, float32 public output.
        return (if (union > 0.0) intersection / union else 0.0).toFloat().toDouble()
    }

    private class Track(initial: DoubleArray) {
        val axes = Array(4) { Axis(initial[it]) }
        var hits = 1
        var id: Int? = null
        var missedSeconds = 0.0
        var updated = true
        fun box() = DoubleArray(4) { axes[it].position }
        fun predict(elapsed: Double) {
            axes.forEach { it.predict(elapsed * 30.0) }
            missedSeconds += elapsed
            updated = false
        }
        fun update(box: DoubleArray) {
            axes.forEachIndexed { i, axis -> axis.update(box[i]) }
            // Only maturity matters; saturate instead of overflowing on long sessions.
            hits = min(2, hits + 1)
            missedSeconds = 0.0
            updated = true
        }
    }

    /** Independent XYXY position/velocity pair, P=I, R=.1, Q=.01*I. */
    private class Axis(var position: Double) {
        private var velocity = 0.0
        private var pp = 1.0
        private var pv = 0.0
        private var vp = 0.0
        private var vv = 1.0
        fun predict(step: Double) {
            val nominal = abs(step - 1.0) <= 0.004 * 30.0
            val s2 = step * step
            val qpp = if (nominal) 0.01 else 0.01 * s2 * s2 / 4.0
            val qpv = if (nominal) 0.0 else 0.01 * s2 * step / 2.0
            val qvv = if (nominal) 0.01 else 0.01 * s2
            position += step * velocity
            pp = pp + step * vp + step * (pv + step * vv) + qpp
            pv += step * vv + qpv
            vp += step * vv + qpv
            vv += qvv
        }
        fun update(measurement: Double) {
            val residual = measurement - position
            val kp = pp / (pp + 0.1)
            val kv = vp / (pp + 0.1)
            position += kp * residual
            velocity += kv * residual
            // Joseph covariance update: (I-KH) P (I-KH)' + K R K'.
            val a = 1.0 - kp
            val oldPp = pp; val oldPv = pv; val oldVp = vp; val oldVv = vv
            pp = a * oldPp * a + kp * 0.1 * kp
            pv = a * oldPp * -kv + a * oldPv + kp * 0.1 * kv
            vp = (-kv * oldPp + oldVp) * a + kv * 0.1 * kp
            vv = (-kv * oldPp + oldVp) * -kv + (-kv * oldPv + oldVv) + kv * 0.1 * kv
            check(position.isFinite() && velocity.isFinite() && pp.isFinite() && vv.isFinite()) {
                "Nonfinite Kalman state"
            }
        }
    }

    companion object {
        const val MAX_TRACKS = 64
    }
}
