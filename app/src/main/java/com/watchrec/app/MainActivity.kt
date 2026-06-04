package com.watchrec.app

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
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
import com.watchrec.app.recorder.AudioRecorder
import com.watchrec.app.util.TimeUtils

class MainActivity : AppCompatActivity() {

    companion object {
        private const val REQUEST_RECORD_AUDIO = 1001
        private const val TIMER_INTERVAL_MS = 200L
    }

    private lateinit var recordButton: FrameLayout
    private lateinit var micIcon: ImageView
    private lateinit var timerText: TextView
    private lateinit var goToListBtn: TextView

    private lateinit var recorder: AudioRecorder
    private val handler = Handler(Looper.getMainLooper())

    private var isRecordingState = false

    private val timerRunnable = object : Runnable {
        override fun run() {
            if (recorder.isRecording) {
                timerText.text = TimeUtils.formatDuration(recorder.getElapsedMillis())
                handler.postDelayed(this, TIMER_INTERVAL_MS)
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // 全屏沉浸
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        hideSystemUI()

        setContentView(R.layout.activity_main)

        recorder = AudioRecorder(this)

        // 预留回调
        recorder.onRecordingComplete = { filePath ->
            onRecordingComplete(filePath)
        }

        recordButton = findViewById(R.id.recordButton)
        micIcon = findViewById(R.id.micIcon)
        timerText = findViewById(R.id.timerText)
        goToListBtn = findViewById(R.id.goToListBtn)

        recordButton.setOnClickListener {
            if (checkAudioPermission()) {
                toggleRecording()
            }
        }

        goToListBtn.setOnClickListener {
            startActivity(Intent(this, RecordingListActivity::class.java))
        }
    }

    override fun onResume() {
        super.onResume()
        hideSystemUI()
    }

    private fun toggleRecording() {
        if (!isRecordingState) {
            startRecording()
        } else {
            stopRecording()
        }
    }

    private fun startRecording() {
        val success = recorder.start()
        if (!success) {
            Toast.makeText(this, R.string.recording_error, Toast.LENGTH_SHORT).show()
            return
        }

        isRecordingState = true
        recordButton.setBackgroundResource(R.drawable.bg_record_btn_recording)
        micIcon.visibility = View.GONE
        timerText.visibility = View.VISIBLE
        timerText.text = "00:00"

        handler.post(timerRunnable)
    }

    private fun stopRecording() {
        handler.removeCallbacks(timerRunnable)
        recorder.stop()

        isRecordingState = false
        recordButton.setBackgroundResource(R.drawable.bg_record_btn_idle)
        timerText.visibility = View.GONE
        micIcon.visibility = View.VISIBLE
    }

    /**
     * 录音完成回调 —— 预留接口，当前仅日志。
     * 后续在此处添加上传逻辑。
     */
    private fun onRecordingComplete(filePath: String) {
        // TODO: 后续添加上传逻辑
    }

    // ── 权限 ──────────────────────────────────────────────────────

    private fun checkAudioPermission(): Boolean {
        return if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            == PackageManager.PERMISSION_GRANTED
        ) {
            true
        } else {
            ActivityCompat.requestPermissions(
                this,
                arrayOf(Manifest.permission.RECORD_AUDIO),
                REQUEST_RECORD_AUDIO
            )
            false
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
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

    override fun onDestroy() {
        super.onDestroy()
        handler.removeCallbacks(timerRunnable)
        if (recorder.isRecording) {
            recorder.cancel()
        }
    }
}
