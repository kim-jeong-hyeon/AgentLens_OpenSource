# AgentLens - Mobile AI Assistant

An Android app that serves as the client for the AgentLens Mobile AI Assistant. It creates a virtual display where an AI agent (powered by the M3A backend in `../standalone_m3a/`) operates apps on the user's behalf — without occupying the physical screen. When the agent needs to communicate, the app plays TTS and shows overlay popups mirroring the virtual display content.

**Target environment:** Android Emulator (root shell available) or rooted device

## Key Features

- **Virtual Display** — Creates a 1080x1920 virtual display via MediaProjection. Apps launched on it are invisible on the physical screen.
- **Background Service** — Runs as a foreground service. User can press Home and use their phone while the agent works.
- **WebSocket Client** — Connects to the M3A Python backend server. Sends the virtual display ID on connect; receives visualization and TTS commands.
- **Overlay Popups** — Shows `TYPE_APPLICATION_OVERLAY` windows that mirror the virtual display content. Supports full-screen (`show_app`) and cropped region (`show_element`) modes. Dismissible via close button or swipe-down.
- **Text-to-Speech** — Plays agent messages via Android TTS when the agent uses `speak` or `ask` actions.
- **App Launching** — Lists installed apps and launches them on the virtual display using root shell (`su -c am start --display`), with API and non-root fallbacks.
- **Live Preview** — Streams the virtual display content onto a TextureView in the app's own UI.
- **Crop & Zoom** — Zoom into a specific region of the virtual display by entering accessibility bounds (supports `[x1,y1][x2,y2]` format).
- **Screenshot Capture** — Captures frames from the live preview (TextureView) or headless mode (ImageReader). Saves to `Pictures/AgentLens/`.
- **Accessibility Tree** — Reads the full UI node tree of apps running on the virtual display via AccessibilityService.
- **Click Injection** — Injects clicks by text label, view ID, or node reference using `performAction(ACTION_CLICK)`.

## Architecture

```
MainActivity (Compose UI, 4 tabs — server URL input, overlay perm check)
  └── MainViewModel (state management)
        ├── ProjectionForegroundService (orchestrator — keeps MediaProjection alive)
        │     ├── VirtualDisplayManager (creates VirtualDisplay + ImageReader)
        │     ├── AgentWebSocketClient (OkHttp WebSocket — connects to M3A backend)
        │     ├── OverlayManager (TYPE_APPLICATION_OVERLAY with TextureView mirroring)
        │     └── TtsManager (Android TextToSpeech wrapper)
        ├── AppLauncher (lists apps, launches on virtual display)
        ├── ScreenshotManager (saves Bitmap to MediaStore)
        └── AgentLensAccessibilityService (reads node tree, injects clicks)
```

## Files

| File | Purpose |
|---|---|
| `MainActivity.kt` | Compose UI with 4 tabs: Control, Apps, Display, A11y Tree. Server URL input, overlay permission flow, MediaProjection consent. Shows WebSocket connection status. |
| `MainViewModel.kt` | Central state management. Exposes StateFlows for projection state, display ID, screenshots, node tree, app list, and crop bounds. Orchestrates all features. |
| `service/ProjectionForegroundService.kt` | Foreground service orchestrator. Holds MediaProjection, VirtualDisplayManager, WebSocket client, TTS manager, and overlay manager. Dispatches WebSocket commands to TTS and overlay. |
| `service/AgentWebSocketClient.kt` | OkHttp WebSocket client. Connects to M3A Python backend, sends `register` with display ID, receives visualization commands. Exponential backoff reconnection. Dispatches callbacks to main thread. |
| `overlay/OverlayManager.kt` | Creates `TYPE_APPLICATION_OVERLAY` windows with TextureView for mirroring virtual display content. `showAppOverlay()` mirrors full display; `showElementOverlay(bounds)` uses Matrix transform to crop and zoom to a specific UI element region. Close button + swipe-down dismiss. |
| `tts/TtsManager.kt` | Wraps `android.speech.tts.TextToSpeech`. Handles async init with pending queue. |
| `virtualdisplay/VirtualDisplayManager.kt` | Creates the virtual display (1080x1920, 320dpi) with `VIRTUAL_DISPLAY_FLAG_PUBLIC \| OWN_CONTENT_ONLY`. Supports dual-mode rendering: TextureView (live) or ImageReader (headless). Exposes `attachSurface()`/`detachSurface()` for overlay mirroring. |
| `virtualdisplay/OverlayDisplayHelper.kt` | Utility for creating trusted overlay displays via `Settings.Global` (alternative to MediaProjection displays). |
| `apps/AppLauncher.kt` | Queries launchable apps via PackageManager. Launches with a 3-tier fallback: root shell, `setLaunchDisplayId()` API, regular shell. All use `--windowingMode 1 --user 0`. |
| `screenshot/ScreenshotManager.kt` | Saves bitmaps to `Pictures/AgentLens/` via MediaStore (API 29+) or external storage (API 26-28). |
| `accessibility/AgentLensAccessibilityService.kt` | Singleton AccessibilityService. Filters windows by display ID (API 30+) or package name. Builds recursive node tree. Click injection via `performAction(ACTION_CLICK)`. |
| `accessibility/NodeInfo.kt` | Data class for serialized accessibility nodes. Includes `flatten()` and `toTreeString()` utilities. |
| `res/xml/accessibility_service_config.xml` | Service config: `flagReportViewIds`, `flagRetrieveInteractiveWindows`, `flagIncludeNotImportantViews`, `canRetrieveWindowContent`, `canPerformGestures`. |

## Permissions

| Permission | Reason |
|---|---|
| `FOREGROUND_SERVICE` | Required for the projection service |
| `FOREGROUND_SERVICE_MEDIA_PROJECTION` | Service type for MediaProjection |
| `QUERY_ALL_PACKAGES` | List all installed apps |
| `POST_NOTIFICATIONS` | Foreground service notification (Android 13+) |
| `SYSTEM_ALERT_WINDOW` | Required for overlay popups (visualization). Prompted on first launch. |
| `INTERNET` | WebSocket connection to the M3A Python backend |

The AccessibilityService must be enabled manually in Settings > Accessibility.

## Build & Run

```bash
# Build and install
./gradlew installDebug

# Grant overlay display permission (optional, for OverlayDisplayHelper)
adb shell pm grant com.marvis.agentlens android.permission.WRITE_SECURE_SETTINGS
```

**Min SDK:** 26 (Android 8.0)
**Compile SDK:** 36
**Dependencies:** Jetpack Compose, Material3, Lifecycle ViewModel, OkHttp 4.12

## Usage

### As Mobile AI Assistant (with M3A backend)

1. Start the M3A backend server: `python run_agent.py --server --goal "..." --package com.android.settings`
2. Open the app. On first launch, grant the overlay permission when prompted.
3. In the **Control** tab, enter the server URL (default: `ws://10.0.2.2:8765` for emulator, or `ws://<host_ip>:8765` for real device).
4. Tap **Start Display** and grant screen capture consent.
5. The app creates a virtual display, connects to the backend, and sends the display ID.
6. The status card shows "Server: Connected". You can now press Home — the service runs in background.
7. The backend launches the target app on the virtual display and starts the agent.
8. When the agent speaks or asks with visualization, an overlay popup appears showing the virtual display content, and TTS plays the message.
9. Dismiss the overlay with the X button or swipe down.

### Standalone (without M3A backend)

1. Open the app, go to **Control** tab, leave the server URL empty or ignore it, tap **Start Display** and grant screen capture consent.
2. Go to **Apps** tab, tap an app to launch it on the virtual display.
3. Go to **Display** tab to see the live preview. Use **Capture** to take a screenshot.
4. Enter bounds (e.g. `[0,500][1080,1500]`) and tap **Crop** to zoom into a region. **Reset** to restore full view.
5. Enable the accessibility service in system settings, then go to **A11y Tree** tab to inspect the node tree and inject clicks.

## Known Limitations

- `setLaunchDisplayId()` throws SecurityException on stock devices — root shell bypass works on emulators and rooted devices.
- `dispatchGesture()` targets the default display only — click injection uses node-based `performAction(ACTION_CLICK)` instead.
- `AccessibilityWindowInfo.getDisplayId()` requires API 30+ — on API 26-29, filtering falls back to package name matching.
- MediaProjection is single-use on Android 14+ — must re-request if revoked.
