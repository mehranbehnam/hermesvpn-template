#!/usr/bin/env python3
"""Weekend festival guarantee (owner 2026-11-08: ads promise 20% from Thursday 01:00 to Saturday 01:00 Tehran,
so the sales bot must apply it even if Hetzner/Agent-Hermes is down). Runs Wednesday 21:30 UTC (Thursday 01:00 Tehran):
sets settings.flash_sale = {pct 20, until Saturday 01:00 Tehran} (48 h, owner 2026-11-08). Never shortens or lowers a sale already running."""
import datetime, json, sqlite3
DB = "/root/.hermes-orders.db"
now = datetime.datetime.now(datetime.timezone.utc)
tehran = datetime.timezone(datetime.timedelta(hours=3, minutes=30))
t = now.astimezone(tehran)
days_to_sat = (5 - t.weekday()) % 7 or 7          # Mon=0 … Sat=5
until = (t + datetime.timedelta(days=days_to_sat)).replace(hour=1, minute=0, second=0, microsecond=0)
until_ms = int(until.timestamp() * 1000)
c = sqlite3.connect(DB)
row = c.execute("SELECT value FROM settings WHERE key='flash_sale'").fetchone()
cur = json.loads(row[0]) if row and row[0] else {}
if cur.get("until", 0) >= until_ms and int(cur.get("pct", 0)) >= 20:
    print("weekend sale already set:", cur)
else:
    v = json.dumps({"pct": 20, "until": until_ms, "reason": "weekend"})
    c.execute("INSERT INTO settings(key,value) VALUES('flash_sale',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (v,))
    c.commit(); print("weekend sale set until", until.isoformat())
c.close()
