"""Guard for every text that reaches customers / the public channel & group (owner, 2026-10-24):
no internal notes, pack names, previews, test text, code, paths or stray English words.
check(text) -> list of problems (empty list = OK). CLI: public_text_guard.py "<text>" (exit 1 if bad)."""
import re, sys

ALLOWED_LATIN = {"hermes", "main", "reality", "vpn", "hermesvpn", "iranvpn", "iran", "agent", "ai", "v2box", "v2ray", "android", "iphone", "ios", "windows",
                 "gb", "telegram", "vless", "wifi", "usdt", "trc20", "ok", "new", "free", "now", "get", "connect",
                 "the", "fastest", "most", "secure", "connection", "app", "bot", "online", "vip", "pro"}
INTERNAL = [r"\bpack\b", r"\bپک\b", r"sticker", r"استیکر", r"نمونه", r"پیش‌?نمایش", r"\bpreview\b", r"\bdry\b",
            r"\btest\b", r"این (یک|یه) تست", r"\bdebug\b", r"file_id", r"/root", r"\.py\b", r"\bsudo\b", r"\bssh\b",
            r"traceback", r"\berror\b", r"\bNone\b", r"\bnull\b", r"localhost", r"127\.0\.0\.1", r"```", r"\{[a-z_]*\}",
            r"\bTODO\b", r"\bpremiumgifts\b", r"animatedemoj", r"\bcron\b", r"\bscript\b", r"اسکریپت", r"کرون"]


def check(text):
    t = text or ""
    probs = []
    if len(t.strip()) < 2:
        probs.append("empty text")
    for pat in INTERNAL:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            probs.append(f"internal/technical word: {m.group(0)!r}")
    stripped = re.sub(r"https?://\S+|t\.me/\S+|@\w+|[\w.-]+\.(com|ir|net|org|me)\b(/\S*)?", " ", t)
    for w in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", stripped):
        if w.lower() not in ALLOWED_LATIN:
            probs.append(f"stray English word: {w!r}")
    return sorted(set(probs))


if __name__ == "__main__":
    p = check(sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read())
    print("OK" if not p else "BLOCKED: " + "; ".join(p))
    sys.exit(1 if p else 0)
