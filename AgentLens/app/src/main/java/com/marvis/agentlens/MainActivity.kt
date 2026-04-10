package com.marvis.agentlens

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.media.projection.MediaProjectionManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import android.graphics.SurfaceTexture
import android.view.Surface
import android.view.TextureView
import androidx.compose.foundation.Image
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.core.graphics.drawable.toBitmap
import com.marvis.agentlens.accessibility.NodeInfo
import com.marvis.agentlens.apps.AppInfo
import com.marvis.agentlens.service.ProjectionForegroundService
import com.marvis.agentlens.ui.theme.AgentLensTheme
import com.marvis.agentlens.virtualdisplay.VirtualDisplayManager

class MainActivity : ComponentActivity() {

    companion object {
        private const val PREFS_NAME = "agentlens_prefs"
        private const val KEY_SERVER_URL = "server_url"
        private const val DEFAULT_SERVER_URL = "ws://127.0.0.1:8765"
    }

    private var pendingServerUrl: String? = null

    private val projectionLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == Activity.RESULT_OK && result.data != null) {
            ProjectionForegroundService.start(
                this, result.resultCode, result.data!!, pendingServerUrl
            )
        } else {
            Toast.makeText(this, "Screen capture permission denied", Toast.LENGTH_SHORT).show()
        }
    }

    private val notificationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { /* proceed regardless */ }

    private val overlayPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) {
        if (!Settings.canDrawOverlays(this)) {
            Toast.makeText(this, "Overlay permission required for visualization", Toast.LENGTH_SHORT).show()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            notificationPermissionLauncher.launch(android.Manifest.permission.POST_NOTIFICATIONS)
        }

        // Request overlay permission if not granted
        if (!Settings.canDrawOverlays(this)) {
            val intent = Intent(
                Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                Uri.parse("package:$packageName")
            )
            overlayPermissionLauncher.launch(intent)
        }

        setContent {
            AgentLensTheme {
                MainScreen(
                    onStartDisplay = { serverUrl ->
                        pendingServerUrl = serverUrl
                        saveServerUrl(serverUrl)
                        requestProjection()
                    },
                    onStopDisplay = { ProjectionForegroundService.stop(this) },
                    onOpenAccessibilitySettings = { openAccessibilitySettings() },
                    savedServerUrl = getSavedServerUrl(),
                )
            }
        }
    }

    private fun requestProjection() {
        val manager = getSystemService(MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        projectionLauncher.launch(manager.createScreenCaptureIntent())
    }

    private fun openAccessibilitySettings() {
        startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
    }

    private fun getSavedServerUrl(): String {
        return getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .getString(KEY_SERVER_URL, DEFAULT_SERVER_URL) ?: DEFAULT_SERVER_URL
    }

    private fun saveServerUrl(url: String) {
        getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit().putString(KEY_SERVER_URL, url).apply()
    }
}

@Composable
fun MainScreen(
    onStartDisplay: (serverUrl: String) -> Unit,
    onStopDisplay: () -> Unit,
    onOpenAccessibilitySettings: () -> Unit,
    savedServerUrl: String,
    vm: MainViewModel = viewModel()
) {
    val isActive by vm.isProjectionActive.collectAsState()
    val displayId by vm.displayId.collectAsState()
    val isA11y by vm.isAccessibilityEnabled.collectAsState()
    val isConnected by ProjectionForegroundService.isConnected.collectAsState()
    val status by vm.statusMessage.collectAsState()
    val screenshot by vm.screenshotBitmap.collectAsState()
    val apps by vm.installedApps.collectAsState()
    val launchedApp by vm.launchedApp.collectAsState()
    val nodeTree by vm.nodeTree.collectAsState()

    var selectedTab by remember { mutableIntStateOf(0) }
    val tabs = listOf("Control", "Apps", "Display", "A11y Tree")

    LaunchedEffect(Unit) {
        vm.loadInstalledApps()
    }

    Scaffold(modifier = Modifier.fillMaxSize()) { padding ->
        Column(modifier = Modifier.padding(padding).fillMaxSize()) {
            // Status bar
            if (status.isNotEmpty()) {
                Text(
                    text = status,
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(MaterialTheme.colorScheme.primaryContainer)
                        .padding(8.dp),
                    color = MaterialTheme.colorScheme.onPrimaryContainer,
                    fontSize = 12.sp
                )
            }

            TabRow(selectedTabIndex = selectedTab) {
                tabs.forEachIndexed { index, title ->
                    Tab(
                        selected = selectedTab == index,
                        onClick = { selectedTab = index },
                        text = { Text(title, maxLines = 1) }
                    )
                }
            }

            when (selectedTab) {
                0 -> ControlPanel(
                    isActive = isActive,
                    displayId = displayId,
                    isA11y = isA11y,
                    isConnected = isConnected,
                    launchedApp = launchedApp,
                    savedServerUrl = savedServerUrl,
                    onStartDisplay = onStartDisplay,
                    onStopDisplay = onStopDisplay,
                    onOpenAccessibilitySettings = onOpenAccessibilitySettings
                )
                1 -> AppPicker(
                    apps = apps,
                    isActive = isActive,
                    onAppSelected = { vm.launchApp(it) }
                )
                2 -> VirtualDisplayPreview(
                    displayManager = vm.virtualDisplayManager.collectAsState().value,
                    screenshot = screenshot,
                    isActive = isActive,
                    onCapture = { vm.captureScreenshot() },
                    onSave = { vm.saveScreenshot() },
                    onTextureViewReady = { vm.updatePreviewTextureView(it) },
                    onTextureViewGone = { vm.updatePreviewTextureView(null) },
                    onSetCrop = { x1, y1, x2, y2 -> vm.setCropBounds(x1, y1, x2, y2) },
                    onResetCrop = { vm.resetCrop() }
                )
                3 -> AccessibilityTreeTab(
                    nodeTree = nodeTree,
                    isA11y = isA11y,
                    isActive = isActive,
                    onFetchTree = { vm.fetchNodeTree() },
                    onClickByText = { vm.clickByText(it) },
                    onClickByViewId = { vm.clickByViewId(it) }
                )
            }
        }
    }
}

@Composable
fun ControlPanel(
    isActive: Boolean,
    displayId: Int,
    isA11y: Boolean,
    isConnected: Boolean,
    launchedApp: AppInfo?,
    savedServerUrl: String,
    onStartDisplay: (serverUrl: String) -> Unit,
    onStopDisplay: () -> Unit,
    onOpenAccessibilitySettings: () -> Unit
) {
    var serverUrl by remember { mutableStateOf(savedServerUrl) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp)
            .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        Text("Virtual Display", style = MaterialTheme.typography.headlineSmall)

        OutlinedTextField(
            value = serverUrl,
            onValueChange = { serverUrl = it },
            label = { Text("Server URL") },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            enabled = !isActive,
        )

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(
                onClick = { onStartDisplay(serverUrl) },
                enabled = !isActive
            ) { Text("Start Display") }

            Button(
                onClick = onStopDisplay,
                enabled = isActive,
                colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error)
            ) { Text("Stop") }
        }

        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(12.dp)) {
                StatusRow("Projection", if (isActive) "Active" else "Inactive")
                StatusRow("Display ID", if (displayId >= 0) "$displayId" else "N/A")
                StatusRow("Server", if (isConnected) "Connected" else "Disconnected")
                StatusRow("Launched App", launchedApp?.appName ?: "None")
            }
        }

        HorizontalDivider()

        Text("Accessibility Service", style = MaterialTheme.typography.headlineSmall)

        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            StatusDot(isA11y)
            Text(if (isA11y) "Enabled" else "Disabled")
        }

        OutlinedButton(onClick = onOpenAccessibilitySettings) {
            Text("Open Accessibility Settings")
        }
    }
}

@Composable
fun StatusRow(label: String, value: String) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 2.dp),
        horizontalArrangement = Arrangement.SpaceBetween
    ) {
        Text(label, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value)
    }
}

@Composable
fun StatusDot(active: Boolean) {
    Box(
        modifier = Modifier
            .size(12.dp)
            .background(
                color = if (active) Color(0xFF4CAF50) else Color(0xFFF44336),
                shape = MaterialTheme.shapes.small
            )
    )
}

@Composable
fun AppPicker(
    apps: List<AppInfo>,
    isActive: Boolean,
    onAppSelected: (AppInfo) -> Unit
) {
    if (!isActive) {
        Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Text("Start projection first")
        }
        return
    }

    // Some packages expose more than one launcher activity (e.g. Google
    // search box), which produced duplicate entries with the same key and
    // crashed the LazyColumn during scroll. Collapse to one row per package.
    val uniqueApps = remember(apps) { apps.distinctBy { it.packageName } }
    LazyColumn(modifier = Modifier.fillMaxSize()) {
        items(uniqueApps, key = { it.packageName }) { app ->
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable { onAppSelected(app) }
                    .padding(horizontal = 16.dp, vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Image(
                    bitmap = remember(app.icon) { app.icon.toBitmap(80, 80).asImageBitmap() },
                    contentDescription = app.appName,
                    modifier = Modifier.size(40.dp)
                )
                Spacer(modifier = Modifier.width(12.dp))
                Column {
                    Text(app.appName, style = MaterialTheme.typography.bodyLarge)
                    Text(
                        app.packageName,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }
        }
    }
}

@Composable
fun VirtualDisplayPreview(
    displayManager: VirtualDisplayManager?,
    screenshot: android.graphics.Bitmap?,
    isActive: Boolean,
    onCapture: () -> Unit,
    onSave: () -> Unit,
    onTextureViewReady: (TextureView) -> Unit,
    onTextureViewGone: () -> Unit,
    onSetCrop: (Int, Int, Int, Int) -> Unit,
    onResetCrop: () -> Unit
) {
    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = onCapture, enabled = isActive) { Text("Capture") }
            OutlinedButton(onClick = onSave, enabled = screenshot != null) { Text("Save") }
        }

        Spacer(modifier = Modifier.height(4.dp))

        // Crop controls
        var boundsInput by remember { mutableStateOf("") }
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            OutlinedTextField(
                value = boundsInput,
                onValueChange = { boundsInput = it },
                label = { Text("Bounds [x1,y1][x2,y2]") },
                modifier = Modifier.weight(1f),
                singleLine = true
            )
            Button(
                onClick = {
                    parseBounds(boundsInput)?.let { (x1, y1, x2, y2) ->
                        onSetCrop(x1, y1, x2, y2)
                    }
                },
                enabled = isActive && boundsInput.isNotEmpty()
            ) { Text("Crop") }
            OutlinedButton(onClick = {
                onResetCrop()
                boundsInput = ""
            }) { Text("Reset") }
        }

        Spacer(modifier = Modifier.height(4.dp))

        if (isActive && displayManager != null) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f)
                    .clipToBounds()
            ) {
                AndroidView(
                    factory = { context ->
                        TextureView(context).apply {
                            surfaceTextureListener = object : TextureView.SurfaceTextureListener {
                                override fun onSurfaceTextureAvailable(texture: SurfaceTexture, w: Int, h: Int) {
                                    texture.setDefaultBufferSize(displayManager.width, displayManager.height)
                                    val surface = Surface(texture)
                                    displayManager.attachSurface(surface)
                                    onTextureViewReady(this@apply)
                                }

                                override fun onSurfaceTextureSizeChanged(texture: SurfaceTexture, w: Int, h: Int) {}

                                override fun onSurfaceTextureDestroyed(texture: SurfaceTexture): Boolean {
                                    displayManager.detachSurface()
                                    onTextureViewGone()
                                    return true
                                }

                                override fun onSurfaceTextureUpdated(texture: SurfaceTexture) {}
                            }
                            // Touch → VD input injection via shell
                            setOnTouchListener(android.view.View.OnTouchListener { v: android.view.View, event: android.view.MotionEvent ->
                                val vdX = (event.x / v.width * displayManager.width).toInt()
                                val vdY = (event.y / v.height * displayManager.height).toInt()
                                val dispId = displayManager.displayId
                                if (event.action == android.view.MotionEvent.ACTION_UP) {
                                    Thread {
                                        try {
                                            Runtime.getRuntime().exec(arrayOf(
                                                "sh", "-c",
                                                "input -d $dispId tap $vdX $vdY"
                                            )).waitFor()
                                        } catch (_: Exception) {}
                                    }.start()
                                }
                                true
                            })
                        }
                    },
                    modifier = Modifier.fillMaxSize()
                )
            }
        } else {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f)
                    .background(MaterialTheme.colorScheme.surfaceVariant),
                contentAlignment = Alignment.Center
            ) {
                Text("Start projection first", color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }

    DisposableEffect(Unit) {
        onDispose {
            displayManager?.detachSurface()
            onTextureViewGone()
        }
    }
}

@Composable
fun AccessibilityTreeTab(
    nodeTree: List<NodeInfo>,
    isA11y: Boolean,
    isActive: Boolean,
    onFetchTree: () -> Unit,
    onClickByText: (String) -> Unit,
    onClickByViewId: (String) -> Unit
) {
    var clickTarget by remember { mutableStateOf("") }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = onFetchTree, enabled = isA11y && isActive) { Text("Fetch Tree") }
        }

        Spacer(modifier = Modifier.height(8.dp))

        // Click injection controls
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            OutlinedTextField(
                value = clickTarget,
                onValueChange = { clickTarget = it },
                label = { Text("Text or ViewId") },
                modifier = Modifier.weight(1f),
                singleLine = true
            )
            Button(
                onClick = { onClickByText(clickTarget) },
                enabled = clickTarget.isNotEmpty() && isA11y
            ) { Text("Click Text") }
            Button(
                onClick = { onClickByViewId(clickTarget) },
                enabled = clickTarget.isNotEmpty() && isA11y
            ) { Text("Click ID") }
        }

        Spacer(modifier = Modifier.height(8.dp))

        // Tree display
        if (nodeTree.isEmpty()) {
            Box(
                modifier = Modifier.fillMaxWidth().weight(1f),
                contentAlignment = Alignment.Center
            ) {
                Text(
                    when {
                        !isA11y -> "Enable accessibility service first"
                        !isActive -> "Start projection first"
                        else -> "Press Fetch Tree to load"
                    },
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        } else {
            Text(
                "${nodeTree.sumOf { it.flatten().size }} nodes",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f)
                    .background(MaterialTheme.colorScheme.surfaceVariant)
                    .padding(8.dp)
            ) {
                Text(
                    text = nodeTree.joinToString("\n") { it.toTreeString() },
                    fontFamily = FontFamily.Monospace,
                    fontSize = 10.sp,
                    lineHeight = 14.sp,
                    modifier = Modifier.verticalScroll(rememberScrollState()),
                    overflow = TextOverflow.Visible
                )
            }
        }
    }
}

private fun parseBounds(input: String): List<Int>? {
    // [x1,y1][x2,y2] format (from accessibility dumps)
    val bracketRegex = Regex("""\[(\d+),(\d+)]\[(\d+),(\d+)]""")
    bracketRegex.find(input)?.let { match ->
        return match.groupValues.drop(1).map { it.toInt() }
    }
    // x1,y1,x2,y2 format
    val parts = input.split(",").mapNotNull { it.trim().toIntOrNull() }
    if (parts.size == 4) return parts
    return null
}
