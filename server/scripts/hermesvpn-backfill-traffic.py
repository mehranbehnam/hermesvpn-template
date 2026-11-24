#!/usr/bin/env python3
"""One-off backfill: every client in `clients` must have a matching row in
`client_traffics`, otherwise x-ui silently discards that account's usage
(it only UPDATEs existing rows, it never INSERTs). Clients provisioned
directly by the bots had no row, so their quota always read as untouched.

Rows are seeded at up=0/down=0 on purpose: whatever Xray has accumulated in
memory for them gets written by x-ui's own traffic job on its next cycle, so
nothing is lost and nothing is double-counted.
"""
import sqlite3, sys

DB = "/etc/x-ui/x-ui.db"

c = sqlite3.connect(DB)
cur = c.cursor()

cur.execute("""SELECT c.email, c.total_gb, c.expiry_time, c.enable
               FROM clients c
               LEFT JOIN client_traffics t ON t.email = c.email
               WHERE t.email IS NULL""")
missing = cur.fetchall()

if not missing:
    print("nothing to backfill — every client already has a traffic row")
    sys.exit(0)

print(f"backfilling {len(missing)} clients:")
for email, total_gb, expiry, enable in missing:
    cur.execute("""INSERT INTO client_traffics
        (inbound_id, enable, email, up, down, expiry_time, total,
         reset, reset_day, reset_max, reset_count, last_online, last_sub_fetch)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (1, enable, email, 0, 0, expiry, total_gb, 0, 0, 0, 0, 0, 0))
    print(f"  + {email}  total={total_gb}  expiry={expiry}")

c.commit()

cur.execute("SELECT COUNT(*) FROM clients")
a = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM client_traffics")
b = cur.fetchone()[0]
c.close()
print(f"\ndone: clients={a}  traffic_rows={b}")
