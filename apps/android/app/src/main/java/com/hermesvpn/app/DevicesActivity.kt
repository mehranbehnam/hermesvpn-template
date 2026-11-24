package com.hermesvpn.app

import android.content.ActivityNotFoundException
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.widget.ImageView
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.google.zxing.BarcodeFormat
import com.journeyapps.barcodescanner.BarcodeEncoder

/**
 * «دستگاه‌ها و اشتراک‌گذاری» — همتای همین صفحه در نسخه‌ی ویندوز:
 * لینک نرم‌افزار ویندوز (فرستادن به کامپیوتر خودت)، QR اپ اندروید برای
 * گوشی‌های دیگر، و معرفی به دوستان (تلگرام/واتس‌اپ/اشتراک سیستم).
 * لینک‌ها ثابت‌اند و همیشه آخرین نسخه را می‌دهند.
 */
class DevicesActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        applyScreenshotProtection(this)
        setContentView(R.layout.activity_devices)

        findViewById<android.widget.ImageButton>(R.id.backButton).setOnClickListener { finish() }
        // کارت بالایی همین صفحه است؛ کلیکش اینجا کاری نمی‌کند
        findViewById<android.view.View>(R.id.devicesPromo).isClickable = false
        findViewById<TextView>(R.id.promoGetWindows).setOnClickListener { sendToPc() }
        findViewById<TextView>(R.id.promoShare).setOnClickListener { shareSystem() }

        findViewById<TextView>(R.id.windowsLinkText).text = LINK_WINDOWS
        findViewById<TextView>(R.id.installedText).text = getString(R.string.dev_installed, BuildConfig.VERSION_NAME)
        findViewById<android.view.View>(R.id.sendToPcButton).setOnClickListener { sendToPc() }
        findViewById<android.view.View>(R.id.copyWindowsButton).setOnClickListener { copy(LINK_WINDOWS) }
        findViewById<android.view.View>(R.id.copyAndroidButton).setOnClickListener { copy(LINK_ANDROID) }
        try {
            findViewById<ImageView>(R.id.androidQr).setImageBitmap(
                BarcodeEncoder().encodeBitmap(LINK_ANDROID, BarcodeFormat.QR_CODE, 512, 512)
            )
        } catch (e: Exception) {
        }

        findViewById<TextView>(R.id.shareTelegram).setOnClickListener {
            open("https://t.me/share/url?url=" + Uri.encode(LINK_ANDROID) + "&text=" + Uri.encode(shareText()))
        }
        findViewById<TextView>(R.id.shareWhatsapp).setOnClickListener {
            open("https://wa.me/?text=" + Uri.encode(shareText()))
        }
        findViewById<TextView>(R.id.shareMore).setOnClickListener { shareSystem() }
    }

    private fun shareText(): String =
        getString(R.string.dev_share_text, LINK_ANDROID, LINK_WINDOWS, SettingsActivity.BOT_CHAT_URL)

    /** لینک ویندوز را با منوی اشتراک سیستم می‌فرستد (مثلاً به Saved Messages تلگرام) تا روی کامپیوتر باز شود. */
    private fun sendToPc() {
        startActivity(Intent.createChooser(
            Intent(Intent.ACTION_SEND).apply {
                type = "text/plain"
                putExtra(Intent.EXTRA_TEXT, "HermesVPN Connect — Windows:\n$LINK_WINDOWS")
            },
            getString(R.string.dev_send_to_pc)
        ))
    }

    private fun shareSystem() {
        startActivity(Intent.createChooser(
            Intent(Intent.ACTION_SEND).apply {
                type = "text/plain"
                putExtra(Intent.EXTRA_TEXT, shareText())
            },
            getString(R.string.dev_share)
        ))
    }

    private fun open(url: String) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
        } catch (e: ActivityNotFoundException) {
            Toast.makeText(this, getString(R.string.error_telegram_not_installed), Toast.LENGTH_LONG).show()
        }
    }

    private fun copy(text: String) {
        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        cm.setPrimaryClip(ClipData.newPlainText("HermesVPN", text))
        Toast.makeText(this, getString(R.string.purchase_copied_toast), Toast.LENGTH_SHORT).show()
    }

    companion object {
        /** ثابت — همیشه آخرین فایل نصبی ویندوز */
        const val LINK_WINDOWS = "https://app.example.com/app/HermesVPN-Connect-Setup.exe"
        /** صفحه‌ی هوشمند: اگر اپ نصب باشد بازش می‌کند، وگرنه آخرین نسخه را دانلود می‌کند */
        const val LINK_ANDROID = "https://app.example.com/open/"
    }
}
