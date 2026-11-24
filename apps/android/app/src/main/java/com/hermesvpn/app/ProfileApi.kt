package com.hermesvpn.app

import org.json.JSONObject
import java.net.URL
import javax.net.ssl.HttpsURLConnection

object ProfileApi {

    private const val BASE_URL = "https://hook.example.com:2087"

    data class Profile(
        val username: String,
        val email: String,
        val displayName: String,
        val avatar: String,
        val expiryTime: Long,
        val totalGb: Long,
        val configCount: Int,
    )

    data class Response(val ok: Boolean, val error: String? = null, val profile: Profile? = null)

    fun fetch(username: String, password: String): Response {
        val body = post("/app/profile", JSONObject().apply {
            put("username", username)
            put("password", password)
        }) ?: return Response(false, "network")
        if (!body.optBoolean("ok")) return Response(false, body.optString("error", "unknown"))

        val p = body.optJSONObject("profile") ?: JSONObject()
        val account = body.optJSONObject("account") ?: JSONObject()
        return Response(
            ok = true,
            profile = Profile(
                username = p.optString("username"),
                email = p.optString("email"),
                displayName = p.optString("display_name"),
                avatar = p.optString("avatar"),
                expiryTime = account.optLong("expiry_time", 0L),
                totalGb = account.optLong("total_gb", 0L),
                configCount = account.optJSONArray("links")?.length() ?: 0,
            )
        )
    }

    fun update(
        username: String,
        password: String,
        displayName: String? = null,
        email: String? = null,
        avatar: String? = null,
    ): Response {
        val payload = JSONObject().apply {
            put("username", username)
            put("password", password)
            displayName?.let { put("display_name", it) }
            email?.let { put("email", it) }
            avatar?.let { put("avatar", it) }
        }
        val body = post("/app/profile/update", payload) ?: return Response(false, "network")
        return if (body.optBoolean("ok")) Response(true)
        else Response(false, body.optString("error", "unknown"))
    }

    fun changePassword(username: String, password: String, newPassword: String): Response {
        val body = post("/app/change-password", JSONObject().apply {
            put("username", username)
            put("password", password)
            put("new_password", newPassword)
        }) ?: return Response(false, "network")
        return if (body.optBoolean("ok")) Response(true)
        else Response(false, body.optString("error", "unknown"))
    }

    /** سرور عمداً همیشه ok می‌ده — رمز تازه فقط به تلگرام خودِ صاحب اکانت
     * می‌ره، پس نباید از جواب فهمید که این یوزرنیم/ایمیل وجود داره یا نه. */
    fun forgotPassword(identifier: String): Response {
        val body = post("/app/forgot-password", JSONObject().apply {
            put("identifier", identifier)
        }) ?: return Response(false, "network")
        return Response(body.optBoolean("ok"))
    }

    private fun post(path: String, payload: JSONObject): JSONObject? {
        return try {
            val conn = URL("$BASE_URL$path").openConnection() as HttpsURLConnection
            conn.connectTimeout = 15_000
            conn.readTimeout = 20_000
            conn.requestMethod = "POST"
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            conn.outputStream.use { it.write(payload.toString().toByteArray(Charsets.UTF_8)) }
            val status = conn.responseCode
            val stream = if (status in 200..299) conn.inputStream else conn.errorStream
            val text = stream?.bufferedReader()?.use { it.readText() } ?: ""
            conn.disconnect()
            if (text.isEmpty()) null else JSONObject(text)
        } catch (e: Exception) {
            null
        }
    }
}
