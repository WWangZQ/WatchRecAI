package com.watchrec.app

import android.content.Context
import android.util.Log
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.watchrec.app.uploader.AudioUploader
import com.watchrec.app.util.FileUtils

/**
 * 后台自动上传 worker。
 *
 * - 每小时执行一次（通过 PeriodicWorkRequest 注册）
 * - 仅在 WiFi（UNMETERED）网络下执行
 * - 如果正在录音，跳过本次执行
 * - 上传完成后自动清理过期录音
 */
class UploadWorker(
    appContext: Context,
    params: WorkerParameters
) : CoroutineWorker(appContext, params) {

    companion object {
        private const val TAG = "UploadWorker"
        const val WORK_NAME = "watchrec_auto_upload"
    }

    override suspend fun doWork(): Result {
        // 如果正在录音，跳过（避免和录音抢资源）
        if (isRecordingActive()) {
            Log.d(TAG, "Recording in progress, skipping upload")
            return Result.retry()
        }

        Log.d(TAG, "Starting background upload")

        // 上传所有待上传的文件（同步执行，阻塞当前 worker 线程）
        uploadPendingSync()

        // 清理已上传且过期的录音
        FileUtils.cleanupUploadedRecordings(applicationContext)

        Log.d(TAG, "Background upload done")
        return Result.success()
    }

    private fun isRecordingActive(): Boolean {
        val dir = FileUtils.getRecordingDir(applicationContext)
        return dir.listFiles()?.any {
            it.name.endsWith("_tmp.m4a")
        } == true
    }

    /**
     * 同步上传所有待上传文件。
     * 传入 applicationContext 确保 SslHelper 初始化（WorkManager 后台运行时没有 Activity）。
     */
    private fun uploadPendingSync() {
        val context = applicationContext
        val dir = FileUtils.getRecordingDir(context)
        val pending = dir.listFiles()
            ?.filter {
                it.isFile
                    && it.name.endsWith(".m4a")
                    && !it.name.endsWith("_tmp.m4a")
                    && !AudioUploader.isUploaded(it)
            }
            ?.sortedByDescending { it.lastModified() }
            ?: return

        if (pending.isEmpty()) return

        Log.d(TAG, "Found ${pending.size} pending file(s)")

        for (file in pending) {
            if (isRecordingActive()) {
                Log.d(TAG, "Recording started mid-upload, aborting")
                return
            }
            val success = AudioUploader.upload(file, context)
            Log.d(TAG, "Upload ${file.name}: ${if (success) "OK" else "FAILED"}")
        }
    }
}
