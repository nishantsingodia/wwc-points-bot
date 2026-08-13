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

# manual_ci_bridges is a HUMAN-VERIFIED name -> cricinfo id mapping, so it is at least as
# authoritative as anything in players.json. It was not consulted here, which meant a bridged
# player whose TOUR was never registered still got no pid: the India ODI leg (OIND) exists in the
# draft but not in tours.json, so Rohit Sharma, Shubman Gill, Virat Kohli, KL Rahul, Kuldeep Yadav,
# Jasprit Bumrah and Gurnoor Brar shipped on placeholder slug: pids and would have settled at ZERO
# (the draft refuses to fuzzy-fall-back for a pid'd player). Reading bridges here means a verified
# id ALWAYS reaches the draft, whether or not its tour has been wired up.
try:
    for _k, _e in json.load(open(os.path.join(HERE, "manual_ci_bridges.json"))).items():
        _cid = str(_e.get("cricinfo_id") or _k.split(":", 1)[-1])
        for _n in _e.get("names", []):
            alias2pid.setdefault(_n, f"ci:{_cid}")
except Exception as _e:
    print(f"  (manual_ci_bridges unreadable: {_e})")
_by_draft = {}
for pid, e in reg.items():
    for a in e.get("aliases", []):
        alias2pid.setdefault(a, pid)
    if e.get("draft_id") is not None:
        _by_draft.setdefault(e["draft_id"], []).append(pid)

# AMBIGUITY GUARD. draft_id takes PRIORITY over the name lookup below, so a registry draft_id
# that is stale or shared silently stamps the WRONG identity onto a draft player — and the draft
# then scores someone else's points under that slot. Found live: ci:1072470 (Shaheen Shah Afridi)
# carried draft_id 10627, which is SIKANDAR RAZA in the draft (Shaheen is 10657); one run of this
# script would have given Sikandar's slot Shaheen's pid. When a draft_id maps to more than one
# registry entry we trust NOTHING and fall through to the name — never a coin flip on identity.
for did, pids in _by_draft.items():
    if len(pids) == 1:
        draftid2pid[did] = pids[0]
    else:
        print(f"  SKIP draft_id {did}: claimed by {len(pids)} registry entries "
              f"({', '.join(pids)}) — ambiguous, falling back to name. "
              f"Fix with identity_healthcheck.py (split-identity blocker).")

raw = json.load(open(DRAFT_RAW))
players = raw if isinstance(raw, list) else raw.get("players", raw)
hit = 0
for p in players:
    pid = draftid2pid.get(p.get("id")) or alias2pid.get(norm(p.get("name", "")))
    if pid:
        p["pid"] = pid; hit += 1
json.dump(raw, open(DRAFT_RAW, "w"), indent=2, ensure_ascii=False)
print(f"backfilled pid into {hit}/{len(players)} draft players -> {DRAFT_RAW}")
