package com.watchrec.app.util

import org.junit.Assert.*
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/**
 * cleanupExpiredUploads 纯函数单元测试。
 *
 * 不依赖 Android，直接跑在 JVM。
 */
class CleanupExpiredUploadsTest {

    @get:Rule
    val tempFolder = TemporaryFolder()

    companion object {
        /** 固定一个 "当前时间"，所有样本相对它构造 */
        private const val NOW = 1_720_000_000_000L  // 2024-07-03 16:26:40 UTC
        private const val DAYS_MS = 24 * 60 * 60 * 1000L
        private const val RETENTION = 3
    }

    private lateinit var dir: File

    @Before
    fun setup() {
        dir = tempFolder.newFolder("recordings")
    }

    // ── 辅助方法 ────────────────────────────────────────────

    /**
     * 创建测试样本文件。
     * @param name 文件名
     * @param uploaded 是否创建 .uploaded 标记
     * @param lastModifiedMs 可选：手动设置文件修改时间（兜底场景用）
     */
    private fun createSample(
        name: String,
        uploaded: Boolean,
        lastModifiedMs: Long? = null
    ): File {
        val file = File(dir, name)
        file.writeBytes(byteArrayOf(0x00, 0x01)) // 几字节假内容
        if (uploaded) {
            File(dir, "$name.uploaded").writeBytes(byteArrayOf(0x00))
        }
        lastModifiedMs?.let { file.setLastModified(it) }
        return file
    }

    private fun assertExists(name: String, expected: Boolean, msg: String) {
        val file = File(dir, name)
        assertEquals("$msg — 文件存在性", expected, file.exists())
    }

    // ── 测试样本 ────────────────────────────────────────────

    @Test
    fun `A - 4 days old + uploaded - should DELETE`() {
        val ts = NOW - 4 * DAYS_MS
        createSample("recording_${ts}_5000.m4a", uploaded = true)

        val deleted = FileUtils.cleanupExpiredUploads(dir, NOW, RETENTION)

        assertEquals("删除计数", 1, deleted)
        assertExists("recording_${ts}_5000.m4a", false, "m4a")
        assertExists("recording_${ts}_5000.m4a.uploaded", false, "marker")
    }

    @Test
    fun `B - 4 days old + NO marker - should KEEP`() {
        val ts = NOW - 4 * DAYS_MS
        createSample("recording_${ts}_5000.m4a", uploaded = false)

        val deleted = FileUtils.cleanupExpiredUploads(dir, NOW, RETENTION)

        assertEquals("删除计数", 0, deleted)
        assertExists("recording_${ts}_5000.m4a", true, "m4a")
    }

    @Test
    fun `C - 1 day old + uploaded - should KEEP`() {
        val ts = NOW - 1 * DAYS_MS
        createSample("recording_${ts}_5000.m4a", uploaded = true)

        val deleted = FileUtils.cleanupExpiredUploads(dir, NOW, RETENTION)

        assertEquals("删除计数", 0, deleted)
        assertExists("recording_${ts}_5000.m4a", true, "m4a")
        assertExists("recording_${ts}_5000.m4a.uploaded", true, "marker")
    }

    @Test
    fun `D - 1 day old + NO marker - should KEEP`() {
        val ts = NOW - 1 * DAYS_MS
        createSample("recording_${ts}_5000.m4a", uploaded = false)

        val deleted = FileUtils.cleanupExpiredUploads(dir, NOW, RETENTION)

        assertEquals("删除计数", 0, deleted)
        assertExists("recording_${ts}_5000.m4a", true, "m4a")
    }

    @Test
    fun `E - boundary just under 3 days + uploaded - should KEEP`() {
        // 3天差5分钟 → 应保留
        val ts = NOW - (3 * DAYS_MS - 5 * 60 * 1000)
        createSample("recording_${ts}_5000.m4a", uploaded = true)

        val deleted = FileUtils.cleanupExpiredUploads(dir, NOW, RETENTION)

        assertEquals("删除计数", 0, deleted)
        assertExists("recording_${ts}_5000.m4a", true, "m4a")
        assertExists("recording_${ts}_5000.m4a.uploaded", true, "marker")
    }

    @Test
    fun `F - boundary just over 3 days + uploaded - should DELETE`() {
        // 3天多5分钟 → 应删除
        val ts = NOW - (3 * DAYS_MS + 5 * 60 * 1000)
        createSample("recording_${ts}_5000.m4a", uploaded = true)

        val deleted = FileUtils.cleanupExpiredUploads(dir, NOW, RETENTION)

        assertEquals("删除计数", 1, deleted)
        assertExists("recording_${ts}_5000.m4a", false, "m4a")
        assertExists("recording_${ts}_5000.m4a.uploaded", false, "marker")
    }

    @Test
    fun `G - unparseable filename + uploaded + old lastModified - should DELETE via fallback`() {
        // 文件名无合法时间戳，靠 lastModified 兜底
        val fourDaysAgo = NOW - 4 * DAYS_MS
        createSample("recording_abc.m4a", uploaded = true, lastModifiedMs = fourDaysAgo)

        val deleted = FileUtils.cleanupExpiredUploads(dir, NOW, RETENTION)

        assertEquals("删除计数", 1, deleted)
        assertExists("recording_abc.m4a", false, "m4a")
        assertExists("recording_abc.m4a.uploaded", false, "marker")
    }

    // ── 综合场景 ────────────────────────────────────────────

    @Test
    fun `mixed - only expired+uploaded gets deleted`() {
        val ts4d = NOW - 4 * DAYS_MS
        val ts1d = NOW - 1 * DAYS_MS

        // A: 4天 + uploaded → 删
        createSample("recording_${ts4d}_1000.m4a", uploaded = true)
        // B: 4天 + 无标记 → 保留
        createSample("recording_${ts4d}_2000.m4a", uploaded = false)
        // C: 1天 + uploaded → 保留
        createSample("recording_${ts1d}_3000.m4a", uploaded = true)

        val deleted = FileUtils.cleanupExpiredUploads(dir, NOW, RETENTION)

        assertEquals("删除计数", 1, deleted)
        assertExists("recording_${ts4d}_1000.m4a", false, "A: 应删")
        assertExists("recording_${ts4d}_2000.m4a", true, "B: 应保留")
        assertExists("recording_${ts1d}_3000.m4a", true, "C: 应保留")
    }

    @Test
    fun `empty directory - returns 0`() {
        val deleted = FileUtils.cleanupExpiredUploads(dir, NOW, RETENTION)
        assertEquals(0, deleted)
    }

    @Test
    fun `non-m4a files are ignored`() {
        val fourDaysAgo = NOW - 4 * DAYS_MS
        val file = File(dir, "recording_${fourDaysAgo}_1000.mp3")
        file.writeBytes(byteArrayOf(0x00))
        File(dir, "recording_${fourDaysAgo}_1000.mp3.uploaded").writeBytes(byteArrayOf(0x00))

        val deleted = FileUtils.cleanupExpiredUploads(dir, NOW, RETENTION)
        assertEquals(0, deleted)
        assertTrue(file.exists())
    }
}
