package com.xmilo_at_your_side.sign_to_speak

import android.content.Context
import android.content.pm.ApplicationInfo
import android.graphics.Bitmap
import android.os.Bundle
import android.os.ParcelFileDescriptor
import android.os.SystemClock
import android.util.Base64
import android.util.JsonReader
import android.util.JsonToken
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.DataInputStream
import java.io.File
import java.math.BigInteger
import java.security.MessageDigest
import java.util.Locale

/** Replays pinned pixels through the same Holistic producer used by live tracking. */
class PhoneHolisticProducerParityTest {
    private data class Frame(
        val index: Long,
        val width: Int,
        val height: Int,
        val length: Int,
        val sha256: String,
        val timestampMs: Long,
    )

    private data class Fixture(val bytes: Long, val frames: List<Frame>)

    @Test
    fun canonicalRgbaReplay() {
        val args = InstrumentationRegistry.getArguments()
        assumeTrue("Opt in with -e signHolisticParity 1", args.getString("signHolisticParity") == "1")
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        require(context.packageName == "com.xmilo_at_your_side.sign_to_speak.qa") { "Isolated QA target required" }
        require(context.applicationInfo.flags and ApplicationInfo.FLAG_TEST_ONLY != 0) { "Test-only target required" }
        require(LiveHolisticHandDetector.isSupportedProcess()) { "ARM64 process required" }

        val runId = requireNotNull(args.getString("signParityRunId")) { "signParityRunId required" }
        require(RUN_ID.matches(runId)) { "Invalid parity run ID" }
        val manifestSha = checkedHash(args.getString("signParityManifestSha256"))
        val framesSha = checkedHash(args.getString("signParityRgbaSha256"))
        verifyBitmapRgbaHash()
        val stage = RecognitionInputStage.create(context.cacheDir, runId)
        val payload = AutoCloseable { RecognitionInputStage.cleanup(stage) }.use {
            val manifest = transfer(stage, runId, "manifest.json", manifestSha, 64L * 1024)
            val fixture = parseFixture(manifest, framesSha)
            val framesFile = transfer(stage, runId, RGBA_NAME, framesSha, fixture.bytes)
            require(framesFile.length() == fixture.bytes) { "Staged RGBA length differs from manifest" }
            val modelSha = hashBundledModel(context)
            val runs = JSONArray()
                .put(replay(context, framesFile, "initial", fixture.frames, complete = true))
                .put(replay(context, framesFile, "interrupted", fixture.frames.take(17), complete = false))
                .put(replay(context, framesFile, "recovered", fixture.frames, complete = true))

            JSONObject()
                .put("type", "phone_holistic_producer_parity")
                .put("schema_version", 1)
                .put("status", "COMPLETED")
                .put("run_id", runId)
                .put("fixture_manifest_sha256", manifestSha)
                .put("frames_sha256", framesSha)
                .put("model_sha256", modelSha)
                .put("input_mode", "canonical_rgba")
                .put("camera_path_tested", false)
                .put("runs", runs)
                .toString().toByteArray(Charsets.UTF_8)
        }
        try {
            require(payload.size in 1..(512 * 1024)) { "Parity result exceeds stream bound" }
            val encoded = Base64.encodeToString(payload, Base64.NO_WRAP)
            check(encoded.isNotEmpty()) { "Parity result could not be encoded" }
            instrumentation.sendStatus(2, Bundle().apply {
                putString("stream", "\nSIGN_HOLISTIC_PARITY_RESULT_BASE64=" + encoded + "\n")
            })
        } finally {
            payload.fill(0)
        }
    }

    private fun transfer(stage: File, runId: String, name: String, sha256: String, maxBytes: Long): File {
        val path = RecognitionInputStage.shellPath(runId, name)
        val automation = InstrumentationRegistry.getInstrumentation().uiAutomation
        val descriptor = automation.executeShellCommand("cat " + path)
        return ParcelFileDescriptor.AutoCloseInputStream(descriptor).use { input ->
            RecognitionInputStage.copy(stage, name, input, sha256, maxBytes)
        }
    }

    private fun parseFixture(file: File, framesSha: String): Fixture {
        val manifest = JsonReader(file.reader(Charsets.UTF_8).buffered()).use { reader ->
            val value = readManifestValue(reader) as? JSONObject ?: error("Expected manifest object")
            require(reader.peek() == JsonToken.END_DOCUMENT) { "Trailing manifest data" }
            value
        }
        manifest.requireKeys(
            "type", "schema_version", "pixel_format", "byte_order", "frames_file",
            "source_clip", "source_model", "frame_count", "frames",
        )
        require(manifest.get("type") == "holistic_rgba_replay_fixture" && manifest.integer("schema_version") == 1L)
        require(manifest.get("pixel_format") == "RGBA_8888" && manifest.get("byte_order") == "row_major_RGBA")

        val blob = manifest.getJSONObject("frames_file")
        blob.requireKeys("name", "bytes", "sha256")
        require(blob.get("name") == RGBA_NAME && checkedHash(blob.get("sha256")) == framesSha)
        val bytes = blob.integer("bytes")
        require(bytes in 1L..(128L * 1024 * 1024)) { "Invalid RGBA blob size" }

        val clip = manifest.getJSONObject("source_clip")
        clip.requireKeys("bytes", "sha256")
        require(clip.integer("bytes") > 0)
        checkedHash(clip.get("sha256"))
        val model = manifest.getJSONObject("source_model")
        model.requireKeys("bytes", "sha256")
        require(model.integer("bytes") == MODEL_BYTES && checkedHash(model.get("sha256")) == MODEL_SHA256) {
            "Source model differs from the pinned bundle"
        }

        val records = manifest.getJSONArray("frames")
        require(records.length() in 1..64 && manifest.integer("frame_count") == records.length().toLong())
        var nextOffset = 0L
        var previousIndex = -1L
        var previousTimestamp = -1L
        var firstPts = BigInteger.ZERO
        var firstDenominator = BigInteger.ONE
        val frames = List(records.length()) { recordIndex ->
            val row = records.getJSONObject(recordIndex)
            row.requireKeys(
                "frame_index", "width", "height", "offset_bytes", "length_bytes",
                "rgba_sha256", "source_pts", "source_time_base", "timestamp_ms",
            )
            val index = row.integer("frame_index")
            val timestamp = row.integer("timestamp_ms")
            require(index > previousIndex && timestamp > previousTimestamp) { "Frame order must increase strictly" }

            val width = row.integer("width")
            val height = row.integer("height")
            require(width in 1L..640L && height in 1L..640L)
            val length = (width * height * 4).toInt()
            require(row.integer("offset_bytes") == nextOffset && row.integer("length_bytes") == length.toLong())
            nextOffset += length
            require(nextOffset <= bytes)

            val timeBase = row.getJSONArray("source_time_base")
            require(timeBase.length() == 2)
            val numerator = timeBase.get(0) as? Long ?: error("Expected time-base numerator")
            val denominator = timeBase.get(1) as? Long ?: error("Expected time-base denominator")
            require(numerator > 0 && denominator > 0)
            val pts = BigInteger.valueOf(row.integer("source_pts")) * BigInteger.valueOf(numerator)
            val timeDenominator = BigInteger.valueOf(denominator)
            if (recordIndex == 0) {
                firstPts = pts
                firstDenominator = timeDenominator
            }
            val relativePts = pts * firstDenominator - firstPts * timeDenominator
            require(relativePts.signum() >= 0)
            val measuredMs = relativePts * BigInteger.valueOf(1000) / (timeDenominator * firstDenominator)
            require(measuredMs == BigInteger.valueOf(timestamp)) { "Timestamp differs from measured relative PTS" }

            previousIndex = index
            previousTimestamp = timestamp
            Frame(index, width.toInt(), height.toInt(), length, checkedHash(row.get("rgba_sha256")), timestamp)
        }
        require(nextOffset == bytes) { "Frame layout does not cover the exact RGBA blob" }
        return Fixture(bytes, frames)
    }

    // Strict parsing preserves integer types and rejects duplicate or unexpected fields.
    private fun readManifestValue(reader: JsonReader, depth: Int = 0): Any {
        require(depth <= 4) { "Manifest nesting exceeds contract" }
        return when (reader.peek()) {
            JsonToken.BEGIN_OBJECT -> JSONObject().apply {
                reader.beginObject()
                while (reader.hasNext()) {
                    val key = reader.nextName()
                    require(!has(key)) { "Duplicate manifest field" }
                    put(key, readManifestValue(reader, depth + 1))
                }
                reader.endObject()
            }
            JsonToken.BEGIN_ARRAY -> JSONArray().apply {
                reader.beginArray()
                while (reader.hasNext()) put(readManifestValue(reader, depth + 1))
                reader.endArray()
            }
            JsonToken.STRING -> reader.nextString()
            JsonToken.NUMBER -> reader.nextString().toLongOrNull() ?: error("Expected JSON integer")
            else -> error("Unexpected manifest value type")
        }
    }

    private fun JSONObject.requireKeys(vararg expected: String) {
        require(keys().asSequence().toSet() == expected.toSet()) { "Unexpected or missing manifest fields" }
    }

    private fun JSONObject.integer(key: String): Long = get(key) as? Long ?: error("Expected manifest integer")

    private fun checkedHash(value: Any?): String {
        require(value is String && SHA256.matches(value)) { "Invalid SHA-256 pin or field" }
        return value
    }

    private fun hashBundledModel(context: Context): String {
        val digest = MessageDigest.getInstance("SHA-256")
        val scratch = ByteArray(64 * 1024)
        var count = 0L
        try {
            context.assets.open(LiveHolisticHandDetector.ASSET).use { input ->
                while (true) {
                    val read = input.read(scratch)
                    if (read < 0) break
                    count += read
                    require(count <= MODEL_BYTES)
                    digest.update(scratch, 0, read)
                }
            }
        } finally {
            scratch.fill(0)
        }
        val sha256 = hex(digest.digest())
        require(count == MODEL_BYTES && sha256 == MODEL_SHA256) { "Actual bundled model differs from contract" }
        return sha256
    }

    private fun replay(
        context: Context,
        framesFile: File,
        name: String,
        frames: List<Frame>,
        complete: Boolean,
    ): JSONObject {
        val results = JSONArray()
        val createStart = SystemClock.elapsedRealtimeNanos()
        val detector = LiveHolisticHandDetector.create(context)
        val createElapsed = detector.use {
            val elapsed = SystemClock.elapsedRealtimeNanos() - createStart
            DataInputStream(framesFile.inputStream().buffered()).use { input ->
                for (frame in frames) results.put(detectFrame(detector, input, frame))
                if (complete) require(input.read() == -1) { "Unexpected bytes after the final frame" }
            }
            elapsed
        }
        return JSONObject().put("name", name)
            .put("detector_create_elapsed_ns", createElapsed)
            .put("detector_closed", true)
            .put("frames", results)
    }

    private fun detectFrame(detector: LiveHandDetector, input: DataInputStream, frame: Frame): JSONObject {
        val rgba = ByteArray(frame.length)
        val pixels = IntArray(frame.width * frame.height)
        try {
            input.readFully(rgba)
            val fileSha256 = hex(MessageDigest.getInstance("SHA-256").digest(rgba))
            require(fileSha256 == frame.sha256) { "RGBA frame hash mismatch" }
            for (index in pixels.indices) {
                val offset = index * 4
                val red = rgba[offset].toInt() and 255
                val green = rgba[offset + 1].toInt() and 255
                val blue = rgba[offset + 2].toInt() and 255
                require(rgba[offset + 3] == 255.toByte()) { "RGBA frame must be opaque" }
                pixels[index] = (255 shl 24) or (red shl 16) or (green shl 8) or blue
            }

            val bitmap = Bitmap.createBitmap(frame.width, frame.height, Bitmap.Config.ARGB_8888)
            return AutoCloseable {
                if (!bitmap.isRecycled) {
                    try { bitmap.eraseColor(0) } finally { bitmap.recycle() }
                }
            }.use {
                bitmap.setPixels(pixels, 0, frame.width, 0, 0, frame.width, frame.height)
                val bitmapSha256 = hashBitmapRgba(bitmap, pixels, rgba)
                require(bitmapSha256 == frame.sha256) { "Detector bitmap RGBA hash mismatch" }
                val start = SystemClock.elapsedRealtimeNanos()
                detector.detect(bitmap, frame.timestampMs).use { hands ->
                    val end = SystemClock.elapsedRealtimeNanos()
                    check(bitmap.isRecycled) { "Production detector did not consume the bitmap" }
                    JSONObject()
                        .put("frame_index", frame.index)
                        .put("timestamp_ms", frame.timestampMs)
                        .put("rgba_sha256", bitmapSha256)
                        .put("landmarks", JSONObject()
                            .put("left_hand", handJson(hands.left))
                            .put("right_hand", handJson(hands.right)))
                        .put("start_elapsed_ns", start)
                        .put("end_elapsed_ns", end)
                        .put("elapsed_ns", end - start)
                        .put("bitmap_consumed", true)
                }
            }
        } finally {
            rgba.fill(0)
            pixels.fill(0)
        }
    }

    private fun hashBitmapRgba(bitmap: Bitmap, pixels: IntArray, rgba: ByteArray): String {
        bitmap.getPixels(pixels, 0, bitmap.width, 0, 0, bitmap.width, bitmap.height)
        for (index in pixels.indices) {
            val argb = pixels[index]
            val offset = index * 4
            rgba[offset] = (argb ushr 16).toByte()
            rgba[offset + 1] = (argb ushr 8).toByte()
            rgba[offset + 2] = argb.toByte()
            rgba[offset + 3] = (argb ushr 24).toByte()
        }
        return hex(MessageDigest.getInstance("SHA-256").digest(rgba))
    }

    private fun verifyBitmapRgbaHash() {
        val expectedRgba = byteArrayOf(
            0x11, 0x22, 0x33, 0xff.toByte(), 0x44, 0x55, 0x66, 0xff.toByte(),
            0x77, 0x88.toByte(), 0x99.toByte(), 0xff.toByte(), 0xaa.toByte(), 0xbb.toByte(), 0xcc.toByte(), 0xff.toByte(),
        )
        val expectedHash = hex(MessageDigest.getInstance("SHA-256").digest(expectedRgba))
        val pixels = intArrayOf(0xff112233.toInt(), 0xff445566.toInt(), 0xff778899.toInt(), 0xffaabbcc.toInt())
        val rgba = ByteArray(expectedRgba.size)
        val bitmap = Bitmap.createBitmap(2, 2, Bitmap.Config.ARGB_8888)
        try {
            bitmap.setPixels(pixels, 0, 2, 0, 0, 2, 2)
            check(hashBitmapRgba(bitmap, pixels, rgba) == expectedHash) { "Known bitmap RGBA hash mismatch" }
            bitmap.setPixel(1, 1, 0xffccbbaa.toInt())
            check(hashBitmapRgba(bitmap, pixels, rgba) != expectedHash) { "Bitmap RGBA hash missed swapped red and blue" }
        } finally {
            bitmap.recycle()
        }
    }

    private fun handJson(points: FloatArray?): Any {
        if (points == null) return JSONObject.NULL
        require(points.size == 63 && points.all { it.isFinite() }) { "Invalid Holistic hand landmarks" }
        return JSONArray().apply {
            for (index in points.indices step 3) {
                put(JSONArray().put(points[index].toDouble())
                    .put(points[index + 1].toDouble()).put(points[index + 2].toDouble()))
            }
        }
    }

    private fun hex(bytes: ByteArray): String =
        bytes.joinToString("") { "%02x".format(Locale.ROOT, it.toInt() and 255) }

    private companion object {
        const val RGBA_NAME = "holistic-parity.rgba"
        const val MODEL_BYTES = 13_683_609L
        const val MODEL_SHA256 = "e2dab61191e2dcd0a15f943d8e3ed1dce13c82dfa597b9dd39f562975a50c3f8"
        val RUN_ID = Regex("[0-9a-f]{32}")
        val SHA256 = Regex("[0-9a-f]{64}")
    }
}
