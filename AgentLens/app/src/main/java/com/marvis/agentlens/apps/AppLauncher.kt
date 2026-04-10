package com.marvis.agentlens.apps

import android.app.ActivityOptions
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ResolveInfo
import android.graphics.drawable.Drawable
import android.util.Log

data class AppInfo(
    val packageName: String,
    val activityName: String,
    val appName: String,
    val icon: Drawable
)

object AppLauncher {

    private const val TAG = "AppLauncher"

    fun getInstalledApps(context: Context): List<AppInfo> {
        val mainIntent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val resolvedApps: List<ResolveInfo> = context.packageManager.queryIntentActivities(
            mainIntent, PackageManager.MATCH_ALL
        )
        return resolvedApps
            .filter { it.activityInfo.packageName != context.packageName }
            .map { resolveInfo ->
                AppInfo(
                    packageName = resolveInfo.activityInfo.packageName,
                    activityName = resolveInfo.activityInfo.name,
                    appName = resolveInfo.loadLabel(context.packageManager).toString(),
                    icon = resolveInfo.loadIcon(context.packageManager)
                )
            }
            .sortedBy { it.appName.lowercase() }
    }

    fun launchOnDisplay(context: Context, appInfo: AppInfo, displayId: Int): Boolean {
        // Try 1: Standard API — setLaunchDisplayId (works on trusted/owned displays)
        try {
            val options = ActivityOptions.makeBasic()
            options.setLaunchDisplayId(displayId)

            val intent = Intent(Intent.ACTION_MAIN).apply {
                addCategory(Intent.CATEGORY_LAUNCHER)
                setClassName(appInfo.packageName, appInfo.activityName)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_MULTIPLE_TASK)
            }

            context.startActivity(intent, options.toBundle())
            Log.i(TAG, "Launched ${appInfo.packageName} on display $displayId via setLaunchDisplayId")
            return true
        } catch (e: SecurityException) {
            Log.w(TAG, "setLaunchDisplayId failed: ${e.message}")
        } catch (e: Exception) {
            Log.w(TAG, "setLaunchDisplayId failed: ${e.message}")
        }

        Log.e(TAG, "Launch failed for ${appInfo.packageName} on display $displayId")
        return false
    }
}
