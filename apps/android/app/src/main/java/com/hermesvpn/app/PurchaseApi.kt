package com.hermesvpn.app

import org.json.JSONObject
import java.net.URL
import java.net.URLEncoder
import javax.net.ssl.HttpsURLConnection

/**
 * لایه‌ی نازک روی endpointهای خرید/پرداخت سمت سرور (همون سرور وب‌هوک،
 * پورت 2087). برخلاف AppConfigApi (که فقط لینک/لاگین می‌گیره)، اینجا شکل
 * پاسخ‌ها متفاوته، برای همین یه JSONObject خام برمی‌گردونه و خودِ UI هرچی
 * لازم داره ازش می‌خونه.
 */
object PurchaseApi {

    private const val BASE_URL = "https://hook.example.com:2087"

    data class ApiResponse(val httpStatus: Int, val json: JSONObject) {
        val ok: Boolean get() = json.optBoolean("ok", false)
        val error: String get() = json.optString("error", "network")
    }

    fun getPlans(): ApiResponse = get("/app/plans")

    fun createOrder(username: String, password: String, planKey: String): ApiResponse =
        post("/app/orders", JSONObject().apply {
            put("username", username); put("password", password); put("plan_key", planKey)
        })

    fun chooseMethod(orderId: Int, username: String, password: String, method: String): ApiResponse =
        post("/app/orders/$orderId/method", JSONObject().apply {
            put("username", username); put("password", password); put("method", method)
        })

    fun uploadReceipt(orderId: Int, username: String, password: String, imageBase64: String): ApiResponse =
        post("/app/orders/$orderId/receipt", JSONObject().apply {
            put("username", username); put("password", password); put("image_base64", imageBase64)
        }, readTimeoutMs = 30_000)

    fun orderStatus(orderId: Int, username: String, password: String): ApiResponse =
        get("/app/orders/$orderId?username=${enc(username)}&password=${enc(password)}")

    private fun enc(s: String) = URLEncoder.encode(s, "UTF-8")

    private fun get(path: String): ApiResponse {
        val conn = URL("$BASE_URL$path").openConnection() as HttpsURLConnection
        conn.connectTimeout = 10_000
        conn.readTimeout = 15_000
        conn.requestMethod = "GET"
        return readJson(conn)
    }

    private fun post(path: String, body: JSONObject, readTimeoutMs: Int = 15_000): ApiResponse {
        val conn = URL("$BASE_URL$path").openConnection() as HttpsURLConnection
        conn.connectTimeout = 10_000
        conn.readTimeout = readTimeoutMs
        conn.requestMethod = "POST"
        conn.doOutput = true
        conn.setRequestProperty("Content-Type", "application/json")
        conn.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
        return readJson(conn)
    }

    private fun readJson(conn: HttpsURLConnection): ApiResponse {
        val status = try { conn.responseCode } catch (e: Exception) { -1 }
        val stream = if (status in 200..299) conn.inputStream else conn.errorStream
        val text = try { stream?.bufferedReader()?.use { it.readText() } } catch (e: Exception) { null } ?: "{}"
        conn.disconnect()
        val json = try { JSONObject(text) } catch (e: Exception) {
            JSONObject().put("ok", false).put("error", "network")
        }
        return ApiResponse(status, json)
    }
}
