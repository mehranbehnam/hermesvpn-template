package com.hermesvpn.app

import android.Manifest
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.PopupMenu
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class ConfigsActivity : AppCompatActivity() {

    private lateinit var prefs: SharedPreferences
    private lateinit var adapter: ConfigsAdapter
    private lateinit var recycler: RecyclerView

    // آکاردئون: هر اکانت یک بخش جمع‌شونده؛ پیش‌فرض فقط بخشی که کانفیگ انتخاب‌شده در آن است باز است
    private val toggledGroups = mutableMapOf<String, Boolean>()
    private var expandedConfigId: String? = null
    private var showFavoritesOnly = false
    private var sortMode = SORT_PING

    private val qrScanner = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val contents = if (result.resultCode == RESULT_OK) {
            result.data?.getStringExtra(QrScanActivity.EXTRA_RESULT)
        } else null
        if (!contents.isNullOrEmpty()) {
            importLink(contents)
            // اسکن پشت‌سرهم: بعد از هر کد دوباره دوربین باز می‌شه تا چندتا
            // کانفیگ رو بدون رفت‌وبرگشت دستی اضافه کنه
            findViewById<View>(R.id.configsRecycler).postDelayed({ launchScanner() }, 900)
        }
    }

    /** هم دوربین (خودِ اسکن) هم گالری (دکمه‌ی «انتخاب از گالری» توی صفحه‌ی
     * اسکن zxing) نیاز به اجازه‌ی زمان‌اجرا دارن؛ بدون این، دکمه‌ی گالری
     * بی‌صدا کار نمی‌کرد. */
    private val requestScanPermissions = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { launchScanner() }

    private val v2boxFilePicker = registerForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) importV2boxFile(uri)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        applyScreenshotProtection(this)
        setContentView(R.layout.activity_configs)
        prefs = getSharedPreferences("hermesvpn_prefs", MODE_PRIVATE)
        sortMode = prefs.getString(KEY_SORT, SORT_PING) ?: SORT_PING

        recycler = findViewById(R.id.configsRecycler)
        recycler.layoutManager = LinearLayoutManager(this)
        adapter = ConfigsAdapter(
            onSelect = { entry ->
                val prevId = ConfigStore.selected(this)?.id
                ConfigStore.select(this, entry.id)
                // وصل هستیم و کانفیگ دیگری انتخاب شد → همان لحظه تونل روی کانفیگ جدید می‌رود (بدون قطع و وصل دستی)
                if (HermesVpnService.isRunning && prevId != entry.id) switchTunnelTo(entry)
                expandedConfigId = if (expandedConfigId == entry.id) null else entry.id
                render()
            },
            onRename = { showRenameDialog(it) },
            onShare = { shareConfig(it) },
            onDelete = { confirmDelete(it) },
            onFavorite = {
                ConfigStore.toggleFavorite(this, it.id)
                render()
            },
            onGroupPing = { group -> pingGroup(group) },
            onGroupToggle = { key ->
                toggledGroups[key] = !(toggledGroups[key] ?: groupOpenByDefault(key))
                render()
            },
            onGroupMenu = { group, anchor -> showGroupMenu(group, anchor) },
        )
        recycler.adapter = adapter

        findViewById<View>(R.id.addConfigButton).setOnClickListener { showAddMenu(it) }
        findViewById<View>(R.id.scanQrButton).setOnClickListener { ensureScanPermissionsThenLaunch() }
        findViewById<View>(R.id.configsSettingsButton).setOnClickListener {
            startActivity(Intent(this, SettingsActivity::class.java))
        }
        findViewById<View>(R.id.favoritesButton).setOnClickListener {
            showFavoritesOnly = !showFavoritesOnly
            (it as android.widget.ImageButton).setImageResource(
                if (showFavoritesOnly) R.drawable.ic_star else R.drawable.ic_star_outline
            )
            render()
        }
        findViewById<View>(R.id.pingAllButton).setOnClickListener { pingAll() }
        findViewById<View>(R.id.subChipButton).setOnClickListener { showAddSubscriptionDialog() }
        findViewById<TextView>(R.id.sortModeText).setOnClickListener { showSortMenu(it) }
        findViewById<View>(R.id.menuButton).setOnClickListener { showOverflowMenu(it) }

        BottomNav.bind(this, findViewById(R.id.bottomNav), BottomNav.TAB_CONFIGS)
    }

    override fun onResume() {
        super.onResume()
        UpdateUi.applyBadges(this)
        render()
    }

    private var lastGroupKeys: Set<String> = emptySet()
    private var lastSelectedKey: String? = null

    private fun groupKey(e: ConfigStore.Entry) =
        if (e.group == ConfigStore.GROUP_HERMES) "${ConfigStore.GROUP_HERMES}|${e.account ?: ""}" else e.group

    private fun groupOpenByDefault(key: String) = key == lastSelectedKey || lastGroupKeys.size <= 1

    private fun render() {
        val all = ConfigStore.all(this)
        val selectedId = ConfigStore.selectedId(this)
        val visible = if (showFavoritesOnly) all.filter { it.favorite } else all

        findViewById<View>(R.id.emptyText).visibility = if (visible.isEmpty()) View.VISIBLE else View.GONE

        val sorted = when (sortMode) {
            SORT_NAME -> visible.sortedBy { it.name.lowercase() }
            SORT_PING -> visible.sortedWith(compareBy {
                // بی‌پاسخ‌ها و تست‌نشده‌ها همیشه ته لیست
                when {
                    it.ping >= 0 -> it.ping
                    it.ping == ConfigStore.PING_UNKNOWN -> 1_000_000
                    else -> 2_000_000
                }
            })
            else -> visible
        }

        val rows = mutableListOf<ConfigsAdapter.Row>()
        val mainName = prefs.getString("auth_username", "")?.ifBlank { null } ?: ConfigStore.GROUP_HERMES
        val grouped = sorted.groupBy { groupKey(it) }
        lastGroupKeys = grouped.keys
        lastSelectedKey = sorted.firstOrNull { it.id == selectedId }?.let { groupKey(it) }
        for ((key, entries) in grouped) {
            val group = entries.first().group
            val acct = if (group == ConfigStore.GROUP_HERMES) (entries.first().account ?: mainName) else null
            val expanded = toggledGroups[key] ?: groupOpenByDefault(key)
            val best = entries.filter { it.ping >= 0 }.minByOrNull { it.ping }
            val hasSel = entries.any { it.id == selectedId }
            rows.add(
                ConfigsAdapter.Row.Group(
                    name = (if (acct != null) "👤 $acct" else group) + if (hasSel) " ●" else "",
                    count = entries.size,
                    expanded = expanded,
                    info = best?.let { getString(R.string.group_best_ping, it.ping) }
                        ?: getString(R.string.group_no_info),
                    key = key,
                    group = group,
                )
            )
            if (expanded) {
                entries.forEach {
                    val shown = if (acct != null && it.account != null) it.copy(name = it.name.removeSuffix(" · ${it.account}")) else it
                    rows.add(
                        ConfigsAdapter.Row.Config(shown, it.id == selectedId, it.id == expandedConfigId)
                    )
                }
            }
        }
        adapter.submit(rows)
        findViewById<TextView>(R.id.sortModeText).text = getString(
            if (sortMode == SORT_NAME) R.string.sort_by_name else R.string.sort_by_ping
        )
    }

    /** قبل از باز کردن اسکنر، دوربین و گالری رو چک می‌کنه — بدون این، دکمه‌ی
     * «انتخاب از گالری» توی صفحه‌ی اسکن بی‌صدا کار نمی‌کرد. */
    private fun ensureScanPermissionsThenLaunch() {
        val galleryPermission = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            Manifest.permission.READ_MEDIA_IMAGES
        } else {
            Manifest.permission.READ_EXTERNAL_STORAGE
        }
        val missing = listOf(Manifest.permission.CAMERA, galleryPermission).filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isEmpty()) {
            launchScanner()
        } else {
            requestScanPermissions.launch(missing.toTypedArray())
        }
    }

    private fun launchScanner() {
        qrScanner.launch(Intent(this, QrScanActivity::class.java))
    }

    /** منوی «+» — دقیقاً به سبک V2Box: هر راه افزودن کانفیگ یک ردیف جداست،
     * به‌جای یک دیالوگ عمومی. «افزودن کانفیگ دستی» زیرمنو داره (پروتکل رو
     * انتخاب می‌کنی، بعد فرم همون پروتکل باز می‌شه). */
    private fun showAddMenu(anchor: View) {
        PopupMenu(this, anchor).apply {
            menuInflater.inflate(R.menu.menu_add_config, menu)
            setOnMenuItemClickListener { item ->
                when (item.itemId) {
                    R.id.action_import_uri_clipboard -> importUriFromClipboard()
                    R.id.action_import_json_clipboard -> importJsonFromClipboard()
                    R.id.action_import_v2box_file -> v2boxFilePicker.launch("*/*")
                    R.id.action_add_subscription -> showAddSubscriptionDialog()
                    R.id.action_manual_vless -> showManualConfigDialog(ManualProtocol.VLESS)
                    R.id.action_manual_trojan -> showManualConfigDialog(ManualProtocol.TROJAN)
                    R.id.action_manual_shadowsocks -> showManualConfigDialog(ManualProtocol.SHADOWSOCKS)
                    R.id.action_manual_socks -> showManualConfigDialog(ManualProtocol.SOCKS)
                    R.id.action_update_all_subscriptions -> refreshAccountConfigs()
                }
                true
            }
            show()
        }
    }

    private fun clipboardText(): String {
        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        return cm.primaryClip?.getItemAt(0)?.text?.toString()?.trim() ?: ""
    }

    private fun importUriFromClipboard() {
        val text = clipboardText()
        if (text.isEmpty()) {
            Toast.makeText(this, getString(R.string.add_config_clipboard_empty), Toast.LENGTH_SHORT).show()
            return
        }
        importLink(text)
    }

    /** JSON کامل v2ray/xray (نه لینک اشتراک‌گذاری) — همون outbound اصلی رو
     * پیدا و به یک ورودی مستقل تبدیل می‌کنه. */
    private fun importJsonFromClipboard() {
        val text = clipboardText()
        if (text.isEmpty()) {
            Toast.makeText(this, getString(R.string.add_config_clipboard_empty), Toast.LENGTH_SHORT).show()
            return
        }
        val outbound = VlessConfig.extractPrimaryOutbound(text)
        if (outbound == null) {
            Toast.makeText(this, getString(R.string.add_config_not_json), Toast.LENGTH_LONG).show()
            return
        }
        addFromOutboundJson(outbound)
    }

    private fun addFromOutboundJson(outbound: org.json.JSONObject) {
        val protocol = outbound.optString("protocol", "config")
        val address = try {
            val vnext = outbound.optJSONObject("settings")?.optJSONArray("vnext")?.optJSONObject(0)
            val servers = outbound.optJSONObject("settings")?.optJSONArray("servers")?.optJSONObject(0)
            (vnext ?: servers)?.optString("address") ?: protocol
        } catch (e: Exception) {
            protocol
        }
        ConfigStore.addRawOutbound(
            this, name = "$protocol ($address)", protocol = protocol, address = address,
            outboundJson = outbound.toString(),
        )
        Toast.makeText(this, getString(R.string.add_config_json_added), Toast.LENGTH_SHORT).show()
        render()
    }

    /** فایل .v2box (یا هر export مشابه): اول امتحان می‌کنه یه کانفیگ کامل
     * v2ray/xray بخونه، اگه نبود دنبال لینک‌های share (vless/vmess/...) توی
     * متنش می‌گرده — همون‌طور که v2box خودش این فایل‌ها رو صادر می‌کنه. */
    private fun importV2boxFile(uri: Uri) {
        lifecycleScope.launch {
            val text = try {
                withContext(Dispatchers.IO) {
                    contentResolver.openInputStream(uri)?.use { it.readBytes() }?.decodeToString()
                }
            } catch (e: Exception) {
                null
            }
            if (text.isNullOrBlank()) {
                Toast.makeText(this@ConfigsActivity, getString(R.string.add_config_invalid), Toast.LENGTH_LONG).show()
                return@launch
            }
            if (Tunnels.normalize(text) != null) {
                importLink(text)
                return@launch
            }
            val outbound = VlessConfig.extractPrimaryOutbound(text)
            if (outbound != null) {
                addFromOutboundJson(outbound)
                return@launch
            }
            importLink(text)
        }
    }

    private fun switchTunnelTo(entry: ConfigStore.Entry) {
        val cfg = try {
            VlessConfig.muxEnabled = prefs.getBoolean("mux_enabled", false)
            if (entry.rawOutboundJson != null) VlessConfig.buildXrayConfigFromOutbound(entry.rawOutboundJson, 10808)
            else VlessConfig.buildXrayConfig(entry.uri, 10808)
        } catch (e: Exception) {
            Toast.makeText(this, "${getString(R.string.error_config)}: ${e.message}", Toast.LENGTH_LONG).show()
            return
        }
        startService(Intent(this, HermesVpnService::class.java).apply { action = HermesVpnService.ACTION_DISCONNECT })
        recycler.postDelayed({
            startForegroundService(Intent(this, HermesVpnService::class.java).apply {
                action = HermesVpnService.ACTION_CONNECT
                putExtra(HermesVpnService.EXTRA_XRAY_CONFIG, cfg)
            })
        }, 700)
    }

    private fun showAddSubscriptionDialog() {
        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 24, 48, 0)
        }
        // مثل ویندوز: توضیح کوتاه، اول لینک (اگر در کلیپ‌بورد لینک هست، خودش پر می‌شود)، بعد نام
        val clip = (getSystemService(CLIPBOARD_SERVICE) as android.content.ClipboardManager)
            .primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(this)?.toString()?.trim() ?: ""
        val hintView = TextView(this).apply {
            text = getString(R.string.sub_hint); textSize = 13f
            setTextColor(androidx.core.content.ContextCompat.getColor(this@ConfigsActivity, R.color.text_secondary))
            setPadding(0, 0, 0, 16)
        }
        val urlInput = EditText(this).apply {
            hint = getString(R.string.subscription_url_hint)
            textDirection = View.TEXT_DIRECTION_LTR
            if (LinkImporter.isHttpUrl(clip)) setText(clip)
        }
        val nameInput = EditText(this).apply { hint = getString(R.string.subscription_name_hint) }
        container.addView(hintView)
        container.addView(urlInput)
        container.addView(nameInput)
        AlertDialog.Builder(this)
            .setTitle(R.string.add_menu_subscription)
            .setView(container)
            .setPositiveButton(R.string.btn_save) { _, _ ->
                val url = urlInput.text?.toString()?.trim() ?: ""
                if (url.isEmpty()) return@setPositiveButton
                val name = nameInput.text?.toString()?.trim()?.ifEmpty { null } ?: "Subscription"
                // لینک ساب خودِ HermesVPN (…/sub/<id>) = اکانت: همه‌ی مسیرها + به‌روزرسانی خودکار، مثل نسخه‌ی ویندوز
                if (url.contains("/sub/") && url.contains("example.com")) {
                    val subId = url.substringAfter("/sub/").substringBefore("?").substringBefore("#").trim('/')
                    lifecycleScope.launch {
                        getSharedPreferences("hermesvpn_prefs", MODE_PRIVATE).edit().putString("sub_id", subId).apply()
                        val ok = AccountSync.refresh(this@ConfigsActivity)
                        Toast.makeText(this@ConfigsActivity,
                            getString(if (ok) R.string.subscription_added else R.string.error_not_found), Toast.LENGTH_LONG).show()
                        render()
                    }
                    return@setPositiveButton
                }
                lifecycleScope.launch {
                    val sub = SubscriptionStore.add(this@ConfigsActivity, name, url, autoUpdate = true)
                    val result = LinkImporter.importSubscription(this@ConfigsActivity, sub)
                    val message = if (result.added > 0) {
                        getString(R.string.subscription_added)
                    } else {
                        getString(R.string.add_config_invalid)
                    }
                    Toast.makeText(this@ConfigsActivity, message, Toast.LENGTH_LONG).show()
                    render()
                }
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private enum class ManualProtocol { VLESS, TROJAN, SHADOWSOCKS, SOCKS }

    /** فرم دستی هر پروتکل — یک لینک share-style می‌سازه و از همون مسیر
     * importLink عبورش می‌ده، پس هیچ منطق جداگانه‌ای برای ذخیره لازم نیست. */
    private fun showManualConfigDialog(protocol: ManualProtocol) {
        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 24, 48, 0)
        }
        val remarkInput = EditText(this).apply { hint = getString(R.string.manual_hint_remark) }
        val addressInput = EditText(this).apply { hint = getString(R.string.manual_hint_address) }
        val portInput = EditText(this).apply {
            hint = getString(R.string.manual_hint_port)
            inputType = android.text.InputType.TYPE_CLASS_NUMBER
        }
        container.addView(remarkInput)
        container.addView(addressInput)
        container.addView(portInput)

        val secretInput = EditText(this)
        val title: Int
        when (protocol) {
            ManualProtocol.VLESS -> {
                title = R.string.manual_config_title_vless
                secretInput.hint = getString(R.string.manual_hint_uuid)
            }
            ManualProtocol.TROJAN -> {
                title = R.string.manual_config_title_trojan
                secretInput.hint = getString(R.string.manual_hint_password)
            }
            ManualProtocol.SHADOWSOCKS -> {
                title = R.string.manual_config_title_shadowsocks
                secretInput.hint = getString(R.string.manual_hint_password)
            }
            ManualProtocol.SOCKS -> {
                title = R.string.manual_config_title_socks
                secretInput.hint = getString(R.string.manual_hint_password)
            }
        }
        container.addView(secretInput)

        val methodInput = EditText(this).apply { hint = getString(R.string.manual_hint_method) }
        val usernameInput = EditText(this).apply { hint = getString(R.string.manual_hint_username) }
        if (protocol == ManualProtocol.SHADOWSOCKS) container.addView(methodInput)
        if (protocol == ManualProtocol.SOCKS) container.addView(usernameInput)

        AlertDialog.Builder(this)
            .setTitle(title)
            .setView(container)
            .setPositiveButton(R.string.btn_save) { _, _ ->
                val address = addressInput.text?.toString()?.trim() ?: ""
                val port = portInput.text?.toString()?.trim() ?: ""
                val secret = secretInput.text?.toString()?.trim() ?: ""
                if (address.isEmpty() || port.isEmpty() || secret.isEmpty()) {
                    Toast.makeText(
                        this, getString(R.string.manual_config_missing_fields, secretInput.hint), Toast.LENGTH_LONG
                    ).show()
                    return@setPositiveButton
                }
                val remark = Uri.encode(remarkInput.text?.toString()?.trim()?.ifEmpty { null } ?: address)
                val uri = when (protocol) {
                    ManualProtocol.VLESS ->
                        "vless://$secret@$address:$port?encryption=none&security=none#$remark"
                    ManualProtocol.TROJAN ->
                        "trojan://${Uri.encode(secret)}@$address:$port#$remark"
                    ManualProtocol.SHADOWSOCKS -> {
                        val method = methodInput.text?.toString()?.trim()?.ifEmpty { "aes-256-gcm" } ?: "aes-256-gcm"
                        val userInfo = android.util.Base64.encodeToString(
                            "$method:$secret".toByteArray(), android.util.Base64.NO_WRAP or android.util.Base64.URL_SAFE
                        )
                        "ss://$userInfo@$address:$port#$remark"
                    }
                    ManualProtocol.SOCKS -> {
                        val user = usernameInput.text?.toString()?.trim()
                        val userInfo = if (!user.isNullOrEmpty()) {
                            android.util.Base64.encodeToString(
                                "$user:$secret".toByteArray(), android.util.Base64.NO_WRAP or android.util.Base64.URL_SAFE
                            )
                        } else null
                        if (userInfo != null) "socks://$userInfo@$address:$port#$remark"
                        else "socks://$address:$port#$remark"
                    }
                }
                importLink(uri)
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    /** لینک تکی، چندتایی، یا لینک اشتراک — همه از یک مسیر، بدون سقف تعداد. */
    private fun importLink(raw: String) {
        if (raw.isBlank()) return
        lifecycleScope.launch {
            val result = LinkImporter.import(this@ConfigsActivity, raw)
            val message = when {
                result.added > 0 && result.skipped > 0 ->
                    getString(R.string.add_config_added_skipped, result.added, result.skipped)
                result.added > 0 -> getString(R.string.add_config_added, result.added)
                result.error == "network" -> getString(R.string.error_network)
                result.error?.startsWith("tunnel:") == true -> tunnelErrorText(result.error.removePrefix("tunnel:"))
                result.skipped > 0 -> getString(R.string.add_config_all_duplicates)
                else -> getString(R.string.add_config_invalid)
            }
            Toast.makeText(this@ConfigsActivity, message, Toast.LENGTH_LONG).show()
            render()
        }
    }

    private fun tunnelErrorText(err: String): String = when {
        err.contains("not made by HermesVPN") -> getString(R.string.tunnel_err_foreign)
        err.contains("expired") -> getString(R.string.tunnel_err_expired)
        else -> getString(R.string.tunnel_err_invalid)
    }

    private fun showRenameDialog(entry: ConfigStore.Entry) {
        val input = EditText(this).apply {
            setText(entry.name)
            setPadding(48, 40, 48, 40)
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.action_rename)
            .setView(input)
            .setPositiveButton(R.string.btn_save) { _, _ ->
                val name = input.text?.toString()?.trim() ?: ""
                if (name.isNotEmpty()) {
                    ConfigStore.rename(this, entry.id, name)
                    render()
                }
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private fun shareConfig(entry: ConfigStore.Entry) {
        // مسیر اضطراری: لینک hvpn:// فقط شناسه است و فایل قفل‌دار هم نباید
        // دست‌به‌دست شود (هر مشتری فایل خودش را از پشتیبانی می‌گیرد).
        if (Tunnels.tunnelIdOf(entry.rawOutboundJson) != null) {
            Toast.makeText(this, getString(R.string.tunnel_no_share), Toast.LENGTH_LONG).show()
            return
        }
        startActivity(
            Intent.createChooser(
                Intent(Intent.ACTION_SEND).apply {
                    type = "text/plain"
                    putExtra(Intent.EXTRA_TEXT, entry.uri)
                },
                getString(R.string.action_share)
            )
        )
    }

    private fun confirmDelete(entry: ConfigStore.Entry) {
        AlertDialog.Builder(this)
            .setTitle(R.string.action_delete)
            .setMessage(getString(R.string.delete_config_confirm, entry.name))
            .setPositiveButton(R.string.action_delete) { _, _ ->
                ConfigStore.remove(this, entry.id)
                Tunnels.specFile(this, entry.id).delete()
                render()
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private fun pingAll() = pingEntries(ConfigStore.all(this))

    private fun pingGroup(group: String) =
        pingEntries(ConfigStore.all(this).filter { it.group == group })

    private fun pingEntries(entries: List<ConfigStore.Entry>) {
        if (entries.isEmpty()) return
        val pingAllText = findViewById<TextView>(R.id.pingAllButton)
        pingAllText.text = getString(R.string.configs_pinging)
        lifecycleScope.launch {
            val results = withContext(Dispatchers.IO) {
                entries.map { entry ->
                    async { entry.id to PingTester.measure(entry.uri) }
                }.awaitAll()
            }
            results.forEach { (id, ping) -> ConfigStore.setPing(this@ConfigsActivity, id, ping) }
            pingAllText.text = getString(R.string.configs_ping_all)
            render()
        }
    }

    private fun showSortMenu(anchor: View) {
        PopupMenu(this, anchor).apply {
            menu.add(0, 1, 0, getString(R.string.sort_by_ping))
            menu.add(0, 2, 1, getString(R.string.sort_by_name))
            setOnMenuItemClickListener { item ->
                sortMode = if (item.itemId == 1) SORT_PING else SORT_NAME
                prefs.edit().putString(KEY_SORT, sortMode).apply()
                render()
                true
            }
            show()
        }
    }

    private fun showGroupMenu(group: String, anchor: View) {
        PopupMenu(this, anchor).apply {
            menu.add(0, 1, 0, getString(R.string.configs_ping_all))
            if (group == ConfigStore.GROUP_HERMES) {
                menu.add(0, 2, 1, getString(R.string.subscription_update_now))
            } else {
                menu.add(0, 3, 1, getString(R.string.group_delete_all))
            }
            setOnMenuItemClickListener { item ->
                when (item.itemId) {
                    1 -> pingGroup(group)
                    2 -> refreshAccountConfigs()
                    3 -> confirmDeleteGroup(group)
                }
                true
            }
            show()
        }
    }

    private fun confirmDeleteGroup(group: String) {
        AlertDialog.Builder(this)
            .setTitle(R.string.group_delete_all)
            .setMessage(getString(R.string.group_delete_all_confirm, group))
            .setPositiveButton(R.string.action_delete) { _, _ ->
                ConfigStore.all(this).filter { it.group == group }
                    .forEach { ConfigStore.remove(this, it.id) }
                render()
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private fun showOverflowMenu(anchor: View) {
        PopupMenu(this, anchor).apply {
            menu.add(0, 1, 0, getString(R.string.subscription_update_now))
            menu.add(0, 2, 1, getString(R.string.configs_ping_all))
            menu.add(0, 3, 2, getString(R.string.subscription_settings))
            setOnMenuItemClickListener { item ->
                when (item.itemId) {
                    1 -> refreshAccountConfigs()
                    2 -> pingAll()
                    3 -> startActivity(Intent(this@ConfigsActivity, SubscriptionSettingsActivity::class.java))
                }
                true
            }
            show()
        }
    }

    /** هم لینک‌های حساب HermesVPN و هم هر لینک اشتراکی که کاربر اضافه کرده. */
    private fun refreshAccountConfigs() {
        lifecycleScope.launch {
            val accountOk = AccountSync.refresh(this@ConfigsActivity)
            val subs = LinkImporter.refreshAllSubscriptions(this@ConfigsActivity)
            val message = when {
                accountOk || subs.added > 0 -> getString(R.string.subscription_updated)
                subs.error == "no_subscriptions" && !accountOk -> getString(R.string.error_network)
                else -> getString(R.string.subscription_up_to_date)
            }
            Toast.makeText(this@ConfigsActivity, message, Toast.LENGTH_SHORT).show()
            render()
        }
    }

    companion object {
        private const val KEY_SORT = "configs_sort_mode"
        private const val SORT_PING = "ping"
        private const val SORT_NAME = "name"
    }
}
