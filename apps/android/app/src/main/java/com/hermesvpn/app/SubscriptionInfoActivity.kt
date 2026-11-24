package com.hermesvpn.app

import android.content.Intent
import android.content.SharedPreferences
import android.os.Bundle
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.google.android.material.button.MaterialButton
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class SubscriptionInfoActivity : AppCompatActivity() {

    private lateinit var prefs: SharedPreferences

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        applyScreenshotProtection(this)
        setContentView(R.layout.activity_subscription_info)
        prefs = getSharedPreferences("hermesvpn_prefs", MODE_PRIVATE)

        findViewById<android.widget.ImageButton>(R.id.backButton).setOnClickListener { finish() }

        findViewById<MaterialButton>(R.id.loginNavButton).setOnClickListener {
            startActivity(Intent(this, AuthActivity::class.java))
        }
        findViewById<MaterialButton>(R.id.registerNavButton).setOnClickListener {
            startActivity(Intent(this, AuthActivity::class.java).apply {
                putExtra(AuthActivity.EXTRA_START_ON_REGISTER, true)
            })
        }
        findViewById<MaterialButton>(R.id.buyPlanButton).setOnClickListener {
            startActivity(Intent(this, PurchaseActivity::class.java))
        }
        findViewById<MaterialButton>(R.id.refreshButton).setOnClickListener { refresh() }
        findViewById<MaterialButton>(R.id.logoutButton).setOnClickListener {
            prefs.edit().putString("auth_username", "").putString("auth_password", "").apply()
            Toast.makeText(this, getString(R.string.logout_toast), Toast.LENGTH_SHORT).show()
            render()
        }
    }

    override fun onResume() {
        super.onResume()
        render()
        if (prefs.getString("auth_username", "")?.isNotEmpty() == true) refresh()
    }

    private fun render() {
        val username = prefs.getString("auth_username", "")?.trim() ?: ""
        val loggedIn = username.isNotEmpty()
        findViewById<LinearLayout>(R.id.loggedInGroup).visibility = if (loggedIn) View.VISIBLE else View.GONE
        findViewById<LinearLayout>(R.id.loggedOutGroup).visibility = if (loggedIn) View.GONE else View.VISIBLE
        if (!loggedIn) return

        findViewById<TextView>(R.id.usernameText).text = username

        val expiry = prefs.getLong("account_expiry", 0L)
        findViewById<TextView>(R.id.expiryText).text = if (expiry > 0) {
            SimpleDateFormat("yyyy/MM/dd", Locale.US).format(Date(expiry))
        } else {
            getString(R.string.subscription_unlimited)
        }

        val totalGb = prefs.getLong("account_total_gb", 0L)
        findViewById<TextView>(R.id.trafficText).text = if (totalGb > 0) {
            "${totalGb / (1024L * 1024L * 1024L)} GB"
        } else {
            getString(R.string.subscription_unlimited)
        }

        val count = ConfigStore.all(this).count { it.group == ConfigStore.GROUP_HERMES }
        findViewById<TextView>(R.id.configCountText).text = count.toString()
    }

    private fun refresh() {
        val username = prefs.getString("auth_username", "")?.trim() ?: return
        val password = prefs.getString("auth_password", "") ?: return
        if (username.isEmpty()) return
        lifecycleScope.launch {
            val result = try {
                withContext(Dispatchers.IO) { AppConfigApi.login(username, password) }
            } catch (e: Exception) {
                return@launch
            }
            if (!result.ok) return@launch
            prefs.edit()
                .putLong("account_expiry", result.expiryTime)
                .putLong("account_total_gb", result.totalGb)
                .apply()
            ConfigStore.syncAccountLinks(this@SubscriptionInfoActivity, result.links)
            render()
        }
    }
}
