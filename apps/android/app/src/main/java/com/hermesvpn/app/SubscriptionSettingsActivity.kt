package com.hermesvpn.app

import android.content.SharedPreferences
import android.os.Bundle
import android.view.View
import android.widget.CheckBox
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.google.android.material.button.MaterialButton
import com.google.android.material.textfield.TextInputEditText
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class SubscriptionSettingsActivity : AppCompatActivity() {

    private lateinit var prefs: SharedPreferences

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        applyScreenshotProtection(this)
        setContentView(R.layout.activity_subscription_settings)
        prefs = getSharedPreferences("hermesvpn_prefs", MODE_PRIVATE)

        findViewById<android.widget.ImageButton>(R.id.backButton).setOnClickListener { finish() }

        val input = findViewById<TextInputEditText>(R.id.profileLinkInput)
        input.setText(prefs.getString("sub_id", ""))

        findViewById<MaterialButton>(R.id.saveProfileLinkButton).setOnClickListener {
            val raw = input.text?.toString()?.trim() ?: ""
            if (raw.isEmpty()) {
                Toast.makeText(this, getString(R.string.add_config_invalid), Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            saveAndUpdate(raw)
        }

        findViewById<View>(R.id.updateNowRow).setOnClickListener { updateNow() }

        val autoUpdate = findViewById<CheckBox>(R.id.autoUpdateCheck)
        autoUpdate.isChecked = prefs.getBoolean("subscription_auto_update", true)
        autoUpdate.setOnCheckedChangeListener { _, checked ->
            prefs.edit().putBoolean("subscription_auto_update", checked).apply()
        }

        renderLastUpdate()
    }

    /** لینک اشتراک عمومی (http) مستقیم وارد می‌شه؛ شناسه/لینک اکانت HermesVPN
     * به‌عنوان sub_id ذخیره می‌شه و از مسیر حساب به‌روز می‌شه. */
    private fun saveAndUpdate(raw: String) {
        lifecycleScope.launch {
            val isExternalSubscription = (raw.startsWith("http://") || raw.startsWith("https://")) &&
                !raw.contains("/sub/")
            val message = if (isExternalSubscription) {
                val result = LinkImporter.import(this@SubscriptionSettingsActivity, raw)
                when {
                    result.added > 0 -> getString(R.string.add_config_added, result.added)
                    result.error == "network" -> getString(R.string.error_network)
                    else -> getString(R.string.add_config_all_duplicates)
                }
            } else {
                prefs.edit().putString("sub_id", extractSubId(raw)).apply()
                if (AccountSync.refresh(this@SubscriptionSettingsActivity)) {
                    getString(R.string.subscription_updated)
                } else {
                    getString(R.string.error_not_found)
                }
            }
            Toast.makeText(this@SubscriptionSettingsActivity, message, Toast.LENGTH_LONG).show()
            renderLastUpdate()
        }
    }

    private fun updateNow() {
        lifecycleScope.launch {
            val accountOk = AccountSync.refresh(this@SubscriptionSettingsActivity)
            val subs = LinkImporter.refreshAllSubscriptions(this@SubscriptionSettingsActivity)
            val message = if (accountOk || subs.added > 0) {
                getString(R.string.subscription_updated)
            } else {
                getString(R.string.subscription_up_to_date)
            }
            Toast.makeText(this@SubscriptionSettingsActivity, message, Toast.LENGTH_SHORT).show()
            renderLastUpdate()
        }
    }

    private fun renderLastUpdate() {
        val ts = prefs.getLong("last_subscription_update", 0L)
        findViewById<TextView>(R.id.lastUpdateText).text = if (ts > 0) {
            SimpleDateFormat("yyyy/MM/dd HH:mm", Locale.US).format(Date(ts))
        } else {
            "—"
        }
    }

    private fun extractSubId(input: String): String {
        val marker = "/sub/"
        val idx = input.indexOf(marker)
        return if (idx >= 0) input.substring(idx + marker.length).trim('/', ' ') else input
    }
}
