package com.hermesvpn.app

import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import android.os.Bundle
import android.widget.ImageButton
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.google.zxing.BinaryBitmap
import com.google.zxing.MultiFormatReader
import com.google.zxing.RGBLuminanceSource
import com.google.zxing.common.HybridBinarizer
import com.journeyapps.barcodescanner.BarcodeCallback
import com.journeyapps.barcodescanner.BarcodeResult
import com.journeyapps.barcodescanner.DecoratedBarcodeView
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * صفحه‌ی اسکن اختصاصی — عمداً به‌جای CaptureActivity خودِ کتابخانه‌ی zxing
 * (که از android.app.Activity ساده ارث می‌بره، نه AppCompatActivity/
 * ComponentActivity، پس registerForActivityResult روش کار نمی‌کنه و نمی‌شد
 * دکمه‌ی گالری رو باهاش اضافه کرد). این‌جا مستقیم از DecoratedBarcodeView
 * (لایه‌ی پایین‌ترِ همون کتابخونه) استفاده می‌شه، هم برای اسکن زنده هم برای
 * خوندن QR از یه عکسِ انتخابی. نتیجه از هر دو راه یکسانه: EXTRA_RESULT.
 */
class QrScanActivity : AppCompatActivity() {

    private lateinit var barcodeView: DecoratedBarcodeView
    private var resultReturned = false

    private val galleryPicker = registerForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) decodeFromGallery(uri)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        applyScreenshotProtection(this)
        setContentView(R.layout.activity_qr_scan)

        barcodeView = findViewById(R.id.barcodeScanner)
        findViewById<ImageButton>(R.id.scanCloseButton).setOnClickListener { finish() }
        findViewById<ImageButton>(R.id.scanGalleryButton).setOnClickListener { galleryPicker.launch("image/*") }
    }

    override fun onResume() {
        super.onResume()
        resultReturned = false
        barcodeView.resume()
        barcodeView.decodeSingle(object : BarcodeCallback {
            override fun barcodeResult(result: BarcodeResult) {
                returnResult(result.text)
            }
        })
    }

    override fun onPause() {
        super.onPause()
        barcodeView.pause()
    }

    override fun onKeyDown(keyCode: Int, event: android.view.KeyEvent?): Boolean {
        if (barcodeView.onKeyDown(keyCode, event)) return true
        return super.onKeyDown(keyCode, event)
    }

    /** بهترین‌تلاش: عکسِ انتخابی رو در چند مقیاس امتحان می‌کنه چون QR توی
     * عکس‌های واقعی (نه اسکرین‌شاتِ تمیز) اغلب کوچیک/کج می‌افته و decode
     * مستقیم روی رزولوشن کامل شکست می‌خوره. */
    private fun decodeFromGallery(uri: Uri) {
        lifecycleScope.launch {
            val text = withContext(Dispatchers.IO) { decodeBitmapFromUri(uri) }
            if (text != null) {
                returnResult(text)
            } else {
                Toast.makeText(this@QrScanActivity, getString(R.string.add_config_no_qr_in_image), Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun decodeBitmapFromUri(uri: Uri): String? {
        val bitmap = try {
            contentResolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it) }
        } catch (e: Exception) {
            null
        } ?: return null
        return decodeBitmap(bitmap) ?: decodeBitmap(downscale(bitmap, 1200))
    }

    private fun downscale(bitmap: Bitmap, maxDim: Int): Bitmap {
        val ratio = maxDim.toFloat() / maxOf(bitmap.width, bitmap.height)
        if (ratio >= 1f) return bitmap
        return Bitmap.createScaledBitmap(bitmap, (bitmap.width * ratio).toInt(), (bitmap.height * ratio).toInt(), true)
    }

    private fun decodeBitmap(bitmap: Bitmap): String? {
        return try {
            val pixels = IntArray(bitmap.width * bitmap.height)
            bitmap.getPixels(pixels, 0, bitmap.width, 0, 0, bitmap.width, bitmap.height)
            val source = RGBLuminanceSource(bitmap.width, bitmap.height, pixels)
            val binaryBitmap = BinaryBitmap(HybridBinarizer(source))
            MultiFormatReader().decode(binaryBitmap).text
        } catch (e: Exception) {
            null
        }
    }

    private fun returnResult(text: String) {
        if (resultReturned) return
        resultReturned = true
        setResult(RESULT_OK, Intent().putExtra(EXTRA_RESULT, text))
        finish()
    }

    companion object {
        const val EXTRA_RESULT = "qr_text"
    }
}
