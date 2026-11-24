#!/usr/bin/env python3
"""HermesVPN sales nudges, sent by the SALES bot (@YourSalesBot). Runs every 30 min (systemd timer).

  1) Renewal reminder: account started and (≤ 3 days left OR ≥ 80% of the volume used) -> one reminder per
     account per period, with a «🔁 تمدید همین اکانت» button. Expired in the last 2 days -> one «expired» reminder.
  2) Abandoned order: order still `awaiting_receipt` (plan chosen, nothing paid)
       ~1 h later  -> reminder with the order's own payment buttons (the unique amount is still reserved)
       ~24 h later -> reminder with «ادامه‌ی خرید همین پلن» (creates a fresh order)
     skipped if the customer has since paid another order.

  hermesvpn-sales-nudges.py --dry-run     # list what would be sent, send nothing
"""
import json, sqlite3, sys, time, urllib.parse, urllib.request

ORDERS_DB = "/root/.hermes-orders.db"
X_UI_DB = "/etc/x-ui/x-ui.db"
TOKEN = open("/root/.hermes-customer-bot-token").read().strip()
ADMIN = 1000000001
sys.path.insert(0, "/usr/local/bin")
from hermesvpn_public_text_guard import check as guard

DRY = "--dry-run" in sys.argv
NOW = time.time()
NOW_MS = int(NOW * 1000)


def api(method, **p):
    p = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in p.items()}
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/{method}", data=urllib.parse.urlencode(p).encode())
    return json.load(urllib.request.urlopen(req, timeout=20))


def odb():
    c = sqlite3.connect(ORDERS_DB)
    c.execute("CREATE TABLE IF NOT EXISTS nudges (key TEXT PRIMARY KEY, ts INTEGER)")
    return c


def sent_before(c, key):
    return c.execute("SELECT 1 FROM nudges WHERE key=?", (key,)).fetchone() is not None


def send(c, key, chat_id, text, kb, ignore=()):
    probe = text
    for w in ignore:                       # customer's own account name (often Latin) is fine
        probe = probe.replace(w, "")
    problems = guard(probe)
    if problems:
        print("BLOCKED by guard", key, problems)
        return
    if DRY:
        print(f"[dry] {key} -> {chat_id}: {text.splitlines()[0]}")
        return
    try:
        api("sendMessage", chat_id=chat_id, text=text, reply_markup={"inline_keyboard": kb})
        c.execute("INSERT OR REPLACE INTO nudges VALUES (?,?)", (key, int(NOW)))
        c.commit()
        print("sent", key, chat_id)
    except Exception as e:
        # blocked the bot etc.: remember it so we do not retry every 30 minutes
        c.execute("INSERT OR REPLACE INTO nudges VALUES (?,?)", (key, int(NOW)))
        c.commit()
        print("fail", key, chat_id, str(e)[:80])
    time.sleep(0.5)


def fmt_gb(b):
    return f"{b / 1024**3:.1f}".rstrip("0").rstrip(".")


def renewals(c):
    """One message per customer (resellers own many accounts), listing every account that needs attention."""
    x = sqlite3.connect(f"file:{X_UI_DB}?mode=ro", uri=True)
    rows = x.execute("""SELECT c.email, c.tg_id, c.total_gb, c.expiry_time, c.enable, COALESCE(t.up,0)+COALESCE(t.down,0)
                        FROM clients c LEFT JOIN client_traffics t ON t.email=c.email
                        WHERE c.tg_id IS NOT NULL AND c.tg_id != 0""").fetchall()
    x.close()
    per = {}
    for email, tg, total, exp, enable, used in rows:
        if email.startswith("trial_") or not tg:
            continue
        left_d = (exp - NOW_MS) / 86400000 if exp and exp > 0 else None
        pct = (used / total) if total else 0
        period = str(exp)
        item = None
        if left_d is not None and -2 <= left_d < 0:
            item = (f"expired:{email}:{period}", f"⏰ «{email}» تموم شده")
        elif enable and left_d is not None and 0 <= left_d <= 3:
            item = (f"days:{email}:{period}", f"⏳ «{email}» — {max(1, round(left_d))} روز مونده")
        elif enable and total and pct >= 0.8:
            item = (f"vol:{email}:{period}", f"📊 «{email}» — {int(pct * 100)}٪ حجم مصرف شده ({fmt_gb(used)} از {fmt_gb(total)} گیگ)")
        if item and not sent_before(c, item[0]):
            per.setdefault(tg, []).append((email,) + item)
    for tg, items in per.items():
        lines = "\n".join(it[2] for it in items[:12])
        more = f"\n… و {len(items) - 12} اکانت دیگه" if len(items) > 12 else ""
        text = ("🔔 یادآوری تمدید HermesVPN\n\n" + lines + more +
                "\n\nبرای اینکه وسط کار قطع نشی، از دکمه‌ی زیر تمدید کن 👇\n"
                "🎁 هر دوستی دعوت کنی و خرید کنه، ۱۰ گیگ رایگان می‌گیری.")
        kb = [[{"text": f"🔁 تمدید {it[0]}", "callback_data": f"renewpick:{it[0]}"[:64]}] for it in items[:6]]
        send(c, "|".join(it[1] for it in items), tg, text, kb, ignore=[it[0] for it in items])
        if not DRY:                            # mark every account of this message as reminded
            for it in items:
                c.execute("INSERT OR REPLACE INTO nudges VALUES (?,?)", (it[1], int(NOW)))
            c.commit()


def abandoned(c):
    rows = c.execute("""SELECT id, tg_id, plan_key, target_email, created_at FROM orders
                        WHERE status='awaiting_receipt' AND created_at > ?""", (NOW_MS - 3 * 86400000,)).fetchall()
    for oid, tg, plan, target, created in rows:
        if not tg or tg == ADMIN:
            continue
        paid_since = c.execute("SELECT 1 FROM orders WHERE tg_id=? AND status IN ('approved','pending_review') AND created_at>?",
                               (tg, created)).fetchone()
        if paid_since:
            continue
        age_h = (NOW_MS - created) / 3600000
        if 1 <= age_h < 5 and not sent_before(c, f"ab1:{oid}"):
            send(c, f"ab1:{oid}", tg,
                 f"🛒 سفارش #{oid} هنوز منتظر پرداخته.\n\nاگه جایی گیر کردی، همین‌جا بنویس تا پشتیبانی کمکت کنه؛ "
                 "وگرنه از دکمه‌ی زیر پرداخت رو کامل کن 👇",
                 [[{"text": "💳 کارت به کارت", "callback_data": f"paymethod:card:{oid}"}],
                  [{"text": "💎 کریپتو (USDT)", "callback_data": f"paymethod:crypto:{oid}"}]])
        elif 24 <= age_h < 72 and not sent_before(c, f"ab24:{oid}"):
            cb = f"buyrenew:{plan}:{target}" if target else f"buy:{plan}"
            send(c, f"ab24:{oid}", tg,
                 "👋 دیروز یه سرویس انتخاب کرده بودی ولی خریدت کامل نشد.\n\n"
                 "اگه سؤالی داری همین‌جا بپرس. هر وقت آماده بودی، با دکمه‌ی زیر همون پلن رو دوباره شروع کن 👇\n"
                 "🧪 هنوز امتحان نکردی؟ تست رایگان ۵۰۰ مگ / ۲۴ ساعت هم داریم.",
                 [[{"text": "🛒 ادامه‌ی خرید همین پلن", "callback_data": cb[:64]}]])


ADMIN_BOT_TOKEN = open("/root/.hermes-bot-token").read().strip()
PLANS = [("20gb", "20 گیگ", 60000), ("50gb", "50 گیگ", 100000), ("100gb", "100 گیگ", 150000), ("unlimited", "نامحدود", 250000)]
COUPON_PCT = 10


def receipts_waiting(c):
    """Owner item: receipts waiting > 30 min -> one urgent DM to the admin (admin bot); each order alerted once."""
    rows = c.execute("""SELECT id, tg_id, tg_name, tg_username, plan_key, total_price, created_at FROM orders
                        WHERE status='pending_review' AND created_at < ? ORDER BY created_at""", (NOW_MS - 30 * 60000,)).fetchall()
    new = [r for r in rows if not sent_before(c, f"rcpt:{r[0]}")]
    if not new:
        return
    def age(ms):
        m = int((NOW_MS - ms) / 60000)
        return f"{m} دقیقه" if m < 120 else (f"{m // 60} ساعت" if m < 2880 else f"{m // 1440} روز")
    lines = [f"• #{oid} — {name or ''} {un or ''} — {plan} — {int(pr or 0):,} تومان — {age(cr)} منتظر"
             for oid, tg, name, un, plan, pr, cr in new]
    text = (f"🚨 {len(new)} رسید منتظر بررسی\n\n" + "\n".join(lines[:25]) +
            "\n\nاز «🧾 سفارش‌ها» در همین ربات تأیید یا رد کن؛ مشتری‌ها منتظرند.")
    if DRY:
        print("[dry] admin receipts alert:", len(new))
        return
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"https://api.telegram.org/bot{ADMIN_BOT_TOKEN}/sendMessage",
            data=urllib.parse.urlencode({"chat_id": ADMIN, "text": text, "reply_markup": json.dumps({"inline_keyboard":
                [[{"text": f"🧾 #{oid}", "callback_data": f"ordv:{oid}"},
                  {"text": f"🙋 {(name or un or str(tg))[:18]}", "callback_data": f"cust:{tg}"}]
                 for oid, tg, name, un, plan, pr, cr in new[:8]]})}).encode()), timeout=20).read()
    except Exception as e:
        print("admin alert failed", str(e)[:80])
        return
    for r in new:
        c.execute("INSERT OR REPLACE INTO nudges VALUES (?,?)", (f"rcpt:{r[0]}", int(NOW)))
    c.commit()


def trial_coupons(c):
    """Owner item: free trial finished and nothing bought yet -> 10% first-purchase code valid 24 h."""
    c.execute("""CREATE TABLE IF NOT EXISTS coupons (
        tg_id INTEGER PRIMARY KEY, pct INTEGER, expires_at INTEGER, used_order INTEGER, created_at INTEGER)""")
    x = sqlite3.connect(f"file:{X_UI_DB}?mode=ro", uri=True)
    rows = x.execute("""SELECT c.email, c.tg_id, c.total_gb, c.expiry_time, COALESCE(t.up,0)+COALESCE(t.down,0)
                        FROM clients c LEFT JOIN client_traffics t ON t.email=c.email
                        WHERE c.email LIKE 'trial_%' AND c.tg_id IS NOT NULL AND c.tg_id != 0""").fetchall()
    x.close()
    for email, tg, total, exp, used in rows:
        ended = (exp and 0 < exp < NOW_MS and NOW_MS - exp < 2 * 86400000) or (total and used >= total * 0.98)
        if not ended or tg == ADMIN:
            continue
        if c.execute("SELECT 1 FROM coupons WHERE tg_id=?", (tg,)).fetchone():
            continue
        if c.execute("SELECT 1 FROM orders WHERE tg_id=? AND status='approved'", (tg,)).fetchone():
            continue
        lines = "\n".join(f"• {lbl} — {int(round(pr * (100 - COUPON_PCT) / 100 / 1000.0)) * 1000:,} تومان (به‌جای {pr:,})"
                          for _, lbl, pr in PLANS)
        text = ("🎁 تست رایگانت تموم شد — امیدواریم سرعت و کیفیت رو پسندیده باشی!\n\n"
                f"فقط تا ۲۴ ساعت، اولین خریدت با {COUPON_PCT}٪ تخفیف (پلن‌های ۳۰ روزه):\n{lines}\n\n"
                "پلنت رو انتخاب کن، تخفیف خودکار اعمال می‌شه 👇")
        kb = [[{"text": f"🛒 {lbl}", "callback_data": f"buy:{key}"}] for key, lbl, _ in PLANS]
        if DRY:
            print("[dry] coupon ->", tg, email)
            continue
        c.execute("INSERT OR REPLACE INTO coupons VALUES (?,?,?,?,?)", (tg, COUPON_PCT, NOW_MS + 86400000, None, NOW_MS))
        c.commit()
        send(c, f"coupon:{tg}", tg, text, kb)


def main():
    c = odb()
    try:
        renewals(c)
        abandoned(c)
        receipts_waiting(c)
        trial_coupons(c)
    finally:
        c.close()


if __name__ == "__main__":
    main()
