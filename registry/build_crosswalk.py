#!/usr/bin/env python3
"""registry/crosswalk.json — the cricsheet_id ⇄ cricinfo_id spine — REGENERATED from people.csv.

WHY THIS FILE EXISTS
====================
`crosswalk.json` is documented as "a pure function of people.csv", and every identity claim in
this repo rests on it: `build_registry` derives each player's `cricsheet_id` from his verified
cricinfo id through here, so cs and ci point at the SAME person by construction. But there was no
committed way to REGENERATE it. It was built once, by hand, on 25 Jul 2026 — and then silently
rotted for five weeks while cricsheet kept registering players.

Measured 31 Aug 2026: the committed file held **18253** mappings against people.csv's **18423**.
170 missing, and they were not obscure:
  · bb194908 -> 681099  Ali Usman   — the ONLY `identity_healthcheck` blocker left on the live
    ENG v PAK Test squad. He anchored fine to ci:681099 from ESPN, but with no cs mapping his
    `cricsheet_id` was null, so fixable-miss fired against the auction record that did have one.
  · f52fc698 -> 1500753 ZN Carter   — sat on the "Needs Cricinfo ID" tab as unanswerable
  · b76a2178 -> 1394274 M Dindyal   — likewise
Both tab rows were reported to the owner as "genuinely needs a human". Neither did. A stale spine
does not fail loudly; it just quietly turns answerable questions into human work.

USAGE
=====
    python3 registry/build_crosswalk.py            # dry run: report the delta, write nothing
    python3 registry/build_crosswalk.py --write    # persist
    python3 registry/build_crosswalk.py --people /path/to/people.csv [--write]

Deterministic: keys are sorted, so a re-run on the same input is byte-identical — the same
discipline as `cricbuzz_match_map` (log the fact, derive the view). Regenerate, never hand-edit.

REFUSALS (this file writes nothing rather than corrupt the spine)
  · a SHRINK — fewer mappings than the committed file. cricsheet can serve a truncated or partial
    register on a bad day, and an absence is not a deletion. Overrideable with --allow-shrink for
    a real upstream removal, which should be a deliberate, explained act.
  · a cricinfo id claimed by TWO cricsheet ids. `build_registry.load_crosswalk` inverts this map
    (`CI2CS = {ci: cs for cs, ci in ...}`) and relies on that being unique; a collision would make
    the reverse silently last-wins, which is how one human ends up wearing another's cricsheet id.
    Named, never collapsed.
"""
import argparse
import csv
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CROSSWALK_PATH = os.path.join(HERE, "crosswalk.json")
REGISTER_URL = "https://cricsheet.org/register/people.csv"
# cricsheet.org is NOT ESPN. site.api.espn.com 403s browser UAs and every fetcher swallows it (see
# CLAUDE.md); this host has no such rule. An honest bot UA works on both, so use one and never
# copy an ESPN UA constant in here — the two hosts' rules are opposite and must not be unified.
UA = "wwc-points-bot/1.0 (+https://github.com/nishantsingodia)"


def fetch_people(path=None):
    """people.csv rows. A local --people path wins; otherwise fetch the register."""
    if path:
        with open(path, encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    req = urllib.request.Request(REGISTER_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return list(csv.DictReader(r.read().decode("utf-8").splitlines()))


def build(rows):
    """(cs2ci, ci_alt, collisions). Pure — no clock, no environment, no network."""
    cs2ci, ci_alt, seen_ci = {}, {}, {}
    collisions = []
    for r in rows:
        cs = (r.get("identifier") or "").strip()
        ci = (r.get("key_cricinfo") or "").strip()
        if not cs or not ci:
            continue
        # ⛔ ONE CRICINFO ID, ONE CRICSHEET ID. The reverse map is built by inversion and assumed
        # unique; report the pair rather than let the last row win.
        if ci in seen_ci and seen_ci[ci] != cs:
            collisions.append((ci, seen_ci[ci], cs, (r.get("name") or "").strip()))
            continue
        seen_ci[ci] = cs
        cs2ci[cs] = ci
        # alternate cricinfo profiles (a player with two cricinfo pages) fold onto the primary
        for k in ("key_cricinfo_2", "key_cricinfo_3"):
            alt = (r.get(k) or "").strip()
            if alt and alt != ci:
                ci_alt[alt] = ci
    return cs2ci, ci_alt, collisions


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--people", help="local people.csv (default: fetch the cricsheet register)")
    ap.add_argument("--write", action="store_true", help="persist (default is a dry run)")
    ap.add_argument("--allow-shrink", action="store_true",
                    help="permit FEWER mappings than the committed file (a real upstream removal)")
    ap.add_argument("--out", default=CROSSWALK_PATH)
    args = ap.parse_args(argv)

    rows = fetch_people(args.people)
    cs2ci, ci_alt, collisions = build(rows)
    print(f"people.csv: {len(rows)} rows -> cs2ci {len(cs2ci)} | ci_alt {len(ci_alt)}",
          file=sys.stderr)

    if collisions:
        for ci, a, b, nm in collisions:
            print(f"  ⛔ cricinfo {ci} claimed by cricsheet {a} AND {b} ({nm})", file=sys.stderr)
        print(f"REFUSING to write: {len(collisions)} cricinfo id(s) under >1 cricsheet id. The "
              f"reverse map (CI2CS) is built by inversion and must be unique.", file=sys.stderr)
        return 1

    old = {}
    if os.path.exists(args.out):
        try:
            old = json.load(open(args.out)).get("cs2ci", {})
        except Exception as e:
            print(f"  (committed crosswalk unreadable, treating as empty: {e})", file=sys.stderr)
    added = sorted(set(cs2ci) - set(old))
    removed = sorted(set(old) - set(cs2ci))
    changed = sorted(k for k in set(cs2ci) & set(old) if cs2ci[k] != old[k])
    print(f"delta vs committed: +{len(added)} new, -{len(removed)} gone, ~{len(changed)} remapped",
          file=sys.stderr)
    for k in changed:
        print(f"  ~ {k}: {old[k]} -> {cs2ci[k]}", file=sys.stderr)

    if len(cs2ci) < len(old) and not args.allow_shrink:
        print(f"REFUSING to write: {len(cs2ci)} mappings is FEWER than the committed "
              f"{len(old)}. cricsheet can serve a partial register, and an absence is not a "
              f"deletion. Pass --allow-shrink if this removal is real.", file=sys.stderr)
        return 1

    if not args.write:
        print("DRY RUN — pass --write to persist", file=sys.stderr)
        return 0

    blob = {"cs2ci": {k: cs2ci[k] for k in sorted(cs2ci)},
            "ci_alt": {k: ci_alt[k] for k in sorted(ci_alt)}}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(blob, fh, indent=1, ensure_ascii=False)
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
