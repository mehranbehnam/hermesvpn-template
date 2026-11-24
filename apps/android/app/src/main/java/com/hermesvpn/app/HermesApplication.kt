package com.hermesvpn.app

import android.app.Activity
import android.app.Application
import android.view.WindowManager
import androidx.appcompat.app.AppCompatDelegate

class HermesApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        val prefs = getSharedPreferences("hermesvpn_prefs", MODE_PRIVATE)
        applyThemeMode(prefs.getString("theme_mode", "system") ?: "system")

        // فایل‌های داده‌ی مسیریابی (geoip/geosite) را زود و در پس‌زمینه آماده
        // می‌کنیم. تا وقتی آماده نشده‌اند، VlessConfig سراغ قانون‌های ایران
        // نمی‌رود — چون هسته با ارجاع به فایلِ نبوده اصلاً بالا نمی‌آید و
        // نتیجه‌اش «اصلاً وصل نمی‌شود» است، نه «مسیریابی ناقص».
        // نوار وضعیت/ناوبری گوشی روی متن‌های اپ نیفتد (سامسونگ اندروید ۱۵+ اپ را تمام‌صفحه
        // می‌کند): به محتوای هر صفحه دقیقاً به اندازه‌ی نوارهای سیستم فاصله بده. وقتی
        // سیستم خودش فاصله را رعایت کرده باشد، این عدد صفر است و چیزی عوض نمی‌شود.
        registerActivityLifecycleCallbacks(object : ActivityLifecycleCallbacks {
            override fun onActivityCreated(a: Activity, b: android.os.Bundle?) {
                val content = a.findViewById<android.view.View>(android.R.id.content) ?: return
                androidx.core.view.ViewCompat.setOnApplyWindowInsetsListener(content) { v, insets ->
                    val bars = insets.getInsets(
                        androidx.core.view.WindowInsetsCompat.Type.systemBars() or
                            androidx.core.view.WindowInsetsCompat.Type.displayCutout())
                    v.setPadding(bars.left, bars.top, bars.right, bars.bottom)
                    androidx.core.view.WindowInsetsCompat.CONSUMED
                }
            }
            override fun onActivityStarted(a: Activity) {}
            override fun onActivityResumed(a: Activity) {}
            override fun onActivityPaused(a: Activity) {}
            override fun onActivityStopped(a: Activity) {}
            override fun onActivitySaveInstanceState(a: Activity, b: android.os.Bundle) {}
            override fun onActivityDestroyed(a: Activity) {}
        })

        RouteRules.load(this)
        Thread({ VlessConfig.prepareGeoAssets(this) }, "geo-assets").apply {
            priority = Thread.MIN_PRIORITY
            start()
        }
    }
}

/** جلوی اسکرین‌شات و پیش‌نمایش اپ توی لیست اپ‌های اخیر رو می‌گیره — باید قبل
 * از setContentView صدا زده بشه. */
fun applyScreenshotProtection(activity: Activity) {
    val prefs = activity.getSharedPreferences("hermesvpn_prefs", Activity.MODE_PRIVATE)
    if (prefs.getBoolean("screenshot_protection", false)) {
        activity.window.setFlags(WindowManager.LayoutParams.FLAG_SECURE, WindowManager.LayoutParams.FLAG_SECURE)
    }
}

fun applyThemeMode(mode: String) {
    AppCompatDelegate.setDefaultNightMode(
        when (mode) {
            "dark" -> AppCompatDelegate.MODE_NIGHT_YES
            "light" -> AppCompatDelegate.MODE_NIGHT_NO
            else -> AppCompatDelegate.MODE_NIGHT_FOLLOW_SYSTEM
        }
    )
}
