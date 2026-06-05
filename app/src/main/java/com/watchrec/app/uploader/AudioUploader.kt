package com.watchrec.app.uploader

import android.content.Context
import android.util.Log
import com.watchrec.app.util.FileUtils
import java.io.DataOutputStream
import java.io.File
import java.io.FileInputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

/**
 * 录音文件上传工具（单例）。
 *
 * - 用 HttpURLConnection 发送 multipart/form-data POST
 * - 上传成功后创建 .uploaded 标记文件
 * - 独立线程池，不依赖 Activity 或 Service 生命周期
 */
object AudioUploader {

    private const val TAG = "AudioUploader"
    private const val BOUNDARY_PREFIX = "----WatchRecBoundary"
    private val BOUNDARY = "$BOUNDARY_PREFIX${System.currentTimeMillis()}"
    private const val LINE_END = "\r\n"

    private val executor = Executors.newSingleThreadExecutor()

    /** 最近一次失败原因（供 UI 或调试读取） */
    var lastError: String? = null
        private set

    /** 上传完成回调（在后台线程调用，如需更新 UI 请切到主线程） */
    var onUploadComplete: ((fileName: String, success: Boolean) -> Unit)? = null

    // ── 公开接口 ─────────────────────────────────────────────────

    /**
     * 检测服务器是否在线。
     * 会阻塞当前线程，必须在后台线程调用。
     */
    fun isServerOnline(): Boolean {
        return try {
            val url = URL("${Config.SERVER_URL}/health")
            val conn = url.openConnection() as HttpURLConnection
            conn.connectTimeout = 3000
            conn.readTimeout = 3000
            conn.requestMethod = "GET"
            val ok = conn.responseCode == 200
            conn.disconnect()
            if (ok) lastError = null
            ok
        } catch (e: Exception) {
            val msg = "${e.javaClass.simpleName}: ${e.message}"
            lastError = msg
            Log.d(TAG, "Server offline: $msg")
            false
        }
    }

    /**
     * 上传单个文件。
     * 会阻塞当前线程，必须在后台线程调用。
     *
     * @return true 上传成功
     */
    fun upload(file: File): Boolean {
        if (isUploaded(file)) return true
        return try {
            val ok = doUpload(file)
            if (ok) {
                markAsUploaded(file)
                lastError = null
            }
            ok
        } catch (e: Exception) {
            val msg = "${e.javaClass.simpleName}: ${e.message}"
            lastError = msg
            Log.e(TAG, "Upload failed: ${file.name} — $msg", e)
            false
        }
    }

    /**
     * 异步上传单个文件（不阻塞调用方）。
     */
    fun uploadAsync(file: File) {
        executor.execute {
            val success = upload(file)
            onUploadComplete?.invoke(file.name, success)
        }
    }

    /**
     * 扫描录音目录，上传所有未标记 .uploaded 的文件。
     * 在后台线程执行。
     */
    fun uploadPendingFiles(context: Context) {
        executor.execute {
            val dir = FileUtils.getRecordingDir(context)
            val pending = dir.listFiles()
                ?.filter {
                    it.isFile
                        && it.name.endsWith(".m4a")
                        && !isUploaded(it)
                }
                ?.sortedByDescending { it.lastModified() }
                ?: return@execute

            if (pending.isEmpty()) return@execute
            Log.d(TAG, "Found ${pending.size} pending file(s)")

            for (file in pending) {
                val success = upload(file)
                onUploadComplete?.invoke(file.name, success)
            }
        }
    }

    /**
     * 文件是否已上传（检查 .uploaded 标记文件）。
     */
    fun isUploaded(file: File): Boolean {
        return File(file.absolutePath + ".uploaded").exists()
    }

    // ── 内部实现 ─────────────────────────────────────────────────

    private fun markAsUploaded(file: File) {
        File(file.absolutePath + ".uploaded").createNewFile()
    }

    private fun doUpload(file: File): Boolean {
        val url = URL("${Config.SERVER_URL}/upload")
        val conn = url.openConnection() as HttpURLConnection

        try {
            conn.doOutput = true
            conn.requestMethod = "POST"
            conn.setChunkedStreamingMode(16384)
            conn.setRequestProperty("Content-Type", "multipart/form-data; boundary=$BOUNDARY")
            conn.connectTimeout = 10_000
            conn.readTimeout = 60_000

            DataOutputStream(conn.outputStream).use { out ->
                out.writeBytes("--$BOUNDARY$LINE_END")
                out.writeBytes("Content-Disposition: form-data; name=\"file\"; filename=\"${file.name}\"$LINE_END")
                out.writeBytes("Content-Type: audio/mp4$LINE_END")
                out.writeBytes(LINE_END)

                FileInputStream(file).use { fis ->
                    val buffer = ByteArray(8192)
                    var bytesRead: Int
                    while (fis.read(buffer).also { bytesRead = it } != -1) {
                        out.write(buffer, 0, bytesRead)
                    }
                }

                out.writeBytes(LINE_END)
                out.writeBytes("--$BOUNDARY--$LINE_END")
                out.flush()
            }

            val code = conn.responseCode
            val ok = code in 200..299
            if (ok) {
                Log.d(TAG, "Uploaded: ${file.name}")
            } else {
                lastError = "HTTP $code"
                Log.e(TAG, "Upload failed: HTTP $code for ${file.name}")
            }
            return ok
        } finally {
            conn.disconnect()
        }
    }
}
