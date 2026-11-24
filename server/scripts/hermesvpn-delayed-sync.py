#!/usr/bin/env python3
"""HermesVPN delayed-start sync: when x-ui starts an account on its first
connection it rewrites client_traffics.expiry_time (negative -> real date).
Copy the real date into `clients` and the inbounds JSON too. Idempotent."""
import json, sqlite3, time
DB = "/etc/x-ui/x-ui.db"
c = sqlite3.connect(DB, timeout=30)
cur = c.cursor()
rows = cur.execute(
    "SELECT cl.email, ct.expiry_time FROM clients cl "
    "JOIN client_traffics ct ON ct.email = cl.email "
    "WHERE cl.expiry_time < 0 AND ct.expiry_time > 0").fetchall()
if rows:
    started = dict(rows)
    now_ms = int(time.time() * 1000)
    for email, exp in rows:
        cur.execute("UPDATE clients SET expiry_time=?, updated_at=? WHERE email=? AND expiry_time < 0",
                    (exp, now_ms, email))
    for iid, settings in cur.execute("SELECT id, settings FROM inbounds WHERE id IN (1,2)").fetchall():
        data = json.loads(settings)
        changed = False
        for cl in data.get("clients", []):
            if cl.get("email") in started and (cl.get("expiryTime") or 0) < 0:
                cl["expiryTime"] = started[cl["email"]]
                changed = True
        if changed:
            cur.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(data), iid))
    c.commit()
    print(f"started & synced: {len(rows)} -> " + ", ".join(e for e, _ in rows))
c.close()
