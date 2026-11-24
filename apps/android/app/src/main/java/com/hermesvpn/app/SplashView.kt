package com.hermesvpn.app

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.BitmapShader
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.PorterDuff
import android.graphics.PorterDuffXfermode
import android.graphics.RadialGradient
import android.graphics.RectF
import android.graphics.Shader
import android.view.View
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.min
import kotlin.math.sin

/**
 * صفحه‌ی شروع — دقیقاً همان طرح تأییدشده‌ی نسخه‌ی ویندوز (splash v2، ۲۰۲۶-۱۱-۰۶):
 * آیکون هرمس با «نفس کشیدن» آرام، نوشته‌ی Hermes VPN با برق نوری که از رویش رد
 * می‌شود، هاله‌ی طلایی پشت نوشته، و نوار بارگذاری طلایی-فیروزه‌ای.
 * همه‌چیز با Canvas کشیده می‌شود و زمان‌بندی‌ها عیناً از CSS ویندوز آمده‌اند.
 */
class SplashView(context: Context) : View(context) {

    private val d = resources.displayMetrics.density
    private fun dp(v: Float) = v * d

    private val icon: Bitmap = BitmapFactory.decodeResource(resources, R.drawable.splash_icon)
    private val logo: Bitmap = BitmapFactory.decodeResource(resources, R.drawable.splash_logo)
    private val start = System.nanoTime()

    private val bgPaint = Paint()
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)
    private val iconPaint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)
    private val shinePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { xfermode = PorterDuffXfermode(PorterDuff.Mode.SRC_ATOP) }
    private val rect = RectF()
    private var bgW = 0; private var bgH = 0

    init {
        setBackgroundColor(Color.parseColor("#050B14"))
        isClickable = true                       // لمس‌ها به صفحه‌ی زیرین نرسد
    }

    /** ease-in-out مثل CSS */
    private fun ease(t: Float): Float {
        val x = t.coerceIn(0f, 1f)
        return x * x * (3f - 2f * x)
    }

    override fun onDraw(canvas: Canvas) {
        val w = width.toFloat(); val h = height.toFloat()
        if (w <= 0f || h <= 0f) return
        val tMs = (System.nanoTime() - start) / 1e6f

        // پس‌زمینه: radial-gradient(circle at 50% 40%, #13345C 0%, #0A1A30 38%, #050B14 70%)
        if (bgW != width || bgH != height) {
            val cx = w / 2f; val cy = h * 0.4f
            val r = hypot(maxOf(cx, w - cx), maxOf(cy, h - cy))   // farthest-corner
            bgPaint.shader = RadialGradient(cx, cy, r,
                intArrayOf(0xFF13345C.toInt(), 0xFF0A1A30.toInt(), 0xFF050B14.toInt(), 0xFF050B14.toInt()),
                floatArrayOf(0f, 0.38f, 0.70f, 1f), Shader.TileMode.CLAMP)
            bgW = width; bgH = height
        }
        canvas.drawRect(0f, 0f, w, h, bgPaint)

        // اندازه‌ها مثل ویندوز: آیکون ۱۵۶، فاصله ۳۰، نوشته min(520, 78vw)، فاصله ۳۴، نوار ۱۷۰×۳
        val iconS = min(dp(156f), w * 0.42f)
        val logoW = min(dp(520f), w * 0.78f)
        val logoH = logoW * logo.height / logo.width
        val gap1 = dp(30f); val gap2 = dp(34f); val barW = dp(170f); val barH = dp(3f)
        val total = iconS + gap1 + logoH + gap2 + barH
        var y = (h - total) / 2f
        val cx = w / 2f

        // ---- آیکون: breathS 2.4s ease-in-out infinite alternate (scale 1 → 1.04)
        val cyc = (tMs / 2400f) % 2f
        val breath = 1f + 0.04f * ease(if (cyc < 1f) cyc else 2f - cyc)
        val icy = y + iconS / 2f
        // هاله‌ی فیروزه‌ای 0 0 70px rgba(52,200,240,.30) + سایه‌ی تیره‌ی زیرین
        paint.shader = RadialGradient(cx, icy + dp(18f), iconS * 0.85f,
            intArrayOf(0x8C000000.toInt(), 0x00000000), null, Shader.TileMode.CLAMP)
        canvas.drawCircle(cx, icy + dp(18f), iconS * 0.85f, paint)
        paint.shader = RadialGradient(cx, icy, iconS * 0.5f + dp(70f),
            intArrayOf(0x4D34C8F0, 0x2634C8F0, 0x0034C8F0), floatArrayOf(0f, 0.55f, 1f), Shader.TileMode.CLAMP)
        canvas.drawCircle(cx, icy, iconS * 0.5f + dp(70f), paint)
        paint.shader = null

        canvas.save()
        canvas.scale(breath, breath, cx, icy)
        val half = iconS / 2f
        rect.set(cx - half, icy - half, cx + half, icy + half)
        val r = iconS * 36f / 156f                          // border-radius 36px
        val m = Matrix().apply {
            setScale(iconS / icon.width, iconS / icon.height); postTranslate(rect.left, rect.top)
        }
        iconPaint.shader = BitmapShader(icon, Shader.TileMode.CLAMP, Shader.TileMode.CLAMP).apply { setLocalMatrix(m) }
        canvas.drawRoundRect(rect, r, r, iconPaint)
        // حاشیه‌ی ظریف 0 0 0 1px rgba(255,255,255,.08)
        paint.style = Paint.Style.STROKE; paint.strokeWidth = dp(1f); paint.color = 0x14FFFFFF
        canvas.drawRoundRect(rect, r, r, paint)
        paint.style = Paint.Style.FILL
        canvas.restore()
        y += iconS + gap1

        // ---- هاله‌ی طلایی پشت نوشته: radial rgba(255,186,60,.16) → 0 در ۷۰٪
        val glowW = min(dp(640f), w * 0.8f); val glowH = dp(170f)
        val lcy = y + logoH / 2f
        canvas.save()
        canvas.scale(1f, glowH / glowW, cx, lcy)
        paint.shader = RadialGradient(cx, lcy, glowW / 2f,
            intArrayOf(0x29FFBA3C, 0x00FFBA3C, 0x00FFBA3C), floatArrayOf(0f, 0.7f, 1f), Shader.TileMode.CLAMP)
        canvas.drawCircle(cx, lcy, glowW / 2f, paint)
        paint.shader = null
        canvas.restore()

        // ---- نوشته + برق نور (logoSweep 2.2s ease-in-out .3s infinite؛ ۰ تا ۷۰٪ حرکت، بعد مکث)
        rect.set(cx - logoW / 2f, y, cx + logoW / 2f, y + logoH)
        // سایه‌ی زیرین drop-shadow(0 6px 18px rgba(0,0,0,.5)) — ملایم
        paint.alpha = 70
        canvas.save(); canvas.translate(0f, dp(6f)); paint.colorFilter = android.graphics.PorterDuffColorFilter(Color.BLACK, PorterDuff.Mode.SRC_IN)
        canvas.drawBitmap(logo, null, rect, paint); canvas.restore()
        paint.colorFilter = null; paint.alpha = 255
        val layer = canvas.saveLayer(rect, null)
        canvas.drawBitmap(logo, null, rect, paint)
        val st = tMs - 300f
        if (st > 0f) {
            val ph = (st % 2200f) / 2200f
            val p = if (ph < 0.7f) 1.2f - 1.4f * ease(ph / 0.7f) else -0.2f   // background-position 120% → -20%
            // background-size 260%: نوار نور در ۴۸٪ گرادیان، نیم‌پهنا ۱۰٪ از ۲٫۶ برابر عرض
            val c = rect.left + p * (-1.6f * logoW) + 1.248f * logoW
            val hw = 0.26f * logoW
            val a = Math.toRadians(105.0)
            val dx = sin(a).toFloat(); val dy = -cos(a).toFloat()
            val my = rect.centerY()
            shinePaint.shader = LinearGradient(c - dx * hw, my - dy * hw, c + dx * hw, my + dy * hw,
                intArrayOf(0x00FFFFFF, 0xCCFFFFFF.toInt(), 0x00FFFFFF), null, Shader.TileMode.CLAMP)
            canvas.drawRect(rect, shinePaint)
        }
        canvas.restoreToCount(layer)
        y += logoH + gap2

        // ---- نوار بارگذاری: 170×3، تکه‌ی ۴۰٪ با sbar 1.1s ease-in-out (-110% → 260%)
        rect.set(cx - barW / 2f, y, cx + barW / 2f, y + barH)
        paint.color = 0x1AFFFFFF
        canvas.drawRoundRect(rect, barH / 2f, barH / 2f, paint)
        val seg = barW * 0.4f
        val bp = ease((tMs % 1100f) / 1100f)
        val sx = rect.left + seg * (-1.1f + 3.7f * bp)
        canvas.save(); canvas.clipRect(rect)
        paint.shader = LinearGradient(sx, 0f, sx + seg, 0f,
            intArrayOf(0xFFE8B04A.toInt(), 0xFFFFE7A8.toInt(), 0xFF34C8F0.toInt()), null, Shader.TileMode.CLAMP)
        rect.set(sx, y, sx + seg, y + barH)
        canvas.drawRoundRect(rect, barH / 2f, barH / 2f, paint)
        paint.shader = null
        canvas.restore()

        postInvalidateOnAnimation()
    }
}
