package com.marvis.agentlens.accessibility

import android.graphics.Rect

data class NodeInfo(
    val className: String?,
    val text: String?,
    val contentDescription: String?,
    val viewIdResourceName: String?,
    val bounds: Rect,
    val isClickable: Boolean,
    val isScrollable: Boolean,
    val isEnabled: Boolean,
    val children: List<NodeInfo>,
    val depth: Int = 0
) {
    fun flatten(): List<NodeInfo> {
        return listOf(this) + children.flatMap { it.flatten() }
    }

    fun toTreeString(indent: Int = 0): String {
        val sb = StringBuilder()
        val prefix = "  ".repeat(indent)
        sb.append(prefix)
        sb.append(className ?: "?")
        if (!text.isNullOrEmpty()) sb.append(" text=\"$text\"")
        if (!contentDescription.isNullOrEmpty()) sb.append(" desc=\"$contentDescription\"")
        if (!viewIdResourceName.isNullOrEmpty()) sb.append(" id=$viewIdResourceName")
        if (isClickable) sb.append(" [clickable]")
        sb.append(" $bounds")
        sb.append("\n")
        for (child in children) {
            sb.append(child.toTreeString(indent + 1))
        }
        return sb.toString()
    }
}
