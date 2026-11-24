#!/usr/bin/env python3
"""HermesVPN buyer broadcast via the SALES bot (@YourSalesBot).
Sends to every customer with an approved order. Big animated stickers before/after the text.
  hermesvpn-buyer-broadcast.py --dry-run                       # count + preview, sends nothing
  hermesvpn-buyer-broadcast.py --before 🎁 --after 🎆           # standard reminder (rebuy + referral link)
  hermesvpn-buyer-broadcast.py --text "متن {name} {link}" --before 🎉
{name} = customer name, {link} = the customer's personal referral link.
Stickers: ONLY big animated stickers (PremiumGifts for 🎁, AnimatedEmojies, ...). An emoji without a
big sticker is replaced by a big festive one (🎉🎊🥳🎆) — never sent as a small emoji.
"""
import argparse, importlib.util, json, sqlite3, sys, time, urllib.parse, urllib.request

TOKEN = open("/root/.hermes-customer-bot-token").read().strip()
BIG_PACKS = ["PremiumGifts", "AnimatedEmojies", "EmojiAnimations", "AnimatedEmojiPack", "MotionEmoji"]
FALLBACK = ["🎉", "🎊", "🥳", "🎆", "✨", "💥", "🎈"]  # owner: ONLY big stickers, never a small bare emoji


def api(method, **p):
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/{method}", data=urllib.parse.urlencode(p).encode())
    return json.load(urllib.request.urlopen(req, timeout=20))["result"]


def admin_module():
    spec = importlib.util.spec_from_file_location("hb", "/usr/local/bin/hermes-bot.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def sticker_map():
    out = {}
    for name in BIG_PACKS:
        try:
            for s in api("getStickerSet", name=name)["stickers"]:
                out.setdefault((s.get("emoji") or "").replace("️", ""), s["file_id"])
        except Exception:
            pass
    return out


def resolve(e, smap):
    fid = smap.get(e.replace("\ufe0f", ""))
    if fid:
        return fid, e
    for alt in FALLBACK:
        if smap.get(alt):
            return smap[alt], alt
    return None, None


def send_emojis(chat_id, emojis, smap):
    for e in emojis:
        fid, _ = resolve(e, smap)
        if fid:
            api("sendSticker", chat_id=chat_id, sticker=fid)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--before", default="", help="emojis sent as big stickers BEFORE the text, comma/space separated")
    ap.add_argument("--after", default="", help="emojis sent as big stickers AFTER the text")
    ap.add_argument("--text", help="custom text; {name} and {link} placeholders. Default: rebuy + referral reminder")
    a = ap.parse_args()
    m = admin_module()
    buyers = m.buyer_ids_for_reminder()
    split = lambda s: [x for x in s.replace(",", " ").split() if x]
    before, after = split(a.before), split(a.after)
    link = lambda tg: f"https://t.me/{m.SALES_BOT_USERNAME}?start=ref_{tg}"
    render = (lambda tg, name: a.text.replace("{name}", name or "").replace("{link}", link(tg))) if a.text else m.buyer_reminder_text
    last = m.last_reminder_ts()
    hours = (time.time() - last) / 3600 if last else None
    sys.path.insert(0, "/usr/local/bin")
    from hermesvpn_public_text_guard import check
    problems = check(render(123456789, "علی"))
    if problems:
        print("BLOCKED by public text guard (nothing sent): " + "; ".join(problems))
        sys.exit(3)
    if a.dry_run:
        print(f"DRY RUN: would send to {len(buyers)} buyers; before={before} after={after}")
        if hours is not None:
            print(f"last broadcast {hours:.1f}h ago" + (" — WARNING: under 24h, users may block the bot" if hours < 24 else ""))
        smap = sticker_map()
        for e in before + after:
            fid, used = resolve(e, smap)
            print(f"sticker {e}: " + ("big sticker OK" if used == e else (f"not in big packs -> replaced with big {used}" if fid else "NOT FOUND, skipped")))
        print("---- preview ----\n" + render(123456789, "علی"))
        return
    smap = sticker_map() if (before or after) else {}
    ok = fail = 0
    for tg, name in buyers:
        try:
            body = render(tg, name)
            if check(body):
                print("skip", tg, "text failed guard:", check(body)); fail += 1; continue
            send_emojis(tg, before, smap)
            api("sendMessage", chat_id=tg, text=body)
            send_emojis(tg, after, smap)
            ok += 1
        except Exception as e:
            fail += 1
            print("fail", tg, str(e)[:80])
        time.sleep(0.6)
    c = sqlite3.connect(m.ORDERS_DB)
    c.execute("INSERT INTO settings (key, value) VALUES ('buyer_reminder_ts', ?) "
              "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(int(time.time())),))
    c.commit()
    c.close()
    print(f"sent to {ok} buyers, {fail} failed")


if __name__ == "__main__":
    main()
