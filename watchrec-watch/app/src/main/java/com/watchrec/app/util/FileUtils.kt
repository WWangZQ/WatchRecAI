package com.watchrec.app.util

import android.content.Context
import android.util.Log
import com.watchrec.app.model.RecordingItem
import java.io.File

object FileUtils {

    private const val TAG = "FileUtils"
    private const val DIR_NAME = "recordings"

    /** 已上传录音保留天数 */
    const val MAX_RETENTION_DAYS = 3

    /**
     * 获取录音文件存储目录，不存在则创建。
     */
    fun getRecordingDir(context: Context): File {
        val dir = File(context.getExternalFilesDir(null), DIR_NAME)
        if (!dir.exists()) dir.mkdirs()
        return dir
    }

    /**
     * 列出所有录音文件，按时间倒序（最新在前）。
     */
    fun listRecordings(context: Context): List<RecordingItem> {
        val dir = getRecordingDir(context)
        return dir.listFiles()
            ?.filter { it.isFile && it.name.endsWith(".m4a") }
            ?.mapNotNull { RecordingItem.fromFile(it) }
            ?.sortedByDescending { it.timestamp }
            ?: emptyList()
    }

    /**
     * 删除指定录音文件。
     */
    fun deleteRecording(file: File): Boolean {
        return file.delete()
    }

    /**
     * 清理已上传且超过保留期的录音（Android 包装）。
     */
    fun cleanupUploadedRecordings(context: Context) {
        val dir = getRecordingDir(context)
        val deleted = cleanupExpiredUploads(dir, System.currentTimeMillis(), MAX_RETENTION_DAYS)
        if (deleted > 0) {
            Log.i(TAG, "Cleanup: deleted $deleted expired uploaded recording(s)")
        }
    }

    // ── 纯函数（可测试，无 Android 依赖）─────────────────────

    /**
     * 清理已上传且超过保留期的录音。
     *
     * 安全原则：只删同时满足两个条件的文件——
     *   (a) 存在 .uploaded 标记（确认上传成功）
     *   (b) 录制时间距今 > retentionDays 天
     *
     * @param dir 录音目录
     * @param nowMs 当前时间戳（毫秒），测试时可注入
     * @param retentionDays 保留天数
     * @return 删除的文件数量
     */
    fun cleanupExpiredUploads(dir: File, nowMs: Long, retentionDays: Int): Int {
        val cutoff = nowMs - retentionDays * 24 * 60 * 60 * 1000L
        var deleted = 0

        dir.listFiles()
            ?.filter { it.isFile && it.name.endsWith(".m4a") }
            ?.forEach { file ->
                val marker = File(file.absolutePath + ".uploaded")
                if (!marker.exists()) return@forEach

                val timestamp = parseTimestamp(file.name)
                val recordedAt = timestamp ?: file.lastModified()

                if (recordedAt < cutoff) {
                    if (file.delete()) {
                        marker.delete()
                        deleted++
                    }
                }
            }

        return deleted
    }

    /**
     * 从文件名解析录制时间戳（毫秒）。
     * 格式：recording_<timestamp>_<durationMs>.m4a
     * 解析失败返回 null。
     */
    fun parseTimestamp(fileName: String): Long? {
        val name = fileName.substringBeforeLast('.')
        val prefix = "recording_"
        if (!name.startsWith(prefix)) return null
        val rest = name.removePrefix(prefix)
        val firstUnderscore = rest.indexOf('_')
        if (firstUnderscore <= 0) return null
        return rest.substring(0, firstUnderscore).toLongOrNull()
    }
}
