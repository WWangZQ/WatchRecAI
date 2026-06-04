package com.watchrec.app.util

import android.content.Context
import com.watchrec.app.model.RecordingItem
import java.io.File

object FileUtils {

    private const val DIR_NAME = "recordings"

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
}
