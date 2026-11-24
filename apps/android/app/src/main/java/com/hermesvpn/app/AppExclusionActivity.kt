package com.hermesvpn.app

import android.content.Intent
import android.content.SharedPreferences
import android.graphics.drawable.Drawable
import android.os.Bundle
import android.text.Editable
import android.text.TextWatcher
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ArrayAdapter
import android.widget.BaseAdapter
import android.widget.CheckBox
import android.widget.EditText
import android.widget.ImageView
import android.widget.ListView
import android.widget.Spinner
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity

class AppExclusionActivity : AppCompatActivity() {

    data class AppEntry(val packageName: String, val label: String, val icon: Drawable)

    private lateinit var prefs: SharedPreferences
    private lateinit var allApps: List<AppEntry>
    private lateinit var adapter: AppListAdapter
    private val selected = mutableSetOf<String>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        applyScreenshotProtection(this)
        setContentView(R.layout.activity_app_exclusion)
        prefs = getSharedPreferences("hermesvpn_prefs", MODE_PRIVATE)

        findViewById<android.widget.ImageButton>(R.id.backButton).setOnClickListener { finish() }

        // فقط اپ‌هایی که آیکون قابل‌بازکردن دارن (queryIntentActivities) — رو
        // Android 11+ روش درست و بدون نیاز به پرمیشن حساس QUERY_ALL_PACKAGES
        // برای دیدن لیست اپ‌های نصب‌شده همینه (باید <queries> هم توی مانیفست باشه).
        val pm = packageManager
        val launcherIntent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        allApps = pm.queryIntentActivities(launcherIntent, 0)
            .map { it.activityInfo.applicationInfo }
            .filter { it.packageName != packageName }
            .distinctBy { it.packageName }
            .map { AppEntry(it.packageName, pm.getApplicationLabel(it).toString(), pm.getApplicationIcon(it)) }
            .sortedBy { it.label.lowercase() }

        val mode = prefs.getString("tunnel_mode", "exclude") ?: "exclude"
        selected.addAll(prefs.getStringSet(if (mode == "include") "included_apps" else "excluded_apps", emptySet()) ?: emptySet())

        val modeSpinner = findViewById<Spinner>(R.id.tunnelModeSpinner)
        val modeOptions = listOf(getString(R.string.tunnel_mode_exclude), getString(R.string.tunnel_mode_include))
        modeSpinner.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, modeOptions)
        modeSpinner.setSelection(if (mode == "include") 1 else 0)
        modeSpinner.onItemSelectedListener = object : android.widget.AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: android.widget.AdapterView<*>?, view: View?, position: Int, id: Long) {
                val newMode = if (position == 1) "include" else "exclude"
                if (newMode != prefs.getString("tunnel_mode", "exclude")) {
                    prefs.edit().putString("tunnel_mode", newMode).apply()
                    selected.clear()
                    selected.addAll(prefs.getStringSet(if (newMode == "include") "included_apps" else "excluded_apps", emptySet()) ?: emptySet())
                    adapter.notifyDataSetChanged()
                }
            }
            override fun onNothingSelected(parent: android.widget.AdapterView<*>?) {}
        }

        adapter = AppListAdapter(allApps)
        val listView = findViewById<ListView>(R.id.appListView)
        listView.adapter = adapter
        listView.setOnItemClickListener { _, _, position, _ ->
            val pkg = adapter.getItem(position).packageName
            if (!selected.remove(pkg)) selected.add(pkg)
            saveSelection()
            adapter.notifyDataSetChanged()
        }

        findViewById<EditText>(R.id.searchInput).addTextChangedListener(object : TextWatcher {
            override fun afterTextChanged(s: Editable?) {
                adapter.filter(s?.toString() ?: "")
            }
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
        })
    }

    private fun saveSelection() {
        val mode = prefs.getString("tunnel_mode", "exclude") ?: "exclude"
        prefs.edit().putStringSet(if (mode == "include") "included_apps" else "excluded_apps", selected.toSet()).apply()
    }

    private inner class AppListAdapter(all: List<AppEntry>) : BaseAdapter() {
        private var full = all
        private var shown = all

        fun filter(query: String) {
            shown = if (query.isBlank()) full
                    else full.filter { it.label.contains(query, ignoreCase = true) }
            notifyDataSetChanged()
        }

        override fun getCount() = shown.size
        override fun getItem(position: Int) = shown[position]
        override fun getItemId(position: Int) = position.toLong()

        override fun getView(position: Int, convertView: View?, parent: ViewGroup): View {
            val view = convertView ?: LayoutInflater.from(this@AppExclusionActivity)
                .inflate(R.layout.item_app_row, parent, false)
            val entry = shown[position]
            view.findViewById<ImageView>(R.id.appIcon).setImageDrawable(entry.icon)
            view.findViewById<TextView>(R.id.appLabel).text = entry.label
            view.findViewById<CheckBox>(R.id.appCheck).isChecked = selected.contains(entry.packageName)
            return view
        }
    }
}
