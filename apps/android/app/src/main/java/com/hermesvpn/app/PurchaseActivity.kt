package com.hermesvpn.app

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.net.Uri
import android.os.Bundle
import android.util.Base64
import android.view.LayoutInflater
import android.view.View
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.google.android.material.button.MaterialButton
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

/**
 * جریان خرید/تمدید داخل اپ: پلن → روش پرداخت → جزئیات (کارت/کریپتو/واکیف/
 * درگاه خودکار) → آپلود رسید یا انتظار تأیید خودکار. همون بک‌اندی که ربات
 * تلگرام استفاده می‌کنه (create_order/do_provision_order/process_receipt_for_order)،
 * فقط از راه API به‌جای پیام تلگرام.
 */
class PurchaseActivity : AppCompatActivity() {

    private lateinit var prefs: SharedPreferences
    private lateinit var username: String
    private lateinit var password: String

    private lateinit var plansContainer: LinearLayout
    private lateinit var plansLoadingText: TextView
    private lateinit var continueButton: MaterialButton
    private lateinit var methodSection: LinearLayout
    private lateinit var detailsSection: LinearLayout
    private lateinit var detailsText: TextView
    private lateinit var copyButton: MaterialButton
    private lateinit var openGatewayButton: MaterialButton
    private lateinit var uploadReceiptButton: MaterialButton
    private lateinit var checkStatusButton: MaterialButton
    private lateinit var statusResultText: TextView

    private var plans: List<JSONObject> = emptyList()
    private var selectedPlanKey: String? = null
    private var orderId: Int? = null
    private var gatewayCheckoutUrl: String? = null
    private var copyableText: String = ""

    private val receiptPicker = registerForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) uploadReceipt(uri)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        applyScreenshotProtection(this)
        setContentView(R.layout.activity_purchase)
        prefs = getSharedPreferences("hermesvpn_prefs", MODE_PRIVATE)
        username = prefs.getString("auth_username", "") ?: ""
        password = prefs.getString("auth_password", "") ?: ""

        findViewById<android.widget.ImageButton>(R.id.backButton).setOnClickListener { finish() }

        plansContainer = findViewById(R.id.plansContainer)
        plansLoadingText = findViewById(R.id.plansLoadingText)
        continueButton = findViewById(R.id.continueButton)
        methodSection = findViewById(R.id.methodSection)
        detailsSection = findViewById(R.id.detailsSection)
        detailsText = findViewById(R.id.detailsText)
        copyButton = findViewById(R.id.copyButton)
        openGatewayButton = findViewById(R.id.openGatewayButton)
        uploadReceiptButton = findViewById(R.id.uploadReceiptButton)
        checkStatusButton = findViewById(R.id.checkStatusButton)
        statusResultText = findViewById(R.id.statusResultText)

        if (username.isEmpty() || password.isEmpty()) {
            Toast.makeText(this, getString(R.string.error_no_link), Toast.LENGTH_LONG).show()
            startActivity(Intent(this, SettingsActivity::class.java))
            finish()
            return
        }

        loadPlans()

        continueButton.setOnClickListener { createOrder() }
        copyButton.setOnClickListener { copyToClipboard() }
        checkStatusButton.setOnClickListener { checkStatus() }
        openGatewayButton.setOnClickListener {
            gatewayCheckoutUrl?.let { url -> startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }
        }
        uploadReceiptButton.setOnClickListener { receiptPicker.launch("image/*") }

        findViewById<MaterialButton>(R.id.methodCardButton).setOnClickListener { chooseMethod("card") }
        findViewById<MaterialButton>(R.id.methodCryptoButton).setOnClickListener { chooseMethod("crypto") }
        findViewById<MaterialButton>(R.id.methodBankTryButton).setOnClickListener { chooseMethod("bank_try") }
        findViewById<MaterialButton>(R.id.methodGatewayButton).setOnClickListener { chooseMethod("card_gateway") }
    }

    private fun loadPlans() {
        lifecycleScope.launch {
            val resp = try {
                withContext(Dispatchers.IO) { PurchaseApi.getPlans() }
            } catch (e: Exception) {
                plansLoadingText.text = getString(R.string.purchase_error_generic)
                return@launch
            }
            if (!resp.ok) {
                plansLoadingText.text = getString(R.string.purchase_error_generic)
                return@launch
            }
            val arr = resp.json.optJSONArray("plans") ?: return@launch
            val list = mutableListOf<JSONObject>()
            for (i in 0 until arr.length()) list.add(arr.getJSONObject(i))
            plans = list
            plansLoadingText.visibility = View.GONE
            renderPlans()
        }
    }

    private fun renderPlans() {
        plansContainer.removeAllViews()
        val inflater = LayoutInflater.from(this)
        for (plan in plans) {
            val row = inflater.inflate(R.layout.item_plan_row, plansContainer, false)
            val radio = row.findViewById<RadioButton>(R.id.planRadio)
            row.findViewById<TextView>(R.id.planLabel).text = plan.optString("label")
            row.findViewById<TextView>(R.id.planPrice).text = getString(R.string.price_toman, "%,d".format(plan.optLong("price")))
            row.setOnClickListener {
                selectedPlanKey = plan.optString("key")
                for (i in 0 until plansContainer.childCount) {
                    plansContainer.getChildAt(i).findViewById<RadioButton>(R.id.planRadio).isChecked = false
                }
                radio.isChecked = true
                continueButton.visibility = View.VISIBLE
            }
            plansContainer.addView(row)
        }
    }

    private fun createOrder() {
        val planKey = selectedPlanKey ?: return
        continueButton.isEnabled = false
        lifecycleScope.launch {
            val resp = try {
                withContext(Dispatchers.IO) { PurchaseApi.createOrder(username, password, planKey) }
            } catch (e: Exception) {
                Toast.makeText(this@PurchaseActivity, getString(R.string.error_network), Toast.LENGTH_LONG).show()
                continueButton.isEnabled = true
                return@launch
            }
            continueButton.isEnabled = true
            if (!resp.ok) {
                Toast.makeText(this@PurchaseActivity, getString(R.string.purchase_error_generic), Toast.LENGTH_LONG).show()
                return@launch
            }
            orderId = resp.json.optInt("order_id")
            continueButton.visibility = View.GONE
            methodSection.visibility = View.VISIBLE
        }
    }

    private fun chooseMethod(method: String) {
        val id = orderId ?: return
        detailsSection.visibility = View.GONE
        lifecycleScope.launch {
            val resp = try {
                withContext(Dispatchers.IO) { PurchaseApi.chooseMethod(id, username, password, method) }
            } catch (e: Exception) {
                Toast.makeText(this@PurchaseActivity, getString(R.string.error_network), Toast.LENGTH_LONG).show()
                return@launch
            }
            if (!resp.ok) {
                val msg = if (resp.error == "gateway_not_configured")
                    getString(R.string.purchase_gateway_unavailable) else getString(R.string.purchase_error_generic)
                Toast.makeText(this@PurchaseActivity, msg, Toast.LENGTH_LONG).show()
                return@launch
            }
            showDetails(method, resp.json)
        }
    }

    private fun showDetails(method: String, json: JSONObject) {
        detailsSection.visibility = View.VISIBLE
        openGatewayButton.visibility = View.GONE
        uploadReceiptButton.visibility = View.GONE
        statusResultText.text = ""

        when (method) {
            "card" -> {
                val text = getString(R.string.purchase_card_details, json.optString("card_number"),
                        json.optString("card_holder"), "%,d".format(json.optLong("amount_toman")))
                detailsText.text = text
                copyableText = json.optString("card_number")
                uploadReceiptButton.visibility = View.VISIBLE
            }
            "bank_try" -> {
                val text = "Card: ${json.optString("card_number")}\n" +
                        "Holder: ${json.optString("card_holder")}\n" +
                        "Amount: ${json.optDouble("try_amount")} TRY"
                detailsText.text = text
                copyableText = json.optString("card_number")
                uploadReceiptButton.visibility = View.VISIBLE
            }
            "crypto" -> {
                val text = "Wallet (TRC20 / USDT):\n${json.optString("wallet_address")}\n\n" +
                        "Amount: ${json.optDouble("usdt_amount")} USDT\n\n" +
                        "⚠️ TRC20 network only."
                detailsText.text = text
                copyableText = json.optString("wallet_address")
            }
            "card_gateway" -> {
                gatewayCheckoutUrl = json.optString("checkout_url")
                detailsText.text = "Tap below to pay securely on the bank's own page."
                copyableText = gatewayCheckoutUrl ?: ""
                openGatewayButton.visibility = View.VISIBLE
            }
        }
    }

    private fun uploadReceipt(uri: Uri) {
        val id = orderId ?: return
        uploadReceiptButton.isEnabled = false
        uploadReceiptButton.text = getString(R.string.purchase_receipt_uploading)
        lifecycleScope.launch {
            val result = try {
                withContext(Dispatchers.IO) {
                    val bytes = contentResolver.openInputStream(uri)?.use { it.readBytes() }
                        ?: throw IllegalStateException("no bytes")
                    val b64 = Base64.encodeToString(bytes, Base64.NO_WRAP)
                    PurchaseApi.uploadReceipt(id, username, password, b64)
                }
            } catch (e: Exception) {
                null
            }
            uploadReceiptButton.isEnabled = true
            uploadReceiptButton.text = getString(R.string.purchase_upload_receipt)
            if (result == null || !result.ok) {
                Toast.makeText(this@PurchaseActivity, getString(R.string.purchase_error_generic), Toast.LENGTH_LONG).show()
                return@launch
            }
            statusResultText.text = result.json.optString("message")
        }
    }

    private fun checkStatus() {
        val id = orderId ?: return
        lifecycleScope.launch {
            val resp = try {
                withContext(Dispatchers.IO) { PurchaseApi.orderStatus(id, username, password) }
            } catch (e: Exception) {
                Toast.makeText(this@PurchaseActivity, getString(R.string.error_network), Toast.LENGTH_LONG).show()
                return@launch
            }
            if (!resp.ok) {
                statusResultText.text = getString(R.string.purchase_error_generic)
                return@launch
            }
            val status = resp.json.optString("status")
            statusResultText.text = when (status) {
                "approved" -> "✅ ${getString(R.string.login_success_toast)}"
                "pending_review" -> "⏳ …"
                else -> status
            }
            if (status == "approved") {
                android.os.Handler(mainLooper).postDelayed({ finish() }, 1500)
            }
        }
    }

    private fun copyToClipboard() {
        if (copyableText.isEmpty()) return
        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        cm.setPrimaryClip(ClipData.newPlainText("HermesVPN", copyableText))
        Toast.makeText(this, getString(R.string.purchase_copied_toast), Toast.LENGTH_SHORT).show()
    }
}
