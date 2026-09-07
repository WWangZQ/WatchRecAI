package com.watchrec.app.uploader

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import java.net.Inet4Address
import org.json.JSONObject

sealed interface UploadTarget {
    val baseUrl: String
    val token: String
    data class LAN(override val baseUrl: String, override val token: String) : UploadTarget
    data class VPS(override val baseUrl: String, override val token: String) : UploadTarget
}

object UploadRouter {
    private var cachedTarget: UploadTarget? = null
    private var cachedConfig: Connection? = null
    private var cacheTime = 0L
    @Synchronized fun invalidate() { cachedTarget = null; cachedConfig = null; cacheTime = 0 }
    @Synchronized fun resolve(context: Context): UploadTarget {
        Config.init(context)
        val cfg = Config.current()
        val now = System.currentTimeMillis()
        cachedTarget?.let { if (cachedConfig == cfg && now - cacheTime < 60_000) return it }
        val target = doResolve(context, cfg)
        cachedTarget = target; cachedConfig = cfg; cacheTime = now
        return target
    }
    private fun healthy(base: String, token: String): Boolean {
        if (base.isBlank()) return false
        val conn = SslHelper.open("$base/health", token)
        return try {
            conn.connectTimeout = 1500; conn.readTimeout = 1500
            conn.responseCode == 200 && JSONObject(conn.inputStream.bufferedReader().use { it.readText() }).optString("status") == "alive"
        } catch (e: Exception) { false } finally { conn.disconnect() }
    }
    private fun doResolve(context: Context, cfg: Connection): UploadTarget {
        if (!cfg.configured) return UploadTarget.VPS("", "")
        if (healthy(cfg.lanUrl, cfg.token)) return UploadTarget.LAN(cfg.lanUrl, cfg.token)
        if (cfg.vpsUrl.isBlank()) return UploadTarget.LAN(cfg.lanUrl, cfg.token)
        val conn = SslHelper.open("${cfg.vpsUrl}/lan-info", cfg.token)
        try {
            conn.connectTimeout = 2000; conn.readTimeout = 2000
            if (conn.responseCode == 200) {
                val info = JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
                val ip = info.optString("lan_ip", "")
                val port = info.optInt("port", 0)
                val watchIp = getWatchWifiIp(context)
                if (port in 1..65535 && watchIp != null && sameSubnet24(watchIp, ip)) {
                    val base = "http://$ip:$port"
                    if (healthy(base, cfg.token)) return UploadTarget.LAN(base, cfg.token)
                }
            }
        } catch (e: Exception) { /* VPS 上传自行处理网络失败 */ }
        finally { conn.disconnect() }
        return UploadTarget.VPS(cfg.vpsUrl, cfg.token)
    }
    fun getWatchWifiIp(context: Context): String? {
        return try {
            val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
            val network = cm.activeNetwork ?: return null
            if (cm.getNetworkCapabilities(network)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) != true) return null
            cm.getLinkProperties(network)?.linkAddresses?.firstOrNull {
                it.address is Inet4Address && !it.address.isLoopbackAddress
            }?.address?.hostAddress
        } catch (e: Exception) { null }
    }
    private fun sameSubnet24(a: String, b: String): Boolean {
        val x = a.split('.'); val y = b.split('.')
        return x.size == 4 && y.size == 4 && y.all { (it.toIntOrNull() ?: -1) in 0..255 } && x.take(3) == y.take(3)
    }
}
