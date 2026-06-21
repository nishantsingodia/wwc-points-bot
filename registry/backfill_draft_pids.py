#!/usr/bin/env python3
"""Backfill the stable registry `pid` into wwc-draft/data/players-raw.json.

The draft keeps its integer `id` as its internal key (draft_picks reference it — never
change it). We ADD a `pid` field = the registry's stable identity, so the draft can join
the points sheet by Player ID instead of fuzzy-matching names. Players the registry doesn't
cover keep no pid and fall back to the existing fuzzy lookup. Idempotent; ADD-only.
"""
import os, json, re, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
DRAFT_RAW = os.environ.get("DRAFT_RAW", "/Users/nishant-singodia/wwc-draft/data/players-raw.json")

def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s.lower())).strip()

reg = json.load(open(os.path.join(HERE, "players.json")))["players"]
alias2pid, draftid2pid = {}, {}
for pid, e in reg.items():
    for a in e.get("aliases", []):
        alias2pid.setdefault(a, pid)
    if e.get("draft_id") is not None:
        draftid2pid[e["draft_id"]] = pid

raw = json.load(open(DRAFT_RAW))
players = raw if isinstance(raw, list) else raw.get("players", raw)
hit = 0
for p in players:
    pid = draftid2pid.get(p.get("id")) or alias2pid.get(norm(p.get("name", "")))
    if pid:
        p["pid"] = pid; hit += 1
json.dump(raw, open(DRAFT_RAW, "w"), indent=2, ensure_ascii=False)
print(f"backfilled pid into {hit}/{len(players)} draft players -> {DRAFT_RAW}")
