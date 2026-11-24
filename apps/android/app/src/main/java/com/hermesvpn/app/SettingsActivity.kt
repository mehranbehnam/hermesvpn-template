package com.hermesvpn.app

import android.content.ActivityNotFoundException
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.net.Uri
import android.os.Bundle
import android.view.View
import android.widget.ImageView
import android.widget.PopupMenu
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.app.AppCompatDelegate
import androidx.core.os.LocaleListCompat
import java.util.UUID

/** تنظیمات به سبک V2Box: بخش DEVICE (زبان، شناسه‌ی دستگاه، اشتراک) و بخش
 * SETTINGS (تونل، DNS، مسیریابی، اشتراک، تست سرعت، ظاهر، درباره). */
class SettingsActivity : AppCompatActivity() {

    private lateinit var prefs: SharedPreferences

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        applyScreenshotProtection(this)
        setContentView(R.layout.activity_settings)
        prefs = getSharedPreferences("hermesvpn_prefs", MODE_PRIVATE)

        findViewById<android.widget.ImageButton>(R.id.backButton).setOnClickListener { finish() }

        setupLanguageRow()
        setupDeviceId()

        // کارت «روی همه‌ی دستگاه‌هات» بالای تنظیمات
        val openDevices = { startActivity(Intent(this, DevicesActivity::class.java)) }
        findViewById<View>(R.id.devicesPromo).setOnClickListener { openDevices() }
        findViewById<View>(R.id.promoGetWindows).setOnClickListener { openDevices() }
        findViewById<View>(R.id.promoShare).setOnClickListener { openDevices() }

        bindRow(R.id.profileRow, R.drawable.ic_person, R.string.profile_title) {
            val loggedIn = (prefs.getString("auth_username", "") ?: "").isNotEmpty()
            startActivity(
                Intent(this, if (loggedIn) ProfileActivity::class.java else AuthActivity::class.java)
            )
        }
        bindRow(R.id.subscriptionInfoRow, R.drawable.ic_crown, R.string.subscription_info) {
            startActivity(Intent(this, SubscriptionInfoActivity::class.java))
        }
        bindRow(R.id.tunnelSettingsRow, R.drawable.ic_tunnel, R.string.tunnel_settings) {
            startActivity(Intent(this, TunnelSettingsActivity::class.java))
        }
        bindRow(R.id.dnsSettingsRow, R.drawable.ic_dns, R.string.dns_settings) {
            startActivity(Intent(this, DnsSettingsActivity::class.java))
        }
        bindRow(R.id.routeSettingsRow, R.drawable.ic_route, R.string.route_settings) {
            startActivity(Intent(this, RouteSettingsActivity::class.java))
        }
        bindRow(R.id.subscriptionSettingsRow, R.drawable.ic_link, R.string.subscription_settings) {
            startActivity(Intent(this, SubscriptionSettingsActivity::class.java))
        }
        bindRow(R.id.speedTestRow, R.drawable.ic_speed, R.string.speed_test) {
            startActivity(Intent(this, SpeedTestActivity::class.java))
        }
        bindRow(R.id.appearanceRow, R.drawable.ic_appearance, R.string.settings_theme_section) {
            startActivity(Intent(this, AppearanceActivity::class.java))
        }
        bindRow(R.id.updateRow, R.drawable.ic_info, R.string.update_check_menu) {
            startActivity(Intent(this, UpdateActivity::class.java))
        }
        bindRow(R.id.aboutRow, R.drawable.ic_info, R.string.settings_about_section) {
            startActivity(Intent(this, AboutActivity::class.java))
        }
        bindRow(R.id.telegramChannelRow, R.drawable.ic_telegram, R.string.telegram_channel_row) {
            openUrl(TELEGRAM_CHANNEL_URL)
        }
        bindRow(R.id.supportRow, R.drawable.ic_person, R.string.support_row) {
            openUrl(BOT_CHAT_URL)
        }

        BottomNav.bind(this, findViewById(R.id.bottomNav), BottomNav.TAB_SETTINGS)
    }

    override fun onResume() {
        super.onResume()
        // مثل ویندوز: نقطه‌ی قرمز روی Settings + کارت «نسخه‌ی جدید» پایین همین صفحه
        UpdateUi.applyBadges(this)
        UpdateUi.bindSettingsCard(this, findViewById(R.id.settingsList))
    }

    private fun setupLanguageRow() {
        val row = findViewById<View>(R.id.languageRow)
        row.findViewById<TextView>(R.id.rowTitle).text = getString(R.string.language_row)
        val value = row.findViewById<TextView>(R.id.rowValue)
        value.text = currentLanguageLabel()
        row.setOnClickListener { anchor ->
            PopupMenu(this, anchor).apply {
                menu.add(0, 0, 0, getString(R.string.language_auto))
                menu.add(0, 1, 1, getString(R.string.lang_english))
                menu.add(0, 2, 2, getString(R.string.lang_persian))
                setOnMenuItemClickListener { item ->
                    val tags = when (item.itemId) {
                        1 -> "en"
                        2 -> "fa"
                        else -> ""
                    }
                    prefs.edit().putString("app_language", tags).apply()
                    AppCompatDelegate.setApplicationLocales(LocaleListCompat.forLanguageTags(tags))
                    value.text = currentLanguageLabel()
                    true
                }
                show()
            }
        }
    }

    private fun currentLanguageLabel(): String = when (prefs.getString("app_language", "")) {
        "en" -> getString(R.string.lang_english)
        "fa" -> getString(R.string.lang_persian)
        else -> getString(R.string.language_auto)
    }

    /** شناسه‌ی محلی دستگاه برای پشتیبانی — هیچ‌جا فرستاده نمی‌شه، فقط اینجا
     * ساخته و نشون داده می‌شه تا مشتری بتونه موقع تیکت کپی‌ش کنه. */
    private fun setupDeviceId() {
        var id = prefs.getString("device_id", "") ?: ""
        if (id.isEmpty()) {
            id = UUID.randomUUID().toString()
            prefs.edit().putString("device_id", id).apply()
        }
        findViewById<TextView>(R.id.deviceIdText).text = id
        findViewById<View>(R.id.deviceIdCopyButton).setOnClickListener {
            val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            cm.setPrimaryClip(ClipData.newPlainText("HermesVPN Device ID", id))
            Toast.makeText(this, getString(R.string.purchase_copied_toast), Toast.LENGTH_SHORT).show()
        }
    }

    private fun bindRow(rowId: Int, icon: Int, titleRes: Int, onClick: () -> Unit) {
        val row = findViewById<View>(rowId)
        row.findViewById<ImageView>(R.id.rowIcon).setImageResource(icon)
        row.findViewById<TextView>(R.id.rowTitle).text = getString(titleRes)
        row.findViewById<TextView>(R.id.rowSubtitle).visibility = View.GONE
        row.setOnClickListener { onClick() }
    }

    private fun openUrl(url: String) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
        } catch (e: ActivityNotFoundException) {
            Toast.makeText(this, getString(R.string.error_telegram_not_installed), Toast.LENGTH_LONG).show()
        }
    }

    companion object {
        const val TELEGRAM_CHANNEL_URL = "https://t.me/hermevpn"
        const val BOT_CHAT_URL = "https://t.me/YourSalesBot"
    }
}
