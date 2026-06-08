package com.watchrec.app.uploader

/**
 * 上传相关配置。
 * SERVER_URL 保留给 4b 局域网直传用。
 */
object Config {
    /** 电脑端局域网地址（4b 直传用，暂时未使用） */
    const val SERVER_URL = "http://10.129.35.132:8765"

    /** VPS 中转地址 */
    const val VPS_URL = "https://202.189.23.245:27312"

    /** 鉴权 token */
    const val APP_TOKEN = "CHANGE_ME"
}
