package com.watchrec.app.uploader

import android.content.Context
import android.content.SharedPreferences
import java.net.URI

data class Connection(val vpsUrl: String, val lanUrl: String, val token: String) {
    val configured: Boolean get() = token.isNotBlank() && (vpsUrl.isNotBlank() || lanUrl.isNotBlank())
}

object Config {
    private lateinit var prefs: SharedPreferences
    fun init(context: Context) {
        prefs = context.applicationContext.getSharedPreferences("watchrec_connection", Context.MODE_PRIVATE)
    }
    fun current(): Connection = Connection(
        prefs.getString("vps_url", "") ?: "",
        prefs.getString("lan_url", "") ?: "",
        prefs.getString("token", "") ?: ""
    )
    val retentionDays: Int get() = prefs.getInt("retention_days", 0)
    fun save(vps: String, lan: String, token: String, days: Int) {
        val next = Connection(normalizeUrl(vps), normalizeUrl(lan), validateToken(token))
        require(days in 0..365) { "保留天数填写 0–365，0 表示不自动删除。" }
        require((next.vpsUrl.isBlank() && next.lanUrl.isBlank()) || next.token.isNotBlank()) { "请填写连接密钥。" }
        check(prefs.edit().putString("vps_url", next.vpsUrl).putString("lan_url", next.lanUrl)
            .putString("token", next.token).putInt("retention_days", days).commit()) { "设置保存失败，请重试。" }
        UploadRouter.invalidate()
    }
    fun normalizeUrl(raw: String): String {
        val value = raw.trim().trimEnd('/')
        if (value.isBlank()) return ""
        val uri = try { URI(value) } catch (e: Exception) { throw IllegalArgumentException("服务器地址格式不正确。") }
        require(uri.scheme in listOf("http", "https") && !uri.host.isNullOrBlank()
            && uri.rawUserInfo == null && uri.rawQuery == null && uri.rawFragment == null
            && (uri.port == -1 || uri.port in 1..65535) && !value.contains('\\')) {
            "请填写 http:// 或 https:// 开头的域名或 IP，可加端口，不能含账号、空格或查询参数。"
        }
        return value
    }
    fun validateToken(raw: String): String {
        val token = raw.trim()
        require(token.isBlank() || token.matches(Regex("[A-Za-z0-9_.~-]{16,}"))) { "密钥至少 16 位，使用英文字母、数字或 _ . ~ -。" }
        return token
    }
}
