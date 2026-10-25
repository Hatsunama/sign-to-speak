package com.xmilo_at_your_side.sign_to_speak

import android.Manifest
import android.content.pm.PackageManager
import android.view.View
import android.widget.FrameLayout
import androidx.camera.core.CameraState
import androidx.camera.core.CameraSelector
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.LiveData
import androidx.lifecycle.Observer
import com.facebook.react.bridge.Arguments
import com.facebook.react.bridge.LifecycleEventListener
import com.facebook.react.uimanager.ThemedReactContext
import com.facebook.react.uimanager.UIManagerHelper
import com.facebook.react.uimanager.events.Event
import com.facebook.react.bridge.WritableMap

/** Preview only. No frame analysis, capture, recording, model, transcript or speech. */
class PrivateCameraView(private val reactContext: ThemedReactContext) :
    FrameLayout(reactContext), LifecycleEventListener {
    private val previewView = PreviewView(reactContext)
    private var provider: ProcessCameraProvider? = null
    private var preview: Preview? = null
    private val lease = PreviewLease()
    private enum class Lens(val selector: CameraSelector, val unavailableStatus: String) {
        BACK(CameraSelector.DEFAULT_BACK_CAMERA, "rear_camera_unavailable"),
        FRONT(CameraSelector.DEFAULT_FRONT_CAMERA, "front_camera_unavailable"),
    }
    private var selectedLens: Lens? = Lens.BACK
    private var requested = false
    private var observing = false
    private var disposed = false
    private var cameraActivity: MainActivity? = null
    private var cleanupFailed = false
    private var streamObserver: Observer<PreviewView.StreamState>? = null
    private var cameraStates: LiveData<CameraState>? = null
    private var cameraObserver: Observer<CameraState>? = null
    private var previewLayoutPosted = false
    private val layoutPreview = Runnable {
        try {
            if (isAttachedToWindow && !disposed && width > 0 && height > 0) {
                // Fabric does not lay out children again when CameraX adds its TextureView.
                // Use the bounds Fabric already assigned to this native host.
                previewView.measure(
                    View.MeasureSpec.makeMeasureSpec(width, View.MeasureSpec.EXACTLY),
                    View.MeasureSpec.makeMeasureSpec(height, View.MeasureSpec.EXACTLY),
                )
                previewView.layout(0, 0, width, height)
            }
        } finally {
            previewLayoutPosted = false
        }
    }

    init {
        registerWithActivity()
        // TextureView remains inside the normal secure Activity composition.
        previewView.implementationMode = PreviewView.ImplementationMode.COMPATIBLE
        addView(previewView, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT))
    }

    fun setLensFacing(lensFacing: String?) {
        if (disposed) return
        val next = when (lensFacing) {
            "back" -> Lens.BACK
            "front" -> Lens.FRONT
            else -> null
        }
        if (next == selectedLens) return
        val wasRequested = requested
        // Invalidate the previous lease and release its owned Preview before a new bind.
        val released = if (wasRequested) stop() else true
        selectedLens = next
        if (next == null) {
            if (released) {
                if (cleanupFailed || (cameraActivity?.cameraCleanupFailures ?: 0) > 0) reportCleanupFailure()
                else status("camera_unavailable")
            }
            return
        }
        if (!wasRequested || !released || disposed) return
        registerWithActivity()
        if (cameraActivity?.capturePrivacyReady != true ||
            cleanupFailed || (cameraActivity?.cameraCleanupFailures ?: 0) > 0 ||
            !isAttachedToWindow) {
            if (cleanupFailed || (cameraActivity?.cameraCleanupFailures ?: 0) > 0) reportCleanupFailure()
            else if (cameraActivity?.capturePrivacyReady != true) status("capture_privacy_unavailable")
            return
        }
        val owner = reactContext.currentActivity as? LifecycleOwner
        if (owner == null || !owner.lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)) return
        requested = true
        start()
    }

    fun setActive(active: Boolean) {
        if (!active) { stop(); return }
        if (disposed || requested) return
        registerWithActivity()
        if (cameraActivity?.capturePrivacyReady != true) {
            stop()
            status("capture_privacy_unavailable")
            return
        }
        if (cleanupFailed || (cameraActivity?.cameraCleanupFailures ?: 0) > 0) {
            reportCleanupFailure(); return
        }
        if (selectedLens == null) { status("camera_unavailable"); return }
        requested = true
        if (isAttachedToWindow) start()
    }

    private fun start() {
        if (!requested || disposed || !isAttachedToWindow) return
        registerWithActivity()
        if (cameraActivity?.capturePrivacyReady != true) {
            if (stop()) status("capture_privacy_unavailable")
            return
        }
        if (cleanupFailed || (cameraActivity?.cameraCleanupFailures ?: 0) > 0) {
            if (stop()) reportCleanupFailure()
            return
        }
        val lens = selectedLens
        if (lens == null) {
            if (stop()) status("camera_unavailable")
            return
        }
        val owner = reactContext.currentActivity as? LifecycleOwner
        if (owner == null || !owner.lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)) {
            if (stop()) status("stopped"); return
        }
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            if (stop()) status("permission_denied"); return
        }
        val token = lease.begin(true, true)
        try {
            val future = ProcessCameraProvider.getInstance(context)
            future.addListener({
                if (!requested || disposed || !isAttachedToWindow || !lease.isCurrent(token) ||
                    !owner.lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)) return@addListener
                // A different view may have recorded an Activity-wide cleanup fault
                // while this request was waiting for its provider.
                registerWithActivity()
                if (cleanupFailed || (cameraActivity?.cameraCleanupFailures ?: 0) > 0) {
                    if (stop()) reportCleanupFailure()
                    return@addListener
                }
                if (cameraActivity?.capturePrivacyReady != true) {
                    if (stop()) status("capture_privacy_unavailable")
                    return@addListener
                }
                try {
                    val cameraProvider = future.get()
                    val camera = lens.selector
                    if (!cameraProvider.hasCamera(camera)) {
                        if (stop()) status(lens.unavailableStatus); return@addListener
                    }
                    val hadRetiredStream = previewView.previewStreamState.value == PreviewView.StreamState.STREAMING
                    val useCase = Preview.Builder().build()
                    provider = cameraProvider
                    preview = useCase
                    useCase.setSurfaceProvider(previewView.surfaceProvider)
                    val boundCamera = cameraProvider.bindToLifecycle(owner, camera, useCase)
                    previewView.visibility = VISIBLE
                    observeCamera(owner, token, boundCamera.cameraInfo.cameraState, hadRetiredStream)
                } catch (_: Exception) {
                    if (stop()) status("camera_unavailable")
                }
            }, ContextCompat.getMainExecutor(context))
        } catch (_: Exception) {
            if (isCurrent(token) && stop()) status("camera_unavailable")
        }
    }

    private fun isCurrent(token: Long) = requested && !disposed &&
        isAttachedToWindow && lease.isCurrent(token)

    private fun observeCamera(
        owner: LifecycleOwner, token: Long, states: LiveData<CameraState>, hadRetiredStream: Boolean,
    ) {
        // Opening/pending without an error is not proof of frames and may keep waiting.
        val errors = Observer<CameraState> { state ->
            if (isCurrent(token) && state.error != null) {
                if (stop()) status("camera_unavailable")
            }
        }
        cameraStates = states
        cameraObserver = errors
        states.observe(owner, errors)
        if (!isCurrent(token)) return // observe may synchronously report an existing error.

        var streamed = false
        // LiveData may still cache STREAMING from a retired binding. Require its
        // IDLE transition before accepting a new session's streaming signal.
        var waitingForIdle = hadRetiredStream
        val streams = Observer<PreviewView.StreamState> { state ->
            if (isCurrent(token)) {
                when (state) {
                    PreviewView.StreamState.STREAMING -> if (!waitingForIdle && !streamed) {
                        streamed = true
                        status("preview_only")
                    }
                    PreviewView.StreamState.IDLE -> {
                        waitingForIdle = false
                        // Initial IDLE is expected. Once frames stop, stop claiming
                        // a live preview; only an explicit user Start can retry.
                        if (streamed && stop()) status("stopped")
                    }
                    else -> Unit
                }
            }
        }
        streamObserver = streams
        previewView.previewStreamState.observe(owner, streams)
    }

    private fun stop(): Boolean {
        requested = false
        lease.stop()
        previewView.visibility = INVISIBLE
        val useCase = preview
        val cameraProvider = provider
        val streams = streamObserver
        val states = cameraStates
        val errors = cameraObserver
        preview = null
        provider = null
        streamObserver = null
        cameraStates = null
        cameraObserver = null
        var failed = false
        // Every owned action gets one attempt, even if a preceding action throws.
        // No global unbind, blind retry, retained exception, or content logging.
        fun release(action: () -> Unit) {
            try { action() } catch (_: RuntimeException) { failed = true }
        }
        if (streams != null) release { previewView.previewStreamState.removeObserver(streams) }
        if (errors != null) release { states?.removeObserver(errors) }
        if (useCase != null) {
            release { useCase.setSurfaceProvider(null) }
            release { cameraProvider?.unbind(useCase) }
        }
        if (failed) {
            cleanupFailed = true
            cameraActivity?.recordCameraCleanupFailure()
        }
        if (cleanupFailed) {
            // Disposal can suppress the view event; the Activity fault latch is
            // authoritative and also rejects/explains a fresh view's next Start.
            try { reportCleanupFailure() } catch (_: RuntimeException) { /* Keep the native latch. */ }
        }
        // No app-owned frame/landmark/transcript buffers exist in this milestone.
        return !cleanupFailed
    }

    private fun registerWithActivity() {
        val activity = reactContext.currentActivity as? MainActivity
        if (cameraActivity === activity) return
        cameraActivity?.unregisterCameraView(this)
        cameraActivity = activity
        activity?.registerCameraView(this)
    }

    internal fun cancelFromNativeBack() { if (stop()) status("stopped") }
    internal fun reportCleanupFailure() { status("camera_cleanup_failed") }

    fun dispose() {
        disposed = true
        cancelPreviewLayout()
        try { stop() }
        finally {
            try {
                if (observing) reactContext.removeLifecycleEventListener(this)
            } finally {
                observing = false
                cameraActivity?.unregisterCameraView(this)
                cameraActivity = null
            }
        }
    }

    override fun requestLayout() {
        super.requestLayout()
        // The React parent intentionally ignores a child's native layout request.
        // CameraX can add or resize its preview child after Fabric's layout pass.
        if (!isAttachedToWindow || disposed || width <= 0 || height <= 0 || previewLayoutPosted) return
        previewLayoutPosted = true
        if (!post(layoutPreview)) previewLayoutPosted = false
    }

    private fun cancelPreviewLayout() {
        removeCallbacks(layoutPreview)
        previewLayoutPosted = false
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        if (disposed) return
        registerWithActivity()
        if (!observing) reactContext.addLifecycleEventListener(this)
        observing = true
        if (requested) start()
    }

    override fun onDetachedFromWindow() {
        try { stop() }
        finally {
            try { if (observing) reactContext.removeLifecycleEventListener(this) }
            finally {
                observing = false
                cancelPreviewLayout()
                super.onDetachedFromWindow()
            }
        }
    }

    override fun onHostPause() { if (stop()) status("stopped") }
    override fun onHostDestroy() { dispose() }
    override fun onHostResume() = Unit // User must explicitly start again.

    private fun status(value: String) {
        if (id == NO_ID || disposed || !reactContext.hasActiveReactInstance()) return
        UIManagerHelper.getEventDispatcherForReactTag(reactContext, id)?.dispatchEvent(
            CameraStatusEvent(UIManagerHelper.getSurfaceId(this), id, value))
    }
}

private class CameraStatusEvent(surfaceId: Int, viewTag: Int, private val value: String) :
    Event<CameraStatusEvent>(surfaceId, viewTag) {
    override fun getEventName() = "topStatus"
    override fun canCoalesce() = false
    override fun getEventData(): WritableMap = Arguments.createMap().apply { putString("status", value) }
}
