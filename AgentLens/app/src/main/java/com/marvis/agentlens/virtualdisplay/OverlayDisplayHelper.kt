package com.marvis.agentlens.virtualdisplay

import android.content.Context
import android.hardware.display.DisplayManager
import android.provider.Settings
import android.util.Log
import android.view.Display

/**
 * Creates a trusted overlay display via Settings.Global.overlay_display_devices.
 * Requires WRITE_SECURE_SETTINGS permission (grant via ADB):
 *   adb shell pm grant com.marvis.agentlens android.permission.WRITE_SECURE_SETTINGS
 */
object OverlayDisplayHelper {

    private const val TAG = "OverlayDisplayHelper"
    private const val SETTING_KEY = "overlay_display_devices"

    fun createOverlayDisplay(context: Context, width: Int, height: Int, dpi: Int): Boolean {
        return try {
            val value = "${width}x${height}/$dpi"
            Settings.Global.putString(context.contentResolver, SETTING_KEY, value)
            Log.i(TAG, "Set overlay_display_devices to: $value")
            true
        } catch (e: SecurityException) {
            Log.e(TAG, "Need WRITE_SECURE_SETTINGS. Run: adb shell pm grant ${context.packageName} android.permission.WRITE_SECURE_SETTINGS", e)
            false
        }
    }

    fun removeOverlayDisplay(context: Context) {
        try {
            Settings.Global.putString(context.contentResolver, SETTING_KEY, "")
            Log.i(TAG, "Removed overlay display")
        } catch (e: SecurityException) {
            Log.w(TAG, "Cannot remove overlay display without WRITE_SECURE_SETTINGS")
        }
    }

    fun findOverlayDisplayId(context: Context): Int {
        val dm = context.getSystemService(Context.DISPLAY_SERVICE) as DisplayManager
        val displays = dm.displays
        for (display in displays) {
            // Overlay displays have type TYPE_OVERLAY (4) and are not the built-in screen
            if (display.displayId != Display.DEFAULT_DISPLAY && display.name.startsWith("Overlay")) {
                Log.i(TAG, "Found overlay display: id=${display.displayId}, name=${display.name}")
                return display.displayId
            }
        }
        return -1
    }
}
