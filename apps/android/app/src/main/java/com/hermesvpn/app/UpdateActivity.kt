package com.hermesvpn.app

import android.app.DownloadManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.database.Cursor
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.view.View
import android.widget.Button
import android.widget.ProgressBar
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.FileProvider
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

/**
 * صفحه‌ی به‌روزرسانی: دانلود و نصب همین‌جا، بدون رفتن به تلگرام یا مرورگر.
 *
 * فایل داخل پوشه‌ی خصوصیِ خود اپ دانلود می‌شود و با FileProvider به نصب‌کننده‌ی
 * اندروید داده می‌شود — نه در حافظه‌ی عمومی، پس هیچ اپ دیگری نمی‌تواند فایل را
 * بین دانلود و نصب عوض کند.
 */
class UpdateActivity : AppCompatActivity() {

    private lateinit var title: TextView
    private lateinit var notes: TextView
    private lateinit var status: TextView
    private lateinit var bar: ProgressBar
    private lateinit var action: Button
    private lateinit var later: Button

    private var info: UpdateChecker.Info? = null
    private var downloadId: Long = -1
    private var apkFile: File? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_update)

        title = findViewById(R.id.updTitle)
        notes = findViewById(R.id.updNotes)
        status = findViewById(R.id.updStatus)
        bar = findViewById(R.id.updProgress)
        action = findViewById(R.id.updAction)
        later = findViewById(R.id.updLater)

        later.setOnClickListener {
            info?.let { UpdateChecker.skip(this, it.versionCode) }
            finish()
        }

        title.text = getString(R.string.update_checking)
        action.isEnabled = false

        lifecycleScope.launch {
            val found = withContext(Dispatchers.IO) { UpdateChecker.fetch(this@UpdateActivity) }
            UpdateChecker.markChecked(this@UpdateActivity)
            if (found == null) {
                title.text = getString(R.string.update_none_title)
                notes.text = getString(R.string.update_none_body, BuildConfig.VERSION_NAME)
                action.text = getString(R.string.update_close)
                action.isEnabled = true
                action.setOnClickListener { finish() }
                later.visibility = View.GONE
                return@launch
            }
            info = found
            title.text = getString(R.string.update_available_title, found.versionName)
            notes.text = found.notes.ifBlank { getString(R.string.update_default_notes) }
            action.text = getString(R.string.update_download)
            action.isEnabled = true
            action.setOnClickListener { startDownload(found) }
            if (found.mandatory) later.visibility = View.GONE
        }
    }

    private fun startDownload(info: UpdateChecker.Info) {
        action.isEnabled = false
        bar.visibility = View.VISIBLE
        bar.isIndeterminate = false
        bar.progress = 0
        status.visibility = View.VISIBLE
        status.text = getString(R.string.update_downloading)

        val dir = File(getExternalFilesDir(null), "updates").apply { mkdirs() }
        val target = File(dir, "HermesVPN-${info.versionCode}.apk")
        if (target.exists()) target.delete()
        apkFile = target

        val req = DownloadManager.Request(Uri.parse(info.apkUrl))
            .setTitle(getString(R.string.app_name))
            .setDescription(getString(R.string.update_downloading))
            .setDestinationUri(Uri.fromFile(target))
            .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE)
            .setAllowedOverMetered(true)

        val dm = getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
        downloadId = dm.enqueue(req)
        watch(dm)
    }

    /** درصد واقعی را نشان می‌دهد — روی 4G ایران دانلود ۵۰ مگابایت طول می‌کشد و
     *  بدون نوار پیشرفت کاربر فکر می‌کند اپ هنگ کرده. */
    private fun watch(dm: DownloadManager) {
        lifecycleScope.launch {
            while (true) {
                delay(700)
                val q = DownloadManager.Query().setFilterById(downloadId)
                val c: Cursor = dm.query(q) ?: break
                c.use {
                    if (!it.moveToFirst()) return@use
                    val st = it.getInt(it.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS))
                    val got = it.getLong(it.getColumnIndexOrThrow(DownloadManager.COLUMN_BYTES_DOWNLOADED_SO_FAR))
                    val total = it.getLong(it.getColumnIndexOrThrow(DownloadManager.COLUMN_TOTAL_SIZE_BYTES))
                    when (st) {
                        DownloadManager.STATUS_SUCCESSFUL -> {
                            bar.progress = 100
                            status.text = getString(R.string.update_installing)
                            install()
                            return@launch
                        }
                        DownloadManager.STATUS_FAILED -> {
                            status.text = getString(R.string.update_failed)
                            bar.visibility = View.GONE
                            action.isEnabled = true
                            action.text = getString(R.string.update_retry)
                            return@launch
                        }
                        else -> if (total > 0) {
                            bar.progress = ((got * 100) / total).toInt()
                            status.text = getString(
                                R.string.update_progress,
                                got / 1048576, total / 1048576
                            )
                        }
                    }
                }
            }
        }
    }

    private fun install() {
        val file = apkFile ?: return

        // اندروید ۸ به بعد نصب از منبع ناشناس اجازه‌ی جداگانه می‌خواهد.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && !packageManager.canRequestPackageInstalls()) {
            status.text = getString(R.string.update_need_permission)
            action.isEnabled = true
            action.text = getString(R.string.update_open_settings)
            action.setOnClickListener {
                startActivity(
                    Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:$packageName"))
                )
                // وقتی برگشت، دکمه دوباره نصب را ادامه می‌دهد
                action.text = getString(R.string.update_install)
                action.setOnClickListener { install() }
            }
            return
        }

        val uri = FileProvider.getUriForFile(this, "$packageName.fileprovider", file)
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, "application/vnd.android.package-archive")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        try {
            startActivity(intent)
            finish()
        } catch (e: Exception) {
            status.text = getString(R.string.update_failed)
            action.isEnabled = true
            action.text = getString(R.string.update_retry)
        }
    }
}
