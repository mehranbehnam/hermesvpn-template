#!/usr/bin/env python3
"""
HermesVPN customer-facing sales bot. Separate Telegram bot/token from the
admin bot (hermes-bot.py) — customers place orders and self-serve reconnect
help here; only the admin bot can do destructive/infra operations.

Payment: card-to-card with manual admin approval (MVP). Designed so a
gateway (Zarinpal) or crypto can be added later as another PAYMENT_METHOD
without restructuring the order flow.

Data model: this bot keeps its own small sqlite db (ORDERS_DB) for orders —
separate from x-ui's database — and links a Telegram user to their VPN
client via the `clients.tg_id` column in the shared x-ui database (X_UI_DB),
so "reconnect me" requests can find the right account without asking for a
name.
"""
import sqlite3, json, time, uuid, random, string, subprocess, os, re, threading, ssl, io, html, hashlib, base64
import iyzipay
import urllib.request, urllib.parse
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytesseract
from PIL import Image
import sys as _sys
_sys.path.insert(0, "/usr/local/bin")
import hermesvpn_ordinfo as ordinfo  # «مبلغ از کجا آمد» + لاگ دکمه‌های مشتری (2026-11-11)


def esc(s):
    return html.escape(str(s))


DIVIDER_C = "――――――――――――――――"

BOT_TOKEN_FILE = "/root/.hermes-customer-bot-token"
PAID_WEBHOOK_SECRET_FILE = "/root/.hermes-paid-webhook-secret"
PAID_WEBHOOK_PORT = 2087
WEBHOOK_DOMAIN = "hook.example.com"
WEBHOOK_CERT = f"/etc/letsencrypt/live/{WEBHOOK_DOMAIN}/fullchain.pem"
WEBHOOK_KEY = f"/etc/letsencrypt/live/{WEBHOOK_DOMAIN}/privkey.pem"
ADMIN_CHAT_ID = 1000000001
X_UI_DB = "/etc/x-ui/x-ui.db"
ORDERS_DB = "/root/.hermes-orders.db"
QR_DIR = "/root/client-qr"
ORIGIN_IP = "203.0.113.10"  # Reality inbound connects directly here, bypassing Cloudflare
# IPهای جایگزینِ کلادفلر برای Main: همان سرویس از رنج‌های دیگرِ کلادفلر که بعضی اپراتورهای ایران کمتر خفه‌شان
# می‌کنند (تست ۲۰۲۶-۱۰-۲۹ روی همراه اول تا ۵ برابر Main). فقط گزینه‌ی اضافه‌اند؛ لینک Main اصلی دست نمی‌خورد.
MAIN_ALT_IPS = ["188.114.96.3", "162.159.135.1", "190.93.244.1"]
# XHTTP over Cloudflare (inbound 3, port 2083) — for TCI/Mokhaberat lines. Only handed to our own apps that
# can build an xhttp config: Windows (all versions) and Android 0.6.7+ (they send User-Agent HermesVPN-*).
XHTTP_INBOUND, XHTTP_PORT = 3, 2083
REQ_CTX = threading.local()          # per-request User-Agent of the app HTTP API


def client_supports_xhttp():
    ua = getattr(REQ_CTX, "ua", "") or ""
    return ua.startswith("HermesVPN-Windows/") or ua.startswith("HermesVPN-Android/")
OFFSET_FILE = "/root/.hermes-customer-bot-offset"

CARD_NUMBER = "6037-7012-1446-8545"
CARD_HOLDER = "پرهام حیدری"

# پرداخت کریپتو (USDT روی شبکه‌ی ترون/TRC20) — جایگزین/کنار کارت‌به‌کارت.
USDT_WALLET_ADDRESS = "TVQBixxQXs65mtx2F1MGoRjwda2tYBeqXE"
USDT_TRC20_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"  # قرارداد رسمی USDT روی ترون
# نرخ تومان به ازای هر ۱ USDT — نوسان داره، باید دستی و دوره‌ای آپدیت بشه.
USDT_TOMAN_RATE = 950_000

# درگاه کارتی واقعی (iyzico، تسویه به حساب VakıfBank). فایل کانفیگ عمداً
# جدا از کده — همون الگوی توکن‌های ربات و رمز وب‌هوک — تا ساخت/عوض‌کردن کلید
# نیاز به دیپلوی کد نداشته باشه. تا وقتی این فایل ساخته نشده یا placeholder
# داره، این روش پرداخت غیرفعال می‌مونه (iyzico_configured() چک می‌کنه).
IYZICO_CONFIG_FILE = "/root/.hermes-iyzico-config.json"
TRY_TOMAN_RATE = 21_000  # نرخ تومان به ازای هر ۱ لیر ترکیه — نوسان داره، دستی آپدیت بشه.
IYZICO_CALLBACK_URL = f"https://{WEBHOOK_DOMAIN}:{PAID_WEBHOOK_PORT}/app/iyzico/callback"

# واریز کارت‌به‌کارت به حساب ترکیه (VakıfBank) + تشخیص خودکار با پیامک —
# راه‌حل موقت تا وقتی حساب iyzico تأیید بشه، دقیقاً همون الگوی وب‌هوک
# پیامک بانک ایرانی (MacroDroid روی گوشی ترکیه، نه ایران).
VAKIF_CARD_NUMBER = "4836 1214 6069 6681"
VAKIF_CARD_HOLDER = "—"  # ⚠️ اسم واقعی روی کارت رو اینجا بذار — از عکس کارت قابل‌خوندن نبود
VAKIF_WEBHOOK_SECRET_FILE = "/root/.hermes-vakif-webhook-secret"

FORCE_JOIN_CHANNEL = "@YourChannel"
FORCE_JOIN_URL = "https://t.me/hermevpn"

PLANS = [
    {"key": "20gb", "label": "20 گیگ / 30 روز", "gb": 20, "days": 30, "quantity": 1, "price": 60000},
    {"key": "50gb", "label": "50 گیگ / 30 روز", "gb": 50, "days": 30, "quantity": 1, "price": 100000},
    {"key": "100gb", "label": "100 گیگ / 30 روز", "gb": 100, "days": 30, "quantity": 1, "price": 150000},
    {"key": "unlimited", "label": "نامحدود / 30 روز", "gb": 0, "days": 30, "quantity": 1, "price": 250000},
]

# Fixed wholesale bundles — priced as a discounted package, NOT quantity*single_price.
BULK_PACKAGES = [
    {"key": "bulk10_20gb", "label": "بسته 10تایی — 20 گیگ / 30 روز", "gb": 20, "days": 30, "quantity": 10, "price": 450000},
    {"key": "bulk10_50gb", "label": "بسته 10تایی — 50 گیگ / 30 روز", "gb": 50, "days": 30, "quantity": 10, "price": 800000},
    {"key": "bulk10_100gb", "label": "بسته 10تایی — 100 گیگ / 30 روز", "gb": 100, "days": 30, "quantity": 10, "price": 1200000},
    {"key": "bulk10_unlimited", "label": "بسته 10تایی — نامحدود / 30 روز", "gb": 0, "days": 30, "quantity": 10, "price": 2000000},
]

PLAN_BY_KEY = {p["key"]: p for p in PLANS + BULK_PACKAGES}

with open(BOT_TOKEN_FILE) as f:
    BOT_TOKEN = f.read().strip()
API = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ---------- telegram api ----------

POLL_TIMEOUT = 30


def api_call(method, data=None, files=None, timeout=25):
    url = f"{API}/{method}"
    payload = dict(data or {})
    for k, v in list(payload.items()):
        if isinstance(v, (dict, list)):
            payload[k] = json.dumps(v)
    if files:
        boundary = "----HermesCustBoundary"
        body = b""
        for k, v in payload.items():
            body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
        for k, (fname, fdata) in files.items():
            body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fname}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode()
            body += fdata
            body += b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    else:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(payload).encode())
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _hist_bot(chat_id, text):
    """هر چه ربات به مشتری می‌گوید در سابقه‌ی همان مشتری (direction='bot'); ادمین مستثنی."""
    try:
        if isinstance(chat_id, int) and chat_id > 0 and chat_id != ADMIN_CHAT_ID and text:
            log_support_message(chat_id, "bot", re.sub(r"<[^>]+>", "", str(text))[:1500])
    except Exception:
        pass


def send_message(chat_id, text, reply_markup=None, parse_mode=None):
    _hist_bot(chat_id, text)
    data = {"chat_id": chat_id, "text": text}
    # پیام ساده‌ی بدون دکمه در چت خصوصی همیشه منوی اصلی را هم می‌آورد، تا منو هیچ‌وقت
    # غیب نماند (مالک: «منو مدام غیب می‌شود و باید /start بزنم»).
    if reply_markup is None and isinstance(chat_id, int) and chat_id > 0:
        try:
            reply_markup = main_menu(chat_id)
        except Exception:
            reply_markup = None
    if reply_markup is not None:
        data["reply_markup"] = reply_markup
    if parse_mode:
        data["parse_mode"] = parse_mode
    api_call("sendMessage", data)


def edit_message(chat_id, message_id, text, reply_markup=None, parse_mode=None):
    _hist_bot(chat_id, text)
    data = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup is not None:
        data["reply_markup"] = reply_markup
    if parse_mode:
        data["parse_mode"] = parse_mode
    try:
        api_call("editMessageText", data)
    except Exception:
        pass


def answer_callback(callback_id, text=None):
    try:
        api_call("answerCallbackQuery", {"callback_query_id": callback_id, **({"text": text} if text else {})})
    except Exception:
        pass


def send_photo(chat_id, path_or_file_id, caption=None, reply_markup=None, is_file_id=False,
               parse_mode=None):
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
    if parse_mode:
        data["parse_mode"] = parse_mode
    if reply_markup is None and isinstance(chat_id, int) and chat_id > 0:
        try:
            reply_markup = main_menu(chat_id)      # عکس هم منو را همراه بیاورد، مثل send_message
        except Exception:
            reply_markup = None
    if reply_markup is not None:
        data["reply_markup"] = reply_markup
    if is_file_id:
        data["photo"] = path_or_file_id
        api_call("sendPhoto", data)
    else:
        with open(path_or_file_id, "rb") as f:
            api_call("sendPhoto", data, files={"photo": (os.path.basename(path_or_file_id), f.read())})


def forward_message(chat_id, from_chat_id, message_id):
    api_call("forwardMessage", {"chat_id": chat_id, "from_chat_id": from_chat_id, "message_id": message_id})


def is_channel_member(tg_id):
    """Requires the bot to be an admin (or member) of FORCE_JOIN_CHANNEL."""
    try:
        resp = api_call("getChatMember", {"chat_id": FORCE_JOIN_CHANNEL, "user_id": tg_id})
        status = resp.get("result", {}).get("status", "left")
        return status in ("member", "administrator", "creator")
    except Exception as e:
        print("membership check failed:", e)
        return True  # fail-open: a broken check must never lock out real customers


def join_gate_keyboard():
    return inline([
        [{"text": "🔗 عضویت در کانال", "url": FORCE_JOIN_URL}],
        [{"text": "✅ عضو شدم، چک کن", "callback_data": "checkjoin"}],
    ])


def randid(n=16):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def inline(rows):
    return {"inline_keyboard": rows}


# ---------- orders db (separate from x-ui) ----------

def orders_db():
    c = sqlite3.connect(ORDERS_DB)
    c.execute("""CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id INTEGER, tg_username TEXT, tg_name TEXT,
        plan_key TEXT, status TEXT, receipt_file_id TEXT,
        created_at INTEGER, decided_at INTEGER,
        quantity INTEGER DEFAULT 1, total_price INTEGER
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        tg_id INTEGER PRIMARY KEY, display_name TEXT, created_at INTEGER
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value TEXT
    )""")
    # migration for DBs created before quantity/total_price existed
    cols = [r[1] for r in c.execute("PRAGMA table_info(orders)").fetchall()]
    if "quantity" not in cols:
        c.execute("ALTER TABLE orders ADD COLUMN quantity INTEGER DEFAULT 1")
    if "total_price" not in cols:
        c.execute("ALTER TABLE orders ADD COLUMN total_price INTEGER")
    if "target_email" not in cols:
        c.execute("ALTER TABLE orders ADD COLUMN target_email TEXT")
    # receipt OCR: raw text always kept (manual fallback) + best-effort parsed fields
    if "receipt_ocr_text" not in cols:
        c.execute("ALTER TABLE orders ADD COLUMN receipt_ocr_text TEXT")
    if "receipt_amount" not in cols:
        c.execute("ALTER TABLE orders ADD COLUMN receipt_amount INTEGER")
    if "receipt_tracking" not in cols:
        c.execute("ALTER TABLE orders ADD COLUMN receipt_tracking TEXT")
    if "receipt_date" not in cols:
        c.execute("ALTER TABLE orders ADD COLUMN receipt_date TEXT")
    if "receipt_name" not in cols:
        c.execute("ALTER TABLE orders ADD COLUMN receipt_name TEXT")
    if "receipt_ocr_corrected" not in cols:
        c.execute("ALTER TABLE orders ADD COLUMN receipt_ocr_corrected INTEGER DEFAULT 0")
    if "client_email" not in cols:
        c.execute("ALTER TABLE orders ADD COLUMN client_email TEXT")
    if "base_price" not in cols:
        c.execute("ALTER TABLE orders ADD COLUMN base_price INTEGER")
    if "usdt_amount" not in cols:
        # فقط برای سفارش‌هایی که روش پرداخت‌شون کریپتوعه پر می‌شه؛ بقیه NULL می‌مونن.
        c.execute("ALTER TABLE orders ADD COLUMN usdt_amount REAL")
    if "usdt_tx_hash" not in cols:
        c.execute("ALTER TABLE orders ADD COLUMN usdt_tx_hash TEXT")
    if "iyzico_token" not in cols:
        c.execute("ALTER TABLE orders ADD COLUMN iyzico_token TEXT")
    if "try_amount" not in cols:
        c.execute("ALTER TABLE orders ADD COLUMN try_amount REAL")
    # Customer-chosen nicknames for their accounts. Deliberately kept OUT of
    # x-ui: the real client email stays the stable identity that the server,
    # the panel and the traffic stats key on, while this is purely a display
    # label the customer can rename freely (a reseller labels each account
    # with their own buyer's name).
    c.execute("""CREATE TABLE IF NOT EXISTS client_labels (
        email TEXT PRIMARY KEY, label TEXT, updated_at INTEGER
    )""")
    # لاگین اپ موبایل. یک ردیف به‌ازای هر کلاینت (email همچنان PK و همون
    # چیزیه که ensure_app_credentials موقع پرداخت خودکار می‌سازه)، ولی حالا
    # یه username دلخواه هم داره که خودِ مشتری از توی ربات انتخاب می‌کنه
    # (می‌تونه با email یکی نباشه) — یکتاییِ username رو کدِ پایتون چک می‌کنه،
    # نه دیتابیس. فقط هش پسورد نگه داشته می‌شه.
    c.execute("""CREATE TABLE IF NOT EXISTS app_credentials (
        email TEXT PRIMARY KEY, username TEXT, salt TEXT, password_hash TEXT, created_at INTEGER
    )""")
    cred_cols = [r[1] for r in c.execute("PRAGMA table_info(app_credentials)").fetchall()]
    if "username" not in cred_cols:
        c.execute("ALTER TABLE app_credentials ADD COLUMN username TEXT")
        c.execute("UPDATE app_credentials SET username = email WHERE username IS NULL")
    if "contact_email" not in cred_cols:
        c.execute("ALTER TABLE app_credentials ADD COLUMN contact_email TEXT")
    if "display_name" not in cred_cols:
        c.execute("ALTER TABLE app_credentials ADD COLUMN display_name TEXT")
    if "avatar" not in cred_cols:
        # عکس پروفایل به‌صورت base64 — عمداً توی همین ردیف، نه فایل جدا:
        # اپ قبل از فرستادن کوچیکش می‌کنه، پس چند ده کیلوبایته و ارزش
        # نگه‌داری یه مسیر فایل و سرو کردنش رو نداره.
        c.execute("ALTER TABLE app_credentials ADD COLUMN avatar TEXT")
    # توکن «اتصال مستقیم به نرم‌افزار». برای هر اکانت جداست — پس کسی که
    # ۱۰ تا اکانت خریده، زیر هر اکانت دکمه‌ی خودش رو می‌بینه و هر کدوم رو
    # روی گوشیِ خودش باز می‌کنه. عمداً چندبارمصرفه (تا انقضا): همون پیامی که
    # این دکمه توشه، لینک vless و QR رو هم به‌صورت متن ساده داره، پس توکن
    # چیزی به سطح افشا اضافه نمی‌کنه — و این‌جوری اگه کاربر اول اپ رو نصب کنه
    # و بعد برگرده، دکمه هنوز کار می‌کنه.
    c.execute("""CREATE TABLE IF NOT EXISTS app_link_tokens (
        token TEXT PRIMARY KEY, email TEXT, created_at INTEGER,
        last_used_at INTEGER, use_count INTEGER DEFAULT 0
    )""")
    return c


def set_order_client_email(order_id, email_or_list):
    """Records which x-ui client account(s) an order actually provisioned —
    the one durable, unambiguous identifier for 'cut this account off' if a
    payment turns out to be a problem (comma-separated for bulk orders)."""
    value = email_or_list if isinstance(email_or_list, str) else ",".join(email_or_list)
    c = orders_db()
    c.execute("UPDATE orders SET client_email=? WHERE id=?", (value, order_id))
    c.commit()
    c.close()


def get_setting(key, default=None):
    c = orders_db()
    cur = c.cursor()
    cur.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cur.fetchone()
    c.close()
    return row[0] if row else default


def set_setting(key, value):
    c = orders_db()
    c.execute("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (key, value))
    c.commit()
    c.close()


def auto_approve_enabled():
    return get_setting("auto_approve", "1") == "1"


def upsert_user(tg_id, display_name):
    c = orders_db()
    c.execute("INSERT INTO users (tg_id, display_name, created_at) VALUES (?,?,?) "
              "ON CONFLICT(tg_id) DO UPDATE SET display_name=excluded.display_name",
              (tg_id, display_name, int(time.time() * 1000)))
    c.commit()
    c.close()


PRICE_OFFSET_MAX = 200
AMOUNT_RESERVED_WINDOW_MS = 6 * 3600 * 1000


def unique_total_price(base_price):
    """A card-to-card payment can only be matched back to an order by its
    amount, so two in-flight orders at the same price are ambiguous — the
    bank SMS for one of them matches whichever order is older and credits
    the wrong customer. That actually happened (a payment at 20:01 landed on
    a 9-hour-old order instead of the one just placed), so every order gets
    a small unique offset added to its price, making each in-flight amount
    distinct. Amounts of recently-decided orders stay reserved for a few
    hours too, in case their bank SMS arrives late."""
    c = orders_db()
    cur = c.cursor()
    cutoff = int(time.time() * 1000) - AMOUNT_RESERVED_WINDOW_MS
    cur.execute("""SELECT total_price FROM orders
                   WHERE total_price IS NOT NULL
                     AND (status IN ('awaiting_receipt','pending_review')
                          OR (decided_at IS NOT NULL AND decided_at >= ?))""", (cutoff,))
    taken = {r[0] for r in cur.fetchall()}
    c.close()
    # Offsets start at 1, never 0: the round base price is deliberately never
    # assigned to anyone. If a customer rounds their payment down out of
    # habit (pays 60,000 when their order says 60,003), it then matches NO
    # order at all and the admin gets told — instead of silently crediting
    # whoever happened to hold the round amount.
    for offset in range(1, PRICE_OFFSET_MAX):
        if base_price + offset not in taken:
            return base_price + offset
    # Every slot taken (would need 200 concurrent orders at one price) —
    # fall back to the base price; matching goes ambiguous again for this
    # one order, and the daily audit is what catches it.
    print(f"[order] WARNING: no unique amount slot free for base {base_price}", flush=True)
    return base_price


def create_order(tg_id, tg_username, tg_name, plan_key, quantity, base_price, target_email=None):
    """Returns (order_id, payable) — `payable` is the plan price plus the
    small uniqueness offset, and is the amount the customer must actually
    transfer for the bank SMS to match this specific order."""
    payable = unique_total_price(base_price)
    c = orders_db()
    cur = c.cursor()
    cur.execute("""INSERT INTO orders
        (tg_id, tg_username, tg_name, plan_key, status, created_at, quantity, total_price, base_price, target_email)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (tg_id, tg_username, tg_name, plan_key, "awaiting_receipt", int(time.time() * 1000), quantity, payable, base_price, target_email))
    order_id = cur.lastrowid
    c.commit()
    c.close()
    return order_id, payable


def unique_usdt_amount(base_toman):
    """همون منطق unique_total_price ولی برای USDT: به‌جای افست چندتومانی، یه
    افست خیلی‌کوچیک (میکرو-USDT، تا ۶ رقم اعشار که TRC20 پشتیبانی می‌کنه)
    اضافه می‌کنه تا هیچ دو سفارش باز هم‌زمان مبلغ کریپتوی یکسان نداشته باشن."""
    base_usdt = round(base_toman / USDT_TOMAN_RATE, 2)
    c = orders_db()
    cur = c.cursor()
    cur.execute("SELECT usdt_amount FROM orders WHERE status IN ('awaiting_receipt','pending_review') AND usdt_amount IS NOT NULL")
    taken = {round(r[0], 6) for r in cur.fetchall()}
    c.close()
    for offset in range(1, 1000):
        candidate = round(base_usdt + offset / 1_000_000, 6)
        if candidate not in taken:
            return candidate
    return base_usdt


def set_order_usdt_amount(order_id, usdt_amount):
    c = orders_db()
    c.execute("UPDATE orders SET usdt_amount=? WHERE id=?", (usdt_amount, order_id))
    c.commit()
    c.close()


def unique_try_amount(base_toman):
    """همون منطق unique_total_price ولی برای لیر ترکیه — چون کوچیک‌ترین واحد
    TRY کروش (۱/۱۰۰ لیر) هست، افست به کروش اضافه می‌شه، نه تومان."""
    base_try = round(base_toman / TRY_TOMAN_RATE, 2)
    c = orders_db()
    cur = c.cursor()
    cur.execute("SELECT try_amount FROM orders WHERE status IN ('awaiting_receipt','pending_review') AND try_amount IS NOT NULL")
    taken = {round(r[0], 2) for r in cur.fetchall()}
    c.close()
    for offset in range(1, 99):
        candidate = round(base_try + offset / 100, 2)
        if candidate not in taken:
            return candidate
    return base_try


def set_order_try_amount(order_id, try_amount):
    c = orders_db()
    c.execute("UPDATE orders SET try_amount=? WHERE id=?", (try_amount, order_id))
    c.commit()
    c.close()


def oldest_pending_order_for_try_amount(amount):
    c = orders_db()
    cur = c.cursor()
    cur.execute("""SELECT id FROM orders WHERE try_amount=? AND status IN ('awaiting_receipt','pending_review')
                   ORDER BY id ASC LIMIT 1""", (round(amount, 2),))
    row = cur.fetchone()
    c.close()
    return row[0] if row else None


def attach_receipt(order_id, file_id, ocr_text=None, amount=None, tracking=None, date=None, name=None, ocr_corrected=False):
    c = orders_db()
    c.execute("""UPDATE orders SET receipt_file_id=?, status=?, receipt_ocr_text=?, receipt_amount=?,
                 receipt_tracking=?, receipt_date=?, receipt_name=?, receipt_ocr_corrected=? WHERE id=?""",
              (file_id, "pending_review", ocr_text, amount, tracking, date, name, 1 if ocr_corrected else 0, order_id))
    c.commit()
    c.close()


# ---------- receipt OCR (best-effort — raw text is always kept as a manual fallback) ----------

FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
AR_DIGITS = "٠١٢٣٤٥٦٧٨٩"


def normalize_digits(s):
    for i, d in enumerate(FA_DIGITS):
        s = s.replace(d, str(i))
    for i, d in enumerate(AR_DIGITS):
        s = s.replace(d, str(i))
    return s


def download_telegram_file(file_id):
    info = api_call("getFile", {"file_id": file_id})
    file_path = info["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    with urllib.request.urlopen(url, timeout=25) as resp:
        return resp.read()


def ocr_receipt_text(image_bytes):
    """Deliberately no image preprocessing here: tested grayscale+upscale+
    contrast+binarize against a real receipt that had misread the amount,
    and it made the OCR output WORSE (introduced garbled non-digit text
    instead of clean-but-wrong digits) rather than better — preprocessing
    tuning that isn't validated against real receipts is as likely to hurt
    as help, so this stays plain. Reliability comes from reconcile_ocr_amount
    plus the daily reconciliation job, not from OCR tricks with no evidence
    behind them."""
    img = Image.open(io.BytesIO(image_bytes))
    return pytesseract.image_to_string(img, lang="fas+eng")


def parse_receipt_fields(raw_text):
    """Heuristic regex extraction over raw OCR text — bank-app receipt layouts
    vary a lot and OCR on Persian screenshots isn't perfect, so this is
    best-effort only. The full raw_text is always stored separately so a
    human can always read the receipt manually if a field comes back empty
    or wrong."""
    text = normalize_digits(raw_text or "")
    fields = {"amount": None, "tracking": None, "date": None, "name": None}

    m = re.search(r"(?:شماره\s*پیگیری|کد\s*پیگیری|شماره\s*رهگیری|کد\s*رهگیری|پیگیری|ref(?:erence)?\s*(?:no|number)?)\s*[:：]?\s*(\d{4,20})",
                  text, re.IGNORECASE)
    if m:
        fields["tracking"] = m.group(1)

    m = re.search(r"مبلغ\s*[:：]?\s*([\d,]{3,15})\s*(ریال|تومان)?", text)
    if m:
        amount = int(m.group(1).replace(",", ""))
        if m.group(2) and "ریال" in m.group(2):
            amount //= 10  # bank apps often show Rial; our prices are in Toman
        fields["amount"] = amount

    m = re.search(r"(\d{4}/\d{1,2}/\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)", text)
    if m:
        fields["date"] = m.group(1)

    m = re.search(r"نام(?:\s*و\s*نام\s*خانوادگی)?\s*[:：]?\s*([^\n]{2,40})", text)
    if m:
        fields["name"] = m.group(1).strip()

    return fields


def plan_for_key(plan_key):
    """پلن را برمی‌گرداند و اگر کلید ناشناخته بود، به‌جای KeyError یک پلنِ
    جایگزین می‌دهد.

    چرا: مسیر وب‌هوکِ پیامک بانک این را داخل try سراسری صدا می‌زند، پس یک
    KeyError آنجا یعنی سفارش بی‌صدا معطل می‌ماند و هیچ‌کس نمی‌فهمد چرا.
    کلید ناشناخته واقعاً پیش می‌آید — مثلاً سفارشی که با نسخه‌ی قدیمی‌ترِ
    فهرست پلن‌ها ثبت شده یا از ربات دیگری آمده.
    """
    plan = PLAN_BY_KEY.get(plan_key)
    if plan:
        return plan
    print(f"[plan] کلید ناشناخته: {plan_key!r} — پلن جایگزین استفاده شد", flush=True)
    return {"key": plan_key, "label": plan_key or "نامشخص", "gb": 0, "days": 30, "price": 0}


def get_order(order_id):
    c = orders_db()
    cur = c.cursor()
    cur.execute("SELECT id, tg_id, tg_username, tg_name, plan_key, status, quantity, total_price, target_email FROM orders WHERE id=?", (order_id,))
    row = cur.fetchone()
    c.close()
    return row


def set_order_status(order_id, status):
    c = orders_db()
    c.execute("UPDATE orders SET status=?, decided_at=? WHERE id=?", (status, int(time.time() * 1000), order_id))
    c.commit()
    c.close()


# ---------- x-ui client provisioning (mirrors hermes-bot.py's model) ----------

def sub_domain_current():
    c = sqlite3.connect(X_UI_DB)
    cur = c.cursor()
    cur.execute("SELECT value FROM settings WHERE key='subDomain'")
    row = cur.fetchone()
    c.close()
    return row[0] if row and row[0] else ""


def build_vless_links(email):
    """Direct vless:// URIs for both inbounds, built straight from x-ui's own
    settings — deliberately NOT fetched from the subscription HTTP endpoint,
    since that endpoint enforces a per-client HWID check a plain server-side
    fetch can't satisfy (real VPN apps report their device ID; a script
    can't), which makes it 404 even for valid, active clients."""
    c = sqlite3.connect(X_UI_DB)
    cur = c.cursor()
    cur.execute("SELECT uuid FROM clients WHERE email=? COLLATE NOCASE", (email,))
    row = cur.fetchone()
    if not row:
        c.close()
        return []
    cid = row[0]
    cur.execute("SELECT id, stream_settings FROM inbounds WHERE id IN (1,2,3) ORDER BY id")
    inbounds = {r[0]: json.loads(r[1]) for r in cur.fetchall()}
    c.close()

    links = []
    ss1 = inbounds.get(1)
    if ss1:
        host = ss1["wsSettings"]["host"]
        path = ss1["wsSettings"]["path"]
        sni = ss1["tlsSettings"]["serverName"]
        fp = ss1["tlsSettings"]["settings"]["fingerprint"]
        q = urllib.parse.urlencode({"type": "ws", "security": "tls", "path": path, "host": host, "sni": sni, "fp": fp, "encryption": "none"})
        links.append(("Main (پیش‌فرض)", f"vless://{cid}@{host}:443?{q}#{urllib.parse.quote(email)}-Main"))
        q_main = q

    ss2 = inbounds.get(2)
    if ss2:
        rs = ss2["realitySettings"]
        pbk = rs["settings"]["publicKey"]
        fp = rs["settings"]["fingerprint"]
        sni = rs["serverNames"][0]
        sid = next((s for s in rs["shortIds"] if s), "")
        spx = rs["settings"].get("spiderX", "/")
        q = urllib.parse.urlencode({"security": "reality", "pbk": pbk, "fp": fp, "sni": sni, "sid": sid, "spx": spx,
                                     "type": "tcp", "flow": "xtls-rprx-vision", "encryption": "none"})
        links.append(("Reality (سریع‌تر/مقاوم‌تر)", f"vless://{cid}@{ORIGIN_IP}:8443?{q}#{urllib.parse.quote(email)}-Reality"))

    # گزینه‌های اضافه‌ی Main روی IPهای دیگر کلادفلر؛ آخرِ لیست، تا ترتیب و انتخابِ فعلیِ مشتری عوض نشود.
    if ss1:
        for n, ip in enumerate(MAIN_ALT_IPS, 2):
            links.append((f"Main {n} ⚡ (سریع‌تر در بعضی اپراتورها)", f"vless://{cid}@{ip}:443?{q_main}#{urllib.parse.quote(email)}-Main{n}"))

    ss3 = inbounds.get(XHTTP_INBOUND)
    if ss3 and client_supports_xhttp():
        x = ss3["xhttpSettings"]; t = ss3["tlsSettings"]
        q = urllib.parse.urlencode({"type": "xhttp", "security": "tls", "path": x["path"], "host": x["host"],
                                    "mode": x.get("mode", "auto"), "sni": t["serverName"], "fp": "chrome",
                                    "alpn": "h2,http/1.1", "encryption": "none"})
        links.append(("XHTTP 🛡 (مخصوص مخابرات)", f"vless://{cid}@{x['host']}:{XHTTP_PORT}?{q}#{urllib.parse.quote(email)}-XHTTP"))
    return links


def format_vless_block(email):
    links = build_vless_links(email)
    if not links:
        return ""
    lines = [f"{label}:\n<code>{uri}</code>" for label, uri in links]
    return "\n\n🔗 لینک‌های مستقیم (روش بزن تا کپی شه):\n\n" + "\n\n".join(lines)


TELEGRAM_CAPTION_LIMIT = 1024


APP_LINK_BASE = "https://app.example.com/open/"
WINDOWS_SETUP_URL = "https://app.example.com/app/HermesVPN-Connect-Setup.exe"  # always the latest Windows installer
APP_LINK_TTL = 30 * 24 * 3600      # ثانیه


def create_app_link_token(email):
    """یک توکن تازه برای «اتصال مستقیم به نرم‌افزار» می‌سازد. هر بار که پیام
    لینک ساخته می‌شود توکن تازه‌ای می‌گیرد؛ توکن‌های منقضی همان‌جا پاک می‌شوند."""
    import secrets
    token = secrets.token_urlsafe(24)
    now = int(time.time())
    c = orders_db()
    cur = c.cursor()
    cur.execute("DELETE FROM app_link_tokens WHERE created_at < ?", (now - APP_LINK_TTL,))
    cur.execute("INSERT OR REPLACE INTO app_link_tokens (token, email, created_at, last_used_at, use_count) "
                "VALUES (?,?,?,NULL,0)", (token, email, now))
    c.commit()
    c.close()
    return token


def redeem_app_link_token(token):
    """ایمیل کلاینت را برمی‌گرداند، یا None اگر توکن ناشناخته یا منقضی باشد."""
    now = int(time.time())
    c = orders_db()
    cur = c.cursor()
    cur.execute("SELECT email, created_at FROM app_link_tokens WHERE token=?", (token,))
    row = cur.fetchone()
    if not row or row[1] < now - APP_LINK_TTL:
        c.close()
        return None
    cur.execute("UPDATE app_link_tokens SET last_used_at=?, use_count=use_count+1 WHERE token=?", (now, token))
    c.commit()
    c.close()
    return row[0]


def handle_app_download(chat_id):
    """یک لینک هوشمند: اگر نرم‌افزار نصب باشد خودش باز می‌شود، اگر نصب نباشد
    صفحه‌ی دانلود آخرین نسخه می‌آید. چون مسیر ثابت است، همین یک دکمه برای
    همیشه آخرین نسخه را می‌دهد و لازم نیست بعد از هر انتشار عوضش کنیم."""
    send_message(
        chat_id,
        "📥 نرم‌افزار HermesVPN Connect\n\n"
        "روی دکمه بزن:\n"
        "• اگر نصبش داری، خودِ نرم‌افزار باز می‌شود\n"
        "• اگر نداری، آخرین نسخه دانلود می‌شود\n\n"
        "از این نسخه به بعد، نرم‌افزار خودش خبر به‌روزرسانی را به تو می‌دهد.\n\n"
        "💻 جدید: نسخه‌ی ویندوز HermesVPN Connect (ویندوز ۱۰ و ۱۱) — فایل نصبی را بگیر، نصب کن و از آیکون روی دسکتاپ اجرا کن.",
        reply_markup=inline([
            [{"text": "📲 دانلود / باز کردن نرم‌افزار", "url": APP_LINK_BASE}],
            [{"text": "💻 دانلود نسخه‌ی ویندوز", "url": WINDOWS_SETUP_URL}],
        ]),
    )


def app_connect_row(email):
    """ردیف دکمه‌ی «اتصال به نرم‌افزار» برای یک اکانت مشخص."""
    try:
        token = create_app_link_token(email)
    except Exception:
        return None
    return [{"text": "📲 اتصال به نرم‌افزار", "url": f"{APP_LINK_BASE}?t={token}"}]


def with_app_connect_button(email, reply_markup):
    """دکمه‌ی اتصال را *بالای* بقیه‌ی دکمه‌ها می‌گذارد، بدون دست‌زدن به آن‌ها."""
    row = app_connect_row(email)
    if not row:
        return reply_markup
    rows = list((reply_markup or {}).get("inline_keyboard", []))
    # نرم‌افزار ویندوز با همان اکانت (یوزرنیم/پسورد یا چسباندن لینک اتصال) کار می‌کند
    win_row = [{"text": "💻 نرم‌افزار ویندوز", "url": WINDOWS_SETUP_URL}]
    return {"inline_keyboard": [row, win_row] + rows}


def send_connection_bundle(chat_id, email, sub_link, header, reply_markup=None, extra_block=""):
    """هر پیام لینکی که به مشتری می‌ره **یک بسته‌ی واحد**ه با همین ترتیب ثابت
    (تصمیم صاحب پروژه، 2026-09-11):
      ۱) تصویر QR (بالا)  ۲) لینک‌های مستقیم vless  ۳) لینک اشتراک

    برای اینکه واقعاً زیر هم و توی یک پیام دیده بشن، به‌صورت عکس + کپشن
    فرستاده می‌شه. QR عمداً لینک *اشتراک* رو کد می‌کنه نه vless — چون لینک
    اشتراک با چرخش دامنه خودش به‌روز می‌شه، ولی یه vless اسکن‌شده ثابت می‌مونه
    و بعد از اولین چرخش دامنه از کار می‌افته."""
    qr_path = f"{QR_DIR}/{email}.png"
    os.makedirs(QR_DIR, exist_ok=True)
    # هر بار از نو ساخته می‌شه؛ اگه دامنه چرخیده باشه QR قدیمی دیگه معتبر نیست.
    subprocess.run(["qrencode", "-o", qr_path, "-s", "8", sub_link], check=True)

    # هر بسته‌ی لینک دکمه‌ی اتصال مستقیم خودش را می‌گیرد. چون این تابع در
    # خرید عمده به‌ازای *هر اکانت* جداگانه صدا زده می‌شود، خریدارِ ۱۰ اکانت
    # زیر هر اکانت دکمه‌ی مخصوص همان اکانت را می‌بیند.
    reply_markup = with_app_connect_button(email, reply_markup)

    caption = (
        header
        + format_vless_block(email)
        + f"\n\n📋 لینک اشتراک (توی اپ «Update Subscription» بزن):\n<code>{sub_link}</code>"
        + extra_block
    ).strip()

    if len(caption) <= TELEGRAM_CAPTION_LIMIT:
        send_photo(chat_id, qr_path, caption=caption, reply_markup=reply_markup,
                   parse_mode="HTML")
        return
    # سقف کپشن تلگرام ۱۰۲۴ کاراکتره؛ اگه رد شد (مثلاً بلوک ورود اپ هم اضافه
    # شده)، عکس اول می‌ره و بقیه بلافاصله زیرش — ترتیب دیداری همون می‌مونه.
    send_photo(chat_id, qr_path, caption=header.strip() or "📱 QR لینک اشتراک",
               parse_mode="HTML")
    rest = (
        format_vless_block(email)
        + f"\n\n📋 لینک اشتراک (توی اپ «Update Subscription» بزن):\n<code>{sub_link}</code>"
        + extra_block
    ).strip()
    send_message(chat_id, rest, reply_markup=reply_markup, parse_mode="HTML")


def _hash_password(password, salt_hex):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 100_000).hex()


def ensure_app_credentials(email):
    """اولین‌باری که یه اکانت فعال می‌شه (خرید تازه یا تمدید)، یه یوزر/پسورد
    موقت برای لاگین اپ HermesVPN Connect می‌سازه — اگه از قبل نداشته باشه
    (یوزرنیمش همون email کلاینته). این فقط برای دسترسی فوریه؛ مشتری همیشه
    می‌تونه بعداً از «📱 ثبت‌نام اپ» یوزر/پسورد دلخواه خودشو جایگزینش کنه.
    پسورد فقط همین یک‌بار (تازه‌ساخته‌شدن) به‌صورت متن ساده برگردونده می‌شه؛
    بعدش فقط هش‌ش می‌مونه، حتی خودمون هم نمی‌تونیم دوباره نشونش بدیم."""
    c = orders_db()
    cur = c.cursor()
    cur.execute("SELECT 1 FROM app_credentials WHERE email=? COLLATE NOCASE", (email,))
    if cur.fetchone():
        c.close()
        return None
    password = randid(8)
    salt = os.urandom(16).hex()
    cur.execute("INSERT INTO app_credentials (email, username, salt, password_hash, created_at) VALUES (?,?,?,?,?)",
                (email, email, salt, _hash_password(password, salt), int(time.time() * 1000)))
    c.commit()
    c.close()
    return password


def username_taken(username, except_email=None):
    c = orders_db()
    cur = c.cursor()
    if except_email:
        cur.execute("SELECT 1 FROM app_credentials WHERE username=? COLLATE NOCASE AND email<>? COLLATE NOCASE",
                    (username, except_email))
    else:
        cur.execute("SELECT 1 FROM app_credentials WHERE username=? COLLATE NOCASE", (username,))
    row = cur.fetchone()
    c.close()
    return row is not None


def auto_app_username(tg_id, tg_username, exclude_email):
    """یوزرنیم لاگین اپ رو خودکار از روی یوزرنیم تلگرام مشتری می‌سازه — نه
    چیزی که ازش بپرسیم. اگه تلگرامش یوزرنیم نداشته باشه از tg_id استفاده
    می‌کنه. برای فروشنده‌هایی که چند اکانت زیر یه تلگرام دارن (که همه یوزرنیم
    تلگرام یکسان می‌گیرن)، شماره‌ی -2/-3/... اضافه می‌شه، دقیقاً همون الگوی
    unique_email برای اسم‌گذاری اکانت‌های شماره‌دار."""
    base = re.sub(r"[^A-Za-z0-9_]", "", tg_username or "") or f"tg{tg_id}"
    if not base[0].isalpha():
        base = "u" + base
    if not username_taken(base, except_email=exclude_email):
        return base
    n = 2
    while username_taken(f"{base}-{n}", except_email=exclude_email):
        n += 1
    return f"{base}-{n}"


def get_app_username(email):
    c = orders_db()
    cur = c.cursor()
    cur.execute("SELECT username FROM app_credentials WHERE email=? COLLATE NOCASE", (email,))
    row = cur.fetchone()
    c.close()
    return row[0] if row else None


def set_app_credentials(email, username, password, contact_email=None):
    """ثبت‌نام دلخواه مشتری (یوزرنیم/پسورد انتخابیِ خودش) — جایگزین هر
    اعتبارنامه‌ی قبلی همون اکانت می‌شه (چه خودکار چه ثبت‌نام قبلی).
    contact_email فقط برای تماس/بازیابیه، جای email (شناسه‌ی داخلی کلاینت)
    رو نمی‌گیره."""
    salt = os.urandom(16).hex()
    c = orders_db()
    cur = c.cursor()
    cur.execute("""INSERT INTO app_credentials (email, username, salt, password_hash, contact_email, created_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(email) DO UPDATE SET username=excluded.username,
                       salt=excluded.salt, password_hash=excluded.password_hash,
                       contact_email=COALESCE(excluded.contact_email, app_credentials.contact_email)""",
                (email, username, salt, _hash_password(password, salt), contact_email, int(time.time() * 1000)))
    c.commit()
    c.close()


def tg_id_for_username(tg_username):
    """نگاشت یوزرنیم تلگرام به tg_id از روی سفارش‌های ثبت‌شده — وضعیت سفارش
    مهم نیست، چون این نگاشت خودش پایداره و حتی یه سفارش ناتمام هم اثباتش
    می‌کنه."""
    c = orders_db()
    cur = c.cursor()
    cur.execute("""SELECT tg_id FROM orders
                   WHERE tg_username=? COLLATE NOCASE AND tg_id IS NOT NULL AND tg_id<>0
                   ORDER BY id DESC LIMIT 1""", (tg_username,))
    row = cur.fetchone()
    c.close()
    return row[0] if row else None


def find_client_email_by_tg_username(tg_username):
    """اکانت مشتری رو از روی یوزرنیم تلگرامش پیدا می‌کنه.

    اول از راه tg_id می‌ره (یوزرنیم → tg_id از سفارش‌ها، بعد tg_id → کلاینت
    توی x-ui) چون ستون tg_id روی ۲۹ تا از ۴۶ کلاینت پره، در حالی که ستون
    client_email توی سفارش‌ها بعداً اضافه شده و فقط چند سفارش آخر دارنش.
    اگه اون مسیر جواب نداد، از client_email آخرین سفارش تأییدشده استفاده
    می‌کنه."""
    tg_id = tg_id_for_username(tg_username)
    if tg_id:
        x = sqlite3.connect(X_UI_DB)
        xc = x.cursor()
        # فروشنده‌ها چند اکانت زیر یه تلگرام دارن؛ اونی که اسمش دقیقاً
        # یوزرنیم تلگرامه اکانت اصلیشونه، وگرنه قدیمی‌ترین اکانت فعال.
        xc.execute("SELECT email FROM clients WHERE tg_id=? AND email=? COLLATE NOCASE", (tg_id, tg_username))
        hit = xc.fetchone()
        if not hit:
            xc.execute("SELECT email FROM clients WHERE tg_id=? AND enable=1 ORDER BY id LIMIT 1", (tg_id,))
            hit = xc.fetchone()
        x.close()
        if hit:
            return hit[0]

    c = orders_db()
    cur = c.cursor()
    cur.execute("""SELECT client_email FROM orders
                   WHERE tg_username=? COLLATE NOCASE AND status='approved' AND client_email IS NOT NULL
                   ORDER BY decided_at DESC LIMIT 1""", (tg_username,))
    row = cur.fetchone()
    c.close()
    if not row or not row[0]:
        return None
    # client_email is comma-separated for bulk orders (set_order_client_email)
    # — take the first account in that case.
    return row[0].split(",")[0].strip()


def verify_app_credentials(username, password):
    """یوزرنیم یا ایمیل — هر دو برای ورود قبولن."""
    c = orders_db()
    cur = c.cursor()
    cur.execute("""SELECT email, salt, password_hash FROM app_credentials
                   WHERE username=? COLLATE NOCASE OR contact_email=? COLLATE NOCASE""",
                (username, username))
    row = cur.fetchone()
    c.close()
    if not row:
        return None
    real_email, salt, stored_hash = row
    return real_email if _hash_password(password, salt) == stored_hash else None


def resolve_tg_id_for_email(email):
    """برای سفارش‌هایی که از اپ (نه تلگرام) میان — باید بدونیم این اکانت مال
    کدوم tg_id هست تا سفارش با همون ساختار همیشگی (کلید روی tg_id) ثبت بشه
    و کل مسیر تأیید/اطلاع‌رسانی موجود بدون تغییر کار کنه."""
    c = sqlite3.connect(X_UI_DB)
    cur = c.cursor()
    cur.execute("SELECT tg_id FROM clients WHERE email=? COLLATE NOCASE", (email,))
    row = cur.fetchone()
    c.close()
    return row[0] if row and row[0] else None


def get_app_profile(email):
    """پروفایل اپ برای یک اکانت — چیزهایی که فقط توی app_credentials هستن."""
    c = orders_db()
    cur = c.cursor()
    cur.execute("""SELECT username, contact_email, display_name, avatar, created_at
                   FROM app_credentials WHERE email=? COLLATE NOCASE""", (email,))
    row = cur.fetchone()
    c.close()
    if not row:
        return None
    return {
        "username": row[0],
        "email": row[1] or "",
        "display_name": row[2] or "",
        "avatar": row[3] or "",
        "created_at": row[4] or 0,
    }


def update_app_profile(email, display_name=None, contact_email=None, avatar=None):
    """فقط فیلدهایی که فرستاده شدن عوض می‌شن؛ بقیه دست‌نخورده می‌مونن."""
    sets, params = [], []
    if display_name is not None:
        sets.append("display_name=?")
        params.append(display_name)
    if contact_email is not None:
        sets.append("contact_email=?")
        params.append(contact_email)
    if avatar is not None:
        sets.append("avatar=?")
        params.append(avatar)
    if not sets:
        return
    params.append(email)
    c = orders_db()
    c.execute(f"UPDATE app_credentials SET {', '.join(sets)} WHERE email=? COLLATE NOCASE", params)
    c.commit()
    c.close()


def change_app_password(email, new_password):
    c = orders_db()
    cur = c.cursor()
    cur.execute("SELECT username FROM app_credentials WHERE email=? COLLATE NOCASE", (email,))
    row = cur.fetchone()
    c.close()
    if not row:
        return False
    set_app_credentials(email, row[0], new_password)
    return True


def find_account_by_identifier(identifier):
    """برای فراموشی رمز: یوزرنیم اپ، ایمیل ثبت‌شده، یا یوزرنیم تلگرام —
    هر سه به همون اکانت می‌رسن."""
    c = orders_db()
    cur = c.cursor()
    cur.execute("""SELECT email FROM app_credentials
                   WHERE username=? COLLATE NOCASE OR contact_email=? COLLATE NOCASE""",
                (identifier, identifier))
    row = cur.fetchone()
    c.close()
    if row:
        return row[0]
    return find_client_email_by_tg_username(identifier.lstrip("@"))


def reset_password_via_telegram(identifier):
    """رمز تازه می‌سازه و فقط به تلگرامِ خودِ صاحب اکانت می‌فرسته — نه به
    کسی که درخواست داده. برای همین لو رفتن این endpoint خطری نداره: کسی که
    به تلگرام مشتری دسترسی نداره چیزی گیرش نمیاد. به همین دلیل هم پاسخ برای
    اکانت ناموجود و موجود یکیه (تا نشه لیست مشتری‌ها رو حدس زد)."""
    email = find_account_by_identifier(identifier)
    if not email:
        return False
    tg_id = resolve_tg_id_for_email(email)
    if not tg_id:
        return False
    username = get_app_username(email) or email
    new_password = randid(10)
    set_app_credentials(email, username, new_password)
    send_message(
        tg_id,
        "🔑 رمز تازه‌ی اپ HermesVPN Connect:\n\n"
        f"یوزرنیم: <code>{username}</code>\n"
        f"پسورد: <code>{new_password}</code>\n\n"
        "اگه این درخواست کار تو نبوده، همین رمز رو از توی اپ عوض کن.",
        parse_mode="HTML",
    )
    return True


def account_status_response(email):
    """پاسخ مشترک برای هر دو راه ورود اپ (sub_id یا یوزر/پسورد) — یه‌جا چک
    می‌کنه مسدود/منقضی نباشه و لینک‌های واقعی رو برمی‌گردونه."""
    c = sqlite3.connect(X_UI_DB)
    cur = c.cursor()
    cur.execute("SELECT email, enable, expiry_time, total_gb FROM clients WHERE email=? COLLATE NOCASE", (email,))
    row = cur.fetchone()
    c.close()
    if not row:
        return 404, {"ok": False, "error": "not_found"}
    real_email, enable, expiry_time, total_gb = row
    if not enable:
        return 403, {"ok": False, "error": "blocked"}
    if expiry_time and expiry_time > 0 and expiry_time < int(time.time() * 1000):
        return 403, {"ok": False, "error": "expired"}
    links = build_vless_links(real_email)
    return 200, {
        "ok": True,
        "email": real_email,
        "expiry_time": expiry_time,
        "total_gb": total_gb,
        "links": [{"label": label, "uri": uri} for label, uri in links],
    }


def format_app_login_block(email, password):
    return (f"\n\n📱 ورود به نرم‌افزار HermesVPN Connect (اندروید و ویندوز 💻):\n"
            f"یوزرنیم: <code>{email}</code>\n"
            f"پسورد: <code>{password}</code>\n"
            f"با همین یوزرنیم و پسورد هم در اپ اندروید و هم در نرم‌افزار ویندوز وارد شو "
            f"(تنظیمات ← پروفایل ← ورود).\n"
            f"(این پسورد رو یه‌جا نگه دار — دوباره نشونش نمی‌دیم)")


def unique_email(base):
    """Numbered accounts get a dash before the counter ('leyla-2', not
    'leyla2'): usernames often already end in digits, so gluing the counter
    straight on produced names like 'user_name058' that are genuinely hard to
    tell apart when a customer holds ten of them."""
    base = re.sub(r"[^A-Za-z0-9_]", "", base) or "user"
    c = sqlite3.connect(X_UI_DB)
    cur = c.cursor()
    cur.execute("SELECT 1 FROM clients WHERE email=?", (base,))
    if not cur.fetchone():
        c.close()
        return base
    n = 2
    while True:
        candidate = f"{base}-{n}"
        cur.execute("SELECT 1 FROM clients WHERE email=?", (candidate,))
        if not cur.fetchone():
            c.close()
            return candidate
        n += 1


def upsert_traffic_row(cur, email, total_bytes, expiry_ms, enable=1, reset_usage=False):
    """x-ui's traffic job only ever UPDATEs client_traffics — it never creates a
    row, and silently discards the usage of any email it doesn't find there.
    Clients we insert straight into the DB therefore never accumulated usage and
    reported their full quota forever. Every path that creates a client or
    changes its quota/expiry has to keep this row in step.

    reset_usage=True is a renewal (fresh period, counters back to zero);
    False is a top-up (added quota, usage so far stays on the meter)."""
    cur.execute("SELECT id FROM client_traffics WHERE email=?", (email,))
    if cur.fetchone() is None:
        cur.execute("""INSERT INTO client_traffics
            (inbound_id, enable, email, up, down, expiry_time, total,
             reset, reset_day, reset_max, reset_count, last_online, last_sub_fetch)
            VALUES (?,?,?,0,0,?,?,0,0,0,0,0,0)""",
            (1, enable, email, expiry_ms, total_bytes))
    elif reset_usage:
        cur.execute("UPDATE client_traffics SET up=0, down=0, total=?, expiry_time=?, enable=? WHERE email=?",
                    (total_bytes, expiry_ms, enable, email))
    else:
        cur.execute("UPDATE client_traffics SET total=?, expiry_time=?, enable=? WHERE email=?",
                    (total_bytes, expiry_ms, enable, email))


def delayed_expiry_ms(days):
    """زمان اکانت از اولین اتصال شروع شود (x-ui: expiryTime منفی = مدت، از اولین ترافیک).
    زیر ۱ روز (اکانت تست ۱ ساعته) مثل قبل از همین لحظه حساب می‌شود."""
    if days and days >= 1:
        return -int(days * 86400000)
    return int((time.time() + days * 86400) * 1000)

def provision_client(email, gb, days, tg_id, restart=True):
    """gb=0 follows x-ui's own convention: 0 total_gb/totalGB == unlimited.

    restart=False یعنی x-ui را ری‌استارت نکن — فراخوان مسئول است خودش در
    پایان یک‌بار ری‌استارت کند. برای خرید عمده لازم است: ۱۰ اکانت یعنی ۱۰
    ری‌استارت و هر ری‌استارت همه‌ی مشتری‌های روی سرور را قطع می‌کند.
    """
    os.makedirs(QR_DIR, exist_ok=True)
    total_bytes = int(gb * 1024 ** 3) if gb else 0
    expiry_ms = delayed_expiry_ms(days)
    now_ms = int(time.time() * 1000)
    cid = str(uuid.uuid4())
    sub_id = randid()

    c = sqlite3.connect(X_UI_DB)
    cur = c.cursor()
    cur.execute("""INSERT INTO clients
        (email, sub_id, uuid, password, auth, flow, security, limit_ip, limit_hwid, total_gb, expiry_time, enable, tg_id, group_name, comment, reset, reset_day, reset_max, traffic_reset, traffic_reset_day, created_at, updated_at, sync_orphaned_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (email, sub_id, cid, randid(), randid(), "", "auto", 0, 2, total_bytes, expiry_ms, 1, tg_id, "", "", 0, 0, 0, "never", 1, now_ms, now_ms, 0))
    client_id = cur.lastrowid
    cur.execute("INSERT INTO client_inbounds (client_id, inbound_id, flow_override, created_at) VALUES (?,?,?,?)", (client_id, 1, "", now_ms))
    cur.execute("INSERT INTO client_inbounds (client_id, inbound_id, flow_override, created_at) VALUES (?,?,?,?)", (client_id, 2, "xtls-rprx-vision", now_ms))
    upsert_traffic_row(cur, email, total_bytes, expiry_ms, 1)

    cur.execute("SELECT id, settings FROM inbounds WHERE id IN (1,2) ORDER BY id")
    rows = {r[0]: json.loads(r[1]) for r in cur.fetchall()}
    base = {"id": cid, "email": email, "enable": True, "expiryTime": expiry_ms, "limitIp": 0,
            "totalGB": total_bytes, "subId": sub_id, "tgId": tg_id, "reset": 0, "comment": "",
            "created_at": now_ms, "updated_at": now_ms}
    rows[1]["clients"].append(dict(base, flow=""))
    rows[2]["clients"].append(dict(base, flow="xtls-rprx-vision"))
    for iid in (1, 2):
        cur.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(rows[iid]), iid))
    c.commit()
    c.close()

    if restart:
        subprocess.run(["systemctl", "restart", "x-ui"], check=True)
    sub_link = f"https://{sub_domain_current()}:2087/sub/{sub_id}"
    qr_path = f"{QR_DIR}/{email}.png"
    subprocess.run(["qrencode", "-o", qr_path, "-s", "8", sub_link], check=True)
    return sub_link, qr_path


def provision_bulk_for_customer(base, quantity, gb, days, tg_id):
    """quantity>1: numbered accounts (base_1, base_2, ...); quantity==1 keeps
    the plain single-purchase naming (bare base, via unique_email)."""
    results = []
    if quantity <= 1:
        email = unique_email(base)
        link, qr = provision_client(email, gb, days, tg_id)
        results.append((email, link, qr))
        return results
    for i in range(1, quantity + 1):
        email = unique_email(f"{base}_{i}")
        link, qr = provision_client(email, gb, days, tg_id, restart=False)
        results.append((email, link, qr))
    # فقط یک ری‌استارت برای کل بسته. قبلاً به‌ازای هر اکانت یکی بود و یک
    # خرید ۱۰تایی همه‌ی مشتری‌های سرور را ده بار قطع‌ووصل می‌کرد.
    subprocess.run(["systemctl", "restart", "x-ui"], check=True)
    return results


TRIAL_GB = 500 / 1024
TRIAL_DAYS = 1  # 24h — از اولین اتصال (delayed start)


def handle_trial(chat_id, user):
    if find_client_by_tg(chat_id):
        send_message(chat_id, "قبلاً یه اکانت (تست یا خریداری‌شده) داری، برای هر حساب تلگرام فقط یه‌بار تست رایگان مجازه.", reply_markup=main_menu())
        return
    base = user.get("username") or user.get("first_name") or f"user{chat_id}"
    email = unique_email(f"trial_{base}")
    try:
        link, qr = provision_client(email, TRIAL_GB, TRIAL_DAYS, chat_id)
    except Exception as e:
        send_message(chat_id, f"خطا در ساخت لینک تست: {e}", reply_markup=main_menu())
        return
    send_connection_bundle(
        chat_id, email, link,
        header="لینک تست رایگانت آماده‌ست 🎁\n۵۰۰ مگابایت / ۲۴ ساعت اعتبار (از اولین اتصال)",
        reply_markup=main_menu(),
        extra_block="\n\nاگه راضی بودی، از «🛒 خرید سرویس» یه پلن کامل تهیه کن.",
    )


# ---------- owner 2026-11-13: «خرید اکانت جدید» / «تمدید اکانت قبلی» ----------
# مشتری فقط این دو دکمه را می‌بیند. پشت صحنه هر اکانت جدیدِ مشتری‌ای که از قبل
# اکانت دارد، زیرِ اولین اکانتش ساخته می‌شود (ali → ali-Zahra / ali-2) و در
# جدول account_parent ثبت می‌شود تا ربات مدیریت «ali - Zahra» نشانش دهد.
NEWACC_WAIT = {}  # chat_id -> (None, ts) — منتظر اسم اکانت جدید
NEWACC_NAME = {}  # chat_id -> (email or "", ts) — اسمِ رزروشده تا انتخاب پلن


def root_account(tg_id):
    """اولین اکانتِ (غیرتست) این مشتری؛ اگر خودش زیراکانت بود، ریشه‌ی آن."""
    rows = [r[0] for r in find_client_by_tg(tg_id)]
    rows = [e for e in reversed(rows) if not e.startswith("trial_")]  # find_client_by_tg: newest first
    if not rows:
        return None
    try:
        c = orders_db()
        c.execute("CREATE TABLE IF NOT EXISTS account_parent (email TEXT PRIMARY KEY, parent TEXT, tg_id INTEGER, created_at INTEGER)")
        r = c.execute("SELECT parent FROM account_parent WHERE email=?", (rows[0],)).fetchone()
        c.close()
        if r and r[0]:
            return r[0]
    except Exception as e:
        print("[newacc] root lookup:", e, flush=True)
    return rows[0]


def child_email(root, name=None):
    """root-name (or root-2, root-3 … when no name), always unique; keeps the dash."""
    root = re.sub(r"[^A-Za-z0-9_-]", "", root) or "user"
    stem = f"{root}-{name}" if name else None
    c = sqlite3.connect(X_UI_DB)
    taken = lambda e: c.execute("SELECT 1 FROM clients WHERE lower(email)=lower(?)", (e,)).fetchone() is not None
    try:
        if stem and not taken(stem):
            return stem
        n = 2
        while True:
            cand = f"{stem}-{n}" if stem else f"{root}-{n}"
            if not taken(cand):
                return cand
            n += 1
    finally:
        c.close()


def set_account_parent(email, parent, tg_id):
    try:
        c = orders_db()
        c.execute("CREATE TABLE IF NOT EXISTS account_parent (email TEXT PRIMARY KEY, parent TEXT, tg_id INTEGER, created_at INTEGER)")
        c.execute("INSERT OR REPLACE INTO account_parent VALUES (?,?,?,?)", (email, parent, tg_id, int(time.time() * 1000)))
        c.commit(); c.close()
    except Exception as e:
        print("[newacc] parent save:", e, flush=True)


def find_client_by_tg(tg_id):
    c = sqlite3.connect(X_UI_DB)
    cur = c.cursor()
    cur.execute("SELECT email, sub_id, total_gb, expiry_time, enable FROM clients WHERE tg_id=? ORDER BY id DESC", (tg_id,))
    rows = cur.fetchall()
    c.close()
    return rows


def traffic_for(email):
    c = sqlite3.connect(X_UI_DB)
    cur = c.cursor()
    cur.execute("SELECT up, down FROM client_traffics WHERE email=?", (email,))
    row = cur.fetchone()
    c.close()
    return ((row[0] or 0) + (row[1] or 0)) if row else 0


# ---------- referral (زیرمجموعه‌گیری) — 2026-10-24 ----------
# دعوت‌شده در اولین خرید واقعی‌اش ۳ گیگ هدیه می‌گیرد؛ دعوت‌کننده به‌ازای هر
# دعوتِ خریدار یک «امتیاز» می‌گیرد و خودش انتخاب می‌کند: هر امتیاز = ۱۰ گیگ
# روی اکانتش، یا ۳ امتیاز = یک اکانت هدیه‌ی ۵۰ گیگ / ۳۰ روز.
# پاداش فقط با خرید تأییدشده فعال می‌شود، نه با صرفِ عضو شدن (ضد اکانت فیک).
REF_REFEREE_BONUS_GB = 3
REF_PER_INVITE_GB = 10
REF_GIFT_INVITES = 3
REF_GIFT_GB = 50
REF_GIFT_DAYS = 30
REF_LOCK = threading.Lock()
BOT_USERNAME = None


def bot_username():
    global BOT_USERNAME
    if not BOT_USERNAME:
        try:
            BOT_USERNAME = api_call("getMe").get("result", {}).get("username") or "YourSalesBot"
        except Exception:
            return "YourSalesBot"
    return BOT_USERNAME


REF_JOIN_BASE = "https://app.example.com/join/?r="   # landing page: poster preview -> bot


def referral_link(tg_id):
    """Link users share. Its Telegram preview shows the invite poster (og:image), then it opens the bot
    with start=ref_<id>. Whenever someone pastes this link anywhere, the poster goes with it."""
    return referral_start_link(tg_id)   # owner: plain t.me link; the poster travels as a photo (forward / inline)


def referral_start_link(tg_id):
    return f"https://t.me/{bot_username()}?start=ref_{tg_id}"


def ref_db():
    c = orders_db()
    c.execute("""CREATE TABLE IF NOT EXISTS referrals (
        referee_tg INTEGER PRIMARY KEY, referrer_tg INTEGER NOT NULL,
        created_at INTEGER, status TEXT DEFAULT 'joined',
        paid_order_id INTEGER, paid_at INTEGER, claimed INTEGER DEFAULT 0, claimed_as TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS ref_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER, kind TEXT, ts INTEGER, detail TEXT
    )""")
    return c


def flash_sale_pct():
    """Global festival discount: settings key flash_sale = {"pct": 20, "until": ms} (set by hermesvpn-flash-sale.py)."""
    try:
        v = json.loads(get_setting("flash_sale", "{}") or "{}")
        return int(v.get("pct", 0)) if v.get("until", 0) > time.time() * 1000 else 0
    except Exception:
        return 0


def flash_sale_info():
    """(pct, until_ms) of the running festival, or (0, 0)."""
    try:
        v = json.loads(get_setting("flash_sale", "{}") or "{}")
        if v.get("until", 0) > time.time() * 1000 and int(v.get("pct", 0)) > 0:
            return int(v["pct"]), int(v["until"])
    except Exception as e:
        print(f"flash_sale_info: {e}", flush=True)
    return 0, 0


def sale_price(price, pct):
    """Same rounding as handle_plan_selected (nearest 1,000 toman)."""
    return max(1000, int(round(price * (100 - pct) / 100 / 1000.0)) * 1000) if pct else price


def flash_sale_note():
    """'🎉 جشنواره‌ی ۲۰٪ تخفیف — فقط تا جمعه ساعت 23:59 (۱ روز و ۵ ساعت مونده)' or ''."""
    pct, until = flash_sale_info()
    if not pct:
        return ""
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    tz = _tz(_td(hours=3, minutes=30))
    end = _dt.fromtimestamp(until / 1000, tz)
    now = _dt.now(tz)
    midnight = end.hour == 0 and end.minute <= 5      # weekend sale ends 00:00 Sat -> "تا آخر جمعه"
    if midnight:
        end -= _td(minutes=end.minute + 1)
    days = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"]
    if end.date() == now.date():
        day = "امروز"
    elif end.date() == (now + _td(days=1)).date():
        day = "فردا"
    else:
        day = days[end.weekday()]
    left = max(0, int(until / 1000 - time.time()))
    h = left // 3600
    if h >= 24:
        left_txt = f"{h // 24} روز و {h % 24} ساعت" if h % 24 else f"{h // 24} روز"
    elif h >= 1:
        left_txt = f"{h} ساعت"
    else:
        left_txt = f"{max(1, left // 60)} دقیقه"
    when = f"آخر {day}" if midnight else f"{day} ساعت {end.strftime('%H:%M')}"
    return f"🎉 جشنواره‌ی {pct}٪ تخفیف — فقط تا {when} ({left_txt} مونده)"


WHEEL = [("c5", 30), ("c10", 25), ("c15", 10), ("gb05", 15), ("gb1", 8), ("none", 12)]
WHEEL_TEXT = {"c5": "کد تخفیف ۵٪", "c10": "کد تخفیف ۱۰٪", "c15": "کد تخفیف ۱۵٪", "gb05": "۵۰۰ مگ حجم هدیه",
              "gb1": "۱ گیگ حجم هدیه", "none": "پوچ"}


def handle_wheel(chat_id):
    """🎡 daily lucky wheel: once per 24 h; coupons last 24 h, volume goes to the newest volume account."""
    c = coupon_db()
    c.execute("CREATE TABLE IF NOT EXISTS wheel (tg_id INTEGER, ts INTEGER, prize TEXT)")
    last = c.execute("SELECT MAX(ts) FROM wheel WHERE tg_id=?", (chat_id,)).fetchone()[0] or 0
    now = int(time.time() * 1000)
    if now - last < 86400000:
        left = int((86400000 - (now - last)) / 3600000) + 1
        c.close()
        send_message(chat_id, f"🎡 امروز گردونه رو چرخوندی! حدود {left} ساعت دیگه دوباره بیا 😉", reply_markup=main_menu())
        return
    prize = random.choices([p for p, _ in WHEEL], weights=[w for _, w in WHEEL])[0]
    note = ""
    if prize in ("gb05", "gb1"):
        accts = topupable_accounts(chat_id)
        if accts:
            try:
                add_gb_to_client(accts[0][1], 0.5 if prize == "gb05" else 1)
                note = f"\nبه اکانت «{accts[0][1]}» اضافه شد ✅"
            except Exception as e:
                print("[wheel] add gb failed:", e, flush=True)
                prize = "c10"
        else:
            prize = "c10"                            # no volume account yet -> a coupon instead
    if prize.startswith("c"):
        pct = int(prize[1:])
        cur = c.execute("SELECT pct, expires_at, used_order FROM coupons WHERE tg_id=?", (chat_id,)).fetchone()
        if not cur or cur[2] is not None or cur[1] < now or cur[0] < pct:
            c.execute("INSERT OR REPLACE INTO coupons VALUES (?,?,?,?,?)", (chat_id, pct, now + 86400000, None, now))
        note = "\nتا ۲۴ ساعت روی خرید یا تمدیدت خودکار اعمال می‌شه 🛒"
    c.execute("INSERT INTO wheel VALUES (?,?,?)", (chat_id, now, prize))
    c.commit()
    c.close()
    if prize == "none":
        text = "🎡 گردونه چرخید… این بار پوچ شد 😅\nفردا دوباره شانست رو امتحان کن!"
    else:
        text = f"🎡 گردونه چرخید… 🎉 برنده شدی: {WHEEL_TEXT[prize]}!{note}"
    send_message(chat_id, text, reply_markup=main_menu())


def coupon_db():
    c = orders_db()
    c.execute("""CREATE TABLE IF NOT EXISTS coupons (
        tg_id INTEGER PRIMARY KEY, pct INTEGER, expires_at INTEGER, used_order INTEGER, created_at INTEGER)""")
    return c


def active_coupon(tg_id):
    """First-purchase discount offered after the free trial (created by hermesvpn-sales-nudges.py)."""
    try:
        c = coupon_db()
        row = c.execute("SELECT pct FROM coupons WHERE tg_id=? AND used_order IS NULL AND expires_at>?",
                        (tg_id, int(time.time() * 1000))).fetchone()
        c.close()
        return row[0] if row else 0
    except Exception as e:
        print("[coupon] lookup failed:", e, flush=True)
        return 0


def mark_coupon_used(tg_id, order_id):
    try:
        c = coupon_db()
        c.execute("UPDATE coupons SET used_order=? WHERE tg_id=? AND used_order IS NULL", (order_id, tg_id))
        c.commit()
        c.close()
    except Exception as e:
        print("[coupon] mark failed:", e, flush=True)


def log_ref_event(tg_id, kind, detail=None):
    """menu = opened «دعوت دوستان», inline_open = opened the share picker, share = actually sent the
    poster to a chat (chosen_inline_result), claim10 / claim50 = took a reward."""
    try:
        c = ref_db()
        c.execute("INSERT INTO ref_events (tg_id, kind, ts, detail) VALUES (?,?,?,?)",
                  (tg_id, kind, int(time.time() * 1000), detail))
        c.commit()
        c.close()
    except Exception as e:
        print("[referral] event log failed:", e, flush=True)


def user_known(tg_id):
    c = orders_db()
    row = c.execute("SELECT 1 FROM users WHERE tg_id=?", (tg_id,)).fetchone()
    had_order = c.execute("SELECT 1 FROM orders WHERE tg_id=? AND status='approved' LIMIT 1", (tg_id,)).fetchone()
    c.close()
    return bool(row or had_order or find_client_by_tg(tg_id))


def record_referral(referee_tg, payload):
    """/start ref_<id> — فقط برای کاربر واقعاً جدید ثبت می‌شود."""
    m = re.match(r"^ref_(\d{4,15})$", payload or "")
    if not m:
        return
    referrer = int(m.group(1))
    if referrer == referee_tg or user_known(referee_tg):
        return
    c = ref_db()
    if not c.execute("SELECT 1 FROM users WHERE tg_id=?", (referrer,)).fetchone():
        c.close()
        return
    cur = c.execute("INSERT OR IGNORE INTO referrals (referee_tg, referrer_tg, created_at) VALUES (?,?,?)",
                    (referee_tg, referrer, int(time.time() * 1000)))
    c.commit()
    c.close()
    if cur.rowcount:
        try:
            send_message(referrer, "👋 یه نفر با لینک دعوتت وارد ربات شد!\n"
                                   "به‌محض اینکه اولین خریدش تأیید بشه، امتیاز دعوتت فعال می‌شه 🎁")
        except Exception as e:
            print("[referral] notify referrer failed:", e, flush=True)


def pending_referee_bonus(tg_id):
    """اگر این کاربر دعوت‌شده است و هنوز خرید تأییدشده نداشته، ۳ گیگ هدیه دارد."""
    c = ref_db()
    row = c.execute("SELECT referrer_tg FROM referrals WHERE referee_tg=? AND status='joined'", (tg_id,)).fetchone()
    c.close()
    return REF_REFEREE_BONUS_GB if row else 0


def ref_available(tg_id):
    c = ref_db()
    n = c.execute("SELECT COUNT(*) FROM referrals WHERE referrer_tg=? AND status='paid' AND claimed=0",
                  (tg_id,)).fetchone()[0]
    c.close()
    return n


def ref_stats(tg_id):
    c = ref_db()
    joined, paid, claimed = c.execute(
        "SELECT COUNT(*), SUM(status='paid'), SUM(claimed) FROM referrals WHERE referrer_tg=?", (tg_id,)).fetchone()
    c.close()
    return joined or 0, paid or 0, claimed or 0


def on_order_approved_referral(order_id, tg_id):
    """بعد از تأیید هر سفارش صدا زده می‌شود. خطایش هرگز نباید فروش را خراب کند."""
    with REF_LOCK:
        c = ref_db()
        row = c.execute("SELECT referrer_tg FROM referrals WHERE referee_tg=? AND status='joined'", (tg_id,)).fetchone()
        if not row:
            c.close()
            return
        referrer = row[0]
        c.execute("UPDATE referrals SET status='paid', paid_order_id=?, paid_at=? WHERE referee_tg=?",
                  (order_id, int(time.time() * 1000), tg_id))
        c.commit()
        c.close()
    avail = ref_available(referrer)
    left = max(0, REF_GIFT_INVITES - avail)
    msg = (f"🎉 یکی از دوستانی که دعوت کردی خرید کرد!\n\n"
           f"امتیاز آماده‌ی تو: {avail}\n"
           f"• می‌تونی همین الان برای هر امتیاز {REF_PER_INVITE_GB} گیگ به اکانتت اضافه کنی\n"
           + (f"• یا {left} دعوت دیگه صبر کنی و یه اکانت {REF_GIFT_GB} گیگ {REF_GIFT_DAYS} روزه هدیه بگیری"
              if left else f"• یا همین الان اکانت هدیه‌ی {REF_GIFT_GB} گیگ رو بگیری 🎁"))
    try:
        send_message(referrer, msg, reply_markup=referral_claim_keyboard(referrer))
    except Exception as e:
        print("[referral] notify referrer failed:", e, flush=True)
    try:
        notify_admin(f"🎁 دعوت موفق: کاربر {tg_id} (سفارش #{order_id}) از طرف {referrer}. امتیاز آماده‌ی {referrer}: {avail}")
    except Exception:
        pass


def add_gb_to_client(email, gb):
    """حجم اضافه (top-up): مصرف فعلی می‌ماند، فقط سقف بالا می‌رود. هر سه جا هم‌زمان."""
    c = sqlite3.connect(X_UI_DB)
    cur = c.cursor()
    cur.execute("SELECT id, uuid, total_gb, expiry_time FROM clients WHERE email=?", (email,))
    row = cur.fetchone()
    if not row or not row[2]:  # پیدا نشد یا نامحدود
        c.close()
        return False
    client_id, cid, cur_total, cur_expiry = row
    new_total = cur_total + int(gb * 1024 ** 3)
    now_ms = int(time.time() * 1000)
    cur.execute("UPDATE clients SET total_gb=?, updated_at=?, enable=1 WHERE id=?", (new_total, now_ms, client_id))
    upsert_traffic_row(cur, email, new_total, cur_expiry, 1)
    cur.execute("SELECT id, settings FROM inbounds WHERE id IN (1,2) ORDER BY id")
    rows = {r[0]: json.loads(r[1]) for r in cur.fetchall()}
    for iid, data in rows.items():
        for cl in data["clients"]:
            if cl.get("id") == cid or cl.get("email") == email:
                cl["totalGB"] = new_total
                cl["enable"] = True
        cur.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(data), iid))
    c.commit()
    c.close()
    subprocess.run(["systemctl", "restart", "x-ui"], check=True)
    return True


def topupable_accounts(tg_id):
    """اکانت‌های حجمی (نه نامحدود) این کاربر: [(client_id, email)]"""
    c = sqlite3.connect(X_UI_DB)
    rows = c.execute("SELECT id, email FROM clients WHERE tg_id=? AND total_gb>0 ORDER BY id DESC", (tg_id,)).fetchall()
    c.close()
    return [r for r in rows if not r[1].startswith("trial_")]


def referral_claim_keyboard(tg_id):
    avail = ref_available(tg_id)
    rows = []
    if avail >= 1:
        rows.append([{"text": f"➕ گرفتن {REF_PER_INVITE_GB} گیگ (۱ امتیاز)", "callback_data": "ref:c10"}])
    if avail >= REF_GIFT_INVITES:
        rows.append([{"text": f"🎁 گرفتن اکانت {REF_GIFT_GB} گیگ ({REF_GIFT_INVITES} امتیاز)", "callback_data": "ref:c50"}])
    rows.append([{"text": "📤 لینک دعوت من", "callback_data": "ref:menu"}])
    return inline(rows)


REF_POSTER = "/root/hermes-posters/referral-share.jpg"   # owner-approved poster for invite links
REF_SHARE_TEXT = ("🚀 فیلترشکن پرسرعت HermesVPN — با این لینک بیای ۳ گیگ هدیه می‌گیری\n"
                  "🎁 تو هم اگه ۳ نفر رو دعوت کنی، یک اکانت ۵۰ گیگ یک‌ماهه جایزه می‌گیری!")


_INLINE_OK = {"v": None, "ts": 0}


def inline_mode_enabled():
    """Inline mode is switched on in BotFather (/setinline); re-checked every 10 minutes."""
    if time.time() - _INLINE_OK["ts"] > 600:
        try:
            _INLINE_OK["v"] = bool(api_call("getMe").get("result", {}).get("supports_inline_queries"))
        except Exception:
            pass
        _INLINE_OK["ts"] = time.time()
    return bool(_INLINE_OK["v"])


def referral_poster_file_id():
    if not os.path.exists(REF_POSTER):
        return None
    return get_setting(f"ref_poster_file_id:{int(os.path.getmtime(REF_POSTER))}")


def referral_caption(tg_id):
    return f"{REF_SHARE_TEXT}\n\n👇 همین الان شروع کن:\n{referral_link(tg_id)}"


def referral_share_row(chat_id):
    if inline_mode_enabled() and referral_poster_file_id():
        # opens the chat picker; the friend receives the POSTER + text + personal link together
        return [{"text": "📤 ارسال پوستر و لینک دعوت برای دوستان", "switch_inline_query": ""}]
    return [{"text": "📤 اشتراک‌گذاری لینک دعوت",
             "url": "https://t.me/share/url?" + urllib.parse.urlencode({
                 "url": referral_link(chat_id), "text": REF_SHARE_TEXT})}]


def handle_inline_query(iq):
    """Inline share: @YourSalesBot in any chat -> poster + invite text + the sender's own link."""
    uid = iq.get("from", {}).get("id")
    if uid:
        log_ref_event(uid, "inline_open")
    fid = referral_poster_file_id()
    results = []
    if uid and fid:
        results.append({
            "type": "photo", "id": f"ref{uid}", "photo_file_id": fid,
            "title": "پوستر دعوت HermesVPN", "description": "۳ گیگ هدیه برای دوستت",
            "caption": referral_caption(uid),
            "reply_markup": {"inline_keyboard": [[{"text": "🚀 شروع و دریافت ۳ گیگ هدیه", "url": referral_start_link(uid)}]]},
        })
    try:
        api_call("answerInlineQuery", {"inline_query_id": iq["id"], "results": results,
                                       "cache_time": 0, "is_personal": True})
    except Exception as e:
        print("[referral] inline answer failed:", e, flush=True)


def send_referral_poster(chat_id):
    """Forwardable poster card: owner's poster + share text + the user's personal invite link."""
    if not os.path.exists(REF_POSTER):
        return False
    caption = referral_caption(chat_id)
    kb = inline([referral_share_row(chat_id)])
    key = f"ref_poster_file_id:{int(os.path.getmtime(REF_POSTER))}"
    fid = get_setting(key)
    data = {"chat_id": chat_id, "caption": caption, "reply_markup": kb}
    try:
        if fid:
            api_call("sendPhoto", dict(data, photo=fid))
        else:
            with open(REF_POSTER, "rb") as f:
                r = api_call("sendPhoto", data, files={"photo": ("hermesvpn.jpg", f.read())})
            sizes = (r.get("result") or {}).get("photo") or []
            if sizes:
                set_setting(key, sizes[-1]["file_id"])
        return True
    except Exception as e:
        print("[referral] poster send failed:", e, flush=True)
        return False


def handle_referral_menu(chat_id, message_id=None):
    joined, paid, claimed = ref_stats(chat_id)
    avail = ref_available(chat_id)
    if not message_id:
        log_ref_event(chat_id, "menu")
        send_referral_poster(chat_id)
    text = (
        "🎁 دعوت دوستان = حجم رایگان!\n\n"
        "👆 پوستر بالا رو برای دوستات فوروارد کن (لینک اختصاصی تو زیرشه)، یا لینکت رو بفرست:\n"
        f"{referral_link(chat_id)}\n\n"
        "هر کس با لینکت بیاد و خرید کنه:\n"
        f"• خودش {REF_REFEREE_BONUS_GB} گیگ هدیه روی اولین خریدش می‌گیره\n"
        f"• تو یه امتیاز می‌گیری: هر امتیاز = {REF_PER_INVITE_GB} گیگ روی اکانتت\n"
        f"  یا صبر کن {REF_GIFT_INVITES} تا بشه و یه اکانت {REF_GIFT_GB} گیگ / {REF_GIFT_DAYS} روزه هدیه بگیر\n\n"
        f"📊 وارد شده با لینکت: {joined} | خرید کرده: {paid} | امتیاز آماده: {avail}"
    )
    if avail:
        kb = referral_claim_keyboard(chat_id)
        kb["inline_keyboard"].append(referral_share_row(chat_id))
    else:
        kb = inline([referral_share_row(chat_id)])
    if message_id:
        edit_message(chat_id, message_id, text, reply_markup=kb)
    else:
        send_message(chat_id, text, reply_markup=kb)


def _consume_credits(tg_id, n, claimed_as):
    with REF_LOCK:
        c = ref_db()
        ids = [r[0] for r in c.execute(
            "SELECT referee_tg FROM referrals WHERE referrer_tg=? AND status='paid' AND claimed=0 ORDER BY paid_at LIMIT ?",
            (tg_id, n)).fetchall()]
        if len(ids) < n:
            c.close()
            return False
        c.executemany("UPDATE referrals SET claimed=1, claimed_as=? WHERE referee_tg=?", [(claimed_as, i) for i in ids])
        c.commit()
        c.close()
        return ids


def _refund_credits(ids):
    c = ref_db()
    c.executemany("UPDATE referrals SET claimed=0, claimed_as=NULL WHERE referee_tg=?", [(i,) for i in ids])
    c.commit()
    c.close()


def my_referral_report(tg_id):
    """Customer-facing version of the admin referral log (from «📶 وضعیت سرویس من»)."""
    import jdatetime
    from datetime import datetime, timezone, timedelta
    tz = timezone(timedelta(hours=3, minutes=30))
    jd = lambda ms: jdatetime.datetime.fromgregorian(datetime=datetime.fromtimestamp(ms / 1000, tz)).strftime("%Y/%m/%d %H:%M") if ms else "?"
    c = ref_db()
    try:
        rows = c.execute("""SELECT COALESCE(u.display_name,''), r.created_at, r.status, r.claimed, COALESCE(r.claimed_as,'')
                            FROM referrals r LEFT JOIN users u ON u.tg_id=r.referee_tg
                            WHERE r.referrer_tg=? ORDER BY r.created_at DESC""", (tg_id,)).fetchall()
        shares = c.execute("SELECT COUNT(*) FROM ref_events WHERE tg_id=? AND kind='share'", (tg_id,)).fetchone()[0]
        mine = c.execute("SELECT status FROM referrals WHERE referee_tg=?", (tg_id,)).fetchone()
    finally:
        c.close()
    paid = sum(1 for r in rows if r[2] == "paid")
    claimed = sum(1 for r in rows if r[3])
    g10 = [r[4].split(":", 1)[1] for r in rows if r[4].startswith("10gb:")]
    g50 = sum(1 for r in rows if r[4] == "gift50") // REF_GIFT_INVITES
    L = ["🎁 گزارش دعوت‌های تو", "",
         f"📤 پوستر دعوت فرستادی: {shares} بار",
         f"👥 با لینکت وارد ربات شدن: {len(rows)} نفر",
         f"✅ دعوتت رو قبول کردن و خرید کردن: {paid} نفر",
         f"⏳ هنوز خرید نکردن: {len(rows) - paid} نفر",
         f"🏆 امتیاز: {paid} گرفتی | {claimed} استفاده کردی | {paid - claimed} آماده",
         "🎁 هدیه‌هایی که گرفتی: " + ("، ".join(
             ([f"{len(g10)} × {REF_PER_INVITE_GB} گیگ"] if g10 else []) + ([f"{g50} اکانت {REF_GIFT_GB} گیگ"] if g50 else []))
             or "هنوز هیچی")]
    if paid - claimed > 0:
        left = max(0, REF_GIFT_INVITES - (paid - claimed))
        L.append(f"\n💡 الان می‌تونی {REF_PER_INVITE_GB} گیگ بگیری" +
                 (f"، یا {left} دعوت دیگه صبر کنی تا اکانت {REF_GIFT_GB} گیگ بگیری." if left else f"، یا اکانت {REF_GIFT_GB} گیگ هدیه‌ت رو بگیری!"))
    if mine:
        L.append("\n↩️ تو خودت با دعوت یکی از دوستات اومدی — " +
                 (f"{REF_REFEREE_BONUS_GB} گیگ هدیه‌ت روی اولین خریدت اعمال شد ✅" if mine[0] == "paid"
                  else f"{REF_REFEREE_BONUS_GB} گیگ هدیه منتظر اولین خریدته 🎁"))
    if rows:
        L.append("\n📜 لاگ دعوت‌ها (جدیدترین اول):")
        for nm, ts, stt, cl, cas in rows[:20]:
            line = f"• {nm or 'دوستت'} — {jd(ts)} — " + ("✅ خرید کرد" if stt == "paid" else "⏳ هنوز خرید نکرده")
            if cl:
                line += " — پاداش: " + (f"{REF_PER_INVITE_GB} گیگ به {cas.split(':', 1)[1]}" if cas.startswith("10gb:") else f"اکانت {REF_GIFT_GB} گیگ")
            L.append(line)
        if len(rows) > 20:
            L.append(f"… و {len(rows) - 20} نفر دیگه")
    else:
        L.append("\nهنوز کسی با لینکت نیومده. پوستر دعوتت رو برای دوستات بفرست 👇")
    return "\n".join(L)


def handle_referral_callback(chat_id, message_id, user, data):
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else "menu"
    if action == "menu":
        handle_referral_menu(chat_id)
        return
    if action == "report":
        kb = referral_claim_keyboard(chat_id) if ref_available(chat_id) else inline([])
        kb["inline_keyboard"].append(referral_share_row(chat_id))
        kb["inline_keyboard"].append([{"text": "◀️ برگشت به سرویس‌هام", "callback_data": "acc:page:0"}])
        edit_message(chat_id, message_id, my_referral_report(chat_id), reply_markup=kb)
        return
    if action == "c10":
        accounts = topupable_accounts(chat_id)
        if not accounts:
            edit_message(chat_id, message_id,
                         "اکانت حجمی فعالی نداری که حجم بهش اضافه بشه (اکانت نامحدود حجم نمی‌گیره).\n"
                         f"می‌تونی صبر کنی {REF_GIFT_INVITES} امتیاز بشه و اکانت هدیه‌ی {REF_GIFT_GB} گیگ بگیری.",
                         reply_markup=referral_claim_keyboard(chat_id))
            return
        if len(accounts) > 1 and len(parts) < 3:
            rows = [[{"text": f"➕ {email}", "callback_data": f"ref:c10:{cid}"}] for cid, email in accounts[:8]]
            edit_message(chat_id, message_id, f"{REF_PER_INVITE_GB} گیگ به کدوم اکانت اضافه بشه؟", reply_markup=inline(rows))
            return
        target = accounts[0][1]
        if len(parts) >= 3:
            match = [e for cid, e in accounts if str(cid) == parts[2]]
            if not match:
                edit_message(chat_id, message_id, "این اکانت مال حساب تو نیست.")
                return
            target = match[0]
        ids = _consume_credits(chat_id, 1, f"10gb:{target}")
        if not ids:
            edit_message(chat_id, message_id, "امتیاز آماده‌ای نداری.")
            return
        try:
            ok = add_gb_to_client(target, REF_PER_INVITE_GB)
        except Exception as e:
            ok = False
            print("[referral] add gb failed:", e, flush=True)
        if not ok:
            _refund_credits(ids)
            edit_message(chat_id, message_id, "الان نشد حجم اضافه بشه؛ امتیازت سر جاشه. چند دقیقه دیگه دوباره امتحان کن.")
            return
        log_ref_event(chat_id, "claim10", target)
        edit_message(chat_id, message_id, f"✅ {REF_PER_INVITE_GB} گیگ به «{target}» اضافه شد. ممنون که HermesVPN رو معرفی کردی 🙏")
        notify_admin(f"🎁 پاداش دعوت: {REF_PER_INVITE_GB}GB به {target} (کاربر {chat_id})")
        return
    if action == "c50":
        ids = _consume_credits(chat_id, REF_GIFT_INVITES, "gift50")
        if not ids:
            edit_message(chat_id, message_id, f"برای اکانت هدیه {REF_GIFT_INVITES} امتیاز لازمه.",
                         reply_markup=referral_claim_keyboard(chat_id))
            return
        base = user.get("username") or user.get("first_name") or f"user{chat_id}"
        email = unique_email(f"gift_{base}")
        try:
            link, qr = provision_client(email, REF_GIFT_GB, REF_GIFT_DAYS, chat_id)
        except Exception as e:
            _refund_credits(ids)
            print("[referral] gift provision failed:", e, flush=True)
            edit_message(chat_id, message_id, "الان نشد اکانت هدیه ساخته بشه؛ امتیازهات سر جاشه. کمی بعد دوباره امتحان کن.")
            return
        log_ref_event(chat_id, "claim50", email)
        edit_message(chat_id, message_id, "🎁 اکانت هدیه‌ت ساخته شد!")
        app_password = ensure_app_credentials(email)
        send_connection_bundle(
            chat_id, email, link,
            header=f"🎁 اکانت هدیه‌ی دعوت دوستان\n{REF_GIFT_GB} گیگ / {REF_GIFT_DAYS} روز (از اولین اتصال)",
            reply_markup=main_menu(),
            extra_block=(format_app_login_block(email, app_password) if app_password else ""),
        )
        notify_admin(f"🎁 اکانت هدیه‌ی دعوت ساخته شد: {email} برای کاربر {chat_id}")
        return


# ---------- flows ----------

def main_menu(chat_id=None):
    rows = [
        ["🛒 خرید سرویس", "📦 خرید عمده"],
        ["🔁 تمدید سرویس", "🧪 تست"],
        ["📶 وضعیت سرویس من", "🔄 دریافت دوباره‌ی لینک در صورت قطعی"],
        ["📥 دانلود نرم‌افزار", "🆘 پشتیبانی"],
        ["🎁 دعوت دوستان (حجم رایگان)", "🎡 گردونه‌ی شانس"],
    ]
    # دکمه‌های ادمین عمداً اینجا نیستند: همه‌شان در ربات ادمین‌اند. چون
    # شناسه‌ی ادمین و خریدار یکی است، هر دکمه‌ی مدیریتی در این منو وسط چتِ
    # خریدِ خودِ صاحب پروژه می‌افتاد و همان «قاطی شدن» را می‌ساخت.
    # is_persistent عمداً نیست: کاربر باید بتواند منو را با دکمه‌ی کیبورد پایین ببرد. گم نشدنش را
    # send_message/send_photo تضمین می‌کنند (هر پیام بدون دکمه، منو را دوباره می‌آورد) و /menu.
    return {"keyboard": rows, "resize_keyboard": True}


def show_pending_orders(chat_id):
    """Emergency manual path that stays available even in auto mode: list
    every order whose customer is still waiting, each with send/cancel
    buttons, so nobody is stuck if the bank-SMS route misses one."""
    c = orders_db()
    cur = c.cursor()
    cur.execute("""SELECT id, tg_username, tg_name, plan_key, quantity, total_price, created_at, target_email
                   FROM orders WHERE status IN ('pending_review','awaiting_receipt')
                   ORDER BY id DESC LIMIT 20""")
    rows = cur.fetchall()
    c.close()
    if not rows:
        send_message(chat_id, "هیچ سفارش معطلی نیست ✅ همه‌چیز تسویه‌ست.", reply_markup=main_menu(chat_id))
        return
    send_message(chat_id, f"{len(rows)} سفارش معطل:")
    now_ms = time.time() * 1000
    for oid, uname, name, plan_key, quantity, total_price, created_at, target_email in rows:
        who = f"@{uname}" if uname else (name or "?")
        mins = int((now_ms - (created_at or now_ms)) / 60000)
        qty_note = f" ×{quantity}" if quantity and quantity > 1 else ""
        kind = f"تمدید «{target_email}»" if target_email else f"{plan_key}{qty_note}"
        send_message(chat_id,
                      f"سفارش #{oid} — {who}\n{kind} — {(total_price or 0):,} تومان\n{mins} دقیقه پیش ثبت شده",
                      reply_markup=inline([[
                          {"text": "📤 ارسال دستی", "callback_data": f"approve:{oid}"},
                          {"text": "❌ کنسل", "callback_data": f"reject:{oid}"},
                      ]]))


def plan_button_text(p):
    pct, _ = flash_sale_info()
    if pct:
        return f"{p['label']} — {sale_price(p['price'], pct):,} تومان (به‌جای {p['price']:,}) 🎉"
    return f"{p['label']} — {p['price']:,} تومان"


CANCEL_ROW = [{"text": "❌ لغو خرید", "callback_data": "cancelbuy"}]  # owner 2026-11-13: هر مرحله، قبل از واریز


def plans_keyboard():
    rows = [[{"text": plan_button_text(p), "callback_data": f"buy:{p['key']}"}] for p in PLANS]
    return inline(rows + [CANCEL_ROW])


def bulk_packages_keyboard():
    rows = [[{"text": f"{p['label']} — {p['price']:,} تومان", "callback_data": f"buy:{p['key']}"}] for p in BULK_PACKAGES] + [CANCEL_ROW]
    return inline(rows)


HELP_TEXT = (
    "به فروشگاه HermesVPN خوش اومدی!\n\n"
    "🛒 خرید سرویس: انتخاب پلن و پرداخت\n"
    "📦 خرید عمده: بسته‌های 10تایی با قیمت ویژه (برای فروشنده‌ها)\n"
    "📶 وضعیت سرویس من: حجم/زمان باقی‌مونده\n"
    "🔄 دریافت دوباره‌ی لینک در صورت قطعی: اگه وصل نمی‌شی، همینو بزن\n"
    "📥 دانلود نرم‌افزار: اپ اندروید و نسخه‌ی جدید ویندوز 💻\n"
    "🆘 پشتیبانی: ارتباط با پشتیبانی\n"
    "🎁 دعوت دوستان: لینک اختصاصی بگیر، با هر خرید دوستت حجم رایگان بگیر"
)


def handle_start(chat_id, user):
    name = user.get("first_name", "") or user.get("username", "") or str(chat_id)
    upsert_user(chat_id, name)
    send_message(chat_id, HELP_TEXT, reply_markup=main_menu(chat_id))


def handle_buy_menu(chat_id):
    """owner 2026-11-13: اول دو دکمه، بعد پلن."""
    NEWACC_NAME.pop(chat_id, None)
    send_message(chat_id, "خرید اکانت جدید یا تمدید اکانت قبلی؟", reply_markup=inline([
        [{"text": "🆕 خرید اکانت جدید", "callback_data": "newstart"}],
        [{"text": "🔁 تمدید اکانت قبلی", "callback_data": "renewstart"}],
        CANCEL_ROW,
    ]))


def send_plans_for_new(chat_id, message_id=None):
    note = flash_sale_note()
    text = (note + "\nتخفیف خودکار روی خرید و تمدید اعمال می‌شه.\n\n" if note else "") + "کدوم پلن رو می‌خوای؟"
    if message_id:
        edit_message(chat_id, message_id, text, reply_markup=plans_keyboard())
    else:
        send_message(chat_id, text, reply_markup=plans_keyboard())


def handle_bulk_menu(chat_id):
    send_message(chat_id, "کدوم بسته‌ی عمده رو می‌خوای؟ (هر بسته شامل 10 اکانت با قیمت ویژه‌ست)", reply_markup=bulk_packages_keyboard())


def renew_plans_keyboard(email):
    rows = [[{"text": plan_button_text(p), "callback_data": f"buyrenew:{p['key']}:{email}"}] for p in PLANS]
    return inline(rows + [CANCEL_ROW])


def handle_renew_menu(chat_id):
    accounts = find_client_by_tg(chat_id)
    if not accounts:
        send_message(chat_id, "هنوز سرویسی برای این حساب پیدا نکردم. اول از «🛒 خرید سرویس» شروع کن.", reply_markup=main_menu())
        return
    if len(accounts) == 1:
        email = accounts[0][0]
        label = get_labels([email]).get(email)
        send_message(chat_id, f"«{label or email}» رو با کدوم پلن تمدید کنم؟", reply_markup=renew_plans_keyboard(email))
        return
    # same paginated, labelled list as the status view — a reseller with 100
    # accounts can't be shown 100 raw buttons
    kb, total, _ = accounts_keyboard(chat_id, 0)
    try:
        kb["inline_keyboard"].append(CANCEL_ROW)
    except Exception:
        pass
    send_message(chat_id, f"{total} اکانت داری — کدومش رو می‌خوای تمدید کنی؟ (روی اکانت بزن، بعد «🔁 تمدید»)",
                  reply_markup=kb)


def show_buy_choice(chat_id, message_id, user, plan_key):
    """پلن تکی + مشتری اکانت دارد → دو دکمه‌ی ساده؛ در غیر این صورت مستقیم فاکتور."""
    plan = PLAN_BY_KEY.get(plan_key)
    if not plan:
        return
    if plan["quantity"] > 1:   # بسته‌ی عمده مثل قبل
        handle_plan_selected(chat_id, message_id, user, plan_key)
        return
    edit_message(chat_id, message_id, f"پلن «{plan['label']}» — کدوم؟", reply_markup=inline([
        [{"text": "🆕 خرید اکانت جدید", "callback_data": f"newacc:{plan_key}"}],
        [{"text": "🔁 تمدید اکانت قبلی", "callback_data": f"renewfor:{plan_key}"}],
    ]))


def handle_plan_selected(chat_id, message_id, user, plan_key, target_email=None, new_email=None):
    plan = PLAN_BY_KEY.get(plan_key)
    if not plan:
        return
    quantity = 1 if (target_email or new_email) else plan["quantity"]
    base_price, coupon_note = plan["price"], ""
    pct = active_coupon(chat_id) if quantity == 1 else 0
    sale = flash_sale_pct() if quantity == 1 else 0       # weekend festival / heart-challenge sale (also renewals)
    disc_src = ""
    if pct:
        try:
            _c = orders_db(); disc_src = ordinfo.coupon_source(_c, chat_id); _c.close()
        except Exception:
            disc_src = "coupon"
    if sale > pct:
        pct = sale
        disc_src = "festival"
    if pct:
        base_price = max(1000, int(round(plan["price"] * (100 - pct) / 100 / 1000.0)) * 1000)
        coupon_note = f"\n🎁 تخفیف {pct}٪ اعمال شد: {plan['price']:,} ← {base_price:,} تومان\n"
        if sale and sale >= pct:
            coupon_note = (f"\n{flash_sale_note()}\n"
                           f"🎁 تخفیف {pct}٪ جشنواره روی این فاکتور اعمال شد: {plan['price']:,} ← {base_price:,} تومان\n")
    order_id, payable = create_order(chat_id, user.get("username", ""), user.get("first_name", ""),
                                      plan_key, quantity, base_price, target_email=target_email)
    ordinfo.store_price_facts(order_id, plan["price"], pct, disc_src if pct else "", "bot")
    if new_email:
        try:
            _c = orders_db(); _c.execute("UPDATE orders SET new_email=? WHERE id=?", (new_email, order_id)); _c.commit(); _c.close()
        except Exception as e:
            print("[newacc] save new_email:", e, flush=True)
    qty_note = f" ({quantity} اکانت)" if quantity > 1 else ""
    action_note = (f"تمدید «{target_email}»" if target_email else
                   f"اکانت جدید «{new_email}» — {plan['label']}" if new_email else f"پلن انتخابی: {plan['label']}{qty_note}")
    text = f"{action_note}\n{coupon_note}\nروش پرداخت رو انتخاب کن (شماره سفارش: #{order_id}):"
    kb = inline([
        [{"text": "💳 کارت به کارت", "callback_data": f"paymethod:card:{order_id}"}],
        [{"text": "💎 کریپتو (USDT)", "callback_data": f"paymethod:crypto:{order_id}"}],
        [{"text": "❌ لغو خرید", "callback_data": f"cancelbuy:{order_id}"}],
    ])
    if message_id:
        edit_message(chat_id, message_id, text, reply_markup=kb, parse_mode="HTML")
    else:
        send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")


def order_discount_line(order_id):
    """'🎁 قیمت اصلی 60,000 — با ۲۰٪ تخفیف: 48,000 تومان' when the order was discounted, else ''."""
    try:
        c = orders_db()
        r = c.execute("SELECT plan_key, base_price FROM orders WHERE id=?", (order_id,)).fetchone()
        c.close()
        plan = PLAN_BY_KEY.get(r[0]) if r else None
        if plan and r[1] and r[1] < plan["price"]:
            pct = round(100 - r[1] * 100 / plan["price"])
            return f"🎁 قیمت اصلی {plan['price']:,} — با {pct}٪ تخفیف: {r[1]:,} تومان\n\n"
    except Exception as e:
        print(f"order_discount_line {order_id}: {e}", flush=True)
    return ""


def show_card_payment(chat_id, message_id, order_id):
    row = get_order(order_id)
    if not row:
        edit_message(chat_id, message_id, "این سفارش پیدا نشد.")
        return
    payable = row[7]  # total_price
    text = (
        order_discount_line(order_id) +
        f"مبلغ قابل پرداخت — <b>دقیقاً همین عدد</b> (روش بزن تا کپی شه):\n"
        f"💰 <code>{payable}</code> تومان  (= {payable * 10:,} ریال)\n\n"
        f"شماره کارت (روش بزن تا کپی شه):\n"
        f"💳 <code>{CARD_NUMBER}</code>\n"
        f"👤 به نام: {CARD_HOLDER}\n\n"
        "━━━━━━━━━━━━━━━\n"
        "⚠️ <b>حتماً دقیقاً همین مبلغ را واریز کن</b> — حتی یک تومان کم یا زیاد،\n"
        "سفارشت شناسایی نمی‌شود و فعال‌سازی‌اش معطل می‌ماند.\n"
        "━━━━━━━━━━━━━━━\n\n"
        f"بعد از واریز، سرویست خودکار فعال می‌شه. اگه تا چند دقیقه نشد، عکس رسید رو همینجا بفرست.\n"
        f"(شماره سفارش: #{order_id})"
    )
    edit_message(chat_id, message_id, text, parse_mode="HTML",
                 reply_markup=inline([[{"text": "❌ لغو خرید (قبل از واریز)", "callback_data": f"cancelbuy:{order_id}"}]]))


def show_crypto_payment(chat_id, message_id, order_id):
    row = get_order(order_id)
    if not row:
        edit_message(chat_id, message_id, "این سفارش پیدا نشد.")
        return
    payable_toman = row[7]  # total_price
    usdt_amount = unique_usdt_amount(payable_toman)
    set_order_usdt_amount(order_id, usdt_amount)

    qr_path = f"{QR_DIR}/usdt-{order_id}.png"
    os.makedirs(QR_DIR, exist_ok=True)
    subprocess.run(["qrencode", "-o", qr_path, "-s", "8", USDT_WALLET_ADDRESS], check=True)

    caption = (
        order_discount_line(order_id) +
        f"مبلغ قابل پرداخت — <b>دقیقاً همین عدد</b> USDT (نه گردشده، شبکه‌ی TRC20):\n"
        f"💎 <code>{usdt_amount}</code> USDT\n\n"
        f"آدرس ولت (روش بزن تا کپی شه):\n"
        f"<code>{USDT_WALLET_ADDRESS}</code>\n\n"
        "━━━━━━━━━━━━━━━\n"
        "⚠️ <b>حتماً دقیقاً همین مبلغ را واریز کن</b> — حتی اختلاف کوچک یعنی\n"
        "سفارشت شناسایی نمی‌شود و فعال‌سازی‌اش معطل می‌ماند.\n"
        "━━━━━━━━━━━━━━━\n\n"
        f"⚠️ فقط شبکه‌ی <b>TRC20 (ترون)</b> — شبکه‌ی اشتباه یعنی از دست رفتن پول.\n"
        f"بعد از واریز، سرویست خودکار طی چند دقیقه فعال می‌شه (وقتی تراکنش روی زنجیره تأیید بشه).\n"
        f"(شماره سفارش: #{order_id})"
    )
    send_photo(chat_id, qr_path, caption=caption, parse_mode="HTML",
               reply_markup=inline([[{"text": "❌ لغو خرید (قبل از واریز)", "callback_data": f"cancelbuy:{order_id}"}]]))


def reconcile_ocr_amount(raw_amount, expected_amount):
    """OCR sometimes drops trailing digits from a grouped number (seen live:
    '600,000' read as '6000', which after the Rial/10 conversion becomes 600
    instead of 60,000). If multiplying the OCR reading by a common
    missed-digit factor lands exactly on the order's real price, treat that
    as the correct amount instead of just flagging a mismatch — this is the
    single most common OCR failure mode here, so auto-correcting it cuts
    down sharply on manual checks without weakening the safety net for
    genuinely wrong amounts (still only 'corrects' to an EXACT match)."""
    if raw_amount is None or not expected_amount:
        return raw_amount, False
    for factor in (10, 100, 1000):
        if raw_amount * factor == expected_amount:
            return expected_amount, True
    return raw_amount, False


def process_receipt_for_order(order_id, image_bytes, uname_label):
    """هسته‌ی مشترک ثبت رسید — چه از عکس تلگرام بیاد چه از آپلود توی اپ.
    OCR می‌کنه، پیوست می‌کنه، و یا خودکار تأیید می‌کنه یا برای ادمین با
    دکمه‌ی تأیید/رد می‌فرسته. خروجی: (ok, پیام برای نشون‌دادن به کاربر)."""
    row = get_order(order_id)
    if not row:
        return False, "سفارش پیدا نشد."
    _, tg_id, tg_username, tg_name, plan_key, status, quantity, total_price, target_email = row

    ocr_text, fields = None, {"amount": None, "tracking": None, "date": None, "name": None}
    try:
        ocr_text = ocr_receipt_text(image_bytes)
        fields = parse_receipt_fields(ocr_text)
    except Exception as e:
        ocr_text = f"[OCR pipeline error: {e}]"

    raw_amount = fields["amount"]
    corrected_amount, was_corrected = reconcile_ocr_amount(raw_amount, total_price)
    amount_ok = corrected_amount is not None and corrected_amount == total_price

    receipt_path = f"{QR_DIR}/receipt-{order_id}-{int(time.time())}.jpg"
    os.makedirs(QR_DIR, exist_ok=True)
    with open(receipt_path, "wb") as f:
        f.write(image_bytes)

    attach_receipt(order_id, receipt_path, ocr_text, corrected_amount, fields["tracking"], fields["date"], fields["name"], was_corrected)
    plan = plan_for_key(plan_key)
    qty_note = f" ({quantity} اکانت)" if quantity > 1 else ""
    action_note = f"تمدید «{target_email}»" if target_email else f"{plan['label']}{qty_note}"
    amount_str = f"{corrected_amount:,} تومان" if corrected_amount else "—"

    if amount_ok and auto_approve_enabled():
        ok, result_text = do_provision_order(order_id)
        correction_note = f" (OCR رقم انداخته بود، خام: {raw_amount:,} — خودکار تصحیح شد)" if was_corrected else ""
        try:
            send_via_admin_bot(ADMIN_CHAT_ID, f"✅ رسید بررسی و خودکار تأیید شد{correction_note} — سفارش #{order_id}\n{result_text}",
                               reply_markup=inline(ordinfo.account_buttons(order_id)))
            send_photo_via_admin_bot(ADMIN_CHAT_ID, receipt_path, caption=f"📎 رسید سفارش #{order_id} (برای سابقه — خودکار تأیید شد)")
        except Exception as e:
            print(f"[receipt] خبر تأیید خودکار به ربات ادمین نرسید: {e}", flush=True)
            notify_admin( f"✅ رسید بررسی و خودکار تأیید شد{correction_note} — سفارش #{order_id}\n{result_text}")
        return True, f"رسید دریافت شد و خودکار تأیید شد ✅ (سفارش #{order_id})"

    mismatch_note = ""
    if corrected_amount is not None and corrected_amount != total_price:
        mismatch_note = f"\n⚠️ مبلغ خونده‌شده از رسید با مبلغ سفارش یکی نیست! (رسید: {corrected_amount:,} / سفارش: {total_price:,})"
    elif was_corrected:
        mismatch_note = f"\n✏️ OCR رقم انداخته بود (خام: {raw_amount:,})، تصحیح شد."
    elif amount_ok:
        mismatch_note = "\n✅ مبلغ با سفارش جور بود، فقط چون حالت خودکار خاموشه نیاز به تأیید دستی داره."

    _ctx = ordinfo.order_context(order_id)
    caption = (
        f"🆕 سفارش جدید #{order_id}\n"
        f"مشتری: {uname_label}\n"
        f"سفارش: {action_note} — {total_price:,} تومان\n\n"
        f"💡 این مبلغ از کجا آمد:\n" + "\n".join("• " + x for x in _ctx["price_lines"][1:]) + "\n\n"
        f"🔎 خوانده‌شده از رسید (خودکار، لزوماً دقیق نیست):\n"
        f"مبلغ: {amount_str}\n"
        f"کد پیگیری: {fields['tracking'] or '—'}\n"
        f"تاریخ: {fields['date'] or '—'}\n"
        f"نام: {fields['name'] or '—'}"
        f"{mismatch_note}"
    )
    review_kb = inline([[{"text": "✅ تأیید و فعال‌سازی", "callback_data": f"approve:{order_id}"},
                          {"text": "❌ رد کردن", "callback_data": f"reject:{order_id}"}]]
                       + ordinfo.account_buttons(order_id, _ctx["accounts"], ctx=_ctx))
    try:
        # روی اسم مشتری کلیک = پنل مشتری در ربات ادمین (اکانت‌ها + چت)، نه پروفایل تلگرامش
        _cap = html.escape(caption, quote=False)
        if _ctx.get("tg_id"):
            _cap = _cap.replace(html.escape(uname_label, quote=False),
                                ordinfo.customer_link_html(_ctx["tg_id"], _ctx.get("name"), _ctx.get("uname")), 1)
        try:
            send_photo_via_admin_bot(ADMIN_CHAT_ID, receipt_path, caption=_cap[:1024], reply_markup=review_kb, parse_mode="HTML")
        except Exception as _e:
            print(f"[receipt] HTML caption failed, plain: {_e}", flush=True)
            send_photo_via_admin_bot(ADMIN_CHAT_ID, receipt_path, caption=caption.replace("@", "")[:1024], reply_markup=review_kb)
    except Exception as e:
        # سفارش نباید بی‌صاحب بماند، ولی مسیر پشتیبان هم نباید دکمه‌ی تأیید
        # را وسط چتِ خریدِ ربات مشتری بیندازد — شناسه‌ی ادمین و خریدار یکی
        # است و دقیقاً همین باعث شده بود صاحب پروژه بگوید «تأیید را به خودم
        # واگذار کرده». فقط خبر می‌دهیم؛ خودِ تصمیم از پنل «🧾 سفارش‌ها» در
        # ربات ادمین گرفته می‌شود که همیشه در دسترس است.
        print(f"[receipt] ارسال به ربات ادمین نشد: {e}", flush=True)
        notify_admin(f"{caption}\n\n⚠️ عکس رسید فرستاده نشد. از «🧾 سفارش‌ها» تصمیم بگیر.")
    return True, f"رسید دریافت شد ✅ (سفارش #{order_id}) — به‌محض تأیید، سرویس فعال می‌شه."


# ---------- owner 2026-11-14: رسیدِ متنی (اشتراک‌گذاری از اپ بانک به‌جای عکس) ----------
_FA2EN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def looks_like_bank_receipt(text):
    t = text or ""
    return ("مبلغ" in t and ("ریال" in t or "تومان" in t)) or (("پیگیری" in t or "مرجع" in t) and ("واریز" in t or "انتقال" in t))


def parse_text_receipt(text):
    """→ (amount_toman or None, unit 'rial'|'toman'|None, raw_number, tracking)"""
    t = (text or "").translate(_FA2EN)
    amt = unit = raw = None
    m = re.search(r"مبلغ[^\d\n]{0,12}([\d][\d,٬.\s]*\d|\d)\s*(ریال|تومان)?", t)
    if m:
        raw = int(re.sub(r"[^\d]", "", m.group(1)) or 0)
        unit = "toman" if m.group(2) == "تومان" else ("rial" if m.group(2) == "ریال" else None)
        amt = raw // 10 if unit == "rial" else raw
    tr = re.search(r"(?:پیگیری|مرجع|ارجاع)[^\d\n]{0,12}(\d{6,})", t)
    return amt, unit, raw, (tr.group(1) if tr else None)


def handle_text_receipt(chat_id, user, text):
    c = orders_db()
    row = c.execute("SELECT id FROM orders WHERE tg_id=? AND status IN ('awaiting_receipt','pending_review') AND created_at>? ORDER BY id DESC LIMIT 1",
                    (chat_id, int(time.time() * 1000) - 48 * 3600000)).fetchone()
    c.close()
    if not row:
        return False
    order_id = row[0]
    o = get_order(order_id)
    total = o[7] or 0
    amt, unit, raw, tracking = parse_text_receipt(text)
    attach_receipt(order_id, "", text[:1500], amt, tracking, None, None, False)
    uname = f"{user.get('first_name', '')} ({user.get('username') or chat_id})"
    if amt == total:
        verdict = "✅ مبلغ با سفارش جور است (رسید متنی — قبل از تأیید، واریز را در حساب چک کن)"
        cust = f"رسیدت ثبت شد ✅ (سفارش #{order_id}) — به‌محض تأیید، سرویست فعال می‌شه."
    elif unit == "rial" and raw == total:
        verdict = (f"⚠️ مشتری عدد سفارش را به‌جای تومان، به ریال زده: {raw:,} ریال = {amt:,} تومان واریز شده؛ "
                   f"سفارش {total:,} تومان است (کسری {total - amt:,} تومان).")
        cust = (f"رسیدت رسید، ولی مبلغ به <b>ریال</b> واریز شده: {raw:,} ریال = {amt:,} تومان.\n"
                f"مبلغ سفارش #{order_id} <b>{total:,} تومان</b> (یعنی {total * 10:,} ریال) است.\n"
                f"لطفاً با «🆘 پشتیبانی» هماهنگ کن تا مابه‌التفاوت ({total - amt:,} تومان) رو بگیم.")
    else:
        verdict = (f"⚠️ مبلغ خوانده‌شده {amt:,} تومان با سفارش ({total:,} تومان) جور نیست." if amt
                   else "⚠️ مبلغ از متن خوانده نشد.")
        cust = f"رسیدت ثبت شد (سفارش #{order_id}) — پشتیبانی بررسیش می‌کنه و خبرت می‌کنیم."
    body = (f"🧾 رسید متنی برای سفارش #{order_id}\nمشتری: {uname}\nمبلغ سفارش: {total:,} تومان\n"
            f"{verdict}\n" + (f"کد پیگیری: {tracking}\n" if tracking else "") + f"\n— متن رسید —\n{text[:1200]}")
    kb = {"inline_keyboard": [[{"text": "✅ تأیید و فعال‌سازی", "callback_data": f"approve:{order_id}"},
                               {"text": "❌ رد کردن", "callback_data": f"reject:{order_id}"}],
                              [{"text": "↩️ پاسخ به مشتری", "callback_data": f"supreply:{chat_id}"}]]
          + ordinfo.account_buttons(order_id)}
    try:
        notify_admin(body, reply_markup=kb)
    except Exception as e:
        print(f"[text-receipt] notify failed: {e}", flush=True)
    send_message(chat_id, cust, reply_markup=main_menu(chat_id), parse_mode="HTML")
    return True


_ORDER_WAIT_NOTIFIED = set()


def notify_order_waiting(order_id, method):
    """owner 2026-11-14: هر سفارشی که به صفحه‌ی پرداخت رسید، همان لحظه به ادمین خبر داده شود
    (با تأیید/رد) — نه فقط بعد از رسید یا پیامک بانک."""
    if order_id in _ORDER_WAIT_NOTIFIED:
        return
    _ORDER_WAIT_NOTIFIED.add(order_id)
    try:
        o = get_order(order_id)
        if not o or o[5] != "awaiting_receipt":
            return
        _, tg_id, uname, name, plan_key, status, qty, total, target = o
        plan = PLAN_BY_KEY.get(plan_key) or {"label": plan_key}
        c = orders_db(); r = c.execute("SELECT new_email FROM orders WHERE id=?", (order_id,)).fetchone(); c.close()
        what = (f"تمدید «{target}»" if target else f"اکانت جدید «{r[0]}»" if r and r[0] else "اکانت جدید") + f" — {plan['label']}"
        body = (f"🕓 سفارش #{order_id} — منتظر واریز\n"
                f"مشتری: {name or ''} ({uname or tg_id})\n{what}\n"
                f"مبلغ: {total:,} تومان (= {total * 10:,} ریال) — {'کارت‌به‌کارت' if method == 'card' else 'USDT'}\n\n"
                "اگر پیامک بانک برسد خودکار تأیید می‌شود؛ اگر نرسید، بعد از چک کردن حساب از همین‌جا تأیید کن.")
        kb = {"inline_keyboard": [[{"text": "✅ تأیید و فعال‌سازی", "callback_data": f"ordact:approve:{order_id}"},
                                   {"text": "❌ رد کردن", "callback_data": f"ordact:reject:{order_id}"}]]
              + ordinfo.account_buttons(order_id)}
        notify_admin(body, reply_markup=kb)
    except Exception as e:
        print(f"[order-wait] notify {order_id}: {e}", flush=True)


def handle_receipt_photo(chat_id, user, photo_file_id):
    c = orders_db()
    cur = c.cursor()
    # owner 2026-11-14 (a customer): رسیدِ پرداختی که همین الان با پیامک بانک تأیید شده به سفارشِ کهنه‌ی دیروز
    # چسبیده بود. اول: اگر در ۳۰ دقیقه‌ی اخیر سفارشی تأیید شده، رسید مال همان است. دوم: فقط سفارش‌های ۴۸ ساعت اخیر.
    now_ms = int(time.time() * 1000)
    rec = cur.execute("SELECT id FROM orders WHERE tg_id=? AND status='approved' AND decided_at>? ORDER BY decided_at DESC LIMIT 1",
                      (chat_id, now_ms - 30 * 60000)).fetchone()
    cur.execute("SELECT id FROM orders WHERE tg_id=? AND status='awaiting_receipt' AND created_at>? ORDER BY id DESC LIMIT 1",
                (chat_id, now_ms - 48 * 3600000))
    row = cur.fetchone()
    c.close()
    if rec and (not row or row[0] < rec[0]):
        send_message(chat_id, f"پرداختت برای سفارش #{rec[0]} قبلاً تأیید شده و اکانتت فعاله ✅\n"
                              "حجم و زمان باقی‌مونده رو از «📶 وضعیت سرویس من» ببین.", reply_markup=main_menu())
        return
    if not row:
        send_message(chat_id, "سفارش بازی برای ثبت رسید پیدا نکردم. اول از «🛒 خرید سرویس» یه پلن انتخاب کن.", reply_markup=main_menu())
        return
    order_id = row[0]
    try:
        image_bytes = download_telegram_file(photo_file_id)
    except Exception as e:
        send_message(chat_id, f"خطا در دانلود عکس: {e}", reply_markup=main_menu())
        return
    uname = f"@{user['username']}" if user.get("username") else str(chat_id)
    ok, msg = process_receipt_for_order(order_id, image_bytes, f"{user.get('first_name','')} ({uname})")
    send_message(chat_id, msg, reply_markup=main_menu())



def renew_existing_client(email, gb, days):
    """Same 'reset usage, extend expiry' semantics as the admin bot's renew —
    keeps `clients` + `client_inbounds` and the inbounds.settings JSON in sync."""
    c = sqlite3.connect(X_UI_DB)
    cur = c.cursor()
    cur.execute("SELECT id, uuid FROM clients WHERE email=?", (email,))
    row = cur.fetchone()
    if not row:
        c.close()
        return None
    client_id, cid = row
    total_bytes = int(gb * 1024 ** 3) if gb else 0
    new_expiry_ms = delayed_expiry_ms(days)
    now_ms = int(time.time() * 1000)

    cur.execute("UPDATE clients SET total_gb=?, expiry_time=?, updated_at=?, enable=1 WHERE id=?",
                (total_bytes, new_expiry_ms, now_ms, client_id))
    upsert_traffic_row(cur, email, total_bytes, new_expiry_ms, 1, reset_usage=True)

    cur.execute("SELECT id, settings FROM inbounds WHERE id IN (1,2) ORDER BY id")
    rows = {r[0]: json.loads(r[1]) for r in cur.fetchall()}
    for iid, data in rows.items():
        for cl in data["clients"]:
            if cl.get("id") == cid or cl.get("email") == email:
                cl["totalGB"] = total_bytes
                cl["expiryTime"] = new_expiry_ms
                cl["enable"] = True
        cur.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(data), iid))

    c.commit()
    c.close()
    subprocess.run(["systemctl", "restart", "x-ui"], check=True)

    cur2 = sqlite3.connect(X_UI_DB).cursor()
    cur2.execute("SELECT sub_id FROM clients WHERE email=?", (email,))
    sub_id = cur2.fetchone()[0]
    link = f"https://{sub_domain_current()}:2087/sub/{sub_id}"
    return link, new_expiry_ms


def client_tg_id(email):
    c = sqlite3.connect(X_UI_DB)
    cur = c.cursor()
    cur.execute("SELECT tg_id FROM clients WHERE email=?", (email,))
    row = cur.fetchone()
    c.close()
    return row[0] if row and row[0] else None


def set_client_enabled(email, enabled):
    """Suspends/restores a client (both the `clients` table 3x-ui's UI reads
    and the inbounds.settings JSON Xray actually reads — the same dual-update
    rule as everywhere else client state is mutated)."""
    c = sqlite3.connect(X_UI_DB)
    cur = c.cursor()
    cur.execute("SELECT id, uuid FROM clients WHERE email=?", (email,))
    row = cur.fetchone()
    if not row:
        c.close()
        return False
    client_id, cid = row
    cur.execute("UPDATE clients SET enable=? WHERE id=?", (1 if enabled else 0, client_id))
    cur.execute("SELECT id, settings FROM inbounds WHERE id IN (1,2) ORDER BY id")
    rows = {r[0]: json.loads(r[1]) for r in cur.fetchall()}
    for iid, data in rows.items():
        for cl in data["clients"]:
            if cl.get("id") == cid or cl.get("email") == email:
                cl["enable"] = enabled
        cur.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(data), iid))
    c.commit()
    c.close()
    subprocess.run(["systemctl", "restart", "x-ui"], check=True)
    return True


def delete_client(email):
    c = sqlite3.connect(X_UI_DB)
    cur = c.cursor()
    cur.execute("SELECT id, uuid FROM clients WHERE email=?", (email,))
    row = cur.fetchone()
    if not row:
        c.close()
        return False
    client_id, cid = row
    cur.execute("DELETE FROM client_inbounds WHERE client_id=?", (client_id,))
    cur.execute("DELETE FROM clients WHERE id=?", (client_id,))
    # email is UNIQUE in client_traffics: leaving the row behind would make a
    # later account with the same name inherit this one's usage.
    cur.execute("DELETE FROM client_traffics WHERE email=?", (email,))
    cur.execute("SELECT id, settings FROM inbounds WHERE id IN (1,2) ORDER BY id")
    rows = {r[0]: json.loads(r[1]) for r in cur.fetchall()}
    for iid, data in rows.items():
        data["clients"] = [cl for cl in data["clients"] if cl.get("id") != cid and cl.get("email") != email]
        cur.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(data), iid))
    c.commit()
    c.close()
    subprocess.run(["systemctl", "restart", "x-ui"], check=True)
    return True


# ---------- abuse detection: repeated resend-link within a short window ----------
# Rationale: a burst of "resend link" presses is either a confused customer
# (fine — admin's unsuspend button fixes that in one tap) or someone probing/
# hammering the bot. Auto-suspending on the 3rd hit in an hour, rather than
# just alerting, stops the latter from costing anything while it waits on
# admin review.

RESEND_LINK_LOG = {}
RESEND_LIMIT = 3
RESEND_WINDOW_S = 3600


def record_resend_and_check_abuse(chat_id):
    now = time.time()
    log = RESEND_LINK_LOG.setdefault(chat_id, [])
    log[:] = [t for t in log if now - t < RESEND_WINDOW_S]
    log.append(now)
    if len(log) >= RESEND_LIMIT:
        log.clear()  # don't re-fire the alert on every press while already suspended
        return True
    return False


# ---------- abuse detection: repeated failed "claim old account" attempts ----------
# Rationale: unlike resend-link, a failed claim has no client to suspend — the
# real risk here is username enumeration (guessing other people's account
# names). So instead of suspending anything, this alerts the admin AND
# temporarily blocks further claim attempts from that Telegram user, which
# stops the guessing itself rather than just reporting it after the fact.

CLAIM_ATTEMPT_LOG = {}
CLAIM_LIMIT = 5
CLAIM_WINDOW_S = 3600
CLAIM_BLOCK_S = 3600
CLAIM_BLOCKED_UNTIL = {}


def claim_blocked(chat_id):
    return time.time() < CLAIM_BLOCKED_UNTIL.get(chat_id, 0)


def record_claim_failure_and_check_abuse(chat_id):
    now = time.time()
    log = CLAIM_ATTEMPT_LOG.setdefault(chat_id, [])
    log[:] = [t for t in log if now - t < CLAIM_WINDOW_S]
    log.append(now)
    if len(log) >= CLAIM_LIMIT:
        log.clear()
        return True
    return False


def link_tg_to_existing_client(username, tg_id):
    """Claims a pre-bot client (created via the admin bot, tg_id still 0) for a
    Telegram user. Returns sub_id on success, 'taken' if already linked to a
    different tg_id, or None if no such client exists."""
    c = sqlite3.connect(X_UI_DB)
    cur = c.cursor()
    cur.execute("SELECT id, email, uuid, sub_id, tg_id FROM clients WHERE email=? COLLATE NOCASE", (username,))
    row = cur.fetchone()
    if not row:
        c.close()
        return None
    client_id, email, cid, sub_id, existing_tg = row
    if existing_tg and existing_tg != tg_id:
        c.close()
        return "taken"

    cur.execute("UPDATE clients SET tg_id=? WHERE id=?", (tg_id, client_id))
    cur.execute("SELECT id, settings FROM inbounds WHERE id IN (1,2) ORDER BY id")
    rows = {r[0]: json.loads(r[1]) for r in cur.fetchall()}
    for iid, data in rows.items():
        for cl in data["clients"]:
            if cl.get("id") == cid or cl.get("email") == email:
                cl["tgId"] = tg_id
        cur.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(data), iid))
    c.commit()
    c.close()
    subprocess.run(["systemctl", "restart", "x-ui"], check=True)
    return sub_id


def do_provision_order(order_id):
    """Shared by button-approve and SMS-amount auto-approve. Returns (ok, message_for_admin)."""
    row = get_order(order_id)
    if not row:
        return False, "این سفارش پیدا نشد."
    _, tg_id, tg_username, tg_name, plan_key, status, quantity, total_price, target_email = row
    if status == "approved":
        return False, f"سفارش #{order_id} قبلاً تأیید شده."
    plan = plan_for_key(plan_key)
    gb_label = "نامحدود" if not plan["gb"] else f"{plan['gb']}GB"
    # هدیه‌ی دعوت‌شده: فقط تک‌اکانت حجمی، فقط اولین خرید
    try:
        ref_bonus = pending_referee_bonus(tg_id) if (plan["gb"] and (quantity or 1) == 1) else 0
    except Exception as e:
        print("[referral] bonus check failed:", e, flush=True)
        ref_bonus = 0
    plan_gb = plan["gb"] + ref_bonus if plan["gb"] else 0
    if ref_bonus:
        gb_label = f"{plan['gb']}GB + {ref_bonus}GB هدیه‌ی دعوت 🎁"

    if target_email:
        try:
            result = renew_existing_client(target_email, plan_gb, plan["days"])
        except Exception as e:
            return False, f"خطا در تمدید سفارش #{order_id}: {e}"
        if result is None:
            return False, f"اکانت «{target_email}» پیدا نشد (شاید حذف شده)."
        link, expiry_ms = result
        set_order_status(order_id, "approved")
        set_order_client_email(order_id, target_email)
        app_password = ensure_app_credentials(target_email)
        login_block = format_app_login_block(target_email, app_password) if app_password else ""
        send_connection_bundle(
            tg_id, target_email, link,
            header=(f"پرداختت تأیید شد ✅ — «{target_email}» تمدید شد.\n"
                    f"پلن: {gb_label} / {plan['days']} روز\nمصرف قبلی صفر شد."),
            reply_markup=main_menu(),
            extra_block=login_block,
        )
        _referral_after_approve(order_id, tg_id)
        return True, f"سفارش #{order_id} (تمدید {target_email}) تأیید و انجام شد ✅"

    quantity = quantity or 1
    base_name = tg_username or tg_name or f"user{tg_id}"
    try:
        _c = orders_db(); _r = _c.execute("SELECT new_email FROM orders WHERE id=?", (order_id,)).fetchone(); _c.close()
        want = _r[0] if _r and _r[0] else None
    except Exception:
        want = None
    root = root_account(tg_id) if quantity == 1 else None
    try:
        if quantity == 1 and (want or root):
            # اکانت جدیدِ مشتریِ قدیمی: زیر اولین اکانتش (ali-Zahra / ali-2)
            _x = sqlite3.connect(X_UI_DB)
            free = lambda e: _x.execute("SELECT 1 FROM clients WHERE lower(email)=lower(?)", (e,)).fetchone() is None
            if want and free(want):
                email = want                       # همان اسمی که موقع سفارش رزرو شد
            elif root:
                email = child_email(root, want[len(root) + 1:] if want and want.startswith(root + "-") else None)
            else:
                email = unique_email(want or base_name)
            _x.close()
            link, qr = provision_client(email, plan_gb, plan["days"], tg_id)
            results = [(email, link, qr)]
            if root:
                set_account_parent(email, root, tg_id)
        else:
            results = provision_bulk_for_customer(base_name, quantity, plan_gb, plan["days"], tg_id)
    except Exception as e:
        return False, f"خطا در فعال‌سازی سفارش #{order_id}: {e}"
    set_order_status(order_id, "approved")
    set_order_client_email(order_id, [r[0] for r in results])

    if quantity == 1:
        email, link, qr = results[0]
        app_password = ensure_app_credentials(email)
        send_connection_bundle(
            tg_id, email, link,
            header=f"پرداختت تأیید شد ✅\nپلن: {gb_label} / {plan['days']} روز",
            reply_markup=main_menu(),
            extra_block=(format_app_login_block(email, app_password) if app_password else ""),
        )
    else:
        send_message(tg_id, f"پرداختت تأیید شد ✅ — بسته‌ی {quantity} اکانتی ({gb_label} / {plan['days']} روز) فعال شد.", reply_markup=main_menu())
        # خرید عمده هم همون بسته‌ی واحد رو می‌گیره، فقط به‌ازای هر اکانت یکی —
        # فروشنده برای هر مشتریِ خودش به QR و هر دو لینک نیاز داره.
        for email, link, _ in results:
            app_password = ensure_app_credentials(email)
            send_connection_bundle(
                tg_id, email, link,
                header=f"👤 {email}",
                extra_block=(format_app_login_block(email, app_password) if app_password else ""),
            )

    _referral_after_approve(order_id, tg_id)
    return True, f"سفارش #{order_id} تأیید و فعال شد ✅ ({quantity} اکانت، پایه: {base_name})"


def _referral_after_approve(order_id, tg_id):
    cp = active_coupon(tg_id)
    if cp:
        # فقط اگر همین سفارش با همین کوپن تخفیف خورده (نه بسته‌ی عمده یا سفارشی که جشنواره‌ی بزرگ‌تر داشت)
        try:
            c = orders_db()
            r = c.execute("SELECT plan_key, base_price, quantity FROM orders WHERE id=?", (order_id,)).fetchone()
            c.close()
            plan = PLAN_BY_KEY.get(r[0]) if r else None
            if plan and (r[2] or 1) == 1 and r[1] and r[1] == sale_price(plan["price"], cp):
                mark_coupon_used(tg_id, order_id)
        except Exception as e:
            print(f"[coupon] after-approve check failed for #{order_id}: {e}", flush=True)
    try:
        on_order_approved_referral(order_id, tg_id)
    except Exception as e:
        print(f"[referral] after-approve failed for #{order_id}: {e}", flush=True)


def approve_order_headless(order_id):
    """تأیید سفارش بدون وابستگی به یک پیام تلگرامی مشخص.

    دکمه‌ی تأیید حالا در ربات ادمین است ولی منطق فعال‌سازی اینجاست. به‌جای
    کپی کردن کد در دو ربات — که یعنی دو نسخه که با هم از هم دور می‌شوند —
    ربات ادمین همین را از راه endpoint داخلی صدا می‌زند. یک منبع حقیقت.
    """
    return do_provision_order(order_id)


def revert_order_headless(order_id):
    """یک سفارشِ اشتباه تأییدشده را از دفتر برمی‌دارد.

    چرا لازم شد: یک واریز به سفارشِ کهنه‌ی مشتری دیگری خورد و آن سفارش
    approved شد، پس گزارش مالی مبلغی را درآمد حساب می‌کرد که هیچ‌وقت
    نیامده بود. تا قبل از این، تنها راه اصلاحش دست بردن مستقیم در
    دیتابیس بود — کاری که صاحب پروژه نباید مجبور به انجامش باشد.

    اکانت‌های ساخته‌شده عمداً دست‌نخورده می‌مانند: پس گرفتن سرویسی که به
    مشتری داده شده تصمیمِ آدم است نه یک عارضه‌ی جانبیِ اصلاح دفتر.
    client_email هم پاک نمی‌شود تا رد آن اکانت‌ها گم نشود.
    """
    row = get_order(order_id)
    if not row:
        return False, "این سفارش پیدا نشد."
    status = row[5]
    if status != "approved":
        return False, f"سفارش #{order_id} تأییدشده نیست (وضعیت: {status})."
    c = orders_db()
    c.execute("UPDATE orders SET status='rejected', receipt_tracking=NULL, receipt_date=NULL "
              "WHERE id=?", (order_id,))
    c.commit()
    c.close()
    emails = row[8] or ""
    note = f"\nاکانت‌های ساخته‌شده دست‌نخورده ماندند: {emails}" if emails else ""
    return True, (f"↩️ سفارش #{order_id} از دفتر برداشته شد — دیگر در گزارش مالی "
                  f"درآمد حساب نمی‌شود.{note}")


def reject_order_headless(order_id):
    row = get_order(order_id)
    if not row:
        return False, "این سفارش پیدا نشد."
    tg_id = row[1]
    set_order_status(order_id, "rejected")
    try:
        send_message(tg_id, "❌ پرداخت شما تایید نشد. اگر فکر می‌کنی اشتباهی رخ داده با پشتیبانی تماس بگیر.")
    except Exception as e:
        print(f"[reject] اطلاع به مشتری نرسید: {e}", flush=True)
    return True, f"سفارش #{order_id} رد شد."


def approve_order(admin_chat_id, message_id, order_id):
    ok, text = do_provision_order(order_id)
    edit_message(admin_chat_id, message_id, text)


# مشتری معمولاً عدد رُند واریز می‌کند، نه مبلغ یکتا. در لاگ واقعی دیده شد:
# سفارش ۶۰٬۰۰۹ تومان بود و واریز ۶۰٬۰۰۰ — تطابق دقیق شکست خورد و سفارش
# دستی ماند. این پنجره یعنی «سفارشی که همین چند ساعت پیش ثبت شده»، چون
# کسی که پول می‌دهد بلافاصله بعد از سفارش می‌دهد نه سه روز بعد.
ROUND_MATCH_WINDOW_SECONDS = 6 * 3600


def pending_orders_for_amount(amount):
    """سفارش‌های منتظری که این واریز می‌تواند مال آن‌ها باشد.

    اول تطابق دقیق با مبلغ یکتا (امن‌ترین و بدون ابهام). اگر نبود، تطابق با
    قیمت پایه ولی فقط بین سفارش‌های تازه — وگرنه یک واریز ۶۰٬۰۰۰ تومانی با
    شش سفارشِ رهاشده‌ی قدیمیِ هم‌قیمت جور درمی‌آید و معلوم نیست مال کدام است.

    خروجی: (فهرست شناسه‌ها، نوع تطابق). فهرست بیش از یکی یعنی مبهم —
    تصمیم با آدم، نه با حدس.
    """
    cutoff_ms = (time.time() - ROUND_MATCH_WINDOW_SECONDS) * 1000
    c = orders_db()
    cur = c.cursor()
    # ★ تطابق دقیق هم پنجره‌ی زمانی دارد. بدون آن یک سفارشِ رهاشده‌ی قدیمی
    # با مبلغ رُند (از قبل از سیستم مبلغ یکتا) با هر واریزِ هم‌مبلغِ تازه جور
    # درمی‌آمد. واقعاً اتفاق افتاد: واریز ۴۵۰٬۰۰۰ به سفارش #۱۰ که ۲۴ روز
    # پیش از مشتری دیگری ثبت شده بود خورد، ۱۰ اکانت رایگان ساخت، و چون هر
    # اکانت x-ui را ری‌استارت می‌کند همه‌ی مشتری‌ها قطع‌ووصل شدند — در حالی
    # که سفارش مشتریِ واقعی معطل ماند.
    cur.execute("SELECT id FROM orders WHERE total_price=? "
                "AND status IN ('awaiting_receipt','pending_review') "
                "AND created_at >= ? ORDER BY id ASC",
                (amount, cutoff_ms))
    exact = [r[0] for r in cur.fetchall()]
    if exact:
        c.close()
        return exact, "exact"

    # تطابق دقیق ولی کهنه: خودکار تأیید نکن، به ادمین بگو تصمیم بگیرد
    cur.execute("SELECT id FROM orders WHERE total_price=? "
                "AND status IN ('awaiting_receipt','pending_review') ORDER BY id ASC",
                (amount,))
    stale_exact = [r[0] for r in cur.fetchall()]

    cur.execute("SELECT id FROM orders WHERE base_price=? "
                "AND status IN ('awaiting_receipt','pending_review') "
                "AND created_at >= ? ORDER BY id DESC",
                (amount, cutoff_ms))
    recent = [r[0] for r in cur.fetchall()]
    c.close()
    if recent:
        return recent, "round"
    if stale_exact:
        return stale_exact, "stale"
    return [], "none"


def oldest_pending_order_for_amount(amount):
    """سازگاری با فراخوان‌های قدیمی — فقط وقتی یک سفارشِ بی‌ابهام هست."""
    ids, _ = pending_orders_for_amount(amount)
    return ids[0] if len(ids) == 1 else None


def set_order_receipt_meta(order_id, tracking=None, date=None):
    """Attaches SMS-derived deposit metadata (masked card suffix as a
    tracking substitute, date/time) to an order — the same receipt_tracking/
    receipt_date columns the OCR-on-photo path fills in, so both routes into
    the financial reports/dispute-search stay consistent."""
    if not tracking and not date:
        return
    c = orders_db()
    if tracking and date:
        c.execute("UPDATE orders SET receipt_tracking=?, receipt_date=? WHERE id=?", (tracking, date, order_id))
    elif tracking:
        c.execute("UPDATE orders SET receipt_tracking=? WHERE id=?", (tracking, order_id))
    else:
        c.execute("UPDATE orders SET receipt_date=? WHERE id=?", (date, order_id))
    c.commit()
    c.close()


# پیامک بانک گاهی دوبار می‌رسد (یک‌بار از خود گوشی، یک‌بار از تلاش مجدد
# MacroDroid). در عمل دو ثانیه فاصله داشتند.
#
# ★ چرا مهم است: نسخه‌ی دوم سفارش را دوبار تأیید نمی‌کند (چون دیگر معطل
# نیست) ولی پیام «هیچ سفارش منتظری با این مبلغ پیدا نشد» می‌فرستد. صاحب
# پروژه با دیدن آن پیام فکر کرد تأیید خودکار شکست خورده و دستی یک سفارشِ
# کهنه‌ی دیگر را تأیید کرد. یعنی خودِ پیامِ گمراه‌کننده خسارت ساخت.
_SEEN_DEPOSITS = {}
DEPOSIT_DEDUP_SECONDS = 15 * 60


def deposit_already_handled(amount_raw, tracking, date):
    """آیا همین واریز را همین چند دقیقه پیش پردازش کرده‌ایم؟

    کلید از سه‌تایی مبلغ و کد پیگیری و تاریخ ساخته می‌شود — همان چیزی که
    بانک در پیامک می‌دهد. دو واریز واقعیِ جدا هیچ‌وقت هر سه را یکسان
    ندارند، چون تاریخ شامل دقیقه است.
    """
    now = time.time()
    for k, t in list(_SEEN_DEPOSITS.items()):
        if now - t > DEPOSIT_DEDUP_SECONDS:
            del _SEEN_DEPOSITS[k]
    key = (str(amount_raw), str(tracking or ""), str(date or ""))
    if key in _SEEN_DEPOSITS:
        return True
    _SEEN_DEPOSITS[key] = now
    return False


def handle_admin_paid_command(amount, tracking=None, date=None):
    ids, kind = pending_orders_for_amount(amount)
    print(f"[webhook] match result: amount={amount} -> {ids} ({kind})", flush=True)

    if kind == "stale":
        # مبلغ دقیقاً با یک سفارش می‌خورد ولی آن سفارش کهنه است. هرگز خودکار
        # تأیید نکن — همین حالت یک‌بار ۱۰ اکانت رایگان به مشتریِ اشتباه داد.
        rows = []
        for oid in ids[:6]:
            r = get_order(oid)
            if not r:
                continue
            rows.append([{"text": f"✅ #{oid} — {(r[7] or 0):,} تومان",
                          "callback_data": f"approve:{oid}"}])
        notify_admin(
            f"⚠️ واریز {amount:,} تومان فقط با سفارش‌های کهنه‌ی "
            f"#{', #'.join(str(i) for i in ids[:6])} جور درمی‌آید.\n"
            "خودکار تأیید نکردم. اگر واقعاً مال همان است، دستی بزن:",
            reply_markup=inline(rows) if rows else None)
        return

    if len(ids) > 1:
        # مبهم: هیچ‌وقت حدس نزن کدام سفارش است — اکانت ممکن است به مشتری
        # اشتباه برسد. فهرست را با دکمه بده تا ادمین یک‌ضربه‌ای تعیین کند.
        rows = []
        for oid in ids[:6]:
            r = get_order(oid)
            if not r:
                continue
            label = f"#{oid} — {(r[7] or 0):,} تومان"
            rows.append([{"text": f"✅ {label}", "callback_data": f"approve:{oid}"}])
        notify_admin(
                     f"💰 واریز {amount:,} تومان رسید ولی با {len(ids)} سفارش جور درمی‌آید.\n"
                     "کدام را فعال کنم؟",
                     reply_markup=inline(rows) if rows else None)
        return

    order_id = ids[0] if ids else None
    if not order_id:
        # ★ لحن این پیام عمداً عوض شد. نسخه‌ی قبلی فقط می‌گفت «پیدا نشد» و
        # همین باعث شد یک‌بار سفارشِ کهنه‌ی بی‌ربطی دستی تأیید شود. مبلغِ
        # یکتا یعنی اگر چیزی جور درنیامد، تأیید کردن یک سفارشِ دیگر تقریباً
        # همیشه اشتباه است.
        notify_admin(
            f"ℹ️ واریز {amount:,} تومان رسید ولی هیچ سفارش معطلی با این مبلغ نیست.\n"
            "معمولاً یعنی همین واریز قبلاً پردازش شده، یا مشتری مبلغ را "
            "گرد کرده.\n\n"
            "⚠️ به‌خاطر این پیام سفارش دیگری را تأیید نکن — مبلغ هر سفارش "
            "یکتاست. از «🧾 سفارش‌ها» ببین کدام واقعاً معطل است.")
        return

    if kind == "round":
        notify_admin(
                     f"ℹ️ واریز {amount:,} تومان با مبلغ دقیق سفارش #{order_id} یکی نبود، "
                     "ولی با قیمت پایه‌اش می‌خورد و تازه ثبت شده — همین را در نظر گرفتم.")

    set_order_receipt_meta(order_id, tracking, date)
    meta_note = ""
    if tracking or date:
        meta_note = f" (کارت: {tracking or '—'} / {date or '—'})"

    if not auto_approve_enabled():
        row = get_order(order_id)
        _, tg_id, tg_username, tg_name, plan_key, status, quantity, total_price, target_email = row
        plan = plan_for_key(plan_key)
        notify_admin(
                     f"💰 واریز {amount:,} تومان تشخیص داده شد (سفارش #{order_id}, پلن {plan['label']}){meta_note}, "
                     f"ولی حالت خودکار خاموشه — برای فعال‌سازی دستی روی دکمه‌ی تأیید همون سفارش بزن.")
        return

    ok, text = do_provision_order(order_id)
    prefix = f"💰 واریز بانکی تشخیص داده شد{meta_note}.\n" if ok else "💰 واریز بانکی تشخیص داده شد، ولی مشکلی پیش اومد.\n"
    _ctx = ordinfo.order_context(order_id)
    notify_admin(prefix + text + "\n\n💡 این مبلغ از کجا آمد:\n" + "\n".join("• " + x for x in _ctx["price_lines"]),
                 reply_markup=inline(ordinfo.account_buttons(order_id, _ctx["accounts"], ctx=_ctx)))


# ---------- admin: auto/manual approval toggle ----------

def auto_toggle_keyboard():
    on = auto_approve_enabled()
    return inline([[
        {"text": ("✅ " if on else "") + "خودکار", "callback_data": "autoset:1"},
        {"text": ("✅ " if not on else "") + "دستی", "callback_data": "autoset:0"},
    ]])


def handle_admin_settings(chat_id):
    state = "خودکار" if auto_approve_enabled() else "دستی"
    send_message(chat_id,
                 f"حالت فعلی تأیید پرداخت: **{state}**\n\n"
                 f"خودکار: واریز شناسایی‌شده بلافاصله خودش تأیید و فعال می‌شه.\n"
                 f"دستی: فقط بهت خبر می‌ده، تأیید نهایی با خودته.",
                 reply_markup=auto_toggle_keyboard())


def reject_order(admin_chat_id, message_id, order_id):
    row = get_order(order_id)
    if not row:
        edit_message(admin_chat_id, message_id, "این سفارش پیدا نشد.")
        return
    _, tg_id = row[0], row[1]
    set_order_status(order_id, "rejected")
    edit_message(admin_chat_id, message_id, f"سفارش #{order_id} رد شد.")
    send_message(tg_id, "متأسفانه رسید پرداختت تأیید نشد. لطفاً با پشتیبانی در ارتباط باش یا دوباره تلاش کن.", reply_markup=main_menu())


CLAIM_WAIT = set()


def prompt_claim(chat_id):
    CLAIM_WAIT.add(chat_id)
    send_message(chat_id,
                  "هنوز سرویسی برای این حساب تلگرام پیدا نکردم.\n\n"
                  "اگه قبلاً (قبل از این بات) یه اکانت VPN داشتی، اسم کاربری‌ای که موقع ساختنش بهت دادن رو همینجا بفرست تا وصلش کنم بهت.\n\n"
                  "اگه اکانت نداری، از «🛒 خرید سرویس» شروع کن.",
                  reply_markup=main_menu())


def handle_claim_attempt(chat_id, user, text):
    CLAIM_WAIT.discard(chat_id)
    if claim_blocked(chat_id):
        send_message(chat_id, "به‌خاطر تلاش‌های ناموفق زیاد، اتصال اکانت موقتاً محدود شده. با پشتیبانی تماس بگیر.", reply_markup=main_menu())
        return
    username = text.strip().lstrip("@")
    result = link_tg_to_existing_client(username, chat_id)
    if result is None:
        if record_claim_failure_and_check_abuse(chat_id):
            CLAIM_BLOCKED_UNTIL[chat_id] = time.time() + CLAIM_BLOCK_S
            uname = f"@{user['username']}" if user.get("username") else str(chat_id)
            notify_admin(
                          f"🚨 <b>هشدار: تلاش‌های زیاد برای حدس‌زدن اسم اکانت</b>\n\n"
                          f"حساب تلگرام {esc(uname)} (id=<code>{chat_id}</code>) توی یک ساعت {CLAIM_LIMIT} بار اسم اشتباه برای اتصال اکانت زده. "
                          f"برای {CLAIM_BLOCK_S // 60} دقیقه از این عملیات موقتاً محروم شد.",
                          parse_mode="HTML")
            send_message(chat_id, "به‌خاطر تلاش‌های ناموفق زیاد، اتصال اکانت موقتاً محدود شده. با پشتیبانی تماس بگیر.", reply_markup=main_menu())
            return
        send_message(chat_id,
                      f"اکانتی با نام «{username}» پیدا نکردم. دوباره روی «🔄 دریافت دوباره‌ی لینک در صورت قطعی» بزن و امتحان کن، یا با پشتیبانی تماس بگیر.",
                      reply_markup=main_menu())
        return
    if result == "taken":
        send_message(chat_id, "این اکانت قبلاً به یه حساب تلگرام دیگه وصل شده. اگه فکر می‌کنی مال خودته، با پشتیبانی تماس بگیر.", reply_markup=main_menu())
        return
    sub_id = result
    link = f"https://{sub_domain_current()}:2087/sub/{sub_id}"
    send_connection_bundle(
        chat_id, username, link,
        header=f"اکانت «{username}» با موفقیت به این حساب تلگرام وصل شد ✅",
        reply_markup=main_menu(),
        extra_block="\n\nاز این به بعد با «🔄 دریافت دوباره‌ی لینک در صورت قطعی» و «📶 وضعیت سرویس من» می‌تونی مدیریتش کنی.",
    )
    uname = f"@{user['username']}" if user.get("username") else str(chat_id)
    notify_admin( f"✅ اکانت قدیمی «{username}» به حساب تلگرام {uname} (id={chat_id}) وصل شد.")


# ---------- customer-facing account manager ----------
# A reseller can hold 100+ accounts, so this is paginated and label-driven:
# dumping every account into one message would blow past Telegram's 4096-char
# limit and produce an unreadable wall of near-identical names.

ACCOUNTS_PAGE_SIZE = 6
RENAME_WAIT = {}
APP_REGISTER_WAIT = {}  # chat_id -> {"email": ..., "username": ...} — منتظر فقط پسورد

XRAY_BIN = "/usr/local/x-ui/bin/xray-linux-arm64"
XRAY_API_ADDR = "127.0.0.1:62789"


_ONLINE_CACHE = {"emails": set(), "ts": 0.0}
ONLINE_CACHE_TTL = 5  # seconds


def get_online_emails():
    """Live connection state from Xray's own stats API. The
    client_traffics.last_online column in the DB is NOT usable for this —
    it was found lagging ~40 hours behind reality.

    Cached for ONLINE_CACHE_TTL seconds. On failure (timeout, bad output),
    the last known-good result is returned instead of an empty set — an
    empty set would render every client offline, which is a false reading,
    not a real one."""
    now = time.time()
    if now - _ONLINE_CACHE["ts"] < ONLINE_CACHE_TTL:
        return _ONLINE_CACHE["emails"]
    try:
        out = subprocess.run(
            [XRAY_BIN, "api", "statsgetallonlineusers", f"--server={XRAY_API_ADDR}"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        emails = set()
        for u in json.loads(out).get("users", []):
            parts = u.split(">>>")  # "user>>>{email}>>>online"
            if len(parts) >= 2:
                emails.add(parts[1])
        _ONLINE_CACHE["emails"] = emails
        _ONLINE_CACHE["ts"] = now
        return emails
    except Exception:
        _ONLINE_CACHE["ts"] = now
        return _ONLINE_CACHE["emails"]


def get_labels(emails):
    if not emails:
        return {}
    c = orders_db()
    cur = c.cursor()
    q = ",".join("?" for _ in emails)
    cur.execute(f"SELECT email, label FROM client_labels WHERE email IN ({q})", list(emails))
    out = dict(cur.fetchall())
    c.close()
    return out


def set_label(email, label):
    c = orders_db()
    c.execute("INSERT INTO client_labels (email, label, updated_at) VALUES (?,?,?) "
              "ON CONFLICT(email) DO UPDATE SET label=excluded.label, updated_at=excluded.updated_at",
              (email, label, int(time.time() * 1000)))
    c.commit()
    c.close()


def fmt_size(n):
    """Megabytes below a gigabyte — the 200MB trial plan rendered as "0GB",
    which looks to the customer like a broken account rather than a small one."""
    n = max(0, n or 0)
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.0f}MB"
    return f"{n / 1024 ** 3:.2f}".rstrip("0").rstrip(".") + "GB"


def remaining_time_fa(expiry_ms):
    """Plain int division showed an expired account as a negative number of
    days, which reads like a bug to the customer."""
    if not expiry_ms:
        return "بدون انقضا"
    if expiry_ms < 0:
        return f"{int(-expiry_ms / 86400000)} روز (از اولین اتصال)"
    remain = expiry_ms - time.time() * 1000
    if remain <= 0:
        return "منقضی"
    days = int(remain / 86400000)
    if days >= 1:
        return f"{days} روز"
    hours = int(remain / 3600000)
    return f"{hours} ساعت" if hours >= 1 else "کمتر از ۱ ساعت"


def account_summary(email, total_gb, expiry_ms, label=None):
    """One compact line: the name the customer chose, then the two numbers
    they actually care about."""
    used = traffic_for(email)
    vol = "نامحدود" if not total_gb else fmt_size(max(0, total_gb - used))
    return f"{label or email} • {vol} • {remaining_time_fa(expiry_ms)}"


def accounts_keyboard(chat_id, page=0):
    rows = find_client_by_tg(chat_id)
    labels = get_labels([r[0] for r in rows])
    online = get_online_emails()
    start = page * ACCOUNTS_PAGE_SIZE
    chunk = rows[start:start + ACCOUNTS_PAGE_SIZE]
    buttons = []
    for email, sub_id, total_gb, expiry_ms, enable in chunk:
        dot = "🔒 " if not enable else ("🔵 " if email in online else "🔴 ")
        buttons.append([{"text": dot + account_summary(email, total_gb, expiry_ms, labels.get(email)),
                          "callback_data": f"acc:view:{email}"}])
    nav = []
    if page > 0:
        nav.append({"text": "◀️ قبلی", "callback_data": f"acc:page:{page-1}"})
    if start + ACCOUNTS_PAGE_SIZE < len(rows):
        nav.append({"text": "بعدی ▶️", "callback_data": f"acc:page:{page+1}"})
    if nav:
        buttons.append(nav)
    buttons.append([{"text": "🎁 دعوت‌های من و هدیه‌ها", "callback_data": "ref:report"}])
    online_count = sum(1 for r in rows if r[0] in online)
    return inline(buttons), len(rows), online_count


def handle_status(chat_id, page=0, message_id=None):
    rows = find_client_by_tg(chat_id)
    if not rows:
        prompt_claim(chat_id)
        return
    kb, total, online_count = accounts_keyboard(chat_id, page)
    pages = (total + ACCOUNTS_PAGE_SIZE - 1) // ACCOUNTS_PAGE_SIZE
    header = (f"📶 سرویس‌های تو ({total} اکانت"
              + (f" — صفحه {page+1} از {pages}" if pages > 1 else "") + ")\n"
              f"🔵 {online_count} آنلاین   🔴 {total - online_count} آفلاین\n"
              "روی هر اکانت بزن:")
    if message_id:
        edit_message(chat_id, message_id, header, reply_markup=kb)
    else:
        send_message(chat_id, header, reply_markup=kb)


def show_account_detail(chat_id, message_id, email):
    rows = [r for r in find_client_by_tg(chat_id) if r[0] == email]
    if not rows:
        edit_message(chat_id, message_id, "این اکانت پیدا نشد.")
        return
    _, sub_id, total_gb, expiry_ms, enable = rows[0]
    label = get_labels([email]).get(email)
    used = traffic_for(email)
    vol_line = ("حجم کل: نامحدود" if not total_gb
                else f"حجم کل: {fmt_size(total_gb)}\n"
                     f"حجم مصرف‌شده: {fmt_size(used)}\n"
                     f"حجم باقی‌مانده: {fmt_size(max(0, total_gb - used))}")
    app_username = get_app_username(email)
    text = (f"📄 <b>{esc(label or email)}</b>\n"
            f"{DIVIDER_C}\n"
            f"شناسه اکانت: <code>{esc(email)}</code>\n"
            + (f"اسم دلخواه: {esc(label)}\n" if label else "")
            + f"اتصال: {'🔵 آنلاین' if email in get_online_emails() else '🔴 آفلاین'}\n"
            + f"وضعیت: {'فعال ✅' if enable else 'مسدود 🔒'}\n"
            + f"{vol_line}\n"
            + f"زمان باقی‌مانده: {remaining_time_fa(expiry_ms)}\n"
            + (f"یوزرنیم اپ: <code>{esc(app_username)}</code>" if app_username else "یوزرنیم اپ: هنوز ثبت‌نام نکردی"))
    app_row = ([{"text": "📱 ثبت‌نام اپ", "callback_data": f"acc:register:{email}"},
                {"text": "🔑 فراموشی پسورد", "callback_data": f"acc:forgotpw:{email}"}]
               if app_username else
               [{"text": "📱 ثبت‌نام اپ", "callback_data": f"acc:register:{email}"}])
    # دکمه‌ی اتصال مستقیم، مخصوصِ همین اکانت. برای فروشنده‌ای که چند اکانت
    # داره یعنی هر اکانت رو باز می‌کنه و همون‌جا دکمه‌ی خودش رو می‌بینه.
    rows_kb = []
    connect_row = app_connect_row(email)
    if connect_row:
        rows_kb.append(connect_row)
    rows_kb += [
        [{"text": "🔁 تمدید همین اکانت", "callback_data": f"renewpick:{email}"}],
        [{"text": "🔄 دریافت لینک", "callback_data": f"acc:link:{email}"},
         {"text": "✏️ تغییر اسم", "callback_data": f"acc:rename:{email}"}],
        app_row,
        [{"text": "🎁 دعوت‌های من و هدیه‌ها", "callback_data": "ref:report"}],
        [{"text": "◀️ برگشت به لیست", "callback_data": "acc:page:0"}],
    ]
    kb = inline(rows_kb)
    edit_message(chat_id, message_id, text, reply_markup=kb, parse_mode="HTML")


def handle_app_register_step(chat_id, text):
    """یوزرنیم از قبل (موقع زدن دکمه) خودکار از روی یوزرنیم تلگرام تعیین شده؛
    اینجا فقط پسورد دلخواه رو می‌گیریم. برای هر دو کلاینت‌های قدیمی و جدید
    یکسان کار می‌کنه، چون فقط به مالکیت تلگرامی اکانت (find_client_by_tg)
    تکیه می‌کنه، نه به اینکه قبلاً اعتبارنامه‌ای داشته یا نه."""
    state = APP_REGISTER_WAIT.get(chat_id)
    if not state:
        return
    email = state["email"]
    owns = any(r[0] == email for r in find_client_by_tg(chat_id))
    if not owns:
        APP_REGISTER_WAIT.pop(chat_id, None)
        send_message(chat_id, "این اکانت مال حساب تو نیست.", reply_markup=main_menu(chat_id))
        return

    password = text
    if len(password) < 4:
        send_message(chat_id, "پسورد خیلی کوتاهه — حداقل ۴ کاراکتر بفرست:")
        return
    APP_REGISTER_WAIT.pop(chat_id, None)
    set_app_credentials(email, state["username"], password)
    send_message(chat_id,
                  f"✅ ثبت‌نام اپ HermesVPN Connect برای «{esc(email)}» انجام شد.\n\n"
                  f"یوزرنیم: <code>{esc(state['username'])}</code>\n"
                  f"پسورد: <code>{esc(password)}</code>\n\n"
                  f"همین دو تا رو توی اپ (تنظیمات → حساب کاربری) وارد کن.",
                  reply_markup=main_menu(chat_id), parse_mode="HTML")


def handle_resend_link(chat_id):
    rows = find_client_by_tg(chat_id)
    if not rows:
        prompt_claim(chat_id)
        return
    if len(rows) > 1:
        # more than one account: let them say which, instead of silently
        # handing back whichever happens to be newest
        kb, total, _ = accounts_keyboard(chat_id, 0)
        send_message(chat_id, f"{total} اکانت داری — لینک کدومش رو می‌خوای؟", reply_markup=kb)
        return
    email, sub_id, total_gb, expiry_ms, enable = rows[0]
    if 0 < expiry_ms < time.time() * 1000:
        send_message(chat_id, "سرویست منقضی شده. برای تمدید از «🛒 خرید سرویس» استفاده کن یا با پشتیبانی تماس بگیر.", reply_markup=main_menu())
        return
    if not enable:
        send_message(chat_id, "اکانتت موقتاً مسدوده و منتظر بررسی ادمینه. با پشتیبانی در تماس باش.", reply_markup=main_menu())
        return

    if record_resend_and_check_abuse(chat_id):
        set_client_enabled(email, False)
        send_message(chat_id,
                      "به‌خاطر استفاده‌ی غیرعادی از این دکمه (چند بار پشت سر هم توی یه ساعت)، اکانتت موقتاً مسدود شد تا ادمین بررسی کنه.\n"
                      "اگه این یه اشتباه بوده (مثلاً مشکل اتصال داشتی)، با پشتیبانی در تماس باش.",
                      reply_markup=main_menu())
        notify_admin(
                      f"🚨 <b>هشدار سوءاستفاده احتمالی</b>\n\n"
                      f"اکانت «{esc(email)}» (تلگرام: <code>{chat_id}</code>) بیش از {RESEND_LIMIT} بار توی یک ساعت روی «دریافت دوباره‌ی لینک» زده و به‌صورت خودکار مسدود شد.\n\n"
                      f"اگه واقعی/اشتباهی بوده، رفع مسدودیت کن؛ اگه مشکوکه، حذفش کن.",
                      reply_markup=inline([[
                          {"text": "✅ رفع مسدودیت", "callback_data": f"unsuspend:{email}"},
                          {"text": "🗑 حذف کلاینت", "callback_data": f"deleteclient:{email}"},
                      ]]), parse_mode="HTML")
        return

    send_account_link(chat_id, email, sub_id)


def send_account_link(chat_id, email, sub_id=None):
    """Delivers one specific account's link — used both by the resend button
    and by the per-account 'دریافت لینک' action, so a customer holding many
    accounts always gets the one they actually picked."""
    if sub_id is None:
        rows = [r for r in find_client_by_tg(chat_id) if r[0] == email]
        if not rows:
            send_message(chat_id, "این اکانت پیدا نشد.", reply_markup=main_menu(chat_id))
            return
        sub_id = rows[0][1]
    label = get_labels([email]).get(email)
    title = f"{label} ({email})" if label else email
    link = f"https://{sub_domain_current()}:2087/sub/{sub_id}"
    send_connection_bundle(
        chat_id, email, link,
        header=f"لینک «{title}»:",
        reply_markup=main_menu(chat_id),
    )


ADMIN_BOT_TOKEN_FILE = "/root/.hermes-bot-token"


def notify_admin(text, reply_markup=None, parse_mode=None):
    """تنها راهِ خبر دادن به ادمین از داخل ربات مشتری.

    همه‌ی گزارش‌ها، تأییدها و مواردی که تعیین تکلیف می‌خواهند باید در
    *ربات ادمین* دیده شوند، نه در چتِ ربات مشتری. قبلاً همه‌جا مستقیم با
    توکن ربات مشتری به ADMIN_CHAT_ID فرستاده می‌شد؛ چون شناسه‌ی ادمین و
    خریدار یکی است، همه‌ی گزارش‌ها وسط چت خرید می‌افتاد.

    اگر ربات ادمین در دسترس نبود، مستقیم از API ربات مشتری می‌رود — یک
    گزارشِ جاافتاده بدتر از یک گزارش در چت اشتباه است. عمداً خودش را صدا
    نمی‌زند تا بازگشت بی‌نهایت نشود.
    """
    try:
        return send_via_admin_bot(ADMIN_CHAT_ID, text, reply_markup=reply_markup)
    except Exception as e:
        print(f"[notify] ربات ادمین در دسترس نبود، مسیر پشتیبان: {e}", flush=True)
        payload = {"chat_id": ADMIN_CHAT_ID, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        if parse_mode:
            payload["parse_mode"] = parse_mode
        return api_call("sendMessage", payload)


def send_photo_via_admin_bot(chat_id, path, caption=None, reply_markup=None, parse_mode=None):
    """عکس رسید را از طرف *ربات ادمین* می‌فرستد.

    قبلاً با send_photo معمولی می‌رفت، یعنی از طرف ربات مشتری. نتیجه‌اش این
    بود که دکمه‌های «تأیید/رد» در همان چتی ظاهر می‌شد که خریدار در آن است —
    و کال‌بکشان هم به ربات مشتری می‌رسید نه ربات ادمین. تأیید سفارش باید
    آنجایی باشد که ادمین کار می‌کند.
    """
    with open(ADMIN_BOT_TOKEN_FILE) as f:
        token = f.read().strip()
    fields = {"chat_id": str(chat_id)}
    if caption:
        fields["caption"] = caption
    if parse_mode:
        fields["parse_mode"] = parse_mode
    if caption:
        ordinfo.linkify_payload(fields, "caption")
        if fields.get("caption_entities"):
            fields["caption_entities"] = json.dumps(fields["caption_entities"])
    if reply_markup is not None:
        fields["reply_markup"] = json.dumps(reply_markup)
    with open(path, "rb") as fh:
        blob = fh.read()
    boundary = "----HermesAdminPhotoBoundary"
    body = b""
    for k, v in fields.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; "
             f"filename=\"{os.path.basename(path)}\"\r\n"
             f"Content-Type: application/octet-stream\r\n\r\n").encode()
    body += blob + b"\r\n" + f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendPhoto", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def send_via_admin_bot(chat_id, text, reply_markup=None):
    """پیام را از طرف *ربات ادمین* می‌فرستد، نه این ربات.

    چرا: دکمه‌ی «پاسخ به این گفتگو» باید در همان رباتی ظاهر شود که ادمین
    کارهای مدیریتی‌اش را آنجا می‌کند. اگر از این ربات فرستاده شود، ادمین
    باید بین دو چت جابه‌جا شود و کال‌بک دکمه هم به ربات اشتباه می‌رسد.
    """
    with open(ADMIN_BOT_TOKEN_FILE) as f:
        token = f.read().strip()
    payload = {"chat_id": chat_id, "text": text}
    ordinfo.linkify_payload(payload, "text")  # اسم اکانت/مشتری = لینک به پنلش در ربات ادمین
    if payload.get("entities"):
        payload["entities"] = json.dumps(payload["entities"])
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    data = urllib.parse.urlencode(payload).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=15) as resp:
        return json.loads(resp.read())


def log_support_message(tg_id, direction, text):
    """سابقه‌ی گفتگوی پشتیبانی (مشترک با ربات ادمین): in = از مشتری، out = از پشتیبانی."""
    try:
        c = orders_db()
        c.execute("""CREATE TABLE IF NOT EXISTS support_messages (
                 id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER NOT NULL,
                 direction TEXT NOT NULL, text TEXT NOT NULL, created_at INTEGER NOT NULL)""")
        c.execute("INSERT INTO support_messages (tg_id, direction, text, created_at) VALUES (?,?,?,?)",
                  (int(tg_id), direction, str(text)[:4000], int(time.time())))
        c.commit()
        c.close()
    except Exception as e:
        print(f"[support] ثبت سابقه نشد: {e}", flush=True)



def _support_pages(rows, label_in, label_out, budget=3300):
    """rows: [(id, direction, text, created_at)] oldest->newest. Pages are built from the
    newest backwards within a character budget; page 0 = newest. Returns list of
    [(id, rendered_text)] per page (each page oldest->newest inside)."""
    import jdatetime
    from datetime import datetime, timezone, timedelta
    tz = timezone(timedelta(hours=3, minutes=30))
    items = []
    for rid, d, t, ts in rows:
        when = jdatetime.datetime.fromgregorian(datetime=datetime.fromtimestamp(ts, tz).replace(tzinfo=None)).strftime("%Y/%m/%d %H:%M")
        who = label_in if d == "in" else label_out
        items.append((rid, f"{who} · {when}\n{html.escape(t)}"))
    pages, cur, size = [], [], 0
    for rid, txt in reversed(items):
        if cur and size + len(txt) > budget:
            pages.append(list(reversed(cur))); cur, size = [], 0
        cur.append((rid, txt)); size += len(txt) + 2
    if cur:
        pages.append(list(reversed(cur)))
    return pages


def _support_rows(conn, tg_id):
    conn.execute("""CREATE TABLE IF NOT EXISTS support_messages (
                 id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER NOT NULL,
                 direction TEXT NOT NULL, text TEXT NOT NULL, created_at INTEGER NOT NULL)""")
    return conn.execute("SELECT id, direction, text, created_at FROM support_messages WHERE tg_id=? AND direction IN ('in','out') ORDER BY id",
                        (int(tg_id),)).fetchall()


def customer_history_view(chat_id, page):
    """(text, keyboard) of the customer's own support chat, or (None, None) if empty."""
    try:
        c = orders_db(); rows = _support_rows(c, chat_id); c.close()
    except Exception:
        return None, None
    pages = _support_pages(rows, "👤 تو", "🛠 پشتیبانی")
    if not pages:
        return None, None
    page = max(0, min(page, len(pages) - 1))
    head = "💬 گفتگوی تو با پشتیبانی HermesVPN"
    if len(pages) > 1:
        head += f"  ({page + 1}/{len(pages)})"
    text = head + "\n\n" + "\n\n".join(t for _, t in pages[page])
    nav = []
    if page + 1 < len(pages):
        nav.append({"text": "◀️ قدیمی‌تر", "callback_data": f"myhist:{page + 1}"})
    if page > 0:
        nav.append({"text": "جدیدتر ▶️", "callback_data": f"myhist:{page - 1}"})
    return text, ({"inline_keyboard": [nav]} if nav else None)


def handle_support(chat_id):
    text, kb = customer_history_view(chat_id, 0)
    if text:
        send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")
    send_message(chat_id, "هر چی می‌خوای همینجا بنویس — به پشتیبانی می‌رسه و جوابت رو هم همینجا می‌گیری.", reply_markup=main_menu())
    # relay to admin is handled generically below for any unrecognized text


# ---------- owner 2026-11-14: فحش و بی‌احترامی → اخطار، محدودیت پشتیبانی، خبر به ادمین ----------
# سرویس VPN مشتری هیچ‌وقت خودکار قطع نمی‌شود (تشخیص کلمه‌ای خطا دارد و سرویس پولی است)؛
# ادمین با دکمه‌ی «🔒 مسدود کردن اکانت‌ها» تصمیم می‌گیرد.
MODERATION_ENABLED = True    # owner approved the texts 2026-11-14
MOD_RESTRICT_S = 24 * 3600
_W = lambda *ws: [w for w in ws]
MOD_SEVERE = _W("کیر", "کیرم", "کیری", "کیرت", "کص", "کس ننت", "کس ننه", "کس مادرت", "کس خواهرت", "کسننت", "کسکش", "کس‌کش", "کصکش", "کسخل", "کصخل", "کسشر", "کصشر", "جنده",
                "مادرجنده", "خارکسده", "خارکصده", "حرومزاده", "حرامزاده", "حرومی", "پدرسگ", "پدرسوخته", "کونی", "کونده",
                "گاییدم", "بگام", "میگام", "گوه", "گوهخوری", "بیناموس", "بی‌ناموس", "لاشی", "لاشخور", "تخمی", "تخم‌سگ",
                "kir", "kiram", "kos", "koskesh", "kosnanat", "jende", "madarjende", "koni", "kooni", "gayidam", "bisharaf",
                "binamoos", "lashi", "haromzade", "pedarsag", "fuck", "fucking", "bitch", "motherfucker", "asshole")
MOD_MILD = _W("احمق", "الاغ", "بی‌شعور", "بیشعور", "بی‌شرف", "بیشرف", "عوضی", "آشغال", "اشغال", "کثافت", "خفه‌شو", "خفه شو",
              "گمشو", "گم شو", "کلاهبردار", "کلاهبردارا", "کلاهبردارید", "کلاهبردارین", "دزد", "دزدا", "دزدید", "شیاد", "ahmagh", "olagh", "khafe sho", "gomsho", "kesafat")
_MOD_RX = {lvl: re.compile(r"(?<![\w\u200c])(" + "|".join(re.escape(w) for w in sorted(ws, key=len, reverse=True)) + r")(?![\w\u200c])", re.I)
           for lvl, ws in (("severe", MOD_SEVERE), ("mild", MOD_MILD))}
MOD_TXT_SEVERE = ("به‌دلیل استفاده از کلمات توهین‌آمیز، دسترسی شما به پشتیبانی تا ۲۴ ساعت محدود شد.\n"
                  "سرویس VPN شما فعال است و از منوی ربات قابل استفاده است. در صورت تکرار، اکانت شما مسدود می‌شود.")
MOD_TXT_MILD = "لطفاً در گفتگو احترام را رعایت کنید. پیام‌های توهین‌آمیز بررسی نمی‌شوند و ممکن است دسترسی شما به پشتیبانی محدود شود."
MOD_TXT_LOCKED = "دسترسی شما به پشتیبانی تا ساعت {until} محدود است. سرویس VPN و منوی ربات در دسترس است."


def _mod_db():
    c = orders_db()
    c.execute("CREATE TABLE IF NOT EXISTS moderation (tg_id INTEGER PRIMARY KEY, strikes INTEGER DEFAULT 0, restricted_until INTEGER DEFAULT 0, last_ts INTEGER)")
    return c


def mod_level(text):
    t = (text or "").replace("ي", "ی").replace("ك", "ک")
    t2 = re.sub(r"(.)\1{2,}", r"\1", t)                 # کیییر → کیر
    for lvl in ("severe", "mild"):
        if _MOD_RX[lvl].search(t) or _MOD_RX[lvl].search(t2):
            return lvl
    # «ک ی ر» / «k.i.r»: فقط برای پیام‌های کوتاه، فاصله‌ها را برداشته و دوباره چک کن
    squeezed = re.sub(r"[\s\u200c._*-]+", "", t2)
    if len(squeezed) <= 12 and _MOD_RX["severe"].fullmatch(squeezed):
        return "severe"
    return None


def mod_restricted_until(tg_id):
    try:
        c = _mod_db(); r = c.execute("SELECT restricted_until FROM moderation WHERE tg_id=?", (tg_id,)).fetchone(); c.close()
        return r[0] if r and r[0] and r[0] > time.time() else 0
    except Exception:
        return 0


def moderate_text(chat_id, user, text):
    """True = پیام همین‌جا تمام شد (اخطار داده شد / محدود است) و نباید به پشتیبانی برود."""
    if not MODERATION_ENABLED or chat_id == ADMIN_CHAT_ID:
        return False
    until = mod_restricted_until(chat_id)
    lvl = mod_level(text)
    try:
        menu_btns = {b for row in main_menu(chat_id)["keyboard"] for b in row}
    except Exception:
        menu_btns = set()
    if until and not lvl and text.strip() not in menu_btns:
        import datetime as _dt
        _tz = _dt.timezone(_dt.timedelta(hours=3, minutes=30))
        send_message(chat_id, MOD_TXT_LOCKED.format(until=_dt.datetime.fromtimestamp(until, _tz).strftime("%H:%M")))
        return True
    if not lvl:
        return False
    now = int(time.time())
    c = _mod_db()
    r = c.execute("SELECT strikes FROM moderation WHERE tg_id=?", (chat_id,)).fetchone()
    strikes = (r[0] if r else 0) + 1
    new_until = now + MOD_RESTRICT_S if lvl == "severe" else (until or 0)
    c.execute("INSERT OR REPLACE INTO moderation VALUES (?,?,?,?)", (chat_id, strikes, new_until, now))
    c.commit(); c.close()
    send_message(chat_id, MOD_TXT_SEVERE if lvl == "severe" else MOD_TXT_MILD)
    uname = f"{user.get('first_name', '')} ({user.get('username') or chat_id})"
    body = (f"🚫 {'فحش' if lvl == 'severe' else 'بی‌احترامی'} از مشتری {uname}\n"
            f"دفعه‌ی {strikes} — " + ("پشتیبانی ۲۴ ساعت محدود شد" if lvl == "severe" else "فقط اخطار داده شد") +
            f"\nسرویس VPN دست نخورده.\n\nمتن پیام:\n{text[:800]}")
    kb = {"inline_keyboard": [[{"text": "🔒 مسدود کردن اکانت‌های VPN", "callback_data": f"modblock:{chat_id}"},
                               {"text": "✅ رفع محدودیت", "callback_data": f"modlift:{chat_id}"}],
                              [{"text": "🙋 پنل مشتری", "callback_data": f"cust:{chat_id}"},
                               {"text": "↩️ پاسخ", "callback_data": f"supreply:{chat_id}"}]]}
    try:
        notify_admin(body, reply_markup=kb)
    except Exception as e:
        print(f"[moderation] notify: {e}", flush=True)
    return lvl == "severe" or bool(until)

# ======================= owner 2026-11-14: پشتیبانی هوشمند مشتری (Claude Haiku 5.5) =======================
# پیام آزاد مشتری → اول دستیار جواب می‌دهد (رسمی و کوتاه، با اطلاعات واقعی اکانت خودش). هر جا مطمئن نبود،
# بحث پول/استرداد/شکایت بود یا مشتری آدم خواست → escalate: همان پیام قبلی به ادمین (با خلاصه‌ی دستیار).
# اگر ادمین در ۳۰ دقیقه‌ی اخیر خودش با این مشتری حرف زده، دستیار کنار می‌کشد و پیام مستقیم به ادمین می‌رود.
SUPPORT_AI_ENABLED = True    # owner: «درستش کن» 2026-11-14
_sys.path.insert(0, "/usr/local/bin")
try:
    import hermesvpn_ai as AIM
except Exception as _e:
    AIM = None
    print("support AI module missing:", _e, flush=True)
SUP_AI_HIST = {}


def _sup_admin_active(chat_id, minutes=30):
    """owner 2026-11-14: «در همه موارد اول هوش مصنوعی جواب بده». فقط وقتی ادمین همین الان گفتگوی
    همین مشتری را در ربات مدیریت باز کرده (حالت پاسخ)، دستیار کنار می‌کشد تا دو نفر هم‌زمان جواب ندهند."""
    try:
        f = "/var/lib/hermesvpn-ai/support_target.json"
        if time.time() - os.path.getmtime(f) > 1800:   # حالت پاسخِ ادمین بعد از ۳۰ دقیقه بی‌کاری اعتبار ندارد
            return False
        t = json.load(open(f))
        return int(chat_id) in {int(v) for v in t.values()}
    except Exception:
        return False


def _sup_tools(chat_id, user, escalated):
    def my_accounts():
        rows = find_client_by_tg(chat_id)
        if not rows:
            return "این مشتری هیچ اکانتی ندارد."
        labels = get_labels([r[0] for r in rows])
        online = get_online_emails()
        out = []
        now = time.time() * 1000
        for email, sub_id, total_gb, expiry_ms, enable in rows[:15]:
            used = traffic_for(email)
            if expiry_ms and expiry_ms < 0:
                left = f"{int(-expiry_ms / 86400000)} روز (از اولین اتصال شروع می‌شود)"
            elif expiry_ms:
                left = f"{max(0, (expiry_ms - now) / 86400000):.0f} روز" if expiry_ms > now else "منقضی شده"
            else:
                left = "بدون انقضا"
            out.append({"account": email, "label": labels.get(email), "enabled": bool(enable), "online_now": email in online,
                        "total_gb": round(total_gb / 1024**3, 1) if total_gb else "نامحدود", "used_gb": round(used / 1024**3, 2),
                        "time_left": left})
        return json.dumps(out, ensure_ascii=False)

    def my_orders():
        c = orders_db()
        rows = c.execute("SELECT id, plan_key, status, total_price, target_email, created_at FROM orders WHERE tg_id=? ORDER BY id DESC LIMIT 6", (chat_id,)).fetchall()
        c.close()
        st = {"awaiting_receipt": "منتظر واریز", "pending_review": "رسید در حال بررسی", "approved": "انجام شده", "rejected": "رد شده", "cancelled": "لغو شده"}
        return json.dumps([{"order": i, "plan": p, "status": st.get(s_, s_), "amount_toman": t, "renew_of": tg} for i, p, s_, t, tg, cr in rows], ensure_ascii=False)

    def resend_links():
        handle_resend_link(chat_id)
        return "لینک‌ها همین الان برای مشتری فرستاده شد."

    def send_app_download():
        handle_app_download(chat_id)
        return "لینک دانلود اپ‌ها برای مشتری فرستاده شد."

    def escalate(summary, proposed_action="none", order_id=None, account=None, gb=None, days=None,
                 message_if_approved=None, message_if_rejected=None):
        escalated.append({"summary": summary, "action": proposed_action, "order_id": order_id, "account": account,
                          "gb": gb, "days": days, "ok": message_if_approved, "no": message_if_rejected})
        return "برای تصمیم صاحب کسب‌وکار ارسال شد."

    fn = lambda n, d, props=None, req=None: {"type": "function", "function": {"name": n, "description": d,
            "parameters": {"type": "object", "properties": props or {}, "required": req or []}}}
    tools = [fn("my_accounts", "This customer's VPN accounts: volume, used, time left, online, enabled."),
             fn("my_orders", "This customer's recent orders and their status."),
             fn("resend_links", "Send the customer fresh connection links for all their accounts (fixes most 'not connecting')."),
             fn("send_app_download", "Send the HermesVPN app download links (Android/Windows)."),
             fn("escalate", "ONLY for hard cases you cannot finish yourself: a payment to approve/reject, refund, free volume/time, "
                "complaint, account change, something you are unsure about, or the customer insists on a human. "
                "Investigate first with your tools, then propose ONE decision for the owner, who taps ✅/❌. "
                "summary: short Persian facts + your recommendation. proposed_action: approve_order|reject_order|topup|none. "
                "For topup give account + gb and/or days. message_if_approved / message_if_rejected: the exact short formal Persian "
                "text the customer will receive after the owner's decision.",
                {"summary": {"type": "string"},
                 "proposed_action": {"type": "string", "enum": ["approve_order", "reject_order", "topup", "none"]},
                 "order_id": {"type": "integer"}, "account": {"type": "string"}, "gb": {"type": "number"}, "days": {"type": "number"},
                 "message_if_approved": {"type": "string"}, "message_if_rejected": {"type": "string"}}, ["summary"])]
    impl = {"my_accounts": my_accounts, "my_orders": my_orders, "resend_links": resend_links,
            "send_app_download": send_app_download, "escalate": escalate}
    return tools, impl


def _sup_system():
    plans = "، ".join(f"{p['label']}: {p['price']:,} تومان" for p in PLANS)
    sale = flash_sale_note() or ""
    return (
        "تو پشتیبان هوشمند HermesVPN در تلگرام هستی و با یک مشتری حرف می‌زنی. "
        f"زمان: {AIM.jalali_today()}.\n"
        "لحن: رسمی، کوتاه و مؤدب؛ اگر مشتری گیج است کمی بیشتر توضیح بده و همکاری کن. فارسی. "
        "فقط درباره‌ی همین سرویس جواب بده. اطلاعات اکانت را فقط از ابزارها بگو و هرگز حدس نزن.\n"
        "کارهایی که خودت می‌کنی: وضعیت اکانت/حجم/زمان (my_accounts)، وضعیت سفارش (my_orders)، لینک دوباره برای وصل نشدن (resend_links)، "
        "لینک دانلود اپ (send_app_download)، راهنمای استفاده.\n"
        "همیشه اول خودت تا جای ممکن کار را حل کن (با ابزارها بررسی کن، توضیح بده، راه‌حل بده). "
        "فقط کارهای سخت را escalate کن: تأیید/رد پرداخت، استرداد، حجم/زمان رایگان یا جبرانی، شکایت جدی، چیزی که مطمئن نیستی. "
        "قبل از escalate با my_orders/my_accounts بررسی کن و یک تصمیم مشخص پیشنهاد بده با متن آماده برای مشتری. "
        "بعد از escalate به مشتری فقط بگو «درخواست شما بررسی می‌شود و نتیجه همین‌جا اطلاع داده می‌شود.»\n"
        "هرگز قول تخفیف، هدیه یا زمان دقیق پاسخ نده. هرگز اطلاعات مشتری دیگر را نگو.\n\n"
        "## راهنمای سرویس\n"
        f"- پلن‌ها (۳۰ روزه): {plans}. خرید و تمدید از دکمه‌ی «🛒 خرید سرویس» (خرید اکانت جدید یا تمدید اکانت قبلی). {sale}\n"
        "- پرداخت: کارت‌به‌کارت با مبلغ دقیقِ نوشته‌شده (به تومان، با چند تومان آخرش) یا تتر؛ بعد از واریز معمولاً خودکار فعال می‌شود. "
        "اگر نشد، عکس رسید را در ربات بفرستد. مبلغ به تومان است نه ریال.\n"
        "- زمان اکانت از اولین اتصال شروع می‌شود. وضعیت از «📶 وضعیت سرویس من».\n"
        "- وصل نمی‌شود: اول «🔄 دریافت دوباره‌ی لینک در صورت قطعی» (یا resend_links). در اپ HermesVPN مسیرها را عوض کند: "
        "Main و Main 2 برای بیشتر اپراتورها، روی همراه اول اگر Reality گیر کرد Main 2، روی مخابرات (ADSL/فیبر) مسیر XHTTP. "
        "اپ را به آخرین نسخه آپدیت کند. در اپ‌های دیگر (v2rayNG، V2Box، Hiddify) لینک ساب را دوباره آپدیت کند.\n"
        "- سایت‌ها و اپ‌های ایرانی در اپ HermesVPN بدون VPN و مستقیم باز می‌شوند.\n"
        "- اپ‌ها: «📥 دانلود نرم‌افزار» (اندروید و ویندوز). دکمه‌ی «📲 اتصال به نرم‌افزار» اکانت را خودکار در اپ اضافه می‌کند.\n"
        "- تست رایگان: ۵۰۰ مگ / ۲۴ ساعت، یک‌بار. دعوت دوستان و گردونه‌ی شانس در منو هست."
    )


def support_ai_reply(chat_id, user, text):
    """True = دستیار جواب داد (یا به ادمین سپرد)؛ False = مسیر قدیمی (مستقیم به ادمین)."""
    if not (SUPPORT_AI_ENABLED and AIM and AIM.available()) or _sup_admin_active(chat_id):
        return False
    escalated = []
    tools, impl = _sup_tools(chat_id, user, escalated)
    try:
        api_call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
    except Exception:
        pass
    try:
        reply, hist = AIM.run_agent(_sup_system(), SUP_AI_HIST.get(chat_id, [])[-12:], text, tools, impl,
                                    who="support", max_steps=6, max_tokens=900)
        SUP_AI_HIST[chat_id] = hist
    except Exception as e:
        print(f"[support-ai] {chat_id}: {e}", flush=True)
        return False
    try:
        _c = orders_db()
        _r = _c.execute("SELECT MAX(id) FROM support_messages WHERE tg_id=? AND direction='cin' AND text=?", (chat_id, text[:4000])).fetchone()
        if _r and _r[0]:
            _c.execute("UPDATE support_messages SET direction='in' WHERE id=?", (_r[0],)); _c.commit()
        else:
            log_support_message(chat_id, "in", text)
        _c.close()
    except Exception:
        log_support_message(chat_id, "in", text)
    if escalated:
        e = escalated[-1]
        uname = f"@{user['username']}" if user.get("username") else str(chat_id)
        act_fa = {"approve_order": f"تأیید سفارش #{e.get('order_id')}", "reject_order": f"رد سفارش #{e.get('order_id')}",
                  "topup": f"شارژ «{e.get('account')}»" + (f" {e.get('gb'):g} گیگ" if e.get("gb") else "") + (f" {e.get('days'):g} روز" if e.get("days") else ""),
                  "none": ""}.get(e.get("action") or "none", "")
        c = orders_db()
        c.execute("""CREATE TABLE IF NOT EXISTS ai_proposals (id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER, action TEXT,
                     params TEXT, msg_ok TEXT, msg_no TEXT, status TEXT DEFAULT 'open', created_at INTEGER)""")
        cur = c.execute("INSERT INTO ai_proposals (tg_id, action, params, msg_ok, msg_no, created_at) VALUES (?,?,?,?,?,?)",
                        (chat_id, e.get("action") or "none", json.dumps({k: e.get(k) for k in ("order_id", "account", "gb", "days")}),
                         e.get("ok"), e.get("no"), int(time.time())))
        pid = cur.lastrowid
        c.commit(); c.close()
        body = (f"🧠 تصمیم لازم — مشتری {user.get('first_name', '')} ({uname})\n\n"
                f"💬 پیام مشتری:\n{text[:600]}\n\n🤖 بررسی دستیار:\n{e.get('summary')}\n"
                + (f"\n✅ پیشنهاد: {act_fa}" if act_fa else "")
                + (f"\n\n📨 اگر تأیید کنی به مشتری:\n{e.get('ok')}" if e.get("ok") else "")
                + (f"\n\n📨 اگر رد کنی به مشتری:\n{e.get('no')}" if e.get("no") else ""))
        kb = {"inline_keyboard": [[{"text": "✅ تأیید", "callback_data": f"aip:{pid}:ok"},
                                   {"text": "❌ رد", "callback_data": f"aip:{pid}:no"}],
                                  [{"text": "↩️ پاسخ دستی", "callback_data": f"supreply:{chat_id}"},
                                   {"text": "💬 سابقه", "callback_data": f"suphist:{chat_id}"},
                                   {"text": "🙋 پنل", "callback_data": f"cust:{chat_id}"}]]}
        try:
            send_via_admin_bot(ADMIN_CHAT_ID, body, reply_markup=kb)
        except Exception:
            notify_admin(body)
    if reply:
        send_message(chat_id, reply.replace("**", ""), reply_markup=main_menu())
    return True
# ======================= /پشتیبانی هوشمند =======================


def handle_message(msg):
    chat_id = msg["chat"]["id"]
    user = msg.get("from", {})
    text = msg.get("text", "")
    photo = msg.get("photo")

    if msg.get("chat", {}).get("type", "private") == "private" and (text or "").startswith("/start "):
        try:
            record_referral(chat_id, text.split(None, 1)[1].strip())
        except Exception as e:
            print("[referral] record failed:", e, flush=True)

    if chat_id != ADMIN_CHAT_ID and not is_channel_member(chat_id):
        send_message(chat_id,
                     f"برای استفاده از ربات، اول باید عضو کانال {FORCE_JOIN_CHANNEL} بشی.\n"
                     f"بعد از عضویت، روی «✅ عضو شدم» بزن.",
                     reply_markup=join_gate_keyboard())
        return

    if photo:
        file_id = photo[-1]["file_id"]
        handle_receipt_photo(chat_id, user, file_id)
        return

    stripped = (text or "").strip()

    if chat_id == ADMIN_CHAT_ID:
        m = re.match(r"^/paid\s+(\d+)$", stripped, re.IGNORECASE)
        if m:
            handle_admin_paid_command(int(m.group(1)))
            return

    if stripped in ("/start buy", "/start trial"):
        # deep links from the group's buy / free-trial buttons
        handle_start(chat_id, user)
        if stripped.endswith("buy"):
            handle_buy_menu(chat_id)
        else:
            handle_trial(chat_id, user)
    elif stripped == "/start invite":
        # deep link from channel/group posts: open the user's own invite poster + link
        handle_start(chat_id, user)
        handle_referral_menu(chat_id)
    elif stripped in ("/start", "/help", "/menu") or stripped.startswith("/start "):
        handle_start(chat_id, user)
    elif stripped in ("🎡 گردونه‌ی شانس", "/wheel"):
        handle_wheel(chat_id)
    elif stripped in ("🎁 دعوت دوستان (حجم رایگان)", "🎁 دعوت دوستان", "/invite"):
        handle_referral_menu(chat_id)
    elif stripped == "🛒 خرید سرویس":
        handle_buy_menu(chat_id)
    elif stripped == "📦 خرید عمده":
        handle_bulk_menu(chat_id)
    elif stripped == "🔁 تمدید سرویس":
        handle_renew_menu(chat_id)
    elif stripped == "🧪 تست":
        handle_trial(chat_id, user)
    elif stripped == "📶 وضعیت سرویس من":
        handle_status(chat_id)
    elif stripped in ("🔄 دریافت دوباره‌ی لینک در صورت قطعی", "🔄 دریافت دوباره‌ی لینک"):
        handle_resend_link(chat_id)
    elif stripped == "📥 دانلود نرم‌افزار":
        handle_app_download(chat_id)
    elif stripped == "🆘 پشتیبانی":
        handle_support(chat_id)
    elif stripped == "📤 سفارش‌های معطل" and chat_id == ADMIN_CHAT_ID:
        show_pending_orders(chat_id)
    elif stripped == "⚙️ تنظیمات پرداخت خودکار" and chat_id == ADMIN_CHAT_ID:
        handle_admin_settings(chat_id)
    elif chat_id in NEWACC_WAIT and stripped and time.time() - NEWACC_WAIT[chat_id][1] < 900:
        if not re.fullmatch(r"[A-Za-z0-9_]{2,20}", stripped):
            send_message(chat_id, "اسم فقط با حروف انگلیسی و عدد، بین ۲ تا ۲۰ حرف. دوباره بفرست:")
        else:
            NEWACC_WAIT.pop(chat_id, None)
            root = root_account(chat_id)
            NEWACC_NAME[chat_id] = (child_email(root, stripped) if root else unique_email(stripped), time.time())
            send_plans_for_new(chat_id)
    elif chat_id in RENAME_WAIT and stripped:
        email = RENAME_WAIT.pop(chat_id)
        new_label = stripped[:40]
        owns = any(r[0] == email for r in find_client_by_tg(chat_id))
        if not owns:
            send_message(chat_id, "این اکانت مال حساب تو نیست.", reply_markup=main_menu(chat_id))
        else:
            set_label(email, new_label)
            send_message(chat_id, f"✅ اسم اکانت به «{esc(new_label)}» تغییر کرد.",
                          reply_markup=main_menu(chat_id), parse_mode="HTML")
    elif chat_id in APP_REGISTER_WAIT and stripped:
        handle_app_register_step(chat_id, stripped)
    elif chat_id in CLAIM_WAIT and stripped:
        handle_claim_attempt(chat_id, user, stripped)
    elif stripped and looks_like_bank_receipt(stripped) and handle_text_receipt(chat_id, user, stripped):
        pass
    elif stripped and support_ai_reply(chat_id, user, stripped):
        pass
    elif stripped:
        # هر چیز دیگری = پیام پشتیبانی. به *ربات ادمین* می‌رود با دکمه‌ای که
        # ادمین با یک ضربه گفتگو را باز می‌کند و همان‌جا جواب می‌دهد.
        uname = f"@{user['username']}" if user.get("username") else str(chat_id)
        body = (f"📩 پشتیبانی از {user.get('first_name','')} ({uname}, id={chat_id}):\n\n{stripped}")
        kb = {"inline_keyboard": [[{"text": "↩️ پاسخ به این گفتگو",
                                     "callback_data": f"supreply:{chat_id}"},
                                    {"text": "💬 سابقه", "callback_data": f"suphist:{chat_id}"}]]}
        try:
            _c = orders_db()
            _r = _c.execute("SELECT MAX(id) FROM support_messages WHERE tg_id=? AND direction='cin' AND text=?", (chat_id, stripped[:4000])).fetchone()
            if _r and _r[0]:
                _c.execute("UPDATE support_messages SET direction='in' WHERE id=?", (_r[0],)); _c.commit()
            else:
                log_support_message(chat_id, "in", stripped)
            _c.close()
        except Exception:
            log_support_message(chat_id, "in", stripped)
        delivered = False
        try:
            send_via_admin_bot(ADMIN_CHAT_ID, body, reply_markup=kb)
            delivered = True
        except Exception as e:
            # اگر ربات ادمین در دسترس نبود، پیام مشتری نباید گم شود
            print(f"[support] ارسال به ربات ادمین نشد: {e}", flush=True)
            try:
                notify_admin( body)
                delivered = True
            except Exception as e2:
                print(f"[support] مسیر پشتیبان هم نشد: {e2}", flush=True)
        send_message(chat_id,
                      "پیامت به پشتیبانی ارسال شد ✅ به‌زودی همینجا جواب می‌گیری."
                      if delivered else
                      "الان نتونستم پیامت رو برسونم؛ چند دقیقه دیگه دوباره بفرست.",
                      reply_markup=main_menu())


def handle_callback(cq):
    chat_id = cq["message"]["chat"]["id"]
    message_id = cq["message"]["message_id"]
    user = cq.get("from", {})
    data = cq.get("data", "")

    if data.startswith("myhist:"):
        answer_callback(cq["id"])
        try:
            text, kb = customer_history_view(chat_id, int(data.split(":", 1)[1]))
            if text:
                edit_message(chat_id, message_id, text, reply_markup=kb, parse_mode="HTML")
        except Exception as e:
            print(f"[support] history page failed: {e}", flush=True)
        return

    if data == "checkjoin":
        if is_channel_member(chat_id):
            answer_callback(cq["id"])
            edit_message(chat_id, message_id, "عضویت تأیید شد ✅")
            handle_start(chat_id, user)
        else:
            answer_callback(cq["id"], "هنوز عضو کانال نشدی. اول عضو شو، بعد دوباره بزن.")
        return
    answer_callback(cq["id"])

    if chat_id != ADMIN_CHAT_ID and not is_channel_member(chat_id):
        edit_message(chat_id, message_id,
                     f"اول باید عضو کانال {FORCE_JOIN_CHANNEL} بشی.", reply_markup=join_gate_keyboard())
        return

    if data.startswith("ref:"):
        handle_referral_callback(chat_id, message_id, user, data)
    elif data == "cancelbuy":
        NEWACC_WAIT.pop(chat_id, None); NEWACC_NAME.pop(chat_id, None)
        edit_message(chat_id, message_id, "خرید لغو شد ❌")
        send_message(chat_id, "هر وقت خواستی دوباره از «🛒 خرید سرویس» شروع کن.", reply_markup=main_menu(chat_id))
    elif data.startswith("cancelbuy:"):
        oid = int(data.split(":", 1)[1])
        send_message(chat_id, f"سفارش #{oid} لغو بشه؟\nاگه پولش رو واریز کردی، لغو نکن — بعد از واریز خودکار فعال می‌شه.",
                     reply_markup=inline([[{"text": "✅ بله، لغو کن", "callback_data": f"cancelok:{oid}"},
                                           {"text": "↩️ نه", "callback_data": f"cancelno:{oid}"}]]))
    elif data.startswith("cancelno:"):
        edit_message(chat_id, message_id, "باشه، سفارشت سر جاشه 👍")
    elif data.startswith("cancelok:"):
        oid = int(data.split(":", 1)[1])
        NEWACC_WAIT.pop(chat_id, None); NEWACC_NAME.pop(chat_id, None)
        row = get_order(oid)
        if not row or row[1] != chat_id:
            edit_message(chat_id, message_id, "این سفارش پیدا نشد.")
        elif row[5] == "awaiting_receipt":
            set_order_status(oid, "cancelled")
            edit_message(chat_id, message_id, f"سفارش #{oid} لغو شد ❌")
            send_message(chat_id, "هر وقت خواستی دوباره از «🛒 خرید سرویس» شروع کن.", reply_markup=main_menu(chat_id))
        elif row[5] == "pending_review":
            edit_message(chat_id, message_id, "رسیدت ثبت شده و در حال بررسیه؛ برای لغو به «🆘 پشتیبانی» پیام بده.")
        elif row[5] == "cancelled":
            edit_message(chat_id, message_id, f"سفارش #{oid} قبلاً لغو شده.")
        else:
            edit_message(chat_id, message_id, "این سفارش انجام شده و دیگه لغو نمی‌شه.")
    elif data == "newstart":
        NEWACC_WAIT[chat_id] = (None, time.time())
        edit_message(chat_id, message_id, "اسم اکانت جدید رو بفرست (فقط حروف انگلیسی و عدد):",
                     reply_markup=inline([[{"text": "⏭ بدون اسم", "callback_data": "newname_skip"}], CANCEL_ROW]))
    elif data == "newname_skip":
        NEWACC_WAIT.pop(chat_id, None)
        root = root_account(chat_id)
        NEWACC_NAME[chat_id] = (child_email(root) if root else "", time.time())
        send_plans_for_new(chat_id, message_id)
    elif data == "renewstart":
        if find_client_by_tg(chat_id):
            handle_renew_menu(chat_id)
        else:
            CLAIM_WAIT.add(chat_id)
            edit_message(chat_id, message_id,
                         "هنوز اکانتی به این حساب تلگرام وصل نیست.\nاگه قبلاً اکانت خریدی، اسم اکانتت رو بفرست تا وصل بشه؛ "
                         "بعد دوباره «🛒 خرید سرویس» رو بزن.",
                         reply_markup=inline([[{"text": "🆕 خرید اکانت جدید", "callback_data": "newstart"}], CANCEL_ROW]))
    elif data.startswith("buy:"):
        plan_key = data.split(":", 1)[1]
        pend = NEWACC_NAME.pop(chat_id, None)
        new_email = pend[0] if pend and pend[0] and time.time() - pend[1] < 1800 else None
        if not new_email and (PLAN_BY_KEY.get(plan_key) or {}).get("quantity", 1) == 1 and not pend:
            root = root_account(chat_id)          # پیام پلن قدیمی: باز هم زیر اکانت اول
            new_email = child_email(root) if root else None
        handle_plan_selected(chat_id, message_id, user, plan_key, new_email=new_email)
    elif data.startswith("renewfor:"):
        plan_key = data.split(":", 1)[1]
        rows = find_client_by_tg(chat_id)
        if not rows:
            CLAIM_WAIT.add(chat_id)
            edit_message(chat_id, message_id,
                         "هنوز اکانتی به این حساب تلگرام وصل نیست.\nاگه قبلاً اکانت خریدی، اسم اکانتت رو بفرست تا وصل بشه؛ "
                         "بعد دوباره «🛒 خرید سرویس» رو بزن.",
                         reply_markup=inline([[{"text": "🆕 خرید اکانت جدید", "callback_data": f"newacc:{plan_key}"}]]))
            return
        labels = get_labels([r[0] for r in rows])
        kb = [[{"text": (labels.get(r[0]) + " — " if labels.get(r[0]) else "") + r[0],
                "callback_data": f"buyrenew:{plan_key}:{r[0]}"[:64]}] for r in rows[:30]]
        edit_message(chat_id, message_id, "کدوم اکانت تمدید بشه؟", reply_markup=inline(kb))
    elif data.startswith("newacc:"):
        plan_key = data.split(":", 1)[1]
        NEWACC_WAIT[chat_id] = (plan_key, time.time())
        edit_message(chat_id, message_id, "اسم اکانت جدید رو بفرست (فقط حروف انگلیسی و عدد):",
                     reply_markup=inline([[{"text": "⏭ بدون اسم", "callback_data": f"newaccauto:{plan_key}"}]]))
    elif data.startswith("newaccauto:"):
        NEWACC_WAIT.pop(chat_id, None)
        plan_key = data.split(":", 1)[1]
        root = root_account(chat_id)
        handle_plan_selected(chat_id, message_id, user, plan_key, new_email=child_email(root) if root else None)
    elif data.startswith("paymethod:"):
        _, method, order_id_str = data.split(":", 2)
        order_id = int(order_id_str)
        if method == "card":
            show_card_payment(chat_id, message_id, order_id)
        else:
            show_crypto_payment(chat_id, message_id, order_id)
        notify_order_waiting(order_id, method)
    elif data.startswith("acc:"):
        _, action, *rest = data.split(":", 2)
        if action == "page":
            handle_status(chat_id, page=int(rest[0]), message_id=message_id)
        elif action == "view":
            show_account_detail(chat_id, message_id, rest[0])
        elif action == "rename":
            RENAME_WAIT[chat_id] = rest[0]
            edit_message(chat_id, message_id,
                          f"اسم جدید برای «{rest[0]}» رو بنویس (لینک و اکانت تغییری نمی‌کنه):")
        elif action == "link":
            send_account_link(chat_id, rest[0])
        elif action == "register":
            email = rest[0]
            username = auto_app_username(chat_id, user.get("username"), exclude_email=email)
            APP_REGISTER_WAIT[chat_id] = {"email": email, "username": username}
            edit_message(chat_id, message_id,
                          f"یوزرنیمت: «{username}» (از یوزرنیم تلگرامت گرفته شد)\n"
                          f"حالا یه پسورد دلخواه بفرست (حداقل ۴ کاراکتر):")
        elif action == "forgotpw":
            email = rest[0]
            username = get_app_username(email) or auto_app_username(chat_id, user.get("username"), exclude_email=email)
            new_password = randid(8)
            set_app_credentials(email, username, new_password)
            edit_message(chat_id, message_id, "پسورد جدیدت رو همین‌جا فرستادم 👇")
            send_message(chat_id,
                          f"🔑 پسورد اپ برای «{esc(email)}» بازنشانی شد:\n\n"
                          f"یوزرنیم: <code>{esc(username)}</code>\n"
                          f"پسورد جدید: <code>{esc(new_password)}</code>",
                          parse_mode="HTML")
    elif data.startswith("renewpick:"):
        email = data.split(":", 1)[1]
        edit_message(chat_id, message_id, f"«{email}» رو با کدوم پلن تمدید کنم؟", reply_markup=renew_plans_keyboard(email))
    elif data.startswith("buyrenew:"):
        _, plan_key, email = data.split(":", 2)
        handle_plan_selected(chat_id, message_id, user, plan_key, target_email=email)
    elif data.startswith("approve:") and chat_id == ADMIN_CHAT_ID:
        approve_order(chat_id, message_id, int(data.split(":", 1)[1]))
    elif data.startswith("reject:") and chat_id == ADMIN_CHAT_ID:
        reject_order(chat_id, message_id, int(data.split(":", 1)[1]))
    elif data.startswith("unsuspend:") and chat_id == ADMIN_CHAT_ID:
        email = data.split(":", 1)[1]
        tg = client_tg_id(email)
        ok = set_client_enabled(email, True)
        if ok:
            if tg:
                send_message(tg, "اکانتت دوباره فعال شد ✅ می‌تونی از سرویس استفاده کنی.", reply_markup=main_menu())
            edit_message(chat_id, message_id, f"✅ اکانت «{email}» رفع مسدودیت شد.")
        else:
            edit_message(chat_id, message_id, f"اکانتی به اسم «{email}» پیدا نشد.")
    elif data.startswith("deleteclient:") and chat_id == ADMIN_CHAT_ID:
        email = data.split(":", 1)[1]
        tg = client_tg_id(email)
        ok = delete_client(email)
        if ok:
            if tg:
                send_message(tg, "اکانتت به‌دلیل استفاده‌ی نامتعارف حذف شد. برای اطلاعات بیشتر با پشتیبانی تماس بگیر.", reply_markup=main_menu())
            edit_message(chat_id, message_id, f"🗑 اکانت «{email}» حذف شد.")
        else:
            edit_message(chat_id, message_id, f"اکانتی به اسم «{email}» پیدا نشد.")
    elif data.startswith("autoset:") and chat_id == ADMIN_CHAT_ID:
        set_setting("auto_approve", data.split(":", 1)[1])
        state = "خودکار" if auto_approve_enabled() else "دستی"
        edit_message(chat_id, message_id,
                     f"حالت فعلی تأیید پرداخت: **{state}**\n\n"
                     f"خودکار: واریز شناسایی‌شده بلافاصله خودش تأیید و فعال می‌شه.\n"
                     f"دستی: فقط بهت خبر می‌ده، تأیید نهایی با خودته.",
                     reply_markup=auto_toggle_keyboard())


def load_or_create_webhook_secret():
    if os.path.exists(PAID_WEBHOOK_SECRET_FILE):
        return open(PAID_WEBHOOK_SECRET_FILE).read().strip()
    secret = "".join(random.choices(string.ascii_letters + string.digits, k=32))
    with open(PAID_WEBHOOK_SECRET_FILE, "w") as f:
        f.write(secret)
    os.chmod(PAID_WEBHOOK_SECRET_FILE, 0o600)
    return secret


WEBHOOK_SECRET = load_or_create_webhook_secret()


def load_or_create_vakif_webhook_secret():
    if os.path.exists(VAKIF_WEBHOOK_SECRET_FILE):
        return open(VAKIF_WEBHOOK_SECRET_FILE).read().strip()
    secret = "".join(random.choices(string.ascii_letters + string.digits, k=32))
    with open(VAKIF_WEBHOOK_SECRET_FILE, "w") as f:
        f.write(secret)
    os.chmod(VAKIF_WEBHOOK_SECRET_FILE, 0o600)
    return secret


VAKIF_WEBHOOK_SECRET = load_or_create_vakif_webhook_secret()

# عکس پروفایل base64 — اپ قبل از ارسال به ۲۵۶ پیکسل کوچیکش می‌کنه، پس این
# سقف با فاصله‌ی زیاد بالاتر از حالت عادیه و فقط جلوی سوءاستفاده رو می‌گیره.
MAX_AVATAR_CHARS = 400_000


class PaidWebhookHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # keep the systemd journal free of routine request noise

    def do_POST(self):
        REQ_CTX.ua = self.headers.get("User-Agent", "")
        path = urllib.parse.urlparse(self.path).path
        if path == "/admin/order-action":
            self._handle_admin_order_action()
            return
        if path == "/app/redeem":
            self._handle_app_redeem()
            return
        if path == "/app/login":
            self._handle_app_login()
            return
        if path == "/app/register":
            self._handle_app_register()
            return
        if path == "/app/forgot-password":
            self._handle_app_forgot_password()
            return
        if path == "/app/profile":
            self._handle_app_profile_get()
            return
        if path == "/app/profile/update":
            self._handle_app_profile_update()
            return
        if path == "/app/change-password":
            self._handle_app_change_password()
            return
        if path == "/app/orders":
            self._handle_app_create_order()
            return
        m = re.match(r"^/app/orders/(\d+)/method$", path)
        if m:
            self._handle_app_order_method(int(m.group(1)))
            return
        m = re.match(r"^/app/orders/(\d+)/receipt$", path)
        if m:
            self._handle_app_order_receipt(int(m.group(1)))
            return
        if path == "/app/iyzico/callback":
            self._handle_iyzico_callback()
            return
        if path == "/vakif-paid":
            self._handle_vakif_webhook()
            return
        if path != "/paid":
            print(f"[webhook] 404: unexpected path {self.path!r}", flush=True)
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8", errors="ignore")
        params = urllib.parse.parse_qs(body)
        secret = (params.get("secret") or [""])[0]
        amount_raw = (params.get("amount") or [""])[0]
        tracking = (params.get("tracking") or [""])[0].strip() or None
        date = (params.get("date") or [""])[0].strip() or None
        print(f"[webhook] received: amount_raw={amount_raw!r} tracking={tracking!r} date={date!r} secret_ok={secret == WEBHOOK_SECRET}", flush=True)

        if secret != WEBHOOK_SECRET:
            print("[webhook] rejected: bad secret", flush=True)
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"forbidden")
            return
        try:
            # Bank Keshavarzi's SMS reports the deposit in Rial; our prices
            # are all in Toman, so this is the one conversion point — doing
            # it here (not in MacroDroid) keeps the phone-side setup simpler.
            digits = int(re.sub(r"\D", "", amount_raw))
            amount = digits // 10
            rem = digits % 10
        except ValueError:
            print(f"[webhook] rejected: bad amount {amount_raw!r}", flush=True)
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"bad amount")
            return

        if deposit_already_handled(amount_raw, tracking, date):
            print(f"[webhook] تکراری، نادیده گرفته شد: {amount_raw!r} {tracking!r} {date!r}", flush=True)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"duplicate")
            return

        if rem:
            # مبلغ ریالی مضرب ۱۰ نیست. یعنی مشتری پسوندِ مبلغ یکتا را
            # به‌جای تومان، ریال زده: قیمت ۴۵۰٬۰۰۲ تومان را «۴٬۵۰۰٬۰۰۰ + ۲»
            # وارد کرده. //10 آن رقم را دور می‌اندازد و مبلغ به قیمت رُند
            # برمی‌گردد — دقیقاً چیزی که مبلغ یکتا برای جلوگیری‌اش ساخته شد.
            # فقط وقتی قبولش کن که به یک سفارشِ تازه‌ی بی‌ابهام برسد.
            alt = amount + rem
            try:
                ids_alt, kind_alt = pending_orders_for_amount(alt)
            except Exception:
                ids_alt, kind_alt = [], "none"
            if kind_alt == "exact" and len(ids_alt) == 1:
                print(f"[webhook] rial-remainder fix: {amount} -> {alt} "
                      f"(سفارش #{ids_alt[0]})", flush=True)
                amount = alt

        print(f"[webhook] parsed amount={amount} toman, looking for a matching pending order...", flush=True)
        try:
            handle_admin_paid_command(amount, tracking, date)
        except Exception as e:
            print("[webhook] error:", e, flush=True)

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def do_GET(self):
        REQ_CTX.ua = self.headers.get("User-Agent", "")
        path = urllib.parse.urlparse(self.path).path
        if path == "/hb/divar":
            # owner 2026-11-17: هتزنر بعد از هر پست موفق دیوار این را صدا می‌زند تا پشتیبان اوراکل
            # پستی را که ادمین‌های گروه پاک کرده‌اند «نرفته» حساب نکند و پست تکراری نگذارد.
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            try:
                ok = q.get("k", [""])[0] == open("/root/.hermesvpn-divar/hb-secret").read().strip()
            except Exception:
                ok = False
            # یا از خود سرور هتزنر (بی‌نیاز از جابه‌جا کردن رمز بین سشن‌ها؛ بدترین سوءاستفاده = یک پست پشتیبان کمتر)
            src = self.headers.get("CF-Connecting-IP") or self.client_address[0]
            ok = ok or src == "203.0.113.20" or src.startswith("2a01:4f8:")   # Hetzner IPv4 / IPv6 range
            if not ok:
                print(f"[hb] rejected from {src}", flush=True)
                self.send_response(403); self.end_headers(); return
            json.dump({"ts": int(time.time()), "id": q.get("id", [""])[0][:20]}, open("/var/lib/hermesvpn-divar-hb.json", "w"))
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
            return
        m = re.match(r"^/sub/([A-Za-z0-9_-]{6,64})/?$", path)
        if m:
            self._handle_full_sub(m.group(1))
            return
        if path == "/app/vless":
            self._handle_app_vless()
            return
        if path == "/app/plans":
            self._handle_app_plans()
            return
        m = re.match(r"^/app/orders/(\d+)$", path)
        if m:
            self._handle_app_order_status(int(m.group(1)))
            return
        if path.startswith("/dl/"):
            self._handle_download(path[4:])
            return
        self.send_response(404)
        self.end_headers()

    DOWNLOAD_DIR = "/var/lib/hermesvpn-downloads"

    def _handle_download(self, name):
        """دانلود فایل‌های عمومی اپ (مثلاً نسخه‌ی ویندوز HermesVPN Connect).
        فقط نام ساده از همین پوشه سرو می‌شه — بدون مسیر، بدون ..؛ پس چیزی
        بیرون از این پوشه قابل دسترس نیست."""
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.(exe|zip|json|apk)$", name or ""):
            self.send_response(404)
            self.end_headers()
            return
        fpath = os.path.join(self.DOWNLOAD_DIR, name)
        if not os.path.isfile(fpath):
            self.send_response(404)
            self.end_headers()
            return
        size = os.path.getsize(fpath)
        self.send_response(200)
        if name.endswith(".json"):
            # update manifest: tiny, never cached, so apps see a new approved version at once
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
        elif name.endswith(".apk"):
            self.send_header("Content-Type", "application/vnd.android.package-archive")
        else:
            self.send_header("Content-Type", "application/octet-stream")
        if not name.endswith(".json"):
            # بگذار Cloudflare فایل را در لبه‌ی خودش نگه دارد: دانلود از ایران از نزدیک‌ترین سرور کلادفلر می‌آید
            # نه هر بار از اوراکل. ۱۰ دقیقه، تا نسخه‌ی جدید روی لینک ثابت حداکثر ۱۰ دقیقه بعد برسد.
            self.send_header("Cache-Control", "public, max-age=600")
        self.send_header("Content-Length", str(size))
        if not name.endswith(".json"):
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.end_headers()
        try:
            with open(fpath, "rb") as f:
                while True:
                    chunk = f.read(256 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError, OSError):
            pass

    def _handle_full_sub(self, sub_id):
        """لینک ساب کامل HermesVPN (مالک ۲۰۲۶-۱۱-۰۹): همه‌ی مسیرها — Main، Main 2-4، Reality، XHTTP — در یک لینک،
        برای هر اپی (v2rayNG، Hiddify، اپ‌های خودمان). برخلاف ساب x-ui (پورت 2096) چک HWID ندارد و مسیرهای
        جایگزین را هم دارد. همان سطح اعتماد ساب x-ui: هر کس sub_id را داشته باشد کانفیگ‌ها را می‌گیرد."""
        c = sqlite3.connect(X_UI_DB)
        row = c.execute("SELECT email FROM clients WHERE sub_id=?", (sub_id,)).fetchone()
        tr = c.execute("SELECT up, down, total, expiry_time FROM client_traffics WHERE email=(SELECT email FROM clients WHERE sub_id=?)",
                       (sub_id,)).fetchone() if row else None
        c.close()
        if not row:
            self.send_response(404); self.end_headers(); return
        email = row[0]
        self._log_fetch(email, "sub")
        REQ_CTX.ua = "HermesVPN-Windows/sub"          # = همه‌ی مسیرها، از جمله XHTTP
        try:
            links = build_vless_links(email)
        finally:
            REQ_CTX.ua = ""
        out = []
        for label, uri in links:
            base = uri.split("#", 1)[0]
            out.append(base + "#" + urllib.parse.quote("HermesVPN | " + label))
        body = base64.b64encode("\n".join(out).encode()).decode().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("profile-title", "base64:" + base64.b64encode("HermesVPN".encode()).decode())
        self.send_header("profile-update-interval", "6")
        if tr:
            up, down, total, exp = tr
            exp_s = int(exp / 1000) if exp and exp > 0 else 0
            self.send_header("subscription-userinfo", f"upload={up or 0}; download={down or 0}; total={total or 0}; expire={exp_s}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_app_vless(self):
        """اپ اندروید لینک را از اینجا می‌گیرد، نه از endpoint واقعی /sub/ —
        همان دلیل build_vless_links: چک HWID سه‌ایکس‌یوآی یک فراخوانی ساده
        (چه از اپ چه از اسکریپت) را رد می‌کند. sub_id همان کلید حامل موجود
        در لینک subscription فعلی مشتری است، سطح اعتمادش هم همان است."""
        query = urllib.parse.urlparse(self.path).query
        sub_id = (urllib.parse.parse_qs(query).get("sub_id") or [""])[0].strip()
        if not sub_id:
            self._send_json(400, {"ok": False, "error": "sub_id missing"})
            return

        c = sqlite3.connect(X_UI_DB)
        cur = c.cursor()
        cur.execute("SELECT email FROM clients WHERE sub_id=?", (sub_id,))
        row = cur.fetchone()
        c.close()
        if not row:
            self._send_json(404, {"ok": False, "error": "not_found"})
            return
        self._record_app_seen(row[0])
        status, body = account_status_response(row[0])
        self._send_json(status, body)

    def _handle_admin_order_action(self):
        """تأیید/رد سفارش از طرف ربات ادمین.

        دکمه‌ها در ربات ادمین‌اند ولی منطق فعال‌سازی فقط اینجاست، پس ربات
        ادمین همین را صدا می‌زند. فقط از روی همین ماشین و با رمز وب‌هوک —
        همان سطح اعتمادی که مسیر پیامک بانک دارد.
        """
        try:
            body = self._read_json_body()
        except Exception:
            self._send_json(400, {"ok": False, "error": "bad_json"})
            return
        if not isinstance(body, dict) or body.get("secret") != load_or_create_webhook_secret():
            self._send_json(403, {"ok": False, "error": "forbidden"})
            return
        try:
            order_id = int(body.get("order_id"))
        except (TypeError, ValueError):
            self._send_json(400, {"ok": False, "error": "bad_order_id"})
            return
        action = body.get("action")
        if action == "approve":
            ok, text = approve_order_headless(order_id)
        elif action == "reject":
            ok, text = reject_order_headless(order_id)
        elif action == "revert":
            ok, text = revert_order_headless(order_id)
        else:
            self._send_json(400, {"ok": False, "error": "bad_action"})
            return
        print(f"[admin-action] {action} order={order_id} ok={ok}", flush=True)
        self._send_json(200, {"ok": bool(ok), "text": text})

    def _handle_app_redeem(self):
        """توکنِ دکمه‌ی «اتصال به نرم‌افزار» را به اکانت واقعی تبدیل می‌کند.

        خروجی همان چیزی است که لاگین معمولی برمی‌گرداند، به‌علاوه‌ی sub_id —
        اپ آن را نگه می‌دارد تا بعداً هم بتواند خودش لینک‌ها را تازه کند،
        بدون اینکه کاربر لازم باشد یوزر/پسورد یا QR جابه‌جا کند."""
        try:
            data = self._read_json_body()
        except Exception:
            self._send_json(400, {"ok": False, "error": "bad_request"})
            return
        token = str(data.get("token") or "").strip()
        # عمداً لاگ می‌شود: وقتی اپ می‌گوید «به سرور نرسیدم»، تنها راه تشخیص
        # اینکه واقعاً نرسیده یا رسیده و رد شده، همین است.
        print(f"[redeem] token={token[:8]}… from={self.client_address[0]}", flush=True)
        if not token:
            self._send_json(400, {"ok": False, "error": "missing_token"})
            return
        email = redeem_app_link_token(token)
        if not email:
            print(f"[redeem] رد شد — توکن ناشناخته/منقضی: {token[:8]}…", flush=True)
            self._send_json(404, {"ok": False, "error": "invalid_token"})
            return
        print(f"[redeem] ✓ {token[:8]}… -> {email}", flush=True)
        status, resp = account_status_response(email)
        if status == 200:
            try:
                c = sqlite3.connect(X_UI_DB)
                cur = c.cursor()
                cur.execute("SELECT sub_id FROM clients WHERE email=? COLLATE NOCASE", (email,))
                row = cur.fetchone()
                c.close()
                if row and row[0]:
                    resp["sub_id"] = row[0]
            except Exception:
                pass
            resp["account"] = email
        self._send_json(status, resp)

    def _log_fetch(self, email, kind):
        """هر بار که اپ/لینک ساب کانفیگ‌ها را می‌گیرد: آی‌پی واقعی کاربر (CF-Connecting-IP) و User-Agent
        (نوع اپ/دستگاه) — برای چکاپ «اکانت تست کیفیت» و عیب‌یابی. ۷ روز نگه داشته می‌شود."""
        try:
            ac = sqlite3.connect(ORDERS_DB, timeout=5)
            ac.execute("CREATE TABLE IF NOT EXISTS fetch_log (ts INTEGER, email TEXT, ip TEXT, ua TEXT, kind TEXT)")
            ip = self.headers.get("CF-Connecting-IP") or self.client_address[0]
            ac.execute("INSERT INTO fetch_log VALUES (?,?,?,?,?)",
                       (int(time.time()), email, ip, (self.headers.get("User-Agent") or "")[:200], kind))
            ac.execute("DELETE FROM fetch_log WHERE ts < ?", (int(time.time()) - 7 * 86400,))
            ac.commit(); ac.close()
        except Exception as e:
            print("[fetch-log] error:", e, flush=True)

    def _record_app_seen(self, email):
        # فقط برای آمار «چند نفر با اپ وصل‌اند» — خطایش نباید اپ را خراب کند.
        self._log_fetch(email, "app")
        try:
            ac = sqlite3.connect(ORDERS_DB, timeout=5)
            ac.execute("CREATE TABLE IF NOT EXISTS app_seen (email TEXT PRIMARY KEY, first_seen INTEGER, last_seen INTEGER, hits INTEGER DEFAULT 0, ip TEXT)")
            now_ts = int(time.time())
            ip = self.headers.get("CF-Connecting-IP") or self.client_address[0]
            ac.execute("""INSERT INTO app_seen (email, first_seen, last_seen, hits, ip) VALUES (?,?,?,1,?)
                          ON CONFLICT(email) DO UPDATE SET last_seen=excluded.last_seen, hits=app_seen.hits+1, ip=excluded.ip""",
                       (email, now_ts, now_ts, ip))
            ac.commit(); ac.close()
        except Exception as e:
            print("[app-seen] error:", e, flush=True)

    def _handle_app_login(self):
        """لاگین اپ با یوزرنیم/پسورد — یوزرنیم همون email کلاینته. جایگزین
        وارد کردن دستی sub_id برای مشتری‌هایی که از این به بعد پرداخت
        می‌کنن و خودکار یوزر/پسورد می‌گیرن (ensure_app_credentials)."""
        length = int(self.headers.get("Content-Length", 0))
        body_raw = self.rfile.read(length).decode("utf-8", errors="ignore")
        try:
            data = json.loads(body_raw)
        except Exception:
            self._send_json(400, {"ok": False, "error": "bad_request"})
            return
        username = str(data.get("username") or "").strip()
        password = str(data.get("password") or "")
        if not username or not password:
            self._send_json(400, {"ok": False, "error": "missing_credentials"})
            return
        email = verify_app_credentials(username, password)
        if not email:
            self._send_json(401, {"ok": False, "error": "invalid_credentials"})
            return
        self._record_app_seen(email)
        status, resp = account_status_response(email)
        self._send_json(status, resp)

    def _handle_app_register(self):
        """ثبت‌نام از توی اپ: با یوزرنیم تلگرامی که مشتری قبلاً باهاش از
        ربات خریده، اکانتش رو پیدا می‌کنه و یوزر/پسورد/ایمیل دلخواهش رو
        روی همون اکانت می‌ذاره (set_app_credentials) — این یعنی خودش رو
        از حالت خودکار (ensure_app_credentials) به حالت دلخواه می‌بره،
        دقیقاً همون کاری که «📱 ثبت‌نام اپ» توی ربات می‌کنه."""
        length = int(self.headers.get("Content-Length", 0))
        body_raw = self.rfile.read(length).decode("utf-8", errors="ignore")
        try:
            data = json.loads(body_raw)
        except Exception:
            self._send_json(400, {"ok": False, "error": "bad_request"})
            return
        tg_username = str(data.get("tg_username") or "").strip().lstrip("@")
        email = str(data.get("email") or "").strip()
        new_username = str(data.get("new_username") or "").strip()
        password = str(data.get("password") or "")
        if not tg_username or not email or not new_username or not password:
            self._send_json(400, {"ok": False, "error": "missing_fields"})
            return
        client_email = find_client_email_by_tg_username(tg_username)
        if not client_email:
            self._send_json(404, {"ok": False, "error": "not_found"})
            return
        if username_taken(new_username, except_email=client_email):
            self._send_json(409, {"ok": False, "error": "username_taken"})
            return
        set_app_credentials(client_email, new_username, password, contact_email=email)
        status, resp = account_status_response(client_email)
        self._send_json(status, resp)

    def _handle_app_forgot_password(self):
        """رمز تازه به تلگرامِ صاحب اکانت فرستاده می‌شه. پاسخ همیشه یکیه —
        چه اکانت پیدا بشه چه نه — تا نشه از این راه فهمید کدوم یوزرنیم/ایمیل
        مشتریِ ماست."""
        try:
            data = self._read_json_body()
        except Exception:
            self._send_json(400, {"ok": False, "error": "bad_request"})
            return
        identifier = str(data.get("identifier") or "").strip()
        if not identifier:
            self._send_json(400, {"ok": False, "error": "missing_identifier"})
            return
        try:
            reset_password_via_telegram(identifier)
        except Exception as e:
            print("[app] forgot-password error:", e, flush=True)
        self._send_json(200, {"ok": True, "sent": True})

    def _handle_app_profile_get(self):
        try:
            data = self._read_json_body()
        except Exception:
            self._send_json(400, {"ok": False, "error": "bad_request"})
            return
        email = verify_app_credentials(
            str(data.get("username") or "").strip(), str(data.get("password") or "")
        )
        if not email:
            self._send_json(401, {"ok": False, "error": "invalid_credentials"})
            return
        profile = get_app_profile(email) or {}
        status, account = account_status_response(email)
        self._send_json(200, {
            "ok": True,
            "profile": profile,
            "account": account if status == 200 else {"error": account.get("error")},
        })

    def _handle_app_profile_update(self):
        try:
            data = self._read_json_body()
        except Exception:
            self._send_json(400, {"ok": False, "error": "bad_request"})
            return
        email = verify_app_credentials(
            str(data.get("username") or "").strip(), str(data.get("password") or "")
        )
        if not email:
            self._send_json(401, {"ok": False, "error": "invalid_credentials"})
            return
        avatar = data.get("avatar")
        if avatar is not None and len(str(avatar)) > MAX_AVATAR_CHARS:
            self._send_json(413, {"ok": False, "error": "avatar_too_large"})
            return
        update_app_profile(
            email,
            display_name=data.get("display_name"),
            contact_email=data.get("email"),
            avatar=avatar,
        )
        self._send_json(200, {"ok": True, "profile": get_app_profile(email)})

    def _handle_app_change_password(self):
        try:
            data = self._read_json_body()
        except Exception:
            self._send_json(400, {"ok": False, "error": "bad_request"})
            return
        email = verify_app_credentials(
            str(data.get("username") or "").strip(), str(data.get("password") or "")
        )
        if not email:
            self._send_json(401, {"ok": False, "error": "invalid_credentials"})
            return
        new_password = str(data.get("new_password") or "")
        if len(new_password) < 6:
            self._send_json(400, {"ok": False, "error": "password_too_short"})
            return
        change_app_password(email, new_password)
        self._send_json(200, {"ok": True})

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8", errors="ignore")
        return json.loads(raw)

    def _authenticate(self, data):
        """یوزر/پسورد اپ رو تأیید می‌کنه و (email, tg_id) برمی‌گردونه، یا
        None اگه نامعتبر بود یا اکانتش به هیچ تلگرامی وصل نشده باشه (claim
        نشده) — چون بدون tg_id نمی‌شه سفارش ثبت کرد."""
        username = str(data.get("username") or "").strip()
        password = str(data.get("password") or "")
        if not username or not password:
            return None
        email = verify_app_credentials(username, password)
        if not email:
            return None
        tg_id = resolve_tg_id_for_email(email)
        if not tg_id:
            return None
        return email, tg_id

    def _handle_app_plans(self):
        self._send_json(200, {"ok": True, "plans": [
            {"key": p["key"], "label": p["label"], "gb": p["gb"], "days": p["days"],
             "price": sale_price(p["price"], flash_sale_pct()), "original_price": p["price"]}
            for p in PLANS
        ]})

    def _handle_app_create_order(self):
        try:
            data = self._read_json_body()
        except Exception:
            self._send_json(400, {"ok": False, "error": "bad_request"})
            return
        auth = self._authenticate(data)
        if not auth:
            self._send_json(401, {"ok": False, "error": "invalid_credentials"})
            return
        email, tg_id = auth
        plan = PLAN_BY_KEY.get(str(data.get("plan_key") or ""))
        if not plan or plan not in PLANS:
            self._send_json(400, {"ok": False, "error": "invalid_plan"})
            return
        # سفارش از اپ همیشه یعنی «تمدید همین اکانتی که باهاش لاگین کرده» —
        # نه ساخت اکانت جدید، چون یوزر/پسورد اپ از قبل به یه اکانت مشخص گره
        # خورده (دقیقاً مثل دکمه‌ی «🔁 تمدید سرویس» توی ربات).
        # همان تخفیفی که ربات می‌دهد (کوپن گردونه/خوش‌آمد یا جشنواره، هر کدام بیشتر) — تا مبلغ پرداخت اپ با ربات یکی باشد
        cpct, fpct = active_coupon(tg_id), flash_sale_pct()
        pct = max(cpct, fpct)
        price = sale_price(plan["price"], pct)
        order_id, payable = create_order(tg_id, "", email, plan["key"], 1, price, target_email=email)
        try:
            _c = orders_db(); _src = "festival" if fpct >= cpct else ordinfo.coupon_source(_c, tg_id); _c.close()
        except Exception:
            _src = "coupon"
        ordinfo.store_price_facts(order_id, plan["price"], pct, _src if pct else "", "app")
        ordinfo.log_action(tg_id, "app", f"سفارش تمدید «{email}» از داخل اپ (پلن {plan['label']})")
        self._send_json(200, {"ok": True, "order_id": order_id, "amount_toman": payable,
                              "original_toman": plan["price"], "discount_pct": pct})

    def _handle_app_order_method(self, order_id):
        try:
            data = self._read_json_body()
        except Exception:
            self._send_json(400, {"ok": False, "error": "bad_request"})
            return
        auth = self._authenticate(data)
        if not auth:
            self._send_json(401, {"ok": False, "error": "invalid_credentials"})
            return
        _, tg_id = auth
        row = get_order(order_id)
        if not row or row[1] != tg_id:
            self._send_json(404, {"ok": False, "error": "not_found"})
            return
        total_price = row[7]
        method = data.get("method")
        if method == "crypto":
            usdt_amount = unique_usdt_amount(total_price)
            set_order_usdt_amount(order_id, usdt_amount)
            self._send_json(200, {
                "ok": True, "method": "crypto",
                "wallet_address": USDT_WALLET_ADDRESS, "usdt_amount": usdt_amount,
            })
        elif method == "card_gateway":
            ok, result = iyzico_create_checkout(order_id, total_price, tg_id)
            if not ok:
                self._send_json(503, {"ok": False, "error": result})
                return
            self._send_json(200, {"ok": True, "method": "card_gateway", "checkout_url": result})
        elif method == "bank_try":
            try_amount = unique_try_amount(total_price)
            set_order_try_amount(order_id, try_amount)
            self._send_json(200, {
                "ok": True, "method": "bank_try",
                "card_number": VAKIF_CARD_NUMBER, "card_holder": VAKIF_CARD_HOLDER, "try_amount": try_amount,
            })
        else:
            self._send_json(200, {
                "ok": True, "method": "card",
                "card_number": CARD_NUMBER, "card_holder": CARD_HOLDER, "amount_toman": total_price,
            })

    def _handle_app_order_receipt(self, order_id):
        try:
            data = self._read_json_body()
        except Exception:
            self._send_json(400, {"ok": False, "error": "bad_request"})
            return
        auth = self._authenticate(data)
        if not auth:
            self._send_json(401, {"ok": False, "error": "invalid_credentials"})
            return
        email, tg_id = auth
        row = get_order(order_id)
        if not row or row[1] != tg_id:
            self._send_json(404, {"ok": False, "error": "not_found"})
            return
        try:
            image_bytes = base64.b64decode(str(data.get("image_base64") or ""))
            if not image_bytes:
                raise ValueError("empty")
        except Exception:
            self._send_json(400, {"ok": False, "error": "bad_image"})
            return
        ok, msg = process_receipt_for_order(order_id, image_bytes, f"اپ موبایل ({email})")
        self._send_json(200, {"ok": ok, "message": msg})

    def _handle_app_order_status(self, order_id):
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        username = (params.get("username") or [""])[0]
        password = (params.get("password") or [""])[0]
        email = verify_app_credentials(username, password) if username and password else None
        if not email:
            self._send_json(401, {"ok": False, "error": "invalid_credentials"})
            return
        tg_id = resolve_tg_id_for_email(email)
        row = get_order(order_id)
        if not row or row[1] != tg_id:
            self._send_json(404, {"ok": False, "error": "not_found"})
            return
        result = {"ok": True, "status": row[5]}
        if row[5] == "approved":
            _, acc_body = account_status_response(email)
            result["account"] = acc_body
        self._send_json(200, result)

    def _handle_vakif_webhook(self):
        """دقیقاً همون الگوی /paid برای بانک ایرانی — فقط این‌بار پیامک از
        MacroDroid روی گوشیِ ترکیه (VakıfBank) میاد و مبلغ لیره، نه تومان/
        ریال، پس نیازی به تقسیم بر ۱۰ نداره. راه‌حل موقت تا فعال شدن iyzico."""
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8", errors="ignore")
        params = urllib.parse.parse_qs(body)
        secret = (params.get("secret") or [""])[0]
        amount_raw = (params.get("amount") or [""])[0]
        print(f"[vakif] received: amount_raw={amount_raw!r} secret_ok={secret == VAKIF_WEBHOOK_SECRET}", flush=True)

        if secret != VAKIF_WEBHOOK_SECRET:
            print("[vakif] rejected: bad secret", flush=True)
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"forbidden")
            return
        try:
            amount = round(float(re.sub(r"[^\d.]", "", amount_raw.replace(",", "."))), 2)
        except ValueError:
            print(f"[vakif] rejected: bad amount {amount_raw!r}", flush=True)
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"bad amount")
            return

        order_id = oldest_pending_order_for_try_amount(amount)
        if order_id:
            try:
                ok, msg = do_provision_order(order_id)
                notify_admin( f"🇹🇷 واریز واکیف تأیید و خودکار فعال شد — سفارش #{order_id} ({amount} TRY)\n{msg}")
            except Exception as e:
                print(f"[vakif] خطا در فعال‌سازی سفارش #{order_id}: {e}", flush=True)
        else:
            print(f"[vakif] هیچ سفارش منتظری با مبلغ {amount} TRY پیدا نشد", flush=True)
            notify_admin( f"🇹🇷 واریز {amount} TRY به واکیف دیده شد ولی به هیچ سفارشی نخورد — دستی چک کن.")

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def _handle_iyzico_callback(self):
        """iyzico بعد از تکمیل پرداخت روی صفحه‌ی خودش، مرورگر/وب‌ویوی مشتری
        رو با یه POST فرم (نه JSON) به این آدرس ریدایرکت می‌کنه — طبق مستندات
        خودشون فیلد token همینجا میاد. اینجا وضعیت نهایی رو دوباره از خودِ
        iyzico استعلام می‌کنیم (retrieve)، هیچ‌وقت فقط به رسیدن callback
        اعتماد نمی‌کنیم، بعد یه صفحه‌ی ساده برمی‌گردونیم که بگه برگرد به اپ."""
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8", errors="ignore")
        params = urllib.parse.parse_qs(body)
        token = (params.get("token") or [""])[0]
        ok, msg = (False, "no token") if not token else iyzico_verify_and_provision(token)
        print(f"[iyzico] callback token={token!r} ok={ok} msg={msg}", flush=True)
        html_body = (
            "<html><body style='font-family:sans-serif;text-align:center;padding:40px'>"
            + ("<h2>✅ پرداخت با موفقیت انجام شد</h2><p>برگرد به اپ HermesVPN Connect.</p>"
               if ok else "<h2>⚠️ پرداخت تکمیل نشد</h2><p>اگه مبلغ کسر شده، با پشتیبانی تماس بگیر.</p>")
            + "</body></html>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html_body)))
        self.end_headers()
        self.wfile.write(html_body)

    def _send_json(self, status, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def iyzico_options():
    """فایل کانفیگ رو می‌خونه؛ اگه نساخته شده یا هنوز placeholder داره، None
    برمی‌گردونه — یعنی این روش پرداخت هنوز غیرفعاله. برای فعال‌سازی نهایی:
    sudo nano /root/.hermes-iyzico-config.json
    {"api_key": "...", "secret_key": "...", "base_url": "sandbox-api.iyzipay.com"}
    (base_url رو موقع رفتن به production بذار api.iyzipay.com)"""
    try:
        with open(IYZICO_CONFIG_FILE) as f:
            cfg = json.load(f)
    except Exception:
        return None
    if not cfg.get("api_key") or not cfg.get("secret_key") or cfg.get("api_key") == "PLACEHOLDER":
        return None
    return {
        "api_key": cfg["api_key"],
        "secret_key": cfg["secret_key"],
        "base_url": cfg.get("base_url", "sandbox-api.iyzipay.com"),
    }


def set_order_iyzico_token(order_id, token):
    c = orders_db()
    c.execute("UPDATE orders SET iyzico_token=? WHERE id=?", (token, order_id))
    c.commit()
    c.close()


def iyzico_create_checkout(order_id, amount_toman, tg_id):
    """صفحه‌ی پرداخت کارتیِ میزبانی‌شده روی خودِ iyzico رو می‌سازه — شماره‌کارت
    مشتری هیچ‌وقت از سرور ما رد نمی‌شه (فقط iyzico می‌بینتش)، پس هیچ الزام
    PCI-DSS‌ای روی ما نیست. خروجی: (ok, checkout_url) یا (False, پیام خطا).

    ⚠️ بخش‌های buyer/address پایین جای‌گیرنده (placeholder) هستن چون از
    مشتری‌ها اصلاً KYC نمی‌گیریم (فقط یوزرنیم تلگرام) — iyzico این فیلدها رو
    الزامی می‌دونه ولی مقدار واقعی نداریم. قبل از رفتن به production حتماً
    با پشتیبانی iyzico چک کن این جای‌گیرنده‌ها برای تسویه‌ی واقعی کافی‌ان یا
    باید یه فرم KYC حداقلی به مشتری اضافه کنیم."""
    options = iyzico_options()
    if not options:
        return False, "gateway_not_configured"
    price_try = round(amount_toman / TRY_TOMAN_RATE, 2)
    request = {
        "locale": "en",
        "conversationId": str(order_id),
        "price": str(price_try),
        "paidPrice": str(price_try),
        "currency": "TRY",
        "basketId": f"order-{order_id}",
        "paymentGroup": "PRODUCT",
        "callbackUrl": IYZICO_CALLBACK_URL,
        "enabledInstallments": [1],
        "buyer": {
            "id": str(tg_id),
            "name": "HermesVPN",
            "surname": "Customer",
            "gsmNumber": "+905000000000",
            "email": f"tg{tg_id}@YourChannel.example",
            "identityNumber": "11111111111",
            "lastLoginDate": "2026-01-01 00:00:00",
            "registrationDate": "2026-01-01 00:00:00",
            "registrationAddress": "N/A",
            "ip": "85.34.78.112",
            "city": "Istanbul",
            "country": "Turkey",
            "zipCode": "34000",
        },
        "shippingAddress": {
            "contactName": "HermesVPN Customer", "city": "Istanbul", "country": "Turkey",
            "address": "N/A", "zipCode": "34000",
        },
        "billingAddress": {
            "contactName": "HermesVPN Customer", "city": "Istanbul", "country": "Turkey",
            "address": "N/A", "zipCode": "34000",
        },
        "basketItems": [{
            "id": f"plan-{order_id}", "name": "HermesVPN subscription",
            "category1": "VPN", "itemType": "VIRTUAL", "price": str(price_try),
        }],
    }
    try:
        resp = iyzipay.CheckoutFormInitialize().create(request, options)
        result = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return False, f"iyzico request failed: {e}"
    if result.get("status") != "success":
        return False, result.get("errorMessage", "iyzico error")
    set_order_iyzico_token(order_id, result.get("token"))
    return True, result.get("paymentPageUrl")


def iyzico_verify_and_provision(token):
    """iyzico بعد از تکمیل پرداخت روی صفحه‌ی خودش، به IYZICO_CALLBACK_URL ما
    یه token می‌فرسته؛ اینجا با همون token وضعیت نهایی رو از خودِ iyzico
    می‌پرسیم (هیچ‌وقت فقط به همین که callback صدا زده شده اعتماد نمی‌کنیم)."""
    options = iyzico_options()
    if not options:
        return False, "gateway_not_configured"
    try:
        resp = iyzipay.CheckoutForm().retrieve({"locale": "en", "token": token}, options)
        result = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return False, f"iyzico retrieve failed: {e}"
    if result.get("status") != "success" or result.get("paymentStatus") != "SUCCESS":
        return False, result.get("errorMessage", "payment not completed")
    try:
        order_id = int(result.get("conversationId"))
    except (TypeError, ValueError):
        return False, "bad conversationId"
    return do_provision_order(order_id)


def start_webhook_server():
    server = ThreadingHTTPServer(("0.0.0.0", PAID_WEBHOOK_PORT), PaidWebhookHandler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=WEBHOOK_CERT, keyfile=WEBHOOK_KEY)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()


def check_usdt_payments():
    """همون ایده‌ی وب‌هوک پیامک بانکی، فقط منبع داده TronGrid عمومیه به‌جای
    پیامک. TronGrid برای هر تراکنش TRC20 واردشده به ولت رو برمی‌گردونه؛
    مبلغش رو با سفارش‌های منتظر (بر اساس همون افست میکرو-USDTِ یکتا) تطبیق
    می‌دیم. tx_hash هر بار ذخیره می‌شه تا یه تراکنش دوبار پردازش نشه."""
    try:
        url = (f"https://api.trongrid.io/v1/accounts/{USDT_WALLET_ADDRESS}/transactions/trc20"
               f"?limit=20&contract_address={USDT_TRC20_CONTRACT}&only_to=true&order_by=block_timestamp,desc")
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print("[usdt] درخواست TronGrid ناموفق:", e, flush=True)
        return

    c = orders_db()
    cur = c.cursor()
    cur.execute("SELECT id, usdt_amount FROM orders WHERE status IN ('awaiting_receipt','pending_review') AND usdt_amount IS NOT NULL")
    pending = {round(r[1], 6): r[0] for r in cur.fetchall()}
    cur.execute("SELECT usdt_tx_hash FROM orders WHERE usdt_tx_hash IS NOT NULL")
    seen_hashes = {r[0] for r in cur.fetchall()}
    c.close()
    if not pending:
        return

    for tx in data.get("data", []):
        tx_hash = tx.get("transaction_id")
        if not tx_hash or tx_hash in seen_hashes:
            continue
        try:
            decimals = int(tx.get("token_info", {}).get("decimals", 6))
            amount = round(int(tx["value"]) / (10 ** decimals), 6)
        except Exception:
            continue
        order_id = pending.get(amount)
        if not order_id:
            continue
        print(f"[usdt] تراکنش {tx_hash} با مبلغ {amount} روی سفارش #{order_id} تطبیق یافت", flush=True)
        c2 = orders_db()
        c2.execute("UPDATE orders SET usdt_tx_hash=? WHERE id=?", (tx_hash, order_id))
        c2.commit()
        c2.close()
        try:
            ok, msg = do_provision_order(order_id)
            notify_admin( f"💎 پرداخت USDT تأیید و خودکار فعال شد — سفارش #{order_id}\n{msg}")
        except Exception as e:
            print(f"[usdt] خطا در فعال‌سازی سفارش #{order_id}: {e}", flush=True)


def start_usdt_watcher():
    def loop():
        while True:
            try:
                check_usdt_payments()
            except Exception as e:
                print("[usdt] خطای حلقه‌ی نگهبان:", e, flush=True)
            time.sleep(45)
    threading.Thread(target=loop, daemon=True).start()


def main():
    start_webhook_server()
    start_usdt_watcher()
    offset = 0
    if os.path.exists(OFFSET_FILE):
        offset = int(open(OFFSET_FILE).read().strip() or 0)
    try:   # دکمه‌ی آبی «Menu» تلگرام: /menu منوی اصلی را برمی‌گرداند
        api_call("setMyCommands", {"commands": [
            {"command": "menu", "description": "نمایش منوی اصلی"},
            {"command": "start", "description": "شروع"},
        ]})
    except Exception:
        pass
    while True:
        try:
            # تایم‌اوت سوکت باید از long-poll بلندتر باشد. قبلاً سوکت ۲۵ ثانیه
            # و long-poll ۳۰ ثانیه بود: هر بار که ۲۵ ثانیه پیامی نمی‌آمد سوکت
            # خطا می‌داد، اجرا به except می‌افتاد و ۵ ثانیه می‌خوابید — یعنی
            # ربات در آن ۵ ثانیه هیچ دکمه‌ای را نمی‌شنید. در لاگ ۳ ساعته،
            # ۲۰۰ بار همین اتفاق افتاده بود (~۱۷ دقیقه ناشنوایی).
            resp = api_call("getUpdates", {"offset": offset, "timeout": POLL_TIMEOUT},
                            timeout=POLL_TIMEOUT + 15)
            for update in resp.get("result", []):
                offset = update["update_id"] + 1
                open(OFFSET_FILE, "w").write(str(offset))
                if "inline_query" in update:
                    handle_inline_query(update["inline_query"])
                elif "chosen_inline_result" in update:
                    log_ref_event(update["chosen_inline_result"].get("from", {}).get("id"), "share")
                elif "callback_query" in update:
                    _cq = update["callback_query"]
                    ordinfo.log_action(_cq.get("from", {}).get("id"), "cb", _cq.get("data", ""))
                    try:   # سابقه‌ی کامل گفتگو: دکمه‌ای که مشتری زد
                        _uid = _cq.get("from", {}).get("id")
                        if _uid and _uid != ADMIN_CHAT_ID:
                            log_support_message(_uid, "cin", "🔘 " + ordinfo._label("cb", _cq.get("data", "")))
                    except Exception:
                        pass
                    handle_callback(_cq)
                elif "message" in update:
                    _m = update["message"]
                    if _m.get("chat", {}).get("type") == "private":
                        ordinfo.log_action(_m.get("from", {}).get("id"),
                                           "photo" if _m.get("photo") else ("file" if _m.get("document") else "btn"),
                                           _m.get("text") or _m.get("caption") or "")
                        _uid = _m.get("from", {}).get("id")
                        _txt = _m.get("text") or _m.get("caption") or ""
                        if _uid and _uid != ADMIN_CHAT_ID:
                            log_support_message(_uid, "cin", (_txt or ("📷 عکس" if _m.get("photo") else "📎 فایل" if _m.get("document") else "🎙 پیام"))[:4000])
                            if _txt and not _txt.startswith("/") and moderate_text(_uid, _m.get("from", {}), _txt):
                                continue
                    handle_message(_m)
        except Exception as e:
            # تایم‌اوت خواندن در long-poll یک رویداد عادی است نه خطا؛ بلافاصله
            # دوباره وصل شو. فقط خطای واقعی مکث کوتاه می‌خواهد.
            if isinstance(e, socket.timeout) or "timed out" in str(e).lower():
                continue
            print("loop error:", e)
            time.sleep(2)


if __name__ == "__main__":
    main()
