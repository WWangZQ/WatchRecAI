package com.watchrec.app.util

import com.watchrec.app.uploader.Config
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class ConnectionSettingsTest {
    @Test fun domainIpAndPrefixAddressesAreAccepted() {
        assertEquals("https://rec.example.com", Config.normalizeUrl(" https://rec.example.com/ "))
        assertEquals("http://192.168.1.2:18765", Config.normalizeUrl("http://192.168.1.2:18765"))
        assertEquals("https://example.com:8443/watchrec", Config.normalizeUrl("https://example.com:8443/watchrec/"))
        assertEquals("http://[::1]:8765", Config.normalizeUrl("http://[::1]:8765"))
        assertEquals("", Config.normalizeUrl(""))
    }
    @Test fun unsafeAndIncompleteAddressesAreRejected() {
        for (value in listOf("example.com", "ftp://host", "http://user:pass@host", "http://host:0", "http://host:65536", "http://host?secret=1", "http://host/#fragment", "http://host/a b")) {
            assertThrows(value, IllegalArgumentException::class.java) { Config.normalizeUrl(value) }
        }
    }
    @Test fun tokenMustBeSuitableForAuthorizationHeader() {
        assertEquals("sample-token-12345678", Config.validateToken(" sample-token-12345678 "))
        for (value in listOf("short", "secret-token-1234\nInjection", "中文密钥不能放入此处1234567890")) {
            assertThrows(IllegalArgumentException::class.java) { Config.validateToken(value) }
        }
    }
}
