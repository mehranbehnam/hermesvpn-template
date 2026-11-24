#!/usr/bin/env python3
"""Activity log of one HermesVPN account (owner 2026-11-10): which sites/services it connected to in the last N hours,
apps inferred from domains, routes used, operator of direct (Reality) connections and traffic. Only domain names are
known (HTTPS content and exact pages are not visible). Source: xray access log (x-ui rotates it daily).
usage: hermesvpn-activity.py <email> [hours=24]   (root)"""
import collections, datetime, json, re, sqlite3, subprocess, sys, urllib.request

EMAIL = sys.argv[1]
HOURS = float(sys.argv[2]) if len(sys.argv) > 2 else 24
LOG = "/var/log/x-ui/access.log"
TZ = datetime.timezone(datetime.timedelta(hours=3, minutes=30))
ROUTE = {"in-443-tcp": "Main (Cloudflare)", "inbound-8443": "Reality", "in-2083-xhttp": "XHTTP"}
APPS = [  # (substring of domain, app name)
    ("telegram", "تلگرام"), ("t.me", "تلگرام"), ("instagram", "اینستاگرام"), ("cdninstagram", "اینستاگرام"),
    ("fbcdn", "اینستاگرام/فیسبوک"), ("facebook", "فیسبوک"), ("whatsapp", "واتساپ"), ("youtube", "یوتیوب"),
    ("googlevideo", "یوتیوب"), ("ytimg", "یوتیوب"), ("tiktok", "تیک‌تاک"), ("byteoversea", "تیک‌تاک"), ("twitter", "توییتر/X"),
    ("x.com", "توییتر/X"), ("twimg", "توییتر/X"), ("chatgpt", "ChatGPT"), ("openai", "ChatGPT"), ("claude", "Claude"),
    ("anthropic", "Claude"), ("gemini", "Gemini"), ("netflix", "نتفلیکس"), ("spotify", "اسپاتیفای"), ("discord", "دیسکورد"),
    ("snapchat", "اسنپ‌چت"), ("linkedin", "لینکدین"), ("github", "GitHub"), ("steam", "Steam"), ("binance", "بایننس"),
    ("tradingview", "TradingView"), ("pinterest", "پینترست"), ("reddit", "ردیت"), ("microsoft", "ویندوز/مایکروسافت"),
    ("windowsupdate", "ویندوز/مایکروسافت"), ("apple.com", "اپل"), ("icloud", "اپل"), ("google", "گوگل"), ("gstatic", "گوگل"),
]
TG_NETS = ("149.154.", "91.108.", "95.161.")       # Telegram data centers (connections by IP)
MEDIA = [  # media CDNs: (substring of host, label) — only WHEN/WHERE media was loaded, never WHICH photo/video
    ("googlevideo.com", "پخش ویدیو در یوتیوب"), ("cdninstagram.com", "عکس/ویدیو/ریلز اینستاگرام"),
    ("video.", "ویدیو"), ("fbcdn.net", "عکس/ویدیو اینستاگرام/فیسبوک"), ("tiktokcdn", "ویدیو تیک‌تاک"),
    ("tiktokv.com", "ویدیو تیک‌تاک"), ("ibyteimg", "ویدیو تیک‌تاک"), ("twimg.com", "عکس/ویدیو توییتر/X"),
    ("mmg.whatsapp.net", "عکس/ویدیو/ویس واتساپ"), ("media", "رسانه"), ("nflxvideo", "ویدیو نتفلیکس"),
    ("ttvnw.net", "پخش زنده توییچ"), ("vimeocdn", "ویدیو ویمیو"), ("scdn.co", "موسیقی اسپاتیفای"),
    ("aparat", "ویدیو آپارات"), ("pinimg.com", "عکس پینترست"), ("redd.it", "عکس/ویدیو ردیت"),
    ("redditmedia", "عکس/ویدیو ردیت"), ("sc-cdn.net", "عکس/ویدیو اسنپ‌چت"), ("snapchat", "عکس/ویدیو اسنپ‌چت"),
    ("licdn.com", "عکس/ویدیو لینکدین"), ("discordapp.net", "عکس/ویدیو دیسکورد"), ("telesco.pe", "ویدیو تلگرام"),
    ("cdn-telegram", "عکس/ویدیو تلگرام"), ("userapi.com", "عکس/ویدیو VK"), ("likee", "ویدیو لایکی"),
    ("dailymotion", "ویدیو دیلی‌موشن"), ("dmcdn", "ویدیو دیلی‌موشن"), ("rumble", "ویدیو رامبل"),
    ("kick.com", "پخش زنده کیک"), ("soundcloud", "موسیقی ساندکلاد"), ("sndcdn", "موسیقی ساندکلاد"),
    ("ytimg.com", "عکس/تامبنیل یوتیوب"), ("ggpht.com", "عکس گوگل/یوتیوب"), ("googleusercontent", "عکس/فایل گوگل"),
]
# any other site: words in the host name that mark photo/video servers (attributed to that site)
GENERIC_MEDIA = [(re.compile(r"(^|[.-])(video|videos|vid|vod|stream|hls|m3u8|mp4)[0-9]*([.-]|$)"), "ویدیو"),
                 (re.compile(r"(^|[.-])(img|imgs|image|images|photo|photos|pic|pics|thumb|thumbs|media|scontent)[0-9]*([.-]|$)"), "عکس/رسانه")]
TG_BURST = 15   # ≥ this many Telegram connections in one minute ≈ media download/upload


def regdom(host):
    if re.fullmatch(r"[\d.]+", host) or ":" in host:
        return host
    p = host.split(".")
    return ".".join(p[-3:]) if len(p) >= 3 and p[-2] in ("co", "com", "org", "net", "ac") and len(p[-1]) == 2 else ".".join(p[-2:])


DETAIL = "--detail" in sys.argv
MEDIA_SESS = []


def media_sessions(media_ev, gap=300):
    """group media events per label; a new session after `gap` seconds idle → [(label, start, end, count)]"""
    cur, out = {}, []
    for ts, lab, host in media_ev:
        t = datetime.datetime.strptime(ts, "%Y/%m/%d %H:%M:%S")
        c = cur.get(lab)
        if c and (t - c[1]).total_seconds() <= gap:
            c[1] = t; c[2] += 1; c[3].add(host)
        else:
            if c:
                out.append((lab, c[0], c[1], c[2], c[3]))
            cur[lab] = [t, t, 1, {host}]
    out += [(l, v[0], v[1], v[2], v[3]) for l, v in cur.items()]
    out.sort(key=lambda x: x[1])
    fmt = lambda t: t.strftime("%Y/%m/%d %H:%M:%S")
    return [(l, fmt(a), fmt(b), n, h) for l, a, b, n, h in out]


def write_detail(email, events, loc):
    """Full timeline file: app sessions (start-end, count), per-hour activity, and every connection in order
    (consecutive duplicates folded)."""
    path = f"/tmp/activity-{re.sub(r'[^A-Za-z0-9_-]', '_', email)}.txt"
    L = [f"لاگ ریز فعالیت «{email}» — همه‌ی اتصال‌ها به ترتیب زمان (وقت تهران)", "فقط نام سایت/آی‌پی و پورت ثبت می‌شود؛ محتوا دیده نمی‌شود.", ""]
    # app sessions: a new session when the same app is idle > 5 min
    sess = {}
    out_sess = []
    for ts, proto, host, port, route, app, blk in events:
        if not app:
            continue
        t = datetime.datetime.strptime(ts, "%Y/%m/%d %H:%M:%S")
        cur = sess.get(app)
        if cur and (t - cur[1]).total_seconds() <= 300:
            cur[1] = t; cur[2] += 1
        else:
            if cur:
                out_sess.append((app, cur[0], cur[1], cur[2]))
            sess[app] = [t, t, 1]
    out_sess += [(a, v[0], v[1], v[2]) for a, v in sess.items()]
    out_sess.sort(key=lambda x: x[1])
    f = lambda t: loc(t.strftime("%Y/%m/%d %H:%M:%S"))
    L.append("== جلسه‌های استفاده از هر اپ (شروع — پایان، تعداد اتصال) ==")
    for app, a, b, n in out_sess:
        mins = max(1, int((b - a).total_seconds() // 60))
        L.append(f"{f(a)} — {f(b)[-5:]}  ({mins} دقیقه)  {app}  [{n} اتصال]")
    L.append("")
    L.append("== عکس و ویدیو (زمان و سرویس؛ کدام عکس/ویدیو دیده نمی‌شود) ==")
    for lab, a, b, n, hosts in MEDIA_SESS:
        L.append(f"{loc(a)} — {loc(b)[-5:]}  {lab}  [{n} اتصال]")
        L.append("      لینک‌ها: " + "  ".join(("https://" + h) if "." in h else h for h in sorted(hosts)[:8]))
    L.append("")
    L.append("== فعالیت ساعت‌به‌ساعت ==")
    per_h = collections.Counter(loc(ts)[:8] + ":00" for ts, *_ in events)
    for h in sorted(per_h):
        L.append(f"{h}  {'█' * min(60, per_h[h] // 5 + 1)} {per_h[h]}")
    L.append("")
    L.append("== همه‌ی اتصال‌ها (زمان | پروتکل | مقصد:پورت | مسیر | اپ) ==")
    prev, rep = None, 0
    for ts, proto, host, port, route, app, blk in events:
        key = (proto, host, port, route)
        if key == prev:
            rep += 1; continue
        if rep:
            L.append(f"        … و {rep} بار دیگر همین")
        rep = 0; prev = key
        L.append(f"{loc(ts)}:{ts[17:19]} | {proto.upper():3} | {host}:{port} | {route}{' | ' + app if app else ''}{' | مسدود' if blk else ''}")
    if rep:
        L.append(f"        … و {rep} بار دیگر همین")
    open(path, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(f"\n@@FILE@@{path}")


def main():
    x = sqlite3.connect("/etc/x-ui/x-ui.db")
    row = x.execute("SELECT email FROM clients WHERE email=? COLLATE NOCASE", (EMAIL,)).fetchone()
    tr = x.execute("SELECT up, down FROM client_traffics WHERE email=? COLLATE NOCASE", (EMAIL,)).fetchone()
    x.close()
    if not row:
        print(f"اکانتی به اسم «{EMAIL}» پیدا نشد."); return
    email = row[0]
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=HOURS)
    cutoff = since.strftime("%Y/%m/%d %H:%M:%S")
    lines = subprocess.run(["grep", "-aF", f"email: {email}", LOG], capture_output=True, text=True).stdout.splitlines()
    doms, apps, routes, direct_ips, hours = collections.Counter(), collections.Counter(), collections.Counter(), collections.Counter(), collections.Counter()
    first = last = None; total = 0
    media_ev = []        # (utc_ts_str, label)
    tg_min = collections.Counter()
    events = []          # (utc_ts_str, proto, host, port, route, app, blocked)
    for ln in lines:
        if not ln.endswith(f"email: {email}") or ln[:19] < cutoff:
            continue
        m = re.search(r"from (?:tcp:|udp:)?([\d.]+):\d+ accepted (\w+):(\S+?):(\d+) \[([^\] ]+)", ln)
        if not m:
            continue
        src, proto, host, port, tag = m.groups()
        total += 1; first = first or ln[:19]; last = ln[:19]
        routes[ROUTE.get(tag, tag)] += 1
        hours[ln[11:13]] += 1
        d = regdom(host)
        if host.startswith(TG_NETS):
            d = "تلگرام (آی‌پی مستقیم)"
        elif port == "3478":
            d = "تماس صوتی/تصویری (TURN)"
        doms[d] += 1
        app = next((a for k, a in APPS if k in host), None)
        if host.startswith(TG_NETS):
            app = "تلگرام"
        if app:
            apps[app] += 1
        if tag == "inbound-8443":
            direct_ips[src] += 1
        events.append((ln[:19], proto, host, port, ROUTE.get(tag, tag), app or "", "blocked" in ln))
        lab = None if "netseer" in host else next((l for k, l in MEDIA if k in host), None)
        if lab == "ویدیو" and app:
            lab = f"ویدیو ({app})"
        if lab == "رسانه":
            lab = f"رسانه ({app})" if app else None
        if not lab and not re.fullmatch(r"[\d.]+", host):
            for rx, kind in GENERIC_MEDIA:
                if rx.search(host):
                    lab = f"{kind} — {app or regdom(host)}"
                    break
        if lab:
            media_ev.append((ln[:19], lab, host))
        if host.startswith(TG_NETS):
            tg_min[ln[:16]] += 1

    def loc(ts):
        return datetime.datetime.strptime(ts, "%Y/%m/%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc).astimezone(TZ).strftime("%m/%d %H:%M")

    out = [f"🕵️ لاگ فعالیت «{email}» — {HOURS:g} ساعت اخیر"]
    if not total:
        out.append("\nدر این بازه هیچ اتصالی ثبت نشده (یا لاگ امروز از نیمه‌شب شروع شده).")
        print("\n".join(out)); return
    out.append(f"اولین/آخرین اتصال: {loc(first)} / {loc(last)} (تهران) — {total:,} اتصال")
    out.append("\n📱 اپ‌ها/سرویس‌ها (از روی نام سایت‌ها):")
    for a, n in apps.most_common(12):
        out.append(f"• {a}: {n:,}")
    out.append("\n🌐 پرتکرارترین سایت‌ها:")
    for d, n in doms.most_common(20):
        out.append(f"• {d}: {n:,}")
    for m_, n in tg_min.items():
        if n >= TG_BURST:
            media_ev.append((m_ + ":00", "عکس/ویدیو/فایل در تلگرام (احتمالی)", "سرورهای تلگرام"))
    media_ev.sort()
    msess = media_sessions(media_ev)
    out.append("\n🎬 عکس و ویدیو (زمان و سرویس — محتوا دیده نمی‌شود):")
    if msess:
        for lab, a, b, n, _h in msess[-12:]:
            out.append(f"• {loc(a)} تا {loc(b)[-5:]} ({max(1, int((datetime.datetime.strptime(b, '%Y/%m/%d %H:%M:%S') - datetime.datetime.strptime(a, '%Y/%m/%d %H:%M:%S')).total_seconds() // 60))} دقیقه) — {lab}")
            hl = [h for h in sorted(_h) if "." in h][:2]
            if hl:
                out.append("   🔗 " + "  ".join("https://" + h for h in hl))
    else:
        out.append("• در این بازه عکس/ویدیویی از سرویس‌های شناخته‌شده بارگذاری نشده.")
    out.append("\n🛰 مسیرها: " + "، ".join(f"{r} {n:,}" for r, n in routes.most_common()))
    for ip, n in direct_ips.most_common(2):
        try:
            d = json.load(urllib.request.urlopen(f"http://ip-api.com/json/{ip}?fields=isp,city", timeout=8))
            out.append(f"• اینترنت (از اتصال مستقیم): {d.get('isp')} — {d.get('city')}")
        except Exception:
            pass
    if tr:
        out.append(f"\n📊 ترافیک کل اکانت: دانلود {tr[1] / 1e9:.2f} GB، آپلود {tr[0] / 1e9:.2f} GB")
    out.append("\nفقط نام سایت‌ها ثبت می‌شود؛ محتوای صفحه‌ها و پیام‌ها دیده نمی‌شود.")
    print("\n".join(out)[:4000])
    if DETAIL:
        global MEDIA_SESS
        MEDIA_SESS = msess
        write_detail(email, events, loc)


if __name__ == "__main__":
    main()
