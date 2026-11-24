#!/usr/bin/env python3
"""Consistency audit across the three places a client exists: the `clients`
table (panel), the inbounds.settings JSON (what Xray actually enforces), and
client_traffics (usage/quota meter). Read-only — reports, changes nothing."""
import json, sqlite3, time

DB = "/etc/x-ui/x-ui.db"
now = int(time.time() * 1000)

c = sqlite3.connect(DB)
cur = c.cursor()

cur.execute("SELECT email, total_gb, expiry_time, enable FROM clients")
clients = {r[0]: {"total": r[1], "expiry": r[2], "enable": r[3]} for r in cur.fetchall()}

cur.execute("SELECT email, up, down, total, expiry_time, enable FROM client_traffics")
traffics = {r[0]: {"up": r[1] or 0, "down": r[2] or 0, "total": r[3], "expiry": r[4], "enable": r[5]}
            for r in cur.fetchall()}

inbound = {}
cur.execute("SELECT id, settings FROM inbounds WHERE id IN (1,2) ORDER BY id")
for iid, s in cur.fetchall():
    for cl in json.loads(s)["clients"]:
        inbound.setdefault(cl["email"], {})[iid] = cl
c.close()

problems = []

# 1. every client present in all three places, on both inbounds
for email, cl in clients.items():
    if email not in traffics:
        problems.append(f"NO METER      {email} — usage will be discarded by x-ui")
    inb = inbound.get(email, {})
    for iid in (1, 2):
        if iid not in inb:
            problems.append(f"NOT IN XRAY   {email} — missing from inbound {iid}, link won't work")

# 2. nothing left behind after a delete
for email in traffics:
    if email not in clients:
        problems.append(f"ORPHAN METER  {email} — traffic row with no client")
for email in inbound:
    if email not in clients:
        problems.append(f"ORPHAN XRAY   {email} — in inbound JSON but not in clients table")

# 3. the three copies of quota/expiry/enable must agree
for email, cl in clients.items():
    t = traffics.get(email)
    if t:
        if t["total"] != cl["total"]:
            problems.append(f"QUOTA DRIFT   {email} — clients={cl['total']} meter={t['total']}")
        if t["expiry"] != cl["expiry"]:
            problems.append(f"EXPIRY DRIFT  {email} — clients={cl['expiry']} meter={t['expiry']}")
        if bool(t["enable"]) != bool(cl["enable"]):
            problems.append(f"ENABLE DRIFT  {email} — clients={cl['enable']} meter={t['enable']}")
    for iid, j in inbound.get(email, {}).items():
        if j.get("totalGB") != cl["total"]:
            problems.append(f"QUOTA DRIFT   {email} — clients={cl['total']} inbound{iid}={j.get('totalGB')}")
        if j.get("expiryTime") != cl["expiry"]:
            problems.append(f"EXPIRY DRIFT  {email} — clients={cl['expiry']} inbound{iid}={j.get('expiryTime')}")
        if bool(j.get("enable")) != bool(cl["enable"]):
            problems.append(f"ENABLE DRIFT  {email} — clients={cl['enable']} inbound{iid}={j.get('enable')}")

# 4. accounts that should no longer be usable but still are
for email, cl in clients.items():
    t = traffics.get(email, {})
    used = t.get("up", 0) + t.get("down", 0)
    if cl["enable"]:
        if cl["expiry"] and cl["expiry"] > 0 and cl["expiry"] < now:
            days = (now - cl["expiry"]) / 86400000
            problems.append(f"EXPIRED LIVE  {email} — expired {days:.1f} days ago, still enabled")
        if cl["total"] and used >= cl["total"]:
            problems.append(f"OVER QUOTA    {email} — used {used/1024**3:.2f}GB of {cl['total']/1024**3:.0f}GB, still enabled")

print(f"clients={len(clients)}  meters={len(traffics)}  in_xray={len(inbound)}")
print(f"problems found: {len(problems)}\n")
for p in sorted(problems):
    print(" ", p)
