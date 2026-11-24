package com.hermesvpn.app

import java.net.InetSocketAddress
import java.net.Socket

/**
 * تأخیر واقعیِ رسیدن به سرور: زمان برقراری اتصال TCP. عمداً از ICMP ping
 * استفاده نمی‌کنیم — روی اندروید بدون root جواب نمی‌ده و خیلی از سرورها هم
 * ICMP رو می‌بندن، در حالی که پورت ورودی همیشه بازه.
 */
object PingTester {

    private const val TIMEOUT_MS = 3000

    fun measure(uri: String): Int {
        val hostPort = extractHostPort(uri) ?: return ConfigStore.PING_TIMEOUT
        val (host, port) = hostPort
        return try {
            val start = System.currentTimeMillis()
            Socket().use { socket ->
                socket.connect(InetSocketAddress(host, port), TIMEOUT_MS)
            }
            (System.currentTimeMillis() - start).toInt()
        } catch (e: Exception) {
            ConfigStore.PING_TIMEOUT
        }
    }

    private fun extractHostPort(uri: String): Pair<String, Int>? {
        val afterAt = uri.substringAfter("@", "")
        if (afterAt.isEmpty()) return null
        val hostPort = afterAt.substringBefore("?").substringBefore("#")
        val host = hostPort.substringBeforeLast(":", "")
        val port = hostPort.substringAfterLast(":", "").toIntOrNull()
        if (host.isEmpty() || port == null) return null
        return host to port
    }
}
