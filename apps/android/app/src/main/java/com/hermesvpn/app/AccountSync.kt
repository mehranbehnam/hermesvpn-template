package com.hermesvpn.app

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * یک نقطه‌ی واحد برای «لینک‌های حسابم رو از سرور بگیر و توی انبار کانفیگ‌ها
 * بریز» — هم صفحه‌ی اصلی موقع اتصال، هم Configs موقع Update، هم تنظیمات
 * اشتراک از همین استفاده می‌کنن تا رفتارشون یکی باشه.
 */
object AccountSync {

    suspend fun refresh(ctx: Context): Boolean {
        val prefs = ctx.getSharedPreferences("hermesvpn_prefs", Context.MODE_PRIVATE)
        val username = prefs.getString("auth_username", "")?.trim() ?: ""
        val password = prefs.getString("auth_password", "") ?: ""
        val subId = prefs.getString("sub_id", "")?.trim() ?: ""
        if (username.isEmpty() && subId.isEmpty()) return false

        val result = try {
            withContext(Dispatchers.IO) {
                if (username.isNotEmpty()) AppConfigApi.login(username, password)
                else AppConfigApi.fetchVlessLinks(subId)
            }
        } catch (e: Exception) {
            return false
        }
        if (!result.ok || result.links.isEmpty()) return false

        ConfigStore.syncAccountLinks(ctx, result.links)
        prefs.edit().putLong("last_subscription_update", System.currentTimeMillis()).apply()
        return true
    }
}
