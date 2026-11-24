package com.hermesvpn.app

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.util.Base64
import android.view.View
import android.widget.Button
import android.widget.ProgressBar
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.net.URL

/**
 * ورود یک‌ضربه‌ای از ربات فروش.
 *
 * سه شکل ورودی پشتیبانی می‌شود، هر سه از طریق همان App Link تأییدشده
 * (`https://app.example.com/open/?…`) — اگر اپ نصب باشد اندروید مستقیم
 * به این صفحه می‌دهد، وگرنه همان URL در مرورگر باز می‌شود و لینک دانلود اپ
 * را نشان می‌دهد:
 *
 *   ?t=<توکن>    ربات HermesVPN. توکن به سرور داده می‌شود و اکانت واقعی
 *                برمی‌گردد. برای ربات‌هایی که به همان پنل وصل‌اند.
 *   ?c=<base64>  یک یا چند لینک vless:// که مستقیم داخل خود پیوند کدگذاری
 *                شده‌اند. هیچ سروری وسط نیست، پس هر ربات فروشی روی هر پنلی
 *                می‌تواند بدون هیچ API‌ای از آن استفاده کند.
 *   ?s=<url>     آدرس اشتراک (subscription). اپ خودش می‌گیردش، رمزگشایی
 *                می‌کند و همه‌ی کانفیگ‌های داخلش را وارد می‌کند.
 *
 * ?c= و ?s= چیزی به سطح افشا اضافه نمی‌کنند: همان پیام تلگرام خودش لینک
 * vless و لینک ساب را به‌صورت متن ساده دارد.
 */
class DeepLinkActivity : AppCompatActivity() {

    private lateinit var progress: ProgressBar
    private lateinit var title: TextView
    private lateinit var message: TextView
    private lateinit var action: Button
    private lateinit var build: TextView

    private var retry: (() -> Unit)? = null
    private var succeeded = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        applyScreenshotProtection(this)
        setContentView(R.layout.activity_deeplink)

        progress = findViewById(R.id.linkProgress)
        title = findViewById(R.id.linkTitle)
        message = findViewById(R.id.linkMessage)
        action = findViewById(R.id.linkAction)
        build = findViewById(R.id.linkBuild)
        // نسخه‌ی دقیقِ بیلد همیشه روی همین صفحه دیده می‌شه — وقتی چیزی کار
        // نمی‌کنه، اولین سؤال «کدوم نسخه رو داری؟» است و این جواب می‌ده.
        build.text = getString(R.string.deeplink_build, BuildConfig.VERSION_NAME, BuildConfig.BUILD_STAMP)

        action.setOnClickListener {
            val again = retry
            if (again != null && !succeeded) again() else goHome()
        }

        route(intent?.data)
    }

    /** فایل .hvpn یا لینک hvpn:// (از تلگرام، بله، مدیر فایل). */
    private fun importTunnel(uri: Uri) {
        working()
        lifecycleScope.launch {
            val text = withContext(Dispatchers.IO) {
                try {
                    if (uri.scheme.equals("hvpn", true)) uri.toString()
                    else contentResolver.openInputStream(uri)?.use { it.readBytes().take(1 shl 20).toByteArray() }?.decodeToString()
                } catch (e: Exception) { null }
            }
            if (text == null || Tunnels.normalize(text) == null) {
                showError(getString(R.string.tunnel_err_invalid))
                return@launch
            }
            val (entry, err) = withContext(Dispatchers.IO) { Tunnels.add(this@DeepLinkActivity, text, ConfigStore.GROUP_HERMES) }
            if (entry == null) {
                showError(getString(when {
                    err?.contains("not made by HermesVPN") == true -> R.string.tunnel_err_foreign
                    err?.contains("expired") == true -> R.string.tunnel_err_expired
                    else -> R.string.tunnel_err_invalid
                }))
                return@launch
            }
            ConfigStore.select(this@DeepLinkActivity, entry.id)
            done("")
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        route(intent.data)
    }

    private fun route(uri: Uri?) {
        if (uri == null) {
            showError(getString(R.string.deeplink_no_token))
            return
        }

        if (uri.scheme.equals("hvpn", true) || uri.scheme.equals("content", true) || uri.scheme.equals("file", true)) {
            importTunnel(uri)
            return
        }

        // لینک مستقیم vless:// (وقتی کاربر جایی روی خود لینک بزند)
        if (uri.scheme.equals("vless", ignoreCase = true)) {
            importConfigs(listOf(uri.toString()))
            return
        }

        val token = param(uri, "t") ?: param(uri, "token")
        if (!token.isNullOrEmpty()) {
            retry = { redeemToken(token) }
            redeemToken(token)
            return
        }

        // شکلی که ربات‌های دیگر (مثل ربات فروش ایران) استفاده می‌کنند:
        // hermesvpn://import?url=<لینک vless، URL-encoded>. این‌جا هیچ سروری
        // وسط نیست — اندروید خودش پیوند را به اپ می‌دهد.
        val direct = param(uri, "url")
        if (!direct.isNullOrEmpty()) {
            val decoded = decodePayload(direct) ?: direct
            importConfigs(splitConfigs(decoded))
            return
        }

        val packed = param(uri, "c")
        if (!packed.isNullOrEmpty()) {
            val decoded = decodePayload(packed)
            if (decoded == null) {
                showError(getString(R.string.deeplink_bad_payload))
            } else {
                importConfigs(splitConfigs(decoded))
            }
            return
        }

        val sub = param(uri, "s")
        if (!sub.isNullOrEmpty()) {
            val subUrl = decodePayload(sub)?.takeIf { it.startsWith("http", true) } ?: sub
            retry = { fetchSubscription(subUrl) }
            fetchSubscription(subUrl)
            return
        }

        showError(getString(R.string.deeplink_no_token))
    }

    /**
     * پارامتر را اول از query و بعد از fragment می‌خواند.
     *
     * شکلِ توصیه‌شده fragment است (`/open/#c=…`) نه query (`/open/?c=…`): مرورگرها
     * fragment را هرگز همراه درخواست به سرور نمی‌فرستند، ولی اندروید کلِ URI را
     * به اپ می‌دهد. یعنی کانفیگِ مشتری حتی وقتی اپ نصب نیست و صفحه در مرورگر
     * باز می‌شود، به لاگ هیچ سروری نمی‌رسد. شکل query هم برای سازگاری با
     * لینک‌های قدیمی پشتیبانی می‌شود.
     */
    private fun param(uri: Uri, name: String): String? {
        uri.getQueryParameter(name)?.trim()?.takeIf { it.isNotEmpty() }?.let { return it }
        val frag = uri.fragment ?: return null
        for (part in frag.split('&')) {
            val i = part.indexOf('=')
            if (i > 0 && part.substring(0, i).trim() == name) {
                val raw = part.substring(i + 1).trim()
                if (raw.isNotEmpty()) {
                    return try { Uri.decode(raw) } catch (e: Exception) { raw }
                }
            }
        }
        return null
    }

    /** base64 (هر دو گویشِ استاندارد و URL-safe) یا متنِ خام. */
    private fun decodePayload(raw: String): String? {
        if (raw.startsWith("vless://", true) || raw.startsWith("http", true)) return raw
        for (flags in intArrayOf(
            Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING,
            Base64.DEFAULT,
        )) {
            try {
                val text = String(Base64.decode(raw, flags), Charsets.UTF_8)
                if (text.isNotBlank()) return text
            } catch (e: Exception) {
                // گویشِ بعدی را امتحان کن
            }
        }
        return null
    }

    private fun splitConfigs(text: String): List<String> =
        text.split('\n', '\r', ',', ' ')
            .map { it.trim() }
            .filter { it.startsWith("vless://", ignoreCase = true) }

    // ---------- سه مسیرِ ورودی ----------

    private fun redeemToken(token: String) {
        working()
        lifecycleScope.launch {
            val result = try {
                withContext(Dispatchers.IO) { AppConfigApi.redeem(token) }
            } catch (e: Exception) {
                showError(netError(e)); return@launch
            }
            if (!result.ok) {
                showError(
                    when (result.error) {
                        "invalid_token" -> getString(R.string.deeplink_invalid_token)
                        "expired" -> getString(R.string.error_expired)
                        "blocked" -> getString(R.string.error_blocked)
                        "not_found" -> getString(R.string.error_not_found)
                        else -> getString(R.string.error_network)
                    }
                )
                return@launch
            }
            ConfigStore.syncAccountLinks(
                this@DeepLinkActivity, result.links,
                account = result.account, selectFirst = true
            )
            // sub_id را نگه می‌داریم تا اپ بعداً هم بتواند خودش لینک‌ها را
            // تازه کند (چرخش دامنه) بدون اینکه کاربر کاری بکند.
            result.subId?.let {
                getSharedPreferences("hermesvpn_prefs", MODE_PRIVATE)
                    .edit().putString("sub_id", it).apply()
            }
            done(result.account ?: "")
        }
    }

    private fun fetchSubscription(subUrl: String) {
        working()
        lifecycleScope.launch {
            val body = try {
                withContext(Dispatchers.IO) {
                    val conn = URL(subUrl).openConnection()
                    conn.connectTimeout = 15_000
                    conn.readTimeout = 15_000
                    conn.getInputStream().bufferedReader().use { it.readText() }
                }
            } catch (e: Exception) {
                showError(netError(e)); return@launch
            }
            // بدنه‌ی لینک‌های اشتراک معمولاً base64 است، ولی بعضی پنل‌ها
            // متنِ خام می‌دهند — هر دو پذیرفته می‌شود.
            val text = decodePayload(body.trim()) ?: body
            val configs = splitConfigs(text).ifEmpty { splitConfigs(body) }
            if (configs.isEmpty()) {
                showError(getString(R.string.deeplink_bad_payload))
            } else {
                importConfigs(configs)
            }
        }
    }

    /** کانفیگ‌ها را وارد و اولی را انتخاب می‌کند، تا کاربر فقط Connect بزند. */
    private fun importConfigs(configs: List<String>) {
        if (configs.isEmpty()) {
            showError(getString(R.string.deeplink_bad_payload))
            return
        }
        var firstId: String? = null
        var added = 0
        for (uri in configs) {
            val entry = ConfigStore.parse(uri, ConfigStore.GROUP_HERMES) ?: continue
            val existing = ConfigStore.all(this).firstOrNull { it.uri == uri.trim() }
            if (existing != null) {
                if (firstId == null) firstId = existing.id
                continue
            }
            ConfigStore.add(this, entry)
            if (firstId == null) firstId = entry.id
            added++
        }
        if (firstId == null) {
            showError(getString(R.string.deeplink_bad_payload))
            return
        }
        ConfigStore.select(this, firstId)
        done(if (added > 1) "$added" else "")
    }

    // ---------- وضعیت صفحه ----------

    private fun working() {
        title.text = getString(R.string.deeplink_working_title)
        message.text = getString(R.string.deeplink_working_body)
        progress.visibility = View.VISIBLE
        action.visibility = View.GONE
    }

    private fun done(subject: String) {
        succeeded = true
        progress.visibility = View.GONE
        title.text = getString(R.string.deeplink_done_title)
        message.text = getString(R.string.deeplink_done_body, subject)
        action.text = getString(R.string.deeplink_go_connect)
        action.visibility = View.VISIBLE
    }

    /** خطای واقعی نشان داده می‌شود، نه یک پیام کلی — بدون این، «سرور در
     * دسترس نیست» می‌تواند هر چیزی باشد از قطعی شبکه تا اشکال در خود اپ. */
    private fun netError(e: Exception) =
        getString(R.string.error_network) + "\n\n" + e.javaClass.simpleName + ": " + (e.message ?: "-")

    private fun showError(text: String) {
        progress.visibility = View.GONE
        title.text = getString(R.string.deeplink_failed_title)
        message.text = text
        action.text = if (retry != null) getString(R.string.deeplink_retry)
                      else getString(R.string.deeplink_continue)
        action.visibility = View.VISIBLE
    }

    private fun goHome() {
        startActivity(
            Intent(this, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        )
        finish()
    }
}
