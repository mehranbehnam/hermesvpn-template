#!/usr/bin/env python3
"""HermesVPN monthly egress (outbound traffic) alert — owner request 2026-11-07.

Oracle's free tier includes 10 TB of outbound traffic per month. This script (timer, every
10 min) adds up the server's outbound bytes for the current calendar month (UTC, as Oracle
bills) from the NIC counters, survives reboots/counter resets, and sends the owner ONE admin-bot
message per threshold per month: 7 TB (warning), 9 TB (critical), 10 TB (free quota used up).
First month: the part of the month before this script existed is estimated from the average
rate since boot (marked as estimate).  `--status` prints the current figures (no message).
"""
import datetime, json, os, re, sys, urllib.parse, urllib.request

STATE = "/var/lib/hermesvpn-egress.json"
IFACE = "enp0s6"
TB = 1e12
THRESHOLDS = [(7, "⚠️ هشدار"), (9, "🔴 بحرانی"), (10, "🚨 سهمیه‌ی رایگان تمام شد")]
OWNER = 1000000001


def admin_token():
    src = open("/usr/local/bin/hermes-bot.py", encoding="utf-8").read()
    return open(re.search(r'^BOT_TOKEN_FILE\s*=\s*"([^"]+)"', src, re.M).group(1)).read().strip()


def tx_bytes():
    for line in open("/proc/net/dev"):
        if line.strip().startswith(IFACE + ":"):
            return int(line.split(":", 1)[1].split()[8])
    raise SystemExit(f"interface {IFACE} not found")


def boot_id():
    return open("/proc/sys/kernel/random/boot_id").read().strip()


def uptime_s():
    return float(open("/proc/uptime").read().split()[0])


def send(text):
    data = urllib.parse.urlencode({"chat_id": OWNER, "text": text}).encode()
    urllib.request.urlopen(urllib.request.Request(
        f"https://api.telegram.org/bot{admin_token()}/sendMessage", data=data), timeout=20)


def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    month = now.strftime("%Y-%m")
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    tx, bid = tx_bytes(), boot_id()
    try:
        st = json.load(open(STATE))
    except Exception:
        st = None
    if st is None:
        # first run: estimate this month's earlier traffic from the average rate since boot
        elapsed = (now - month_start).total_seconds()
        up = uptime_s()
        est = tx if up <= elapsed else tx * elapsed / up
        st = {"month": month, "bytes": est, "last_tx": tx, "boot_id": bid, "alerted": [], "estimated": True}
    else:
        delta = tx - st["last_tx"] if (st.get("boot_id") == bid and tx >= st["last_tx"]) else tx
        if st["month"] != month:
            st = {"month": month, "bytes": 0.0, "alerted": [], "estimated": False}
        st["bytes"] += max(0, delta)
        st["last_tx"], st["boot_id"] = tx, bid
    # projection for the whole month at the current month-to-date pace
    days_in_month = ((month_start + datetime.timedelta(days=32)).replace(day=1) - month_start).days
    elapsed_days = max((now - month_start).total_seconds() / 86400, 0.1)
    proj = st["bytes"] / elapsed_days * days_in_month
    used_tb = st["bytes"] / TB
    if "--status" in sys.argv:
        print(f"{month}: {used_tb:.2f} TB used, projected {proj / TB:.2f} TB"
              + (" (part estimated)" if st.get("estimated") else ""))
    for limit, label in THRESHOLDS:
        if used_tb >= limit and limit not in st["alerted"]:
            try:
                send(f"{label} ترافیک ماهانه‌ی سرور HermesVPN\n\n"
                     f"مصرف این ماه ({month}): {used_tb:.2f} ترابایت از ۱۰ ترابایت رایگان اوراکل\n"
                     f"پیش‌بینی تا آخر ماه با همین روند: {proj / TB:.1f} ترابایت\n\n"
                     + ("بیشتر از ۱۰ ترابایت را اوراکل جداگانه حساب می‌کند؛ سرور قطع نمی‌شود."
                        if limit >= 10 else "وقت فکر کردن به سرور دوم یا کنترل پلن‌های پرمصرف است."))
                st["alerted"].append(limit)
            except Exception as e:
                print("alert send failed:", e)
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w"))
    os.replace(tmp, STATE)


if __name__ == "__main__":
    main()
