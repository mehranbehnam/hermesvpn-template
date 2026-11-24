package com.hermesvpn.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.content.SharedPreferences
import android.net.Uri
import android.net.VpnService
import android.os.Handler
import android.os.Looper
import android.os.ParcelFileDescriptor
import android.util.Log
import libv2ray.CoreCallbackHandler
import libv2ray.CoreController
import libv2ray.Libv2ray

/**
 * سرویس VPN. رابط TUN را خودِ اندروید (VpnService.Builder) می‌سازد و آدرس/مسیر/DNS
 * را همینجا تنظیم می‌کند؛ سپس فایل‌دیسکریپتور همان رابط به هسته‌ی Xray داده می‌شود
 * تا ترافیک را از آن بخواند/بنویسد. کانفیگ JSON از VlessConfig.buildXrayConfig می‌آید.
 *
 * امضای متدها با javap روی classes.jar واقعیِ libv2ray.aar تأیید شده:
 * startLoop(String, Int) — نه Long؛ CoreCallbackHandler هر سه متد Long برمی‌گرداند.
 */
class HermesVpnService : VpnService() {

    companion object {
        const val ACTION_CONNECT = "com.hermesvpn.app.CONNECT"
        const val ACTION_DISCONNECT = "com.hermesvpn.app.DISCONNECT"
        const val EXTRA_XRAY_CONFIG = "xray_config"
        private const val TAG = "HermesVpnService"
        private const val NOTIFICATION_CHANNEL_ID = "hermesvpn_service"
        private const val NOTIFICATION_ID = 1
        private const val SOCKS_PORT = 10808

        var isRunning: Boolean = false
            private set

        /** لحظه‌ی واقعیِ برقراری اتصال (epoch ms) — نه یه تایمر نمایشی جدا،
         * دقیقاً همون لحظه‌ای که startLoop موفق می‌شه. صفر یعنی وصل نیستیم. */
        var connectStartTimeMs: Long = 0L
            private set

        /** سرعت لحظه‌ای (بایت بر ثانیه). MainActivity موقع onResume ثبت می‌کنه،
         * موقع onPause پاک می‌کنه تا رفرنس اکتیویتی نشت نکنه. */
        var onSpeedUpdate: ((downBytesPerSec: Long, upBytesPerSec: Long) -> Unit)? = null
        var onPingUpdate: ((ms: Long?) -> Unit)? = null
        /** مسیر اضطراری وصل نشد (پیام خطای موتور) — برای Toast در صفحه‌ی اصلی. */
        var onTunnelError: ((msg: String) -> Unit)? = null

        // ۰.۰.۰.۰/۰ منهای بازه‌های خصوصی/رزروشده (RFC1918 + loopback + link-local +
        // multicast) — برای «دور زدن ترافیک LAN». محاسبه‌شده با ipaddress پایتون،
        // دستی حساب نشده تا خطای زیرشبکه نداشته باشه.
        private val PUBLIC_IPV4_ROUTES = listOf(
            "0.0.0.0" to 5, "8.0.0.0" to 7, "11.0.0.0" to 8, "12.0.0.0" to 6,
            "16.0.0.0" to 4, "32.0.0.0" to 3, "64.0.0.0" to 3, "96.0.0.0" to 4,
            "112.0.0.0" to 5, "120.0.0.0" to 6, "124.0.0.0" to 7, "126.0.0.0" to 8,
            "128.0.0.0" to 3, "160.0.0.0" to 5, "168.0.0.0" to 8, "169.0.0.0" to 9,
            "169.128.0.0" to 10, "169.192.0.0" to 11, "169.224.0.0" to 12, "169.240.0.0" to 13,
            "169.248.0.0" to 14, "169.252.0.0" to 15, "169.255.0.0" to 16, "170.0.0.0" to 7,
            "172.0.0.0" to 12, "172.32.0.0" to 11, "172.64.0.0" to 10, "172.128.0.0" to 9,
            "173.0.0.0" to 8, "174.0.0.0" to 7, "176.0.0.0" to 4, "192.0.0.0" to 9,
            "192.128.0.0" to 11, "192.160.0.0" to 13, "192.169.0.0" to 16, "192.170.0.0" to 15,
            "192.172.0.0" to 14, "192.176.0.0" to 12, "192.192.0.0" to 10, "193.0.0.0" to 8,
            "194.0.0.0" to 7, "196.0.0.0" to 6, "200.0.0.0" to 5, "208.0.0.0" to 4,
        )
    }

    private var tunInterface: ParcelFileDescriptor? = null
    private var coreController: CoreController? = null
    private lateinit var prefs: SharedPreferences
    private val statsHandler = Handler(Looper.getMainLooper())

    /** queryAllOutboundTrafficStats شمارنده‌ها رو موقع خوندن صفر می‌کنه، پس
     * عدد هر بار دقیقاً «مقدار از آخرین خوندن تا الان»ه — یعنی خودش سرعته،
     * نیازی به محاسبه‌ی دستیِ دلتا نیست. فرمت: tag,direction,value;... */
    private var lastStatsAt = 0L
    private val STAT_RE = Regex("(uplink|downlink)[^0-9]{0,6}?(\\d+)")
    private val statsRunnable = object : Runnable {
        override fun run() {
            coreController?.let {
                try {
                    val raw = it.queryAllOutboundTrafficStats()
                    var down = 0L
                    var up = 0L
                    if (raw.isNotBlank()) {
                        // هر قالبی که هسته بدهد (tag,uplink,123; یا JSON) — «جهت» و عدد بعدش
                        for (m in STAT_RE.findAll(raw)) {
                            val value = m.groupValues[2].toLongOrNull() ?: continue
                            if (m.groupValues[1] == "downlink") down += value else up += value
                        }
                    }
                    // شمارنده «از آخرین خواندن» است؛ اگر تیک دیرتر/زودتر از ۱ ثانیه رسید،
                    // به بایت‌برثانیه‌ی واقعی تبدیل کن تا نمودار پرش ساختگی نداشته باشد.
                    val nowMs = android.os.SystemClock.elapsedRealtime()
                    val el = if (lastStatsAt == 0L) 1000L else (nowMs - lastStatsAt).coerceIn(250L, 5000L)
                    lastStatsAt = nowMs
                    onSpeedUpdate?.invoke(down * 1000 / el, up * 1000 / el)
                } catch (e: Exception) {
                    Log.w(TAG, "queryAllOutboundTrafficStats ناموفق: ${e.message}")
                }
            }
            if (isRunning) statsHandler.postDelayed(this, 1000)
        }
    }

    private val pingHandler = Handler(Looper.getMainLooper())
    private val pingExecutor = java.util.concurrent.Executors.newSingleThreadExecutor()

    /** measureDelay یه فراخوانی شبکه‌ست (نه فقط خوندن شمارنده)، پس حتماً باید
     * روی ترد جدا اجرا بشه، نه روی همون Handler(mainLooper). هر ۵ ثانیه یه‌بار
     * کافیه — تندتر از این فایده‌ای نداره و فقط بار اضافه می‌کنه. */
    private val pingRunnable = object : Runnable {
        override fun run() {
            val controller = coreController
            if (controller != null) {
                pingExecutor.execute {
                    val delay = try {
                        controller.measureDelay("https://www.gstatic.com/generate_204")
                    } catch (e: Exception) {
                        -1L
                    }
                    onPingUpdate?.invoke(if (delay >= 0) delay else null)
                }
            }
            if (isRunning) pingHandler.postDelayed(this, 5000)
        }
    }

    // چک آپدیت در پس‌زمینه تا وقتی VPN روشن است (owner 2026-11-13: آپدیت بدون تأخیر برسد، حتی اگر
    // کاربر اپ را باز نکند). هر ۱۵ دقیقه؛ اگر شبکه جواب نداد ۲ دقیقه بعد دوباره.
    private val updateHandler = Handler(Looper.getMainLooper())
    private val updateRunnable = object : Runnable {
        override fun run() {
            if (!isRunning) return
            pingExecutor.execute {
                var ok = false
                try {
                    val info = UpdateChecker.fetch(this@HermesVpnService)
                    ok = UpdateChecker.lastFetchOk
                    if (ok) UpdateChecker.markChecked(this@HermesVpnService)
                    if (info != null && (info.mandatory || !UpdateChecker.isSkipped(this@HermesVpnService, info.versionCode))) {
                        UpdateUi.save(this@HermesVpnService, info)
                        val sp = getSharedPreferences("hermesvpn_prefs", MODE_PRIVATE)
                        if (sp.getInt("update_bg_notified_code", 0) != info.versionCode) {  // یک اعلان برای هر نسخه
                            UpdateChecker.notify(this@HermesVpnService, info)
                            sp.edit().putInt("update_bg_notified_code", info.versionCode).apply()
                        }
                    }
                } catch (e: Exception) {
                    Log.w(TAG, "update check: ${e.message}")
                }
                if (isRunning) updateHandler.postDelayed(this, if (ok) 15L * 60 * 1000 else 2L * 60 * 1000)
            }
        }
    }

    override fun onCreate() {
        super.onCreate()
        prefs = getSharedPreferences("hermesvpn_prefs", MODE_PRIVATE)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_DISCONNECT -> {
                stopVpn()
                return START_NOT_STICKY
            }
            ACTION_CONNECT -> {
                val configJson = intent.getStringExtra(EXTRA_XRAY_CONFIG)
                if (configJson.isNullOrEmpty()) {
                    Log.e(TAG, "کانفیگ خالی است، اتصال لغو شد")
                    stopSelf()
                    return START_NOT_STICKY
                }
                prefs.edit().putString("last_xray_config", configJson).apply()
                startForeground(NOTIFICATION_ID, buildNotification())
                startWithTunnel(configJson)
            }
            null -> {
                // سیستم به‌خاطر Always-on VPN دوباره سرویس رو استارت کرده، نه
                // خودِ کاربر — آخرین کانفیگ موفق رو از prefs برمی‌داریم.
                val lastConfig = prefs.getString("last_xray_config", null)
                if (!lastConfig.isNullOrEmpty()) {
                    startForeground(NOTIFICATION_ID, buildNotification())
                    startWithTunnel(lastConfig)
                } else {
                    stopSelf()
                    return START_NOT_STICKY
                }
            }
        }
        return START_STICKY
    }

    /** اگر کانفیگ یک مسیر اضطراری است، اول موتور (روی ترد جدا، چون تونل DNS
     * تا یک دقیقه طول می‌کشد) و بعد هسته. وگرنه مثل قبل مستقیم هسته. */
    private fun startWithTunnel(raw: String) {
        val obj = try { org.json.JSONObject(raw) } catch (e: Exception) { null }
        val tid = obj?.optString(VlessConfig.TUNNEL_KEY).orEmpty()
        if (obj == null || tid.isEmpty()) {
            Tunnels.stop()
            startVpn(raw)
            return
        }
        obj.remove(VlessConfig.TUNNEL_KEY)
        val core = obj.toString()
        Thread {
            val ok = Tunnels.start(applicationContext, tid)
            android.os.Handler(mainLooper).post {
                if (ok) startVpn(core) else {
                    Log.e(TAG, "مسیر اضطراری وصل نشد: ${Tunnels.lastError}")
                    prefs.edit().putString("last_tunnel_error", Tunnels.lastError ?: "").apply()
                    onTunnelError?.invoke(Tunnels.lastError ?: "")
                    stopVpn()
                }
            }
        }.start()
    }

    private fun startVpn(configJson: String) {
        try {
            val fd = establishTun(configJson) ?: run {
                Log.e(TAG, "ساخت رابط TUN شکست خورد")
                stopSelf()
                return
            }
            tunInterface = fd

            // بدون این، هسته فایل‌های geoip.dat/geosite.dat را پیدا نمی‌کند و
            // چون قانون‌های مسیریابی ایران به آن‌ها ارجاع می‌دهند، اصلاً بالا
            // نمی‌آید — یعنی اتصال کلاً شکست می‌خورد، نه اینکه فقط مسیریابی
            // ناقص شود. دارایی‌های APK فایل واقعی روی دیسک نیستند، پس باید
            // یک‌بار بیرون کشیده شوند.
            RouteRules.load(this)
            VlessConfig.prepareGeoAssets(this)
            Libv2ray.initCoreEnv(filesDir.absolutePath, "")
            val controller = Libv2ray.newCoreController(object : CoreCallbackHandler {
                override fun startup(): Long {
                    Log.i(TAG, "هسته‌ی Xray استارت شد")
                    return 0
                }

                override fun shutdown(): Long {
                    Log.i(TAG, "هسته‌ی Xray متوقف شد")
                    return 0
                }

                override fun onEmitStatus(code: Long, message: String?): Long {
                    Log.i(TAG, "وضعیت: $code -> $message")
                    return 0
                }
            })
            coreController = controller
            controller.startLoop(configJson, fd.fd)
            isRunning = true
            connectStartTimeMs = System.currentTimeMillis()
            lastStatsAt = 0L
            statsHandler.post(statsRunnable)
            pingHandler.postDelayed(pingRunnable, 1500)
            updateHandler.removeCallbacks(updateRunnable)
            updateHandler.postDelayed(updateRunnable, 20_000)
        } catch (e: Exception) {
            Log.e(TAG, "اتصال شکست خورد: ${e.message}", e)
            stopVpn()
        }
    }

    /** بسته‌های اپ‌های ایرانیِ نصب‌شده روی همین گوشی.
     *
     * تشخیص بر اساس نام بسته است: اکثر قاطع اپ‌های ایرانی با «ir.» شروع
     * می‌شوند. چند خانواده‌ی پرکاربرد که این‌طور نیستند (عمدتاً بانکی و
     * پرداخت) هم دستی اضافه شده‌اند. عمداً محافظه‌کارانه است: اپی که
     * اشتباهاً استثنا شود فقط از VPN بی‌بهره می‌ماند، ولی اپی که اشتباهاً
     * داخل تونل بماند برای مشتری خراب است. */
    private fun iranianAppPackages(): Set<String> {
        val prefixes = listOf(
            "ir.",
            "com.pmb", "mobile.banking", "com.isc.", "com.bpm.", "com.sadad",
            "com.behpardakht", "com.parsian", "com.tosan", "com.persianswitch",
            "com.mydigipay", "net.igap",
        )
        return try {
            val launcher = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
            val keep = telegramClientPackages()
            packageManager.queryIntentActivities(launcher, 0)
                .mapNotNull { it.activityInfo?.packageName }
                .filter { pkg -> prefixes.any { pkg.startsWith(it, ignoreCase = true) } }
                .filterNot { it in keep }
                .toSet()
        } catch (e: Exception) {
            Log.w(TAG, "شناسایی اپ‌های ایرانی ناموفق بود: ${e.message}")
            emptySet()
        }
    }

    /** کلاینت‌های تلگرام — هر فورکی، با هر نام بسته‌ای.
     *
     * ★ این تابع یک باگ واقعی را می‌بندد. فورک‌های ایرانی تلگرام (طلایی،
     * هاتگرام، موبوگرام، تلگرام فارسی) نام بسته‌شان با «ir.» شروع می‌شود،
     * پس قانونِ «هر چه ir. است از تونل بیرون بماند» آن‌ها را هم بیرون
     * می‌انداخت — یعنی دقیقاً اپ‌هایی که بیشترین نیاز را به تونل دارند،
     * چون به سرورهای تلگرام وصل می‌شوند که فیلترند. مشتری می‌دید تلگرامِ
     * اصلی کار می‌کند ولی تلگرامِ دوم انگار اصلاً VPN نمی‌بیند؛ و درست هم
     * می‌دید — خودمان بیرونش گذاشته بودیم.
     *
     * تشخیص از روی رفتار است نه فهرستِ اسم: هر اپی که اسکیم tg:// را باز
     * می‌کند یک کلاینت تلگرام است. فهرستِ اسم با هر فورک تازه کهنه می‌شود.
     * اجازه‌ی دیدنش در AndroidManifest زیر <queries> از قبل هست.
     */
    private fun telegramClientPackages(): Set<String> {
        return try {
            val tg = Intent(Intent.ACTION_VIEW, Uri.parse("tg://resolve?domain=telegram"))
            packageManager.queryIntentActivities(tg, 0)
                .mapNotNull { it.activityInfo?.packageName }
                .toSet()
        } catch (e: Exception) {
            Log.w(TAG, "شناسایی کلاینت‌های تلگرام ناموفق بود: ${e.message}")
            emptySet()
        }
    }

    /** آی‌پی‌های سرورِ خودِ کانفیگ — فقط IPv4 لفظی، نه دامنه. */
    private fun serverIpv4s(configJson: String): List<Long> {
        return try {
            val outs = org.json.JSONObject(configJson).optJSONArray("outbounds") ?: return emptyList()
            val found = mutableListOf<Long>()
            for (i in 0 until outs.length()) {
                val vnext = outs.optJSONObject(i)?.optJSONObject("settings")
                    ?.optJSONArray("vnext") ?: continue
                for (j in 0 until vnext.length()) {
                    ipv4ToLong(vnext.optJSONObject(j)?.optString("address") ?: "")?.let { found += it }
                }
            }
            found.distinct()
        } catch (e: Exception) {
            Log.w(TAG, "استخراج آی‌پی سرور ناموفق: ${e.message}")
            emptyList()
        }
    }

    private fun ipv4ToLong(ip: String): Long? {
        val parts = ip.split(".")
        if (parts.size != 4) return null
        var v = 0L
        for (p in parts) {
            val n = p.toIntOrNull() ?: return null
            if (n !in 0..255) return null
            v = (v shl 8) or n.toLong()
        }
        return v
    }

    private fun longToIpv4(v: Long): String =
        "${(v shr 24) and 0xFF}.${(v shr 16) and 0xFF}.${(v shr 8) and 0xFF}.${v and 0xFF}"

    /** کوچک‌ترین مجموعه‌ی CIDR که بازه‌ی [start, end] را می‌پوشاند. */
    private fun cidrsForRange(start: Long, end: Long): List<Pair<String, Int>> {
        val out = mutableListOf<Pair<String, Int>>()
        var cur = start
        while (cur <= end) {
            var prefix = 32
            while (prefix > 0) {
                val blockSize = 1L shl (32 - (prefix - 1))
                if (cur % blockSize != 0L) break
                if (cur + blockSize - 1 > end) break
                prefix -= 1
            }
            out.add(longToIpv4(cur) to prefix)
            cur += 1L shl (32 - prefix)
        }
        return out
    }

    /** یک مجموعه‌ی دلخواه از مسیرها، منهای این آی‌پی‌ها. برای حالت «دور
     * زدن LAN» لازم است: آنجا مسیرها از قبل محدودند و باز هم باید آی‌پی
     * سرور از داخلشان بیرون کشیده شود. */
    private fun routesMinus(base: List<Pair<String, Int>>, excluded: List<Long>): List<Pair<String, Int>> {
        val out = mutableListOf<Pair<String, Int>>()
        for ((addr, prefix) in base) {
            val start = ipv4ToLong(addr) ?: continue
            val end = start + (1L shl (32 - prefix)) - 1
            var cur = start
            for (ip in excluded.sorted()) {
                if (ip < cur || ip > end) continue
                if (ip > cur) out += cidrsForRange(cur, ip - 1)
                cur = ip + 1
            }
            if (cur <= end) out += cidrsForRange(cur, end)
        }
        return out
    }

    /** کل فضای IPv4 منهای این آی‌پی‌ها. */
    private fun ipv4RoutesExcluding(excluded: List<Long>): List<Pair<String, Int>> {
        val out = mutableListOf<Pair<String, Int>>()
        var cur = 0L
        for (ip in excluded.sorted()) {
            if (ip > cur) out += cidrsForRange(cur, ip - 1)
            cur = ip + 1
        }
        if (cur <= 0xFFFFFFFFL) out += cidrsForRange(cur, 0xFFFFFFFFL)
        return out
    }

    private fun establishTun(configJson: String): ParcelFileDescriptor? {
        val builder = Builder()
            .setSession("HermesVPN")
            .setMtu(1500)
            .addAddress("10.10.14.1", 30)
            .addAddress("fd00:1:fd00:1:fd00:1:fd00:1", 126)

        // ★ چرا این‌همه پیچیدگی: هسته‌ی ما protect() روی سوکت ندارد، پس تنها
        // راهِ جلوگیری از حلقه این بود که خودِ اپ را با addDisallowedApplication
        // از تونل بیرون بگذاریم. ولی به‌محض اینکه *هر* لیست per-app وجود
        // داشته باشد، اندروید پوشش VPN را UID-محور می‌کند و اپ‌های کلونِ
        // سامسونگ (Dual Messenger، کاربر ۹۵) بیرون می‌مانند — مشتری می‌دید
        // تلگرام اصلی وصل می‌شود ولی نسخه‌ی دوبلیکت انگار اصلاً VPN ندارد.
        //
        // راه‌حل: به‌جای بیرون گذاشتنِ اپ، *آی‌پی سرور* را از مسیرها بیرون
        // می‌گذاریم. ترافیک هسته به سرور از رابط فیزیکی می‌رود (حلقه‌ای
        // نیست) و لیست per-app کاملاً خالی می‌ماند، پس کلون هم پوشش
        // می‌گیرد. فقط وقتی امن است که آدرس سرور یک IPv4 لفظی باشد؛ برای
        // دامنه یا حالت bypass_lan به رفتار قبلی برمی‌گردیم.
        val mode = prefs.getString("tunnel_mode", "exclude") ?: "exclude"
        val selectedApps = prefs.getStringSet(
            if (mode == "include") "included_apps" else "excluded_apps", emptySet()
        ) ?: emptySet()
        val extraExcludes =
            if (mode == "include") emptySet()
            else selectedApps + (if (prefs.getBoolean("bypass_ir_apps", false)) iranianAppPackages() else emptySet())
        // هم آی‌پیِ داخل کانفیگ، هم همه‌ی آی‌پی‌هایی که برای دامنه حل شدند.
        // منبع دوم لازم است چون هسته ممکن است نام را دوباره حل کند.
        val serverIps = (serverIpv4s(configJson) +
            VlessConfig.lastServerIps.mapNotNull { ipv4ToLong(it) } +
            (if (Tunnels.running) Tunnels.bypassIps.mapNotNull { ipv4ToLong(it) } else emptyList())).distinct()
        // ★ حالا پیش‌فرض روشن است، چون روی گوشی واقعی ثابت شد.
        //
        // آزمایش قطعی: در همان تلگرامی که وصل نمی‌شد، پروکسی SOCKS5 روی
        // 127.0.0.1:10808 (همان inbound خودِ اپ) تنظیم شد و بلافاصله وصل
        // شد. یعنی تونل برای آن اپ کاملاً سالم بود و تنها مشکل این بود که
        // بسته‌هایش اصلاً وارد TUN نمی‌شدند — دقیقاً چیزی که لیست per-app
        // با پروفایل‌های کلون (Dual Messenger سامسونگ) می‌کند.
        // بدون تنظیم. تنها چیزی که جلویش را می‌گیرد، انتخابِ صریحِ خودِ
        // کاربر است (حالت include، یا استثنا کردن دستی اپ‌ها، یا روشن کردن
        // «اپ‌های ایرانی بیرون از تونل») — چون آن‌ها ذاتاً لیست per-app
        // می‌سازند و این محدودیت اندروید است، نه چیزی که بشود دورش زد.
        val routeBasedLoopGuard =
            mode != "include" && extraExcludes.isEmpty() && serverIps.isNotEmpty()

        if (routeBasedLoopGuard) {
            Log.i(TAG, "حلقه با مسیر مهار شد (${serverIps.size} آی‌پی سرور بیرون از تونل) — بدون لیست per-app")
            val base = if (prefs.getBoolean("bypass_lan", false))
                routesMinus(PUBLIC_IPV4_ROUTES, serverIps) else ipv4RoutesExcluding(serverIps)
            for ((addr, prefix) in base) {
                builder.addRoute(addr, prefix)
            }
        } else if (prefs.getBoolean("bypass_lan", false)) {
            // به‌جای همه‌چیز (۰.۰.۰.۰/۰)، فقط بازه‌های عمومی — دستگاه‌های شبکه‌ی
            // محلی (پرینتر، روتر، تلویزیون هوشمند) بیرون از تونل قابل‌دسترسی می‌مونن.
            for ((addr, prefix) in PUBLIC_IPV4_ROUTES) {
                builder.addRoute(addr, prefix)
            }
        } else {
            builder.addRoute("0.0.0.0", 0)
        }
        builder.addRoute("::", 0)

        val dnsServer = prefs.getString("dns_server", null)?.trim().takeUnless { it.isNullOrEmpty() } ?: "1.1.1.1"
        builder.addDnsServer(dnsServer)
        builder.addDnsServer("8.8.8.8")

        // Builder.setMetered فقط از API 29 به بعد وجود داره
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.Q &&
            prefs.getBoolean("unmetered_hint", false)) {
            builder.setMetered(false)
        }

        // جداسازی تونلینگ: یا فقط اپ‌های انتخابی تونل بشن (mode=include) یا
        // همه به‌جز اپ‌های انتخابی (mode=exclude، حالت پیش‌فرض). این دو تا
        // روی VpnService.Builder متقابلاً منحصر به فردن — هر دو رو صدا نزن.
        if (mode == "include" && selectedApps.isNotEmpty()) {
            for (pkg in selectedApps) {
                try { builder.addAllowedApplication(pkg) } catch (e: Exception) {
                    Log.w(TAG, "addAllowedApplication ناموفق برای $pkg: ${e.message}")
                }
            }
        } else if (!routeBasedLoopGuard) {
            // مسیر قدیمی: مهار حلقه با بیرون گذاشتنِ خودِ اپ. فقط وقتی به
            // اینجا می‌رسیم که راه مسیرمحور ممکن نبوده — و بهایش این است که
            // اپ‌های کلونِ سامسونگ پوشش نمی‌گیرند.
            try {
                builder.addDisallowedApplication(packageName)
            } catch (e: Exception) {
                Log.w(TAG, "addDisallowedApplication ناموفق: ${e.message}")
            }
            val toExclude = selectedApps.toMutableSet()

            // اپ‌های ایرانی (بانک‌ها، دولت من، درگاه‌های پرداخت) معمولاً *وجودِ*
            // VPN روی دستگاه را تشخیص می‌دهند، نه IP را. برای همین هیچ قانون
            // مسیریابی‌ای حلشان نمی‌کند — تنها راه این است که کلاً از تونل
            // بیرون بمانند تا اصلاً VPN نبینند.
            // ★ پیش‌فرض عمداً خاموش شد. این قابلیت در نسخه‌ی ۰.۵.۲ اضافه شد و
            // همان نسخه‌ای بود که مشتری گفت «بعدش دیگه کار نکرد». تا قبل از
            // آن فقط خودِ اپ استثنا می‌شد و اپ‌های کلونِ سامسونگ (Dual
            // Messenger) درست کار می‌کردند؛ با اضافه شدن استثنای انبوهِ
            // ir.* پوشش VPN روی پروفایل کلون از بین رفت. بیرون ماندن
            // اپ‌های بانکی به قیمت از کار افتادن تلگرام نمی‌ارزد، پس هر کس
            // لازم دارد خودش از تنظیمات تونل روشنش می‌کند.
            if (prefs.getBoolean("bypass_ir_apps", false)) {
                toExclude += iranianAppPackages()
            }

            for (pkg in toExclude) {
                try { builder.addDisallowedApplication(pkg) } catch (e: Exception) {
                    Log.w(TAG, "addDisallowedApplication ناموفق برای $pkg: ${e.message}")
                }
            }
        }

        // ★ خلاصه‌ی آنچه واقعاً اتفاق افتاد را ذخیره کن تا در تنظیمات تونل
        // دیده شود. بدون این، تشخیص اینکه کدام مسیر انتخاب شده فقط حدس بود
        // و هر بار باید یک نسخه‌ی تازه می‌ساختیم تا بفهمیم چه شد.
        val summary = buildString {
            append(if (routeBasedLoopGuard) "مهار حلقه: مسیر (لیست per-app خالی)" else "مهار حلقه: استثنای اپ")
            append("\nحالت: ").append(mode)
            append("\nاپ‌های استثناشده: ")
            append(if (routeBasedLoopGuard) 0 else (extraExcludes.size + 1))
            append("\nآی‌پی سرور بیرون از تونل: ")
            append(if (serverIps.isEmpty()) "—" else serverIps.joinToString { longToIpv4(it) })
            append("\nاپ ایرانی بیرون: ").append(prefs.getBoolean("bypass_ir_apps", false))
            append(" | LAN بیرون: ").append(prefs.getBoolean("bypass_lan", false))
            append("\nانتخاب دستی: ").append(selectedApps.size).append(" اپ")
        }
        prefs.edit().putString("last_tun_summary", summary).apply()
        Log.i(TAG, "خلاصه‌ی تونل:\n" + summary)

        return builder.establish()
    }

    private fun stopVpn() {
        try {
            coreController?.stopLoop()
        } catch (e: Exception) {
            Log.w(TAG, "خطا در توقف هسته: ${e.message}")
        }
        Tunnels.stop()
        coreController = null
        tunInterface?.let {
            try { it.close() } catch (_: Exception) {}
        }
        tunInterface = null
        isRunning = false
        connectStartTimeMs = 0L
        statsHandler.removeCallbacks(statsRunnable)
        pingHandler.removeCallbacks(pingRunnable)
        updateHandler.removeCallbacks(updateRunnable)
        onSpeedUpdate?.invoke(0, 0)
        onPingUpdate?.invoke(null)
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        stopVpn()
        super.onDestroy()
    }

    override fun onRevoke() {
        // کاربر از تنظیمات اندروید دسترسی VPN را لغو کرده
        stopVpn()
        super.onRevoke()
    }

    private fun buildNotification(): Notification {
        val nm = getSystemService(NotificationManager::class.java)
        if (nm.getNotificationChannel(NOTIFICATION_CHANNEL_ID) == null) {
            nm.createNotificationChannel(
                NotificationChannel(
                    NOTIFICATION_CHANNEL_ID, "HermesVPN",
                    NotificationManager.IMPORTANCE_LOW
                )
            )
        }
        val openIntent = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE
        )
        return Notification.Builder(this, NOTIFICATION_CHANNEL_ID)
            .setContentTitle("HermesVPN")
            .setContentText(getString(R.string.vpn_notif_connected))
            .setSmallIcon(android.R.drawable.ic_lock_lock)
            .setContentIntent(openIntent)
            .setOngoing(true)
            .build()
    }
}
