package com.hermesvpn.app

import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.SharedPreferences
import android.os.Bundle
import android.view.View
import android.widget.CheckBox
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity

class TunnelSettingsActivity : AppCompatActivity() {

    private lateinit var prefs: SharedPreferences

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        applyScreenshotProtection(this)
        setContentView(R.layout.activity_tunnel_settings)
        prefs = getSharedPreferences("hermesvpn_prefs", MODE_PRIVATE)

        findViewById<android.widget.ImageButton>(R.id.backButton).setOnClickListener { finish() }

        val perAppRow = findViewById<View>(R.id.perAppRow)
        perAppRow.findViewById<TextView>(R.id.rowTitle).text = getString(R.string.settings_app_exclusions)
        perAppRow.setOnClickListener {
            startActivity(Intent(this, AppExclusionActivity::class.java))
        }

        bindCheck(R.id.bypassLanCheck, "bypass_lan", false)
        bindCheck(R.id.bypassIranAppsCheck, "bypass_ir_apps", false)
        bindCheck(R.id.muxCheck, "mux_enabled", false)
        findViewById<android.widget.TextView>(R.id.tunSummary).text =
            prefs.getString("last_tun_summary", null)
                ?: getString(R.string.tunnel_summary_none)
        bindCheck(R.id.ipv6Check, "enable_ipv6", false)
        bindCheck(R.id.unmeteredHintCheck, "unmetered_hint", false)

        val screenshot = findViewById<CheckBox>(R.id.screenshotProtectionCheck)
        screenshot.isChecked = prefs.getBoolean("screenshot_protection", false)
        screenshot.setOnCheckedChangeListener { _, checked ->
            prefs.edit().putBoolean("screenshot_protection", checked).apply()
            applyScreenshotProtection(this)
        }

        findViewById<View>(R.id.systemVpnSettingsRow).setOnClickListener {
            try {
                startActivity(Intent(android.provider.Settings.ACTION_VPN_SETTINGS))
            } catch (e: ActivityNotFoundException) {
                Toast.makeText(this, getString(R.string.error_network), Toast.LENGTH_SHORT).show()
            }
        }
    }

    override fun onResume() {
        super.onResume()
        val mode = prefs.getString("tunnel_mode", "exclude")
        val count = if (mode == "include") {
            prefs.getStringSet("included_apps", emptySet())?.size ?: 0
        } else {
            prefs.getStringSet("excluded_apps", emptySet())?.size ?: 0
        }
        findViewById<View>(R.id.perAppRow).findViewById<TextView>(R.id.rowValue).text =
            if (count == 0) getString(R.string.tunnel_per_app_off) else getString(R.string.tunnel_per_app_count, count)
    }

    private fun bindCheck(viewId: Int, key: String, default: Boolean) {
        val check = findViewById<CheckBox>(viewId)
        check.isChecked = prefs.getBoolean(key, default)
        check.setOnCheckedChangeListener { _, checked ->
            prefs.edit().putBoolean(key, checked).apply()
        }
    }
}
