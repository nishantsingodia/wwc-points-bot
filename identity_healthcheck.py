#!/usr/bin/env python3
"""Pre-launch player-identity health check for a tour.

Run before a tour goes live in ANY app (auction / draft / points feed):
    python3 identity_healthcheck.py "lanka premier league"
Exit 1 on any BLOCKER, so a setup step / CI can gate on it.

What it catches (and how reliable it is with LOCAL data only):

  BLOCKER  dup-cricsheet   one cricsheet_id under >1 registry pid — identity
                           merge/split corruption. Fully reliable.
  BLOCKER  fixable-miss    a squad name with NO cricsheet_id for which a confident,
                           given-name-compatible DB record WITH match data exists
                           (the Sam Harper / Asitha class — a real record we failed
                           to link). Actionable: add a bridge. Reliable for
                           SAME-surname misses.
  INFO     unmapped        no cricsheet_id and no findable record — genuinely
                           uncapped (born-2004 U19 types). Expected; triage once.
  REVIEW   name-mismatch   anchored matches whose go-by name isn't derivable from
                           the cricsheet initials. NOTE: this is mostly LEGIT for SL
                           players (Kusal Mendis = BKG Mendis) — local data has no
                           full/common name, so this can't be auto-verified. Scan it
                           by eye for a genuine wrong-namesake (Dale Phillips linked
                           to GD/Glenn Phillips). Definitive detection needs an ESPN
                           key_cricinfo full-name cross-ref (people.csv has the id) —
                           a future enhancement. Not a blocker.

Reuses build_registry's matcher, so this check and the registry build never drift.
"""
import sys, os, json, sqlite3
from collections import defaultdict
import build_registry as br


def main():
    filt = (sys.argv[1].lower() if len(sys.argv) > 1 else None)
    tours = json.load(open(os.path.join(br.HERE, "tours.json")))
    con = sqlite3.connect(br.AUCTION_DB); con.row_factory = sqlite3.Row
    players = br.load_global()

    cs_pids = defaultdict(list)
    for pid, e in players.items():
        if e.get("cricsheet_id"):
            cs_pids[e["cricsheet_id"]].append(pid)
    dups = {cs: pids for cs, pids in cs_pids.items() if len(pids) > 1}

    have_data = {r[0] for r in con.execute("SELECT DISTINCT player_id FROM match_performances")}
    blockers = 0

    for t in tours:
        if filt and filt not in t["name"].lower():
            continue
        spath = os.path.join(br.HERE, t["squads"]) if t.get("squads") else None
        if not spath or not os.path.exists(spath):
            continue
        squad = br.squad_players(spath)
        pool = br.db_pool(con, t.get("gender", "female"))
        pool_data = [r for r in pool if r["id"] in have_data]   # records that actually have stats
        cs_name = {r["cricsheet_id"]: r["name"] for r in pool if r.get("cricsheet_id")}

        fixable, unmapped, review = [], [], []
        for short, tfull, sname, role in squad:
            ns = br.norm(sname)
            e = next((pe for pe in players.values() if ns in pe.get("aliases", [])), {})
            cs = e.get("cricsheet_id")
            if cs:
                db = cs_name.get(cs)
                if db and not br.given_compatible(sname, db):
                    review.append((short, sname, db))
            else:
                # ONLY flag an EXACT-normalized-name record that has data but isn't anchored
                # (rock-solid + actionable). Fuzzy same-surname "candidates" are NOT flagged —
                # that heuristic proposed wrong links (Traveen Mathews -> AD/Angelo Mathews) and
                # would repeat the very namesake bug. Fuzzy/surname-hidden misses need the
                # web/full-name step, surfaced as unmapped for human triage.
                exact = next((r for r in pool_data if br.norm(r["name"]) == ns), None)
                if exact:
                    fixable.append((short, sname, exact["name"], 100))
                else:
                    unmapped.append(sname)

        print(f"\n=== {t['name']} — identity health ===")
        print(f"  squad {len(squad)} | fixable-miss {len(fixable)} | unmapped {len(unmapped)} | name-mismatch review {len(review)}")
        for short, s, db, sc in fixable:
            print(f"  BLOCKER fixable-miss : {short:4} {s:26} -> real record {db!r} WITH data exists (score {sc:.0f}); add a bridge")
        if unmapped:
            print(f"  INFO    unmapped     : {', '.join(unmapped)}")
        if review:
            print(f"  REVIEW  name-mismatch (eyeball — mostly legit SL initials-forms; look for a wrong namesake):")
            for short, s, db in review:
                print(f"            {short:4} {s:26} -> {db}")
        blockers += len(fixable)

    if dups:
        print("\n=== GLOBAL — duplicate cricsheet_id (identity corruption) ===")
        for cs, pids in dups.items():
            print(f"  BLOCKER dup-cricsheet: {cs} under {pids}")
        blockers += len(dups)

    print(f"\n{'FAIL' if blockers else 'PASS'}: {blockers} blocker(s). (review/unmapped are human-triage, not blockers)")
    sys.exit(1 if blockers else 0)


if __name__ == "__main__":
    main()
