package com.marvis.agentlens.service

import android.graphics.Rect
import android.os.Handler
import android.os.Looper
import android.util.Log
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.concurrent.TimeUnit

class AgentWebSocketClient(
    private val serverUrl: String,
    private val listener: CommandListener,
) {
    companion object {
        private const val TAG = "AgentWSClient"
        private const val MAX_RECONNECT_DELAY_MS = 30_000L
    }

    data class UIElementData(
        val index: Int,
        val text: String,
        val subtext: String,
        val clickable: Boolean,
        val bounds: Rect?,
    )

    interface CommandListener {
        fun onShowApp(text: String, interactive: Boolean)
        fun onShowElement(text: String, bounds: Rect, interactive: Boolean)
        fun onShowGenUI(text: String, html: String, interactive: Boolean)
        fun onShowParsedUI(text: String, elements: List<UIElementData>, interactive: Boolean)
        fun onSpeak(text: String)
        fun onAsk(text: String)
        fun onLaunchApp(packageName: String)
        fun onLaunchAppResult(packageName: String, success: Boolean)
        fun onAgentState(state: String, detail: String)
        fun onAgentMessage(role: String, text: String)
        fun onDismiss()
        fun onConnected()
        fun onDisconnected()
    }

    private val mainHandler = Handler(Looper.getMainLooper())
    // OkHttp's pingInterval is BOTH the ping send interval AND the pong
    // timeout — if no pong arrives within `pingInterval` of a ping going
    // out, OkHttp tears the WebSocket down as a "Connection failed". The
    // Python backend's event loop responds to pings reliably, but during
    // an LLM call + screenshot encode it can briefly stall by a few
    // hundred ms; combined with a 5 s window that was enough to flap the
    // socket and drop in-flight overlay commands. 30 s is generous
    // enough to absorb LLM/agent jitter but still detects a real dead
    // connection within a step.
    private val client = OkHttpClient.Builder()
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .pingInterval(30, TimeUnit.SECONDS)
        .build()

    private var webSocket: WebSocket? = null
    private var reconnectDelay = 1000L
    private var shouldReconnect = true

    fun connect() {
        shouldReconnect = true
        doConnect()
    }

    private fun doConnect() {
        val request = Request.Builder().url(serverUrl).build()
        webSocket = client.newWebSocket(request, object : WebSocketListener() {

            override fun onOpen(webSocket: WebSocket, response: Response) {
                Log.i(TAG, "Connected to $serverUrl")
                reconnectDelay = 1000L
                mainHandler.post { listener.onConnected() }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                Log.d(TAG, "Received: $text")
                try {
                    val msg = JSONObject(text)
                    dispatch(msg)
                } catch (e: Exception) {
                    Log.e(TAG, "Failed to parse message", e)
                }
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                Log.i(TAG, "Connection closed: $reason")
                mainHandler.post { listener.onDisconnected() }
                scheduleReconnect()
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Log.w(TAG, "Connection failed: ${t.message}")
                mainHandler.post { listener.onDisconnected() }
                scheduleReconnect()
            }
        })
    }

    private fun dispatch(msg: JSONObject) {
        val type = msg.optString("type", "")
        val text = msg.optString("text", "")
        val interactive = msg.optBoolean("interactive", false)

        mainHandler.post {
            when (type) {
                "show_app" -> listener.onShowApp(text, interactive)
                "show_element" -> {
                    val bounds = msg.optJSONObject("bounds")
                    if (bounds != null) {
                        val rect = Rect(
                            bounds.optInt("x1"),
                            bounds.optInt("y1"),
                            bounds.optInt("x2"),
                            bounds.optInt("y2"),
                        )
                        listener.onShowElement(text, rect, interactive)
                    } else {
                        listener.onSpeak(text)
                    }
                }
                "show_genui" -> {
                    val html = msg.optString("html", "")
                    listener.onShowGenUI(text, html, interactive)
                }
                "show_partial_ui", "show_full_ui" -> {
                    val elementsArr = msg.optJSONArray("elements")
                    val elements = mutableListOf<UIElementData>()
                    if (elementsArr != null) {
                        for (i in 0 until elementsArr.length()) {
                            val e = elementsArr.getJSONObject(i)
                            val b = e.optJSONObject("bounds")
                            val rect = if (b != null) Rect(
                                b.optInt("x1"), b.optInt("y1"),
                                b.optInt("x2"), b.optInt("y2")
                            ) else null
                            elements.add(UIElementData(
                                index = e.optInt("index"),
                                text = e.optString("text", ""),
                                subtext = e.optString("subtext", ""),
                                clickable = e.optBoolean("clickable", false),
                                bounds = rect,
                            ))
                        }
                    }
                    listener.onShowParsedUI(text, elements, interactive)
                }
                "speak" -> listener.onSpeak(text)
                "ask" -> listener.onAsk(text)
                "launch_app" -> {
                    val packageName = msg.optString("package", "")
                    if (packageName.isNotEmpty()) listener.onLaunchApp(packageName)
                }
                "launch_app_result" -> {
                    val packageName = msg.optString("package", "")
                    val success = msg.optBoolean("success", false)
                    listener.onLaunchAppResult(packageName, success)
                }
                "agent_state" -> {
                    val state = msg.optString("state", "")
                    val detail = msg.optString("detail", "")
                    listener.onAgentState(state, detail)
                }
                "agent_message" -> {
                    val role = msg.optString("role", "agent")
                    val agentText = msg.optString("text", "")
                    listener.onAgentMessage(role, agentText)
                }
                "capture_screenshot" -> {
                    // Run on background thread to avoid blocking main thread during sleep
                    Thread {
                        val vdm = com.marvis.agentlens.service.ProjectionForegroundService.virtualDisplayManager.value
                        // Original surface-swap capture path that worked
                        // reliably for the DoorDash demo earlier.
                        val bitmap = vdm?.captureScreenshot()
                        val base64Str = if (bitmap != null) {
                            val stream = java.io.ByteArrayOutputStream()
                            bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 90, stream)
                            bitmap.recycle()
                            android.util.Base64.encodeToString(stream.toByteArray(), android.util.Base64.NO_WRAP)
                        } else ""
                        val resp = JSONObject().apply {
                            put("type", "screenshot")
                            put("data", base64Str)
                        }
                        webSocket?.send(resp.toString())
                        Log.d(TAG, "Sent screenshot (${base64Str.length} chars)")
                    }.start()
                }
                "get_ui_tree" -> {
                    val displayId = msg.optInt("display_id", -1)
                    val targetPackage = msg.optString("package", "").ifBlank { null }
                    val service = com.marvis.agentlens.accessibility.AgentLensAccessibilityService.instance.value
                    val xml = service?.dumpDisplayXml(displayId, targetPackage) ?: "<hierarchy rotation=\"0\"></hierarchy>"
                    val resp = JSONObject().apply {
                        put("type", "ui_tree")
                        put("xml", xml)
                    }
                    webSocket?.send(resp.toString())
                    Log.d(TAG, "Sent ui_tree (${xml.length} chars)")
                }
                "click_by_text" -> {
                    val displayId = msg.optInt("display_id", -1)
                    val clickText = msg.optString("text", "")
                    val targetPackage = msg.optString("package", "").ifBlank { null }
                    val service = com.marvis.agentlens.accessibility.AgentLensAccessibilityService.instance.value
                    val success = service?.clickByText(displayId, clickText, targetPackage) ?: false
                    val resp = JSONObject().apply {
                        put("type", "click_result")
                        put("text", clickText)
                        put("success", success)
                    }
                    webSocket?.send(resp.toString())
                    Log.i(TAG, "click_by_text '$clickText' -> $success")
                }
                "set_sf_display_id" -> {
                    val sfId = msg.optString("sf_display_id", "")
                    if (sfId.isNotEmpty()) {
                        Log.i(TAG, "Set SF display ID: $sfId")
                        com.marvis.agentlens.overlay.OverlayManager.staticSfDisplayId = sfId
                    }
                }
                "dismiss" -> listener.onDismiss()
                else -> Log.w(TAG, "Unknown command type: $type")
            }
        }
    }

    fun sendRegister(displayId: Int) {
        val msg = JSONObject().apply {
            put("type", "register")
            put("display_id", displayId)
        }
        webSocket?.send(msg.toString())
        Log.i(TAG, "Sent register: display_id=$displayId")
    }

    fun send(message: JSONObject) {
        webSocket?.send(message.toString())
    }

    fun sendTouch(action: Int, x: Float, y: Float) {
        val actionStr = when (action) {
            0 -> "down"   // MotionEvent.ACTION_DOWN
            2 -> "move"   // MotionEvent.ACTION_MOVE
            1 -> "up"     // MotionEvent.ACTION_UP
            else -> return
        }
        val msg = JSONObject().apply {
            put("type", "touch")
            put("action", actionStr)
            put("x", x.toDouble())
            put("y", y.toDouble())
        }
        webSocket?.send(msg.toString())
    }

    fun sendGenUIAction(actionJson: String) {
        val msg = JSONObject().apply {
            put("type", "genui_action")
            put("payload", actionJson)
        }
        webSocket?.send(msg.toString())
    }

    fun sendUserGoal(text: String) {
        val msg = JSONObject().apply {
            put("type", "user_goal")
            put("text", text)
        }
        webSocket?.send(msg.toString())
        Log.i(TAG, "Sent user_goal: ${text.take(80)}")
    }

    fun sendLaunchAppRequest(packageName: String, activityName: String) {
        val msg = JSONObject().apply {
            put("type", "launch_app_request")
            put("package", packageName)
            put("activity", activityName)
        }
        webSocket?.send(msg.toString())
        Log.i(TAG, "Sent launch_app_request: $packageName/$activityName")
    }

    fun sendAppLaunched(packageName: String, success: Boolean) {
        val msg = JSONObject().apply {
            put("type", "app_launched")
            put("package", packageName)
            put("success", success)
        }
        webSocket?.send(msg.toString())
    }

    fun sendUserInteraction(interactionType: String) {
        val msg = JSONObject().apply {
            put("type", "user_interaction")
            put("interaction_type", interactionType)
        }
        webSocket?.send(msg.toString())
    }

    private fun scheduleReconnect() {
        if (!shouldReconnect) return
        Log.i(TAG, "Reconnecting in ${reconnectDelay}ms...")
        mainHandler.postDelayed({
            if (shouldReconnect) doConnect()
        }, reconnectDelay)
        reconnectDelay = (reconnectDelay * 2).coerceAtMost(MAX_RECONNECT_DELAY_MS)
    }

    fun disconnect() {
        shouldReconnect = false
        mainHandler.removeCallbacksAndMessages(null)
        webSocket?.close(1000, "Client disconnecting")
        webSocket = null
    }
}
