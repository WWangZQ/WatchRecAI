package com.watchrec.app.util

import java.util.Locale

object TimeUtils {

    /**
     * 将毫秒格式化为 MM:SS。
     */
    fun formatDuration(millis: Long): String {
        val totalSeconds = millis / 1000
        val minutes = totalSeconds / 60
        val seconds = totalSeconds % 60
        return String.format(Locale.US, "%02d:%02d", minutes, seconds)
    }

    /**
     * 将时间戳格式化为 yyyy-MM-dd HH:mm。
     */
    fun formatDateTime(timestamp: Long): String {
        val sdf = java.text.SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.getDefault())
        return sdf.format(java.util.Date(timestamp))
    }
}
