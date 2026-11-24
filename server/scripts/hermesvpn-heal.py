#!/usr/bin/env python3
"""HermesVPN «🛠 رفع کلی مشکل» (owner 2026-11-20). One tap from the admin bot (Telegram or Bale) when the owner has
no Claude/AI access: check the whole server, repair what is safely repairable, report in Persian.
Safe, idempotent steps only — never reboot, never apt upgrade, never touch the DB contents.
  usage: hermesvpn-heal.py [--dry-run]     (run as root)"""
import json, os, socket, sqlite3, ssl, subprocess, sys, time, urllib.request

DRY = "--dry-run" in sys.argv
OUT, FIXED, WARN = [], [], []


def sh(cmd, timeout=60):
    r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def active(unit):
    return sh(["systemctl", "is-active", unit])[1].strip()


def restart(unit, why):
    if DRY:
        FIXED.append(f"(آزمایشی) {unit} ری‌استارت می‌شد — {why}"); return True
    sh(["systemctl", "restart", unit], 120)
    time.sleep(4)
    ok = active(unit) == "active"
    (FIXED if ok else WARN).append(f"{unit}: {'ری‌استارت شد و الان فعال است' if ok else 'ری‌استارت شد ولی هنوز فعال نیست'} — {why}")
    return ok


def port_open(port, host="127.0.0.1"):
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except Exception:
        return False


# 1) core services ------------------------------------------------------------
OUT.append("۱) سرویس‌ها")
for unit, name in (("x-ui", "پنل و هسته‌ی VPN"), ("hermes-customer-bot", "ربات فروش"), ("hermes-bot", "ربات مدیریت تلگرام"),
                   ("hermes-bot-bale", "ربات مدیریت بله"), ("hermes-tgsocks", "SOCKS تلگرام")):
    st = active(unit)
    if st == "unknown" or st == "":
        continue
    if st != "active":
        restart(unit, f"{name} {st} بود")
    OUT.append(f"• {name}: {active(unit)}")

# 2) VPN ports ---------------------------------------------------------------
OUT.append("\n۲) درگاه‌های VPN")
ports = {443: "Main", 8443: "Reality", 2083: "XHTTP", 2087: "ربات فروش/لینک ساب"}
missing = [p for p in ports if not port_open(p)]
if any(p in missing for p in (443, 8443, 2083)):
    restart("x-ui", "درگاه‌های " + "، ".join(str(p) for p in missing if p != 2087) + " بسته بودند")
    missing = [p for p in ports if not port_open(p)]
if 2087 in missing:
    restart("hermes-customer-bot", "درگاه 2087 بسته بود")
    missing = [p for p in ports if not port_open(p)]
for p, n in ports.items():
    OUT.append(f"• {n} ({p}): {'✅ باز' if p not in missing else '❌ بسته'}")
    if p in missing:
        WARN.append(f"درگاه {p} ({n}) بعد از ری‌استارت هم باز نشد")

# 3) timers (monitor, autoclean, sync …) and failed units -----------------------
OUT.append("\n۳) کارهای زمان‌بندی‌شده")
_, failed = sh("systemctl list-units --failed --no-legend --plain | awk '{print $1}'")
for u in [x for x in failed.split() if x.startswith(("hermes", "antifilter"))]:
    if u == "hermesvpn-divar-fallback.service":      # intentionally off (owner left the group)
        if not DRY:
            sh(["systemctl", "reset-failed", u])
        continue
    if not DRY:
        sh(["systemctl", "reset-failed", u])
    FIXED.append(f"وضعیت خطای {u} پاک شد (بار بعد دوباره اجرا می‌شود)")
_, timers = sh("systemctl list-timers --all --no-legend --plain | awk '{print $NF}'")
bad = [t for t in ("hermesvpn-monitor.timer", "hermesvpn-delayed-sync.timer", "hermesvpn-autoclean.timer",
                   "hermesvpn-sales-nudges.timer", "antifilter-customers.timer") if t in timers and active(t) != "active"]
for t in bad:
    restart(t, "تایمر فعال نبود")
OUT.append("• تایمرها: " + ("✅ همه فعال" if not bad else "🔧 " + "، ".join(bad)))

# 4) disk / memory -----------------------------------------------------------
OUT.append("\n۴) منابع سرور")
st = os.statvfs("/")
used = 100 - st.f_bavail * 100 // st.f_blocks
if used >= 90 and not DRY:
    sh("journalctl --vacuum-size=300M; find /tmp -maxdepth 1 -type f -mtime +2 -delete", 120)
    st = os.statvfs("/"); used2 = 100 - st.f_bavail * 100 // st.f_blocks
    FIXED.append(f"دیسک {used}% پر بود → پاک‌سازی لاگ و موقت → {used2}%"); used = used2
OUT.append(f"• دیسک: {used}% پر" + (" ⚠️" if used >= 90 else ""))
mem = dict((l.split(":")[0], int(l.split()[1])) for l in open("/proc/meminfo") if l.split(":")[0] in ("MemTotal", "MemAvailable"))
OUT.append(f"• حافظه‌ی آزاد: {mem['MemAvailable'] // 1024} MB — بار پردازنده: {open('/proc/loadavg').read().split()[0]}")

# 5) databases -------------------------------------------------------------------
OUT.append("\n۵) دیتابیس‌ها")
for db, n in (("/etc/x-ui/x-ui.db", "پنل"), ("/root/.hermes-orders.db", "سفارش‌ها")):
    try:
        r = sqlite3.connect(f"file:{db}?mode=ro", uri=True).execute("PRAGMA quick_check").fetchone()[0]
        OUT.append(f"• {n}: {'✅ سالم' if r == 'ok' else '⚠️ ' + r}")
        if r != "ok":
            WARN.append(f"دیتابیس {n} مشکل دارد ({r}) — دستی بررسی شود؛ خودکار دست زده نشد")
    except Exception as e:
        OUT.append(f"• {n}: ⚠️ {type(e).__name__}")

# 6) outside world: Telegram, Bale, Cloudflare/sub domain, OpenRouter ------------------------
OUT.append("\n۶) ارتباط با بیرون")
def reach(url, name):
    try:
        urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=12)
        ok = True
    except urllib.error.HTTPError:
        ok = True                        # server answered (4xx) = reachable
    except Exception:
        ok = False
    OUT.append(f"• {name}: {'✅' if ok else '❌'}")
    if not ok:
        WARN.append(f"{name} از سرور در دسترس نیست")
reach("https://api.telegram.org", "تلگرام")
reach("https://tapi.bale.ai", "بله")
reach("https://openrouter.ai/api/v1/models", "هوش مصنوعی (OpenRouter)")
try:
    dom = sqlite3.connect("file:/etc/x-ui/x-ui.db?mode=ro", uri=True).execute(
        "SELECT value FROM settings WHERE key='subDomain'").fetchone()[0]
    ctx = ssl.create_default_context()
    with socket.create_connection((dom, 2087), timeout=10) as s0, ctx.wrap_socket(s0, server_hostname=dom):
        pass
    OUT.append("• دامنه‌ی لینک ساب (Cloudflare): ✅")
except Exception as e:
    OUT.append(f"• دامنه‌ی لینک ساب (Cloudflare): ❌ {type(e).__name__}")
    WARN.append("دامنه‌ی لینک ساب جواب نمی‌دهد — اگر از ایران هم باز نمی‌شود «🌐 رفع فیلترینگ» را بزن")

# 7) reachability from inside Iran (8 datacenter nodes) ------------------------------
OUT.append("\n۷) دسترسی از داخل ایران (۸ نقطه)")
try:
    nodes = "".join(f"&node=ir{i}.node.check-host.net" for i in range(1, 9))
    H = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
    ids = {}
    for name, t in (("Main", "edge.example.com:443"), ("Reality", "203.0.113.10:8443"),
                    ("XHTTP", "edge.example.com:2083")):
        ids[name] = json.load(urllib.request.urlopen(urllib.request.Request(
            f"https://check-host.net/check-tcp?host={t}{nodes}", headers=H), timeout=15))["request_id"]
    time.sleep(12)
    for name, rid in ids.items():
        d = json.load(urllib.request.urlopen(urllib.request.Request(f"https://check-host.net/check-result/{rid}", headers=H), timeout=15))
        answered = [v for v in d.values() if v]            # None = آن نقطه اصلاً جواب نداد (خودِ نود قطع است)
        ok = sum(1 for v in answered if v[0] and "time" in v[0])
        if not answered:
            OUT.append(f"• {name}: نقطه‌های تست ایران جواب ندادند (احتمالاً اینترنت ایران یا خود نودها قطع‌اند؛ مشکل از سرور ما نیست)")
            continue
        OUT.append(f"• {name}: {ok}/{len(answered)} نقطه وصل")
        if ok == 0:
            WARN.append(f"مسیر {name} از هیچ نقطه‌ی فعالِ ایران باز نیست — اگر سایت‌های ایرانی از همان نقطه‌ها باز است، «🌐 رفع فیلترینگ» را بزن")
except Exception as e:
    OUT.append(f"• تست انجام نشد ({type(e).__name__})")

# 8) anti-filter project's own self-heal, if it provides one -------------------------
AF = "/home/ubuntu/antifilter/heal.sh"
if os.path.exists(AF):
    OUT.append("\n۸) ضد فیلتر")
    rc, o = sh(["sudo", "-u", "ubuntu", AF] + (["--dry-run"] if DRY else []), 240)
    OUT.append(o.strip()[-1500:] or ("✅" if rc == 0 else f"⚠️ کد {rc}"))

# verdict -----------------------------------------------------------------------
head = "🛠 رفع کلی مشکل — " + time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()) + (" (آزمایشی)" if DRY else "")
summ = []
if FIXED:
    summ.append("🔧 درست شد:\n" + "\n".join("• " + x for x in FIXED))
if WARN:
    summ.append("⚠️ نیاز به توجه:\n" + "\n".join("• " + x for x in WARN))
if not FIXED and not WARN:
    summ.append("✅ همه‌چیز سالم است؛ مشکلی پیدا نشد.")
print(head + "\n\n" + "\n\n".join(summ) + "\n\n— جزئیات —\n" + "\n".join(OUT))
