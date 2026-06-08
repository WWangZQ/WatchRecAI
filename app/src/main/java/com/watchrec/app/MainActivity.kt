package com.watchrec.app

import android.Manifest
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.view.View
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import com.watchrec.app.uploader.AudioUploader
import com.watchrec.app.util.FileUtils
import com.watchrec.app.util.TimeUtils
import java.util.concurrent.TimeUnit

class MainActivity : AppCompatActivity() {

    companion object {
        private const val REQUEST_RECORD_AUDIO = 1001
        private const val TIMER_INTERVAL_MS = 200L
    }

    private lateinit var recordButton: FrameLayout
    private lateinit var micIcon: ImageView
    private lateinit var timerText: TextView
    private lateinit var goToListBtn: TextView

    private val handler = Handler(Looper.getMainLooper())
    private var service: RecordingService? = null
    private var bound = false

    /** Activity 正在销毁（区分解绑和主动 stopService） */
    private var finishing = false

    // ── Service 绑定 ─────────────────────────────────────────────

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            service = (binder as RecordingService.LocalBinder).getService().also {
                it.stateListener = listener
            }
            bound = true
            syncUIWithService()
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            service?.stateListener = null
            service = null
            bound = false
            handler.removeCallbacks(timerRunnable)
        }
    }

    private val listener = object : RecordingService.StateListener {
        override fun onRecordingStarted() {
            runOnUiThread { setUIRecording() }
        }

        override fun onRecordingStopped(filePath: String) {
            runOnUiThread {
                setUIIdle()
                onRecordingComplete(filePath)
            }
        }

        override fun onRecordingFailed() {
            runOnUiThread {
                setUIIdle()
                Toast.makeText(this@MainActivity, R.string.recording_error, Toast.LENGTH_SHORT).show()
            }
        }
    }

    private val timerRunnable = object : Runnable {
        override fun run() {
            val svc = service
            if (svc != null && svc.isRecording) {
                timerText.text = TimeUtils.formatDuration(svc.getElapsedMillis())
                handler.postDelayed(this, TIMER_INTERVAL_MS)
            }
        }
    }

    // ── 生命周期 ─────────────────────────────────────────────────

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        hideSystemUI()
        setContentView(R.layout.activity_main)

        recordButton = findViewById(R.id.recordButton)
        micIcon = findViewById(R.id.micIcon)
        timerText = findViewById(R.id.timerText)
        goToListBtn = findViewById(R.id.goToListBtn)

        recordButton.setOnClickListener {
            if (checkAudioPermission()) toggleRecording()
        }

        goToListBtn.setOnClickListener {
            startActivity(Intent(this, RecordingListActivity::class.java))
        }

        // 注册后台自动上传（每小时，仅 WiFi）
        scheduleAutoUpload()
        // 清理已上传且过期的录音
        FileUtils.cleanupUploadedRecordings(this)
    }

    override fun onStart() {
        super.onStart()
        bindService(
            Intent(this, RecordingService::class.java),
            connection,
            Context.BIND_AUTO_CREATE
        )
    }

    override fun onResume() {
        super.onResume()
        hideSystemUI()
        syncUIWithService()
        // 重试上传所有未上传的录音
        AudioUploader.uploadPendingFiles(this)
        // 上传后清理过期录音
        FileUtils.cleanupUploadedRecordings(this)
    }

    override fun onStop() {
        super.onStop()
        handler.removeCallbacks(timerRunnable)
        if (bound) {
            service?.stateListener = null
            unbindService(connection)
            bound = false
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        finishing = true
        handler.removeCallbacks(timerRunnable)
    }

    // ── 录音控制 ─────────────────────────────────────────────────

    private fun toggleRecording() {
        val svc = service ?: return
        if (svc.isRecording) {
            RecordingService.stopService(this)
        } else {
            RecordingService.startService(this)
        }
    }

    private fun syncUIWithService() {
        val svc = service ?: return
        if (svc.isRecording) {
            setUIRecording()
        } else {
            setUIIdle()
        }
    }

    private fun setUIRecording() {
        recordButton.setBackgroundResource(R.drawable.bg_record_btn_recording)
        micIcon.visibility = View.GONE
        timerText.visibility = View.VISIBLE
        timerText.text = TimeUtils.formatDuration(service?.getElapsedMillis() ?: 0)
        handler.removeCallbacks(timerRunnable)
        handler.post(timerRunnable)
    }

    private fun setUIIdle() {
        handler.removeCallbacks(timerRunnable)
        recordButton.setBackgroundResource(R.drawable.bg_record_btn_idle)
        timerText.visibility = View.GONE
        micIcon.visibility = View.VISIBLE
    }

    /**
     * 录音完成回调 —— 触发上传。
     */
    private fun onRecordingComplete(filePath: String) {
        val file = java.io.File(filePath)
        AudioUploader.uploadAsync(file, this)
    }

    // ── 权限 ──────────────────────────────────────────────────────

    private fun checkAudioPermission(): Boolean {
        return if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            == PackageManager.PERMISSION_GRANTED
        ) true
        else {
            ActivityCompat.requestPermissions(
                this, arrayOf(Manifest.permission.RECORD_AUDIO), REQUEST_RECORD_AUDIO
            )
            false
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<out String>, grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQUEST_RECORD_AUDIO) {
            if (grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
                toggleRecording()
            } else {
                Toast.makeText(this, R.string.permission_denied, Toast.LENGTH_SHORT).show()
            }
        }
    }

    // ── 后台自动上传 ────────────────────────────────────────────

    private fun scheduleAutoUpload() {
        val constraints = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.UNMETERED) // 仅 WiFi
            // 如需更省电：加 .setRequiresCharging(true) 变为充电时才上传
            .build()

        val request = PeriodicWorkRequestBuilder<UploadWorker>(
            1, TimeUnit.HOURS // 每小时一次
        )
            .setConstraints(constraints)
            .build()

        WorkManager.getInstance(this).enqueueUniquePeriodicWork(
            UploadWorker.WORK_NAME,
            ExistingPeriodicWorkPolicy.KEEP, // 已注册则保留，不重复创建
            request
        )
    }

    // ── 全屏沉浸 ─────────────────────────────────────────────────

    private fun hideSystemUI() {
        window.decorView.systemUiVisibility = (
            View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                or View.SYSTEM_UI_FLAG_FULLSCREEN
                or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                or View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                or View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                or View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
            )
    }
}
