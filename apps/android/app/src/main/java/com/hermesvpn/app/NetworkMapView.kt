package com.hermesvpn.app

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.Rect
import android.graphics.RectF
import android.util.AttributeSet
import android.view.View
import kotlin.math.PI
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.sin
import kotlin.random.Random

/**
 * پس‌زمینه‌ی زنده‌ی صفحه‌ی اول: کره‌ی زمینِ سه‌بعدی (تصویر واقعی NASA Blue Marble
 * + مرز واقعی کشورها، با تصویربرداری متعامد رندر شده — همون چیزی که گوگل‌ارث
 * نشون می‌ده) به‌علاوه‌ی نشانگر سرور روی فرانکفورت و پروازهای مینیمالی که روی
 * سطحِ همین کره حرکت می‌کنن.
 *
 * این‌ها همه تزئینی‌اند؛ اعداد واقعی (سرعت/پینگ/مدت) جای دیگه نشون داده می‌شن.
 */
class NetworkMapView @JvmOverloads constructor(
    context: Context, attrs: AttributeSet? = null
) : View(context, attrs) {

    private val mapBitmap: Bitmap? = try {
        BitmapFactory.decodeResource(resources, R.drawable.world_map)
    } catch (e: Throwable) {
        null
    }

    /** جوّ زمین نسبت به بیننده ثابته (خورشید و چشم جابه‌جا نمی‌شن)، پس
     * برخلاف خودِ سطح، نمی‌چرخه و یه تصویر ثابتِ روی کره‌ست. */
    private val atmosphereBitmap: Bitmap? = try {
        BitmapFactory.decodeResource(resources, R.drawable.globe_overlay)
    } catch (e: Throwable) {
        null
    }

    private val globe = GlobeRenderer(resources)
    private val globeRect = RectF()
    private var rotationDeg = 0f

    /** ترکیبِ کسری بینِ دو فریمِ همسایه‌ی کره — همون چیزی که حرکت رو به‌جای
     * پرشِ پیکسلی، کاملاً روان می‌کنه. */
    private val globeBlendPaint = Paint(Paint.FILTER_BITMAP_FLAG or Paint.ANTI_ALIAS_FLAG)

    /** کره‌ی زنده روی تصویرِ ثابت نرم ظاهر می‌شود (نه یک‌باره)، تا تفاوتِ ریزِ
     * وضوح بینِ این دو هم دیده نشود. */
    private val globeFadePaint = Paint(Paint.FILTER_BITMAP_FLAG or Paint.ANTI_ALIAS_FLAG)
    private var globeShownAt = 0L

    private val bitmapPaint = Paint(Paint.FILTER_BITMAP_FLAG or Paint.ANTI_ALIAS_FLAG)
    private val glowPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val markerPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val trailPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
    }
    private val planePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }

    /** دورخطِ تیره‌ی هواپیما — بدون این، هواپیمای روشن روی یخِ قطبی و صحرا
     * کاملاً گم می‌شه و به‌نظر می‌رسه پرواز وسطِ راه قطع شده. */
    private val planeOutlinePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 2.6f          // در مختصاتِ واحدِ مسیر، نه پیکسل
        strokeJoin = Paint.Join.ROUND
        color = 0xFF04121F.toInt()
    }

    private val density = resources.displayMetrics.density
    private fun dp(v: Float) = v * density

    private val dst = RectF()
    private var serverX = 0f
    private var serverY = 0f
    private var serverVisibility = 0f
    private var globeCx = 0f
    private var globeCy = 0f
    private var globeR = 0f

    private var running = false
    private var seeded = false
    private var startTime = 0L
    private var nextSpawnAt = 0L

    /**
     * یک پرواز، با دو نوع مسیر:
     *
     *  arc=true  کمانِ قطبی: شعاع و زاویه مستقیم درون‌یابی می‌شن، پس پرواز
     *            دقیقاً روی همون کمربندِ کره می‌مونه — هر قدر هم که بازه‌ی
     *            زاویه‌ای بزرگ باشه.
     *  arc=false وترِ بزیه: مسیرهای بلندِ سرتاسری که از روی کره رد می‌شن.
     *
     * چرا هر دو لازمه: با بزیه نمی‌شه نزدیکِ لبه پرواز داشت. وترِ بین دو نقطه‌ی
     * هم‌شعاع با فاصله‌ی زاویه‌ای Δ، وسطش می‌افته روی شعاعِ r·cos(Δ/2) — یعنی
     * برای Δ بزرگ عملاً از مرکزِ کره رد می‌شه؛ و نقطه‌ی کنترلی که بتونه برش
     * گردونه بیرون از دیسک می‌افته و کلمپ می‌شه. برای همین حاشیه‌ی کره همیشه
     * خالی می‌موند.
     */
    private class Flight(
        val arc: Boolean,
        val r0: Float, val r1: Float, val th0: Float, val th1: Float,
        val x0: Float, val y0: Float, val cx: Float, val cy: Float,
        val x1: Float, val y1: Float,
        val start: Long, val duration: Long,
        val scale: Float, val hot: Boolean,
    )

    private val flights = mutableListOf<Flight>()

    /** شبح هواپیمای واقعی (نمای از بالا: دماغه، بال‌های عقب‌گرد، دم افقی)
     * در مختصات واحد — یه‌بار ساخته می‌شه و موقع رسم فقط مقیاس می‌گیره. */
    private val planePath = Path().apply {
        val half = arrayOf(
            9.0f to 0.0f, 6.5f to 0.9f, 2.5f to 1.3f,
            -0.5f to 5.8f, -2.2f to 5.9f, -0.8f to 1.5f,
            -5.2f to 1.3f, -6.8f to 3.4f, -8.0f to 3.4f,
            -7.4f to 1.0f, -8.6f to 0.6f
        )
        moveTo(half[0].first, half[0].second)
        for (i in 1 until half.size) lineTo(half[i].first, half[i].second)
        for (i in half.size - 1 downTo 0) lineTo(half[i].first, -half[i].second)
        close()
    }

    fun start() {
        if (running) return
        running = true
        startTime = System.currentTimeMillis()
        nextSpawnAt = startTime + SPAWN_MIN_MS
        globe.resume()
        seeded = false            // بذر لازمه، ولی تا وقتی اندازه‌ی کره معلوم نشده نه
        postInvalidateOnAnimation()
    }

    fun stop() {
        running = false
        globe.pause()
        flights.clear()
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        val bmp = mapBitmap ?: return
        // عرضِ تصویر همیشه کامل دیده می‌شه (نه center-crop)، وگرنه لبه‌ی چپ و
        // راستِ کره روی صفحه‌های باریک بریده می‌شد و دیگه «کره» به‌نظر نمی‌رسید.
        val scale = w.toFloat() / bmp.width
        val dh = bmp.height * scale
        dst.set(0f, (h - dh) / 2f, w.toFloat(), (h + dh) / 2f)

        globeCx = dst.left + GLOBE_CX * dst.width()
        globeCy = dst.top + GLOBE_CY * dst.height()
        globeR = GLOBE_RX * dst.width()
        globeRect.set(globeCx - globeR, globeCy - globeR, globeCx + globeR, globeCy + globeR)

        globe.prepare((globeR * 2f).toInt())
    }

    override fun onDetachedFromWindow() {
        super.onDetachedFromWindow()
        globe.release()
        globeShownAt = 0L
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val w = width.toFloat()
        val h = height.toFloat()
        if (w <= 0f || h <= 0f) return

        canvas.drawColor(SPACE_BG)
        // تصویر پایه: فضا، ستاره‌ها، هاله‌ی بیرونی — و کره در زاویه‌ی صفر، که
        // تا وقتی رندرِ زنده آماده نشده همون رو نشون می‌دیم (بدون پرشِ دیداری،
        // چون هر دو با یه فرمول ساخته شدن).
        mapBitmap?.let {
            canvas.drawBitmap(it, Rect(0, 0, it.width, it.height), dst, bitmapPaint)
        }

        val now = System.currentTimeMillis()

        rotationDeg = globe.rotationDeg
        // دو فریمِ همسایه‌ی از پیش رندرشده (ترد جدا می‌سازدشون) با آلفای کسری
        // روی هم می‌افتن. چون وزنِ ترکیب برای همه‌ی پیکسل‌ها یکیه، نتیجه دقیقاً
        // درون‌یابیِ زیرپیکسلیِ چرخشه — بدون هیچ پرشی و بدون بار روی ترد اصلی.
        globe.frame?.let { fr ->
            if (globeShownAt == 0L) globeShownAt = now
            val fade = ((now - globeShownAt) / GLOBE_FADE_MS).coerceIn(0f, 1f)
            val fadeA = (fade * 255f).toInt()
            globeFadePaint.alpha = fadeA
            canvas.drawBitmap(fr.lo, null, globeRect, globeFadePaint)
            val a = (globe.blend(fr) * fadeA).toInt()
            if (a > 0) {
                globeBlendPaint.alpha = a
                canvas.drawBitmap(fr.hi, null, globeRect, globeBlendPaint)
            }
            atmosphereBitmap?.let { canvas.drawBitmap(it, null, globeRect, globeFadePaint) }
        }

        updateServerPosition()

        if (running) {
            // بذرِ اولیه به‌محضِ معلوم شدنِ هندسه‌ی کره — start() زودتر از
            // onSizeChanged صدا زده می‌شه و اون موقع globeR هنوز صفره.
            if (!seeded && globeR > 0f) {
                seedFlights(now)
                seeded = true
            }
            if (now >= nextSpawnAt && flights.size < MAX_FLIGHTS) {
                spawnFlight(now, 0L)
                nextSpawnAt = now + Random.nextLong(SPAWN_MIN_MS, SPAWN_MAX_MS)
            }
            val it = flights.iterator()
            while (it.hasNext()) {
                val f = it.next()
                val t = (now - f.start).toFloat() / f.duration
                if (t >= 1f) it.remove() else drawFlight(canvas, f, t)
            }
        }

        drawServerMarker(canvas, now)

        if (running) postInvalidateOnAnimation()
    }

    /** تصویربرداری متعامد — همون فرمولی که خودِ تصویر کره باهاش رندر شده،
     * پس نشانگر دقیقاً روی نقطه‌ی درستِ کره می‌شینه. خروجی مختصات نسبت به
     * مرکز کره، بر حسب شعاع (x به راست، y به پایین). */
    private fun projectToGlobe(latDeg: Double, lonDeg: Double, centerLonDeg: Double): FloatArray {
        val p = Math.toRadians(latDeg)
        val q = Math.toRadians(lonDeg)
        val p0 = Math.toRadians(GLOBE_LAT0)
        val q0 = Math.toRadians(centerLonDeg)
        val dq = q - q0
        val cosC = sin(p0) * sin(p) + cos(p0) * cos(p) * cos(dq)
        val nx = cos(p) * sin(dq)
        val ny = cos(p0) * sin(p) - sin(p0) * cos(p) * cos(dq)
        return floatArrayOf(nx.toFloat(), (-ny).toFloat(), cosC.toFloat())
    }

    // ---------- نشانگر سرور ----------

    /** موقعیت سرور با چرخش کره جابه‌جا می‌شه و وقتی می‌ره پشتِ کره محو می‌شه —
     * چون واقعاً همون‌جا روی کره‌ست، نه یه برچسبِ چسبیده به صفحه. قبل از
     * ساختِ پروازها حساب می‌شه تا مقصدشون موقعیتِ همین لحظه باشه. */
    private fun updateServerPosition() {
        if (globeR <= 0f) return
        val pr = projectToGlobe(SERVER_LAT, SERVER_LON, GLOBE_LON0 + rotationDeg)
        serverX = globeCx + pr[0] * globeR
        serverY = globeCy + pr[1] * globeR
        serverVisibility = (pr[2] / 0.16f).coerceIn(0f, 1f)
    }

    private fun drawServerMarker(canvas: Canvas, now: Long) {
        if (globeR <= 0f) return
        val visibility = serverVisibility
        if (visibility <= 0.01f) return
        // محوشدن نرم نزدیک لبه‌ی کره — کل نشانگر یک لایه می‌شه تا اجزاش
        // (هاله، پرچم، میله، نقطه) با هم و یکنواخت محو بشن.
        val layer = if (visibility < 0.999f) canvas.saveLayerAlpha(
            serverX - dp(40f), serverY - dp(50f), serverX + dp(40f), serverY + dp(40f),
            (visibility * 255).toInt()
        ) else -1

        val period = 2800.0
        val phase = ((((now - startTime).coerceAtLeast(0)) % period) / period).toFloat()
        val ease = sin(phase * PI.toFloat())

        glowPaint.color = ACCENT
        glowPaint.alpha = (80 * (1f - phase)).toInt().coerceIn(0, 80)
        canvas.drawCircle(serverX, serverY, dp(9f) + dp(20f) * ease, glowPaint)
        glowPaint.alpha = 45
        canvas.drawCircle(serverX, serverY, dp(12f), glowPaint)

        // پرچم آلمان روی محل واقعی سرور (فرانکفورت)
        val fw = dp(16f)
        val fh = dp(10.7f)
        val left = serverX - fw / 2f
        val top = serverY - fh - dp(10f)
        val band = fh / 3f

        markerPaint.color = 0xAA000000.toInt()
        canvas.drawRoundRect(
            RectF(left - dp(1.5f), top - dp(1.5f), left + fw + dp(1.5f), top + fh + dp(1.5f)),
            dp(2.5f), dp(2.5f), markerPaint
        )
        markerPaint.color = 0xFF111111.toInt()
        canvas.drawRect(left, top, left + fw, top + band, markerPaint)
        markerPaint.color = 0xFFDD0000.toInt()
        canvas.drawRect(left, top + band, left + fw, top + band * 2, markerPaint)
        markerPaint.color = 0xFFFFCE00.toInt()
        canvas.drawRect(left, top + band * 2, left + fw, top + fh, markerPaint)

        markerPaint.color = 0xCCFFFFFF.toInt()
        canvas.drawRect(serverX - dp(0.6f), top + fh, serverX + dp(0.6f), serverY - dp(2f), markerPaint)
        markerPaint.color = ACCENT
        canvas.drawCircle(serverX, serverY, dp(3f), markerPaint)
        markerPaint.color = 0xFFFFFFFF.toInt()
        canvas.drawCircle(serverX, serverY, dp(1.4f), markerPaint)

        if (layer != -1) canvas.restoreToCount(layer)
    }

    // ---------- پروازها ----------

    /** شعاعِ تصادفی داخل دیسک. توانِ ۰٫۴ (نه ۰٫۵ که پخشِ یکنواختِ سطحه) عمداً
     * کمی به بیرون تمایل داره، چون کمربندهای بیرونی وگرنه خیلی خلوت می‌مونن. */
    private fun randomRadius(): Float =
        Math.pow(Random.nextDouble(), 0.40).toFloat() * MAX_FLIGHT_RADIUS

    private fun pointAt(angle: Float, radius: Float): FloatArray = floatArrayOf(
        globeCx + cos(angle) * radius * globeR,
        globeCy + sin(angle) * radius * globeR
    )

    /** موقع باز شدنِ صفحه، کره بلافاصله پر از پرواز باشه — نه این‌که کاربر
     * چند ثانیه یه کره‌ی خالی ببینه. هر پرواز از وسطِ مسیرش شروع می‌شه. */
    private fun seedFlights(now: Long) {
        flights.clear()
        repeat(SEED_FLIGHTS) { spawnFlight(now, Random.nextLong(0, 6000)) }
    }

    private fun spawnFlight(now: Long, agedMs: Long) {
        if (globeR <= 0f) return
        val duration = Random.nextLong(7500, 15000)
        val start = now - agedMs.coerceAtMost(duration - 400)
        // بازه‌ی اندازه عمداً پهنه: هواپیمای نزدیک و دور کنار هم، حسِ عمق می‌ده
        val scale = 0.60f + Random.nextFloat() * 1.05f

        // گاهی مقصد سرورِ ماست — ولی فقط وقتی سرور این‌طرفِ کره‌ست، وگرنه
        // مقصد یه نقطه‌ی پرتِ نامرئیِ پشتِ کره می‌شد.
        val hot = serverVisibility > 0.6f && Random.nextInt(0, 10) < 2

        if (!hot && Random.nextFloat() >= CROSS_FRACTION) {
            // پروازِ کمربندی. کمربند بر اساس سهمِ از پیش تعیین‌شده انتخاب می‌شه:
            // حلقه‌ی بیرونی سهمِ بیشتری داره چون محیطش بزرگ‌تره و با سهمِ برابر
            // همیشه خلوت‌تر به‌نظر می‌رسید.
            val u = Random.nextFloat()
            val band = when {
                u < BAND_INNER_SHARE -> 0
                u < BAND_INNER_SHARE + BAND_MID_SHARE -> 1
                else -> 2
            }
            val rMin = BAND_R_MIN[band]
            val rMax = BAND_R_MAX[band]
            val jitter = BAND_JITTER[band]
            val r0 = rMin + Random.nextFloat() * (rMax - rMin)
            val r1 = (r0 + (Random.nextFloat() * 2f - 1f) * jitter).coerceIn(0.06f, MAX_FLIGHT_RADIUS)
            val th0 = Random.nextFloat() * 2f * PI.toFloat()
            val span = (BAND_SPAN_MIN[band] +
                Random.nextFloat() * (BAND_SPAN_MAX[band] - BAND_SPAN_MIN[band])) *
                if (Random.nextBoolean()) 1f else -1f
            flights.add(
                Flight(
                    arc = true,
                    r0 = r0, r1 = r1, th0 = th0, th1 = th0 + span,
                    x0 = 0f, y0 = 0f, cx = 0f, cy = 0f, x1 = 0f, y1 = 0f,
                    start = start, duration = duration, scale = scale, hot = false
                )
            )
            return
        }

        // مسیرِ بلندِ سرتاسری. تا پیدا شدنِ مسیرِ به‌اندازه‌ی کافی بلند تلاش
        // می‌کنه؛ قبلاً این‌جا بی‌صدا return می‌کرد و اسپاون هدر می‌رفت.
        var x0: Float
        var y0: Float
        var x1: Float
        var y1: Float
        var len: Float
        var attempt = 0
        do {
            val a = pointAt(Random.nextFloat() * 2f * PI.toFloat(), randomRadius())
            x0 = a[0]; y0 = a[1]
            if (hot) {
                x1 = serverX; y1 = serverY
            } else {
                val b = pointAt(Random.nextFloat() * 2f * PI.toFloat(), randomRadius())
                x1 = b[0]; y1 = b[1]
            }
            len = hypot(x1 - x0, y1 - y0)
            attempt++
        } while (len < globeR * 0.55f && attempt < 8)
        if (len < 1f) return

        // قوسِ ملایم رو به بیرون، با نگه‌داشتنِ نقطه‌ی کنترل داخلِ دیسک
        val dx = x1 - x0
        val dy = y1 - y0
        val mx = (x0 + x1) / 2f
        val my = (y0 + y1) / 2f
        var nx = -dy / len
        var ny = dx / len
        if ((mx - globeCx) * nx + (my - globeCy) * ny < 0f) { nx = -nx; ny = -ny }
        val bow = len * (0.13f + Random.nextFloat() * 0.14f)
        var cx = mx + nx * bow
        var cy = my + ny * bow
        val cd = hypot(cx - globeCx, cy - globeCy)
        if (cd > globeR * 0.95f) {
            val k = globeR * 0.95f / cd
            cx = globeCx + (cx - globeCx) * k
            cy = globeCy + (cy - globeCy) * k
        }

        flights.add(
            Flight(
                arc = false,
                r0 = 0f, r1 = 0f, th0 = 0f, th1 = 0f,
                x0 = x0, y0 = y0, cx = cx, cy = cy, x1 = x1, y1 = y1,
                start = start, duration = duration, scale = scale, hot = hot
            )
        )
    }

    private fun bez(a: Float, c: Float, b: Float, t: Float): Float {
        val u = 1 - t
        return u * u * a + 2 * u * t * c + t * t * b
    }

    private fun posX(f: Flight, t: Float): Float =
        if (f.arc) globeCx + cos(f.th0 + (f.th1 - f.th0) * t) * (f.r0 + (f.r1 - f.r0) * t) * globeR
        else bez(f.x0, f.cx, f.x1, t)

    private fun posY(f: Flight, t: Float): Float =
        if (f.arc) globeCy + sin(f.th0 + (f.th1 - f.th0) * t) * (f.r0 + (f.r1 - f.r0) * t) * globeR
        else bez(f.y0, f.cy, f.y1, t)

    private fun drawFlight(canvas: Canvas, f: Flight, t: Float) {
        val fade = when {
            t < 0.12f -> t / 0.12f
            t > 0.86f -> (1f - t) / 0.14f
            else -> 1f
        }.coerceIn(0f, 1f)

        val x = posX(f, t)
        val y = posY(f, t)

        // ردِ کندانسِ محوشونده روی همون کمان. دو پاسِ جدا: اول کلِ خطِ تیره،
        // بعد کلِ خطِ روشن. اگه قطعه‌به‌قطعه درهم بکشیم، سرِ گردِ هر قطعه‌ی
        // روشن زیرِ قطعه‌ی تیره‌ی بعدی می‌ره و رد، بریده‌بریده دیده می‌شه.
        var i = 1
        while (i <= 2) {
            val dark = i == 1
            trailPaint.color = if (dark) 0xFF04121F.toInt() else if (f.hot) ACCENT else TRAIL
            var prevX = x
            var prevY = y
            var j = 1
            while (j <= TRAIL_SEGMENTS) {
                val tt = t - 0.22f * j / TRAIL_SEGMENTS
                if (tt <= 0f) break
                val nx2 = posX(f, tt)
                val ny2 = posY(f, tt)
                val k = 1f - j.toFloat() / TRAIL_SEGMENTS
                val maxA = if (dark) 95 else 165
                trailPaint.alpha = (maxA * k * k * fade).toInt().coerceIn(0, maxA)
                trailPaint.strokeWidth = (if (dark) dp(2.6f) else dp(1.7f)) * k * f.scale
                canvas.drawLine(prevX, prevY, nx2, ny2, trailPaint)
                prevX = nx2
                prevY = ny2
                j++
            }
            i++
        }

        val ta = (t + 0.012f).coerceAtMost(1f)
        val angle = Math.toDegrees(
            atan2(posY(f, ta) - y, posX(f, ta) - x).toDouble()
        ).toFloat()

        canvas.save()
        canvas.translate(x, y)
        canvas.rotate(angle)
        canvas.scale(dp(0.78f) * f.scale, dp(0.78f) * f.scale)

        // هاله‌ی کوچک زیر هواپیما تا روی زمینه‌ی تیره گم نشه
        glowPaint.color = if (f.hot) ACCENT else PLANE
        glowPaint.alpha = (55 * fade).toInt().coerceIn(0, 55)
        canvas.drawCircle(0f, 0f, 7.5f, glowPaint)

        planeOutlinePaint.alpha = (190 * fade).toInt().coerceIn(0, 190)
        canvas.drawPath(planePath, planeOutlinePaint)
        planePaint.color = if (f.hot) ACCENT_BRIGHT else PLANE
        planePaint.alpha = (255 * fade).toInt().coerceIn(0, 255)
        canvas.drawPath(planePath, planePaint)
        canvas.restore()
    }

    private companion object {
        // تراکم پرواز: باید در هر لحظه و در هر گوشه‌ی کره چند پرواز دیده بشه،
        // وگرنه روی پهنه‌های خالی (اقیانوس‌ها) صفحه مرده به‌نظر می‌رسه.
        /** مدتِ ظاهرشدنِ نرمِ کره‌ی زنده روی تصویر ثابت. */
        const val GLOBE_FADE_MS = 700f

        const val MAX_FLIGHTS = 7
        const val SEED_FLIGHTS = 5
        const val SPAWN_MIN_MS = 900L
        const val SPAWN_MAX_MS = 1800L
        const val MAX_FLIGHT_RADIUS = 0.98f

        /** سهمِ مسیرهای بلندِ سرتاسری؛ بقیه پروازِ کمربندی‌اند. */
        const val CROSS_FRACTION = 0.18f

        // سه کمربند: مرکزی، میانی، لبه. سهمِ لبه بیشتره چون محیطش بزرگ‌تره
        // و با سهمِ برابر همیشه خلوت به‌نظر می‌رسید.
        const val BAND_INNER_SHARE = 0.30f
        const val BAND_MID_SHARE = 0.44f
        val BAND_R_MIN = floatArrayOf(0.10f, 0.56f, 0.80f)
        val BAND_R_MAX = floatArrayOf(0.54f, 0.78f, 0.92f)
        val BAND_JITTER = floatArrayOf(0.10f, 0.07f, 0.05f)
        val BAND_SPAN_MIN = floatArrayOf(1.2f, 1.0f, 0.9f)
        val BAND_SPAN_MAX = floatArrayOf(3.4f, 2.8f, 2.2f)
        const val TRAIL_SEGMENTS = 14

        // هندسه‌ی کره داخل تصویر — دقیقاً همون مقادیری که تصویر باهاشون رندر شد
        const val GLOBE_CX = 0.5000f     // نسبت به عرض تصویر
        const val GLOBE_CY = 0.5023f     // نسبت به ارتفاع تصویر
        const val GLOBE_RX = 0.5000f     // شعاع، نسبت به عرض تصویر
        const val GLOBE_LAT0 = 35.0
        const val GLOBE_LON0 = 15.0

        const val SERVER_LAT = 50.11     // فرانکفورت — محل واقعی سرور
        const val SERVER_LON = 8.68

        const val SPACE_BG = 0xFF050B14.toInt()
        const val ACCENT = 0xFF34C8F0.toInt()
        const val ACCENT_BRIGHT = 0xFFCCF3FF.toInt()
        const val PLANE = 0xFFEAF6FC.toInt()
        const val TRAIL = 0xFFAFD4E6.toInt()
    }
}
