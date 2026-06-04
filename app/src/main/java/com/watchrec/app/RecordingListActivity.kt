package com.watchrec.app

import android.media.MediaMetadataRetriever
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.view.WindowManager
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.SeekBar
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.watchrec.app.adapter.RecordingAdapter
import com.watchrec.app.model.RecordingItem
import com.watchrec.app.player.AudioPlayer
import com.watchrec.app.util.FileUtils
import com.watchrec.app.util.TimeUtils
import java.io.File

class RecordingListActivity : AppCompatActivity() {

    private lateinit var recyclerView: RecyclerView
    private lateinit var emptyText: TextView
    private lateinit var playbackPanel: LinearLayout
    private lateinit var playbackFileName: TextView
    private lateinit var playbackTime: TextView
    private lateinit var playPauseBtn: ImageButton
    private lateinit var stopBtn: ImageButton
    private lateinit var playbackSeekBar: SeekBar

    private lateinit var adapter: RecordingAdapter
    private val player = AudioPlayer()
    private val handler = Handler(Looper.getMainLooper())

    /** 用户正在拖动进度条时，暂停自动刷新 */
    private var userSeeking = false

    private val progressRunnable = object : Runnable {
        override fun run() {
            if (player.isActive() && !userSeeking) {
                val pos = player.getCurrentPosition()
                val dur = player.getDuration()
                playbackSeekBar.progress = if (dur > 0) (pos * 1000 / dur) else 0
                playbackTime.text = "${TimeUtils.formatDuration(pos.toLong())} / ${TimeUtils.formatDuration(dur.toLong())}"
            }
            if (player.isActive()) {
                handler.postDelayed(this, 300L)
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        hideSystemUI()

        setContentView(R.layout.activity_list)

        recyclerView = findViewById(R.id.recyclerView)
        emptyText = findViewById(R.id.emptyText)
        playbackPanel = findViewById(R.id.playbackPanel)
        playbackFileName = findViewById(R.id.playbackFileName)
        playbackTime = findViewById(R.id.playbackTime)
        playPauseBtn = findViewById(R.id.playPauseBtn)
        stopBtn = findViewById(R.id.stopBtn)
        playbackSeekBar = findViewById(R.id.playbackSeekBar)

        // 列表
        adapter = RecordingAdapter(
            onItemClick = { item -> onItemClicked(item) },
            onItemLongClick = { item -> onItemLongClicked(item) }
        )
        recyclerView.layoutManager = LinearLayoutManager(this)
        recyclerView.adapter = adapter

        // 播放器回调
        player.onPrepared = { duration ->
            playbackSeekBar.max = 1000
            playPauseBtn.setImageResource(R.drawable.ic_pause)
            playbackTime.text = "00:00 / ${TimeUtils.formatDuration(duration.toLong())}"
            handler.post(progressRunnable)
        }

        player.onCompletion = {
            resetPlaybackUI()
        }

        player.onError = { _, _ ->
            Toast.makeText(this, R.string.playback_error, Toast.LENGTH_SHORT).show()
            resetPlaybackUI()
        }

        // 播放/暂停按钮
        playPauseBtn.setOnClickListener {
            when {
                player.isPlaying -> player.pause()
                player.isPaused -> player.resume()
            }
            updatePlayPauseIcon()
        }

        // 停止按钮
        stopBtn.setOnClickListener {
            player.stop()
            resetPlaybackUI()
        }

        // 进度条拖动
        playbackSeekBar.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(sb: SeekBar?, progress: Int, fromUser: Boolean) {
                if (fromUser && player.isActive()) {
                    val dur = player.getDuration()
                    val target = dur * progress / 1000
                    playbackTime.text = "${TimeUtils.formatDuration(target.toLong())} / ${TimeUtils.formatDuration(dur.toLong())}"
                }
            }

            override fun onStartTrackingTouch(sb: SeekBar?) {
                userSeeking = true
            }

            override fun onStopTrackingTouch(sb: SeekBar?) {
                if (player.isActive()) {
                    val dur = player.getDuration()
                    val target = dur * (sb?.progress ?: 0) / 1000
                    player.seekTo(target)
                }
                userSeeking = false
            }
        })
    }

    override fun onResume() {
        super.onResume()
        hideSystemUI()
        loadRecordings()
    }

    private fun loadRecordings() {
        val recordings = FileUtils.listRecordings(this)
        adapter.submitList(recordings)

        if (recordings.isEmpty()) {
            emptyText.visibility = View.VISIBLE
            recyclerView.visibility = View.GONE
        } else {
            emptyText.visibility = View.GONE
            recyclerView.visibility = View.VISIBLE
        }
    }

    // ── 列表事件 ─────────────────────────────────────────────────

    private fun onItemClicked(item: RecordingItem) {
        val file = File(item.filePath)
        if (!file.exists()) {
            Toast.makeText(this, "文件不存在", Toast.LENGTH_SHORT).show()
            return
        }

        // 如果点击的已是当前播放文件：切暂停/播放
        if (player.currentFilePath == item.filePath && player.isActive()) {
            if (player.isPlaying) player.pause() else player.resume()
            updatePlayPauseIcon()
            return
        }

        // 开始播放新文件
        player.stop()
        showPlaybackPanel(item)
        player.play(item.filePath)
    }

    private fun onItemLongClicked(item: RecordingItem) {
        AlertDialog.Builder(this)
            .setTitle(R.string.delete_confirm_title)
            .setMessage(R.string.delete_confirm_message)
            .setPositiveButton(R.string.delete) { _, _ ->
                if (player.currentFilePath == item.filePath) {
                    player.stop()
                    resetPlaybackUI()
                }
                FileUtils.deleteRecording(File(item.filePath))
                loadRecordings()
            }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    // ── 播放面板 ─────────────────────────────────────────────────

    private fun showPlaybackPanel(item: RecordingItem) {
        playbackPanel.visibility = View.VISIBLE
        playbackFileName.text = item.fileName
        playbackTime.text = "00:00 / --:--"
        playbackSeekBar.progress = 0
        playPauseBtn.setImageResource(R.drawable.ic_pause)
    }

    private fun resetPlaybackUI() {
        handler.removeCallbacks(progressRunnable)
        playbackPanel.visibility = View.GONE
        playbackSeekBar.progress = 0
        playPauseBtn.setImageResource(R.drawable.ic_play)
    }

    private fun updatePlayPauseIcon() {
        playPauseBtn.setImageResource(
            if (player.isPlaying) R.drawable.ic_pause else R.drawable.ic_play
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

    override fun onDestroy() {
        super.onDestroy()
        handler.removeCallbacks(progressRunnable)
        player.stop()
    }
}
