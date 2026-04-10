package com.marvis.agentlens

import android.app.Application
import android.graphics.Bitmap
import android.graphics.Matrix
import android.util.Log
import android.view.TextureView
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.marvis.agentlens.accessibility.AgentLensAccessibilityService
import com.marvis.agentlens.accessibility.NodeInfo
import com.marvis.agentlens.apps.AppInfo
import com.marvis.agentlens.apps.AppLauncher
import com.marvis.agentlens.screenshot.ScreenshotManager
import com.marvis.agentlens.service.ProjectionForegroundService
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class MainViewModel(application: Application) : AndroidViewModel(application) {

    companion object {
        private const val TAG = "MainViewModel"
    }

    private val _screenshotBitmap = MutableStateFlow<Bitmap?>(null)
    val screenshotBitmap: StateFlow<Bitmap?> = _screenshotBitmap.asStateFlow()

    private val _nodeTree = MutableStateFlow<List<NodeInfo>>(emptyList())
    val nodeTree: StateFlow<List<NodeInfo>> = _nodeTree.asStateFlow()

    private val _installedApps = MutableStateFlow<List<AppInfo>>(emptyList())
    val installedApps: StateFlow<List<AppInfo>> = _installedApps.asStateFlow()

    private val _launchedApp = MutableStateFlow<AppInfo?>(null)
    val launchedApp: StateFlow<AppInfo?> = _launchedApp.asStateFlow()

    private val _statusMessage = MutableStateFlow("")
    val statusMessage: StateFlow<String> = _statusMessage.asStateFlow()

    data class CropBounds(val x1: Int, val y1: Int, val x2: Int, val y2: Int)

    private val _cropBounds = MutableStateFlow<CropBounds?>(null)
    val cropBounds: StateFlow<CropBounds?> = _cropBounds.asStateFlow()

    val virtualDisplayManager = ProjectionForegroundService.virtualDisplayManager

    val displayId: StateFlow<Int> = virtualDisplayManager.map { it?.displayId ?: -1 }
        .stateIn(viewModelScope, SharingStarted.Eagerly, -1)

    val isProjectionActive: StateFlow<Boolean> = virtualDisplayManager.map { it != null }
        .stateIn(viewModelScope, SharingStarted.Eagerly, false)

    val isAccessibilityEnabled: StateFlow<Boolean> = AgentLensAccessibilityService.instance.map { it != null }
        .stateIn(viewModelScope, SharingStarted.Eagerly, false)

    // Reference to the live preview TextureView (set by the Compose UI)
    var previewTextureView: TextureView? = null

    fun updatePreviewTextureView(tv: TextureView?) {
        previewTextureView = tv
        if (tv != null) applyCropTransform()
    }

    fun setCropBounds(x1: Int, y1: Int, x2: Int, y2: Int) {
        val manager = virtualDisplayManager.value ?: return
        val vdW = manager.width
        val vdH = manager.height
        val cx1 = x1.coerceIn(0, vdW - 1)
        val cy1 = y1.coerceIn(0, vdH - 1)
        val cx2 = x2.coerceIn(cx1 + 1, vdW)
        val cy2 = y2.coerceIn(cy1 + 1, vdH)
        _cropBounds.value = CropBounds(cx1, cy1, cx2, cy2)
        applyCropTransform()
    }

    fun resetCrop() {
        _cropBounds.value = null
        applyCropTransform()
    }

    private fun applyCropTransform() {
        val tv = previewTextureView ?: return
        val bounds = _cropBounds.value

        if (bounds == null) {
            tv.setTransform(null)
            tv.clipBounds = null
            return
        }

        val manager = virtualDisplayManager.value ?: return
        val vdW = manager.width.toFloat()
        val vdH = manager.height.toFloat()
        val viewW = tv.width.toFloat()
        val viewH = tv.height.toFloat()
        if (viewW <= 0 || viewH <= 0) return

        val cropW = (bounds.x2 - bounds.x1).toFloat()
        val cropH = (bounds.y2 - bounds.y1).toFloat()

        // Uniform scale to maintain aspect ratio
        val scale = minOf(vdW / cropW, vdH / cropH)

        // Crop region in view coords (before transform)
        val cropLeft = bounds.x1 / vdW * viewW
        val cropTop = bounds.y1 / vdH * viewH

        // Crop region size in view coords after scaling
        val scaledCropW = cropW / vdW * viewW * scale
        val scaledCropH = cropH / vdH * viewH * scale

        // Center the crop region in the view
        val offsetX = (viewW - scaledCropW) / 2f
        val offsetY = (viewH - scaledCropH) / 2f

        val tX = -cropLeft * scale + offsetX
        val tY = -cropTop * scale + offsetY

        val matrix = Matrix()
        matrix.setScale(scale, scale)
        matrix.postTranslate(tX, tY)
        tv.setTransform(matrix)

        // Clip to only the crop output rectangle
        tv.clipBounds = android.graphics.Rect(
            offsetX.toInt(),
            offsetY.toInt(),
            (offsetX + scaledCropW).toInt(),
            (offsetY + scaledCropH).toInt()
        )
    }

    fun loadInstalledApps() {
        viewModelScope.launch(Dispatchers.IO) {
            val apps = AppLauncher.getInstalledApps(getApplication())
            _installedApps.value = apps
            Log.i(TAG, "Loaded ${apps.size} installed apps")
        }
    }

    fun launchApp(appInfo: AppInfo) {
        val did = displayId.value
        if (did < 0) {
            _statusMessage.value = "Virtual display not active"
            return
        }

        // Try launching via Python backend (ADB) over WebSocket
        val ws = ProjectionForegroundService.wsClient
        val connected = ProjectionForegroundService.isConnected.value
        if (ws != null && connected) {
            ws.sendLaunchAppRequest(appInfo.packageName, appInfo.activityName)
            _launchedApp.value = appInfo
            _statusMessage.value = "Launching ${appInfo.appName} via backend..."
            return
        }

        // Fallback: direct launch (may fail without permissions)
        viewModelScope.launch(Dispatchers.IO) {
            val success = AppLauncher.launchOnDisplay(getApplication(), appInfo, did)
            if (success) {
                _launchedApp.value = appInfo
                _statusMessage.value = "Launched ${appInfo.appName} on display $did"
            } else {
                _statusMessage.value = "Failed to launch ${appInfo.appName}"
            }
        }
    }

    fun captureScreenshot() {
        val manager = virtualDisplayManager.value
        if (manager == null) {
            _statusMessage.value = "Virtual display not active"
            return
        }

        val tv = previewTextureView
        if (tv != null) {
            // Live preview is active — grab directly from TextureView
            val bitmap = tv.bitmap
            if (bitmap != null) {
                _screenshotBitmap.value = bitmap
                _statusMessage.value = "Screenshot captured (TextureView)"
                return
            }
        }

        run {
            // Headless mode — use ImageReader
            viewModelScope.launch(Dispatchers.IO) {
                val bitmap = manager.captureFromImageReader()
                if (bitmap != null) {
                    _screenshotBitmap.value = bitmap
                    _statusMessage.value = "Screenshot captured (ImageReader)"
                } else {
                    _statusMessage.value = "Screenshot failed (no image available)"
                }
            }
        }
    }

    fun saveScreenshot() {
        val bitmap = _screenshotBitmap.value ?: return
        viewModelScope.launch(Dispatchers.IO) {
            val filename = "agentlens_${System.currentTimeMillis()}.png"
            val uri = ScreenshotManager.saveBitmap(getApplication(), bitmap, filename)
            withContext(Dispatchers.Main) {
                _statusMessage.value = if (uri != null) "Saved: $filename" else "Save failed"
            }
        }
    }

    fun fetchNodeTree() {
        val did = displayId.value
        val service = AgentLensAccessibilityService.instance.value
        if (service == null) {
            _statusMessage.value = "Accessibility service not enabled"
            return
        }
        if (did < 0) {
            _statusMessage.value = "Virtual display not active"
            return
        }
        viewModelScope.launch(Dispatchers.Default) {
            val tree = service.getNodeTreeForDisplay(did, _launchedApp.value?.packageName)
            _nodeTree.value = tree
            val count = tree.sumOf { it.flatten().size }
            _statusMessage.value = "Node tree: $count nodes"
        }
    }

    fun clickByText(text: String) {
        val did = displayId.value
        val service = AgentLensAccessibilityService.instance.value ?: return
        viewModelScope.launch(Dispatchers.Default) {
            val success = service.clickByText(did, text, _launchedApp.value?.packageName)
            _statusMessage.value = if (success) "Clicked: $text" else "Click failed: $text"
        }
    }

    fun clickByViewId(viewId: String) {
        val did = displayId.value
        val service = AgentLensAccessibilityService.instance.value ?: return
        viewModelScope.launch(Dispatchers.Default) {
            val success = service.clickByViewId(did, viewId, _launchedApp.value?.packageName)
            _statusMessage.value = if (success) "Clicked: $viewId" else "Click failed: $viewId"
        }
    }
}
