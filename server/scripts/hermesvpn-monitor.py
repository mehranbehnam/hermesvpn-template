#!/usr/bin/env python3
"""
HermesVPN health monitor — runs periodically (systemd timer), checks the
things that actually matter (functional WS+TLS handshake, not just an open
port; disk; cert expiry; ping; fail2ban activity) and pushes a Telegram
alert to the admin ONLY on a state change (good->bad or bad->good), so it
never spams. State is persisted in STATE_FILE between runs.
"""
import json, os, re, shutil, sqlite3, subprocess, time
import urllib.request, urllib.parse

BOT_TOKEN_FILE = "/root/.hermes-bot-token"
ADMIN_CHAT_ID = 1000000001
DB = "/etc/x-ui/x-ui.db"
ORDERS_DB = "/root/.hermes-orders.db"
CUSTOMER_BOT_TOKEN_FILE = "/root/.hermes-customer-bot-token"
STATE_FILE = "/root/.hermesvpn-monitor-state.json"
STALE_ORDER_MINUTES = 10

PING_WARN_MS = 150
PING_LOSS_WARN_PCT = 10
DISK_WARN_PCT = 85
CERT_WARN_DAYS = 7

SPEEDTEST_URL = "http://203.0.113.20:8090/speedtest_5mb.bin"
SPEEDTEST_MIN_MBPS = 20  # زیر این مقدار بین دو دیتاسنتر یعنی مشکل واقعی سمت سرور
VPN_IFACE = "enp0s6"

with open(BOT_TOKEN_FILE) as f:
    BOT_TOKEN = f.read().strip()
API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def send_message(text):
    url = f"{API}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": ADMIN_CHAT_ID, "text": text}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=15)
    except Exception as e:
        print("telegram send failed:", e)


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            return json.load(open(STATE_FILE))
        except Exception:
            return {}
    return {}


def save_state(state):
    json.dump(state, open(STATE_FILE, "w"))


def sub_domain_current():
    c = sqlite3.connect(DB)
    cur = c.cursor()
    cur.execute("SELECT value FROM settings WHERE key='subDomain'")
    row = cur.fetchone()
    c.close()
    return row[0] if row and row[0] else ""


def main_inbound_host_path():
    c = sqlite3.connect(DB)
    cur = c.cursor()
    cur.execute("SELECT stream_settings FROM inbounds WHERE id=1")
    ss = json.loads(cur.fetchone()[0])
    c.close()
    # وقتی اعتبارسنجی host عمداً خاموشه (تا کانفیگ‌های قدیمیِ دست مشتری‌ها
    # هم قبول بشن)، wsSettings.host خالیه؛ اون‌وقت دامنه‌ی واقعی همون
    # serverName گواهی TLSه. بدون این، پایش با دامنه‌ی خالی تست می‌کرد و
    # هشدار الکیِ «فیلتر شده» می‌داد.
    host = ss["wsSettings"].get("host") or ss.get("tlsSettings", {}).get("serverName", "")
    return host, ss["wsSettings"]["path"]


def _retry_once(check_fn, delay=4):
    """Any client action (block/delete/renew/topup/domain rotation) restarts
    x-ui, which drops Xray for a couple of seconds. The monitor timer can
    land exactly in that gap and read it as a real outage. One retry after
    a short delay tells a self-inflicted few-second restart apart from an
    actual problem, without hiding a check that's still failing afterward."""
    ok, msg = check_fn()
    if ok:
        return ok, msg
    time.sleep(delay)
    return check_fn()


def _check_ws_handshake_once():
    """The exact failure mode from the real incident: port open, but the
    domain/SNI is blocked or misconfigured so the WS upgrade never completes."""
    try:
        host, path = main_inbound_host_path()
    except Exception as e:
        return False, f"نمی‌تونم تنظیمات inbound رو بخونم: {e}"
    try:
        out = subprocess.run([
            "curl", "-sk", "--max-time", "6", "-i",
            "--resolve", f"{host}:443:127.0.0.1",
            f"https://{host}{path}",
            "-H", "Upgrade: websocket", "-H", "Connection: Upgrade",
            "-H", "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==", "-H", "Sec-WebSocket-Version: 13",
        ], capture_output=True, text=True, timeout=10).stdout
        if "101 Switching Protocols" in out:
            return True, "هندشیک WS+TLS موفق"
        return False, f"هندشیک ناموفق (دامنه: {host}) — ممکنه فیلتر شده باشه"
    except Exception as e:
        return False, f"خطا در تست: {e}"


def check_ws_handshake():
    return _retry_once(_check_ws_handshake_once)


def _check_reality_port_once():
    try:
        r = subprocess.run(["bash", "-c", "timeout 4 bash -c 'echo > /dev/tcp/127.0.0.1/8443'"],
                            capture_output=True, timeout=6)
        return r.returncode == 0, "پورت 8443 باز" if r.returncode == 0 else "پورت 8443 پاسخ نمی‌ده"
    except Exception as e:
        return False, f"خطا: {e}"


def check_reality_port():
    return _retry_once(_check_reality_port_once)


def check_ping(host):
    try:
        out = subprocess.run(["ping", "-c", "4", "-W", "2", host],
                              capture_output=True, text=True, timeout=15).stdout
        m = re.search(r"= [\d.]+/([\d.]+)/[\d.]+/", out)
        loss_m = re.search(r"(\d+)% packet loss", out)
        if not m:
            return False, f"بدون پاسخ از {host}"
        avg = float(m.group(1))
        loss = int(loss_m.group(1)) if loss_m else 0
        ok = avg < PING_WARN_MS and loss < PING_LOSS_WARN_PCT
        return ok, f"{host}: میانگین {avg:.0f}ms, {loss}% packet loss"
    except Exception as e:
        return False, f"خطا در پینگ {host}: {e}"


def check_disk():
    total, used, free = shutil.disk_usage("/")
    pct = used / total * 100
    return pct < DISK_WARN_PCT, f"فضای دیسک استفاده‌شده: {pct:.0f}%"


def check_cert_expiry():
    try:
        domain = None
        try:
            host, _ = main_inbound_host_path()
            domain = host
        except Exception:
            pass
        if not domain:
            return True, "دامنه‌ای برای چک گواهی پیدا نشد"
        out = subprocess.run(["openssl", "x509", "-enddate", "-noout", "-in",
                               f"/etc/letsencrypt/live/{domain}/fullchain.pem"],
                              capture_output=True, text=True, timeout=8).stdout
        m = re.search(r"notAfter=(.+)", out)
        if not m:
            return True, "نتونستم تاریخ انقضا رو بخونم"
        import datetime
        expiry = datetime.datetime.strptime(m.group(1).strip(), "%b %d %H:%M:%S %Y %Z")
        days_left = (expiry - datetime.datetime.utcnow()).days
        return days_left > CERT_WARN_DAYS, f"گواهی SSL: {days_left} روز تا انقضا"
    except Exception as e:
        return True, f"چک گواهی ناموفق (نادیده گرفته شد): {e}"


def _fix_mtu():
    """اعمال دوباره‌ی MTU درست + MSS clamp — بی‌خطر برای اجرای تکراری.
    اگر cloud-init یا یک ری‌استارت، MTU=9000 را برگرداند، این خودکار درستش می‌کند."""
    try:
        subprocess.run(["ip", "link", "set", VPN_IFACE, "mtu", "1500"],
                        capture_output=True, timeout=5)
    except Exception as e:
        print("mtu fix failed:", e)


def check_mtu_pmtud():
    """بسته‌ی ۱۴۷۲ بایتی با DF باید بدون افت برسه (یعنی مسیر واقعی ۱۵۰۰ سالمه).
    اگر نه، یعنی MTU/MSS به حالت خراب برگشته — خودکار اصلاح می‌کنیم و دوباره تست."""
    def _once():
        r = subprocess.run(["ping", "-M", "do", "-c", "3", "-s", "1472", "8.8.8.8"],
                            capture_output=True, text=True, timeout=10)
        return "0% packet loss" in r.stdout

    if _once():
        return True, "MTU/PMTU سالم (بسته‌ی بزرگ بدون افت می‌رسه)"

    _fix_mtu()
    if _once():
        return True, "MTU خراب بود، خودکار اصلاح شد و الان سالمه"
    return False, "MTU/PMTU خرابه — بسته‌ی بزرگ افت می‌کنه، اصلاح خودکار هم جواب نداد"


SPEED_STREAK_FILE = "/var/lib/hermesvpn-speed-streak"


def check_throughput():
    """سرعت واقعی دانلود بین این سرور و Hetzner (دو دیتاسنتر خوب اروپایی) —
    برای تشخیص افت سمت سرور (نه کندی سمت مشتری/ISP که از اینجا قابل دیدن نیست).
    اگه Hetzner یا خودِ اینترنت این سرور مشکل داشته باشه، همینجا معلوم می‌شه."""
    try:
        out = subprocess.run(
            ["curl", "-o", "/dev/null", "-s", "-w", "%{speed_download}",
             "--max-time", "15", SPEEDTEST_URL],
            capture_output=True, text=True, timeout=20).stdout.strip()
        speed_bps = float(out or 0)
        mbps = speed_bps * 8 / 1_000_000
    except Exception as e:
        return True, f"تست سرعت اجرا نشد (نادیده گرفته شد): {e}"

    # ★ یک اندازه‌گیری تنها حق ندارد «خرابی» اعلام کند. در عمل این عدد بین
    # ۱۲ و ۷۵ Mbps بالا و پایین می‌رفت — نوسانِ طبیعیِ یک دانلود کوتاه، نه
    # افت سرویس. هر نوسان یک «مشکل جدید» و بعد یک «برطرف شد» می‌ساخت و
    # هشدار واقعی لای همین نویز گم می‌شد.
    #
    # حالا فقط افتِ *پایدار* شمرده می‌شود: سه اندازه‌گیری پیاپی زیر آستانه.
    # یک اندازه‌گیری سالم شمارنده را صفر می‌کند.
    low = 0
    try:
        if os.path.exists(SPEED_STREAK_FILE):
            low = int(open(SPEED_STREAK_FILE).read().strip() or 0)
    except Exception:
        low = 0
    low = low + 1 if mbps < SPEEDTEST_MIN_MBPS else 0
    try:
        with open(SPEED_STREAK_FILE, "w") as f:
            f.write(str(low))
    except Exception:
        pass

    log_event(f"📈 سرعت به Hetzner: {mbps:.0f} Mbps", "routine")
    if low >= 3:
        return False, f"سرعت به Hetzner سه بار پیاپی پایین ماند: {mbps:.0f} Mbps"
    return True, f"سرعت به Hetzner: {mbps:.0f} Mbps"


def check_fail2ban_recent_bans():
    try:
        out = subprocess.run(["fail2ban-client", "status", "sshd"],
                              capture_output=True, text=True, timeout=8).stdout
        m = re.search(r"Currently banned:\s*(\d+)", out)
        count = int(m.group(1)) if m else 0
        return count, out
    except Exception:
        return 0, ""


def check_data_consistency():
    """A client lives in three places that must agree: the `clients` table, the
    inbounds.settings JSON Xray enforces, and the client_traffics meter. They
    drifted silently once — every bot-provisioned account was missing its meter
    row, so x-ui discarded their usage and they all reported a full quota for
    days. Nothing surfaced it, so it gets checked from here."""
    try:
        c = sqlite3.connect(DB)
        cur = c.cursor()
        cur.execute("SELECT email, total_gb, expiry_time FROM clients")
        clients = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        cur.execute("SELECT email, total, expiry_time FROM client_traffics")
        meters = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        inbound = {}
        cur.execute("SELECT id, settings FROM inbounds WHERE id IN (1,2)")
        for iid, s in cur.fetchall():
            for cl in json.loads(s)["clients"]:
                inbound.setdefault(cl["email"], {})[iid] = cl
        c.close()
    except Exception as e:
        return True, f"چک یکپارچگی ناموفق (نادیده گرفته شد): {e}"

    no_meter = [e for e in clients if e not in meters]
    orphan = [e for e in meters if e not in clients]
    not_in_xray = [e for e in clients if len(inbound.get(e, {})) < 2]
    drift = [e for e, (t, x) in clients.items()
             if e in meters and meters[e] != (t, x)]

    problems = []
    if no_meter:
        problems.append(f"{len(no_meter)} کلاینت بدون شمارنده‌ی مصرف (مصرفشون ثبت نمی‌شه): {', '.join(no_meter[:5])}")
    if not_in_xray:
        problems.append(f"{len(not_in_xray)} کلاینت در Xray نیست (لینکشون کار نمی‌کنه): {', '.join(not_in_xray[:5])}")
    if orphan:
        problems.append(f"{len(orphan)} شمارنده‌ی بی‌صاحب: {', '.join(orphan[:5])}")
    if drift:
        problems.append(f"{len(drift)} کلاینت با حجم/انقضای ناهماهنگ: {', '.join(drift[:5])}")

    if problems:
        return False, "ناهماهنگی داده — " + " | ".join(problems)
    return True, f"داده‌ها یکپارچه‌ان ({len(clients)} کلاینت)"


def check_paid_but_disabled():
    """اکانتی که پول داده ولی خاموش مانده — بی‌صداترین حالتِ ممکن.

    ★ چرا جدا از check_data_consistency: آن چک حضورِ کلاینت در
    inbounds.settings را می‌بیند، ولی x-ui کانفیگ زنده‌ی Xray را از جدول
    `clients` می‌سازد. یک اکانت می‌تواند در JSON باشد و درست به نظر برسد،
    ولی چون clients.enable صفر است اصلاً وارد Xray نشود.

    این دقیقاً اتفاق افتاد: تابع تمدید clients.enable را برنمی‌گرداند، پس
    هر تمدیدِ یک اکانتِ منقضی‌شده بی‌صدا شکست می‌خورد — مشتری پول می‌داد،
    پیام «تمدید شد ✅» می‌گرفت و وصل نمی‌شد. ریشه‌اش رفع شد، ولی این چک
    می‌ماند: هر مسیر دیگری هم که enable را جا بیندازد، اینجا دیده می‌شود
    و دیگر لازم نیست مشتری زنگ بزند تا بفهمیم.

    شرط‌ها عمداً سخت‌گیرانه‌اند: فقط کسی که شمارنده‌اش فعال است، منقضی
    نشده و حجمش هم تمام نشده. یعنی واقعاً باید کار کند و نمی‌کند.
    """
    try:
        c = sqlite3.connect(DB)
        cur = c.cursor()
        now = int(time.time() * 1000)
        cur.execute("""SELECT cl.email FROM clients cl
                       JOIN client_traffics ct ON ct.email = cl.email
                       WHERE cl.enable = 0 AND ct.enable = 1
                         AND (ct.expiry_time <= 0 OR ct.expiry_time > ?)
                         AND (ct.total = 0 OR (ct.up + ct.down) < ct.total)""", (now,))
        stuck = [r[0] for r in cur.fetchall()]
        c.close()
    except Exception as e:
        return True, f"چک «پولی ولی خاموش» ناموفق (نادیده گرفته شد): {e}"

    if stuck:
        return False, ("💸 %d اکانتِ پولی خاموش مانده و در Xray نیست — پول داده‌اند و "
                       "وصل نمی‌شوند: %s" % (len(stuck), "، ".join(stuck[:6])))
    return True, "هیچ اکانت پولی‌ای خاموش نمانده"


def notify_customers_status_change(kind, now_ok, other_ok):
    """پیام وضعیت سرویس به مشتری‌های تأییدشده (tg_id ست‌شده) و فعال (منقضی نشده).
    فقط روی تغییر وضعیت صدا زده می‌شود (همان state machine موجود در main)،
    پس اسپم نمی‌شود — دقیقاً مثل هشدار ادمین، اینجا هم فقط لحظه‌ی تغییر پیام می‌رود."""
    label = "🌐 اتصال اصلی (Main)" if kind == "main" else "🛡 اتصال Reality"
    other_label = "Reality" if kind == "main" else "Main"

    if not now_ok:
        if other_ok:
            text = (f"⚠️ {label} موقتاً با مشکل مواجه شده.\n"
                    f"تا رفع کامل، از اتصال {other_label} توی اپت استفاده کن.\n"
                    "در حال بررسی و رفع هستیم.")
        else:
            text = ("🔴 سرویس موقتاً در دسترس نیست.\n"
                    "در حال بررسی و رفع فوری هستیم — چند دقیقه صبر کن.")
    else:
        text = f"✅ {label} دوباره برقرار شد. اگه وصل نمی‌شی، «Update Subscription» بزن."

    now_ms = time.time() * 1000
    try:
        c = sqlite3.connect(DB)
        rows = c.execute(
            "SELECT tg_id FROM clients WHERE tg_id IS NOT NULL AND tg_id != 0 AND (expiry_time > ? OR expiry_time < 0)",
            (now_ms,)).fetchall()
        c.close()
    except Exception as e:
        print("notify_customers_status_change: db read failed:", e)
        return 0, 0

    try:
        token = open(CUSTOMER_BOT_TOKEN_FILE).read().strip()
    except Exception as e:
        print("notify_customers_status_change: cannot read sales bot token:", e)
        return 0, 0

    sent, failed = 0, 0
    for (tg_id,) in rows:
        data = urllib.parse.urlencode({"chat_id": tg_id, "text": text}).encode()
        try:
            urllib.request.urlopen(urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage", data=data), timeout=15)
            sent += 1
        except Exception:
            failed += 1
    return sent, failed


def alert_stale_order_via_sales_bot(order_id, line):
    """Sent through the SALES bot, not this one, so it can carry that bot's
    own approve/reject buttons — the admin can serve the waiting customer
    with one tap straight from the alert instead of hunting for the order."""
    # owner 2026-11-06: admin alerts belong in the ADMIN bot only, never in the sales bot
    # (the owner's sales-bot chat is also his customer chat). The admin bot's own order
    # buttons (ordact:approve/reject) do the same thing as the old sales-bot buttons.
    token = BOT_TOKEN
    keyboard = {"inline_keyboard": [[
        {"text": "📤 ارسال دستی (تأیید و فعال‌سازی)", "callback_data": f"ordact:approve:{order_id}"},
        {"text": "❌ رد کردن", "callback_data": f"ordact:reject:{order_id}"},
    ]]}
    try:  # owner 2026-11-11: one tap to the customer (accounts + chat) and to the price/step details
        _c = sqlite3.connect(ORDERS_DB); _r = _c.execute("SELECT tg_id, tg_name, tg_username FROM orders WHERE id=?", (order_id,)).fetchone(); _c.close()
        if _r and _r[0]:
            keyboard["inline_keyboard"].append([{"text": f"🙋 مشتری {(_r[1] or _r[2] or '')[:20]} — اکانت‌ها و چت", "callback_data": f"cust:{_r[0]}"}])
        keyboard["inline_keyboard"].append([{"text": "👣 جزئیات مبلغ و کارهای مشتری", "callback_data": f"ordv:{order_id}"}])
    except Exception as e:
        print("stale alert extra buttons:", e)
    data = urllib.parse.urlencode({
        "chat_id": ADMIN_CHAT_ID,
        "text": ("⏳ مشتری منتظره! این سفارش رسید فرستاده ولی هنوز تعیین‌تکلیف نشده\n"
                  "(معمولاً یعنی پیامک بانک نرسیده — MacroDroid روی گوشیت رو چک کن):\n\n"
                  f"{line}\n\nمی‌تونی همین‌جا دستی راهش بندازی 👇"),
        "reply_markup": json.dumps(keyboard),
    }).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data), timeout=15)
        return True
    except Exception as e:
        print("stale order alert failed:", e)
        return False


def check_stale_paid_orders():
    """Orders where the customer already sent a receipt but nothing has
    decided them yet. Normally the bank-SMS webhook settles these within
    seconds, so anything sitting here for half an hour means a paying
    customer is waiting — usually because SMS forwarding (MacroDroid on the
    admin's phone) stopped. The daily audit would eventually catch it, but a
    day is far too long to leave a customer hanging."""
    cutoff = int((time.time() - STALE_ORDER_MINUTES * 60) * 1000)
    try:
        c = sqlite3.connect(ORDERS_DB)
        cur = c.cursor()
        cur.execute("""SELECT id, tg_username, tg_name, total_price, created_at
                       FROM orders WHERE status='pending_review' AND created_at <= ?
                       ORDER BY id""", (cutoff,))
        rows = cur.fetchall()
        c.close()
    except Exception as e:
        print("stale order check failed:", e)
        return []
    out = []
    for oid, uname, name, price, created_at in rows:
        who = f"{name or ''} ({uname})".strip() if uname else (name or "?")  # no @: a mention opens the Telegram profile, not our customer panel
        mins = int((time.time() * 1000 - created_at) / 60000)
        out.append((oid, f"#{oid} — {who} — {(price or 0):,} تومان — {mins} دقیقه منتظر"))
    return out


MONITOR_LOG = "/var/log/hermesvpn-monitor.jsonl"
MONITOR_LOG_MAX = 500


def log_event(text, level="info"):
    """هر رویداد پایش را در یک فایل خطی ثبت می‌کند.

    چرا: مسدودی‌های fail2ban هر چند دقیقه یک بار پیش می‌آیند و اگر هرکدام
    یک پیام تلگرام شوند، تایم‌لاین ربات مدیریت پر می‌شود و هشدارهای واقعی
    لای آن‌ها گم می‌شوند. همه‌چیز اینجا می‌ماند و از دکمه‌ی «لاگ مانیتورینگ»
    دیده می‌شود؛ فقط چیزهایی که واقعاً رسیدگی می‌خواهند پیام می‌شوند.
    """
    try:
        rec = {"t": int(time.time()), "level": level, "text": text}
        with open(MONITOR_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        # فایل را کوتاه نگه دار تا بی‌نهایت رشد نکند
        with open(MONITOR_LOG, encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > MONITOR_LOG_MAX:
            with open(MONITOR_LOG, "w", encoding="utf-8") as f:
                f.writelines(lines[-MONITOR_LOG_MAX:])
    except Exception as e:
        print("log_event failed:", e)


# فقط این‌ها پیام می‌شوند. بقیه‌ی چک‌ها فقط در لاگ می‌نشینند و از دکمه‌ی
# «📡 لاگ مانیتورینگ» دیده می‌شوند.
#
# ★ چرا: تایم‌لاین ربات مدیریت پر شده بود از پیام‌های سرعت که بین ۱۲ و ۷۵
# Mbps بالا و پایین می‌رفت — نوسانِ طبیعیِ یک اندازه‌گیری، نه خرابی. هشدار
# واقعی لای همان نویز گم می‌شد. معیار انتخاب ساده است: اگر دیدنش نیمه‌شب
# ارزش بیدار شدن ندارد، نباید پیام شود.
DM_WORTHY = {
    "ws_handshake",      # مسیر اصلی مشتری‌ها خوابیده
    "reality_port",      # مسیر دوم خوابیده
    "paid_but_disabled", # کسی پول داده و سرویس نمی‌گیرد
    "cert",              # گواهی دارد منقضی می‌شود
    "disk",              # دیسک پر شده
}


CHANNEL_POST_MIN_OUTAGE_S = 360   # only outages of 6+ minutes get a public «resolved» post (item 11)


def post_channel_recovered(minutes):
    """Trust message in @YourChannel after a real outage was fixed (customers already get a DM)."""
    text = ("✅ اختلال کوتاه سرویس برطرف شد و HermesVPN الان به‌طور کامل برقراره.\n\n"
            "اگه هنوز وصل نمی‌شی، توی برنامه «به‌روزرسانی اشتراک» رو بزن یا از ربات @YourSalesBot "
            "«دریافت دوباره‌ی لینک» رو بگیر.\nممنون از صبوری‌تون 🙏")
    try:
        token = open(CUSTOMER_BOT_TOKEN_FILE).read().strip()
        urllib.request.urlopen(urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=urllib.parse.urlencode({"chat_id": "@YourChannel", "text": text}).encode()), timeout=15)
        log_event(f"📣 پیام «اختلال برطرف شد» در کانال منتشر شد (قطعی حدود {minutes} دقیقه)", "info")
    except Exception as e:
        print("channel recovery post failed:", e)


def main():
    state = load_state()
    new_state = {}
    alerts = []

    checks = [
        ("ws_handshake", check_ws_handshake()),
        ("reality_port", check_reality_port()),
        ("ping_google", check_ping("8.8.8.8")),
        ("ping_cloudflare", check_ping("1.1.1.1")),
        ("disk", check_disk()),
        ("cert", check_cert_expiry()),
        ("data_consistency", check_data_consistency()),
        ("paid_but_disabled", check_paid_but_disabled()),
        ("mtu_pmtud", check_mtu_pmtud()),
        ("throughput", check_throughput()),
    ]

    check_results = dict(checks)
    for key, (ok, detail) in checks:
        new_state[key] = ok
        prev = state.get(key)
        if prev is None:
            continue  # first run: establish baseline, don't alert
        if prev and not ok:
            log_event(f"🔴 مشکل جدید: {detail}", "problem")
            if key in DM_WORTHY:
                alerts.append(f"🔴 مشکل جدید: {detail}")
        elif not prev and ok:
            log_event(f"🟢 برطرف شد: {detail}", "recovered")
            if key in DM_WORTHY:
                alerts.append(f"🟢 برطرف شد: {detail}")

        if key in ("ws_handshake", "reality_port"):
            if not ok:
                new_state[f"{key}_down_since"] = state.get(f"{key}_down_since") or time.time()
            elif prev is False:
                down = time.time() - (state.get(f"{key}_down_since") or time.time())
                other_ok = check_results.get("reality_port" if key == "ws_handshake" else "ws_handshake", (True, ""))[0]
                if down >= CHANNEL_POST_MIN_OUTAGE_S and other_ok and not state.get("_channel_posted_" + key):
                    post_channel_recovered(int(down // 60))

        # اطلاع به مشتری‌ها فقط برای دو چیزی که واقعاً روی اتصال اثر دارد
        if key in ("ws_handshake", "reality_port") and ok != prev:
            other_key = "reality_port" if key == "ws_handshake" else "ws_handshake"
            other_ok = check_results.get(other_key, (True, ""))[0]
            kind = "main" if key == "ws_handshake" else "reality"
            try:
                sent, failed = notify_customers_status_change(kind, ok, other_ok)
                print(f"customer notify [{kind} ok={ok}]: sent={sent} failed={failed}")
            except Exception as e:
                print("customer notify failed:", e)

    ban_count, _ = check_fail2ban_recent_bans()
    prev_bans = state.get("fail2ban_count", 0)
    if ban_count > prev_bans:
        # مسدودی fail2ban روال عادی است و رسیدگی نمی‌خواهد — فقط ثبت می‌شود
        log_event(f"🛡 fail2ban: {ban_count - prev_bans} IP جدید مسدود شد (مجموع: {ban_count})", "routine")
    new_state["fail2ban_count"] = ban_count

    # paying customers left waiting — alert once per order, not every run
    stale = check_stale_paid_orders()
    already = set(state.get("stale_orders_alerted", []))
    for oid, line in stale:
        if oid not in already:
            alert_stale_order_via_sales_bot(oid, line)
    new_state["stale_orders_alerted"] = [oid for oid, _ in stale]

    save_state(new_state)

    if alerts:
        send_message("📡 مانیتورینگ HermesVPN:\n\n" + "\n".join(alerts))


if __name__ == "__main__":
    main()
