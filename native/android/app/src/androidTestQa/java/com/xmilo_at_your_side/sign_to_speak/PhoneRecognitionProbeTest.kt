package com.xmilo_at_your_side.sign_to_speak

import android.graphics.Bitmap
import android.media.MediaMetadataRetriever
import android.os.Bundle
import android.os.SystemClock
import androidx.test.platform.app.InstrumentationRegistry
import com.google.mediapipe.framework.image.BitmapImageBuilder
import com.google.mediapipe.tasks.components.containers.NormalizedLandmark
import com.google.mediapipe.tasks.core.BaseOptions
import com.google.mediapipe.tasks.vision.core.RunningMode
import com.google.mediapipe.tasks.vision.holisticlandmarker.HolisticLandmarker
import com.google.mediapipe.tasks.vision.holisticlandmarker.HolisticLandmarkerResult
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.tensorflow.lite.DataType
import org.tensorflow.lite.Interpreter
import java.io.File
import java.io.FileInputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.channels.FileChannel
import java.security.MessageDigest
import java.util.Locale
import java.util.UUID

/** Explicit research run only. No production camera or UI code uses these results. */
class PhoneRecognitionProbeTest {
    private val modelHash = "25531b695e07b21467ef91bea5164c96e36ff86a1fff84182613f39ac4724bed"
    private val holisticPin = "e2dab61191e2dcd0a15f943d8e3ed1dce13c82dfa597b9dd39f562975a50c3f8"
    private val labelsPin = "1fe747c2f44c68dbb396947e35193c96d363f3dede0be8defa5e08546400bf5d"
    private val shaPattern = Regex("[0-9a-f]{64}")
    private val clipPattern = Regex("[a-zA-Z0-9_-]{1,40}")
    private val frameCount = 32
    private val landmarkCount = 543
    private val modelBytes = 36_870_396L

    private data class ClipSpec(val id: String, val file: File, val sha: String, val expectedSource: String, val expected: String)

    @Test
    fun recordedVideoToHolisticToLocalModel() {
        val args: Bundle = InstrumentationRegistry.getArguments()
        assumeTrue("Opt in with -e recognitionProbe true; ordinary QA skips research assets", args.getString("recognitionProbe") == "true")
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        require(context.packageName == "com.xmilo_at_your_side.sign_to_speak.qa") { "Probe requires the isolated QA target" }
        val external = requireNotNull(context.getExternalFilesDir(null)) { "Test private external files unavailable" }
        val root = File(external, "recognition-probe")
        require(root.isDirectory && !root.isSymbolicLink()) { "Private recognition-probe directory missing" }
        val resultFile = owned(root, "result.json")
        // A failed opt-in run must never leave an older result looking current.
        require(!resultFile.exists() || (resultFile.isFile && resultFile.delete())) { "Could not clear prior probe result" }
        val manifestFile = owned(root, "manifest.json")
        val manifestText = readLimited(manifestFile, 16 * 1024)
        val manifestDigest = sha256(manifestText.toByteArray(Charsets.UTF_8))
        val manifest = JSONObject(manifestText)
        requireKeys(manifest, setOf("schema_version", "model_sha256", "holistic_sha256", "labels_sha256", "clips"))
        require(manifest.getInt("schema_version") == 1) { "Unsupported probe manifest schema" }
        require(manifest.getString("model_sha256") == modelHash) { "Model pin differs from reviewed model" }
        val holisticHash = checkedHash(manifest.getString("holistic_sha256"))
        val labelsHash = checkedHash(manifest.getString("labels_sha256"))
        require(holisticHash == holisticPin && labelsHash == labelsPin) { "Tracker or label pin differs from reviewed assets" }
        val modelFile = owned(root, "model.tflite")
        val holisticFile = owned(root, "holistic.task")
        val labelsFile = owned(root, "labels.json")
        require(modelFile.length() == modelBytes) { "Reviewed model size differs" }
        verifyFile(modelFile, modelHash, modelBytes)
        verifyFile(holisticFile, holisticHash, 30L * 1024 * 1024)
        verifyFile(labelsFile, labelsHash, 32L * 1024)
        val labels = parseLabels(readLimited(labelsFile, 32 * 1024))
        val clips = manifest.getJSONArray("clips")
        require(clips.length() in 1..4) { "Probe needs 1 to 4 clips" }
        val seen = mutableSetOf<String>()
        val clipSpecs = ArrayList<ClipSpec>(clips.length())
        for (index in 0 until clips.length()) {
            val clip = clips.getJSONObject(index)
            requireKeys(clip, setOf("clip_id", "path", "expected", "sha256"))
            val id = clip.getString("clip_id")
            require(clipPattern.matches(id) && seen.add(id)) { "Invalid or duplicate clip_id" }
            val path = clip.getString("path")
            require(path == "$id.mp4") { "Clip path must match its identity" }
            val expectedSource = clip.getString("expected")
            val expected = when (expectedSource) {
                "HELLO2" -> "hello"
                "PLEASE" -> "please"
                "THANKYOU" -> "thankyou"
                else -> expectedSource
            }
            require(labels.contains(expected)) { "Expected label absent from official label map" }
            val clipFile = owned(root, path)
            val clipHash = checkedHash(clip.getString("sha256"))
            verifyFile(clipFile, clipHash, 2L * 1024 * 1024)
            clipSpecs.add(ClipSpec(id, clipFile, clipHash, expectedSource, expected))
        }
        val output = JSONObject()
            .put("schema_version", 1)
            .put("run_id", UUID.randomUUID().toString())
            .put("manifest_sha256", manifestDigest)
            .put("scope", "Research-only isolated video classification; no live recognition, segmentation, translation, or validated accuracy")
            .put("model_sha256", modelHash)
            .put("holistic_sha256", holisticHash)
            .put("labels_sha256", labelsHash)
            .put("frames_requested_per_clip", frameCount)
            .put("layout", "first 468 face, 21 left hand, 33 pose, 21 right hand; normalized xyz; missing=NaN; parity unverified")
        val results = JSONArray()
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val driver = PhoneActivityDriver(instrumentation)
        try {
            driver.launch()
            // All asset provenance and manifest fields were checked before opening runtimes.
            val holisticBuffer = readDirect(holisticFile, 30 * 1024 * 1024)
            val modelBuffer = readDirect(modelFile, modelBytes.toInt())
            val baseOptions = BaseOptions.builder().setModelAssetBuffer(holisticBuffer).build()
            val options = HolisticLandmarker.HolisticLandmarkerOptions.builder()
                .setBaseOptions(baseOptions).setRunningMode(RunningMode.VIDEO).build()
            Interpreter(modelBuffer, Interpreter.Options().setNumThreads(1)).use { interpreter ->
                assertEquals(1, interpreter.inputTensorCount)
                assertEquals(1, interpreter.outputTensorCount)
                assertEquals(DataType.FLOAT32, interpreter.getInputTensor(0).dataType())
                assertEquals(DataType.FLOAT32, interpreter.getOutputTensor(0).dataType())
                clipSpecs.forEach { clip ->
                    // VIDEO timestamps restart at zero for each clip; tracking state must restart too.
                    HolisticLandmarker.createFromOptions(context, options).use { detector ->
                        results.put(runClip(clip.id, clip.file, clip.sha, clip.expectedSource, clip.expected, detector, interpreter, labels))
                    }
                }
            }
        } finally {
            driver.close()
        }
        output.put("clips", results)
        assertTrue("Probe wrote no clip results", results.length() > 0)
        atomicWrite(resultFile, output.toString())
    }

    private fun runClip(
        id: String, file: File, sha: String, expectedSource: String, expected: String,
        detector: HolisticLandmarker, interpreter: Interpreter, labels: Array<String>
    ): JSONObject {
        val started = SystemClock.elapsedRealtime()
        val features = ByteBuffer.allocateDirect(frameCount * landmarkCount * 3 * 4).order(ByteOrder.nativeOrder())
        val retriever = MediaMetadataRetriever()
        var frameSeen = 0
        var handFrames = 0
        var faceFrames = 0
        var poseFrames = 0
        try {
            try {
                retriever.setDataSource(file.absolutePath)
                val duration = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull()
                require(duration != null && duration in 100L..15_000L) { "Clip duration must be 0.1 to 15 seconds" }
                val width = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_WIDTH)?.toIntOrNull()
                val height = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_HEIGHT)?.toIntOrNull()
                require(width != null && height != null && width > 0 && height > 0) { "Invalid clip dimensions" }
                val scale = minOf(1.0, 640.0 / maxOf(width, height))
                val scaledWidth = maxOf(1, (width * scale).toInt())
                val scaledHeight = maxOf(1, (height * scale).toInt())
                var lastTimestamp = -1L
                for (index in 0 until frameCount) {
                    val timestampMs = index * (duration - 1) / frameCount
                    require(timestampMs > lastTimestamp) { "Video timestamps must increase strictly" }
                    lastTimestamp = timestampMs
                    var bitmap: Bitmap? = null
                    try {
                        bitmap = retriever.getScaledFrameAtTime(
                            timestampMs * 1000, MediaMetadataRetriever.OPTION_CLOSEST,
                            scaledWidth, scaledHeight
                        ) ?: error("Cannot decode requested video frame")
                        require(maxOf(bitmap.width, bitmap.height) <= 640) { "Decoded frame exceeded edge limit" }
                        val mpImage = BitmapImageBuilder(bitmap).build()
                        try {
                            val result = detector.detectForVideo(mpImage, timestampMs)
                            if (result.leftHandLandmarks().isNotEmpty() || result.rightHandLandmarks().isNotEmpty()) handFrames++
                            if (result.faceLandmarks().isNotEmpty()) faceFrames++
                            if (result.poseLandmarks().isNotEmpty()) poseFrames++
                            appendFeatures(features, result)
                            frameSeen++
                        } finally {
                            mpImage.close()
                        }
                    } finally {
                        bitmap?.recycle()
                    }
                }
            } finally {
                retriever.release()
            }
            val json = JSONObject()
                .put("clip_id", id).put("clip_sha256", sha).put("expected_source", expectedSource).put("expected", expected)
                .put("frames", frameSeen).put("hand_frames", handFrames)
                .put("face_frames", faceFrames).put("pose_frames", poseFrames)
            if (handFrames == 0) {
                return json.put("status", "abstain_no_hands")
                    .put("elapsed_ms", SystemClock.elapsedRealtime() - started)
            }
            features.rewind()
            interpreter.resizeInput(0, intArrayOf(frameSeen, landmarkCount, 3))
            interpreter.allocateTensors()
            require(interpreter.getInputTensor(0).shape().contentEquals(intArrayOf(frameSeen, landmarkCount, 3))) {
                "Unexpected model input shape"
            }
            require(interpreter.getOutputTensor(0).shape().contentEquals(intArrayOf(1, 250))) {
                "Unexpected model output shape"
            }
            val scores = Array(1) { FloatArray(250) }
            interpreter.run(features, scores)
            val distribution = scores[0]
            require(distribution.all { it.isFinite() }) { "Model emitted a nonfinite score" }
            val best = distribution.indices.maxByOrNull { distribution[it] } ?: error("Empty model output")
            val histogram = JSONArray()
            distribution.indices.sortedByDescending { distribution[it] }.take(5).forEach { index ->
                histogram.put(JSONObject().put("label", labels[index]).put("score", distribution[index].toDouble()))
            }
            return json.put("status", "classified_research_only")
                .put("top1", labels[best]).put("top1_score", distribution[best].toDouble())
                .put("matches_expected", labels[best] == expected)
                .put("top5", histogram)
                .put("elapsed_ms", SystemClock.elapsedRealtime() - started)
        } finally {
            features.clear()
            while (features.hasRemaining()) features.put(0.toByte())
        }
    }

    private fun appendFeatures(buffer: ByteBuffer, result: HolisticLandmarkerResult) {
        appendLandmarks(buffer, result.faceLandmarks(), 468)
        appendLandmarks(buffer, result.leftHandLandmarks(), 21)
        appendLandmarks(buffer, result.poseLandmarks(), 33)
        appendLandmarks(buffer, result.rightHandLandmarks(), 21)
    }

    private fun appendLandmarks(buffer: ByteBuffer, points: List<NormalizedLandmark>, count: Int) {
        require(points.isEmpty() || points.size >= count) { "Unexpected partial holistic landmark group" }
        for (index in 0 until count) {
            if (points.isEmpty()) {
                repeat(3) { buffer.putFloat(Float.NaN) }
            } else {
                val point = points[index]
                buffer.putFloat(point.x())
                buffer.putFloat(point.y())
                buffer.putFloat(point.z())
            }
        }
    }

    private fun parseLabels(json: String): Array<String> {
        val map = JSONObject(json)
        require(map.length() == 250) { "Expected official 250-class label map" }
        val labels = arrayOfNulls<String>(250)
        val keys = map.keys()
        while (keys.hasNext()) {
            val label = keys.next()
            val index = map.getInt(label)
            require(index in 0..249 && labels[index] == null) { "Invalid or duplicate label index" }
            labels[index] = label
        }
        require(labels.all { it != null }) { "Incomplete label index range" }
        return Array(250) { labels[it]!! }
    }

    private fun requireKeys(json: JSONObject, expected: Set<String>) {
        val keys = mutableSetOf<String>()
        val iterator = json.keys()
        while (iterator.hasNext()) keys.add(iterator.next())
        require(keys == expected) { "Unexpected or missing manifest fields" }
    }

    private fun sha256(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256")
        .digest(bytes).joinToString("") { "%02x".format(Locale.ROOT, it.toInt() and 0xff) }

    private fun checkedHash(hash: String): String {
        require(shaPattern.matches(hash)) { "Invalid SHA-256 field" }
        return hash
    }

    private fun owned(root: File, relative: String): File {
        require(relative.isNotEmpty() && relative.length <= 100 && !relative.contains('/') && !relative.contains('\\')) {
            "Probe asset name must be a direct child"
        }
        val file = File(root, relative)
        require(file.canonicalFile.parentFile == root.canonicalFile && !file.isSymbolicLink()) {
            "Probe asset escaped private directory"
        }
        return file
    }

    private fun ownedOutput(root: File, name: String): File = owned(root, name)

    private fun File.isSymbolicLink(): Boolean = java.nio.file.Files.isSymbolicLink(toPath())

    private fun verifyFile(file: File, expected: String, maxBytes: Long) {
        require(file.isFile && file.length() in 1..maxBytes) { "Probe asset missing or outside size limit" }
        val digest = MessageDigest.getInstance("SHA-256")
        FileInputStream(file).use { stream ->
            val chunk = ByteArray(64 * 1024)
            while (true) {
                val count = stream.read(chunk)
                if (count < 0) break
                digest.update(chunk, 0, count)
            }
        }
        val actual = digest.digest().joinToString("") { "%02x".format(Locale.ROOT, it.toInt() and 0xff) }
        require(actual == expected) { "Probe asset SHA-256 mismatch" }
    }

    private fun readLimited(file: File, maxBytes: Int): String {
        require(file.isFile && file.length() in 1..maxBytes.toLong()) { "Probe JSON size invalid" }
        return file.readText(Charsets.UTF_8)
    }

    private fun readDirect(file: File, maxBytes: Int): ByteBuffer {
        require(file.length() in 1..maxBytes.toLong()) { "Model asset size invalid" }
        return FileInputStream(file).channel.use { channel ->
            channel.map(FileChannel.MapMode.READ_ONLY, 0, channel.size()).order(ByteOrder.nativeOrder())
        }
    }

    private fun atomicWrite(target: File, json: String) {
        val temp = File(target.parentFile, "result.json.tmp")
        require(temp.canonicalFile.parentFile == target.parentFile.canonicalFile && !temp.isSymbolicLink())
        temp.writeText(json, Charsets.UTF_8)
        require(temp.renameTo(target)) { "Could not atomically publish probe result" }
    }
}
