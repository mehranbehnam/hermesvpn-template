#!/usr/bin/env python3
"""
HermesVPN daily financial reconciliation — runs once every 24h via a
systemd timer.

Context: the customer sales bot auto-approves a payment the moment its
amount checks out (from the bank SMS webhook, or from OCR on a receipt
photo), with no live manual-approval gate in auto mode — deliberately, so a
paying customer is never left waiting on the admin possibly missing a
Telegram notification. The one case OCR can't read with full confidence is
when it has to correct a dropped digit (e.g. '600' -> '60,000' because
×100 exactly matches the order price); that still auto-approves, but is
flagged as receipt_ocr_corrected=1.

This script is the actual safety net for that design: once a day it
re-checks EVERY order approved in the last 24 hours against its own
recorded receipt data. Per explicit request, the result is ONE PDF file
(not a flood of separate chat messages) with every order's numbers AND its
receipt photo embedded, so the admin can flip through and eyeball-verify
each purchase in one document instead of digging through the chat.
"""
import sqlite3, time, os, io
import urllib.request, urllib.parse, json

from fpdf import FPDF
from fpdf.fonts import FontFace
from fpdf.enums import TableCellFillMode
import arabic_reshaper
from bidi.algorithm import get_display
from PIL import Image

ORDERS_DB = "/root/.hermes-orders.db"
# گزارش روزانه باید در *ربات ادمین* بیاید نه ربات مشتری. با توکن ربات
# مشتری فرستاده می‌شد و چون شناسه‌ی ادمین و خریدار یکی است، وسط چت خرید
# می‌افتاد — جایی که مشتری هم آن را می‌بیند.
BOT_TOKEN_FILE = "/root/.hermes-bot-token"
ADMIN_CHAT_ID = 1000000001
VAZIR_REGULAR = "/usr/share/fonts/vazirmatn/Vazirmatn-Regular.ttf"
VAZIR_BOLD = "/usr/share/fonts/vazirmatn/Vazirmatn-Bold.ttf"
BRAND_COLOR = (30, 60, 120)
LIGHT_COLOR = (235, 240, 250)
TMP_DIR = "/tmp/hermesvpn-daily-audit"

with open(BOT_TOKEN_FILE) as f:
    BOT_TOKEN = f.read().strip()
API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def rtl(text):
    return get_display(arabic_reshaper.reshape(str(text)))


def api_call(method, data=None):
    url = f"{API}/{method}"
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data or {}).encode())
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def send_message(text):
    try:
        api_call("sendMessage", {"chat_id": ADMIN_CHAT_ID, "text": text})
    except Exception as e:
        print("telegram send_message failed:", e)


def send_document(path, caption=None):
    url = f"{API}/sendDocument"
    data = {"chat_id": ADMIN_CHAT_ID}
    if caption:
        data["caption"] = caption
    boundary = "----HermesAuditBoundary"
    body = b""
    for k, v in data.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    with open(path, "rb") as f:
        filedata = f.read()
    body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{os.path.basename(path)}\"\r\nContent-Type: application/pdf\r\n\r\n".encode()
    body += filedata
    body += f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print("telegram send_document failed:", e)
        return None


def download_receipt_image(file_id, out_path):
    info = api_call("getFile", {"file_id": file_id})
    file_path = info["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = resp.read()
    with open(out_path, "wb") as f:
        f.write(data)
    return out_path


def order_status(receipt_amount, total_price, corrected):
    if receipt_amount is None:
        return "[بدون رسید] پیامک بانک یا تأیید دستی صرف"
    if receipt_amount != total_price:
        return f"[عدم تطابق] رسید: {receipt_amount:,} / سفارش: {total_price:,}"
    if corrected:
        return "[تصحیح حدسی OCR] مطابقت داره، ولی حدسیه — بازم چک کن"
    return "[تأیید کامل] مطابقت داره"


def add_order_page(pdf, oid, who, client_email, plan_key, quantity, total_price, receipt_amount, corrected, tracking, date, image_path):
    pdf.add_page()
    pdf.set_font("Vazir", "B", 14)
    qty_note = f" ×{quantity}" if quantity and quantity > 1 else ""
    pdf.cell(0, 10, text=rtl(f"سفارش #{oid} — {who}"), align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Vazir", "B", 12)
    emails = [e.strip() for e in (client_email or "").split(",") if e.strip()]
    if len(emails) <= 1:
        pdf.cell(0, 8, text=rtl(f"شناسه‌ی اکانت: {emails[0] if emails else '—'}"), align="R", new_x="LMARGIN", new_y="NEXT")
    else:
        # bulk order: one payment covers the whole batch, so list every
        # account it provisioned, one per line, so admin can copy the exact
        # identifier for any of them individually if needed later
        pdf.cell(0, 8, text=rtl(f"شناسه‌های اکانت ({len(emails)} تایی، یه پرداخت واحد):"), align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Vazir", "", 10)
        for e in emails:
            pdf.cell(0, 6, text=rtl(f"  {e}"), align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Vazir", "", 11)
    pdf.cell(0, 8, text=rtl(f"پلن: {plan_key}{qty_note} — {total_price:,} تومان"), align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, text=rtl(f"کد پیگیری/تاریخ: {tracking or '—'} / {date or '—'}"), align="R", new_x="LMARGIN", new_y="NEXT")
    status = order_status(receipt_amount, total_price, corrected)
    fill = LIGHT_COLOR
    pdf.set_fill_color(*fill)
    pdf.set_font("Vazir", "B", 11)
    pdf.multi_cell(0, 8, text=rtl(status), align="R", fill=True)
    pdf.ln(4)

    if image_path and os.path.exists(image_path):
        try:
            img = Image.open(image_path)
            w, h = img.size
            max_w, max_h = 190, 220
            ratio = min(max_w / w, max_h / h)
            disp_w, disp_h = w * ratio, h * ratio
            x = (210 - disp_w) / 2
            pdf.image(image_path, x=x, y=pdf.get_y(), w=disp_w, h=disp_h)
        except Exception as e:
            pdf.set_font("Vazir", "", 10)
            pdf.cell(0, 8, text=rtl(f"[خطا در نمایش عکس رسید: {e}]"), align="R")
    else:
        pdf.set_font("Vazir", "", 10)
        pdf.cell(0, 8, text=rtl("عکس رسیدی برای این سفارش ثبت نشده."), align="R")


def main():
    os.makedirs(TMP_DIR, exist_ok=True)
    now_ms = int(time.time() * 1000)
    since_ms = now_ms - 24 * 3600 * 1000

    c = sqlite3.connect(ORDERS_DB)
    cur = c.cursor()
    cur.execute("""SELECT id, tg_username, tg_name, plan_key, quantity, total_price,
                          receipt_amount, receipt_ocr_corrected, receipt_tracking, receipt_date, receipt_file_id, client_email
                   FROM orders WHERE status='approved' AND decided_at>=?
                   ORDER BY id""", (since_ms,))
    rows = cur.fetchall()
    c.close()

    total_count = len(rows)
    total_revenue = sum((r[5] or 0) for r in rows)
    mismatches = sum(1 for r in rows if r[6] is not None and r[6] != r[5])
    guessed = sum(1 for r in rows if r[7])
    no_receipt = sum(1 for r in rows if r[6] is None)
    clean = total_count - mismatches - guessed - no_receipt

    pdf = FPDF()
    pdf.add_font("Vazir", "", VAZIR_REGULAR)
    pdf.add_font("Vazir", "B", VAZIR_BOLD)

    # cover / summary page
    pdf.add_page()
    pdf.set_fill_color(*BRAND_COLOR)
    pdf.rect(0, 0, 210, 28, style="F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_xy(10, 6)
    pdf.set_font("Vazir", "B", 18)
    pdf.cell(190, 10, text=rtl("HermesVPN — گزارش تطبیق مالی ۲۴ ساعت"), align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Vazir", "", 10)
    pdf.set_xy(10, 17)
    pdf.cell(190, 8, text=rtl(f"تاریخ تولید گزارش: {time.strftime('%Y-%m-%d %H:%M')} UTC"), align="R")

    pdf.set_text_color(0, 0, 0)
    pdf.set_y(35)
    pdf.set_fill_color(*LIGHT_COLOR)
    pdf.set_draw_color(*BRAND_COLOR)
    pdf.rect(10, pdf.get_y(), 190, 30, style="DF")
    pdf.set_font("Vazir", "B", 13)
    pdf.set_xy(10, pdf.get_y() + 4)
    pdf.cell(190, 8, text=rtl(f"{total_count} سفارش تأییدشده — جمع فروش: {total_revenue:,} تومان"), align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Vazir", "", 11)
    pdf.set_x(10)
    summary_bits = [f"تأیید کامل: {clean}"]
    if mismatches:
        summary_bits.append(f"عدم تطابق: {mismatches}")
    if guessed:
        summary_bits.append(f"تصحیح حدسی: {guessed}")
    if no_receipt:
        summary_bits.append(f"بدون رسید: {no_receipt}")
    pdf.cell(190, 8, text=rtl("   |   ".join(summary_bits)), align="C")

    for oid, uname, name, plan_key, quantity, total_price, receipt_amount, corrected, tracking, date, file_id, client_email in rows:
        who = f"@{uname}" if uname else (name or "?")
        image_path = None
        if file_id:
            image_path = os.path.join(TMP_DIR, f"{oid}.jpg")
            try:
                download_receipt_image(file_id, image_path)
            except Exception as e:
                print(f"failed to download receipt for order {oid}: {e}")
                image_path = None
        add_order_page(pdf, oid, who, client_email, plan_key, quantity, total_price, receipt_amount, corrected, tracking, date, image_path)

    out_path = f"{TMP_DIR}/report-{time.strftime('%Y%m%d')}.pdf"
    pdf.output(out_path)

    send_document(out_path, caption=f"📋 گزارش تطبیق مالی ۲۴ ساعت گذشته — {total_count} سفارش، {total_revenue:,} تومان")

    for f in os.listdir(TMP_DIR):
        try:
            os.remove(os.path.join(TMP_DIR, f))
        except Exception:
            pass

    print(f"daily audit PDF sent: {total_count} orders, {mismatches} mismatches, {guessed} guessed, {no_receipt} no-receipt")


if __name__ == "__main__":
    main()
