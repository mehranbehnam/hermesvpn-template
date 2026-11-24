#!/usr/bin/env python3
"""Mirror the client list of inbound 1 (Main, ws) into inbound 3 (XHTTP over Cloudflare, for TCI lines).
Runs as ExecStartPre of x-ui.service (x-ui is stopped then), so every bot change — which always ends
with `systemctl restart x-ui` — also reaches inbound 3 without touching the bots. Never blocks x-ui:
any error is printed and the script exits 0.   usage: hermesvpn-xhttp-sync.py [db]"""
import json, sqlite3, sys, time

DB = sys.argv[1] if len(sys.argv) > 1 else "/etc/x-ui/x-ui.db"
SRC, DST = 1, 3


def main():
    c = sqlite3.connect(DB, timeout=30)
    cur = c.cursor()
    row = cur.execute("SELECT settings FROM inbounds WHERE id=?", (DST,)).fetchone()
    src = cur.execute("SELECT settings FROM inbounds WHERE id=?", (SRC,)).fetchone()
    if not row or not src:
        print("xhttp-sync: inbound missing, nothing to do"); return
    s_src, s_dst = json.loads(src[0]), json.loads(row[0])
    clients = [dict(cl, flow="") for cl in s_src.get("clients", [])]
    changed = 0
    if s_dst.get("clients") != clients:
        s_dst["clients"] = clients
        cur.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(s_dst), DST))
        changed += 1
    want = {r[0] for r in cur.execute("SELECT client_id FROM client_inbounds WHERE inbound_id=?", (SRC,))}
    have = {r[0] for r in cur.execute("SELECT client_id FROM client_inbounds WHERE inbound_id=?", (DST,))}
    now = int(time.time() * 1000)
    for cid in want - have:
        cur.execute("INSERT OR IGNORE INTO client_inbounds (client_id, inbound_id, flow_override, created_at) VALUES (?,?,?,?)",
                    (cid, DST, "", now)); changed += 1
    for cid in have - want:
        cur.execute("DELETE FROM client_inbounds WHERE client_id=? AND inbound_id=?", (cid, DST)); changed += 1
    c.commit(); c.close()
    print(f"xhttp-sync: {len(clients)} clients, {changed} change(s)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("xhttp-sync error (ignored):", e)
    sys.exit(0)
