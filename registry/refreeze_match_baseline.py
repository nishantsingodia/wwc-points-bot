#!/usr/bin/env python3
"""Re-freeze ONE match's settlement baseline. Deliberately awkward, deliberately audited.

WHY THIS EXISTS AND WHY IT IS NOT A GENERAL TOOL. registry/settlement_snapshots.json is WRITE-ONCE
on purpose: it is the record of what a contest was settled on, and the whole audit surface is the
diff between it and the live sheet. Editing it is normally the bug, not the fix.

The one case it is the fix is a baseline that was never a settlement at all — a match whose
"provisional cut" was minted from a card nobody was ever shown. ETPL Match 11 (Belfast Wolves v
Edinburgh Castle Rockers, 2 Sep 2026) is that case: ESPN's card was TRUNCATED, cricsheet posted in
the same run that first scored the match, and the pre-8-Sep code skipped L1 entirely and froze the
raw ESPN cut. Maxwell went in at 25 off 13 where he had made 35 off 23; Tom Curran at 6 balls of
19. Cricbuzz and cricsheet agreed against ESPN on every one of those fields.

Nobody's money was settled on those numbers — the match published cricsheet's figures once the L2
rows were approved — so the frozen record is not a historical truth being rewritten. It is a
fiction, and it makes /audit report 306 points of movement that never happened.

WHAT IT WRITES. Each row's `fields` gets the OFFICIAL value for exactly the fields the L2 recon
recorded as revised, and nothing else; `points` becomes the published total. Every repaired row
carries a `repaired` stamp naming what moved and why, so the record still explains itself.

    python3 registry/refreeze_match_baseline.py <evidence.csv>            # dry run
    python3 registry/refreeze_match_baseline.py <evidence.csv> --write    # apply

`evidence.csv` is a points-tab export taken BEFORE the fix landed, i.e. one still carrying the
`L2 Recon` column that records base→official per field. That column is the only place the
correction is written down; a later export no longer has it, because those gaps now close by
concurrence and never become rows.
"""
import csv
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wc_fps_to_csv as wc                                              # noqa: E402

STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settlement_snapshots.json")
MATCH_KEY = "2026-09-02::belfast wolves|edinburgh castle rockers"
REASON = ("baseline was minted from a TRUNCATED ESPN card on a match that had never published; "
          "cricbuzz and cricsheet agreed against it on every revised field")
LABEL2FIELD = {v: k for k, v in wc.RECON_LABEL.items()}


def revisions(cell):
    """{field: official_value} from an 'L2 Recon' cell like '⚠ revised: dots 2→9; conc 21→29'."""
    out = {}
    for part in (cell or "").replace("⚠", "").replace("revised:", "").split(";"):
        bits = part.strip().split()
        if len(bits) == 2 and "→" in bits[1]:
            was, now = bits[1].split("→")
            f = LABEL2FIELD.get(bits[0])
            if f and now.lstrip("-").isdigit():
                out[f] = int(now)
    return out


def main(evidence, write=False):
    rows = [r for r in csv.DictReader(open(evidence)) if "Match 11" in r["Match"]]
    if not rows:
        sys.exit(f"no 'Match 11' rows in {evidence}")
    by_pid = {r["Player ID"]: r for r in rows}

    store = json.load(open(STORE))
    targets = [s for s in store["settlements"] if s["match_key"] == MATCH_KEY]
    print(f"{len(targets)} frozen row(s) for {MATCH_KEY}\n")

    changed, checked, refused = [], 0, []
    for s in targets:
        live = by_pid.get(s["pid"])
        if not live:
            refused.append((s["full"], "no live row for this pid")); continue
        rev = revisions(live.get("L2 Recon"))
        pts = int(live["Fantasy Points"] or 0)
        if not rev and pts == s["points"]:
            continue                                    # already correct; leave it alone
        new_fields = dict(s.get("fields") or {}, **rev)
        # PROVE IT before writing: the corrected fields must re-score to the number the sheet
        # actually publishes. Without this the repair is just a differently-sourced guess.
        got = wc.score(wc._hydrate_baseline(new_fields)[0], live["Role"] or "?", fmt="T20")["total"]
        checked += 1
        if got != pts:
            refused.append((s["full"], f"re-scores to {got}, sheet publishes {pts}")); continue
        changed.append((s, new_fields, pts, rev))

    for s, nf, pts, rev in changed:
        moved = ", ".join(f"{wc.RECON_LABEL.get(k, k)} {s['fields'].get(k)}→{v}"
                          for k, v in rev.items())
        print(f"  {s['full'][:22]:22} pts {s['points']:>4} → {pts:<4}  {moved}")
    for who, why in refused:
        print(f"  ⛔ {who[:22]:22} NOT repaired: {why}")
    print(f"\n{len(changed)} row(s) to repair, {checked} re-scored, {len(refused)} refused")

    if not write:
        print("\nDRY RUN — pass --write to apply."); return
    if refused:
        sys.exit("refusing to write while any row failed its re-score check")
    shutil.copy(STORE, STORE + ".bak-prerefreeze")
    for s, nf, pts, rev in changed:
        s["repaired"] = {"on": wc._today_iso(), "reason": REASON,
                         "was_points": s["points"],
                         "was_fields": {k: s["fields"].get(k) for k in rev}}
        s["fields"], s["points"] = nf, pts
    json.dump(store, open(STORE, "w"), indent=2, ensure_ascii=False)
    print(f"\nwrote {len(changed)} row(s); backup at {os.path.basename(STORE)}.bak-prerefreeze")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0], write="--write" in sys.argv)
