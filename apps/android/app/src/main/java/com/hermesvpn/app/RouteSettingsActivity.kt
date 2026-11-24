package com.hermesvpn.app

import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.SharedPreferences
import android.net.Uri
import android.os.Bundle
import android.widget.RadioGroup
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.button.MaterialButton
import com.google.android.material.textfield.TextInputEditText

class RouteSettingsActivity : AppCompatActivity() {

    private lateinit var prefs: SharedPreferences

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        applyScreenshotProtection(this)
        setContentView(R.layout.activity_route_settings)
        prefs = getSharedPreferences("hermesvpn_prefs", MODE_PRIVATE)

        findViewById<android.widget.ImageButton>(R.id.backButton).setOnClickListener { finish() }

        val serverGroup = findViewById<RadioGroup>(R.id.serverRadioGroup)
        serverGroup.check(
            if (prefs.getString("preferred_server", "reality") == "main") R.id.radioMain else R.id.radioReality
        )
        serverGroup.setOnCheckedChangeListener { _, checkedId ->
            val value = if (checkedId == R.id.radioMain) "main" else "reality"
            prefs.edit().putString("preferred_server", value).apply()
        }

        val routeGroup = findViewById<RadioGroup>(R.id.routeModeGroup)
        routeGroup.check(
            if (prefs.getString("route_mode", "global") == "bypass_iran") R.id.routeBypassIran else R.id.routeGlobal
        )
        routeGroup.setOnCheckedChangeListener { _, checkedId ->
            val value = if (checkedId == R.id.routeBypassIran) "bypass_iran" else "global"
            prefs.edit().putString("route_mode", value).apply()
        }

        val proxyInput = findViewById<TextInputEditText>(R.id.telegramProxyInput)
        findViewById<MaterialButton>(R.id.openTelegramButton).setOnClickListener {
            val link = proxyInput.text?.toString()?.trim() ?: ""
            if (!link.startsWith("tg://proxy") && !link.contains("proxy?server=")) {
                Toast.makeText(this, getString(R.string.error_bad_proxy_link), Toast.LENGTH_LONG).show()
                return@setOnClickListener
            }
            try {
                startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(link)))
            } catch (e: ActivityNotFoundException) {
                Toast.makeText(this, getString(R.string.error_telegram_not_installed), Toast.LENGTH_LONG).show()
            }
        }
    }
}
