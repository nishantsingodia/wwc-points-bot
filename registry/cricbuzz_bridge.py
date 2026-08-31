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

⛔ THE CORPUS MUST NOT LAG THE PIN LEDGER (fixed 16 Aug 2026)
  `registry/cricbuzz_match_map.json` knows every cb-match ⇄ ESPN-event pairing that has ever been
  confirmed; --derive took HAND-TYPED `--pair` args and nothing joined the two. The corpus drifted
  behind and stayed there: **83 of 94 pins derived, 11 not** — and six of the twelve `cb:` rows
  then sitting on "Needs Cricinfo ID" were ONE of the eleven (cb154370, CPL Guyana v Jamaica,
  14 Aug), whose whole XI Layer A pairs 22/22. `--derive --from-map` is now the way to run it, and
  `test_the_derive_corpus_does_not_lag_the_pin_ledger` fails if a pin is ever left underived again.
  It re-derives EVERY pin, not just the gap: the store is a pure function of the fact log, so a
  logic change only reaches stored confirmations if their match is derived again.

CLI
  python3 registry/cricbuzz_bridge.py --derive --from-map    # ← THE ONE TO RUN. Pairs come from
          # the pin ledger, ESPN play-by-play from the bot's own WC_CACHE_DIR: normally offline.
  python3 registry/cricbuzz_bridge.py --derive --pair 157061:1537342 --pair 157138:1537349 \
          --cb-cache /path --espn-cache /path        # one-off, hand-typed
  python3 registry/cricbuzz_bridge.py --adopt 12163:633660 --why "owner answered the tab"
          # record a HUMAN answer to a `cb:` Needs-Cricinfo-ID row. tier 1: cross-check only, and
          # it never outranks derived evidence — a contradiction revokes both, as always.
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
import urllib.error
import urllib.request
from collections import defaultdict, namedtuple

# The SHARED name model, used by Layer C. Imported both ways because this module loads as
# `from registry import cricbuzz_bridge` (repo root on sys.path) AND runs directly as
# `python3 registry/cricbuzz_bridge.py --derive` (only registry/ on sys.path).
# `_surname_of` / `_initial_of` are IMPORTED, never re-implemented: they define what a surname and
# an initial ARE, and name_agrees() must answer that exactly as the model does or the filter would
# reject pairs the matcher legitimately made. A second definition is the drift that file forbids.
try:
    from cricket_identity import fuzzy_match_name, norm_name, _surname_of, _initial_of
except ImportError:  # pragma: no cover - direct-run path
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from cricket_identity import fuzzy_match_name, norm_name, _surname_of, _initial_of

REG_DIR = os.path.dirname(os.path.abspath(__file__))
BRIDGE_PATH = os.path.join(REG_DIR, "cricbuzz_bridge.json")
PLAYERS_PATH = os.path.join(REG_DIR, "players.json")
PID_MAP_PATH = os.path.join(REG_DIR, "pid_map.json")
NEEDS_PATH = os.path.join(REG_DIR, "needs_cricinfo_pending.json")
MATCH_MAP_PATH = os.path.join(REG_DIR, "cricbuzz_match_map.json")
TOURS_PATH = os.path.join(os.path.dirname(REG_DIR), "tours.json")
# The bot's own HTTP cache. `cricbuzz.cb_fetch` and `wc_fps_to_csv.espn_get` both write here, so
# everything a --derive needs is normally already on disk and the whole pass is offline.
BOT_CACHE = os.environ.get("WC_CACHE_DIR", "/tmp/wc_api_cache")

SCHEMA = 1

# ── User-Agents ────────────────────────────────────────────────────────────────────────────────
# www.cricbuzz.com serves 403 to a bot UA and 200 to a browser UA. site.api.espn.com is the exact
# opposite (CLAUDE.md, "NEVER send a browser User-Agent to ESPN" — its WAF allowlist keys on the
# substring "github.com", which is why ESPN_UA works). Two hosts, two opposite rules; keeping the
# constants apart and named is the whole defence against someone "tidying" them into one.
CB_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36")
ESPN_UA = "wwc-points-bot/1.0 (+https://github.com/nishantsingodia/wwc-points-bot)"

CB_HOST = "https://www.cricbuzz.com"
CB_SCORECARD_URL = CB_HOST + "/live-cricket-scorecard/{mid}/x"
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
METHOD_SPLIT = "split-fingerprint"   # batting OR bowling alone. Layer A2. Performance evidence.
METHOD_DISMISSAL = "dismissal"
METHOD_NAME = "name"            # Layer C. WITNESS-GRADE ONLY — see resolve()'s create gate.

# Methods resting on what the two feeds INDEPENDENTLY OBSERVED someone do. A name is not one: it
# is a label both feeds copied from the same source, so N matches of it is one fact repeated, not
# N independent facts. resolve() counts only these toward the `create` bar.
PERFORMANCE_METHODS = frozenset({METHOD_FINGERPRINT, METHOD_SPLIT, METHOD_DISMISSAL})
METHOD_MANUAL = "manual"        # the OWNER answered a "Needs Cricinfo ID" row. See adopt().
# A manual confirmation has no match, so it needs a match KEY that cannot collide with a real one
# and cannot be mistaken for one. Because tier counts DISTINCT MATCHES, every manual answer for a
# player collapses into this single slot — so a hand-typed id yields tier 1 (cross-check) and can
# never on its own reach the tier 2 that CREATING points from a Cricbuzz-only field requires.
MANUAL_MATCH = "manual/owner-answer"

# Resolution statuses. An absence must never present as a value (CLAUDE.md rule): every failure
# mode gets its own NAME, so a caller cannot mistake "we don't know him" for "he scored nothing".
OK = "ok"
UNKNOWN = "unknown"                    # never seen on any card we bridged
REVOKED = "revoked"                    # contradicted; refused on purpose
INSUFFICIENT_TIER = "insufficient_tier"  # known, but not enough independent matches for this use
NAME_ONLY = "name_only"                # known by NAME alone — may witness, may never create

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
    pbp = json.loads(_cached_get(ESPN_PBP_URL.format(league=league, ev=event_id), cache, ESPN_UA))
    # Completeness, on the network path too — `limit=1000` covers a T20/Hundred innings pair with
    # room to spare, but "covers it in practice" is not a check. A card short of ESPN's own
    # `count`, or spread over a page this fetcher never asks for, would silently change the
    # fingerprints identity is derived from. Raise; never derive from a partial card.
    com = (pbp.get("commentary") or {}) if isinstance(pbp, dict) else {}
    items = com.get("items") or []
    try:
        npages = int(com.get("pageCount") or 1)
    except (TypeError, ValueError):
        npages = 1
    try:
        expected = int(com.get("count")) if com.get("count") is not None else None
    except (TypeError, ValueError):
        expected = None
    if npages > 1:
        raise BridgeError("espn %s: play-by-play is %d pages at limit=1000 and this fetcher reads "
                          "only the first — refusing a truncated card" % (event_id, npages))
    if expected is not None and len(items) < expected:
        raise BridgeError("espn %s: %d of %d deliveries — truncated, refusing to derive identity "
                          "from it" % (event_id, len(items), expected))
    return pbp


def espn_pbp_from_bot_cache(event_id, espn_series, cache_dir):
    """The SAME play-by-play the bot already fetched, read out of WC_CACHE_DIR. None if absent.

    WHY: the two modules cache the identical bytes under different names. `cricbuzz.cb_fetch`
    writes `cb_sc_<mid>.html`, which is byte-for-byte the name `fetch_cb_scorecard` uses, so the
    Cricbuzz half of a --derive has always been free. The ESPN half was not: the bot writes
    `espn_<series>_playbyplay_event_<ev>_limit_500_page_<n>.json` and this module looked for
    `pbp_<ev>.json`, so every --derive re-fetched ESPN over the network. That is why the derive
    corpus was hand-driven and why it fell 11 matches behind the pin ledger.

    ⛔ COMPLETENESS IS CHECKED HERE, not assumed. `parse_espn` refuses a short card because a
    truncated innings scores as if complete (the Hundred Women's sample came back 197 balls short
    when one page 502'd). A truncated card is just as dangerous to IDENTITY: it silently changes
    a player's fingerprint, which can pair him with the wrong person or drop him. So every page
    ESPN says exists must be on disk and the item count must reach ESPN's own `count`; anything
    less RAISES rather than returning a partial card — an absence must never present as a value.
    """
    pages, expected, npages = [], None, 1
    page = 1
    while page <= 10:
        fp = os.path.join(cache_dir, "espn_%s_playbyplay_event_%s_limit_500_page_%d.json"
                          % (espn_series, event_id, page))
        if not os.path.exists(fp):
            if page == 1:
                return None                       # not cached at all — the caller may fetch
            raise BridgeError("espn %s: playbyplay page %d of %d missing from %s — refusing to "
                              "derive identity from a truncated card"
                              % (event_id, page, npages, cache_dir))
        with open(fp, encoding="utf-8") as fh:
            body = fh.read()
        if not body.strip():
            raise BridgeError("espn %s: empty cache file %s — refusing to read it as data"
                              % (event_id, fp))
        d = json.loads(body)
        com = (d.get("commentary") or {}) if isinstance(d, dict) else {}
        if "commentary" not in (d or {}):
            raise BridgeError("espn %s: cached page %d has no `commentary` key" % (event_id, page))
        pages.append(d)
        if page == 1:
            try:
                npages = int(com.get("pageCount") or 1)
            except (TypeError, ValueError):
                npages = 1
            try:
                expected = int(com.get("count")) if com.get("count") is not None else None
            except (TypeError, ValueError):
                expected = None
        if page >= npages:
            break
        page += 1
    items = []
    for d in pages:
        items += ((d.get("commentary") or {}).get("items") or [])
    if expected is not None and len(items) < expected:
        raise BridgeError("espn %s: cached %d of %d deliveries — truncated, refusing to derive "
                          "identity from it" % (event_id, len(items), expected))
    # ESPN emits byte-identical duplicate commentary items (ev1537345: 259 items, 255 unique ids).
    # normalize_espn_card takes a MAX over running totals so a duplicate cannot inflate a
    # fingerprint the way it inflated dots in the scorer — deduping anyway keeps the two readers'
    # inputs identical, so a fingerprint derived here is the one the bot's card would give.
    seen, uniq = set(), []
    for it in items:
        iid = it.get("id")
        if iid is not None:
            if iid in seen:
                continue
            seen.add(iid)
        uniq.append(it)
    merged = dict(pages[0])
    merged["commentary"] = dict(pages[0].get("commentary") or {}, items=uniq)
    return merged


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
        # RECORD THE NAME OF EVERYONE A DISMISSAL NAMES, not just batters and bowlers. A PURE
        # FIELDER never appears in `batsman`/`bowler`, so his displayName was nowhere on this card
        # — and Layer B exists precisely for that man. Without this, derive_match's name gate
        # compares a real cricbuzz name against "" and reads the ABSENCE as disagreement: it
        # rejected 35 of 51 sound pairs on the first cut. Names only — the fingerprint pools key
        # off `batting`/`bowling`, so nobody new becomes matchable by Layer A/A2/C.
        for _who in ("batsman", "fielder", "bowler"):
            _ath = (d.get(_who) or {}).get("athlete") or {}
            _aid = _ath.get("id")
            if _aid and not names.get(str(_aid)):
                names[str(_aid)] = _ath.get("displayName") or _ath.get("fullName") or ""
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


def layer_a2_split(cb_card, espn_card, done_cb, done_ci):
    """→ {cricbuzz_id: cricinfo_id}. Layer A, RE-RUN PER DISCIPLINE, on the residual only.

    WHY (owner, 25 Aug 2026). Layer A welds batting and bowling into ONE key, so a disagreement in
    EITHER discipline loses the whole player — even when the other is a perfect, unique match. Real
    cases in the pinned corpus: Dani Gibson (Hundred-W, 2 Aug) has an exactly-matching batting line
    and a disagreeing bowling line; same shape for Luke Wood, Gus Atkinson, Brydon Carse, Georgia
    Elwiss. **+10 pairs over 100 matches, 0 conflicts.**

    A SECOND PASS, never a replacement: splitting makes each key less unique, so run alone it scores
    WORSE than the combined key (2015 vs 2053 on the same corpus). Combined first for precision,
    split second for reach. Worth 10 pairs Layer C would also have caught — but caught HERE they
    carry performance evidence, so they may create points, where a name-only bridge may not.

    Min-ball guard per the owner's spec: a batting line counts only with >=1 ball FACED, a bowling
    line only with >=1 ball BOWLED. A zero-ball line carries no discriminating information and
    would collide with every other zero-ball line in the match.
    """
    pairs, conflicted = {}, set()
    for discipline, ball_pos in (("batting", 1), ("bowling", 0)):
        def index(card, skip):
            out = defaultdict(list)
            for pid, line in card[discipline].items():
                if not line or line[ball_pos] < 1 or pid in skip:
                    continue
                out[line].append(pid)
            return out
        cb_idx, espn_idx = index(cb_card, done_cb), index(espn_card, done_ci)
        for line, cb_ids in sorted(cb_idx.items(), key=lambda kv: str(kv[0])):
            espn_ids = espn_idx.get(line, [])
            if len(cb_ids) != 1 or len(espn_ids) != 1:
                continue
            prior = pairs.get(cb_ids[0])
            if prior is not None and str(prior) != str(espn_ids[0]):
                # His BATTING says he is one man, his BOWLING says another. A contradiction is not
                # a choice — refuse both, as every other contradiction here is handled. (0 in the
                # corpus; the rule is what keeps it safe when the first one arrives.)
                conflicted.add(cb_ids[0])
            else:
                pairs[cb_ids[0]] = espn_ids[0]
    for cb_id in conflicted:
        pairs.pop(cb_id, None)
    return pairs


def name_agrees(a, b):
    """Do these spellings denote one person, WITHOUT relying on the shared model's rung 5?

    An ACCEPTANCE FILTER, not a matcher. The shared cricket-identity model still does the matching
    (it is the owner's, ported verbatim, and must not be forked — see that file's header); this
    only decides whether to KEEP what it proposed.

    ⛔ WHY RUNG 5 IS EXCLUDED. Rung 5 accepts when a surname is UNIQUE AMONG THE CANDIDATES, and
    uniqueness among candidates is not uniqueness in truth: a spelling variant removes the real man
    from the count and leaves a namesake as sole holder. Measured, the ONLY error in 2126 players —
    LPL 29 Jul, cricbuzz `Milan Priyanath Rathnayake`, whose true counterpart ESPN spells `Milan
    Rathnayak-A-`; rung 5 saw exactly one `Rathnayak-E-` and returned **Pavan** Rathnayake. Same
    family as the Milan/KTH Ratnayake merge in CLAUDE.md. Rungs 1-4 cost 2 pairs of 73 and take the
    error rate to zero.
    """
    n, k = norm_name(a or ""), norm_name(b or "")
    if not n or not k:
        return False
    if n == k:
        return True                                                    # 1 exact
    n_sur, k_sur = _surname_of(n), _surname_of(k)
    n_ini, k_ini = _initial_of(n), _initial_of(k)
    if n_sur == k_sur and n_ini == k_ini:
        return True                                                    # 2 surname + initial
    if (n_ini == k_ini and (k_sur.startswith(n_sur) or n_sur.startswith(k_sur))
            and min(len(n_sur), len(k_sur)) >= 4):
        return True                                                    # 3 surname prefix
    if k.startswith(n) or n.startswith(k):
        return True                                                    # 4 full-name prefix
    return False                                                       # 5 territory -> refuse


def layer_c_name(cb_card, espn_card, done_cb, done_ci):
    """→ {cricbuzz_id: cricinfo_id}. LAST resort: name, on the residual only.

    Performance evidence ALWAYS wins — this never sees a player Layers A/A2/B already placed. That
    ordering is load-bearing: it is what makes the Rathnayake case unreachable here (Layer A
    resolves Milan correctly, so he is never in this residual).

    The pool is the two feeds' OWN participants in THIS match — ~22 people who provably played.
    That is what makes names usable at all; the same matcher against a global 18k-name registry is
    the Dale-into-Glenn machine.

    Three refusals on top of the model's own null-on-ambiguity:
      - name_agrees()  — nothing rung 5 alone could have produced (see there).
      - SYMMETRY       — the match must survive a reverse lookup. The prefix rungs are directional,
                         so A->B does not imply B->A, and a one-way match is one only one feed
                         would recognise.
      - ONE-TO-ONE     — an ESPN id claimed by two cricbuzz players refuses BOTH. Never last-wins.
    """
    cb_left = [x for x in fingerprints(cb_card) if x not in done_cb]
    espn_left = [e for e in fingerprints(espn_card) if e not in done_ci]
    if not cb_left or not espn_left:
        return {}
    espn_names = [espn_card["names"].get(e, "") for e in espn_left]
    cb_names = [cb_card["names"].get(x, "") for x in cb_left]
    by_espn_name = {}
    for eid, nm in zip(espn_left, espn_names):
        by_espn_name.setdefault(nm, eid)
    proposed = {}
    for cb_id in sorted(cb_left, key=_id_key):
        cb_name = cb_card["names"].get(cb_id, "")
        hit = fuzzy_match_name(cb_name, espn_names)
        if not hit or not name_agrees(cb_name, hit):
            continue
        back = fuzzy_match_name(hit, cb_names)
        if back is None or norm_name(back) != norm_name(cb_name):
            continue
        eid = by_espn_name.get(hit)
        if eid is not None:
            proposed[cb_id] = eid
    claims = defaultdict(list)
    for cb_id, eid in proposed.items():
        claims[str(eid)].append(cb_id)
    return {cb: ci for cb, ci in proposed.items() if len(claims[str(ci)]) == 1}


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
    # Layer A's claims, read BOTH ways, so the two in-match contradiction guards below can ask
    # "is this cricbuzz id already spoken for?" and "is this cricinfo id already spoken for?".
    a_inverse = {}
    for _cb, _ci in a_pairs.items():
        a_inverse.setdefault(str(_ci), _cb)
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
        # ⛔ THE TWO FEEDS NAMING DIFFERENT FIELDERS IS A **VALUE** DISAGREEMENT, NOT AN IDENTITY
        # ONE — and until 16 Aug 2026 this join silently converted one into the other. Measured
        # over all 92 pinned matches with cached payloads: 587 fielder pairs, 534 (90.9%) merely
        # restate what Layer A already derived in the same match, 49 are genuinely NEW (the payoff
        # — a substitute or a pure fielder Layer A can never reach), and **4 CONTRADICT Layer A
        # inside that same match**. All four are one shape: Cricbuzz's card credits the catch to
        # player X, ESPN's credits it to a different player Y, and the join then asserts
        # "cricbuzz X IS cricinfo Y".
        #     cb144747/ev1521239  CB "CJ Jordan c Will Jacks"        ESPN "c Vince"
        #     cb144981/ev1521261  CB "TK-Cadmore c Lhuan-dre Pretorius"  ESPN "c Livingstone"
        #     cb145302/ev1521224  CB "A Capsey c Grace Harris"       ESPN "c Higham"
        #     cb157127/ev1537348  CB "V Shankar c Towhid Hridoy"     ESPN "c Mathew"
        # The cost was not a wrong pair — compile_bridge caught the contradiction — it was worse:
        # the single bad claim REVOKED the player outright, throwing away 8, 8, 8 and 2 matches of
        # good fingerprint evidence respectively, and putting Will Jacks, Lhuan-dre Pretorius and
        # Grace Harris on "Needs Cricinfo ID" as unanswerable identity questions when their
        # cricinfo id was never in doubt. One disputed catch cost three players their bridge.
        # So: when Layer A has ALREADY paired this cricbuzz fielder in THIS match, from figures
        # that are unique on BOTH sides and independent of the disputed field, it wins and the
        # dismissal claim is discarded as identity evidence. The disagreement is not lost — it is
        # already reported through the right channel, as a `catches` diff on the Recon tab
        # (cb_match_perf cross-checks catches/stumpings/runouts per player), which is exactly
        # CLAUDE.md rule E: identity → Needs Cricinfo ID, values → Recon Review, never mixed.
        prior = a_pairs.get(d["fielder"])
        if prior is not None and str(prior) != str(espn_d["fielder"]):
            unjoined.append({"reason": "fielder attribution disputed (VALUE, -> Recon): layer A "
                                       "paired this cricbuzz fielder to ci:%s in this same match"
                                       % prior,
                             "cb_bat": d["bat"], "code": d["code"], "desc": d["desc"],
                             "espn_desc": espn_d["desc"], "cb_fielder": d["fielder"],
                             "layer_a": str(prior), "layer_b": str(espn_d["fielder"])})
            continue
        # THE MIRROR of the guard above, and the reason it exists is that a guard on one side but
        # not its mirror is this repo's most-repeated bug. Same disputed catch, read from the other
        # end: the cricinfo fielder ESPN names is already Layer A's for a DIFFERENT cricbuzz id, so
        # accepting would put two cricbuzz ids on one human. Measured occurrences in the 92-match
        # corpus: **0 of 49** new pairs — it costs nothing today and closes the door the four cases
        # above came through, in the direction where the contradiction would otherwise only surface
        # at store level (compile_bridge's ci→cb check) and revoke BOTH innocent players.
        owner = a_inverse.get(str(espn_d["fielder"]))
        if owner is not None and str(owner) != str(d["fielder"]):
            unjoined.append({"reason": "fielder attribution disputed (VALUE, -> Recon): layer A "
                                       "already gave ci:%s to cricbuzz %s in this same match"
                                       % (espn_d["fielder"], owner),
                             "cb_bat": d["bat"], "code": d["code"], "desc": d["desc"],
                             "espn_desc": espn_d["desc"], "cb_fielder": d["fielder"],
                             "layer_a": str(owner), "layer_b": str(espn_d["fielder"])})
            continue
        pairs[d["fielder"]] = espn_d["fielder"]
    return pairs, unjoined


def fielder_disputes(unjoined):
    """The dismissals both guards above refused — the two feeds naming different fielders.

    Pulled out as a NAMED diagnostic because `layer_conflicts` used to be the only place this
    class ever appeared, and now that layer_b refuses them, `layer_conflicts` is empty by
    construction. An observation that stops being emitted anywhere is the "written but never read"
    bug run backwards; this keeps it readable, on the diagnostics side of the wall where a value
    disagreement belongs.
    """
    return [u for u in unjoined if u.get("reason", "").startswith("fielder attribution disputed")]


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
    split_pairs = layer_a2_split(cb_card, espn_card, set(a_pairs), set(a_pairs.values()))

    confirmations = []
    for cb_id, ci_id in sorted(a_pairs.items(), key=lambda kv: _id_key(kv[0])):
        confirmations.append({"cricbuzz_id": cb_id, "cricinfo_id": ci_id,
                              "match": key, "method": METHOD_FINGERPRINT, "date": date})
    for cb_id, ci_id in sorted(split_pairs.items(), key=lambda kv: _id_key(kv[0])):
        confirmations.append({"cricbuzz_id": cb_id, "cricinfo_id": ci_id,
                              "match": key, "method": METHOD_SPLIT, "date": date})

    # ⛔ THE HOLE THE NAME GATE CLOSES (owner, 25 Aug 2026). layer_b keys on the BATSMAN and reads
    # off who caught him, so if the two feeds credit the catch to DIFFERENT men it welds those two
    # men into one identity. layer_b already refuses that when Layer A independently placed the
    # fielder — but its whole reason to exist is the PURE FIELDER Layer A can never reach, and
    # there nothing contradicts it. 51 such pairs stand in the pinned corpus with no cross-check of
    # any kind. The four real disputes are in CLAUDE.md: `c Will Jacks`/`c Vince`, `c Lhuan-dre
    # Pretorius`/`c Livingstone`, `c Grace Harris`/`c Higham`, `c Towhid Hridoy`/`c Mathew`.
    name_rejected_dismissals, name_gate_abstained = [], []
    for cb_id, ci_id in sorted(b_pairs.items(), key=lambda kv: _id_key(kv[0])):
        if a_pairs.get(cb_id) == ci_id or split_pairs.get(cb_id) == ci_id:
            continue      # already stated by performance in this same match — not new evidence
        if cb_id not in a_pairs and cb_id not in split_pairs:
            cb_nm = cb_card["names"].get(cb_id, "")
            espn_nm = (espn_card["names"].get(str(ci_id), "")
                       or espn_card["names"].get(ci_id, ""))
            # ⛔ ABSENCE IS NOT A CONTRADICTION — the rule this module already applies to pins and
            # to Cricbuzz's None fields. Cricbuzz has NO name for a pure fielder unless he batted
            # or bowled; his name lives only inside the free-text `out_desc` ("c Sadeera
            # Samarawickrama b ..."), which is not something to parse for an identity decision.
            # So this gate REFUTES and never confirms: both names present and disagreeing ->
            # refuse; either missing -> it has nothing to say and the dismissal evidence stands
            # exactly as before the gate existed. Abstentions are counted, so "not checked" can
            # never be read as "checked and passed".
            if not (cb_nm and espn_nm):
                name_gate_abstained.append({"cricbuzz_id": cb_id, "cricinfo_id": ci_id,
                                            "cb_name": cb_nm, "espn_name": espn_nm})
            elif not name_agrees(cb_nm, espn_nm):
                name_rejected_dismissals.append(
                    {"cricbuzz_id": cb_id, "cricinfo_id": ci_id, "cb_name": cb_nm,
                     "espn_name": espn_nm,
                     "why": "dismissal join says these are one man but the feeds' names disagree "
                            "— they credit the catch to different people (VALUE, -> Recon), which "
                            "is not evidence of identity"})
                continue
        confirmations.append({"cricbuzz_id": cb_id, "cricinfo_id": ci_id,
                              "match": key, "method": METHOD_DISMISSAL, "date": date})

    placed_cb = set(a_pairs) | set(split_pairs) | {
        c["cricbuzz_id"] for c in confirmations if c["method"] == METHOD_DISMISSAL}
    placed_ci = set(a_pairs.values()) | set(split_pairs.values()) | {
        c["cricinfo_id"] for c in confirmations if c["method"] == METHOD_DISMISSAL}
    name_pairs = layer_c_name(cb_card, espn_card, placed_cb, placed_ci)
    for cb_id, ci_id in sorted(name_pairs.items(), key=lambda kv: _id_key(kv[0])):
        confirmations.append({"cricbuzz_id": cb_id, "cricinfo_id": ci_id,
                              "match": key, "method": METHOD_NAME, "date": date})
    # Everyone Cricbuzz saw play whom NEITHER layer could pair. This is the residual a human has
    # to look at, and it goes to the "Needs Cricinfo ID" tab — it is an identity question, never
    # a Recon one. Name is carried for the human to read, not for the code to match on.
    bridged = {c["cricbuzz_id"] for c in confirmations}
    unbridged = [{"cricbuzz_id": pid, "name": cb_card["names"].get(pid, ""),
                  "why": "fingerprint not unique on both sides (combined or per-discipline); no "
                         "dismissal to join on; name ambiguous or absent on the other card"}
                 for pid in sorted(fingerprints(cb_card), key=_id_key) if pid not in bridged]

    # Layer B disagreeing with Layer A about the SAME cricbuzz id inside ONE match used to be
    # emitted as two rival confirmations, on the reasoning that "suppressing one would hide the
    # disagreement". It hid something worse: the disagreement is about WHO TOOK THE CATCH, and
    # letting it into the identity log revoked three players whose id was never in question (see
    # layer_b). `layer_b` now refuses those joins outright, so this list is EMPTY BY CONSTRUCTION
    # and stays as a tripwire — non-empty here means a path was added that can still smuggle a
    # value disagreement into the fact log. The disagreements themselves are in `fielder_disputes`.
    conflicts = [{"cricbuzz_id": k, "layer_a": a_pairs[k], "layer_b": b_pairs[k]}
                 for k in sorted(b_pairs) if k in a_pairs and a_pairs[k] != b_pairs[k]]

    diag = {"match": key, "date": date, "gate": detail,
            "layer_a": len(a_pairs), "layer_b": len(b_pairs),
            "layer_b_new": sum(1 for k in b_pairs if k not in a_pairs),
            "layer_conflicts": conflicts,
            "fielder_disputes": fielder_disputes(unjoined),
            # Per-layer counts, so a run log shows WHICH evidence carried the match and a drop in
            # one layer cannot hide behind a rise in another.
            "layer_a2_split": len(split_pairs),
            "layer_c_name": len(name_pairs),
            "dismissals_name_rejected": name_rejected_dismissals,
            "dismissals_name_unverifiable": name_gate_abstained,
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
        row = {"cricbuzz_id": str(c["cricbuzz_id"]), "cricinfo_id": str(c["cricinfo_id"]),
               "match": c["match"], "method": c["method"], "date": c.get("date", "")}
        # This function RE-PROJECTS every confirmation, so any field it does not name is deleted
        # here — the same "written and never read" shape as the reader dropping it, one layer up.
        # `source` (who answered, and why) survives only because it is named.
        if c.get("source"):
            row["source"] = c["source"]
        out.append(row)
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
                         # `source` is written ONLY when there is one (a manual answer carries
                         # who/why). Emitting an empty string on all 2000 derived confirmations
                         # would rewrite the whole file for nothing and break the byte-identical
                         # round-trip the determinism test pins.
                         "confirmations": [dict({"match": c["match"], "method": c["method"],
                                                 "date": c["date"]},
                                                **({"source": c["source"]} if c.get("source")
                                                   else {}))
                                           for c in confs]}

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
    # `source` travels back out too. A field the writer emits and the reader drops is erased on the
    # next recompile — the manual answer would keep its id and silently lose WHO said so.
    # It is omitted when EMPTY, exactly as merge_confirmations and compile_bridge omit it: three
    # projections of one row that disagree about which keys exist make equality comparisons lie,
    # and `adopt`/`record` decide "did anything change?" by comparing two of them.
    def _row(cb_id, ci_id, c):
        r = {"cricbuzz_id": cb_id, "cricinfo_id": ci_id, "match": c["match"],
             "method": c["method"], "date": c.get("date", "")}
        if c.get("source"):
            r["source"] = c["source"]
        return r

    for cb_id, rec in store.get("bridge", {}).items():
        for c in rec["confirmations"]:
            out.append(_row(cb_id, rec["cricinfo_id"], c))
    for cb_id, rec in store.get("revoked", {}).items():
        for c in rec["confirmations"]:
            out.append(_row(cb_id, c["cricinfo_id"], c))
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


def cricbuzz_profile_url(cricbuzz_id):
    """The human's landing page for a cricbuzz id. `/x` is the slug placeholder Cricbuzz accepts
    in place of the real name — the same convention `cricbuzz.scorecard_html` already uses.
    VERIFIED 16 Aug 2026: /profiles/<id>/x → HTTP 200 with the right player's <title> on
    10693 Glenn Phillips, 11101 Grace Harris, 12258 Will Jacks, 50545 Lhuan-dre Pretorius,
    12163 Virandeep Singh, 6667 Imran Tahir, 10384 Shai Hope. Bare /profiles/<id> (no slug) is a
    404 that still returns a 166 KB body, so a naive fetcher would read it as a real page."""
    return "%s/profiles/%s/x" % (CB_HOST, cricbuzz_id)


def cricinfo_profile_url(cricinfo_id):
    return "https://www.espncricinfo.com/cricketers/x-%s" % cricinfo_id


def resolve(store, cricbuzz_id, purpose=PURPOSE_CROSSCHECK):
    """The ONLY sanctioned read path. Returns a Resolution whose `status` names the failure —
    never a bare None a caller can mistake for a zero.

    ⚠ `detail` IS A USER-FACING STRING. `wc_fps_to_csv.cb_match_perf` interpolates it verbatim
    into the "Needs Cricinfo ID" row a human has to answer, so it has to carry the evidence, not
    just the verdict. It used to read `no confirmation on any bridged match` / `cb_id claimed by
    2 cricinfo ids` — true, unanswerable, and with no way to even see who the player is. Every
    branch now carries the cricbuzz profile URL, and where a candidate exists, that candidate with
    its evidence, so answering is a look-and-confirm rather than a research project.
    """
    cb_id = str(cricbuzz_id)
    cb_url = cricbuzz_profile_url(cb_id)
    rev = store.get("revoked", {}).get(cb_id)
    if rev:
        # Name WHICH ids collided and how much evidence each has: the shape of a contradiction is
        # almost always lopsided (8 matches vs 1), and the human cannot see that from the reason.
        claims = rev.get("claims") or {}
        ranked = sorted(claims.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        ev = "; ".join("ci:%s (%d match%s: %s) %s"
                       % (ci, len(ms), "" if len(ms) == 1 else "es", ", ".join(sorted(ms)),
                          cricinfo_profile_url(ci))
                       for ci, ms in ranked)
        return Resolution(None, 0, REVOKED, "%s — %s | cricbuzz %s" % (rev["reason"], ev, cb_url))
    rec = store.get("bridge", {}).get(cb_id)
    if not rec:
        # Say how big the corpus he is absent from actually IS. "No confirmation" reads as "we
        # looked everywhere"; for most of these the truth is that the derive corpus simply does
        # not yet include the match he played in (11 of the 94 pinned matches were underived on
        # 16 Aug 2026, and 6 of the tab's rows were one single match among them).
        n = len({m for r in store.get("bridge", {}).values() for m in r["matches"]})
        return Resolution(None, 0, UNKNOWN,
                          "no confirmation in the %d derived match(es) — either he has not played "
                          "one of them, or his match is pinned but not yet derived (run "
                          "`registry/cricbuzz_bridge.py --derive --from-map`) | cricbuzz %s"
                          % (n, cb_url))
    # ⛔ THE `create` BAR IS COUNTED IN PERFORMANCE MATCHES, NOT IN RAW TIER.
    # `tier` counts DISTINCT MATCHES by any method — right for a cross-check, wrong for creating
    # points. A name is not an observation: it is a label both feeds copied from the same source,
    # so N matches of it is one fact repeated N times. Counting it toward `create` would let a
    # spelling do the job two independent sightings are supposed to do.
    # REAL CASE from the reference pair: cb:12071 -> ci:974109 carries `fingerprint` on
    # cb157061/espn1537342 and `name` on cb157138/espn1537349. Raw tier is 2, so a tier-only gate
    # would clear him to CREATE a Cricbuzz-only field off ONE actual observation. Counting
    # performance matches gives 1 and refuses him — his cross-check rights are untouched.
    # Scoped so a MANUAL answer keeps its own refusal: that is the owner's judgement, already and
    # correctly stopped by tier, and calling it "bridged by NAME alone" would simply be untrue.
    if purpose == PURPOSE_CREATE:
        perf_matches = sorted({c["match"] for c in (rec.get("confirmations") or [])
                               if c.get("method") in PERFORMANCE_METHODS})
        if len(perf_matches) < MIN_MATCHES[PURPOSE_CREATE]:
            if not perf_matches and set(rec.get("methods") or []) == {METHOD_NAME}:
                return Resolution(None, rec["tier"], NAME_ONLY,
                                  "candidate ci:%s is bridged by NAME alone across %d match(es) "
                                  "(%s) — enough to cross-check a number ESPN already has, never "
                                  "to create one. A performance or dismissal confirmation is "
                                  "required for that. %s | cricbuzz %s"
                                  % (rec["cricinfo_id"], rec["tier"], ", ".join(rec["matches"]),
                                     cricinfo_profile_url(rec["cricinfo_id"]), cb_url))
            return Resolution(None, rec["tier"], INSUFFICIENT_TIER,
                              "candidate ci:%s has %d performance-confirmed match(es) (%s) of the "
                              "%d %s needs; tier %d counts every method, including ones that "
                              "cannot license creating points — %s | cricbuzz %s"
                              % (rec["cricinfo_id"], len(perf_matches),
                                 ", ".join(perf_matches) or "none", MIN_MATCHES[PURPOSE_CREATE],
                                 purpose, rec["tier"],
                                 cricinfo_profile_url(rec["cricinfo_id"]), cb_url))
    need = MIN_MATCHES[purpose]
    if rec["tier"] < need:
        return Resolution(None, rec["tier"], INSUFFICIENT_TIER,
                          "candidate ci:%s from %d match(es) (%s) but %s needs %d — %s | "
                          "cricbuzz %s"
                          % (rec["cricinfo_id"], rec["tier"], ", ".join(rec["matches"]), purpose,
                             need, cricinfo_profile_url(rec["cricinfo_id"]), cb_url))
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


def adopt(store, cricbuzz_id, cricinfo_id, source=""):
    """Record the OWNER's answer to a "Needs Cricinfo ID" row as a confirmation. (store, changed).

    ⛔ THE ANSWER HAD NOWHERE TO LAND. `read_needs_cricinfo` consumes a filled-in id into
    `manual_ci_bridges.json` as `ci:<id> -> [normalised NAME]`, and its `extra` alias is built only
    for a `slug:`/`uncapped:` pid — so for a `cb:<id>` row **the cricbuzz id is discarded**. The
    question asked was "which cricinfo id is cricbuzz player N?"; the answer was filed as a name
    alias, which is the one thing that may never decide identity, and the bridge went on saying
    `unknown` for N. Measured on the live tab, 16 Aug 2026: `cb:12163` and `cb:10693` were both
    answered (633660, 823509) and both still resolved to UNKNOWN, so the same row regenerates
    every time either man fields a catch. Written, and never read.

    Deliberately NOT a trump card. If the store already DERIVED a different cricinfo id for this
    cricbuzz id, this claim contradicts it and `compile_bridge` revokes BOTH — loudly, for a human
    — rather than letting one keystroke overwrite N matches of both-sides-unique fingerprints.
    A hand-typed id also cannot reach tier 2 by itself (see MANUAL_MATCH), so it enables the
    cross-check and still gates CREATING points from a Cricbuzz-only field.
    """
    cb_id, ci_id = str(cricbuzz_id).strip(), re.sub(r"\D", "", str(cricinfo_id or ""))
    if not cb_id.isdigit() or int(cb_id) <= 0:
        raise BridgeError("refusing to adopt a non-positive cricbuzz id %r" % cricbuzz_id)
    if not ci_id or int(ci_id) <= 0:
        # An unparseable id is an ABSENCE. Writing "" would put a blank in the identity ledger
        # wearing the clothes of an answer, and every later read would treat it as decided.
        raise BridgeError("refusing to adopt cb%s -> %r: not a cricinfo id" % (cb_id, cricinfo_id))
    conf = {"cricbuzz_id": cb_id, "cricinfo_id": ci_id, "match": MANUAL_MATCH,
            "method": METHOD_MANUAL, "date": "", "source": source or ""}
    log = confirmations_log(store)
    merged = merge_confirmations(log, [conf])
    if merged == log:
        return store, False
    return build_store(merged), True


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
def _load_pair(cb_id, espn_id, cb_cache, espn_cache, league, espn_series=None, bot_cache=None):
    html = fetch_cb_scorecard(cb_id, cb_cache)
    card, header = parse_cb_scorecard_html(html)
    cb = normalize_cb_card(card)
    pbp = None
    if bot_cache and espn_series:
        pbp = espn_pbp_from_bot_cache(espn_id, espn_series, bot_cache)
    if pbp is None:
        # ⚠ THE "LEAGUE" PATH SEGMENT IS THE ESPN **SERIES ID**, not a slug. `wc_fps_to_csv.espn_get`
        # builds `{ESPN_BASE}/{ESPN_SERIES}/{path}` and that is the only shape ESPN answers for
        # these events. The `--league lanka-premier-league` default is a legacy string that works
        # for LPL alone: falling back to it for a Hundred event returned **HTTP 500**, which the
        # caller would otherwise have logged as "ESPN has no play-by-play for this match".
        # Verified 16 Aug 2026: .../cricket/1521193/playbyplay?event=1521218 → 200, 188 items.
        pbp = fetch_espn_pbp(espn_id, espn_series or league, espn_cache)
    return cb, normalize_espn_card(pbp), match_date(header)


# `tours.json` is the one place that says which ESPN series a Cricbuzz series is the witness for.
# Read, never hard-coded: a fifth tour opting in must not need an edit here as well as there.
def pairs_from_match_map(map_path=MATCH_MAP_PATH, tours_path=TOURS_PATH, series=None):
    """→ [(cb_match_id, espn_event_id, cricbuzz_series, espn_series, date)] from the PIN LEDGER.

    ⛔ THIS IS THE JOIN THAT DID NOT EXIST. `registry/cricbuzz_match_map.json` knows every
    cb-match ↔ ESPN-event pairing that has ever been confirmed (94 pins on 16 Aug 2026); the
    bridge's --derive took hand-typed `--pair CBID:ESPNID` arguments. Nothing connected them, so
    the derive corpus drifted behind the ledger and stayed there: **83 of 94 pinned matches were
    derived, 11 were not**, and six of the twelve `cb:` rows sitting on "Needs Cricinfo ID" were
    one single underived match (cb154370, CPL Guyana v Jamaica, 14 Aug) whose whole XI Layer A
    pairs 22/22. Written-but-never-read, in the shape where the unread thing is a whole ledger.
    """
    with open(map_path, encoding="utf-8") as fh:
        store = json.load(fh)
    with open(tours_path, encoding="utf-8") as fh:
        tours = json.load(fh)
    cb2espn = {str(t["cricbuzz_series"]): str(t.get("espn_series") or "")
               for t in tours if t.get("cricbuzz_series")}
    out, unknown = [], set()
    for key, pin in sorted(store.get("pins", {}).items()):
        cb_series = str(pin.get("series_id") or "")
        if series and cb_series != str(series):
            continue
        espn_series = cb2espn.get(cb_series)
        if not espn_series:
            unknown.add(cb_series)
            continue
        for ev in pin.get("espn_events") or []:
            out.append((str(pin["cricbuzz_match_id"]), str(ev), cb_series, espn_series,
                        pin.get("date", "")))
    for s in sorted(unknown):
        print("  cricbuzz series %s is pinned but no tour in tours.json claims it — its matches "
              "are NOT in the derive corpus" % s, file=sys.stderr)
    # A pin can carry more than one ESPN event only if the map itself is contradicted, and it
    # refuses both sides in that case — so this is deduped for safety, not because it is expected.
    seen, uniq = set(), []
    for row in out:
        if row[:2] in seen:
            continue
        seen.add(row[:2])
        uniq.append(row)
    return uniq


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--derive", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--needs", action="store_true",
                    help="print the residual as Needs-Cricinfo-ID rows (identity tab, never Recon)")
    ap.add_argument("--rekey", action="store_true")
    ap.add_argument("--adopt", action="append", default=[], metavar="CBID:CRICINFOID",
                    help="record the OWNER's answer to a `cb:` Needs-Cricinfo-ID row. Tier 1 "
                         "(cross-check) only; it can never alone authorise CREATE.")
    ap.add_argument("--why", default="",
                    help="provenance for --adopt (who said so, and from what)")
    ap.add_argument("--pair", action="append", default=[], metavar="CBID:ESPNID")
    ap.add_argument("--from-map", action="store_true",
                    help="take the pairs from registry/cricbuzz_match_map.json (the pin ledger) "
                         "instead of hand-typed --pair args — the corpus can then never lag it")
    ap.add_argument("--series", default=None,
                    help="with --from-map, restrict to one cricbuzz series id")
    ap.add_argument("--cb-cache", default=None)
    ap.add_argument("--espn-cache", default=None)
    ap.add_argument("--bot-cache", default=BOT_CACHE,
                    help="the bot's WC_CACHE_DIR; ESPN play-by-play is read from it when present "
                         "so a re-derive is offline. '' disables.")
    ap.add_argument("--league", default="lanka-premier-league")
    ap.add_argument("--store", default=BRIDGE_PATH)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    store = load_store(args.store)
    if args.derive:
        specs = [(cb_id, espn_id, None, None, "") for cb_id, _, espn_id in
                 (s.partition(":") for s in args.pair)]
        if args.from_map:
            already = {m for r in store.get("bridge", {}).values() for m in r["matches"]}
            already |= {c["match"] for r in store.get("revoked", {}).values()
                        for c in r["confirmations"]}
            pinned = pairs_from_match_map(series=args.series)
            fresh = [p for p in pinned if match_key(p[0], p[1]) not in already]
            print("from-map: %d pinned pair(s), %d already derived, %d to derive"
                  % (len(pinned), len(pinned) - len(fresh), len(fresh)), file=sys.stderr)
            # Re-derive EVERY pin, not just the new ones: `bridge`/`revoked` are a pure function
            # of the fact log, so a logic change (the layer_b fielder-dispute guard, 16 Aug) only
            # reaches already-stored confirmations if the matches that produced them are derived
            # again. Deriving only the gap would have left the three revoked players revoked.
            specs = list(pinned)
        # CB scorecards live under the SAME name in both caches (`cb_sc_<mid>.html`), so pointing
        # --cb-cache at the bot's directory is always safe and is the default when --bot-cache is.
        cb_cache = args.cb_cache or (args.bot_cache or None)
        confs, diags = [], []
        for cb_id, espn_id, _cb_series, espn_series, _date in specs:
            spec = "%s:%s" % (cb_id, espn_id)
            try:
                cb, espn, date = _load_pair(cb_id, espn_id, cb_cache, args.espn_cache,
                                            args.league, espn_series, args.bot_cache or None)
                c, d = derive_match(cb, espn, cb_id, espn_id, date)
            except BridgeError as e:
                print("REFUSED %s: %s" % (spec, e), file=sys.stderr)
                continue
            except (OSError, urllib.error.URLError) as e:
                # A payload we cannot READ is not a match with no players. Name it and move on;
                # the pin stays, so the next run picks it up (an absence is not a contradiction).
                print("UNREADABLE %s: %s" % (spec, e), file=sys.stderr)
                continue
            confs.extend(c)
            diags.append(d)
            # Every layer is named, including the ones that contributed 0. A layer that only
            # appears when it fires is a layer nobody notices going silent.
            print("%s  A=%d A2=%d B=%d (new %d) C=%d  cb=%d espn=%d  unbridged=%d"
                  % (d["match"], d["layer_a"], d["layer_a2_split"], d["layer_b"],
                     d["layer_b_new"], d["layer_c_name"], d["cb_players"], d["espn_players"],
                     len(d["unbridged_cb"])), file=sys.stderr)
            for x in d["dismissals_name_rejected"]:
                print("   dismissal REFUSED by the name gate (the feeds credit the catch to "
                      "different men — VALUE, -> Recon): cb%s %r vs espn ci:%s %r"
                      % (x["cricbuzz_id"], x["cb_name"], x["cricinfo_id"], x["espn_name"]),
                      file=sys.stderr)
            if d["dismissals_name_unverifiable"]:
                # Counted, never silent: "could not check" must not read as "checked and passed".
                print("   %d dismissal pair(s) the name gate could not check (a name absent on one "
                      "card); accepted on the dismissal evidence alone"
                      % len(d["dismissals_name_unverifiable"]), file=sys.stderr)
            for x in d["layer_conflicts"]:
                print("   ⚠ LAYER CONFLICT in one match: cb%s -> A says ci:%s, B says ci:%s"
                      % (x["cricbuzz_id"], x["layer_a"], x["layer_b"]), file=sys.stderr)
            for x in d["fielder_disputes"]:
                # A VALUE disagreement. Printed so it is visible, NOT stored as identity: the
                # Recon tab already carries it as a per-player `catches` diff.
                print("   fielder disputed (value, already a Recon catches diff): cb%s — "
                      "cricbuzz %r vs espn %r; layer A keeps ci:%s"
                      % (x.get("cb_fielder"), x.get("desc"), x.get("espn_desc"),
                         x.get("layer_a")), file=sys.stderr)
            for x in d["totals_delta"]:
                print("   advisory (Recon, not identity): %s cb=%s espn=%s"
                      % (x["field"], x["cb"], x["espn"]), file=sys.stderr)
        base = confirmations_log(store)
        if args.from_map:
            # ⛔ SUPERSEDE ONLY WHAT WAS ACTUALLY RE-DERIVED. `bridge`/`revoked` are a pure
            # function of the fact log, so a logic change (the layer_b fielder-dispute guard)
            # reaches stored confirmations only if their match is derived again and the OLD
            # confirmations for that match are dropped — otherwise the rival claim survives in
            # the log and the player stays revoked.
            # But the set to drop is the matches this pass DERIVED, never the matches it
            # ATTEMPTED. Keying it on `specs` cost me cb145245/espn1521218 on the first run:
            # its ESPN payload had aged out of the cache, the derive was skipped — and its 22
            # confirmations were deleted anyway, dropping 22 players a tier. An unreadable
            # payload is an ABSENCE; absence is not evidence that nobody played.
            derived = {d["match"] for d in diags}
            base = [c for c in base if c["match"] not in derived]
            skipped = [s for s in specs if match_key(s[0], s[1]) not in derived]
            if skipped:
                print("  %d pinned pair(s) NOT re-derived (payload unreadable/absent); their "
                      "existing confirmations are KEPT: %s"
                      % (len(skipped), ", ".join(match_key(s[0], s[1]) for s in skipped)),
                      file=sys.stderr)
        store = build_store(merge_confirmations(base, confs))
        if not args.dry_run:
            save_store(store, args.store)
    for spec in args.adopt:
        cb_id, _, ci_id = spec.partition(":")
        store, changed = adopt(store, cb_id, ci_id, source=args.why)
        res = resolve(store, cb_id)
        print("adopt cb%s -> ci:%s  %s  (now %s%s)"
              % (cb_id, ci_id, "recorded" if changed else "already recorded", res.status,
                 "" if res.status == OK else " — " + res.detail), file=sys.stderr)
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
