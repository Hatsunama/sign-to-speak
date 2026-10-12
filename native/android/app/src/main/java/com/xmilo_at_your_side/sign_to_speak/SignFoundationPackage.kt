package com.xmilo_at_your_side.sign_to_speak

import com.facebook.react.ReactPackage
import com.facebook.react.bridge.NativeModule
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.uimanager.SimpleViewManager
import com.facebook.react.uimanager.ThemedReactContext
import com.facebook.react.uimanager.ViewManager
import com.facebook.react.uimanager.annotations.ReactProp

class SignFoundationPackage : ReactPackage {
    override fun createNativeModules(context: ReactApplicationContext): List<NativeModule> = emptyList()
    override fun createViewManagers(context: ReactApplicationContext): List<ViewManager<*, *>> =
        listOf(HatsuViewManager(), CameraViewManager())
}

class HatsuViewManager : SimpleViewManager<HatsuBackgroundView>() {
    override fun getName() = "HatsuBackground"
    override fun createViewInstance(context: ThemedReactContext) = HatsuBackgroundView(context)
    @ReactProp(name = "active")
    fun setActive(view: HatsuBackgroundView, active: Boolean) = view.setAnimating(active)
    override fun onDropViewInstance(view: HatsuBackgroundView) {
        view.setAnimating(false)
        super.onDropViewInstance(view)
    }
}

class CameraViewManager : SimpleViewManager<PrivateCameraView>() {
    override fun getName() = "PrivateCameraPreview"
    override fun createViewInstance(context: ThemedReactContext) = PrivateCameraView(context)
    @ReactProp(name = "active")
    fun setActive(view: PrivateCameraView, active: Boolean) = view.setActive(active)
    override fun getExportedCustomDirectEventTypeConstants(): MutableMap<String, Any> =
        mutableMapOf("topStatus" to mapOf("registrationName" to "onStatus"))
    override fun onDropViewInstance(view: PrivateCameraView) {
        try { view.dispose() }
        finally { super.onDropViewInstance(view) }
    }
}
