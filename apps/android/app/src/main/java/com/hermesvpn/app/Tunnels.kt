package com.hermesvpn.app

import android.content.Context
import android.util.Base64
import android.util.Log
import org.json.JSONObject
import java.io.File
import java.net.Inet4Address
import java.net.InetAddress
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.TimeUnit

/**
 * مسیرهای اضطراری (تونل DNS، SSH با Payload/SNI، SSH ساده، فایل قفل‌دار .hvpn).
 *
 * موتور (libhermestunnel.so) یک برنامه‌ی Go جداست که یک پورت SOCKS5 محلی
 * (127.0.0.1:10820) باز می‌کند؛ هسته‌ی Xray همان قالب همیشگی (TUN، مسیریابی
 * ایران، DNS) را دارد و فقط خروجی‌اش به‌جای VLESS، همین SOCKS است.
 * اطلاعات خودِ مسیر (سرور، رمز، Payload) در filesDir/tunnels/<id>.spec است و
 * برای فایل‌های قفل‌دار هرگز در اپ نشان داده نمی‌شود.
 */
object Tunnels {
    private const val TAG = "Tunnels"
    const val SOCKS_PORT = 10820
    const val MARK = "hvpn"
    private val MODES = setOf("dnstt", "ssh-ws", "ssh")

    private const val LIB = "libhermestunnel.so"

    /**
     * فرمان اجرای موتور.
     *
     * ★ بسته‌بندی APK عمداً مثل نسخه‌های قبلی است (کتابخانه‌ها فشرده‌نشده و
     * داخل APK، بدون استخراج): نسخه‌ی 0.7.0 با استخراج کتابخانه‌ها روی
     * گوشی سامسونگ صاحب با «There's a problem with the app file» نصب نشد.
     * پس موتور را خودِ لینکر اندروید مستقیم از داخل APK اجرا می‌کند
     * (linker64 مسیر «base.apk!/lib/<abi>/…» را می‌فهمد). اگر روزی کتابخانه
     * استخراج‌شده باشد، همان مستقیم اجرا می‌شود.
     */
    fun command(ctx: Context, vararg args: String): List<String> {
        val extracted = File(ctx.applicationInfo.nativeLibraryDir, LIB)
        if (extracted.exists()) return listOf(extracted.absolutePath) + args
        val apk = ctx.applicationInfo.sourceDir
        val abi = try {
            java.util.zip.ZipFile(apk).use { z ->
                android.os.Build.SUPPORTED_64_BIT_ABIS.firstOrNull { z.getEntry("lib/$it/$LIB") != null }
            }
        } catch (e: Exception) { null } ?: throw IllegalStateException("engine not available on this phone")
        return listOf("/system/bin/linker64", "$apk!/lib/$abi/$LIB") + args
    }
    private fun dir(ctx: Context) = File(ctx.filesDir, "tunnels").apply { mkdirs() }
    fun specFile(ctx: Context, id: String) = File(dir(ctx), "$id.spec")

    /** متن ورودی یک مسیر اضطراری است؟ (فایل HVPN1، لینک hvpn://، یا JSON با mode) */
    fun normalize(raw: String): String? {
        val t = raw.trim()
        if (t.startsWith("HVPN1")) return t
        if (t.startsWith("hvpn://", ignoreCase = true)) {
            return try {
                String(Base64.decode(t.substring(7).trim(), Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING))
                    .takeIf { it.startsWith("HVPN1") || it.trim().startsWith("{") }
            } catch (e: Exception) { null }
        }
        if (t.startsWith("{")) {
            return try {
                if (JSONObject(t).optString("mode") in MODES) t else null
            } catch (e: Exception) { null }
        }
        return null
    }

    data class Info(val name: String, val mode: String, val locked: Boolean, val expires: Long,
                    val server: String, val bypass: List<String>, val error: String?)

    /** «hermestunnel info» — اعتبار امضا و انقضا را خودِ موتور بررسی می‌کند. */
    fun info(ctx: Context, spec: String): Info {
        return try {
            val p = ProcessBuilder(command(ctx, "info")).redirectErrorStream(true).start()
            p.outputStream.use { it.write(spec.toByteArray()) }
            val line = p.inputStream.bufferedReader().readLine() ?: ""
            p.waitFor(10, TimeUnit.SECONDS)
            val o = JSONObject(line)
            if (o.optString("ev") == "error") return Info("", "", false, 0, "", emptyList(), o.optString("msg"))
            val by = o.optJSONArray("bypass")
            Info(o.optString("name"), o.optString("mode"), o.optBoolean("locked"), o.optLong("expires"),
                o.optString("server"), (0 until (by?.length() ?: 0)).map { by!!.getString(it) }, null)
        } catch (e: Exception) {
            Info("", "", false, 0, "", emptyList(), e.message ?: "engine")
        }
    }

    fun label(mode: String) = when (mode) {
        "dnstt" -> "DNS"
        "ssh-ws" -> "SSH-WS"
        else -> "SSH"
    }

    /** ذخیره به‌عنوان یک کانفیگ عادی در فهرست. برمی‌گرداند: entry یا پیام خطا. */
    fun add(ctx: Context, raw: String, group: String = ConfigStore.GROUP_LOCAL): Pair<ConfigStore.Entry?, String?> {
        val spec = normalize(raw) ?: return null to "invalid"
        val inf = info(ctx, spec)
        if (inf.error != null) return null to inf.error
        val hash = MessageDigest.getInstance("SHA-256").digest(spec.toByteArray())
            .joinToString("") { "%02x".format(it) }.take(16)
        val uri = "hvpn://$hash"
        ConfigStore.all(ctx).firstOrNull { it.uri == uri }?.let { return it to null }
        val id = UUID.randomUUID().toString()
        specFile(ctx, id).writeText(spec)
        val outbound = JSONObject().apply {
            put("protocol", "socks")
            put("settings", JSONObject().put("servers", org.json.JSONArray().put(
                JSONObject().put("address", "127.0.0.1").put("port", SOCKS_PORT))))
            put(MARK, id)
        }
        val entry = ConfigStore.Entry(
            id = id,
            name = inf.name.ifEmpty { "HermesVPN " + label(inf.mode) },
            uri = uri,
            protocol = label(inf.mode) + if (inf.locked) " 🔒" else "",
            address = if (inf.locked) "🔒" else inf.server.ifEmpty { label(inf.mode) },
            group = group,
            rawOutboundJson = outbound.toString(),
        )
        ConfigStore.add(ctx, entry)
        return entry to null
    }

    fun tunnelIdOf(rawOutbound: String?): String? = try {
        rawOutbound?.let { JSONObject(it).optString(MARK).ifEmpty { null } }
    } catch (e: Exception) { null }

    // ---------- اجرا ----------
    @Volatile private var proc: Process? = null
    @Volatile var bypassIps: List<String> = emptyList(); private set
    @Volatile var lastError: String? = null; private set

    /** موتور را بالا می‌آورد و تا آماده شدن SOCKS صبر می‌کند (روی ترد غیراصلی صدا بزنید). */
    @Synchronized
    fun start(ctx: Context, id: String): Boolean {
        stop()
        lastError = null
        val f = specFile(ctx, id)
        if (!f.exists()) { lastError = "missing"; return false }
        val spec = f.readText()
        val inf = info(ctx, spec)
        if (inf.error != null) { lastError = inf.error; return false }
        // موتور Go روی اندروید resolv.conf ندارد → نام‌ها را اپ حل می‌کند.
        val map = mutableListOf<String>()
        val ips = mutableListOf<String>()
        for (h in inf.bypass) {
            if (Regex("^[0-9.]+$").matches(h)) { ips += h; continue }
            try {
                val v4 = InetAddress.getAllByName(h).filterIsInstance<Inet4Address>().mapNotNull { it.hostAddress }
                if (v4.isNotEmpty()) { map += "$h=${v4[0]}"; ips += v4 }
            } catch (e: Exception) { Log.w(TAG, "resolve $h: ${e.message}") }
        }
        bypassIps = ips.distinct()
        val cmd = try { command(ctx, "run", "-socks", "127.0.0.1:$SOCKS_PORT").toMutableList() }
            catch (e: Exception) { lastError = e.message; return false }
        if (map.isNotEmpty()) { cmd += "-map"; cmd += map.joinToString(",") }
        return try {
            val p = ProcessBuilder(cmd).redirectErrorStream(true).start()
            p.outputStream.use { it.write(spec.toByteArray()) }
            proc = p
            val reader = p.inputStream.bufferedReader()
            var ready = false
            val t = Thread {
                try {
                    while (true) {
                        val line = reader.readLine() ?: break
                        val o = try { JSONObject(line) } catch (e: Exception) { continue }
                        when (o.optString("ev")) {
                            "ready" -> { ready = true; synchronized(this) { (this as Object).notifyAll() } }
                            "error" -> { lastError = o.optString("msg"); synchronized(this) { (this as Object).notifyAll() } }
                            "log" -> Log.i(TAG, o.optString("msg"))
                        }
                    }
                } catch (_: Exception) {}
                synchronized(this) { (this as Object).notifyAll() }
            }
            t.isDaemon = true
            t.start()
            val deadline = System.currentTimeMillis() + 75_000
            while (!ready && lastError == null && p.isAlive && System.currentTimeMillis() < deadline) {
                (this as Object).wait(1000)
            }
            if (!ready) {
                if (lastError == null) lastError = if (p.isAlive) "timeout" else "engine stopped"
                stop()
            }
            ready
        } catch (e: Exception) {
            lastError = e.message
            stop()
            false
        }
    }

    fun stop() {
        proc?.let { try { it.destroy() } catch (_: Exception) {} }
        proc = null
    }

    val running: Boolean get() = proc?.isAlive == true
}
