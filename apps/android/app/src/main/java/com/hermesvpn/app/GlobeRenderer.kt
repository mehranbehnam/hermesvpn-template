package com.hermesvpn.app

import android.content.res.Resources
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.SystemClock
import android.util.Log
import kotlin.math.asin
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.floor
import kotlin.math.hypot
import kotlin.math.pow
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * کره‌ی زمینِ چرخان.
 *
 * تصویربرداریِ متعامد یه خاصیت خیلی خوب داره: چرخش حول محورِ قطب‌ها فقط *طول
 * جغرافیایی* هر پیکسل رو جابه‌جا می‌کنه؛ عرض جغرافیایی، نرمالِ سطح و در نتیجه
 * نورپردازی ثابت می‌مونن. پس همه‌ی حساب‌های سنگین (asin/atan2/pow) یک‌بار توی
 * prepare() انجام می‌شن و بعدش هر فریم فقط جدول‌خوانی و ضربه.
 *
 * دو تا نکته که بدون‌شون حرکت تیکه‌تیکه می‌شه و قبلاً هم شد:
 *
 *  ۱) جابه‌جاییِ ستونِ بافت عدد صحیحه. با عرضِ ۲۰۴۸ هر ستون ۰٫۱۸ درجه‌ست که
 *     نزدیکِ مرکزِ کره حدود یک پیکسلِ صفحه می‌شه — یعنی تصویر ۷ بار در ثانیه
 *     یک پیکسل می‌پره. راه‌حل: همیشه *دو* فریمِ همسایه (ستون k و k+1) آماده
 *     نگه داشته می‌شه و روی صفحه با آلفای کسری روی هم می‌افتن. چون وزنِ ترکیب
 *     برای همه‌ی پیکسل‌ها یکیه، این دقیقاً همون درون‌یابیِ دوخطی در راستای طول
 *     جغرافیاییه — ولی به‌جای CPU، کارت گرافیک انجامش می‌ده.
 *
 *  ۲) ساختِ هر فریم چند میلی‌ثانیه طول می‌کشه. روی ترد اصلی یعنی پریدنِ فریم.
 *     پس یه ترد جدا همیشه یک قدم جلوتر رندر می‌کنه و نتیجه رو منتشر می‌کنه؛
 *     ترد اصلی فقط دوتا بیت‌مپِ آماده رو می‌کشه.
 */
class GlobeRenderer(private val res: Resources) {

    /** یک جفتِ آماده برای نمایش: ستونِ k و k+1. تغییرناپذیره تا ترد اصلی
     * بتونه بدون قفل بخونتش. */
    class Frame(val lo: Bitmap, val hi: Bitmap, val col: Int)

    @Volatile
    var frame: Frame? = null
        private set

    @Volatile
    var ready = false
        private set

    private var n = 0
    private var packed: IntArray? = null       // (ردیف بافت) یا (ستون پایه)
    private var shade: ShortArray? = null      // ضریب نور، مقیاس ۲۵۶
    private var rowStart: IntArray? = null
    private var rowEnd: IntArray? = null
    private var tex: IntArray? = null
    private var scratchArr: IntArray? = null

    /** سه بافر: همیشه دوتاش منتشرشده‌ست و سومی آزاده تا ترد رندر بدون
     * برخورد با ترد اصلی توش بنویسه. */
    private var bufs: Array<Bitmap>? = null
    private var nextBuf = 0

    private var preparing = false
    private var worker: Thread? = null
    @Volatile private var workerRunning = false

    // زمانِ چرخش: با توقف/ادامه، زاویه از جایی که بود ادامه پیدا می‌کنه
    private var accumulatedDeg = 0.0
    private var resumedAt = 0L
    @Volatile private var spinning = false

    /**
     * ساعتِ چرخش فقط وقتی راه می‌افتد که اولین فریمِ زنده منتشر شده باشد، و
     * سرعت هم از صفر نرم بالا می‌رود.
     *
     * قبلاً ساعت از resume() شروع می‌شد، ولی آماده‌سازیِ جدول‌ها چند صد میلی‌ثانیه
     * طول می‌کشد؛ پس اولین فریمِ زنده چند پیکسل جلوتر از تصویرِ ثابتِ زاویه‌ی صفر
     * بود و کره با یک «تقه» شروع به حرکت می‌کرد. سرعتِ ناگهانیِ کامل هم خودش
     * یک تکان دیگر بود.
     */
    @Volatile private var clockStarted = false

    private fun startClock() {
        resumedAt = SystemClock.elapsedRealtime()
        clockStarted = true
    }

    /** زمانِ «معادلِ حرکت با سرعت کامل». سرعت با منحنیِ smoothstep از صفر به
     * یک می‌رسد، پس مسافت انتگرالِ آن است: R·(u³ − u⁴/2). در u=1 هم مقدار
     * (R/2) و هم شیب (سرعت ۱) پیوسته‌اند، پس انتهای شتاب‌گیری هم دیده نمی‌شود. */
    private fun travelledMs(): Double {
        if (!clockStarted) return 0.0
        val t = (SystemClock.elapsedRealtime() - resumedAt).toDouble()
        if (t >= RAMP_MS) return RAMP_MS / 2.0 + (t - RAMP_MS)
        val u = t / RAMP_MS
        return RAMP_MS * (u * u * u - u * u * u * u / 2.0)
    }

    val rotationDeg: Float
        get() = ((accumulatedDeg + travelledMs() * DEG_PER_MS) % 360.0).toFloat()

    private fun position(): Double =
        ((accumulatedDeg + travelledMs() * DEG_PER_MS) / 360.0) * TEX_W

    /** کسرِ بینِ ستونِ منتشرشده و ستون بعدی — همون آلفای ترکیب. */
    fun blend(f: Frame): Float {
        val pos = position()
        val d = pos - f.col
        return d.coerceIn(0.0, 1.0).toFloat()
    }

    // ---------- چرخه‌ی عمر ----------

    fun prepare(diameterPx: Int) {
        if (ready || preparing || diameterPx <= 0) return
        preparing = true
        val size = diameterPx.coerceAtMost(MAX_BUFFER).coerceAtLeast(240)
        Thread({
            try {
                build(size)
                ready = true
                if (spinning) startWorker()
            } catch (e: Throwable) {
                // اگر حافظه نرسید یا بافت باز نشد، تصویر ثابتِ کره سر جاش می‌مونه
                Log.w(TAG, "آماده‌سازی کره ناموفق بود، تصویر ثابت استفاده می‌شود: ${e.message}")
                release()
            } finally {
                preparing = false
            }
        }, "globe-prepare").apply { priority = Thread.MIN_PRIORITY }.start()
    }

    fun resume() {
        if (spinning) return
        spinning = true
        // اگر فریمی از قبل روی صفحه هست (برگشت به اپ)، از همان‌جا نرم راه بیفت؛
        // وگرنه ساعت را ترد رندر بعد از انتشارِ اولین فریم روشن می‌کند.
        if (frame != null) startClock()
        if (ready) startWorker()
    }

    fun pause() {
        if (clockStarted) {
            accumulatedDeg = (accumulatedDeg + travelledMs() * DEG_PER_MS) % 360.0
            clockStarted = false
        }
        spinning = false
        stopWorker()
    }

    fun release() {
        stopWorker()
        synchronized(this) {
            ready = false
            frame = null
            tex = null
            packed = null
            shade = null
            rowStart = null
            rowEnd = null
            scratchArr = null
            // عمداً recycle() نمی‌شن: اگه ترد اصلی همون لحظه رفرنس داشته باشه
            // کرشِ use-after-recycle می‌ده. رها کردنِ رفرنس کافیه، GC جمعش می‌کنه.
            bufs = null
            n = 0
        }
    }

    // ---------- ترد رندر ----------

    private fun startWorker() {
        if (workerRunning) return
        workerRunning = true
        worker = Thread({
            try {
                loop()
            } catch (e: Throwable) {
                Log.w(TAG, "ترد رندر کره متوقف شد: ${e.message}")
            }
        }, "globe-render").apply { priority = Thread.MIN_PRIORITY; start() }
    }

    private fun stopWorker() {
        workerRunning = false
        worker?.interrupt()
        worker = null
    }

    private fun loop() {
        while (workerRunning) {
            val k = floor(position()).toInt()
            val cur = frame
            when {
                cur == null -> {
                    publish(renderInto(k), renderInto(k + 1), k)
                    // ساعت دقیقاً از همین لحظه شروع می‌شود، پس اولین فریمِ زنده
                    // هم‌زاویه‌ی تصویرِ ثابت است و پرشی دیده نمی‌شود.
                    if (spinning && !clockStarted) startClock()
                }
                k > cur.col -> {
                    // یک قدم جلو: بالاییِ قبلی می‌شه پایینیِ جدید، فقط یک رندر لازمه
                    val nk = cur.col + 1
                    publish(cur.hi, renderInto(nk + 1), nk)
                }
                else -> {
                    // به‌روزیم: تا رسیدنِ ستون بعدی بخواب (بیدارباشِ اضافه = مصرف باتری)
                    val waitMs = ((cur.col + 1 - position()) / DEG_PER_MS * 360.0 / TEX_W).toLong()
                    try {
                        Thread.sleep(waitMs.coerceIn(8L, 250L))
                    } catch (e: InterruptedException) {
                        return
                    }
                }
            }
        }
    }

    private fun publish(lo: Bitmap, hi: Bitmap, col: Int) {
        frame = Frame(lo, hi, col)
    }

    /** در بافرِ آزاد (نه هیچ‌کدوم از دوتای منتشرشده) رندر می‌کنه. */
    private fun renderInto(col: Int): Bitmap {
        val all = bufs ?: throw IllegalStateException("بافرها آماده نیستند")
        val cur = frame
        var target: Bitmap? = null
        for (i in 0 until all.size) {
            val b = all[(nextBuf + i) % all.size]
            if (cur == null || (b !== cur.lo && b !== cur.hi)) {
                target = b
                nextBuf = (nextBuf + i + 1) % all.size
                break
            }
        }
        val bmp = target ?: all[0]
        renderColumn(bmp, col)
        return bmp
    }

    /** ساختِ یک فریم در جابه‌جاییِ ستونیِ داده‌شده: فقط جدول‌خوانی و ضرب. */
    private fun renderColumn(bmp: Bitmap, col: Int) {
        val size = n
        val t = tex ?: return
        val p = packed ?: return
        val s = shade ?: return
        val rs = rowStart ?: return
        val re = rowEnd ?: return
        if (size <= 0 || bmp.isRecycled) return

        val out = scratch(size)
        var rot = col % TEX_W
        if (rot < 0) rot += TEX_W

        for (py in 0 until size) {
            val a = rs[py]
            if (a < 0) continue
            val b = re[py]
            val base = py * size
            for (px in a..b) {
                val i = base + px
                val pk = p[i]
                val idx = (pk and ROW_MASK) or ((pk + rot) and COL_MASK)
                val c = t[idx]
                val sh = s[i].toInt()
                val r = (((c ushr 16) and 0xFF) * sh) ushr 8
                val g = (((c ushr 8) and 0xFF) * sh) ushr 8
                val bl = ((c and 0xFF) * sh) ushr 8
                out[i] = -0x1000000 or
                    (r.coerceAtMost(255) shl 16) or
                    (g.coerceAtMost(255) shl 8) or
                    bl.coerceAtMost(255)
            }
        }
        bmp.setPixels(out, 0, size, 0, 0, size, size)
    }

    private fun scratch(size: Int): IntArray {
        var a = scratchArr
        if (a == null || a.size != size * size) {
            a = IntArray(size * size)          // بیرونِ دیسک صفر می‌ماند ⇒ شفاف
            scratchArr = a
        }
        return a
    }

    // ---------- آماده‌سازی جدول‌ها ----------

    private fun build(size: Int) {
        val opts = BitmapFactory.Options().apply { inPreferredConfig = Bitmap.Config.ARGB_8888 }
        val texBmp = BitmapFactory.decodeResource(res, R.drawable.globe_texture, opts)
            ?: throw IllegalStateException("بافت کره باز نشد")
        val pixels = IntArray(TEX_W * TEX_H)
        texBmp.getPixels(pixels, 0, TEX_W, 0, 0, TEX_W, TEX_H)
        texBmp.recycle()

        val p = IntArray(size * size)
        val s = ShortArray(size * size)
        val rs = IntArray(size)
        val re = IntArray(size)

        val p0 = Math.toRadians(LAT0)
        val sinP0 = sin(p0)
        val cosP0 = cos(p0)

        for (py in 0 until size) {
            val yScreen = ((py + 0.5) / size) * 2.0 - 1.0     // پایین مثبت
            val yMap = -yScreen                                // شمال مثبت
            var start = -1
            var end = -1
            for (px in 0 until size) {
                val x = ((px + 0.5) / size) * 2.0 - 1.0
                val rho = hypot(x, yScreen)
                val i = py * size + px
                if (rho > 1.0) continue
                if (start < 0) start = px
                end = px

                val rhoS = if (rho < 1e-9) 1e-9 else rho
                val c = asin(rho.coerceAtMost(1.0))
                val sinC = sin(c)
                val cosC = cos(c)

                val lat = asin((cosC * sinP0 + (yMap * sinC * cosP0) / rhoS).coerceIn(-1.0, 1.0))
                val lonRel = atan2(x * sinC, rhoS * cosC * cosP0 - yMap * sinC * sinP0)

                val v = (((Math.PI / 2 - lat) / Math.PI) * (TEX_H - 1)).toInt().coerceIn(0, TEX_H - 1)
                val lonAbs = lonRel + Math.toRadians(LON0)
                var colBase = (((lonAbs + Math.PI) / (2 * Math.PI)) * TEX_W).toInt()
                colBase = ((colBase % TEX_W) + TEX_W) % TEX_W
                p[i] = (v shl COL_BITS) or colBase

                val nz = sqrt((1.0 - rho * rho).coerceAtLeast(0.0))
                val diff = (x * LX + yScreen * LY + nz * LZ).coerceIn(0.0, 1.0)
                // این فرمول باید مو‌به‌مو با اسکریپت ساخت تصویر ثابت یکی باشد
                val sh = (0.58 + 0.58 * diff) * (0.36 + 0.64 * nz.pow(0.32))
                s[i] = (sh * 256.0).toInt().coerceIn(0, 32767).toShort()
            }
            rs[py] = start
            re[py] = end
        }

        val three = Array(3) { Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888) }
        synchronized(this) {
            n = size
            tex = pixels
            packed = p
            shade = s
            rowStart = rs
            rowEnd = re
            bufs = three
        }
    }

    companion object {
        private const val TAG = "GlobeRenderer"
        const val TEX_W = 2048
        const val TEX_H = 1024
        private const val COL_BITS = 11               // 2^11 = 2048
        private const val COL_MASK = TEX_W - 1
        private const val ROW_MASK = COL_MASK.inv()
        private const val MAX_BUFFER = 960            // مصالحه‌ی وضوح، حافظه و هزینه‌ی هر فریم

        const val LAT0 = 35.0                          // زاویه‌ی دیدِ کره
        const val LON0 = 15.0

        /** یک دور کامل در ۵ دقیقه — عمداً خیلی آروم و آرامش‌بخش. */
        const val DEG_PER_MS = 360.0 / 300_000.0

        /** مدتِ شتاب‌گیریِ نرم از حالت ایستاده تا سرعت کامل. */
        private const val RAMP_MS = 3500.0

        private const val LX = -0.42 / 0.99985
        private const val LY = -0.48 / 0.99985
        private const val LZ = 0.77 / 0.99985
    }
}
