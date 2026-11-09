package com.xmilo_at_your_side.sign_to_speak

/** Host-only protocol harness. All association logic lives in the production class. */
fun main(args: Array<String>) {
    if (args.contentEquals(arrayOf("--contract"))) {
        contractChecks()
        println("Native contract checks passed")
        return
    }
    val tracker = RoboflowHandTracker()
    fun hand(raw: String): FloatArray? =
        if (raw == "null") null else raw.split(',').map { it.toFloat() }.toFloatArray()
    generateSequence(::readLine).forEach { line ->
        try {
            when (line) {
                "reset" -> { tracker.reset(); println("reset") }
                "close" -> { tracker.close(); println("close") }
                else -> {
                    val parts = line.split('|')
                    require(parts.size == 5)
                    val result = tracker.update(hand(parts[3]), hand(parts[4]),
                        parts[0].toDouble(), parts[1].toInt(), parts[2].toInt())
                    println(result.joinToString(";") {
                        "${it.side.toString().lowercase()},${it.generation},${it.trackerId ?: "null"},${it.continuous}"
                    }.ifEmpty { "empty" })
                }
            }
        } catch (_: IllegalArgumentException) {
            println("error")
        } catch (_: IllegalStateException) {
            println("error")
        }
    }
}


/** Native ownership/admission checks that have no equivalent Python lifecycle API. */
private fun contractChecks() {
    fun hand(x: Float, y: Float = .25f) = FloatArray(63) { index ->
        val point = index / 3
        when (index % 3) {
            0 -> x + (point % 5) * .012f
            1 -> y + (point / 5) * .015f
            else -> point * .001f
        }
    }
    fun rejects(block: () -> Unit) {
        var rejected = false
        try { block() } catch (_: IllegalArgumentException) { rejected = true }
        catch (_: IllegalStateException) { rejected = true }
        check(rejected) { "Invalid call accepted" }
    }
    val tracker = RoboflowHandTracker()
    val initialGeneration = tracker.generation
    val left = hand(.2f)
    val copy = left.copyOf()
    tracker.update(left, null, 0.0, 640, 480)
    check(left.contentEquals(copy)) { "Input mutated" }
    // Caller mutations after return cannot alter retained geometric state.
    left.fill(3.5f)
    val confirmed = tracker.update(copy, null, 100.0, 640, 480).single()
    check(confirmed.trackerId != null && !confirmed.continuous)
    val invalid = listOf(FloatArray(62), hand(.2f).also { it[0] = Float.NaN },
        hand(.2f).also { it[2] = Float.POSITIVE_INFINITY }, FloatArray(63),
        hand(.2f).also { it[2] = -5f })
    for (bad in invalid) rejects { tracker.update(copy, bad, 200.0, 640, 480) }
    rejects { tracker.update(copy, null, Double.NaN, 640, 480) }
    rejects { tracker.update(copy, null, 200.0, 0, 480) }
    rejects { tracker.update(copy, null, 200.0, 1280, 480) }
    check(tracker.generation == initialGeneration)
    val after = tracker.update(copy, null, 200.0, 640, 480).single()
    check(after.trackerId == confirmed.trackerId && after.continuous)
    tracker.reset()
    check(tracker.generation == initialGeneration + 1)
    rejects { tracker.update(copy, null, 0.0, 640, 480, initialGeneration) }
    check(tracker.update(copy, null, 0.0, 640, 480).single().trackerId == null)
    tracker.close()
    val closedGeneration = tracker.generation
    tracker.close()
    check(tracker.generation == closedGeneration)
    rejects { tracker.update(copy, null, 100.0, 640, 480) }
    rejects { tracker.reset() }

    // Confirm disjoint short-lived hypotheses faster than their 500ms expiry.
    // Exceeding the documented bound must reset rather than silently evict IDs.
    val bounded = RoboflowHandTracker()
    var timestamp = 0.0
    var overflow = false
    for (pair in 0..32) {
        fun location(index: Int): FloatArray = hand(-3.7f + (index % 16) * .2f,
                                                   -3.7f + (index / 16) * .2f)
        try {
            repeat(2) {
                bounded.update(location(pair * 2), location(pair * 2 + 1), timestamp++, 640, 480)
            }
        } catch (_: IllegalStateException) {
            check(pair == 32) { "Hypothesis bound failed too early" }
            overflow = true
        }
    }
    check(overflow && bounded.generation == 1L) { "Hypothesis limit did not fail closed" }
    check(bounded.update(copy, null, 0.0, 640, 480).single().trackerId == null)
    bounded.close()
}
