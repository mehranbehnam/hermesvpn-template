package com.hermesvpn.app

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity

class AboutActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        applyScreenshotProtection(this)
        setContentView(R.layout.activity_about)

        findViewById<android.widget.ImageButton>(R.id.backButton).setOnClickListener { finish() }
        findViewById<TextView>(R.id.versionText).text = BuildConfig.VERSION_NAME
        findViewById<android.widget.LinearLayout>(R.id.privacyPolicyRow).setOnClickListener {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://app.example.com/")))
        }
    }
}
