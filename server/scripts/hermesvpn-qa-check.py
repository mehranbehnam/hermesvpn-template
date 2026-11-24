#!/usr/bin/env python3
"""HermesVPN «اکانت تست کیفیت» checkup (owner 2026-11-10).
The owner connects any device / operator / location with the QA account (email qa-test); this prints a full
Persian report: which device+app fetched the configs and from which operator/city, which routes (Main/Reality/
XHTTP) actually reached the server, traffic & average speed since the last checkup, server health and live
TCP reachability from 8 points inside Iran.   usage: hermesvpn-qa-check.py [minutes=20]   (run as root)"""
import collections, datetime, json, os, re, sqlite3, subprocess, sys, time, urllib.request

EMAIL = "qa-test"
MIN = int(sys.argv[1]) if len(sys.argv) > 1 else 20
XDB, ODB, LOG = "/etc/x-ui/x-ui.db", "/root/.hermes-orders.db", "/var/log/x-ui/access.log"
STATE = "/var/lib/hermesvpn-qa.json"
TZ = datetime.timezone(datetime.timedelta(hours=3, minutes=30))
ROUTE = {"in-443-tcp": "Main (Cloudflare، شامل Main 2-4)", "inbound-8443": "Reality (مستقیم)", "in-2083-xhttp": "XHTTP (مخابرات)"}


def fa_time(ts):
    return datetime.datetime.fromtimestamp(ts, TZ).strftime("%H:%M:%S")


def ipinfo(ip, cache={}):
    if ip in cache:
        return cache[ip]
    try:
        d = json.load(urllib.request.urlopen(f"http://ip-api.com/json/{ip}?fields=country,city,isp,as", timeout=8))
        r = f"{d.get('isp','?')} — {d.get('city','?')}، {d.get('country','?')}"
    except Exception:
        r = "نامشخص"
    cache[ip] = r
    return r


def device(ua):
    ua = ua or ""
    for pat, name in ((r"HermesVPN-Android/([\d.]+)", "اپ اندروید HermesVPN {}"), (r"HermesVPN-Windows/([\w.]+)", "اپ ویندوز HermesVPN {}"),
                      (r"v2rayNG/([\d.]+)", "v2rayNG {}"), (r"[Hh]iddify\S*/?([\d.]*)", "Hiddify {}"), (r"[Ss]treisand", "Streisand"),
                      (r"[Vv]2[Bb]ox", "V2Box"), (r"[Nn]ekoBox|NekoRay", "NekoBox"), (r"Dalvik/", "اپ اندروید HermesVPN (نسخه‌ی قدیمی)"),
                      (r"Mozilla", "مرورگر")):
        m = re.search(pat, ua)
        if m:
            return name.format(m.group(1) if m.groups() else "").strip()
    return ua[:60] or "نامشخص"


def main():
    now = time.time()
    since = now - MIN * 60
    out = [f"🔎 چکاپ اکانت تست کیفیت — {datetime.datetime.now(TZ):%Y-%m-%d %H:%M} (بازه‌ی {MIN} دقیقه‌ی اخیر)"]

    # --- device / app / operator (from config fetches) ---
    o = sqlite3.connect(ODB)
    o.execute("CREATE TABLE IF NOT EXISTS fetch_log (ts INTEGER, email TEXT, ip TEXT, ua TEXT, kind TEXT)")
    rows = o.execute("SELECT ts, ip, ua, kind FROM fetch_log WHERE email=? ORDER BY ts DESC LIMIT 6", (EMAIL,)).fetchall()
    o.close()
    out.append("\n📱 دستگاه‌ها و اینترنت (آخرین دریافت کانفیگ):")
    if rows:
        for ts, ip, ua, kind in rows:
            out.append(f"• {fa_time(ts)} — {device(ua)} ({'لینک ساب' if kind == 'sub' else 'اپ'})\n   {ip} → {ipinfo(ip)}")
    else:
        out.append("• هنوز هیچ دستگاهی کانفیگ این اکانت را نگرفته.")

    # --- connections that reached the server ---
    per_route, dests, reality_ips, blocked, first, last = collections.Counter(), collections.Counter(), collections.Counter(), 0, None, None
    cutoff = datetime.datetime.fromtimestamp(since, datetime.timezone.utc).strftime("%Y/%m/%d %H:%M:%S")
    try:
        lines = subprocess.run(["grep", "-a", f"email: {EMAIL}$", LOG], capture_output=True, text=True).stdout.splitlines()
    except Exception:
        lines = []
    for ln in lines:
        ts = ln[:19]
        if ts < cutoff:
            continue
        first = first or ts; last = ts
        m = re.search(r"from (?:tcp:|udp:)?([\d.]+):\d+ accepted \w+:([^ ]+) \[([^\] ]+) (?:>>|->) (\w+)", ln)
        if not m:
            continue
        src, dst, tag, act = m.groups()
        per_route[tag] += 1
        dests[dst.rsplit(":", 1)[0]] += 1
        if act == "blocked":
            blocked += 1
        if tag == "inbound-8443":
            reality_ips[src] += 1
    out.append("\n🛰 اتصال‌هایی که به سرور رسید:")
    if per_route:
        for tag, n in per_route.most_common():
            out.append(f"• {ROUTE.get(tag, tag)}: {n} اتصال")
        out.append(f"• اولین/آخرین: {first[11:]} / {last[11:]} UTC")
        out.append("• مقصدهای پرتکرار: " + "، ".join(d for d, _ in dests.most_common(5)))
        for ip, n in reality_ips.most_common(3):
            out.append(f"• Reality از {ip} → {ipinfo(ip)}")
        if blocked:
            out.append(f"• {blocked} درخواست به مقصدهای داخلی/مسدود (طبیعی، مثل آدرس‌های محلی)")
    else:
        out.append("• ❌ در این بازه هیچ اتصالی از این اکانت به سرور نرسید (یعنی اینترنت/اپراتور جلویش را گرفته یا اپ وصل نشده).")

    # --- traffic & speed since the last checkup ---
    x = sqlite3.connect(XDB)
    tr = x.execute("SELECT up, down FROM client_traffics WHERE email=?", (EMAIL,)).fetchone() or (0, 0)
    x.close()
    try:
        st = json.load(open(STATE))
    except Exception:
        st = {}
    out.append("\n📊 ترافیک:")
    out.append(f"• کل: دانلود {tr[1] / 1e6:.1f} MB، آپلود {tr[0] / 1e6:.1f} MB")
    if st.get("ts"):
        dt = now - st["ts"]; dd = tr[1] - st.get("down", 0); du = tr[0] - st.get("up", 0)
        out.append(f"• از چکاپ قبلی ({dt / 60:.0f} دقیقه پیش): دانلود {dd / 1e6:.1f} MB، آپلود {du / 1e6:.1f} MB"
                   + (f" — میانگین {dd * 8 / dt / 1e6:.2f} Mbps" if dt > 0 else ""))
    json.dump({"ts": now, "up": tr[0], "down": tr[1]}, open(STATE, "w"))
    out.append("  (برای اندازه‌گیری سرعت: یک چکاپ بزن، ۱-۲ دقیقه ویدیو/اسپیدتست، دوباره چکاپ بزن.)")

    # --- server health ---
    def sh(cmd):
        return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()
    out.append("\n🖥 سلامت سرور:")
    out.append(f"• x-ui: {sh('systemctl is-active x-ui')}، بار پردازنده: {open('/proc/loadavg').read().split()[0]}")
    for port, name in ((443, "Main"), (8443, "Reality"), (2083, "XHTTP")):
        n = sh(f"ss -Htn state established '( sport = :{port} )' | wc -l")
        out.append(f"• اتصال‌های فعال {name} ({port}): {n}")

    # --- reachability from inside Iran (check-host.net, datacenter nodes) ---
    out.append("\n🇮🇷 دسترسی از داخل ایران (۸ نقطه، دیتاسنتر):")
    nodes = "".join(f"&node=ir{i}.node.check-host.net" for i in range(1, 9))
    targets = (("Main", "edge.example.com:443"), ("Reality", "203.0.113.10:8443"), ("XHTTP", "edge.example.com:2083"))
    ids = {}
    for name, t in targets:
        try:
            req = urllib.request.Request(f"https://check-host.net/check-tcp?host={t}{nodes}", headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
            ids[name] = json.load(urllib.request.urlopen(req, timeout=15))["request_id"]
        except Exception:
            ids[name] = None
    time.sleep(12)
    for name, _ in targets:
        rid = ids.get(name)
        if not rid:
            out.append(f"• {name}: تست انجام نشد"); continue
        try:
            req = urllib.request.Request(f"https://check-host.net/check-result/{rid}", headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
            d = json.load(urllib.request.urlopen(req, timeout=15))
            ok = [v[0]["time"] for v in d.values() if v and v[0] and "time" in v[0]]
            out.append(f"• {name}: {len(ok)}/{len(d)} نقطه وصل" + (f"، میانگین {sum(ok) / len(ok) * 1000:.0f} ms" if ok else ""))
        except Exception:
            out.append(f"• {name}: نتیجه نیامد")

    # --- verdict ---
    out.append("\n✅ جمع‌بندی:")
    if not rows:
        out.append("• اول با این اکانت روی دستگاه وصل شو (اپ یا لینک ساب)، بعد چکاپ را بزن.")
    elif not per_route:
        out.append("• دستگاه کانفیگ گرفته ولی هیچ مسیری به سرور نرسیده → اپراتور/وای‌فای مسیرها را بسته؛ یکی‌یکی Main 2، Reality و XHTTP را امتحان کن.")
    else:
        best = per_route.most_common(1)[0][0]
        out.append(f"• مسیری که روی این اینترنت کار کرد: {ROUTE.get(best, best)}")
    print("\n".join(out))


if __name__ == "__main__":
    main()
