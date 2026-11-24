# HermesVPN — الگوی کامل سرویس VPN

این ریپو **نسخه‌ی الگو**ی پروژه‌ی HermesVPN است: همه‌ی کد، بدون هیچ رمز، توکن، کلید، اطلاعات مشتری یا آدرس سرور واقعی.
آی‌پی‌ها با `203.0.113.x`، دامنه‌ها با `example.com` و آیدی ربات‌ها با `YourSalesBot`/`YourAdminBot`/`@YourChannel` جایگزین شده‌اند؛
بعد از کلون، این‌ها را با مقادیر خودتان عوض کنید (جست‌وجو: `example.com`، `203.0.113.`، `Your`).

## بخش‌ها
| پوشه | چیست |
|---|---|
| `server/bots/` | ربات فروش (خرید، پرداخت کارت‌به‌کارت با تأیید خودکار پیامک، تمدید، تست رایگان، دعوت، پشتیبانی هوشمند) و ربات مدیریت (تلگرام + بله، داشبورد، مدیریت کلاینت‌ها، سفارش‌ها، دستیار هوشمند با صدا) |
| `server/scripts/` | پاک‌سازی خودکار، مانیتورینگ، گزارش روزانه، شروع اشتراک از اولین اتصال، بکاپ، رفع کلی مشکل، تبلیغات و … |
| `server/systemd/` | سرویس‌ها و تایمرهای systemd برای همه‌ی موارد بالا |
| `apps/android/` | اپ اندروید HermesVPN Connect (Kotlin، هسته‌ی Xray، به‌روزرسانی داخل اپ، مسیرهای اضطراری) |
| `apps/windows/` | اپ ویندوز (Go + Wails، Xray با TUN) |
| `engine/tunnel/` | موتور مسیر اضطراری: تونل DNS (dnstt)، SSH با Payload/SNI، فایل کانفیگ قفل‌دار `.hvpn` |

## راه‌اندازی سرور (Ubuntu)
1. پنل **3x-ui** را نصب کنید (inbound اول: VLESS+WS+TLS پشت Cloudflare؛ دوم: VLESS Reality). مسیر دیتابیس: `/etc/x-ui/x-ui.db`.
2. فایل‌های `server/bots/*` و `server/scripts/*` را در `/usr/local/bin/` و `server/systemd/*` را در `/etc/systemd/system/` بگذارید.
3. فایل‌های محرمانه را طبق `.env.example` بسازید (مالک root، دسترسی 600).
4. در کد، `example.com`، `203.0.113.10` (آی‌پی سرور)، `/your-ws-path`، شناسه‌ی چت ادمین (`1000000001`) و آیدی ربات/کانال را با مقادیر خودتان عوض کنید.
5. `systemctl daemon-reload && systemctl enable --now hermes-bot hermes-customer-bot` و تایمرهای `hermesvpn-*`.

## ساخت اپ اندروید
- JDK 17 و Android SDK (compileSdk 36). هسته‌ی Xray: فایل `libv2ray.aar` از پروژه‌ی [AndroidLibXrayLite](https://github.com/2dust/AndroidLibXrayLite) را در `apps/android/app/libs/` بگذارید.
- موتور تونل: `cd engine/tunnel && GOOS=android GOARCH=arm64 CGO_ENABLED=0 go build -o ../../apps/android/app/src/main/jniLibs/arm64-v8a/libhermestunnel.so ./cmd/hermestunnel`
- keystore خودتان را بسازید و `keystore/keystore.properties` را پر کنید؛ بعد `gradle assembleRelease`.

## ساخت اپ ویندوز
- Go 1.23+ و [Wails v2](https://wails.io). فایل‌های `xray.exe`، `wintun.dll`، `geoip.dat`، `geosite.dat` را (gzip‌شده) در `apps/windows/resources/` بگذارید.
- `wails build -platform windows/amd64`

## موتور تونل و فایل قفل‌دار
- یک بار کلید بسازید (کلید خصوصی امضا + کلید رمزنگاری، فایل `engine/keys_embed.go`)؛ این فایل و پوشه‌ی `.keys` هرگز نباید در گیت بروند.
- `hvpn-seal` از یک JSON مسیر، فایل `.hvpn` امضاشده و رمزشده می‌سازد که فقط اپ شما بازش می‌کند.

## امنیت
`.gitignore` جلوی آپلود فایل‌های محرمانه را می‌گیرد. قبل از هر push، هیچ توکن، کلید، دیتابیس یا لاگی را اضافه نکنید.
