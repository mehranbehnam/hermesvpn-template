#!/usr/bin/env python3
"""Hourly: aggregate the HermesVPN admin-bot AI usage (/var/log/hermesvpn-ai.jsonl) into a small public JSON
(only per-day/per-model token and cost totals, no content) for Agent Hermes' token-usage pie chart."""
import collections, json, os, time
SRC = "/var/log/hermesvpn-ai.jsonl"
DST = "/var/lib/hermesvpn-downloads/ai-usage-7f3c9e21b4.json"
agg = collections.defaultdict(lambda: {"calls": 0, "in": 0, "out": 0, "cost": 0.0})
try:
    for ln in open(SRC):
        d = json.loads(ln)
        if d.get("who") == "test":
            continue
        day = time.strftime("%Y-%m-%d", time.gmtime(d.get("ts", 0)))
        k = (day, d.get("model") or "?", d.get("who") or "?")
        a = agg[k]; a["calls"] += 1; a["in"] += d.get("in") or 0; a["out"] += d.get("out") or 0; a["cost"] += float(d.get("cost") or 0)
except FileNotFoundError:
    pass
rows = [{"day": d, "model": m, "who": w, **v} for (d, m, w), v in sorted(agg.items())]
out = {"source": "hermesvpn-admin-bot (oracle)", "updated": int(time.time()), "rows": rows}
tmp = DST + ".tmp"
json.dump(out, open(tmp, "w"))
os.chmod(tmp, 0o644)
os.replace(tmp, DST)
