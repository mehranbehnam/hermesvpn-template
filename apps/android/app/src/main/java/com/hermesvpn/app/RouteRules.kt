package com.hermesvpn.app

import android.content.Context
import org.json.JSONObject
import java.net.URL

/**
 * فهرست مسیریابیِ به‌روزشونده از سرور.
 *
 * چرا وجود دارد: بعضی سایت‌ها را نه با IP می‌شود تشخیص داد و نه در فهرست
 * آماده‌ی geosite هستند (مثلاً سایت‌های ایرانی که پشت Cloudflare‌اند). بدون
 * این، هر بار که مشتری یک سایت را گزارش می‌کرد باید نسخه‌ی جدید اپ منتشر
 * می‌شد و همه‌ی مشتری‌ها باید دوباره نصب می‌کردند.
 *
 * حالا فقط یک خط روی سرور اضافه می‌شود و همه‌ی گوشی‌ها ظرف یک روز می‌گیرندش.
 * شکست در دریافت هیچ‌وقت مانع اتصال نمی‌شود — آخرین نسخه‌ی ذخیره‌شده (یا
 * فهرست خالی) استفاده می‌شود.
 */
object RouteRules {

    private const val URL_ROUTES = "https://app.example.com/rules/routes.json"
    private const val REFRESH_INTERVAL_MS = 24 * 60 * 60 * 1000L
    private const val KEY_JSON = "route_rules_json"
    private const val KEY_FETCHED_AT = "route_rules_fetched_at"

    @Volatile
    var proxyDomains: List<String> = emptyList()
        private set

    @Volatile
    var directDomains: List<String> = emptyList()
        private set

    /** از حافظه‌ی محلی می‌خواند (فوری) و در صورت کهنه بودن، در پس‌زمینه تازه می‌کند. */
    fun load(ctx: Context) {
        val prefs = ctx.getSharedPreferences("hermesvpn_prefs", Context.MODE_PRIVATE)
        prefs.getString(KEY_JSON, null)?.let { parse(it) }

        val age = System.currentTimeMillis() - prefs.getLong(KEY_FETCHED_AT, 0L)
        if (age < REFRESH_INTERVAL_MS) return

        Thread({
            try {
                val conn = URL(URL_ROUTES).openConnection()
                conn.connectTimeout = 8_000
                conn.readTimeout = 8_000
                val body = conn.getInputStream().bufferedReader().use { it.readText() }
                if (parse(body)) {
                    prefs.edit()
                        .putString(KEY_JSON, body)
                        .putLong(KEY_FETCHED_AT, System.currentTimeMillis())
                        .apply()
                }
            } catch (e: Exception) {
                // بی‌سر و صدا رد شو: فهرست قبلی سر جایش است
            }
        }, "route-rules").apply { priority = Thread.MIN_PRIORITY; start() }
    }

    private fun parse(json: String): Boolean = try {
        val root = JSONObject(json)
        proxyDomains = root.optJSONArray("proxy").toList()
        directDomains = root.optJSONArray("direct").toList()
        true
    } catch (e: Exception) {
        false
    }

    private fun org.json.JSONArray?.toList(): List<String> {
        if (this == null) return emptyList()
        val out = ArrayList<String>(length())
        for (i in 0 until length()) {
            optString(i).trim().takeIf { it.isNotEmpty() }?.let { out.add(it) }
        }
        return out
    }
}
