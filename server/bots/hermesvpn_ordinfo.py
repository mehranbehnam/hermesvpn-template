"""HermesVPN — «این مبلغ از کجا آمد» + «مشتری قبلش چه کرد» برای هر سفارش (owner 2026-11-11).

Shared by hermes-customer-bot.py (logs every button / message of a customer, stores price facts on each order)
and hermes-bot.py (admin order card, receipt photo, bank-match notice). Lives in /usr/local/bin, imported with
sys.path.insert(0, "/usr/local/bin").
"""
import datetime
import sqlite3
import time

ODB = "/root/.hermes-orders.db"
XDB = "/etc/x-ui/x-ui.db"
TZ = datetime.timezone(datetime.timedelta(hours=3, minutes=30))
# fallback for orders created before price facts were stored on the order (copy of PLANS in the sales bot)
PLAN_FALLBACK = {"20gb": ("20 گیگ / 30 روز", 60000), "50gb": ("50 گیگ / 30 روز", 100000),
                 "100gb": ("100 گیگ / 30 روز", 150000), "bulk10_20gb": ("بسته 10تایی — 20 گیگ", 450000),
                 "bulk10_50gb": ("بسته 10تایی — 50 گیگ", 800000), "bulk10_100gb": ("بسته 10تایی — 100 گیگ", 1200000)}
SRC_FA = {"festival": "جشنواره‌ی تخفیف", "wheel": "کوپن گردونه‌ی شانس", "welcome": "کوپن خوش‌آمد (بعد از تست)",
          "coupon": "کوپن تخفیف"}
CH_FA = {"bot": "ربات فروش", "app": "داخل اپ (ویندوز/اندروید)"}


def _db():
    c = sqlite3.connect(ODB, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS user_actions (ts INTEGER, tg_id INTEGER, kind TEXT, data TEXT)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_user_actions ON user_actions(tg_id, ts)")
    cols = {r[1] for r in c.execute("PRAGMA table_info(orders)")}
    for col, typ in (("plan_price", "INTEGER"), ("discount_pct", "INTEGER"), ("discount_src", "TEXT"), ("channel", "TEXT"), ("new_email", "TEXT")):
        if col not in cols:
            c.execute(f"ALTER TABLE orders ADD COLUMN {col} {typ}")
    return c


def log_action(tg_id, kind, data):
    """kind: btn (menu/text), cb (inline button), photo, file, app. Never raises."""
    if not tg_id:
        return
    try:
        c = _db()
        now = int(time.time() * 1000)
        c.execute("INSERT INTO user_actions VALUES (?,?,?,?)", (now, int(tg_id), kind, str(data or "")[:120]))
        c.execute("DELETE FROM user_actions WHERE ts < ?", (now - 60 * 86400000,))
        c.commit(); c.close()
    except Exception as e:
        print(f"[ordinfo] log_action: {e}", flush=True)


def store_price_facts(order_id, plan_price, pct, src, channel):
    try:
        c = _db()
        c.execute("UPDATE orders SET plan_price=?, discount_pct=?, discount_src=?, channel=? WHERE id=?",
                  (plan_price, pct or 0, src or "", channel, order_id))
        c.commit(); c.close()
    except Exception as e:
        print(f"[ordinfo] store_price_facts: {e}", flush=True)


def coupon_source(c, tg_id):
    """wheel if the customer's coupon was created together with a wheel spin, else welcome."""
    r = c.execute("SELECT created_at FROM coupons WHERE tg_id=?", (tg_id,)).fetchone()
    if not r or not r[0]:
        return "coupon"
    w = c.execute("SELECT 1 FROM wheel WHERE tg_id=? AND ABS(ts-?)<120000", (tg_id, r[0])).fetchone()
    return "wheel" if w else "welcome"


def _label(kind, data):
    d = data or ""
    if kind == "btn":
        return f"زد: «{d}»" if not d.startswith("/") else f"دستور {d}"
    if kind == "photo":
        return "📷 عکس فرستاد (رسید؟)"
    if kind == "file":
        return "📎 فایل فرستاد"
    if kind == "app":
        return f"📱 {d}"
    p = d.split(":")
    k = p[0]
    if k == "buy":
        lab = PLAN_FALLBACK.get(p[1], (p[1],))[0] if len(p) > 1 else "?"
        return f"انتخاب پلن «{lab}» (خرید جدید)"
    if k == "buyrenew":
        lab = PLAN_FALLBACK.get(p[1], (p[1],))[0] if len(p) > 1 else "?"
        return f"تمدید «{':'.join(p[2:])}» با پلن «{lab}»"
    if k == "renewpick":
        return f"انتخاب اکانت «{':'.join(p[1:])}» برای تمدید"
    if k == "paymethod":
        m = {"card": "کارت‌به‌کارت", "crypto": "کریپتو (USDT)"}.get(p[1] if len(p) > 1 else "", p[1] if len(p) > 1 else "?")
        return f"روش پرداخت: {m}" + (f" (سفارش #{p[2]})" if len(p) > 2 else "")
    if k == "acc":
        if len(p) > 2 and p[1] == "view":
            return f"باز کردن اکانت «{':'.join(p[2:])}»"
        return "لیست سرویس‌هایش را دید"
    if k == "ref":
        return "بخش دعوت دوستان"
    if k == "myhist":
        return "سابقه‌ی پشتیبانی"
    if k == "checkjoin":
        return "تأیید عضویت کانال"
    return f"دکمه {d[:40]}"


def _fmt(ts_ms):
    return datetime.datetime.fromtimestamp(ts_ms / 1000, TZ).strftime("%m/%d %H:%M")


def order_context(order_id, max_steps=14):
    """Returns dict: price_lines (list[str]), steps (list[str]), accounts (list[str]) — plain text (escape for HTML)."""
    out = {"price_lines": [], "steps": [], "accounts": [], "tg_id": None, "name": None, "uname": None}
    try:
        c = _db()
        r = c.execute("""SELECT tg_id, plan_key, quantity, total_price, base_price, target_email, client_email, created_at,
                                decided_at, plan_price, discount_pct, discount_src, channel, status FROM orders WHERE id=?""",
                      (order_id,)).fetchone()
        if not r:
            c.close(); return out
        (tg, pk, qty, total, base, target, cemail, created, decided, pprice, dpct, dsrc, ch, status) = r
        out["tg_id"] = tg
        nm = c.execute("SELECT tg_name, tg_username FROM orders WHERE id=?", (order_id,)).fetchone()
        out["name"], out["uname"] = nm if nm else (None, None)
        label, fprice = PLAN_FALLBACK.get(pk, (pk or "?", None))
        pprice = pprice or fprice
        P = out["price_lines"]
        P.append(f"نوع: {'تمدید «' + target + '»' if target else 'خرید جدید'} — پلن {label}" + (f" ×{qty}" if qty and qty > 1 else ""))
        if pprice:
            P.append(f"قیمت پلن: {pprice:,} تومان")
        if base and pprice and base < pprice:
            pct = dpct or round(100 - base * 100 / pprice)
            src = SRC_FA.get(dsrc or "", "") if dsrc else ""
            if not src:  # old order: guess from coupons
                used = c.execute("SELECT 1 FROM coupons WHERE tg_id=? AND used_order=?", (tg, order_id)).fetchone()
                src = SRC_FA[coupon_source(c, tg)] if used else "جشنواره یا کوپن (قبل از ثبت جزئیات)"
            P.append(f"تخفیف: {pct}٪ — {src} → {base:,} تومان")
        else:
            P.append("تخفیف: ندارد")
        if total and base and total != base:
            P.append(f"کد شناسایی واریز: +{total - base:,} تومان (برای تشخیص خودکار پیامک بانک)")
        P.append(f"مبلغ نهایی که باید واریز کند: {(total or 0):,} تومان")
        if ch:
            P.append(f"ثبت از: {CH_FA.get(ch, ch)}")

        # timeline: logged actions + wheel spins + coupons + this customer's orders, around this order
        t0 = (created or 0) - 6 * 3600000
        t1 = min((decided or int(time.time() * 1000)), (created or 0) + 24 * 3600000) + 60000
        ev = [(ts, _label(k, d)) for ts, k, d in c.execute(
            "SELECT ts, kind, data FROM user_actions WHERE tg_id=? AND ts BETWEEN ? AND ? ORDER BY ts", (tg, t0, t1))]
        prize_fa = {"none": "پوچ", "c5": "کد تخفیف ۵٪", "c10": "کد تخفیف ۱۰٪", "c15": "کد تخفیف ۱۵٪", "gb05": "۵۰۰ مگ حجم", "gb1": "۱ گیگ حجم"}
        ev += [(ts, f"🎡 گردونه چرخاند: {prize_fa.get(p, p)}") for ts, p in c.execute(
            "SELECT ts, prize FROM wheel WHERE tg_id=? AND ts BETWEEN ? AND ?", (tg, t0 - 18 * 3600000, t1))]
        ev += [(ts, f"🧾 سفارش #{i} ثبت شد ({PLAN_FALLBACK.get(k, (k,))[0]}، {(tp or 0):,} ت)" + (" ← همین سفارش" if i == order_id else ""))
               for i, ts, k, tp in c.execute("SELECT id, created_at, plan_key, total_price FROM orders WHERE tg_id=? AND created_at BETWEEN ? AND ?",
                                             (tg, t0, t1))]
        if decided:
            ev.append((decided, f"✔️ تعیین‌تکلیف: {status}"))
        ev.sort()
        out["steps"] = [f"{_fmt(ts)} — {lab}" for ts, lab in ev][-max_steps:]
        if not c.execute("SELECT 1 FROM user_actions WHERE tg_id=? LIMIT 1", (tg,)).fetchone():
            out["steps"].append("(ثبت دکمه‌ها از امروز شروع شده؛ برای سفارش‌های قدیمی‌تر فقط سفارش‌ها و گردونه دیده می‌شود)")
        c.close()

        acc = [e.strip() for e in (str(cemail or "").split(",") + [target or ""]) if e and e.strip()]
        if not acc and tg:
            x = sqlite3.connect(XDB, timeout=10)
            acc = [e for (e,) in x.execute("SELECT email FROM clients WHERE tg_id=? ORDER BY id DESC LIMIT 3", (tg,))]
            x.close()
        out["accounts"] = list(dict.fromkeys(acc))[:4]
    except Exception as e:
        print(f"[ordinfo] order_context {order_id}: {e}", flush=True)
    return out


def account_buttons(order_id, accounts=None, with_details=True, ctx=None):
    """Inline rows: 🙋 customer panel (cust:<tg>) + 👤 open each client card (opencl:<email>) + 👣 full details (ordv:<id>)."""
    ctx = ctx or order_context(order_id)
    if accounts is None:
        accounts = ctx["accounts"]
    rows = []
    if ctx.get("tg_id"):
        rows.append([customer_button(ctx["tg_id"], ctx.get("name"), ctx.get("uname"))])
    btns = [{"text": f"👤 اکانت {e[:24]}", "callback_data": f"opencl:{e}"[:64]} for e in accounts]
    for i in range(0, len(btns), 2):
        rows.append(btns[i:i + 2])
    if with_details:
        rows.append([{"text": "👣 جزئیات مبلغ و کارهای مشتری", "callback_data": f"ordv:{order_id}"}])
    return rows


# ---------- customer: one tap from any order message → his accounts + chat (owner 2026-11-11) ----------
ADMIN_BOT_USERNAME = "YourAdminBot"


def _esc(s):
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def customer_link_html(tg_id, name=None, uname=None):
    """HTML link that opens the ADMIN bot's customer panel (/start cu<id>) instead of the Telegram profile.
    The username is shown without '@' on purpose — '@user' would be auto-linked to the profile."""
    label = " ".join(x for x in (name or "", f"({uname})" if uname else "") if x) or str(tg_id)
    return f'<a href="https://t.me/{ADMIN_BOT_USERNAME}?start=cu{int(tg_id)}">🙋 {_esc(label)}</a>'


def customer_button(tg_id, name=None, uname=None):
    label = name or uname or str(tg_id)
    return {"text": f"🙋 مشتری {label[:20]} — اکانت‌ها و چت", "callback_data": f"cust:{int(tg_id)}"}


def customer_panel(tg_id, online=None):
    """(html_text, inline_rows) — everything about one customer: who, accounts (status, remaining), orders, chat."""
    tg_id = int(tg_id)
    online = online or set()
    c = _db()
    o = c.execute("SELECT tg_name, tg_username FROM orders WHERE tg_id=? ORDER BY id DESC LIMIT 1", (tg_id,)).fetchone()
    try:
        u = c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()
    except Exception:
        u = None
    orders = c.execute("SELECT id, plan_key, status, total_price, target_email, created_at FROM orders "
                       "WHERE tg_id=? ORDER BY id DESC LIMIT 6", (tg_id,)).fetchall()
    acts = c.execute("SELECT ts, kind, data FROM user_actions WHERE tg_id=? ORDER BY ts DESC LIMIT 6", (tg_id,)).fetchall()
    c.close()
    name, uname = (o or (None, None))
    x = sqlite3.connect(XDB, timeout=10)
    accs = x.execute("""SELECT c.email, c.enable, c.total_gb, c.expiry_time, COALESCE(t.up,0)+COALESCE(t.down,0)
                        FROM clients c LEFT JOIN client_traffics t ON t.email=c.email
                        WHERE c.tg_id=? ORDER BY c.id DESC""", (tg_id,)).fetchall()
    x.close()
    now = time.time() * 1000
    L = [f"🙋 <b>مشتری: {_esc(name or '?')}</b>" + (f" — {_esc(uname)}" if uname else ""),
         f"شناسه تلگرام: <code>{tg_id}</code>", "", f"👤 <b>اکانت‌ها ({len(accs)})</b>"]
    try:
        _pc = sqlite3.connect(ODB, timeout=10)
        parent = dict(_pc.execute("SELECT email, parent FROM account_parent WHERE tg_id=?", (tg_id,)).fetchall())
        _pc.close()
    except Exception:
        parent = {}
    # ریشه‌ها اول، هر زیراکانت درست زیر اکانت مادرش
    order = []
    for row in sorted(accs, key=lambda r: r[0]):
        if row[0] not in parent:
            order.append(row)
            order += [r for r in sorted(accs, key=lambda r: r[0]) if parent.get(r[0]) == row[0]]
    order += [r for r in accs if r not in order]
    accs = order
    for e, en, tot, exp, used in accs[:12]:
        dot = "🔒" if not en else ("🔵" if e in online else "🔴")
        gb = f"{used / 1073741824:.1f}/{tot / 1073741824:.0f} گیگ" if tot else f"{used / 1073741824:.1f} گیگ/نامحدود"
        if exp and exp > 0:
            d = (exp - now) / 86400000
            ex = f"{d:.0f} روز مانده" if d >= 0 else "منقضی"
        elif exp and exp < 0:
            ex = "هنوز شروع نشده"
        else:
            ex = "بدون انقضا"
        if e in parent:
            L.append(f"      └ {dot} <code>{_esc(parent[e])} - {_esc(e[len(parent[e]) + 1:] if e.startswith(parent[e] + '-') else e)}</code> — {gb} — {ex}")
        else:
            L.append(f"    {dot} <code>{_esc(e)}</code> — {gb} — {ex}")
    if not accs:
        L.append("    (اکانتی به این تلگرام وصل نیست)")
    st_fa = {"approved": "✅", "rejected": "❌", "pending_review": "⏳ رسید منتظر", "awaiting_receipt": "🕓 بی‌رسید"}
    if orders:
        L += ["", "🧾 <b>سفارش‌های اخیر</b>"]
        for oid, pk, st, tp, tgt, cr in orders:
            L.append(f"    #{oid} {_fmt(cr)} — {PLAN_FALLBACK.get(pk, (pk,))[0]}{' (تمدید ' + _esc(tgt) + ')' if tgt else ''} — "
                     f"{(tp or 0):,} ت — {st_fa.get(st, st)}")
    if acts:
        L += ["", "👣 <b>آخرین کارها در ربات</b>"] + [f"    {_fmt(ts)} — {_esc(_label(k, d))}" for ts, k, d in reversed(acts)]
    rows = [[{"text": "💬 پیام به این مشتری (چت از طریق ربات فروش)", "callback_data": f"supreply:{tg_id}"}],
            [{"text": "🗂 سابقه‌ی گفتگو", "callback_data": f"suphist:{tg_id}:0"}]]
    if uname:
        rows[1].append({"text": "📱 پروفایل تلگرام", "url": f"https://t.me/{uname}"})
    btns = [{"text": f"👤 {e[:26]}", "callback_data": f"opencl:{e}"[:64]} for e, *_ in accs[:10]]
    for i in range(0, len(btns), 2):
        rows.append(btns[i:i + 2])
    rows += [[{"text": f"🧾 سفارش #{oid}", "callback_data": f"ordv:{oid}"} for oid, *_ in orders[:3]]] if orders else []
    return "\n".join(L), rows


# ---------- every customer / account name in admin messages = a link to his panel (owner 2026-11-12) ----------
import re as _re
_LK = {"ts": 0, "rx": None, "email": {}, "user": {}}


def _link_maps():
    if _LK["rx"] is not None and time.time() - _LK["ts"] < 120:
        return _LK
    email, user = {}, {}
    try:
        x = sqlite3.connect(XDB, timeout=10)
        for cid, e in x.execute("SELECT id, email FROM clients"):
            if e and len(e) >= 4:
                email[e.lower()] = cid
        x.close()
        c = sqlite3.connect(ODB, timeout=10)
        for u, tg in c.execute("SELECT tg_username, tg_id FROM orders WHERE tg_username<>'' AND tg_id IS NOT NULL ORDER BY id"):
            if u and len(u) >= 5:
                user[u.lower().lstrip("@")] = tg
        c.close()
    except Exception as e:
        print(f"[ordinfo] link maps: {e}", flush=True)
    keys = sorted(set(email) | set(user), key=len, reverse=True)
    _LK.update(ts=time.time(), email=email, user=user,
               rx=_re.compile(r"(?<![\w./=?&-])(@?)(" + "|".join(_re.escape(k) for k in keys) + r")(?![\w-])", _re.I) if keys else False)
    return _LK


def _url_for(at, name):
    m = _link_maps()
    k = name.lower()
    if at and k in m["user"]:
        return f"https://t.me/{ADMIN_BOT_USERNAME}?start=cu{m['user'][k]}"
    if k in m["email"]:
        return f"https://t.me/{ADMIN_BOT_USERNAME}?start=ci{m['email'][k]}"
    if k in m["user"]:
        return f"https://t.me/{ADMIN_BOT_USERNAME}?start=cu{m['user'][k]}"
    return None


def linkify_payload(payload, field):
    """Mutates a Telegram API payload (sendMessage/editMessageText: field='text'; captions: 'caption') so every
    known account name / customer username becomes a link to the admin bot (/start ci<client id> | cu<tg id>).
    Plain text → text_link entities (UTF-16 offsets); HTML → <a> tags outside a/code/pre. Never raises."""
    try:
        text = payload.get(field)
        if not text or not isinstance(text, str) or payload.get("entities") or payload.get("caption_entities"):
            return payload
        m = _link_maps()
        rx = m["rx"]
        if not rx:
            return payload
        pm = (payload.get("parse_mode") or "").upper()
        if pm == "HTML":
            out, depth = [], 0
            for part in _re.split(r"(<[^>]+>)", text):
                if part.startswith("<") and part.endswith(">"):
                    tag = _re.match(r"</?\s*([a-zA-Z-]+)", part)
                    if tag and tag.group(1).lower() in ("a", "code", "pre"):
                        depth += -1 if part.startswith("</") else 1
                    out.append(part)
                elif depth <= 0 and part:
                    def _sub(mo):
                        u = _url_for(mo.group(1), mo.group(2))
                        return f'<a href="{u}">{mo.group(0)}</a>' if u else mo.group(0)
                    out.append(rx.sub(_sub, part))
                else:
                    out.append(part)
            payload[field] = "".join(out)
            return payload
        if pm:  # Markdown: leave alone
            return payload
        urls = [(u.start(), u.end()) for u in _re.finditer(r"https?://\S+|t\.me/\S+", text)]
        ents = []
        for mo in rx.finditer(text):
            if any(a <= mo.start() < b for a, b in urls):
                continue
            u = _url_for(mo.group(1), mo.group(2))
            if not u:
                continue
            off = len(text[:mo.start()].encode("utf-16-le")) // 2
            ln = len(mo.group(0).encode("utf-16-le")) // 2
            ents.append({"type": "text_link", "offset": off, "length": ln, "url": u})
        if ents:
            # explicit entities turn off Telegram's own auto-detection, so keep links / @mentions / commands clickable
            u16 = lambda i: len(text[:i].encode("utf-16-le")) // 2
            taken = [(e["offset"], e["offset"] + e["length"]) for e in ents]
            for typ, rxs in (("url", r"https?://[^\s<>]+|t\.me/[^\s<>]+"), ("mention", r"(?<![\w@])@[A-Za-z][A-Za-z0-9_]{3,31}"),
                             ("bot_command", r"(?<![\w/])/[A-Za-z][A-Za-z0-9_]{0,31}")):
                for mo in _re.finditer(rxs, text):
                    o, l = u16(mo.start()), u16(mo.end()) - u16(mo.start())
                    if not any(a < o + l and o < b for a, b in taken):
                        ents.append({"type": typ, "offset": o, "length": l}); taken.append((o, o + l))
            ents.sort(key=lambda e: e["offset"])
            payload["entities" if field == "text" else "caption_entities"] = ents
    except Exception as e:
        print(f"[ordinfo] linkify: {e}", flush=True)
    return payload


def account_family(email):
    """(parent or None, [children]) for the admin status card: «ali - Zahra»."""
    try:
        c = sqlite3.connect(ODB, timeout=10)
        c.execute("CREATE TABLE IF NOT EXISTS account_parent (email TEXT PRIMARY KEY, parent TEXT, tg_id INTEGER, created_at INTEGER)")
        p = c.execute("SELECT parent FROM account_parent WHERE email=?", (email,)).fetchone()
        kids = [r[0] for r in c.execute("SELECT email FROM account_parent WHERE parent=? ORDER BY created_at", (email,))]
        c.close()
        return (p[0] if p else None), kids
    except Exception:
        return None, []
