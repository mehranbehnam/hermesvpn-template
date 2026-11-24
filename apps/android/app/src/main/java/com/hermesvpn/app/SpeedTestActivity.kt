package com.hermesvpn.app

import android.os.Bundle
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.google.android.material.button.MaterialButton
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.net.URL

class SpeedTestActivity : AppCompatActivity() {

    private lateinit var resultText: TextView
    private lateinit var unitText: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        applyScreenshotProtection(this)
        setContentView(R.layout.activity_speed_test)

        findViewById<android.widget.ImageButton>(R.id.backButton).setOnClickListener { finish() }
        resultText = findViewById(R.id.speedTestResultText)
        unitText = findViewById(R.id.speedTestUnitText)

        val selected = ConfigStore.selected(this)
        findViewById<TextView>(R.id.speedTestTargetText).text =
            selected?.name ?: getString(R.string.speed_test_no_config)

        findViewById<MaterialButton>(R.id.runLatencyButton).setOnClickListener { runLatency() }
        findViewById<MaterialButton>(R.id.runDownloadButton).setOnClickListener { runDownload() }
    }

    private fun runLatency() {
        val entry = ConfigStore.selected(this) ?: return
        resultText.text = "…"
        unitText.text = getString(R.string.speed_test_latency)
        lifecycleScope.launch {
            val ping = withContext(Dispatchers.IO) { PingTester.measure(entry.uri) }
            ConfigStore.setPing(this@SpeedTestActivity, entry.id, ping)
            resultText.text = if (ping >= 0) ping.toString() else getString(R.string.ping_timeout)
        }
    }

    /** سرعت واقعی دانلود از مسیر فعلی دستگاه — اگر VPN وصل باشه از داخل تونل
     * اندازه گرفته می‌شه، چون کل ترافیک اپ از همون مسیر می‌ره. */
    private fun runDownload() {
        resultText.text = "…"
        unitText.text = getString(R.string.speed_test_download)
        lifecycleScope.launch {
            val mbps = withContext(Dispatchers.IO) { measureDownloadMbps() }
            resultText.text = if (mbps >= 0) String.format("%.1f", mbps) else "—"
        }
    }

    private fun measureDownloadMbps(): Double {
        return try {
            val conn = URL(TEST_FILE_URL).openConnection()
            conn.connectTimeout = 10_000
            conn.readTimeout = 15_000
            val start = System.currentTimeMillis()
            var total = 0L
            conn.getInputStream().use { stream ->
                val buffer = ByteArray(16 * 1024)
                while (true) {
                    val read = stream.read(buffer)
                    if (read <= 0) break
                    total += read
                    if (System.currentTimeMillis() - start > MAX_TEST_MS) break
                }
            }
            val seconds = (System.currentTimeMillis() - start) / 1000.0
            if (seconds <= 0) -1.0 else (total * 8.0 / 1_000_000.0) / seconds
        } catch (e: Exception) {
            -1.0
        }
    }

    companion object {
        private const val TEST_FILE_URL = "https://speed.cloudflare.com/__down?bytes=10000000"
        private const val MAX_TEST_MS = 10_000
    }
}
