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
import com.google.mediapipe.framework.image.ByteBufferImageBuilder
import com.google.mediapipe.framework.image.MPImage
import com.google.mediapipe.tasks.components.containers.NormalizedLandmark
import com.google.mediapipe.tasks.core.BaseOptions
import com.google.mediapipe.tasks.vision.core.RunningMode
import com.google.mediapipe.tasks.vision.holisticlandmarker.HolisticLandmarker
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.DataInputStream
import java.io.File
import java.math.BigInteger
import java.nio.ByteBuffer
import java.security.MessageDigest
import java.util.Locale

/** Replays pinned pixels through the live Holistic producer and separate QA graph controls. */
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

    private enum class ReplayMode(val resultType: String, val marker: String) {
        CANONICAL("phone_holistic_producer_parity", "SIGN_HOLISTIC_PARITY_RESULT_BASE64="),
        OPTIONAL_OUTPUTS(
            "phone_holistic_optional_output_diagnostic",
            "SIGN_HOLISTIC_OPTIONAL_OUTPUT_RESULT_BASE64=",
        ),
        RGBA_BYTEBUFFER("phone_holistic_bytebuffer_diagnostic", "SIGN_HOLISTIC_BYTEBUFFER_RESULT_BASE64="),
        POSE_STAGE("phone_holistic_pose_stage_diagnostic", "SIGN_HOLISTIC_POSE_STAGE_RESULT_BASE64="),
    }

    @Test
    fun canonicalRgbaReplay() {
        val args = InstrumentationRegistry.getArguments()
        assumeTrue("Opt in with -e signHolisticParity 1", args.getString("signHolisticParity") == "1")
        runReplay(args, ReplayMode.CANONICAL)
    }

    @Test
    fun optionalOutputsGraphDiagnostic() {
        val args = InstrumentationRegistry.getArguments()
        assumeTrue(
            "Opt in with -e signHolisticOptionalOutputs 1",
            args.getString("signHolisticOptionalOutputs") == "1",
        )
        runReplay(args, ReplayMode.OPTIONAL_OUTPUTS)
    }

    @Test
    fun rgbaByteBufferGraphDiagnostic() {
        val args = InstrumentationRegistry.getArguments()
        assumeTrue("Opt in with -e signHolisticByteBuffer 1", args.getString("signHolisticByteBuffer") == "1")
        runReplay(args, ReplayMode.RGBA_BYTEBUFFER)
    }

    @Test
    fun poseStageGraphDiagnostic() {
        val args = InstrumentationRegistry.getArguments()
        assumeTrue("Opt in with -e signHolisticPoseStage 1", args.getString("signHolisticPoseStage") == "1")
        runReplay(args, ReplayMode.POSE_STAGE)
    }

    private fun runReplay(args: Bundle, mode: ReplayMode) {
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
            val createDetector: () -> LiveHandDetector = when (mode) {
                ReplayMode.CANONICAL -> { { LiveHolisticHandDetector.create(context) } }
                ReplayMode.OPTIONAL_OUTPUTS -> { { createOptionalOutputDetector(context) } }
                ReplayMode.RGBA_BYTEBUFFER -> {
                    { RgbaByteBufferDetector(context, fixture.frames) }
                }
                ReplayMode.POSE_STAGE -> {
                    val selectedTimestamps = fixture.frames
                        .filter { it.index in POSE_STAGE_FRAME_INDICES }
                        .map { it.timestampMs }
                        .toSet()
                    require(selectedTimestamps.size == POSE_STAGE_FRAME_INDICES.size) {
                        "Pose diagnostic requires both selected fixture indices"
                    }
                    ({ PoseStageDetector(context, selectedTimestamps) })
                }
            }
            val runs = JSONArray()
                .put(replay(createDetector, framesFile, "initial", fixture.frames, complete = true))
                .put(replay(createDetector, framesFile, "interrupted", fixture.frames.take(17), complete = false))
                .put(replay(createDetector, framesFile, "recovered", fixture.frames, complete = true))

            JSONObject()
                .put("type", mode.resultType)
                .put("schema_version", 1)
                .put("status", "COMPLETED")
                .put("run_id", runId)
                .put("fixture_manifest_sha256", manifestSha)
                .put("frames_sha256", framesSha)
                .put("model_sha256", modelSha)
                .put("input_mode", "canonical_rgba")
                .put("camera_path_tested", false)
                .apply {
                    if (mode == ReplayMode.OPTIONAL_OUTPUTS) {
                        put("diagnostic_mode", "optional_outputs_enabled")
                        put("production_path_tested", false)
                        put("outputFaceBlendshapes", true)
                        put("outputPoseSegmentationMasks", true)
                    }
                    if (mode == ReplayMode.RGBA_BYTEBUFFER) {
                        put("diagnostic_mode", "rgba_bytebuffer_input")
                        put("production_path_tested", false)
                        put("image_transport", "rgba_bytebuffer")
                    }
                    if (mode == ReplayMode.POSE_STAGE) {
                        put("diagnostic_mode", "pose_stage_landmarks")
                        put("production_path_tested", false)
                    }
                }
                .put("runs", runs)
                .toString().toByteArray(Charsets.UTF_8)
        }
        try {
            require(payload.size in 1..(512 * 1024)) { "Parity result exceeds stream bound" }
            val encoded = Base64.encodeToString(payload, Base64.NO_WRAP)
            check(encoded.isNotEmpty()) { "Parity result could not be encoded" }
            if (mode != ReplayMode.CANONICAL) {
                // Instrumentation sends a UTF-16 string through Binder.
                require(encoded.length <= 350_000) { "Diagnostic result exceeds Binder stream bound" }
            }
            instrumentation.sendStatus(2, Bundle().apply {
                putString("stream", "\n" + mode.marker + encoded + "\n")
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
        createDetector: () -> LiveHandDetector,
        framesFile: File,
        name: String,
        frames: List<Frame>,
        complete: Boolean,
    ): JSONObject {
        val results = JSONArray()
        val createStart = SystemClock.elapsedRealtimeNanos()
        val detector = createDetector()
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

    private fun createOptionalOutputDetector(context: Context): LiveHandDetector {
        val base = BaseOptions.builder().setModelAssetPath(LiveHolisticHandDetector.ASSET).build()
        val options = HolisticLandmarker.HolisticLandmarkerOptions.builder()
            .setBaseOptions(base)
            .setRunningMode(RunningMode.VIDEO)
            .setOutputPoseSegmentationMasks(true)
            .setOutputFaceBlendshapes(true)
            .build()
        val graph = HolisticLandmarker.createFromOptions(context, options)
        return object : LiveHandDetector {
            override fun detect(bitmap: Bitmap, timestampMs: Long): DetectedHands {
                var left: FloatArray? = null
                var right: FloatArray? = null
                try {
                    return HolisticBitmapInput.consume(bitmap) { image ->
                        val result = graph.detectForVideo(image, timestampMs)
                        val mask = result.segmentationMask().orElse(null)
                        AutoCloseable { mask?.close() }.use {
                            left = copyDiagnosticHand(result.leftHandLandmarks())
                            right = copyDiagnosticHand(result.rightHandLandmarks())
                            DetectedHands(left, right)
                        }
                    }
                } catch (failure: Throwable) {
                    left?.fill(0f)
                    right?.fill(0f)
                    throw failure
                }
            }

            override fun close() = graph.close()
        }
    }

    private inner class RgbaByteBufferDetector(context: Context, frames: List<Frame>) : LiveHandDetector {
        private val width = frames.first().width
        private val height = frames.first().height
        private val expectedHashes = frames.associate { it.timestampMs to it.sha256 }

        init {
            require(width in 1..640 && height in 1..640)
            require(frames.all { it.width == width && it.height == height }) {
                "ByteBuffer replay requires constant frame dimensions"
            }
        }

        private val pixels = IntArray(width * height)
        private val rgba = ByteArray(width * height * 4)
        private val buffer = ByteBuffer.allocateDirect(rgba.size)
        private val digest = MessageDigest.getInstance("SHA-256")
        private val graph = HolisticLandmarker.createFromOptions(
            context,
            HolisticLandmarker.HolisticLandmarkerOptions.builder()
                .setBaseOptions(BaseOptions.builder().setModelAssetPath(LiveHolisticHandDetector.ASSET).build())
                .setRunningMode(RunningMode.VIDEO)
                .build(),
        )

        var graphRgbaSha256: String? = null
            private set

        override fun detect(bitmap: Bitmap, timestampMs: Long): DetectedHands {
            graphRgbaSha256 = null
            var left: FloatArray? = null
            var right: FloatArray? = null
            try {
                return HolisticBitmapInput.consume(bitmap) {
                    require(bitmap.width == width && bitmap.height == height)
                    readBitmapRgba(bitmap, pixels, rgba)
                    buffer.clear()
                    buffer.put(rgba)
                    buffer.flip()
                    digest.update(buffer.duplicate())
                    val sha256 = hex(digest.digest())
                    require(sha256 == expectedHashes.getValue(timestampMs)) { "Graph input RGBA hash mismatch" }

                    ByteBufferImageBuilder(buffer, width, height, MPImage.IMAGE_FORMAT_RGBA).build().use { image ->
                        val result = graph.detectForVideo(image, timestampMs)
                        left = copyDiagnosticHand(result.leftHandLandmarks())
                        right = copyDiagnosticHand(result.rightHandLandmarks())
                    }
                    graphRgbaSha256 = sha256
                    DetectedHands(left, right)
                }
            } catch (failure: Throwable) {
                left?.fill(0f)
                right?.fill(0f)
                throw failure
            } finally {
                pixels.fill(0)
                rgba.fill(0)
                buffer.clear()
                buffer.put(rgba)
                buffer.clear()
            }
        }

        override fun close() = graph.close()
    }

    private inner class PoseStageDetector(
        context: Context,
        private val selectedTimestamps: Set<Long>,
    ) : LiveHandDetector {
        private val graph = HolisticLandmarker.createFromOptions(
            context,
            HolisticLandmarker.HolisticLandmarkerOptions.builder()
                .setBaseOptions(BaseOptions.builder().setModelAssetPath(LiveHolisticHandDetector.ASSET).build())
                .setRunningMode(RunningMode.VIDEO)
                .build(),
        )

        private var poseTimestampMs: Long? = null
        private var pose: FloatArray? = null

        override fun detect(bitmap: Bitmap, timestampMs: Long): DetectedHands {
            clearPose()
            var left: FloatArray? = null
            var right: FloatArray? = null
            try {
                return HolisticBitmapInput.consume(bitmap) { image ->
                    val result = graph.detectForVideo(image, timestampMs)
                    left = copyDiagnosticHand(result.leftHandLandmarks())
                    right = copyDiagnosticHand(result.rightHandLandmarks())
                    if (timestampMs in selectedTimestamps) {
                        pose = copyDiagnosticLandmarks(result.poseLandmarks(), 33)
                        poseTimestampMs = timestampMs
                    }
                    DetectedHands(left, right)
                }
            } catch (failure: Throwable) {
                left?.fill(0f)
                right?.fill(0f)
                clearPose()
                throw failure
            }
        }

        fun takePose(timestampMs: Long): FloatArray? {
            check(poseTimestampMs == timestampMs) { "Pose diagnostic timestamp mismatch" }
            val coordinates = pose
            pose = null
            poseTimestampMs = null
            return coordinates
        }

        private fun clearPose() {
            pose?.fill(0f)
            pose = null
            poseTimestampMs = null
        }

        override fun close() {
            clearPose()
            graph.close()
        }
    }

    private fun copyDiagnosticHand(points: List<NormalizedLandmark>): FloatArray? =
        copyDiagnosticLandmarks(points, 21)

    private fun copyDiagnosticLandmarks(points: List<NormalizedLandmark>, landmarkCount: Int): FloatArray? {
        if (points.isEmpty()) return null
        require(points.size == landmarkCount)
        val coordinates = FloatArray(landmarkCount * 3)
        try {
            for (index in points.indices) {
                val point = points[index]
                val offset = index * 3
                coordinates[offset] = point.x()
                coordinates[offset + 1] = point.y()
                coordinates[offset + 2] = point.z()
            }
            return coordinates
        } catch (failure: Throwable) {
            coordinates.fill(0f)
            throw failure
        }
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
                    check(bitmap.isRecycled) { "Replay detector did not consume the bitmap" }
                    val landmarks = JSONObject()
                        .put("left_hand", landmarksJson(hands.left, 21))
                        .put("right_hand", landmarksJson(hands.right, 21))
                    if (detector is PoseStageDetector && frame.index in POSE_STAGE_FRAME_INDICES) {
                        val pose = detector.takePose(frame.timestampMs)
                        try {
                            landmarks.put("pose", landmarksJson(pose, 33))
                        } finally {
                            pose?.fill(0f)
                        }
                    }
                    JSONObject()
                        .put("frame_index", frame.index)
                        .put("timestamp_ms", frame.timestampMs)
                        .put("rgba_sha256", bitmapSha256)
                        .put("landmarks", landmarks)
                        .put("start_elapsed_ns", start)
                        .put("end_elapsed_ns", end)
                        .put("elapsed_ns", end - start)
                        .put("bitmap_consumed", true)
                        .apply {
                            if (detector is RgbaByteBufferDetector) {
                                put("graph_rgba_sha256", checkNotNull(detector.graphRgbaSha256))
                            }
                        }
                }
            }
        } finally {
            rgba.fill(0)
            pixels.fill(0)
        }
    }

    private fun hashBitmapRgba(bitmap: Bitmap, pixels: IntArray, rgba: ByteArray): String {
        readBitmapRgba(bitmap, pixels, rgba)
        return hex(MessageDigest.getInstance("SHA-256").digest(rgba))
    }

    private fun readBitmapRgba(bitmap: Bitmap, pixels: IntArray, rgba: ByteArray) {
        bitmap.getPixels(pixels, 0, bitmap.width, 0, 0, bitmap.width, bitmap.height)
        for (index in pixels.indices) {
            val argb = pixels[index]
            val offset = index * 4
            rgba[offset] = (argb ushr 16).toByte()
            rgba[offset + 1] = (argb ushr 8).toByte()
            rgba[offset + 2] = argb.toByte()
            rgba[offset + 3] = (argb ushr 24).toByte()
        }
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

    private fun landmarksJson(points: FloatArray?, landmarkCount: Int): Any {
        if (points == null) return JSONObject.NULL
        require(points.size == landmarkCount * 3 && points.all { it.isFinite() }) {
            "Invalid Holistic landmarks"
        }
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
        val POSE_STAGE_FRAME_INDICES = setOf(0L, 34L)
        val RUN_ID = Regex("[0-9a-f]{32}")
        val SHA256 = Regex("[0-9a-f]{64}")
    }
}
