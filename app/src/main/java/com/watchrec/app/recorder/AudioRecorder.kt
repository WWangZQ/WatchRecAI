package com.watchrec.app.recorder

import android.content.Context
import android.media.MediaRecorder
import android.os.Build
import android.util.Log
import com.watchrec.app.util.FileUtils
import java.io.File
import java.io.IOException

/**
 * MediaRecorder 封装，提供 start/stop 控制。
 *
 * 录音参数：AAC / .m4a / 44100Hz / 128kbps。
 *
 * 预留 [onRecordingComplete] 回调，将来用于上传等后处理。
 */
class AudioRecorder(private val context: Context) {

    companion object {
        private const val TAG = "AudioRecorder"
        private const val SAMPLE_RATE = 44100
        private const val BIT_RATE = 128_000
    }

    var isRecording = false
        private set

    /** 录音开始时间戳（毫秒），用于计算实时时长 */
    var startTimeMillis = 0L
        private set

    /** 录音完成回调，预留接口 */
    var onRecordingComplete: ((filePath: String) -> Unit)? = null

    private var recorder: MediaRecorder? = null
    private var currentFile: File? = null

    /**
     * 开始录音。如果已经在录音则忽略。
     */
    fun start(): Boolean {
        if (isRecording) return false

        val dir = FileUtils.getRecordingDir(context)
        // 临时文件名，stop() 时重命名为包含时长的最终文件名
        val tmpName = "recording_${System.currentTimeMillis()}_tmp.m4a"
        val file = File(dir, tmpName)
        currentFile = file

        recorder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            MediaRecorder(context)
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
                setOutputFile(file.absolutePath)
                prepare()
                start()
            }
            isRecording = true
            startTimeMillis = System.currentTimeMillis()
            Log.d(TAG, "Recording started: ${file.absolutePath}")
            return true
        } catch (e: IOException) {
            Log.e(TAG, "Failed to start recording", e)
            releaseRecorder()
            file.delete()
            return false
        }
    }

    /**
     * 停止录音，计算时长，重命名为最终文件名。
     * @return 录音文件路径，失败返回 null。
     */
    fun stop(): String? {
        if (!isRecording) return null

        try {
            recorder?.stop()
        } catch (e: RuntimeException) {
            Log.e(TAG, "MediaRecorder.stop() failed", e)
        }

        val durationMs = System.currentTimeMillis() - startTimeMillis
        releaseRecorder()
        isRecording = false

        val tmpFile = currentFile ?: return null

        // 从临时文件名提取 timestamp，生成最终文件名
        // 临时名：recording_<timestamp>_tmp.m4a
        val name = tmpFile.nameWithoutExtension // recording_<ts>_tmp
        val tsStr = name.removePrefix("recording_").removeSuffix("_tmp")
        val finalName = "recording_${tsStr}_$durationMs.m4a"
        val finalFile = File(tmpFile.parentFile, finalName)

        val renamed = tmpFile.renameTo(finalFile)
        val resultPath = if (renamed) finalFile.absolutePath else tmpFile.absolutePath

        Log.d(TAG, "Recording saved: $resultPath (${durationMs}ms)")
        onRecordingComplete?.invoke(resultPath)

        return resultPath
    }

    /**
     * 取消录音并删除文件。
     */
    fun cancel() {
        if (!isRecording) return
        releaseRecorder()
        currentFile?.delete()
        isRecording = false
    }

    /**
     * 获取当前录音时长（毫秒）。
     */
    fun getElapsedMillis(): Long {
        if (!isRecording) return 0L
        return System.currentTimeMillis() - startTimeMillis
    }

    private fun releaseRecorder() {
        try {
            recorder?.release()
        } catch (e: Exception) {
            Log.e(TAG, "Error releasing recorder", e)
        }
        recorder = null
    }
}
