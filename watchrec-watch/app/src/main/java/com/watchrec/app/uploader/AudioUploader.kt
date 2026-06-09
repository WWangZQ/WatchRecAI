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
import javax.net.ssl.HttpsURLConnection

/**
 * 录音文件上传工具（单例）。
 *
 * - 支持局域网直传（HTTP）和 VPS 中转（HTTPS + 证书钉扎）
 * - setChunkedStreamingMode 流式上传，内存占用恒定
 * - SslHelper.init 在每个上传入口防御性调用（支持 WorkManager）
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
     * 检测 VPS 是否在线。
     * 会阻塞当前线程，必须在后台线程调用。
     */
    fun isServerOnline(context: Context): Boolean {
        SslHelper.init(context)
        return try {
            val conn = openConnection("${Config.VPS_URL}/health", "GET")
            conn.connectTimeout = 5_000
            conn.readTimeout = 5_000
            val ok = conn.responseCode == 200
            conn.disconnect()
            if (ok) lastError = null
            ok
        } catch (e: Exception) {
            val msg = "${e.javaClass.simpleName}: ${e.message}"
            lastError = msg
            Log.d(TAG, "VPS offline: $msg")
            false
        }
    }

    /**
     * 上传单个文件（自动选路）。
     * 会阻塞当前线程，必须在后台线程调用。
     */
    fun upload(file: File, context: Context): Boolean {
        val target = UploadRouter.resolve(context)
        return upload(file, context, target)
    }

    /**
     * 上传单个文件（使用已解析的目标，批量调用时避免重复选路）。
     */
    fun upload(file: File, context: Context, target: UploadTarget): Boolean {
        SslHelper.init(context)
        if (isUploaded(file)) return true
        return try {
            val ok = doUpload(file, target.baseUrl)
            if (ok) {
                markAsUploaded(file)
                lastError = null
            }
            ok
        } catch (e: Exception) {
            val msg = "${e.javaClass.simpleName}: ${e.message}"
            lastError = msg
            Log.e(TAG, "Upload failed: ${file.name} → $target — $msg", e)
            false
        }
    }

    /**
     * 异步上传单个文件（不阻塞调用方）。
     * 在 executor 线程中解析选路目标。
     */
    fun uploadAsync(file: File, context: Context) {
        executor.execute {
            val target = UploadRouter.resolve(context)
            val success = upload(file, context, target)
            onUploadComplete?.invoke(file.name, success)
        }
    }

    /**
     * 扫描录音目录，上传所有未标记 .uploaded 的文件。
     * 选路一批解析一次。
     */
    fun uploadPendingFiles(context: Context) {
        executor.execute {
            SslHelper.init(context)
            val target = UploadRouter.resolve(context)
            Log.d(TAG, "Upload target: $target")

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
                val success = upload(file, context, target)
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

    /**
     * 构建 HTTP(S) 连接。
     * LAN 走明文 HttpURLConnection（networkSecurityConfig 全局放行）。
     * VPS 走 HTTPS + SslHelper 证书钉扎。
     * 两条路都带 Authorization: Bearer token。
     */
    private fun openConnection(urlStr: String, method: String): HttpURLConnection {
        val conn = URL(urlStr).openConnection() as HttpURLConnection
        conn.requestMethod = method
        conn.setRequestProperty("Authorization", "Bearer ${Config.APP_TOKEN}")
        conn.connectTimeout = 10_000
        conn.readTimeout = 60_000

        // 仅 HTTPS 时才钉扎证书
        if (conn is HttpsURLConnection) {
            SslHelper.getFactory()?.let { conn.sslSocketFactory = it }
            SslHelper.getVerifier()?.let { conn.hostnameVerifier = it }
        }
        return conn
    }

    /**
     * 流式上传文件到目标地址。
     * setChunkedStreamingMode 保证内存占用恒定，不会因大文件 OOM。
     */
    private fun doUpload(file: File, baseUrl: String): Boolean {
        val conn = openConnection("$baseUrl/upload", "POST")

        try {
            conn.doOutput = true
            conn.setChunkedStreamingMode(16384)
            conn.setRequestProperty("Content-Type", "multipart/form-data; boundary=$BOUNDARY")

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
                Log.d(TAG, "Uploaded: ${file.name} → $baseUrl")
            } else {
                lastError = "HTTP $code"
                Log.e(TAG, "Upload failed: HTTP $code for ${file.name} → $baseUrl")
            }
            return ok
        } finally {
            conn.disconnect()
        }
    }
}
