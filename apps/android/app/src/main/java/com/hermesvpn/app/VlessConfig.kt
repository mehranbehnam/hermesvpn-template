package com.hermesvpn.app

import org.json.JSONArray
import org.json.JSONObject
import java.net.URI
import java.net.URLDecoder

/**
 * تبدیل لینک vless:// (دقیقاً همان دو فرمتی که hermes-customer-bot.py می‌سازد —
 * Main پشت WS+TLS، یا Reality مستقیم روی TCP) به کانفیگ کامل JSON که Xray-core
 * می‌فهمد. عمداً برای همین دو فرمت مشخص نوشته شده، نه یک پارسر عمومی VLESS —
 * چون تنها منبع واقعی این لینک‌ها همان سرور خودمان است.
 */
object VlessConfig {

    class ParseException(message: String) : Exception(message)

    /** DNSهای داخل ایران — فقط برای حل دامنه‌های ایرانی استفاده می‌شوند. */
    private const val IR_DNS_PRIMARY = "178.22.122.100"
    private const val IR_DNS_SECONDARY = "185.51.200.2"

    /** وقتی true باشد یعنی geoip.dat و geosite.dat کنار هسته هستند و می‌شود
     * به قانون‌های ایران ارجاع داد. */
    @Volatile
    var geoAssetsReady = false
        private set

    /** mux پیش‌فرض خاموش است.
     *
     * ★ mux در نسخه‌ی ۰.۵.۲ روشن شد و دقیقاً همان نسخه‌ای بود که مشتری گفت
     * «بعدش دیگه کار نکرد». تلگرام ده‌ها اتصال طولانی‌مدت باز می‌کند و با
     * mux همه‌شان روی یک نشست مشترک می‌افتند؛ وقتی دو نمونه‌ی تلگرام
     * (یا دو اکانت) هم‌زمان وصل می‌شوند، اولی جا می‌گیرد و دومی برای همیشه
     * روی Connecting می‌ماند. v2rayNG هم به همین دلیل mux را پیش‌فرض خاموش
     * دارد. سرعتِ بیشتر به قیمتِ وصل نشدن تلگرام نمی‌ارزد.
     */
    @Volatile
    var muxEnabled = false

    /** آی‌پی‌هایی که آخرین‌بار برای سرور حل شدند.
     *
     * سرویس همه‌ی این‌ها را از مسیرهای تونل بیرون می‌گذارد. فقط آی‌پیِ
     * قفل‌شده کافی نیست: اگر هسته به هر دلیلی دوباره نام را حل کند و
     * آی‌پی دیگری از همان مجموعه بگیرد، آن یکی هم نباید داخل تونل بیفتد.
     */
    @Volatile
    var lastServerIps: List<String> = emptyList()

    /** داده‌های مسیریابی را از دارایی‌های APK به filesDir می‌آورد (دارایی‌های
     * APK فایل واقعی روی دیسک نیستند و هسته نمی‌تواند بخواندشان). فقط وقتی
     * کپی می‌کند که نباشند یا اندازه‌شان فرق کند. */
    fun prepareGeoAssets(ctx: android.content.Context) {
        var ok = true
        for (name in arrayOf("geoip.dat", "geosite.dat")) {
            try {
                val target = java.io.File(ctx.filesDir, name)
                val expected = ctx.assets.openFd(name).use { it.length }
                if (target.exists() && target.length() == expected) continue
                ctx.assets.open(name).use { input ->
                    target.outputStream().use { output -> input.copyTo(output) }
                }
            } catch (e: Exception) {
                ok = false
            }
        }
        geoAssetsReady = ok
    }

    fun buildXrayConfig(vlessLink: String, socksPort: Int): String {
        tunnelMode = false
        val uri = try {
            URI(vlessLink)
        } catch (e: Exception) {
            throw ParseException("لینک نامعتبر است: ${e.message}")
        }
        if (uri.scheme != "vless") {
            throw ParseException("این یک لینک vless:// نیست")
        }

        val uuid = uri.userInfo ?: throw ParseException("UUID در لینک پیدا نشد")
        val host = uri.host ?: throw ParseException("آدرس سرور در لینک پیدا نشد")
        val port = if (uri.port > 0) uri.port else throw ParseException("پورت در لینک پیدا نشد")

        val params = parseQuery(uri.rawQuery ?: "")
        val security = params["security"] ?: "none"

        val outbound = JSONObject().apply {
            put("protocol", "vless")
            put("tag", "proxy")
            put("settings", JSONObject().apply {
                put("vnext", JSONArray().put(JSONObject().apply {
                    put("address", host)
                    put("port", port)
                    put("users", JSONArray().put(JSONObject().apply {
                        put("id", uuid)
                        put("encryption", params["encryption"] ?: "none")
                        if (security == "reality") {
                            put("flow", params["flow"] ?: "xtls-rprx-vision")
                        }
                    }))
                }))
            })
            put("streamSettings", buildStreamSettings(security, params))
        }

        return wrapOutbound(outbound, socksPort)
    }

    /** برای کانفیگ‌هایی که کاربر مستقیم یک outbound خامِ v2ray/xray پیست
     * کرده (نه لینک اشتراک‌گذاری) — همون outbound رو عیناً با tag=proxy
     * توی همون قالب inbound/routing استاندارد می‌ذاره. */
    fun buildXrayConfigFromOutbound(outboundJson: String, socksPort: Int): String {
        val outbound = try {
            JSONObject(outboundJson)
        } catch (e: Exception) {
            throw ParseException("JSON نامعتبر است: ${e.message}")
        }
        outbound.put("tag", "proxy")
        // مسیر اضطراری: خروجی = SOCKS محلیِ موتور. نشانه‌ی داخلی را پاک کن
        // (هسته نباید ببیند) و آی‌پی‌های سرورِ قبلی را دور بریز تا مسیرهای
        // کهنه بیرون از تونل نمانند؛ آی‌پی‌های واقعی را موتور می‌دهد.
        tunnelMode = outbound.has(Tunnels.MARK)
        val tunnelId = outbound.optString(Tunnels.MARK)
        if (tunnelMode) {
            outbound.remove(Tunnels.MARK)
            lastServerIps = emptyList()
        }
        val built = wrapOutbound(outbound, socksPort)
        // شناسه‌ی مسیر در خودِ کانفیگ ذخیره می‌شود (سرویس قبل از دادن به هسته
        // برش می‌دارد) تا Always-on VPN هم بعد از ری‌استارت بداند کدام موتور.
        return if (tunnelMode) JSONObject(built).put(TUNNEL_KEY, tunnelId).toString() else built
    }

    /** true وقتی کانفیگ فعلی یک مسیر اضطراری است (SOCKS محلی، بدون UDP). */
    @Volatile var tunnelMode = false
    const val TUNNEL_KEY = "hermesTunnel"

    /**
     * چند جریان را روی یک اتصالِ تونل سوار می‌کند.
     *
     * چرا: هر اتصال تازه از ایران تا سرور یک دست‌دادن TLS کامل می‌خواهد که
     * روی این مسیر ۰٫۵ تا ۰٫۸ ثانیه است. یک صفحه‌ی وب ده‌ها اتصال باز
     * می‌کند، پس همان هزینه ده‌ها بار تکرار می‌شد. با mux فقط اتصال اول
     * این هزینه را می‌دهد.
     *
     * اندازه‌گیری واقعی از تهران روی همین سرور:
     *   باز شدن صفحه: ۱٫۰۲ → ۰٫۵۷ ثانیه   |   دانلود: ۹ → ۲۰٫۶ مگابایت/ثانیه
     *
     * ★ با flow=xtls-rprx-vision (یعنی Reality) عمداً فعال نمی‌شود: خود
     * xray این ترکیب را نمی‌پذیرد و اتصال اصلاً برقرار نمی‌شود.
     */
    private fun applyMux(outbound: JSONObject) {
        if (outbound.optString("protocol") !in setOf("vless", "vmess", "trojan")) return
        val flow = outbound.optJSONObject("settings")
            ?.optJSONArray("vnext")?.optJSONObject(0)
            ?.optJSONArray("users")?.optJSONObject(0)
            ?.optString("flow") ?: ""
        if (!muxEnabled) return
        if (flow.contains("vision", ignoreCase = true)) return
        if (outbound.has("mux")) return   // کانفیگ دستیِ کاربر را بازنویسی نکن
        outbound.put("mux", JSONObject().apply {
            put("enabled", true)
            put("concurrency", 8)
            put("xudpConcurrency", 8)
        })
    }

    /** دامنه‌ی سرور را به یک IPv4 تبدیل می‌کند. در هر خطایی دست نمی‌زند —
     * یک کانفیگ کمی کندتر بهتر از کانفیگی است که اصلاً وصل نمی‌شود. */
    /** آدرس سرور را به یک IPv4 قفل می‌کند و همه‌ی آی‌پی‌های حل‌شده را
     * برای سرویس نگه می‌دارد.
     *
     * بدون تنظیم و بدون استثنا اجرا می‌شود: تنها راهِ جلوگیری از حلقه بدون
     * ساختن لیست per-app همین است، و لیست per-app اپ‌های کلون را از پوشش
     * VPN بیرون می‌اندازد. مشتری نباید برای درست کار کردن تلگرام تیکی
     * بزند.
     */
    private fun pinAddress(outbound: JSONObject) {
        try {
            val vnext = outbound.optJSONObject("settings")?.optJSONArray("vnext")
                ?.optJSONObject(0) ?: return
            val addr = vnext.optString("address", "")
            if (addr.isEmpty() || Regex("^[0-9.]+$").matches(addr)) return
            // ★ حتماً روی ترد دیگر. ساخت کانفیگ از یک coroutine روی ترد
            // اصلی صدا زده می‌شود و اندروید آنجا DNS را ممنوع کرده
            // (NetworkOnMainThreadException). قبلاً همین استثنا بی‌صدا در
            // catch می‌افتاد و نتیجه‌اش این بود که آدرس هیچ‌وقت قفل نمی‌شد و
            // «مهار حلقه با مسیر» هرگز فعال نمی‌شد — بدون هیچ نشانه‌ای.
            val holder = arrayOfNulls<String>(1)
            val all = mutableListOf<String>()
            val t = Thread {
                try {
                    val v4 = java.net.InetAddress.getAllByName(addr)
                        .filterIsInstance<java.net.Inet4Address>()
                        .mapNotNull { it.hostAddress }
                    all.addAll(v4)
                    holder[0] = v4.firstOrNull()
                } catch (e: Exception) {
                    holder[0] = null
                }
            }
            t.start()
            t.join(5000)
            val ip = holder[0] ?: return
            lastServerIps = all.toList()
            // host و sni در streamSettings‌اند و دست نمی‌خورند، پس TLS سالم می‌ماند.
            vnext.put("address", ip)
            android.util.Log.i("VlessConfig", "آدرس سرور به $ip قفل شد (دامنه: $addr)")
        } catch (e: Exception) {
            android.util.Log.w("VlessConfig", "قفل کردن آدرس ناموفق: ${e.message}")
        }
    }

    private fun wrapOutbound(outbound: JSONObject, socksPort: Int): String {
        pinAddress(outbound)
        applyMux(outbound)
        val inboundTun = JSONObject().apply {
            put("protocol", "tun")
            put("settings", JSONObject().apply {
                put("name", "tun0")
                put("mtu", 1500)
            })
            put("sniffing", JSONObject().apply {
                put("enabled", true)
                put("destOverride", JSONArray().put("http").put("tls"))
            })
        }

        // پراکسی SOCKS محلی هم برای تست/دیباگ از بیرون اپ مفید است (پورت روی 127.0.0.1)
        val inboundSocks = JSONObject().apply {
            put("tag", "socks-in")
            put("protocol", "socks")
            put("listen", "127.0.0.1")
            put("port", socksPort)
            put("settings", JSONObject().apply { put("udp", true) })
            put("sniffing", JSONObject().apply {
                put("enabled", true)
                put("destOverride", JSONArray().put("http").put("tls"))
            })
        }

        // ─────────── تونلِ تقسیم‌شده‌ی خودکار ───────────
        // هدف: هیچ‌وقت مجبور نباشیم سایت‌ها را تک‌تک به فهرست اضافه کنیم.
        //
        // کلید کار «IPIfNonMatch» است: اگر هیچ قانونی بر اساس *دامنه* نخورد،
        // Xray خودش دامنه را به IP تبدیل می‌کند و دوباره قانون‌ها را می‌سنجد.
        // پس هر سایتی که روی IP ایران میزبانی شود خودکار مستقیم می‌رود، حتی
        // اگر اسمش در هیچ فهرستی نباشد.
        //
        // ⚠️ دام مهم: نباید هیچ قانون فراگیری (مثل network=tcp,udp → proxy) در
        // انتهای فهرست باشد. آن قانون در همان مرحله‌ی تطبیقِ دامنه می‌گیرد و
        // نوبت هرگز به تبدیل دامنه به IP نمی‌رسد — یعنی کل مکانیزم خاموش
        // می‌شود. به‌جایش، چون proxy اولین خروجیِ فهرست است، خودش پیش‌فرض
        // است. این با تست واقعی سنجیده شد: با قانون فراگیر، basalam.com و
        // sahamyab.com (که در فهرست نیستند ولی IP ایران دارند) به پراکسی
        // می‌رفتند؛ بدون آن، مستقیم می‌روند.
        val irDns = JSONArray().put(IR_DNS_PRIMARY).put(IR_DNS_SECONDARY)

        val rules = JSONArray()

        // درخواست‌های DNS به موتور DNS خودِ Xray سپرده می‌شوند، نه اینکه خام رد شوند
        // ★ هر مقصد IPv6 فوراً رد می‌شود.
        //
        // سرور ما اصلاً IPv6 ندارد (نه آدرس سراسری، نه خروجی) ولی تونل به
        // گوشی یک آدرس IPv6 می‌دهد و ::/0 را هم داخل تونل می‌فرستد. نتیجه:
        // اندروید فکر می‌کند IPv6 دارد، تلگرام سراغ دیتاسنترهای IPv6 خودش
        // می‌رود، بسته‌ها وارد تونل می‌شوند و همان‌جا در چاه می‌افتند — و
        // تلگرام تا ابد روی «Connecting» می‌ماند. بعضی اکانت‌ها کار می‌کردند
        // و بعضی نه، چون هر اکانت روی دیتاسنتر دیگری است و آدرس v4 بعضی
        // از قبل کش شده بود.
        //
        // رد کردنِ فوری بهتر از در چاه انداختن است: کلاینت سریع شکست را
        // می‌فهمد و خودش به IPv4 برمی‌گردد. مسیر ::/0 را هم نگه می‌داریم تا
        // ترافیک v6 از کنار تونل بیرون نزند.
        rules.put(JSONObject().apply {
            put("type", "field")
            put("ip", JSONArray().put("::/0"))
            put("outboundTag", "block")
        })
        rules.put(JSONObject().apply {
            put("type", "field"); put("port", 53); put("outboundTag", "dns-out")
        })
        // ⚠️ پیش از قانون شبکه‌ی محلی: وقتی DNS داخل ایران مسموم می‌شود، دامنه‌ی
        // فیلترشده به 10.10.34.34 می‌رسد که *داخل بازه‌ی خصوصی* است. بدون این
        // قانون، همان دامنه به‌جای تونل مستقیم می‌رفت و کاربر صفحه‌ی فیلترینگ
        // می‌دید. این آدرس‌ها هیچ‌وقت شبکه‌ی محلی واقعی نیستند.
        rules.put(JSONObject().apply {
            put("type", "field")
            put("ip", JSONArray().put("10.10.34.0/24"))
            put("outboundTag", "proxy")
        })

        // شبکه‌ی محلی هرگز از تونل رد نمی‌شود
        rules.put(JSONObject().apply {
            put("type", "field")
            put("ip", JSONArray().put("geoip:private"))
            put("outboundTag", "direct")
        })
        // خودِ DNSهای ایرانی هم باید مستقیم صدا زده شوند
        rules.put(JSONObject().apply {
            put("type", "field"); put("ip", irDns); put("outboundTag", "direct")
        })
        // فهرستِ به‌روزشونده از سرور: سایت‌هایی که نه با IP و نه با geosite
        // تشخیص داده نمی‌شوند. چون بالاتر از قانون‌های ایران می‌آید، می‌تواند
        // استثنا هم بسازد.
        if (RouteRules.proxyDomains.isNotEmpty()) {
            rules.put(JSONObject().apply {
                put("type", "field")
                put("domain", JSONArray().also { arr ->
                    RouteRules.proxyDomains.forEach { arr.put("domain:" + it) }
                })
                put("outboundTag", "proxy")
            })
        }
        if (RouteRules.directDomains.isNotEmpty()) {
            rules.put(JSONObject().apply {
                put("type", "field")
                put("domain", JSONArray().also { arr ->
                    RouteRules.directDomains.forEach { arr.put("domain:" + it) }
                })
                put("outboundTag", "direct")
            })
        }

        // فهرست آماده‌ی سایت‌های ایرانی + هر دامنه‌ی ir.
        rules.put(JSONObject().apply {
            put("type", "field")
            put("domain", JSONArray().put("geosite:category-ir").put("domain:ir"))
            put("outboundTag", "direct")
        })
        // و مهم‌تر از همه: هر مقصدی که IP‌اش ایران باشد
        rules.put(JSONObject().apply {
            put("type", "field")
            put("ip", JSONArray().put("geoip:ir"))
            put("outboundTag", "direct")
        })

        // اگر داده‌ی مسیریابی در دسترس نیست، به رفتار ساده‌ی قبلی برمی‌گردیم:
        // همه‌چیز از تونل. مسیریابی ایران را از دست می‌دهیم ولی اتصال برقرار
        // می‌شود — که مهم‌تر است.
        val routing = if (geoAssetsReady) {
            JSONObject().apply {
                put("domainStrategy", "IPIfNonMatch")
                put("rules", rules)
            }
        } else {
            JSONObject().apply {
                put("domainStrategy", "AsIs")
                // حتی بدون داده‌ی مسیریابی هم IPv6 باید رد شود، وگرنه همان
                // «Connecting» برمی‌گردد.
                put("rules", JSONArray().put(JSONObject().apply {
                    put("type", "field")
                    put("ip", JSONArray().put("::/0"))
                    put("outboundTag", "block")
                }).put(JSONObject().apply {
                    put("type", "field")
                    put("network", "tcp,udp")
                    put("outboundTag", "proxy")
                }))
            }
        }

        // مسیر اضطراری UDP ندارد: QUIC (UDP 443) را همان اول رد کن تا اپ‌ها
        // فوراً به TCP برگردند به‌جای چند ثانیه انتظار.
        if (tunnelMode) {
            val old = routing.optJSONArray("rules") ?: JSONArray()
            val merged = JSONArray().put(JSONObject().apply {
                put("type", "field"); put("network", "udp"); put("port", "443"); put("outboundTag", "block")
            })
            for (i in 0 until old.length()) merged.put(old.get(i))
            routing.put("rules", merged)
        }

        // دامنه‌های ایرانی با DNS ایرانی حل می‌شوند (سریع‌تر و درست‌تر)، بقیه
        // از داخل تونل تا جواب مسموم نگیرند.
        val dns = JSONObject().apply {
            put("servers", JSONArray()
                .put(JSONObject().apply {
                    put("address", IR_DNS_PRIMARY)
                    put("domains", JSONArray().put("geosite:category-ir").put("domain:ir"))
                    put("skipFallback", true)
                })
                .put(JSONObject().apply {
                    put("address", IR_DNS_SECONDARY)
                    put("domains", JSONArray().put("geosite:category-ir").put("domain:ir"))
                    put("skipFallback", true)
                })
                // مسیر اضطراری UDP ندارد → DNS خارجی روی TCP از داخل تونل
                .put(if (tunnelMode) "tcp://1.1.1.1" else "1.1.1.1")
                .put(if (tunnelMode) "tcp://8.8.8.8" else "8.8.8.8"))
            put("queryStrategy", "UseIPv4")
        }

        val config = JSONObject().apply {
            put("log", JSONObject().apply { put("loglevel", "warning") })
            put("inbounds", JSONArray().put(inboundTun).put(inboundSocks))
            // ترتیب مهم است: proxy اولِ فهرست یعنی خروجیِ پیش‌فرضِ هر چیزی
            // که هیچ قانونی نگرفتش.
            put("outbounds", JSONArray().put(outbound)
                .put(JSONObject().apply { put("protocol", "freedom"); put("tag", "direct") })
                .put(JSONObject().apply { put("protocol", "dns"); put("tag", "dns-out") })
                .put(JSONObject().apply { put("protocol", "blackhole"); put("tag", "block") }))
            // UseIPv4 همیشه لازم است، نه فقط وقتی داده‌ی geo هست: اگر DNS
            // رکورد AAAA برگرداند، باز هم مقصد IPv6 ساخته می‌شود.
            put("dns", if (geoAssetsReady) dns else JSONObject().apply {
                put("servers", if (tunnelMode) JSONArray().put("tcp://1.1.1.1").put("tcp://8.8.8.8")
                    else JSONArray().put("1.1.1.1").put("8.8.8.8"))
                put("queryStrategy", "UseIPv4")
            })
            put("routing", routing)
            // بدون این دو، هسته‌ی Xray اصلاً ترافیک نمی‌شمارد و
            // queryAllOutboundTrafficStats همیشه خالی برمی‌گشت → سرعت «۰ B/s» و نمودار صاف.
            put("stats", JSONObject())
            put("policy", JSONObject().apply {
                put("system", JSONObject().apply {
                    put("statsOutboundUplink", true)
                    put("statsOutboundDownlink", true)
                })
            })
        }

        return config.toString()
    }

    /** بهترین‌تلاش: از یک JSON کامل v2ray/xray (که خروجی outbounds داره)،
     * اولین outbound واقعی (نه freedom/blackhole) رو بیرون می‌کشه. کلاینت‌های
     * مختلف (v2rayNG، v2box، خودِ xray) دقیقاً یک فرمت واحد صادر نمی‌کنن، پس
     * این عمداً به‌جای یک اسکیمای سخت‌گیرانه، ساختار «outbounds» رو دنبال
     * می‌کنه که بین همه مشترکه. */
    fun extractPrimaryOutbound(fullConfigJson: String): JSONObject? {
        val root = try {
            JSONObject(fullConfigJson)
        } catch (e: Exception) {
            return null
        }
        val outbounds = root.optJSONArray("outbounds") ?: return null
        for (i in 0 until outbounds.length()) {
            val ob = outbounds.optJSONObject(i) ?: continue
            val protocol = ob.optString("protocol", "")
            if (protocol.isNotEmpty() && protocol !in setOf("freedom", "blackhole", "dns")) {
                return ob
            }
        }
        return null
    }

    private fun buildStreamSettings(security: String, params: Map<String, String>): JSONObject {
        return JSONObject().apply {
            put("network", params["type"] ?: "tcp")
            when (security) {
                "tls" -> {
                    put("security", "tls")
                    put("tlsSettings", JSONObject().apply {
                        put("serverName", params["sni"] ?: params["host"] ?: "")
                        put("fingerprint", params["fp"] ?: "chrome")
                        params["alpn"]?.takeIf { it.isNotBlank() }?.let { a ->
                            put("alpn", JSONArray().apply { a.split(",").forEach { put(it.trim()) } })
                        }
                    })
                    // XHTTP از راه Cloudflare (مسیر مخصوص مخابرات)
                    if ((params["type"] ?: "tcp") in setOf("xhttp", "splithttp")) {
                        put("network", "xhttp")
                        put("xhttpSettings", JSONObject().apply {
                            put("path", params["path"] ?: "/")
                            put("host", params["host"] ?: "")
                            put("mode", params["mode"] ?: "auto")
                        })
                    }
                    if ((params["type"] ?: "tcp") == "ws") {
                        put("wsSettings", JSONObject().apply {
                            put("path", params["path"] ?: "/")
                            put("headers", JSONObject().apply {
                                put("Host", params["host"] ?: "")
                            })
                        })
                    }
                }
                "reality" -> {
                    put("security", "reality")
                    put("realitySettings", JSONObject().apply {
                        put("serverName", params["sni"] ?: "")
                        put("fingerprint", params["fp"] ?: "chrome")
                        put("publicKey", params["pbk"] ?: "")
                        put("shortId", params["sid"] ?: "")
                        put("spiderX", params["spx"] ?: "/")
                    })
                }
                else -> put("security", "none")
            }
        }
    }

    private fun parseQuery(rawQuery: String): Map<String, String> {
        if (rawQuery.isEmpty()) return emptyMap()
        return rawQuery.split("&").mapNotNull { pair ->
            val idx = pair.indexOf('=')
            if (idx < 0) return@mapNotNull null
            val key = pair.substring(0, idx)
            val value = URLDecoder.decode(pair.substring(idx + 1), "UTF-8")
            key to value
        }.toMap()
    }
}
