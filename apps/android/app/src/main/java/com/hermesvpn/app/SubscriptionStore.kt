package com.hermesvpn.app

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

/**
 * اشتراک‌های کاربر. هر اشتراک یک گروه مستقل توی لیست کانفیگ‌هاست (با همون
 * اسمی که کاربر بهش داده)، و موقع به‌روزرسانی، محتوای همون گروه جایگزین
 * می‌شه — نه اینکه روی هم انباشته بشه.
 */
object SubscriptionStore {

    data class Subscription(
        val id: String,
        var name: String,
        var url: String,
        var autoUpdate: Boolean = true,
        var lastUpdate: Long = 0L,
        var count: Int = 0,
    )

    private const val KEY = "subscriptions_json"

    private fun prefs(ctx: Context) = ctx.getSharedPreferences("hermesvpn_prefs", Context.MODE_PRIVATE)

    fun all(ctx: Context): MutableList<Subscription> {
        val raw = prefs(ctx).getString(KEY, "") ?: ""
        if (raw.isEmpty()) return mutableListOf()
        val out = mutableListOf<Subscription>()
        val arr = try { JSONArray(raw) } catch (e: Exception) { return out }
        for (i in 0 until arr.length()) {
            val o = arr.optJSONObject(i) ?: continue
            out.add(
                Subscription(
                    id = o.optString("id"),
                    name = o.optString("name"),
                    url = o.optString("url"),
                    autoUpdate = o.optBoolean("auto_update", true),
                    lastUpdate = o.optLong("last_update", 0L),
                    count = o.optInt("count", 0),
                )
            )
        }
        return out
    }

    fun save(ctx: Context, list: List<Subscription>) {
        val arr = JSONArray()
        for (s in list) {
            arr.put(JSONObject().apply {
                put("id", s.id)
                put("name", s.name)
                put("url", s.url)
                put("auto_update", s.autoUpdate)
                put("last_update", s.lastUpdate)
                put("count", s.count)
            })
        }
        prefs(ctx).edit().putString(KEY, arr.toString()).apply()
    }

    fun add(ctx: Context, name: String, url: String, autoUpdate: Boolean): Subscription {
        val list = all(ctx)
        val sub = Subscription(
            id = UUID.randomUUID().toString(),
            name = uniqueName(list, name),
            url = url,
            autoUpdate = autoUpdate,
        )
        list.add(sub)
        save(ctx, list)
        return sub
    }

    fun update(ctx: Context, sub: Subscription) {
        val list = all(ctx)
        val index = list.indexOfFirst { it.id == sub.id }
        if (index >= 0) list[index] = sub else list.add(sub)
        save(ctx, list)
    }

    fun remove(ctx: Context, id: String, alsoRemoveConfigs: Boolean) {
        val list = all(ctx)
        val sub = list.firstOrNull { it.id == id } ?: return
        list.removeAll { it.id == id }
        save(ctx, list)
        if (alsoRemoveConfigs) {
            ConfigStore.all(ctx).filter { it.group == sub.name }
                .forEach { ConfigStore.remove(ctx, it.id) }
        }
    }

    fun byGroupName(ctx: Context, group: String): Subscription? =
        all(ctx).firstOrNull { it.name == group }

    /** دو اشتراک با یک اسم یعنی دو گروه که روی هم می‌افتن، پس اسم یکتا می‌شه. */
    private fun uniqueName(list: List<Subscription>, name: String): String {
        val base = name.ifBlank { "Subscription" }
        if (list.none { it.name == base }) return base
        var n = 2
        while (list.any { it.name == "$base-$n" }) n++
        return "$base-$n"
    }
}
