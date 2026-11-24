package com.hermesvpn.app

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.ShapeDrawable
import android.graphics.drawable.shapes.OvalShape
import android.text.TextUtils
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView

/**
 * رفتار آپدیت دقیقاً مثل نسخه‌ی ویندوز:
 *  • نوار «نسخه‌ی جدید X آمد» بالای صفحه — برای هر نسخه فقط یک‌بار؛ هر لمسی (به‌روزرسانی
 *    بزند یا نه، یا ×) آن را برای همیشه می‌بندد.
 *  • بعد از آن: نقطه‌ی قرمز روی آیکون Settings (تب پایین و دکمه‌ی بالای صفحه‌ی اصلی) و یک
 *    کارت «نسخه‌ی جدید X آماده است» پایین تنظیمات که با زدنش نصب انجام می‌شود.
 */
object UpdateUi {
    private const val PREFS = "hermesvpn_prefs"
    private const val K_CODE = "update_avail_code"
    private const val K_NAME = "update_avail_name"
    private const val K_NOTES = "update_avail_notes"
    private const val K_DISMISSED = "update_banner_dismissed_code"
    private const val CARD_TAG = "hv_update_card"

    data class Avail(val code: Int, val name: String, val notes: String)

    private fun p(ctx: Context) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun save(ctx: Context, info: UpdateChecker.Info) {
        p(ctx).edit().putInt(K_CODE, info.versionCode).putString(K_NAME, info.versionName)
            .putString(K_NOTES, info.notes).apply()
    }

    /** نسخه‌ی تازه‌تر ثبت‌شده، یا null (بعد از نصب نسخه‌ی جدید خودبه‌خود پاک می‌شود). */
    fun available(ctx: Context): Avail? {
        val code = p(ctx).getInt(K_CODE, 0)
        if (code <= BuildConfig.VERSION_CODE) return null
        return Avail(code, p(ctx).getString(K_NAME, "") ?: "", p(ctx).getString(K_NOTES, "") ?: "")
    }

    private fun dp(ctx: Context, v: Float) = v * ctx.resources.displayMetrics.density

    private fun openUpdate(a: Activity) = a.startActivity(Intent(a, UpdateActivity::class.java))

    // ---------- نقطه‌ی قرمز ----------
    private fun setDot(v: View?, on: Boolean) {
        v ?: return
        if (v.width == 0) { v.post { setDot(v, on) }; return }
        v.overlay.clear()
        if (!on) return
        val s = v.width
        val r = (dp(v.context, 9f)).toInt()
        val dot = ShapeDrawable(OvalShape()).apply { paint.color = Color.parseColor("#FF3B30") }
        dot.setBounds(s - r, 0, s, r)
        v.overlay.add(dot)
    }

    fun applyBadges(a: Activity) {
        val on = available(a) != null
        setDot(a.findViewById<ImageView?>(R.id.tabSettingsIcon), on)
        setDot(a.findViewById<View?>(R.id.settingsButton), on)
    }

    // ---------- نوار بالای صفحه (یک‌بار برای هر نسخه) ----------
    fun maybeShowBanner(a: Activity) {
        val av = available(a) ?: return
        if (p(a).getInt(K_DISMISSED, 0) == av.code) return
        val root = a.window.decorView as ViewGroup
        if (root.findViewWithTag<View>("hv_update_banner") != null) return

        val bar = LinearLayout(a).apply {
            tag = "hv_update_banner"
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            val pad = dp(a, 14f).toInt()
            setPadding(pad, dp(a, 10f).toInt(), dp(a, 8f).toInt(), dp(a, 10f).toInt())
            background = GradientDrawable().apply {
                cornerRadius = dp(a, 18f)
                setColor(Color.parseColor("#EE0F2236"))
                setStroke(dp(a, 1f).toInt(), Color.parseColor("#5534C8F0"))
            }
            elevation = dp(a, 8f)
        }
        val dot = View(a).apply {
            background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(Color.parseColor("#34C8F0")) }
        }
        bar.addView(dot, LinearLayout.LayoutParams(dp(a, 8f).toInt(), dp(a, 8f).toInt()).apply { marginEnd = dp(a, 10f).toInt() })
        bar.addView(TextView(a).apply {
            text = a.getString(R.string.upd_banner, av.name)
            setTextColor(Color.WHITE); textSize = 14f
            maxLines = 1; ellipsize = TextUtils.TruncateAt.END
        }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        val btn = TextView(a).apply {
            text = a.getString(R.string.upd_banner_btn)
            setTextColor(Color.parseColor("#04121C")); textSize = 13f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            setPadding(dp(a, 14f).toInt(), dp(a, 7f).toInt(), dp(a, 14f).toInt(), dp(a, 7f).toInt())
            background = GradientDrawable().apply { cornerRadius = dp(a, 14f); setColor(Color.parseColor("#34C8F0")) }
        }
        bar.addView(btn)
        val x = TextView(a).apply {
            text = "×"; setTextColor(Color.parseColor("#9FB3C8")); textSize = 20f
            setPadding(dp(a, 12f).toInt(), 0, dp(a, 6f).toInt(), 0)
        }
        bar.addView(x)

        val top = androidx.core.view.ViewCompat.getRootWindowInsets(root)
            ?.getInsets(androidx.core.view.WindowInsetsCompat.Type.statusBars())?.top ?: 0
        val lp = FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.TOP).apply {
            val m = dp(a, 12f).toInt()
            setMargins(m, top + dp(a, 8f).toInt(), m, 0)
        }
        bar.translationY = -dp(a, 120f); bar.alpha = 0f
        root.addView(bar, lp)
        bar.animate().translationY(0f).alpha(1f).setDuration(350).start()

        // هر لمسی = دیگر برای این نسخه نشان داده نشود؛ دکمه‌ی به‌روزرسانی صفحه‌ی نصب را باز می‌کند
        fun close(update: Boolean) {
            p(a).edit().putInt(K_DISMISSED, av.code).apply()
            bar.animate().translationY(-dp(a, 120f)).alpha(0f).setDuration(300)
                .withEndAction { root.removeView(bar) }.start()
            if (update) openUpdate(a)
        }
        btn.setOnClickListener { close(true) }
        x.setOnClickListener { close(false) }
        bar.setOnClickListener { close(false) }
    }

    // ---------- کارت پایین تنظیمات ----------
    fun bindSettingsCard(a: Activity, list: ViewGroup) {
        list.findViewWithTag<View>(CARD_TAG)?.let { list.removeView(it) }
        val av = available(a) ?: return
        val card = LinearLayout(a).apply {
            tag = CARD_TAG
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            val pad = dp(a, 16f).toInt()
            setPadding(pad, pad, pad, pad)
            background = GradientDrawable().apply {
                cornerRadius = dp(a, 22f)
                setColor(Color.parseColor("#0F2236"))
                setStroke(dp(a, 1f).toInt(), Color.parseColor("#6634C8F0"))
            }
            isClickable = true
            setOnClickListener { openUpdate(a) }
        }
        card.addView(ImageView(a).apply {
            setImageResource(R.drawable.ic_info); setColorFilter(Color.parseColor("#34C8F0"))
        }, LinearLayout.LayoutParams(dp(a, 26f).toInt(), dp(a, 26f).toInt()).apply { marginEnd = dp(a, 14f).toInt() })
        val texts = LinearLayout(a).apply { orientation = LinearLayout.VERTICAL }
        texts.addView(TextView(a).apply {
            text = a.getString(R.string.upd_card_title, av.name); setTextColor(Color.WHITE); textSize = 16f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
        })
        texts.addView(TextView(a).apply {
            text = av.notes.ifBlank { a.getString(R.string.upd_card_sub, BuildConfig.VERSION_NAME) }
            setTextColor(Color.parseColor("#9FB3C8")); textSize = 13f; maxLines = 3; ellipsize = TextUtils.TruncateAt.END
        })
        card.addView(texts, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        card.addView(TextView(a).apply {
            text = a.getString(R.string.upd_banner_btn)
            setTextColor(Color.parseColor("#04121C")); textSize = 13f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            setPadding(dp(a, 14f).toInt(), dp(a, 8f).toInt(), dp(a, 14f).toInt(), dp(a, 8f).toInt())
            background = GradientDrawable().apply { cornerRadius = dp(a, 14f); setColor(Color.parseColor("#34C8F0")) }
            setOnClickListener { openUpdate(a) }
        }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { marginStart = dp(a, 10f).toInt() })
        list.addView(card, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            topMargin = dp(a, 20f).toInt()
        })
    }
}
