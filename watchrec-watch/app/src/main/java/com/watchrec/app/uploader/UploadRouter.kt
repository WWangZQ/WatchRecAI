package com.watchrec.app.uploader

import android.content.Context
import android.content.SharedPreferences
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.util.Log
import java.net.HttpURLConnection
import java.net.Inet4Address
import java.net.URL

/**
 * 上传选路：局域网直传 vs VPS 中转。
 *
 * 每批上传解析一次（带 60s TTL 缓存），切换网络后自动重新解析。
 */
sealed interface UploadTarget {
    val baseUrl: String

    data class LAN(override val baseUrl: String) : UploadTarget
    data class VPS(override val baseUrl: String) : UploadTarget
}

object UploadRouter {

    private const val TAG = "UploadRouter"
    private const val CACHE_TTL_MS = 60_000L
    private const val PREFS_NAME = "upload_router"
    private const val KEY_LAST_LAN_IP = "last_lan_ip"

    private var cachedTarget: UploadTarget? = null
    private var cacheTime: Long = 0

    /**
     * 解析上传目标（带 60s TTL 缓存）。
     * 每批上传调一次，同一网络环境内不会重复查询。
     */
    fun resolve(context: Context): UploadTarget {
        val now = System.currentTimeMillis()
        cachedTarget?.let { target ->
            if (now - cacheTime < CACHE_TTL_MS) return target
        }
        val target = doResolve(context)
        cachedTarget = target
        cacheTime = now
        Log.d(TAG, "Resolved: $target")
        return target
    }

    /**
     * 清除缓存（网络切换时可调用）。
     */
    fun invalidate() {
        cachedTarget = null
        cacheTime = 0
    }

    private fun doResolve(context: Context): UploadTarget {
        val vpsBase = Config.VPS_URL

        // 1. 从 VPS 获取电脑局域网信息
        val lanInfo = fetchLanInfo(context) ?: run {
            // VPS 连不上，尝试缓存的 LAN IP 兜底
            val cachedIp = loadLastLanIp(context)
            if (cachedIp != null) {
                Log.d(TAG, "VPS unreachable, trying cached LAN IP: $cachedIp")
                return tryLan(context, cachedIp, 8765) ?: UploadTarget.VPS(vpsBase)
            }
            return UploadTarget.VPS(vpsBase)
        }

        val lanIp = lanInfo.first
        val lanPort = lanInfo.second

        if (lanIp == null) {
            return UploadTarget.VPS(vpsBase)
        }

        // 2. 对比网段
        val watchIp = getWatchWifiIp(context)
        if (watchIp == null || !sameSubnet24(watchIp, lanIp)) {
            Log.d(TAG, "Different subnet: watch=$watchIp, pc=$lanIp → VPS")
            return UploadTarget.VPS(vpsBase)
        }

        // 3. 探测局域网 /health
        return tryLan(context, lanIp, lanPort) ?: UploadTarget.VPS(vpsBase)
    }

    /**
     * 从 VPS /lan-info 获取电脑局域网信息。
     * @return (lan_ip, port) 或 null（VPS 连不上）
     */
    private fun fetchLanInfo(context: Context): Pair<String?, Int>? {
        SslHelper.init(context)
        return try {
            val conn = openVpsConn("/lan-info", "GET")
            conn.connectTimeout = 2_000
            conn.readTimeout = 2_000
            if (conn.responseCode != 200) {
                conn.disconnect()
                return null
            }
            val body = conn.inputStream.bufferedReader().readText()
            conn.disconnect()

            val ip = Regex(""""lan_ip"\s*:\s*"([^"]+)"""").find(body)?.groupValues?.get(1)
            val port = Regex(""""port"\s*:\s*(\d+)""").find(body)?.groupValues?.get(1)?.toIntOrNull() ?: 8765

            if (ip == null || ip == "null") Pair(null, port)
            else Pair(ip, port)
        } catch (e: Exception) {
            Log.d(TAG, "fetchLanInfo failed: ${e.message}")
            null
        }
    }

    /**
     * 探测局域网电脑 /health。
     * @return LAN target 或 null（探不通）
     */
    private fun tryLan(context: Context, ip: String, port: Int): UploadTarget.LAN? {
        return try {
            val url = URL("http://$ip:$port/health")
            val conn = url.openConnection() as HttpURLConnection
            conn.connectTimeout = 1_500
            conn.readTimeout = 1_500
            conn.requestMethod = "GET"
            conn.setRequestProperty("Authorization", "Bearer ${Config.APP_TOKEN}")
            val ok = conn.responseCode == 200
            conn.disconnect()
            if (ok) {
                saveLastLanIp(context, ip)
                UploadTarget.LAN("http://$ip:$port")
            } else null
        } catch (e: Exception) {
            Log.d(TAG, "LAN probe failed ($ip:$port): ${e.message}")
            null
        }
    }

    /**
     * 获取手表当前 WiFi IPv4 地址。
     */
    fun getWatchWifiIp(context: Context): String? {
        try {
            val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
            val network = cm.activeNetwork ?: return null
            val caps = cm.getNetworkCapabilities(network) ?: return null
            if (!caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) return null
            val props = cm.getLinkProperties(network) ?: return null
            return props.linkAddresses
                .filter { it.address is Inet4Address && !it.address.isLoopbackAddress }
                .map { it.address.hostAddress }
                .firstOrNull()
        } catch (e: Exception) {
            Log.d(TAG, "getWatchWifiIp failed: ${e.message}")
            return null
        }
    }

    /**
     * /24 网段比较。
     */
    private fun sameSubnet24(a: String, b: String): Boolean {
        val aParts = a.split(".")
        val bParts = b.split(".")
        if (aParts.size != 4 || bParts.size != 4) return false
        return aParts[0] == bParts[0] && aParts[1] == bParts[1] && aParts[2] == bParts[2]
    }

    // ── VPS 连接（选路专用，复用 SslHelper）──────────────────

    private fun openVpsConn(path: String, method: String): HttpURLConnection {
        val conn = URL("${Config.VPS_URL}$path").openConnection() as HttpURLConnection
        conn.requestMethod = method
        conn.setRequestProperty("Authorization", "Bearer ${Config.APP_TOKEN}")
        if (conn is javax.net.ssl.HttpsURLConnection) {
            SslHelper.getFactory()?.let { conn.sslSocketFactory = it }
            SslHelper.getVerifier()?.let { conn.hostnameVerifier = it }
        }
        return conn
    }

    // ── lastLanIp 持久化（断网兜底）──────────────────────────

    private fun prefs(context: Context): SharedPreferences =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    private fun saveLastLanIp(context: Context, ip: String) {
        prefs(context).edit().putString(KEY_LAST_LAN_IP, ip).apply()
    }

    private fun loadLastLanIp(context: Context): String? =
        prefs(context).getString(KEY_LAST_LAN_IP, null)
}
