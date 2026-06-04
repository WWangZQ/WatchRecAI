package com.watchrec.app

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.media.MediaRecorder
import android.os.Binder
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.util.Log
import androidx.core.app.NotificationCompat
import com.watchrec.app.util.FileUtils
import com.watchrec.app.util.TimeUtils
import java.io.File
import java.io.IOException

/**
 * 前台录音服务。
 *
 * - 持有 WakeLock 防止息屏后 CPU 休眠
 * - 前台通知显示实时计时
 * - Activity 通过 Binder 监听状态变化
 */
class RecordingService : Service() {

    companion object {
        private const val TAG = "RecordingService"
        private const val CHANNEL_ID = "watchrec_recording"
        private const val NOTIFICATION_ID = 1
        private const val SAMPLE_RATE = 44100
        private const val BIT_RATE = 128_000
        private const val NOTIFY_INTERVAL = 1000L

        const val ACTION_START = "com.watchrec.app.ACTION_START"
        const val ACTION_STOP = "com.watchrec.app.ACTION_STOP"

        fun startService(context: Context) {
            val intent = Intent(context, RecordingService::class.java).apply {
                action = ACTION_START
            }
            context.startForegroundService(intent)
        }

        fun stopService(context: Context) {
            val intent = Intent(context, RecordingService::class.java).apply {
                action = ACTION_STOP
            }
            context.startService(intent)
        }
    }

    /** 状态变化监听器，供 bindService 的 Activity 使用 */
    interface StateListener {
        fun onRecordingStarted()
        fun onRecordingStopped(filePath: String)
        fun onRecordingFailed()
    }

    // ── Binder ───────────────────────────────────────────────────

    inner class LocalBinder : Binder() {
        fun getService(): RecordingService = this@RecordingService
    }

    private val binder = LocalBinder()

    override fun onBind(intent: Intent?): IBinder = binder

    // ── 状态（供 Activity 通过 Binder 读取）────────────────────

    var isRecording = false
        private set

    var stateListener: StateListener? = null

    private var startTimeMillis = 0L

    fun getElapsedMillis(): Long {
        if (!isRecording) return 0L
        return System.currentTimeMillis() - startTimeMillis
    }

    // ── 内部字段 ─────────────────────────────────────────────────

    private var recorder: MediaRecorder? = null
    private var currentFile: File? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private val handler = Handler(Looper.getMainLooper())

    private val notifyRunnable = object : Runnable {
        override fun run() {
            if (isRecording) {
                updateNotification()
                handler.postDelayed(this, NOTIFY_INTERVAL)
            }
        }
    }

    // ── 生命周期 ─────────────────────────────────────────────────

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> {
                if (!isRecording) {
                    startRecording()
                }
            }
            ACTION_STOP -> {
                stopRecordingAndShutdown()
            }
            null -> {
                // 系统重建服务，无法恢复状态，直接停止
                stopSelf()
            }
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        super.onDestroy()
        if (isRecording) {
            stopRecordingAndShutdown()
        }
        handler.removeCallbacksAndMessages(null)
        Log.d(TAG, "Service destroyed")
    }

    // ── 录音逻辑 ─────────────────────────────────────────────────

    private fun startRecording() {
        acquireWakeLock()
        val tmpFile = createTempFile()

        recorder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            MediaRecorder(this)
        } else {
            @Suppress("DEPRECATION")
            MediaRecorder()
        }

        try {
            recorder?.apply {
                setAudioSource(MediaRecorder.AudioSource.MIC)
                setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
                setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
                setAudioSamplingRate(SAMPLE_RATE)
                setAudioEncodingBitRate(BIT_RATE)
                setOutputFile(tmpFile.absolutePath)
                prepare()
                start()
            }
            currentFile = tmpFile
            startTimeMillis = System.currentTimeMillis()
            isRecording = true

            showNotification()
            handler.postDelayed(notifyRunnable, NOTIFY_INTERVAL)
            stateListener?.onRecordingStarted()
            Log.d(TAG, "Recording started: ${tmpFile.absolutePath}")
        } catch (e: IOException) {
            Log.e(TAG, "Failed to start recording", e)
            releaseRecorder()
            tmpFile.delete()
            releaseWakeLock()
            stopForeground(true)
            stopSelf()
            stateListener?.onRecordingFailed()
        }
    }

    private fun stopRecordingAndShutdown() {
        val filePath = stopRecording()
        handler.removeCallbacks(notifyRunnable)
        stopForeground(true)
        stopSelf()
        filePath?.let { stateListener?.onRecordingStopped(it) }
    }

    private fun stopRecording(): String? {
        if (!isRecording) return null

        try {
            recorder?.stop()
        } catch (e: RuntimeException) {
            Log.e(TAG, "MediaRecorder.stop() failed", e)
        }

        val durationMs = System.currentTimeMillis() - startTimeMillis
        releaseRecorder()
        isRecording = false
        releaseWakeLock()

        val tmpFile = currentFile ?: return null
        val resultPath = renameWithDuration(tmpFile, durationMs)
        Log.d(TAG, "Recording saved: $resultPath (${durationMs}ms)")
        return resultPath
    }

    // ── 文件辅助 ─────────────────────────────────────────────────

    private fun createTempFile(): File {
        val dir = FileUtils.getRecordingDir(this)
        return File(dir, "recording_${System.currentTimeMillis()}_tmp.m4a")
    }

    private fun renameWithDuration(tmpFile: File, durationMs: Long): String {
        val name = tmpFile.nameWithoutExtension
        val tsStr = name.removePrefix("recording_").removeSuffix("_tmp")
        val finalName = "recording_${tsStr}_$durationMs.m4a"
        val finalFile = File(tmpFile.parentFile, finalName)
        val renamed = tmpFile.renameTo(finalFile)
        return if (renamed) finalFile.absolutePath else tmpFile.absolutePath
    }

    // ── WakeLock ─────────────────────────────────────────────────

    private fun acquireWakeLock() {
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "WatchRec::Recording")
        wakeLock?.acquire(60 * 60 * 1000L) // 最长 1 小时安全上限
    }

    private fun releaseWakeLock() {
        wakeLock?.let {
            if (it.isHeld) it.release()
        }
        wakeLock = null
    }

    // ── MediaRecorder 清理 ──────────────────────────────────────

    private fun releaseRecorder() {
        try {
            recorder?.release()
        } catch (e: Exception) {
            Log.e(TAG, "Error releasing recorder", e)
        }
        recorder = null
    }

    // ── 通知 ─────────────────────────────────────────────────────

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            "录音",
            NotificationManager.IMPORTANCE_LOW
        ).apply {
            description = "WatchRec 录音状态"
            setShowBadge(false)
        }
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(channel)
    }

    private fun showNotification() {
        val pendingIntent = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_mic)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(buildNotifyText())
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setContentIntent(pendingIntent)
            .build()
        startForeground(NOTIFICATION_ID, notification)
    }

    private fun updateNotification() {
        val nm = getSystemService(NotificationManager::class.java)
        val pendingIntent = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_mic)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(buildNotifyText())
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setContentIntent(pendingIntent)
            .build()
        nm.notify(NOTIFICATION_ID, notification)
    }

    private fun buildNotifyText(): String {
        val elapsed = TimeUtils.formatDuration(getElapsedMillis())
        return getString(R.string.notify_recording, elapsed)
    }
}
