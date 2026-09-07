package com.watchrec.app.uploader

import android.content.Context
import java.net.HttpURLConnection
import java.net.URL

/** 使用 Android 系统信任链和标准主机名校验，不内置私人证书。 */
object SslHelper {
    fun init(context: Context) { Config.init(context) }
    fun open(url: String, token: String, method: String = "GET"): HttpURLConnection {
        return (URL(url).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            instanceFollowRedirects = false
            setRequestProperty("Authorization", "Bearer $token")
            connectTimeout = 5_000
            readTimeout = 8_000
        }
    }
}
