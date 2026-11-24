import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val keystoreProps = Properties().apply {
    val f = rootProject.file("../keystore/keystore.properties")
    if (f.exists()) load(f.inputStream())
}

android {
    namespace = "com.hermesvpn.app"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.hermesvpn.app"
        minSdk = 26
        // اندروید وقتی نسخه‌ی هدفِ اپ بیش از یک نسخه از خودش عقب‌تر باشد به
        // کاربر هشدار «این اپ با نسخه‌ی جدید اندروید سازگار نیست» می‌دهد.
        // ۳۵ کافی است تا این هشدار روی اندروید ۱۶ هم نیاید.
        targetSdk = 35
        // هر انتشار باید versionCode/versionName تازه بگیرد؛ وقتی همه ۱ بودند،
        // از تنظیمات اندروید نمی‌شد فهمید نسخه‌ی جدید واقعاً نصب شده یا نه.
        versionCode = 32
        versionName = "0.7.0"
        buildConfigField("String", "BUILD_STAMP", "\"1001-1130\"")
    }

    // یک APK جدا برای هر معماری گوشی. هسته‌ی Xray حدود ۳۴ مگابایت برای *هر*
    // معماری است و نسخه‌ی یکجا ۱۶۰ مگابایت می‌شد — روی 4G ایران دانلودش
    // نیمه‌کاره می‌ماند و نسخه‌ی جدید عملاً به گوشی نمی‌رسید. x86/x86_64 فقط
    // برای شبیه‌ساز کامپیوترند و روی گوشی واقعی استفاده نمی‌شوند.
    splits {
        abi {
            isEnable = true
            reset()
            include("arm64-v8a", "armeabi-v7a")
            isUniversalApk = false
        }
    }

    signingConfigs {
        create("release") {
            if (keystoreProps.containsKey("storeFile")) {
                storeFile = rootProject.file(keystoreProps["storeFile"] as String)
                storePassword = keystoreProps["storePassword"] as String
                keyAlias = keystoreProps["keyAlias"] as String
                keyPassword = keystoreProps["keyPassword"] as String
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("release")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        viewBinding = true
        buildConfig = true
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.4")
    implementation("androidx.recyclerview:recyclerview:1.3.2")
    implementation("com.journeyapps:zxing-android-embedded:4.3.0")

    // هسته‌ی Xray کامپایل‌شده برای اندروید (خروجی gomobile bind از AndroidLibXrayLite)
    implementation(files("libs/libv2ray.aar"))
}
