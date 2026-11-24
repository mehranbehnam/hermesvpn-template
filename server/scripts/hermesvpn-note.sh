#!/bin/bash
# ثبت یک تغییر/تصمیم در دفترچه‌ی تغییرات سند handoff.
# استفاده:  sudo hermesvpn-note.sh "متن تغییر یا تصمیم"

set -euo pipefail
DOC=/root/HERMESVPN-INFRASTRUCTURE-HANDOFF.md

if [ $# -eq 0 ]; then
    echo "استفاده: sudo hermesvpn-note.sh \"متن تغییر یا تصمیم\""
    echo "مثال:   sudo hermesvpn-note.sh \"قیمت پلن ۵۰ گیگ به ۱۲۰ هزار تومان تغییر کرد\""
    exit 1
fi

STAMP=$(TZ=Asia/Tehran date '+%Y-%m-%d %H:%M')
printf '\n- **%s** — %s\n' "$STAMP" "$*" >> "$DOC"

echo "ثبت شد:"
tail -2 "$DOC"
