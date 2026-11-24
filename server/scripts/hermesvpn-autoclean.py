#!/usr/bin/env python3
"""حذف خودکار اکانت‌های منقضی — ۴۸ ساعت بعد از تاریخ انقضا.

چرا: اکانت‌های تمدیدنشده روی سرور تلنبار می‌شوند و پنل، لیست‌ها و دیتابیس را
شلوغ می‌کنند. ۴۸ ساعت مهلت داده می‌شود تا کسی که یک روز دیر تمدید می‌کند
اکانتش را از دست ندهد.

منطق حذف عمداً از خودِ ربات مدیریت import می‌شود (delete_client) و دوباره
نوشته نشده: یک کلاینت در سه جا زندگی می‌کند (clients+client_inbounds، JSON
داخل inbounds.settings، و client_traffics) و هر پیاده‌سازی موازی دیر یا زود
از قانون طلایی منحرف می‌شود. hermes-bot.py گاردِ __main__ دارد، پس import
حلقه‌ی ربات را اجرا نمی‌کند.
"""
import importlib.util
import sqlite3
import sys
import time

GRACE_HOURS = 48
BOT_PATH = "/usr/local/bin/hermes-bot.py"
LOG_PATH = "/var/log/hermesvpn-autoclean.log"

spec = importlib.util.spec_from_file_location("hermes_bot", BOT_PATH)
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def expired_beyond_grace():
    """اکانت‌هایی که بیش از GRACE_HOURS از انقضاشان گذشته.
    expiry_time == 0 یعنی بدون انقضا — هرگز حذف نمی‌شود."""
    cutoff = int((time.time() - GRACE_HOURS * 3600) * 1000)
    c = sqlite3.connect(bot.DB)
    rows = list(c.execute(
        "SELECT email, expiry_time FROM clients "
        "WHERE expiry_time > 0 AND expiry_time < ? ORDER BY expiry_time",
        (cutoff,)))
    c.close()
    return rows


def main():
    doomed = expired_beyond_grace()
    if not doomed:
        return

    deleted, failed = [], []
    for email, expiry in doomed:
        try:
            # restart=False: یک ری‌استارت در پایان، نه یکی به‌ازای هر حذف —
            # وگرنه هر حذف یک قطعی کوتاه برای همه‌ی مشتری‌هاست.
            if bot.delete_client(email, restart=False):
                deleted.append((email, expiry))
            else:
                failed.append(email)
        except Exception as e:
            failed.append(f"{email} ({e})")

    if deleted:
        import subprocess
        subprocess.run(["systemctl", "restart", "x-ui"], check=True)

    log(f"deleted={len(deleted)} failed={len(failed)} "
        f"emails={[e for e, _ in deleted]}")

    now_ms = time.time() * 1000
    lines = [f"🧹 پاک‌سازی خودکار — {len(deleted)} اکانت منقضی حذف شد",
             f"(بیش از {GRACE_HOURS} ساعت از انقضاشون گذشته بود)", ""]
    for email, expiry in deleted:
        hours = int((now_ms - expiry) / 3600000)
        lines.append(f"• {email} — {hours} ساعت پیش منقضی شده بود")
    if failed:
        lines.append("")
        lines.append("⚠️ حذف نشد: " + "، ".join(str(x) for x in failed[:10]))

    for chat_id in bot.ALLOWED_CHAT_IDS:
        try:
            bot.send_message(chat_id, "\n".join(lines))
        except Exception as e:
            log(f"telegram notify failed: {e}")


if __name__ == "__main__":
    if "--dry-run" in sys.argv:
        rows = expired_beyond_grace()
        now_ms = time.time() * 1000
        print(f"اجرای آزمایشی — {len(rows)} اکانت مشمول حذف:")
        for email, expiry in rows:
            print(f"  {email} — {int((now_ms - expiry) / 3600000)} ساعت پیش منقضی شده")
        sys.exit(0)
    main()
