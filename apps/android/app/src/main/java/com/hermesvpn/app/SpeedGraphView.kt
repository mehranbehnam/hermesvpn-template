package com.hermesvpn.app

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.Path
import android.graphics.Shader
import android.util.AttributeSet
import android.view.View

/**
 * نمودار زنده‌ی سرعت — همان روش نسخه‌ی ویندوز (۱.۰.۱۸).
 *
 * داده‌ها ۱۰۰٪ واقعی‌اند (HermesVpnService.onSpeedUpdate از شمارنده‌های Xray).
 * حرکت با «ساعت مجازی» با سرعت ثابت است و به زمانِ رسیدن نمونه‌ها بستگی ندارد:
 * هر نمونه‌ی k در خانه‌ی k می‌نشیند و ساعت حدود ۱٫۷ خانه عقب‌تر از تازه‌ترین نمونه
 * حرکت می‌کند، پس نقطه‌ی تازه همیشه بیرون از کادر اضافه می‌شود و بعد آرام وارد
 * می‌شود. اگر نمونه‌ها کمی زود/دیر برسند، سرعت حداکثر ±۴٪ و آن هم تدریجی عوض
 * می‌شود — هیچ‌وقت پرش دیده نمی‌شود. مقیاس عمودی هم بر اساس زمان نرم می‌شود.
 */
class SpeedGraphView @JvmOverloads constructor(
    context: Context, attrs: AttributeSet? = null
) : View(context, attrs) {

    private val slots = 48               // ~۴۸ ثانیه‌ی اخیر در عرض کادر
    private val intervalMs = 1000f       // هم‌تراز با statsRunnable سرویس

    private class Sample(val k: Long, val d: Float, val u: Float)
    private val samples = ArrayDeque<Sample>()
    private var nextK = 0L
    private var vt = 0.0                 // ساعت مجازی (برحسب خانه)
    private var rate = 1.0
    private var lastFrameNs = 0L
    private var displayedMax = 64f * 1024f

    private val downGlow = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE; strokeCap = Paint.Cap.ROUND; strokeJoin = Paint.Join.ROUND
    }
    private val downLine = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE; strokeCap = Paint.Cap.ROUND; strokeJoin = Paint.Join.ROUND
    }
    private val upLine = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE; strokeCap = Paint.Cap.ROUND; strokeJoin = Paint.Join.ROUND
    }
    private val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val baselinePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE; color = 0x1AFFFFFF; strokeWidth = 1f
    }
    private var fillShaderH = -1f

    private val density = resources.displayMetrics.density
    private fun dp(v: Float) = v * density

    private val linePath = Path()
    private val fillPath = Path()

    /** یک نمونه‌ی واقعی (بایت بر ثانیه) از هسته. */
    fun addSample(downBytesPerSec: Long, upBytesPerSec: Long) {
        if (samples.isEmpty()) { vt = nextK - 1.2; rate = 1.0 }   // اولین نقطه درست بیرون لبه
        samples.addLast(Sample(nextK++, downBytesPerSec.toFloat(), upBytesPerSec.toFloat()))
        while (samples.size > slots + 4) samples.removeFirst()
        postInvalidateOnAnimation()
    }

    fun clear() {
        samples.clear()
        nextK = 0L; vt = 0.0; rate = 1.0
        displayedMax = 64f * 1024f
        lastFrameNs = 0L
        invalidate()
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        lastFrameNs = 0L
        postInvalidateOnAnimation()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val now = System.nanoTime()
        val dt = if (lastFrameNs == 0L) 16f else ((now - lastFrameNs) / 1e6f).coerceIn(0f, 100f)
        lastFrameNs = now

        val w = width.toFloat()
        val h = height.toFloat()
        if (w <= 0f || h <= 0f) return
        canvas.drawLine(0f, h - dp(1f), w, h - dp(1f), baselinePaint)

        if (samples.isNotEmpty()) {
            val newest = (nextK - 1).toDouble()
            // اگر نمودار مدتی دیده نشد (صفحه خاموش/پس‌زمینه) و ساعت خیلی عقب ماند، دوباره پشت
            // تازه‌ترین نمونه بنشیند؛ وگرنه نقطه‌های دیدنی از بافر بیرون می‌افتند و موج محو می‌شود.
            if (newest - vt > 4) { vt = newest - 1.7; rate = 1.0 }
            val lag = newest - vt
            val target = 1.0 + ((lag - 1.7) * 0.03).coerceIn(-0.04, 0.04)
            rate += (target - rate) * (dt / 3000f).coerceAtMost(1f)
            vt += (dt / intervalMs) * rate
            if (vt > newest + 0.2) vt = newest + 0.2           // هیچ‌وقت از داده جلو نزن
        }

        val step = w / (slots - 1).toFloat()
        val vis = samples.filter { w - ((vt - it.k) * step).toFloat() > -2 * step }
        if (vis.size >= 2) {
            var peak = 64f * 1024f
            for (s in vis) { if (s.d > peak) peak = s.d; if (s.u > peak) peak = s.u }
            displayedMax += (peak * 1.15f - displayedMax) * (dt / 400f).coerceAtMost(1f)

            val top = dp(6f)
            val usable = h - top - dp(4f)
            if (fillShaderH != h) {
                fillPaint.shader = LinearGradient(0f, top, 0f, h, 0x6634C8F0, Color.TRANSPARENT, Shader.TileMode.CLAMP)
                fillShaderH = h
            }
            val n = vis.size
            val xs = FloatArray(n) { w - ((vt - vis[it].k) * step).toFloat() }
            val yd = FloatArray(n) { top + usable * (1f - (vis[it].d / displayedMax).coerceIn(0f, 1f)) }
            val yu = FloatArray(n) { top + usable * (1f - (vis[it].u / displayedMax).coerceIn(0f, 1f)) }

            buildSmoothPath(xs, yd, linePath)
            fillPath.set(linePath)
            fillPath.lineTo(xs[n - 1], h)
            fillPath.lineTo(xs[0], h)
            fillPath.close()
            canvas.drawPath(fillPath, fillPaint)
            // درخشش بدون BlurMaskFilter (که شتاب سخت‌افزاری را خاموش می‌کند)
            downGlow.color = 0xFF34C8F0.toInt()
            downGlow.alpha = 28; downGlow.strokeWidth = dp(7f); canvas.drawPath(linePath, downGlow)
            downGlow.alpha = 52; downGlow.strokeWidth = dp(4.2f); canvas.drawPath(linePath, downGlow)
            downLine.color = 0xFF8EE9FF.toInt()
            downLine.alpha = 255; downLine.strokeWidth = dp(2.0f)
            canvas.drawPath(linePath, downLine)

            buildSmoothPath(xs, yu, linePath)
            upLine.color = 0xFF9E7BFF.toInt()
            upLine.alpha = 46; upLine.strokeWidth = dp(4f); canvas.drawPath(linePath, upLine)
            upLine.alpha = 210; upLine.strokeWidth = dp(1.6f); canvas.drawPath(linePath, upLine)
        }

        // تا وقتی نمودار دیده می‌شود و داده دارد، هر فریم حرکت کن (۶۰ فریم، پیوسته)
        if (samples.isNotEmpty() && isShown) postInvalidateOnAnimation()
    }

    /** منحنی یکنوا (کنترل‌ها در وسط افقی): نرم، بدون بیرون‌زدگی زیر صفر یا بالای قله. */
    private fun buildSmoothPath(xs: FloatArray, ys: FloatArray, out: Path) {
        out.reset()
        out.moveTo(xs[0], ys[0])
        for (i in 1 until xs.size) {
            val mx = (xs[i - 1] + xs[i]) / 2f
            out.cubicTo(mx, ys[i - 1], mx, ys[i], xs[i], ys[i])
        }
    }
}
