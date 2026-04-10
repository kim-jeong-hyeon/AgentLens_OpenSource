package com.marvis.agentlens.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.Rect
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.IBinder
import android.util.Log
import com.marvis.agentlens.R
import com.marvis.agentlens.apps.AppLauncher
import com.marvis.agentlens.overlay.OverlayManager
import com.marvis.agentlens.tts.TtsManager
import com.marvis.agentlens.virtualdisplay.VirtualDisplayManager
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

class ProjectionForegroundService : Service() {

    companion object {
        private const val TAG = "ProjectionService"
        private const val CHANNEL_ID = "agentlens_projection_channel"
        private const val NOTIFICATION_ID = 1001
        const val EXTRA_RESULT_CODE = "result_code"
        const val EXTRA_RESULT_DATA = "result_data"
        const val EXTRA_SERVER_URL = "server_url"

        private val _virtualDisplayManager = MutableStateFlow<VirtualDisplayManager?>(null)
        val virtualDisplayManager: StateFlow<VirtualDisplayManager?> = _virtualDisplayManager.asStateFlow()

        private val _isConnected = MutableStateFlow(false)
        val isConnected: StateFlow<Boolean> = _isConnected.asStateFlow()

        private var _wsClient: AgentWebSocketClient? = null
        val wsClient: AgentWebSocketClient? get() = _wsClient

        data class AgentChatState(val state: String, val detail: String)
        data class AgentChatMessage(val role: String, val text: String)

        private val _agentState = MutableStateFlow(AgentChatState("idle", ""))
        val agentState: StateFlow<AgentChatState> = _agentState.asStateFlow()

        private val _chatMessages = MutableStateFlow<List<AgentChatMessage>>(emptyList())
        val chatMessages: StateFlow<List<AgentChatMessage>> = _chatMessages.asStateFlow()

        fun start(context: Context, resultCode: Int, resultData: Intent, serverUrl: String?) {
            val intent = Intent(context, ProjectionForegroundService::class.java).apply {
                putExtra(EXTRA_RESULT_CODE, resultCode)
                putExtra(EXTRA_RESULT_DATA, resultData)
                if (serverUrl != null) putExtra(EXTRA_SERVER_URL, serverUrl)
            }
            context.startForegroundService(intent)
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, ProjectionForegroundService::class.java))
        }
    }

    private var mediaProjection: MediaProjection? = null
    private var wsClient: AgentWebSocketClient?
        get() = _wsClient
        set(value) { _wsClient = value }
    private var overlayManager: OverlayManager? = null
    private var ttsManager: TtsManager? = null

    private val projectionCallback = object : MediaProjection.Callback() {
        override fun onStop() {
            Log.i(TAG, "MediaProjection stopped by system")
            overlayManager?.dismiss()
            overlayManager?.hideFab()
            releaseVirtualDisplay()
            stopSelf()
        }
    }

    private val commandListener = object : AgentWebSocketClient.CommandListener {
        override fun onShowApp(text: String, interactive: Boolean) {
            Log.i(TAG, "Command: show_app, text=$text, interactive=$interactive")
            ttsManager?.speak(text)
            val vdm = _virtualDisplayManager.value ?: return
            overlayManager?.showAppOverlay(vdm, interactive, text)
        }

        override fun onShowElement(text: String, bounds: Rect, interactive: Boolean) {
            Log.i(TAG, "Command: show_element, text=$text, bounds=$bounds, interactive=$interactive")
            ttsManager?.speak(text)
            val vdm = _virtualDisplayManager.value ?: return
            overlayManager?.showElementOverlay(vdm, bounds, interactive, text)
        }

        override fun onShowGenUI(text: String, html: String, interactive: Boolean) {
            Log.i(TAG, "Command: show_genui, text=$text, interactive=$interactive")
            ttsManager?.speak(text)
            overlayManager?.showGenUIOverlay(html, interactive, text)
        }

        override fun onShowParsedUI(text: String, elements: List<AgentWebSocketClient.UIElementData>, interactive: Boolean) {
            Log.i(TAG, "Command: show_parsed_ui, text=$text, ${elements.size} elements, interactive=$interactive")
            ttsManager?.speak(text)
            overlayManager?.showParsedUIOverlay(elements, interactive, text)
        }

        override fun onSpeak(text: String) {
            Log.i(TAG, "Command: speak, text=$text")
            ttsManager?.speak(text)
        }

        override fun onAsk(text: String) {
            Log.i(TAG, "Command: ask, text=$text")
            ttsManager?.speak(text)
            overlayManager?.showChatOverlay(text)
        }

        override fun onLaunchApp(packageName: String) {
            Log.i(TAG, "Command: launch_app, package=$packageName")
            val displayId = _virtualDisplayManager.value?.displayId ?: return
            val apps = AppLauncher.getInstalledApps(this@ProjectionForegroundService)
            val appInfo = apps.find { it.packageName == packageName }
            if (appInfo != null) {
                val success = AppLauncher.launchOnDisplay(this@ProjectionForegroundService, appInfo, displayId)
                Log.i(TAG, "Launch $packageName on display $displayId: $success")
                wsClient?.sendAppLaunched(packageName, success)
            } else {
                Log.w(TAG, "App not found: $packageName")
                wsClient?.sendAppLaunched(packageName, false)
            }
        }

        override fun onLaunchAppResult(packageName: String, success: Boolean) {
            Log.i(TAG, "Launch result: $packageName success=$success")
        }

        override fun onAgentState(state: String, detail: String) {
            Log.i(TAG, "Agent state: $state ($detail)")
            _agentState.value = AgentChatState(state, detail)
            // Toggle FAB vs processing spinner based on the agent state.
            when (state) {
                "thinking", "executing", "waiting" -> {
                    overlayManager?.showProcessing()
                }
                "idle", "done", "error" -> {
                    overlayManager?.hideProcessing()
                    overlayManager?.showFab()
                }
            }
        }

        override fun onAgentMessage(role: String, text: String) {
            Log.i(TAG, "Agent message [$role]: ${text.take(80)}")
            val current = _chatMessages.value
            _chatMessages.value = current + AgentChatMessage(role, text)
        }

        override fun onDismiss() {
            Log.i(TAG, "Command: dismiss")
            overlayManager?.dismiss()
        }

        override fun onConnected() {
            _isConnected.value = true
            val displayId = _virtualDisplayManager.value?.displayId ?: -1
            if (displayId >= 0) {
                wsClient?.sendRegister(displayId)
            }
        }

        override fun onDisconnected() {
            _isConnected.value = false
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent == null) {
            stopSelf()
            return START_NOT_STICKY
        }

        val resultCode = intent.getIntExtra(EXTRA_RESULT_CODE, -1)
        @Suppress("DEPRECATION")
        val resultData = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            intent.getParcelableExtra(EXTRA_RESULT_DATA, Intent::class.java)
        } else {
            intent.getParcelableExtra(EXTRA_RESULT_DATA)
        }

        if (resultData == null) {
            Log.e(TAG, "No result data provided")
            stopSelf()
            return START_NOT_STICKY
        }

        // Release previous virtual display if service is restarted
        releaseVirtualDisplay()

        createNotificationChannel()
        val notification = buildNotification()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }

        val projectionManager = getSystemService(MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        mediaProjection = projectionManager.getMediaProjection(resultCode, resultData)

        if (mediaProjection == null) {
            Log.e(TAG, "Failed to get MediaProjection")
            stopSelf()
            return START_NOT_STICKY
        }

        mediaProjection!!.registerCallback(projectionCallback, null)

        val manager = VirtualDisplayManager(this, mediaProjection)
        manager.create()
        _virtualDisplayManager.value = manager

        Log.i(TAG, "Service started, display ID: ${manager.displayId}")

        // Initialize TTS and overlay. Tear down any overlay windows that
        // a previous service instance leaked (e.g. force-stopped, crashed,
        // or test scripts that exited before sending dismiss). Without
        // this, every Stop/Start Display cycle stacks another popup on the
        // user's main screen.
        OverlayManager.removeAllTrackedOverlays(this)
        ttsManager = TtsManager(this)
        overlayManager = OverlayManager(this).apply {
            touchCallback = { action, vdX, vdY ->
                wsClient?.sendTouch(action, vdX, vdY)
            }
            genUIActionCallback = { actionJson ->
                wsClient?.sendGenUIAction(actionJson)
            }
            onDismissed = {
                wsClient?.sendUserInteraction("dismiss")
            }
            goalSubmitCallback = { text ->
                Log.i(TAG, "User submitted goal: $text")
                wsClient?.sendUserGoal(text)
            }
            // Show the persistent chat FAB so the user can call the agent
            // from anywhere on the device.
            showFab()
        }

        // Connect to Python backend via WebSocket. Tear down any previous
        // client first — onStartCommand can fire more than once per service
        // instance (Start Display tapped twice, system redeliver, etc.) and
        // each ghost OkHttp WebSocket keeps its own reconnect loop alive,
        // racing with the live one and causing the server to drop the
        // currently-active connection mid-step.
        wsClient?.disconnect()
        wsClient = null
        val serverUrl = intent.getStringExtra(EXTRA_SERVER_URL)
        if (!serverUrl.isNullOrBlank()) {
            wsClient = AgentWebSocketClient(serverUrl, commandListener)
            wsClient?.connect()
            Log.i(TAG, "WebSocket client connecting to $serverUrl")
        }

        return START_NOT_STICKY
    }

    override fun onDestroy() {
        wsClient?.disconnect()
        wsClient = null
        overlayManager?.dismiss()
        overlayManager?.hideFab()
        overlayManager = null
        ttsManager?.shutdown()
        ttsManager = null
        _isConnected.value = false
        releaseVirtualDisplay()
        Log.i(TAG, "Service destroyed")
        super.onDestroy()
    }

    private fun releaseVirtualDisplay() {
        _virtualDisplayManager.value?.release()
        _virtualDisplayManager.value = null
        mediaProjection?.unregisterCallback(projectionCallback)
        mediaProjection = null
    }

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.projection_notification_channel),
            NotificationManager.IMPORTANCE_LOW
        )
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(channel)
    }

    private fun buildNotification(): Notification {
        return Notification.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.projection_notification_title))
            .setContentText(getString(R.string.projection_notification_text))
            .setSmallIcon(android.R.drawable.ic_menu_camera)
            .setOngoing(true)
            .build()
    }
}
