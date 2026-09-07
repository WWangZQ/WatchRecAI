package com.watchrec.app

import android.graphics.Color
import android.os.Bundle
import android.text.InputType
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.watchrec.app.uploader.Config
import com.watchrec.app.uploader.SslHelper
import org.json.JSONObject
import javax.net.ssl.SSLException

class SettingsActivity : AppCompatActivity() {
    private lateinit var content: LinearLayout
    private lateinit var vps: EditText
    private lateinit var lan: EditText
    private lateinit var token: EditText
    private lateinit var days: EditText
    private lateinit var status: TextView
    private lateinit var test: Button
    private lateinit var save: Button

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()
    private fun label(text: String, size: Float = 14f): TextView = TextView(this).also {
        it.text = text; it.textSize = size; it.setTextColor(Color.rgb(45, 43, 40))
        it.setPadding(0, dp(10), 0, dp(4)); content.addView(it)
    }
    private fun field(hint: String, type: Int = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI): EditText = EditText(this).also {
        it.hint = hint; it.inputType = type; it.textSize = 14f
        it.setTextColor(Color.rgb(45, 43, 40)); it.setHintTextColor(Color.rgb(120, 116, 110))
        it.setSingleLine(true); content.addView(it, LinearLayout.LayoutParams(-1, dp(52)))
    }
    private fun button(text: String, action: () -> Unit): Button = Button(this).also {
        it.text = text; it.isAllCaps = false; it.textSize = 14f
        it.setOnClickListener { action() }; content.addView(it, LinearLayout.LayoutParams(-1, dp(52)))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Config.init(this)
        content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(12), dp(16), dp(28))
            setBackgroundColor(Color.rgb(250, 249, 247))
        }
        val scroll = ScrollView(this).apply { addView(content, ViewGroup.LayoutParams(-1, -2)) }
        setContentView(scroll)
        label("连接设置", 20f)
        label("不设置也能录音。连接后会自动重试上传保存在手表里的录音。")
        label("VPS 服务器地址")
        vps = field("https://rec.example.com")
        label("可填域名或 IP:端口，必须带 http:// 或 https://。不用加 /upload。公网建议 HTTPS。")
        label("连接密钥 APP_TOKEN")
        token = field("与 VPS、电脑填写相同密钥", InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD)
        label("电脑直传地址（选填）")
        lan = field("http://192.168.1.20:18765")
        label("同一 Wi-Fi 下可直传。电脑要先启用局域网直传。只用 VPS 时留空；只在家用也可只填此项。HTTP 会明文传输。")
        label("上传成功后保留天数")
        days = field("0", InputType.TYPE_CLASS_NUMBER)
        label("0 = 不自动删除。1–365 = 清理已上传且录制时间超过天数的录音。未上传录音始终保留。")
        val current = Config.current()
        vps.setText(current.vpsUrl); lan.setText(current.lanUrl)
        days.setText(Config.retentionDays.toString())
        if (current.token.isNotBlank()) token.hint = "密钥已保存；留空保留"
        status = label("")
        test = button("测试连接") { testConnection() }
        save = button("保存并返回") {
            try {
                Config.save(vps.text.toString(), lan.text.toString(), token.text.toString().ifBlank { Config.current().token },
                    days.text.toString().toIntOrNull() ?: -1)
                finish()
            } catch (e: Exception) { status.text = e.message }
        }
        button("返回，暂不连接") { finish() }
    }

    private fun testConnection() {
        val base: String
        val key: String
        try {
            base = Config.normalizeUrl(vps.text.toString()).ifBlank { Config.normalizeUrl(lan.text.toString()) }
            key = Config.validateToken(token.text.toString().ifBlank { Config.current().token })
            require(base.isNotBlank() && key.isNotBlank()) { "请先填地址和密钥。" }
        } catch (e: Exception) { status.text = e.message; return }
        test.isEnabled = false; save.isEnabled = false; status.text = "正在测试…"
        Thread {
            val message = try {
                val conn = SslHelper.open("$base/health", key)
                try {
                    when (conn.responseCode) {
                        200 -> {
                            val result = JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
                            if (result.optString("status") == "alive" && result.optString("service") in listOf("watchrec-vps", "watchrec-pc"))
                                "连接成功！点击保存后开始上传。" else "地址能访问，但不是此版本的 WatchRec 服务。"
                        }
                        401 -> "连接密钥不匹配，请与 VPS 和电脑核对。"
                        in 300..399 -> "地址发生重定向，请填写最终 HTTPS 地址。"
                        else -> "服务器返回 HTTP ${conn.responseCode}，请核对地址和端口。"
                    }
                } finally { conn.disconnect() }
            } catch (e: SSLException) { "证书校验失败。请使用有效的公共证书，并核对手表日期和服务器域名。" }
            catch (e: Exception) { "连接失败，请检查 Wi-Fi、地址和防火墙。" }
            runOnUiThread {
                if (!isFinishing && !isDestroyed) { status.text = message; test.isEnabled = true; save.isEnabled = true }
            }
        }.start()
    }
}
