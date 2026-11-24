package com.hermesvpn.app

import android.content.Context
import android.util.Base64
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.net.URL
import javax.net.ssl.HttpsURLConnection

/**
 * واردکردن کانفیگ — بدون محدودیت تعداد. همه‌ی راه‌ها (چسباندن لینک، اسکن QR،
 * فایل، لینک اشتراک) به همین‌جا می‌رسن.
 */
object LinkImporter {

    private val SCHEMES = listOf(
        "vless://", "vmess://", "trojan://", "ss://", "ssr://",
        "socks://", "hysteria://", "hysteria2://", "hy2://", "tuic://",
    )

    data class ImportResult(val added: Int, val skipped: Int, val error: String? = null)

    /** یک یا چند لینک مستقیم. لینک اشتراک http هم قبوله ولی بدون ساختن گروه. */
    suspend fun import(ctx: Context, raw: String, group: String = ConfigStore.GROUP_LOCAL): ImportResult {
        val text = raw.trim()
        if (text.isEmpty()) return ImportResult(0, 0, "empty")
        // مسیر اضطراری (فایل قفل‌دار HVPN1، لینک hvpn://، یا JSON مسیر)
        if (Tunnels.normalize(text) != null) {
            val (entry, err) = withContext(Dispatchers.IO) { Tunnels.add(ctx, text, group) }
            return if (entry != null) ImportResult(1, 0) else ImportResult(0, 0, "tunnel:" + (err ?: ""))
        }

        val payload = if (isHttpUrl(text)) {
            fetch(text) ?: return ImportResult(0, 0, "network")
        } else {
            text
        }
        return addAll(ctx, extractLinks(payload), group, replaceGroup = false)
    }

    /** اشتراک: محتوا گرفته می‌شه و کلِ گروهِ همون اشتراک باهاش جایگزین می‌شه،
     * تا کانفیگ‌های حذف‌شده‌ی سمت سرور اینجا باقی نمونن. */
    suspend fun importSubscription(ctx: Context, sub: SubscriptionStore.Subscription): ImportResult {
        val payload = fetch(sub.url) ?: return ImportResult(0, 0, "network")
        val links = extractLinks(payload)
        if (links.isEmpty()) return ImportResult(0, 0, "no_links")

        val result = addAll(ctx, links, sub.name, replaceGroup = true)
        sub.lastUpdate = System.currentTimeMillis()
        sub.count = ConfigStore.all(ctx).count { it.group == sub.name }
        SubscriptionStore.update(ctx, sub)
        ctx.getSharedPreferences("hermesvpn_prefs", Context.MODE_PRIVATE)
            .edit().putLong("last_subscription_update", System.currentTimeMillis()).apply()
        return result
    }

    suspend fun refreshAllSubscriptions(ctx: Context): ImportResult {
        val subs = SubscriptionStore.all(ctx)
        if (subs.isEmpty()) return ImportResult(0, 0, "no_subscriptions")
        var added = 0
        var skipped = 0
        for (sub in subs) {
            val r = importSubscription(ctx, sub)
            added += r.added
            skipped += r.skipped
        }
        return ImportResult(added, skipped)
    }

    private fun addAll(
        ctx: Context,
        links: List<String>,
        group: String,
        replaceGroup: Boolean,
    ): ImportResult {
        if (links.isEmpty()) return ImportResult(0, 0, "no_links")

        if (replaceGroup) {
            val incoming = links.toSet()
            ConfigStore.all(ctx)
                .filter { it.group == group && it.uri !in incoming }
                .forEach { ConfigStore.remove(ctx, it.id) }
        }

        val existing = ConfigStore.all(ctx).map { it.uri }.toSet()
        var added = 0
        var skipped = 0
        for (uri in links) {
            if (uri in existing) {
                skipped++
                continue
            }
            val entry = ConfigStore.parse(uri, group)
            if (entry == null) {
                skipped++
                continue
            }
            ConfigStore.add(ctx, entry)
            added++
        }
        return ImportResult(added, skipped)
    }

    fun extractLinks(payload: String): List<String> =
        payload.split("\n", "\r", " ", "\t")
            .map { it.trim() }
            .filter { line -> SCHEMES.any { line.startsWith(it, ignoreCase = true) } }

    fun isHttpUrl(text: String) = text.startsWith("http://") || text.startsWith("https://")

    private suspend fun fetch(url: String): String? = withContext(Dispatchers.IO) {
        try {
            val conn = URL(url).openConnection()
            conn.connectTimeout = 15_000
            conn.readTimeout = 20_000
            // بعضی پنل‌ها بسته به User-Agent خروجی متفاوت می‌دن؛ این مقدار
            // همونیه که کلاینت‌های v2ray می‌فرستن و همه‌جا پشتیبانی می‌شه.
            (conn as? HttpsURLConnection)?.setRequestProperty("User-Agent", "v2rayNG/1.8.5")
            val body = conn.getInputStream().bufferedReader().use { it.readText() }.trim()
            decodeIfBase64(body)
        } catch (e: Exception) {
            null
        }
    }

    /** خروجی لینک‌های اشتراک معمولاً base64 است، ولی بعضی سرورها متن خام
     * می‌دهند — هر دو باید کار کند. */
    private fun decodeIfBase64(body: String): String {
        if (SCHEMES.any { body.contains(it, ignoreCase = true) }) return body
        return try {
            val cleaned = body.replace("\n", "").replace("\r", "")
            String(Base64.decode(cleaned, Base64.DEFAULT), Charsets.UTF_8)
        } catch (e: Exception) {
            body
        }
    }
}
