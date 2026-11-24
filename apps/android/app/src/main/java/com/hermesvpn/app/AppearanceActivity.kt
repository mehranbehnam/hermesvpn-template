package com.hermesvpn.app

import android.content.SharedPreferences
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.core.os.LocaleListCompat
import com.google.android.material.button.MaterialButton

class AppearanceActivity : AppCompatActivity() {

    private lateinit var prefs: SharedPreferences

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        applyScreenshotProtection(this)
        setContentView(R.layout.activity_appearance)
        prefs = getSharedPreferences("hermesvpn_prefs", MODE_PRIVATE)

        findViewById<android.widget.ImageButton>(R.id.backButton).setOnClickListener { finish() }

        val setMode = { mode: String ->
            prefs.edit().putString("theme_mode", mode).apply()
            applyThemeMode(mode)
        }
        findViewById<MaterialButton>(R.id.themeLightButton).setOnClickListener { setMode("light") }
        findViewById<MaterialButton>(R.id.themeDarkButton).setOnClickListener { setMode("dark") }
        findViewById<MaterialButton>(R.id.themeSystemButton).setOnClickListener { setMode("system") }

        findViewById<MaterialButton>(R.id.langEnglishButton).setOnClickListener {
            androidx.appcompat.app.AppCompatDelegate.setApplicationLocales(LocaleListCompat.forLanguageTags("en"))
        }
        findViewById<MaterialButton>(R.id.langPersianButton).setOnClickListener {
            androidx.appcompat.app.AppCompatDelegate.setApplicationLocales(LocaleListCompat.forLanguageTags("fa"))
        }
    }
}
