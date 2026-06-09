package com.watchrec.app.uploader

import android.content.Context
import android.util.Log
import java.security.KeyStore
import java.security.cert.CertificateFactory
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.TrustManagerFactory

/**
 * 自签名证书钉扎。
 *
 * 从 res/raw/server_cert.crt 加载证书，构建只信任它的 SSLSocketFactory。
 * init() 是幂等的，可从任意入口（Activity / WorkManager / Service）反复调用。
 */
object SslHelper {

    private const val TAG = "SslHelper"

    private var factory: SSLSocketFactory? = null
    private var verifier: HostnameVerifier? = null
    private var initialized = false

    /**
     * 幂等初始化。重复调用直接返回。
     */
    @Synchronized
    fun init(context: Context) {
        if (initialized) return
        try {
            val cf = CertificateFactory.getInstance("X.509")
            val cert = context.resources.openRawResource(
                context.resources.getIdentifier("server_cert", "raw", context.packageName)
            ).use { cf.generateCertificate(it) }

            val ks = KeyStore.getInstance(KeyStore.getDefaultType()).apply { load(null) }
            ks.setCertificateEntry("vps", cert)

            val tmf = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm()).apply {
                init(ks)
            }

            val ssl = SSLContext.getInstance("TLS").apply {
                init(null, tmf.trustManagers, null)
            }

            factory = ssl.socketFactory
            verifier = HostnameVerifier { hostname, _ ->
                hostname == "202.189.23.245"
            }
            initialized = true
            Log.d(TAG, "SSL pinning initialized")
        } catch (e: Exception) {
            Log.e(TAG, "SSL init failed", e)
        }
    }

    fun getFactory(): SSLSocketFactory? = factory

    fun getVerifier(): HostnameVerifier? = verifier
}
