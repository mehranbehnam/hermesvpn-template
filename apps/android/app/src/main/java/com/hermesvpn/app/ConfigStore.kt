package com.hermesvpn.app

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

/**
 * انبار کانفیگ‌های کاربر. کانفیگ‌هایی که از حساب خودِ HermesVPN میان توی
 * گروه "HermesVPN" خودکار همگام می‌شن (با کلید uri شناخته می‌شن تا با هر
 * لاگین دوباره تکراری نشن)، و هرچی کاربر دستی اضافه کنه توی گروه "Local".
 * روی SharedPreferences به شکل JSON نگه داشته می‌شه — حجمش کمه و نیازی به
 * دیتابیس نداره.
 */
object ConfigStore {

    const val GROUP_HERMES = "HermesVPN"
    const val GROUP_LOCAL = "Local"

    data class Entry(
        val id: String,
        var name: String,
        val uri: String,
        val protocol: String,
        val address: String,
        val group: String,
        var ping: Int = PING_UNKNOWN,
        var favorite: Boolean = false,
        // فقط برای کانفیگ‌هایی که از JSON خام v2ray/xray وارد شدن (نه لینک
        // اشتراک‌گذاری) — همون outbound رو مستقیم نگه می‌داره چون این نوع
        // کانفیگ‌ها معادل رشته‌ای (uri) ندارن. وقتی پر باشه، اولویت با
        // همینه، نه uri.
        val rawOutboundJson: String? = null,
        // کدام اکانتِ خریداری‌شده این کانفیگ را آورده. کسی که چند اکانت
        // خریده می‌تواند همه را روی یک گوشی فعال کند و بینشان جابه‌جا شود؛
        // بدون این فیلد، هر اکانت جدید کانفیگ‌های قبلی را پاک می‌کرد.
        val account: String? = null,
    )

    const val PING_UNKNOWN = -1
    const val PING_TIMEOUT = -2

    private const val KEY_CONFIGS = "configs_json"
    private const val KEY_SELECTED = "selected_config_id"

    private fun prefs(ctx: Context) = ctx.getSharedPreferences("hermesvpn_prefs", Context.MODE_PRIVATE)

    fun all(ctx: Context): MutableList<Entry> {
        val raw = prefs(ctx).getString(KEY_CONFIGS, "") ?: ""
        if (raw.isEmpty()) return mutableListOf()
        val out = mutableListOf<Entry>()
        val arr = try { JSONArray(raw) } catch (e: Exception) { return out }
        for (i in 0 until arr.length()) {
            val o = arr.optJSONObject(i) ?: continue
            out.add(
                Entry(
                    id = o.optString("id"),
                    name = o.optString("name"),
                    uri = o.optString("uri"),
                    protocol = o.optString("protocol"),
                    address = o.optString("address"),
                    group = o.optString("group", GROUP_LOCAL),
                    ping = o.optInt("ping", PING_UNKNOWN),
                    favorite = o.optBoolean("favorite", false),
                    rawOutboundJson = o.optString("raw_outbound", "").ifEmpty { null },
                    account = o.optString("account", "").ifEmpty { null },
                )
            )
        }
        return out
    }

    fun save(ctx: Context, list: List<Entry>) {
        val arr = JSONArray()
        for (e in list) {
            arr.put(JSONObject().apply {
                put("id", e.id)
                put("name", e.name)
                put("uri", e.uri)
                put("protocol", e.protocol)
                put("address", e.address)
                put("group", e.group)
                put("ping", e.ping)
                put("favorite", e.favorite)
                if (e.rawOutboundJson != null) put("raw_outbound", e.rawOutboundJson)
                if (e.account != null) put("account", e.account)
            })
        }
        prefs(ctx).edit().putString(KEY_CONFIGS, arr.toString()).apply()
    }

    /** یک outbound خامِ v2ray/xray (نه لینک اشتراک‌گذاری) رو به یک ورودی
     * تبدیل می‌کنه — برای «وارد کردن JSON از کلیپ‌بورد/فایل». */
    fun addRawOutbound(ctx: Context, name: String, protocol: String, address: String, outboundJson: String, group: String = GROUP_LOCAL): Entry {
        val entry = Entry(
            id = UUID.randomUUID().toString(),
            name = name,
            uri = "json://" + outboundJson.hashCode(),
            protocol = protocol.uppercase(),
            address = address,
            group = group,
            rawOutboundJson = outboundJson,
        )
        add(ctx, entry)
        return entry
    }

    /** یک لینک vless:// (یا هر طرح پشتیبانی‌شده) رو به یک ورودی تبدیل می‌کنه. */
    fun parse(uri: String, group: String, fallbackName: String? = null): Entry? {
        val trimmed = uri.trim()
        val protocol = trimmed.substringBefore("://", "").uppercase()
        if (protocol.isEmpty()) return null
        val host = try {
            val afterAt = trimmed.substringAfter("@", "")
            val hostPort = afterAt.substringBefore("?").substringBefore("#")
            hostPort.substringBeforeLast(":").ifEmpty { hostPort }
        } catch (e: Exception) {
            ""
        }
        val tag = trimmed.substringAfter("#", "").let {
            try { java.net.URLDecoder.decode(it, "UTF-8") } catch (e: Exception) { it }
        }
        return Entry(
            id = UUID.randomUUID().toString(),
            name = tag.ifEmpty { fallbackName ?: host.ifEmpty { protocol } },
            uri = trimmed,
            protocol = protocol,
            address = host,
            group = group,
        )
    }

    fun add(ctx: Context, entry: Entry) {
        val list = all(ctx)
        if (list.any { it.uri == entry.uri }) return
        list.add(entry)
        save(ctx, list)
        if (selectedId(ctx) == null) select(ctx, entry.id)
    }

    fun remove(ctx: Context, id: String) {
        val list = all(ctx)
        list.removeAll { it.id == id }
        save(ctx, list)
        if (selectedId(ctx) == id) {
            prefs(ctx).edit().remove(KEY_SELECTED).apply()
            list.firstOrNull()?.let { select(ctx, it.id) }
        }
    }

    fun rename(ctx: Context, id: String, newName: String) {
        val list = all(ctx)
        list.firstOrNull { it.id == id }?.name = newName
        save(ctx, list)
    }

    fun toggleFavorite(ctx: Context, id: String) {
        val list = all(ctx)
        list.firstOrNull { it.id == id }?.let { it.favorite = !it.favorite }
        save(ctx, list)
    }

    fun setPing(ctx: Context, id: String, ping: Int) {
        val list = all(ctx)
        list.firstOrNull { it.id == id }?.ping = ping
        save(ctx, list)
    }

    fun select(ctx: Context, id: String) {
        prefs(ctx).edit().putString(KEY_SELECTED, id).apply()
    }

    fun selectedId(ctx: Context): String? =
        prefs(ctx).getString(KEY_SELECTED, null)?.takeIf { it.isNotEmpty() }

    fun selected(ctx: Context): Entry? {
        val id = selectedId(ctx) ?: return null
        return all(ctx).firstOrNull { it.id == id }
    }

    /** لینک‌های تازه‌ی حساب رو با گروه HermesVPN همگام می‌کنه: هرچی سرور داده
     * می‌مونه/اضافه می‌شه، هرچی دیگه نیست از همون گروه پاک می‌شه. انتخاب فعلی
     * کاربر حفظ می‌شه مگه اینکه خود اون کانفیگ حذف شده باشه. */
    /**
     * لینک‌های یک اکانت را جایگزین می‌کند.
     *
     * [account] مشخص می‌کند این لینک‌ها مالِ کدام اکانتِ خریداری‌شده‌اند. فقط
     * کانفیگ‌های *همان* اکانت که دیگر در لیست تازه نیستند پاک می‌شوند — پس
     * کسی که چند اکانت خریده می‌تواند همه را روی یک گوشی داشته باشد و
     * فعال‌کردن اکانت دوم، اولی را از بین نمی‌برد.
     *
     * [selectFirst] یعنی بعد از افزودن، اولین کانفیگ همین اکانت انتخاب شود
     * (وقتی کاربر تازه از ربات روی «اتصال به نرم‌افزار» زده — انتظار دارد
     * همان اکانت آماده‌ی استفاده باشد).
     */
    fun syncAccountLinks(
        ctx: Context,
        links: List<AppConfigApi.VlessLink>,
        account: String? = null,
        selectFirst: Boolean = false,
    ) {
        if (links.isEmpty()) return
        val list = all(ctx)
        val incomingUris = links.map { it.uri.trim() }.toSet()
        list.removeAll { it.group == GROUP_HERMES && it.account == account && it.uri !in incomingUris }
        for (link in links) {
            val label = if (account != null) "${link.label} · $account" else link.label
            val idx = list.indexOfFirst { it.uri == link.uri.trim() }
            if (idx >= 0) {
                // ممکن است همین لینک قبلاً دستی (QR/کلیپ‌بورد) با گروه Local وارد
                // شده باشد. حالا که معلوم شد مالِ اکانتِ HermesVPN است، گروه و
                // اکانتش هم به‌روز می‌شود؛ قبلاً فقط اسم عوض می‌شد و صفحه‌ی اصلی
                // به‌جای «آلمان» آدرس خامِ سرور را نشان می‌داد.
                list[idx] = list[idx].copy(name = label, group = GROUP_HERMES, account = account)
                continue
            }
            parse(link.uri, GROUP_HERMES, label)?.let {
                list.add(it.copy(name = label, account = account))
            }
        }
        save(ctx, list)
        val stored = all(ctx)
        if (selectFirst) {
            stored.firstOrNull { it.account == account && it.uri in incomingUris }
                ?.let { select(ctx, it.id) }
        } else if (stored.none { it.id == selectedId(ctx) }) {
            stored.firstOrNull { it.group == GROUP_HERMES }?.let { select(ctx, it.id) }
        }
    }
}
