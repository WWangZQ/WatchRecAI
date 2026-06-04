package com.watchrec.app.recorder

import android.content.Context
import android.util.Log
import com.watchrec.app.util.FileUtils
import java.io.File

/**
 * 录音文件工具类。
 *
 * MediaRecorder 录音逻辑已迁移到 [com.watchrec.app.RecordingService]。
 * 此类仅保留文件命名辅助方法，供 Service 和其他模块复用。
 */
object AudioRecorder {

    private const val TAG = "AudioRecorder"

    /**
     * 创建录音临时文件。
     * 录音结束后由 Service 调用 [renameWithDuration] 生成最终文件名。
     */
    fun createTempFile(context: Context): File {
        val dir = FileUtils.getRecordingDir(context)
        return File(dir, "recording_${System.currentTimeMillis()}_tmp.m4a")
    }

    /**
     * 将临时文件重命名为包含时长的最终文件名。
     * 格式：recording_<timestamp>_<durationMs>.m4a
     *
     * @return 最终文件路径
     */
    fun renameWithDuration(tmpFile: File, durationMs: Long): String {
        val name = tmpFile.nameWithoutExtension
        val tsStr = name.removePrefix("recording_").removeSuffix("_tmp")
        val finalName = "recording_${tsStr}_$durationMs.m4a"
        val finalFile = File(tmpFile.parentFile, finalName)
        val renamed = tmpFile.renameTo(finalFile)
        val result = if (renamed) finalFile.absolutePath else tmpFile.absolutePath
        Log.d(TAG, "Renamed: $result (${durationMs}ms)")
        return result
    }
}
