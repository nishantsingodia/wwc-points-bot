#!/usr/bin/env python3
"""Seed the WRITE-ONCE settlement baseline for matches that were already settled before the
baseline existed (LPL 2026 + The Hundred Men's/Women's 2026, settled Jul 2026).

Why this is needed at all
-------------------------
`registry/settlement_snapshots.json` freezes each player's points the FIRST time their match is
published COMPLETED. For every future match that happens automatically. But LPL and both
Hundreds had ALREADY flipped to `cricsheet · official` by the time the snapshot was built — so a
normal run would freeze the CURRENT (post-cricsheet) numbers and call them "settled", quietly
asserting that nothing ever changed. That is precisely the failure we are trying to make
visible, so the baseline for those tours has to be reconstructed from a pre-cricsheet run.

Sources, in order of trust
--------------------------
1. `registry/settlement_evidence/pre_cricsheet_*.csv` — output of a real bot run from 22 Jul,
   BEFORE cricsheet posted for these tours. This is a genuine "what was on screen" record, so
   rows recovered from it are marked provenance `seed`.
2. Nothing else. A match with no pre-cricsheet evidence is recorded as provenance `unknown` with
   its CURRENT points, and the app renders it as "no settled baseline recorded" rather than
   claiming a zero delta. Guessing a baseline would defeat the purpose.

The pre-migration pid problem
-----------------------------
The 22 Jul CSVs predate the cricinfo-id migration (25 Jul), so their Player IDs are the old
cricsheet-hash form. `registry/pid_map.json` (old pid -> `ci:`) is applied so the seeded rows key
on the SAME pid the live sheet and the draft app use. Rows whose pid can't be mapped fall back to
a registry name lookup, and anything still unresolved is reported, never silently dropped.

Usage:  python3 seed_settlements.py [--apply]     (default is a dry run)
"""
import csv
import glob
import json
import os
import sys

_ARGS = list(sys.argv[1:])                    # capture BEFORE clearing (importing wc_fps_to_csv
sys.argv = [sys.argv[0]]                      # must not see our flags)
import wc_fps_to_csv as w                     # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
EVIDENCE = os.path.join(HERE, "registry", "settlement_evidence")

# pre-cricsheet CSV -> (tour name, squads file). The tab/tour names must match tours.json so the
# app groups the audit rows under the right tour.
SEEDS = [
    ("pre_cricsheet_lpl_2026_points.csv", "Lanka Premier League 2026", "lpl_2026_squads.json"),
    ("pre_cricsheet_the_hundred_mens_2026_points.csv", "The Hundred Men's Competition 2026",
     "the_hundred_men_s_competition_2026_squads.json"),
    ("pre_cricsheet_the_hundred_womens_2026_points.csv", "The Hundred Women's Competition 2026",
     "the_hundred_women_s_competition_2026_squads.json"),
]


def load_pid_map():
    try:
        return json.load(open(os.path.join(HERE, "registry", "pid_map.json")))
    except Exception:
        return {}


PID_MAP = load_pid_map()


def resolve(pid, full):
    """Old pid -> ci: pid. Falls back to a registry NAME lookup (the display names in these CSVs
    are already canonical, written by PID2DISP on the run that produced them)."""
    pid = (pid or "").strip()
    if pid:
        mapped = PID_MAP.get(pid)
        if mapped:
            return mapped, "pid_map"
        if pid.startswith("ci:"):
            return pid, "already-ci"
    by_name = w.resolve_pid(full or "")
    if by_name:
        return by_name, "name"
    return "", "UNRESOLVED"


def short_to_canon(squads_file):
    """Team short code -> canonical franchise name, so a match_key can be rebuilt from a CSV row
    (the CSV carries only the short code, the key needs canonical full names)."""
    try:
        d = json.load(open(os.path.join(HERE, squads_file)))
    except Exception as e:
        print(f"  !! cannot read {squads_file}: {e}", file=sys.stderr)
        return {}
    return {code: v.get("name", code) for code, v in d.items()}


def match_key_from_row(mdate, match_label, code2canon):
    """Rebuild the stable match_key from the row's 'Match' label ('Match 6 — DS v KR').
    Uses the SAME match_key_of/team_key the bot writes, so seeded rows join the live sheet."""
    if "—" in match_label:
        _, _, teams_part = match_label.partition("—")
    else:
        teams_part = match_label
    codes = [c.strip() for c in teams_part.split(" v ") if c.strip()]
    if len(codes) != 2:
        return None, codes
    canon = [code2canon.get(c) for c in codes]
    if not all(canon):
        return None, codes
    return w.match_key_of(mdate, canon), codes


def main():
    apply = "--apply" in _ARGS
    w.SETTLEMENTS = w._load_settlements()
    print(f"existing baseline rows: {len(w.SETTLEMENTS)}")

    added = skipped = unresolved = badkey = 0
    per_tour = {}
    for fname, tour, squads_file in SEEDS:
        path = os.path.join(EVIDENCE, fname)
        if not os.path.exists(path):
            print(f"-- {tour}: no evidence file ({fname}) — skipped", file=sys.stderr)
            continue
        code2canon = short_to_canon(squads_file)
        rows = list(csv.DictReader(open(path)))
        n_add = 0
        for r in rows:
            # Only rows the tour actually PUBLISHED as settled. A row still LIVE at that point
            # was never settled, so it has no baseline to record.
            if (r.get("Match Status") or "").strip() not in ("COMPLETED", "COMPLETED_FLAGGED"):
                skipped += 1
                continue
            mk, codes = match_key_from_row(r.get("Date", ""), r.get("Match", ""), code2canon)
            if not mk:
                badkey += 1
                continue
            pid, how = resolve(r.get("Player ID"), r.get("Full Name"))
            if not pid:
                unresolved += 1
                print(f"   UNRESOLVED pid: {r.get('Date')} {r.get('Team')} "
                      f"{r.get('Full Name')} (old pid {r.get('Player ID') or '-'})", file=sys.stderr)
                continue
            pts_raw = (r.get("Fantasy Points") or "").strip()
            try:
                pts = float(pts_raw) if pts_raw else 0
                pts = int(pts) if pts == int(pts) else pts
            except ValueError:
                pts = 0
            if (mk, pid) in w.SETTLEMENTS:
                skipped += 1
                continue
            w.SETTLEMENTS[(mk, pid)] = {
                "match_key": mk, "tour": tour, "match": r.get("Match", ""),
                "date": r.get("Date", ""), "team": r.get("Team", ""), "pid": pid,
                "full": r.get("Full Name", ""), "points": pts,
                "status": r.get("Match Status", ""), "source": r.get("Source", ""),
                "frozen_at": "2026-07-22", "provenance": "seed"}
            n_add += 1
            added += 1
        per_tour[tour] = (n_add, len(rows))
        print(f"-- {tour}: seeded {n_add} of {len(rows)} evidence rows")

    print(f"\nadded {added} | skipped {skipped} (not settled / already present) | "
          f"unresolved pid {unresolved} | unbuildable match_key {badkey}")

    # ── PASS 2: claim the already-completed matches we have NO evidence for ──────────
    # Everything these tours completed after 22 Jul has no pre-cricsheet record. If we leave
    # those (match_key, pid) pairs empty, the next normal run freezes TODAY's post-cricsheet
    # number as "what it was settled on" — asserting a zero delta on exactly the matches where
    # Hasaranga reads 0. Claiming the slot now with provenance 'unknown' makes the write-once
    # rule work FOR us: the app renders "no settled baseline recorded" instead of a false all-clear.
    unknown = 0
    for fname, tour, squads_file in [("live_lpl.csv", "Lanka Premier League 2026",
                                      "lpl_2026_squads.json"),
                                     ("live_hnd_m.csv", "The Hundred Men's Competition 2026",
                                      "the_hundred_men_s_competition_2026_squads.json"),
                                     ("live_hnd_w.csv", "The Hundred Women's Competition 2026",
                                      "the_hundred_women_s_competition_2026_squads.json")]:
        path = os.path.join(EVIDENCE, fname)
        if not os.path.exists(path):
            continue
        code2canon = short_to_canon(squads_file)
        for r in csv.DictReader(open(path)):
            if (r.get("Match Status") or "").strip() not in ("COMPLETED", "COMPLETED_FLAGGED"):
                continue
            mk, _ = match_key_from_row(r.get("Date", ""), r.get("Match", ""), code2canon)
            pid = (r.get("Player ID") or "").strip()
            if not mk or not pid or (mk, pid) in w.SETTLEMENTS:
                continue
            pts_raw = (r.get("Fantasy Points") or "").strip()
            try:
                pts = float(pts_raw) if pts_raw else 0
                pts = int(pts) if pts == int(pts) else pts
            except ValueError:
                pts = 0
            w.SETTLEMENTS[(mk, pid)] = {
                "match_key": mk, "tour": tour, "match": r.get("Match", ""),
                "date": r.get("Date", ""), "team": r.get("Team", ""), "pid": pid,
                "full": r.get("Full Name", ""), "points": pts,
                "status": r.get("Match Status", ""), "source": r.get("Source", ""),
                "frozen_at": "2026-07-29", "provenance": "unknown"}
            unknown += 1
    print(f"pass 2: claimed {unknown} row(s) as provenance 'unknown' "
          f"(completed before the baseline existed, no pre-cricsheet evidence)")
    print(f"baseline total now: {len(w.SETTLEMENTS)}")
    if apply:
        w.save_settlements()
        print(f"WROTE {w.SETTLEMENT_PATH}")
    else:
        print("\nDRY RUN — re-run with --apply to write registry/settlement_snapshots.json")


if __name__ == "__main__":
    main()
