package com.xmilo_at_your_side.sign_to_speak

import android.Manifest
import android.content.pm.PackageManager
import android.widget.FrameLayout
import androidx.camera.core.CameraSelector
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
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
    private var requested = false
    private var observing = false
    private var disposed = false
    private var cameraActivity: MainActivity? = null

    init {
        registerWithActivity()
        // TextureView remains inside the normal secure Activity composition.
        previewView.implementationMode = PreviewView.ImplementationMode.COMPATIBLE
        addView(previewView, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT))
    }

    fun setActive(active: Boolean) {
        if (!active) { stop(); return }
        if (disposed || requested) return
        registerWithActivity()
        if ((cameraActivity?.cameraCleanupFailures ?: 0) > 0) {
            reportCleanupFailure(); return
        }
        requested = true
        if (isAttachedToWindow) start()
    }

    private fun start() {
        val owner = reactContext.currentActivity as? LifecycleOwner
        if (owner == null || !owner.lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)) {
            stop(); status("stopped"); return
        }
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            stop(); status("permission_denied"); return
        }
        val token = lease.begin(true, true)
        val future = ProcessCameraProvider.getInstance(context)
        future.addListener({
            if (!requested || disposed || !isAttachedToWindow || !lease.isCurrent(token) ||
                !owner.lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)) return@addListener
            try {
                val cameraProvider = future.get()
                val camera = CameraSelector.DEFAULT_FRONT_CAMERA
                if (!cameraProvider.hasCamera(camera)) {
                    stop(); status("front_camera_unavailable"); return@addListener
                }
                val useCase = Preview.Builder().build()
                useCase.setSurfaceProvider(previewView.surfaceProvider)
                provider = cameraProvider
                preview = useCase
                cameraProvider.bindToLifecycle(owner, camera, useCase)
                previewView.visibility = VISIBLE
                status("preview_only")
            } catch (_: Exception) {
                stop(); status("camera_unavailable")
            }
        }, ContextCompat.getMainExecutor(context))
    }

    private fun stop() {
        requested = false
        lease.stop()
        previewView.visibility = INVISIBLE
        val useCase = preview
        val cameraProvider = provider
        preview = null
        provider = null
        if (useCase != null) {
            try { useCase.setSurfaceProvider(null) }
            finally { cameraProvider?.unbind(useCase) }
        }
        // No app-owned frame/landmark/transcript buffers exist in this milestone.
    }

    private fun registerWithActivity() {
        val activity = reactContext.currentActivity as? MainActivity
        if (cameraActivity === activity) return
        cameraActivity?.unregisterCameraView(this)
        cameraActivity = activity
        activity?.registerCameraView(this)
    }

    internal fun cancelFromNativeBack() { stop(); status("stopped") }
    internal fun reportCleanupFailure() { status("camera_cleanup_failed") }

    fun dispose() {
        disposed = true
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

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        if (disposed) return
        registerWithActivity()
        if (!observing) reactContext.addLifecycleEventListener(this)
        observing = true
        if (requested) start()
    }

    override fun onDetachedFromWindow() {
        stop()
        if (observing) reactContext.removeLifecycleEventListener(this)
        observing = false
        super.onDetachedFromWindow()
    }

    override fun onHostPause() { stop(); status("stopped") }
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
