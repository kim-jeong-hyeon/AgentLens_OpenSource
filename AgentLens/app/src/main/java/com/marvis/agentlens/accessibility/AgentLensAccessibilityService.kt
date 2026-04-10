package com.marvis.agentlens.accessibility

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.graphics.Rect
import android.os.Build
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

class AgentLensAccessibilityService : AccessibilityService() {

    companion object {
        private const val TAG = "AgentLensA11yService"

        private val _instance = MutableStateFlow<AgentLensAccessibilityService?>(null)
        val instance: StateFlow<AgentLensAccessibilityService?> = _instance.asStateFlow()

        fun isRunning(): Boolean = _instance.value != null
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        _instance.value = this
        Log.i(TAG, "Accessibility service connected")
    }

    override fun onDestroy() {
        _instance.value = null
        Log.i(TAG, "Accessibility service destroyed")
        super.onDestroy()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // We handle events on-demand rather than reactively
    }

    override fun onInterrupt() {
        Log.w(TAG, "Accessibility service interrupted")
    }

    /**
     * Get root nodes for all windows on a specific display.
     * On API 30+, filters by display ID. On older APIs, filters by package name.
     */
    fun getRootsOnDisplay(displayId: Int, targetPackage: String? = null): List<AccessibilityNodeInfo> {
        // API 33+: getWindowsOnAllDisplays() includes virtual display windows.
        // API 30-32: windows only returns default display; fall back to package filter.
        // API <30: no displayId field; fall back to package filter.
        val allWindows: List<AccessibilityWindowInfo>
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            // getWindowsOnAllDisplays() returns Map<Int, List<AccessibilityWindowInfo>>
            val windowsMap = getWindowsOnAllDisplays()
            val displayIds = mutableListOf<Int>()
            val windowsList = mutableListOf<AccessibilityWindowInfo>()
            for (i in 0 until windowsMap.size()) {
                displayIds.add(windowsMap.keyAt(i))
                windowsList.addAll(windowsMap.valueAt(i))
            }
            Log.i(TAG, "getWindowsOnAllDisplays: displays=$displayIds, totalWindows=${windowsList.size}")
            allWindows = windowsList
        } else {
            allWindows = windows ?: emptyList()
        }

        val filtered = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            allWindows.filter { it.displayId == displayId }
        } else {
            if (targetPackage != null) {
                allWindows.filter { window ->
                    window.root?.packageName?.toString() == targetPackage
                }
            } else {
                allWindows
            }
        }

        return filtered.mapNotNull { it.root }
    }

    /**
     * Build the full node tree from a root node.
     */
    fun buildNodeTree(root: AccessibilityNodeInfo, depth: Int = 0): NodeInfo {
        val bounds = Rect()
        root.getBoundsInScreen(bounds)

        val children = (0 until root.childCount).mapNotNull { i ->
            root.getChild(i)?.let { buildNodeTree(it, depth + 1) }
        }

        return NodeInfo(
            className = root.className?.toString(),
            text = root.text?.toString(),
            contentDescription = root.contentDescription?.toString(),
            viewIdResourceName = root.viewIdResourceName,
            bounds = bounds,
            isClickable = root.isClickable,
            isScrollable = root.isScrollable,
            isEnabled = root.isEnabled,
            children = children,
            depth = depth
        )
    }

    /**
     * Get the complete node tree for the virtual display.
     */
    fun getNodeTreeForDisplay(displayId: Int, targetPackage: String? = null): List<NodeInfo> {
        return getRootsOnDisplay(displayId, targetPackage).map { buildNodeTree(it) }
    }

    /**
     * Click on a specific node using accessibility action.
     */
    fun clickNode(node: AccessibilityNodeInfo): Boolean {
        return node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
    }

    /**
     * Find a node by text in the virtual display and click it.
     */
    fun clickByText(displayId: Int, text: String, targetPackage: String? = null): Boolean {
        val roots = getRootsOnDisplay(displayId, targetPackage)
        for (root in roots) {
            val nodes = root.findAccessibilityNodeInfosByText(text)
            for (node in nodes) {
                if (node.isClickable) {
                    return clickNode(node)
                }
                // Walk up to find a clickable ancestor
                var parent = node.parent
                while (parent != null) {
                    if (parent.isClickable) {
                        return clickNode(parent)
                    }
                    parent = parent.parent
                }
            }
        }
        return false
    }

    /**
     * Find a node by view ID in the virtual display and click it.
     */
    fun clickByViewId(displayId: Int, viewId: String, targetPackage: String? = null): Boolean {
        val roots = getRootsOnDisplay(displayId, targetPackage)
        for (root in roots) {
            val nodes = root.findAccessibilityNodeInfosByViewId(viewId)
            for (node in nodes) {
                if (node.isClickable) {
                    return clickNode(node)
                }
                var parent = node.parent
                while (parent != null) {
                    if (parent.isClickable) {
                        return clickNode(parent)
                    }
                    parent = parent.parent
                }
            }
        }
        return false
    }

    /**
     * Dump the UI tree for a display as uiautomator-compatible XML.
     */
    fun dumpDisplayXml(displayId: Int, targetPackage: String? = null): String {
        val allWindows = windows ?: emptyList()
        Log.i(TAG, "dumpDisplayXml: displayId=$displayId, targetPackage=$targetPackage, totalWindows=${allWindows.size}")
        for (w in allWindows) {
            Log.i(TAG, "  window: title=${w.title}, displayId=${if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) w.displayId else "N/A"}, type=${w.type}")
        }
        val roots = getRootsOnDisplay(displayId, targetPackage)
        Log.i(TAG, "dumpDisplayXml: found ${roots.size} roots")
        val sb = StringBuilder()
        sb.append("<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>")
        sb.append("<hierarchy rotation=\"0\">")
        for (root in roots) {
            dumpNodeXml(root, sb)
        }
        sb.append("</hierarchy>")
        return sb.toString()
    }

    private fun dumpNodeXml(node: AccessibilityNodeInfo, sb: StringBuilder) {
        val bounds = Rect()
        node.getBoundsInScreen(bounds)
        val boundsStr = "[${bounds.left},${bounds.top}][${bounds.right},${bounds.bottom}]"

        sb.append("<node")
        sb.append(" index=\"0\"")
        sb.append(" text=\"${escapeXml(node.text?.toString() ?: "")}\"")
        sb.append(" resource-id=\"${escapeXml(node.viewIdResourceName ?: "")}\"")
        sb.append(" class=\"${escapeXml(node.className?.toString() ?: "")}\"")
        sb.append(" package=\"${escapeXml(node.packageName?.toString() ?: "")}\"")
        sb.append(" content-desc=\"${escapeXml(node.contentDescription?.toString() ?: "")}\"")
        sb.append(" checkable=\"${node.isCheckable}\"")
        sb.append(" checked=\"${node.isChecked}\"")
        sb.append(" clickable=\"${node.isClickable}\"")
        sb.append(" enabled=\"${node.isEnabled}\"")
        sb.append(" focusable=\"${node.isFocusable}\"")
        sb.append(" focused=\"${node.isFocused}\"")
        sb.append(" scrollable=\"${node.isScrollable}\"")
        sb.append(" long-clickable=\"${node.isLongClickable}\"")
        sb.append(" password=\"${node.isPassword}\"")
        sb.append(" selected=\"${node.isSelected}\"")
        sb.append(" bounds=\"$boundsStr\"")
        sb.append(">")

        for (i in 0 until node.childCount) {
            node.getChild(i)?.let { dumpNodeXml(it, sb) }
        }

        sb.append("</node>")
    }

    private fun escapeXml(s: String): String {
        return s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\"", "&quot;")
            .replace("'", "&apos;")
    }

    /**
     * Inject a tap gesture at coordinates.
     * NOTE: dispatchGesture targets the default display. For virtual display
     * coordinate-based taps, use node-based clicks instead.
     */
    fun tapAt(x: Float, y: Float, callback: GestureResultCallback? = null): Boolean {
        val path = Path().apply { moveTo(x, y) }
        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0L, 100L))
            .build()
        return dispatchGesture(gesture, callback, null)
    }
}
