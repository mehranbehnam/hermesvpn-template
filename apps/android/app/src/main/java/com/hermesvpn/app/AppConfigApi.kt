package com.hermesvpn.app

import org.json.JSONObject
import java.net.URL
import javax.net.ssl.HttpsURLConnection

/**
 * لینک‌های اتصال رو از endpoint اختصاصیِ اپ می‌گیره (نه از /sub/ واقعیِ سه‌ایکس‌یوآی
 * که یه چک HWID داره و فقط اپ‌های V2Ray/Xray واقعی رو قبول می‌کنه، نه یه فراخوانی
 * ساده‌ی HTTP). این endpoint روی همون دامنه‌ی وب‌هوک پرداخته — دامنه‌ای که با دکمه‌ی
 * «رفع فیلترینگ» عوض نمی‌شه (فقط دامنه‌ی inbound اصلی عوض می‌شه)، پس هاردکد کردنش
 * اینجا امنه.
 */
object AppConfigApi {

    private const val BASE_URL = "https://hook.example.com:2087"

    data class VlessLink(val label: String, val uri: String)
    data class Result(
        val ok: Boolean,
        val error: String? = null,
        val links: List<VlessLink> = emptyList(),
        val expiryTime: Long = 0L,
        val totalGb: Long = 0L,
        /** فقط در پاسخِ redeem پر می‌شود — شناسه‌ی اشتراک و نامِ اکانت. */
        val subId: String? = null,
        val account: String? = null,
    )

    /**
     * توکنِ دکمه‌ی «اتصال به نرم‌افزار» در ربات را به یک اکانتِ واقعی تبدیل
     * می‌کند. خروجی همان لینک‌های لاگین معمولی است، به‌علاوه‌ی sub_id که اپ
     * نگه می‌دارد تا بعداً هم بدون یوزر/پسورد بتواند لینک‌ها را تازه کند.
     */
    fun redeem(token: String): Result {
        val url = URL("$BASE_URL/app/redeem")
        val conn = url.openConnection() as HttpsURLConnection
        conn.setRequestProperty("User-Agent", "HermesVPN-Android/${BuildConfig.VERSION_NAME}")   // سرور با این، مسیر XHTTP را هم می‌دهد
        conn.connectTimeout = 15_000
        conn.readTimeout = 15_000
        conn.requestMethod = "POST"
        conn.doOutput = true
        conn.setRequestProperty("Content-Type", "application/json")
        val payload = JSONObject().put("token", token).toString().toByteArray(Charsets.UTF_8)
        conn.outputStream.use { it.write(payload) }
        return readResult(conn)
    }

    fun fetchVlessLinks(subId: String): Result {
        val url = URL("$BASE_URL/app/vless?sub_id=${java.net.URLEncoder.encode(subId, "UTF-8")}")
        val conn = url.openConnection() as HttpsURLConnection
        conn.setRequestProperty("User-Agent", "HermesVPN-Android/${BuildConfig.VERSION_NAME}")   // سرور با این، مسیر XHTTP را هم می‌دهد
        conn.connectTimeout = 10_000
        conn.readTimeout = 10_000
        conn.requestMethod = "GET"
        return readResult(conn)
    }

    /** با یوزرنیم/پسورد واقعی — همونی که ربات موقع پرداخت خودکار می‌سازه. */
    fun login(username: String, password: String): Result {
        val url = URL("$BASE_URL/app/login")
        val conn = url.openConnection() as HttpsURLConnection
        conn.setRequestProperty("User-Agent", "HermesVPN-Android/${BuildConfig.VERSION_NAME}")   // سرور با این، مسیر XHTTP را هم می‌دهد
        conn.connectTimeout = 10_000
        conn.readTimeout = 10_000
        conn.requestMethod = "POST"
        conn.doOutput = true
        conn.setRequestProperty("Content-Type", "application/json")
        val payload = JSONObject().apply {
            put("username", username)
            put("password", password)
        }.toString().toByteArray(Charsets.UTF_8)
        conn.outputStream.use { it.write(payload) }
        return readResult(conn)
    }

    /** ثبت‌نام از توی اپ: با یوزرنیم تلگرامی که قبلاً از ربات خرید کرده (پیدا
     * می‌شه از روی سفارش‌های تأییدشده)، یه یوزر/پسورد دلخواه + ایمیل تماس براش
     * ثبت می‌کنه. اگه هیچ خرید تأییدشده‌ای زیر اون یوزرنیم تلگرام نباشه، سرور
     * not_found برمی‌گردونه. */
    fun register(tgUsername: String, email: String, newUsername: String, password: String): Result {
        val url = URL("$BASE_URL/app/register")
        val conn = url.openConnection() as HttpsURLConnection
        conn.setRequestProperty("User-Agent", "HermesVPN-Android/${BuildConfig.VERSION_NAME}")   // سرور با این، مسیر XHTTP را هم می‌دهد
        conn.connectTimeout = 10_000
        conn.readTimeout = 10_000
        conn.requestMethod = "POST"
        conn.doOutput = true
        conn.setRequestProperty("Content-Type", "application/json")
        val payload = JSONObject().apply {
            put("tg_username", tgUsername)
            put("email", email)
            put("new_username", newUsername)
            put("password", password)
        }.toString().toByteArray(Charsets.UTF_8)
        conn.outputStream.use { it.write(payload) }
        return readResult(conn)
    }

    private fun readResult(conn: HttpsURLConnection): Result {
        val status = conn.responseCode
        val stream = if (status in 200..299) conn.inputStream else conn.errorStream
        val body = stream?.bufferedReader()?.use { it.readText() } ?: ""
        conn.disconnect()

        if (body.isEmpty()) {
            return Result(ok = false, error = "network")
        }

        val json = JSONObject(body)
        if (!json.optBoolean("ok", false)) {
            return Result(ok = false, error = json.optString("error", "unknown"))
        }

        val links = mutableListOf<VlessLink>()
        val arr = json.optJSONArray("links")
        if (arr != null) {
            for (i in 0 until arr.length()) {
                val item = arr.getJSONObject(i)
                links.add(VlessLink(item.optString("label"), item.optString("uri")))
            }
        }
        return Result(
            ok = true,
            links = links,
            expiryTime = json.optLong("expiry_time", 0L),
            totalGb = json.optLong("total_gb", 0L),
            subId = json.optString("sub_id", "").ifEmpty { null },
            account = json.optString("account", "").ifEmpty { null },
        )
    }
}
