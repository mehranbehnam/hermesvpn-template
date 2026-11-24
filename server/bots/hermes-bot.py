#!/usr/bin/env python3
"""
HermesVPN Telegram bot — menu-driven client management for a 3x-ui (Xray) VPN server.
Runs standalone as a systemd service (hermes-bot.service); independent of any chat session.
Source of truth for state: /etc/x-ui/x-ui.db (sqlite). Always keep the normalized
`clients`/`client_inbounds` tables AND the `inbounds.settings` JSON blob in sync —
the panel UI reads the former, Xray itself reads the latter.
"""
import sqlite3, json, time, uuid, random, string, subprocess, os, re, html, csv, threading
import urllib.request, urllib.parse
import socket
import ssl
from datetime import datetime, timedelta, timezone
import jdatetime
import arabic_reshaper
from bidi.algorithm import get_display
from fpdf import FPDF
from fpdf.fonts import FontFace
from fpdf.enums import TableCellFillMode


def esc(s):
    return html.escape(str(s))

BOT_TOKEN_FILE = "/root/.hermes-bot-token"
CF_CREDS_FILE = "/root/.secrets/cloudflare.ini"
ALLOWED_CHAT_IDS = {1000000001}
DB = "/etc/x-ui/x-ui.db"
VAZIR_REGULAR = "/usr/share/fonts/vazirmatn/Vazirmatn-Regular.ttf"
VAZIR_BOLD = "/usr/share/fonts/vazirmatn/Vazirmatn-Bold.ttf"
PDF_DIR = "/root/fin-reports"
ORDERS_DB = "/root/.hermes-orders.db"  # customer (sales) bot's orders db — shared; also used to read/write the auto_approve setting
IRAN_TZ = timezone(timedelta(hours=3, minutes=30))
SUB_PORT = "2087"   # full HermesVPN sub (all routes) served by the sales bot; x-ui 2096 sub still works for old links
QR_DIR = "/root/client-qr"
OFFSET_FILE = "/root/.hermes-bot-offset"
ZONE_ID = "6f1f657c80721cdb58bb8f4a0a9cb46f"
ZONE_NAME = "example.com"
# IP سرور فعلی (اوراکل). این مقدار بعد از مهاجرت به‌روز نشده بود و روی IP
# سرور قدیمی مونده بود؛ نتیجه‌ش این شد که rotate_domain رکورد کلادفلر رو به
# یه سرور مرده اشاره می‌داد و با هر چرخش دامنه، کل مشتری‌های مسیر WS قطع
# می‌شدن. اگه سرور دوباره مهاجرت کرد، همین‌جا هم باید عوض بشه.
ORIGIN_IP = "203.0.113.10"

with open(BOT_TOKEN_FILE) as f:
    BOT_TOKEN = f.read().strip()
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ---------- «حالت بله» (owner 2026-11-20، درخواست نشست ضد فیلتر) ----------
# همین کد با HERMES_BALE_MODE=1 (سرویس جدای hermes-bot-bale) رابط ادمین را روی بازوی بله «دفترچه» می‌آورد تا در
# فیلترینگ سنگین هم کنترل باشد. سرویس تلگرامی hermes-bot دست نمی‌خورد. در بله (بدون رمزنگاری سرتاسری):
# هیچ لینک/کانفیگ/IP/دامنه/QR/فایل نمی‌رود و واژه‌های حساس خنثی می‌شوند (_bale_scrub).
BALE_MODE = os.environ.get("HERMES_BALE_MODE") == "1"
if BALE_MODE:
    with open("/home/ubuntu/antifilter/.secrets/bale_admin_token") as f:
        BOT_TOKEN = f.read().strip()
    API = f"https://tapi.bale.ai/bot{BOT_TOKEN}"
    ALLOWED_CHAT_IDS = {1000000002}
    OFFSET_FILE = "/root/.hermes-bot-bale-offset"
_BALE_WORDS = [("فیلترشکن", "سرویس"), ("وی پی ان", "سرویس"), ("وی‌پی‌ان", "سرویس"), ("VPN", "سرویس"), ("vpn", "سرویس"),
               ("کانفیگ", "تنظیمات"), ("config", "تنظیمات"), ("Config", "تنظیمات"), ("Reality", "مسیر ۲"), ("XHTTP", "مسیر ۳"),
               ("پروکسی", "مسیر"), ("proxy", "مسیر")]


def _bale_scrub(t):
    if not isinstance(t, str):
        return t
    t = re.sub(r"<[^>]+>", "", t)                                                     # بله: بدون HTML
    t = t.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))   # اعداد فارسی → لاتین برای پوشاندن
    t = re.sub(r"\bIR\s?\d{2}(?:[\s-]?\d){22}\b", "IR****", t, flags=re.I)                 # شبا
    t = re.sub(r"(?<!\d)(?:\d[\s-]?){15}\d(?!\d)", lambda m: "****" + re.sub(r"\D", "", m.group(0))[-4:], t)  # کارت ۱۶ رقمی
    t = re.sub(r"(?<!\d)(?:\+98|0098|0)9\d{9}(?!\d)", lambda m: "****" + m.group(0)[-4:], t)  # موبایل
    t = re.sub(r"(?<!\d)\d{8,}(?!\d)", lambda m: "****" + m.group(0)[-4:], t)            # پیگیری/مرجع/شناسه‌های بلند
    t = re.sub(r"\b(?:vless|vmess|trojan|ss|hysteria2?|tuic)://\S+", "[در دفترچه‌ی خصوصی]", t)
    t = re.sub(r"https?://\S+", "[لینک در دفترچه‌ی خصوصی]", t)
    t = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b", "[IP]", t)
    t = re.sub(r"\b[\w-]+\.(?:example\.com|example\.net)\b", "[دامنه]", t)
    for a, b in _BALE_WORDS:
        t = t.replace(a, b)
    return t


AF_DOCS = "/home/ubuntu/antifilter/apps-script/af_docs.py"
CUR_REQ = {"text": "", "ts": 0}          # آخرین درخواست صاحب (دکمه/کال‌بک/ویس) — برای عنوان یادداشت‌های دفترچه
_SECTION_RULES = [("پراکسی", "📡 پراکسی تلگرام"), ("mtproto", "📡 پراکسی تلگرام"), ("dns-new", "👤 حساب‌های تونل مشتری"),
                  ("dns-show", "👤 حساب‌های تونل مشتری"), ("dns-pause", "👤 حساب‌های تونل مشتری"), ("dns-resume", "👤 حساب‌های تونل مشتری"),
                  ("dns-del", "👤 حساب‌های تونل مشتری"), ("dns-hvpn owner", "🧭 تونل DNS — حساب من"), ("dns-hvpn", "👤 حساب‌های تونل مشتری"), ("afpause", "👤 حساب‌های تونل مشتری"), ("afresume", "👤 حساب‌های تونل مشتری"),
                  ("دانلود اپ", "📌 سایر"), ("تونل", "🧭 تونل DNS — حساب من"), ("dns", "🧭 تونل DNS — حساب من"),
                  ("سفارش", "🧾 سفارش‌ها و مالی"), ("ord", "🧾 سفارش‌ها و مالی"), ("مالی", "🧾 سفارش‌ها و مالی"), ("فروش", "🧾 سفارش‌ها و مالی"),
                  ("پشتیبانی", "💬 پشتیبانی"), ("sup", "💬 پشتیبانی"), ("گفتگو", "💬 پشتیبانی"),
                  ("رفع کلی", "🖥 سرور / رفع مشکل"), ("سرور", "🖥 سرور / رفع مشکل"), ("پینگ", "🖥 سرور / رفع مشکل"),
                  ("کلاینت", "👥 کلاینت‌ها"), ("status", "👥 کلاینت‌ها"), ("opencl", "👥 کلاینت‌ها"), ("cust", "👥 کلاینت‌ها")]


def _note_meta(kind):
    req = CUR_REQ.get("text") or ""
    hay = f"{kind} {req}".lower()
    section = next((sec for k, sec in _SECTION_RULES if k.lower() in hay), "🤖 دستیار" if req else "📌 سایر")
    m = re.search(r"\b(dns-(?:new|show|pause|resume|del|hvpn)|afpause|afresume|afacc|afshow|opencl|status:pick)[ :]+([A-Za-z0-9_.-]{3,40})", f"{kind} {req}")
    customer = m.group(2) if m else ""
    title = str(kind).replace("مسیر اضطراری: ", "").strip()
    title = {"mtproto": "لینک پراکسی تلگرام", "dns": "تنظیمات تونل DNS من", "sendMessage": "پیام ربات", "editMessageText": "پیام ربات",
             "sendPhoto": "تصویر", "sendDocument": "فایل"}.get(title.split()[0] if title else "", title)
    return section, title, req, customer


def bale_private_note(kind, payload):
    """متن کامل و پوشانده‌نشده‌ی موارد حساس → دفترچه‌ی خصوصی صاحب (Google Doc، از راه Apps Script نشست ضد فیلتر).
    پس‌زمینه و غیرهمزمان (هر تماس ۲-۵ ثانیه)، با کاربر ubuntu چون فایل‌های کلید مال اوست."""
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    if not text or not os.path.exists(AF_DOCS):
        return None
    section, title, req, customer = _note_meta(kind)
    def _run():
        try:
            # ساختار برای مرتب نوشتن در Google Doc: بخش › عنوان › مشتری (اگر af_docs «--json» را پشتیبانی کند، همان)
            if os.path.exists("/home/ubuntu/antifilter/.note_json_ok"):
                args, inp = ["note", "--json", "-"], json.dumps({"section": section, "title": title, "request": req,
                                                                  "customer": customer, "ts": int(time.time()), "body": text[:20000]},
                                                                 ensure_ascii=False)
            else:
                label = " › ".join(x for x in (section, title, customer) if x)
                args, inp = ["note", label, "-"], (f"درخواست: {req}\n\n" if req else "") + text[:20000]
            r = subprocess.run(["sudo", "-u", "ubuntu", AF_DOCS] + args, input=inp,
                               capture_output=True, text=True, timeout=60)
            if '"ok": true' not in (r.stdout or "").replace('"ok":true', '"ok": true'):
                print("bale_private_note failed:", (r.stdout or r.stderr)[:200])
        except Exception as e:
            print("bale_private_note error:", e)
    threading.Thread(target=_run, daemon=True).start()
    return True


def cf_token():
    with open(CF_CREDS_FILE) as f:
        for line in f:
            if "dns_cloudflare_api_token" in line:
                return line.split("=", 1)[1].strip()
    raise RuntimeError("cloudflare token not found")


# ---------- low-level telegram api ----------

POLL_TIMEOUT = 30


_LINK_FIELD = {"sendMessage": "text", "editMessageText": "text", "sendPhoto": "caption", "sendDocument": "caption",
               "editMessageCaption": "caption", "sendVideo": "caption"}


def api_call(method, data=None, files=None, timeout=25):
    url = f"{API}/{method}"
    payload = dict(data or {})
    if BALE_MODE:
        if method in ("sendPhoto", "sendDocument", "sendVideo"):
            bale_private_note(method, "[" + method + "] " + str(payload.get("caption") or ""))
            method, files = "sendMessage", None
            url = f"{API}/sendMessage"
            payload = {"chat_id": payload.get("chat_id"), "text": "📒 این مورد فایل/تصویر دارد — جزئیات در دفترچه‌ی خصوصی.",
                       "reply_markup": payload.get("reply_markup")}
            payload = {k: v for k, v in payload.items() if v is not None}
        if method == "setMyCommands":
            return {"ok": True}
        for fld in ("text", "caption"):
            if fld in payload:
                _orig = payload[fld]
                payload[fld] = _bale_scrub(_orig)
                if isinstance(_orig, str) and re.sub(r"<[^>]+>", "", _orig) != payload[fld]:
                    bale_private_note(method, re.sub(r"<[^>]+>", "", _orig))   # نسخه‌ی کامل فقط در دفترچه‌ی خصوصی
        rm = payload.get("reply_markup")
        if isinstance(rm, dict) and rm.get("inline_keyboard"):
            rows = [[dict(b, text=_bale_scrub(b.get("text", ""))) for b in row if "url" not in b] for row in rm["inline_keyboard"]]
            payload["reply_markup"] = dict(rm, inline_keyboard=[r for r in rows if r])
        payload.pop("parse_mode", None); payload.pop("entities", None); payload.pop("caption_entities", None)
    # هر اسم اکانت/یوزرنیم مشتری در پیام‌های ادمین = لینک به کارت اکانت و پنل مشتری (owner 2026-11-12)
    if method in _LINK_FIELD and not BALE_MODE:
        try:
            if int(payload.get("chat_id", 0)) in ALLOWED_CHAT_IDS:
                import sys as _sys
                _sys.path.insert(0, "/usr/local/bin")
                import hermesvpn_ordinfo as _oi
                _oi.linkify_payload(payload, _LINK_FIELD[method])
        except Exception as _e:
            print("linkify skipped:", _e)
    for k, v in list(payload.items()):
        if isinstance(v, (dict, list)):
            payload[k] = json.dumps(v)
    if files:
        boundary = "----HermesBotBoundary"
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


def _auto_menu(chat_id, reply_markup):
    """هر پیامی که کیبورد ندارد منوی اصلی را با خودش می‌برد تا منو هیچ‌وقت نپرد
    (به‌جز وقتی ادمین در حالت چت پشتیبانی است که کیبورد خودش را دارد)."""
    if reply_markup is None and chat_id not in globals().get("SUPPORT_TARGET", {}):
        return main_menu()
    return reply_markup


def send_message(chat_id, text, reply_markup=None, parse_mode=None):
    reply_markup = _auto_menu(chat_id, reply_markup)
    data = {"chat_id": chat_id, "text": text}
    if reply_markup is not None:
        data["reply_markup"] = reply_markup
    if parse_mode:
        data["parse_mode"] = parse_mode
    api_call("sendMessage", data)


def edit_message(chat_id, message_id, text, reply_markup=None, parse_mode=None):
    data = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup is not None:
        data["reply_markup"] = reply_markup
    if parse_mode:
        data["parse_mode"] = parse_mode
    try:
        api_call("editMessageText", data)
    except Exception:
        send_message(chat_id, text, reply_markup, parse_mode)


def answer_callback(callback_id, text=None):
    data = {"callback_query_id": callback_id}
    if text:
        data["text"] = text
    try:
        api_call("answerCallbackQuery", data)
    except Exception:
        pass


def send_photo(chat_id, path, caption, reply_markup=None):
    reply_markup = _auto_menu(chat_id, reply_markup)
    with open(path, "rb") as f:
        data = {"chat_id": chat_id, "caption": caption}
        if reply_markup is not None:
            data["reply_markup"] = reply_markup
        api_call("sendPhoto", data, files={"photo": (os.path.basename(path), f.read())})


def send_document(chat_id, path, caption=None, reply_markup=None):
    reply_markup = _auto_menu(chat_id, reply_markup)
    with open(path, "rb") as f:
        data = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption
        if reply_markup is not None:
            data["reply_markup"] = reply_markup
        api_call("sendDocument", data, files={"document": (os.path.basename(path), f.read())})


def randid(n=16):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


# ---------- keyboards ----------

# ---------- owner 2026-11-14: منوی دسته‌بندی‌شده به‌جای ۲۸ دکمه پشت هم ----------
# همه‌ی دکمه‌های قبلی با همان متن سر جایشان‌اند (handle_menu_button دست نخورده)، فقط داخل
# دسته‌ها چیده شده‌اند. چون فقط یک ادمین داریم، «دسته‌ی باز» یک متغیر سراسری است و _auto_menu
# همان دسته را دوباره می‌آورد تا بعد از هر کار، منو به ریشه نپرد.
MENU_HOME = "🏠 منوی اصلی"
MENU_CATS = {
    "👥 کلاینت‌ها": [
        ["➕ کلاینت جدید", "📦 ساخت انبوه"],
        ["📋 لیست کلاینت‌ها", "🔍 وضعیت کلاینت"],
        ["🔄 تمدید", "➕ افزایش حجم/زمان"],
        ["🔒 مسدود/فعال کلاینت", "🔒 لیست مسدودی‌ها"],
        ["🗑 حذف کلاینت", "🕵️ لاگ فعالیت اکانت"],
        ["🔄 تمدید انبوه", "🗑 حذف انبوه"],
    ],
    "🧾 سفارش‌ها و مالی": [
        ["🧾 سفارش‌ها", "📊 گزارش مالی"],
        ["⚙️ تنظیمات پرداخت خودکار"],
    ],
    "🆘 پشتیبانی": [
        ["💬 گفتگوهای اخیر"],
    ],
    "🖥 سرور": [
        ["🏓 پینگ سرور", "📡 وضعیت کامل سیستم"],
        ["📈 نمودار ترافیک سرور", "📡 لاگ مانیتورینگ"],
        ["🌐 رفع فیلترینگ", "🧪 اکانت تست کیفیت"],
        ["🔧 نگهداری سرور", "🛠 رفع کلی مشکل"],
    ],
    "🆘 مسیرهای اضطراری": [
        ["📡 پراکسی تلگرام", "🧭 تونل DNS (حساب من)"],
        ["➕ حساب تونل برای مشتری", "📋 فهرست حساب‌ها"],
        ["📲 دانلود اپ‌ها"],
    ],
    "📣 فروش و تبلیغ": [
        ["🎁 دعوت‌ها", "📣 یادآوری به خریداران"],
        ["🎡 لاگ گردونه‌ی شانس"],
    ],
}
MENU_ROOT = [
    ["📊 داشبورد"],
    ["👥 کلاینت‌ها", "🧾 سفارش‌ها و مالی"],
    ["🆘 پشتیبانی", "🖥 سرور"],
    ["📣 فروش و تبلیغ", "❓ راهنما"],
    ["🆘 مسیرهای اضطراری", "🛠 رفع کلی مشکل"],
]
CUR_MENU = {"cat": None}


def main_menu():
    cat = CUR_MENU.get("cat")
    rows = (MENU_CATS[cat] + [[MENU_HOME]]) if cat in MENU_CATS else MENU_ROOT
    return {
        "keyboard": rows,
        "resize_keyboard": True,
        # is_persistent عمداً نیست: مالک باید بتواند منو را با دکمه‌ی کیبورد پایین ببرد تا جا برای
        # دیدن پیام‌ها باز شود. گم نشدنش را _auto_menu تضمین می‌کند (هر پیام منو را دوباره می‌فرستد)
        # و دستور /menu در دکمه‌ی آبی «Menu» تلگرام.
    }


def dashboard_text():
    """خلاصه‌ی یک‌نگاه: فروش امروز، سفارش‌های منتظر، کاربران، سلامت سرور."""
    L = ["📊 <b>داشبورد</b> — " + to_jalali(datetime.now(IRAN_TZ))]
    try:
        day0 = datetime.now(IRAN_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        t0 = int(day0.timestamp() * 1000)
        c = sqlite3.connect(ORDERS_DB)
        n, tot = c.execute("SELECT COUNT(*), COALESCE(SUM(total_price),0) FROM orders WHERE status='approved' AND decided_at>=?", (t0,)).fetchone()
        wait = c.execute("SELECT COUNT(*) FROM orders WHERE status='awaiting_receipt' AND created_at>=?", (t0 - 86400000,)).fetchone()[0]
        rev = c.execute("SELECT COUNT(*) FROM orders WHERE status='pending_review'").fetchone()[0]
        c.close()
        L += ["", f"💰 فروش امروز: {n} سفارش — {tot:,} تومان",
              f"🔎 رسید منتظر بررسی: {rev}" + (" ⚠️" if rev else ""),
              f"🕓 منتظر واریز (۲۴ ساعت اخیر): {wait}"]
    except Exception as e:
        L.append(f"سفارش‌ها خوانده نشد: {e}")
    try:
        x = sqlite3.connect(DB)
        now = int(time.time() * 1000)
        total = x.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
        active = x.execute("SELECT COUNT(*) FROM clients WHERE enable=1 AND (expiry_time<=0 OR expiry_time>?)", (now,)).fetchone()[0]
        soon = x.execute("SELECT COUNT(*) FROM clients WHERE enable=1 AND expiry_time>? AND expiry_time<?", (now, now + 3 * 86400000)).fetchone()[0]
        x.close()
        online = len(get_online_emails())
        L += ["", f"👥 اکانت‌ها: {total} (فعال {active}) — 🔵 آنلاین الان: {online}",
              f"⏳ تا ۳ روز دیگر تمام می‌شوند: {soon}"]
    except Exception as e:
        L.append(f"اکانت‌ها خوانده نشد: {e}")
    try:
        xs = subprocess.run(["systemctl", "is-active", "x-ui", "hermes-customer-bot"], capture_output=True, text=True).stdout.split()
        load = open("/proc/loadavg").read().split()[0]
        ok = all(v == "active" for v in xs)
        L += ["", f"🖥 سرور: {'✅ سالم' if ok else '⚠️ ' + ' / '.join(xs)} — بار پردازنده {load}"]
    except Exception:
        pass
    return "\n".join(L)


def show_dashboard(chat_id):
    send_message(chat_id, dashboard_text(), parse_mode="HTML",
                 reply_markup=inline([[{"text": "🧾 سفارش‌های منتظر", "callback_data": "ord:open:0"},
                                       {"text": "🔄 تازه‌سازی", "callback_data": "dash"}]]))


AI_KEY_FILE = "/root/.hermesvpn-openrouter-key"


def save_ai_key(chat_id, message_id, key):
    try:
        api_call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
    except Exception:
        pass
    key = key.split()[0]
    try:
        req = urllib.request.Request("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"})
        d = json.load(urllib.request.urlopen(req, timeout=20)).get("data", {})
    except Exception as e:
        send_message(chat_id, f"❌ این کلید کار نکرد ({type(e).__name__}). از OpenRouter دوباره کپی کن و بفرست.")
        return
    old = os.umask(0o077)
    try:
        with open(AI_KEY_FILE, "w") as f:
            f.write(key + "\n")
    finally:
        os.umask(old)
    os.chmod(AI_KEY_FILE, 0o600)
    lim = d.get("limit")
    send_message(chat_id, "✅ کلید هوش مصنوعی ذخیره شد و پیامش پاک شد.\n"
                 f"نام: {d.get('label') or '—'} — سقف: {('$' + str(lim)) if lim is not None else 'بدون سقف'} — مصرف تا الان: ${d.get('usage', 0):.4f}")



# ======================= owner 2026-11-14: دستیار هوش مصنوعی ربات مدیریت (Claude Haiku 5.5) =======================
# هر متن آزادی که منو/فرم/گفتگوی پشتیبانی نباشد به دستیار می‌رود. خواندن اطلاعات را خودش انجام می‌دهد؛
# هر کار تغییردهنده (ساخت، تمدید، شارژ، مسدود، حذف، تأیید/رد سفارش، پیام به مشتری) فقط «پیشنهاد» می‌شود و
# با دکمه‌ی ✅ ادمین اجرا می‌شود — هیچ کاری بی‌تأیید انجام نمی‌شود.
import sys as _sys_ai
_sys_ai.path.insert(0, "/usr/local/bin")
try:
    import hermesvpn_ai as AIM
except Exception as _e:
    AIM = None
    print("AI module missing:", _e)
AI_HISTORY = {}
AI_PENDING = {}
_TAG_RX = re.compile(r"<[^>]+>")


def _plain(t):
    return html.unescape(_TAG_RX.sub("", t or ""))


def _ai_client_status(name):
    st = status_client(name)
    if st is None:
        x = sqlite3.connect(DB); r = x.execute("SELECT email FROM clients WHERE lower(email)=lower(?)", (name,)).fetchone(); x.close()
        if r:
            st = status_client(r[0]); name = r[0]
    if st is None:
        return f"اکانتی به اسم {name} نیست"
    label, _ = client_extra_info(name)
    gb = lambda b: f"{b / 1024**3:.2f}GB"
    return json.dumps({"account": name, "label": label, "enabled": bool(st["enable"]), "online": st["online_state"],
                       "total": "نامحدود" if not st["total_b"] else gb(st["total_b"]), "used": gb(st["used_b"]),
                       "remaining": gb(st["remain_b"]) if st["total_b"] else "نامحدود", "time_left": st["remain_txt"],
                       "owner_tg_id": st["tg_id"]}, ensure_ascii=False)


def _ai_search_clients(query=""):
    x = sqlite3.connect(DB)
    rows = x.execute("SELECT email, enable, tg_id FROM clients WHERE email LIKE ? ORDER BY id DESC LIMIT 30", (f"%{query}%",)).fetchall()
    x.close()
    online = get_online_emails()
    return json.dumps([{"account": e, "enabled": bool(en), "online": e in online, "tg_id": tg} for e, en, tg in rows], ensure_ascii=False) or "[]"


def _ai_list_orders(filter="open", limit=15):
    c = sqlite3.connect(ORDERS_DB)
    q = "SELECT id, tg_username, tg_name, plan_key, status, total_price, target_email, created_at FROM orders"
    if filter == "open":
        q += " WHERE status IN ('awaiting_receipt','pending_review')"
    elif filter == "today":
        t0 = int(datetime.now(IRAN_TZ).replace(hour=0, minute=0, second=0).timestamp() * 1000)
        q += f" WHERE created_at >= {t0}"
    rows = c.execute(q + " ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    c.close()
    return json.dumps([{"order": i, "customer": u or n, "plan": p, "status": st, "amount_toman": t, "renew_of": tg,
                        "created": datetime.fromtimestamp((cr or 0) / 1000, IRAN_TZ).strftime("%m/%d %H:%M")}
                       for i, u, n, p, st, t, tg, cr in rows], ensure_ascii=False)


def _ai_get_order(order_id):
    text, _ = order_card(int(order_id))
    return _plain(text) if text else "سفارش پیدا نشد"


def _ai_get_customer(who):
    who = str(who).strip()
    tg = int(who) if who.isdigit() else customer_from_text("@" + who.lstrip("@"))
    if not tg:
        x = sqlite3.connect(DB); r = x.execute("SELECT tg_id FROM clients WHERE lower(email)=lower(?)", (who,)).fetchone(); x.close()
        tg = r[0] if r and r[0] else None
    if not tg:
        return "مشتری پیدا نشد"
    import hermesvpn_ordinfo as _oi
    text, _ = _oi.customer_panel(int(tg), online=get_online_emails())
    return _plain(text)


def _ai_sales(period="today"):
    return _plain(format_sales_report(period if period in ("today", "week", "month", "all") else "today"))


def _ai_support_history(tg_id):
    return _plain(support_history_text(int(tg_id)))[-3500:]


def _ai_propose(action, summary, name=None, gb=None, days=None, order_id=None, tg_id=None, text=None):
    pid = f"{int(time.time()*1000) % 10**9}"
    AI_PENDING[pid] = {"action": action, "name": name, "gb": gb, "days": days, "order_id": order_id, "tg_id": tg_id, "text": text}
    send_message(next(iter(ALLOWED_CHAT_IDS)), f"⚙️ پیشنهاد دستیار:\n{summary}",
                 reply_markup=inline([[{"text": "✅ انجام بده", "callback_data": f"aiok:{pid}"},
                                       {"text": "❌ نه", "callback_data": f"aino:{pid}"}]]))
    return "پیشنهاد برای ادمین با دکمه‌ی تأیید فرستاده شد؛ تا تأیید نکند اجرا نمی‌شود."


def ai_execute(chat_id, pid):
    a = AI_PENDING.pop(pid, None)
    if not a:
        send_message(chat_id, "این پیشنهاد دیگر معتبر نیست."); return
    act, name = a["action"], a.get("name")
    gb = float(a["gb"]) if a.get("gb") not in (None, "") else None
    days = float(a["days"]) if a.get("days") not in (None, "") else None
    if act == "create":
        finish_create(chat_id, name, gb, days)
    elif act == "renew":
        do_renew(chat_id, name, gb, days)
    elif act == "topup":
        finish_topup(chat_id, {"name": name, "gb": gb, "days": days})
    elif act in ("block", "unblock"):
        ok = set_client_enabled(name, act == "unblock")
        send_message(chat_id, ("✅ " if ok else "❌ ") + (f"{name} " + ("فعال شد" if act == "unblock" else "مسدود شد") if ok else "پیدا نشد"))
    elif act == "delete":
        ok = delete_client(name)
        send_message(chat_id, f"🗑 {name} حذف شد." if ok else f"{name} پیدا نشد.")
    elif act in ("approve_order", "reject_order"):
        res = customer_order_action(str(a["order_id"]), "approve" if act == "approve_order" else "reject")
        send_message(chat_id, ("✅ " if res.get("ok") else "⚠️ ") + (res.get("text") or ""))
    elif act in ("tunnel_new", "tunnel_pause", "tunnel_resume", "tunnel_delete"):
        nm = af_clean_name(name or "")
        if not nm:
            send_message(chat_id, "اسم حساب تونل معتبر نیست."); return
        af_run(chat_id, {"tunnel_new": "dns-new", "tunnel_pause": "dns-pause", "tunnel_resume": "dns-resume",
                         "tunnel_delete": "dns-del"}[act], nm)
    elif act == "message_customer":
        send_via_customer_bot(int(a["tg_id"]), f"👤 پشتیبانی:\n\n{a['text']}")
        log_support_message(int(a["tg_id"]), "out", a["text"])
        send_message(chat_id, "✅ پیام برای مشتری فرستاده شد.")
    else:
        send_message(chat_id, f"کار ناشناخته: {act}")


AI_DIR = "/var/lib/hermesvpn-ai"
AI_MEM = AI_DIR + "/memory.json"
AI_HIST_FILE = AI_DIR + "/history.json"


def _mem_load():
    try:
        return json.load(open(AI_MEM))
    except Exception:
        return []


def _mem_save(m):
    os.makedirs(AI_DIR, exist_ok=True)
    tmp = AI_MEM + ".tmp"
    json.dump(m, open(tmp, "w"), ensure_ascii=False, indent=1)
    os.replace(tmp, AI_MEM)
    os.chmod(AI_MEM, 0o600)


def _ai_remember(text, kind="lesson"):
    m = _mem_load()
    nid = (max([x["id"] for x in m]) + 1) if m else 1
    m.append({"id": nid, "ts": datetime.now(IRAN_TZ).strftime("%Y-%m-%d"), "kind": kind, "text": str(text)[:500]})
    _mem_save(m[-120:])
    return f"به حافظه اضافه شد (#{nid})"


def _ai_forget(id):
    m = _mem_load()
    n = [x for x in m if x["id"] != int(id)]
    _mem_save(n)
    return "پاک شد" if len(n) < len(m) else "پیدا نشد"


def _ai_memories():
    return "\n".join(f"#{x['id']} [{x['kind']}] {x['ts']}: {x['text']}" for x in _mem_load()) or "(حافظه خالی است)"


def _sh(cmd, timeout=60):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return (r.stdout or "") + (r.stderr or "")


def _ai_server_status():
    out = []
    for svc in ("x-ui", "hermes-customer-bot", "hermes-bot", "hermesvpn-monitor.timer"):
        out.append(f"{svc}: {_sh(['systemctl', 'is-active', svc]).strip()}")
    out.append("load: " + open("/proc/loadavg").read().split()[0])
    out.append(_sh(["df", "-h", "/"]).splitlines()[-1])
    out.append(_sh(["free", "-m"]).splitlines()[1])
    for port, nm in ((443, "Main"), (8443, "Reality"), (2083, "XHTTP")):
        n = _sh(["bash", "-c", f"ss -Htn state established '( sport = :{port} )' | wc -l"]).strip()
        out.append(f"اتصال فعال {nm} ({port}): {n}")
    out.append(f"اکانت آنلاین: {len(get_online_emails())}")
    return "\n".join(out)


def _ai_bank_deposits(hours=24):
    t = _sh(["journalctl", "-u", "hermes-customer-bot", "--since", f"-{int(hours)}h", "--no-pager", "-o", "short-iso"], 90)
    lines = [l.split("python3", 1)[-1] for l in t.splitlines() if "[webhook]" in l and ("received" in l or "match" in l or "تکراری" in l)]
    return "\n".join(lines[-40:]) or "در این بازه هیچ پیامک بانکی به ربات نرسیده."


def _ai_orders_by_amount(amount_toman):
    a = int(amount_toman)
    c = sqlite3.connect(ORDERS_DB)
    rows = c.execute("""SELECT id, tg_username, tg_name, status, total_price, base_price, created_at FROM orders
                        WHERE total_price IN (?,?) OR base_price IN (?,?) OR total_price BETWEEN ? AND ?
                        ORDER BY id DESC LIMIT 15""", (a, a * 10, a, a * 10, a - 10, a + 10)).fetchall()
    c.close()
    return json.dumps([{"order": i, "customer": u or n, "status": st, "total": t, "base": b,
                        "created": datetime.fromtimestamp((cr or 0) / 1000, IRAN_TZ).strftime("%m/%d %H:%M")}
                       for i, u, n, st, t, b, cr in rows], ensure_ascii=False) or "[]"


def _ai_activity(name, hours=6):
    return _sh(["/usr/local/bin/hermesvpn-activity.py", str(name), str(int(hours))], 150)[:3500]


def _ai_expiring(days=3):
    now = int(time.time() * 1000)
    x = sqlite3.connect(DB)
    rows = x.execute("SELECT email, expiry_time, tg_id FROM clients WHERE enable=1 AND expiry_time>? AND expiry_time<? ORDER BY expiry_time",
                     (now, now + int(days) * 86400000)).fetchall()
    x.close()
    return json.dumps([{"account": e, "days_left": round((ex - now) / 86400000, 1), "tg_id": tg} for e, ex, tg in rows], ensure_ascii=False)


def _ai_ledger(term):
    return _sh(["sudo", "-u", "ubuntu", "ssh", "-o", "BatchMode=yes", "hetzner-log", "grep", str(term)], 40)[-3500:]


def _ai_recent_support():
    c = sqlite3.connect(ORDERS_DB)
    rows = c.execute("""SELECT tg_id, MAX(created_at) FROM support_messages WHERE direction IN ('in','out')
                        GROUP BY tg_id ORDER BY MAX(created_at) DESC LIMIT 15""").fetchall()
    out = []
    for tg, ts in rows:
        last = c.execute("SELECT direction, text FROM support_messages WHERE tg_id=? AND direction IN ('in','out') ORDER BY id DESC LIMIT 1", (tg,)).fetchone()
        nm = c.execute("SELECT COALESCE(NULLIF(tg_username,''), tg_name) FROM orders WHERE tg_id=? ORDER BY id DESC LIMIT 1", (tg,)).fetchone()
        out.append({"tg_id": tg, "name": nm[0] if nm else None, "when": datetime.fromtimestamp(ts, IRAN_TZ).strftime("%m/%d %H:%M"),
                    "last_from": "customer" if last and last[0] == "in" else "support", "last_text": (last[1] if last else "")[:160]})
    c.close()
    return json.dumps(out, ensure_ascii=False)


def _fn(name, desc, props=None, req=None):
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props or {}, "required": req or []}}}


AI_TOOLS = [
    _fn("client_status", "Full status of one VPN account (volume, used, time left, online, enabled, owner).", {"name": {"type": "string"}}, ["name"]),
    _fn("search_clients", "Find VPN accounts whose name contains text.", {"query": {"type": "string"}}),
    _fn("list_orders", "List orders. filter: open (awaiting payment / receipt to review), today, all.",
        {"filter": {"type": "string", "enum": ["open", "today", "all"]}, "limit": {"type": "integer"}}),
    _fn("get_order", "Full card of one order: price breakdown, receipt, customer steps.", {"order_id": {"type": "integer"}}, ["order_id"]),
    _fn("get_customer", "Customer panel: all accounts, orders, recent actions. who = telegram id, @username or an account name.",
        {"who": {"type": "string"}}, ["who"]),
    _fn("dashboard", "Today's sales, pending orders, accounts online, server health."),
    _fn("sales_report", "Sales report for a period.", {"period": {"type": "string", "enum": ["today", "week", "month", "all"]}}),
    _fn("support_history", "Recent conversation of a customer with the bot and support.", {"tg_id": {"type": "integer"}}, ["tg_id"]),
    _fn("propose_action", "Propose ONE change for the admin to confirm with a button (nothing runs without his tap). "
        "actions: create(name,gb,days), renew(name,gb,days), topup(name,gb,days add), block(name), unblock(name), delete(name), "
        "approve_order(order_id), reject_order(order_id), message_customer(tg_id,text), "
        "tunnel_new(name) / tunnel_pause(name) / tunnel_resume(name) / tunnel_delete(name) for DNS-tunnel accounts "
        "(name: lowercase a-z0-9, 3-16, starts with a letter — usually the customer's account name cleaned; if unknown, ASK which customer first). "
        "summary = one Persian line describing it.",
        {"action": {"type": "string", "enum": ["create", "renew", "topup", "block", "unblock", "delete", "approve_order",
                                                "reject_order", "message_customer", "tunnel_new", "tunnel_pause",
                                                "tunnel_resume", "tunnel_delete"]},
         "summary": {"type": "string"}, "name": {"type": "string"}, "gb": {"type": "number"}, "days": {"type": "number"},
         "order_id": {"type": "integer"}, "tg_id": {"type": "integer"}, "text": {"type": "string"}}, ["action", "summary"]),
]
AI_TOOLS += [
    _fn("remember", "Save a durable lesson/preference/fact the admin taught you (a correction, a rule, a fact about a customer). "
        "Write it as one clear Persian sentence. Use it whenever he corrects you or says 'از این به بعد'.",
        {"text": {"type": "string"}, "kind": {"type": "string", "enum": ["lesson", "preference", "customer", "fact"]}}, ["text"]),
    _fn("forget", "Delete a memory by id (when the admin says it is wrong/outdated).", {"id": {"type": "integer"}}, ["id"]),
    _fn("memories", "List everything in long-term memory."),
    _fn("server_status", "Live server health: services, load, disk, RAM, active connections per route, accounts online."),
    _fn("bank_deposits", "Bank SMS deposits the bot received (amount in RIAL, card, time) and which order each matched, last N hours.",
        {"hours": {"type": "integer"}}),
    _fn("orders_by_amount", "Find orders whose amount matches a deposit (toman; also checks the rial/toman x10 mistake).",
        {"amount_toman": {"type": "integer"}}, ["amount_toman"]),
    _fn("account_activity", "What an account did through the VPN in the last N hours (apps, sites, routes, operator).",
        {"name": {"type": "string"}, "hours": {"type": "integer"}}, ["name"]),
    _fn("expiring_accounts", "Active accounts expiring within N days.", {"days": {"type": "integer"}}),
    _fn("recent_support", "Latest customer support conversations and who spoke last (to find unanswered customers)."),
    _fn("ledger_search", "Search the shared operations ledger (history of every change made to the servers/bots).",
        {"term": {"type": "string"}}, ["term"]),
]

def _af_out(*args):
    r = subprocess.run(["sudo", "-u", "ubuntu", AF_ROUTES] + list(args), capture_output=True, text=True, timeout=90)
    return (r.stdout or r.stderr or "").strip()[:3000]


AI_TOOLS += [
    _fn("emergency_telegram_proxy", "Owner's emergency Telegram MTProto proxy link (section «🆘 مسیرهای اضطراری»)."),
    _fn("emergency_dns_mine", "Settings of the owner's own DNS-tunnel test account (HTTP Custom)."),
    _fn("emergency_dns_list", "List of customer DNS-tunnel accounts with status active/paused and live connections."),
]
def _af_ai(*args):
    out = _af_out(*args)
    if BALE_MODE and args[0] in ("mtproto", "dns"):
        bale_private_note("مسیر اضطراری: " + " ".join(args), out)
        return "نتیجه در دفترچه‌ی خصوصی (Google Docs) صاحب ثبت شد؛ در این چت (بله) لینک/تنظیمات را ننویس، فقط بگو در دفترچه ثبت شد."
    return out


AI_IMPL_EXTRA = {"emergency_telegram_proxy": lambda: _af_ai("mtproto"), "emergency_dns_mine": lambda: _af_ai("dns"),
                 "emergency_dns_list": lambda: _af_ai("dns-list", "--json")}

AI_IMPL = {"remember": _ai_remember, "forget": _ai_forget, "memories": _ai_memories, "server_status": _ai_server_status,
           "bank_deposits": _ai_bank_deposits, "orders_by_amount": _ai_orders_by_amount, "account_activity": _ai_activity,
           "expiring_accounts": _ai_expiring, "ledger_search": _ai_ledger, "recent_support": _ai_recent_support,
           **AI_IMPL_EXTRA,
           "client_status": _ai_client_status, "search_clients": _ai_search_clients, "list_orders": _ai_list_orders,
           "get_order": _ai_get_order, "get_customer": _ai_get_customer, "dashboard": lambda: _plain(dashboard_text()),
           "sales_report": _ai_sales, "support_history": _ai_support_history, "propose_action": _ai_propose}


def ai_system_prompt():
    try:
        know = open(AI_DIR + "/knowledge.md").read()
    except Exception:
        know = ""
    mem = _ai_memories()
    return (
        "تو مدیر عملیات هوشمند HermesVPN هستی: یک همکار باتجربه و دقیق برای صاحب کسب‌وکار (ادمین) در ربات مدیریت. "
        f"زمان فعلی: {AIM.jalali_today()}.\n\n"
        "## روش کار\n"
        "1) اول منظور واقعی ادمین را بفهم؛ «همون/اون/قبلی» را از گفتگو و حافظه حل کن. فقط اگر واقعاً مبهم بود یک سؤال کوتاه بپرس.\n"
        "2) مثل یک مدیر فکر کن: برای هر موضوع چند ابزار را پشت هم صدا بزن و داده‌ها را به هم وصل کن "
        "(مثلاً واریز ناشناس → bank_deposits + orders_by_amount + get_customer؛ «مشتری X مشکل دارد» → get_customer + client_status + account_activity + support_history).\n"
        "3) جواب: خلاصه‌ی نتیجه + علت + کاری که پیشنهاد می‌کنی. اعداد/مبلغ/تاریخ/وضعیت فقط از خروجی ابزار؛ هرگز حدس نزن؛ اگر نمی‌دانی بگو.\n"
        "4) هر تغییر فقط با propose_action (یکی‌یکی) و منتظر دکمه‌ی ادمین؛ هیچ‌وقت نگو انجام شد مگر خروجی ابزار بگوید.\n"
        "5) یادگیری: هر وقت ادمین اشتباهت را اصلاح کرد، قاعده‌ای گفت، ترجیحی داشت یا نکته‌ای درباره‌ی مشتری گفت، "
        "همان لحظه با remember یک جمله‌ی روشن ذخیره کن و در کارهای بعدی رعایتش کن. حافظه‌ی غلط را با forget پاک کن.\n"
        "6) پیام ادمین ممکن است از ویس آمده باشد (غلط تایپی/محاوره) — منظور را بفهم. برای «حساب تونل/تونل DNS/پراکسی تلگرام/مسیر اضطراری» "
        "از ابزارهای emergency_* و برای ساخت/توقف/حذف از propose_action با tunnel_* استفاده کن؛ اگر اسم مشتری را نگفت، کوتاه بپرس برای کدام مشتری.\n"
        "7) فارسی، مرتب و کوتاه؛ بدون تعارف و بدون لیست پیشنهادهای بی‌ربط در آخر. نکته‌ی مهمی که خودت دیدی (ریسک، مشتری معطل، واریز ناجور) را بگو.\n\n"
        "## دانش کسب‌وکار\n" + know[:7000] +
        "\n\n## حافظه‌ی بلندمدت (آموخته‌ها از ادمین — حتماً رعایت کن)\n" + mem[:6000]
    )


def ai_handle_voice(chat_id, text):
    AF_WAIT.pop(chat_id, None)
    try:
        reset_state(chat_id)
    except Exception:
        STATE.pop(chat_id, None)
    return ai_handle(chat_id, text)


def ai_handle(chat_id, text):
    if not (AIM and AIM.available()):
        return False
    def _run():
        try:
            api_call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
        except Exception:
            pass
        try:
            if chat_id not in AI_HISTORY:
                try:
                    AI_HISTORY[chat_id] = json.load(open(AI_HIST_FILE)).get(str(chat_id), [])
                except Exception:
                    AI_HISTORY[chat_id] = []
            reply, hist = AIM.run_agent(ai_system_prompt(), AI_HISTORY.get(chat_id, [])[-24:], text, AI_TOOLS, AI_IMPL,
                                        who="admin", max_steps=12, max_tokens=3500, primary=AIM.HAIKU)
            AI_HISTORY[chat_id] = hist
            try:
                os.makedirs(AI_DIR, exist_ok=True)
                json.dump({str(chat_id): hist[-30:]}, open(AI_HIST_FILE, "w"), ensure_ascii=False)
                os.chmod(AI_HIST_FILE, 0o600)
            except Exception:
                pass
            send_message(chat_id, "🤖 " + (reply or "انجام شد."))
        except Exception as e:
            send_message(chat_id, f"🤖 دستیار الان در دسترس نیست ({type(e).__name__}). از منو استفاده کن.")
    threading.Thread(target=_run, daemon=True).start()
    return True
# ======================= /دستیار =======================

def show_recent_support(chat_id):
    try:
        c = sqlite3.connect(ORDERS_DB)
        rows = c.execute("""SELECT tg_id, MAX(created_at), SUM(direction='in') FROM support_messages
                            WHERE direction IN ('in','out') GROUP BY tg_id ORDER BY MAX(created_at) DESC LIMIT 12""").fetchall()
        names = dict(c.execute("SELECT tg_id, COALESCE(NULLIF(tg_username,''), tg_name) FROM orders GROUP BY tg_id").fetchall())
        c.close()
    except Exception as e:
        send_message(chat_id, f"خطا: {e}"); return
    if not rows:
        send_message(chat_id, "هنوز گفتگویی ثبت نشده."); return
    kb = []
    for tg, ts, nin in rows:
        when = datetime.fromtimestamp(ts, IRAN_TZ).strftime("%m/%d %H:%M")
        kb.append([{"text": f"🙋 {(names.get(tg) or tg)} — {when}", "callback_data": f"suphist:{tg}:0"},
                   {"text": "↩️ پاسخ", "callback_data": f"supreply:{tg}"}])
    send_message(chat_id, "💬 گفتگوهای اخیر (تازه‌ترین بالا):", reply_markup=inline(kb))


def inline(rows):
    return {"inline_keyboard": rows}


# ---------- server maintenance from Telegram ----------
# Deliberately a fixed menu of named actions, never arbitrary shell input: if
# this Telegram account is ever compromised, the blast radius stays "restart a
# service", not "run anything as root on the VPN server".

MAINTENANCE_ACTIONS = {
    "sales": ("♻️ ری‌استارت ربات فروش", ["systemctl", "restart", "hermes-customer-bot"]),
    "admin": ("♻️ ری‌استارت ربات مدیریت", ["systemctl", "restart", "hermes-bot"]),
    "xui": ("♻️ ری‌استارت پنل x-ui", ["systemctl", "restart", "x-ui"]),
}


def maintenance_keyboard():
    return inline([
        [{"text": MAINTENANCE_ACTIONS["sales"][0], "callback_data": "maint:sales"}],
        [{"text": MAINTENANCE_ACTIONS["xui"][0], "callback_data": "maint:xui"}],
        [{"text": MAINTENANCE_ACTIONS["admin"][0], "callback_data": "maint:admin"}],
        [{"text": "🩺 بررسی یکپارچگی داده", "callback_data": "maint:health"}],
        [{"text": "📜 خطاهای اخیر", "callback_data": "maint:logs"}],
        [{"text": "💾 منابع سرور", "callback_data": "maint:res"}],
    ])


def run_fixed(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return f"خطا در اجرا: {e}"


def services_state():
    out = run_fixed(["systemctl", "is-active", "hermes-bot", "hermes-customer-bot", "x-ui"])
    names = ["ربات مدیریت", "ربات فروش", "پنل x-ui"]
    rows = []
    for nm, st in zip(names, out.split()):
        rows.append(("🟢 " if st == "active" else "🔴 ") + nm + ": " + st)
    return "\n".join(rows)


def do_maintenance(chat_id, action):
    if action == "health":
        send_message(chat_id, "در حال بررسی...")
        out = run_fixed(["python3", "/usr/local/bin/hermesvpn-health-check.py"], timeout=120)
        send_message(chat_id, "🩺 نتیجه‌ی بررسی:\n\n" + out.strip()[:3500],
                     reply_markup=main_menu())
        return
    if action == "logs":
        out = run_fixed(["journalctl", "-u", "hermes-bot", "-u", "hermes-customer-bot",
                         "--since", "2 hours ago", "--no-pager", "-p", "warning"], timeout=60)
        body = out.strip() or "هیچ خطایی در ۲ ساعت گذشته ثبت نشده ✅"
        send_message(chat_id, "📜 خطاهای اخیر:\n\n" + body[-3500:], reply_markup=main_menu())
        return
    if action == "res":
        disk = run_fixed(["df", "-h", "/"]).strip().splitlines()
        mem = run_fixed(["free", "-h"]).strip().splitlines()
        up = run_fixed(["uptime", "-p"]).strip()
        block = "\n".join(disk[:2] + [""] + mem[:2])
        send_message(chat_id,
                     "💾 منابع سرور:\n\n<pre>" + esc(block) + "</pre>\n"
                     + "روشن بودن: " + esc(up) + "\n\n" + services_state(),
                     parse_mode="HTML", reply_markup=main_menu())
        return

    if action not in MAINTENANCE_ACTIONS:
        send_message(chat_id, "این گزینه رو نمی‌شناسم.", reply_markup=main_menu())
        return

    label, cmd = MAINTENANCE_ACTIONS[action]
    if action == "admin":
        # Restarting ourselves kills this process, so the confirmation has to go
        # out first and the restart has to outlive us.
        send_message(chat_id, "ربات مدیریت داره ری‌استارت می‌شه، چند ثانیه دیگه دوباره جواب می‌ده.",
                     reply_markup=main_menu())
        subprocess.Popen(["sh", "-c", "sleep 2; systemctl restart hermes-bot"],
                         start_new_session=True)
        return

    send_message(chat_id, "در حال انجام: " + label)
    run_fixed(cmd, timeout=90)
    time.sleep(4)
    send_message(chat_id, "انجام شد.\n\n" + services_state(), reply_markup=main_menu())


def chunk(items, n):
    return [items[i:i + n] for i in range(0, len(items), n)]


MULTIDEL_PER_PAGE = 8


def multidel_text(data):
    sel = data.get("selected", [])
    total = len(data.get("emails", []))
    head = f"☑️ حذف انتخابی — {total} کلاینت\n\nروی هرکدوم بزن تا تیک بخوره."
    if sel:
        preview = "، ".join(sel[:12]) + ("..." if len(sel) > 12 else "")
        head += f"\n\nانتخاب‌شده ({len(sel)}): {preview}"
    return head


def multidel_keyboard(data):
    """لیست تیک‌خور با صفحه‌بندی. انتخاب‌ها در STATE می‌مانند و با عوض‌کردن
    صفحه از بین نمی‌روند، چون معمولاً باید از چند صفحه انتخاب شود."""
    emails = data.get("emails", [])
    selected = set(data.get("selected", []))
    page = data.get("page", 0)
    dots = client_dot_map(emails)
    start = page * MULTIDEL_PER_PAGE
    rows = []
    for e in emails[start:start + MULTIDEL_PER_PAGE]:
        mark = "✅" if e in selected else "⬜️"
        rows.append([{"text": f"{mark} {dots.get(e, '🔴')} {e}",
                      "callback_data": f"multidel:tog:{e}"}])
    nav = []
    if page > 0:
        nav.append({"text": "◀️ قبلی", "callback_data": f"multidel:page:{page - 1}"})
    if start + MULTIDEL_PER_PAGE < len(emails):
        nav.append({"text": "▶️ بعدی", "callback_data": f"multidel:page:{page + 1}"})
    if nav:
        rows.append(nav)
    if selected:
        rows.append([{"text": f"🗑 حذف {len(selected)} انتخاب‌شده",
                      "callback_data": "multidel:go"}])
    rows.append([{"text": "❌ لغو", "callback_data": "multidel:cancel"}])
    return inline(rows)


GB_CHOICES = [5, 10, 20, 30, 50, 100]
DAY_CHOICES = [7, 30, 60, 90, 180, 365]


def gb_picker(prefix):
    buttons = [{"text": f"{g} گیگ", "callback_data": f"{prefix}:gb:{g}"} for g in GB_CHOICES]
    rows = chunk(buttons, 3)
    rows.append([{"text": "🔢 مقدار دیگر", "callback_data": f"{prefix}:gbcustom"}])
    if prefix == "create":
        # میان‌بر یک‌لمسی: حجم و زمان با هم تعیین می‌شوند و مرحله‌ی انتخاب روز
        # رد می‌شود، چون این دو ترکیب پرتکرارترین‌اند.
        rows.append([{"text": "♾ نامحدود یک ماهه", "callback_data": f"{prefix}:unlim:30"}])
        rows.append([{"text": "♾ نامحدود", "callback_data": f"{prefix}:unlim:0"}])
    if prefix == "topup":
        rows.append([{"text": "⏭ رد کن (فقط زمان)", "callback_data": f"{prefix}:gbskip"}])
        # همان دو میان‌بر ساخت، برای کلاینت موجود — این‌ها «اضافه» نمی‌کنند
        # بلکه پلن را روی نامحدود می‌گذارند، پس مسیرشان جداست.
        rows.append([{"text": "♾ نامحدود یک ماهه", "callback_data": f"{prefix}:unlim:30"}])
        rows.append([{"text": "♾ نامحدود", "callback_data": f"{prefix}:unlim:0"}])
    return inline(rows)


def days_picker(prefix):
    buttons = [{"text": f"{d} روز", "callback_data": f"{prefix}:days:{d}"} for d in DAY_CHOICES]
    rows = chunk(buttons, 3)
    rows.append([{"text": "🔢 مقدار دیگر", "callback_data": f"{prefix}:dayscustom"}])
    if prefix == "create":
        rows.append([{"text": "♾ بدون انقضا (همیشگی)", "callback_data": f"{prefix}:days:0"}])
    if prefix == "topup":
        rows.append([{"text": "⏭ رد کن (فقط حجم)", "callback_data": f"{prefix}:daysskip"}])
    return inline(rows)


def client_picker(prefix, emails):
    if not emails:
        return None
    dots = client_dot_map(emails)
    buttons = [{"text": f"{dots.get(e, '🔴')} {e}", "callback_data": f"{prefix}:pick:{e}"} for e in emails]
    return inline(chunk(buttons, 2))


def yesno(prefix, confirm="✅ تأیید", cancel="❌ لغو"):
    """برچسب پیش‌فرض عمداً «تأیید/لغو» است نه «بله/نه» — موقع اسکرول سریع،
    «بله» کنار یک سؤالِ خوانده‌نشده خطرناک است. کارهای برگشت‌ناپذیر برچسب
    صریح خودشان را می‌دهند («بله، پاک کن»)."""
    return inline([[{"text": confirm, "callback_data": f"{prefix}:yes"},
                     {"text": cancel, "callback_data": f"{prefix}:no"}]])


def renew_confirm_text(name, gb=None, days=None):
    """تمدید مصرف را صفر و تاریخ را جلو می‌برد — برگشت‌پذیر نیست.
    مقدارهای واقعی نشان داده می‌شوند تا تأیید، تأییدِ همان چیزی باشد که اجرا می‌شود."""
    vol = f"{gb:g}GB" if gb else "همون حجم قبلی"
    dur = f"{int(days)} روز از همین الان" if days else "۳۰ روز از همین الان"
    return (f"تمدید «{name}»؟\n\n"
            f"• {vol}\n"
            f"• {dur}\n"
            "• مصرف صفر می‌شه\n\n"
            "این کار برگشت‌پذیر نیست.")


def bulk_mode_picker():
    return inline([
        [{"text": "🔢 بازه‌ی دقیق (مثلاً 1 تا 50)", "callback_data": "bulkcreate:mode:range"}],
        [{"text": "➕ تعداد (ادامه‌ی خودکار شماره‌گذاری)", "callback_data": "bulkcreate:mode:count"}],
    ])


# ---------- per-chat conversation state ----------

STATE = {}  # chat_id -> {"flow": str, "step": str, "data": {...}}


def reset_state(chat_id):
    STATE.pop(chat_id, None)


# ---------- natural-language parsing (Persian + Finglish) — fallback path ----------

GB_WORDS = r"(?:گیگ(?:ابایت)?|gig(?:s|abyte)?|gb)"
DAY_WORDS = r"(?:روز|day|ruz|rooz|rouz|roz)"
MONTH_WORDS = r"(?:ماه|month|mah|maah)"
NAME_HINT = r"(?:به\s*)?\b(?:اسم|نام|esme?|name|بنام)\b\s+([A-Za-z؀-ۿ]+)"
ONE_WORDS = r"(?:یک|یه|yek|yeki)"

STATUS_WORDS = r"(?:چقدر|چند|مونده|مانده|وضعیت|status|monde|mande|cheghadr|chand)"
RENEW_WORDS = r"(?:تمدید|تجدید|renew|tamdid)"
ADD_WORDS = r"(?:اضافه|ezafe|add)"
ROTATE_WORDS = r"(?:فیلتر|filter)"
ROTATE_ACTION_WORDS = r"(?:شد|شده|درستش\s*کن|درست\s*کن|fix|dorobare|دوباره|reconnect|unblock)"
DELETE_WORDS = r"(?:پاک\s*کن|پاکش\s*کن|حذف\s*کن|حذفش\s*کن|حذف|delete|remove|pak\s*kon|pakesh\s*kon|hazf\s*kon|hazfesh\s*kon)"

# bulk: "mersad34 ta mersad54" (explicit range) or "mersad 20 ta account" (count, auto-continues numbering)
BULK_RANGE_RE = re.compile(r"([A-Za-z؀-ۿ]+)\s*(\d+)\s*(?:تا|ta)\s*(?:[A-Za-z؀-ۿ]+)?\s*(\d+)", re.IGNORECASE)
BULK_COUNT_RE = re.compile(r"([A-Za-z؀-ۿ]+)\s+(\d+)\s*(?:تا)?\s*(?:اکانت|acc(?:ount)?|کلاینت|client|حساب|نفر|nafar)\b", re.IGNORECASE)

STOPWORDS = {
    "برای", "بساز", "بسازش", "درست", "کن", "ایجاد", "روز", "گیگ", "ماه", "با", "و", "رو", "را",
    "besaz", "dorost", "kon", "dorostesh", "baraye", "vase", "ba", "va", "ro", "ra",
    "gig", "gb", "rooz", "ruz", "rouz", "roz", "mah", "maah", "month", "day", "yek", "yeki", "sakhte", "misazi",
    "berferest", "inja", "ye",
    "یک", "یه", "این", "اون", "لطفا", "لطفاً", "جدید", "new",
    "کلاینت", "client", "یوزر", "user", "اکانت", "account", "اسمش", "اسم", "نامش", "نام", "esm", "esmesh",
    "تمدید", "تجدید", "renew", "tamdid", "اضافه", "ezafe", "add", "چقدر", "چند", "مونده", "مانده",
    "وضعیت", "status", "monde", "mande", "cheghadr", "chand", "فیلتر", "filter", "شد", "شده",
    "دوباره", "fix", "reconnect", "unblock", "به", "be",
    "پاک", "پاکش", "حذف", "حذفش", "delete", "remove", "pak", "pakesh", "hazf", "hazfesh",
}


def to_number(word):
    persian_digits = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
    word = word.translate(persian_digits)
    try:
        return float(word)
    except ValueError:
        return None


def extract_amounts(text):
    text = re.sub(ONE_WORDS + r"\s*(?=" + GB_WORDS + "|" + DAY_WORDS + "|" + MONTH_WORDS + ")", "1 ", text, flags=re.IGNORECASE)
    gb = None
    days = None
    m = re.search(r"(\d+(?:\.\d+)?)\s*" + GB_WORDS, text, re.IGNORECASE)
    if m:
        gb = to_number(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*" + MONTH_WORDS, text, re.IGNORECASE)
    if m:
        days = to_number(m.group(1)) * 30
    else:
        m = re.search(r"(\d+(?:\.\d+)?)\s*" + DAY_WORDS, text, re.IGNORECASE)
        if m:
            days = to_number(m.group(1))
    return text, gb, days


def extract_name(text):
    m = re.search(NAME_HINT, text, re.IGNORECASE)
    if m:
        return m.group(1)
    cleaned = re.sub(r"\d+(?:\.\d+)?\s*(?:" + GB_WORDS + "|" + DAY_WORDS + "|" + MONTH_WORDS + ")", " ", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\d+", " ", cleaned)
    tokens = re.findall(r"[A-Za-z؀-ۿ_]+", cleaned)
    for tok in tokens:
        if tok.lower() in STOPWORDS:
            continue
        return tok
    return None


def parse_intent(text):
    if re.search(ROTATE_WORDS, text, re.IGNORECASE) and re.search(ROTATE_ACTION_WORDS, text, re.IGNORECASE):
        return "rotate", None, None, None

    text2, gb, days = extract_amounts(text)
    name = extract_name(text2)

    if re.search(DELETE_WORDS, text, re.IGNORECASE):
        return "delete", name, None, None
    if re.search(RENEW_WORDS, text, re.IGNORECASE):
        return "renew", name, gb, days
    if re.search(ADD_WORDS, text, re.IGNORECASE) and (gb or days):
        return "topup", name, gb, days
    if gb and days:
        return "create", name, gb, days
    if name and re.search(STATUS_WORDS, text, re.IGNORECASE):
        return "status", name, None, None
    if name and not gb and not days:
        return "status", name, None, None
    return "unknown", name, gb, days


# ---------- db helpers ----------

def get_client(cur, name):
    cur.execute("SELECT id, uuid, sub_id, total_gb, expiry_time FROM clients WHERE email=?", (name,))
    return cur.fetchone()


def list_client_emails():
    c = sqlite3.connect(DB)
    cur = c.cursor()
    cur.execute("SELECT email FROM clients ORDER BY id")
    rows = [r[0] for r in cur.fetchall()]
    c.close()
    return rows


PAGE_SIZE = 10


def clients_page(page=0, page_size=PAGE_SIZE):
    c = sqlite3.connect(DB)
    cur = c.cursor()
    cur.execute("SELECT email FROM clients ORDER BY email LIMIT ? OFFSET ?", (page_size + 1, page * page_size))
    rows = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT COUNT(*) FROM clients")
    total = cur.fetchone()[0]
    c.close()
    has_more = len(rows) > page_size
    return rows[:page_size], has_more, total


SEARCH_LIMIT = 30


def search_clients(query, limit=SEARCH_LIMIT):
    """Substring, case-insensitive search — scales to large client counts
    instead of dumping every client as a button."""
    c = sqlite3.connect(DB)
    cur = c.cursor()
    cur.execute("SELECT email FROM clients WHERE email LIKE ? ORDER BY email LIMIT ?",
                (f"%{query}%", limit + 1))
    rows = [r[0] for r in cur.fetchall()]
    c.close()
    truncated = len(rows) > limit
    return rows[:limit], truncated


XRAY_BIN = "/usr/local/x-ui/bin/xray-linux-arm64"
XRAY_API_ADDR = "127.0.0.1:62789"


_ONLINE_CACHE = {"emails": set(), "ts": 0.0}
ONLINE_CACHE_TTL = 5  # seconds


def get_online_emails():
    """Live connection state straight from Xray's own gRPC stats API.
    `client_traffics.last_online` in the DB is NOT a usable online/offline
    signal — checked directly and found it lagging by ~40 hours behind real
    connections (5 clients were actually connected live per Xray's API while
    the DB column showed nothing recent for any client). Xray itself always
    knows who's connected right now via `xray api statsgetallonlineusers`.

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
        data = json.loads(out)
        emails = set()
        for u in data.get("users", []):
            parts = u.split(">>>")  # "user>>>{email}>>>online"
            if len(parts) >= 2:
                emails.add(parts[1])
        _ONLINE_CACHE["emails"] = emails
        _ONLINE_CACHE["ts"] = now
        return emails
    except Exception:
        _ONLINE_CACHE["ts"] = now
        return _ONLINE_CACHE["emails"]


def online_status_map(emails):
    """Batch online/offline lookup for a list of emails — used to put a
    🔵/🔴 right on list rows/buttons without opening each client individually."""
    if not emails:
        return {}
    online = get_online_emails()
    return {e: e in online for e in emails}


def client_dot_map(emails):
    """چراغ وضعیت: 🔒 مسدود، 🔵 آنلاین، 🔴 آفلاین.

    مسدودی بر آنلاین/آفلاین مقدم است، چون کلاینت مسدود اصلاً نمی‌تواند وصل
    شود. بدون این، یک اکانت مسدود در لیست دقیقاً شبیه یک اکانت سالمِ آفلاین
    دیده می‌شد — ربات فروش این را به مشتری نشان می‌داد ولی ادمین نمی‌دید."""
    if not emails:
        return {}
    online = get_online_emails()
    enabled = {}
    try:
        c = sqlite3.connect(DB)
        enabled = {r[0]: r[1] for r in c.execute("SELECT email, enable FROM clients")}
        c.close()
    except Exception as e:
        print("client_dot_map failed:", e)
    return {e: ("🔒" if enabled.get(e, 1) == 0
                else ("🔵" if e in online else "🔴")) for e in emails}


def all_labels():
    """اسم دلخواهی که مشتری/فروشنده روی اکانت‌ها گذاشته (دیتابیس ربات فروش)."""
    try:
        c = sqlite3.connect(ORDERS_DB)
        d = {r[0]: r[1] for r in c.execute("SELECT email, label FROM client_labels")}
        c.close()
        return d
    except Exception as e:
        print("all_labels failed:", e)
        return {}


# ---------- financial reports (reads the sales bot's orders.db) ----------

PERIOD_LABELS = {"today": "امروز", "week": "این هفته", "month": "این ماه", "all": "کل (از ابتدا)"}


def epoch_ms(dt):
    return int(dt.timestamp() * 1000)


def period_bounds(period):
    now = datetime.now(IRAN_TZ)
    if period == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        days_since_sat = (now.weekday() - 5) % 7  # Iranian week starts Saturday
        start = (now - timedelta(days=days_since_sat)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:  # all
        start = datetime.fromtimestamp(0, IRAN_TZ)
    return epoch_ms(start), epoch_ms(now)


def to_jalali(dt):
    """dt: an IRAN_TZ-local datetime -> Jalali 'YYYY/MM/DD HH:MM' string."""
    j = jdatetime.datetime.fromgregorian(datetime=dt.replace(tzinfo=None))
    return j.strftime("%Y/%m/%d %H:%M")


def to_jalali_date(dt):
    j = jdatetime.date.fromgregorian(date=dt.date())
    return j.strftime("%Y/%m/%d")


def parse_date_iran_midnight(text):
    """Accepts a Jalali (Shamsi) date, e.g. 1405-06-15 or 1405/06/15."""
    text = text.strip()
    parts = re.split(r"[/-]", text)
    if len(parts) != 3:
        return None
    try:
        jy, jm, jd = (int(p) for p in parts)
        g = jdatetime.date(jy, jm, jd).togregorian()
        return datetime(g.year, g.month, g.day, tzinfo=IRAN_TZ)
    except ValueError:
        return None


TRANSACTION_FIELDS = "decided_at, tg_username, tg_name, plan_key, quantity, total_price, target_email, receipt_tracking, receipt_date, receipt_name, receipt_amount"
ITEMIZED_LIMIT = 50


def sales_between(start_ms, end_ms):
    c = sqlite3.connect(ORDERS_DB)
    cur = c.cursor()
    cur.execute("SELECT COUNT(*), COALESCE(SUM(total_price),0) FROM orders WHERE status='approved' AND decided_at>=? AND decided_at<?",
                (start_ms, end_ms))
    count, total = cur.fetchone()
    c.close()
    return count, total


def transactions_between(start_ms, end_ms, limit=ITEMIZED_LIMIT):
    c = sqlite3.connect(ORDERS_DB)
    cur = c.cursor()
    cur.execute(f"SELECT {TRANSACTION_FIELDS} FROM orders WHERE status='approved' AND decided_at>=? AND decided_at<? ORDER BY decided_at DESC LIMIT ?",
                (start_ms, end_ms, limit + 1))
    rows = cur.fetchall()
    c.close()
    return rows[:limit], len(rows) > limit


DIVIDER = "――――――――――――――――"


def format_transaction_oneline(row):
    """Two-line block per sale so it can be scanned at a glance: who/what on
    line 1, amount/time (the two numbers people actually check) on line 2,
    with the amount bolded since it's usually what a quick glance is for."""
    decided_at, tg_username, tg_name, plan_key, quantity, total_price, target_email = row[:7]
    when = to_jalali(datetime.fromtimestamp((decided_at or 0) / 1000, IRAN_TZ))
    who = esc(f"@{tg_username}" if tg_username else (tg_name or "?"))
    qty_note = f" ×{quantity}" if quantity and quantity > 1 else ""
    kind = esc(f"تمدید «{target_email}»" if target_email else (plan_key or "?"))
    return (f"👤 <b>{who}</b> — {kind}{qty_note}\n"
            f"    💰 <b>{(total_price or 0):,}</b> تومان   🕐 {when}")


def itemized_block(start_ms, end_ms):
    rows, truncated = transactions_between(start_ms, end_ms)
    if not rows:
        return ""
    lines = [format_transaction_oneline(r) for r in rows]
    block = "\n\n👥 <b>ریز فروش</b>\n" + DIVIDER + "\n" + "\n\n".join(lines)
    if truncated:
        block += f"\n\n<i>(فقط {ITEMIZED_LIMIT} تای اول — برای بقیه از «🔎 جستجوی تراکنش» یا بازه‌ی کوچیک‌تر استفاده کن)</i>"
    return block


def format_sales_report(period):
    start_ms, end_ms = period_bounds(period)
    count, total = sales_between(start_ms, end_ms)
    header = (f"📊 <b>فروش {PERIOD_LABELS[period]}</b>\n{DIVIDER}\n"
              f"🧾 تعداد: <b>{count}</b> تراکنش   💰 جمع: <b>{total:,}</b> تومان")
    return header + itemized_block(start_ms, end_ms)


def format_range_report(start_dt, end_dt):
    start_ms = epoch_ms(start_dt)
    end_ms = epoch_ms(end_dt + timedelta(days=1))  # end date inclusive
    count, total = sales_between(start_ms, end_ms)
    header = (f"📊 <b>فروش از {to_jalali_date(start_dt)} تا {to_jalali_date(end_dt)}</b>\n{DIVIDER}\n"
              f"🧾 تعداد: <b>{count}</b> تراکنش   💰 جمع: <b>{total:,}</b> تومان")
    return header + itemized_block(start_ms, end_ms)


def recent_transactions(limit=15):
    c = sqlite3.connect(ORDERS_DB)
    cur = c.cursor()
    cur.execute(f"SELECT {TRANSACTION_FIELDS} FROM orders WHERE status='approved' ORDER BY decided_at DESC LIMIT ?", (limit,))
    rows = cur.fetchall()
    c.close()
    return rows


def search_transactions(query, limit=15):
    """Dispute-resolution lookup: matches tracking number, receipt name, buyer
    username/name, or amount, across all approved sales — so a claim like
    'X paid Y at time Z with tracking code W' can be verified or refuted."""
    c = sqlite3.connect(ORDERS_DB)
    cur = c.cursor()
    like = f"%{query}%"
    cur.execute(f"""SELECT {TRANSACTION_FIELDS} FROM orders WHERE status='approved' AND (
                    receipt_tracking LIKE ? OR receipt_name LIKE ? OR tg_username LIKE ? OR tg_name LIKE ?
                    OR CAST(total_price AS TEXT) LIKE ? OR CAST(receipt_amount AS TEXT) LIKE ?
                 ) ORDER BY decided_at DESC LIMIT ?""",
                (like, like, like, like, like, like, limit))
    rows = cur.fetchall()
    c.close()
    return rows


def format_transaction(row):
    decided_at, tg_username, tg_name, plan_key, quantity, total_price, target_email, tracking, receipt_date, receipt_name, receipt_amount = row
    when = to_jalali(datetime.fromtimestamp((decided_at or 0) / 1000, IRAN_TZ))
    who = esc(f"@{tg_username}" if tg_username else (tg_name or "?"))
    qty_note = f" ×{quantity}" if quantity and quantity > 1 else ""
    kind = esc(f"تمدید «{target_email}»" if target_email else (plan_key or "?"))
    lines = [f"👤 <b>{who}</b> — {kind}{qty_note}",
             f"    💰 <b>{(total_price or 0):,}</b> تومان   🕐 {when}"]
    detail = []
    if tracking:
        detail.append(f"کد پیگیری: <code>{esc(tracking)}</code>")
    if receipt_date:
        detail.append(f"تاریخ رسید: {esc(receipt_date)}")
    if receipt_name:
        detail.append(f"نام رسید: {esc(receipt_name)}")
    if receipt_amount and receipt_amount != total_price:
        detail.append(f"⚠️ مبلغ رسید: <b>{receipt_amount:,}</b>")
    if detail:
        lines.append("    " + " | ".join(detail))
    return "\n".join(lines)


EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U00002190-\U000021FF\U00002B00-\U00002BFF]+")


def rtl(text):
    return get_display(arabic_reshaper.reshape(text))


PDF_BRAND_COLOR = (30, 60, 120)
PDF_LIGHT_COLOR = (235, 240, 250)


def transaction_row_fields(row):
    """Plain (no HTML) structured fields for a PDF table row — kept separate
    from format_transaction_oneline, which returns Telegram HTML markup that
    a PDF renderer would print as literal text, not formatting."""
    decided_at, tg_username, tg_name, plan_key, quantity, total_price, target_email = row[:7]
    when = to_jalali(datetime.fromtimestamp((decided_at or 0) / 1000, IRAN_TZ))
    who = f"@{tg_username}" if tg_username else (tg_name or "?")
    qty_note = f" ×{quantity}" if quantity and quantity > 1 else ""
    kind = f"تمدید «{target_email}»" if target_email else (plan_key or "?")
    return when, f"{(total_price or 0):,}", f"{kind}{qty_note}", who


def render_report_pdf(title, summary, table_rows, out_path, truncated=False, total=None):
    """Renders a Persian financial report as a styled PDF: a branded header
    band, a summary stat box, and a bordered/colored table of sales — using
    Vazirmatn (a proper, fully-glyph-complete Persian font; the OS-bundled
    KACST font silently drops letters like 'ی' in some positions, verified
    before switching) with arabic_reshaper+python-bidi for correct letter
    joining and right-to-left order."""
    pdf = FPDF()
    pdf.add_page()
    pdf.add_font("Vazir", "", VAZIR_REGULAR)
    pdf.add_font("Vazir", "B", VAZIR_BOLD)

    pdf.set_fill_color(*PDF_BRAND_COLOR)
    pdf.rect(0, 0, 210, 28, style="F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_xy(10, 6)
    pdf.set_font("Vazir", "B", 18)
    pdf.cell(190, 10, text=rtl(f"HermesVPN — {EMOJI_RE.sub('', title).strip()}"), align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Vazir", "", 10)
    pdf.set_xy(10, 17)
    pdf.cell(190, 8, text=rtl(f"تاریخ تولید گزارش: {to_jalali(datetime.now(IRAN_TZ))}"), align="R")

    pdf.set_text_color(0, 0, 0)
    pdf.set_y(35)
    pdf.set_fill_color(*PDF_LIGHT_COLOR)
    pdf.set_draw_color(*PDF_BRAND_COLOR)
    pdf.rect(10, pdf.get_y(), 190, 16, style="DF")
    pdf.set_font("Vazir", "B", 13)
    pdf.set_xy(10, pdf.get_y() + 4)
    pdf.cell(190, 8, text=rtl(EMOJI_RE.sub("", summary).strip()), align="C")
    pdf.ln(22)

    if table_rows:
        pdf.set_font("Vazir", "", 11)
        headings_style = FontFace(emphasis="BOLD", color=255, fill_color=PDF_BRAND_COLOR)
        with pdf.table(
            col_widths=(28, 32, 45, 55, 20),
            text_align=("C", "C", "R", "R", "C"),
            headings_style=headings_style,
            cell_fill_color=PDF_LIGHT_COLOR,
            cell_fill_mode=TableCellFillMode.ROWS,
            borders_layout="ALL",
            padding=2,
        ) as table:
            header = table.row()
            for h in [rtl("تاریخ"), rtl("مبلغ"), rtl("پلن/بسته"), rtl("خریدار"), "#"]:
                header.cell(h)
            for i, (when, amount, plan, buyer) in enumerate(table_rows, 1):
                row = table.row()
                row.cell(when)
                row.cell(amount)
                row.cell(rtl(plan))
                row.cell(rtl(buyer))
                row.cell(str(i))
        if truncated:
            pdf.ln(4)
            pdf.set_font("Vazir", "", 9)
            pdf.cell(0, 6, text=rtl(f"(فقط {ITEMIZED_LIMIT} تای اول نمایش داده شده)"), align="R")
    else:
        pdf.set_font("Vazir", "", 12)
        pdf.cell(0, 10, text=rtl("تراکنشی برای نمایش نیست."), align="C")

    if total is not None:
        pdf.ln(6)
        pdf.set_fill_color(*PDF_BRAND_COLOR)
        pdf.rect(10, pdf.get_y(), 190, 14, style="F")
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Vazir", "B", 13)
        pdf.set_xy(10, pdf.get_y() + 3)
        report_date = to_jalali_date(datetime.now(IRAN_TZ))
        pdf.cell(190, 8, text=rtl(f"جمع کل فروش: {total:,} تومان — تاریخ گزارش: {report_date}"), align="C")
        pdf.set_text_color(0, 0, 0)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    pdf.output(out_path)
    return out_path


def period_report_data(period):
    start_ms, end_ms = period_bounds(period)
    count, total = sales_between(start_ms, end_ms)
    title = f"گزارش فروش {PERIOD_LABELS[period]}"
    summary = f"{count} تراکنش — جمعاً {total:,} تومان"
    rows, truncated = transactions_between(start_ms, end_ms)
    return title, summary, [transaction_row_fields(r) for r in rows], truncated, total


def range_report_data(start_dt, end_dt):
    start_ms = epoch_ms(start_dt)
    end_ms = epoch_ms(end_dt + timedelta(days=1))
    count, total = sales_between(start_ms, end_ms)
    title = f"گزارش فروش از {to_jalali_date(start_dt)} تا {to_jalali_date(end_dt)}"
    summary = f"{count} تراکنش — جمعاً {total:,} تومان"
    rows, truncated = transactions_between(start_ms, end_ms)
    return title, summary, [transaction_row_fields(r) for r in rows], truncated, total


def recent_report_data(limit=15):
    rows = recent_transactions(limit)
    title = "آخرین تراکنش‌ها"
    total = sum((r[5] or 0) for r in rows)
    summary = f"{len(rows)} تراکنش — جمعاً {total:,} تومان"
    return title, summary, [transaction_row_fields(r) for r in rows], False, total


# ---------- payment auto-approve toggle (shared setting with the sales bot) ----------
# Same `settings` table/key the sales bot's own auto_approve_enabled() reads —
# a single source of truth, so toggling from either bot has the same real
# effect and they can never disagree with each other.

def auto_approve_enabled():
    c = sqlite3.connect(ORDERS_DB)
    cur = c.cursor()
    cur.execute("SELECT value FROM settings WHERE key='auto_approve'")
    row = cur.fetchone()
    c.close()
    return (row[0] if row else "1") == "1"


def set_auto_approve(enabled):
    c = sqlite3.connect(ORDERS_DB)
    c.execute("INSERT INTO settings (key, value) VALUES ('auto_approve', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              ("1" if enabled else "0",))
    c.commit()
    c.close()


def auto_toggle_keyboard():
    on = auto_approve_enabled()
    return inline([[
        {"text": ("✅ " if on else "") + "خودکار", "callback_data": "autoset:1"},
        {"text": ("✅ " if not on else "") + "دستی", "callback_data": "autoset:0"},
    ]])


def auto_settings_text():
    state = "خودکار" if auto_approve_enabled() else "دستی"
    return (f"حالت فعلی تأیید پرداخت: {state}\n\n"
            f"خودکار: واریز شناسایی‌شده (چه از پیامک بانک، چه از رسیدی که OCR درست خونده) بلافاصله خودش تأیید و فعال می‌شه — فقط یه اطلاع‌رسانی برات میاد.\n"
            f"دستی: هر رسیدی که برسه، فقط بهت خبر می‌ده و منتظر تأیید دستی خودت می‌مونه (دقیقاً مثل قبل).")


def pdf_buttons(kind):
    return [{"text": "🖨 چاپ لیست", "callback_data": f"finpdf:{kind}"},
            {"text": "📄 ذخیره PDF", "callback_data": f"finpdf:{kind}"},
            {"text": "📥 اکسل حسابداری", "callback_data": f"fincsv:{kind}"}]


ACCOUNTING_HEADERS = [
    "شماره سفارش", "تاریخ و ساعت (شمسی)", "خریدار", "شناسه اکانت",
    "پلن", "تعداد", "مبلغ پرداختی (تومان)", "قیمت پایه (تومان)",
    "کد پیگیری/کارت", "تاریخ بانکی", "نام روی رسید", "مبلغ خوانده‌شده از رسید",
]


def accounting_rows(start_ms, end_ms):
    c = sqlite3.connect(ORDERS_DB)
    cur = c.cursor()
    cur.execute("""SELECT id, decided_at, tg_username, tg_name, client_email, plan_key, quantity,
                          total_price, base_price, receipt_tracking, receipt_date, receipt_name, receipt_amount
                   FROM orders WHERE status='approved' AND decided_at>=? AND decided_at<?
                   ORDER BY decided_at""", (start_ms, end_ms))
    rows = cur.fetchall()
    c.close()
    out = []
    for (oid, decided_at, uname, name, email, plan_key, qty, total, base,
         tracking, rdate, rname, ramount) in rows:
        when = to_jalali(datetime.fromtimestamp((decided_at or 0) / 1000, IRAN_TZ))
        who = f"@{uname}" if uname else (name or "")
        out.append([oid, when, who, email or "", plan_key or "", qty or 1,
                    total or 0, base or "", tracking or "", rdate or "", rname or "",
                    ramount if ramount is not None else ""])
    return out


def build_accounting_csv(rows, out_path, title):
    """Excel-ready CSV: UTF-8 **with BOM**, because Excel otherwise renders
    Persian text as mojibake, which is exactly the file an accountant would
    bounce back."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([title])
        w.writerow([])
        w.writerow(ACCOUNTING_HEADERS)
        total_sum = 0
        for r in rows:
            w.writerow(r)
            total_sum += r[6] or 0
        w.writerow([])
        w.writerow(["", "", "", "", "", "جمع کل:", total_sum])
    return out_path


def financial_menu_keyboard():
    return inline([
        [{"text": "📅 امروز", "callback_data": "fin:today"}, {"text": "📅 این هفته", "callback_data": "fin:week"}],
        [{"text": "📅 این ماه", "callback_data": "fin:month"}, {"text": "🗂 کل (از ابتدا)", "callback_data": "fin:all"}],
        [{"text": "🧾 آخرین تراکنش‌ها", "callback_data": "fin:recent"}],
        [{"text": "🗓 بازه‌ی دلخواه", "callback_data": "fin:range"}],
        [{"text": "🔎 جستجوی تراکنش (اختلاف مالی)", "callback_data": "fin:search"}],
    ])


def show_financial_menu(chat_id):
    send_message(chat_id, "کدوم گزارش مالی رو می‌خوای؟", reply_markup=financial_menu_keyboard())


def sync_inbound_json(cur, uuid_, name, total_bytes, expiry_ms):
    cur.execute("SELECT id, settings FROM inbounds WHERE id IN (1,2) ORDER BY id")
    rows = {r[0]: json.loads(r[1]) for r in cur.fetchall()}
    for iid, data in rows.items():
        for cl in data["clients"]:
            if cl.get("id") == uuid_ or cl.get("email") == name:
                cl["totalGB"] = total_bytes
                cl["expiryTime"] = expiry_ms
                cl["enable"] = True
        cur.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(data), iid))


# ---------- actions (all keep clients+client_inbounds AND inbounds.settings JSON in sync) ----------

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

def create_client(name, gb, days):
    os.makedirs(QR_DIR, exist_ok=True)
    total_bytes = int(gb * 1024 ** 3)
    # در x-ui صفر یعنی نامحدود: total_gb=0 حجم بی‌نهایت، expiry_time=0 بدون
    # انقضا. بدون این شرط، days=0 تاریخ انقضا را روی «همین لحظه» می‌گذاشت و
    # کلاینت بلافاصله منقضی به دنیا می‌آمد.
    expiry_ms = delayed_expiry_ms(days) if days else 0
    now_ms = int(time.time() * 1000)
    cid = str(uuid.uuid4())
    sub_id = randid()

    c = sqlite3.connect(DB)
    cur = c.cursor()

    cur.execute("""INSERT INTO clients
        (email, sub_id, uuid, password, auth, flow, security, limit_ip, limit_hwid, total_gb, expiry_time, enable, tg_id, group_name, comment, reset, reset_day, reset_max, traffic_reset, traffic_reset_day, created_at, updated_at, sync_orphaned_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (name, sub_id, cid, randid(), randid(), "", "auto", 0, 2, total_bytes, expiry_ms, 1, 0, "", "", 0, 0, 0, "never", 1, now_ms, now_ms, 0))
    client_id = cur.lastrowid
    cur.execute("INSERT INTO client_inbounds (client_id, inbound_id, flow_override, created_at) VALUES (?,?,?,?)", (client_id, 1, "", now_ms))
    cur.execute("INSERT INTO client_inbounds (client_id, inbound_id, flow_override, created_at) VALUES (?,?,?,?)", (client_id, 2, "xtls-rprx-vision", now_ms))
    upsert_traffic_row(cur, name, total_bytes, expiry_ms, 1)

    cur.execute("SELECT id, settings FROM inbounds WHERE id IN (1,2) ORDER BY id")
    rows = {r[0]: json.loads(r[1]) for r in cur.fetchall()}
    base = {"id": cid, "email": name, "enable": True, "expiryTime": expiry_ms, "limitIp": 0,
            "totalGB": total_bytes, "subId": sub_id, "tgId": 0, "reset": 0, "comment": "",
            "created_at": now_ms, "updated_at": now_ms}
    rows[1]["clients"].append(dict(base, flow=""))
    rows[2]["clients"].append(dict(base, flow="xtls-rprx-vision"))
    for iid in (1, 2):
        cur.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(rows[iid]), iid))
    c.commit()
    c.close()

    subprocess.run(["systemctl", "restart", "x-ui"], check=True)
    sub_link = f"https://{sub_domain_current()}:{SUB_PORT}/sub/{sub_id}"
    qr_path = f"{QR_DIR}/{name}.png"
    subprocess.run(["qrencode", "-o", qr_path, "-s", "8", sub_link], check=True)
    return sub_link, qr_path


def get_next_number_range(base_name, count):
    """For 'create N accounts named <base><n>' with no explicit range: continue
    numbering after the highest existing <base><n> found."""
    c = sqlite3.connect(DB)
    cur = c.cursor()
    cur.execute("SELECT email FROM clients WHERE email LIKE ?", (base_name + "%",))
    existing = [r[0] for r in cur.fetchall()]
    c.close()
    max_n = 0
    pat = re.compile(r"^" + re.escape(base_name) + r"(\d+)$")
    for e in existing:
        m = pat.match(e)
        if m:
            max_n = max(max_n, int(m.group(1)))
    start = max_n + 1
    return list(range(start, start + count))


def bulk_create(base_name, numbers, gb, days):
    """Create many sequentially-numbered clients (<base_name><n>) in one DB
    transaction + one x-ui restart, instead of one round-trip per account."""
    total_bytes = int(gb * 1024 ** 3)
    # در x-ui صفر یعنی نامحدود: total_gb=0 حجم بی‌نهایت، expiry_time=0 بدون
    # انقضا. بدون این شرط، days=0 تاریخ انقضا را روی «همین لحظه» می‌گذاشت و
    # کلاینت بلافاصله منقضی به دنیا می‌آمد.
    expiry_ms = delayed_expiry_ms(days) if days else 0
    now_ms = int(time.time() * 1000)

    c = sqlite3.connect(DB)
    cur = c.cursor()

    cur.execute("SELECT email FROM clients WHERE email LIKE ?", (base_name + "%",))
    existing = {r[0] for r in cur.fetchall()}

    cur.execute("SELECT id, settings FROM inbounds WHERE id IN (1,2) ORDER BY id")
    rows = {r[0]: json.loads(r[1]) for r in cur.fetchall()}

    created, skipped = [], []
    for n in numbers:
        email = f"{base_name}{n}"
        if email in existing:
            skipped.append(email)
            continue
        cid = str(uuid.uuid4())
        sub_id = randid()
        cur.execute("""INSERT INTO clients
            (email, sub_id, uuid, password, auth, flow, security, limit_ip, limit_hwid, total_gb, expiry_time, enable, tg_id, group_name, comment, reset, reset_day, reset_max, traffic_reset, traffic_reset_day, created_at, updated_at, sync_orphaned_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (email, sub_id, cid, randid(), randid(), "", "auto", 0, 2, total_bytes, expiry_ms, 1, 0, "", "", 0, 0, 0, "never", 1, now_ms, now_ms, 0))
        client_id = cur.lastrowid
        cur.execute("INSERT INTO client_inbounds (client_id, inbound_id, flow_override, created_at) VALUES (?,?,?,?)", (client_id, 1, "", now_ms))
        cur.execute("INSERT INTO client_inbounds (client_id, inbound_id, flow_override, created_at) VALUES (?,?,?,?)", (client_id, 2, "xtls-rprx-vision", now_ms))

        base = {"id": cid, "email": email, "enable": True, "expiryTime": expiry_ms, "limitIp": 0,
                "totalGB": total_bytes, "subId": sub_id, "tgId": 0, "reset": 0, "comment": "",
                "created_at": now_ms, "updated_at": now_ms}
        rows[1]["clients"].append(dict(base, flow=""))
        rows[2]["clients"].append(dict(base, flow="xtls-rprx-vision"))
        created.append((email, sub_id))

    for iid in (1, 2):
        cur.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(rows[iid]), iid))
    c.commit()
    c.close()

    if created:
        subprocess.run(["systemctl", "restart", "x-ui"], check=True)
    return created, skipped


def bulk_delete(base_name, numbers):
    emails = [f"{base_name}{n}" for n in numbers]
    c = sqlite3.connect(DB)
    cur = c.cursor()
    cur.execute("SELECT id, settings FROM inbounds WHERE id IN (1,2) ORDER BY id")
    rows = {r[0]: json.loads(r[1]) for r in cur.fetchall()}

    deleted, missing = [], []
    for email in emails:
        row = get_client(cur, email)
        if not row:
            missing.append(email)
            continue
        client_id, cid, sub_id, _, _ = row
        cur.execute("DELETE FROM client_inbounds WHERE client_id=?", (client_id,))
        cur.execute("DELETE FROM clients WHERE id=?", (client_id,))
        cur.execute("DELETE FROM client_traffics WHERE email=?", (email,))
        cur.execute("DELETE FROM client_hwids WHERE sub_id=?", (sub_id,))
        for iid, data in rows.items():
            data["clients"] = [cl for cl in data["clients"] if cl.get("id") != cid and cl.get("email") != email]
        deleted.append(email)

    for iid in (1, 2):
        cur.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(rows[iid]), iid))
    c.commit()
    c.close()

    if deleted:
        subprocess.run(["systemctl", "restart", "x-ui"], check=True)
        for email in deleted:
            qr_path = f"{QR_DIR}/{email}.png"
            if os.path.exists(qr_path):
                os.remove(qr_path)
    return deleted, missing


def clients_matching_prefix(prefix, limit=500):
    c = sqlite3.connect(DB)
    cur = c.cursor()
    cur.execute("SELECT email FROM clients WHERE email LIKE ? ORDER BY email LIMIT ?", (prefix + "%", limit))
    rows = [r[0] for r in cur.fetchall()]
    c.close()
    return rows


def bulk_renew_by_prefix(emails, gb=None, days=None):
    """Same 'reset usage, extend expiry' semantics as renew_client, batched for
    an entire wholesale group in one DB transaction + one x-ui restart."""
    c = sqlite3.connect(DB)
    cur = c.cursor()
    cur.execute("SELECT id, settings FROM inbounds WHERE id IN (1,2) ORDER BY id")
    rows = {r[0]: json.loads(r[1]) for r in cur.fetchall()}

    now_ms = int(time.time() * 1000)
    new_expiry_ms = delayed_expiry_ms(days or 30)
    renewed = []
    for email in emails:
        row = get_client(cur, email)
        if not row:
            continue
        client_id, cid, sub_id, cur_total_gb, _ = row
        new_total_bytes = int(gb * 1024 ** 3) if gb else cur_total_gb
        cur.execute("UPDATE clients SET total_gb=?, expiry_time=?, updated_at=?, enable=1 WHERE id=?",
                    (new_total_bytes, new_expiry_ms, now_ms, client_id))
        upsert_traffic_row(cur, email, new_total_bytes, new_expiry_ms, 1, reset_usage=True)
        for iid, data in rows.items():
            for cl in data["clients"]:
                if cl.get("id") == cid or cl.get("email") == email:
                    cl["totalGB"] = new_total_bytes
                    cl["expiryTime"] = new_expiry_ms
        renewed.append(email)

    for iid in (1, 2):
        cur.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(rows[iid]), iid))
    c.commit()
    c.close()
    if renewed:
        subprocess.run(["systemctl", "restart", "x-ui"], check=True)
    return renewed


def bulk_delete_by_prefix(emails):
    return bulk_delete_exact(emails)


def bulk_delete_exact(emails):
    c = sqlite3.connect(DB)
    cur = c.cursor()
    cur.execute("SELECT id, settings FROM inbounds WHERE id IN (1,2) ORDER BY id")
    rows = {r[0]: json.loads(r[1]) for r in cur.fetchall()}

    deleted = []
    for email in emails:
        row = get_client(cur, email)
        if not row:
            continue
        client_id, cid, sub_id, _, _ = row
        cur.execute("DELETE FROM client_inbounds WHERE client_id=?", (client_id,))
        cur.execute("DELETE FROM clients WHERE id=?", (client_id,))
        cur.execute("DELETE FROM client_traffics WHERE email=?", (email,))
        cur.execute("DELETE FROM client_hwids WHERE sub_id=?", (sub_id,))
        for iid, data in rows.items():
            data["clients"] = [cl for cl in data["clients"] if cl.get("id") != cid and cl.get("email") != email]
        deleted.append(email)

    for iid in (1, 2):
        cur.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(rows[iid]), iid))
    c.commit()
    c.close()
    if deleted:
        subprocess.run(["systemctl", "restart", "x-ui"], check=True)
        for email in deleted:
            qr_path = f"{QR_DIR}/{email}.png"
            if os.path.exists(qr_path):
                os.remove(qr_path)
    return deleted


def renew_client(name, gb=None, days=None):
    c = sqlite3.connect(DB)
    cur = c.cursor()
    row = get_client(cur, name)
    if not row:
        c.close()
        return None
    client_id, cid, sub_id, cur_total_gb, cur_expiry = row

    new_total_bytes = int(gb * 1024 ** 3) if gb else cur_total_gb
    new_expiry_ms = delayed_expiry_ms(days or 30)

    cur.execute("UPDATE clients SET total_gb=?, expiry_time=?, updated_at=?, enable=1 WHERE id=?",
                (new_total_bytes, new_expiry_ms, int(time.time() * 1000), client_id))
    upsert_traffic_row(cur, name, new_total_bytes, new_expiry_ms, 1, reset_usage=True)
    sync_inbound_json(cur, cid, name, new_total_bytes, new_expiry_ms)
    c.commit()
    c.close()
    subprocess.run(["systemctl", "restart", "x-ui"], check=True)
    return new_total_bytes, new_expiry_ms


def topup_client(name, gb=None, days=None):
    c = sqlite3.connect(DB)
    cur = c.cursor()
    row = get_client(cur, name)
    if not row:
        c.close()
        return None
    client_id, cid, sub_id, cur_total_gb, cur_expiry = row

    new_total_bytes = cur_total_gb + int(gb * 1024 ** 3) if gb else cur_total_gb
    if cur_expiry < 0:  # هنوز شروع نشده: فقط به مدت اضافه کن
        new_expiry_ms = cur_expiry - int(days * 86400 * 1000) if days else cur_expiry
    else:
        base_expiry = max(cur_expiry, int(time.time() * 1000))
        new_expiry_ms = base_expiry + int(days * 86400 * 1000) if days else cur_expiry

    cur.execute("UPDATE clients SET total_gb=?, expiry_time=?, updated_at=?, enable=1 WHERE id=?",
                (new_total_bytes, new_expiry_ms, int(time.time() * 1000), client_id))
    # top-up keeps the usage already on the meter, only the ceiling moves
    upsert_traffic_row(cur, name, new_total_bytes, new_expiry_ms, 1)
    sync_inbound_json(cur, cid, name, new_total_bytes, new_expiry_ms)
    c.commit()
    c.close()
    subprocess.run(["systemctl", "restart", "x-ui"], check=True)
    return new_total_bytes, new_expiry_ms


def make_unlimited(name, days):
    """کلاینت موجود را نامحدود می‌کند: حجم صفر (بی‌نهایت) و انقضا بر اساس days
    (days=0 یعنی همیشگی).

    عمداً جدا از topup_client است: آن تابع به مقدار فعلی «اضافه» می‌کند و
    نامحدود عددی نیست که بشود اضافه کرد — باید صفر گذاشته شود. هر سه جا
    هم‌زمان به‌روز می‌شوند (قانون طلایی)."""
    c = sqlite3.connect(DB)
    cur = c.cursor()
    row = get_client(cur, name)
    if not row:
        c.close()
        return None
    client_id, cid, sub_id, cur_total_gb, cur_expiry = row

    new_expiry_ms = delayed_expiry_ms(days) if days else 0

    cur.execute("UPDATE clients SET total_gb=?, expiry_time=?, updated_at=?, enable=1 WHERE id=?",
                (0, new_expiry_ms, int(time.time() * 1000), client_id))
    upsert_traffic_row(cur, name, 0, new_expiry_ms, 1)
    sync_inbound_json(cur, cid, name, 0, new_expiry_ms)
    c.commit()
    c.close()
    subprocess.run(["systemctl", "restart", "x-ui"], check=True)
    return 0, new_expiry_ms


def delete_client(name, restart=True):
    c = sqlite3.connect(DB)
    cur = c.cursor()
    row = get_client(cur, name)
    if not row:
        c.close()
        return False
    client_id, cid, sub_id, cur_total_gb, cur_expiry = row

    cur.execute("DELETE FROM client_inbounds WHERE client_id=?", (client_id,))
    cur.execute("DELETE FROM clients WHERE id=?", (client_id,))
    cur.execute("DELETE FROM client_traffics WHERE email=?", (name,))
    cur.execute("DELETE FROM client_hwids WHERE sub_id=?", (sub_id,))

    cur.execute("SELECT id, settings FROM inbounds WHERE id IN (1,2) ORDER BY id")
    rows = {r[0]: json.loads(r[1]) for r in cur.fetchall()}
    for iid, data in rows.items():
        data["clients"] = [cl for cl in data["clients"] if cl.get("id") != cid and cl.get("email") != name]
        cur.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(data), iid))

    c.commit()
    c.close()
    # حذف انبوه با restart=False صدا زده می‌شود و یک‌بار در پایان ری‌استارت
    # می‌کند — وگرنه حذف ۱۰ کلاینت یعنی ۱۰ بار قطعی سرویس برای همه‌ی مشتری‌ها.
    if restart:
        subprocess.run(["systemctl", "restart", "x-ui"], check=True)
    qr_path = f"{QR_DIR}/{name}.png"
    if os.path.exists(qr_path):
        os.remove(qr_path)
    return True


def client_is_enabled(name):
    c = sqlite3.connect(DB)
    cur = c.cursor()
    cur.execute("SELECT enable FROM clients WHERE email=?", (name,))
    row = cur.fetchone()
    c.close()
    return bool(row[0]) if row else None


def set_client_enabled(name, enabled):
    """Suspend/restore a client — the quick, reversible action for 'this
    payment looks wrong, cut them off while I check' (delete stays for when
    you're sure). Same dual-update rule as everywhere else: the `clients`
    table the panel UI reads, and the inbounds.settings JSON Xray actually
    reads."""
    c = sqlite3.connect(DB)
    cur = c.cursor()
    row = get_client(cur, name)
    if not row:
        c.close()
        return False
    client_id, cid, sub_id, cur_total_gb, cur_expiry = row
    cur.execute("UPDATE clients SET enable=? WHERE id=?", (1 if enabled else 0, client_id))
    cur.execute("UPDATE client_traffics SET enable=? WHERE email=?", (1 if enabled else 0, name))
    cur.execute("SELECT id, settings FROM inbounds WHERE id IN (1,2) ORDER BY id")
    rows = {r[0]: json.loads(r[1]) for r in cur.fetchall()}
    for iid, data in rows.items():
        for cl in data["clients"]:
            if cl.get("id") == cid or cl.get("email") == name:
                cl["enable"] = enabled
        cur.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(data), iid))
    c.commit()
    c.close()
    subprocess.run(["systemctl", "restart", "x-ui"], check=True)
    return True


def ping_host(host, count=4):
    """Round-trip time from THIS server to `host` — network health as seen from
    where the VPN actually runs, not from wherever the admin happens to be."""
    try:
        out = subprocess.run(["ping", "-c", str(count), "-W", "2", host],
                              capture_output=True, text=True, timeout=15).stdout
        m = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/", out)
        loss_m = re.search(r"(\d+)% packet loss", out)
        if m:
            return f"min/avg/max: {m.group(1)}/{m.group(2)}/{m.group(3)} ms (loss: {loss_m.group(1) if loss_m else '?'}%)"
        return "بدون پاسخ (timeout)"
    except Exception as e:
        return f"خطا: {e}"


def check_ws_handshake():
    """The exact failure mode a domain-block causes: port open, but the WS
    upgrade never completes because the SNI/host got blocked."""
    try:
        c = sqlite3.connect(DB)
        cur = c.cursor()
        cur.execute("SELECT stream_settings FROM inbounds WHERE id=1")
        ss = json.loads(cur.fetchone()[0])
        c.close()
        host, path = ss["wsSettings"]["host"], ss["wsSettings"]["path"]
    except Exception as e:
        return f"❌ نمی‌تونم تنظیمات رو بخونم: {e}"
    try:
        out = subprocess.run([
            "curl", "-sk", "--max-time", "6", "-i",
            "--resolve", f"{host}:443:127.0.0.1", f"https://{host}{path}",
            "-H", "Upgrade: websocket", "-H", "Connection: Upgrade",
            "-H", "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==", "-H", "Sec-WebSocket-Version: 13",
        ], capture_output=True, text=True, timeout=10).stdout
        return "✅ موفق" if "101 Switching Protocols" in out else f"❌ ناموفق (احتمال فیلترشدن دامنه: {host})"
    except Exception as e:
        return f"❌ خطا: {e}"


def check_disk_usage():
    try:
        import shutil
        total, used, free = shutil.disk_usage("/")
        pct = used / total * 100
        return f"{'✅' if pct < 85 else '⚠️'} {pct:.0f}% استفاده‌شده"
    except Exception as e:
        return f"خطا: {e}"


def check_cert_days_left():
    try:
        domain = None
        c = sqlite3.connect(DB)
        cur = c.cursor()
        cur.execute("SELECT stream_settings FROM inbounds WHERE id=1")
        ss = json.loads(cur.fetchone()[0])
        c.close()
        domain = ss["tlsSettings"]["serverName"]
        out = subprocess.run(["openssl", "x509", "-enddate", "-noout", "-in",
                               f"/etc/letsencrypt/live/{domain}/fullchain.pem"],
                              capture_output=True, text=True, timeout=8).stdout
        m = re.search(r"notAfter=(.+)", out)
        if not m:
            return "نامشخص"
        import datetime
        expiry = datetime.datetime.strptime(m.group(1).strip(), "%b %d %H:%M:%S %Y %Z")
        days_left = (expiry - datetime.datetime.utcnow()).days
        return f"{'✅' if days_left > 7 else '⚠️'} {days_left} روز تا انقضا"
    except Exception as e:
        return f"خطا: {e}"


def check_fail2ban_banned():
    try:
        out = subprocess.run(["fail2ban-client", "status", "sshd"],
                              capture_output=True, text=True, timeout=8).stdout
        m = re.search(r"Currently banned:\s*(\d+)", out)
        return m.group(1) if m else "0"
    except Exception:
        return "نصب نیست"


def check_full_system():
    lines = ["📡 وضعیت کامل سیستم:\n"]
    lines.append(f"🖥 x-ui/xray: {'✅ فعال' if subprocess.run(['systemctl','is-active','x-ui'], capture_output=True, text=True).stdout.strip()=='active' else '❌ غیرفعال'}")
    lines.append(f"🔌 هندشیک WS+TLS (Main): {check_ws_handshake()}")
    reality_ok = subprocess.run(["bash", "-c", "timeout 3 bash -c 'echo > /dev/tcp/127.0.0.1/8443'"], capture_output=True).returncode == 0
    lines.append(f"🛡 پورت Reality (8443): {'✅ باز' if reality_ok else '❌ پاسخ نمی‌ده'}")
    lines.append(f"💾 فضای دیسک: {check_disk_usage()}")
    lines.append(f"📜 گواهی SSL: {check_cert_days_left()}")
    lines.append(f"🚫 IP مسدود شده (fail2ban): {check_fail2ban_banned()}")
    lines.append(f"🌐 پینگ گوگل: {ping_host('8.8.8.8')}")
    return "\n".join(lines)


def check_server_ping():
    lines = []
    lines.append(f"🖥 xray/x-ui: {'✅ فعال' if subprocess.run(['systemctl','is-active','x-ui'], capture_output=True, text=True).stdout.strip()=='active' else '❌ غیرفعال'}")
    for label, host in (("🌐 گوگل DNS (8.8.8.8)", "8.8.8.8"), ("🌐 کلادفلر (1.1.1.1)", "1.1.1.1")):
        lines.append(f"{label}: {ping_host(host)}")
    domain = sub_domain_current()
    if domain:
        lines.append(f"🔗 دامنه‌ی فعلی ({domain}): {ping_host(domain)}")
    return "\n".join(lines)


def format_last_seen(is_online, last_online_ms):
    """is_online comes from Xray's live stats API (the actual source of
    truth); last_online_ms (from client_traffics, may be stale/absent) is
    only used for the historical 'last seen X ago' text when offline."""
    if is_online:
        return "🔵 الان آنلاینه"
    if not last_online_ms:
        return "⚪ هنوز وصل نشده"
    age_s = (time.time() * 1000 - last_online_ms) / 1000
    if age_s < 3600:
        return f"⚪ آفلاین (آخرین اتصال: {int(age_s/60)} دقیقه پیش)"
    if age_s < 86400:
        return f"⚪ آفلاین (آخرین اتصال: {int(age_s/3600)} ساعت پیش)"
    return f"⚪ آفلاین (آخرین اتصال: {int(age_s/86400)} روز پیش)"


def fmt_size(n):
    """Megabytes below a gigabyte — the 200MB trial plan rendered as "0GB",
    which looks like a broken account rather than a small one."""
    n = max(0, n or 0)
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.0f}MB"
    return f"{n / 1024 ** 3:.2f}".rstrip("0").rstrip(".") + "GB"


def remaining_time_fa(expiry_ms):
    """Plain int division showed an expired account as a negative number of
    days, which reads like a bug rather than an expiry."""
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


def status_client(name):
    c = sqlite3.connect(DB)
    cur = c.cursor()
    cur.execute("SELECT total_gb, expiry_time, enable, sub_id, tg_id, created_at "
                "FROM clients WHERE email=?", (name,))
    row = cur.fetchone()
    if not row:
        c.close()
        return None
    total_gb, expiry_ms, enable, sub_id, tg_id, created_at = row
    cur.execute("SELECT up, down, last_online FROM client_traffics WHERE email=?", (name,))
    tr = cur.fetchone()
    c.close()
    used = ((tr[0] or 0) + (tr[1] or 0)) if tr else 0
    last_online = tr[2] if tr else 0
    is_online = name in get_online_emails()
    return {
        "remain_b": max(0, total_gb - used), "total_b": total_gb, "used_b": used,
        "remain_txt": remaining_time_fa(expiry_ms),
        "online_state": format_last_seen(is_online, last_online),
        "enable": enable, "sub_id": sub_id, "tg_id": tg_id, "created_at": created_at,
    }


APP_LINK_BASE = "https://app.example.com/open/"
APP_DOWNLOAD_URL = "https://app.example.com/open/"
WINDOWS_SETUP_URL = "https://app.example.com/app/HermesVPN-Connect-Setup.exe"  # always the latest Windows installer


def vless_link_for(email):
    """لینک vless اینباند اصلی برای این اکانت — مستقیم از تنظیمات x-ui.

    همان منطقی که ربات فروش استفاده می‌کند، شامل fp؛ بدون آن بعضی
    کلاینت‌ها وصل نمی‌شوند.
    """
    try:
        c = sqlite3.connect(DB)
        cur = c.cursor()
        cur.execute("SELECT uuid FROM clients WHERE email=? COLLATE NOCASE", (email,))
        row = cur.fetchone()
        if not row:
            c.close()
            return ""
        cid = row[0]
        cur.execute("SELECT stream_settings FROM inbounds WHERE id=1")
        r2 = cur.fetchone()
        c.close()
        if not r2:
            return ""
        ss = json.loads(r2[0])
        host = ss["wsSettings"]["host"]
        q = urllib.parse.urlencode({
            "type": "ws", "security": "tls", "path": ss["wsSettings"]["path"],
            "host": host, "sni": ss["tlsSettings"]["serverName"],
            "fp": ss["tlsSettings"]["settings"]["fingerprint"], "encryption": "none"})
        return f"vless://{cid}@{host}:443?{q}#{urllib.parse.quote(email)}"
    except Exception as e:
        print(f"[vless] ساخت لینک برای {email} نشد: {e}", flush=True)
        return ""


ORIGIN_IP = "203.0.113.10"
MAIN_ALT_IPS = ["188.114.96.3", "162.159.135.1", "190.93.244.1"]   # همان IPهای ربات فروش


def admin_direct_links(email):
    """همان مسیرهایی که خریدارِ ربات فروش می‌گیرد: Main، Main 2-4 (IPهای دیگر کلادفلر)، Reality و XHTTP.
    قبلاً اکانتِ ساخت ادمین فقط لینک ساب و یک کانفیگ Main داشت؛ اگر Main روی شبکه‌ی کاربر بد بود، جایگزینی نداشت."""
    c = sqlite3.connect(DB); cur = c.cursor()
    row = cur.execute("SELECT uuid FROM clients WHERE email=? COLLATE NOCASE", (email,)).fetchone()
    if not row:
        c.close(); return []
    cid = row[0]
    ib = {r[0]: json.loads(r[1]) for r in cur.execute("SELECT id, stream_settings FROM inbounds WHERE id IN (1,2,3)")}
    c.close()
    tag = urllib.parse.quote(email)
    out = []
    if 1 in ib:
        s1 = ib[1]
        q = urllib.parse.urlencode({"type": "ws", "security": "tls", "path": s1["wsSettings"]["path"], "host": s1["wsSettings"]["host"],
                                    "sni": s1["tlsSettings"]["serverName"], "fp": s1["tlsSettings"]["settings"]["fingerprint"], "encryption": "none"})
        host = s1["wsSettings"]["host"]
        out.append(("Main (پیش‌فرض)", f"vless://{cid}@{host}:443?{q}#{tag}-Main"))
        for n, ip in enumerate(MAIN_ALT_IPS, 2):
            out.append((f"Main {n} ⚡", f"vless://{cid}@{ip}:443?{q}#{tag}-Main{n}"))
    if 2 in ib:
        rs = ib[2]["realitySettings"]
        q = urllib.parse.urlencode({"security": "reality", "pbk": rs["settings"]["publicKey"], "fp": rs["settings"]["fingerprint"],
                                    "sni": rs["serverNames"][0], "sid": next((x for x in rs["shortIds"] if x), ""),
                                    "spx": rs["settings"].get("spiderX", "/"), "type": "tcp", "flow": "xtls-rprx-vision", "encryption": "none"})
        out.append(("Reality", f"vless://{cid}@{ORIGIN_IP}:8443?{q}#{tag}-Reality"))
    if 3 in ib:
        x, t = ib[3]["xhttpSettings"], ib[3]["tlsSettings"]
        q = urllib.parse.urlencode({"type": "xhttp", "security": "tls", "path": x["path"], "host": x["host"], "mode": x.get("mode", "auto"),
                                    "sni": t["serverName"], "fp": "chrome", "alpn": "h2,http/1.1", "encryption": "none"})
        out.append(("XHTTP 🛡 (مخابرات)", f"vless://{cid}@{x['host']}:2083?{q}#{tag}-XHTTP"))
    return out


def app_account_link(email):
    """لینک «اتصال به نرم‌افزار» مثل ربات فروش: اپ به خودِ اکانت وصل می‌شود (همه‌ی مسیرها + به‌روزرسانی خودکار)،
    نه یک کانفیگ ثابت. توکن در همان جدول ربات فروش (app_link_tokens) ساخته می‌شود."""
    import secrets
    token = secrets.token_urlsafe(24)
    c = sqlite3.connect(ORDERS_DB)
    c.execute("""CREATE TABLE IF NOT EXISTS app_link_tokens (token TEXT PRIMARY KEY, email TEXT NOT NULL,
                 created_at INTEGER NOT NULL, last_used_at INTEGER, use_count INTEGER DEFAULT 0)""")
    c.execute("INSERT OR REPLACE INTO app_link_tokens (token, email, created_at, last_used_at, use_count) VALUES (?,?,?,NULL,0)",
              (token, email, int(time.time())))
    c.commit(); c.close()
    return f"{APP_LINK_BASE}?t={token}"


def send_direct_links(chat_id, email):
    links = admin_direct_links(email)
    if not links:
        send_message(chat_id, "اکانتی با این اسم پیدا نشد."); return
    body = "\n\n".join(f"{html.escape(l)}:\n<code>{html.escape(u)}</code>" for l, u in links)
    send_message(chat_id, f"🔗 لینک‌های مستقیم «{html.escape(email)}» (روی هر کدام بزن تا کپی شه):\n\n{body}\n\n"
                 "اگر یکی روی شبکه‌ی کاربر خوب نبود، بعدی را بدهید. برای اپ HermesVPN بهتر است دکمه‌ی «📲 اتصال به نرم‌افزار» را بفرستید.",
                 parse_mode="HTML",
                 reply_markup=inline([[{"text": "📲 اتصال به نرم‌افزار", "url": app_account_link(email)}]]))


def app_connect_url(vless_uri):
    """fragment نه query: مرورگر بعد از # را به سرور نمی‌فرستد، پس کانفیگ
    مشتری در لاگ هیچ سروری نمی‌نشیند."""
    return APP_LINK_BASE + "#url=" + urllib.parse.quote(vless_uri, safe="")


def client_extra_info(email):
    """اسم دلخواهی که مشتری/فروشنده روی اکانت گذاشته، به‌همراه خریدهای همان
    اکانت. هر دو در دیتابیس ربات فروش‌اند و تا امروز از دید ادمین پنهان بودند:
    ادمین اسم `Mersadbehnam_7` را می‌دید ولی نمی‌دانست فروشنده صدایش می‌کند «علی»."""
    label, orders = None, []
    try:
        c = sqlite3.connect(ORDERS_DB)
        cur = c.cursor()
        cur.execute("SELECT label FROM client_labels WHERE email=?", (email,))
        r = cur.fetchone()
        label = r[0] if r else None
        cur.execute("SELECT id, decided_at, total_price, plan_key FROM orders "
                    "WHERE client_email=? AND status='approved' "
                    "ORDER BY decided_at DESC LIMIT 3", (email,))
        orders = cur.fetchall()
        c.close()
    except Exception as e:
        print("client_extra_info failed:", e)
    return label, orders


CLIST_PER_PAGE = 20


def clients_list_page(query, page, only_blocked=False):
    """یک صفحه از لیست: متن خوانا + ایمیل‌های همان صفحه (برای ساخت دکمه‌ها).
    قبلاً کل ۱۰۰ کلاینت در یک پیام می‌رفت که هم غیرقابل‌خواندن بود و هم به
    سقف ۴۰۹۶ کاراکتری تلگرام نزدیک می‌شد."""
    c = sqlite3.connect(DB)
    cur = c.cursor()
    conditions, params = [], []
    if query:
        conditions.append("email LIKE ?")
        params.append(f"%{query}%")
    if only_blocked:
        conditions.append("enable = 0")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    cur.execute(f"SELECT email, total_gb, expiry_time FROM clients {where} ORDER BY id", params)
    rows = cur.fetchall()
    cur.execute("SELECT email, up, down FROM client_traffics")
    traffic = {r[0]: (r[1] or 0) + (r[2] or 0) for r in cur.fetchall()}
    c.close()

    total = len(rows)
    start = max(0, page) * CLIST_PER_PAGE
    page_rows = rows[start:start + CLIST_PER_PAGE]
    if not page_rows:
        if query:
            empty = f"کسی با «{query}» پیدا نشد."
        elif only_blocked:
            empty = "هیچ کلاینت مسدودی وجود نداره."
        else:
            empty = "کلاینتی وجود نداره."
        return empty, [], False, {}

    emails = [r[0] for r in page_rows]
    dots = client_dot_map(emails)
    labels = all_labels()
    lines = []
    for email, total_gb, expiry_ms in page_rows:
        used = traffic.get(email, 0)
        vol = "نامحدود" if not total_gb else f"{fmt_size(used)}/{fmt_size(total_gb)}"
        label = labels.get(email)
        shown = f"{email} «{label}»" if label else email
        lines.append(f"{dots.get(email, '🔴')} {shown}: {vol} • {remaining_time_fa(expiry_ms)}")

    pages = max(1, (total + CLIST_PER_PAGE - 1) // CLIST_PER_PAGE)
    title = "🔒 کلاینت‌های مسدود" if only_blocked else "📋 کلاینت‌ها"
    head = f"{title} — صفحه {min(page, pages - 1) + 1} از {pages} (کل: {total})"
    if query:
        head += f" — جستجوی «{query}»"
    return head + "\n\n" + "\n".join(lines), emails, start + CLIST_PER_PAGE < total, dots


def clients_list_keyboard(emails, page, has_more, dots=None, nav_prefix="clist"):
    """هر کلاینت یک دکمه که کارت وضعیتش را باز می‌کند (callback موجود status:pick).
    چراغ وضعیت روی خودِ دکمه هم می‌آید — دکمه چیزی است که چشم اول می‌بیند."""
    dots = dots or {}
    rows = chunk([{"text": f"{dots.get(e, '🔴')} {e}",
                   "callback_data": f"status:pick:{e}"} for e in emails], 2)
    nav = []
    if page > 0:
        nav.append({"text": "◀️ قبلی", "callback_data": f"{nav_prefix}:{page - 1}"})
    if has_more:
        nav.append({"text": "▶️ بعدی", "callback_data": f"{nav_prefix}:{page + 1}"})
    if nav:
        rows.append(nav)
    return inline(rows)


def send_clients_page(chat_id, query=None, page=0):
    text, emails, has_more, dots = clients_list_page(query, page)
    STATE[chat_id] = {"flow": "clist", "step": "browse",
                      "data": {"query": query, "page": page}}
    if not emails:
        send_message(chat_id, text, reply_markup=main_menu())
        return
    send_message(chat_id, text,
                 reply_markup=clients_list_keyboard(emails, page, has_more, dots))


def send_blocked_list(chat_id, page=0):
    text, emails, has_more, dots = clients_list_page(None, page, only_blocked=True)
    STATE[chat_id] = {"flow": "blist", "step": "browse", "data": {"page": page}}
    if not emails:
        send_message(chat_id, text, reply_markup=main_menu())
        return
    send_message(chat_id, text,
                 reply_markup=clients_list_keyboard(emails, page, has_more, dots, nav_prefix="blist"))


# ---------- domain rotation (filter recovery) ----------

def sub_domain_current():
    c = sqlite3.connect(DB)
    cur = c.cursor()
    cur.execute("SELECT value FROM settings WHERE key='subDomain'")
    row = cur.fetchone()
    c.close()
    return row[0] if row and row[0] else ""


def cf_api(method, path, data=None):
    url = f"https://api.cloudflare.com/client/v4{path}"
    headers = {"Authorization": f"Bearer {cf_token()}", "Content-Type": "application/json"}
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read())


def rotate_domain():
    new_sub = f"edge-{randid(8)}.{ZONE_NAME}"
    cf_api("POST", f"/zones/{ZONE_ID}/dns_records",
           {"type": "A", "name": new_sub, "content": ORIGIN_IP, "ttl": 1, "proxied": True})

    subprocess.run([
        "certbot", "certonly", "--dns-cloudflare",
        "--dns-cloudflare-credentials", CF_CREDS_FILE,
        "--dns-cloudflare-propagation-seconds", "30",
        "-d", new_sub, "--non-interactive", "--agree-tos", "-m", "admin@" + ZONE_NAME,
    ], check=True)

    c = sqlite3.connect(DB)
    cur = c.cursor()
    cur.execute("SELECT stream_settings FROM inbounds WHERE id=1")
    ss = json.loads(cur.fetchone()[0])
    # عمداً هر بار host/path رو خالی می‌ذاریم (نه دامنه/مسیر جدید) — یعنی
    # xray دیگه هیچ‌وقت هیچ Host یا path ای رو رد نمی‌کنه. قبلاً اینجا
    # new_sub/new_path ست می‌شد که باعث می‌شد هر مشتری‌ای که هنوز کانفیگ
    # قدیمی داره (تا وقتی اپش خودش صبر کنه و subscription رو رفرش کنه)
    # کاملاً قطع بشه — یعنی هر چرخش دامنه یه قطعی چندده‌دقیقه‌ای برای همه
    # می‌ساخت. الان چون این چک خاموشه، فقط UUID (احراز هویت واقعی VLESS)
    # مهمه؛ مشتری با هر دامنه/مسیر قدیمی‌ای هم بلافاصله وصل می‌مونه.
    ss["wsSettings"]["host"] = ""
    ss["wsSettings"]["path"] = ""
    ss["tlsSettings"]["serverName"] = new_sub
    ss["tlsSettings"]["certificates"][0]["certificateFile"] = f"/etc/letsencrypt/live/{new_sub}/fullchain.pem"
    ss["tlsSettings"]["certificates"][0]["keyFile"] = f"/etc/letsencrypt/live/{new_sub}/privkey.pem"
    cur.execute("UPDATE inbounds SET stream_settings=? WHERE id=1", (json.dumps(ss),))
    c.commit()
    c.close()

    subprocess.run(["systemctl", "restart", "x-ui"], check=True)
    return new_sub


CUSTOMER_BOT_TOKEN_FILE = "/root/.hermes-customer-bot-token"


# ─── گفتگوی دوطرفه‌ی پشتیبانی ───
# مشتری در ربات فروش پیام می‌دهد؛ پیامش با دکمه‌ی «پاسخ» به همین‌جا می‌آید.
# ادمین یک بار روی دکمه می‌زند و از آن به بعد هر چه بنویسد مستقیم برای همان
# مشتری می‌رود — بدون اینکه لازم باشد آیدی را جایی کپی کند یا از ربات خارج شود.
# در حافظه نگه داشته می‌شود نه دیتابیس: یک گفتگوی باز فقط باید تا ری‌استارت
# بعدی دوام بیاورد، و اگر گم شد ادمین دوباره روی دکمه می‌زند.
class _PersistDict(dict):
    """گفتگوی باز پشتیبانی با ری‌استارت ربات گم نشود (owner 2026-11-14: بعد از ری‌استارت، جواب ادمین به مشتری
    به‌جای مشتری به‌عنوان «اسم کلاینت» تفسیر شد)."""
    PATH = "/var/lib/hermesvpn-ai/support_target" + ("_bale" if os.environ.get("HERMES_BALE_MODE") == "1" else "") + ".json"

    def __init__(self):
        super().__init__()
        try:
            self.update({int(k): int(v) for k, v in json.load(open(self.PATH)).items()})
        except Exception:
            pass

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.PATH), exist_ok=True)
            json.dump({str(k): v for k, v in self.items()}, open(self.PATH, "w"))
        except Exception as e:
            print("support target save:", e)

    def __setitem__(self, k, v):
        super().__setitem__(k, v); self._save()

    def pop(self, k, *d):
        r = super().pop(k, *d); self._save(); return r


SUPPORT_TARGET = _PersistDict()   # admin chat_id -> customer chat_id (persisted)
SUPPORT_END_BUTTON = "🔚 پایان گفتگو با مشتری"


def log_support_message(tg_id, direction, text):
    try:
        c = sqlite3.connect(ORDERS_DB)
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
        who = {"in": label_in, "cin": "👤 مشتری (در ربات)", "bot": "🤖 ربات"}.get(d, label_out)
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
    return conn.execute("SELECT id, direction, text, created_at FROM support_messages WHERE tg_id=? ORDER BY id",
                        (int(tg_id),)).fetchall()


def admin_history_view(tg_id, page, delete_mode=False):
    """(text, keyboard) of the full support chat with one customer, paginated; admin-only delete."""
    try:
        c = sqlite3.connect(ORDERS_DB); rows = _support_rows(c, tg_id); c.close()
    except Exception as e:
        return f"خطا در خواندن سابقه: {e}", None
    pages = _support_pages(rows, "👤 مشتری", "🛠 پشتیبانی")
    reply_row = [{"text": "✉️ پیام به این مشتری", "callback_data": f"supreply:{tg_id}"}]
    if not pages:
        return (f"💬 سابقه‌ی گفتگو با {tg_id}\n\nهنوز پیامی ثبت نشده.", {"inline_keyboard": [reply_row]})
    page = max(0, min(page, len(pages) - 1))
    head = f"💬 سابقه‌ی گفتگو با {tg_id} — {len(rows)} پیام"
    if len(pages) > 1:
        head += f"  (صفحه {page + 1}/{len(pages)})"
    kb = []
    if delete_mode:
        head += "\n🗑 روی پیامی که می‌خواهی پاک شود بزن:"
        for rid, txt in pages[page]:
            first = txt.split("\n", 1)[0]
            body = html.unescape(txt.split("\n", 1)[1] if "\n" in txt else "")[:28]
            kb.append([{"text": f"🗑 {first.split(' · ')[0]}: {body}", "callback_data": f"supdx:{rid}:{tg_id}:{page}"}])
        kb.append([{"text": "✔️ تمام", "callback_data": f"suphist:{tg_id}:{page}"}])
    else:
        nav = []
        if page + 1 < len(pages):
            nav.append({"text": "◀️ قدیمی‌تر", "callback_data": f"suphist:{tg_id}:{page + 1}"})
        if page > 0:
            nav.append({"text": "جدیدتر ▶️", "callback_data": f"suphist:{tg_id}:{page - 1}"})
        if nav:
            kb.append(nav)
        kb.append(reply_row)
        kb.append([{"text": "🗑 حذف پیام", "callback_data": f"supdel:{tg_id}:{page}"},
                   {"text": "🗑 پاک کردن کل سابقه", "callback_data": f"supclr:{tg_id}"}])
    text = head + "\n\n" + "\n\n".join(t for _, t in pages[page])
    return text, {"inline_keyboard": kb}


def support_history_text(tg_id, limit=20):
    return admin_history_view(tg_id, 0)[0]


def support_reply_keyboard():
    # در حالت چت با مشتری هم منوی اصلی زیر دکمه‌ی «پایان گفتگو» می‌ماند تا هیچ‌وقت گم نشود
    return {"keyboard": [[SUPPORT_END_BUTTON]] + main_menu()["keyboard"], "resize_keyboard": True}


PAID_WEBHOOK_SECRET_FILE = "/root/.hermes-paid-webhook-secret"
# سرور داخلی ربات مشتری TLS دارد (همان پورتی که کال‌بک iyzico رویش است).
# روی لوپ‌بک گواهی با نام دامنه نمی‌خواند، پس عمداً بررسی گواهی خاموش است:
# ترافیک از خود ماشین بیرون نمی‌رود و رمز وب‌هوک احراز هویت را انجام می‌دهد.
CUSTOMER_API_BASE = "https://127.0.0.1:2087"
_LOOPBACK_SSL = ssl.create_default_context()
_LOOPBACK_SSL.check_hostname = False
_LOOPBACK_SSL.verify_mode = ssl.CERT_NONE


def customer_order_action(order_id, action):
    """تأیید یا رد سفارش را به ربات مشتری می‌سپارد.

    منطق فعال‌سازی (ساخت کلاینت، ارسال لینک، ثبت وضعیت) فقط آنجاست. دکمه‌ها
    اینجا هستند چون ادمین اینجا کار می‌کند، ولی کد دو بار نوشته نمی‌شود —
    وگرنه روزی یکی از دو نسخه عوض می‌شود و دیگری نه.
    """
    with open(PAID_WEBHOOK_SECRET_FILE) as f:
        secret = f.read().strip()
    payload = json.dumps({"secret": secret, "order_id": int(order_id), "action": action}).encode()
    req = urllib.request.Request(f"{CUSTOMER_API_BASE}/admin/order-action", data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60, context=_LOOPBACK_SSL) as resp:
        return json.loads(resp.read())


def edit_caption(chat_id, message_id, caption):
    """پیامِ بررسی یک عکس است، پس کپشنش ویرایش می‌شود نه متنش."""
    return api_call("editMessageCaption",
                    {"chat_id": chat_id, "message_id": message_id, "caption": caption})


def send_via_customer_bot(chat_id, text):
    """Clients link their Telegram via the SALES bot (@YourSalesBot), not
    this admin bot — so reaching them after a rotation means sending through
    that bot's own token, not this bot's."""
    with open(CUSTOMER_BOT_TOKEN_FILE) as f:
        token = f.read().strip()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=15) as resp:
        return json.loads(resp.read())


def notify_valid_clients_after_rotation():
    """After filter-recovery rotation, proactively nudge every Telegram-linked
    client whose subscription hasn't expired to refresh now, instead of
    waiting for their app's own auto-refresh timer. Only reaches clients who
    already linked their Telegram (tg_id set) via the sales bot's buy/claim
    flow — a bot can't message a user who never started a chat with it, so
    accounts nobody has claimed yet still need manual/out-of-band contact."""
    now_ms = time.time() * 1000
    c = sqlite3.connect(DB)
    cur = c.cursor()
    cur.execute("SELECT email, sub_id, tg_id FROM clients WHERE tg_id IS NOT NULL AND tg_id != 0 AND (expiry_time > ? OR expiry_time < 0)", (now_ms,))
    rows = cur.fetchall()
    c.close()
    domain = sub_domain_current()
    sent, failed = 0, 0
    for email, sub_id, tg_id in rows:
        link = f"https://{domain}:{SUB_PORT}/sub/{sub_id}"
        try:
            _send_bundle_via_customer_bot(
                tg_id, email, link,
                header=("فیلترینگ رفع شد ✅\n"
                        "اگه وصل نمی‌شی، توی اپت روی «Update Subscription» بزن "
                        "(یا چند دقیقه صبر کن، خودش به‌روز می‌شه)."),
            )
            sent += 1
        except Exception:
            failed += 1
    return sent, failed, len(rows)


def _send_bundle_via_customer_bot(tg_id, email, sub_link, header):
    """بسته‌ی استاندارد لینک (QR + vless + لینک اشتراک) رو می‌فرسته.

    عمداً کد ساخت لینک اینجا کپی نشده: تابع اصلی (send_connection_bundle)
    توی ربات فروشه چون اونجاست که ساختار لینک‌ها تعریف شده. اینجا همون فایل
    توی یه پروسه‌ی جدا اجرا می‌شه تا اگه فردا فرمت لینک عوض شد، فقط یه‌جا
    عوض بشه و این مسیر هم خودکار همون رو بفرسته."""
    script = (
        "import sys\n"
        "src = open('/usr/local/bin/hermes-customer-bot.py').read()\n"
        "exec(src.split('def main(')[0])\n"
        "send_connection_bundle(int(sys.argv[1]), sys.argv[2], sys.argv[3], header=sys.argv[4])\n"
    )
    subprocess.run(
        ["python3", "-c", script, str(tg_id), email, sub_link, header],
        check=True, capture_output=True, timeout=60,
    )


# ---------- menu-driven flows ----------

HELP_TEXT = (
    "از منوی پایین صفحه استفاده کن — دکمه بزن، دیگه نیازی به تایپ دقیق نیست.\n\n"
    "همچنین می‌تونی مثل یه پیام عادی هم بنویسی، مثلاً:\n"
    "«بهنام رو با 20 گیگ و 30 روز بساز»\n"
    "«بهنام چقدر داره»\n"
    "«بهنام رو تمدید کن»\n"
    "«به بهنام 10 گیگ اضافه کن»\n"
    "«بهنام رو پاک کن»\n"
    "«دوباره فیلتر شد درستش کن»\n\n"
    "ساخت انبوه (فروش عمده):\n"
    "«مرسد 20 تا اکانت بساز» - از شماره‌ی بعدی موجود ادامه می‌ده\n"
    "«مرسد 34 تا مرسد 54 بساز» - بازه‌ی دقیق\n"
    "«لیست مرسد» - فقط کلاینت‌های مرسد رو نشون می‌ده (برای هزاران کلاینت هم کار می‌کنه)\n\n"
    "از دکمه‌های «📦 ساخت انبوه»، «🔄 تمدید انبوه» و «🗑 حذف انبوه» هم می‌تونی\n"
    "بدون تایپ، با ویزارد قدم‌به‌قدم همین کارها رو انجام بدی.\n\n"
    "«🏓 پینگ سرور» و «📡 وضعیت کامل سیستم» وضعیت شبکه/سرور رو همین الان نشون می‌دن.\n\n"
    "«🎁 دعوت‌ها» آمار سیستم زیرمجموعه‌گیری ربات فروش (دعوت‌شده ۳ گیگ، دعوت‌کننده ۱۰ گیگ یا ۳ دعوت = اکانت ۵۰ گیگ).\n"
    "«📣 یادآوری به خریداران» پیام خرید دوباره + لینک دعوت اختصاصی را برای همه‌ی خریداران از ربات فروش می‌فرستد (با تأیید)."
)


# ---------- referral stats + buyer reminder (2026-10-24) ----------
SALES_BOT_USERNAME = "YourSalesBot"
REMINDER_GAP_S = 24 * 3600


def wheel_log_text():
    import jdatetime
    from datetime import datetime, timezone, timedelta
    tz = timezone(timedelta(hours=3, minutes=30))
    jd = lambda ms: jdatetime.datetime.fromgregorian(datetime=datetime.fromtimestamp(ms/1000, tz)).strftime("%m/%d %H:%M")
    PRIZE = {"c5": "۵٪ تخفیف", "c10": "۱۰٪ تخفیف", "c15": "۱۵٪ تخفیف", "gb05": "۵۰۰ مگ", "gb1": "۱ گیگ", "none": "پوچ"}
    c = sqlite3.connect(ORDERS_DB)
    try:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='wheel'").fetchone():
            return "🎡 لاگ گردونه\n\nهنوز کسی گردونه رو نچرخونده."
        total = c.execute("SELECT COUNT(*) FROM wheel").fetchone()[0]
        users = c.execute("SELECT COUNT(DISTINCT tg_id) FROM wheel").fetchone()[0]
        today0 = int(datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()*1000)
        today = c.execute("SELECT COUNT(*) FROM wheel WHERE ts>=?", (today0,)).fetchone()[0]
        byp = dict(c.execute("SELECT prize, COUNT(*) FROM wheel GROUP BY prize").fetchall())
        rows = c.execute("""SELECT w.tg_id, COALESCE(u.display_name,''), w.ts, w.prize FROM wheel w
                            LEFT JOIN users u ON u.tg_id=w.tg_id ORDER BY w.ts DESC LIMIT 40""").fetchall()
    finally:
        c.close()
    L = [f"🎡 لاگ گردونه‌ی شانس", "",
         f"کل چرخش: {total} | کاربر یکتا: {users} | امروز: {today}",
         "جوایز: " + "، ".join(f"{PRIZE.get(k,k)}×{v}" for k,v in sorted(byp.items(), key=lambda x:-x[1])), "",
         "آخرین چرخش‌ها:"]
    for tg, name, ts, prize in rows:
        L.append(f"• {esc(name) or tg} ({tg}) — {jd(ts)} — {PRIZE.get(prize, prize)}")
    return "\n".join(L)


def referral_report_text():
    c = sqlite3.connect(ORDERS_DB)
    try:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='referrals'").fetchone():
            return "🎁 دعوت‌ها\n\nهنوز هیچ دعوتی ثبت نشده."
        joined, paid, claimed = c.execute(
            "SELECT COUNT(*), COALESCE(SUM(status='paid'),0), COALESCE(SUM(claimed),0) FROM referrals").fetchone()
        g10 = c.execute("SELECT COUNT(*) FROM referrals WHERE claimed_as LIKE '10gb:%'").fetchone()[0]
        g50 = c.execute("SELECT COUNT(*) FROM referrals WHERE claimed_as='gift50'").fetchone()[0] // 3
        top = c.execute("""SELECT r.referrer_tg, COALESCE(u.display_name,''), COUNT(*), COALESCE(SUM(r.status='paid'),0)
                           FROM referrals r LEFT JOIN users u ON u.tg_id=r.referrer_tg
                           GROUP BY r.referrer_tg ORDER BY 4 DESC, 3 DESC LIMIT 10""").fetchall()
    finally:
        c.close()
    lines = [f"🎁 دعوت‌ها",
             f"وارد شده با لینک دعوت: {joined}",
             f"دعوت‌های منجر به خرید: {paid}",
             f"پاداش گرفته‌شده: {g10} × ۱۰ گیگ، {g50} اکانت ۵۰ گیگ",
             f"امتیاز آماده‌ی برداشت‌نشده: {paid - claimed}", ""]
    if top:
        lines.append("برترین معرف‌ها (وارد شده / خرید):")
        for tg, name, n, p in top:
            lines.append(f"• {name or tg} ({tg}): {n} / {p}")
    return "\n".join(lines)


def buyer_ids_for_reminder():
    c = sqlite3.connect(ORDERS_DB)
    rows = c.execute("""SELECT o.tg_id, COALESCE(u.display_name, o.tg_name, '') FROM orders o
                        LEFT JOIN users u ON u.tg_id=o.tg_id
                        WHERE o.status='approved' AND o.tg_id IS NOT NULL
                        GROUP BY o.tg_id""").fetchall()
    c.close()
    return rows


def buyer_reminder_text(tg_id, name):
    hello = f"سلام {name} 👋" if name else "سلام 👋"
    return (f"{hello}\n"
            "ممنون که HermesVPN رو انتخاب کردی 🙏\n\n"
            "🔁 اگه حجم یا زمانت رو به اتمامه، از «🔁 تمدید سرویس» توی همین ربات با یه کلیک تمدید کن.\n\n"
            "🆕 خبر خوب: از این به بعد معرفی دوستان جایزه داره!\n"
            "هر دوستی که با لینک تو بیاد و خرید کنه:\n"
            "• خودش ۳ گیگ هدیه می‌گیره\n"
            "• تو ۱۰ گیگ رایگان می‌گیری — یا با ۳ دعوت یه اکانت ۵۰ گیگ یک‌ماهه هدیه!\n\n"
            f"🔗 لینک اختصاصی تو:\nhttps://t.me/{SALES_BOT_USERNAME}?start=ref_{tg_id}\n\n"
            "🧪 تست رایگان هم حالا ۵۰۰ مگ / ۲۴ ساعته — برای دوستات بفرست امتحان کنن.\n\n"
            "🚀 جدید: HermesVPN حالا روی ویندوز هم هست! با همین اکانتت (همون یوزرنیم و پسورد) "
            "هم روی گوشی و هم روی کامپیوتر وصل شو — یک کلیک، کل سیستم.\n"
            f"💻 دانلود ویندوز:\n{WINDOWS_SETUP_URL}\n"
            f"📱 دانلود اپ اندروید:\n{APP_DOWNLOAD_URL}\n\n"
            "📢 کانال ما: @YourChannel")


def last_reminder_ts():
    c = sqlite3.connect(ORDERS_DB)
    try:
        row = c.execute("SELECT value FROM settings WHERE key='buyer_reminder_ts'").fetchone()
    finally:
        c.close()
    return int(row[0]) if row else 0


def show_buyer_reminder_preview(chat_id):
    buyers = buyer_ids_for_reminder()
    ago = time.time() - last_reminder_ts()
    warn = (f"\n\n⚠️ آخرین ارسال {int(ago // 3600)} ساعت پیش بوده؛ ارسال زیاد باعث بلاک شدن ربات توسط کاربرها می‌شه."
            if ago < REMINDER_GAP_S else "")
    sample = buyer_reminder_text(123456789, "علی")
    send_message(chat_id,
                 f"📣 این پیام برای {len(buyers)} خریدار از طرف ربات فروش ارسال می‌شه (هر کس لینک دعوت خودش رو می‌گیره):\n"
                 f"――――――――――――――――\n{sample}\n――――――――――――――――{warn}",
                 reply_markup=inline([[{"text": f"✅ ارسال برای {len(buyers)} نفر", "callback_data": "rebuy:send"}],
                                      [{"text": "❌ انصراف", "callback_data": "rebuy:cancel"}]]))


def send_buyer_reminders(admin_chat_id):
    import threading
    def run():
        ok = fail = 0
        for tg_id, name in buyer_ids_for_reminder():
            try:
                send_via_customer_bot(tg_id, buyer_reminder_text(tg_id, name))
                ok += 1
            except Exception as e:
                fail += 1
                print(f"[reminder] {tg_id}: {e}", flush=True)
            time.sleep(0.5)
        c = sqlite3.connect(ORDERS_DB)
        c.execute("INSERT INTO settings (key, value) VALUES ('buyer_reminder_ts', ?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(int(time.time())),))
        c.commit()
        c.close()
        send_message(admin_chat_id, f"📣 یادآوری ارسال شد: {ok} موفق، {fail} ناموفق (احتمالاً ربات را بلاک کرده‌اند).")
    threading.Thread(target=run, daemon=True).start()


def start_create_flow(chat_id):
    STATE[chat_id] = {"flow": "create", "step": "name", "data": {}}
    send_message(chat_id, "اسم کلاینت جدید رو بفرست:")


def page_keyboard(flow, page, has_more, matches):
    dots = client_dot_map(matches)
    buttons = [{"text": f"{dots.get(e, '🔴')} {e}", "callback_data": f"{flow}:pick:{e}"} for e in matches]
    rows = chunk(buttons, 2)
    nav = []
    if page > 0:
        nav.append({"text": "◀️ قبلی", "callback_data": f"{flow}:page:{page-1}"})
    if has_more:
        nav.append({"text": "▶️ بعدی", "callback_data": f"{flow}:page:{page+1}"})
    if nav:
        rows.append(nav)
    return inline(rows)


def start_picker_flow(chat_id, flow, prompt):
    """Shows a paginated button-list (works fine for the current handful of
    clients) but also accepts typed search text — so it stays usable if the
    list ever grows into the hundreds/thousands."""
    if not list_client_emails():
        send_message(chat_id, "هنوز هیچ کلاینتی نساختی.")
        return
    STATE[chat_id] = {"flow": flow, "step": "search", "data": {"page": 0}}
    matches, has_more, total = clients_page(0)
    note = f"\n\n({len(matches)} از {total} — یا اسمی رو تایپ کن تا جستجو کنم)"
    send_message(chat_id, prompt + note, reply_markup=page_keyboard(flow, 0, has_more, matches))


def run_picked_action(chat_id, flow, name, message_id=None):
    if flow == "status":
        show_status(chat_id, name)
        reset_state(chat_id)
    elif flow == "renew":
        STATE[chat_id] = {"flow": "renew", "step": "confirm", "data": {"name": name}}
        send_message(chat_id, renew_confirm_text(name),
                     reply_markup=yesno("renewone", "🔄 بله، تمدید کن"))
    elif flow == "topup":
        STATE[chat_id] = {"flow": "topup", "step": "gb", "data": {"name": name}}
        send_message(chat_id, f"چقدر به {name} اضافه کنم؟", reply_markup=gb_picker("topup"))
    elif flow == "delete":
        STATE[chat_id] = {"flow": "delete", "step": "confirm", "data": {"name": name}}
        send_message(chat_id, f"مطمئنی {name} کامل پاک بشه؟", reply_markup=yesno("delete", "🗑 بله، پاک کن"))
    elif flow == "suspend":
        enabled = client_is_enabled(name)
        if enabled is None:
            send_message(chat_id, f"کلاینتی به اسم {name} پیدا نشد.")
            reset_state(chat_id)
            return
        action = "مسدود" if enabled else "فعال"
        STATE[chat_id] = {"flow": "suspend", "step": "confirm", "data": {"name": name, "target_enabled": not enabled}}
        send_message(chat_id, f"{name} الان {'فعاله' if enabled else 'مسدوده'} — {action} بشه؟", reply_markup=yesno("suspend"))


def handle_search_step(chat_id, flow, query):
    matches, truncated = search_clients(query)
    if not matches:
        # owner 2026-11-20: اگر وسط انتخاب اکانت، جمله یا سؤال گفت (تایپ یا ویس)، یعنی دستور تازه است نه اسم اکانت → دستیار
        if (" " in query.strip() or "?" in query or "؟" in query) and ai_handle(chat_id, query):
            reset_state(chat_id)
            return
        send_message(chat_id, f"کسی با «{query}» پیدا نشد. دوباره امتحان کن یا اسم دقیق‌تر بفرست.")
        return
    if len(matches) == 1:
        run_picked_action(chat_id, flow, matches[0])
        return
    note = f"\n({SEARCH_LIMIT} نتیجه اول، برای باریک‌تر کردن دقیق‌تر بنویس)" if truncated else ""
    send_message(chat_id, f"کدوم یکی؟{note}", reply_markup=client_picker(flow, matches))


# ---------- پنل سفارش‌ها ----------
# تا قبل از این، تنها راه رسیدگی به یک سفارش، دکمه‌های چسبیده به عکس رسید
# بود. اگر آن پیام در تایم‌لاین گم می‌شد یا اصلاً نمی‌رسید، سفارش عملاً
# بی‌صاحب می‌ماند و تنها چاره دست بردن در دیتابیس بود. این پنل همیشه در
# دسترس است و هر سفارش را از هر وضعیتی قابل رسیدگی می‌کند.

ORDERS_PAGE_SIZE = 8

ORDER_STATUS_FA = {
    "awaiting_receipt": ("⏳", "منتظر رسید"),
    "pending_review": ("🔎", "منتظر بررسی"),
    "approved": ("✅", "تأییدشده"),
    "rejected": ("❌", "ردشده"),
    "cancelled": ("🚫", "لغو توسط مشتری"),
}

ORDER_FILTERS = {
    "open": ("معطل", ("awaiting_receipt", "pending_review")),
    "done": ("تأییدشده", ("approved",)),
    "no": ("ردشده", ("rejected",)),
}


def _order_status_bits(status):
    return ORDER_STATUS_FA.get(status, ("•", status or "?"))


def _order_age(created_at):
    if not created_at:
        return "?"
    mins = (time.time() * 1000 - created_at) / 60000
    if mins < 60:
        return f"{mins:.0f} دقیقه"
    if mins < 1440:
        return f"{mins / 60:.0f} ساعت"
    return f"{mins / 1440:.0f} روز"


def orders_page(filt, page):
    """یک صفحه از سفارش‌ها. تازه‌ترین بالا، چون همان‌هایی که رسیدگی
    می‌خواهند تازه‌اند."""
    statuses = ORDER_FILTERS.get(filt, ORDER_FILTERS["open"])[1]
    ph = ",".join("?" * len(statuses))
    c = sqlite3.connect(ORDERS_DB)
    cur = c.cursor()
    cur.execute(f"SELECT COUNT(*) FROM orders WHERE status IN ({ph})", statuses)
    total = cur.fetchone()[0]
    cur.execute(
        f"SELECT id, status, tg_username, tg_name, plan_key, quantity, total_price, "
        f"created_at, target_email FROM orders WHERE status IN ({ph}) "
        f"ORDER BY id DESC LIMIT ? OFFSET ?",
        (*statuses, ORDERS_PAGE_SIZE, page * ORDERS_PAGE_SIZE))
    rows = cur.fetchall()
    c.close()
    return rows, total


def orders_page_text(filt, page, rows, total):
    label = ORDER_FILTERS.get(filt, ORDER_FILTERS["open"])[0]
    if not rows:
        return f"🧾 <b>سفارش‌های {label}</b>\n\nهیچ موردی نیست."
    first = page * ORDERS_PAGE_SIZE + 1
    out = [f"🧾 <b>سفارش‌های {label}</b> — {total} مورد",
           f"نمایش {first} تا {first + len(rows) - 1}", ""]
    for oid, status, uname, name, plan_key, qty, total_price, created, target in rows:
        icon, st = _order_status_bits(status)
        who = f"@{uname}" if uname else (name or "?")
        qty_note = f" ×{qty}" if qty and qty > 1 else ""
        kind = f"تمدید «{target}»" if target else f"{plan_key or '?'}{qty_note}"
        out.append(f"{icon} <b>#{oid}</b> — {esc(who)}")
        out.append(f"    {esc(kind)} — {(total_price or 0):,} تومان — {_order_age(created)} پیش")
    return "\n".join(out)


def orders_keyboard(filt, page, rows, total):
    kb = chunk([{"text": f"{_order_status_bits(r[1])[0]} #{r[0]}",
                 "callback_data": f"ordv:{r[0]}"} for r in rows], 4)
    nav = []
    if page > 0:
        nav.append({"text": "◀️ قبلی", "callback_data": f"ord:{filt}:{page - 1}"})
    if (page + 1) * ORDERS_PAGE_SIZE < total:
        nav.append({"text": "▶️ بعدی", "callback_data": f"ord:{filt}:{page + 1}"})
    if nav:
        kb.append(nav)
    # فیلترِ فعال دکمه نمی‌شود — دکمه‌ای که کاری نمی‌کند فقط جا می‌گیرد.
    kb.append([{"text": f"{'🔹' if f == filt else ''}{lbl}".strip(),
                "callback_data": f"ord:{f}:0"}
               for f, (lbl, _) in ORDER_FILTERS.items() if f != filt])
    kb.append([{"text": "🔄 تازه‌سازی", "callback_data": f"ord:{filt}:{page}"}])
    return inline(kb)


def send_orders_page(chat_id, filt="open", page=0, message_id=None):
    rows, total = orders_page(filt, page)
    text = orders_page_text(filt, page, rows, total)
    kb = orders_keyboard(filt, page, rows, total)
    if message_id:
        try:
            edit_message(chat_id, message_id, text, reply_markup=kb, parse_mode="HTML")
            return
        except Exception:
            pass
    send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")


def _oi_link(tg_id, name, uname):
    try:
        import sys as _sys
        _sys.path.insert(0, "/usr/local/bin")
        import hermesvpn_ordinfo as _oi
        return _oi.customer_link_html(tg_id, name, uname)
    except Exception:
        return esc(f"{name or ''} {uname or ''}")


def order_card(order_id):
    """کارت کامل یک سفارش — همان چیزی که برای تصمیم گرفتن لازم است، بدون
    اینکه لازم باشد جای دیگری را نگاه کنی."""
    c = sqlite3.connect(ORDERS_DB)
    cur = c.cursor()
    cur.execute("SELECT id, tg_id, tg_username, tg_name, plan_key, status, quantity, "
                "total_price, base_price, target_email, client_email, created_at, "
                "decided_at, receipt_tracking, receipt_date, receipt_name, receipt_amount "
                "FROM orders WHERE id=?", (order_id,))
    row = cur.fetchone()
    c.close()
    if not row:
        return None, None
    (oid, tg_id, uname, name, plan_key, status, qty, total_price, base_price,
     target, client_email, created, decided, tracking, rdate, rname, ramount) = row

    icon, st = _order_status_bits(status)
    who = f"@{uname}" if uname else (name or "?")
    qty_note = f" ×{qty}" if qty and qty > 1 else ""
    kind = f"تمدید «{target}»" if target else f"{plan_key or '?'}{qty_note}"

    lines = [f"🧾 <b>سفارش #{oid}</b>", "",
             f"وضعیت: {icon} {st}",
             f"مشتری: {_oi_link(tg_id, name, uname)}",
             f"شناسه تلگرام: <code>{tg_id}</code>",
             f"سفارش: {esc(kind)}",
             f"مبلغ: {(total_price or 0):,} تومان"]
    if base_price and base_price != total_price:
        lines.append(f"قیمت پایه: {base_price:,} تومان")
    if created:
        lines.append("ثبت: " + to_jalali(datetime.fromtimestamp(created / 1000, IRAN_TZ))
                     + f" ({_order_age(created)} پیش)")
    if decided:
        lines.append("تصمیم: " + to_jalali(datetime.fromtimestamp(decided / 1000, IRAN_TZ)))
    if any((tracking, rdate, rname, ramount)):
        lines += ["", "🧾 <b>رسید</b>"]
        if ramount:
            lines.append(f"    مبلغ خوانده‌شده: {ramount:,} تومان")
        if tracking:
            lines.append(f"    کد پیگیری: <code>{esc(str(tracking))}</code>")
        if rdate:
            lines.append(f"    تاریخ: {esc(str(rdate))}")
        if rname:
            lines.append(f"    نام: {esc(str(rname))}")
    try:
        import sys as _sys
        _sys.path.insert(0, "/usr/local/bin")
        import hermesvpn_ordinfo as _oi
        ctx = _oi.order_context(oid)
    except Exception as e:
        print("order_context failed:", e)
        ctx = {"price_lines": [], "steps": [], "accounts": []}
    if ctx["price_lines"]:
        lines += ["", "💡 <b>این مبلغ از کجا آمد</b>"] + [f"    • {esc(x)}" for x in ctx["price_lines"]]
    if ctx["steps"]:
        lines += ["", "👣 <b>کارهای مشتری (به ترتیب زمان)</b>"] + [f"    {esc(x)}" for x in ctx["steps"]]
    if client_email:
        lines += ["", "👤 <b>اکانت‌های ساخته‌شده</b>"]
        for e in str(client_email).split(","):
            if e.strip():
                lines.append(f"    • <code>{esc(e.strip())}</code>")

    rows_kb = []
    if status in ("awaiting_receipt", "pending_review"):
        rows_kb.append([{"text": "✅ تأیید و فعال‌سازی", "callback_data": f"ordact:approve:{oid}"},
                        {"text": "❌ رد کردن", "callback_data": f"ordact:reject:{oid}"}])
    elif status == "approved":
        # ↩️ فقط دفتر را اصلاح می‌کند و به اکانت‌ها دست نمی‌زند — پس گرفتن
        # سرویسی که به مشتری داده شده تصمیم جداگانه‌ای است.
        rows_kb.append([{"text": "↩️ اصلاح (درآمد جعلی)", "callback_data": f"ordask:revert:{oid}"}])
    elif status == "rejected":
        rows_kb.append([{"text": "✅ تأیید و فعال‌سازی", "callback_data": f"ordact:approve:{oid}"}])
    rows_kb.append([{"text": f"🙋 مشتری {(name or uname or str(tg_id))[:20]} — اکانت‌ها و چت", "callback_data": f"cust:{tg_id}"}])
    acc = [{"text": f"👤 اکانت {e[:24]}", "callback_data": f"opencl:{e}"[:64]} for e in ctx["accounts"]]
    for i in range(0, len(acc), 2):
        rows_kb.append(acc[i:i + 2])
    rows_kb.append([{"text": "🔄 تازه‌سازی", "callback_data": f"ordv:{oid}"},
                    {"text": "◀️ برگشت به لیست", "callback_data": "ord:open:0"}])
    return "\n".join(lines), inline(rows_kb)


def customer_from_text(text, forwarded=False):
    """@username (typed alone) or, in a forwarded admin message, the first @username / #order → customer tg_id."""
    t = (text or "").strip()
    m = re.fullmatch(r"@([A-Za-z0-9_]{4,32})", t)
    if not m and forwarded:
        m = re.search(r"@([A-Za-z0-9_]{4,32})", t) or re.search(r"#(\d{1,7})\b", t)
    if not m:
        return None
    try:
        c = sqlite3.connect(ORDERS_DB)
        key = m.group(1)
        if key.isdigit() and m.re.pattern.startswith("#"):
            r = c.execute("SELECT tg_id FROM orders WHERE id=?", (int(key),)).fetchone()
        else:
            r = c.execute("SELECT tg_id FROM orders WHERE lower(tg_username)=lower(?) ORDER BY id DESC LIMIT 1", (key,)).fetchone()
            if not r:
                try:
                    r = c.execute("SELECT tg_id FROM users WHERE lower(username)=lower(?) LIMIT 1", (key,)).fetchone()
                except Exception:
                    r = None
        c.close()
        return r[0] if r and r[0] else None
    except Exception as e:
        print("customer_from_text:", e)
        return None


def show_customer(chat_id, tg_id):
    """پنل مشتری: اکانت‌ها (چراغ آنلاین، حجم، روز مانده)، سفارش‌ها، آخرین کارها، دکمه‌ی چت."""
    try:
        import sys as _sys
        _sys.path.insert(0, "/usr/local/bin")
        import hermesvpn_ordinfo as _oi
        text, rows = _oi.customer_panel(int(tg_id), online=get_online_emails())
        send_message(chat_id, text, reply_markup=inline(rows), parse_mode="HTML")
    except Exception as e:
        send_message(chat_id, f"پنل مشتری باز نشد: {e}")


def show_order(chat_id, order_id, message_id=None):
    text, kb = order_card(order_id)
    if text is None:
        send_message(chat_id, f"سفارش #{order_id} پیدا نشد.", reply_markup=main_menu())
        return
    if message_id:
        try:
            edit_message(chat_id, message_id, text, reply_markup=kb, parse_mode="HTML")
            return
        except Exception:
            pass
    send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")


QA_EMAIL = "qa-test"


def run_qa_check(chat_id):
    """چکاپ کامل اکانت تست کیفیت در پس‌زمینه (حدود ۳۰ ثانیه، به‌خاطر تست از ۸ نقطه‌ی ایران)."""
    send_message(chat_id, "⏳ چکاپ کامل شروع شد (حدود ۳۰ ثانیه)…")
    def _run():
        try:
            r = subprocess.run(["/usr/local/bin/hermesvpn-qa-check.py", "20"], capture_output=True, text=True, timeout=150)
            txt = (r.stdout or r.stderr or "خروجی نداشت")[:4000]
        except Exception as e:
            txt = f"❌ چکاپ انجام نشد: {type(e).__name__}"
        send_message(chat_id, txt, reply_markup=inline([[{"text": "🔁 چکاپ دوباره", "callback_data": "qacheck"}]]))
    threading.Thread(target=_run, daemon=True).start()


def show_qa_account(chat_id):
    c = sqlite3.connect(DB)
    row = c.execute("SELECT sub_id FROM clients WHERE email=?", (QA_EMAIL,)).fetchone()
    c.close()
    if not row:
        send_message(chat_id, "اکانت تست کیفیت پیدا نشد."); return
    send_message(chat_id,
                 "🧪 اکانت تست کیفیت (qa-test) — نامحدود، بدون انقضا، بدون محدودیت دستگاه\n\n"
                 "۱. روی دستگاهی که می‌خواهی تست کنی، با «📲 اتصال به نرم‌افزار» یا لینک ساب زیر وصل شو.\n"
                 "۲. روی همان اینترنتی که می‌خواهی تست کنی (مخابرات، اپراتور، وای‌فای…) چند سایت/ویدیو باز کن.\n"
                 "۳. «🔎 شروع چکاپ کامل» را بزن: دستگاه، اپ، اپراتور و شهر، مسیرهایی که رسیدند، ترافیک و سرعت، سلامت سرور و دسترسی از داخل ایران گزارش می‌شود.\n\n"
                 f"لینک ساب (همه‌ی مسیرها):\n<code>https://{sub_domain_current()}:{SUB_PORT}/sub/{row[0]}</code>",
                 parse_mode="HTML",
                 reply_markup=inline([[{"text": "📲 اتصال به نرم‌افزار", "url": app_account_link(QA_EMAIL)}],
                                      [{"text": "🔗 لینک‌های مستقیم", "callback_data": f"stlinks:{QA_EMAIL}"}],
                                      [{"text": "🔎 شروع چکاپ کامل", "callback_data": "qacheck"}]]))


ACT_PAGE = 12


def actlog_clients():
    """کلاینت‌ها، فعال‌ترین‌ها (آخرین اتصال) اول."""
    c = sqlite3.connect(DB)
    rows = c.execute("""SELECT c.id, c.email, COALESCE(t.last_online, 0) FROM clients c
                        LEFT JOIN client_traffics t ON t.email = c.email
                        ORDER BY COALESCE(t.last_online, 0) DESC, c.email COLLATE NOCASE""").fetchall()
    c.close()
    return rows


def send_actlog_picker(chat_id, page, message_id=None):
    rows = actlog_clients()
    pages = max(1, (len(rows) + ACT_PAGE - 1) // ACT_PAGE)
    page = max(0, min(page, pages - 1))
    dots = client_dot_map([r[1] for r in rows])
    rows.sort(key=lambda r: dots.get(r[1]) != "🔵")  # آنلاین‌ها اول (sort پایدار است)
    chunk = rows[page * ACT_PAGE:(page + 1) * ACT_PAGE]
    lbl = lambda r: f"{dots.get(r[1], '🔴')} {r[1][:28]}"
    kb = [[{"text": lbl(chunk[i]), "callback_data": f"actid:{chunk[i][0]}"}] +
          ([{"text": lbl(chunk[i + 1]), "callback_data": f"actid:{chunk[i + 1][0]}"}] if i + 1 < len(chunk) else [])
          for i in range(0, len(chunk), 2)]
    nav = []
    if page > 0:
        nav.append({"text": "◀️ قبلی", "callback_data": f"actpg:{page - 1}"})
    if page + 1 < pages:
        nav.append({"text": "بعدی ▶️", "callback_data": f"actpg:{page + 1}"})
    if nav:
        kb.append(nav)
    text = (f"🕵️ لاگ فعالیت — کدام اکانت؟ (صفحه {page + 1}/{pages})\n🔵 آنلاین  🔴 آفلاین  🔒 مسدود\n"
            "می‌توانی اسم اکانت را هم تایپ کنی.")
    if message_id:
        edit_message(chat_id, message_id, text, reply_markup=inline(kb))
    else:
        send_message(chat_id, text, reply_markup=inline(kb))


def run_activity_log(chat_id, name, hours):
    send_message(chat_id, f"⏳ دارم لاگ «{name}» را جمع می‌کنم…")
    def _run():
        try:
            r = subprocess.run(["/usr/local/bin/hermesvpn-activity.py", name, str(hours), "--detail"],
                               capture_output=True, text=True, timeout=180)
            out = r.stdout or r.stderr or "خروجی نداشت"
        except Exception as e:
            out = f"❌ لاگ گرفته نشد: {type(e).__name__}"
        fpath = None
        if "@@FILE@@" in out:
            out, fpath = out.split("@@FILE@@", 1)
            fpath = fpath.strip()
        send_message(chat_id, out.strip()[:4000], reply_markup=inline([[{"text": f"🔁 {h} ساعت", "callback_data": f"actlog:{h}"} for h in (1, 6, 24)]]))
        if fpath and os.path.exists(fpath):
            try:
                send_document(chat_id, fpath, caption=f"📄 لاگ ریز «{name}» — جلسه‌های هر اپ، فعالیت ساعت‌به‌ساعت و همه‌ی اتصال‌ها به ترتیب زمان")
            except Exception as e:
                send_message(chat_id, f"فایل لاگ ریز فرستاده نشد: {type(e).__name__}")
            finally:
                try:
                    os.remove(fpath)
                except OSError:
                    pass
    threading.Thread(target=_run, daemon=True).start()


AF_ROUTES = "/home/ubuntu/antifilter/af_routes.py"
AF_WAIT = {}   # chat_id -> "new" | "del"


def af_run(chat_id, *args):
    """مسیرهای اضطراری (نشست ضد فیلتر): خروجی فقط برای صاحب؛ هیچ‌چیز خودکار برای مشتری نمی‌رود."""
    def _run():
        try:
            r = subprocess.run(["sudo", "-u", "ubuntu", AF_ROUTES] + list(args), capture_output=True, text=True, timeout=90)
            out = (r.stdout or r.stderr or "خروجی نداشت").strip()
        except Exception as e:
            out = f"⚠️ اجرا نشد: {type(e).__name__}"
        if BALE_MODE:
            # owner 2026-11-21: در بله نتیجه‌ی مسیرهای اضطراری فقط در دفترچه‌ی خصوصی گوگل، نه در خود چت
            bale_private_note("مسیر اضطراری: " + " ".join(args), out)
            send_message(chat_id, ("⚠️ انجام نشد — جزئیات در دفترچه‌ی خصوصی." if out.startswith("⚠️")
                                   else "✅ انجام شد — نتیجه در دفترچه‌ی خصوصی (Google Docs) ثبت شد."))
            return
        for i in range(0, len(out), 3800):
            send_message(chat_id, out[i:i + 3800])
    threading.Thread(target=_run, daemon=True).start()


def af_list_page(chat_id, message_id=None):
    """فهرست حساب‌های تونل مثل لیست کلاینت‌ها: هر حساب یک دکمه با چراغ وضعیت."""
    def _run():
        try:
            r = subprocess.run(["sudo", "-u", "ubuntu", AF_ROUTES, "dns-list", "--json"], capture_output=True, text=True, timeout=60)
            accs = json.loads(r.stdout or "[]")
        except Exception as e:
            send_message(chat_id, f"⚠️ فهرست گرفته نشد: {type(e).__name__}"); return
        if not accs:
            txt, kb = "🧭 هنوز حساب تونلی برای مشتری ساخته نشده.", [[{"text": "➕ حساب جدید", "callback_data": "afnew"}]]
        else:
            act = sum(1 for a in accs if a.get("status") == "active")
            txt = f"🧭 حساب‌های تونل ({len(accs)} — فعال {act}، متوقف {len(accs) - act}). روی هر حساب بزن:"
            kb = [[{"text": ("🔵 " if a.get("status") == "active" else "⏸ ") + a["name"]
                    + (f" · {a['conns']} اتصال" if a.get("conns") else ""), "callback_data": f"afacc:{a['name']}"}] for a in accs[:60]]
            kb.append([{"text": "➕ حساب جدید", "callback_data": "afnew"}, {"text": "🔄 تازه‌سازی", "callback_data": "aflist"}])
        if message_id:
            try:
                edit_message(chat_id, message_id, txt, reply_markup=inline(kb)); return
            except Exception:
                pass
        send_message(chat_id, txt, reply_markup=inline(kb))
    threading.Thread(target=_run, daemon=True).start()


def af_acc_page(chat_id, message_id, name):
    def _run():
        try:
            r = subprocess.run(["sudo", "-u", "ubuntu", AF_ROUTES, "dns-list", "--json"], capture_output=True, text=True, timeout=60)
            a = next((x for x in json.loads(r.stdout or "[]") if x.get("name") == name), None)
        except Exception:
            a = None
        if not a:
            send_message(chat_id, f"حساب «{name}» پیدا نشد."); return
        fmt = lambda t: datetime.fromtimestamp(t, IRAN_TZ).strftime("%Y-%m-%d %H:%M") if t else "—"
        txt = (f"🧭 حساب تونل «{name}»\nوضعیت: {'🔵 فعال' if a.get('status') == 'active' else '⏸ متوقف'}\n"
               f"ساخت: {fmt(a.get('created'))}\nآخرین ورود: {fmt(a.get('last_seen'))}\n"
               + (f"اتصال الان: {a['conns']}\n" if "conns" in a else ""))
        tog = ({"text": "⏸ توقف", "callback_data": f"afpause:{name}"} if a.get("status") == "active"
               else {"text": "▶️ ادامه", "callback_data": f"afresume:{name}"})
        kb = [[tog, {"text": "⚙️ تنظیمات", "callback_data": f"afshow:{name}"}],
              [{"text": "🔒 فایل قفل‌دار برای اپ", "callback_data": f"afhvpn:{name}"}],
              [{"text": "🗑 حذف", "callback_data": f"afdelask:{name}"}, {"text": "◀️ فهرست", "callback_data": "aflist"}]]
        try:
            edit_message(chat_id, message_id, txt, reply_markup=inline(kb))
        except Exception:
            send_message(chat_id, txt, reply_markup=inline(kb))
    threading.Thread(target=_run, daemon=True).start()


HVPN_MAKER = "/usr/local/bin/hermesvpn-hvpn.py"
HVPN_HOWTO = ("طرز استفاده برای مشتری:\n"
              "۱. اپ HermesVPN Connect (نسخه‌ی ۰.۷.۰ به بعد) را نصب کند.\n"
              "۲. روی همین فایل بزند → «باز کردن با HermesVPN» (یا در اپ: افزودن کانفیگ → از فایل).\n"
              "۳. در فهرست کانفیگ‌ها «مسیر اضطراری» را انتخاب و وصل شود (اتصال تا یک دقیقه طول می‌کشد).\n"
              "فایل قفل است: سرور و رمز دیده نمی‌شود و فقط تا تاریخ انقضا کار می‌کند.")


def hvpn_make(chat_id, name, days):
    """فایل قفل‌دار .hvpn برای یک حساب تونل. تلگرام: خودِ فایل برای صاحب؛ بله: لینک hvpn:// فقط در دفترچه."""
    def _run():
        args = ["owner", str(days)] if name == "owner" else ["make", name, str(days)]
        try:
            r = subprocess.run(["sudo", "-u", "ubuntu", HVPN_MAKER] + args, capture_output=True, text=True, timeout=60)
            d = json.loads(r.stdout or "{}")
        except Exception as e:
            d = {"ok": False, "error": type(e).__name__}
        if not d.get("ok"):
            send_message(chat_id, "⚠️ فایل ساخته نشد: " + str(d.get("error", "خطای نامعلوم"))); return
        until = datetime.fromtimestamp(d["expires"], IRAN_TZ).strftime("%Y-%m-%d")
        head = f"🔒 فایل قفل‌دار مسیر اضطراری «{name}» — اعتبار تا {until} (شناسه {d['id']})"
        if BALE_MODE:
            bale_private_note(f"مسیر اضطراری: dns-hvpn {name}",
                              head + "\n\nلینک (در اپ: افزودن کانفیگ → چسباندن لینک):\n" + d["link"] + "\n\n" + HVPN_HOWTO)
            send_message(chat_id, "✅ فایل قفل‌دار ساخته شد — لینکش در دفترچه‌ی خصوصی (Google Docs) ثبت شد.")
            return
        send_document(chat_id, d["path"], caption=head + "\n\n" + HVPN_HOWTO + "\n\nاین فایل را خودت برای مشتری بفرست.")
    threading.Thread(target=_run, daemon=True).start()


def af_clean_name(t):
    n = re.sub(r"[^a-z0-9]", "", (t or "").lower())
    n = re.sub(r"^[0-9]+", "", n)[:16]
    return n if re.fullmatch(r"[a-z][a-z0-9]{2,15}", n) else None


APP_DL = "/var/lib/hermesvpn-downloads"
APP_STABLE = [("📱 اندروید (بیشتر گوشی‌ها)", "https://app.example.com/app/HermesVPN-Connect.apk"),
              ("📱 اندروید (گوشی قدیمی، armv7)", "https://app.example.com/app/HermesVPN-Connect-armv7.apk"),
              ("💻 ویندوز", "https://app.example.com/app/HermesVPN-Connect-Setup.exe")]
APP_BETA = "https://hook.example.com:2087/dl/"


def apps_download(chat_id):
    """لینک‌های همیشگی اپ‌ها (+ نسخه‌ی آزمایشی اگر از نسخه‌ی منتشرشده جلوتر است). در بله فقط دفترچه."""
    def ver(name, key="versionName"):
        try:
            return json.load(open(os.path.join(APP_DL, name))).get(key, "")
        except Exception:
            return ""
    a_ver, w_ver = ver("HermesVPN-Android-update.json"), ver("HermesVPN-Windows-update.json")
    a_code = ver("HermesVPN-Android-update.json", "versionCode") or 0
    lines = ["📲 دانلود اپ‌های HermesVPN Connect",
             f"نسخه‌ی منتشرشده: اندروید {a_ver or '—'} · ویندوز {w_ver or '—'}", ""]
    for label, url in APP_STABLE:
        lines.append(f"{label}:\n{url}")
    beta = []
    try:
        codes = sorted(int(m.group(1)) for f in os.listdir(APP_DL)
                       for m in [re.fullmatch(r"HermesVPN-Connect-(\d+)\.apk", f)] if m)
        if codes and codes[-1] > int(a_code):
            c = codes[-1]
            beta += [("🧪 اندروید آزمایشی", f"{APP_BETA}HermesVPN-Connect-{c}.apk"),
                     ("🧪 اندروید آزمایشی armv7", f"{APP_BETA}HermesVPN-Connect-armv7-{c}.apk")]
        if os.path.exists(os.path.join(APP_DL, "HermesVPN-Connect-Setup-beta.exe")):
            beta.append(("🧪 ویندوز آزمایشی", f"{APP_BETA}HermesVPN-Connect-Setup-beta.exe"))
    except Exception:
        pass
    if beta:
        lines += ["", "نسخه‌ی آزمایشی (فقط برای تست خودت، اعلام نشده):"] + [f"{l}:\n{u}" for l, u in beta]
    lines += ["", "ℹ️ این لینک‌ها همیشه ثابت‌اند. هر کس یک بار نصب کند، آپدیت‌های بعدی خودکار داخل خود اپ می‌رسد "
              "(حداکثر ۱۵ دقیقه بعد از انتشار) و دیگر لازم نیست لینک نصب دوباره فرستاده شود."]
    text = "\n".join(lines)
    if BALE_MODE:
        bale_private_note("دانلود اپ‌ها", text)
        send_message(chat_id, "✅ لینک‌های دانلود اپ‌ها در دفترچه‌ی خصوصی (Google Docs) ثبت شد.")
        return
    kb = [[{"text": l, "url": u}] for l, u in APP_STABLE + beta]
    send_message(chat_id, text, reply_markup=inline(kb))


def handle_menu_button(chat_id, text):
    AF_WAIT.pop(chat_id, None) if text in (MENU_HOME, "📊 داشبورد") or text in MENU_CATS else None
    if text == "📡 پراکسی تلگرام":
        af_run(chat_id, "mtproto"); return True
    if text == "📲 دانلود اپ‌ها":
        apps_download(chat_id); return True
    if text == "🧭 تونل DNS (حساب من)":
        af_run(chat_id, "dns")
        send_message(chat_id, "برای اپ HermesVPN هم می‌خواهی؟",
                     reply_markup=inline([[{"text": "🔒 فایل قفل‌دار من (تست)", "callback_data": "afhvpn:owner"}]]))
        return True
    if text in ("📋 فهرست حساب‌ها", "🗑 حذف حساب تونل"):
        af_list_page(chat_id); return True
    if text == "➕ حساب تونل برای مشتری":
        AF_WAIT[chat_id] = "new"
        send_message(chat_id, "اسم حساب تونل را بفرست (حروف کوچک انگلیسی و عدد، ۳ تا ۱۶ حرف — معمولاً همان نام اکانت مشتری).\n"
                              "چیزی برای مشتری فرستاده نمی‌شود؛ تنظیمات فقط همین‌جا برای تو می‌آید.")
        return True
    if text == "🗑 حذف حساب تونل":
        AF_WAIT[chat_id] = "del"
        send_message(chat_id, "اسم حساب تونلی که باید حذف شود را بفرست:")
        return True
    if text in MENU_CATS:
        CUR_MENU["cat"] = text
        send_message(chat_id, f"{text} — یکی از گزینه‌ها رو بزن:", reply_markup=main_menu())
        return True
    if text == MENU_HOME:
        CUR_MENU["cat"] = None
        AF_WAIT.pop(chat_id, None)
        # اول خودِ کیبورد منوی اصلی (پیام داشبورد دکمه‌ی شیشه‌ای دارد و کیبورد پایین را عوض نمی‌کند — باگ بله/تلگرام)
        send_message(chat_id, "🏠 منوی اصلی", reply_markup=main_menu())
        show_dashboard(chat_id)
        return True
    if text == "📊 داشبورد":
        show_dashboard(chat_id)
        return True
    if text == "🛠 رفع کلی مشکل":
        send_message(chat_id, "🛠 بررسی و رفع مشکل شروع شد (حدود ۳۰ ثانیه)…")
        def _heal():
            try:
                r = subprocess.run(["/usr/local/bin/hermesvpn-heal.py"], capture_output=True, text=True, timeout=600)
                out = (r.stdout or r.stderr or "خروجی نداشت")
            except Exception as e:
                out = f"❌ اجرا نشد: {type(e).__name__}: {e}"
            for i in range(0, len(out), 3800):
                send_message(chat_id, out[i:i + 3800])
        threading.Thread(target=_heal, daemon=True).start()
        return True
    if text == "💬 گفتگوهای اخیر":
        show_recent_support(chat_id)
        return True
    if text == "🕵️ لاگ فعالیت اکانت":
        STATE[chat_id] = {"flow": "actlog", "step": "name", "data": {}}
        send_actlog_picker(chat_id, 0)
        return True
    if text == "🧪 اکانت تست کیفیت":
        show_qa_account(chat_id)
        return True
    if text == "📈 نمودار ترافیک سرور":
        # same renderer as the weekly Friday chart; runs in the background so the bot stays responsive
        send_message(chat_id, "⏳ دارم نمودار ترافیک را می‌سازم…")
        def _run():
            try:
                subprocess.run(["/usr/local/bin/hermesvpn-egress-chart.sh"], check=True, timeout=180,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            except Exception as e:
                send_message(chat_id, f"❌ نمودار ساخته نشد: {type(e).__name__}")
        threading.Thread(target=_run, daemon=True).start()
        return True
    if text == "➕ کلاینت جدید":
        start_create_flow(chat_id)
        return True
    if text == "📡 لاگ مانیتورینگ":
        show_monitor_log(chat_id)
        return True
    if text == "📋 لیست کلاینت‌ها":
        send_clients_page(chat_id)
        return True
    if text == "🧾 سفارش‌ها":
        send_orders_page(chat_id)
        return True
    if text == "🔒 لیست مسدودی‌ها":
        send_blocked_list(chat_id)
        return True
    if text == "📊 گزارش مالی":
        show_financial_menu(chat_id)
        return True
    if text == "🎡 لاگ گردونه‌ی شانس":
        send_message(chat_id, wheel_log_text(), reply_markup=main_menu(), parse_mode="HTML")
        return True
    if text == "🎁 دعوت‌ها":
        send_message(chat_id, referral_report_text(), reply_markup=main_menu())
        return True
    if text == "📣 یادآوری به خریداران":
        show_buyer_reminder_preview(chat_id)
        return True
    if text == "⚙️ تنظیمات پرداخت خودکار":
        send_message(chat_id, auto_settings_text(), reply_markup=auto_toggle_keyboard())
        return True
    if text == "🔍 وضعیت کلاینت":
        start_picker_flow(chat_id, "status", "کدوم کلاینت رو می‌خوای چک کنی؟")
        return True
    if text == "🔄 تمدید":
        start_picker_flow(chat_id, "renew", "کدوم کلاینت رو تمدید کنم؟ (همون حجم قبلی، ۳۰ روز از الان، مصرف صفر می‌شه)")
        return True
    if text == "➕ افزایش حجم/زمان":
        start_picker_flow(chat_id, "topup", "به کدوم کلاینت اضافه کنم؟")
        return True
    if text == "🗑 حذف کلاینت":
        start_picker_flow(chat_id, "delete", "کدوم کلاینت رو پاک کنم؟")
        return True
    if text == "🔒 مسدود/فعال کلاینت":
        start_picker_flow(chat_id, "suspend", "کدوم کلاینت رو مسدود یا فعال کنم؟ (شناسه‌ی اکانت رو از گزارش مالی یا سفارش پیدا کن)")
        return True
    if text == "🌐 رفع فیلترینگ":
        STATE[chat_id] = {"flow": "rotate", "step": "confirm", "data": {}}
        send_message(chat_id, "دامنه رو بچرخونم؟ (لینک‌های subscription قبلی خودکار آپدیت می‌شن)", reply_markup=yesno("rotate"))
        return True
    if text == "🔧 نگهداری سرور":
        send_message(chat_id, "کاری که می‌خوای انجام بشه رو انتخاب کن:",
                     reply_markup=maintenance_keyboard())
        return True
    if text == "🏓 پینگ سرور":
        send_message(chat_id, "در حال تست شبکه سرور... چند لحظه صبر کن.")
        send_message(chat_id, check_server_ping(), reply_markup=main_menu())
        return True
    if text == "📡 وضعیت کامل سیستم":
        send_message(chat_id, "در حال بررسی همه‌چیز... چند لحظه صبر کن.")
        send_message(chat_id, check_full_system(), reply_markup=main_menu())
        return True
    if text == "📦 ساخت انبوه":
        STATE[chat_id] = {"flow": "bulkcreate", "step": "name", "data": {}}
        send_message(chat_id, "اسم پایه رو بفرست (مثلاً «مرسد» → مرسد1، مرسد2، ...):")
        return True
    if text == "🔄 تمدید انبوه":
        STATE[chat_id] = {"flow": "bulkrenew", "step": "prefix", "data": {}}
        send_message(chat_id, "اسم پایه رو بفرست (مثلاً «مرسد» → همه‌ی مرسد1، مرسد2، ... تمدید می‌شن):")
        return True
    if text == "🗑 حذف انبوه":
        send_message(chat_id, "چطور انتخاب کنم؟", reply_markup=inline([
            [{"text": "☑️ انتخاب از لیست (تیک بزن)", "callback_data": "multidel:start"}],
            [{"text": "🔤 با اسم پایه (مثلاً مرسد۱ تا مرسد۱۰)", "callback_data": "bulkdelmode:prefix"}],
        ]))
        return True
    if text == "❓ راهنما" or text in ("/start", "/help"):
        send_message(chat_id, HELP_TEXT, reply_markup=main_menu())
        return True
    return False


def finish_create(chat_id, name, gb, days):
    try:
        link, qr = create_client(name, gb, days)
        vol_txt = "حجم نامحدود" if not gb else f"{gb:g}GB"
        dur_txt = "بدون انقضا" if not days else f"{days:g} روز"
        send_photo(chat_id, qr,
                   f"کلاینت: {name}\nپلن: {vol_txt} / {dur_txt}\nلینک اشتراک:\n{link}",
                   reply_markup=main_menu())
        try:
            send_direct_links(chat_id, name)    # همان مسیرهای چندگانه‌ی خریدارها + لینک اتصال به اپ
        except Exception as e:
            print("[create] direct links failed:", e, flush=True)
    except Exception as e:
        send_message(chat_id, f"خطا: {e}", reply_markup=main_menu())
    reset_state(chat_id)


def handle_text(chat_id, text):
    stripped = text.strip()
    CUR_REQ.update(text=stripped[:200], ts=time.time())

    if stripped == SUPPORT_END_BUTTON:
        who = SUPPORT_TARGET.pop(chat_id, None)
        send_message(chat_id,
                     f"گفتگو با مشتری {who} بسته شد." if who else "گفتگوی بازی نداشتی.",
                     reply_markup=main_menu())
        return

    if handle_menu_button(chat_id, stripped):
        return

    if chat_id in AF_WAIT:
        mode = AF_WAIT.pop(chat_id)
        name = af_clean_name(stripped) if " " not in stripped.strip() else None
        if not name and ai_handle(chat_id, stripped):
            return
        if not name:
            send_message(chat_id, "اسم معتبر نیست (حروف کوچک انگلیسی و عدد، با حرف شروع شود، ۳ تا ۱۶ حرف). دوباره از منو بزن.")
            return
        if mode == "new":
            send_message(chat_id, f"⏳ ساخت حساب تونل «{name}»…")
            af_run(chat_id, "dns-new", name)
        else:
            send_message(chat_id, f"حساب تونل «{name}» حذف و اتصال‌هایش قطع شود؟",
                         reply_markup=inline([[{"text": "✅ بله، حذف کن", "callback_data": f"afdel:{name}"},
                                               {"text": "↩️ نه", "callback_data": "noop"}]]))
        return

    state = STATE.get(chat_id)
    if state:
        flow, step = state["flow"], state["step"]
        if flow == "actlog" and step == "name":
            name = stripped
            c = sqlite3.connect(DB)
            row = c.execute("SELECT email FROM clients WHERE email=? COLLATE NOCASE", (name,)).fetchone()
            c.close()
            if not row:
                send_message(chat_id, f"اکانتی به اسم «{name}» پیدا نشد. دوباره بفرست یا از منو چیز دیگری بزن.")
                return
            STATE[chat_id] = {"flow": "actlog", "step": "range", "data": {"name": row[0]}}
            send_message(chat_id, f"لاگ «{row[0]}» برای چه بازه‌ای؟",
                         reply_markup=inline([[{"text": f"{h} ساعت اخیر", "callback_data": f"actlog:{h}"} for h in (1, 6, 24)]]))
            return

        if flow == "strename" and step == "label":
            # اسم دلخواه در همان جدولی می‌نشیند که ربات فروش می‌خواند، پس
            # مشتری هم همان را می‌بیند.
            new_label = stripped[:40]
            target = state["data"]["name"]
            STATE.pop(chat_id, None)
            try:
                c = sqlite3.connect(ORDERS_DB)
                c.execute("INSERT INTO client_labels (email,label,updated_at) VALUES (?,?,?) "
                          "ON CONFLICT(email) DO UPDATE SET label=excluded.label, "
                          "updated_at=excluded.updated_at",
                          (target, new_label, int(time.time() * 1000)))
                c.commit()
                c.close()
                send_message(chat_id, f"✅ اسم «{esc(target)}» شد «{esc(new_label)}».",
                             parse_mode="HTML")
            except Exception as e:
                send_message(chat_id, f"❌ ذخیره نشد: {type(e).__name__}: {e}")
            show_status(chat_id, target)
            return
        if flow == "create" and step == "name":
            state["data"]["name"] = stripped
            state["step"] = "gb"
            send_message(chat_id, f"باشه، «{stripped}». حالا حجم رو انتخاب کن:", reply_markup=gb_picker("create"))
            return
        if flow == "create" and step == "gb_custom":
            val = to_number(stripped)
            if val is None:
                send_message(chat_id, "یه عدد بفرست، مثلاً 15")
                return
            state["data"]["gb"] = val
            state["step"] = "days"
            send_message(chat_id, "حالا مدت رو انتخاب کن:", reply_markup=days_picker("create"))
            return
        if flow == "create" and step == "days_custom":
            val = to_number(stripped)
            if val is None:
                send_message(chat_id, "یه عدد بفرست، مثلاً 45")
                return
            finish_create(chat_id, state["data"]["name"], state["data"]["gb"], val)
            return
        if flow == "finrange" and step == "start":
            dt = parse_date_iran_midnight(stripped)
            if not dt:
                send_message(chat_id, "فرمت درست نیست. مثال شمسی: 1405-06-01")
                return
            state["data"]["start"] = dt
            state["step"] = "end"
            send_message(chat_id, "حالا تاریخ پایان بازه رو بفرست (شمسی، همون روز هم توی گزارش حساب می‌شه):")
            return
        if flow == "finrange" and step == "end":
            dt_end = parse_date_iran_midnight(stripped)
            if not dt_end:
                send_message(chat_id, "فرمت درست نیست. مثال شمسی: 1405-06-15")
                return
            start_dt = state["data"]["start"]
            report = format_range_report(start_dt, dt_end)
            reset_state(chat_id)
            j_start = to_jalali_date(start_dt).replace("/", "-")
            j_end = to_jalali_date(dt_end).replace("/", "-")
            send_message(chat_id, report, reply_markup=inline([pdf_buttons(f"range:{j_start}:{j_end}")]), parse_mode="HTML")
            return
        if flow == "finsearch" and step == "query":
            reset_state(chat_id)
            rows = search_transactions(stripped)
            if not rows:
                send_message(chat_id, f"چیزی با «{stripped}» پیدا نشد.", reply_markup=main_menu())
                return
            text = f"🔎 <b>نتایج جستجوی «{esc(stripped)}»</b>\n{DIVIDER}\n\n" + "\n\n".join(format_transaction(r) for r in rows)
            send_message(chat_id, text, reply_markup=main_menu(), parse_mode="HTML")
            return
        if flow == "topup" and step == "gb_custom":
            val = to_number(stripped)
            if val is None:
                send_message(chat_id, "یه عدد بفرست")
                return
            state["data"]["gb"] = val
            state["step"] = "days"
            send_message(chat_id, "زمان اضافه هم می‌خوای؟", reply_markup=days_picker("topup"))
            return
        if flow == "topup" and step == "days_custom":
            val = to_number(stripped)
            if val is None:
                send_message(chat_id, "یه عدد بفرست")
                return
            state["data"]["days"] = val
            finish_topup(chat_id, state["data"])
            return
        if step == "search":
            handle_search_step(chat_id, flow, stripped)
            return

        if flow == "bulkcreate" and step == "name":
            state["data"]["base_name"] = stripped
            state["step"] = "mode"
            send_message(chat_id, f"باشه، اسم پایه: «{stripped}». چطور شماره‌گذاری کنم؟", reply_markup=bulk_mode_picker())
            return
        if flow == "bulkcreate" and step == "range_start":
            val = to_number(stripped)
            if val is None:
                send_message(chat_id, "یه عدد بفرست، مثلاً 1")
                return
            state["data"]["range_start"] = int(val)
            state["step"] = "range_end"
            send_message(chat_id, "تا چه شماره‌ای؟ (عدد پایانی رو بفرست)")
            return
        if flow == "bulkcreate" and step == "range_end":
            val = to_number(stripped)
            if val is None:
                send_message(chat_id, "یه عدد بفرست، مثلاً 50")
                return
            lo, hi = sorted((state["data"]["range_start"], int(val)))
            state["data"]["numbers"] = list(range(lo, hi + 1))
            state["step"] = "gb"
            send_message(chat_id, f"باشه، {len(state['data']['numbers'])} تا کلاینت. حالا حجم رو انتخاب کن:", reply_markup=gb_picker("bulkcreate"))
            return
        if flow == "bulkcreate" and step == "count":
            val = to_number(stripped)
            if val is None or val <= 0:
                send_message(chat_id, "یه عدد بفرست، مثلاً 20")
                return
            state["data"]["numbers"] = get_next_number_range(state["data"]["base_name"], int(val))
            state["step"] = "gb"
            send_message(chat_id, f"باشه، {int(val)} تا کلاینت. حالا حجم رو انتخاب کن:", reply_markup=gb_picker("bulkcreate"))
            return
        if flow == "bulkcreate" and step == "gb_custom":
            val = to_number(stripped)
            if val is None:
                send_message(chat_id, "یه عدد بفرست")
                return
            state["data"]["gb"] = val
            state["step"] = "days"
            send_message(chat_id, "حالا مدت رو انتخاب کن:", reply_markup=days_picker("bulkcreate"))
            return
        if flow == "bulkcreate" and step == "days_custom":
            val = to_number(stripped)
            if val is None:
                send_message(chat_id, "یه عدد بفرست")
                return
            confirm_or_run_bulk_create(chat_id, state["data"]["base_name"], state["data"]["numbers"], state["data"]["gb"], val)
            return

        if flow == "bulkrenew" and step == "prefix":
            matches = clients_matching_prefix(stripped)
            if not matches:
                send_message(chat_id, f"کسی با پیشوند «{stripped}» پیدا نشد.", reply_markup=main_menu())
                reset_state(chat_id)
                return
            STATE[chat_id] = {"flow": "bulkrenew", "step": "confirm", "data": {"emails": matches}}
            preview = "، ".join(matches[:5]) + ("، ..." if len(matches) > 5 else "")
            send_message(chat_id, f"{len(matches)} کلاینت پیدا شد ({preview})\nهمه رو تمدید کنم؟ (همون حجم قبلی، 30 روز از الان، مصرف صفر می‌شه)", reply_markup=yesno("bulkrenewpfx"))
            return
        if flow == "bulkdeletepfx" and step == "prefix":
            matches = clients_matching_prefix(stripped)
            if not matches:
                send_message(chat_id, f"کسی با پیشوند «{stripped}» پیدا نشد.", reply_markup=main_menu())
                reset_state(chat_id)
                return
            STATE[chat_id] = {"flow": "bulkdeletepfx", "step": "confirm", "data": {"emails": matches}}
            preview = "، ".join(matches[:5]) + ("، ..." if len(matches) > 5 else "")
            send_message(chat_id, f"⚠️ {len(matches)} کلاینت پیدا شد ({preview})\nمطمئنی همه‌شون کامل پاک بشن؟", reply_markup=yesno("bulkdelpfx", "🗑 بله، همه رو پاک کن"))
            return

    # "لیست" / "list" with an optional filter word, e.g. "لیست مرسد" — shows only
    # matching clients instead of the full roster (also scales past 1000 clients)
    m = re.match(r"^(?:/list|لیست|list)\s*(.*)$", stripped, re.IGNORECASE)
    if m:
        query = m.group(1).strip()
        send_clients_page(chat_id, query or None)
        return

    # bulk create/delete: "mersad34 ta mersad54" (range) or
    # "mersad 20 ta account" (count, continues numbering automatically)
    bulk = parse_bulk(stripped)
    if bulk:
        action, base_name, numbers = bulk
        if action == "bulk_create":
            _, gb, days = extract_amounts(stripped)
            do_bulk_create(chat_id, base_name, numbers, gb or 20, days or 30)
        else:
            do_bulk_delete_confirm(chat_id, base_name, numbers)
        return

    # fallback: free-form natural language (backward compatible)
    # گفتگوی باز پشتیبانی: وقتی ادمین گفتگویی باز کرده، متن آزادش پاسخ به
    # مشتری است نه دستور. عمداً *قبل* از parse_intent است — اولین بار این را
    # آخر گذاشته بودم و نتیجه‌اش این شد که «پینگت تموم شد» به‌عنوان نام
    # کلاینت تفسیر شد و جواب «کلاینتی به این اسم پیدا نشد» آمد.
    # بعد از handle_menu_button و STATE است، پس منو و جریان‌های چندمرحله‌ای
    # همچنان کار می‌کنند.
    target = SUPPORT_TARGET.get(chat_id)
    # حالت «پاسخ به مشتری» بعد از ۳۰ دقیقه بی‌کاری خودش بسته شود (owner 2026-11-17: بعد از ذخیره‌ی دائمی این حالت،
    # از ۱۵ نوامبر باز مانده بود و هر متن ادمین به‌جای دستیار هوشمند به سمت مشتری می‌رفت)
    try:
        _age = time.time() - os.path.getmtime(_PersistDict.PATH)
    except Exception:
        _age = 0
    if target and _age > 1800:
        SUPPORT_TARGET.pop(chat_id, None)
        send_message(chat_id, f"ℹ️ گفتگوی پشتیبانی با مشتری {target} به‌خاطر ۳۰ دقیقه بی‌کاری بسته شد؛ این پیامت به دستیار رفت.")
        target = None
    if target:
        try:
            os.utime(_PersistDict.PATH, None)   # هر پاسخ، مهلت ۳۰ دقیقه را تازه می‌کند
        except Exception:
            pass
        try:
            send_via_customer_bot(target, f"👤 پشتیبانی:\n\n{stripped}")
            log_support_message(target, "out", stripped)
            send_message(chat_id, f"✅ برای مشتری {target} فرستاده شد.",
                         reply_markup=support_reply_keyboard())
        except Exception as e:
            send_message(chat_id, f"❌ ارسال نشد: {type(e).__name__}: {e}",
                         reply_markup=support_reply_keyboard())
        return

    if ai_handle(chat_id, stripped):
        return

    intent, name, gb, days = parse_intent(stripped)

    if intent == "rotate":
        do_rotate(chat_id)
        return
    if intent == "create":
        finish_create(chat_id, name, gb, days)
        return
    if intent == "renew":
        if not name:
            send_message(chat_id, "اسم کلاینت رو متوجه نشدم.")
            return
        STATE[chat_id] = {"flow": "renew", "step": "confirm",
                          "data": {"name": name, "gb": gb, "days": days}}
        send_message(chat_id, renew_confirm_text(name, gb, days),
                     reply_markup=yesno("renewone", "🔄 بله، تمدید کن"))
        return
    if intent == "topup":
        finish_topup(chat_id, {"name": name, "gb": gb, "days": days})
        return
    if intent == "delete":
        if not name:
            send_message(chat_id, "اسم کلاینت رو متوجه نشدم.")
            return
        STATE[chat_id] = {"flow": "delete_confirm_text", "step": "confirm", "data": {"name": name}}
        send_message(chat_id, f"مطمئنی می‌خوای {name} رو کامل پاک کنی؟", reply_markup=yesno("delete", "🗑 بله، پاک کن"))
        return
    if intent == "status":
        if not name:
            send_message(chat_id, "اسم کلاینت رو متوجه نشدم.")
            return
        show_status(chat_id, name)
        return

    send_message(chat_id, "متوجه نشدم. از منو استفاده کن یا " + HELP_TEXT, reply_markup=main_menu())


def parse_bulk(text):
    """Returns ("bulk_create"|"bulk_delete", base_name, [numbers]) or None."""
    is_delete = bool(re.search(DELETE_WORDS, text, re.IGNORECASE))

    m = BULK_RANGE_RE.search(text)
    if m:
        base_name, n1, n2 = m.group(1), int(m.group(2)), int(m.group(3))
        lo, hi = (n1, n2) if n1 <= n2 else (n2, n1)
        return ("bulk_delete" if is_delete else "bulk_create"), base_name, list(range(lo, hi + 1))

    m = BULK_COUNT_RE.search(text)
    if m and not is_delete:  # "delete N accounts of X" is ambiguous — require an explicit range for deletes
        base_name, count = m.group(1), int(m.group(2))
        return "bulk_create", base_name, get_next_number_range(base_name, count)

    return None


def do_bulk_create(chat_id, base_name, numbers, gb, days):
    send_message(chat_id, f"در حال ساخت {len(numbers)} کلاینت ({base_name}{numbers[0]} تا {base_name}{numbers[-1]})، پلن {gb:g}GB/{days:g} روز...")
    try:
        created, skipped = bulk_create(base_name, numbers, gb, days)
    except Exception as e:
        send_message(chat_id, f"خطا: {e}", reply_markup=main_menu())
        return
    domain = sub_domain_current()
    lines = [f"{email}: https://{domain}:{SUB_PORT}/sub/{sub_id}" for email, sub_id in created]
    header = f"{len(created)} کلاینت ساخته شد."
    if skipped:
        header += f" ({len(skipped)} تا از قبل وجود داشتن و رد شدن: {', '.join(skipped)})"
    send_message(chat_id, header, reply_markup=main_menu())
    for i in range(0, len(lines), 25):
        send_message(chat_id, "\n".join(lines[i:i + 25]))


BULK_CONFIRM_THRESHOLD = 10


def confirm_or_run_bulk_create(chat_id, base_name, numbers, gb, days):
    if len(numbers) > BULK_CONFIRM_THRESHOLD:
        STATE[chat_id] = {"flow": "bulkcreateconfirm", "step": "confirm",
                           "data": {"base_name": base_name, "numbers": numbers, "gb": gb, "days": days}}
        send_message(chat_id,
                     f"⚠️ داری {len(numbers)} کلاینت می‌سازی ({base_name}{numbers[0]} تا {base_name}{numbers[-1]}, "
                     f"پلن {gb:g}GB/{days:g} روز). مطمئنی؟",
                     reply_markup=yesno("bulkcreateconfirm"))
        return
    reset_state(chat_id)
    do_bulk_create(chat_id, base_name, numbers, gb, days)


def do_bulk_delete_confirm(chat_id, base_name, numbers):
    STATE[chat_id] = {"flow": "bulk_delete", "step": "confirm", "data": {"base_name": base_name, "numbers": numbers}}
    send_message(chat_id, f"مطمئنی می‌خوای {len(numbers)} کلاینت ({base_name}{numbers[0]} تا {base_name}{numbers[-1]}) کامل پاک بشن؟", reply_markup=yesno("bulkdelete", "🗑 بله، همه رو پاک کن"))


def do_rotate(chat_id):
    send_message(chat_id, "در حال چرخش دامنه... چند لحظه صبر کن.")
    try:
        new_sub = rotate_domain()
    except Exception as e:
        send_message(chat_id, f"خطا در چرخش دامنه: {e}", reply_markup=main_menu())
        return

    send_message(chat_id, f"انجام شد. دامنه جدید: {new_sub}\nلینک‌های subscription قبلی خودکار آپدیت شدن.\nدر حال اطلاع‌رسانی به کلاینت‌های معتبر...")
    try:
        sent, failed, total = notify_valid_clients_after_rotation()
        note = f" ({failed} تا ناموفق)" if failed else ""
        send_message(chat_id,
                     f"به {sent} از {total} کلاینت معتبر و متصل‌به‌تلگرام، پیام رفع فیلترینگ + لینک سالم فرستاده شد.{note}\n\n"
                     f"⚠️ کلاینت‌هایی که هنوز تلگرامشون به اکانتشون وصل نشده (با «🔄 دریافت دوباره‌ی لینک» توی ربات فروش) این پیام خودکار رو نمی‌گیرن.",
                     reply_markup=main_menu())
    except Exception as e:
        send_message(chat_id, f"چرخش دامنه انجام شد ولی اطلاع‌رسانی به کلاینت‌ها خطا داد: {e}", reply_markup=main_menu())


def do_renew(chat_id, name, gb, days):
    result = renew_client(name, gb, days)
    if result is None:
        send_message(chat_id, f"کلاینتی به اسم {name} پیدا نشد.", reply_markup=main_menu())
        return
    total_bytes, expiry_ms = result
    remain_days = int(-expiry_ms / 86400000) if expiry_ms < 0 else int((expiry_ms - time.time() * 1000) / 86400000)
    send_message(chat_id, f"{name} تمدید شد.\nپلن: {total_bytes/1024**3:g}GB / {remain_days} روز باقی‌مانده\nمصرف قبلی صفر شد.", reply_markup=main_menu())


def finish_topup(chat_id, data):
    name, gb, days = data.get("name"), data.get("gb"), data.get("days")
    if not name:
        send_message(chat_id, "اسم کلاینت رو متوجه نشدم.", reply_markup=main_menu())
        reset_state(chat_id)
        return
    result = topup_client(name, gb, days)
    reset_state(chat_id)
    if result is None:
        send_message(chat_id, f"کلاینتی به اسم {name} پیدا نشد.", reply_markup=main_menu())
        return
    total_bytes, expiry_ms = result
    remain_days = int(-expiry_ms / 86400000) if expiry_ms < 0 else int((expiry_ms - time.time() * 1000) / 86400000)
    send_message(chat_id, f"{name} آپدیت شد.\nسقف حجم الان: {total_bytes/1024**3:g}GB\n{remain_days} روز تا انقضا.", reply_markup=main_menu())


MONITOR_LOG = "/var/log/hermesvpn-monitor.jsonl"


def show_monitor_log(chat_id, limit=25):
    """آخرین رویدادهای پایش، تازه‌ترین بالا.

    قبلاً هر مسدودی fail2ban یک پیام جدا می‌شد و تایم‌لاین ربات مدیریت را
    پر می‌کرد. حالا همه اینجا جمع‌اند و فقط چیزهایی که رسیدگی می‌خواهند
    پیام می‌شوند.
    """
    try:
        with open(MONITOR_LOG, encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
    except FileNotFoundError:
        send_message(chat_id, "📡 هنوز رویدادی ثبت نشده.", reply_markup=main_menu())
        return
    except Exception as e:
        send_message(chat_id, f"❌ لاگ خوانده نشد: {type(e).__name__}: {e}",
                     reply_markup=main_menu())
        return

    recs = []
    for l in lines[-limit:]:
        try:
            recs.append(json.loads(l))
        except Exception:
            continue
    if not recs:
        send_message(chat_id, "📡 هنوز رویدادی ثبت نشده.", reply_markup=main_menu())
        return

    icon = {"problem": "🔴", "recovered": "🟢", "routine": "🛡", "info": "•"}
    counts = {}
    out = [f"📡 <b>لاگ مانیتورینگ</b> — {len(recs)} رویداد آخر", DIVIDER, ""]
    for r in reversed(recs):
        lvl = r.get("level", "info")
        counts[lvl] = counts.get(lvl, 0) + 1
        when = to_jalali(datetime.fromtimestamp(r.get("t", 0), IRAN_TZ))
        txt = esc(r.get("text", ""))
        out.append(f"{icon.get(lvl, '•')} {when}\n   {txt}")
    out += ["", DIVIDER,
            "مشکل: %d | برطرف‌شده: %d | روال عادی: %d" % (
                counts.get("problem", 0), counts.get("recovered", 0), counts.get("routine", 0))]

    text = "\n".join(out)
    # سقف پیام تلگرام؛ اگر بلند شد از انتها می‌بریم تا تازه‌ترین‌ها بمانند
    if len(text) > 3800:
        text = text[:3800] + "\n…"
    send_message(chat_id, text, reply_markup=inline([[
        {"text": "🔄 تازه‌سازی", "callback_data": "monlog"},
        {"text": "🧹 پاک کردن لاگ", "callback_data": "monlogclear"},
    ]]), parse_mode="HTML")


def show_status(chat_id, name):
    result = status_client(name)
    if result is None:
        send_message(chat_id, f"کلاینتی به اسم {name} پیدا نشد.", reply_markup=main_menu())
        return
    st = result
    label, orders = client_extra_info(name)

    vol = ("حجم کل: نامحدود" if not st["total_b"]
           else f"حجم کل: {fmt_size(st['total_b'])}\n"
                f"حجم مصرف‌شده: {fmt_size(st['used_b'])}\n"
                f"حجم باقی‌مانده: {fmt_size(st['remain_b'])}")

    lines = [f"شناسه اکانت: <code>{esc(name)}</code>"]
    if label:
        lines.append(f"اسم دلخواه مشتری: {esc(label)}")
    try:
        import sys as _sys
        _sys.path.insert(0, "/usr/local/bin")
        import hermesvpn_ordinfo as _oi
        _par, _kids = _oi.account_family(name)
        if _par:
            _short = name[len(_par) + 1:] if name.startswith(_par + "-") else name
            lines.append(f"🌿 زیراکانت: <b>{esc(_par)} - {esc(_short)}</b> (ساخته‌شده از اکانت {esc(_par)})")
        if _kids:
            lines.append("🌿 زیراکانت‌ها: " + "، ".join(esc(k) for k in _kids))
    except Exception as _e:
        print("account_family:", _e)
    lines.append(f"اتصال: {st['online_state']}")
    lines.append(f"وضعیت: {'فعال ✅' if st['enable'] else 'مسدود 🔒'}")
    lines.append(vol)
    lines.append(f"زمان باقی‌مانده: {st['remain_txt']}")
    lines.append(f"مالک تلگرام: {st['tg_id'] if st['tg_id'] else 'وصل نشده'}")
    if st["created_at"]:
        lines.append("تاریخ ساخت: " + to_jalali(
            datetime.fromtimestamp(st["created_at"] / 1000, IRAN_TZ)))
    lines.append("لینک اشتراک:\n<code>"
                 f"https://{sub_domain_current()}:{SUB_PORT}/sub/{st['sub_id']}</code>")

    if orders:
        lines.append("\nخریدهای این اکانت:")
        for oid, dec, price, plan in orders:
            when = to_jalali(datetime.fromtimestamp((dec or 0) / 1000, IRAN_TZ)) if dec else "?"
            lines.append(f"  #{oid} — {esc(plan or '?')} — {(price or 0):,} تومان — {when}")
    else:
        lines.append("\nخریدی ثبت نشده (احتمالاً دستی ساخته شده).")

    # دکمه‌ها همین‌جا، تا مسدود/رفع‌مسدودی یک لمس فاصله داشته باشد نه یک منوی
    # کامل. هر دو callback از قبل وجود دارند و به همان مسیر تأیید می‌روند.
    toggle_txt = "✅ رفع مسدودی" if not st["enable"] else "🔒 مسدود کن"
    rows = [
        [{"text": toggle_txt, "callback_data": f"suspend:pick:{name}"}],
        [{"text": "🗑 حذف کلاینت", "callback_data": f"delete:pick:{name}"}],
        [{"text": "🔄 تازه‌سازی", "callback_data": f"status:pick:{name}"}],
        [{"text": "🔁 تمدید همین اکانت", "callback_data": f"strenew:{name}"},
         {"text": "✏️ تغییر اسم", "callback_data": f"strename:{name}"}],
    ]
    # اتصال به اکانت (همه‌ی مسیرها + به‌روزرسانی خودکار)، مثل ربات فروش — نه یک کانفیگ ثابت
    try:
        rows.append([{"text": "📲 اتصال به نرم‌افزار", "url": app_account_link(name)},
                     {"text": "🔗 لینک‌های مستقیم", "callback_data": f"stlinks:{name}"}])
    except Exception as e:
        print("[card] app link failed:", e, flush=True)
    rows.append([{"text": "⬇️ دانلود HermesVPN Connect", "url": APP_DOWNLOAD_URL},
                 {"text": "💻 نسخه‌ی ویندوز", "url": WINDOWS_SETUP_URL}])
    if st["tg_id"]:
        rows.append([{"text": "✉️ پیام به کلاینت", "callback_data": f"supreply:{st['tg_id']}"},
                     {"text": "💬 سابقه‌ی گفتگو", "callback_data": f"suphist:{st['tg_id']}"}])
        rows.append([{"text": "🎁 گزارش دعوت و هدیه‌ها", "callback_data": f"refrep:{st['tg_id']}"}])
    else:
        # بدون مالک تلگرام هم دکمه‌ها دیده شوند؛ با زدنشان توضیح داده می‌شود چرا پیام به کسی نمی‌رسد
        rows.append([{"text": "✉️ پیام به کلاینت", "callback_data": "supnoown"},
                     {"text": "💬 سابقه‌ی گفتگو", "callback_data": "supnoown"}])
    rows.append([{"text": "◀️ برگشت به لیست", "callback_data": "stback"}])
    kb = inline(rows)
    send_message(chat_id, "\n".join(lines), reply_markup=kb, parse_mode="HTML")


# ---------- callback (inline button) handling ----------

def referral_user_report(tg_id):
    """Per-customer referral log for the admin card (tables written by the sales bot)."""
    c = sqlite3.connect(ORDERS_DB)
    try:
        have = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "referrals" not in have:
            return "🎁 هنوز هیچ دعوتی در سیستم ثبت نشده."
        name = (c.execute("SELECT display_name FROM users WHERE tg_id=?", (tg_id,)).fetchone() or [""])[0]
        ev = {}
        if "ref_events" in have:
            ev = dict(c.execute("SELECT kind, COUNT(*) FROM ref_events WHERE tg_id=? GROUP BY kind", (tg_id,)).fetchall())
            last = c.execute("SELECT MAX(ts) FROM ref_events WHERE tg_id=? AND kind IN ('share','menu','inline_open')",
                             (tg_id,)).fetchone()[0]
        else:
            last = None
        rows = c.execute("""SELECT r.referee_tg, COALESCE(u.display_name,''), r.created_at, r.status, r.paid_order_id,
                                   r.claimed, COALESCE(r.claimed_as,'')
                            FROM referrals r LEFT JOIN users u ON u.tg_id=r.referee_tg
                            WHERE r.referrer_tg=? ORDER BY r.created_at DESC""", (tg_id,)).fetchall()
        mine = c.execute("SELECT referrer_tg, status, paid_order_id FROM referrals WHERE referee_tg=?", (tg_id,)).fetchone()
    finally:
        c.close()
    jd = lambda ms: to_jalali(datetime.fromtimestamp(ms / 1000, IRAN_TZ)) if ms else "?"
    paid = sum(1 for r in rows if r[3] == "paid")
    claimed = sum(1 for r in rows if r[5])
    g10 = sum(1 for r in rows if r[6].startswith("10gb:"))
    g50 = sum(1 for r in rows if r[6] == "gift50") // 3
    L = [f"🎁 گزارش دعوت — {esc(name) or tg_id} ({tg_id})", "",
         f"📤 ارسال پوستر دعوت برای دیگران: {ev.get('share', 0)} بار",
         f"🔎 باز کردن پنجره‌ی ارسال: {ev.get('inline_open', 0)} بار | باز کردن منوی دعوت: {ev.get('menu', 0)} بار",
         f"👥 وارد شده با لینکش: {len(rows)} نفر | خرید کرده: {paid} نفر",
         f"🏆 امتیاز: {paid} کسب‌شده | {claimed} برداشت‌شده | {paid - claimed} آماده",
         f"🎁 هدیه‌های دریافتی: {g10} × ۱۰ گیگ" + (f" | {g50} اکانت ۵۰ گیگ" if g50 else ""),
         f"🕒 آخرین فعالیت دعوت: {jd(last) if last else '—'}"]
    if mine:
        L.append(f"\n↩️ خودش دعوت‌شده‌ی {mine[0]} است — " +
                 (f"۳ گیگ هدیه با سفارش #{mine[2]} گرفت" if mine[1] == "paid" else "هنوز خرید نکرده (۳ گیگ هدیه در انتظار)"))
    if rows:
        L.append("\n📜 لاگ دعوت‌شده‌ها (جدیدترین اول):")
        for ref, nm, ts, stt, oid, cl, cas in rows[:20]:
            st_txt = (f"خرید کرد (سفارش #{oid})" if stt == "paid" else "فقط وارد شد")
            cl_txt = (" — پاداش: " + ("۱۰ گیگ به " + esc(cas.split(':', 1)[1]) if cas.startswith("10gb:") else "اکانت ۵۰ گیگ")) if cl else ""
            L.append(f"• {esc(nm) or ref} ({ref}) — {jd(ts)} — {st_txt}{cl_txt}")
        if len(rows) > 20:
            L.append(f"… و {len(rows) - 20} نفر دیگر")
    return "\n".join(L)


def handle_callback(chat_id, callback_id, data, message_id):
    CUR_REQ.update(text=data[:120], ts=time.time())
    if data.startswith("refrep:"):
        answer_callback(callback_id)
        try:
            send_message(chat_id, referral_user_report(int(data.split(":", 1)[1])), parse_mode="HTML")
        except Exception as e:
            send_message(chat_id, f"گزارش دعوت ساخته نشد: {e}")
        return

    if data.startswith("rebuy:"):
        answer_callback(callback_id)
        if data == "rebuy:send" and chat_id in ALLOWED_CHAT_IDS:
            edit_message(chat_id, message_id, "📣 ارسال شروع شد… نتیجه رو همینجا می‌فرستم.")
            send_buyer_reminders(chat_id)
        else:
            edit_message(chat_id, message_id, "ارسال لغو شد.")
        return

    if data.startswith("ord:"):
        _, filt, page = data.split(":", 2)
        answer_callback(callback_id)
        send_orders_page(chat_id, filt, int(page), message_id=message_id)
        return

    if data.startswith("modblock:") or data.startswith("modlift:"):
        answer_callback(callback_id)
        act, tg = data.split(":", 1); tg = int(tg)
        if act == "modlift":
            c = sqlite3.connect(ORDERS_DB)
            c.execute("UPDATE moderation SET restricted_until=0 WHERE tg_id=?", (tg,)); c.commit(); c.close()
            send_message(chat_id, f"✅ محدودیت پشتیبانی مشتری {tg} برداشته شد.")
        else:
            x = sqlite3.connect(DB); emails = [r[0] for r in x.execute("SELECT email FROM clients WHERE tg_id=? AND enable=1", (tg,))]; x.close()
            if not emails:
                send_message(chat_id, "اکانت فعالی به این مشتری وصل نیست."); return
            send_message(chat_id, f"🔒 همه‌ی اکانت‌های VPN این مشتری مسدود شوند؟\n{', '.join(emails)}",
                         reply_markup=inline([[{"text": "✅ بله، مسدود کن", "callback_data": f"modblockok:{tg}"},
                                               {"text": "↩️ نه", "callback_data": "noop"}]]))
        return

    if data.startswith("aip:"):
        # تصمیم ادمین روی پیشنهاد پشتیبانی هوشمند (owner 2026-11-14)
        answer_callback(callback_id)
        _, pid, verdict = data.split(":", 2)
        c = sqlite3.connect(ORDERS_DB)
        row = c.execute("SELECT tg_id, action, params, msg_ok, msg_no, status FROM ai_proposals WHERE id=?", (int(pid),)).fetchone()
        if not row or row[5] != "open":
            c.close(); send_message(chat_id, "این مورد قبلاً تعیین‌تکلیف شده."); return
        tg, act, params, msg_ok, msg_no, _st = row
        prm = json.loads(params or "{}")
        result = ""
        try:
            if verdict == "ok":
                if act in ("approve_order", "reject_order") and prm.get("order_id"):
                    res = customer_order_action(str(prm["order_id"]), "approve" if act == "approve_order" else "reject")
                    result = ("✅ " if res.get("ok") else "⚠️ ") + (res.get("text") or "")
                elif act == "topup" and prm.get("account"):
                    r = topup_client(prm["account"], prm.get("gb"), prm.get("days"))
                    result = "✅ شارژ شد" if r else "⚠️ اکانت پیدا نشد"
                msg = msg_ok
            else:
                msg = msg_no
            if msg:
                send_via_customer_bot(int(tg), msg)
                log_support_message(int(tg), "out", msg)
            c.execute("UPDATE ai_proposals SET status=? WHERE id=?", ("approved" if verdict == "ok" else "rejected", int(pid)))
            c.commit()
        except Exception as e:
            result = f"❌ خطا: {type(e).__name__}: {e}"
        c.close()
        try:
            api_call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}})
        except Exception:
            pass
        send_message(chat_id, ("✅ تأیید شد" if verdict == "ok" else "❌ رد شد") + (f" — {result}" if result else "")
                     + ("\nپیام برای مشتری فرستاده شد." if msg else ""))
        return

    if data.startswith("aiok:") or data.startswith("aino:"):
        answer_callback(callback_id)
        pid = data.split(":", 1)[1]
        if data.startswith("aino:"):
            AI_PENDING.pop(pid, None)
            edit_message(chat_id, message_id, "❌ پیشنهاد لغو شد.")
        else:
            edit_message(chat_id, message_id, "⏳ در حال انجام…")
            try:
                ai_execute(chat_id, pid)
            except Exception as e:
                send_message(chat_id, f"❌ انجام نشد: {type(e).__name__}: {e}")
        return

    if data == "aflist":
        answer_callback(callback_id); af_list_page(chat_id, message_id); return
    if data == "afnew":
        answer_callback(callback_id)
        AF_WAIT[chat_id] = "new"
        send_message(chat_id, "اسم حساب تونل را بفرست (حروف کوچک انگلیسی و عدد، ۳ تا ۱۶ حرف):")
        return
    if data.startswith(("afacc:", "afpause:", "afresume:", "afshow:", "afdelask:")):
        answer_callback(callback_id)
        act, name = data.split(":", 1)
        name = af_clean_name(name)
        if not name:
            return
        if act == "afacc":
            af_acc_page(chat_id, message_id, name)
        elif act in ("afpause", "afresume"):
            def _t():
                r = subprocess.run(["sudo", "-u", "ubuntu", AF_ROUTES, "dns-pause" if act == "afpause" else "dns-resume", name],
                                   capture_output=True, text=True, timeout=90)
                o = (r.stdout or r.stderr or "").strip() or "انجام شد"
                if BALE_MODE:
                    bale_private_note(f"مسیر اضطراری: {act} {name}", o)
                    o = "⚠️ انجام نشد — جزئیات در دفترچه‌ی خصوصی." if o.startswith("⚠️") else "✅ انجام شد (جزئیات در دفترچه‌ی خصوصی)."
                send_message(chat_id, o)
                af_acc_page(chat_id, message_id, name)
            threading.Thread(target=_t, daemon=True).start()
        elif act == "afshow":
            af_run(chat_id, "dns-show", name)
        else:
            send_message(chat_id, f"حساب تونل «{name}» کامل حذف و اتصال‌هایش قطع شود؟",
                         reply_markup=inline([[{"text": "✅ بله، حذف کن", "callback_data": f"afdel:{name}"},
                                               {"text": "↩️ نه", "callback_data": "noop"}]]))
        return

    if data.startswith("afhvpn:"):
        answer_callback(callback_id)
        name = data.split(":", 1)[1]
        name = "owner" if name == "owner" else af_clean_name(name)
        if name:
            send_message(chat_id, f"🔒 فایل قفل‌دار «{name}» چند روز اعتبار داشته باشد؟",
                         reply_markup=inline([[{"text": f"{d} روز", "callback_data": f"afhvpnd:{name}:{d}"} for d in (7, 30, 90)]]))
        return
    if data.startswith("afhvpnd:"):
        answer_callback(callback_id)
        _, name, days = (data.split(":") + ["", ""])[:3]
        name = "owner" if name == "owner" else af_clean_name(name)
        if name and days.isdigit():
            edit_message(chat_id, message_id, f"⏳ ساخت فایل قفل‌دار «{name}» ({days} روز)…")
            hvpn_make(chat_id, name, int(days))
        return

    if data.startswith("afdel:"):
        answer_callback(callback_id)
        name = af_clean_name(data.split(":", 1)[1])
        if name:
            edit_message(chat_id, message_id, f"⏳ حذف حساب تونل «{name}»…")
            af_run(chat_id, "dns-del", name)
        return

    if data == "noop":
        answer_callback(callback_id)
        edit_message(chat_id, message_id, "باشه، کاری انجام نشد.")
        return

    if data.startswith("modblockok:"):
        answer_callback(callback_id)
        tg = int(data.split(":", 1)[1])
        x = sqlite3.connect(DB); emails = [r[0] for r in x.execute("SELECT email FROM clients WHERE tg_id=? AND enable=1", (tg,))]; x.close()
        done = [e for e in emails if set_client_enabled(e, False)]
        send_message(chat_id, "🔒 مسدود شد: " + ("، ".join(done) if done else "هیچ"))
        return

    if data == "dash":
        answer_callback(callback_id)
        try:
            edit_message(chat_id, message_id, dashboard_text(), parse_mode="HTML",
                         reply_markup=inline([[{"text": "🧾 سفارش‌های منتظر", "callback_data": "ord:open:0"},
                                               {"text": "🔄 تازه‌سازی", "callback_data": "dash"}]]))
        except Exception:
            show_dashboard(chat_id)
        return

    if data.startswith("cust:"):
        answer_callback(callback_id)
        show_customer(chat_id, data.split(":", 1)[1])
        return

    if data.startswith("opencl:"):
        # از کارت سفارش / عکس رسید / خبر واریز: کارت کامل اکانت را در یک پیام تازه باز کن (پیام سفارش دست نمی‌خورد)
        answer_callback(callback_id)
        show_status(chat_id, data.split(":", 1)[1])
        return

    if data.startswith("ordv:"):
        answer_callback(callback_id)
        show_order(chat_id, int(data.split(":", 1)[1]), message_id=message_id)
        return

    if data.startswith("ordask:"):
        # «اصلاح» گزارش مالی را عوض می‌کند، پس یک تأیید می‌گیرد. تأیید و رد
        # عمداً این مرحله را ندارند چون خودشان جواب برمی‌گردانند و در پنل
        # دیده می‌شوند؛ این یکی چیزی است که آدم به‌ندرت می‌زند.
        _, act, oid = data.split(":", 2)
        answer_callback(callback_id)
        send_message(chat_id,
                     f"سفارش #{oid} را از دفتر بردارم؟\n"
                     "مبلغش دیگر در گزارش مالی درآمد حساب نمی‌شود.\n"
                     "اکانت‌های ساخته‌شده دست‌نخورده می‌مانند.",
                     reply_markup=inline([
                         [{"text": "↩️ بله، اصلاح کن", "callback_data": f"ordact:{act}:{oid}"}],
                         [{"text": "انصراف", "callback_data": f"ordv:{oid}"}]]))
        return

    if data.startswith("ordact:"):
        _, act, oid = data.split(":", 2)
        answer_callback(callback_id, "در حال انجام…")
        try:
            res = customer_order_action(oid, act)
            mark = "✅" if res.get("ok") else "⚠️"
            text = res.get("text") or ("انجام شد" if res.get("ok") else "انجام نشد")
        except Exception as e:
            mark, text = "❌", f"{type(e).__name__}: {e}"
        send_message(chat_id, f"{mark} {text}")
        show_order(chat_id, int(oid))
        return

    if data == "monlog":
        show_monitor_log(chat_id)
        return

    if data == "monlogclear":
        try:
            open(MONITOR_LOG, "w").close()
            send_message(chat_id, "🧹 لاگ مانیتورینگ پاک شد.", reply_markup=main_menu())
        except Exception as e:
            send_message(chat_id, f"❌ پاک نشد: {type(e).__name__}: {e}")
        return

    if data.startswith("actpg:"):
        answer_callback(callback_id)
        send_actlog_picker(chat_id, int(data.split(":", 1)[1]), message_id)
        return

    if data.startswith("actid:"):
        answer_callback(callback_id)
        c = sqlite3.connect(DB)
        row = c.execute("SELECT email FROM clients WHERE id=?", (int(data.split(":", 1)[1]),)).fetchone()
        c.close()
        if not row:
            send_message(chat_id, "این اکانت دیگر وجود ندارد."); return
        STATE[chat_id] = {"flow": "actlog", "step": "range", "data": {"name": row[0]}}
        send_message(chat_id, f"لاگ «{row[0]}» برای چه بازه‌ای؟",
                     reply_markup=inline([[{"text": f"{h} ساعت اخیر", "callback_data": f"actlog:{h}"} for h in (1, 6, 24)]]))
        return

    if data.startswith("actlog:"):
        answer_callback(callback_id)
        st_ = STATE.get(chat_id) or {}
        name = (st_.get("data") or {}).get("name")
        if not name:
            send_message(chat_id, "اول از منو «🕵️ لاگ فعالیت اکانت» را بزن و اسم اکانت را بفرست."); return
        run_activity_log(chat_id, name, int(data.split(":", 1)[1]))
        return

    if data == "qacheck":
        answer_callback(callback_id)
        run_qa_check(chat_id)
        return

    if data.startswith("stlinks:"):
        answer_callback(callback_id)
        send_direct_links(chat_id, data.split(":", 1)[1])
        return

    if data == "stback":
        send_clients_page(chat_id)
        return

    if data.startswith("strenew:"):
        name = data.split(":", 1)[1]
        STATE[chat_id] = {"flow": "renew", "step": "confirm", "data": {"name": name}}
        send_message(chat_id, renew_confirm_text(name, None, None),
                     reply_markup=yesno("renewone", "🔄 بله، تمدید کن"))
        return

    if data.startswith("strename:"):
        name = data.split(":", 1)[1]
        STATE[chat_id] = {"flow": "strename", "step": "label", "data": {"name": name}}
        send_message(chat_id, f"اسم دلخواه تازه برای «{esc(name)}» رو بفرست. /cancel برای لغو.",
                     parse_mode="HTML")
        return

    if data.startswith("approve:") or data.startswith("reject:"):
        action, _, raw = data.partition(":")
        try:
            res = customer_order_action(raw, action)
            text = res.get("text") or ("انجام شد" if res.get("ok") else "انجام نشد")
            mark = "✅" if res.get("ok") else "⚠️"
        except Exception as e:
            text, mark = f"{type(e).__name__}: {e}", "❌"
        try:
            edit_caption(chat_id, message_id, f"{mark} {text}")
        except Exception:
            send_message(chat_id, f"{mark} {text}")
        return

    if data == "supnoown":
        answer_callback(callback_id)
        send_message(chat_id, "این اکانت به تلگرامِ هیچ مشتری‌ای وصل نیست («مالک تلگرام: وصل نشده»)، "
                              "پس پیام به کسی نمی‌رسد و سابقه‌ای هم ندارد.\n\n"
                              "وقتی صاحبش از ربات فروش خرید/تمدید کند یا در ربات «📶 وضعیت سرویس من» را بزند و اکانت را وصل کند، "
                              "این دو دکمه برای همین کارت فعال می‌شوند.")
        return

    if data.startswith(("suphist:", "supdel:", "supdx:", "supclr:", "supclrok:")):
        answer_callback(callback_id)
        p = data.split(":")
        try:
            if p[0] == "suphist":
                tg, page = int(p[1]), (int(p[2]) if len(p) > 2 else -1)
                text, kb = admin_history_view(tg, max(page, 0))
                if page < 0:      # from a card / relay message: open as a new message
                    send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")
                else:
                    edit_message(chat_id, message_id, text, reply_markup=kb, parse_mode="HTML")
            elif p[0] == "supdel":
                text, kb = admin_history_view(int(p[1]), int(p[2]), delete_mode=True)
                edit_message(chat_id, message_id, text, reply_markup=kb, parse_mode="HTML")
            elif p[0] == "supdx":
                rid, tg, page = int(p[1]), int(p[2]), int(p[3])
                c = sqlite3.connect(ORDERS_DB)
                c.execute("DELETE FROM support_messages WHERE id=? AND tg_id=?", (rid, tg)); c.commit(); c.close()
                text, kb = admin_history_view(tg, page, delete_mode=True)
                edit_message(chat_id, message_id, text, reply_markup=kb, parse_mode="HTML")
            elif p[0] == "supclr":
                tg = int(p[1])
                edit_message(chat_id, message_id, f"کل سابقه‌ی گفتگو با {tg} پاک شود؟ (برگشت‌پذیر نیست)",
                             reply_markup={"inline_keyboard": [[{"text": "🗑 بله، پاک کن", "callback_data": f"supclrok:{tg}"},
                                                                {"text": "انصراف", "callback_data": f"suphist:{tg}:0"}]]})
            elif p[0] == "supclrok":
                tg = int(p[1])
                c = sqlite3.connect(ORDERS_DB)
                c.execute("DELETE FROM support_messages WHERE tg_id=?", (tg,)); c.commit(); c.close()
                text, kb = admin_history_view(tg, 0)
                edit_message(chat_id, message_id, "✅ سابقه پاک شد.\n\n" + text, reply_markup=kb, parse_mode="HTML")
        except Exception as e:
            send_message(chat_id, f"خطا: {type(e).__name__}: {e}")
        return

    if data.startswith("supreply:"):
        try:
            SUPPORT_TARGET[chat_id] = int(data.split(":", 1)[1])
            STATE.pop(chat_id, None)  # پاسخ به مشتری: حالت جستجو/فرم قبلی پاک شود
        except ValueError:
            send_message(chat_id, "شناسه‌ی مشتری خوانده نشد.")
            return
        send_message(chat_id,
                     f"↩️ گفتگو با مشتری {SUPPORT_TARGET[chat_id]} باز شد.\n"
                     "از حالا هر چه بنویسی مستقیم برای او می‌رود.",
                     reply_markup=support_reply_keyboard())
        return

    answer_callback(callback_id)
    parts = data.split(":")
    prefix = parts[0]
    state = STATE.get(chat_id)

    if prefix == "clist":
        page = int(parts[1])
        st = STATE.get(chat_id)
        query = st["data"].get("query") if st and st.get("flow") == "clist" else None
        text, emails, has_more, dots = clients_list_page(query, page)
        STATE[chat_id] = {"flow": "clist", "step": "browse",
                          "data": {"query": query, "page": page}}
        edit_message(chat_id, message_id, text,
                     reply_markup=clients_list_keyboard(emails, page, has_more, dots))
        return

    if prefix == "blist":
        page = int(parts[1])
        text, emails, has_more, dots = clients_list_page(None, page, only_blocked=True)
        STATE[chat_id] = {"flow": "blist", "step": "browse", "data": {"page": page}}
        edit_message(chat_id, message_id, text,
                     reply_markup=clients_list_keyboard(emails, page, has_more, dots, nav_prefix="blist"))
        return

    if prefix == "bulkdelmode":
        STATE[chat_id] = {"flow": "bulkdeletepfx", "step": "prefix", "data": {}}
        edit_message(chat_id, message_id,
                     "اسم پایه رو بفرست (مثلاً «مرسد» → همه‌ی مرسد1، مرسد2، ... پاک می‌شن):")
        return

    if prefix == "multidel":
        action = parts[1]
        if action == "start":
            emails = list_client_emails()
            if not emails:
                edit_message(chat_id, message_id, "کلاینتی وجود نداره.")
                return
            STATE[chat_id] = {"flow": "multidel", "step": "select",
                              "data": {"emails": emails, "selected": [], "page": 0}}
            data = STATE[chat_id]["data"]
            edit_message(chat_id, message_id, multidel_text(data),
                         reply_markup=multidel_keyboard(data))
            return

        st = STATE.get(chat_id)
        if not st or st.get("flow") != "multidel":
            edit_message(chat_id, message_id, "این لیست منقضی شده — دوباره از منو شروع کن.")
            return
        data = st["data"]

        if action == "tog":
            email = ":".join(parts[2:])
            sel = data.setdefault("selected", [])
            if email in sel:
                sel.remove(email)
            else:
                sel.append(email)
            edit_message(chat_id, message_id, multidel_text(data),
                         reply_markup=multidel_keyboard(data))
            return
        if action == "page":
            data["page"] = int(parts[2])
            edit_message(chat_id, message_id, multidel_text(data),
                         reply_markup=multidel_keyboard(data))
            return
        if action == "cancel":
            edit_message(chat_id, message_id, "لغو شد.")
            send_message(chat_id, "منو:", reply_markup=main_menu())
            reset_state(chat_id)
            return
        if action == "go":
            sel = data.get("selected", [])
            if not sel:
                edit_message(chat_id, message_id, multidel_text(data),
                             reply_markup=multidel_keyboard(data))
                return
            st["step"] = "confirm"
            preview = "، ".join(sel[:15]) + ("..." if len(sel) > 15 else "")
            edit_message(chat_id, message_id,
                         f"⚠️ مطمئنی این {len(sel)} کلاینت کامل پاک بشن؟\n\n{preview}\n\n"
                         "این کار برگشت‌پذیر نیست.",
                         reply_markup=yesno("multidelgo", "🗑 بله، همه رو پاک کن"))
            return
        return

    if prefix == "multidelgo":
        st = STATE.get(chat_id)
        sel = st["data"].get("selected", []) if st and st.get("flow") == "multidel" else []
        if parts[1] == "yes" and sel:
            edit_message(chat_id, message_id, f"در حال حذف {len(sel)} کلاینت...")
            done, missing = 0, []
            for e in sel:
                if delete_client(e, restart=False):
                    done += 1
                else:
                    missing.append(e)
            if done:
                subprocess.run(["systemctl", "restart", "x-ui"], check=True)
            msg = f"✅ {done} کلاینت پاک شد."
            if missing:
                msg += "\n⚠️ پیدا نشد: " + "، ".join(missing[:10])
            send_message(chat_id, msg, reply_markup=main_menu())
        else:
            edit_message(chat_id, message_id, "لغو شد.")
            send_message(chat_id, "منو:", reply_markup=main_menu())
        reset_state(chat_id)
        return

    if prefix == "unlimgo":
        pending = state["data"] if state and state.get("flow") == "unlim" else None
        if parts[2] == "yes" and pending and pending.get("name"):
            name = pending["name"]
            edit_message(chat_id, message_id, "در حال اعمال...")
            res = make_unlimited(name, float(parts[1]))
            if res is None:
                send_message(chat_id, f"کلاینتی به اسم {name} پیدا نشد.",
                             reply_markup=main_menu())
            else:
                _, exp = res
                send_message(chat_id,
                             f"{name} نامحدود شد.\n"
                             f"حجم کل: نامحدود\n"
                             f"زمان باقی‌مانده: {remaining_time_fa(exp)}",
                             reply_markup=main_menu())
        else:
            edit_message(chat_id, message_id, "لغو شد.")
            send_message(chat_id, "منو:", reply_markup=main_menu())
        reset_state(chat_id)
        return

    if prefix == "renewone":
        pending = state["data"] if state and state.get("flow") == "renew" else None
        if parts[1] == "yes" and pending and pending.get("name"):
            edit_message(chat_id, message_id, f"در حال تمدید {pending['name']}...")
            do_renew(chat_id, pending["name"], pending.get("gb"), pending.get("days"))
        else:
            edit_message(chat_id, message_id, "لغو شد.")
            send_message(chat_id, "منو:", reply_markup=main_menu())
        reset_state(chat_id)
        return

    if prefix == "maint":
        action = parts[1]
        # فقط ری‌استارت‌ها تأیید می‌خواهند؛ health/logs/res فقط می‌خوانند.
        if action in ("sales", "xui", "admin"):
            edit_message(chat_id, message_id,
                         f"{MAINTENANCE_ACTIONS[action][0]}\n\nسرویس چند ثانیه قطع می‌شه.",
                         reply_markup=yesno(f"maintdo:{action}", "♻️ بله، ری‌استارت کن"))
            return
        edit_message(chat_id, message_id, "🔧 نگهداری سرور")
        do_maintenance(chat_id, action)
        return

    if prefix == "maintdo":
        if parts[2] == "yes":
            edit_message(chat_id, message_id, "🔧 در حال اجرا...")
            do_maintenance(chat_id, parts[1])
        else:
            edit_message(chat_id, message_id, "لغو شد.")
            send_message(chat_id, "منو:", reply_markup=main_menu())
        return

    if prefix == "rotate":
        if parts[1] == "yes":
            edit_message(chat_id, message_id, "در حال چرخش دامنه...")
            do_rotate(chat_id)
        else:
            edit_message(chat_id, message_id, "لغو شد.")
        reset_state(chat_id)
        return

    # شرط parts[1] لازم است: بدون آن، این هندلر «delete:pick:<email>» را هم
    # می‌گرفت (دکمه‌ی حذف روی کارت وضعیت) و چون parts[1] برابر "yes" نبود،
    # به‌جای پرسیدن، «لغو شد» می‌داد و کلاینت هیچ‌وقت به مرحله‌ی تأیید نمی‌رسید.
    # هندلر suspend از اول همین محافظ را داشت؛ delete نداشت.
    if prefix == "delete" and parts[1] in ("yes", "no"):
        pending_name = None
        if state and state.get("flow") in ("delete", "delete_confirm_text"):
            pending_name = state["data"].get("name")
        if parts[1] == "yes" and pending_name:
            ok = delete_client(pending_name)
            edit_message(chat_id, message_id, f"{pending_name} پاک شد." if ok else f"کلاینتی به اسم {pending_name} پیدا نشد.")
            send_message(chat_id, "منو:", reply_markup=main_menu())
        elif parts[1] == "yes":
            # «بله» زده شده ولی حافظه‌ی جریان رفته — یعنی پیام قدیمی است.
            edit_message(chat_id, message_id,
                         "این پیام قدیمیه و دیگه معتبر نیست — دوباره از منو شروع کن.")
            send_message(chat_id, "منو:", reply_markup=main_menu())
        else:
            edit_message(chat_id, message_id, "لغو شد.")
        reset_state(chat_id)
        return

    if prefix == "suspend" and parts[1] in ("yes", "no"):
        pending = state["data"] if state and state.get("flow") == "suspend" else None
        if parts[1] == "yes" and pending:
            name, target_enabled = pending["name"], pending["target_enabled"]
            ok = set_client_enabled(name, target_enabled)
            verb = "فعال" if target_enabled else "مسدود"
            edit_message(chat_id, message_id, f"{name} {verb} شد." if ok else f"کلاینتی به اسم {name} پیدا نشد.")
            send_message(chat_id, "منو:", reply_markup=main_menu())
        else:
            edit_message(chat_id, message_id, "لغو شد.")
        reset_state(chat_id)
        return

    if prefix == "bulkcreateconfirm":
        pending = state["data"] if state and state.get("flow") == "bulkcreateconfirm" else None
        if parts[1] == "yes" and pending:
            edit_message(chat_id, message_id, "در حال ساخت...")
            reset_state(chat_id)
            do_bulk_create(chat_id, pending["base_name"], pending["numbers"], pending["gb"], pending["days"])
        else:
            edit_message(chat_id, message_id, "لغو شد.")
            reset_state(chat_id)
        return

    if prefix == "bulkrenewpfx":
        pending = state["data"] if state and state.get("flow") == "bulkrenew" else None
        if parts[1] == "yes" and pending:
            edit_message(chat_id, message_id, "در حال تمدید...")
            renewed = bulk_renew_by_prefix(pending["emails"])
            send_message(chat_id, f"{len(renewed)} کلاینت تمدید شد.", reply_markup=main_menu())
        else:
            edit_message(chat_id, message_id, "لغو شد.")
        reset_state(chat_id)
        return

    if prefix == "bulkdelpfx":
        pending = state["data"] if state and state.get("flow") == "bulkdeletepfx" else None
        if parts[1] == "yes" and pending:
            edit_message(chat_id, message_id, "در حال حذف...")
            deleted = bulk_delete_by_prefix(pending["emails"])
            send_message(chat_id, f"{len(deleted)} کلاینت پاک شد.", reply_markup=main_menu())
        else:
            edit_message(chat_id, message_id, "لغو شد.")
        reset_state(chat_id)
        return

    if prefix == "bulkcreate" and state and state.get("flow") == "bulkcreate":
        if parts[1] == "mode":
            if parts[2] == "range":
                state["step"] = "range_start"
                edit_message(chat_id, message_id, "از چه شماره‌ای شروع کنم؟ (عدد ابتدایی رو بفرست)")
            else:
                state["step"] = "count"
                edit_message(chat_id, message_id, "چند تا کلاینت بسازم؟ (یه عدد بفرست)")
            return
        if parts[1] == "gb":
            state["data"]["gb"] = float(parts[2])
            state["step"] = "days"
            edit_message(chat_id, message_id, f"حجم: {parts[2]} گیگ. حالا مدت رو انتخاب کن:", reply_markup=days_picker("bulkcreate"))
            return
        if parts[1] == "gbcustom":
            state["step"] = "gb_custom"
            edit_message(chat_id, message_id, "عدد حجم (گیگ) رو بفرست:")
            return
        if parts[1] == "days":
            edit_message(chat_id, message_id, "در حال بررسی...")
            confirm_or_run_bulk_create(chat_id, state["data"]["base_name"], state["data"]["numbers"], state["data"]["gb"], float(parts[2]))
            return
        if parts[1] == "dayscustom":
            state["step"] = "days_custom"
            edit_message(chat_id, message_id, "عدد مدت (روز) رو بفرست:")
            return

    if prefix == "bulkdelete":
        pending = state["data"] if state and state.get("flow") == "bulk_delete" else None
        if parts[1] == "yes" and pending:
            deleted, missing = bulk_delete(pending["base_name"], pending["numbers"])
            msg = f"{len(deleted)} کلاینت پاک شد."
            if missing:
                msg += f" ({len(missing)} تا از قبل وجود نداشتن)"
            edit_message(chat_id, message_id, msg)
            send_message(chat_id, "منو:", reply_markup=main_menu())
        else:
            edit_message(chat_id, message_id, "لغو شد.")
        reset_state(chat_id)
        return

    if prefix == "fin":
        action = parts[1]
        if action in ("today", "week", "month", "all"):
            edit_message(chat_id, message_id, format_sales_report(action), reply_markup=inline([pdf_buttons(action)]), parse_mode="HTML")
            return
        if action == "recent":
            rows = recent_transactions(15)
            if not rows:
                edit_message(chat_id, message_id, "هنوز هیچ فروشی ثبت نشده.")
                return
            text = f"🧾 <b>آخرین تراکنش‌ها</b>\n{DIVIDER}\n\n" + "\n\n".join(format_transaction(r) for r in rows)
            edit_message(chat_id, message_id, text, reply_markup=inline([pdf_buttons("recent")]), parse_mode="HTML")
            return
        if action == "range":
            STATE[chat_id] = {"flow": "finrange", "step": "start", "data": {}}
            edit_message(chat_id, message_id, "تاریخ شروع رو به فرم شمسی بفرست (مثلاً 1405-06-01):")
            return
        if action == "search":
            STATE[chat_id] = {"flow": "finsearch", "step": "query", "data": {}}
            edit_message(chat_id, message_id, "کد پیگیری، اسم، یوزرنیم، یا مبلغ رو بفرست تا توی فروش‌های تأییدشده جستجو کنم:")
            return

    if prefix == "autoset":
        set_auto_approve(parts[1] == "1")
        edit_message(chat_id, message_id, auto_settings_text(), reply_markup=auto_toggle_keyboard())
        return

    if prefix == "finpdf":
        kind = parts[1]
        try:
            if kind in ("today", "week", "month", "all"):
                title, summary, table_rows, truncated, total = period_report_data(kind)
            elif kind == "recent":
                title, summary, table_rows, truncated, total = recent_report_data(15)
            elif kind == "range" and len(parts) >= 4:
                start_dt = parse_date_iran_midnight(parts[2])
                end_dt = parse_date_iran_midnight(parts[3])
                title, summary, table_rows, truncated, total = range_report_data(start_dt, end_dt)
            else:
                return
            path = f"{PDF_DIR}/{kind}-{randid(6)}.pdf"
            render_report_pdf(title, summary, table_rows, path, truncated, total)
            send_document(chat_id, path,
                          caption="برای «چاپ»، فایل رو باز کن و از گزینه‌ی Print توی اپ نمایش PDF گوشیت استفاده کن (یه ربات نمی‌تونه مستقیم پرینتر رو صدا بزنه).")
            os.remove(path)
        except Exception as e:
            send_message(chat_id, f"خطا در ساخت PDF: {e}")
        return

    if prefix == "fincsv":
        kind = parts[1]
        try:
            if kind in ("today", "week", "month", "all"):
                start_ms, end_ms = period_bounds(kind)
                title = f"گزارش حسابداری — فروش {PERIOD_LABELS[kind]}"
            elif kind == "recent":
                start_ms, end_ms = period_bounds("all")
                title = "گزارش حسابداری — آخرین تراکنش‌ها"
            elif kind == "range" and len(parts) >= 4:
                start_dt = parse_date_iran_midnight(parts[2])
                end_dt = parse_date_iran_midnight(parts[3])
                start_ms, end_ms = epoch_ms(start_dt), epoch_ms(end_dt + timedelta(days=1))
                title = f"گزارش حسابداری — از {to_jalali_date(start_dt)} تا {to_jalali_date(end_dt)}"
            else:
                return
            rows = accounting_rows(start_ms, end_ms)
            if not rows:
                send_message(chat_id, "توی این بازه هیچ فروش تأییدشده‌ای نبود.")
                return
            path = f"{PDF_DIR}/accounting-{kind}-{randid(6)}.csv"
            build_accounting_csv(rows, path, title)
            send_document(chat_id, path,
                          caption=f"📥 {title}\n{len(rows)} تراکنش — برای حسابدارت؛ مستقیم توی اکسل باز می‌شه.")
            os.remove(path)
        except Exception as e:
            send_message(chat_id, f"خطا در ساخت فایل اکسل: {e}")
        return

    if prefix in ("status", "renew", "topup", "delete", "suspend") and parts[1] == "page":
        page = int(parts[2])
        matches, has_more, total = clients_page(page)
        if state:
            state["data"]["page"] = page
        edit_message(chat_id, message_id, f"({len(matches)} از {total} — یا اسمی رو تایپ کن تا جستجو کنم)",
                     reply_markup=page_keyboard(prefix, page, has_more, matches))
        return

    if prefix in ("status", "renew", "topup", "delete", "suspend") and len(parts) >= 3 and parts[1] == "pick":
        name = ":".join(parts[2:])
        if prefix == "status":
            edit_message(chat_id, message_id, f"در حال بررسی {name}...")
            show_status(chat_id, name)
            reset_state(chat_id)
            return
        if prefix == "renew":
            STATE[chat_id] = {"flow": "renew", "step": "confirm", "data": {"name": name}}
            edit_message(chat_id, message_id, renew_confirm_text(name),
                         reply_markup=yesno("renewone", "🔄 بله، تمدید کن"))
            return
        if prefix == "topup":
            STATE[chat_id] = {"flow": "topup", "step": "gb", "data": {"name": name}}
            edit_message(chat_id, message_id, f"چقدر به {name} اضافه کنم؟")
            send_message(chat_id, "حجم:", reply_markup=gb_picker("topup"))
            return
        if prefix == "delete":
            STATE[chat_id] = {"flow": "delete", "step": "confirm", "data": {"name": name}}
            edit_message(chat_id, message_id, f"مطمئنی {name} کامل پاک بشه؟", reply_markup=yesno("delete", "🗑 بله، پاک کن"))
            return
        if prefix == "suspend":
            enabled = client_is_enabled(name)
            if enabled is None:
                edit_message(chat_id, message_id, f"کلاینتی به اسم {name} پیدا نشد.")
                reset_state(chat_id)
                return
            action = "مسدود" if enabled else "فعال"
            STATE[chat_id] = {"flow": "suspend", "step": "confirm", "data": {"name": name, "target_enabled": not enabled}}
            edit_message(chat_id, message_id, f"{name} الان {'فعاله' if enabled else 'مسدوده'} — {action} بشه؟", reply_markup=yesno("suspend"))
            return

    if prefix == "create" and state and state.get("flow") == "create":
        if parts[1] == "unlim":
            edit_message(chat_id, message_id, "در حال ساخت...")
            finish_create(chat_id, state["data"]["name"], 0, float(parts[2]))
            return
        if parts[1] == "gb":
            state["data"]["gb"] = float(parts[2])
            state["step"] = "days"
            gb_txt = "نامحدود" if parts[2] == "0" else f"{parts[2]} گیگ"
            edit_message(chat_id, message_id, f"حجم: {gb_txt}. حالا مدت رو انتخاب کن:",
                         reply_markup=days_picker("create"))
            return
        if parts[1] == "gbcustom":
            state["step"] = "gb_custom"
            edit_message(chat_id, message_id, "عدد حجم (گیگ) رو بفرست:")
            return
        if parts[1] == "days":
            finish_create(chat_id, state["data"]["name"], state["data"]["gb"], float(parts[2]))
            edit_message(chat_id, message_id, "در حال ساخت...")
            return
        if parts[1] == "dayscustom":
            state["step"] = "days_custom"
            edit_message(chat_id, message_id, "عدد مدت (روز) رو بفرست:")
            return

    if prefix == "topup" and state and state.get("flow") == "topup":
        if parts[1] == "unlim":
            name = state["data"]["name"]
            days = float(parts[2])
            dur = "بدون انقضا (همیشگی)" if not days else f"{int(days)} روز از الان"
            STATE[chat_id] = {"flow": "unlim", "step": "confirm",
                              "data": {"name": name, "days": days}}
            edit_message(chat_id, message_id,
                         f"«{name}» نامحدود بشه؟\n\n"
                         f"• حجم: نامحدود\n"
                         f"• زمان: {dur}\n\n"
                         "پلن فعلی جایگزین می‌شه (نه اضافه).",
                         reply_markup=yesno(f"unlimgo:{int(days)}", "♾ بله، نامحدود کن"))
            return
        if parts[1] == "gb":
            state["data"]["gb"] = float(parts[2])
            state["step"] = "days"
            edit_message(chat_id, message_id, f"حجم اضافه: {parts[2]} گیگ. زمان اضافه هم می‌خوای؟")
            send_message(chat_id, "مدت:", reply_markup=days_picker("topup"))
            return
        if prefix == "topup" and parts[1] == "gbcustom":
            state["step"] = "gb_custom"
            edit_message(chat_id, message_id, "عدد حجم (گیگ) رو بفرست:")
            return
        if parts[1] == "gbskip":
            state["step"] = "days"
            edit_message(chat_id, message_id, "باشه، فقط زمان. چقدر اضافه کنم؟")
            send_message(chat_id, "مدت:", reply_markup=days_picker("topup"))
            return
        if parts[1] == "days":
            state["data"]["days"] = float(parts[2])
            edit_message(chat_id, message_id, "در حال اعمال...")
            finish_topup(chat_id, state["data"])
            return
        if parts[1] == "dayscustom":
            state["step"] = "days_custom"
            edit_message(chat_id, message_id, "عدد مدت (روز) رو بفرست:")
            return
        if parts[1] == "daysskip":
            edit_message(chat_id, message_id, "در حال اعمال...")
            finish_topup(chat_id, state["data"])
            return


# ---------- main loop ----------

UPLOAD_DIR = "/root/admin-uploads"


STT_MODEL = "google/gemini-3.7-flash"   # owner 2026-11-20: «پیام صوتی رو با Gemini به متن تبدیل کن»


def admin_voice_file(msg):
    """(file_id, format) اگر پیام صوتی/ویس است (تلگرام: voice/audio؛ بله: گاهی به‌شکل document با .ogg)."""
    if msg.get("voice"):
        return msg["voice"]["file_id"], "ogg"
    if msg.get("audio"):
        return msg["audio"]["file_id"], (msg["audio"].get("mime_type") or "audio/mp3").split("/")[-1].replace("mpeg", "mp3")
    d = msg.get("document") or {}
    nm, mt = (d.get("file_name") or "").lower(), (d.get("mime_type") or "").lower()
    if d and (mt.startswith("audio/") or nm.endswith((".ogg", ".oga", ".opus", ".mp3", ".m4a", ".wav"))):
        ext = nm.rsplit(".", 1)[-1] if "." in nm else mt.split("/")[-1]
        return d["file_id"], {"oga": "ogg", "opus": "ogg", "mpeg": "mp3"}.get(ext, ext)
    return None


def admin_voice_to_text(chat_id, fid, fmt):
    def _run():
        try:
            info = api_call("getFile", {"file_id": fid})
            data = urllib.request.urlopen(API.replace("/bot", "/file/bot", 1) + "/" + info["result"]["file_path"], timeout=60).read()
            import base64 as _b64
            key = open("/root/.hermesvpn-openrouter-key").read().strip()
            body = {"model": STT_MODEL, "max_tokens": 600, "temperature": 0, "usage": {"include": True},
                    "messages": [{"role": "user", "content": [
                        {"type": "text", "text": "این پیام صوتی فارسی را دقیقاً و کامل به متن فارسی بنویس. فقط خود متن، بدون هیچ توضیح."},
                        {"type": "input_audio", "input_audio": {"data": _b64.b64encode(data).decode(), "format": fmt or "ogg"}}]}]}
            req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=json.dumps(body).encode(),
                                         headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            d = json.load(urllib.request.urlopen(req, timeout=90))
            text = (d["choices"][0]["message"].get("content") or "").strip()
            try:
                AIM._log_usage("stt", d)
            except Exception:
                pass
            if not text:
                send_message(chat_id, "🎙 صدا فهمیده نشد؛ لطفاً دوباره بگو یا تایپ کن."); return
            send_message(chat_id, f"🎙 «{text}»")
            # owner 2026-11-20: ویس همیشه «دستور تازه» است — هر مرحله‌ی نیمه‌کاره (اسم، عدد، انتخاب) کنار گذاشته می‌شود
            # و دستیار منظور را می‌فهمد و به همان بخش می‌رود. فقط حالت باز «پاسخ به مشتری» و دکمه‌های منو مثل تایپ رفتار می‌کنند.
            menu_btns = {b for row in main_menu()["keyboard"] for b in row} | {b for rows in MENU_CATS.values() for row in rows for b in row}
            if text.strip() in menu_btns or SUPPORT_TARGET.get(chat_id) or not ai_handle_voice(chat_id, text):
                handle_text(chat_id, text)
        except Exception as e:
            send_message(chat_id, f"🎙 تبدیل صدا به متن نشد ({type(e).__name__}). لطفاً تایپ کن.")
    threading.Thread(target=_run, daemon=True).start()


def save_admin_upload(chat_id, msg):
    """عکس/فایلی که مدیر برای ربات می‌فرستد روی سرور ذخیره می‌شود تا نشست‌های
    پشتیبانی (مثلاً برای ساخت آیکون) به آن دسترسی داشته باشند."""
    try:
        if msg.get("document"):
            fid = msg["document"]["file_id"]
            name = msg["document"].get("file_name") or "file"
        else:
            fid = msg["photo"][-1]["file_id"]
            name = "photo.jpg"
        info = api_call("getFile", {"file_id": fid})
        fpath = info["result"]["file_path"]
        data = urllib.request.urlopen(API.replace("/bot", "/file/bot", 1) + "/" + fpath, timeout=60).read()
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)[-60:]
        dest = os.path.join(UPLOAD_DIR, time.strftime("%Y%m%d-%H%M%S-") + safe)
        with open(dest, "wb") as f:
            f.write(data)
        send_message(chat_id, "✅ فایل روی سرور ذخیره شد:\n" + dest + "\n(" + str(len(data) // 1024) + " KB)")
    except Exception as e:
        send_message(chat_id, "❌ ذخیره‌ی فایل ناموفق بود: " + str(e))


def main():
    offset = 0
    if os.path.exists(OFFSET_FILE):
        offset = int(open(OFFSET_FILE).read().strip() or 0)

    try:
        api_call("setMyCommands", {"commands": [
            {"command": "menu", "description": "نمایش منوی اصلی"},
            {"command": "start", "description": "شروع / نمایش منو"},
            {"command": "help", "description": "راهنما"},
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

                cq = update.get("callback_query")
                if cq:
                    chat_id = cq["message"]["chat"]["id"]
                    if chat_id not in ALLOWED_CHAT_IDS:
                        continue
                    handle_callback(chat_id, cq["id"], cq.get("data", ""), cq["message"]["message_id"])
                    continue

                msg = update.get("message")
                if not msg:
                    continue
                chat_id = msg["chat"]["id"]
                if chat_id not in ALLOWED_CHAT_IDS:
                    continue
                text = msg.get("text", "")
                if not text and msg.get("caption") and (msg.get("forward_origin") or msg.get("forward_date")):
                    _tg = customer_from_text(msg["caption"], forwarded=True)  # رسیدِ فورواردشده → پنل مشتری
                    if _tg:
                        show_customer(chat_id, _tg)
                        continue
                _vf = None if text else admin_voice_file(msg)
                if _vf:
                    admin_voice_to_text(chat_id, *_vf)     # ویس = همان دستور تایپی
                    continue
                if not text and (msg.get("photo") or msg.get("document")):
                    save_admin_upload(chat_id, msg)
                    continue
                if not text:
                    continue
                if text.strip().startswith("sk-or-"):
                    # کلید OpenRouter برای هوش مصنوعی ربات (owner 2026-11-14): ذخیره با دسترسی 600، پیام فوراً پاک
                    save_ai_key(chat_id, msg.get("message_id"), text.strip())
                    continue
                if text.strip().split("@")[0] == "/menu":
                    CUR_MENU["cat"] = None
                    send_message(chat_id, "📋 منوی اصلی", reply_markup=main_menu())
                    continue
                # پیام قدیمی را فوروارد کرد یا @یوزرنیم/#سفارش تایپ کرد → پنل همان مشتری (owner 2026-11-11)
                _tg = customer_from_text(text, forwarded=bool(msg.get("forward_origin") or msg.get("forward_date")))
                if _tg:
                    show_customer(chat_id, _tg)
                    continue
                if text.strip().startswith("/start ci") and text.strip()[9:].isdigit():
                    _cx = sqlite3.connect(DB)
                    _r = _cx.execute("SELECT email, tg_id FROM clients WHERE id=?", (int(text.strip()[9:]),)).fetchone()
                    _cx.close()
                    if _r:
                        show_status(chat_id, _r[0])
                        if _r[1]:
                            show_customer(chat_id, _r[1])
                    else:
                        send_message(chat_id, "این اکانت دیگر وجود ندارد.")
                    continue
                if text.strip().startswith("/start cu") and text.strip()[9:].isdigit():
                    show_customer(chat_id, text.strip()[9:])
                    continue
                if text.strip() in ("/start", "/help"):
                    CUR_MENU["cat"] = None
                    reset_state(chat_id)
                    send_message(chat_id, HELP_TEXT, reply_markup=main_menu())
                    continue
                handle_text(chat_id, text)
        except Exception as e:
            # تایم‌اوت خواندن در long-poll یک رویداد عادی است نه خطا؛ بلافاصله
            # دوباره وصل شو. فقط خطای واقعی مکث کوتاه می‌خواهد.
            if isinstance(e, socket.timeout) or "timed out" in str(e).lower():
                continue
            print("loop error:", e)
            time.sleep(2)


if __name__ == "__main__":
    main()
