package com.hermesvpn.app

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import org.json.JSONObject
import java.net.URL

/**
 * به‌روزرسانی داخل خود نرم‌افزار.
 *
 * قبلاً هر بار که نسخه‌ی تازه‌ای می‌ساختیم باید دستی برای تک‌تک مشتری‌ها لینک
 * می‌فرستادیم. حالا اپ خودش روزی یک‌بار `version.json` را می‌خواند و اگر نسخه‌ی
 * تازه‌تری بود، اعلان می‌دهد؛ کاربر روی اعلان می‌زند و همان‌جا دانلود و نصب
 * می‌شود — بدون تلگرام، بدون لینک دستی.
 *
 * چک کردن هیچ‌وقت جلوی کار اپ را نمی‌گیرد: اگر شبکه نبود یا فایل خراب بود،
 * بی‌صدا رد می‌شود.
 */
object UpdateChecker {

    const val MANIFEST_URL = "https://app.example.com/app/version.json"
    // پشتیبان روی سرور اوراکل (ربات فروش، پورت 2087) تا اگر هتزنر در دسترس نبود
    // باز هم آپدیت به دست مشتری برسد. به ترتیب امتحان می‌شوند.
    private val MANIFEST_URLS = listOf(
        MANIFEST_URL,
        "https://hook.example.com:2087/dl/HermesVPN-Android-update.json",
    )

    private const val PREFS = "hermesvpn_prefs"
    private const val KEY_LAST_CHECK = "update_last_check"
    private const val KEY_SKIPPED = "update_skipped_code"
    // ★ قبلاً ۱۲ ساعت بود. نتیجه‌اش این شد که یک رفع‌اشکال فوری منتشر شد و
    // صاحب پروژه تا نیم روز بعد هیچ اعلانی نگرفت، چون تایمر همان صبح ری‌ست
    // شده بود. این فقط یک JSON چهارصد بایتی است؛ یک ساعت هم بیش از کافی
    // است و جلوی شبکه زدن در هر بار باز شدن اپ را می‌گیرد.
    private const val CHECK_INTERVAL_MS = 15L * 60 * 1000   // مالک: آپدیت ظرف ۱۵ دقیقه برسد
    private const val CHANNEL_ID = "hermes_update"
    private const val NOTIF_ID = 4201

    /** نتیجه‌ی آخرین fetch: false یعنی هیچ آدرسی جواب نداد (شبکه/فیلتر) — آن‌وقت زودتر دوباره چک می‌کنیم. */
    @Volatile var lastFetchOk: Boolean = false

    data class Info(
        val versionCode: Int,
        val versionName: String,
        val notes: String,
        val apkUrl: String,
        val mandatory: Boolean,
    )

    /** نسخه‌ی تازه، یا null اگر چیزی برای به‌روزرسانی نیست. */
    fun fetch(ctx: Context): Info? {
        val text = MANIFEST_URLS.firstNotNullOfOrNull { u ->
            try {
                val conn = URL(u).openConnection()
                conn.connectTimeout = 10_000
                conn.readTimeout = 10_000
                conn.getInputStream().bufferedReader().use { it.readText() }
                    .takeIf { it.trimStart().startsWith("{") }
            } catch (e: Exception) {
                null
            }
        }
        lastFetchOk = text != null
        if (text == null) return null

        val info = try {
            val o = JSONObject(text)
            val code = o.getInt("versionCode")
            // هر معماری فایل خودش را دارد؛ نسخه‌ی یکجا ۱۶۰ مگابایت می‌شد و
            // روی 4G ایران هیچ‌وقت کامل دانلود نمی‌شد.
            val apks = o.optJSONObject("apk")
            val abi = Build.SUPPORTED_ABIS.firstOrNull { apks?.has(it) == true }
            val url = when {
                abi != null -> apks!!.getString(abi)
                apks != null -> apks.optString("arm64-v8a", "")
                else -> o.optString("url", "")
            }
            if (url.isEmpty()) return null
            Info(
                versionCode = code,
                versionName = o.optString("versionName", ""),
                // یادداشت نسخه به زبان اپ: notes_en برای زبان‌های غیر فارسی
                notes = if (ctx.resources.configuration.locales[0].language == "fa") o.optString("notes", "")
                        else o.optString("notes_en", ""),
                apkUrl = url,
                mandatory = o.optBoolean("mandatory", false),
            )
        } catch (e: Exception) {
            return null
        }

        return if (info.versionCode > BuildConfig.VERSION_CODE) info else null
    }

    /** آیا الان وقتِ چک کردن هست؟ (برای اینکه هر بار باز شدن اپ شبکه نزند) */
    fun due(ctx: Context): Boolean {
        val p = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        return System.currentTimeMillis() - p.getLong(KEY_LAST_CHECK, 0L) > CHECK_INTERVAL_MS
    }

    fun markChecked(ctx: Context) {
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putLong(KEY_LAST_CHECK, System.currentTimeMillis()).apply()
    }

    /** کاربر گفته این نسخه را نمی‌خواهم — دیگر برایش اعلان نده. */
    fun skip(ctx: Context, code: Int) {
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putInt(KEY_SKIPPED, code).apply()
    }

    fun isSkipped(ctx: Context, code: Int): Boolean =
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getInt(KEY_SKIPPED, 0) == code

    /** اعلان «نسخه‌ی جدید آمد» — زدن روی آن مستقیم صفحه‌ی به‌روزرسانی را باز می‌کند. */
    fun notify(ctx: Context, info: Info) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ctx.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED
        ) return

        val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            nm.createNotificationChannel(
                NotificationChannel(
                    CHANNEL_ID,
                    ctx.getString(R.string.update_channel),
                    NotificationManager.IMPORTANCE_DEFAULT
                )
            )
        }

        val intent = Intent(ctx, UpdateActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        val pi = PendingIntent.getActivity(
            ctx, 0, intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val n = NotificationCompat.Builder(ctx, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .setContentTitle(ctx.getString(R.string.update_notif_title, info.versionName))
            .setContentText(ctx.getString(R.string.update_notif_body))
            .setStyle(NotificationCompat.BigTextStyle().bigText(
                info.notes.ifBlank { ctx.getString(R.string.update_notif_body) }
            ))
            .setContentIntent(pi)
            .setAutoCancel(true)
            .build()

        try {
            NotificationManagerCompat.from(ctx).notify(NOTIF_ID, n)
        } catch (e: SecurityException) {
            // اجازه‌ی اعلان لحظه‌ی آخر گرفته شد — مهم نیست، بنر داخل اپ هست
        }
    }
}
