#!/bin/bash
# Render the monthly egress chart (as user ubuntu, headless Chromium) and send it to the owner via the admin bot.
# Used by: admin-bot button "📊 نمودار ترافیک" and the weekly Friday timer.
set -e
OUT=/tmp/hermesvpn-egress-$(date +%s).png
runuser -u ubuntu -- python3 /usr/local/lib/hermesvpn/egress_chart.py "$OUT" >/dev/null
python3 - "$OUT" <<'PY'
import json, re, sys, uuid, urllib.request
src = open('/usr/local/bin/hermes-bot.py', encoding='utf-8').read()
tok = open(re.search(r'^BOT_TOKEN_FILE\s*=\s*"([^"]+)"', src, re.M).group(1)).read().strip()
cap = "📊 ترافیک ماهانه‌ی سرور HermesVPN"
b = uuid.uuid4().hex; body = b""
# main admin menu (persistent) rides along so the keyboard never disappears
# menu became categorized (2026-11-14): main_menu needs MENU_* / CUR_MENU defined just above it
mm = src[src.index('MENU_HOME = '):src.index('\ndef dashboard_text')]
ns = {}; exec(mm, ns); kb = json.dumps(ns["main_menu"](), ensure_ascii=False)
for k, v in (("chat_id", "1000000001"), ("caption", cap), ("reply_markup", kb)):
    body += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
body += f'--{b}\r\nContent-Disposition: form-data; name="photo"; filename="traffic.png"\r\nContent-Type: image/png\r\n\r\n'.encode() + open(sys.argv[1], 'rb').read() + b"\r\n" + f"--{b}--\r\n".encode()
r = json.load(urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendPhoto", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={b}"}), timeout=60))
print("sent:", r.get("ok"))
PY
rm -f "$OUT"
