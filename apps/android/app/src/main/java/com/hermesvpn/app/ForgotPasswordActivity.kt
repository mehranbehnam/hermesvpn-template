package com.hermesvpn.app

import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.View
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.google.android.material.button.MaterialButton
import com.google.android.material.textfield.TextInputEditText
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** رمز تازه به تلگرامِ خودِ صاحب اکانت فرستاده می‌شه — نه به این صفحه. برای
 * همین پیام موفقیت همیشه یکیه، حتی اگه اکانتی پیدا نشه: از روی جواب نباید
 * بشه فهمید کدوم یوزرنیم/ایمیل مشتری ماست. */
class ForgotPasswordActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        applyScreenshotProtection(this)
        setContentView(R.layout.activity_forgot_password)

        findViewById<android.widget.ImageButton>(R.id.backButton).setOnClickListener { finish() }

        val input = findViewById<TextInputEditText>(R.id.identifierInput)
        val button = findViewById<MaterialButton>(R.id.sendResetButton)
        val resultText = findViewById<TextView>(R.id.resultText)

        button.setOnClickListener {
            val identifier = input.text?.toString()?.trim()?.removePrefix("@") ?: ""
            if (identifier.isEmpty()) {
                Toast.makeText(this, getString(R.string.error_fill_all_fields), Toast.LENGTH_LONG).show()
                return@setOnClickListener
            }
            button.isEnabled = false
            lifecycleScope.launch {
                val response = withContext(Dispatchers.IO) { ProfileApi.forgotPassword(identifier) }
                button.isEnabled = true
                if (!response.ok) {
                    Toast.makeText(
                        this@ForgotPasswordActivity,
                        getString(R.string.error_network),
                        Toast.LENGTH_LONG
                    ).show()
                    return@launch
                }
                resultText.visibility = View.VISIBLE
                resultText.text = getString(R.string.forgot_password_sent)
            }
        }

        findViewById<TextView>(R.id.openBotText).setOnClickListener {
            try {
                startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(SettingsActivity.BOT_CHAT_URL)))
            } catch (e: ActivityNotFoundException) {
                Toast.makeText(this, getString(R.string.error_telegram_not_installed), Toast.LENGTH_LONG).show()
            }
        }
    }
}
