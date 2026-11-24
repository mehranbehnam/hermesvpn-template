#!/usr/bin/env python3
"""HermesVPN ad rotation (owner request 2026-11-05): posts the poster series one after another.

Runs every 30 minutes (systemd timer). Posts at most one ad per Tehran day, at/after 20:30 Tehran,
cycling ad1..ad5. The very first ad goes out as soon as the sales bot is allowed to post in the channel.
Targets come from /var/lib/hermesvpn-ads/targets.json (default ["@YourChannel"]).
Every caption is checked with hermesvpn_public_text_guard before sending. Owner gets a report in the admin bot.
Never prints tokens.
"""
import datetime, importlib.util, json, os, re, sys, uuid, urllib.parse, urllib.request

ADS = "/var/lib/hermesvpn-ads"
STATE = f"{ADS}/state.json"
ORDER = ["1-windows", "2-devices", "3-trial", "4-invite", "5-plans"]
TEHRAN = datetime.timezone(datetime.timedelta(hours=3, minutes=30))
POST_AT = (20, 30)
OWNER = 1000000001


def token_of(pyfile):
    src = open(pyfile, encoding="utf-8").read()
    return open(re.search(r'^BOT_TOKEN_FILE\s*=\s*"([^"]+)"', src, re.M).group(1)).read().strip()


SALES = token_of("/usr/local/bin/hermes-customer-bot.py")
ADMIN = token_of("/usr/local/bin/hermes-bot.py")


def api(tok, method, **p):
    url = f"https://api.telegram.org/bot{tok}/{method}?" + urllib.parse.urlencode(p)
    try:
        return json.load(urllib.request.urlopen(url, timeout=30))
    except urllib.error.HTTPError as e:
        return json.load(e)


def send_photo(tok, chat, path, caption):
    b = uuid.uuid4().hex
    body = b""
    for k, v in (("chat_id", str(chat)), ("caption", caption)):
        body += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    body += (f'--{b}\r\nContent-Disposition: form-data; name="photo"; filename="ad.jpg"\r\n'
             f'Content-Type: image/jpeg\r\n\r\n').encode() + open(path, "rb").read() + b"\r\n"
    body += f"--{b}--\r\n".encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendPhoto", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=90))
    except urllib.error.HTTPError as e:
        return json.load(e)


def owner(text):
    api(ADMIN, "sendMessage", chat_id=OWNER, text=text)


def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"next": 0, "last_day": "", "blocked_notified": ""}


def save_state(s):
    tmp = STATE + ".tmp"
    json.dump(s, open(tmp, "w"))
    os.replace(tmp, STATE)


def can_post(chat):
    me = api(SALES, "getMe").get("result", {})
    m = api(SALES, "getChatMember", chat_id=chat, user_id=me.get("id")).get("result", {})
    if str(chat).startswith("@") and m.get("status") == "administrator":
        return bool(m.get("can_post_messages"))      # channel
    return m.get("status") in ("administrator", "member", "creator")  # group


def main():
    force = "--now" in sys.argv
    st = load_state()
    now = datetime.datetime.now(TEHRAN)
    today = now.strftime("%Y-%m-%d")
    first = st.get("last_day", "") == ""
    if not force:
        if st.get("last_day") == today:
            return
        if not first and (now.hour, now.minute) < POST_AT:
            return
    try:
        targets = json.load(open(f"{ADS}/targets.json"))
    except Exception:
        targets = ["@YourChannel"]
    ok_targets = [t for t in targets if can_post(t)]
    if not ok_targets:
        if st.get("blocked_notified") != today:
            owner("📣 تبلیغ امروز HermesVPN ارسال نشد: ربات فروش هنوز اجازه‌ی «Post Messages» در کانال را ندارد.\n"
                  "کانال ← Administrators ← YourSalesBot ← Post Messages را روشن کن؛ بعدش خودکار ارسال می‌شود.")
            st["blocked_notified"] = today
            save_state(st)
        return
    name = ORDER[st.get("next", 0) % len(ORDER)]
    caption = open(f"{ADS}/cap{name}.txt", encoding="utf-8").read().strip()
    spec = importlib.util.spec_from_file_location("g", "/usr/local/bin/hermesvpn_public_text_guard.py")
    g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)
    probs = g.check(caption)
    if probs:
        owner(f"📣 تبلیغ «{name}» ارسال نشد — متنش از بررسی متن عمومی رد شد: {'; '.join(probs)}")
        return
    sent, failed = [], []
    for t in ok_targets:
        r = send_photo(SALES, t, f"{ADS}/ad{name}.jpg", caption)
        (sent if r.get("ok") else failed).append(f"{t}{'' if r.get('ok') else ' (' + str(r.get('description')) + ')'}")
    if sent:
        st["next"] = st.get("next", 0) + 1
        st["last_day"] = today
        save_state(st)
    nxt = ORDER[st.get("next", 0) % len(ORDER)]
    owner(f"📣 تبلیغ HermesVPN ارسال شد: «{name}» → {', '.join(sent) or '—'}"
          + (f"\nناموفق: {', '.join(failed)}" if failed else "")
          + f"\nبعدی: «{nxt}» — فردا ساعت ۲۰:۳۰ تهران")
    print("posted", name, sent, failed)


if __name__ == "__main__":
    main()
