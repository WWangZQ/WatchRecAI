package com.watchrec.app.model

import java.io.File

/**
 * 录音文件的数据模型。
 * 文件名格式：recording_<timestamp>_<durationMs>.m4a
 */
data class RecordingItem(
    val fileName: String,
    val filePath: String,
    val duration: Long,   // 毫秒，录制时记录的时长
    val timestamp: Long   // 录制开始时间戳，从文件名解析
) {
    companion object {
        private const val PREFIX = "recording_"
        private const val SUFFIX = ".m4a"

        /**
         * 生成录音文件名，包含时长信息。
         */
        fun generateFileName(durationMs: Long): String {
            return "$PREFIX${System.currentTimeMillis()}_$durationMs$SUFFIX"
        }

        /**
         * 从文件创建 RecordingItem，解析文件名中的时间戳和时长。
         * 兼容旧格式 recording_<timestamp>.m4a（时长为 0）。
         */
        fun fromFile(file: File): RecordingItem? {
            val name = file.nameWithoutExtension
            if (!name.startsWith(PREFIX)) return null
            val rest = name.removePrefix(PREFIX)

            // 新格式：<timestamp>_<duration>
            val lastUnderscore = rest.lastIndexOf('_')
            if (lastUnderscore > 0) {
                val tsStr = rest.substring(0, lastUnderscore)
                val durStr = rest.substring(lastUnderscore + 1)
                val timestamp = tsStr.toLongOrNull()
                val duration = durStr.toLongOrNull()
                if (timestamp != null && duration != null) {
                    return RecordingItem(
                        fileName = file.name,
                        filePath = file.absolutePath,
                        duration = duration,
                        timestamp = timestamp
                    )
                }
            }

            // 旧格式：<timestamp>
            val timestamp = rest.toLongOrNull() ?: return null
            return RecordingItem(
                fileName = file.name,
                filePath = file.absolutePath,
                duration = 0L,
                timestamp = timestamp
            )
        }
    }
}
