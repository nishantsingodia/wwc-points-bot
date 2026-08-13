#!/usr/bin/env python3
"""PROVENANCE for tests/fixtures/espn_l1/ — trimmed REAL ESPN payloads for LPL ev 1537349.

ev 1537349 is cricinfo's id for the same match Cricbuzz calls cb157138 (LPL 2026, 19th Match,
1 Aug 2026), whose Cricbuzz payloads are already committed in tests/fixtures/cricbuzz/. Having
BOTH feeds' real payloads for ONE match is what lets tests/test_cricbuzz_l1.py run the whole
witness path — cricbuzz.parse_match -> the derived bridge -> compute_l1_gaps vs parse_espn —
with no network at all.

Trimmed to exactly the fields parse_espn / espn_xi / espn_runouts / espn_batting_card /
espn_expected_balls read; everything else (news, videos, standings, wallclock, media) dropped.
Numbers are UNMODIFIED. Regenerate:

    WC_CACHE_DIR=/tmp/wc_api_cache python3 tests/fixtures/espn_l1/make_fixtures.py
"""
import json
import os
import sys

SRC = os.environ.get("WC_CACHE_DIR", "/tmp/wc_api_cache")
HERE = os.path.dirname(os.path.abspath(__file__))
EV, SERIES = "1537349", "1537330"

PBP_KEEP = ("id", "playType", "period", "scoreValue", "text", "shortText", "preText",
            "over", "bowler", "batsman", "dismissal", "otherBatsman", "otherBowler")


def _athlete(node):
    a = (node or {}).get("athlete") or {}
    return {"athlete": {k: a[k] for k in ("id", "fullName", "displayName") if k in a}}


def trim_pbp(d):
    items = []
    for it in ((d.get("commentary") or {}).get("items") or []):
        o = {k: it[k] for k in PBP_KEEP if k in it and k not in
             ("bowler", "batsman", "dismissal", "otherBatsman", "otherBowler")}
        for k in ("bowler", "batsman", "otherBatsman", "otherBowler"):
            if k in it:
                o[k] = _athlete(it[k])
        dis = it.get("dismissal") or {}
        if dis.get("dismissal"):
            o["dismissal"] = {"dismissal": True, "type": dis.get("type"),
                              "text": dis.get("text"),
                              "batsman": _athlete(dis.get("batsman")),
                              "bowler": _athlete(dis.get("bowler")),
                              "fielder": _athlete(dis.get("fielder"))}
        items.append(o)
    com = d.get("commentary") or {}
    return {"commentary": {"count": com.get("count"), "pageIndex": com.get("pageIndex"),
                           "pageCount": com.get("pageCount"), "items": items}}


def trim_summary(d):
    rosters = []
    for team in d.get("rosters") or []:
        out = []
        for p in team.get("roster") or []:
            a = p.get("athlete") or {}
            ls = []
            for l in p.get("linescores") or []:
                st = l.get("statistics") or {}
                keep = {}
                if st.get("batting"):
                    b = st["batting"]
                    keep["batting"] = {k: b[k] for k in ("order", "outDetails") if k in b}
                if st.get("bowling"):
                    w = st["bowling"]
                    keep["bowling"] = {s: {"balls": (w.get(s) or {}).get("balls")}
                                       for s in ("overallLhb", "overallRhb") if w.get(s)}
                if keep:
                    ls.append({"period": l.get("period"), "statistics": keep})
            out.append({"starter": p.get("starter"), "subbedIn": p.get("subbedIn"),
                        "athlete": {k: a[k] for k in ("id", "fullName", "displayName") if k in a},
                        "linescores": ls})
        rosters.append({"team": {"displayName": (team.get("team") or {}).get("displayName")},
                        "roster": out})
    return {"rosters": rosters}


def main():
    pbp_src = os.path.join(SRC, f"espn_{SERIES}_playbyplay_event_{EV}_limit_600.json")
    sum_src = os.path.join(SRC, f"espn_{SERIES}_summary_event_{EV}.json")
    for p in (pbp_src, sum_src):
        if not os.path.exists(p):
            sys.exit(f"missing source payload {p} — populate the ESPN cache first")
    for name, src, fn in (("playbyplay", pbp_src, trim_pbp), ("summary", sum_src, trim_summary)):
        with open(src) as fh:
            out = fn(json.load(fh))
        dst = os.path.join(HERE, f"espn_{EV}_{name}.json")
        with open(dst, "w") as fh:
            json.dump(out, fh, separators=(",", ":"), sort_keys=True)
        print(f"{dst}: {os.path.getsize(dst)} bytes")


if __name__ == "__main__":
    main()
