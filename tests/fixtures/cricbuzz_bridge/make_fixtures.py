#!/usr/bin/env python3
"""Regenerate tests/fixtures/cricbuzz/*.json from cached Cricbuzz + ESPN payloads.

PROVENANCE — every byte in these fixtures is real feed output, trimmed, never hand-written:
  cb_sc_<id>.html   Cricbuzz scorecard page       https://www.cricbuzz.com/live-cricket-scorecard/<id>/x
  pbp_<ev>.json     ESPN play-by-play             site.api.espn.com .../playbyplay?event=<ev>
Fixtures shipped (LPL 2026):
  m12  cb157061 / espn1537342   the eval's first reference match
  m19  cb157138 / espn1537349   the eval's second reference match; positional alignment 5/16
  m05  cb156988 / espn1537335   the SUBSTITUTE DISAGREEMENT — ESPN + cricsheet say
                                "c sub (Pawan Sandesh)", Cricbuzz says "c Garuka Sanketh"
  m10  cb157039 / espn1537340   Lizaad Williams "retd out" 0 off 0 on CB, absent from ESPN's
                                ball-by-ball — pins the all-zero batting-line fold
Also shipped (The Hundred Women's 2026):
  gh   cb145302 / espn1521224   the FIELDER-ATTRIBUTION DISPUTE — Cricbuzz says "A Capsey c Grace
                                Harris b Charis Pavely", ESPN says "c Higham". Joining them minted
                                cb11101 (Grace Harris) onto ci:874201 and REVOKED her, discarding
                                eight matches of fingerprint evidence. 3 of the 12 `cb:` rows on
                                "Needs Cricinfo ID" on 16 Aug 2026 were this one shape.

The Cricbuzz side is kept as the RAW `scoreCard` list (trimmed to the fields the module reads) so
the tests exercise the real parser, including the all-zero did-not-bat rows.
The ESPN side is REDUCED: each athlete's final running totals collapse to a single commentary
item, and every dismissal item is kept verbatim. `normalize_espn_card` takes the MAX of the
running totals, so the reduction is faithful by construction — and it keeps a 750KB payload down
to ~15KB. `espn_running_totals.json` holds 12 consecutive REAL items so the max-over-items
behaviour itself stays pinned.

    python3 tests/fixtures/cricbuzz/make_fixtures.py <dir-with-cached-payloads>
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "registry"))
import cricbuzz_bridge as cbb  # noqa: E402

# (cricbuzz match, espn event, espn SERIES). The series is only needed to find the payload in the
# bot's own cache (`espn_<series>_playbyplay_event_<ev>_limit_500_page_<n>.json`); the standalone
# `pbp_<ev>.json` name is tried first so an old cache dir still works.
MATCHES = [(157061, 1537342, "1537330"), (157138, 1537349, "1537330"),
           (156988, 1537335, "1537330"), (157039, 1537340, "1537330"),
           (145302, 1521224, "1521193")]

BAT_KEYS = ("batId", "batName", "runs", "balls", "fours", "sixes", "outDesc", "wicketCode",
            "fielderId1", "fielderId2", "bowlerId", "dots")
BOWL_KEYS = ("bowlerId", "bowlName", "overs", "balls", "runs", "wickets", "maidens", "dots",
             "wides", "no_balls")


def trim_cb(score_card):
    out = []
    for inn in score_card:
        bat = {k: {kk: v.get(kk) for kk in BAT_KEYS if kk in v}
               for k, v in ((inn.get("batTeamDetails") or {}).get("batsmenData") or {}).items()}
        bowl = {k: {kk: v.get(kk) for kk in BOWL_KEYS if kk in v}
                for k, v in ((inn.get("bowlTeamDetails") or {}).get("bowlersData") or {}).items()}
        out.append({"matchId": inn.get("matchId"), "inningsId": inn.get("inningsId"),
                    "batTeamDetails": {"batTeamName": (inn.get("batTeamDetails") or {}).get("batTeamName"),
                                       "batsmenData": bat},
                    "bowlTeamDetails": {"bowlTeamName": (inn.get("bowlTeamDetails") or {}).get("bowlTeamName"),
                                        "bowlersData": bowl}})
    return out


def _ath(o):
    a = (o or {}).get("athlete") or {}
    return {"id": a.get("id"), "displayName": a.get("displayName")}


def reduce_espn(pbp):
    """One item per athlete carrying their FINAL running totals + every dismissal item verbatim."""
    bat, bowl = {}, {}
    dismissals = []
    for it in pbp["commentary"]["items"]:
        for key in ("batsman", "otherBatsman"):
            o = it.get(key) or {}
            aid = ((o.get("athlete") or {}).get("id"))
            if not aid:
                continue
            cur = (o.get("totalRuns", 0) or 0, o.get("faced", 0) or 0,
                   o.get("fours", 0) or 0, o.get("sixes", 0) or 0)
            prev = bat.get(aid)
            if prev is None or cur[1] > prev[0][1] or (cur[1] == prev[0][1] and cur[0] > prev[0][0]):
                bat[aid] = (cur, _ath(o))
        for key in ("bowler", "otherBowler"):
            o = it.get(key) or {}
            aid = ((o.get("athlete") or {}).get("id"))
            if not aid:
                continue
            cur = (o.get("balls", 0) or 0, o.get("conceded", 0) or 0, o.get("wickets", 0) or 0,
                   o.get("maidens", 0) or 0)
            prev = bowl.get(aid)
            if prev is None or cur[0] > prev[0][0]:
                bowl[aid] = (cur, _ath(o))
        d = it.get("dismissal") or {}
        if d.get("dismissal"):
            dismissals.append({"dismissal": {
                "dismissal": True, "type": d.get("type"), "text": d.get("text"),
                "batsman": {"athlete": _ath(d.get("batsman"))} if d.get("batsman") else None,
                "bowler": {"athlete": _ath(d.get("bowler"))} if d.get("bowler") else None,
                "fielder": {"athlete": _ath(d.get("fielder"))} if d.get("fielder") else None}})
    items = []
    for aid in sorted(bat, key=int):
        (r, b, f4, f6), ath = bat[aid]
        items.append({"batsman": {"athlete": ath, "totalRuns": r, "faced": b,
                                  "fours": f4, "sixes": f6}})
    for aid in sorted(bowl, key=int):
        (b, c, w, m), ath = bowl[aid]
        items.append({"bowler": {"athlete": ath, "balls": b, "conceded": c,
                                 "wickets": w, "maidens": m}})
    items.extend(dismissals)
    return {"commentary": {"items": items}}


def _pbp(src, ev, series):
    """The standalone `pbp_<ev>.json` if it is there, else the bot's own cached play-by-play
    (which is what actually exists on a machine that has run the bot). The bot-cache reader
    refuses a truncated card rather than returning a partial one."""
    plain = os.path.join(src, "pbp_%d.json" % ev)
    if os.path.exists(plain):
        return json.load(open(plain, encoding="utf-8"))
    pbp = cbb.espn_pbp_from_bot_cache(str(ev), series, src)
    if pbp is None:
        raise SystemExit("no cached play-by-play for event %s in %s" % (ev, src))
    return pbp


def main(src):
    for cb_id, ev, series in MATCHES:
        html = open(os.path.join(src, "cb_sc_%d.html" % cb_id), encoding="utf-8").read()
        card, header = cbb.parse_cb_scorecard_html(html)
        pbp = _pbp(src, ev, series)
        fx = {"cb_match_id": str(cb_id), "espn_event_id": str(ev),
              "date": cbb.match_date(header),
              "cb_score_card": trim_cb(card), "espn_pbp": reduce_espn(pbp)}
        path = os.path.join(HERE, "cb%d_espn%d.json" % (cb_id, ev))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(fx, fh, indent=0, ensure_ascii=False, separators=(",", ":"))
            fh.write("\n")
        # sanity: the reduced payload must reproduce the full one
        full = cbb.normalize_espn_card(pbp)
        red = cbb.normalize_espn_card(fx["espn_pbp"])
        assert full["batting"] == red["batting"], cb_id
        assert full["bowling"] == red["bowling"], cb_id
        assert full["dismissals"] == red["dismissals"], cb_id
        print("wrote", os.path.basename(path), os.path.getsize(path), "bytes")

    pbp = _pbp(src, 1537342, "1537330")
    keep = []
    for it in pbp["commentary"]["items"][:14]:
        row = {}
        for key in ("batsman", "otherBatsman"):
            o = it.get(key)
            if o:
                row[key] = {"athlete": _ath(o), "totalRuns": o.get("totalRuns"),
                            "faced": o.get("faced"), "fours": o.get("fours"),
                            "sixes": o.get("sixes")}
        for key in ("bowler", "otherBowler"):
            o = it.get(key)
            if o:
                row[key] = {"athlete": _ath(o), "balls": o.get("balls"),
                            "conceded": o.get("conceded"), "wickets": o.get("wickets")}
        keep.append(row)
    with open(os.path.join(HERE, "espn_running_totals.json"), "w", encoding="utf-8") as fh:
        json.dump({"commentary": {"items": keep}}, fh, indent=1, ensure_ascii=False)
        fh.write("\n")

    # a REAL flight page, rebuilt small: the module's own chunk encoding around one trimmed
    # innings. Pins flight_text + parse_cb_scorecard_html without shipping a 570KB page.
    card, header = cbb.parse_cb_scorecard_html(
        open(os.path.join(src, "cb_sc_157061.html"), encoding="utf-8").read())
    payload = json.dumps({"matchHeader": header, "scoreCard": trim_cb(card)[:1]},
                         ensure_ascii=False)
    chunk = json.dumps(payload)[1:-1]
    html = ('<html><body><script>self.__next_f.push([1,"%s"])</script></body></html>' % chunk)
    with open(os.path.join(HERE, "flight_page.html"), "w", encoding="utf-8") as fh:
        fh.write(html)
    print("wrote flight_page.html", len(html), "bytes")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
