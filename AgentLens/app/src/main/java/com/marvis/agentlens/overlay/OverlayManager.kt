package com.marvis.agentlens.overlay

import android.content.Context
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.PixelFormat
import android.graphics.Rect
import android.graphics.SurfaceTexture
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.util.Log
import android.view.GestureDetector
import android.view.Gravity
import android.view.MotionEvent
import android.view.Surface
import android.view.TextureView
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.ImageView
import android.annotation.SuppressLint
import android.webkit.JavascriptInterface
import android.webkit.WebView
import android.widget.LinearLayout
import android.widget.TextView
import com.marvis.agentlens.virtualdisplay.VirtualDisplayManager

class OverlayManager(private val context: Context) {

    companion object {
        private const val TAG = "OverlayManager"
        private const val OVERLAY_PADDING_DP = 16
        private const val CLOSE_BUTTON_SIZE_DP = 28
        private const val CORNER_RADIUS_DP = 20f
        private const val SWIPE_THRESHOLD = 200
        private const val HEADER_HEIGHT_DP = 48

        /** Static SF display ID — set from WebSocket handler. */
        @JvmStatic
        var staticSfDisplayId: String? = null

        // Process-wide registry of every TYPE_APPLICATION_OVERLAY view we
        // have added. Required so a freshly constructed OverlayManager (e.g.
        // after the foreground service is restarted, or after the user
        // toggles Stop/Start Display) can still tear down windows that the
        // previous instance leaked. Without this, every restart leaks one
        // overlay window and they pile up on the user's main display.
        private val allOverlayViews = mutableSetOf<View>()

        /**
         * Force-remove every overlay window this process has tracked.
         * Safe to call from any [OverlayManager] instance — even one that
         * did not add the views originally — because Android's
         * WindowManager is process-scoped and will accept removal of any
         * view it currently owns.
         */
        @JvmStatic
        fun removeAllTrackedOverlays(context: Context) {
            if (allOverlayViews.isEmpty()) return
            val wm = context.getSystemService(Context.WINDOW_SERVICE) as WindowManager
            val snapshot = allOverlayViews.toList()
            allOverlayViews.clear()
            for (v in snapshot) {
                try {
                    wm.removeView(v)
                } catch (e: Exception) {
                    Log.w(TAG, "removeAllTrackedOverlays: removeView failed for $v", e)
                }
            }
            Log.i(TAG, "removeAllTrackedOverlays: cleared ${snapshot.size} leftover overlays")
        }
    }

    private val windowManager = context.getSystemService(Context.WINDOW_SERVICE) as WindowManager
    private val mainHandler = android.os.Handler(android.os.Looper.getMainLooper())
    private var overlayView: View? = null
    private var overlayTextureView: TextureView? = null
    private var overlayImageView: android.widget.ImageView? = null
    private var refreshRunnable: Runnable? = null
    private var currentVdManager: VirtualDisplayManager? = null
    // Hidden TextureView attached to VD for bitmap capture
    private var captureTextureView: TextureView? = null
    private var captureContainer: FrameLayout? = null
    // Persistent floating action button (always visible while service is running).
    private var fabView: View? = null
    // Spinning indicator shown while the agent is processing.
    private var spinnerView: View? = null

    // Interactive touch support
    var touchCallback: ((action: Int, vdX: Float, vdY: Float) -> Unit)? = null
    var genUIActionCallback: ((actionJson: String) -> Unit)? = null
    var onDismissed: (() -> Unit)? = null
    var goalSubmitCallback: ((text: String) -> Unit)? = null
    private var isInteractive: Boolean = false
    private var currentTransformMatrix: Matrix? = null
    private var currentVdWidth: Int = 0
    private var currentVdHeight: Int = 0
    private var currentMessage: String = ""

    val isShowing: Boolean get() = overlayView != null

    /**
     * Show a draggable floating action button that opens the chat overlay
     * when tapped. Persistent — survives [dismiss] of other overlays.
     */
    fun showFab() {
        if (fabView != null) return  // already showing

        val density = context.resources.displayMetrics.density
        val sizePx = (56 * density).toInt()

        val fab = TextView(context).apply {
            text = "AI"
            setTextColor(Color.WHITE)
            textSize = 16f
            typeface = Typeface.DEFAULT_BOLD
            gravity = Gravity.CENTER
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#6366F1"))
                shape = GradientDrawable.OVAL
            }
            elevation = 12 * density
            isClickable = true
            isFocusable = true
        }

        val params = WindowManager.LayoutParams(
            sizePx, sizePx,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.BOTTOM or Gravity.END
            x = (16 * density).toInt()
            y = (96 * density).toInt()
        }

        // Make the FAB draggable so users can move it out of the way.
        var initialX = 0
        var initialY = 0
        var touchStartX = 0f
        var touchStartY = 0f
        var movedDuringTouch = false
        fab.setOnTouchListener { _, event ->
            when (event.action) {
                MotionEvent.ACTION_DOWN -> {
                    initialX = params.x
                    initialY = params.y
                    touchStartX = event.rawX
                    touchStartY = event.rawY
                    movedDuringTouch = false
                    true
                }
                MotionEvent.ACTION_MOVE -> {
                    val dx = (event.rawX - touchStartX).toInt()
                    val dy = (event.rawY - touchStartY).toInt()
                    if (Math.abs(dx) > 16 || Math.abs(dy) > 16) {
                        movedDuringTouch = true
                    }
                    params.x = initialX - dx  // gravity END means x grows leftward
                    params.y = initialY - dy
                    try { windowManager.updateViewLayout(fab, params) } catch (_: Exception) {}
                    true
                }
                MotionEvent.ACTION_UP -> {
                    if (!movedDuringTouch) {
                        // Treat as a click — open the chat overlay.
                        showChatOverlay()
                    }
                    true
                }
                else -> false
            }
        }

        try {
            windowManager.addView(fab, params)
            fabView = fab
            Log.i(TAG, "FAB shown")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to show FAB", e)
        }
    }

    fun hideFab() {
        val v = fabView ?: return
        try { windowManager.removeView(v) } catch (_: Exception) {}
        fabView = null
        Log.i(TAG, "FAB hidden")
    }

    /**
     * Show a translucent spinning indicator in place of the FAB while the
     * agent is processing. Hides the FAB so they don't overlap.
     */
    fun showProcessing() {
        if (spinnerView != null) return  // already showing
        hideFab()

        val density = context.resources.displayMetrics.density
        val sizePx = (56 * density).toInt()

        val spinner = android.widget.ProgressBar(context).apply {
            isIndeterminate = true
            // Tint the indeterminate drawable to match the FAB color.
            indeterminateTintList = android.content.res.ColorStateList.valueOf(
                Color.parseColor("#6366F1")
            )
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#33FFFFFF"))  // 20% white = translucent
                shape = GradientDrawable.OVAL
            }
            val pad = (12 * density).toInt()
            setPadding(pad, pad, pad, pad)
            elevation = 12 * density
        }

        val params = WindowManager.LayoutParams(
            sizePx, sizePx,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                    WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.BOTTOM or Gravity.END
            x = (16 * density).toInt()
            y = (96 * density).toInt()
        }

        try {
            windowManager.addView(spinner, params)
            spinnerView = spinner
            Log.i(TAG, "Processing spinner shown")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to show spinner", e)
        }
    }

    fun hideProcessing() {
        val v = spinnerView ?: return
        try { windowManager.removeView(v) } catch (_: Exception) {}
        spinnerView = null
        Log.i(TAG, "Processing spinner hidden")
    }

    /**
     * Modern AI-assistant style chat overlay. Slides up from the bottom as a
     * sheet, shows a greeting bubble, and exposes a text input row with mic
     * (visual only) + send buttons.
     */
    fun showChatOverlay(message: String = "How can I help you today?") {
        dismiss()
        currentMessage = message

        val density = context.resources.displayMetrics.density
        val cornerRadiusPx = (24 * density)

        // Outer container fills the screen so we can place a bottom sheet
        // anchored to the bottom edge with a translucent backdrop above.
        val rootContainer = FrameLayout(context).apply {
            setBackgroundColor(Color.parseColor("#66000000"))  // 40% black scrim
            isClickable = true
            isFocusable = true
            // Tap on backdrop dismisses the sheet (but not when tapping the sheet itself).
            setOnClickListener { dismiss() }
        }

        // The bottom sheet card.
        val sheet = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#FFFFFFFF"))
                cornerRadii = floatArrayOf(
                    cornerRadiusPx, cornerRadiusPx,  // top-left
                    cornerRadiusPx, cornerRadiusPx,  // top-right
                    0f, 0f,                          // bottom-right
                    0f, 0f,                          // bottom-left
                )
            }
            clipToOutline = true
            outlineProvider = object : android.view.ViewOutlineProvider() {
                override fun getOutline(view: View, outline: android.graphics.Outline) {
                    outline.setRoundRect(0, 0, view.width, view.height + cornerRadiusPx.toInt(), cornerRadiusPx)
                }
            }
            elevation = 24 * density
            setPadding(
                (20 * density).toInt(),
                (16 * density).toInt(),
                (20 * density).toInt(),
                (24 * density).toInt(),
            )
            // Swallow tap so we don't bubble up to the backdrop dismiss listener.
            isClickable = true
            isFocusable = true
            setOnClickListener { /* no-op */ }
        }

        // Drag handle (just visual)
        val handle = View(context).apply {
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#E0E0E0"))
                this.cornerRadius = (3 * density)
            }
            layoutParams = LinearLayout.LayoutParams((44 * density).toInt(), (4 * density).toInt()).apply {
                gravity = Gravity.CENTER_HORIZONTAL
                bottomMargin = (12 * density).toInt()
            }
        }
        sheet.addView(handle)

        // Header row: avatar + title + close button
        val header = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            )
        }

        val avatar = TextView(context).apply {
            text = "✨"
            textSize = 18f
            gravity = Gravity.CENTER
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#EEF2FF"))
                shape = GradientDrawable.OVAL
            }
            val avatarSize = (36 * density).toInt()
            layoutParams = LinearLayout.LayoutParams(avatarSize, avatarSize).apply {
                marginEnd = (10 * density).toInt()
            }
        }
        header.addView(avatar)

        val titleColumn = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                0,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                1f,
            )
        }
        titleColumn.addView(TextView(context).apply {
            text = "AgentLens Agent"
            setTextColor(Color.parseColor("#111827"))
            textSize = 16f
            typeface = Typeface.DEFAULT_BOLD
        })
        titleColumn.addView(TextView(context).apply {
            text = "Online · ready to help"
            setTextColor(Color.parseColor("#6B7280"))
            textSize = 11f
        })
        header.addView(titleColumn)

        val closeBtn = TextView(context).apply {
            text = "✕"
            textSize = 16f
            setTextColor(Color.parseColor("#9CA3AF"))
            gravity = Gravity.CENTER
            val s = (32 * density).toInt()
            layoutParams = LinearLayout.LayoutParams(s, s)
            isClickable = true
            isFocusable = true
            setOnClickListener { dismiss() }
        }
        header.addView(closeBtn)
        sheet.addView(header)

        // Greeting bubble (assistant message)
        val bubble = TextView(context).apply {
            text = message
            setTextColor(Color.parseColor("#1F2937"))
            textSize = 14f
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#F3F4F6"))
                this.cornerRadius = (16 * density)
            }
            setPadding(
                (14 * density).toInt(),
                (10 * density).toInt(),
                (14 * density).toInt(),
                (10 * density).toInt(),
            )
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply {
                topMargin = (16 * density).toInt()
                marginEnd = (40 * density).toInt()
            }
        }
        sheet.addView(bubble)

        // Suggestion chips row
        val chipsRow = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply {
                topMargin = (12 * density).toInt()
            }
        }
        val suggestions = listOf(
            "Set a timer",
            "Show me this month's calendar",
            "Summarize this week's schedule",
        )
        suggestions.forEachIndexed { idx, text ->
            val chip = TextView(context).apply {
                this.text = text
                setTextColor(Color.parseColor("#4F46E5"))
                textSize = 12f
                typeface = Typeface.DEFAULT_BOLD
                background = GradientDrawable().apply {
                    setColor(Color.parseColor("#EEF2FF"))
                    this.cornerRadius = (16 * density)
                }
                setPadding(
                    (12 * density).toInt(),
                    (8 * density).toInt(),
                    (12 * density).toInt(),
                    (8 * density).toInt(),
                )
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                ).apply {
                    if (idx > 0) marginStart = (8 * density).toInt()
                }
                isClickable = true
                isFocusable = true
                setOnClickListener {
                    goalSubmitCallback?.invoke(text)
                    dismiss()
                }
            }
            chipsRow.addView(chip)
        }
        // Wrap chips in a horizontal scrollview so it doesn't overflow.
        val chipsScroll = android.widget.HorizontalScrollView(context).apply {
            isHorizontalScrollBarEnabled = false
            addView(chipsRow)
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            )
        }
        sheet.addView(chipsScroll)

        // Input row pinned at bottom: text field + mic button + send button
        val inputRow = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#F9FAFB"))
                this.cornerRadius = (24 * density)
            }
            setPadding(
                (12 * density).toInt(),
                (4 * density).toInt(),
                (4 * density).toInt(),
                (4 * density).toInt(),
            )
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply {
                topMargin = (16 * density).toInt()
            }
        }

        val input = android.widget.EditText(context).apply {
            hint = "Ask anything…"
            setHintTextColor(Color.parseColor("#9CA3AF"))
            setTextColor(Color.parseColor("#111827"))
            textSize = 14f
            background = null
            setSingleLine(true)
            inputType = android.text.InputType.TYPE_CLASS_TEXT or
                    android.text.InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
            layoutParams = LinearLayout.LayoutParams(
                0,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                1f,
            )
        }
        inputRow.addView(input)

        val micBtn = ImageView(context).apply {
            setImageResource(com.marvis.agentlens.R.drawable.ic_mic)
            setColorFilter(Color.parseColor("#9CA3AF"))
            scaleType = ImageView.ScaleType.CENTER_INSIDE
            val s = (40 * density).toInt()
            val pad = (10 * density).toInt()
            layoutParams = LinearLayout.LayoutParams(s, s).apply {
                marginEnd = (4 * density).toInt()
            }
            setPadding(pad, pad, pad, pad)
            isClickable = true
            isFocusable = true
            background = GradientDrawable().apply {
                setColor(Color.TRANSPARENT)
                shape = GradientDrawable.OVAL
            }
            setOnClickListener {
                android.widget.Toast.makeText(
                    context,
                    "Voice input coming soon",
                    android.widget.Toast.LENGTH_SHORT,
                ).show()
            }
        }
        inputRow.addView(micBtn)

        val sendBtn = ImageView(context).apply {
            setImageResource(com.marvis.agentlens.R.drawable.ic_send)
            setColorFilter(Color.WHITE)
            scaleType = ImageView.ScaleType.CENTER_INSIDE
            val s = (40 * density).toInt()
            val pad = (10 * density).toInt()
            layoutParams = LinearLayout.LayoutParams(s, s)
            setPadding(pad, pad, pad, pad)
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#6366F1"))
                shape = GradientDrawable.OVAL
            }
            isClickable = true
            isFocusable = true
            setOnClickListener {
                val text = input.text.toString().trim()
                if (text.isNotEmpty()) {
                    goalSubmitCallback?.invoke(text)
                    dismiss()
                }
            }
        }
        inputRow.addView(sendBtn)

        sheet.addView(inputRow)

        // Anchor the sheet to the bottom of the screen.
        val sheetParams = FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT,
        ).apply {
            gravity = Gravity.BOTTOM
        }
        rootContainer.addView(sheet, sheetParams)

        val params = WindowManager.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            0,
            PixelFormat.TRANSLUCENT,
        ).apply {
            softInputMode = WindowManager.LayoutParams.SOFT_INPUT_STATE_VISIBLE
        }

        try {
            windowManager.addView(rootContainer, params)
            overlayView = rootContainer
            allOverlayViews.add(rootContainer)
            input.requestFocus()
            Log.i(TAG, "Chat overlay shown")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to show chat overlay", e)
        }
    }

    fun showAppOverlay(vdManager: VirtualDisplayManager, interactive: Boolean = false, message: String = "") {
        dismiss()
        currentVdManager = vdManager
        isInteractive = interactive
        currentTransformMatrix = null
        currentVdWidth = vdManager.width
        currentVdHeight = vdManager.height
        currentMessage = message
        createOverlay(vdManager, cropBounds = null)
    }

    fun showGenUIOverlay(html: String, interactive: Boolean = false, message: String = "") {
        dismiss()
        currentMessage = message
        isInteractive = interactive
        createGenUIOverlay(html)
    }

    /**
     * Show a parsed UI overlay with native list items.
     * Each element is rendered as a tappable row.
     */
    fun showParsedUIOverlay(
        elements: List<com.marvis.agentlens.service.AgentWebSocketClient.UIElementData>,
        interactive: Boolean = false,
        message: String = "",
    ) {
        dismiss()
        isInteractive = interactive
        currentMessage = message
        createParsedUIOverlay(elements)
    }

    fun showElementOverlay(vdManager: VirtualDisplayManager, bounds: Rect, interactive: Boolean = false, message: String = "") {
        dismiss()
        currentVdManager = vdManager
        isInteractive = interactive
        currentTransformMatrix = null
        currentVdWidth = vdManager.width
        currentVdHeight = vdManager.height
        currentMessage = message
        createOverlay(vdManager, cropBounds = bounds)
    }

    fun dismiss(silent: Boolean = false) {
        // Stop refresh timer
        refreshRunnable?.let { mainHandler.removeCallbacks(it) }
        refreshRunnable = null
        overlayImageView = null

        val wasShowing = overlayView != null || allOverlayViews.isNotEmpty()

        // Tear down EVERY tracked overlay window. We used to remove only the
        // most recent `overlayView`, but if a previous show*Overlay call had
        // failed mid-creation (or this manager replaced an older instance
        // that was force-killed) then orphaned windows from the past would
        // pile up on the user's main display. Iterating the static registry
        // here makes dismiss idempotent across instances.
        val snapshot = allOverlayViews.toList()
        allOverlayViews.clear()
        for (v in snapshot) {
            try {
                windowManager.removeView(v)
            } catch (e: Exception) {
                Log.w(TAG, "Failed to remove tracked overlay", e)
            }
        }
        overlayView = null
        overlayTextureView = null
        currentVdManager = null
        currentTransformMatrix = null
        isInteractive = false
        if (wasShowing && !silent) {
            onDismissed?.invoke()
        }
        Log.d(TAG, "Overlay dismissed (cleared ${snapshot.size} tracked window(s))")
    }

    private fun mapTouchToVD(touchX: Float, touchY: Float): Pair<Float, Float>? {
        val matrix = currentTransformMatrix
        if (matrix != null) {
            val inverse = Matrix()
            if (!matrix.invert(inverse)) return null
            val pts = floatArrayOf(touchX, touchY)
            inverse.mapPoints(pts)
            val x = pts[0].coerceIn(0f, currentVdWidth.toFloat())
            val y = pts[1].coerceIn(0f, currentVdHeight.toFloat())
            return Pair(x, y)
        } else {
            val tv = overlayTextureView ?: return null
            val viewW = tv.width.toFloat()
            val viewH = tv.height.toFloat()
            if (viewW <= 0 || viewH <= 0) return null
            val x = (touchX * currentVdWidth / viewW).coerceIn(0f, currentVdWidth.toFloat())
            val y = (touchY * currentVdHeight / viewH).coerceIn(0f, currentVdHeight.toFloat())
            return Pair(x, y)
        }
    }

    private fun createOverlay(vdManager: VirtualDisplayManager, cropBounds: Rect?) {
        val density = context.resources.displayMetrics.density
        val closeSizePx = (CLOSE_BUTTON_SIZE_DP * density).toInt()
        val cornerRadiusPx = CORNER_RADIUS_DP * density
        val headerHeightPx = (HEADER_HEIGHT_DP * density).toInt()

        val screenWidth = context.resources.displayMetrics.widthPixels
        val screenHeight = context.resources.displayMetrics.heightPixels
        // Full screen width for maximum resolution
        val overlayWidth = screenWidth

        // Calculate content height and TextureView dimensions
        val contentHeight: Int
        val textureWidth: Int
        val textureHeight: Int

        if (cropBounds != null) {
            // For crop: TextureView matches VD resolution scaled to screen width
            // so we get near-1:1 pixel mapping for the crop region
            textureWidth = screenWidth
            textureHeight = (screenWidth.toFloat() * vdManager.height / vdManager.width).toInt()
            // Content area shows only the crop portion. Honor the requested
            // crop height closely; just clamp to a small floor (so a
            // pathologically small crop is still tappable) and a sensible
            // ceiling (so it never grows past 3/4 of the screen).
            val cropRatio = cropBounds.height().toFloat() / vdManager.height
            val minCrop = (160 * density).toInt()
            contentHeight = (textureHeight * cropRatio).toInt()
                .coerceIn(minCrop, screenHeight * 3 / 4)
        } else {
            textureWidth = screenWidth
            textureHeight = (screenWidth.toFloat() * vdManager.height / vdManager.width).toInt()
            contentHeight = textureHeight.coerceAtMost(screenHeight * 3 / 4)
        }

        val footerHeightPx = 0 // if (isInteractive) (48 * density).toInt() else 0
        val overlayHeight = headerHeightPx + contentHeight + footerHeightPx

        // Root container — white card with rounded top corners
        val rootContainer = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            val bg = GradientDrawable().apply {
                setColor(Color.WHITE)
                cornerRadii = floatArrayOf(
                    cornerRadiusPx, cornerRadiusPx,  // top-left
                    cornerRadiusPx, cornerRadiusPx,  // top-right
                    0f, 0f,                           // bottom-right
                    0f, 0f,                           // bottom-left
                )
            }
            background = bg
            clipToOutline = true
            outlineProvider = object : android.view.ViewOutlineProvider() {
                override fun getOutline(view: View, outline: android.graphics.Outline) {
                    outline.setRoundRect(0, 0, view.width, view.height + cornerRadiusPx.toInt(), cornerRadiusPx)
                }
            }
            clipChildren = true
            elevation = 16 * density
        }

        // ── Header: message text + X button ──
        val header = FrameLayout(context).apply {
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                headerHeightPx,
            )
            setBackgroundColor(Color.WHITE)
        }

        // Message text
        val messageText = TextView(context).apply {
            text = currentMessage.ifBlank { " " }
            setTextColor(Color.parseColor("#1A1A1A"))
            textSize = 14f
            typeface = Typeface.DEFAULT_BOLD
            maxLines = 1
            setPadding((16 * density).toInt(), 0, (48 * density).toInt(), 0)
            layoutParams = FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
            ).apply {
                gravity = Gravity.CENTER_VERTICAL
            }
            this.gravity = Gravity.CENTER_VERTICAL
        }
        header.addView(messageText)

        // X close button
        val closeButton = ImageView(context).apply {
            setImageResource(android.R.drawable.ic_menu_close_clear_cancel)
            setColorFilter(Color.parseColor("#666666"))
            setPadding(
                (6 * density).toInt(),
                (6 * density).toInt(),
                (6 * density).toInt(),
                (6 * density).toInt(),
            )
            layoutParams = FrameLayout.LayoutParams(closeSizePx, closeSizePx).apply {
                gravity = Gravity.CENTER_VERTICAL or Gravity.END
                setMargins(0, 0, (12 * density).toInt(), 0)
            }
            setOnClickListener { dismiss() }
        }
        header.addView(closeButton)

        // Divider line
        val divider = View(context).apply {
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                (1 * density).toInt(),
            )
            setBackgroundColor(Color.parseColor("#E0E0E0"))
        }

        // ── TextureView for VD content ──
        // For crop: TextureView is full VD-ratio size inside a clip container.
        // For full: TextureView fills the content area.
        val textureView = TextureView(context).apply {
            layoutParams = if (cropBounds != null) {
                // Full VD-ratio size for high resolution
                FrameLayout.LayoutParams(textureWidth, textureHeight)
            } else {
                LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    contentHeight,
                )
            }
        }

        // For crop, wrap TextureView in a FrameLayout that clips to content area,
        // and offset the TextureView so the crop region is visible.
        val contentView: View = if (cropBounds != null) {
            val clipContainer = FrameLayout(context).apply {
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    contentHeight,
                )
                clipChildren = true
                clipToPadding = true
            }
            clipContainer.addView(textureView)
            clipContainer
        } else {
            textureView
        }

        textureView.surfaceTextureListener = object : TextureView.SurfaceTextureListener {
            override fun onSurfaceTextureAvailable(st: SurfaceTexture, w: Int, h: Int) {
                st.setDefaultBufferSize(vdManager.width, vdManager.height)
                val surface = Surface(st)
                vdManager.attachSurface(surface)
                Log.d(TAG, "Overlay surface attached, textureView=${textureView.width}x${textureView.height}")

                if (cropBounds != null) {
                    textureView.post {
                        val yOffset = -(cropBounds.top.toFloat() / vdManager.height * textureHeight).toInt()
                        textureView.translationY = yOffset.toFloat()
                        Log.d(TAG, "Crop offset: yOffset=$yOffset, cropTop=${cropBounds.top}")
                    }
                }
            }

            override fun onSurfaceTextureSizeChanged(st: SurfaceTexture, w: Int, h: Int) {}

            override fun onSurfaceTextureDestroyed(st: SurfaceTexture): Boolean {
                vdManager.detachSurface()
                return true
            }

            override fun onSurfaceTextureUpdated(st: SurfaceTexture) {}
        }

        // Touch handling on TextureView for interactive mode.
        //
        // Touches are forwarded to the underlying virtual display via
        // touchCallback (which the WebSocket layer relays to the Python
        // backend, which injects ADB taps on the VD). The user can keep
        // tapping/typing in the mirrored real form for as many actions as
        // they need — they explicitly close the overlay with the X button
        // when finished, which fires `dismiss()` and signals the agent
        // through `onDismissed`.
        //
        // We previously auto-dismissed 200 ms after the FIRST ACTION_UP
        // (so a single-tap "pick an option" flow could move on), but that
        // made multi-field form input impossible: the first tap on any
        // text field would tear down the overlay before the user could
        // type a single character.
        if (isInteractive) {
            textureView.setOnTouchListener { _, event ->
                val vdCoords = mapTouchToVD(event.x, event.y)
                if (vdCoords != null) {
                    Log.d(TAG, "Touch: action=${event.action}, vd=(${vdCoords.first.toInt()}, ${vdCoords.second.toInt()})")
                    touchCallback?.invoke(event.action, vdCoords.first, vdCoords.second)
                }
                true
            }
        }

        // Assemble layout
        rootContainer.addView(header)
        rootContainer.addView(divider)
        rootContainer.addView(contentView)

        // Footer with Done button — only shown for Ask (interactive) overlays
        // if (isInteractive) {
        //     val footer = FrameLayout(context).apply {
        //         layoutParams = LinearLayout.LayoutParams(
        //             ViewGroup.LayoutParams.MATCH_PARENT,
        //             footerHeightPx,
        //         )
        //         setBackgroundColor(Color.WHITE)
        //     }
        //     val doneButton = TextView(context).apply {
        //         text = "Done"
        //         textSize = 15f
        //         typeface = Typeface.DEFAULT_BOLD
        //         setTextColor(Color.WHITE)
        //         gravity = Gravity.CENTER
        //         background = GradientDrawable().apply {
        //             setColor(Color.parseColor("#6366F1"))
        //             cornerRadius = 20 * density
        //         }
        //         val hPad = (20 * density).toInt()
        //         val vPad = (8 * density).toInt()
        //         setPadding(hPad, vPad, hPad, vPad)
        //         layoutParams = FrameLayout.LayoutParams(
        //             ViewGroup.LayoutParams.WRAP_CONTENT,
        //             ViewGroup.LayoutParams.WRAP_CONTENT,
        //         ).apply {
        //             gravity = Gravity.CENTER_VERTICAL or Gravity.END
        //             setMargins(0, 0, (16 * density).toInt(), 0)
        //         }
        //         isClickable = true
        //         isFocusable = true
        //         setOnClickListener { dismiss() }
        //     }
        //     footer.addView(doneButton)
        //     rootContainer.addView(footer)
        // }

        // Swipe-down to dismiss (only in non-interactive mode)
        if (!isInteractive) {
            val gestureDetector = GestureDetector(context, object : GestureDetector.SimpleOnGestureListener() {
                override fun onFling(
                    e1: MotionEvent?,
                    e2: MotionEvent,
                    velocityX: Float,
                    velocityY: Float,
                ): Boolean {
                    if (e1 != null && e2.y - e1.y > SWIPE_THRESHOLD && velocityY > 0) {
                        dismiss()
                        return true
                    }
                    return false
                }
            })

            rootContainer.setOnTouchListener { _, event ->
                gestureDetector.onTouchEvent(event)
                false
            }
        }

        // Window params — bottom sheet style
        val windowFlags = if (isInteractive) {
            WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN
        } else {
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                    WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN
        }

        val params = WindowManager.LayoutParams(
            overlayWidth,
            overlayHeight,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            windowFlags,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL
            y = 0
        }

        windowManager.addView(rootContainer, params)
        overlayView = rootContainer
        allOverlayViews.add(rootContainer)
        overlayTextureView = textureView
        Log.i(TAG, "Overlay shown: ${overlayWidth}x${overlayHeight}, crop=$cropBounds, interactive=$isInteractive")
    }

    private fun applyCropTransform(
        tv: TextureView,
        vdManager: VirtualDisplayManager,
        bounds: Rect,
    ) {
        val vdW = vdManager.width.toFloat()
        val vdH = vdManager.height.toFloat()
        val viewW = tv.width.toFloat()
        val viewH = tv.height.toFloat()
        if (viewW <= 0 || viewH <= 0) return

        val cropW = bounds.width().toFloat()
        val cropH = bounds.height().toFloat()
        if (cropW <= 0 || cropH <= 0) return

        // TextureView automatically maps the VD buffer (vdW x vdH) to fill (viewW x viewH).
        // The Matrix we set is applied ON TOP of that base mapping.
        //
        // In view coordinates (after base mapping), the crop region is:
        //   cropLeft_v = bounds.left / vdW * viewW
        //   cropTop_v  = bounds.top  / vdH * viewH
        //   cropW_v    = cropW / vdW * viewW
        //   cropH_v    = cropH / vdH * viewH
        //
        // We want cropW_v * scale = viewW  →  scale = viewW / cropW_v = vdW / cropW
        // But also cropH_v * scale should fit in viewH.
        // Use width-based scale so the crop fills the panel width:
        val scale = viewW / (cropW / vdW * viewW)  // simplifies to vdW / cropW

        val cropLeftV = bounds.left.toFloat() / vdW * viewW
        val cropTopV  = bounds.top.toFloat()  / vdH * viewH
        val cropWV = cropW / vdW * viewW
        val cropHV = cropH / vdH * viewH

        // After scaling: crop occupies (cropWV * scale) x (cropHV * scale) in view
        val scaledCropW = cropWV * scale  // = viewW
        val scaledCropH = cropHV * scale

        // Center horizontally, align top vertically
        val offsetX = (viewW - scaledCropW) / 2f  // should be ~0
        val offsetY = 0f  // align to top

        val tX = -cropLeftV * scale + offsetX
        val tY = -cropTopV * scale + offsetY

        val matrix = Matrix()
        matrix.setScale(scale, scale)
        matrix.postTranslate(tX, tY)
        tv.setTransform(matrix)

        currentTransformMatrix = Matrix(matrix)

        // Clip to crop region only
        tv.clipBounds = android.graphics.Rect(
            offsetX.toInt().coerceAtLeast(0),
            offsetY.toInt().coerceAtLeast(0),
            (offsetX + scaledCropW).toInt().coerceAtMost(viewW.toInt()),
            (offsetY + scaledCropH).toInt().coerceAtMost(viewH.toInt()),
        )

        Log.d(TAG, "CropTransform: scale=$scale, cropInView=(${cropLeftV},${cropTopV},${cropWV},${cropHV}), clip=${tv.clipBounds}")
    }

    private fun createGenUIOverlay(html: String) {
        val density = context.resources.displayMetrics.density
        val paddingPx = (OVERLAY_PADDING_DP * density).toInt()
        val closeSizePx = (CLOSE_BUTTON_SIZE_DP * density).toInt()
        val cornerRadiusPx = CORNER_RADIUS_DP * density
        val headerHeightPx = (HEADER_HEIGHT_DP * density).toInt()

        val screenWidth = context.resources.displayMetrics.widthPixels
        val overlayWidth = screenWidth - paddingPx * 2

        // Root container
        val rootContainer = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            val bg = GradientDrawable().apply {
                setColor(Color.WHITE)
                cornerRadii = floatArrayOf(
                    cornerRadiusPx, cornerRadiusPx,
                    cornerRadiusPx, cornerRadiusPx,
                    0f, 0f,
                    0f, 0f,
                )
            }
            background = bg
            clipToOutline = true
            outlineProvider = object : android.view.ViewOutlineProvider() {
                override fun getOutline(view: View, outline: android.graphics.Outline) {
                    outline.setRoundRect(0, 0, view.width, view.height + cornerRadiusPx.toInt(), cornerRadiusPx)
                }
            }
            clipChildren = true
            elevation = 16 * density
        }

        // Header
        val header = FrameLayout(context).apply {
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                headerHeightPx,
            )
            setBackgroundColor(Color.WHITE)
        }

        val messageText = TextView(context).apply {
            text = currentMessage.ifBlank { " " }
            setTextColor(Color.parseColor("#1A1A1A"))
            textSize = 14f
            typeface = Typeface.DEFAULT_BOLD
            maxLines = 1
            setPadding((16 * density).toInt(), 0, (48 * density).toInt(), 0)
            layoutParams = FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
            ).apply {
                gravity = Gravity.CENTER_VERTICAL
            }
            this.gravity = Gravity.CENTER_VERTICAL
        }
        header.addView(messageText)

        val closeButton = ImageView(context).apply {
            setImageResource(android.R.drawable.ic_menu_close_clear_cancel)
            setColorFilter(Color.parseColor("#666666"))
            setPadding(
                (6 * density).toInt(),
                (6 * density).toInt(),
                (6 * density).toInt(),
                (6 * density).toInt(),
            )
            layoutParams = FrameLayout.LayoutParams(closeSizePx, closeSizePx).apply {
                gravity = Gravity.CENTER_VERTICAL or Gravity.END
                setMargins(0, 0, (12 * density).toInt(), 0)
            }
            setOnClickListener { dismiss() }
        }
        header.addView(closeButton)

        val divider = View(context).apply {
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                (1 * density).toInt(),
            )
            setBackgroundColor(Color.parseColor("#E0E0E0"))
        }

        // WebView for GenUI HTML. Use height=0 + weight=1 so the WebView
        // fills the remaining space inside the capped overlay window.
        // The overlay window itself is capped further down to ~60 % of
        // the screen height so the popup never swallows the entire
        // screen. The WebView scrolls internally if the rendered HTML
        // exceeds that cap.
        // Sentinel root reference + measurements available to the JS bridge
        // closure below. We need a deferred reference because the bridge is
        // installed before the View hierarchy exists. (`density`,
        // `headerHeightPx` are already declared at the top of this fn.)
        val screenHeightPx = context.resources.displayMetrics.heightPixels
        // Hard ceiling so an absurdly long card still cannot completely
        // cover the underlying app — internal scroll kicks in past this.
        val maxOverlayHeight = (screenHeightPx * 0.85f).toInt()
        // Reserve room for the header strip + divider so we don't clip
        // the action buttons at the bottom.
        val chromeHeightPx = headerHeightPx + (1 * density).toInt()

        // We construct the WebView first, then the params, then attach.
        // The params are reassigned by the bridge once the JS measures
        // the body height.
        lateinit var rootRef: View
        lateinit var paramsRef: WindowManager.LayoutParams

        val webView = WebView(context).apply {
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                0,
                1f,
            )
            setBackgroundColor(Color.WHITE)
            @SuppressLint("SetJavaScriptEnabled")
            settings.javaScriptEnabled = true
            addJavascriptInterface(object {
                @JavascriptInterface
                fun onAction(actionJson: String) {
                    Log.i(TAG, "GenUI action: $actionJson")
                    genUIActionCallback?.invoke(actionJson)
                    // Auto-dismiss overlay after user action
                    android.os.Handler(android.os.Looper.getMainLooper()).post {
                        dismiss()
                    }
                }

                @JavascriptInterface
                fun reportContentHeight(cssPx: Int) {
                    // The JS reports its scrollHeight in CSS pixels. Convert
                    // to physical pixels and resize the overlay window so
                    // the popup hugs the actual card. Cap at 85 % screen.
                    val contentPx = (cssPx * density).toInt()
                    val desired = (contentPx + chromeHeightPx)
                        .coerceAtMost(maxOverlayHeight)
                    android.os.Handler(android.os.Looper.getMainLooper()).post {
                        try {
                            paramsRef.height = desired
                            windowManager.updateViewLayout(rootRef, paramsRef)
                            Log.i(TAG, "GenUI overlay resized to height=$desired (content=$contentPx, max=$maxOverlayHeight)")
                        } catch (e: Exception) {
                            Log.w(TAG, "GenUI overlay resize failed", e)
                        }
                    }
                }
            }, "GenUIBridge")

            // Inject a tiny script that runs after the page is laid out and
            // reports its height back. We poll briefly to handle async images
            // / late layout shifts so the final size is accurate.
            webViewClient = object : android.webkit.WebViewClient() {
                override fun onPageFinished(view: WebView, url: String) {
                    val js = """
                        (function() {
                          function send() {
                            var h = Math.max(
                              document.body ? document.body.scrollHeight : 0,
                              document.documentElement ? document.documentElement.scrollHeight : 0
                            );
                            if (window.GenUIBridge && GenUIBridge.reportContentHeight) {
                              GenUIBridge.reportContentHeight(h);
                            }
                          }
                          send();
                          // Re-measure after async layout / image loads.
                          setTimeout(send, 100);
                          setTimeout(send, 400);
                        })();
                    """.trimIndent()
                    view.evaluateJavascript(js, null)
                }
            }
            loadDataWithBaseURL(null, html, "text/html", "UTF-8", null)
        }

        rootContainer.addView(header)
        rootContainer.addView(divider)
        rootContainer.addView(webView)

        val params = WindowManager.LayoutParams(
            overlayWidth,
            // Initial guess: 60 % of screen. The JS bridge tightens it to
            // the real content height as soon as the page lays out, so the
            // popup ends up hugging the card.
            (screenHeightPx * 0.6f).toInt(),
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL
            y = 0
        }
        paramsRef = params
        rootRef = rootContainer

        windowManager.addView(rootContainer, params)
        overlayView = rootContainer
        allOverlayViews.add(rootContainer)
        Log.i(TAG, "GenUI overlay shown: ${overlayWidth}x(initial ${params.height}, max $maxOverlayHeight)")
    }

    private fun createParsedUIOverlay(
        elements: List<com.marvis.agentlens.service.AgentWebSocketClient.UIElementData>,
    ) {
        val density = context.resources.displayMetrics.density
        val paddingPx = (OVERLAY_PADDING_DP * density).toInt()
        val closeSizePx = (CLOSE_BUTTON_SIZE_DP * density).toInt()
        val cornerRadiusPx = CORNER_RADIUS_DP * density
        val headerHeightPx = (HEADER_HEIGHT_DP * density).toInt()

        val screenWidth = context.resources.displayMetrics.widthPixels
        val screenHeight = context.resources.displayMetrics.heightPixels
        val overlayWidth = screenWidth - paddingPx * 2

        // Root container
        val rootContainer = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            val bg = GradientDrawable().apply {
                setColor(Color.WHITE)
                cornerRadii = floatArrayOf(
                    cornerRadiusPx, cornerRadiusPx,
                    cornerRadiusPx, cornerRadiusPx,
                    0f, 0f, 0f, 0f,
                )
            }
            background = bg
            clipToOutline = true
            outlineProvider = object : android.view.ViewOutlineProvider() {
                override fun getOutline(view: View, outline: android.graphics.Outline) {
                    outline.setRoundRect(0, 0, view.width, view.height + cornerRadiusPx.toInt(), cornerRadiusPx)
                }
            }
            clipChildren = true
            elevation = 16 * density
        }

        // ── Header ──
        val header = FrameLayout(context).apply {
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, headerHeightPx,
            )
            setBackgroundColor(Color.WHITE)
        }
        val messageText = TextView(context).apply {
            text = currentMessage.ifBlank { " " }
            setTextColor(Color.parseColor("#1A1A1A"))
            textSize = 14f
            typeface = Typeface.DEFAULT_BOLD
            maxLines = 1
            setPadding((16 * density).toInt(), 0, (48 * density).toInt(), 0)
            layoutParams = FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
            ).apply { gravity = Gravity.CENTER_VERTICAL }
            this.gravity = Gravity.CENTER_VERTICAL
        }
        header.addView(messageText)

        val closeButton = ImageView(context).apply {
            setImageResource(android.R.drawable.ic_menu_close_clear_cancel)
            setColorFilter(Color.parseColor("#666666"))
            val p = (6 * density).toInt()
            setPadding(p, p, p, p)
            layoutParams = FrameLayout.LayoutParams(closeSizePx, closeSizePx).apply {
                gravity = Gravity.CENTER_VERTICAL or Gravity.END
                setMargins(0, 0, (12 * density).toInt(), 0)
            }
            setOnClickListener { dismiss() }
        }
        header.addView(closeButton)

        val divider = View(context).apply {
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, (1 * density).toInt(),
            )
            setBackgroundColor(Color.parseColor("#E0E0E0"))
        }

        // ── Scrollable list of elements ──
        val scrollView = android.widget.ScrollView(context).apply {
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                0, // weight-based
                1f,
            )
            setBackgroundColor(Color.WHITE)
        }

        val listContainer = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, (4 * density).toInt(), 0, (4 * density).toInt())
        }

        for (elem in elements) {
            val row = LinearLayout(context).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(
                    (20 * density).toInt(), (14 * density).toInt(),
                    (20 * density).toInt(), (14 * density).toInt(),
                )
                if (elem.clickable && isInteractive) {
                    isClickable = true
                    isFocusable = true
                    // Ripple effect
                    val attrs = intArrayOf(android.R.attr.selectableItemBackground)
                    val ta = context.obtainStyledAttributes(attrs)
                    foreground = ta.getDrawable(0)
                    ta.recycle()

                    setOnClickListener {
                        Log.i(TAG, "Parsed UI tap: index=${elem.index}, text=${elem.text}")
                        // Send element selection back
                        val actionJson = org.json.JSONObject().apply {
                            put("type", "element_tap")
                            put("index", elem.index)
                            if (elem.bounds != null) {
                                put("bounds", org.json.JSONObject().apply {
                                    put("x1", elem.bounds.left)
                                    put("y1", elem.bounds.top)
                                    put("x2", elem.bounds.right)
                                    put("y2", elem.bounds.bottom)
                                })
                            }
                        }
                        genUIActionCallback?.invoke(actionJson.toString())
                        dismiss()
                    }
                }
            }

            // Main text
            val textView = TextView(context).apply {
                text = elem.text.ifBlank { "(no text)" }
                setTextColor(Color.parseColor("#1A1A1A"))
                textSize = 16f
                typeface = Typeface.DEFAULT_BOLD
                maxLines = 2
            }
            row.addView(textView)

            // Subtext
            if (elem.subtext.isNotBlank() && elem.subtext != elem.text) {
                val subtextView = TextView(context).apply {
                    text = elem.subtext
                    setTextColor(Color.parseColor("#666666"))
                    textSize = 13f
                    maxLines = 2
                    setPadding(0, (2 * density).toInt(), 0, 0)
                }
                row.addView(subtextView)
            }

            // Clickable indicator
            if (elem.clickable && isInteractive) {
                val indicator = TextView(context).apply {
                    text = "탭하여 선택"
                    setTextColor(Color.parseColor("#4A90D9"))
                    textSize = 11f
                    setPadding(0, (4 * density).toInt(), 0, 0)
                }
                row.addView(indicator)
            }

            listContainer.addView(row)

            // Separator
            val sep = View(context).apply {
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, (1 * density).toInt(),
                ).apply {
                    setMargins((20 * density).toInt(), 0, (20 * density).toInt(), 0)
                }
                setBackgroundColor(Color.parseColor("#F0F0F0"))
            }
            listContainer.addView(sep)
        }

        scrollView.addView(listContainer)

        // Assemble
        rootContainer.addView(header)
        rootContainer.addView(divider)
        rootContainer.addView(scrollView)

        // Window params
        val maxHeight = (screenHeight * 0.6f).toInt()
        val params = WindowManager.LayoutParams(
            overlayWidth,
            maxHeight,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            if (isInteractive) WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN
            else WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL
            y = 0
        }

        windowManager.addView(rootContainer, params)
        overlayView = rootContainer
        allOverlayViews.add(rootContainer)
        Log.i(TAG, "Parsed UI overlay shown: ${elements.size} elements, interactive=$isInteractive")
    }

    /**
     * Get SurfaceFlinger display ID for screencap -d.
     */
    private fun querySfDisplayId(): String? {
        try {
            val proc = Runtime.getRuntime().exec(arrayOf(
                "sh", "-c", "dumpsys SurfaceFlinger --display-id | grep AgentLens-VirtualDisplay"
            ))
            val output = proc.inputStream.bufferedReader().readText()
            proc.waitFor()
            // Extract "Display NNNNN" number
            val match = Regex("Display (\\d+)").find(output)
            return match?.groupValues?.get(1)
        } catch (e: Exception) {
            Log.e(TAG, "getSfDisplayId failed", e)
            return null
        }
    }

    /** SF display ID set externally (from Python server or ADB query). */
    var sfDisplayId: String? = null

    /**
     * Capture VD screenshot using screencap -d.
     */
    private fun captureVdBitmap(): android.graphics.Bitmap? {
        val sfId = sfDisplayId ?: staticSfDisplayId ?: querySfDisplayId()
        if (sfId == null) {
            Log.w(TAG, "captureVdBitmap: no SF display ID")
            return null
        }
        sfDisplayId = sfId // cache
        try {
            val proc = Runtime.getRuntime().exec(arrayOf(
                "sh", "-c", "screencap -d $sfId -p"
            ))
            val bytes = proc.inputStream.readBytes()
            val exitCode = proc.waitFor()
            Log.d(TAG, "screencap: sfId=$sfId, bytes=${bytes.size}, exit=$exitCode")
            if (bytes.isEmpty()) return null
            return android.graphics.BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
        } catch (e: Exception) {
            Log.e(TAG, "captureVdBitmap failed", e)
            return null
        }
    }

    /**
     * Screencap-based overlay: captures VD frames and displays in ImageView.
     */
    private fun createScreencapOverlay(cropBounds: Rect?) {
        val density = context.resources.displayMetrics.density
        val closeSizePx = (CLOSE_BUTTON_SIZE_DP * density).toInt()
        val cornerRadiusPx = CORNER_RADIUS_DP * density
        val headerHeightPx = (HEADER_HEIGHT_DP * density).toInt()

        val screenWidth = context.resources.displayMetrics.widthPixels
        val screenHeight = context.resources.displayMetrics.heightPixels
        val overlayWidth = screenWidth

        // Content height based on crop or full VD
        val contentHeight: Int
        if (cropBounds != null) {
            val cropAspect = cropBounds.width().toFloat() / cropBounds.height().toFloat()
            contentHeight = (overlayWidth / cropAspect).toInt()
                .coerceIn(screenHeight * 2 / 5, screenHeight * 3 / 4)
        } else {
            contentHeight = (overlayWidth.toFloat() * currentVdHeight / currentVdWidth).toInt()
                .coerceAtMost(screenHeight * 3 / 4)
        }
        val overlayHeight = headerHeightPx + contentHeight

        // Root container
        val rootContainer = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            val bg = GradientDrawable().apply {
                setColor(Color.WHITE)
                cornerRadii = floatArrayOf(
                    cornerRadiusPx, cornerRadiusPx,
                    cornerRadiusPx, cornerRadiusPx,
                    0f, 0f, 0f, 0f,
                )
            }
            background = bg
            clipToOutline = true
            outlineProvider = object : android.view.ViewOutlineProvider() {
                override fun getOutline(view: View, outline: android.graphics.Outline) {
                    outline.setRoundRect(0, 0, view.width, view.height + cornerRadiusPx.toInt(), cornerRadiusPx)
                }
            }
            clipChildren = true
            elevation = 16 * density
        }

        // Header
        val header = FrameLayout(context).apply {
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, headerHeightPx,
            )
            setBackgroundColor(Color.WHITE)
        }
        val messageText = TextView(context).apply {
            text = currentMessage.ifBlank { " " }
            setTextColor(Color.parseColor("#1A1A1A"))
            textSize = 14f
            typeface = Typeface.DEFAULT_BOLD
            maxLines = 1
            setPadding((16 * density).toInt(), 0, (48 * density).toInt(), 0)
            layoutParams = FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
            ).apply { gravity = Gravity.CENTER_VERTICAL }
            this.gravity = Gravity.CENTER_VERTICAL
        }
        header.addView(messageText)

        val closeButton = ImageView(context).apply {
            setImageResource(android.R.drawable.ic_menu_close_clear_cancel)
            setColorFilter(Color.parseColor("#666666"))
            val p = (6 * density).toInt()
            setPadding(p, p, p, p)
            layoutParams = FrameLayout.LayoutParams(closeSizePx, closeSizePx).apply {
                gravity = Gravity.CENTER_VERTICAL or Gravity.END
                setMargins(0, 0, (12 * density).toInt(), 0)
            }
            setOnClickListener { dismiss() }
        }
        header.addView(closeButton)

        val divider = View(context).apply {
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, (1 * density).toInt(),
            )
            setBackgroundColor(Color.parseColor("#E0E0E0"))
        }

        // ImageView for VD screenshot
        val imageView = ImageView(context).apply {
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                contentHeight,
            )
            scaleType = ImageView.ScaleType.FIT_CENTER
            setBackgroundColor(Color.WHITE)
        }
        overlayImageView = imageView

        // Touch handling for interactive mode
        if (isInteractive) {
            imageView.setOnTouchListener { _, event ->
                if (event.action == android.view.MotionEvent.ACTION_UP) {
                    // Map touch to VD coordinates
                    val imgW = imageView.width.toFloat()
                    val imgH = imageView.height.toFloat()
                    if (imgW > 0 && imgH > 0) {
                        var vdX: Float
                        var vdY: Float
                        if (cropBounds != null) {
                            vdX = cropBounds.left + event.x / imgW * cropBounds.width()
                            vdY = cropBounds.top + event.y / imgH * cropBounds.height()
                        } else {
                            vdX = event.x / imgW * currentVdWidth
                            vdY = event.y / imgH * currentVdHeight
                        }
                        Log.i(TAG, "Screencap overlay tap: vd=(${vdX.toInt()}, ${vdY.toInt()})")
                        touchCallback?.invoke(0, vdX, vdY) // ACTION_DOWN
                        touchCallback?.invoke(1, vdX, vdY) // ACTION_UP
                        // Refresh after touch
                        mainHandler.postDelayed({ refreshScreencap(cropBounds) }, 500)
                    }
                }
                true
            }
        }

        // Assemble
        rootContainer.addView(header)
        rootContainer.addView(divider)
        rootContainer.addView(imageView)

        // Window params
        val windowFlags = if (isInteractive) {
            WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN
        } else {
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                    WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN
        }

        val params = WindowManager.LayoutParams(
            overlayWidth,
            overlayHeight,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            windowFlags,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL
            y = 0
        }

        windowManager.addView(rootContainer, params)
        overlayView = rootContainer
        allOverlayViews.add(rootContainer)
        Log.i(TAG, "Screencap overlay shown: ${overlayWidth}x${overlayHeight}, crop=$cropBounds")

        // Start periodic refresh
        refreshScreencap(cropBounds)
        val runnable = object : Runnable {
            override fun run() {
                if (overlayView != null) {
                    refreshScreencap(cropBounds)
                    mainHandler.postDelayed(this, 100) // ~10 fps
                }
            }
        }
        refreshRunnable = runnable
        mainHandler.postDelayed(runnable, 100)
    }

    /**
     * Capture and update the overlay ImageView.
     */
    private fun refreshScreencap(cropBounds: Rect?) {
        val iv = overlayImageView ?: return
        Thread {
            val bitmap = captureVdBitmap()
            if (bitmap != null) {
                val displayBitmap = if (cropBounds != null) {
                    // Crop the bitmap
                    val x = cropBounds.left.coerceIn(0, bitmap.width - 1)
                    val y = cropBounds.top.coerceIn(0, bitmap.height - 1)
                    val w = cropBounds.width().coerceAtMost(bitmap.width - x)
                    val h = cropBounds.height().coerceAtMost(bitmap.height - y)
                    if (w > 0 && h > 0) {
                        android.graphics.Bitmap.createBitmap(bitmap, x, y, w, h)
                    } else {
                        bitmap
                    }
                } else {
                    bitmap
                }
                mainHandler.post {
                    iv.setImageBitmap(displayBitmap)
                }
            }
        }.start()
    }
}
