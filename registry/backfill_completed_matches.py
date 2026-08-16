#!/usr/bin/env python3
"""Derive registry/completed_matches.json from registry/settlement_snapshots.json.

WHY THIS EXISTS. The "COMPLETED never returns to LIVE" ratchet used to be inferred from the
settlement store: a settlement row for (match_key, pid) could only ever have been written by a
COMPLETED publish, so its existence proved the match had published. That inference stops being
complete the moment base points freeze at L1_DONE instead of on any COMPLETED publish — a match can
now publish COMPLETED and freeze nothing — so the fact needs its own record.

This script writes the record for everything that published BEFORE the ledger existed. It is a
PURE FUNCTION of the settlement store (the `frozen_at` on the row supplies `first_seen`; no clock
is read), so re-running it reproduces the same file byte for byte. Regenerate, never hand-edit.

    python3 registry/backfill_completed_matches.py [--write]

Without --write it prints what it would do and touches nothing. It never OVERWRITES an entry the
running bot has already recorded: those carry the real publish event, this file only fills gaps.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SETTLEMENTS = os.path.join(HERE, "settlement_snapshots.json")
COMPLETED = os.path.join(HERE, "completed_matches.json")


def derive():
    rows = json.load(open(SETTLEMENTS)).get("settlements", [])
    out = {}
    for r in rows:
        key = (r.get("tour", ""), r.get("match_key", ""))
        if not key[1]:
            continue
        # Earliest freeze date for the match = the run on which it first published COMPLETED.
        # min() over the whole match, so re-runs that added players later cannot move it.
        cur = out.get(key)
        seen = r.get("frozen_at", "")
        if cur is None:
            out[key] = {"tour": key[0], "match_key": key[1], "match": r.get("match", ""),
                        "date": r.get("date", ""), "first_status": r.get("status", ""),
                        "first_seen": seen, "provenance": "derived-from-settlement"}
        elif seen and (not cur["first_seen"] or seen < cur["first_seen"]):
            cur["first_seen"] = seen
            cur["first_status"] = r.get("status", "")
    return out


def main():
    derived = derive()
    try:
        cur = {(r.get("tour", ""), r.get("match_key", "")): r
               for r in json.load(open(COMPLETED)).get("completed", [])}
    except Exception:
        cur = {}
    added = {k: v for k, v in derived.items() if k not in cur}
    merged = dict(cur)
    merged.update(added)
    print(f"settlement store: {len(derived)} distinct (tour, match) published COMPLETED")
    print(f"ledger before: {len(cur)}   adding: {len(added)}   after: {len(merged)}")
    if "--write" not in sys.argv:
        print("(dry run — pass --write to persist)")
        return
    rows = sorted(merged.values(), key=lambda r: (r.get("date", ""), r.get("tour", ""),
                                                  r.get("match_key", "")))
    json.dump({"note": "WRITE-ONCE record of every match that has PUBLISHED as COMPLETED or "
                       "COMPLETED_FLAGGED, independent of whether its base points froze. This is "
                       "the 'COMPLETED never returns to LIVE' ratchet. It used to be inferred from "
                       "settlement_snapshots.json, which stopped being a valid proxy when base "
                       "points began freezing at L1_DONE instead of on any COMPLETED publish: a "
                       "match can now publish without freezing anything. Never edited by hand and "
                       "never revoked.",
               "completed": rows},
              open(COMPLETED, "w"), indent=1, ensure_ascii=False)
    print(f"wrote {COMPLETED}")


if __name__ == "__main__":
    main()
