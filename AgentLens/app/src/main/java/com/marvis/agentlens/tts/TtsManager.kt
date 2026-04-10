package com.marvis.agentlens.tts

import android.content.Context
import android.media.AudioAttributes
import android.speech.tts.TextToSpeech
import android.util.Log
import java.util.Locale
import java.util.UUID

class TtsManager(context: Context) : TextToSpeech.OnInitListener {

    companion object {
        private const val TAG = "TtsManager"
    }

    private val tts = TextToSpeech(context, this)
    private var isReady = false
    private val pendingQueue = mutableListOf<String>()

    private val audioAttributes = AudioAttributes.Builder()
        .setUsage(AudioAttributes.USAGE_ASSISTANT)
        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
        .build()

    override fun onInit(status: Int) {
        if (status == TextToSpeech.SUCCESS) {
            tts.language = Locale.US
            tts.setAudioAttributes(audioAttributes)
            isReady = true
            Log.i(TAG, "TTS initialized")
            // Warm up the TTS engine so the first real speak has no delay
            tts.speak(" ", TextToSpeech.QUEUE_FLUSH, null, "warmup")
            pendingQueue.forEach { speak(it) }
            pendingQueue.clear()
        } else {
            Log.e(TAG, "TTS initialization failed with status: $status")
        }
    }

    fun speak(text: String) {
        if (!isReady) {
            pendingQueue.add(text)
            return
        }
        tts.speak(text, TextToSpeech.QUEUE_ADD, null, UUID.randomUUID().toString())
    }

    fun shutdown() {
        tts.stop()
        tts.shutdown()
        Log.i(TAG, "TTS shut down")
    }
}
