package com.hermesvpn.app

import android.content.SharedPreferences
import android.os.Bundle
import android.view.View
import android.widget.LinearLayout
import android.widget.RadioGroup
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.textfield.TextInputEditText

class DnsSettingsActivity : AppCompatActivity() {

    private lateinit var prefs: SharedPreferences

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        applyScreenshotProtection(this)
        setContentView(R.layout.activity_dns_settings)
        prefs = getSharedPreferences("hermesvpn_prefs", MODE_PRIVATE)

        findViewById<android.widget.ImageButton>(R.id.backButton).setOnClickListener { finish() }

        val group = findViewById<RadioGroup>(R.id.dnsRadioGroup)
        val customGroup = findViewById<LinearLayout>(R.id.customDnsGroup)
        val input = findViewById<TextInputEditText>(R.id.dnsServerInput)

        val current = prefs.getString("dns_server", CLOUDFLARE) ?: CLOUDFLARE
        input.setText(current)
        when (current) {
            CLOUDFLARE -> group.check(R.id.dnsCloudflare)
            GOOGLE -> group.check(R.id.dnsGoogle)
            ADGUARD -> group.check(R.id.dnsAdguard)
            else -> group.check(R.id.dnsCustom)
        }
        customGroup.visibility = if (group.checkedRadioButtonId == R.id.dnsCustom) View.VISIBLE else View.GONE

        group.setOnCheckedChangeListener { _, checkedId ->
            val value = when (checkedId) {
                R.id.dnsCloudflare -> CLOUDFLARE
                R.id.dnsGoogle -> GOOGLE
                R.id.dnsAdguard -> ADGUARD
                else -> null
            }
            customGroup.visibility = if (value == null) View.VISIBLE else View.GONE
            if (value != null) {
                prefs.edit().putString("dns_server", value).apply()
                input.setText(value)
            }
        }

        input.setOnFocusChangeListener { _, hasFocus ->
            if (!hasFocus && group.checkedRadioButtonId == R.id.dnsCustom) {
                val value = input.text?.toString()?.trim()
                prefs.edit().putString("dns_server", if (value.isNullOrEmpty()) CLOUDFLARE else value).apply()
            }
        }
    }

    companion object {
        private const val CLOUDFLARE = "1.1.1.1"
        private const val GOOGLE = "8.8.8.8"
        private const val ADGUARD = "94.140.14.14"
    }
}
