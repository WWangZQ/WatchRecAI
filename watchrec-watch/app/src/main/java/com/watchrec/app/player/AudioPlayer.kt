package com.watchrec.app.player

import android.media.MediaPlayer
import android.util.Log

/**
 * MediaPlayer 封装，提供播放/暂停/停止控制和进度查询。
 */
class AudioPlayer {

    companion object {
        private const val TAG = "AudioPlayer"
    }

    var isPlaying = false
        private set
    var isPaused = false
        private set
    var isPrepared = false
        private set

    /** 当前播放的文件路径 */
    var currentFilePath: String? = null
        private set

    var onPrepared: ((duration: Int) -> Unit)? = null
    var onCompletion: (() -> Unit)? = null
    var onError: ((what: Int, extra: Int) -> Unit)? = null

    private var player: MediaPlayer? = null

    /**
     * 加载并开始播放指定文件。
     */
    fun play(filePath: String) {
        stop() // 先释放之前的播放器

        currentFilePath = filePath
        player = MediaPlayer().apply {
            setDataSource(filePath)
            prepareAsync()
        }

        player?.setOnPreparedListener {
            isPrepared = true
            isPlaying = true
            isPaused = false
            it.start()
            onPrepared?.invoke(it.duration)
            Log.d(TAG, "Playback started: $filePath")
        }

        player?.setOnCompletionListener {
            isPlaying = false
            isPaused = false
            onCompletion?.invoke()
            Log.d(TAG, "Playback completed")
        }

        player?.setOnErrorListener { _, what, extra ->
            Log.e(TAG, "Playback error: what=$what, extra=$extra")
            onError?.invoke(what, extra)
            true
        }
    }

    /**
     * 暂停播放。
     */
    fun pause() {
        if (isPlaying && isPrepared) {
            try {
                player?.pause()
                isPlaying = false
                isPaused = true
            } catch (e: Exception) {
                Log.e(TAG, "Failed to pause", e)
            }
        }
    }

    /**
     * 恢复播放。
     */
    fun resume() {
        if (isPaused && isPrepared) {
            try {
                player?.start()
                isPlaying = true
                isPaused = false
            } catch (e: Exception) {
                Log.e(TAG, "Failed to resume", e)
            }
        }
    }

    /**
     * 停止播放并释放资源。
     */
    fun stop() {
        try {
            player?.apply {
                if (isPlaying || isPaused) {
                    try { stop() } catch (_: Exception) {}
                }
                release()
            }
        } catch (e: Exception) {
            Log.e(TAG, "Error stopping player", e)
        }
        player = null
        isPlaying = false
        isPaused = false
        isPrepared = false
        currentFilePath = null
    }

    /**
     * 跳转到指定位置。
     */
    fun seekTo(positionMs: Int) {
        if (isPrepared) {
            try {
                player?.seekTo(positionMs)
            } catch (e: Exception) {
                Log.e(TAG, "Failed to seek", e)
            }
        }
    }

    /**
     * 获取当前播放位置（毫秒）。
     */
    fun getCurrentPosition(): Int {
        return if (isPrepared) {
            try { player?.currentPosition ?: 0 } catch (_: Exception) { 0 }
        } else 0
    }

    /**
     * 获取总时长（毫秒）。
     */
    fun getDuration(): Int {
        return if (isPrepared) {
            try { player?.duration ?: 0 } catch (_: Exception) { 0 }
        } else 0
    }

    /**
     * 是否正在播放或暂停中（即有活跃的播放会话）。
     */
    fun isActive(): Boolean = isPlaying || isPaused
}
