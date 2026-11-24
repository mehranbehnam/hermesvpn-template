package com.hermesvpn.app

import android.content.Intent
import android.content.SharedPreferences
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import android.os.Bundle
import android.util.Base64
import android.widget.ImageView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.google.android.material.button.MaterialButton
import com.google.android.material.textfield.TextInputEditText
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class ProfileActivity : AppCompatActivity() {

    private lateinit var prefs: SharedPreferences
    private lateinit var username: String
    private lateinit var password: String

    private val avatarPicker = registerForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) uploadAvatar(uri)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        applyScreenshotProtection(this)
        setContentView(R.layout.activity_profile)
        prefs = getSharedPreferences("hermesvpn_prefs", MODE_PRIVATE)
        username = prefs.getString("auth_username", "")?.trim() ?: ""
        password = prefs.getString("auth_password", "") ?: ""

        findViewById<android.widget.ImageButton>(R.id.backButton).setOnClickListener { finish() }

        if (username.isEmpty()) {
            startActivity(Intent(this, AuthActivity::class.java))
            finish()
            return
        }

        findViewById<ImageView>(R.id.avatarImage).setOnClickListener { avatarPicker.launch("image/*") }
        findViewById<ImageView>(R.id.avatarEditBadge).setOnClickListener { avatarPicker.launch("image/*") }

        findViewById<MaterialButton>(R.id.saveProfileButton).setOnClickListener { saveDetails() }
        findViewById<MaterialButton>(R.id.changePasswordButton).setOnClickListener { changePassword() }
        findViewById<MaterialButton>(R.id.buyPlanButton).setOnClickListener {
            startActivity(Intent(this, PurchaseActivity::class.java))
        }
        findViewById<MaterialButton>(R.id.logoutButton).setOnClickListener {
            prefs.edit()
                .putString("auth_username", "")
                .putString("auth_password", "")
                .remove("profile_avatar")
                .apply()
            Toast.makeText(this, getString(R.string.logout_toast), Toast.LENGTH_SHORT).show()
            finish()
        }

        showCachedAvatar()
        findViewById<TextView>(R.id.profileUsernameText).text = username
        loadProfile()
    }

    private fun loadProfile() {
        lifecycleScope.launch {
            val response = withContext(Dispatchers.IO) { ProfileApi.fetch(username, password) }
            val profile = response.profile ?: return@launch
            findViewById<TextView>(R.id.profileUsernameText).text =
                profile.displayName.ifEmpty { profile.username }
            findViewById<TextView>(R.id.profileEmailText).text = profile.email
            findViewById<TextInputEditText>(R.id.displayNameInput).setText(profile.displayName)
            findViewById<TextInputEditText>(R.id.emailInput).setText(profile.email)

            findViewById<TextView>(R.id.profileExpiryText).text = if (profile.expiryTime > 0) {
                SimpleDateFormat("yyyy/MM/dd", Locale.US).format(Date(profile.expiryTime))
            } else {
                getString(R.string.subscription_unlimited)
            }
            findViewById<TextView>(R.id.profileTrafficText).text = if (profile.totalGb > 0) {
                "${profile.totalGb / (1024L * 1024L * 1024L)} GB"
            } else {
                getString(R.string.subscription_unlimited)
            }
            findViewById<TextView>(R.id.profileConfigCountText).text = profile.configCount.toString()

            if (profile.avatar.isNotEmpty()) {
                prefs.edit().putString("profile_avatar", profile.avatar).apply()
                showCachedAvatar()
            }
        }
    }

    private fun showCachedAvatar() {
        val b64 = prefs.getString("profile_avatar", "") ?: ""
        if (b64.isEmpty()) return
        val bitmap = try {
            val bytes = Base64.decode(b64, Base64.DEFAULT)
            BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
        } catch (e: Exception) {
            null
        } ?: return
        findViewById<ImageView>(R.id.avatarImage).apply {
            setPadding(0, 0, 0, 0)
            setImageBitmap(bitmap)
        }
    }

    private fun saveDetails() {
        val displayName = findViewById<TextInputEditText>(R.id.displayNameInput).text?.toString()?.trim() ?: ""
        val email = findViewById<TextInputEditText>(R.id.emailInput).text?.toString()?.trim() ?: ""
        if (email.isNotEmpty() && !android.util.Patterns.EMAIL_ADDRESS.matcher(email).matches()) {
            Toast.makeText(this, getString(R.string.error_bad_email), Toast.LENGTH_LONG).show()
            return
        }
        lifecycleScope.launch {
            val response = withContext(Dispatchers.IO) {
                ProfileApi.update(username, password, displayName = displayName, email = email)
            }
            Toast.makeText(
                this@ProfileActivity,
                getString(if (response.ok) R.string.saved_toast else R.string.error_network),
                Toast.LENGTH_SHORT
            ).show()
            if (response.ok) loadProfile()
        }
    }

    private fun changePassword() {
        val newPassword = findViewById<TextInputEditText>(R.id.newPasswordInput).text?.toString() ?: ""
        if (newPassword.length < 6) {
            Toast.makeText(this, getString(R.string.error_password_too_short), Toast.LENGTH_LONG).show()
            return
        }
        lifecycleScope.launch {
            val response = withContext(Dispatchers.IO) {
                ProfileApi.changePassword(username, password, newPassword)
            }
            if (!response.ok) {
                Toast.makeText(this@ProfileActivity, getString(R.string.error_network), Toast.LENGTH_LONG).show()
                return@launch
            }
            // نشست فعلی باید با رمز تازه ادامه پیدا کنه، وگرنه هر درخواست بعدی ۴۰۱ می‌گیره
            password = newPassword
            prefs.edit().putString("auth_password", newPassword).apply()
            findViewById<TextInputEditText>(R.id.newPasswordInput).setText("")
            Toast.makeText(this@ProfileActivity, getString(R.string.profile_password_changed), Toast.LENGTH_LONG).show()
        }
    }

    private fun uploadAvatar(uri: Uri) {
        lifecycleScope.launch {
            val b64 = withContext(Dispatchers.IO) { encodeScaledAvatar(uri) }
            if (b64 == null) {
                Toast.makeText(this@ProfileActivity, getString(R.string.profile_avatar_failed), Toast.LENGTH_LONG).show()
                return@launch
            }
            val response = withContext(Dispatchers.IO) {
                ProfileApi.update(username, password, avatar = b64)
            }
            if (!response.ok) {
                Toast.makeText(this@ProfileActivity, getString(R.string.error_network), Toast.LENGTH_LONG).show()
                return@launch
            }
            prefs.edit().putString("profile_avatar", b64).apply()
            showCachedAvatar()
            Toast.makeText(this@ProfileActivity, getString(R.string.saved_toast), Toast.LENGTH_SHORT).show()
        }
    }

    /** عکس انتخابی می‌تونه چند مگابایت باشه؛ قبل از فرستادن به ۲۵۶ پیکسل و
     * JPEG کیفیت ۸۰ کوچیک می‌شه تا هم ترافیک کم بشه هم زیر سقف سرور بمونه. */
    private fun encodeScaledAvatar(uri: Uri): String? {
        return try {
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            contentResolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, bounds) }

            var sample = 1
            while (bounds.outWidth / sample > AVATAR_SIZE * 2 || bounds.outHeight / sample > AVATAR_SIZE * 2) {
                sample *= 2
            }

            val options = BitmapFactory.Options().apply { inSampleSize = sample }
            val decoded = contentResolver.openInputStream(uri)?.use {
                BitmapFactory.decodeStream(it, null, options)
            } ?: return null

            val scaled = Bitmap.createScaledBitmap(decoded, AVATAR_SIZE, AVATAR_SIZE, true)
            val out = ByteArrayOutputStream()
            scaled.compress(Bitmap.CompressFormat.JPEG, 80, out)
            Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
        } catch (e: Exception) {
            null
        }
    }

    companion object {
        private const val AVATAR_SIZE = 256
    }
}
