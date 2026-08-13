#!/usr/bin/env python3
"""registry/cricbuzz_bridge.py — cricbuzz_id ⇄ cricinfo_id, DERIVED FROM PLAY, never from names.

WHY THIS FILE EXISTS
  Cricbuzz is the only feed that attributes a RUN OUT to a fielder. Measured over 24 LPL 2026
  events, ESPN populates `dismissal.fielder` for caught 214/214 and stumped 5/5 and for run out
  **0/19** — so a run-out fielding point can only ever be CREATED from Cricbuzz. But Cricbuzz
  numbers players in its own namespace and there is no table to look the mapping up in:
  people.csv `key_cricbuzz` is populated for 8 of our 679 players (0.3%), and Wikidata has no
  Cricbuzz property at all (confirmed by SPARQL).
  The fallback is NOT a name matcher. Name matching as an identity decider corrupted 20 live rows
  (CLAUDE.md, "Player identity"). So the pairing is derived from what the two feeds INDEPENDENTLY
  OBSERVED the same person DO in the same match, and is emitted only when that observation is
  unique on BOTH sides. Player names appear in this module only inside audit strings a human
  reads — never in a comparison that decides a pairing. `test_names_do_not_decide` pins that:
  replace every name on both sides with "X" and the derived pairs are byte-identical.

TWO LAYERS
  A  performance fingerprint — per player, bat (runs, balls, 4s, 6s) and bowl (balls, conceded,
     wickets). Emit only where the fingerprint is unique on BOTH sides.
  B  dismissal join — map CB `batId` → cricinfo id via Layer A, find the unique ESPN dismissal
     with the same (batsman cricinfo id, type), then pair CB `fielderId1` → ESPN
     `dismissal.fielder.athlete.id`. Layer B is what bridges a BRAND-NEW player from his FIRST
     match: it keys on the already-bridged BATSMAN, so the newcomer needs no stats of his own.
     Join on the bridged batsman, NOT on position — CB orders dismissals by batting order and
     ESPN chronologically, and naive positional alignment scored 5/16 on CB 157138.

THINGS THAT WILL BITE YOU (all measured, all pinned by tests)
  • CB emits an ALL-ZERO batting row for a did-not-bat; ESPN omits the row entirely. Without
    normalising that to "no batting observation" the fingerprints of every bowler-who-didn't-bat
    disagree and bridging falls 98% → 75%. The normalisation is applied to BOTH sides (an ESPN
    batter with 0 off 0 and no dismissal is the mirror case) — a guard on one side only is the
    recurring bug class in this repo.
  • CB `maidens` is CORRUPT on the Hundred: a verbatim copy of `dots` on 13/13 bowlers. This
    module therefore NEVER READS CB `maidens`, on any tour, and excludes maidens from the bowling
    fingerprint on BOTH sides. Measured cost on LPL: zero (see the report in the commit message).
  • CB per-bowler `dots` is 0 for every bowler on the LPL cards. Do not source dots from here.
  • USER-AGENT — the folklore is REFUTED; the DIRECTIONAL RULE still stands. An earlier version of
    this header asserted "www.cricbuzz.com REQUIRES a browser UA (a bot UA gets 403)". MEASURED
    13 Aug 2026 on /live-cricket-scorecard/157138/x: browser UA -> 200 / 609293 B, bot UA -> 200 /
    609293 B, no UA header -> 200 / 609293 B — byte-identical. There is no UA gate on Cricbuzz
    today. The browser UA below is kept as a defensive default only. What IS load-bearing, and
    what the two constants exist to keep apart, is that site.api.espn.com genuinely 403s a browser
    UA (CLAUDE.md; cost a day): never pass CB_UA to an ESPN fetcher and never "tidy" ESPN_UA into
    it. Two constants in two modules, on purpose.
  • THIS MODULE'S CB FETCHER IS NOT THE PRODUCTION ONE. `cricbuzz.cb_fetch` is — it is the single
    fetch path the bot uses (wc_fps_to_csv -> cricbuzz.parse_match -> card_from_cricbuzz_match).
    `fetch_cb_scorecard` here exists for the offline --derive CLI and keeps this module runnable
    with cricbuzz.py absent or mid-edit. Both now refuse to cache or replay an empty body; see
    _cached_get.

WHAT IS STORED vs WHAT MAY BE USED
  registry/cricbuzz_bridge.json stores EVERY confirmation, tiered by how many DISTINCT MATCHES
  produced it. Storage is never gated; USE is:
    tier >= 1  cross-check only. A wrong pair here can only produce a DISAGREEMENT, which
               produces a named Recon row a human reads. Fail-safe direction.
    tier >= 2  required before a CB-ONLY field may CREATE points (run-out attribution,
               direct/assisted, any fielding ESPN leaves blank).
  Independence is counted in MATCHES, not methods: two confirmations inside one match share that
  match's data, so they are not independent evidence.

CONTRADICTION
  If two matches derive DIFFERENT cricinfo ids for one cricbuzz id — or two cricbuzz ids land on
  one cricinfo id — BOTH are refused and moved to `revoked`. Never last-wins: a contradiction is
  evidence that one of the two derivations is wrong, and we do not know which.

IDEMPOTENCE / DETERMINISM
  `confirmations` is an append-only fact log; `bridge` and `revoked` are a PURE FUNCTION of it
  (`compile_bridge`). No clock is read anywhere — a confirmation's `date` is the MATCH date out
  of the payload, so re-deriving a season reproduces the file byte for byte.

⚠ players.json IS A MIRROR, NOT THE STORE. `build_registry.py` rewrites registry/players.json
  from scratch, which ERASES the `cricbuzz_id` field this module writes there. cricbuzz_bridge.json
  is the durable store; re-run `python3 registry/cricbuzz_bridge.py --apply` after every
  build_registry run (it is idempotent and needs no network).

CLI
  python3 registry/cricbuzz_bridge.py --derive --pair 157061:1537342 --pair 157138:1537349 \
          --cb-cache /path --espn-cache /path        # derive + merge + write the store
  python3 registry/cricbuzz_bridge.py --apply        # mirror the store into players.json
  python3 registry/cricbuzz_bridge.py --report       # tiers, revocations, off-registry ids
  python3 registry/cricbuzz_bridge.py --rekey        # apply registry/pid_map.json to stored ci ids
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
from collections import defaultdict, namedtuple

REG_DIR = os.path.dirname(os.path.abspath(__file__))
BRIDGE_PATH = os.path.join(REG_DIR, "cricbuzz_bridge.json")
PLAYERS_PATH = os.path.join(REG_DIR, "players.json")
PID_MAP_PATH = os.path.join(REG_DIR, "pid_map.json")
NEEDS_PATH = os.path.join(REG_DIR, "needs_cricinfo_pending.json")

SCHEMA = 1

# ── User-Agents ────────────────────────────────────────────────────────────────────────────────
# www.cricbuzz.com serves 403 to a bot UA and 200 to a browser UA. site.api.espn.com is the exact
# opposite (CLAUDE.md, "NEVER send a browser User-Agent to ESPN" — its WAF allowlist keys on the
# substring "github.com", which is why ESPN_UA works). Two hosts, two opposite rules; keeping the
# constants apart and named is the whole defence against someone "tidying" them into one.
CB_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36")
ESPN_UA = "wwc-points-bot/1.0 (+https://github.com/nishantsingodia/wwc-points-bot)"

CB_SCORECARD_URL = "https://www.cricbuzz.com/live-cricket-scorecard/{mid}/x"
ESPN_PBP_URL = ("https://site.api.espn.com/apis/site/v2/sports/cricket/{league}/playbyplay"
                "?event={ev}&limit=1000")

# CB wicketCode → ESPN dismissal.type. ESPN does not distinguish caught-and-bowled from caught
# (measured: 0 occurrences of any 'caught and bowled' type across 24 events, 214 'caught'), so
# CAUGHTBOWLED folds into 'caught'. Codes absent from this map are simply not joinable.
CB_WICKET_TO_ESPN = {
    "CAUGHT": "caught",
    "CAUGHTBOWLED": "caught",
    "STUMPED": "stumped",
    "BOWLED": "bowled",
    "LBW": "leg before wicket",
    "RUNOUT": "run out",
    "HITWICKET": "hit wicket",
}
# Only these carry a fielder ESPN can be joined against. Measured on 24 LPL events:
# caught 214/214 have dismissal.fielder, stumped 5/5, run out 0/19, bowled 0/56, lbw 0/32.
FIELDING_CODES = ("CAUGHT", "CAUGHTBOWLED", "STUMPED")

# The substitute marker. Neither feed exposes a structural flag for it — ESPN's dismissal object
# has no `substitute` field (checked on the raw payload), only the rendered text "c sub (Name)";
# Cricbuzz only has "c (sub)Name" inside outDesc. This is NOT name matching: the markers are read
# to REFUSE a pair, never to create one, so no identity can be fabricated by them.
_ESPN_SUB = "sub ("
_CB_SUB = "(sub)"

METHOD_FINGERPRINT = "fingerprint"
METHOD_DISMISSAL = "dismissal"

# Resolution statuses. An absence must never present as a value (CLAUDE.md rule): every failure
# mode gets its own NAME, so a caller cannot mistake "we don't know him" for "he scored nothing".
OK = "ok"
UNKNOWN = "unknown"                    # never seen on any card we bridged
REVOKED = "revoked"                    # contradicted; refused on purpose
INSUFFICIENT_TIER = "insufficient_tier"  # known, but not enough independent matches for this use

PURPOSE_CROSSCHECK = "crosscheck"      # needs 1 confirming match
PURPOSE_CREATE = "create"              # needs 2 independent confirming matches
MIN_MATCHES = {PURPOSE_CROSSCHECK: 1, PURPOSE_CREATE: 2}

Resolution = namedtuple("Resolution", "cricinfo_id tier status detail")


class BridgeError(Exception):
    """A refusal we want LOUD. Never downgrade one of these to a silent empty result."""


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 1. Fetch
# ══════════════════════════════════════════════════════════════════════════════════════════════
def _cached_get(url, cache_path, ua, referer=None, timeout=40):
    if cache_path and os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as fh:
            body = fh.read()
        if body.strip():
            return body
        # An EMPTY cache file is corruption, not content. Cricbuzz answers some ordinary requests
        # with HTTP 204 + an empty body and urllib does not raise on it, so the pre-hardening
        # version of this function wrote that empty body to disk and replayed it forever — every
        # later read then parsed "" into "this match had no players". Same discipline as
        # cricbuzz.cb_fetch, which is the production path.
        raise BridgeError("empty cache file %s — refusing to read it as data" % cache_path)
    headers = {"User-Agent": ua}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", "replace")
    if not body.strip():
        raise BridgeError("HTTP 2xx with an EMPTY body for %s — never cached, never scored" % url)
    if cache_path:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as fh:
            fh.write(body)
        time.sleep(0.3)
    return body


def fetch_cb_scorecard(match_id, cache_dir=None):
    """Cricbuzz scorecard HTML for the offline --derive CLI.

    NOT the production fetcher — `cricbuzz.cb_fetch` is (see the UA note in the module header).
    The browser UA is defensive, not required: measured 13 Aug 2026, Cricbuzz serves a bot UA and
    a browser UA byte-identically. The rule that matters is that this constant never reaches ESPN.
    """
    cache = os.path.join(cache_dir, "cb_sc_%s.html" % match_id) if cache_dir else None
    return _cached_get(CB_SCORECARD_URL.format(mid=match_id), cache, CB_UA,
                       referer="https://www.cricbuzz.com/")


def fetch_espn_pbp(event_id, league="lanka-premier-league", cache_dir=None):
    """ESPN play-by-play JSON. HONEST BOT UA — a browser UA gets 403 and every fetcher swallows
    it, which is indistinguishable from 'ESPN has no data'. Provided for convenience only; the
    bot's own parse_espn has the completeness gate (espn_expected_balls) this module does not
    duplicate. Prefer passing a payload the bot already validated."""
    cache = os.path.join(cache_dir, "pbp_%s.json" % event_id) if cache_dir else None
    return json.loads(_cached_get(ESPN_PBP_URL.format(league=league, ev=event_id), cache, ESPN_UA))


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 2. Cricbuzz payload parsing (Next.js RSC "flight" chunks)
# ══════════════════════════════════════════════════════════════════════════════════════════════
_PUSH = re.compile(r'self\.__next_f\.push\(\[1,\s*"((?:[^"\\]|\\.)*)"\s*\]\)')


def flight_text(html):
    """Concatenate the Next.js RSC flight chunks a Cricbuzz page ships its data in.

    Each chunk is a JS string literal, which is also a JSON string — `json.loads('"' + s + '"')`
    is the CORRECT decode. `unicode_escape` is lossy for non-ASCII (mangles names in audit
    strings) so it is deliberately not used as a fallback: a chunk we cannot decode is dropped
    and shows up as a parse failure downstream, not as silently wrong text.
    """
    out = []
    for chunk in _PUSH.findall(html):
        try:
            out.append(json.loads('"' + chunk + '"'))
        except Exception:
            continue
    return "".join(out)


def _brace_slice(s, i):
    """s[i] is '[' or '{'; return the balanced literal, string-aware. None if unbalanced."""
    depth, j, instr, esc = 0, i, False, False
    while j < len(s):
        c = s[j]
        if instr:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = False
        else:
            if c == '"':
                instr = True
            elif c in "[{":
                depth += 1
            elif c in "]}":
                depth -= 1
                if depth == 0:
                    return s[i:j + 1]
        j += 1
    return None


def _extract_literal(payload, key, opener):
    k = payload.find('"%s"' % key)
    if k < 0:
        return None
    b = payload.find(opener, k)
    if b < 0:
        return None
    lit = _brace_slice(payload, b)
    if lit is None:
        return None
    try:
        return json.loads(lit)
    except Exception:
        return None


def parse_cb_scorecard_html(html):
    """→ (scoreCard list, matchHeader dict). Raises BridgeError rather than returning an empty
    card: a truncated/blank Cricbuzz page must not read as 'nobody played'."""
    payload = flight_text(html)
    if not payload:
        raise BridgeError("cricbuzz: no flight chunks in page (layout change or a block page?)")
    card = _extract_literal(payload, "scoreCard", "[")
    if not card:
        raise BridgeError("cricbuzz: 'scoreCard' missing or unparseable in flight payload")
    header = _extract_literal(payload, "matchHeader", "{") or {}
    return card, header


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 3. Normalised cards — the ONLY shape the bridge logic sees
# ══════════════════════════════════════════════════════════════════════════════════════════════
# {"batting":  {id: (runs, balls, fours, sixes) | None},
#  "bowling":  {id: (balls, conceded, wickets) | None},
#  "dismissals":[{"bat": id, "code"/"type": ..., "fielder": id|None, "bowler": id|None,
#                 "desc": str}],            # CB order = batting order, ESPN order = chronological
#  "names":    {id: str},                   # AUDIT ONLY. Never read by a pairing decision.
#  "totals":   (legal_balls, runs_conceded, bowler_wickets, dismissals)}

def batting_line(runs, balls, fours, sixes):
    """THE did-not-bat normalisation. `None` = "no batting information", and an ALL-ZERO line is
    exactly that. Applied identically to both feeds.

    Cricbuzz writes an all-zero batting row for a player who never batted; ESPN omits the row.
    Left alone, every bowler-who-didn't-bat fingerprints as ((0,0,0,0), bowl) on one side and
    (None, bowl) on the other and never pairs. MEASURED over 16 LPL matches / 377 player slots:
        both sides normalised (shipped)        365  (96.8%)
        CB row left all-zero, ESPN normalised  277  (73.5%)
        ESPN row materialised, CB normalised   278  (73.7%)
    i.e. the 98% → 75% cliff the eval flagged, reproduced — and reproduced in BOTH directions.
    The mirror matters as much as the rule; a guard on one side only is this repo's most
    expensive recurring bug.

    It folds "0 off 0" in with "did not bat" DELIBERATELY, and that is not the "absence read as a
    zero" mistake — this is a FINGERPRINT, not a score, and an all-zero line carries no
    discriminating information either way. Folding them also deletes a whole class of feed
    disagreement about whether a 0-off-0 innings happened at all: LPL cb157039/ev1537340 has
    Lizaad Williams "retd out" 0 off 0 on Cricbuzz's card and NO batting appearance anywhere in
    ESPN's ball-by-ball. Measured: folding gains that pair and loses none. It can never create a
    pair either — it only removes a bucket, so strictly fewer fingerprints can collide.
    """
    if runs == 0 and balls == 0 and fours == 0 and sixes == 0:
        return None
    return (runs, balls, fours, sixes)


def normalize_cb_card(score_card):
    batting, bowling, names, dismissals = {}, {}, {}, []
    tb = tr = tw = 0
    for inn in score_card:
        bat_rows = ((inn.get("batTeamDetails") or {}).get("batsmenData") or {})
        # dict insertion order is bat_1..bat_11 == BATTING ORDER. Preserved so a test can show
        # that positional alignment against ESPN's chronological order does not work.
        for row in bat_rows.values():
            bid = row.get("batId")
            if not bid:
                continue
            bid = str(bid)
            names[bid] = row.get("batName") or ""
            code = (row.get("wicketCode") or "").strip()
            desc = (row.get("outDesc") or "").strip()
            fp = batting_line(row.get("runs", 0) or 0, row.get("balls", 0) or 0,
                              row.get("fours", 0) or 0, row.get("sixes", 0) or 0)
            if fp is not None or bid not in batting:
                batting[bid] = fp
            if code:
                f1 = row.get("fielderId1") or 0
                bw = row.get("bowlerId") or 0
                dismissals.append({"bat": bid, "code": code, "desc": desc,
                                   "fielder": str(f1) if f1 else None,
                                   "bowler": str(bw) if bw else None})
        bowl_rows = ((inn.get("bowlTeamDetails") or {}).get("bowlersData") or {})
        for row in bowl_rows.values():
            wid = row.get("bowlerId")
            if not wid:
                continue
            wid = str(wid)
            names[wid] = row.get("bowlName") or names.get(wid, "")
            balls = row.get("balls", 0) or 0
            runs = row.get("runs", 0) or 0
            wkts = row.get("wickets", 0) or 0
            # NOTE: row["maidens"] and row["dots"] are deliberately NOT read. `maidens` is a
            # verbatim copy of `dots` on the Hundred (13/13 bowlers) and `dots` is 0 for every
            # bowler on the LPL cards. A shared parser must never touch either.
            bowling[wid] = (balls, runs, wkts)
            tb += balls
            tr += runs
            tw += wkts
    for pid in bowling:
        batting.setdefault(pid, None)
    for pid in batting:
        bowling.setdefault(pid, None)
    return {"batting": batting, "bowling": bowling, "dismissals": dismissals,
            "names": names, "totals": (tb, tr, tw, len(dismissals))}


def normalize_espn_card(pbp):
    """Derive a card from ESPN ball-by-ball. Each commentary item carries the batsman's and
    bowler's RUNNING totals after that ball, so the final figure is the max — `otherBatsman` /
    `otherBowler` copies can be stale, which is exactly what max absorbs."""
    items = (pbp.get("commentary") or {}).get("items") or []
    bat_raw, bowl, names = {}, {}, {}
    dismissals, seen_d = [], set()
    for it in items:
        for key in ("batsman", "otherBatsman"):
            b = it.get(key) or {}
            ath = b.get("athlete") or {}
            aid = ath.get("id")
            if not aid:
                continue
            aid = str(aid)
            names[aid] = ath.get("displayName") or names.get(aid, "")
            cur = (b.get("totalRuns", 0) or 0, b.get("faced", 0) or 0,
                   b.get("fours", 0) or 0, b.get("sixes", 0) or 0)
            prev = bat_raw.get(aid)
            if prev is None or cur[1] > prev[1] or (cur[1] == prev[1] and cur[0] > prev[0]):
                bat_raw[aid] = cur
        for key in ("bowler", "otherBowler"):
            b = it.get(key) or {}
            ath = b.get("athlete") or {}
            aid = ath.get("id")
            if not aid:
                continue
            aid = str(aid)
            names[aid] = ath.get("displayName") or names.get(aid, "")
            cur = (b.get("balls", 0) or 0, b.get("conceded", 0) or 0, b.get("wickets", 0) or 0)
            prev = bowl.get(aid)
            if prev is None or cur[0] > prev[0]:
                bowl[aid] = cur
        d = it.get("dismissal") or {}
        if not d.get("dismissal"):
            continue
        bat = ((d.get("batsman") or {}).get("athlete") or {}).get("id")
        fld = ((d.get("fielder") or {}).get("athlete") or {}).get("id")
        bwl = ((d.get("bowler") or {}).get("athlete") or {}).get("id")
        rec = {"bat": str(bat) if bat else None, "type": (d.get("type") or "").lower(),
               "fielder": str(fld) if fld else None, "bowler": str(bwl) if bwl else None,
               "desc": d.get("text") or ""}
        # ESPN can repeat a dismissal across commentary items (a review, a re-post). Dedupe on
        # the dismissal's own content, or the repeat reads as "not unique" and kills the join.
        key = (rec["bat"], rec["type"], rec["fielder"], rec["bowler"])
        if key in seen_d:
            continue
        seen_d.add(key)
        dismissals.append(rec)
    batting = {aid: batting_line(*cur) for aid, cur in bat_raw.items()}
    for pid in bowl:
        batting.setdefault(pid, None)
    bowling = dict(bowl)
    for pid in batting:
        bowling.setdefault(pid, None)
    tb = sum(v[0] for v in bowl.values())
    tr = sum(v[1] for v in bowl.values())
    tw = sum(v[2] for v in bowl.values())
    return {"batting": batting, "bowling": bowling, "dismissals": dismissals,
            "names": names, "totals": (tb, tr, tw, len(dismissals))}


def card_from_cricbuzz_match(m):
    """Adapter for a `cricbuzz.CricbuzzMatch` (duck-typed: `.perf`, `.dismissals`, `.header`).

    cricbuzz.py owns the fetch/parse/dots pipeline; this module owns identity. Nothing is imported
    from it — the adapter takes an already-parsed object — so the bridge and its tests stay
    runnable when that module is absent or mid-change, and there is exactly one place to fix if
    its record shape moves.
    Two deliberate differences from cricbuzz.bat_fingerprint/bowl_fingerprint:
      • batting still goes through `batting_line`, so an all-zero innings folds the same way no
        matter which parser produced it;
      • the bowling tuple is (balls, conceded, wickets) WITHOUT maidens — cricbuzz's 4-tuple
        carries `maidens=None` on The Hundred and a maidens-bearing tuple was measured to pair
        one FEWER player on the LPL, so it is dropped everywhere rather than per-format.
    """
    batting, bowling, names = {}, {}, {}
    tb = tr = tw = 0
    for key, p in m.perf.items():
        pid = str(p.get("cb_id") or key.split(":", 1)[-1])
        names[pid] = p.get("name") or ""
        batting[pid] = (batting_line(p.get("r", 0), p.get("b", 0), p.get("4s", 0), p.get("6s", 0))
                        if p.get("batted") else None)
        if p.get("bowled"):
            bowling[pid] = (p.get("balls", 0), p.get("runs_conceded", 0), p.get("w", 0))
            tb += p.get("balls", 0)
            tr += p.get("runs_conceded", 0)
            tw += p.get("w", 0)
        else:
            bowling[pid] = None
    dismissals = []
    for d in m.dismissals:
        fids = d.get("fielder_cb_ids") or []
        dismissals.append({"bat": str(d.get("batter_cb_id")), "code": d.get("code") or "",
                           "desc": d.get("out_desc") or "",
                           "fielder": str(fids[0]) if fids else None,
                           "bowler": str(d.get("bowler_cb_id")) if d.get("bowler_cb_id") else None})
    return {"batting": batting, "bowling": bowling, "dismissals": dismissals,
            "names": names, "totals": (tb, tr, tw, len(dismissals))}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 4. The same-match gate
# ══════════════════════════════════════════════════════════════════════════════════════════════
TOTALS_LABELS = ("legal_balls", "runs_conceded", "bowler_wickets", "dismissals")
BALLS_TOL = 6          # one over. Measured delta on all 16 real pairings: 0.
MIN_PAIR_YIELD = 0.5   # of the smaller side's observed players
MIN_PAIRS_ABS = 8


def totals_delta(cb_card, espn_card):
    """ADVISORY ONLY — the two feeds' (legal balls, runs conceded, bowler wickets, dismissals).

    A disagreement here is a VALUE disagreement, which belongs in the Recon tab, not in an
    identity decision (CLAUDE.md rule E), and it must NOT veto a bridge. It cannot manufacture a
    wrong pair either: a stat one feed has wrong makes that ONE player's fingerprint fail to
    match, i.e. he silently does not pair — the fail-safe direction.
    Measured on 16 LPL pairings: 14 agree exactly; cb157010/ev1537337 differs by 1 run conceded
    (a genuine L1-class scorer disagreement) and cb157039/ev1537340 by 1 dismissal (CB emits a
    RETD_OUT row, ESPN emits no dismissal item for it). An earlier version of this gate REFUSED
    both matches and threw away 45 good pairs for it. Do not put that back.
    """
    a, b = cb_card["totals"], espn_card["totals"]
    return [{"field": TOTALS_LABELS[i], "cb": a[i], "espn": b[i]}
            for i in range(len(TOTALS_LABELS)) if a[i] != b[i]]


def same_match_gate(cb_card, espn_card, a_pairs):
    """(ok, detail). Answers "did the caller pair the RIGHT CB match with the RIGHT ESPN event,
    at the same state of play?" — name-free, and decided by the Layer-A yield itself.

    MEASURED, 16 CB matches × 16 ESPN events = 256 pairings:
        diagonal (correct pairing)   21–24 pairs
        off-diagonal (wrong pairing)  0–2 pairs, mean 0.37
    Perfect separation, which is what makes the yield a gate rather than a heuristic. Note the
    off-diagonal is NOT always zero — two players in different matches do occasionally share a
    fingerprint — so accepting a mis-paired match would emit up to 2 confidently WRONG pairs.
    Ball totals alone can NOT do this job: 240 legal balls is the modal T20 innings, so the ball
    delta between two DIFFERENT matches is frequently 0. It is kept only as an independent
    structural check on state of play (an ESPN card fetched mid-innings against a final CB card).
    """
    n_cb, n_espn = len(fingerprints(cb_card)), len(fingerprints(espn_card))
    ball_delta = abs(cb_card["totals"][0] - espn_card["totals"][0])
    if ball_delta > BALLS_TOL:
        return False, ("legal balls cb=%d espn=%d (delta %d > %d) — different match, or one side "
                       "is mid-innings" % (cb_card["totals"][0], espn_card["totals"][0],
                                           ball_delta, BALLS_TOL))
    need = max(MIN_PAIRS_ABS, int(MIN_PAIR_YIELD * min(n_cb, n_espn)))
    if len(a_pairs) < need:
        return False, ("only %d fingerprint pairs from cb=%d/espn=%d players (need %d) — these "
                       "two payloads are not the same match at the same state of play"
                       % (len(a_pairs), n_cb, n_espn, need))
    return True, "%d/%d fingerprint pairs, ball delta %d" % (len(a_pairs), min(n_cb, n_espn),
                                                             ball_delta)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 5. Layer A — performance fingerprint
# ══════════════════════════════════════════════════════════════════════════════════════════════
def fingerprints(card):
    """{player_id: (bat_tuple|None, bowl_tuple|None)} for everyone with ANY observation."""
    out = {}
    for pid in set(card["batting"]) | set(card["bowling"]):
        fp = (card["batting"].get(pid), card["bowling"].get(pid))
        if fp[0] is None and fp[1] is None:
            continue          # no observation at all — nothing to pair on. Not an error.
        out[pid] = fp
    return out


def _invert(fps):
    inv = defaultdict(list)
    for pid, fp in sorted(fps.items()):
        inv[fp].append(pid)
    return inv


def layer_a(cb_card, espn_card):
    """→ {cricbuzz_id: cricinfo_id}. UNIQUE ON BOTH SIDES ONLY.

    A fingerprint shared by two players on either side is discarded, not tie-broken: any
    tie-break available here would be the name, and a name may never CREATE a pairing.
    """
    cb_inv, espn_inv = _invert(fingerprints(cb_card)), _invert(fingerprints(espn_card))
    pairs = {}
    for fp, cb_ids in sorted(cb_inv.items(), key=lambda kv: str(kv[0])):
        if len(cb_ids) != 1:
            continue
        espn_ids = espn_inv.get(fp, [])
        if len(espn_ids) != 1:
            continue
        pairs[cb_ids[0]] = espn_ids[0]
    return pairs


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 6. Layer B — dismissal join
# ══════════════════════════════════════════════════════════════════════════════════════════════
def layer_b(cb_card, espn_card, a_pairs):
    """→ ({cricbuzz_fielder_id: cricinfo_id}, [unjoined reasons]).

    Keys on the BRIDGED BATSMAN, never on position: CB lists dismissals in batting order and ESPN
    in chronological order, so positional alignment scored 5/16 on CB 157138 while this scored
    16/16. Because the anchor is the batsman, a fielder who has never batted or bowled — a
    brand-new player in his FIRST match — is bridged from his catch alone.
    """
    by_bat = defaultdict(list)
    for d in espn_card["dismissals"]:
        if d["bat"]:
            by_bat[d["bat"]].append(d)
    pairs, unjoined = {}, []
    for d in cb_card["dismissals"]:
        ci_bat = a_pairs.get(d["bat"])
        if not ci_bat:
            unjoined.append({"reason": "batsman not bridged", "cb_bat": d["bat"],
                             "code": d["code"], "desc": d["desc"]})
            continue
        want = CB_WICKET_TO_ESPN.get(d["code"])
        if not want:
            unjoined.append({"reason": "wicket code not mapped", "cb_bat": d["bat"],
                             "code": d["code"], "desc": d["desc"]})
            continue
        cands = [e for e in by_bat.get(ci_bat, []) if e["type"] == want]
        if len(cands) != 1:
            unjoined.append({"reason": "no unique ESPN dismissal", "cb_bat": d["bat"],
                             "code": d["code"], "desc": d["desc"], "candidates": len(cands)})
            continue
        espn_d = cands[0]
        if d["code"] not in FIELDING_CODES:
            # bowled / lbw / run out: the join succeeded but there is no fielder to pair.
            # ESPN leaves dismissal.fielder empty for run out on 0/19 measured — that hole is the
            # module's only irreducible residual and is reported, never guessed at.
            continue
        if not d["fielder"] or not espn_d["fielder"]:
            unjoined.append({"reason": "fielder absent on one side", "cb_bat": d["bat"],
                             "code": d["code"], "desc": d["desc"]})
            continue
        # ⛔ SUBSTITUTE DISAGREEMENT — the one way this join was measured to go WRONG.
        # LPL match 5 (cb156988/espn1537335): ESPN and cricsheet both say BR McDermott was caught
        # by "sub (Pawan Sandesh)"; Cricbuzz's card says "c Garuka Sanketh", no sub marker. The
        # join therefore paired Cricbuzz's Garuka Sanketh onto Pawan Sandesh's cricinfo id — a
        # confidently wrong identity manufactured out of a VALUE disagreement about who took the
        # catch. It survived only because Layer A had independently pinned that same cricbuzz id
        # elsewhere in the match and the contradiction detector caught it; had he not batted or
        # bowled it would have shipped silently.
        # When exactly ONE side marks the fielder a substitute the two feeds are not describing
        # the same fielder, so the pair is refused. Measured over 16 matches / 144 fielder
        # emissions: 5 substitute catches, 4 marked sub on BOTH sides (all four correct against
        # cricsheet, and they are the highest-value pairs Layer B produces — a substitute never
        # bats or bowls, so Layer A can never reach him), 1 marked on one side only (the corrupt
        # one). This guard removes exactly the wrong pair and none of the right ones.
        sub_cb = _CB_SUB in (d["desc"] or "")
        sub_espn = _ESPN_SUB in (espn_d["desc"] or "")
        if sub_cb != sub_espn:
            unjoined.append({"reason": "substitute disagreement (cb_sub=%s espn_sub=%s)"
                                       % (sub_cb, sub_espn),
                             "cb_bat": d["bat"], "code": d["code"], "desc": d["desc"],
                             "espn_desc": espn_d["desc"]})
            continue
        pairs[d["fielder"]] = espn_d["fielder"]
    return pairs, unjoined


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 7. Deriving a match → confirmations
# ══════════════════════════════════════════════════════════════════════════════════════════════
def match_date(header):
    """UTC date of the match, from the payload. Deterministic ON PURPOSE — no clock is read
    anywhere in this module, so re-deriving a season reproduces the store byte for byte."""
    ts = header.get("matchStartTimestamp") or header.get("startDate")
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d", time.gmtime(int(ts) / 1000.0))


def _id_key(pid):
    """Deterministic ordering for feed ids. They are numeric in practice, but a non-numeric one
    must sort rather than raise — an ordering helper is not the place to blow up a season."""
    pid = str(pid)
    return (0, int(pid), "") if pid.isdigit() else (1, 0, pid)


def match_key(cb_match_id, espn_event_id):
    return "cb%s/espn%s" % (cb_match_id, espn_event_id)


def derive_match(cb_card, espn_card, cb_match_id, espn_event_id, date=""):
    """→ (confirmations, diagnostics). Raises BridgeError if the two payloads are not the same
    match at the same state of play."""
    key = match_key(cb_match_id, espn_event_id)
    a_pairs = layer_a(cb_card, espn_card)
    ok, detail = same_match_gate(cb_card, espn_card, a_pairs)
    if not ok:
        raise BridgeError("cb%s vs espn%s: %s" % (cb_match_id, espn_event_id, detail))
    b_pairs, unjoined = layer_b(cb_card, espn_card, a_pairs)

    confirmations = []
    for cb_id, ci_id in sorted(a_pairs.items(), key=lambda kv: _id_key(kv[0])):
        confirmations.append({"cricbuzz_id": cb_id, "cricinfo_id": ci_id,
                              "match": key, "method": METHOD_FINGERPRINT, "date": date})
    for cb_id, ci_id in sorted(b_pairs.items(), key=lambda kv: _id_key(kv[0])):
        if a_pairs.get(cb_id) == ci_id:
            continue      # already stated by Layer A in this same match — not new evidence
        confirmations.append({"cricbuzz_id": cb_id, "cricinfo_id": ci_id,
                              "match": key, "method": METHOD_DISMISSAL, "date": date})
    # Everyone Cricbuzz saw play whom NEITHER layer could pair. This is the residual a human has
    # to look at, and it goes to the "Needs Cricinfo ID" tab — it is an identity question, never
    # a Recon one. Name is carried for the human to read, not for the code to match on.
    bridged = set(a_pairs) | set(b_pairs)
    unbridged = [{"cricbuzz_id": pid, "name": cb_card["names"].get(pid, ""),
                  "why": "fingerprint not unique on both sides; no dismissal to join on"}
                 for pid in sorted(fingerprints(cb_card), key=_id_key) if pid not in bridged]

    # Layer B disagreeing with Layer A about the SAME cricbuzz id inside ONE match is the
    # loudest corruption signal there is. Both claims are still emitted — compile_bridge revokes
    # the pair and a human sees WHICH two cricinfo ids were claimed; suppressing one here would
    # hide the disagreement and quietly keep a pair that might be the wrong one. The only such
    # case measured in 16 matches was the substitute mix-up now caught in layer_b, so if this
    # list is ever non-empty a NEW failure mode has appeared and wants reading.
    conflicts = [{"cricbuzz_id": k, "layer_a": a_pairs[k], "layer_b": b_pairs[k]}
                 for k in sorted(b_pairs) if k in a_pairs and a_pairs[k] != b_pairs[k]]

    diag = {"match": key, "date": date, "gate": detail,
            "layer_a": len(a_pairs), "layer_b": len(b_pairs),
            "layer_b_new": sum(1 for k in b_pairs if k not in a_pairs),
            "layer_conflicts": conflicts,
            "cb_players": len(fingerprints(cb_card)), "espn_players": len(fingerprints(espn_card)),
            "totals_delta": totals_delta(cb_card, espn_card),
            "unjoined": unjoined, "unbridged_cb": unbridged}
    return confirmations, diag


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 8. The store — an append-only fact log, everything else derived from it
# ══════════════════════════════════════════════════════════════════════════════════════════════
def _conf_sort_key(c):
    return (str(c["cricbuzz_id"]), c["match"], c["method"], str(c["cricinfo_id"]))


def merge_confirmations(existing, new):
    """Union, deduped on (cricbuzz_id, match, method, cricinfo_id), deterministically ordered.
    A CONFLICTING claim is kept, not overwritten — that is what makes the contradiction visible."""
    seen, out = set(), []
    for c in sorted(list(existing) + list(new), key=_conf_sort_key):
        k = _conf_sort_key(c)
        if k in seen:
            continue
        seen.add(k)
        out.append({"cricbuzz_id": str(c["cricbuzz_id"]), "cricinfo_id": str(c["cricinfo_id"]),
                    "match": c["match"], "method": c["method"], "date": c.get("date", "")})
    return out


def compile_bridge(confirmations):
    """PURE function of the fact log → ({bridge}, {revoked}).

    Two contradiction directions, and both are checked — a guard applied to one side but not its
    mirror is this repo's most expensive recurring bug:
      cb → ci   one cricbuzz id claimed by two cricinfo ids
      ci → cb   one cricinfo id claimed by two cricbuzz ids
    Either way BOTH claims are refused. Never last-wins: a contradiction says one derivation is
    wrong and does not say which.
    """
    by_cb = defaultdict(list)
    for c in confirmations:
        by_cb[str(c["cricbuzz_id"])].append(c)

    bridge, revoked = {}, {}
    for cb_id in sorted(by_cb, key=_id_key):
        confs = sorted(by_cb[cb_id], key=_conf_sort_key)
        claims = defaultdict(list)
        for c in confs:
            claims[str(c["cricinfo_id"])].append(c["match"])
        # PROVENANCE, stored per pair so any single pair can be traced and revoked on its own.
        # The cricbuzz id lives in the key and (for a clean pair) the cricinfo id in the record,
        # so a confirmation carries only what varies. There is no second flat copy of this log:
        # `confirmations_log()` reconstructs it, which keeps ONE source of truth in the file.
        if len(claims) > 1:
            revoked[cb_id] = {"reason": "cb_id claimed by %d cricinfo ids" % len(claims),
                              "claims": {ci: sorted(set(ms)) for ci, ms in sorted(claims.items())},
                              "confirmations": [{"cricinfo_id": c["cricinfo_id"],
                                                 "match": c["match"], "method": c["method"],
                                                 "date": c["date"]} for c in confs]}
            continue
        ci_id = next(iter(claims))
        matches = sorted({c["match"] for c in confs})
        bridge[cb_id] = {"cricinfo_id": ci_id, "tier": len(matches), "matches": matches,
                         "methods": sorted({c["method"] for c in confs}),
                         "confirmations": [{"match": c["match"], "method": c["method"],
                                            "date": c["date"]} for c in confs]}

    # mirror direction
    by_ci = defaultdict(list)
    for cb_id, rec in bridge.items():
        by_ci[rec["cricinfo_id"]].append(cb_id)
    for ci_id, cb_ids in sorted(by_ci.items()):
        if len(cb_ids) < 2:
            continue
        for cb_id in sorted(cb_ids):
            rec = bridge.pop(cb_id)
            revoked[cb_id] = {"reason": "cricinfo id %s claimed by %d cricbuzz ids" % (ci_id, len(cb_ids)),
                              "claims": {ci_id: rec["matches"]},
                              "collides_with": sorted(x for x in cb_ids if x != cb_id),
                              "confirmations": [dict(c, cricinfo_id=ci_id)
                                                for c in rec["confirmations"]]}
    return bridge, revoked


def confirmations_log(store):
    """Flatten the per-pair provenance back into the full-form fact log compile_bridge eats.
    The file stores each confirmation exactly ONCE, under its pair; this is the reader."""
    out = []
    for cb_id, rec in store.get("bridge", {}).items():
        for c in rec["confirmations"]:
            out.append({"cricbuzz_id": cb_id, "cricinfo_id": rec["cricinfo_id"],
                        "match": c["match"], "method": c["method"], "date": c.get("date", "")})
    for cb_id, rec in store.get("revoked", {}).items():
        for c in rec["confirmations"]:
            out.append({"cricbuzz_id": cb_id, "cricinfo_id": c["cricinfo_id"],
                        "match": c["match"], "method": c["method"], "date": c.get("date", "")})
    return sorted(out, key=_conf_sort_key)


def load_store(path=BRIDGE_PATH):
    if not os.path.exists(path):
        return {"_schema": SCHEMA, "_anchor": "cricbuzz_id -> cricinfo_id",
                "bridge": {}, "revoked": {}}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def build_store(confirmations):
    bridge, revoked = compile_bridge(confirmations)
    return {
        "_schema": SCHEMA,
        "_anchor": ("cricbuzz_id -> cricinfo_id, derived from unique performance fingerprints "
                    "(Layer A) and the dismissal join (Layer B). NEVER from names."),
        "_tiers": ("tier = number of DISTINCT MATCHES confirming the pair. >=1 cross-check only; "
                   ">=2 required before a CB-only field may CREATE points."),
        "_note": ("each pair's `confirmations` list IS the fact log; bridge/revoked are a pure "
                  "function of it (compile_bridge o confirmations_log). Regenerate, never "
                  "hand-edit. A contradicted pair moves to `revoked` and is refused BOTH ways — "
                  "there is no last-wins."),
        "counts": {"confirmations": len(merge_confirmations(confirmations, [])),
                   "bridged": len(bridge), "revoked": len(revoked),
                   "tier2_plus": sum(1 for v in bridge.values() if v["tier"] >= 2)},
        "bridge": bridge,
        "revoked": revoked,
    }


def save_store(store, path=BRIDGE_PATH):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(store, fh, indent=1, ensure_ascii=False, sort_keys=False)
        fh.write("\n")
    return path


def resolve(store, cricbuzz_id, purpose=PURPOSE_CROSSCHECK):
    """The ONLY sanctioned read path. Returns a Resolution whose `status` names the failure —
    never a bare None a caller can mistake for a zero."""
    cb_id = str(cricbuzz_id)
    if cb_id in store.get("revoked", {}):
        return Resolution(None, 0, REVOKED, store["revoked"][cb_id]["reason"])
    rec = store.get("bridge", {}).get(cb_id)
    if not rec:
        return Resolution(None, 0, UNKNOWN, "no confirmation on any bridged match")
    need = MIN_MATCHES[purpose]
    if rec["tier"] < need:
        return Resolution(None, rec["tier"], INSUFFICIENT_TIER,
                          "tier %d < %d required for %s" % (rec["tier"], need, purpose))
    return Resolution(rec["cricinfo_id"], rec["tier"], OK,
                      "%d match(es): %s" % (rec["tier"], ", ".join(rec["matches"])))


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 9. Mirror into players.json  +  route the residual to "Needs Cricinfo ID"
# ══════════════════════════════════════════════════════════════════════════════════════════════
def apply_to_players_json(store, players_path=PLAYERS_PATH, write=True):
    """Write the resolved cricbuzz_id onto the ci: entry so every consumer reads the REGISTRY,
    never a local per-app map. `cricbuzz_tier` travels with it so a consumer can gate CREATE use
    without loading this store.

    Does NOT create registry entries. A cricinfo id we bridged but do not carry is an IDENTITY
    question → the "Needs Cricinfo ID" tab, never the Recon tab.
    ⚠ build_registry.py rewrites players.json wholesale and will erase these two fields; re-run
    `--apply` after it. cricbuzz_bridge.json is the store, players.json is only the mirror.
    """
    with open(players_path, encoding="utf-8") as fh:
        reg = json.load(fh)
    players = reg["players"]
    by_ci = {}
    for pid, rec in players.items():
        ci = rec.get("cricinfo_id")
        if ci:
            by_ci.setdefault(str(ci), []).append(pid)

    applied, off_registry, cleared = 0, [], 0
    wanted = {}
    for cb_id, rec in store.get("bridge", {}).items():
        pids = by_ci.get(rec["cricinfo_id"], [])
        if not pids:
            off_registry.append({"cricbuzz_id": cb_id, "cricinfo_id": rec["cricinfo_id"],
                                 "tier": rec["tier"], "matches": rec["matches"]})
            continue
        for pid in pids:
            wanted[pid] = (cb_id, rec["tier"])

    for pid, rec in players.items():
        want = wanted.get(pid)
        if want:
            if rec.get("cricbuzz_id") != want[0] or rec.get("cricbuzz_tier") != want[1]:
                applied += 1
            rec["cricbuzz_id"], rec["cricbuzz_tier"] = want[0], want[1]
        elif "cricbuzz_id" in rec:
            # a pair that was revoked since the last apply must DISAPPEAR from the mirror.
            rec.pop("cricbuzz_id", None)
            rec.pop("cricbuzz_tier", None)
            cleared += 1

    if write:
        with open(players_path, "w", encoding="utf-8") as fh:
            json.dump(reg, fh, indent=1, ensure_ascii=False)
    return {"applied": applied, "cleared": cleared, "off_registry": off_registry,
            "mirrored": len(wanted)}


def needs_cricinfo_rows(store, diagnostics, players_path=PLAYERS_PATH):
    """Rows for the "Needs Cricinfo ID" tab — the identity tab, per CLAUDE.md rule E. Value
    disagreements go to Recon; none of these are value disagreements.

    Three residual classes:
      revoked         — contradicted pair, a human must say which claim is wrong
      off_registry    — bridged to a cricinfo id the registry does not carry
      unbridged       — a Cricbuzz player no layer could pair in the matches supplied
    """
    with open(players_path, encoding="utf-8") as fh:
        known = {str(r.get("cricinfo_id")) for r in json.load(fh)["players"].values()
                 if r.get("cricinfo_id")}
    rows = []
    for cb_id, rec in sorted(store.get("revoked", {}).items()):
        rows.append({"cricbuzz_id": cb_id, "issue": "contradicted", "detail": rec["reason"],
                     "claims": rec.get("claims", {})})
    for cb_id, rec in sorted(store.get("bridge", {}).items()):
        if rec["cricinfo_id"] not in known:
            rows.append({"cricbuzz_id": cb_id, "issue": "cricinfo id not in registry",
                         "cricinfo_id": rec["cricinfo_id"], "tier": rec["tier"],
                         "cricinfo_url": "https://www.espncricinfo.com/cricketers/x-%s"
                                         % rec["cricinfo_id"]})
    for d in diagnostics or []:
        for u in d.get("unbridged_cb", []):
            rows.append({"cricbuzz_id": u["cricbuzz_id"], "issue": "unbridged on %s" % d["match"],
                         "detail": u.get("why", ""), "name_for_human": u.get("name", "")})
    return rows


def rekey(store, pid_map_path=PID_MAP_PATH):
    """Apply registry/pid_map.json (old pid → 'ci:<id>') to the STORED cricinfo ids.

    This file is keyed on the cricbuzz id and carries the cricinfo id as a VALUE, so an identity
    migration that re-anchors ci ids orphans the values, not the keys. That is exactly what
    happened to the draft's pid-keyed data/player-photos.json — orphaned for 4 days, 5/838
    resolving. Re-keying is therefore a first-class command, not a note in a doc.
    """
    with open(pid_map_path, encoding="utf-8") as fh:
        pmap = json.load(fh)
    lut = {}
    for old, new in pmap.items():
        if isinstance(new, str) and new.startswith("ci:"):
            lut[str(old)] = new[3:]
    log, changed = confirmations_log(store), 0
    for c in log:
        new = lut.get(str(c["cricinfo_id"]))
        if new and new != c["cricinfo_id"]:
            c["cricinfo_id"] = new
            changed += 1
    return build_store(merge_confirmations(log, [])), changed


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 10. CLI
# ══════════════════════════════════════════════════════════════════════════════════════════════
def _load_pair(cb_id, espn_id, cb_cache, espn_cache, league):
    html = fetch_cb_scorecard(cb_id, cb_cache)
    card, header = parse_cb_scorecard_html(html)
    cb = normalize_cb_card(card)
    espn = normalize_espn_card(fetch_espn_pbp(espn_id, league, espn_cache))
    return cb, espn, match_date(header)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--derive", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--needs", action="store_true",
                    help="print the residual as Needs-Cricinfo-ID rows (identity tab, never Recon)")
    ap.add_argument("--rekey", action="store_true")
    ap.add_argument("--pair", action="append", default=[], metavar="CBID:ESPNID")
    ap.add_argument("--cb-cache", default=None)
    ap.add_argument("--espn-cache", default=None)
    ap.add_argument("--league", default="lanka-premier-league")
    ap.add_argument("--store", default=BRIDGE_PATH)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    store = load_store(args.store)
    if args.derive:
        confs, diags = [], []
        for spec in args.pair:
            cb_id, espn_id = spec.split(":")
            try:
                cb, espn, date = _load_pair(cb_id, espn_id, args.cb_cache, args.espn_cache,
                                            args.league)
                c, d = derive_match(cb, espn, cb_id, espn_id, date)
            except BridgeError as e:
                print("REFUSED %s: %s" % (spec, e), file=sys.stderr)
                continue
            confs.extend(c)
            diags.append(d)
            print("%s  layerA=%d layerB=%d (new %d)  cb=%d espn=%d"
                  % (d["match"], d["layer_a"], d["layer_b"], d["layer_b_new"],
                     d["cb_players"], d["espn_players"]), file=sys.stderr)
            for x in d["layer_conflicts"]:
                print("   ⚠ LAYER CONFLICT in one match: cb%s -> A says ci:%s, B says ci:%s"
                      % (x["cricbuzz_id"], x["layer_a"], x["layer_b"]), file=sys.stderr)
            for x in d["totals_delta"]:
                print("   advisory (Recon, not identity): %s cb=%s espn=%s"
                      % (x["field"], x["cb"], x["espn"]), file=sys.stderr)
        store = build_store(merge_confirmations(confirmations_log(store), confs))
        if not args.dry_run:
            save_store(store, args.store)
    if args.rekey:
        store, n = rekey(store)
        print("rekey: %d confirmations re-anchored" % n, file=sys.stderr)
        if not args.dry_run:
            save_store(store, args.store)
    if args.apply:
        res = apply_to_players_json(store, write=not args.dry_run)
        print("players.json: mirrored=%d changed=%d cleared=%d off-registry=%d"
              % (res["mirrored"], res["applied"], res["cleared"], len(res["off_registry"])),
              file=sys.stderr)
        for o in res["off_registry"]:
            print("   off-registry -> Needs Cricinfo ID: cb%s -> ci:%s (tier %d)"
                  % (o["cricbuzz_id"], o["cricinfo_id"], o["tier"]), file=sys.stderr)
    if args.report:
        print(json.dumps(store.get("counts", {}), indent=1))
        hist = defaultdict(int)
        for rec in store.get("bridge", {}).values():
            hist[rec["tier"]] += 1
        print("tier histogram (matches confirming): %s"
              % json.dumps({str(k): hist[k] for k in sorted(hist)}))
        print("CREATE-capable (tier>=2): %d of %d"
              % (sum(v for k, v in hist.items() if k >= 2), len(store.get("bridge", {}))))
        for cb_id, rec in sorted(store.get("revoked", {}).items()):
            print("REVOKED cb%s: %s %s" % (cb_id, rec["reason"], json.dumps(rec.get("claims"))))
    if args.needs:
        # An unbridgeable Cricbuzz player is an IDENTITY question, so it goes to the "Needs
        # Cricinfo ID" tab and never to Recon (CLAUDE.md rule E). Printed rather than written
        # into needs_cricinfo_pending.json because build_registry.py OWNS that file and rewrites
        # it wholesale — appending here would be erased on the next build, i.e. written and never
        # read. Pipe this into tour_sync_finalize.write_needs_cricinfo_tab to close the loop.
        print(json.dumps(needs_cricinfo_rows(store, []), indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
