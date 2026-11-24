package com.hermesvpn.app

import android.animation.AnimatorSet
import android.animation.ObjectAnimator
import android.content.Intent
import android.content.SharedPreferences
import android.net.VpnService
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.view.animation.AccelerateDecelerateInterpolator
import android.view.animation.LinearInterpolator
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * صفحه‌ی اصلی (اتصال). تنظیمات حساب/سرور/زبان توی SettingsActivity هستن —
 * این صفحه فقط وضعیت و دکمه‌ی اتصال رو نشون می‌ده، مثل رابط‌های VPN حرفه‌ای.
 *
 * TODO برای مرحله‌ی بعد (عمداً هنوز پیاده نشده — کارهای بک‌اند/حساب‌کاربری‌اند):
 *  - گرفتن خودکار شناسه از حساب کاربری (ورود با شماره/تلگرام)، نه وارد کردن دستی
 *  - خرید/تمدید پلن از داخل اپ (Google Play Billing)
 */
class MainActivity : AppCompatActivity() {

    private lateinit var prefs: SharedPreferences
    private lateinit var connectButton: TextView
    private lateinit var downloadSpeedText: TextView
    private lateinit var uploadSpeedText: TextView
    private lateinit var pingText: TextView
    private lateinit var networkMapView: NetworkMapView
    private lateinit var speedGraphView: SpeedGraphView
    private lateinit var currentLocationText: TextView
    private lateinit var connectionDurationText: TextView
    private lateinit var pulseRingOuter: View
    private lateinit var pulseRingInner: View
    private lateinit var orbRing3: View
    private var pulseAnimator: AnimatorSet? = null
    private var breathAnimator: AnimatorSet? = null

    private val durationHandler = Handler(Looper.getMainLooper())
    private val durationTicker = object : Runnable {
        override fun run() {
            updateDurationText()
            durationHandler.postDelayed(this, 1000)
        }
    }

    private val vpnPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == RESULT_OK) {
            actuallyStartVpn()
        } else {
            Toast.makeText(this, getString(R.string.msg_vpn_permission_denied), Toast.LENGTH_LONG).show()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        applyScreenshotProtection(this)
        setContentView(R.layout.activity_main)
        // صفحه‌ی شروع مثل ویندوز: فقط وقتی اپ از آیکون باز می‌شود (نه برگشت از پس‌زمینه
        // یا چرخش صفحه)، ۱٫۱ ثانیه + ۰٫۴۵ ثانیه محو شدن — همان زمان‌بندی نسخه‌ی ویندوز.
        if (savedInstanceState == null && intent?.action == Intent.ACTION_MAIN) {
            val splash = SplashView(this)
            val root = window.decorView as android.view.ViewGroup
            root.addView(splash, android.view.ViewGroup.LayoutParams(
                android.view.ViewGroup.LayoutParams.MATCH_PARENT, android.view.ViewGroup.LayoutParams.MATCH_PARENT))
            splash.postDelayed({
                splash.animate().alpha(0f).setDuration(450).withEndAction { root.removeView(splash) }.start()
            }, 1100)
        }

        prefs = getSharedPreferences("hermesvpn_prefs", MODE_PRIVATE)
        connectButton = findViewById(R.id.connectButton)
        downloadSpeedText = findViewById(R.id.downloadSpeedText)
        uploadSpeedText = findViewById(R.id.uploadSpeedText)
        pingText = findViewById(R.id.pingText)
        networkMapView = findViewById(R.id.networkMapView)
        speedGraphView = findViewById(R.id.speedGraphView)
        currentLocationText = findViewById(R.id.currentLocationText)
        connectionDurationText = findViewById(R.id.connectionDurationText)
        pulseRingOuter = findViewById(R.id.pulseRingOuter)
        pulseRingInner = findViewById(R.id.pulseRingInner)
        orbRing3 = findViewById(R.id.orbRing3)

        findViewById<View>(R.id.settingsButton).setOnClickListener {
            startActivity(Intent(this, SettingsActivity::class.java))
        }

        findViewById<View>(R.id.buyButton).setOnClickListener {
            startActivity(Intent(this, PurchaseActivity::class.java))
        }

        connectButton.setOnClickListener {
            if (HermesVpnService.isRunning) {
                disconnect()
            } else {
                connect()
            }
        }

        BottomNav.bind(this, findViewById(R.id.bottomNav), BottomNav.TAB_HOME)

        updateStatusUi()
        handleAutoConnect(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleAutoConnect(intent)
    }

    /** وقتی کاربر از تب دیگه‌ای دکمه‌ی Connect نوار پایین رو زده — اجازه‌ی
     * VPN فقط از اینجا گرفته می‌شه، پس اتصال هم باید همین‌جا شروع بشه. */
    private fun handleAutoConnect(intent: Intent?) {
        if (intent?.getBooleanExtra(EXTRA_AUTO_CONNECT, false) != true) return
        intent.removeExtra(EXTRA_AUTO_CONNECT)
        if (!HermesVpnService.isRunning) connect()
    }

    override fun onResume() {
        super.onResume()
        updateStatusUi()
        networkMapView.start()
        startBreathing()
        HermesVpnService.onSpeedUpdate = { down, up ->
            downloadSpeedText.text = formatSpeed(down)
            uploadSpeedText.text = formatSpeed(up)
            speedGraphView.addSample(down, up)
        }
        HermesVpnService.onTunnelError = { _ ->
            runOnUiThread {
                Toast.makeText(this, getString(R.string.tunnel_connect_failed), Toast.LENGTH_LONG).show()
                updateStatusUi()
            }
        }
        HermesVpnService.onPingUpdate = { ms ->
            runOnUiThread { pingText.text = if (ms != null) "${ms}ms" else "--" }
        }
        if (HermesVpnService.isRunning) durationHandler.post(durationTicker)
        UpdateUi.applyBadges(this)
        UpdateUi.maybeShowBanner(this)
        checkForUpdate()
    }

    /**
     * بررسی بی‌صدای نسخه‌ی جدید.
     *
     * قبلاً برای هر نسخه باید دستی به تک‌تک مشتری‌ها لینک می‌فرستادیم. حالا اپ
     * خودش روزی دو بار چک می‌کند و اگر نسخه‌ی تازه‌ای بود اعلان می‌دهد — کاربر
     * روی اعلان می‌زند و همان‌جا دانلود و نصب می‌شود.
     *
     * هیچ‌وقت جلوی کار اپ را نمی‌گیرد: روی رشته‌ی جدا اجرا می‌شود و اگر شبکه
     * نبود بی‌صدا رد می‌شود.
     */
    private fun checkForUpdate() {
        if (!UpdateChecker.due(this)) return
        lifecycleScope.launch {
            val info = withContext(Dispatchers.IO) { UpdateChecker.fetch(this@MainActivity) }
            // فقط وقتی واقعاً جواب گرفتیم ۱۵ دقیقه صبر کن؛ اگر شبکه نبود، دفعه‌ی بعدِ باز شدن دوباره چک شود
            if (UpdateChecker.lastFetchOk) UpdateChecker.markChecked(this@MainActivity)
            if (info == null) return@launch
            // اگر کاربر قبلاً همین نسخه را رد کرده، دیگر مزاحمش نشو
            if (!info.mandatory && UpdateChecker.isSkipped(this@MainActivity, info.versionCode)) return@launch
            UpdateUi.save(this@MainActivity, info)
            UpdateChecker.notify(this@MainActivity, info)
            // مثل ویندوز: نوار بالای صفحه یک‌بار برای این نسخه، بعد نقطه‌ی قرمز روی Settings
            UpdateUi.maybeShowBanner(this@MainActivity)
            UpdateUi.applyBadges(this@MainActivity)
        }
    }

    override fun onPause() {
        super.onPause()
        networkMapView.stop()
        stopBreathing()
        HermesVpnService.onPingUpdate = null
        HermesVpnService.onTunnelError = null
        HermesVpnService.onSpeedUpdate = null
        durationHandler.removeCallbacks(durationTicker)
    }

    /** مدت واقعیِ اتصال — از HermesVpnService.connectStartTimeMs، نه یه
     * تایمر جدا که وصل بودن رو فرض کنه. اگه سرویس از قبل (مثلاً با
     * Always-on) وصل بوده، همون لحظه‌ی واقعیِ شروعش رو نشون می‌ده. */
    private fun updateDurationText() {
        val startedAt = HermesVpnService.connectStartTimeMs
        if (!HermesVpnService.isRunning || startedAt <= 0L) return
        val elapsedSec = ((System.currentTimeMillis() - startedAt) / 1000).coerceAtLeast(0)
        val h = elapsedSec / 3600
        val m = (elapsedSec % 3600) / 60
        val s = elapsedSec % 60
        connectionDurationText.text = String.format("%02d:%02d:%02d", h, m, s)
    }

    private fun startPulseAnimation() {
        if (pulseAnimator?.isRunning == true) return
        pulseRingOuter.scaleX = 1f; pulseRingOuter.scaleY = 1f
        pulseRingInner.scaleX = 1f; pulseRingInner.scaleY = 1f
        val outer = ObjectAnimator.ofPropertyValuesHolder(
            pulseRingOuter,
            android.animation.PropertyValuesHolder.ofFloat(View.SCALE_X, 1f, 1.18f),
            android.animation.PropertyValuesHolder.ofFloat(View.SCALE_Y, 1f, 1.18f),
            android.animation.PropertyValuesHolder.ofFloat(View.ALPHA, 0.55f, 0f),
        ).apply {
            duration = 1800
            repeatCount = ObjectAnimator.INFINITE
            interpolator = LinearInterpolator()
        }
        val inner = ObjectAnimator.ofPropertyValuesHolder(
            pulseRingInner,
            android.animation.PropertyValuesHolder.ofFloat(View.SCALE_X, 1f, 1.12f),
            android.animation.PropertyValuesHolder.ofFloat(View.SCALE_Y, 1f, 1.12f),
            android.animation.PropertyValuesHolder.ofFloat(View.ALPHA, 0.7f, 0f),
        ).apply {
            duration = 1800
            startDelay = 600
            repeatCount = ObjectAnimator.INFINITE
            interpolator = LinearInterpolator()
        }
        pulseAnimator = AnimatorSet().apply { playTogether(outer, inner); start() }
    }

    private fun stopPulseAnimation() {
        pulseAnimator?.cancel()
        pulseAnimator = null
        pulseRingOuter.alpha = 0f
        pulseRingInner.alpha = 0f
    }

    /** «نفس‌کشیدن» بسیار آرومِ کره و حلقه‌هاش — همیشه روشنه (حتی وقتی قطعیم)
     * و صرفاً برای اینه که کره حسِ یه جسمِ سه‌بعدیِ زنده رو بده، نه یه دایره‌ی
     * چسبیده به صفحه. هیچ ربطی به وضعیت اتصال نداره. */
    private fun startBreathing() {
        if (breathAnimator?.isRunning == true) return
        val orb = ObjectAnimator.ofPropertyValuesHolder(
            connectButton,
            android.animation.PropertyValuesHolder.ofFloat(View.SCALE_X, 1f, 1.035f),
            android.animation.PropertyValuesHolder.ofFloat(View.SCALE_Y, 1f, 1.035f),
        ).apply {
            duration = 3200
            repeatCount = ObjectAnimator.INFINITE
            repeatMode = ObjectAnimator.REVERSE
            interpolator = AccelerateDecelerateInterpolator()
        }
        val halo = ObjectAnimator.ofPropertyValuesHolder(
            orbRing3,
            android.animation.PropertyValuesHolder.ofFloat(View.SCALE_X, 1f, 1.055f),
            android.animation.PropertyValuesHolder.ofFloat(View.SCALE_Y, 1f, 1.055f),
            android.animation.PropertyValuesHolder.ofFloat(View.ALPHA, 0.22f, 0.45f),
        ).apply {
            duration = 3200
            repeatCount = ObjectAnimator.INFINITE
            repeatMode = ObjectAnimator.REVERSE
            interpolator = AccelerateDecelerateInterpolator()
        }
        breathAnimator = AnimatorSet().apply { playTogether(orb, halo); start() }
    }

    private fun stopBreathing() {
        breathAnimator?.cancel()
        breathAnimator = null
        connectButton.scaleX = 1f
        connectButton.scaleY = 1f
        orbRing3.scaleX = 1f
        orbRing3.scaleY = 1f
        orbRing3.alpha = 0.35f
    }

    /** موقعیت واقعیِ سروری که کانفیگ انتخاب‌شده بهش وصل می‌شه — برای
     * لینک‌های خودِ HermesVPN واقعاً فرانکفورت‌اند (همون‌جایی که سرور
     * تولیدی روشه)، نه یه پرچم نمایشی؛ برای کانفیگ‌های وارد‌شده‌ی دیگه
     * چون موقعیت واقعی‌شون رو نمی‌دونیم، فقط آدرس واقعی‌شون نشون داده می‌شه. */
    private fun updateLocationText() {
        val selected = ConfigStore.selected(this)
        // هر کانفیگی که به سرورهای خودِ HermesVPN وصل می‌شود واقعاً فرانکفورت
        // است — چه از حساب آمده باشد، چه از دکمه‌ی ربات، چه قبلاً دستی وارد شده
        // باشد (دامنه‌هایش همه زیر example.com هستند). برای کانفیگ‌های
        // دیگر موقعیت واقعی را نمی‌دانیم، پس اسمِ کوتاهِ کانفیگ نشان داده می‌شود،
        // نه آدرسِ خامِ سرور که ترکیب صفحه را به هم می‌ریخت.
        val isHermes = selected != null && (
            selected.group == ConfigStore.GROUP_HERMES ||
                selected.account != null ||
                selected.address.endsWith("example.com", ignoreCase = true)
            )
        currentLocationText.text = when {
            selected == null -> ""
            isHermes -> "🇩🇪 ${getString(R.string.location_germany)}"
            else -> selected.name
        }
    }

    private fun formatSpeed(bytesPerSec: Long): String = when {
        bytesPerSec >= 1024 * 1024 -> String.format("%.1f MB/s", bytesPerSec / (1024.0 * 1024.0))
        bytesPerSec >= 1024 -> String.format("%.0f KB/s", bytesPerSec / 1024.0)
        else -> "$bytesPerSec B/s"
    }

    private fun connect() {
        val username = prefs.getString("auth_username", "")?.trim() ?: ""
        val password = prefs.getString("auth_password", "") ?: ""
        val subId = prefs.getString("sub_id", "")?.trim() ?: ""

        // کانفیگ‌های محلی (اسکن QR، لینک دستی، یا JSON خام) به حساب نیازی ندارن — مستقیم وصل می‌شن
        val selected = ConfigStore.selected(this)
        if (selected != null && selected.group == ConfigStore.GROUP_LOCAL) {
            startWithEntry(selected)
            return
        }

        if (username.isEmpty() && subId.isEmpty()) {
            if (selected != null) {
                startWithEntry(selected)
                return
            }
            Toast.makeText(this, getString(R.string.error_no_link), Toast.LENGTH_LONG).show()
            startActivity(Intent(this, SettingsActivity::class.java))
            return
        }

        // اتصال سریع: اگر کانفیگ ذخیره‌شده داریم، همان لحظه وصل شو و لیست کانفیگ‌ها را بعداً از داخل خود
        // تونل تازه کن. قبلاً هر اتصال اول بیرون از VPN با سرور تماس می‌گرفت؛ از ایران ۱۰-۲۰ ثانیه طول
        // می‌کشید و اگر شبکه کند بود، اصلاً وصل نمی‌شد.
        if (selected != null) {
            startWithEntry(selected)
            lifecycleScope.launch {
                kotlinx.coroutines.delay(5000)
                try { AccountSync.refresh(this@MainActivity) } catch (_: Exception) {}
            }
            return
        }

        connectButton.text = getString(R.string.status_connecting)
        lifecycleScope.launch {
            val result = try {
                withContext(Dispatchers.IO) {
                    if (username.isNotEmpty()) AppConfigApi.login(username, password)
                    else AppConfigApi.fetchVlessLinks(subId)
                }
            } catch (e: Exception) {
                Toast.makeText(this@MainActivity, getString(R.string.error_network), Toast.LENGTH_LONG).show()
                updateStatusUi()
                return@launch
            }

            if (!result.ok) {
                val msg = when (result.error) {
                    "not_found" -> getString(R.string.error_not_found)
                    "invalid_credentials" -> getString(R.string.error_invalid_credentials)
                    "blocked" -> getString(R.string.error_blocked)
                    "expired" -> getString(R.string.error_expired)
                    else -> getString(R.string.error_network)
                }
                Toast.makeText(this@MainActivity, msg, Toast.LENGTH_LONG).show()
                updateStatusUi()
                return@launch
            }

            ConfigStore.syncAccountLinks(this@MainActivity, result.links)

            // اگه کاربر توی تب Configs کانفیگی رو انتخاب کرده، همون اولویت داره
            val selectedEntry = ConfigStore.selected(this@MainActivity)
            if (selectedEntry != null) {
                startWithEntry(selectedEntry)
                return@launch
            }
            val preferred = prefs.getString("preferred_server", "reality") ?: "reality"
            val chosen = pickLink(result.links, preferred)
            if (chosen == null) {
                Toast.makeText(this@MainActivity, getString(R.string.error_config), Toast.LENGTH_LONG).show()
                updateStatusUi()
                return@launch
            }

            val configJson = try {
                VlessConfig.muxEnabled = getSharedPreferences("hermesvpn_prefs", MODE_PRIVATE)
                    .getBoolean("mux_enabled", false)
                VlessConfig.buildXrayConfig(chosen, 10808)
            } catch (e: VlessConfig.ParseException) {
                Toast.makeText(this@MainActivity, "${getString(R.string.error_config)}: ${e.message}", Toast.LENGTH_LONG).show()
                updateStatusUi()
                return@launch
            }

            val prepareIntent = VpnService.prepare(this@MainActivity)
            pendingConfig = configJson
            if (prepareIntent != null) {
                vpnPermissionLauncher.launch(prepareIntent)
            } else {
                actuallyStartVpn()
            }
        }
    }

    /** ترجیح کاربر (سریع/مقاوم = Reality، پیش‌فرض = Main)؛ اگه اون یکی موجود نبود، هرچی هست. */
    private fun pickLink(links: List<AppConfigApi.VlessLink>, preferred: String): String? {
        if (links.isEmpty()) return null
        val wantsReality = preferred == "reality"
        val match = links.firstOrNull { l ->
            if (wantsReality) l.label.contains("Reality") else l.label.contains("Main")
        }
        return (match ?: links.first()).uri
    }

    /** یک کانفیگ مشخص را مستقیم به هسته می‌دهد (بدون رفت‌وبرگشت به سرور
     * حساب) — چه از لینک vless ساخته بشه چه از یک outbound خامِ وارد‌شده. */
    private fun startWithEntry(entry: ConfigStore.Entry) {
        val configJson = try {
            if (entry.rawOutboundJson != null) {
                VlessConfig.muxEnabled = getSharedPreferences("hermesvpn_prefs", MODE_PRIVATE)
                    .getBoolean("mux_enabled", false)
                VlessConfig.buildXrayConfigFromOutbound(entry.rawOutboundJson, 10808)
            } else {
                VlessConfig.muxEnabled = getSharedPreferences("hermesvpn_prefs", MODE_PRIVATE)
                    .getBoolean("mux_enabled", false)
                VlessConfig.buildXrayConfig(entry.uri, 10808)
            }
        } catch (e: VlessConfig.ParseException) {
            Toast.makeText(this, "${getString(R.string.error_config)}: ${e.message}", Toast.LENGTH_LONG).show()
            updateStatusUi()
            return
        }
        pendingConfig = configJson
        val prepareIntent = VpnService.prepare(this)
        if (prepareIntent != null) {
            vpnPermissionLauncher.launch(prepareIntent)
        } else {
            actuallyStartVpn()
        }
    }

    private var pendingConfig: String? = null

    private fun actuallyStartVpn() {
        val config = pendingConfig ?: return
        val intent = Intent(this, HermesVpnService::class.java).apply {
            action = HermesVpnService.ACTION_CONNECT
            putExtra(HermesVpnService.EXTRA_XRAY_CONFIG, config)
        }
        startForegroundService(intent)
        connectButton.text = getString(R.string.status_connecting)
        connectButton.postDelayed({ updateStatusUi() }, 1500)
    }

    private fun disconnect() {
        val intent = Intent(this, HermesVpnService::class.java).apply {
            action = HermesVpnService.ACTION_DISCONNECT
        }
        startService(intent)
        connectButton.postDelayed({ updateStatusUi() }, 500)
    }

    private fun updateStatusUi() {
        updateLocationText()
        if (HermesVpnService.isRunning) {
            connectButton.text = getString(R.string.btn_disconnect)
            connectButton.background = ContextCompat.getDrawable(this, R.drawable.bg_connect_orb)
            connectionDurationText.visibility = View.VISIBLE
            updateDurationText()
            durationHandler.removeCallbacks(durationTicker)
            durationHandler.post(durationTicker)
            startPulseAnimation()
        } else {
            connectButton.text = getString(R.string.btn_connect)
            connectButton.background = ContextCompat.getDrawable(this, R.drawable.bg_connect_orb_off)
            downloadSpeedText.text = formatSpeed(0)
            uploadSpeedText.text = formatSpeed(0)
            pingText.text = "--"
            connectionDurationText.visibility = View.INVISIBLE
            durationHandler.removeCallbacks(durationTicker)
            stopPulseAnimation()
            speedGraphView.clear()
        }
    }

    companion object {
        const val EXTRA_AUTO_CONNECT = "auto_connect"
    }
}
