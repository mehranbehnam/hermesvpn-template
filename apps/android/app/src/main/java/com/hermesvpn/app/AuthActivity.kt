package com.hermesvpn.app

import android.content.Intent
import android.content.SharedPreferences
import android.os.Bundle
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.google.android.material.button.MaterialButton
import com.google.android.material.textfield.TextInputEditText
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** ورود و ثبت‌نام توی یک صفحه، با سوییچ بالای فرم. */
class AuthActivity : AppCompatActivity() {

    private lateinit var prefs: SharedPreferences

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        applyScreenshotProtection(this)
        setContentView(R.layout.activity_auth)
        prefs = getSharedPreferences("hermesvpn_prefs", MODE_PRIVATE)

        findViewById<android.widget.ImageButton>(R.id.backButton).setOnClickListener { finish() }

        findViewById<TextView>(R.id.tabLogin).setOnClickListener { showTab(showLogin = true) }
        findViewById<TextView>(R.id.tabRegister).setOnClickListener { showTab(showLogin = false) }
        showTab(showLogin = intent.getBooleanExtra(EXTRA_START_ON_REGISTER, false).not())

        findViewById<TextInputEditText>(R.id.loginIdentifierInput)
            .setText(prefs.getString("auth_username", ""))

        findViewById<MaterialButton>(R.id.loginButton).setOnClickListener { doLogin() }
        findViewById<MaterialButton>(R.id.registerButton).setOnClickListener { doRegister() }

        findViewById<TextView>(R.id.forgotPasswordText).setOnClickListener {
            startActivity(Intent(this, ForgotPasswordActivity::class.java))
        }
        findViewById<TextView>(R.id.registerHelpText).setOnClickListener {
            startActivity(Intent(this, PurchaseActivity::class.java))
        }
    }

    private fun showTab(showLogin: Boolean) {
        findViewById<LinearLayout>(R.id.loginForm).visibility = if (showLogin) View.VISIBLE else View.GONE
        findViewById<LinearLayout>(R.id.registerForm).visibility = if (showLogin) View.GONE else View.VISIBLE

        val active = findViewById<TextView>(if (showLogin) R.id.tabLogin else R.id.tabRegister)
        val inactive = findViewById<TextView>(if (showLogin) R.id.tabRegister else R.id.tabLogin)
        active.setBackgroundResource(R.drawable.bg_segment_active)
        active.setTextColor(ContextCompat.getColor(this, R.color.text_primary))
        inactive.background = null
        inactive.setTextColor(ContextCompat.getColor(this, R.color.text_secondary))
    }

    private fun doLogin() {
        val identifier = findViewById<TextInputEditText>(R.id.loginIdentifierInput)
            .text?.toString()?.trim()?.removePrefix("@") ?: ""
        val password = findViewById<TextInputEditText>(R.id.loginPasswordInput).text?.toString() ?: ""
        if (identifier.isEmpty() || password.isEmpty()) {
            Toast.makeText(this, getString(R.string.error_fill_all_fields), Toast.LENGTH_LONG).show()
            return
        }
        val button = findViewById<MaterialButton>(R.id.loginButton)
        button.isEnabled = false
        lifecycleScope.launch {
            val result = try {
                withContext(Dispatchers.IO) { AppConfigApi.login(identifier, password) }
            } catch (e: Exception) {
                button.isEnabled = true
                Toast.makeText(this@AuthActivity, getString(R.string.error_network), Toast.LENGTH_LONG).show()
                return@launch
            }
            button.isEnabled = true
            if (!result.ok) {
                Toast.makeText(this@AuthActivity, errorMessage(result.error), Toast.LENGTH_LONG).show()
                return@launch
            }
            persistSession(identifier, password, result)
        }
    }

    private fun doRegister() {
        val tgUsername = findViewById<TextInputEditText>(R.id.registerTgInput)
            .text?.toString()?.trim()?.removePrefix("@") ?: ""
        val email = findViewById<TextInputEditText>(R.id.registerEmailInput).text?.toString()?.trim() ?: ""
        val newUsername = findViewById<TextInputEditText>(R.id.registerUsernameInput).text?.toString()?.trim() ?: ""
        val password = findViewById<TextInputEditText>(R.id.registerPasswordInput).text?.toString() ?: ""

        if (tgUsername.isEmpty() || email.isEmpty() || newUsername.isEmpty() || password.isEmpty()) {
            Toast.makeText(this, getString(R.string.error_fill_all_fields), Toast.LENGTH_LONG).show()
            return
        }
        if (!android.util.Patterns.EMAIL_ADDRESS.matcher(email).matches()) {
            Toast.makeText(this, getString(R.string.error_bad_email), Toast.LENGTH_LONG).show()
            return
        }
        if (password.length < 6) {
            Toast.makeText(this, getString(R.string.error_password_too_short), Toast.LENGTH_LONG).show()
            return
        }

        val button = findViewById<MaterialButton>(R.id.registerButton)
        button.isEnabled = false
        lifecycleScope.launch {
            val result = try {
                withContext(Dispatchers.IO) {
                    AppConfigApi.register(tgUsername, email, newUsername, password)
                }
            } catch (e: Exception) {
                button.isEnabled = true
                Toast.makeText(this@AuthActivity, getString(R.string.error_network), Toast.LENGTH_LONG).show()
                return@launch
            }
            button.isEnabled = true
            if (!result.ok) {
                Toast.makeText(this@AuthActivity, errorMessage(result.error), Toast.LENGTH_LONG).show()
                return@launch
            }
            persistSession(newUsername, password, result)
        }
    }

    private fun persistSession(username: String, password: String, result: AppConfigApi.Result) {
        prefs.edit()
            .putString("auth_username", username)
            .putString("auth_password", password)
            .putString("sub_id", "")
            .putLong("account_expiry", result.expiryTime)
            .putLong("account_total_gb", result.totalGb)
            .putLong("last_subscription_update", System.currentTimeMillis())
            .apply()
        // کانفیگ‌های اشتراکِ خریداری‌شده همون لحظه توی تب Configs ظاهر می‌شن
        ConfigStore.syncAccountLinks(this, result.links)
        Toast.makeText(
            this,
            getString(R.string.login_success_with_configs, result.links.size),
            Toast.LENGTH_LONG
        ).show()
        finish()
    }

    private fun errorMessage(error: String?): String = when (error) {
        "invalid_credentials" -> getString(R.string.error_invalid_credentials)
        "blocked" -> getString(R.string.error_blocked)
        "expired" -> getString(R.string.error_expired)
        "not_found" -> getString(R.string.error_register_not_found)
        "username_taken" -> getString(R.string.error_username_taken)
        else -> getString(R.string.error_network)
    }

    companion object {
        const val EXTRA_START_ON_REGISTER = "start_on_register"
    }
}
