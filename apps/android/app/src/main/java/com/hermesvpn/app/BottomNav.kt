package com.hermesvpn.app

import android.app.Activity
import android.content.Intent
import android.view.View
import android.widget.ImageView
import android.widget.TextView
import androidx.core.content.ContextCompat

/**
 * نوار تب پایین، مشترک بین سه صفحه. عمداً Fragment نشد: صفحه‌ی اصلی نقشه‌ی
 * زنده‌ی سنگینی داره که با چرخه‌ی عمر Activity تنظیم شده؛ به‌جاش هر تب یک
 * Activity می‌مونه و جابه‌جایی با REORDER_TO_FRONT انجام می‌شه تا پشته بالا
 * نره و وضعیت هر تب حفظ بشه.
 */
object BottomNav {

    const val TAB_HOME = 0
    const val TAB_CONFIGS = 1
    const val TAB_SETTINGS = 2

    fun bind(activity: Activity, root: View, current: Int) {
        val tabs = listOf(
            Triple(R.id.tabHome, R.id.tabHomeIcon, R.id.tabHomeLabel),
            Triple(R.id.tabConfigs, R.id.tabConfigsIcon, R.id.tabConfigsLabel),
            Triple(R.id.tabSettings, R.id.tabSettingsIcon, R.id.tabSettingsLabel),
        )
        val activeColor = ContextCompat.getColor(activity, R.color.primary)
        val inactiveColor = ContextCompat.getColor(activity, R.color.text_secondary)

        tabs.forEachIndexed { index, (containerId, iconId, labelId) ->
            val color = if (index == current) activeColor else inactiveColor
            root.findViewById<ImageView>(iconId).setColorFilter(color)
            root.findViewById<TextView>(labelId).setTextColor(color)
            root.findViewById<View>(containerId).setOnClickListener {
                if (index != current) navigate(activity, index)
            }
        }
    }

    private fun navigate(activity: Activity, target: Int) {
        val cls = when (target) {
            TAB_HOME -> MainActivity::class.java
            TAB_CONFIGS -> ConfigsActivity::class.java
            else -> SettingsActivity::class.java
        }
        activity.startActivity(
            Intent(activity, cls).apply {
                addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            }
        )
        activity.overridePendingTransition(0, 0)
    }
}
