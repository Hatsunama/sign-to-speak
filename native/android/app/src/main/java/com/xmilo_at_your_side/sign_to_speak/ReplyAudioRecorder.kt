package com.xmilo_at_your_side.sign_to_speak

import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build

/** The only production microphone source: 16 kHz mono PCM16, volatile and privacy-sensitive. */
internal class ReplyAudioRecorder : ReplySession.Recorder {
  private val record: AudioRecord
  @Volatile private var started = false
  @Volatile private var closed = false

  init {
    check(Build.VERSION.SDK_INT >= Build.VERSION_CODES.R)
    val format = AudioFormat.Builder()
      .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
      .setSampleRate(16_000)
      .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
      .build()
    val minimum = AudioRecord.getMinBufferSize(
      16_000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
    check(minimum > 0)
    val candidate = AudioRecord.Builder()
      .setAudioSource(MediaRecorder.AudioSource.VOICE_RECOGNITION)
      .setAudioFormat(format)
      .setBufferSizeInBytes(maxOf(minimum, 4_096))
      .setPrivacySensitive(true)
      .build()
    record = try {
      check(candidate.state == AudioRecord.STATE_INITIALIZED)
      check(candidate.sampleRate == 16_000)
      check(candidate.channelCount == 1)
      check(candidate.audioFormat == AudioFormat.ENCODING_PCM_16BIT)
      check(candidate.isPrivacySensitive)
      candidate
    } catch (failure: RuntimeException) {
      releaseRejected(candidate, failure)
      throw failure
    } catch (failure: LinkageError) {
      releaseRejected(candidate, failure)
      throw failure
    }
  }

  @Synchronized override fun start() {
    check(!closed && !started)
    record.startRecording()
    check(record.recordingState == AudioRecord.RECORDSTATE_RECORDING)
    started = true
  }

  override fun read(target: ShortArray, offset: Int, count: Int): Int =
    record.read(target, offset, count, AudioRecord.READ_BLOCKING)

  @Synchronized override fun stop() {
    if (!started) return
    // AudioRecord.stop() is the native unblock for a concurrent blocking read.
    record.stop()
    started = false
  }

  @Synchronized override fun close() {
    if (closed) return
    closed = true
    var failure: RuntimeException? = null
    try { stop() } catch (problem: RuntimeException) { failure = problem }
    try { record.release() } catch (problem: RuntimeException) { if (failure == null) failure = problem }
    failure?.let { throw it }
  }

  private fun releaseRejected(candidate: AudioRecord, failure: Throwable) {
    try { candidate.release() }
    catch (cleanup: RuntimeException) { failure.addSuppressed(cleanup) }
    catch (cleanup: LinkageError) { failure.addSuppressed(cleanup) }
  }
}
