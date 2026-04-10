package com.marvis.agentlens.virtualdisplay

import android.content.Context
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.graphics.SurfaceTexture
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.Image
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.PixelCopy
import android.view.Surface
import android.view.SurfaceView

class VirtualDisplayManager(
    private val context: Context,
    private val mediaProjection: MediaProjection? = null,
    val width: Int = 1080,
    val height: Int = 1920,
    val dpi: Int = 320
) {
    companion object {
        private const val TAG = "VirtualDisplayManager"
        private const val DISPLAY_NAME = "AgentLens-VirtualDisplay"
    }

    private var virtualDisplay: VirtualDisplay? = null
    private var imageReader: ImageReader? = null
    private var surfaceTexture: SurfaceTexture? = null
    private var textureSurface: Surface? = null
    private var isSurfaceViewAttached = false
    // Surface currently driving the VD when a live preview is attached.
    // Saved in attachSurface() so captureScreenshot() can restore it after
    // its temporary swap to an ImageReader. Without this, captureScreenshot
    // used to leave the VD pointing at a closed ImageReader surface and the
    // VD stopped producing frames forever.
    private var attachedSurface: Surface? = null

    val displayId: Int
        get() = virtualDisplay?.display?.displayId ?: -1

    val imageReaderSurface: Surface?
        get() = imageReader?.surface

    fun create() {
        val displayManager = context.getSystemService(Context.DISPLAY_SERVICE) as DisplayManager

        // SurfaceTexture-backed Surface as primary VD output. Using ImageReader
        // here causes canHostTasks=false on the VD, which makes
        // `am start --display N` silently fall back to display 0.
        val st = SurfaceTexture(0).apply {
            setDefaultBufferSize(width, height)
        }
        surfaceTexture = st
        textureSurface = Surface(st)

        // Persistent ImageReader for headless screenshot capture is created on
        // demand by [captureScreenshot]; we no longer wire it to the VD's
        // primary surface.

        // Do NOT include VIRTUAL_DISPLAY_FLAG_PRESENTATION: it sets canHostTasks=false
        // so `am start --display` silently falls back to display 0.
        val flags = DisplayManager.VIRTUAL_DISPLAY_FLAG_OWN_CONTENT_ONLY or
                DisplayManager.VIRTUAL_DISPLAY_FLAG_PUBLIC

        virtualDisplay = displayManager.createVirtualDisplay(
            DISPLAY_NAME,
            width,
            height,
            dpi,
            textureSurface,
            flags,
            object : VirtualDisplay.Callback() {
                override fun onPaused() { Log.d(TAG, "Virtual display paused") }
                override fun onResumed() { Log.d(TAG, "Virtual display resumed") }
                override fun onStopped() { Log.d(TAG, "Virtual display stopped") }
            },
            null
        )

        val display = virtualDisplay?.display
        Log.i(TAG, "Virtual display created: id=$displayId, flags=${display?.flags}, name=${display?.name}")
    }

    fun attachSurface(surface: Surface) {
        virtualDisplay?.surface = surface
        attachedSurface = surface
        isSurfaceViewAttached = true
        Log.d(TAG, "Attached live preview surface")
    }

    fun detachSurface() {
        virtualDisplay?.surface = textureSurface
        attachedSurface = null
        isSurfaceViewAttached = false
        Log.d(TAG, "Detached live preview, back to SurfaceTexture")
    }

    fun captureFromSurfaceView(surfaceView: SurfaceView, callback: (Bitmap?) -> Unit) {
        val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        val handler = Handler(Looper.getMainLooper())
        try {
            PixelCopy.request(surfaceView, bitmap, { result ->
                if (result == PixelCopy.SUCCESS) {
                    callback(bitmap)
                } else {
                    Log.w(TAG, "PixelCopy failed with result: $result")
                    bitmap.recycle()
                    callback(null)
                }
            }, handler)
        } catch (e: Exception) {
            Log.e(TAG, "PixelCopy exception", e)
            bitmap.recycle()
            callback(null)
        }
    }

    /**
     * Capture screenshot by temporarily switching VD surface to an ImageReader.
     * Works regardless of whether overlay/preview is attached.
     */
    @Synchronized
    fun captureScreenshot(): Bitmap? {
        val vd = virtualDisplay ?: run {
            Log.w(TAG, "captureScreenshot: virtualDisplay is null")
            return null
        }
        val reader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 2)
        return try {
            // Switch VD output to temp ImageReader.
            vd.surface = reader.surface
            // Drain any stale buffers that might already be in the queue from
            // a previous capture so we cannot accidentally return an old
            // frame as the "new" one.
            while (true) {
                val stale = reader.acquireLatestImage() ?: break
                stale.close()
            }
            // Force the VirtualDisplay to invalidate and re-render. Without
            // this, a steady-state app like DoorDash never pushes a fresh
            // buffer to the new ImageReader because nothing on screen is
            // actually changing — the swap delivers nothing. A 1-pixel
            // resize round-trip is the cheapest way to make SurfaceFlinger
            // composite the entire VD onto the new consumer surface.
            try {
                vd.resize(width, height - 1, dpi)
                vd.resize(width, height, dpi)
            } catch (e: Exception) {
                Log.w(TAG, "captureScreenshot: vd.resize trick failed", e)
            }
            Log.d(TAG, "captureScreenshot: switched to ImageReader, waiting for frame...")
            // Wait for a frame to arrive
            var image: Image? = null
            for (i in 1..10) {
                Thread.sleep(200)
                image = reader.acquireLatestImage()
                if (image != null) {
                    Log.d(TAG, "captureScreenshot: got frame after ${i * 200}ms")
                    break
                }
            }
            if (image == null) {
                Log.w(TAG, "captureScreenshot: no frame received after retries")
                return null
            }
            val plane = image.planes[0]
            val buffer = plane.buffer
            val pixelStride = plane.pixelStride
            val rowStride = plane.rowStride
            val rowPadding = rowStride - pixelStride * width

            val bitmapWidth = width + rowPadding / pixelStride
            val bitmap = Bitmap.createBitmap(bitmapWidth, height, Bitmap.Config.ARGB_8888)
            bitmap.copyPixelsFromBuffer(buffer)
            image.close()

            val result = if (rowPadding > 0) {
                val cropped = Bitmap.createBitmap(bitmap, 0, 0, width, height)
                bitmap.recycle()
                cropped
            } else {
                bitmap
            }
            result
        } catch (e: Exception) {
            Log.e(TAG, "captureScreenshot failed", e)
            null
        } finally {
            // Always restore a live surface so the VD keeps producing frames
            // after this capture. If a SurfaceView preview is attached we put
            // its surface back; otherwise fall back to the SurfaceTexture
            // primary surface. Failing to do this leaves the VD pointing at
            // the about-to-be-closed ImageReader surface and every subsequent
            // capture returns null.
            val restore = if (isSurfaceViewAttached) attachedSurface else textureSurface
            if (restore != null) {
                vd.surface = restore
            } else {
                Log.w(TAG, "captureScreenshot: no surface to restore (attached=$isSurfaceViewAttached)")
            }
            reader.close()
        }
    }

    fun captureFromImageReader(): Bitmap? {
        val reader = imageReader ?: return null
        var image: Image? = null
        try {
            // acquireLatestImage is non-blocking. If no frame has arrived yet
            // (e.g. just after the VD was created or launching a new activity),
            // poll for up to 1.5s.
            for (i in 1..15) {
                image = reader.acquireLatestImage()
                if (image != null) break
                Thread.sleep(100)
            }
            if (image == null) {
                Log.w(TAG, "captureFromImageReader: no frame after 1.5s")
                return null
            }
            val plane = image.planes[0]
            val buffer = plane.buffer
            val pixelStride = plane.pixelStride
            val rowStride = plane.rowStride
            val rowPadding = rowStride - pixelStride * width

            val bitmapWidth = width + rowPadding / pixelStride
            val bitmap = Bitmap.createBitmap(bitmapWidth, height, Bitmap.Config.ARGB_8888)
            bitmap.copyPixelsFromBuffer(buffer)

            return if (rowPadding > 0) {
                val cropped = Bitmap.createBitmap(bitmap, 0, 0, width, height)
                bitmap.recycle()
                cropped
            } else {
                bitmap
            }
        } finally {
            image?.close()
        }
    }

    fun release() {
        virtualDisplay?.release()
        virtualDisplay = null
        imageReader?.close()
        imageReader = null
        textureSurface?.release()
        textureSurface = null
        surfaceTexture?.release()
        surfaceTexture = null
        mediaProjection?.stop()
        Log.i(TAG, "Virtual display released")
    }
}
