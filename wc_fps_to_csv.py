#!/usr/bin/env python3
"""
Women's T20 WC 2026 -> Dream11 fantasy-points sheet (CSV).

Computes Dream11 T20 points (mirrors src/lib/fantasy-points/{rules,calculator}.ts)
for every COMPLETED match and writes one flat CSV (Match column) listing ALL squad
players of both teams + raw stats + points breakdown. DNP players get blank stats.

Source priority per match (recorded in the Status column):
  1. cricsheet (official, exact everything) — overrides all when posted (lags days)
  2. else cricapi scorecard (PRIMARY base) + ESPN/cricinfo ball-by-ball for the
     dot-balls and the +4 in-XI cricapi can't supply; runs/wickets cross-checked
     between the two (disagreements flagged in Status)
  3. else cricapi alone (no dots/XI) if ESPN is unavailable
Super-over deliveries are excluded. Match<->feed joins tolerate a ±1 day offset.
Milestone bonuses are highest-only (25/50/75/100 replace lower).

Usage:
  CRICKET_API_KEY=<key> python3 data/wc_fps_to_csv.py [out.csv]
"""
import os, sys, json, re, csv, time, glob, html, unicodedata, urllib.request
from difflib import SequenceMatcher
from datetime import date, timedelta, datetime, timezone

# FREQUENT mode = the every-5-min "live lineup" tick (vs the 2-hourly full run).
# It only does work inside a match's toss window and caches series_info so the
# extra ticks don't blow cricapi's 100/day cap. Set via env in live-lineup.yml.
FREQUENT = os.environ.get("FREQUENT") == "1"
# ON_DEMAND mode = a human tapped "Refresh live points" in the draft app
# (workflow_dispatch from on-demand-refresh.yml). Runs LIGHT like the frequent tick
# (cached series_info, no review write-back) but SKIPS the toss-window gate, because
# the human is explicitly asking mid-innings. ~1-2 cricapi hits/run.
ON_DEMAND = os.environ.get("ON_DEMAND") == "1"
FREQUENT = FREQUENT or ON_DEMAND   # on-demand inherits the light-run behaviour
# The every-5-min live tick must NEVER spend a cricapi hit — its lineup + live score are on
# ESPN (keyless), and cricapi's daily budget is precious. In tick mode, api() serves cached
# cricapi data (any age — a match list / final scorecard doesn't change) and otherwise reports
# a benign miss so the caller falls back to ESPN; it does NOT call cricapi. Only the full run
# (every 4h) and the human on-demand button spend cricapi. espn_get() is untouched, so the tick
# still publishes the announced XI + live points from ESPN. (Human on-demand is NOT cache-only.)
TICK_CACHE_ONLY = FREQUENT and not ON_DEMAND
_CRICAPI_HITS = 0   # cricapi live hits spent THIS run — Phase-0 instrumentation (logged + STATUS tab)

def date_variants(d):
    """A date plus ±1 day (ISO strings) — to absorb timezone differences between feeds."""
    try:
        b = date.fromisoformat(d)
        return [d, (b - timedelta(days=1)).isoformat(), (b + timedelta(days=1)).isoformat()]
    except ValueError:
        return [d]

API = "https://api.cricapi.com/v1"
# One or more cricapi keys (free tier = 100 hits/day each). Provide extras to fail over
# when one is exhausted/blocked: CRICKET_API_KEY can be comma-separated, and/or set
# CRICKET_API_KEY2. The bot rotates to the next key on a quota/"Blocked" response.
API_KEYS = [k.strip() for k in (
    os.environ.get("CRICKET_API_KEY", "").split(",") + [os.environ.get("CRICKET_API_KEY2", "")]
) if k.strip()]
KEY = API_KEYS[0] if API_KEYS else ""   # primary (back-compat for the startup check)
_key_idx = 0   # index of the key currently in use; advances on quota/blocked failures
# Per-key quota snapshot captured from cricapi's `info` block on each live hit
# (free tier = 100 hits/day per key). {key_index: {"today": hitsToday, "limit": hitsLimit}}.
# Surfaced to the draft app via write_status_tab -> the "hits left today" gauge.
API_QUOTA = {}
# Series id to pull. Override with env SERIES_ID to run ANY other tour (no code change).
WC_SERIES = os.environ.get("SERIES_ID", "f3e5c7dd-332c-4893-9067-aa2bfe6d2b85").strip()  # default: ICC Women's T20 WC 2026
SQUAD_TS = os.path.join(os.path.dirname(__file__), "..", "src", "lib", "squads", "womens-t20-wc-2026.ts")
SQUADS_JSON = os.environ.get("SQUADS_JSON", os.path.join(os.path.dirname(__file__), "squads.json"))  # standalone / per-tour
CACHE = os.environ.get("WC_CACHE_DIR", "/tmp/wc_api_cache")
CRICSHEET_DIR = os.environ.get("CRICSHEET_DIR", "/tmp/t20scan")  # extracted cricsheet JSONs
OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/wc_fantasy_points.csv"
# Google Sheet write target (CI). If both set, also writes cells into the tab.
GSHEET_ID = os.environ.get("GSHEET_ID", "").strip()
GSHEET_TAB = os.environ.get("GSHEET_TAB", "WWC T20 POINTS").strip()
# ESPN = free ball-by-ball source for completed matches -> exact bowler dot-balls
# (cricsheet is exact too but lags days; ESPN is available right after a match).
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/cricket"
# ESPN's WAF 403s browser-impersonating User-Agents: a bare "Mozilla/5.0" is rejected while curl's,
# urllib's default and an honest bot UA all pass. espn_get() swallows the error and returns {}, so
# this presented as "ESPN has no data" — i.e. NO dots, NO maidens, NO announced XI and no ESPN
# scorecard fallback, the single-source fields per the recon model. Do NOT put "Mozilla" back.
ESPN_UA = "wwc-points-bot/1.0 (+https://github.com/nishantsingodia/wwc-points-bot)"
ESPN_SERIES = os.environ.get("ESPN_SERIES_ID", "1483859").strip()  # ICC Women's T20 WC 2026

# ---- Dream11 T20 rules (mirror of T20_RULES in rules.ts) ----
R = dict(
    perRun=1, b4=4, b6=6, m25=4, m50=8, m75=12, m100=16, duck=-2,
    wkt=30, lbwb=8, dot=1, maiden=12, h3=4, h4=8, h5=12,
    catch=8, c3=4, stump=12, dro=12, ro=6, xi=4,
)
# ---- Dream11 ODI rules (mirror of ODI_RULES in rules.ts) ----
# vs T20: duck -3; dot = 1 per 3 dots (dotGroup); maiden +4; hauls at 4w/5w/6w;
# SR gate 20 balls + shifted bands; econ gate 30 balls + shifted bands (see _score_odi).
R_ODI = dict(
    perRun=1, b4=4, b6=6, m25=4, m50=8, m75=12, m100=16, duck=-3,
    wkt=30, lbwb=8, dotGroup=3, dotPts=1, maiden=4, h4=4, h5=8, h6=12,
    catch=8, c3=4, stump=12, dro=12, ro=6, xi=4,
)
# ---- Dream11 The Hundred (100-ball) rules — mirror of compute_fantasy_points_hundred in
# cricket-auction-helper/data/etl_cricsheet.py. Same core scale as T20 (run+1, four+4, six+6,
# wicket+30, dot+1, duck -2, fielding, +4 XI) but The Hundred awards NO strike-rate, NO economy
# and NO maiden points, and wicket hauls are tiered from 2w: 2w+4 / 3w+8 / 4w+12 / 5w+16. ----
R_HUN = dict(
    perRun=1, b4=4, b6=6, m25=4, m50=8, m75=12, m100=16, duck=-2,
    wkt=30, lbwb=8, dot=1, h2=4, h3=8, h4=12, h5=16,
    catch=8, c3=4, stump=12, dro=12, ro=6, xi=4,
)

def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s.lower())).strip()

# ---- canonical Player Registry (GLOBAL identity; built once by build_registry.py) ----
# registry/players.json maps each player's stable pid (cricsheet_id when known) to its
# display name + EVERY feed spelling (aliases). This turns name-matching from a per-match
# fuzzy gamble into a deterministic dictionary lookup. Identity is global & permanent — a
# player resolved in one tour is resolved in all future tours with zero rework.
def load_registry():
    path = os.path.join(os.path.dirname(__file__), "registry", "players.json")
    alias2pid, pid2disp, cs2pid = {}, {}, {}
    try:
        players = json.load(open(path)).get("players", {})
    except Exception:
        return alias2pid, pid2disp, cs2pid
    for pid, e in players.items():
        pid2disp[pid] = e.get("display") or pid
        for a in e.get("aliases", []):
            alias2pid.setdefault(a, pid)
        # cricsheet person id -> pid. The registry DERIVES cricsheet_id from the verified
        # cricinfo id via the crosswalk, so this index is id-anchored, never fuzzy.
        cs = e.get("cricsheet_id")
        if cs:
            cs2pid.setdefault(str(cs), pid)
    return alias2pid, pid2disp, cs2pid

ALIAS2PID, PID2DISP, CS2PID = load_registry()

def load_crosswalk():
    """people.csv-derived cricsheet_id -> cricinfo_id. Used to make an UNRESOLVABLE official-card
    row actionable: we may not know the player, but we can name the exact cricinfo id to bridge,
    so the fix is one paste instead of a research task. Never used to invent identity."""
    path = os.path.join(os.path.dirname(__file__), "registry", "crosswalk.json")
    try:
        return json.load(open(path)).get("cs2ci", {}) or {}
    except Exception:
        return {}

CS2CI = load_crosswalk()

def cricinfo_hint(v):
    """'cricinfo 1100812 — espncricinfo.com/cricketers/x-1100812' for an unresolved cricsheet row.
    Two different Hundred Women's players are BOTH written 'E Jones' by cricsheet (cs 971cb321 =
    Eve Jones, cs 4cf60e73 = cricinfo 1100812, a different person). A name-based matcher merges
    them silently; the id tells them apart and tells you precisely who is missing."""
    cs = (v or {}).get("cs_id")
    ci = CS2CI.get(str(cs)) if cs else None
    if not ci:
        return f"cricsheet id {cs}" if cs else "no cricsheet id"
    return f"cricinfo {ci} — espncricinfo.com/cricketers/x-{ci} (cricsheet {cs})"

def load_team_aliases():
    """Central franchise-name identity — the TEAM analog of the player registry/manual_aliases.
    Maps every feed team-name VARIANT to the canonical franchise name (as used in the squads +
    auction + draft). Normalizing here fixes BOTH cricapi->squad mapping AND ESPN event resolution
    when a feed carries a stale/rebranded name (LPL 2026: cricapi feeds the 2025 names while the
    squads/ESPN use the 2026 ones). Returns {norm(variant): canonical display name}."""
    path = os.path.join(os.path.dirname(__file__), "registry", "team_aliases.json")
    canon = {}
    try:
        for canonical, variants in json.load(open(path)).get("aliases", {}).items():
            canon[norm(canonical)] = canonical
            for v in variants:
                canon[norm(v)] = canonical
    except Exception:
        pass
    return canon

TEAM_CANON = load_team_aliases()

def canon_team(name):
    """Resolve a feed team name to its canonical franchise name (no-op if unknown)."""
    return TEAM_CANON.get(norm(name), name) if name else name
UNMATCHED_LOG = set()   # feed names that needed fuzzy / had no squad match -> grow the registry
# Structured review queue (across all tours), written to the sheet's "Needs Review" tab so the
# rare manual case is fixable WITHOUT code: {tour, team, feed, kind, suggestion}.
REVIEW = []
AUTO_ALIASES = []       # high-confidence fuzzy hits -> auto-written to the Player Aliases tab
CONFIRMED = []          # (feed, correct) the user marked Yes in Needs Review -> persist + apply
PRIOR_CONFIRM = {}      # (tour, feed) -> the user's Yes/No so far (preserved across rewrites)
PRIOR_CLOSEST = {}      # (tour, feed) -> the Closest Match value last seen (preserve user edits)
PRIOR_ROLE = {}         # (tour, feed) -> the Role value last seen (preserve user edits)
ROLE_OVERRIDE = {}      # norm(feed) -> role you set in Needs Review (drives SR/Econ scoring)
ACK = set()             # norm(feed) the user resolved (Yes/No/New) -> stop re-flagging in Needs Review
CURRENT_TOUR = ""       # set by run_tour so logged items know which tour they came from
CURRENT_FMT = "T20"     # set by run_tour from tour["format"] ("T20" | "ODI" | "HUN"); drives score() + match filter

# Identity-anomaly queue (written to the sheet's "Identity Anomalies" tab). These are the
# OPPOSITE failure from Needs Review: not "this name matched nobody" but "two DIFFERENT players
# look merged into one id" (false_merge) or "one id appears on two rows in a match" (duplicate_pid).
# Surfaced for a Yes/No so identity changes are never silent. Separate tab + separate globals so
# the Needs Review / Player Aliases no-code flow is completely untouched.
ANOMALIES = []          # {tour, kind, pid, display, context, names, finding}
PRIOR_ANOMALY = {}      # (tour, pid, kind) -> the user's Yes/No so far (preserved across rewrites)
ANOMALY_ACK = set()     # (tour, pid, kind) the user answered -> stop re-flagging

# Sheet-driven NEW players: global identity (pid+aliases) + per-tour membership, loaded at startup
# and injected into the tour squads — so a player added via "New" (or auto-added on a silent-drop)
# resolves + counts + is draftable WITHOUT a build_registry rebuild. Persisted to
# registry/new_players.json and committed back by the workflow. See load_new_players/register_new_player.
NEW_PLAYERS_DATA = {"players": []}   # the loaded ledger, mutated in-memory, saved once at run end

# Recon-review queue (written to the sheet's "Recon Review" tab). Feed disagreements the human
# resolves by picking a value; a match stays LIVE until its L1 gaps are approved. Separate tab +
# globals so the Needs Review / Identity Anomalies flows are untouched. See match_key_of/etc.
RECON_REVIEW = []       # rows to publish: {match_key, tour, match, date, pid, full, param, s1, s2, tier}
PRIOR_RECON = {}        # (match_key, pid, param) -> the user's "Correct Value" so far (preserved)
PRIOR_MANUAL = {}       # (match_key, pid, param) -> the user's "Manual Value" so far (preserved)
RECON_ACK = set()       # (match_key, pid, param) approved + applied -> stop re-flagging
# Identity gaps found while SCORING (an official-card row we can't resolve, or a squad player
# still on a slug:/uncapped: pid). These go to the "Needs Cricinfo ID" tab — the same
# self-maintaining loop build_registry uses — NOT to Recon Review, whose S1/S2 dropdown asks a
# value question that makes no sense for "who is this player?".
NEEDS_CRICINFO = []
# norm(name) of every player who APPEARED in a scored card this run. A pre-debut squad member has
# no cricinfo id ANYWHERE — he is uncapped, so he is not in people.csv either and may not have a
# cricinfo page at all — so asking a human for one is asking for something that does not exist.
# The moment he plays, ESPN's athlete.id IS his cricinfo id and we get it free. See
# pending_registry_gaps.
APPEARED = set()     # {player, current_pid, tour, team, closest_guess}
RECON_OVERRIDES = {}    # match_key -> [approved override dicts] (loaded once in main, before tours)

# ── ESPN card HELD — the ball-count gate fired ───────────────────────────────
# parse_espn refuses a ball-by-ball shorter than ESPN's OWN scorecard says it should be. Refusing
# is right. Refusing SILENTLY is not: the caller cannot tell `{}` -> "refused" from `{}` -> "ESPN
# has no data", so on an ESPN-only tour the match falls through the no-data guard, `continue`s, and
# is ABSENT FROM THE SHEET ENTIRELY — retried forever, with no cutoff, visible only in a workflow
# log. A permanently-broken card therefore meant a match that never scored, never settled and
# never appeared anywhere a human looks.
# MEASURED over the cached ESPN corpus (123 bodies / 71 distinct events / 6 series / T20+ODI+HUN):
# the ball-by-ball delivery count equals the scorecard's per-bowler ball total EXACTLY — delta 0 on
# 71/71 events, zero over-counts, zero under-counts. That includes the Hundred Women's events whose
# card is one delivery short of cricsheet in BOTH feeds (internally consistent, so the gate is
# correctly silent — this gate measures FETCH completeness, not truth). So a fire is never routine
# noise: every fire is a real anomaly and deserves a named, answerable row.
ESPN_HOLDS = {}                  # event_id -> {kind, got, expected, tour, match, date, match_key, ...}
ESPN_CARD_SCORE_ANYWAY = set()   # event ids a human approved (S2) -> score the short card, flagged
# Retry budget. Anchored to the MATCH clock, deliberately NOT to a persisted retry counter: a
# counter lives in a file the workflow has to commit, and a commit that fails would silently reset
# the budget so the escalation never arrives — an absence read as a zero, the exact bug class this
# gate exists to catch. Every transient cause clears in minutes (a 502 is one run; the empty card
# re-probed clean minutes later; the live scorecard-ahead-of-commentary race clears next tick), so
# hours of persistence past the end of the match means it is not transient.
ESPN_HOLD_GRACE_H = float(os.environ.get("ESPN_HOLD_GRACE_H", "6"))
OVER_HRS_MAX = 12   # the ODI value of run_tour's OVER_HRS; the budget uses the LONGER of the two
                    # so a T20 hold is never escalated early on an ODI-length game's clock.

def _hold_espn_card(event_id, kind, got, expected):
    """Record WHY this card was refused and return parse_espn's refusal value, so a refusal is
    never again indistinguishable from 'ESPN has no data for this match'."""
    ESPN_HOLDS[str(event_id)] = {"kind": kind, "got": got, "expected": expected,
                                 "event": str(event_id)}
    return {}, False

def _release_espn_card(event_id):
    """A good parse CLEARS the hold. Without this a healed match stays red forever — the
    written-but-never-cleared mirror of the same bug class the gate exists for."""
    ESPN_HOLDS.pop(str(event_id), None)

# Hold kinds that mean "we could not REACH ESPN", as opposed to "ESPN gave us a card that
# contradicts itself". Only the second kind is a question a human can answer.
_HOLD_UNREACHABLE = ("page_fetch_failed",)


def _espn_hold_escalated(hours_since_start, is_live, approved, kind=None):
    """Has this hold outlived the retry budget? Pure, so the cutoff is testable without a fetch.

    `hours_since_start` is hours since the match's SCHEDULED START (None when unparseable, which
    must NEVER escalate — an unknown clock is not an expired one). A live match never escalates:
    its card is legitimately still filling, and the summary/playbyplay fetches are seconds apart,
    so a one- or two-ball shortfall mid-innings is a fetch-ordering race, not a defect."""
    if is_live or approved or hours_since_start is None:
        return False
    # A TRANSPORT FAILURE IS NOT A SUSPECT CARD. page_fetch_failed means the fetch itself did not
    # come back — expected reads None because the summary failed too — so there is nothing for a
    # human to adjudicate: the answer is "try again", which the next run does anyway. Escalating it
    # asked the owner to rule on data nobody had seen, and it withheld a MATCH THAT WAS FINE: the
    # Hundred Women's 2026-08-12 (ev 1521227) was held 49h after start while ESPN in fact serves
    # 206 deliveries against a scorecard expecting 206, two full rosters and a completed innings.
    # Only a card we DID fetch and that contradicts itself (short_of_self_count / short_vs_scorecard)
    # is a real question, because there the data is present and wrong.
    if kind in _HOLD_UNREACHABLE:
        return False
    return hours_since_start >= OVER_HRS_MAX + ESPN_HOLD_GRACE_H

# ── Cricbuzz: the L1 SECOND WITNESS ──────────────────────────────────────────────────────────
# cricapi is the historical second witness and its code is untouched — it is still the witness on
# every tour that does not set `cricbuzz_series`. Cricbuzz replaces it where a tour opts in,
# because it is a strictly better witness: MEASURED on the two LPL matches with cached payloads
# (cb157138/ev1537349, cb157061/ev1537342) Cricbuzz and ESPN agree 48/48 player-rows on ALL of
# r, b, 4s, 6s, w, balls, runs_conceded, dots, maidens, catches, stumpings, runouts, dro, lbwb —
# i.e. it can cross-check ten fields cricapi carries NOTHING for, with zero measured noise. See
# RECON_L1_CB. cricapi, by contrast, returns a stub card for most franchise-league matches.
#
# SOFT IMPORT ON PURPOSE. Cricbuzz is an enhancement, never a dependency: a missing module, a
# broken bridge file or a Cricbuzz outage must degrade to "score off ESPN and say so", exactly
# like the existing no-data guards. It must never be able to stop a tour scoring.
try:
    import cricbuzz as _cricbuzz
    from registry import cricbuzz_bridge as _cb_bridge
    _CB_IMPORT_ERR = ""
except Exception as _cb_exc:                # ImportError, a syntax error in a mid-edit module, ...
    _cricbuzz = _cb_bridge = None
    _CB_IMPORT_ERR = repr(_cb_exc)
CB_SERIES = ""          # per-tour: tours.json "cricbuzz_series". "" => cricapi stays the witness.
CB_STORE = None         # the DERIVED cricbuzz_id -> cricinfo_id bridge, loaded once in main()
CB_DIAG = {}            # match_key -> {"witness", "note", "bridged", "unbridged", "cb_match"}

def guess_role(p):
    """Best-guess role from a player's feed stats (so '?' is never shown bare in review)."""
    if (p.get("stumpings", 0) or 0) > 0:
        return "WK"
    bowled = (p.get("balls", 0) or 0) > 0
    batted = (p.get("b", 0) or 0) > 0 or (p.get("r", 0) or 0) > 0
    if bowled and batted:
        return "AR"
    if bowled:
        return "BOWL"
    return "BAT"

def closest_squad(name, team_players):
    """Best-guess closest squad player for an unmatched feed name (surname-weighted), even
    below the match threshold — so 'Needs Review' can say 'is this <X>? Yes/No'."""
    nn = norm(name); nt = nn.split()
    if not nt:
        return ("", 0.0)
    ln = nt[-1]
    best, best_sc = "", 0.0
    for short, sname, role in team_players:
        st = norm(sname).split()
        if not st:
            continue
        sc = SequenceMatcher(None, nn, norm(sname)).ratio() * 60
        sc += SequenceMatcher(None, ln, st[-1]).ratio() * 40   # surname similarity dominates
        if st[-1] == ln:
            sc += 10
        if sc > best_sc:
            best_sc, best = sc, sname
    return (best, best_sc)

def dedup_review(items):
    """Collapse repeats (a player flagged in several matches) to one row per (tour, feed, kind)."""
    seen, out = set(), []
    for r in items:
        k = (r["tour"], r["feed"], r["kind"])
        if k in seen:
            continue
        seen.add(k); out.append(r)
    return out

def add_sheet_aliases(pairs):
    """Merge user-entered aliases (from the sheet's 'Player Aliases' tab) into ALIAS2PID.
    pairs: list of (feed_name, correct_player). 'correct_player' is any spelling/pid already
    known to the registry; we resolve it to its pid so the feed name points at the same player."""
    n = 0
    for feed, correct in pairs:
        if not feed or not correct:
            continue
        pid = ALIAS2PID.get(norm(correct)) or (correct.strip() if correct.strip() in PID2DISP else None)
        if pid:
            ALIAS2PID[norm(feed)] = pid; n += 1
        else:
            print(f"  sheet-alias: '{correct}' not found in registry — skipping '{feed}'", file=sys.stderr)
    return n

def resolve_pid(name):
    """Deterministic identity lookup: feed/squad name -> stable pid (or None)."""
    return ALIAS2PID.get(norm(name))

# Names a cricsheet file resolved BY ID whose spelling the registry didn't know yet. Learned
# from an authoritative id (never a guess), applied in-memory this run and persisted so the
# spelling is known to every app that reads the registry. See CRICSHEET_LEARNED_PATH.
CS_LEARNED = {}         # norm(cricsheet name) -> pid  (new this run)

def note_appearance(perf):
    """Remember who turned out, so the identity queue can tell a pre-debut squad name from a man
    who PLAYED and still has no id. Only the second is a question a human can answer."""
    for k, val in (perf or {}).items():
        if isinstance(val, dict) and val.get("played"):
            APPEARED.add(k)
            nm = norm(val.get("name") or "")
            if nm:
                APPEARED.add(nm)


def resolve_perf_pid(v):
    """Identity for ONE feed perf entry, id-first.

    Cricsheet writes players in initials form ('PWH de Silva' for Wanindu Hasaranga) and
    every cricsheet file carries its own `info.registry.people` name -> cricsheet person id
    map. Our registry stores each player's cricsheet_id (DERIVED from the verified cricinfo
    id), so a cricsheet row can be resolved by ID — deterministic, no fuzzy, no namesake
    risk. This is what stops the 'Hasaranga scored 0 once cricsheet landed' class: his squad
    row and his official-card row now land on the SAME pid instead of one zeroing and the
    other emitting as an unjoinable blank-pid orphan.

    Falls back to the name lookup for non-cricsheet feeds (cricapi/ESPN carry no cs id)."""
    cs = v.get("cs_id") if isinstance(v, dict) else None
    if cs:
        pid = CS2PID.get(str(cs))
        if pid:
            nn = norm(v.get("name", ""))
            # Teach the registry this spelling (id-anchored) so name lookups work too.
            if nn and nn not in ALIAS2PID:
                ALIAS2PID[nn] = pid
                CS_LEARNED[nn] = pid
                print(f"  cricsheet-id alias learned: '{v.get('name')}' -> {pid} "
                      f"({PID2DISP.get(pid, pid)}) via cricsheet id {cs}", file=sys.stderr)
            return pid
    # ESPN athlete.id IS the cricinfo id (build_registry.py:336), so `ci:<athlete.id>` is a DIRECT
    # registry key — no lookup table, no fuzzy, no namesake risk. This is the ESPN half of the same
    # id-anchoring that fixed cricsheet: without it an ESPN row could only ever be found by name,
    # and a spelling the alias table didn't know took its dots/maidens down with it (silently, as
    # 0 — cricapi carries neither, so nothing else could supply them).
    eid = v.get("espn_id") if isinstance(v, dict) else None
    if eid:
        pid = f"ci:{str(eid).strip()}"
        if pid in PID2DISP:
            nn = norm(v.get("name", ""))
            if nn and nn not in ALIAS2PID:
                ALIAS2PID[nn] = pid
                CS_LEARNED[nn] = pid
                print(f"  espn-id alias learned: '{v.get('name')}' -> {pid} "
                      f"({PID2DISP.get(pid, pid)}) via espn athlete id {eid}", file=sys.stderr)
            return pid
    by_name = resolve_pid(v.get("name", "") if isinstance(v, dict) else "")
    if by_name or not eid:
        # NEVER let an ESPN id override a pid the registry already holds for this name. If the
        # squad carries him as a placeholder (uncapped:/cs:) and we minted ci:<athlete.id> here,
        # the SAME person would exist under two pids — the points row on one, the draft row on the
        # other — and the draft would score him zero. That is the split-identity blocker
        # identity_healthcheck exists to catch; don't manufacture one. Promoting a placeholder to
        # its cricinfo id is build_registry's job, from a human-asserted bridge.
        return by_name
    # A player in the match who is in NO squad and NO registry entry — a mid-tournament signing or
    # an injury replacement. Until now this returned None and the row published with a BLANK Player
    # ID: unjoinable by the draft, and invisible to every pid-keyed check. But ESPN has already told
    # us exactly who he is — athlete.id IS the cricinfo id — so the identity is not in doubt and
    # guessing plays no part. Mint the id-anchored pid, and surface him for a human to fold into the
    # squad. Live: Rivaldo A Clarke (ci:1275938) and Kevlon Alston Anderson (ci:1209188), both in
    # Barbados' XI for CPL ev 1534182 and in no squad list we hold.
    eid_s = str(eid).strip()
    if not eid_s.isdigit() or eid_s == "0":
        # A cricinfo id is a positive integer. Anything else is not an id we can anchor on, so
        # fall back rather than mint something shaped like a pid but meaning nothing.
        return by_name
    pid = f"ci:{eid_s}"
    nm = v.get("name", "")
    if norm(nm) not in ALIAS2PID:
        ALIAS2PID[norm(nm)] = pid
        NEEDS_CRICINFO.append({
            "player": nm, "current_pid": pid, "tour": CURRENT_TOUR, "team": v.get("team", "?"),
            "closest_guess": f"NOT IN ANY SQUAD — id is ESPN's own athlete.id, verify at "
                             f"espncricinfo.com/cricketers/x-{eid} then add him to the squad",
        })
        print(f"  new player not in any squad: '{nm}' -> {pid} (ESPN athlete id, id-anchored) "
              f"— listed in '{NEEDS_CRICINFO_TAB}' for squad follow-up", file=sys.stderr)
    return pid

def _cricsheet_match(a, b):
    """True if `b` is a cricsheet-style initials name of `a` (e.g. 'Danni Wyatt' <-> 'DN Wyatt')."""
    s, d = norm(a).split(), norm(b).split()
    if not s or len(d) < 2:
        return False
    s_surnames = set(s[1:]) or {s[0]}
    return (d[-1] in s_surnames and len(d[-1]) >= 4 and d[0][0] == s[0][0])

def same_person_plausible(a, b):
    """Mirror of build_registry.given_compatible: do two names plausibly denote ONE person?
    Used to tell an INTENDED merge (two spellings of one player, e.g. cricsheet initials) from a
    SMEAR (two different people who share a surname). Conservative: when unsure, returns False so
    the anomaly is surfaced rather than hidden."""
    if _cricsheet_match(a, b) or _cricsheet_match(b, a):
        return True
    ta, tb = norm(a).split(), norm(b).split()
    if not ta or not tb:
        return False
    ga, gb = ta[0], tb[0]
    if ga == gb:
        return True
    if len(ga) <= 2 or len(gb) <= 2:          # an initial form ('S Luus' vs 'Sune Luus')
        return True
    return ga.startswith(gb) or gb.startswith(ga)

def long_form_plausible(a, b):
    """Is one name the FULL-FORM version of the other? ('Mohommed Shiraz' vs 'Katupulle Gedara
    Mohamed Shiraz Sahab' — the same LPL bowler, cricsheet 'M Shiraz', cricinfo 801817.)

    `same_person_plausible` compares first tokens, so a Sri Lankan full name — village/family
    prefix first, announced name buried in the middle — reads as a different person and a correct
    human answer gets refused. This is the narrow widening: the SHORT name's tokens must be
    substantially present in the LONG one, and the long one must actually look like a full form
    (4+ tokens) unless every short token matches. 0.80 admits mohommed/mohamed, a real spelling drift.

    Stays strict where it matters: 'Dale Phillips' vs 'Glenn Phillips' shares only the surname and
    neither is 4+ tokens -> still False, so the merge that started all this is still refused."""
    ta, tb = norm(a).split(), norm(b).split()
    if not ta or not tb:
        return False
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    hits = sum(1 for s in short
               if any(s == l or SequenceMatcher(None, s, l).ratio() >= 0.80 for l in long_))
    if hits == len(short):
        return True
    return len(long_) >= 4 and hits >= 2

JUNK_NAMES = {"player not found", "sub", "substitute", "not available"}
def is_junk(name):
    n = norm(name)
    return (not n) or n in JUNK_NAMES or "player not found" in n

def merge_perf(a, b):
    """Merge two perf dicts that resolved to the SAME pid (one player whose stats the feed
    split across two spellings, e.g. cricsheet 'DN Wyatt' + cricapi 'Danni Wyatt')."""
    if a is None: return b
    if b is None: return a
    out = dict(a)
    for k in ("r", "b", "4s", "6s", "balls", "runs_conceded", "w", "lbwb", "dots",
              "maidens", "catches", "stumpings", "runouts", "dro"):
        out[k] = (a.get(k, 0) or 0) + (b.get(k, 0) or 0)
    out["played"] = a.get("played") or b.get("played")
    out["dismissed"] = a.get("dismissed") or b.get("dismissed")
    out["dismissal"] = a.get("dismissal") or b.get("dismissal")
    out["bat_order"] = a.get("bat_order") or b.get("bat_order")
    out["team"] = a.get("team") or b.get("team")
    out["name"] = a.get("name") or b.get("name")
    return out

# API spelling -> squad spelling, for cases below the fuzzy threshold.
# ALSO used to CANONICALIZE within a single feed: cricapi sometimes spells the SAME
# player two ways in one scorecard (e.g. structured batting/bowling = "Charlotte Dean",
# but the dismissal-text = "Charlie Dean"), which splits her stats across two entries
# and the squad-matcher then grabs only one. Mapping the variant here collapses them.
ALIAS = {
    "kavisha dilhari": "kaveesha dilhari",
    "sugandika kumari": "sugandika dasanayaka",
    "charlotte dean": "charlie dean",   # cricapi formal name vs squad/ESPN "Charlie Dean"
    # MLC 2026: cricsheet uses initials whose first letter differs from the announced
    # first name, so the surname+first-initial rule can't catch them (the rest of the
    # MLC roster's initials DO match the announced names -> handled by fuzzy matching).
    "pwa mulder": "wiaan mulder",
    "gc viljoen": "hardus viljoen",
    "gsnfg jayasuriya": "shehan jayasuriya",
}

def match_squad_to_perf(team_players, pool, quiet=False):
    """team_players: [(short,name,role)]; pool: {normname: perf}.

    `quiet=True` suppresses every global side effect (ANOMALIES / UNMATCHED_LOG / AUTO_ALIASES /
    REVIEW) so the RECON path can re-run the identical matcher to reconstruct a baseline without
    double-reporting anomalies the emit path already reported. Matching behaviour is unchanged —
    that is the whole point: the baseline must be built by the SAME matcher that produced the
    published number, or it invents differences that never happened (the `dots 0→N` class).
    Identity-first matching: resolve every feed entry and every squad player to a stable
    registry pid and match on that (deterministic, no threshold, no surname collisions).
    Feed entries sharing a pid are MERGED (fixes stats split across two spellings). Only
    players the registry doesn't cover yet fall to the old fuzzy matcher — and every fuzzy
    hit + every genuine leftover is logged to UNMATCHED_LOG so the registry can be grown.
    Returns {(short,name): perf or None}, leftover pool, ambiguous set (kept for callers)."""
    # Drop junk feed entries ("Player Not Found", empty, stray punctuation).
    pool = {k: v for k, v in pool.items() if k and not is_junk(v.get("name", k))}
    # 1) index feed entries by pid, merging split spellings of the same player
    pid_pool, unresolved, pid_names = {}, {}, {}
    for k, v in pool.items():
        pid = resolve_perf_pid(v) or resolve_pid(v.get("name", k))
        if pid:
            pid_pool[pid] = merge_perf(pid_pool.get(pid), v)
            pid_names.setdefault(pid, []).append(v.get("name", k))   # track who folded into each pid
        else:
            unresolved[k] = v
    # FALSE-MERGE detector: a pid that absorbed 2+ feed names that are NOT plausibly the same
    # person means two DIFFERENT players share one id (a registry smear) — merge_perf just SUMMED
    # their stats. Surface it (don't silently sum). Intended merges (one player, two spellings)
    # pass same_person_plausible and are NOT flagged.
    for pid, names in pid_names.items():
        uniq = sorted({norm(n) for n in names if n})
        if len(uniq) > 1:
            incompatible = any(not same_person_plausible(uniq[0], n) for n in uniq[1:])
            if incompatible and not quiet:
                ANOMALIES.append({"tour": CURRENT_TOUR, "kind": "false_merge", "pid": pid,
                                  "display": PID2DISP.get(pid, pid), "context": CURRENT_TOUR,
                                  "names": [n for n in names if n],
                                  "finding": "feed spellings " + " / ".join(sorted(set(names)))
                                             + " all resolve to one id -> their stats were summed"})
    assigned, used_pid, used_uk = {}, set(), set()
    pending = []  # squad players the registry couldn't resolve to an available feed pid
    for short, name, role in team_players:
        pid = resolve_pid(name)
        if pid and pid in pid_pool and pid not in used_pid:
            assigned[(short, name)] = pid_pool[pid]
            used_pid.add(pid)
        else:
            pending.append((short, name, role))
    # 2) FUZZY FALLBACK (legacy behaviour) — only squad players unresolved by pid, matched
    #    against feed entries that also lacked a pid. Logged, never silent.
    if pending and unresolved:
        pairs = []
        for short, name, role in pending:
            nn = norm(name); nt = nn.split(); ln, fi = nt[-1], nt[0][0]
            for uk, uv in unresolved.items():
                ak = ALIAS.get(uk, uk); pt = ak.split()
                if not pt: continue
                pl, pf = pt[-1], pt[0][0]
                if ak == nn: sc = 100.0
                elif set(nt).issubset(set(pt)): sc = 95.0
                elif pl == ln and pf == fi: sc = 92.0
                elif pl == ln and max((SequenceMatcher(None, a, b).ratio()
                                       for a in nt for b in pt), default=0) >= 0.85: sc = 90.0
                else:
                    sc = SequenceMatcher(None, nn, ak).ratio() * 100
                    if pl == ln: sc += 8
                    if pt[0] == nt[0]: sc += 6
                pairs.append((sc, short, name, uk))
        pairs.sort(key=lambda x: -x[0])
        used_sq = set()
        for sc, short, name, uk in pairs:
            if sc < 84 or (short, name) in used_sq or uk in used_uk:
                continue
            assigned[(short, name)] = unresolved[uk]
            used_sq.add((short, name)); used_uk.add(uk)
            feed_nm = unresolved[uk].get("name", uk)
            if not quiet:
                UNMATCHED_LOG.add(f"AUTO   feed '{feed_nm}' -> squad '{name}' (score {sc:.0f})")
                # High-confidence fuzzy hit: auto-promote to the Player Aliases store (no user action).
                AUTO_ALIASES.append((feed_nm, name))
    leftover = {k: v for k, v in unresolved.items() if k not in used_uk}
    for k, v in leftover.items():
        if v.get("played") and not quiet:
            feed_nm = v.get("name", k)
            guess, gsc = closest_squad(feed_nm, team_players)
            guess = guess if gsc >= 55 else ""   # blank if no plausible squad player (genuine non-squad)
            UNMATCHED_LOG.add(f"REVIEW feed '{feed_nm}' (team {v.get('team', '?')}) — closest: {guess or '(none)'}")
            # Low-confidence: needs a human Yes/No. Record the best-guess closest squad player + role.
            REVIEW.append({"tour": CURRENT_TOUR, "team": v.get("team", "?"), "feed": feed_nm,
                           "kind": "review", "suggestion": guess, "role": guess_role(v)})
    return assigned, leftover, set()

def best_team(name, team_map):
    """Fuzzy-match a player name to an ESPN roster {norm_name: team} and return the team
    (handles verbose ESPN spellings). '' if no confident match."""
    nn = norm(name); nt = nn.split()
    if not nt:
        return ""
    ln, fi = nt[-1], nt[0][0]
    best, best_sc = "", 0.0
    for k, tfull in team_map.items():
        kt = k.split(); kl, kf = kt[-1], kt[0][0]
        if k == nn:
            sc = 100.0
        elif set(nt).issubset(set(kt)) or set(kt).issubset(set(nt)):
            sc = 95.0
        elif kl == ln and kf == fi:
            sc = 92.0
        elif kl == ln:
            sc = 86.0
        else:
            sc = SequenceMatcher(None, nn, k).ratio() * 100
        if sc > best_sc:
            best_sc, best = sc, tfull
    return best if best_sc >= 84 else ""

def _cache_file(path, params):
    """Where api() persists this request. Exposed so a caller that can SEE the response is not
    final can evict it (see evict_empty_scorecard)."""
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    key = re.sub(r"[^a-z0-9]", "_", f"{path}_{qs}".lower())
    return os.path.join(CACHE, key + ".json")

def evict_empty_scorecard(mid):
    """Delete a cached cricapi scorecard that carries NO performance data.

    api() persists on `status == "success"`, but cricapi answers SUCCESS with an empty scorecard
    for franchise leagues it hasn't populated yet. Once `matchEnded` flips, that blank is cached
    as the immutable final and re-read forever — cricapi is never asked again even after it fills
    the card in. The Hundred had real cricapi cards in July and none from 7 Aug for exactly this
    reason: we froze a blank and stopped listening.

    An empty card is not a final card. Evict it so the next run re-asks, which restores the
    two-feed cross-check instead of silently running single-source off ESPN."""
    try:
        fp = _cache_file("match_scorecard", {"id": mid})
        if os.path.exists(fp):
            os.remove(fp)
            return True
    except Exception as e:
        print(f"  (could not evict empty scorecard cache for {mid}: {e})", file=sys.stderr)
    return False

def api(path, cache=True, ttl=None, persist=True, **params):
    """GET with optional caching. Scorecards are cached (immutable once ended);
    series_info is NOT cached in the full run (so re-runs detect newly-completed matches),
    but the frequent tick caches it with a TTL to stay under cricapi's 100/day cap.

    RESILIENCE: if the live fetch fails (network error / quota exhausted / cricapi outage)
    but a previously-cached copy exists, fall back to the STALE copy rather than failing.
    Scorecards are immutable so a stale hit is exact; a stale series_info just means a
    brand-new match is scored one cycle late — far better than freezing the whole sheet
    (the old behaviour aborted the tour, so a cricapi blip left the sheet stale anyway)."""
    os.makedirs(CACHE, exist_ok=True)
    qs = "&".join(f"{k}={v}" for k, v in params.items())   # also used to build the URL below
    fp = _cache_file(path, params)
    fresh = os.path.exists(fp) and (ttl is None or (time.time() - os.path.getmtime(fp) < ttl))
    if cache and fresh:
        return json.load(open(fp))
    # 5-min live tick: never spend a cricapi hit. Serve any cached copy (stale is fine — a match
    # list / final scorecard doesn't change), else report a miss so the caller falls back to ESPN.
    if TICK_CACHE_ONLY:
        return json.load(open(fp)) if os.path.exists(fp) else {
            "status": "failure", "reason": "live tick: cricapi skipped (cache-only)"}

    def is_quota(d):
        r = (d.get("reason") or "").lower()
        return d.get("status") != "success" and ("limit" in r or "block" in r or "hits" in r)

    def record_quota(d):
        # cricapi returns {"info": {"hitsToday": N, "hitsLimit": 100, ...}} on every v1
        # response; remember the current key's day-cumulative usage so we can show it.
        info = d.get("info") if isinstance(d, dict) else None
        if not isinstance(info, dict):
            return
        today, limit = info.get("hitsToday"), info.get("hitsLimit")
        if today is None and limit is None:
            return
        prev = API_QUOTA.get(_key_idx, {})
        API_QUOTA[_key_idx] = {
            "today": int(today) if today is not None else prev.get("today", 0),
            "limit": int(limit) if limit is not None else prev.get("limit", 100),
        }

    global _key_idx
    data = {"status": "failure", "reason": "no api key"}
    # Try the current key; on a quota/blocked response, fail over to the next key(s).
    for attempt in range(max(1, len(API_KEYS))):
        if not API_KEYS:
            break
        url = f"{API}/{path}?apikey={API_KEYS[_key_idx]}&{qs}"
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                data = json.load(r)
        except Exception as e:
            data = {"status": "failure", "reason": f"fetch error: {e}"}
        if data.get("status") == "success" or not is_quota(data):
            break
        if _key_idx + 1 < len(API_KEYS):
            print(f"  api({path}): key #{_key_idx+1} {data.get('reason','')!r} — failing over to key #{_key_idx+2}", file=sys.stderr)
            _key_idx += 1
        else:
            break
    if data.get("status") == "success":
        global _CRICAPI_HITS
        _CRICAPI_HITS += 1   # a real live hit that spent one cricapi request from the daily budget
        if persist:   # live/in-progress scorecards pass persist=False: never cache a
            json.dump(data, open(fp, "w"))   # mid-match snapshot (would freeze live pts + poison the final read)
        record_quota(data)   # only on a live hit — a cached/stale read spends no quota
    elif os.path.exists(fp):
        # live fetch failed but we have a cached copy -> use it (stale, but keeps the sheet live)
        print(f"  api({path}): live fetch failed ({data.get('reason','')}); using cached copy", file=sys.stderr)
        data = json.load(open(fp))
    time.sleep(0.4)
    return data

# ---- squads: short -> {"name": full team name, "players": [(name, role)]} ----
def load_squads():
    # Standalone (CI) path: a committed squads.json, so the app's TS isn't needed.
    if SQUADS_JSON and os.path.exists(SQUADS_JSON):
        raw = json.load(open(SQUADS_JSON))
        return {k: {"name": v["name"], "players": [tuple(p) for p in v["players"]]}
                for k, v in raw.items()}
    if not SQUADS_JSON or not os.path.exists(SQUAD_TS):
        # No squad list for this tour -> "featured players only" mode: the sheet lists
        # everyone who appears in a scorecard (no DNP rows; team attributed from the feed).
        return {}
    txt = open(SQUAD_TS).read()
    teams = {}              # short -> {"name":..., "players":[(name,role)]}
    cur = None
    last_name = None
    for line in txt.splitlines():
        mn = re.search(r'name:\s*"([^"]+Women)"', line)
        if mn:
            last_name = mn.group(1)
        ms = re.search(r'short:\s*"([A-Z]+)"', line)
        if ms:
            cur = ms.group(1)
            teams[cur] = {"name": last_name, "players": []}
            continue
        mp = re.search(r'\{\s*name:\s*"([^"]+)",\s*role:\s*"([^"]+)"', line)
        if mp and cur:
            teams[cur]["players"].append((mp.group(1), mp.group(2)))
    return teams

def blank_perf(name, espn_id=""):
    # `espn_id` is ESPN's athlete.id, which IS the cricinfo id (build_registry.py:336) — carrying it
    # lets an ESPN row resolve by ID exactly like a cricsheet row resolves by cs_id, instead of by
    # name. Name matching on the ESPN side is what silently lost dots/maidens.
    return dict(name=name, team="", espn_id=str(espn_id or ""), r=0, b=0, **{"4s": 0, "6s": 0},
                dismissed=False, dismissal="", balls=0, runs_conceded=0, w=0, lbwb=0, dots=0,
                maidens=0, catches=0, stumpings=0, runouts=0, dro=0, played=False,
                bat_order=0)  # 1-based batting position from the scorecard (0 = unknown/DNB)

_GENDER_QUAL = re.compile(r"\(\s*(?:men|women)\s*\)|\b(?:men|women)\b", re.I)
def team_key(teams):
    """Date-independent team identity: franchise aliases folded (canon_team) and gender
    qualifiers dropped. ESPN suffixes gender as '(Men)'/'(Women)', cricapi uses the bare name or
    'X Women', cricsheet varies — all must collapse to one key (fixes The Hundred M/W matching)."""
    return frozenset(norm(_GENDER_QUAL.sub("", canon_team(t))) for t in teams)

# ---- cricsheet ball-by-ball (mirror of etl_cricsheet.py) -> EXACT dots/maidens/XI ----
def load_cricsheet_index(dirpath, gender="female"):
    """(date, team_key) -> json path, for completed matches of the given gender
    (so a men's tour matches men's cricsheet files, not women's, and vice versa).
    Format-agnostic: filters on gender + dates only, so it indexes whatever archives
    were unzipped into dirpath (T20s and/or ODIs); the tour's match filter picks the
    format, and (date, teams) keys it to the specific game."""
    idx = {}
    if not os.path.isdir(dirpath):
        return idx
    for f in glob.glob(os.path.join(dirpath, "*.json")):
        if os.path.basename(f) == "README.json":
            continue
        try:
            info = json.load(open(f)).get("info", {})
        except Exception:
            continue
        if info.get("gender") != gender or not info.get("dates"):
            continue
        idx[(info["dates"][0], team_key(info.get("teams", [])))] = f
    return idx

def parse_cricsheet(path):
    d = json.load(open(path)); info = d["info"]
    perf = {}
    # cricsheet's OWN name -> person-id registry, carried in every match file. Stamped onto
    # each perf entry so identity resolves by ID (resolve_perf_pid) instead of by the
    # initials-form spelling, which the alias table can't be expected to enumerate.
    people = info.get("registry", {}).get("people", {}) or {}
    def get(n):
        k = norm(n)
        if k not in perf:
            perf[k] = blank_perf(n)
            cs = people.get(n)
            if cs:
                perf[k]["cs_id"] = str(cs)
        return perf[k]
    for tname, plist in info.get("players", {}).items():   # known playing XI -> +4 each
        for n in plist:
            p = get(n); p["played"] = True; p["team"] = p["team"] or tname
    for inn in d.get("innings", []):
        if inn.get("super_over"):
            continue  # super-over deliveries score no fantasy points (match ESPN's period>2 skip)
        bat_pos = 0  # batting order = order players first appear at the crease this innings
        def crease(nm):
            nonlocal bat_pos
            p = get(nm); p["played"] = True
            if not p.get("bat_order"):
                bat_pos += 1; p["bat_order"] = bat_pos
            return p
        for over in inn.get("overs", []):
            legal = over_runs = 0; over_bowler = None
            for dl in over.get("deliveries", []):
                rb = dl.get("runs", {}).get("batter", 0); rt = dl.get("runs", {}).get("total", 0)
                ex = dl.get("extras", {}); is_wide = "wides" in ex; is_nb = "noballs" in ex
                legald = not is_wide and not is_nb
                # Runs CHARGED TO THE BOWLER exclude byes/leg-byes (the keeper's leak, not the
                # bowler's) — this drives economy, maidens and dots. Wides/no-balls ARE charged.
                bcharged = rt - (ex.get("byes", 0) or 0) - (ex.get("legbyes", 0) or 0)
                if over_bowler is None: over_bowler = dl["bowler"]
                # register striker then non-striker so openers get positions 1 & 2
                crease(dl["batter"])
                if dl.get("non_striker"): crease(dl["non_striker"])
                pb = get(dl["batter"]); pb["played"] = True
                if not is_wide: pb["b"] += 1
                pb["r"] += rb
                # cricsheet marks all-run 4s/6s and overthrows with `non_boundary: true`. Counting
                # them as boundaries inflates GROUND TRUTH — which is worse than an ordinary bug:
                # it invents an L2 recon row against a correct ESPN value, and answering S2 there
                # moves a SETTLED number in the wrong direction. 5 occurrences across the 141 LPL
                # files; the only remaining ESPN-vs-cricsheet diff was exactly this, with ESPN right.
                if not dl.get("runs", {}).get("non_boundary"):
                    if rb == 4: pb["4s"] += 1
                    elif rb == 6: pb["6s"] += 1
                pw = get(dl["bowler"]); pw["played"] = True
                pw["runs_conceded"] += bcharged
                # A maiden is an over with NO RUNS CHARGED TO THE BOWLER. Byes/leg-byes do not
                # break one (never his leak); WIDES AND NO-BALLS DO, and they were invisible here
                # because over_runs only accumulated on LEGAL deliveries. The over still reaches
                # exactly 6 legal balls — the extra is re-bowled, so it runs to 7 or 8 deliveries —
                # so `legal == 6 and over_runs == 0` passed and paid for a maiden never bowled.
                # Recounted from raw deliveries: 8 of 47 LPL maidens phantom (17%), 2683 of 16391
                # ODI. Worth +12 FP each in T20. Real: LPL 1238758 over 1 (Udana) — 8 deliveries,
                # 4 dots + 2 wides + a 4-run leg bye + a dot, 2 runs charged, scored a MAIDEN.
                # ⚠ BOTH parsers carried the identical rule, so the ESPN-vs-cricsheet harness
                # compared EQUAL — agreeing and both wrong. Only a raw recount finds this class;
                # cross-feed reconciliation structurally cannot. `bcharged` is ALREADY exactly
                # "runs charged to this bowler" (byes/leg-byes subtracted, wide/no-ball penalty
                # kept), so summing ALL of it is the whole fix — no special case for either rule.
                over_runs += bcharged
                if legald:
                    pw["balls"] += 1; legal += 1
                    if bcharged == 0: pw["dots"] += 1
                for w in dl.get("wickets", []):
                    kind = w.get("kind", ""); fs = w.get("fielders", [])
                    po = get(w.get("player_out", "")); po["dismissed"] = True; po["dismissal"] = kind
                    if kind not in ("run out", "retired hurt", "retired not out",
                                    "retired out", "obstructing the field", "hit wicket"):
                        pw["w"] += 1
                        if kind in ("bowled", "lbw"): pw["lbwb"] += 1
                    if kind == "hit wicket": pw["w"] += 1
                    if kind == "caught":
                        for fi in fs:
                            fn = fi.get("name", "")
                            if fn and fn != dl["bowler"]: get(fn)["catches"] += 1
                    if kind == "caught and bowled":   # the bowler caught it — credit the catch too
                        pw["catches"] += 1
                    if kind == "stumped":
                        for fi in fs:
                            if fi.get("name"): get(fi["name"])["stumpings"] += 1
                    if kind == "run out":
                        for fi in fs:
                            if fi.get("name"):
                                rp = get(fi["name"]); rp["runouts"] += 1
                                if len(fs) == 1: rp["dro"] += 1
            if legal == 6 and over_runs == 0 and over_bowler:
                get(over_bowler)["maidens"] += 1
    return perf, info.get("teams", [])

_ESPN_FAILED = set()

def _espn_warn_once(exc, url):
    """Report each distinct ESPN transport failure ONCE. espn_get returns {} on error, which is
    indistinguishable from 'ESPN has no data for this match' — that is how a blanket 403 hid as a
    quiet loss of dots/maidens/XI across every tour."""
    key = f"{type(exc).__name__}:{getattr(exc, 'code', '')}"
    if key not in _ESPN_FAILED:
        _ESPN_FAILED.add(key)
        print(f"  espn: fetch failed ({exc}) — e.g. {url[:110]}", file=sys.stderr)

def espn_get(path, cache=True, **params):
    os.makedirs(CACHE, exist_ok=True)
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    key = re.sub(r"[^a-z0-9]", "_", f"espn_{ESPN_SERIES}_{path}_{qs}".lower())
    fp = os.path.join(CACHE, key + ".json")
    if cache and os.path.exists(fp):
        _hit = json.load(open(fp))
        # HEAL AN ALREADY-POISONED ENTRY. Refusing to WRITE an empty scoreboard (below) stops new
        # poison, but CI restores its cache between runs, so a day cached empty during an ESPN
        # wobble would stay empty forever and the fix would never reach the match it lost. A cached
        # scoreboard with no events is therefore re-fetched rather than trusted: if the day really
        # is empty we learn that again cheaply, and if it is not, the fixture comes back.
        if not (path == "scoreboard" and not (_hit.get("events") or [])):
            return _hit
        print(f"  espn: cached scoreboard {params.get('dates','?')} is EMPTY — re-fetching rather "
              f"than trusting it (this is how 2026-08-04 was lost)", file=sys.stderr)
    url = f"{ESPN_BASE}/{ESPN_SERIES}/{path}?{qs}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": ESPN_UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except Exception as e:
        _espn_warn_once(e, url)
        return {}
    # ⛔ NEVER PERSIST AN EMPTY SCOREBOARD. A transport failure returns {} above and is not cached,
    # but ESPN also answers with a well-formed 200 carrying "events": [] — and THAT is
    # indistinguishable from "no matches were played that day", so it used to be cached forever.
    # Past dates are cached permanently (fresh is only today/yesterday), so ONE transient empty
    # response permanently erased a fixture. Live consequence: the Hundred Men's 2026-08-04
    # (Sunrisers Leeds v London Spirit, ESPN event 1521250) vanished from the sheet — the tab's
    # dates jump 08-03 -> 08-05 — while registry/settlement_snapshots.json still holds 65 rows and
    # 2195 FP for it, and draft contest YVDHLS sits on it with both selections frozen, one side
    # worth 902.5 pts, every player reading "—". The data was never gone: a fresh fetch returns the
    # event, and parse_espn scores it 211/211 with no hold. Only the cached emptiness was.
    # Re-fetching a genuinely empty day is cheap now that the back-scan is floored by tours.json
    # "starts", and getting a real day back is worth far more than the request it costs.
    if path == "scoreboard" and not (data.get("events") or []):
        print(f"  espn: scoreboard {params.get('dates','?')} returned NO events — not caching it "
              f"(an empty day and a bad moment look identical; caching would make it permanent)",
              file=sys.stderr)
        time.sleep(0.3)
        return data
    json.dump(data, open(fp, "w"))
    time.sleep(0.3)
    return data

def espn_event_id(mdate, teams):
    """Map a match (date 'YYYY-MM-DD' + team names) to its ESPN event id, tolerating
    a ±1 day timezone offset between feeds."""
    want = team_key(teams)
    for d in date_variants(mdate):
        sb = espn_get("scoreboard", cache=False, dates=d.replace("-", ""))
        for e in sb.get("events", []):
            comps = e.get("competitions", [{}])[0].get("competitors", [])
            tnames = [c.get("team", {}).get("displayName", "") for c in comps]
            if team_key(tnames) == want:
                return e.get("id")
    return None

def espn_dots(event_id):
    """Per-bowler dot-ball counts from ESPN ball-by-ball (completed match)."""
    d = espn_get("playbyplay", event=event_id, limit=600)
    out = {}
    for it in d.get("commentary", {}).get("items", []):
        ath = (it.get("bowler") or {}).get("athlete") or {}
        name = ath.get("fullName") or ath.get("name")
        if not name:
            continue
        k = norm(name)
        if k not in out:
            out[k] = {"name": name, "dots": 0,
                      "espn_id": str(ath.get("id", "") or "")}
        desc = (it.get("playType", {}).get("description") or "").lower()
        if "wide" in desc or "no ball" in desc or "no-ball" in desc:
            continue  # illegal delivery: not a dot
        try:
            sv = int(it.get("scoreValue", 0) or 0)
        except (TypeError, ValueError):
            sv = 0
        if sv == 0 and desc not in ("bye", "leg bye"):
            out[k]["dots"] += 1
    return out

def espn_toss(event_id):
    """Toss result text from ESPN summary notes (type 'toss'), e.g.
    'Bangladesh, elected to bat first'. Empty string if not posted yet."""
    try:
        d = espn_get("summary", event=event_id)
    except Exception:
        return ""
    for n in d.get("notes", []):
        if (n.get("type") or "").lower() == "toss":
            return re.sub(r"\s*,\s*", ", ", (n.get("text") or "").strip())
    return ""

def espn_team_map(event_id, fresh=False):
    """{norm(player): team displayName} from ESPN rosters — reliable team attribution
    (cricapi's innings labels are sometimes malformed)."""
    d = espn_get("summary", cache=not fresh, event=event_id)
    out = {}
    for team in d.get("rosters", []):
        tn = team.get("team", {}).get("displayName", "")
        for p in team.get("roster", []):
            a = p.get("athlete", {})
            nm = a.get("fullName") or a.get("displayName")
            if nm and tn:
                out[norm(nm)] = tn
    return out

def espn_runouts(event_id, fresh=False):
    """Run-out dismissals WITH their fielders, from the ESPN `summary` payload.

    WHY THIS EXISTS: `playbyplay` — which parse_espn reads for every other dismissal — carries a
    `dismissal.fielder` that is ALWAYS EMPTY for a run out, and its `shortText`/`text` never name
    the fielders either ("B Fernando run out 21 (15m 13b...)"). Measured over 24 LPL events: 19
    run-out items, 0 with a fielder, 0 with a parseable name. Caught worked (214/214), so the gap
    was invisible — run-outs simply scored zero for everyone, ~1.1 per match at 6 pts (assisted)
    or 12 (direct).

    `summary` has it, structured, at rosters[].roster[].linescores[].statistics.batting.outDetails,
    and `summary` is ALREADY FETCHED in the same run under the same cache key (espn_xi/
    espn_team_map). So this costs no extra request.

    Verified against cricsheet over 22 LPL matches: 17 run-outs vs 17, agreement 22/22, fielder
    sets exact on all 17 including 2-man assists and substitutes, and direct-vs-assisted
    (len(fielders)==1) correct 17/17. Format-agnostic — also verified on the Hundred.

    Returns [{"fielders": [(name, espn_id), ...]}] per run out; athlete.id IS the cricinfo id."""
    d = espn_get("summary", cache=not fresh, event=event_id)
    outs = []
    for team in d.get("rosters", []) or []:
        for p in team.get("roster", []) or []:
            for ls in p.get("linescores", []) or []:
                det = (((ls.get("statistics") or {}).get("batting") or {}).get("outDetails")) or {}
                if (det.get("dismissalCard") or "").strip().lower() != "run out":
                    continue
                fl = []
                for f in det.get("fielders", []) or []:
                    a = f.get("athlete") or {}
                    nm = a.get("fullName") or a.get("displayName")
                    if nm:
                        fl.append((nm, str(a.get("id") or "")))
                if fl:
                    outs.append({"fielders": fl})
    return outs

# ESPN `outDetails.dismissalCard` — the COMPLETE set observed across the 101 cached summary
# payloads on disk (CPL + LPL + Hundred M/W + the NZ-WI ODIs), with counts:
#   'c' 579 | 'not out' 240 | 'bowled' 167 | 'run out' 66 | 'lbw' 59 | 'st' 25
#   'retired not out' 4 | 'retired out' 2
# i.e. ESPN uses the SCORECARD abbreviations ('c', 'st'), not the playbyplay's spelled-out
# 'caught'/'stumped' — do not reuse the playbyplay `type` vocabulary here.
# NOT-OUT SET: '' (absent), 'not out', and 'retired not out' — which is ESPN's label for retired
# hurt (its own shortText for all 4 occurrences is literally "retired hurt"). A retired-hurt batter
# is not dismissed, so he must not take the −2 duck. 'retired out' IS a dismissal (the batter is
# out; the bowler simply gets no credit, which is already handled in the playbyplay loop).
# ⚠ parse_cricsheet DISAGREES on retired hurt — it marks every `wickets[]` kind dismissed,
# including 'retired hurt' (83 occurrences in the cricsheet fixture corpus). Measured impact of the
# disagreement on the 29-match sample: 1 player (BKG Mendis, LPL 2026-07-19, 2 off 4) — no duck
# either way. Not "fixed" on the cricsheet side here: `dismissed` is not in RECON_L2, and rewriting
# the official-card parser would move L2 baselines for a case worth 0 points.
ESPN_NOT_OUT_CARDS = ("", "not out", "retired not out")

def espn_batting_card(event_id, fresh=False):
    """Each batter's OWN scorecard line from `summary`: batting position + how he was out.

    WHY THIS EXISTS — two separate bugs, one payload:

    (1) BATTING ORDER. `blank_perf` has carried a `bat_order` field (and the sheet a "Bat Order"
        column) that the ESPN path never filled, so every ESPN-sourced tour published it blank.
        It is right here at rosters[].roster[].linescores[].statistics.batting.order — the SAME
        object espn_runouts already reads outDetails from. Verified 60/60 against cricsheet with
        0 disagreements. ESPN emits `batting` ONLY for the innings a player actually batted in and
        `order` is always 1..11 (1142 entries across the cached payloads, none 0/None), so a
        did-not-bat player simply has no entry and correctly keeps bat_order 0.

    (2) THE DISMISSED FLAG. ESPN's ball-by-ball attaches the dismissal to the item whose `batsman`
        is the STRIKER, not the batter it actually happened to. So the striker was flagged
        dismissed (twice, if he was later out himself) and the real victim never was. Measured on
        the 5 completed CPL matches (ESPN_SERIES 8623), before → after:
          ev1534179  14→15  −Mohammad Hassan Khan  +Keemo Paul, +Shayan Jahangir  (2 run outs)
          ev1534180  12→11  −Matthew Breetzke      (see below — NO run out in this match)
          ev1534181  15→15  −Fabian Allen          +Jahmar Hamilton               (1 run out)
          ev1534182  12→12  −Hunain Shah           +Vitel Lawes  ⇒ 1 BOGUS DUCK removed
          ev1534183  13→13  clean
        Totals: 4 wrongly flagged cleared, 4 real dismissals recovered, ducks 12 → 11.
        ⚠ THE CLASS IS WIDER THAN RUN OUTS, and ev1534180 is the counter-example: it contains no
        run out at all. Joshua Da Silva RETIRED HURT while Breetzke was on strike, so Breetzke was
        flagged — and Da Silva must NOT be marked dismissed (retired hurt is not out). Anything
        ESPN emits as a `dismissal` item can name a batter other than the striker.
        Against cricsheet over 29 matches (LPL 8 / Hundred men 8 / Hundred women 8 / NZ-WI ODI 5,
        joined on ids via registry/crosswalk.json) the signature ran at 19 wrong flags before and
        0 after; every one was `cs='run out', espn=False` (the victim) or `cs='', espn=True` (the
        striker). Ducks moved LPL 3→3, Hundred men 12→12, Hundred women 13→13, ODI 6→7 (cs=7).
        It drives the −2 duck penalty and the Dismissal column. The player's own scorecard line
        has no striker/non-striker ambiguity at all.

    `summary` is ALREADY FETCHED this run under the same cache key (espn_xi / espn_runouts /
    espn_expected_balls), so this costs no extra request.

    Returns {norm(name): {"name", "espn_id", "order", "dismissed", "dismissal"}}."""
    d = espn_get("summary", cache=not fresh, event=event_id)
    out = {}
    for team in d.get("rosters", []) or []:
        for p in team.get("roster", []) or []:
            a = p.get("athlete") or {}
            nm = a.get("fullName") or a.get("displayName")
            if not nm:
                continue
            for ls in p.get("linescores", []) or []:
                # Super-over innings are period 3+, and D11 awards no super-over points — the
                # playbyplay loop already skips them, so the batting card must too or a super-over
                # duck/dismissal would leak in through the back door.
                try:
                    per = int(ls.get("period") or 0)
                except (TypeError, ValueError):
                    per = 0
                if per > 2:
                    continue
                bat = ((ls.get("statistics") or {}).get("batting")) or {}
                if not bat:
                    continue          # did not bat in this innings
                k = norm(nm)
                if k in out:
                    continue          # first innings he batted in wins (mirrors parse_cricsheet)
                det = bat.get("outDetails") or {}
                card = (det.get("dismissalCard") or "").strip().lower()
                try:
                    order = int(bat.get("order") or 0)
                except (TypeError, ValueError):
                    order = 0
                # ESPN's scorecard dismissal string ("c &dagger;Pooran b Gleeson", "run out
                # (Joseph)", " b Lennox") — the form the owner reads during recon. The playbyplay
                # alternative is the commentary headline ("Seales to Hendricks, OUT"), which names
                # the STRIKER and so was actively misleading on exactly the rows this fixes.
                txt = html.unescape(det.get("shortText") or "").strip()
                dismissed = card not in ESPN_NOT_OUT_CARDS
                out[k] = {"name": nm, "espn_id": str(a.get("id") or ""), "order": order,
                          "dismissed": dismissed, "dismissal": (txt or card) if dismissed else ""}
    return out

def espn_xi(event_id, fresh=False):
    """Playing XI (incl. subs that came on) from ESPN summary -> for the +4 in-XI bonus,
    even for players who didn't bat/bowl/field (e.g. a captain who wasn't needed)."""
    d = espn_get("summary", cache=not fresh, event=event_id)
    out = {}
    for team in d.get("rosters", []):
        for p in team.get("roster", []):
            a = p.get("athlete", {})
            nm = a.get("fullName") or a.get("displayName")
            if nm and (p.get("starter") or p.get("subbedIn")):
                out[norm(nm)] = {"name": nm, "espn_id": str(a.get("id", "") or "")}
    return out

def espn_expected_balls(event_id, fresh=False):
    """Legal deliveries ESPN's SCORECARD says were bowled — an independent expectation.

    The point is that this comes from a DIFFERENT endpoint and a DIFFERENT field family than the
    ball-by-ball, so it cannot be wrong in the same way at the same time. `summary` is already
    fetched this run under the same cache key (espn_xi / espn_runouts), so it costs no request.

    Per-bowler `overallLhb.balls + overallRhb.balls` (balls to left- and right-handers) sums to the
    innings length: verified 236 v 236 on CPL ev 1534183, the match that exposed the need for it.
    Returns 0 when the scorecard has no bowling figures — the check is then simply unavailable."""
    d = espn_get("summary", cache=not fresh, event=event_id)
    tot = 0
    for team in d.get("rosters", []) or []:
        for p in team.get("roster", []) or []:
            for ls in p.get("linescores", []) or []:
                bw = ((ls.get("statistics") or {}).get("bowling")) or {}
                for side in ("overallLhb", "overallRhb"):
                    try:
                        tot += int((bw.get(side) or {}).get("balls") or 0)
                    except (TypeError, ValueError):
                        pass
    return tot

def parse_espn(event_id, fresh=False):
    """Full scorecard from ESPN ball-by-ball (cricinfo) — exact dots/maidens/fielding,
    plus the XI from the summary. Super-over deliveries (period>2) are excluded
    (Dream11 awards no points for them). Returns (perf, super_over_seen).
    fresh=True bypasses the cache — used for PROVISIONAL (no-cricsheet) matches whose ESPN
    data is still settling, so a stale mid-match snapshot can't freeze an incomplete XI
    (the bug that dropped Shubham Ranjane, an in-XI player who didn't bat/bowl)."""
    # PAGINATE. limit=600 silently truncates: an ODI innings has ~605 deliveries, so NZ-WI event
    # 1538624 returned items=600 of count=605 with pageCount=2 — the last 5 balls of the innings
    # vanished, with no error. T20 fits in one page, which is why this hid.
    #
    # ⛔ ESPN CAN RETURN AN EMPTY-BUT-WELL-FORMED CARD. Observed live on CPL ev 1534183: a 200 whose
    # body was {"commentary": {"count": 1, "pageCount": 1, "items": [<the pre-match "Hello and
    # welcome back, folks!" preamble>]}} — for a match ESPN's own scoreboard called Final. Every
    # count-based guard below passes it (1 item of a self-reported 1), so the match scored with all
    # 22 players Played=Y, every stat 0 and a bare +4 XI bonus. With settlement recording live that
    # is what would FREEZE as the money baseline.
    #
    # It is INTERMITTENT, not a limit threshold. It first appeared at limit=1000 while limit=600/500
    # returned the real 236 items, which looked like a page-size cap — but a re-probe of the SAME
    # events minutes later returned 256/236 correctly at limit=1000, and limit=100 had 502'd in the
    # same window. So the trigger is ESPN-side flakiness, and ANY limit can hit it. Do not "fix" it
    # by tuning this number and do not trust a low limit to prevent it.
    # The real defence is the scorecard ball-count cross-check after the parse loop — a different
    # endpoint and field family, so it cannot go blank in the same breath. 500 is kept only because
    # it is a verified-good page size that pages safely on a ~605-ball ODI innings.
    # A PARTIAL fetch must NEVER be scored. espn_get returns {} on any failure (502, timeout,
    # WAF), so a page that fails silently contributed nothing and the loop just ended — producing
    # a truncated innings that scores as if complete. Measured: the Hundred Women's sample came
    # back 197 balls / 221 runs / 80 dots SHORT of cricsheet, every field down ~14%, purely
    # because one event 502'd mid-sweep. On the feed we are about to make primary, that is the
    # most dangerous failure mode there is — it is wrong, it is silent, and it looks complete.
    # So: any failed page, or fewer items than ESPN's own `count`, aborts the whole match. An
    # empty perf makes the caller treat ESPN as unavailable (the existing no-data guard skips the
    # match and retries next run) rather than publishing a truncated scorecard.
    items, _page, _expected = [], 1, None
    while True:
        pbp = espn_get("playbyplay", cache=not fresh, event=event_id, limit=500, page=_page)
        com = (pbp.get("commentary") or {}) if isinstance(pbp, dict) else {}
        if not pbp or "commentary" not in (pbp or {}):
            print(f"  espn {event_id}: playbyplay page {_page} FAILED — refusing to score a "
                  f"partial scorecard (HELD, see the run summary)", file=sys.stderr)
            return _hold_espn_card(event_id, "page_fetch_failed", len(items), None)
        items += com.get("items", []) or []
        try:
            npages = int(com.get("pageCount") or 1)
        except (TypeError, ValueError):
            npages = 1
        if _expected is None:
            try:
                _expected = int(com.get("count")) if com.get("count") is not None else None
            except (TypeError, ValueError):
                _expected = None
        if _page >= npages or _page >= 10 or npages in (0, None):
            break
        _page += 1
    if _expected is not None and len(items) < _expected:
        print(f"  espn {event_id}: got {len(items)} of {_expected} deliveries — truncated, "
              f"refusing to score (HELD, see the run summary)", file=sys.stderr)
        return _hold_espn_card(event_id, "short_of_self_count", len(items), _expected)
    # DEDUP. ESPN emits byte-identical duplicate commentary items (ev 1537345: 259 items, 255
    # unique ids). Re-counting a delivery inflated dots, balls, runs and boundaries — Kamil
    # Mishara read 49 off 22 with 7 fours against cricsheet's 44 off 19 with 6 (+9 FP, the single
    # largest error in the set). Keep the first occurrence of each id.
    _seen, _uniq = set(), []
    for _it in items:
        _iid = _it.get("id")
        if _iid is not None:
            if _iid in _seen:
                continue
            _seen.add(_iid)
        _uniq.append(_it)
    if len(_uniq) != len(items):
        print(f"  espn {event_id}: dropped {len(items) - len(_uniq)} duplicate commentary item(s)",
              file=sys.stderr)
    items = _uniq
    perf, overs, super_over = {}, {}, False
    # Degraded-mode fallback ONLY (see the batting-card application after the loop):
    # {norm(name): (name, athlete_id, text)} derived from the ball-by-ball. Never written straight
    # onto a perf row. The id is carried so the fallback row is still ID-anchored, not name-only.
    pbp_dis = {}
    def get(n, aid=""):
        # Key by norm(name) for the pool contract, but CARRY athlete.id so the row resolves by id.
        k = norm(n)
        if k not in perf:
            perf[k] = blank_perf(n, espn_id=aid)
        elif aid and not perf[k].get("espn_id"):
            perf[k]["espn_id"] = str(aid)
        return perf[k]
    _deliveries = 0  # EVERY delivery seen, extras and super-over INCLUDED — see the balls check
    for it in items:
        per = it.get("period")
        desc = (it.get("playType", {}).get("description") or "").lower()
        is_wide = desc == "wide"
        # ESPN labels a no-ball by its OUTCOME ("bye" / "leg bye" / "four" / "run"), putting the
        # no-ball only in shortText — so `"no ball" not in desc` counted 19 of them as legal balls
        # across 22 LPL matches, inflating bowler balls (wrong econ/SR denominators) and, because
        # scoreValue INCLUDES the no-ball penalty, the batter's runs by +1 each.
        _short = (it.get("shortText") or "") + " " + (it.get("text") or "")
        is_nb = ("no ball" in desc) or bool(re.search(r"\(?no[- ]?ball\)?", _short, re.I))
        legal = not is_wide and not is_nb
        _bwa = (it.get("bowler", {}).get("athlete") or {})
        _bta = (it.get("batsman", {}).get("athlete") or {})
        bw, bw_id = _bwa.get("fullName"), _bwa.get("id", "")
        bt, bt_id = _bta.get("fullName"), _bta.get("id", "")
        # Counted BEFORE the super-over skip so it is comparable to the scorecard's ball total,
        # which has no way to exclude the super over either. Scoring still excludes it (D11 awards
        # no super-over points) — this counter exists ONLY to verify we fetched the whole match.
        # Requires a named bowler, so non-delivery commentary (the "Hello and welcome" preamble
        # that limit=1000 returns INSTEAD of the innings) is not miscounted as a ball. Counts
        # EVERY delivery, wides and no-balls included, because that is what the scorecard's ball
        # total counts — measured exact on CPL ev 1534183 (226 legal + 10 wide = 236 = scorecard)
        # and ev 1534179 (240 + 16 = 256 = scorecard). Deliberately NOT filtered by `legal`: a
        # completeness check must not depend on the extras-parsing it exists to validate.
        if bw:
            _deliveries += 1
        if per and per > 2:
            super_over = True
            continue
        try:
            sv = int(it.get("scoreValue", 0) or 0)
        except (TypeError, ValueError):
            sv = 0
        if bt:
            pb = get(bt, bt_id); pb["played"] = True
            if not is_wide:
                pb["b"] += 1
            if desc in ("run", "four", "six"):
                pb["r"] += (sv - 1) if is_nb else sv   # strip the no-ball penalty off the batter
            if desc == "four":
                pb["4s"] += 1
            elif desc == "six":
                pb["6s"] += 1
        if bw:
            pw = get(bw, bw_id); pw["played"] = True
            # Runs charged to the bowler exclude byes/leg-byes (drives economy, maidens, dots).
            # Byes/leg-byes are the keeper's leak, not the bowler's — EXCEPT the no-ball penalty
            # itself, which is always charged to the bowler.
            bcharged = (1 if is_nb else 0) if desc in ("bye", "leg bye") else sv
            if legal:
                pw["balls"] += 1
            pw["runs_conceded"] += bcharged
            if legal and bcharged == 0:
                pw["dots"] += 1
            ok = (per, it.get("over", {}).get("number"))
            o = overs.setdefault(ok, {"legal": 0, "runs": 0, "bowler": bw})
            o["bowler"] = bw
            # See the cricsheet twin above: wides and no-balls ARE charged to the bowler and must
            # break the maiden, so the over total takes EVERY delivery, not just the legal ones.
            # Kept byte-for-byte equivalent to that parser on purpose — a divergence between the
            # two IS an L1 recon row on a real match.
            o["runs"] += bcharged
            if legal:
                o["legal"] += 1
        dis = it.get("dismissal") or {}
        if dis.get("dismissal"):
            typ = (dis.get("type") or "").lower()
            # ⛔ `it["batsman"]` is the STRIKER of this delivery, NOT necessarily the batter this
            # dismissal happened to — on a run out (and on a retired) they are different people.
            # Writing dismissed=True onto him here is what wrongly flagged 4 batters and missed 4
            # real dismissals across the 5 completed CPL matches, including 1 bogus duck. The
            # authoritative source is each batter's OWN scorecard line (espn_batting_card, applied
            # after this loop); this dict is kept only as the degraded fallback for when the
            # summary carries no batting card at all.
            # `dis["batsman"]` DOES name the correct victim (measured: on ev1534179 the item whose
            # striker is Reeza Hendricks carries dis.batsman = Shayan Jahangir), so prefer it —
            # but it is still the ball-by-ball, so it stays second to the scorecard.
            _dba = (dis.get("batsman") or {}).get("athlete") or {}
            _dis_bt = _dba.get("fullName") or _dba.get("displayName") or bt
            _dis_id = str(_dba.get("id") or "") if (_dba.get("fullName")
                                                    or _dba.get("displayName")) else bt_id
            if _dis_bt:
                pbp_dis[norm(_dis_bt)] = (_dis_bt, _dis_id, it.get("shortText") or typ)
            # Robust exclusion: ESPN emits variants like "retired not out (hurt)" that an exact
            # tuple misses (that bug wrongly credited De Lange a 3rd wicket). Match by prefix.
            not_bowler_wkt = (typ == "run out" or typ.startswith("retired")
                              or "obstruct" in typ or typ == "hit wicket")
            if bw and not not_bowler_wkt:
                pw = get(bw, bw_id); pw["w"] += 1
                if typ in ("bowled", "lbw", "leg before wicket"):   # ESPN spells lbw out
                    pw["lbwb"] += 1
            elif bw and typ == "hit wicket":   # bowler's wicket (no lbw/bowled bonus)
                get(bw)["w"] += 1
            _fla = (dis.get("fielder", {}).get("athlete") or {})
            fld, fld_id = _fla.get("fullName"), _fla.get("id", "")
            # CARRY fld_id. It was extracted on the line above and then never read ANYWHERE —
            # `grep fld_id` returned exactly one hit, the assignment. So a catcher/stumper was
            # created by NAME, the one resolution route this project forbids. It is present on
            # 603/603 caught-or-stumped dismissals in the cached corpus, and invisible for 595 of
            # them only because the fielder also batted, bowled or sat in the XI and picked up an
            # id there. The 8 it is NOT invisible for are SUBSTITUTE fielders — absent from
            # rosters, never at the crease, never with the ball — and 4 of those have no pid at
            # all, reaching the right human purely on a name-similarity score. One is decided by a
            # 95-vs-90 margin between two Fernandos in the same Jaffna Kings squad, while ESPN was
            # handing us 1074333 the whole time. That is the Dale-into-Glenn class, on a live tour.
            if typ == "caught and bowled" or (typ == "caught" and fld and norm(fld) == norm(bw or "")):
                if bw: get(bw, bw_id)["catches"] += 1   # caught off own bowling — credit the catch
            elif typ == "caught" and fld and norm(fld) != norm(bw or ""):
                get(fld, fld_id)["catches"] += 1
            elif typ == "stumped" and fld:
                get(fld, fld_id)["stumpings"] += 1
            # NOTE: run outs are deliberately NOT credited here. playbyplay's dismissal.fielder is
            # always empty for a run out and neither shortText nor text names the fielders, so any
            # attempt from this payload silently credits nobody. They are applied below from the
            # `summary` payload instead (espn_runouts), which carries them structured.
    # ⛔ COMPLETENESS, CROSS-CHECKED — do not trust the ball-by-ball's own account of itself.
    # The guard above compares items against `count`, but `count` is written by the same code path
    # that writes the items, so a degraded response is self-consistent and passes: at limit=1000
    # ESPN returned count=1 with one preamble item and CPL ev 1534183 scored as a completed match
    # in which all 22 players did nothing. So verify against the SCORECARD's ball total instead —
    # a different endpoint, a different field family, and unable to fail in the same way.
    _exp_balls = espn_expected_balls(event_id, fresh)
    if _exp_balls and _deliveries < _exp_balls:
        if str(event_id) not in ESPN_CARD_SCORE_ANYWAY:
            print(f"  espn {event_id}: ball-by-ball has {_deliveries} deliveries but the "
                  f"scorecard says {_exp_balls} — INCOMPLETE, HELD (not scored; a named "
                  f"'ESPN CARD' row goes to the Recon tab once the retry budget expires)",
                  file=sys.stderr)
            return _hold_espn_card(event_id, "short_vs_scorecard", _deliveries, _exp_balls)
        # A human answered S2 on the Recon row: score the short card DELIBERATELY. The hold entry
        # stays (marked approved) so the row still carries the shortfall — an approved decision to
        # accept degraded data must never publish looking clean.
        print(f"  espn {event_id}: {_deliveries} of {_exp_balls} deliveries — SCORING ANYWAY "
              f"per the approved 'ESPN CARD' recon decision", file=sys.stderr)
        ESPN_HOLDS[str(event_id)] = {"kind": "short_vs_scorecard", "got": _deliveries,
                                     "expected": _exp_balls, "event": str(event_id),
                                     "approved": True}
    else:
        # Clears page_fetch_failed / short_of_self_count / short_vs_scorecard alike: whatever this
        # event was held on last run, this parse is complete. LOAD-BEARING and easy to drop —
        # without it a hold from a previous parse of the same event survives a healed card and
        # keeps the run red forever, the written-but-never-cleared mirror of the very bug class
        # this gate exists for.
        _release_espn_card(event_id)
    if not _exp_balls:
        # Not fatal (a card with no bowling figures is legitimately unverifiable), but it means
        # this match published WITHOUT the cross-check — say so rather than implying it passed.
        print(f"  espn {event_id}: scorecard carries no bowling figures — ball-count cross-check "
              f"UNAVAILABLE for this match", file=sys.stderr)

    # ── batting position + dismissed, from each batter's OWN scorecard line (espn_batting_card).
    # AUTHORITATIVE, and applied in BOTH directions: it sets the run-out victim dismissed AND
    # clears the striker the ball-by-ball wrongly flagged. A one-directional patch would leave the
    # bogus duck in place — that mirror-guard gap is the recurring bug class in this file.
    # Bowler wicket credit is NOT touched here (it was already correct: run-outs are excluded via
    # not_bowler_wkt, giving 13 bowler wickets + 2 run-outs = 15 = ESPN's own header on ev1534179).
    _card = espn_batting_card(event_id, fresh)
    if _card:
        for k, c in _card.items():
            p = get(c["name"], c.get("espn_id") or "")
            p["played"] = True                      # he came to the crease
            p["bat_order"] = c["order"] or p.get("bat_order") or 0
            p["dismissed"] = c["dismissed"]
            p["dismissal"] = c["dismissal"]
    else:
        # An absence must never present as a value: with no scorecard batting card we CANNOT know
        # who was out, so say so loudly rather than publishing silence as "nobody was dismissed".
        # The ball-by-ball fallback keyed on dis.batsman is better than the old striker-keyed
        # write, but it is unverified against the scorecard and carries no batting order.
        print(f"  espn {event_id}: summary carries NO batting card — bat_order UNAVAILABLE and "
              f"dismissals fall back to ball-by-ball (unverified)", file=sys.stderr)
        for nm, aid, txt in pbp_dis.values():
            fp = get(nm, aid)      # a batter ESPN says was OUT played, even if he faced no ball
            fp["played"] = True; fp["dismissed"] = True; fp["dismissal"] = txt

    for o in overs.values():
        if o["legal"] == 6 and o["runs"] == 0 and o["bowler"]:
            get(o["bowler"])["maidens"] += 1
    # Run outs from `summary` (see espn_runouts): playbyplay cannot supply the fielders.
    for ro in espn_runouts(event_id, fresh):
        names = ro["fielders"]
        for nm, eid in names:
            rp = get(nm)
            if eid and not rp.get("espn_id"):
                rp["espn_id"] = eid          # athlete.id IS the cricinfo id
            rp["runouts"] += 1
            if len(names) == 1:
                rp["dro"] += 1               # unassisted -> direct-hit bonus

    for k, e in espn_xi(event_id, fresh).items():   # +4 in-XI even for players with no stat line
        if k not in perf:
            # CARRY THE ID. espn_xi already returns athlete.id (which IS the cricinfo id) and this
            # path threw it away, so a player who appeared ONLY in the XI — never batted, bowled or
            # fielded — arrived with espn_id="" and could be resolved by NAME alone. That is the one
            # resolution route this project forbids, and when the name is unknown the row publishes
            # with a BLANK Player ID and can never join the draft. Live example: Kevlon Alston
            # Anderson, CPL ev 1534182, in the XI for Barbados but absent from our squad list.
            perf[k] = blank_perf(e["name"], espn_id=e.get("espn_id", ""))
        elif e.get("espn_id") and not perf[k].get("espn_id"):
            perf[k]["espn_id"] = str(e["espn_id"])
        perf[k]["played"] = True
    return perf, super_over

def crosscheck(cs, api):
    """Compare overlapping stats between cricsheet & cricapi for the same match.
    Returns list of (player, field, cricsheet_val, cricapi_val) disagreements.
    Dots are intentionally excluded (cricapi has none)."""
    diffs = []
    for k, c in cs.items():
        a = api.get(k)
        if not a:
            continue
        for f in ("r", "b", "4s", "6s", "w", "maidens", "balls", "catches", "stumpings"):
            if (c.get(f) or 0) != (a.get(f) or 0):
                diffs.append((c["name"], f, c.get(f) or 0, a.get(f) or 0))
    return diffs

# ---- D11 scoring (mirror of calculator.ts) ----
def score(p, role, fmt=None):
    """Dispatch to the format's scorer. fmt defaults to the running tour's CURRENT_FMT."""
    f = (fmt or CURRENT_FMT or "T20").upper()
    if f == "ODI":
        return _score_odi(p, role)
    if f == "HUN":
        return _score_hundred(p, role)
    return _score_t20(p, role)

def _bowled(p):
    """Did this player bowl? Prefer a known ball count, but fall back to any hard bowling
    signal. cricapi omits the `overs` field on 100-ball (The Hundred) cards — and can drop it
    on a feed hiccup in any format — so bowlers can arrive with balls=0. We backfill balls from
    ESPN (see the merge near L1487), but when THAT also misses (e.g. the bowler's scorecard line
    didn't name-match), `balls` stays 0. Gating the whole bowling block on `balls > 0` alone then
    silently zeroes a bowler whose wickets/runs DID come through from cricapi — the recurring
    "Gleeson 4-for -> 4-pts" / "Usman Tariq 2-for -> 0 bowl pts" bug. A wicket, a conceded run,
    a dot, an lbw/bowled or a maiden is proof enough that they bowled. The economy/SR sub-blocks
    keep their own `balls >= N` gates (they divide by balls), so they correctly stay 0 when the
    ball count is unknown — only the ball-count-independent points (wkt/haul/dot/lbw/maiden) are
    recovered."""
    return bool(p["balls"] or p["w"] or p["runs_conceded"] or p["dots"]
                or p["lbwb"] or p["maidens"])

def _score_t20(p, role):
    bat = bowl = field = sr_pts = eco_pts = 0
    if p["b"] > 0 or p["r"] > 0:
        bat += p["r"] * R["perRun"] + p["4s"] * R["b4"] + p["6s"] * R["b6"]
        # Milestone bonus: only the HIGHEST reached applies (each replaces the lower).
        if p["r"] >= 100: bat += R["m100"]
        elif p["r"] >= 75: bat += R["m75"]
        elif p["r"] >= 50: bat += R["m50"]
        elif p["r"] >= 25: bat += R["m25"]
        if p["b"] >= 10 and role != "BOWL":
            sr = p["r"] / p["b"] * 100
            if sr > 170: sr_pts += 6
            elif sr > 150: sr_pts += 4
            elif sr >= 130: sr_pts += 2
            elif 60 <= sr <= 70: sr_pts += -2
            elif 50 <= sr < 60: sr_pts += -4
            elif sr < 50: sr_pts += -6
    # Duck (-2) for BAT/WK/AR dismissed for 0 — OUTSIDE the b>0/r>0 gate so a batter
    # run out for 0 off 0 balls (backing up at the non-striker's end) still gets it.
    if p["dismissed"] and p["r"] == 0 and role != "BOWL":
        bat += R["duck"]
    if _bowled(p):
        bowl += p["w"] * R["wkt"] + p["lbwb"] * R["lbwb"] + p["dots"] * R["dot"] + p["maidens"] * R["maiden"]
        if p["w"] >= 5: bowl += R["h5"]
        elif p["w"] >= 4: bowl += R["h4"]
        elif p["w"] >= 3: bowl += R["h3"]
        if p["balls"] >= 12:
            econ = p["runs_conceded"] / (p["balls"] / 6)
            if econ < 5: eco_pts += 6
            elif econ < 6: eco_pts += 4
            elif econ <= 7: eco_pts += 2
            elif 10 <= econ <= 11: eco_pts += -2
            elif 11 < econ <= 12: eco_pts += -4
            elif econ > 12: eco_pts += -6
    field += p["catches"] * R["catch"]
    if p["catches"] >= 3: field += R["c3"]
    field += p["stumpings"] * R["stump"]
    field += p["dro"] * R["dro"] + (p["runouts"] - p["dro"]) * R["ro"]
    xi = R["xi"] if p["played"] else 0
    total = bat + bowl + field + sr_pts + eco_pts + xi
    return dict(bat=bat, bowl=bowl, field=field, sr=sr_pts, eco=eco_pts, xi=xi, total=total)

def _score_hundred(p, role):
    """D11 The Hundred (100-ball) scorer — mirror of compute_fantasy_points_hundred in the
    auction ETL. Same core scale as T20 EXCEPT: NO strike-rate, NO economy and NO maiden
    points (The Hundred awards none), and wicket hauls tier from a 2-for: 2w+4 / 3w+8 /
    4w+12 / 5w+16. sr and eco are returned as 0 so the sheet's Pts SR / Pts Econ columns
    stay present and truthfully read 0 for this format."""
    R2 = R_HUN
    bat = bowl = field = 0
    if p["b"] > 0 or p["r"] > 0:
        bat += p["r"] * R2["perRun"] + p["4s"] * R2["b4"] + p["6s"] * R2["b6"]
        if p["r"] >= 100: bat += R2["m100"]
        elif p["r"] >= 75: bat += R2["m75"]
        elif p["r"] >= 50: bat += R2["m50"]
        elif p["r"] >= 25: bat += R2["m25"]
        # No strike-rate points in The Hundred.
    # Duck (-2) OUTSIDE the b>0/r>0 gate (a 0-off-0 run-out still counts), as in _score_t20.
    if p["dismissed"] and p["r"] == 0 and role != "BOWL":
        bat += R2["duck"]
    if _bowled(p):
        bowl += p["w"] * R2["wkt"] + p["lbwb"] * R2["lbwb"] + p["dots"] * R2["dot"]
        if p["w"] >= 5: bowl += R2["h5"]
        elif p["w"] >= 4: bowl += R2["h4"]
        elif p["w"] >= 3: bowl += R2["h3"]
        elif p["w"] >= 2: bowl += R2["h2"]
        # No maiden, no economy points in The Hundred.
    field += p["catches"] * R2["catch"]
    if p["catches"] >= 3: field += R2["c3"]
    field += p["stumpings"] * R2["stump"]
    field += p["dro"] * R2["dro"] + (p["runouts"] - p["dro"]) * R2["ro"]
    xi = R2["xi"] if p["played"] else 0
    total = bat + bowl + field + xi
    return dict(bat=bat, bowl=bowl, field=field, sr=0, eco=0, xi=xi, total=total)

def _score_odi(p, role):
    """D11 ODI scorer (mirror of ODI_RULES / calculateOdiPoints in the app + the ETL).
    vs T20: duck -3; milestones highest-only; dot = 1 per 3 dots; maiden +4; hauls 4w/5w/6w;
    SR gate 20 balls with shifted bands; econ gate 30 balls with shifted bands."""
    R2 = R_ODI
    bat = bowl = field = sr_pts = eco_pts = 0
    if p["b"] > 0 or p["r"] > 0:
        bat += p["r"] * R2["perRun"] + p["4s"] * R2["b4"] + p["6s"] * R2["b6"]
        if p["r"] >= 100: bat += R2["m100"]
        elif p["r"] >= 75: bat += R2["m75"]
        elif p["r"] >= 50: bat += R2["m50"]
        elif p["r"] >= 25: bat += R2["m25"]
        if p["b"] >= 20 and role != "BOWL":
            sr = p["r"] / p["b"] * 100
            if sr > 140: sr_pts += 6
            elif sr > 120: sr_pts += 4
            elif sr >= 100: sr_pts += 2
            elif 40 <= sr <= 50: sr_pts += -2
            elif 30 <= sr < 40: sr_pts += -4
            elif sr < 30: sr_pts += -6
    if p["dismissed"] and p["r"] == 0 and role != "BOWL":
        bat += R2["duck"]
    if _bowled(p):
        bowl += (p["w"] * R2["wkt"] + p["lbwb"] * R2["lbwb"]
                 + (p["dots"] // R2["dotGroup"]) * R2["dotPts"] + p["maidens"] * R2["maiden"])
        if p["w"] >= 6: bowl += R2["h6"]
        elif p["w"] >= 5: bowl += R2["h5"]
        elif p["w"] >= 4: bowl += R2["h4"]
        if p["balls"] >= 30:
            econ = p["runs_conceded"] / (p["balls"] / 6)
            if econ < 2.5: eco_pts += 6
            elif econ < 3.5: eco_pts += 4
            elif econ <= 4.5: eco_pts += 2
            elif 7 <= econ <= 8: eco_pts += -2
            elif 8 < econ <= 9: eco_pts += -4
            elif econ > 9: eco_pts += -6
    field += p["catches"] * R2["catch"]
    if p["catches"] >= 3: field += R2["c3"]
    field += p["stumpings"] * R2["stump"]
    field += p["dro"] * R2["dro"] + (p["runouts"] - p["dro"]) * R2["ro"]
    xi = R2["xi"] if p["played"] else 0
    total = bat + bowl + field + sr_pts + eco_pts + xi
    return dict(bat=bat, bowl=bowl, field=field, sr=sr_pts, eco=eco_pts, xi=xi, total=total)

def overs_to_balls(o):
    o = float(o or 0)
    whole = int(o)
    return whole * 6 + round((o - whole) * 10)

def parse_match(mid, live=False):
    """Return {normalized_name: perf-dict} for one match's scorecard. For a LIVE (in-progress)
    match, fetch FRESH and don't persist — the scorecard is still changing, so a cached snapshot
    would freeze live points, and persisting it would poison the final read once the match ends."""
    d = api("match_scorecard", id=mid, cache=not live, persist=not live)
    _mid_for_evict = mid
    perf = {}   # norm name -> dict
    def get(n):
        k = norm(n)
        k = ALIAS.get(k, k)   # canonicalize feed name variants so split spellings merge
        if k not in perf:
            perf[k] = blank_perf(n)
        return perf[k]
    innings = d.get("data", {}).get("scorecard", [])
    bat_teams = [re.sub(r"\s+Inning.*$", "", inn.get("inning", "")).strip() for inn in innings]
    all_teams = list(dict.fromkeys(t for t in bat_teams if t))
    def other(t):
        o = [x for x in all_teams if x != t]
        return o[0] if len(o) == 1 else ""
    def setteam(pl, t):
        if t and "," not in t and not pl["team"]:   # skip cricapi's malformed combined labels
            pl["team"] = t
    for i, inn in enumerate(innings):
        bat_team = bat_teams[i]; bowl_team = other(bat_team)
        for pos, bt in enumerate(inn.get("batting", []), 1):
            pl = get(bt["batsman"]["name"]); pl["played"] = True; setteam(pl, bat_team)
            if not pl.get("bat_order"):
                pl["bat_order"] = pos  # scorecard batting position (this innings)
            pl["r"] += bt.get("r", 0) or 0; pl["b"] += bt.get("b", 0) or 0
            pl["4s"] += bt.get("4s", 0) or 0; pl["6s"] += bt.get("6s", 0) or 0
            dis = (bt.get("dismissal") or "").lower()
            dtext = (bt.get("dismissal-text") or "")
            if dtext and "not out" not in dtext.lower() and dtext.lower() != "not out":
                pl["dismissed"] = True; pl["dismissal"] = dtext
            # credit lbw/bowled bonus to the bowler. cricapi sometimes returns a NULL
            # bowler object even when the dismissal-text clearly names them (seen for
            # "Charlie Dean": every "lbw b Charlie Dean" came back bowler=None), so fall
            # back to parsing the bowler out of the text — else the +8 silently vanishes.
            if "bowled" in dis or "lbw" in dis:
                bname = (bt.get("bowler") or {}).get("name")
                if not bname:
                    mb = re.search(r"\bb ([^()]+)$", dtext)
                    if mb:
                        bname = mb.group(1).strip()
                if bname:
                    setteam(get(bname), bowl_team)
                    get(bname)["lbwb"] += 1
            # caught & bowled: cricapi's `catching` array OMITS the bowler-as-catcher, so the
            # +8 catch silently vanishes. ESPN + cricsheet both credit it; match them here.
            # Match "c & b X" / "c and b X", or "c X b X" where catcher == bowler.
            cbn = None
            mcb = re.search(r"\bc\s*(?:&|and)\s*b\s+(.+)$", dtext, re.I)
            if mcb:
                cbn = mcb.group(1).strip()
            else:
                m2 = re.search(r"\bc\s+(.+?)\s+b\s+(.+)$", dtext, re.I)
                if m2 and norm(m2.group(1)) == norm(m2.group(2)):
                    cbn = m2.group(2).strip()   # catcher == bowler -> caught & bowled
            if cbn:
                cbp = get(cbn); cbp["played"] = True; setteam(cbp, bowl_team)
                cbp["catches"] += 1
            # run-outs: parse fielders from dismissal text -> direct (1 fielder) vs assisted (2+)
            if "run out" in dtext.lower():
                m = re.search(r"run out \(([^)]*)\)", dtext, re.I)
                if m:
                    fielders = [re.sub(r"(sub\b|†|\[|\])", "", f).strip()
                                for f in m.group(1).split("/")]
                    fielders = [f for f in fielders if f]
                    direct = len(fielders) == 1
                    for fn in fielders:
                        fp = get(fn); fp["played"] = True; setteam(fp, bowl_team)
                        fp["runouts"] += 1
                        if direct:
                            fp["dro"] += 1
        for bw in inn.get("bowling", []):
            pl = get(bw["bowler"]["name"]); pl["played"] = True; setteam(pl, bowl_team)
            pl["balls"] += overs_to_balls(bw.get("o", 0))
            pl["runs_conceded"] += bw.get("r", 0) or 0
            pl["w"] += bw.get("w", 0) or 0
            pl["maidens"] += bw.get("m", 0) or 0
        for ct in inn.get("catching", []):
            if not ct.get("catcher", {}).get("name"):
                continue
            pl = get(ct["catcher"]["name"]); pl["played"] = True; setteam(pl, bowl_team)
            pl["catches"] += ct.get("catch", 0) or 0
            pl["stumpings"] += ct.get("stumped", 0) or 0
            # run-outs come from dismissal-text parsing (direct vs assisted), not here
    # A card with nobody who batted, bowled or fielded is cricapi saying "I don't have this yet",
    # not "nothing happened". api() already persisted it (status was "success"), so drop that file
    # or the blank becomes the permanent answer and cricapi is never asked again.
    if not live and not any(_perf_has_activity(v) for v in perf.values()):
        if evict_empty_scorecard(_mid_for_evict):
            print(f"  cricapi returned an EMPTY scorecard for match {_mid_for_evict} — "
                  f"evicted from cache so it is re-fetched (not frozen as final)", file=sys.stderr)
    return perf

# ── Reconciliation (two-stage audit trail) ──────────────────────────────────
# L1 = cricapi ↔ ESPN during the provisional cut (both are live feeds; the only
#      fields BOTH carry are runs/wkts/4s/6s — cricapi has no dots/maidens).
# L2 = cricsheet (official) ↔ the provisional cut, once cricsheet posts. Richer:
#      cricsheet has everything, so we compare the full fantasy-relevant set.
RECON_L1 = ["r", "w", "4s", "6s"]
RECON_L2 = ["r", "w", "4s", "6s", "dots", "maidens", "runs_conceded",
            "catches", "stumpings", "runouts"]
RECON_LABEL = {"r": "runs", "w": "wkts", "4s": "4s", "6s": "6s", "dots": "dots",
               "maidens": "maid", "runs_conceded": "conc", "catches": "ct",
               "stumpings": "st", "runouts": "ro",
               # Only ever compared when the witness is Cricbuzz (cricapi carries none of these).
               "b": "faced", "balls": "bowled", "dro": "d-ro", "lbwb": "lbw/b"}

# The L1 field set when the second witness is CRICBUZZ. RECON_L1 above is the cricapi set and is
# short for one reason only: those are the ONLY four fields cricapi and ESPN both carry. Cricbuzz
# carries the whole card, so the comparison stops being four-fields-wide.
#
# MEASURED before choosing this list — cb157138/ev1537349 and cb157061/ev1537342 (LPL, the two
# matches with cached Cricbuzz payloads), 48 player-rows joined by the DERIVED bridge, never by
# name: 48/48 agree on every field below. 0 disagreements, so this is a real second opinion on ten
# fields that had none, not a new source of Recon noise.
#
# WHAT IS DELIBERATELY LEFT OUT, and why (this is the "don't flood the tab" list):
#   `dismissed` — a DEFINITIONAL divergence, not a data one. Cricbuzz marks RETIREDHURT dismissed
#     (so does parse_cricsheet: 83 occurrences in the fixture corpus); ESPN's scorecard card calls
#     it "retired not out" and NOT dismissed. Comparing it would raise a row on every retired-hurt
#     that no human can usefully answer, for a field worth 0 points except the -2 duck.
#   `bat_order` — not a scoring input, and a 12th-man/substitute changes it legitimately.
# ⛔ ABSENCE IS NOT A VALUE. Cricbuzz writes None (never 0) for a field it could not establish —
# `maidens` on The Hundred (corrupt: a verbatim copy of `dots` on 13/13 bowlers), `dots` when the
# completeness gate failed, `balls` when a bowler row carries neither balls nor overs. A None is
# SKIPPED by _l1_pair_gaps, never compared as 0: comparing it would silently manufacture a
# disagreement out of an absence, which is this file's most expensive recurring bug.
RECON_L1_CB = ["r", "b", "4s", "6s", "w", "balls", "runs_conceded", "dots", "maidens",
               "catches", "stumpings", "runouts", "dro", "lbwb"]

def recon_gaps(a, b, fields, sep="/"):
    """Compare two perf dicts on `fields`; return a compact 'field a{sep}b' gap string
    (empty if every field agrees, or either side is missing). For L1 use sep='/'
    (cricapi/ESPN — two parallel feeds); for L2 use sep='→' (provisional→official, so the
    arrow reads as 'was → corrected to')."""
    if not a or not b:
        return ""
    out = []
    for f in fields:
        av, bv = a.get(f, 0), b.get(f, 0)
        # ABSENT ON EITHER SIDE = nothing to compare. Cricbuzz writes None (never 0) for a field
        # it could not establish — HUN `maidens` is hard-ignored as corrupt, `dots` is None when
        # the ball-count completeness gate failed. The old `or 0` turned that absence into a real
        # zero, so the L1 column would shout "maid 0/3" on a field nobody ever measured, while the
        # gate (which does skip it) called the match clean — the column contradicting the gate,
        # which is a bug class this file has already paid for once.
        if av is None or bv is None:
            continue
        if (av or 0) != (bv or 0):
            out.append(f"{RECON_LABEL.get(f, f)} {av or 0}{sep}{bv or 0}")
    return "; ".join(out)

# Fields score() INDEXES. If a frozen baseline is missing one of these we cannot re-score it, and
# defaulting it to 0 is not a harmless shortcut: absent `lbwb` understates the baseline by 8 points
# per bowled/lbw wicket and absent `dro` mis-splits a run-out (12 vs 6), which would MANUFACTURE an
# L2 gap on a settled match. Say "unverified" instead of inventing a number.
_SCORING_CRITICAL = ("r", "b", "4s", "6s", "balls", "runs_conceded", "w", "lbwb", "dots",
                     "maidens", "catches", "stumpings", "runouts", "dro", "dismissed", "played")


def _hydrate_baseline(frozen):
    """A frozen `fields` dict widened to a full perf shape, or (None, missing) if it cannot be.

    Baselines are frozen with whatever SETTLED_FIELDS held AT THE TIME, so older rows genuinely
    lack keys the scorer now indexes — `dismissed` was added today because it drives the duck, and
    freezing only the dismissal TEXT was the same written-but-never-read asymmetry. Handing such a
    row to score() raises KeyError, which the backstop caught and reported as
    "pts ?→? (backstop failed — unverified)" with no clue which field was to blame.
    """
    if not frozen:
        return None, ()
    missing = tuple(k for k in _SCORING_CRITICAL if k not in frozen)
    if missing:
        return None, missing
    p = blank_perf(frozen.get("name", ""))
    p.update(frozen)
    return p, ()


def points_gap(a, b, role, sep="→"):
    """BACKSTOP: did the SCORED TOTAL move, whatever fields did it?

    recon_gaps only compares an enumerated field list, so a change in a field nobody thought
    to list slips through as '✓ complete' while the points on screen quietly change — balls
    faced and balls bowled aren't in RECON_L2 yet they drive the SR and economy components
    (LPL: Dickwella 69 -> 63, flagged clean). Comparing the total closes the class for good:
    any future field we forget is still caught, because points are what a contest settles on."""
    if not a or not b:
        return ""
    try:
        pa = score(a, role)["total"]
        pb = score(b, role)["total"]
    except KeyError as e:
        # A key the scorer indexes is absent — almost always a baseline frozen before that field
        # joined SETTLED_FIELDS. Name the field: "backstop failed" told nobody what to fix.
        _miss = _hydrate_baseline(a)[1] or _hydrate_baseline(b)[1] or (str(e).strip("'"),)
        print(f"!! points backstop: baseline predates {'/'.join(_miss)} — cannot re-score it, so "
              f"the TOTAL is unverified for this player (fields listed above are still compared)",
              file=sys.stderr)
        return f"pts ?{sep}? (baseline predates {'/'.join(_miss)} — total unverified)"
    except Exception as e:
        # LOUD, not silent: a backstop that quietly switches itself off is worse than none —
        # it would report "✓ complete" on exactly the matches it failed to check.
        print(f"!! points backstop could not score a perf dict ({e}) — "
              f"treating as UNVERIFIED for this player", file=sys.stderr)
        return "pts ?→? (backstop failed — unverified)"
    return f"pts {pa}{sep}{pb}" if pa != pb else ""

def _by_pid(perf):
    """Index a perf dict by stable pid (first spelling wins on a pid collision). id-first, so
    a cricsheet initials-form row keys on the SAME pid as its squad/cricapi spelling."""
    out = {}
    for v in perf.values():
        pid = resolve_perf_pid(v)
        if pid and pid not in out:
            out[pid] = v
    return out

# Fields NO live feed but ESPN supplies. cricapi carries neither, so when an ESPN row is missing
# these stay at blank_perf's 0 — indistinguishable from a genuine zero. Everything that went wrong
# in the LPL M2/M4 class traces back to that one collapse.
ESPN_ONLY_FIELDS = ("dots", "maidens")

def merge_espn_into(assigned, espn_assigned):
    """Merge ESPN's ball-by-ball extras onto a cricapi-assigned scorecard, IN PLACE.

    SINGLE implementation, deliberately: the emit path and the recon baseline both call this, so
    the published number and the number L2 compares against cannot drift apart. Two independent
    merge/match paths is exactly what produced the phantom `dots 0→N` review rows — the emit path
    found the bowler via the squad-anchored fuzzy fallback, the baseline rebuilt him with a strict
    id-only index, missed, and reported correctly-scored dots as an official revision.

    Returns (xcheck, unsourced): xcheck = keys where cricapi and ESPN disagree on runs/wkts/conc;
    unsourced = keys with deliveries bowled but NO ESPN row, i.e. scored on an ASSUMED zero for
    dots+maidens rather than a measured one."""
    xcheck, unsourced = set(), set()
    for k, base in assigned.items():
        # `_bowled`, not `balls` — see the twin guard below. A Hundred bowler arrives with
        # balls=0 (cricapi omits `overs` on 100-ball cards), so a balls-keyed test is DEAD on
        # exactly the format where it matters most.
        if base and _bowled(base) and k not in espn_assigned:
            unsourced.add(k)
    for k, e in espn_assigned.items():
        base = assigned.get(k)
        if base:
            base["dots"] = e["dots"]                      # exact dots from ESPN
            base["maidens"] = e.get("maidens", base.get("maidens", 0))  # + maidens (cricapi has none)
            # Bowler balls-bowled: cricapi omits the overs field on 100-ball (The Hundred)
            # scorecards, so its bowlers arrive with balls=0 — which zeroes the ENTIRE bowling
            # block (wkt/dot/maiden/econ are all gated on `balls > 0` in _score_t20). ESPN
            # counts deliveries one-by-one (format-agnostic), so backfill from it when cricapi
            # supplied none. Guarded on a falsy cricapi count, so standard T20/ODI (where
            # cricapi's over-derived balls are authoritative) are untouched.
            if not base.get("balls"):
                base["balls"] = e.get("balls", 0)
            # Batting position, same guarded-backfill pattern: 0 is blank_perf's documented
            # "unknown/DNB", and ESPN emits `batting.order` ONLY for a player who actually came to
            # the crease, so a falsy cricapi value is a genuine absence and never a real position.
            # cricapi's own order stays authoritative wherever it has one.
            # NOT done for `dismissed`: there, False is a legitimate VALUE (not out), so "cricapi
            # says False" and "cricapi said nothing" are indistinguishable — backfilling it would
            # be inventing a dismissal from an absence, which is the exact bug class E2 just fixed.
            if not base.get("bat_order"):
                base["bat_order"] = e.get("bat_order", 0)
            if (base.get("r", 0) != e.get("r", 0) or base.get("w", 0) != e.get("w", 0)
                    or base.get("runs_conceded", 0) != e.get("runs_conceded", 0)):
                xcheck.add(k)                             # cricapi vs ESPN disagree
        elif e.get("played"):
            # cricapi has no line for this player, but ESPN does. The old code copied ONLY
            # dots+maidens off a FULL ESPN record and dropped the rest — runs, balls, wickets,
            # boundaries, fielding, all binned — so he scored the bare +4 XI bonus and nothing
            # else. Measured on a real case: published 4 pts against 110 actually earned.
            # The comment below used to read "in XI, no cricapi line -> +4", which was the
            # intent when ESPN was a dots-only side-source; ESPN is a FULL scorecard source now
            # (see the source-priority note in run_tour), so discarding it is throwing away data
            # we hold. Rule: nothing goes unconsumed.
            np = dict(e); np["played"] = True
            np.setdefault("dots", 0); np.setdefault("maidens", 0)
            assigned[k] = np
    return xcheck, unsourced

def build_provisional_cut(team_players, api_perf, espn_perf):
    """Reconstruct the provisional cut (cricapi scorecard + ESPN dots/maidens) EXACTLY as the emit
    path builds it, keyed by pid. Used as the L2 baseline so 'what cricsheet revised' is measured
    against what was genuinely published — never against a value the reconstruction failed to find.

    Runs the SAME squad-anchored matcher as emit (quiet, so anomalies aren't double-reported), so a
    bowler emit resolved via fuzzy fallback resolves here too. The old code indexed ESPN with a bare
    id-only lookup, lost every row whose spelling wasn't in the alias table, and silently kept
    cricapi's 0 for dots — cricapi has no dots at all.

    Returns (prov_by_pid, unsourced_pids)."""
    capi_assigned = match_squad_to_perf(team_players, api_perf, quiet=True)[0] if api_perf else {}
    prov = {k: dict(v) for k, v in capi_assigned.items() if v}
    unsourced = set()
    if espn_perf:
        espn_assigned = match_squad_to_perf(team_players, espn_perf, quiet=True)[0]
        _, unsourced = merge_espn_into(prov, {k: v for k, v in espn_assigned.items() if v})
    else:
        # No ESPN at all: every bowler's dots/maidens are an assumption, not a measurement.
        unsourced = {k for k, v in prov.items() if v and v.get("balls")}
    by_pid, unsourced_pids = {}, set()
    for (short, name), v in prov.items():
        pid = resolve_pid(name) or resolve_perf_pid(v)
        if not pid:
            continue
        if pid not in by_pid:
            by_pid[pid] = v
        if (short, name) in unsourced:
            unsourced_pids.add(pid)
    return by_pid, unsourced_pids

def unresolved_official(cs_perf):
    """Official-card (cricsheet) entries that resolve to NO pid. A blank pid on the OFFICIAL
    card is an identity failure by definition: the draft joins by pid and refuses to fuzzy-fall
    back for a pid'd player, so these points can never reach a contest — they emit as an
    orphan row and the squad row they belong to silently reads 0. Never let this be quiet."""
    return [v for v in cs_perf.values()
            if v.get("played") and not resolve_perf_pid(v)]

# ── Recon Review: human-in-the-loop reconciliation of feed disagreements ─────
# When cricapi and ESPN disagree (L1) or cricsheet revises an already-scored match
# (L2), surface the gap as an APPROVABLE row in the "Recon Review" sheet tab and keep
# the match LIVE until the human picks a value. Mirrors the Identity Anomalies pattern.
# Pure helpers below (no sheet/network) so they're unit-testable.
LABEL2FIELD = {v: k for k, v in RECON_LABEL.items()}  # "runs" -> "r", etc. (reversible)

# A trivial cross-feed run gap (one batter off by 1-2 between cricapi and ESPN) isn't worth
# holding a match for — it's a single fantasy point of uncertainty. Wickets and boundaries are
# ALWAYS flagged (they move real points: a wicket is 30, a four/six is 4-6 + the run).
L1_RUN_TOL = int(os.environ.get("RECON_L1_RUN_TOL", "1"))

def _l1_field_material(field, cv, ev):
    """Is this per-field witness/ESPN difference worth flagging? Runs only beyond L1_RUN_TOL;
    everything else always (equal -> never).

    ⛔ Either side being None means ABSENT, and an absence is never a disagreement. cricapi's perf
    dicts come from blank_perf and are all-zero, so this branch is dead on the cricapi witness and
    changes nothing there; Cricbuzz writes None for a field it could not establish (HUN maidens,
    a failed dots derivation) and comparing that as 0 would fabricate a gap out of thin air."""
    if cv is None or ev is None:
        return False
    if cv == ev:
        return False
    if field == "r":
        return abs(cv - ev) > L1_RUN_TOL
    return True

def _l1_pair_gaps(c, e, fields):
    """The MATERIAL per-field gap strings between one witness row and one ESPN row.
    Shared by compute_l1_gaps and build_recon_rows so the gate and the review rows can never
    disagree about which differences count (they were two copies of the same expression)."""
    out = []
    for f in fields:
        # A MISSING key defaults to 0 (blank_perf guarantees every key, so this only affects
        # hand-built dicts); an EXPLICITLY-None value stays None and is skipped as absent. The two
        # cases have to stay distinguishable or "Cricbuzz could not establish this" collapses into
        # "Cricbuzz says zero".
        cv, ev = c.get(f, 0), e.get(f, 0)
        if _l1_field_material(f, cv, ev):
            out.append(f"{RECON_LABEL.get(f, f)} {cv or 0}/{ev or 0}")
    return out

def match_key_of(mdate, teams):
    """Stable, order-independent match identity (date + team pair) — NEVER the renumbered
    'Match N' label. Mirrors the draft app's teams+date join."""
    return f"{mdate}::" + "|".join(sorted(team_key(teams)))

def _perf_has_activity(p):
    """Did this feed actually observe the player DO anything? Balls faced/bowled, runs, wickets or
    boundaries. Distinguishes a real performance from a bare '+4 in-XI' placeholder row."""
    return any((p or {}).get(k, 0) for k in ("b", "balls", "r", "w", "4s", "6s"))

def _espn_has_ballbyball(e):
    """ESPN with no balls faced/bowled and no runs/wkts/boundaries is a '+4 in-XI' placeholder
    (ESPN lacks ball-by-ball for this match), NOT an observed 0. Without this guard, a match
    ESPN never ball-tracked would falsely flag EVERY player as cricapi-vs-0."""
    return _perf_has_activity(e)

def compute_l1_gaps(capi_pid, espn_pid, fields=None, witness="cricapi"):
    """{pid: gap_string} for a MATERIAL L1 disagreement between the two live feeds (a 1-run
    blip is ignored; everything else always counts — see _l1_field_material).

    `capi_pid` is THE SECOND WITNESS, whichever feed that is for this tour: cricapi by default,
    Cricbuzz where the tour sets `cricbuzz_series`. The parameter keeps its old name so every
    existing caller and test is untouched; `witness` only names it in the messages, and `fields`
    widens the comparison to RECON_L1_CB when Cricbuzz is supplying it.

    Iterates the UNION of both feeds, not just the witness's keys. Iterating `capi_pid` alone meant
    a player cricapi never listed produced no gap, no flag and no review row — he was scored off
    ESPN's row (or off nothing) and the match published COMPLETED with no one the wiser. A player
    ONE feed has and the other doesn't is exactly the case a two-feed reconciliation exists to
    surface, so it is reported as a gap rather than skipped in silence.

    ⛔ ONE EXCEPTION, AND IT IS AN IDENTITY EXCEPTION. When the witness is CRICBUZZ, "ESPN has a
    row and the witness does not" is NOT reported. Cricbuzz players reach this function only after
    the derived bridge resolves their cricbuzz id to a cricinfo id, so a missing witness row is
    overwhelmingly "we could not BRIDGE him", not "Cricbuzz did not see him" — and an identity gap
    must never appear in the Recon tab (CLAUDE.md rule E). Cold-start makes that concrete: bridge
    coverage on a season's first two matches is 0%, so this direction would post 22 unanswerable
    rows per match. The unbridged residual is counted, logged and routed to "Needs Cricinfo ID"
    by cb_match_perf instead. The MIRROR direction (Cricbuzz saw a performance ESPN missed) is
    still reported — that is a value fact about ESPN, and it is the class that published 4 pts
    against 110 earned.

    Still skipped: a pid ESPN has no ball-by-ball for (all-zero placeholder) when the witness DOES
    have him — there is genuinely nothing to cross-check there, and flagging it would fire on
    every player of every match ESPN hasn't ball-tracked."""
    fields = fields or RECON_L1
    # Is ESPN covering this match AT ALL? If it isn't, a missing ESPN row carries no information
    # about any individual player, and flagging one per player would bury the tab in a wall of
    # noise for what is really ONE match-level fact ("no ESPN feed"). That whole-match case is the
    # gate's job (no dots supplied -> unconsumed -> LIVE), not this function's.
    espn_covers_match = any(_espn_has_ballbyball(v) for v in espn_pid.values())
    # ...and the EXACT SAME question about cricapi, which was never asked. cricapi returns a STUB
    # scorecard for franchise leagues (players listed, every stat zero — documented in CLAUDE.md
    # as "match_scorecard returns 'not found' for most franchise-league matches"). Without this,
    # a stub reads as an observed 0 and every player becomes a "disagreement": 381 rows of
    # "Sean Dickson runs 0/34" for a human to arbitrate, on 12 Hundred/LPL matches where cricapi
    # simply had no data. One feed having nothing is a MATCH-level fact, not 381 player facts.
    capi_covers_match = any(_perf_has_activity(v) for v in capi_pid.values())
    gaps = {}
    for pid in set(capi_pid) | set(espn_pid):
        c, e = capi_pid.get(pid), espn_pid.get(pid)
        if not capi_covers_match:
            continue                          # cricapi has no data for this match at all
        if c is not None and e is not None:
            if not _espn_has_ballbyball(e):
                continue                      # ESPN blank for this player — nothing to compare
            if not _perf_has_activity(c):
                continue                      # cricapi blank for this player — the mirror case
            parts = _l1_pair_gaps(c, e, fields)
            if parts:
                gaps[pid] = "; ".join(parts)
        elif e is not None and _espn_has_ballbyball(e) and capi_covers_match:
            # ESPN tracked a real performance the witness never listed. NOT a silent zero — this
            # is the class that published 4 pts against 110 earned. Suppressed for Cricbuzz: see
            # the identity exception in the docstring.
            if witness != "cricbuzz":
                gaps[pid] = f"present in ESPN only ({witness} has no line)"
        elif c is not None and _perf_has_activity(c) and espn_covers_match:
            # The witness saw a performance ESPN missed, on a match ESPN otherwise covers — so the
            # absence is about THIS player, not about the feed. The reverse blind spot.
            gaps[pid] = f"present in {witness} only (ESPN has no line)"
    return gaps

def identity_break(prov_pid, cs_pid, cs_orphans):
    """The failure the L2 gate used to be BLIND to.

    `l2_pairs` only iterates pids present in cricsheet, so a player whose official-card
    spelling didn't resolve produces no pair, no flag and no review row — his squad row just
    reads 0 and the match completes clean. That is exactly how Hasaranga's 114-point captain
    innings (LPL Match 6, 21 Jul) became a 0 on a match badged COMPLETED with no flag.

    Returns (zeroed_pids, orphan_names): squad players who PLAYED in the provisional cut but
    are absent from the official card, and official-card rows that resolve to no pid. Either
    on its own is suspicious; together they are almost certainly the same person."""
    zeroed = sorted(pid for pid, v in prov_pid.items()
                    if v.get("played") and pid not in cs_pid)
    orphans = sorted((v.get("name") or "") for v in cs_orphans)
    return zeroed, orphans

# Fields NO second live feed can validate: only ESPN supplies them during the provisional window
# (cricapi carries neither), so there is no value-vs-value comparison to make at L1. They are
# ACCEPTED at L1 and first validated at L2, when cricsheet posts. Absence is a different matter —
# a bowler who bowled with no dots source is unconsumed data and holds the match LIVE.
RECON_L1_SINGLE = ["dots", "maidens"]

def classify_recon_state(cs_path, unresolved, unsourced, l2_pairs, l2_appr, unattributed=()):
    """The recon axis, orthogonal to Match Status.

      L1_OPEN    — feeds disagree, or data is unconsumed. Match is LIVE; base points not yet frozen.
      L1_DONE    — L1 reconciled and everything consumed. Base points FROZEN. cricsheet not in yet.
      L2_PENDING — cricsheet posted and differs from the frozen baseline on ≥1 unapproved player.
      L2_DONE    — cricsheet posted and agrees, or every difference has been approved.

    Deliberately NOT folded into Match Status: a match is COMPLETED the moment L1 is done and stays
    COMPLETED forever, while L2 moves underneath it on its own timeline. One column cannot carry
    two independent lifecycles — trying to is what produced a COMPLETED_FLAGGED that meant
    'unverified single feed' OR 'official revision pending' OR 'identity unresolved' OR 'L2 open',
    with no way for the app to tell which.

    `unattributed` = played performances that landed on no squad slot at all (find_unattributed).
    They are unconsumed data in the most literal sense — not a disputed value, an ABSENT row — so
    they open L1 exactly like `unsourced` does."""
    if unresolved or unsourced or unattributed:
        return "L1_OPEN"
    if not cs_path:
        return "L1_DONE"
    return "L2_PENDING" if any(pid not in l2_appr for pid in l2_pairs) else "L2_DONE"

RECON_STATE_LABEL = {"L1_OPEN": "⏳ L1 recon open", "L1_DONE": "✅ L1 recon done",
                     "L2_PENDING": "🔵 L2 recon pending", "L2_DONE": "✅ L2 recon done"}

def classify_match_status(cs_path, espn_present, l1_gaps, unresolved, l2_dirty, id_break=False,
                          unsourced=(), already_completed=False, capi_present=True,
                          witness="cricapi", unattributed=()):
    """Per-match status the draft app reads.

    The gate asks ONE question: can every player's score be fully accounted for? Not 'did two
    feeds agree on four fields?' — that narrower question is what let LPL M2/M4 publish COMPLETED
    with bowlers scored on dots=0 (cricapi carries no dots; the ESPN row never matched; L1 compares
    only r/w/4s/6s so both feeds 'agreed' and it sailed through clean). A field nobody thought to
    compare is not a field that was verified.

    `unsourced` = players whose scored total rests on a field NO available feed supplied. Absence
    of a source is NOT a zero, and must never publish as final.

    `already_completed` = this match has a frozen settlement baseline, i.e. it HAS published as
    COMPLETED before. The spec says COMPLETED never returns to LIVE, but nothing enforced it: the
    status was recomputed from scratch every run, so any newly-appearing gap silently un-published
    a settled match. On 7-9 Aug it un-published 12 Hundred/LPL matches (410 frozen baseline rows)
    days after they were settled — results vanished from the app and the recon tab filled with
    hundreds of rows for matches nobody thought were open. A new gap on a settled match is worth
    FLAGGING, never worth retracting the result.

    `unattributed` = played performances that reached the end of the pipeline on no squad slot
    (find_unattributed). It is tested FIRST, ahead of even the cricsheet branch, because it is not
    a question about which of two numbers is right — it is a row that is NOT IN THE SHEET. cricsheet
    posting cannot un-drop a player the pipeline never attributed to a side, so letting the
    `cs_path` early return swallow it would recreate the exact silence this check exists to end."""
    if unattributed:
        n = len(unattributed)
        msg = f"{n} performance{'' if n == 1 else 's'} not attributed to a team"
        return (("COMPLETED_FLAGGED", "⚠ " + msg) if already_completed
                else ("LIVE", "⏳ " + msg))
    if cs_path:
        if id_break:
            return ("COMPLETED_FLAGGED", "⚠ identity unresolved on official card")
        return ("COMPLETED_FLAGGED", "⚠ official revision pending") if l2_dirty else ("COMPLETED", "")
    # UNSOURCED beats every other non-cricsheet verdict: an unresolved L1 gap is 'two feeds
    # disagree, pick one'; an unsourced field is 'nobody measured this and we scored it as 0'.
    # The second is strictly worse — there is no value to pick, and nothing downstream will notice.
    if unsourced:
        n = len(unsourced)
        msg = f"{n} player{'' if n == 1 else 's'} scored without a dot-ball source"
        return (("COMPLETED_FLAGGED", "⚠ " + msg) if already_completed
                else ("LIVE", "⏳ " + msg))
    if not espn_present:
        return ("COMPLETED_FLAGGED", f"⚠ unverified — single feed ({witness} only)")
    if not capi_present:
        # `capi_present` is really "the second WITNESS had a card" — cricapi by default, Cricbuzz
        # where the tour enables it. This is the fail-safe branch the Cricbuzz wiring depends on:
        # a Cricbuzz outage, an unresolvable Cricbuzz match id or an empty bridge all land here,
        # the match still scores off ESPN, and the row SAYS it was never cross-checked.
        return ("COMPLETED_FLAGGED",
                f"⚠ unverified — single feed (ESPN only, {witness} had no card)")
    if unresolved:
        n = len(unresolved)
        msg = f"pending recon approval ({n} player{'' if n == 1 else 's'})"
        return (("COMPLETED_FLAGGED", "⚠ " + msg) if already_completed
                else ("LIVE", "⏳ " + msg))
    return ("COMPLETED", "")

def _resolve_override_value(o, capi_pid, espn_pid):
    """Concrete value for an override: S1=cricapi feed, S2=ESPN feed, else the stored Manual value."""
    src = o.get("source")
    if src == "S1":
        return (capi_pid.get(o.get("pid"), {}) or {}).get(o.get("field"), o.get("value"))
    if src == "S2":
        return (espn_pid.get(o.get("pid"), {}) or {}).get(o.get("field"), o.get("value"))
    return o.get("value")  # Manual

def apply_recon_overrides(perf_by_pid, capi_pid, espn_pid, l1_gaps, match_key, overrides_idx,
                          sources_out=None, fields=None):
    """Mutate L1 perf dicts in `perf_by_pid` (keyed by pid) per APPROVED overrides for this
    match. A match-level seed ('use S1/S2 for the whole match') expands to every differing
    player's RECON_L1 fields; player-level overrides overlay (win) on the same (pid, field).
    Re-scoring after this recomputes all derived bonuses. Returns the resolved pids.

    `sources_out`, if given, is filled with {pid: {field: 'S1'|'S2'|'Manual'}} — WHICH source each
    override came from. That provenance is frozen alongside the settled value so the record
    explains itself: an audit can show not just what a player settled on but why. Passed as an
    out-param rather than a second return value so existing callers and tests keep working."""
    fields = fields or RECON_L1
    ovs = overrides_idx.get(match_key, [])
    if not ovs:
        return set()
    resolved = {}  # (pid, field) -> (value, source)
    for o in ovs:  # match-level seeds first
        if o.get("scope") == "match":
            src = o.get("source")
            feed = capi_pid if src == "S1" else espn_pid if src == "S2" else None
            if feed is None:
                continue
            for pid in l1_gaps:
                f = feed.get(pid)
                if f:
                    for field in fields:
                        # An ABSENT witness value must not overwrite a scored one. Cricbuzz writes
                        # None for a field it could not establish (HUN maidens, a failed dots
                        # derivation); `.get(field, 0)` would turn that into a real 0 and a
                        # whole-match "use S1" seed would silently zero it for every bowler.
                        v = f.get(field, 0)
                        if v is not None:
                            resolved[(pid, field)] = (v, src)
    for o in ovs:  # player-level overlays win over seeds
        if o.get("scope") == "player":
            pid, field = o.get("pid"), o.get("field")
            if pid and field:
                resolved[(pid, field)] = (_resolve_override_value(o, capi_pid, espn_pid),
                                          o.get("source"))
    applied = set()
    for (pid, field), (val, src) in resolved.items():
        d = perf_by_pid.get(pid)
        if d is not None and val is not None:
            d[field] = val
            applied.add(pid)
            if sources_out is not None:
                sources_out.setdefault(pid, {})[field] = src
    return applied

def l2_approved_pids(match_key, overrides_idx):
    """{pid: source} for L2 (official-revision) approvals on this match. source 'S2' = accept
    official cricsheet; anything else = keep the held provisional value."""
    return {o.get("pid"): o.get("source", "S2")
            for o in overrides_idx.get(match_key, []) if o.get("scope") == "l2"}

def player_recon_markers(unresolved, l2_pairs, l2_appr):
    """pid -> per-player marker for the draft UI, so it can flag exactly WHICH players aren't
    settled: '⏳ unreconciled' for an unresolved L1 (cricapi↔ESPN) gap, '⚠ official revision' for
    an unapproved cricsheet (L2) revision. Resolution-aware: an approved player isn't marked."""
    out = {pid: "⏳ unreconciled" for pid in unresolved}
    for pid in l2_pairs:
        if pid not in l2_appr:          # ANSWERED (S1 or S2) = decided; only silence is pending
            out[pid] = "⚠ official revision"
    return out

def reconciled_provisional(prov_pid, capi_pid, espn_pid, l1_gaps, match_key, overrides_idx,
                           fields=None):
    """The provisional cut with the human's APPROVED L1 overrides applied — i.e. the value people
    actually saw after reconciling witness↔ESPN. L2 must compare cricsheet against THIS, not the
    raw witness: an official figure that CONFIRMS an approved correction (e.g. you picked ESPN's
    2 wkts and cricsheet also says 2) is then silent, and only a genuine change from the shown
    value is flagged for approval. Returns a fresh dict (prov_pid is not mutated)."""
    recon = {pid: dict(v) for pid, v in prov_pid.items()}
    apply_recon_overrides(recon, capi_pid, espn_pid, l1_gaps, match_key, overrides_idx,
                          fields=fields)
    return recon

def build_recon_rows(match_key, label, mdate, tour, unresolved, capi_pid, espn_pid,
                     fields=None, witness="cricapi"):
    """ONE row per (player, differing field) — NO whole-match collapse. A match where neither
    feed is wholly right (some players' correct value is the witness, others ESPN — e.g. Match 23)
    can only be resolved per-player, and even a 'whole-match freeze' flags just the handful of
    players who actually differ. Only MATERIAL field diffs (see _l1_field_material) become rows.

    `witness` is stamped into the row's own text because the sheet's S1 column header is global
    across tours: a run where LPL is Cricbuzz-witnessed and MLC is cricapi-witnessed would
    otherwise put two different feeds' numbers under one header with nothing to tell them apart.
    A stored approval records only "S1", so the row has to say what S1 was."""
    fields = fields or RECON_L1
    rows = []
    for pid in unresolved:
        c, e = capi_pid.get(pid, {}), espn_pid.get(pid, {})
        for field in fields:
            cv, ev = c.get(field, 0), e.get(field, 0)
            if _l1_field_material(field, cv, ev):
                rows.append({"match_key": match_key, "tour": tour, "match": label, "date": mdate,
                             "pid": pid, "full": PID2DISP.get(pid, pid),
                             "param": RECON_LABEL.get(field, field), "field": field,
                             "s1": f"{cv} ({witness})" if witness != "cricapi" else cv,
                             "s2": ev, "tier": "player", "witness": witness})
    return rows

def _espn_hold_row(hold):
    """The NAMED Recon Review row for an ESPN card the gate refused past its retry budget.

    It is neither an identity question nor a per-player value question — it asks "is ESPN's card
    for this MATCH usable at all?", so it is keyed on the ESPN event (pid `espn:<eventId>`) and
    carries both numbers as its own evidence. Answering it either way is what stops the run failing:
    the gate's job is to make sure a HUMAN decided, not to guess on his behalf."""
    got, exp = hold.get("got"), hold.get("expected")
    short = (exp - got) if isinstance(exp, int) and isinstance(got, int) else "?"
    hrs = hold.get("hours") or 0
    return {"match_key": hold.get("match_key", ""), "tour": hold.get("tour", ""),
            "match": hold.get("match", ""), "date": hold.get("date", ""),
            "pid": f"espn:{hold.get('event', '')}", "full": "— WHOLE MATCH: ESPN card —",
            "param": "ESPN CARD",
            "s1": (f"ESPN ball-by-ball = {got} deliveries; ESPN's own scorecard = {exp} "
                   f"({short} missing, {hold.get('kind', '?')}) — held {hrs:.0f}h after start. "
                   f"Nothing has published or settled for this match."),
            "s2": ("S1 = keep holding (nothing publishes) · "
                   "S2 = score the short card anyway, permanently flagged"),
            "tier": "espn"}

# ── Cricbuzz as the L1 second witness ────────────────────────────────────────────────────────
def cb_witness_active():
    """Is Cricbuzz the second witness for the tour currently being processed?"""
    return bool(CB_SERIES and _cricbuzz is not None and _cb_bridge is not None
                and (CB_STORE or {}).get("bridge"))

def cb_match_perf(mdate, teams, espn_perf, fresh=False):
    """Cricbuzz's card for one match, keyed by the SAME pid space as every other feed.

    Returns (perf_by_pid, note, diag). `perf_by_pid` is None whenever Cricbuzz cannot serve as a
    witness for this match — and `note` then says WHY, in words that go into the sheet and the
    log. It is never {} for a failure: {} would read as "Cricbuzz saw nobody play", which is the
    one thing this project keeps paying for.

    FAIL-SAFE IS THE WHOLE CONTRACT. Every failure mode — module missing, series unset, match id
    not uniquely resolvable, HTTP 204/403/timeout, an unreadable card, an empty bridge — returns
    None and lets the caller score off ESPN exactly as it does today, with the row flagged
    "single feed (ESPN only, cricbuzz had no card)". Cricbuzz can only ever ADD a cross-check;
    it can never take a match away.

    IDENTITY IS NEVER GUESSED HERE. A Cricbuzz row enters the witness view only if
    registry/cricbuzz_bridge.json resolves its cricbuzz id to a cricinfo id at tier >= 1, which
    was DERIVED from what the two feeds independently observed the same person do (performance
    fingerprint + dismissal join), never from a name. Everyone else is counted as unbridged; the
    subset whose contribution ESPN structurally cannot supply (a run-out fielder — ESPN populates
    dismissal.fielder 0/19 for run outs) is routed to "Needs Cricinfo ID", because that is an
    identity question and identity never appears in the Recon tab.

    ⛔ THE JOIN IS TO THE PID **ESPN'S OWN ROW** GOT, NOT TO `ci:<bridged id>`. Those are not the
    same key, and assuming they were manufactured a phantom Recon row on the very first pair of
    real matches: ESPN athlete.id 1364327 (V Lahiru) on LPL ev1537342 is resolved by
    resolve_perf_pid to ci:784375, because 1364327 is not a registry key and the registry carries
    that spelling under a DIFFERENT cricinfo id (people.csv has both 784375 "CBRLS Kumara" and
    1364327 "V Lahiru"). Keying Cricbuzz on ci:1364327 put the same human under two pids, and the
    union in compute_l1_gaps then reported him as "present in cricbuzz only" — an IDENTITY artifact
    posted to the Recon tab, which is precisely what rule E forbids. So the athlete-id → pid map is
    taken from ESPN's rows for THIS match, which is a pure id-to-id join: Cricbuzz's cricbuzz id →
    (derived bridge) → cricinfo id → (ESPN's athlete.id, which IS the cricinfo id) → the pid ESPN's
    row already carries. No name is consulted at any step. A duplicate-cricinfo-id question like
    Kumara's is left as the identity question it is, not smuggled in as a value disagreement."""
    if not CB_SERIES:
        return None, "", {}
    if _cricbuzz is None or _cb_bridge is None:
        return None, f"cricbuzz module unavailable ({_CB_IMPORT_ERR or 'not importable'})", {}
    if not CB_STORE:
        return None, "cricbuzz bridge store is empty — no derived cricbuzz→cricinfo pairs", {}
    diag = {}
    try:
        cb_mid = _cricbuzz.resolve_match_id(CB_SERIES, mdate, teams, fresh=fresh)
    except Exception as e:                    # CricbuzzUnavailable / ParseError / anything else
        return None, f"cricbuzz series {CB_SERIES} unreachable ({e})", {}
    if not cb_mid:
        # resolve_match_id returns None rather than guessing (0 hits, or a same-day double-header
        # between the same two sides). Measured 22/22 unique with 0 collisions on LPL 2026.
        return None, f"no unique cricbuzz match for {mdate} {teams} in series {CB_SERIES}", {}
    diag["cb_match"] = cb_mid
    try:
        m = _cricbuzz.parse_match(cb_mid, fresh=fresh)
    except Exception as e:
        return None, f"cricbuzz match {cb_mid}: {e}", diag
    # ESPN athlete.id -> the pid ESPN's own row landed on. Built from THIS match only, so the two
    # sides of the comparison are literally the same rows.
    ci2pid = {}
    for v in (espn_perf or {}).values():
        eid = str(v.get("espn_id") or "")
        if eid:
            ci2pid.setdefault(eid, resolve_perf_pid(v) or "")
    by_pid, unbridged, needs, offcard = {}, [], [], []
    for p in m.perf.values():
        res = _cb_bridge.resolve(CB_STORE, p["cb_id"], _cb_bridge.PURPOSE_CROSSCHECK)
        if res.status != _cb_bridge.OK:
            unbridged.append(p)
            # The one residual that COSTS points: a player whose only contribution is fielding a
            # run out. ESPN cannot supply him at all, so nobody else will ever raise him.
            if (p.get("runouts") or 0) or (p.get("catches") or 0) or (p.get("stumpings") or 0):
                needs.append((p, res))
            continue
        pid = ci2pid.get(str(res.cricinfo_id))
        if not pid:
            # Cricbuzz bridged him, but ESPN's card for this match carries no row with that
            # athlete id. Accept `ci:<id>` ONLY if the registry already knows that key — then
            # "present in cricbuzz only (ESPN has no line)" is a nameable, answerable VALUE row
            # (the class that published 4 pts against 110 earned). If the registry doesn't know
            # it either, we cannot name him, so it is an identity gap and it stays out of Recon.
            cand = f"ci:{res.cricinfo_id}"
            if cand in PID2DISP:
                pid = cand
            else:
                offcard.append((p, res))
                continue
        if pid not in by_pid:
            by_pid[pid] = p
    for p, res in offcard:
        NEEDS_CRICINFO.append({
            "player": p.get("name") or f"cricbuzz player {p['cb_id']}",
            "current_pid": f"cb:{p['cb_id']}", "tour": CURRENT_TOUR,
            "team": canon_team(p.get("team", "")) or "",
            "closest_guess": (f"bridged to cricinfo id {res.cricinfo_id} (tier {res.tier}) but "
                              f"neither ESPN's card for cb match {cb_mid} nor the registry carries "
                              f"that id — https://www.espncricinfo.com/cricketers/x-{res.cricinfo_id}")})
    diag.update({"bridged": len(by_pid), "unbridged": len(unbridged), "off_card": len(offcard),
                 "cb_players": len(m.perf), "warnings": list(m.warnings),
                 "dots_source": m.dots_source, "maidens_source": m.maidens_source})
    for p, res in needs:
        NEEDS_CRICINFO.append({
            "player": p.get("name") or f"cricbuzz player {p['cb_id']}",
            "current_pid": f"cb:{p['cb_id']}", "tour": CURRENT_TOUR,
            "team": canon_team(p.get("team", "")) or "",
            "closest_guess": (f"cricbuzz-only fielding credit (ct {p.get('catches', 0)} / "
                              f"st {p.get('stumpings', 0)} / ro {p.get('runouts', 0)}) on "
                              f"cb match {cb_mid}; bridge says {res.status} ({res.detail}). "
                              f"Run: python3 registry/cricbuzz_bridge.py --derive")})
    note = (f"cricbuzz cb{cb_mid}: {len(by_pid)}/{len(m.perf)} bridged"
            + (f", {len(unbridged)} unbridged" if unbridged else "")
            + (f", {len(offcard)} bridged-but-not-on-ESPN's-card" if offcard else ""))
    if not by_pid:
        # Cold start: the bridge is derived FROM played matches, so a season's first fixtures have
        # 0% coverage by construction (measured: tier-1 coverage 0% entering matches 1-2, 48%
        # entering 3, 88-100% from 4 on). Not a failure — but it IS "no witness", and it must say
        # so rather than present an empty dict as agreement.
        return None, note + " — no bridged player, cricbuzz cannot witness this match", diag
    return by_pid, note, diag

# ── ESPN-derived match list (a tour with NO cricapi series) ───────────────────────────────
# The match LIST has always come from cricapi's series_info, so a tour without a cricapi series id
# aborted at the series_info guard and scored NOTHING — no tab, no points, silently. That is not a
# hypothetical: CPL 2026 came in through the keyless ESPN path (`cricapi_series: ""`), its first
# match was played on 7 Aug 2026, and the draft has been offering all 35 fixtures as draftable
# contests the whole time. The scorecards were always reachable — ESPN carries the league in full —
# it was only the LIST of matches that had a single hard-wired source.
#
# So build the list from ESPN too. The rest of run_tour is unchanged: the shape returned here is the
# cricapi matchList shape, and every consumer (is_fmt / near_today / is_over / the pending-XI pass)
# reads the same keys. The one deliberate omission is `id` — the CRICAPI match id — which we do not
# have and must not invent; the scoring loop already guards `if m.get("id")` and falls through to
# the ESPN scorecard, which is exactly the intended single-source behaviour.
# Our format code -> the matchType token is_fmt matches on. They are NOT the same vocabulary:
# is_fmt looks for "t20"/"hundred"/"odi" substrings, so "HUN" would match nothing.
_FMT_MATCHTYPE = {"HUN": "hundred", "T20": "t20", "ODI": "odi", "TEST": "test"}

ESPN_LIST_BACK_STOP = 8      # consecutive empty days that end the backward scan
ESPN_LIST_MAX_BACK  = 150    # hard cap on how far back we look
ESPN_LIST_FORWARD   = 14     # days ahead to list upcoming fixtures (for the announced-XI pass)

def _espn_event_to_match(e):
    """One ESPN scoreboard event -> one cricapi-matchList-shaped dict (minus the cricapi id)."""
    comp = (e.get("competitions") or [{}])[0]
    teams = [c.get("team", {}).get("displayName", "") for c in comp.get("competitors", [])]
    if len(teams) != 2 or not all(teams):
        return None
    state = (((e.get("status") or {}).get("type") or {}).get("state") or "").lower()
    dt = e.get("date") or ""
    return {
        "name": e.get("name") or " v ".join(teams),
        # class.eventType ("T20"/"ODI") is the reliable format field. The event NAME of a league
        # fixture is just "Team A v Team B" with no format in it, so the name-sniff fallback in
        # is_fmt() would reject every match — this is why tour_sync had to read eventType too.
        # class.eventType is the reliable format field — WHEN ESPN SETS IT. It is BLANK on the
        # Hundred Women's series (1521193) while the Men's (1521176) reports "T20", so is_fmt fell
        # through to sniffing the event NAME, and a league fixture is just "A v B" with no format
        # word in it. Result: not one Women's match was admitted — 0 of 34 "completed" — while the
        # Men's ran fine. Falling back to the TOUR'S OWN declared format is not a guess: tours.json
        # says what this competition is, and every event inside that series is that format.
        # ⚠ Emit the token is_fmt actually SPEAKS. CURRENT_FMT for the Hundred is "HUN", and
        # is_fmt tests for "t20" or "hundred" — neither is a substring of "hun", so a raw
        # lowercase of the format silently rejected EVERY match: the Women's series reported
        # "0/34 completed" and wrote an empty tab, while the Men's survived only because ESPN
        # happens to set eventType="T20" there. _FMT_MATCHTYPE keeps the two vocabularies aligned.
        "matchType": (((comp.get("class") or {}).get("eventType") or "").lower()
                      or _FMT_MATCHTYPE.get((CURRENT_FMT or "").upper(),
                                            (CURRENT_FMT or "").lower())),
        "teams": teams,
        "date": dt[:10],
        "dateTimeGMT": dt,
        # ESPN's state is a genuine improvement on cricapi's matchEnded, which sat False for DAYS on
        # LPL/Hundred. "post" means ESPN has called the result.
        "matchStarted": state in ("in", "post"),
        "matchEnded": state == "post",
        "espn_event": e.get("id"),
    }

def espn_match_list(tour, squad_team_names):
    """Scan the ESPN league scoreboard day by day and return this tour's fixtures."""
    if not ESPN_SERIES:
        return []
    today = date.today()
    try:
        end_d = date.fromisoformat(tour.get("ends") or "")
    except ValueError:
        end_d = today + timedelta(days=ESPN_LIST_FORWARD)
    want = team_key(squad_team_names)
    out, empty_run = {}, 0

    def scan(d):
        # Past days are immutable, so cache them; today/tomorrow must be fresh or a live match's
        # state would be frozen at whatever it was the first time we looked (the same reason
        # espn_event_id passes cache=False).
        fresh = d >= today - timedelta(days=1)
        sb = espn_get("scoreboard", cache=not fresh, dates=d.strftime("%Y%m%d"))
        hits = 0
        for e in sb.get("events", []):
            m = _espn_event_to_match(e)
            # A league id can carry more than the squad we hold (e.g. a women's edition under the
            # same competition). Admit only fixtures between teams we actually have a squad for.
            if not m or not (team_key(m["teams"]) & want):
                continue
            out[e.get("id") or f"{m['date']}|{m['name']}"] = m
            hits += 1
        return hits

    # backwards from the anchor until the competition demonstrably hasn't started
    # Bound the scan by the tour's KNOWN span when we have one. Without this the back-scan walks up
    # to 150 uncached days per tour at a rate limit — minutes of pure network before a single match
    # is scored, four times over. `starts` is honoured when tours.json carries it; otherwise the
    # empty-day stop still applies.
    try:
        floor_d = date.fromisoformat(tour.get("starts") or "")
    except ValueError:
        floor_d = None
    d = min(today, end_d)
    for _ in range(ESPN_LIST_MAX_BACK):
        empty_run = 0 if scan(d) else empty_run + 1
        if empty_run >= ESPN_LIST_BACK_STOP:
            break
        d -= timedelta(days=1)
        if floor_d and d < floor_d:
            break
    # and forwards for upcoming fixtures
    d = min(today, end_d) + timedelta(days=1)
    stop = min(today + timedelta(days=ESPN_LIST_FORWARD), end_d)
    while d <= stop:
        scan(d)
        d += timedelta(days=1)
    return sorted(out.values(), key=lambda m: (m.get("dateTimeGMT") or "", m.get("name") or ""))

def run_tour(tour):
    """Process ONE tour (its own cricapi+ESPN series + squad list) and write its tab."""
    global WC_SERIES, ESPN_SERIES, SQUADS_JSON, GSHEET_TAB, CURRENT_TOUR, CURRENT_FMT, CB_SERIES
    WC_SERIES = tour["cricapi_series"]
    ESPN_SERIES = tour.get("espn_series", "")
    SQUADS_JSON = tour.get("squads_path", "")
    GSHEET_TAB = tour["tab"]
    CURRENT_TOUR = tour["name"]
    CURRENT_FMT = (tour.get("format") or "T20").upper()
    # ⛔ ONE-WAY, AND DELIBERATELY IN A COMMITTED FILE. Setting `cricbuzz_series` makes Cricbuzz
    # this tour's S1, and an approval stored in registry/recon_overrides.json records only "S1" —
    # not which feed S1 was. So flipping it on a tour that already has approved S1 player
    # overrides retroactively changes what those approvals resolve to. That is a settled-points
    # mover, so it is SHOUTED here rather than discovered later (see the warning below).
    CB_SERIES = str(tour.get("cricbuzz_series") or "").strip()
    out_csv = tour.get("out_csv", OUT)
    print(f"=== Tour: {tour['name']}  ->  tab '{GSHEET_TAB}' ===", file=sys.stderr)
    # WITNESS BANNER. Which feed is S1 for this tour is the single most load-bearing fact about
    # every Recon row it produces, and it used to be implicit ("cricapi, always").
    L1_WITNESS = "cricbuzz" if cb_witness_active() else "cricapi"
    L1_FIELDS = RECON_L1_CB if L1_WITNESS == "cricbuzz" else RECON_L1
    if CB_SERIES and L1_WITNESS != "cricbuzz":
        print(f"  ⚠ cricbuzz_series {CB_SERIES} is set but Cricbuzz is NOT witnessing this tour "
              f"({'module not importable: ' + _CB_IMPORT_ERR if _cricbuzz is None or _cb_bridge is None else 'bridge store empty'})"
              f" — falling back to cricapi. Matches still score off ESPN.", file=sys.stderr)
    print(f"  L1 second witness: {L1_WITNESS} ({len(L1_FIELDS)} fields cross-checked)",
          file=sys.stderr)
    if L1_WITNESS == "cricbuzz":
        _s1_prior = sum(1 for ovs in RECON_OVERRIDES.values() for o in ovs
                        if o.get("scope") in ("player", "match") and o.get("source") == "S1")
        if _s1_prior:
            print(f"  ⚠ {_s1_prior} approved S1 override(s) exist in the ledger from the cricapi "
                  f"era. 'S1' is positional — those now resolve to CRICBUZZ's number wherever "
                  f"their match_key belongs to this tour. Re-check them if any settled points "
                  f"move (registry/recon_overrides.json).", file=sys.stderr)

    squads = load_squads()
    # map normalized full team name -> short code (plus a "Women"-stripped variant,
    # since feeds vary between "Sri Lanka" and "Sri Lanka Women")
    name2short = {norm(v["name"]): k for k, v in squads.items()}
    # Strip EITHER gender qualifier, not just "Women". This stripped only "women" — a mirror
    # omission that cost the entire Hundred Men's competition: cricapi fed men's teams unsuffixed
    # ("London Spirit") so nothing ever exercised the men's side, but ESPN feeds "London Spirit
    # (Men)", so once the match list came from ESPN every men's team resolved to NO squad code and
    # the tour wrote ZERO rows — after scoring all 32 matches and bridging 21-22 players in each.
    # _GENDER_QUAL is the same pattern team_key already uses, so the two can no longer disagree.
    strip_women = lambda s: norm(_GENDER_QUAL.sub("", s or ""))
    name2short_stripped = {strip_women(v["name"]): k for k, v in squads.items()}
    def short_of(team_full):
        return name2short.get(norm(team_full)) or name2short_stripped.get(strip_women(team_full))

    # Full run: always fresh (detect newly-ended matches). Frequent tick: cache 2h
    # so the every-5-min ticks don't spend the cricapi daily budget.
    # On-demand fetches series_info FRESH (a human is asking mid-match — detect the just-started
    # game); the every-5-min tick still caches 2h to protect the cricapi daily budget.
    # MATCH LIST SOURCE. cricapi is needed for the list ONLY while it is still the second witness.
    # Once a tour names a cricbuzz_series, Cricbuzz is the witness and ESPN is the scorecard, so a
    # cricapi key buys nothing — yet a blank/expired key still aborted the whole tour at the
    # series_info guard below ("series_info fetch failed or empty"), taking LPL and both Hundred
    # competitions down with it even though every match was fully available from ESPN + Cricbuzz.
    # Prefer ESPN's list for those, and fall back to cricapi only if ESPN yields nothing.
    _cb_witness = bool((tour.get("cricbuzz_series") or "").strip())
    if _cb_witness and ESPN_SERIES:
        matches = espn_match_list(tour, [v["name"] for v in squads.values()])
        if matches:
            print(f"  match list: {len(matches)} fixture(s) from ESPN series {ESPN_SERIES} "
                  f"(cricbuzz is the witness — cricapi not consulted)", file=sys.stderr)
        else:
            print(f"  match list: ESPN series {ESPN_SERIES} returned no fixtures — "
                  f"falling back to cricapi", file=sys.stderr)
    else:
        matches = []
    if not matches and WC_SERIES:
        info = api("series_info", cache=(FREQUENT and not ON_DEMAND),
                   ttl=(7200 if FREQUENT else None), id=WC_SERIES)
        matches = info.get("data", {}).get("matchList", [])
        # Guard: if cricapi failed/returned nothing, ABORT before touching the sheet
        # (otherwise we'd clear it and write an empty table — wiping good data).
        if info.get("status") != "success" or not matches:
            sys.exit("series_info fetch failed or empty — aborting; sheet left unchanged.")
    elif not matches:
        # ESPN-only tour: the fixture list comes from the league scoreboard instead.
        matches = espn_match_list(tour, [v["name"] for v in squads.values()])
        # Same guard, same reason — an empty list must never be allowed to blank a written tab.
        if not matches:
            sys.exit(f"no ESPN fixtures found for series {ESPN_SERIES or '(unset)'} — "
                     f"aborting; sheet left unchanged.")
        print(f"  match list: {len(matches)} fixture(s) from ESPN series {ESPN_SERIES} "
              f"(no cricapi series — single-source tour)", file=sys.stderr)
    # Normalize every feed team name to the squad's CANONICAL name up front, so all downstream
    # consumers (name2short squad mapping, match labels, team_key, ESPN/cricsheet lookup) see one
    # consistent name. Two feed quirks this fixes: (a) franchise renames — cricapi feeds LPL's 2025
    # names (Marvels/Falcons/Strikers) vs the 2026 squad/ESPN names (Gallants/Royals/Kaps) — folded
    # via the central team-alias map; (b) gender suffixes — cricapi feeds The Hundred women's teams
    # as "MI London Women" while the squad is "MI London" — resolved via short_of's 'Women'-stripping.
    for _m in matches:
        if not _m.get("teams"):
            continue
        fixed = []
        for t in _m["teams"]:
            t = canon_team(t)                                   # fold franchise renames
            sh = short_of(t)                                    # gender-agnostic squad lookup
            fixed.append(squads[sh]["name"] if sh else t)       # -> squad's canonical name
        _m["teams"] = fixed
    # Format filter — a tour can mix formats, so we keep only the matches in the
    # running tour's format (CURRENT_FMT: "T20" default, or "ODI"). cricapi sometimes
    # leaves matchType null (seen on ODIs!), so trust matchType when present, else fall
    # back to the match name.
    def is_fmt(m):
        mt = (m.get("matchType") or "").lower()
        if CURRENT_FMT == "ODI":
            if mt:
                return "odi" in mt
            nm = (m.get("name") or "").lower()
            return "odi" in nm and "t20" not in nm and "test" not in nm
        # T20 (default) AND The Hundred (CURRENT_FMT "HUN"): both take this non-ODI branch for
        # MATCH ADMISSION (cricapi labels the Hundred's matchType variably — "hundred" or "t20").
        # The SCORER still differs — score() routes HUN to _score_hundred (no SR/econ/maiden).
        if mt:
            return "t20" in mt or "hundred" in mt
        nm = (m.get("name") or "").lower()
        return ("t20" in nm or "hundred" in nm) and "odi" not in nm and "test" not in nm
    today = date.today()
    def near_today(m):
        for ds in date_variants(m.get("date", "")):
            try:
                if abs((date.fromisoformat(ds) - today).days) <= 1:
                    return True
            except ValueError:
                pass
        return False
    # A match is OVER when cricapi flips matchEnded — but that flag is UNRELIABLE on some feeds
    # (LPL 2026: every played match sat matchStarted=True / matchEnded=False for days, status text
    # stuck on "Match starts at ..."). So we ALSO treat a started match as over once enough real
    # time has passed since its scheduled start for the game to have finished. Without this
    # fallback a completed-but-unflagged match is only scored "live" while within near_today, then
    # silently vanishes — its points never finalize (the root cause of "LPL points not updating").
    OVER_HRS = 12 if CURRENT_FMT == "ODI" else 8   # T20 ~3.5h / ODI ~8h, plus a rain/delay buffer
    def hours_since_start(m):
        dt = m.get("dateTimeGMT") or ((m.get("date") + "T00:00:00Z") if m.get("date") else "")
        try:
            start = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return None
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - start).total_seconds() / 3600.0
    def is_over(m):
        if m.get("matchEnded"):
            return True
        if not m.get("matchStarted"):
            return False
        h = hours_since_start(m)
        return h is not None and h >= OVER_HRS
    ended = [m for m in matches if is_fmt(m) and is_over(m)]
    # LIVE = started, genuinely still in play (not yet over by time), near today. Scored
    # in-progress from cricapi (fresh) + ESPN and marked LIVE; superseded by the ended-path scoring
    # (cricsheet when it posts, else the frozen provisional) once the match is over.
    live = [m for m in matches if is_fmt(m) and m.get("matchStarted")
            and not is_over(m) and near_today(m)]
    cs_idx = load_cricsheet_index(CRICSHEET_DIR, tour.get("gender", "female"))
    print(f"{len(ended)}/{len(matches)} completed, {len(live)} in-progress | cricsheet {tour.get('gender','female')} matches indexed: {len(cs_idx)}", file=sys.stderr)

    cols = ["Match", "Date", "Team", "Player ID", "Full Name", "Role", "Played",
            "Runs", "Balls", "4s", "6s", "SR", "Dismissal",
            "Overs", "Maidens", "Dots", "Runs Conceded", "Wickets", "Econ",
            "Catches", "Stumpings", "Run Outs",
            "Pts Bat", "Pts Bowl", "Pts Field", "Pts SR", "Pts Econ", "Pts XI",
            "Fantasy Points", "Source", "In Squad List", "Bat Order",
            "L1 Recon", "L2 Recon", "Match Status", "Recon Flag", "Player Recon",
            # Recon State is the SECOND axis, independent of Match Status. A match can be
            # COMPLETED while L2 is still open; encoding both in one column is what made
            # COMPLETED_FLAGGED mean four different things. Points Delta is the settled-vs-live
            # movement for this player — 0/blank when nothing moved.
            "Recon State", "Points Delta"]
    rows = []
    n_cs = n_espn = n_api = 0
    _key = lambda x: x.get("dateTimeGMT", x.get("date", ""))
    to_score = [(m, False) for m in sorted(ended, key=_key)] + [(m, True) for m in sorted(live, key=_key)]
    for mi, (m, is_live) in enumerate(to_score, 1):
        teams = m.get("teams", [])
        mdate = m.get("date", "")
        label = f"Match {mi} — " + " v ".join(name2short.get(norm(t), t) for t in teams)

        # cricapi scorecard (used as fallback + an independent cross-check). Fetch it FRESH
        # (no-persist) while the match isn't authoritatively final — in-play, OR over-by-time but
        # cricapi hasn't confirmed matchEnded (LPL's stuck flag), so its card may still be settling.
        # Once cricapi flips matchEnded we cache the immutable final; a cricsheet card, when posted,
        # overrides either source.
        fetch_fresh = is_live or not m.get("matchEnded")
        try:
            if not m.get("id") and WC_SERIES:
                # cricapi listed the fixture but gave no match id, so no scorecard can ever be
                # requested for it. Silent until now: api_perf just came back {} and the match
                # quietly ran single-source off ESPN. Say it out loud — "cricapi has no data" and
                # "we never asked cricapi" are different problems with different fixes.
                # (On an ESPN-only tour there is no cricapi id BY DESIGN — not a defect, so no noise.)
                print(f"  {label}: cricapi listed this fixture with NO match id — "
                      f"no scorecard can be fetched; running single-source", file=sys.stderr)
            api_perf = {k: v for k, v in parse_match(m["id"], live=fetch_fresh).items() if v["played"]} if m.get("id") else {}
            if m.get("id") and not api_perf:
                print(f"  {label}: cricapi id {m['id']} returned NO player data "
                      f"(fresh={fetch_fresh})", file=sys.stderr)
        except Exception:
            api_perf = {}

        # Source priority:
        #   cricsheet (official, exact everything) — when posted, overrides all
        #   else cricapi scorecard (PRIMARY base) + ESPN/cricinfo for dot-balls & the +4 XI
        #        (cricapi's scorecard tracks the official card best; ESPN fills what it lacks)
        #   else cricapi alone (limited: no dots/XI) if ESPN is unavailable
        cs_path = next((cs_idx[(d, team_key(teams))] for d in date_variants(mdate)
                        if (d, team_key(teams)) in cs_idx), None)
        # ALWAYS pull ESPN ball-by-ball when available — it feeds dots/XI in the provisional
        # cut AND the L1/L2 reconciliation columns (we compare every source against the
        # scorer, even once cricsheet is the official source). ESPN is free/unmetered.
        # Fetch ESPN FRESH (bypass cache) for provisional matches — their ESPN data is still
        # settling (roster/stats finalize over hours), so a cached mid-match snapshot would
        # freeze an incomplete XI and drop in-XI players who hadn't batted/bowled yet (e.g.
        # Shubham Ranjane). Cricsheet-settled matches keep the cache (ESPN is recon-only there).
        espn_fresh = not cs_path
        espn_perf, super_over, team_map = {}, False, {}
        ev = espn_event_id(mdate, teams)
        if ev:
            espn_perf, super_over = parse_espn(ev, fresh=espn_fresh)
            espn_perf = {k: v for k, v in espn_perf.items() if v["played"]}
            team_map = espn_team_map(ev, fresh=espn_fresh)
        # A REFUSED ESPN card must be NAMED, never merely absent. Stamp this match onto the hold
        # (parse_espn only knows an event id) and decide its phase from the MATCH clock. Two phases,
        # on purpose: within the budget it is a quiet ⏳ retry, because every transient cause heals
        # in minutes and a row that appears and vanishes teaches the owner to ignore the tab; past
        # the budget it becomes a ⛔ named row he must answer, and the run goes red.
        hold = ESPN_HOLDS.get(str(ev)) if ev else None
        if hold is not None:
            _hrs = hours_since_start(m)
            hold.update({"tour": CURRENT_TOUR, "match": label, "date": mdate, "hours": _hrs,
                         "match_key": match_key_of(mdate, teams), "event": str(ev)})
            hold["escalated"] = _espn_hold_escalated(_hrs, is_live, hold.get("approved"),
                                                     kind=hold.get("kind"))
            _rk = (hold["match_key"], f"espn:{ev}", "ESPN CARD")
            if hold["escalated"] and not FREQUENT and _rk not in RECON_ACK:
                RECON_REVIEW.append(_espn_hold_row(hold))
            print(f"  {label}: ESPN card {'ESCALATED' if hold['escalated'] else 'held'} — "
                  f"{hold.get('got')} of {hold.get('expected')} deliveries "
                  f"({hold.get('kind')})", file=sys.stderr)
        cs_perf = ({k: v for k, v in parse_cricsheet(cs_path)[0].items() if v["played"]}
                   if cs_path else {})
        # AUTOPILOT DATA GUARD: a match can be over (by cricapi's flag OR our time fallback) yet have
        # NO scorecard in ANY source — cricsheet not posted, cricapi returns "scorecard not found",
        # ESPN event unmapped (the LPL 2026 state on 21 Jul). Emitting it anyway would show a
        # misleading "COMPLETED" match where every player scores only the +4 XI bonus. Skip it and
        # re-check next run instead; it auto-fills the moment cricsheet, cricapi OR ESPN posts data.
        _sf = ("r", "b", "balls", "w", "catches", "stumpings", "runouts", "4s", "6s")
        _has = lambda d: any(any(p.get(f) for f in _sf) for p in d.values())
        if not cs_perf and not _has(api_perf) and not _has(espn_perf):
            # Distinguish the two reasons this looks identical from here. "Nobody has posted a card
            # yet" is normal and self-healing; "ESPN posted one and we REFUSED it" is a held match
            # that will never self-heal if the card is permanently short — and on an ESPN-only tour
            # this `continue` is the line that makes it invisible. The hold above already queued the
            # named row; say which case this is in the log too.
            print(f"  {label}: " + (
                f"ESPN card HELD ({hold.get('got')}/{hold.get('expected')} deliveries) and no other "
                f"source — NOT published; see the 'ESPN CARD' row in the Recon tab"
                if hold is not None else
                "no scorecard in any source yet (cricsheet/cricapi/ESPN all empty) — skipped; "
                "will retry next run"), file=sys.stderr)
            continue
        # Record who turned out, BEFORE the source branches, from every card we hold. The identity
        # queue uses this to tell a pre-debut squad name (no id exists anywhere; nothing to ask)
        # from a man who PLAYED and still has no id (a real question).
        for _src in (cs_perf, espn_perf, api_perf):
            note_appearance(_src)
        if cs_path:
            perf = cs_perf
            n_cs += 1; dots_final = True; status = "cricsheet · official"
        elif espn_perf:
            # Not from cricsheet yet -> dots are single-sourced from ESPN with NO validator
            # (cricapi has no dots, cricsheet not posted). Flag the whole row PROVISIONAL so
            # it's clear these numbers may be revised once cricsheet posts (lags ~1-5 days),
            # which then overwrites ESPN dots with exact figures.
            # cricapi is the PRIMARY base, but when its scorecard is EMPTY (LPL 2026: cricapi never
            # populates a match it hasn't flagged as started) fall back to ESPN's FULL scorecard so
            # real batting/bowling points flow — otherwise everyone scores just the +4 XI bonus.
            perf = api_perf if api_perf else espn_perf
            n_espn += 1; dots_final = True
            base_src = "cricapi + ESPN dots/XI" if api_perf else "ESPN scorecard (cricapi empty)"
            status = (base_src + " · ⏳ provisional (dots unverified, awaiting cricsheet)"
                      + (" · super-over excl" if super_over else ""))
        else:
            perf = api_perf
            n_api += 1; dots_final = False
            status = "cricapi · limited (no dots/XI — ESPN unavailable) · ⏳ provisional (awaiting cricsheet)"

        # Per-pid views of each RAW source, for L1 (witness vs ESPN, feed-against-feed).
        # NOTE: `_by_pid` is a strict id-only index and is correct HERE — L1 compares two raw
        # feeds. It is NOT correct for rebuilding the provisional cut; that needs the same
        # squad-anchored matcher emit used (see build_provisional_cut, called below once
        # `team_players` exists).
        capi_pid, espn_pid, cs_pid = _by_pid(api_perf), _by_pid(espn_perf), _by_pid(cs_perf)

        # ── The L1 SECOND WITNESS. cricapi unless the tour set `cricbuzz_series`. ──────────────
        # Placed HERE, after the no-data guard and after _by_pid, for two reasons:
        #   (a) a match nobody has a card for cannot be cross-checked either, and Cricbuzz's
        #       scorecard page is ~600KB — fetching one for a fixture we just `continue`d past is
        #       pure waste against an undocumented endpoint;
        #   (b) cb_match_perf runs resolve_perf_pid over ESPN's rows to find the pid each one got,
        #       and resolve_perf_pid LEARNS (it writes id-anchored spellings into ALIAS2PID). Doing
        #       that before _by_pid would change the order in which cricapi/ESPN/cricsheet rows are
        #       resolved on a CB tour. Running after _by_pid makes those calls pure cache hits, so
        #       enabling Cricbuzz cannot perturb anyone else's identity resolution.
        # Also skipped on a LIVE match and on the 5-minute FREQUENT tick: the live branch below
        # already forces status LIVE and clears every per-player recon marker (mid-match feed gaps
        # settle by the end), so a fetch there would cost a page per match per tick and buy nothing.
        cb_pid, cb_note, cb_diag = None, "", {}
        if cb_witness_active() and not is_live and not FREQUENT:
            cb_pid, cb_note, cb_diag = cb_match_perf(mdate, teams, espn_perf, fresh=espn_fresh)
            CB_DIAG[match_key_of(mdate, teams)] = {"note": cb_note, **cb_diag}
            if cb_note:
                print(f"  {label}: {cb_note}", file=sys.stderr)
            for _w in cb_diag.get("warnings", []):
                print(f"  {label}: cricbuzz warning — {_w}", file=sys.stderr)
        # Whichever feed is actually witnessing THIS match. A Cricbuzz tour whose card is
        # unavailable falls back to cricapi's dict (usually empty on the franchise leagues), which
        # is exactly the "single feed (ESPN only)" flag — never a silent pass.
        witness = "cricbuzz" if cb_pid is not None else "cricapi"
        wit_fields = RECON_L1_CB if witness == "cricbuzz" else RECON_L1
        # THE S1 SLOT. Cricbuzz's rows are already pid-keyed (cb_match_perf resolves them through
        # the derived bridge), so they drop straight in. cricapi's own dict is untouched and still
        # feeds the BASE card — Cricbuzz replaces cricapi only as the L1 WITNESS, never as a
        # scoring source: nothing here writes a Cricbuzz number onto a scored row except through
        # an approved S1 override, which is the same sanctioned route cricapi always had.
        wit_pid = cb_pid if cb_pid is not None else capi_pid
        if witness == "cricbuzz":
            status += f" · cricbuzz cross-checked ({cb_diag.get('bridged', 0)} players)"
        elif CB_SERIES:
            # Configured but not witnessing THIS match. Say it on the row, not just in the log —
            # a silently-missing cross-check is indistinguishable from a passing one.
            status += " · ⚠ cricbuzz unavailable" + (f" ({cb_note})" if cb_note else "")

        team_players = []
        for tname in teams:
            short = name2short.get(norm(tname))
            if short:
                team_players += [(short, n, r) for n, r in squads[short]["players"]]
        # WHERE EACH SLOT CAME FROM -> the 'In Squad List' column. That column read "Y" on
        # 2228/2228 rows across the three ESPN-sourced tabs (14 Aug 2026) and therefore carried no
        # information at all: its only "N" producer was the `leftover` emit, which the pid-minting
        # change killed, while the auto-add below PERSISTS every feed-discovered player into
        # new_players.json — so the loop just above re-injects him as an ordinary squad member on
        # the very next run and he reads "Y" forever after. A column that cannot say no is not a
        # check. It now records the slot's PROVENANCE, which is the thing a human actually wants to
        # know when a name looks wrong: 'Y' = the announced squad file, 'new' = a human typed him
        # into Needs Review, 'auto' = only the feed ever said he was on this side, 'N' = he is on
        # no list anywhere (the un-attributable review row, which is not scored).
        slot_src = {}
        # Inject sheet-driven NEW players (marked "New" or auto-added on a past run) who belong to
        # this match's teams + tour — so they're scored without a build_registry rebuild.
        match_shorts = {name2short.get(norm(t)) for t in teams} - {None}
        have_names = {norm(n) for _, n, _ in team_players}
        for e in NEW_PLAYERS_DATA.get("players", []):
            if CURRENT_TOUR not in e.get("tours", []):
                continue
            es = short_of(e.get("team", "")) or e.get("team", "")
            if es in match_shorts and norm(e.get("display", "")) not in have_names:
                team_players.append((es, e["display"], e.get("role") or "?"))
                have_names.add(norm(e["display"]))
                slot_src[(es, norm(e["display"]))] = e.get("source") or "new"
        assigned, leftover, ambiguous = match_squad_to_perf(team_players, perf)

        # SILENT-DROP AUTO-ADD: a globally-known player who PLAYED but is in no squad slot would
        # otherwise vanish (resolves to a pid -> not a no-pid leftover -> never emitted). Add her
        # to this match (so she counts now) AND persist (source:'auto') so she's a member next run.
        # Guard (i) played; (ii) pid not already claimed (find_silent_drops ensures this) -> one
        # slot, no double-count. The false-merge detector in match_squad_to_perf stays on.
        for pid, v in find_silent_drops(perf, assigned, team_players):
            # Attribute the team the SAME way the leftover emit below does. Reading only
            # v["team"] made this whole auto-add dead code on any ESPN-sourced tour: parse_espn
            # never sets `team` on a perf entry (blank_perf defaults it to ""), so `es` was always
            # "" and every silent drop hit the `continue`. The comment called a blank feed team
            # "rare" — on the feed we are making primary it is universal. ESPN's own roster map is
            # the authority here, with the same best_team fallback the leftover path uses.
            tfull = (team_map.get(norm(v.get("name", "")))
                     or best_team(v.get("name", ""), team_map)
                     or v.get("team", ""))
            es = short_of(tfull) or ""
            if es not in match_shorts:
                # Genuinely un-attributable. This `continue` used to say "the leftover pass still
                # emits him for review" — it does not, and has not since resolve_perf_pid started
                # minting ci:<athlete.id> (a minted pid means he is never a no-pid leftover). No
                # row, no review entry, no log line. find_unattributed below now catches exactly
                # this, from the far end of the pipeline, so the drop cannot be silent again.
                continue
            disp = PID2DISP.get(pid, v.get("name", "")) or v.get("name", "")
            role = guess_role(v)
            # DEDUP ON THE EMIT KEY. emit() iterates team_players and looks up
            # assigned[(short, name)], so a REPEATED (es, disp) tuple emits the SAME perf TWICE —
            # once under the squad role, once under guess_role — as two rows scored DIFFERENTLY.
            # Neither existing guard can see it: find_silent_drops builds `claimed` from
            # resolve_pid(squad name), so a squad player whose NAME the registry cannot resolve is
            # in neither claimed set even though her slot is about to be emitted; and the
            # duplicate-pid detector below folds team_players names into a SET, so two identical
            # names collapse to one before it ever looks.
            # LIVE ON THE SHEET: ci:1229018 in "Match 1 — OIRE v OWI" scored 2 (BOWL) and -1 (AR,
            # which takes the ODI duck the BOWL role is exempt from) — the same performance, two
            # roles, two numbers. Five different app reductions then disagreed about which to show.
            slot = (es, disp)
            if any(sh == es and nm == disp for sh, nm, _ in team_players):
                if not assigned.get(slot):
                    assigned[slot] = v          # fill the EXISTING slot; never append a second
                    register_new_player(pid=pid, display=disp, feed=v.get("name", ""), team=es,
                                        role=role, tour=CURRENT_TOUR, source="auto")
                    print(f"AUTO-ADD (filled existing slot): {disp} (pid {pid}) — the squad row "
                          f"was unmatched; filled it instead of appending a duplicate.",
                          file=sys.stderr)
                else:
                    # The slot already holds a DIFFERENT perf: two humans share this display name
                    # in this match. Never clobber (that destroys the other performance) and never
                    # quietly emit a second row — shout, and let a human link the identity.
                    ANOMALIES.append({"tour": CURRENT_TOUR, "kind": "duplicate_slot", "pid": pid,
                                      "display": disp, "context": label,
                                      "names": [disp, v.get("name", "")],
                                      "finding": f"a second player resolving to {pid} claims the "
                                                 f"slot ({es}, {disp}) already held by another "
                                                 f"perf — auto-add SKIPPED, link the identity"})
                    print(f"!! AUTO-ADD CONFLICT: ({es}, {disp}) already holds a different perf; "
                          f"{pid} not added — see ANOMALIES.", file=sys.stderr)
                continue
            team_players.append((es, disp, role))
            assigned[slot] = v
            slot_src[(es, norm(disp))] = "auto"
            register_new_player(pid=pid, display=disp, feed=v.get("name", ""), team=es,
                                role=role, tour=CURRENT_TOUR, source="auto")
            print(f"AUTO-ADD: {disp} (pid {pid}) played {GSHEET_TAB} but was in no squad slot "
                  f"-> added to {es} this run + persisted.", file=sys.stderr)

        # ── THE RE-ARMED CHECK. Measured from the far end of the pipeline, after every path that
        # can place a performance on a row has had its turn (squad match, fuzzy fallback, sheet-
        # driven New players, the auto-add). Anything still here is a played performance that will
        # appear NOWHERE, which is the one outcome this project treats as unacceptable regardless of
        # cause: "Nothing goes unconsumed — no silent zeros, no dropped players."
        #
        # WHERE IT SURFACES, and why not the Recon tab. Recon Review answers "which of these two
        # numbers is right?" (S1/S2/Manual); Needs Cricinfo ID answers "who is this?" (type an id).
        # This is neither. Identity is not in doubt — ESPN's athlete.id IS the cricinfo id, so we
        # know exactly who he is; and there is no second number to choose between, only a row that
        # does not exist. The open question is "WHICH SIDE was he on?", and the answer to that is a
        # team code — which the 'Needs Review' tab already takes (it has a Team column, and
        # answering 'New' runs register_new_player with it, writing new_players.json, which the
        # injection loop above reads on the next run). Routing it to Recon would hand the owner an
        # S1/S2 dropdown for a question neither S1 nor S2 can answer — the same argument this file
        # already makes when it keeps identity out of the Recon tab.
        # The match-level obligation is met separately and unconditionally: the gate holds the
        # match (LIVE, or FLAGGED if it has already published — the ratchet still wins), and the
        # flag NAMES the player, so nobody has to be watching the right tab to notice.
        unattributed = find_unattributed(perf, assigned, team_players, leftover)
        for _pid, _v in unattributed:
            _nm = _v.get("name", "")
            _seen_team = (team_map.get(norm(_nm)) or best_team(_nm, team_map)
                          or _v.get("team", "") or "")
            REVIEW.append({"tour": CURRENT_TOUR, "team": _seen_team or "?", "feed": _nm,
                           "kind": "review", "role": guess_role(_v),
                           "suggestion": PID2DISP.get(_pid, "") if _pid else "",
                           # ACK must not silence this one — see write_review_tab.
                           "unattributed": True})
            UNMATCHED_LOG.add(f"UNATTRIBUTED feed '{_nm}' pid {_pid or '(none)'} — played "
                              f"{label}, roster team {_seen_team or '(none)'} matches neither "
                              f"{sorted(match_shorts)}")
            print(f"⛔ UNATTRIBUTED: '{_nm}' (pid {_pid or 'none'}) played {label} but could not be "
                  f"attributed to either side (roster team {_seen_team or '(none)'!r}) — NOT scored; "
                  f"match held, listed in '{REVIEW_TAB}'", file=sys.stderr)

        # Merge ESPN (squad-level): inject dot-balls, credit +4 in-XI to players cricapi
        # didn't list, and cross-check runs/wickets — disagreements flagged for review.
        # ONLY in the provisional path: when cricsheet is the scorer, its dots/maidens are
        # exact and must NOT be overwritten by ESPN (ESPN is then recon-only).
        xcheck = set()
        espn_assigned = {}
        emit_unsourced = set()
        if espn_perf and not cs_path:
            espn_assigned = match_squad_to_perf(team_players, espn_perf)[0]
            xcheck, emit_unsourced = merge_espn_into(
                assigned, {k: v for k, v in espn_assigned.items() if v})
        elif not cs_path:
            # No ESPN scorecard at all -> nothing supplies dots/maidens. Every bowler is scored on
            # an ASSUMED zero for both; record it so the gate can refuse to call this COMPLETED.
            # `_bowled`, not `balls`. cricapi omits `overs` on 100-ball cards, so EVERY Hundred
            # bowler arrives with balls=0 and this guard was structurally dead on exactly the
            # format+outage combination that froze 279 settlement rows on 4-5 Aug (+617 FP against
            # cricsheet) — a WRITE-ONCE freeze, so those baselines can never be corrected in place.
            # `_bowled` is the predicate the SCORER itself uses, so the guard and the score can no
            # longer disagree about whether a man bowled. Widening only: a bowler with balls>0
            # still trips it. Safe against KeyError — every perf comes from blank_perf.
            emit_unsourced = {k for k, v in assigned.items() if v and _bowled(v)}

        # The provisional cut, rebuilt with the SAME matcher emit uses (see build_provisional_cut).
        # On a cricsheet run `assigned` already holds official figures, so the baseline has to be
        # reconstructed from the raw cricapi+ESPN feeds — but via the squad-anchored matcher, not
        # the strict id index that produced the phantom `dots 0→N` rows.
        prov_pid, prov_unsourced = build_provisional_cut(team_players, api_perf, espn_perf)

        # ── Recon Review + match status (human-in-the-loop reconciliation) ───────
        # Compute per-match status BEFORE emit (emit closes over it). ANY unresolved L1 gap
        # holds the match LIVE; approved overrides are applied to the perf dicts emit() scores.
        mk = match_key_of(mdate, teams)
        l1_gaps = compute_l1_gaps(wit_pid, espn_pid, fields=wit_fields, witness=witness)
        perf_by_pid = {}   # pid -> the SAME perf dict objects emit() scores, so overrides stick
        for (sh_, nm_), dd in assigned.items():
            pp = resolve_pid(nm_) or resolve_perf_pid(dd) or ""
            if pp and pp not in perf_by_pid:
                perf_by_pid[pp] = dd
        assigned_team_of = {}
        for (sh_, nm_), _dd in assigned.items():
            pp = resolve_pid(nm_)
            if pp and pp not in assigned_team_of:
                assigned_team_of[pp] = sh_
        # Role per pid, so the points backstop can re-score both sides consistently (role drives
        # the SR/econ penalties — scoring one side as a different role would fake a delta).
        role_by_pid = {}
        for short_, name_, role_ in team_players:
            pp = resolve_pid(name_)
            if pp and pp not in role_by_pid:
                role_by_pid[pp] = role_ if role_ != "?" else (ROLE_OVERRIDE.get(norm(name_)) or "?")
        override_sources = {}   # pid -> {field: 'S1'|'S2'|'Manual'}, frozen with the settled row
        applied = apply_recon_overrides(perf_by_pid, wit_pid, espn_pid, l1_gaps, mk,
                                        RECON_OVERRIDES, sources_out=override_sources,
                                        fields=wit_fields)
        unresolved = {pid: g for pid, g in l1_gaps.items() if pid not in applied}
        # L2 baseline = the L1-RECONCILED provisional cut (raw cricapi+ESPN with the approved L1
        # override applied) — exactly what people saw. Comparing cricsheet against THIS (not raw
        # cricapi) keeps an official figure that confirms an approved correction silent, and flags
        # only a genuine change from the shown value.
        recon_prov = reconciled_provisional(prov_pid, wit_pid, espn_pid, l1_gaps, mk,
                                            RECON_OVERRIDES, fields=wit_fields)
        # THE L2 BASELINE. Read the FROZEN reconciled-L1 record; only fall back to a recomputation
        # for rows settled before field-level freezing existed.
        #
        # The baseline is what was published and frozen as base points AFTER the approved L1
        # overrides — which may be S1 (cricapi), S2 (ESPN) or a hand-typed Manual number. Reading
        # it, rather than rebuilding it, is the whole fix: a recomputation cannot know what was
        # shown, so it invented `dots 0→N` revisions for values that were already correct, and the
        # hold below then wrote those fabrications over cricsheet's exact figures.
        n_legacy = 0
        def _l2_baseline(pid):
            nonlocal n_legacy
            frozen = settled_baseline(mk, pid)
            if frozen is not None:
                return frozen
            n_legacy += 1
            return recon_prov.get(pid)
        l2_pairs = {}
        if cs_pid:
            for pid in cs_pid:
                base = _l2_baseline(pid)
                g = recon_gaps(base, cs_pid[pid], RECON_L2, sep="→")
                if not g:
                    # Nothing in the compared field list moved — but did the SCORED TOTAL?
                    g = points_gap(base, cs_pid[pid], role_by_pid.get(pid, "?") or "?")
                if g:
                    l2_pairs[pid] = g
        if n_legacy:
            print(f"  {label}: {n_legacy} player(s) settled before field-level freezing — L2 "
                  f"compared against a RECOMPUTED baseline for them, not the frozen one",
                  file=sys.stderr)
        # IDENTITY BREAK on the official card (previously invisible to this gate entirely).
        cs_orphans = unresolved_official(cs_perf) if cs_path else []
        id_zeroed, id_orphans = (identity_break(recon_prov, cs_pid, cs_orphans)
                                 if cs_path else ([], []))
        id_break = bool(cs_orphans) or bool(id_zeroed)
        l2_appr = l2_approved_pids(mk, RECON_OVERRIDES)
        # L2 HOLD (decision 3): until the official revision is APPROVED (source S2), keep showing
        # the last-approved (L1-reconciled) value. Deliberately inverts the usual "cricsheet
        # overrides everything" rule — we never silently revise a result the user already saw.
        # It MUST hold the same baseline it compared against — the frozen reconciled-L1 value.
        # Holding a recomputation is what corrupted settled points: the recompute fabricated a 0,
        # the fabricated 0 created the gap, and the gap then imposed the fabricated 0 over
        # cricsheet's exact figure. Self-fulfilling. Reading the frozen record breaks that loop,
        # because the value being held is by construction the one that was published.
        if cs_path and l2_pairs:
            for pid in l2_pairs:
                base = _l2_baseline(pid)
                if l2_appr.get(pid) != "S2" and base:
                    dd = perf_by_pid.get(pid)
                    if dd is not None:
                        for field in RECON_L2:
                            pv = base.get(field)
                            if pv is not None:
                                dd[field] = pv
        # IDENTITY HOLD: a squad player who PLAYED in the provisional cut but is missing from the
        # official card, while that card carries an unresolvable row, is an identity failure — NOT
        # a 0. Hold the provisional value (same discipline as the L2 hold) rather than zeroing a
        # settled score on a name-match miss, and flag it for a human to link.
        id_held = set()
        if cs_path and id_zeroed and cs_orphans:
            for pid in id_zeroed:
                if l2_appr.get(pid) == "S2":
                    continue          # human confirmed the official card: 0 is genuine
                dd = perf_by_pid.get(pid)
                base = _l2_baseline(pid)
                if dd is not None and base:
                    for field in SETTLED_FIELDS:
                        pv = base.get(field)
                        if pv is not None:
                            dd[field] = pv
                    id_held.add(pid)
        l2_dirty = any(pid not in l2_appr for pid in l2_pairs)
        # The write-once settlement baseline doubles as the "has this ever published as
        # COMPLETED?" record — which is exactly the ratchet the spec's "COMPLETED never returns to
        # LIVE" needs, and it costs nothing extra because the rows are already loaded.
        # TOUR-SCOPED. match_key is date::teamA|teamB with the gender qualifier stripped, so the
        # Hundred's same-day men's/women's double-headers between the same franchises COLLIDE:
        # measured 31 of 31 Women's keys also carry Men's rows — e.g.
        # 2026-08-04::london spirit|sunrisers leeds holds 33 Men's rows AND 32 Women's under one key.
        # An unscoped `any(k[0] == mk)` therefore answered "yes, already completed" for a Women's
        # match off the MEN'S result, and tours.json lists Men's first, so from run 2 onward every
        # Hundred Women's fixture read already_completed=True. That silently disables the LIVE arm
        # of classify_match_status on 60 of 92 live matches: a match with an unattributed player, or
        # a HELD ESPN card, could never go LIVE — it returned COMPLETED_FLAGGED, which the draft
        # treats as done, so the contest settled with the performance simply missing.
        # Scoping on the `tour` already stored on every settlement record (verified present on all
        # 3380) fixes it WITHOUT touching match_key — so nothing is re-keyed, no settled row is
        # orphaned, and the draft (which never reads this key at all) is unaffected.
        already_completed = any(k[0] == mk and v.get("tour") == CURRENT_TOUR
                                for k, v in SETTLEMENTS.items())
        match_status, recon_flag = classify_match_status(cs_path, bool(espn_perf), l1_gaps,
                                                        unresolved, l2_dirty, id_break=id_break,
                                                        unsourced=emit_unsourced,
                                                        already_completed=already_completed,
                                                        capi_present=bool(wit_pid),
                                                        witness=witness,
                                                        unattributed=unattributed)
        recon_state = classify_recon_state(cs_path, unresolved, emit_unsourced, l2_pairs, l2_appr,
                                           unattributed=unattributed)
        recon_state_col = RECON_STATE_LABEL.get(recon_state, recon_state)
        # Per-player markers so the draft UI can flag WHICH players aren't reconciled yet.
        player_recon = player_recon_markers(unresolved, l2_pairs, l2_appr)
        # Name the players the gate is holding for, so the hold is actionable rather than mysterious.
        for (sh_, nm_) in emit_unsourced:
            pp = resolve_pid(nm_)
            if pp:
                player_recon[pp] = "⛔ no dot-ball source"
        for pid in id_zeroed:
            if l2_appr.get(pid) != "S2":
                player_recon[pid] = "⛔ identity unresolved"
        # NAME the un-attributable players on the match flag. A count alone ("1 performance not
        # attributed to a team") sends the reader looking for a scoring bug; the name plus the team
        # string the roster actually gave is the whole diagnosis, and it is on EVERY row of the
        # match so it cannot be missed by looking at the wrong player.
        if unattributed:
            _names = ", ".join((PID2DISP.get(p) or v.get("name", "") or p or "?")
                               for p, v in unattributed)
            recon_flag += f" ({_names}) — answer in '{REVIEW_TAB}'"
            for _pid, _v in unattributed:
                if _pid:
                    player_recon[_pid] = "⛔ unattributed — no team, not scored"
        # NAME the hold on the match itself. Without this the flag reads "N players scored without
        # a dot-ball source" — true, but it sends the reader hunting a dots bug when the actual
        # cause is that ESPN's whole card was refused as incomplete. A held match is also forced
        # back to LIVE so base points cannot freeze on a card we refused (the ratchet still wins:
        # a match that has ALREADY published COMPLETED never returns to LIVE, it just gets flagged).
        if hold is not None and not cs_path:
            _g, _e = hold.get("got"), hold.get("expected")
            if hold.get("approved"):
                recon_flag = f"⚠ ESPN card SHORT ({_g}/{_e} deliveries) — scored anyway, approved"
            else:
                recon_flag = (("⛔ " if hold.get("escalated") else "⏳ ")
                              + f"ESPN card incomplete: {_g} of {_e} deliveries"
                              + ("" if hold.get("escalated") else " — retrying"))
                if not already_completed:
                    match_status = "LIVE"
                    recon_state, recon_state_col = "L1_OPEN", RECON_STATE_LABEL.get("L1_OPEN", "L1_OPEN")
        # LIVE (in-progress): the whole match is provisional-live — force the status, and skip
        # recon gating/queueing + per-player noise (mid-match witness↔ESPN gaps settle by end).
        if is_live:
            match_status, recon_flag, player_recon = "LIVE", "🔴 in progress", {}
            recon_state, recon_state_col = "L1_OPEN", "🔴 in progress"
        # Queue review rows for UNRESOLVED gaps (skip ones already approved+acked).
        if unresolved and not cs_path and not is_live:
            new_rows = build_recon_rows(mk, label, mdate, CURRENT_TOUR, unresolved,
                                        wit_pid, espn_pid, fields=wit_fields, witness=witness)
            RECON_REVIEW.extend(r for r in new_rows
                                if (mk, r.get("pid", ""), r.get("param", "")) not in RECON_ACK)
        for pid, g in l2_pairs.items():
            if l2_appr.get(pid) != "S2" and (mk, pid, "L2") not in RECON_ACK:
                RECON_REVIEW.append({"match_key": mk, "tour": CURRENT_TOUR, "match": label,
                                     "date": mdate, "pid": pid, "full": PID2DISP.get(pid, pid),
                                     "param": "L2", "s1": g,
                                     "s2": "S2 = take cricsheet's number · S1 = keep what was settled",
                                     "tier": "l2"})
        # IDENTITY. Two DIFFERENT questions, so they go to two different tabs — routing them by
        # what the human is actually being asked:
        #
        #   "Did he really not play?"      -> Recon Review. A VALUE decision, S1/S2 fits.
        #   "Who is this player?"          -> Needs Cricinfo ID. An IDENTITY gap; an S1/S2
        #                                     dropdown is meaningless there, and this tab is the
        #                                     established self-maintaining loop (drop the id ->
        #                                     manual_ci_bridges picks it up on the next build).
        #
        # Discriminator: a squad player already anchored to a real `ci:` pid can only be a
        # selection question. One still sitting on a slug:/uncapped: pid has no cricinfo id at
        # all — that IS the identity gap, and belongs with the orphan that matches him.
        if cs_path and not is_live:
            # Pair each unresolved squad player with the official-card row that is almost certainly
            # the same person, so the human fills ONE id instead of the same number twice (once for
            # "Matthew Revis", once for "ML Revis"). Surname evidence first, then a strict
            # one-left/one-left fallback — never a guess when two of either remain.
            unpaired = [pid for pid in id_zeroed if not pid.startswith("ci:")]
            paired, taken = {}, set()
            for pid in unpaired:
                sn = norm(PID2DISP.get(pid, pid)).split()[-1:] or [""]
                hits = [v for v in cs_orphans if v.get("name") and v["name"] not in taken
                        and norm(v["name"]).split()[-1:] == sn]
                if len(hits) == 1:
                    paired[pid] = hits[0]; taken.add(hits[0]["name"])
            leftover_p = [p for p in unpaired if p not in paired]
            leftover_o = [v for v in cs_orphans if v.get("name") not in taken]
            if len(leftover_p) == 1 and len(leftover_o) == 1:
                paired[leftover_p[0]] = leftover_o[0]; taken.add(leftover_o[0]["name"])

            for pid in id_zeroed:
                if l2_appr.get(pid) == "S2" or (mk, pid, "ID") in RECON_ACK:
                    continue
                disp = PID2DISP.get(pid, pid)
                if not pid.startswith("ci:"):
                    mate = paired.get(pid)
                    NEEDS_CRICINFO.append({
                        "player": disp, "current_pid": pid, "tour": CURRENT_TOUR,
                        "team": (assigned_team_of.get(pid) or ""),
                        "closest_guess": (
                            f"official card calls him '{mate.get('name')}' — {cricinfo_hint(mate)}"
                            if mate else
                            ("candidates: " + ", ".join(
                                f"{v.get('name')} [{cricinfo_hint(v)}]" for v in cs_orphans)
                             if cs_orphans else f"no cricinfo id yet ({label}, {mdate})"))})
                    continue
                held = " · value HELD" if pid in id_held else ""
                RECON_REVIEW.append({
                    "match_key": mk, "tour": CURRENT_TOUR, "match": label, "date": mdate,
                    "pid": pid, "full": disp, "param": "ID",
                    "s1": f"played provisionally, absent from official card{held}",
                    "s2": ("S2 = accept the official card (he did not feature, 0 stands); "
                           "S1 = keep the held value"),
                    "tier": "id"})
            # An unresolvable official row is an identity gap by definition: its points cannot
            # join ANY contest (the draft joins by pid and never fuzzy-falls-back for a pid'd
            # player), so they sit silently outside the game. Straight to Needs Cricinfo ID, with
            # the crosswalk-derived cricinfo id so filling it in is a copy, not a research task.
            for v in cs_orphans:
                nm = v.get("name") or ""
                if nm in taken:
                    continue    # already listed under the squad player it belongs to — one row, one id
                NEEDS_CRICINFO.append({
                    "player": nm, "current_pid": f"cs:{v.get('cs_id') or nm}",
                    "tour": CURRENT_TOUR, "team": canon_team(v.get("team", "")) or "",
                    "closest_guess": f"{cricinfo_hint(v)} · official card only ({label}, {mdate})"})

        def emit(short, name, role, d, in_squad, attributed=True):
            """`attributed=False` = a played performance we could not put on either side.

            It is published — silence is the bug being fixed — but it is NOT SCORED and it must not
            read like a row that was. Every points cell is left BLANK rather than 0: a 0 there is
            an absence wearing the clothes of a value, which is the exact class of defect this file
            keeps paying for. The raw stats stay so a human can see what is at stake, `Played` stays
            Y because he did play, and `record_settlement` is skipped — a performance nobody has
            attributed must never enter the WRITE-ONCE money baseline."""
            src = status
            if not attributed:
                src += " · ⛔ UNATTRIBUTED (no team) — NOT SCORED"
            # Resolve identity: stable Player ID + canonical display name (so the row shows
            # ONE consistent name regardless of which feed/spelling supplied the stats, and
            # the draft can join by id instead of fuzzy-matching the name).
            pid = resolve_pid(name) or (resolve_pid(d["name"]) if d else "") or ""
            full = PID2DISP.get(pid, name) if pid else name
            # ── Two-stage reconciliation ──────────────────────────────────────────
            # L1: witness ↔ ESPN agreement during the provisional cut (both live feeds).
            #     Value reads '<witness>/ESPN' — the legend is appended so the sheet is
            #     self-explaining even in a run where different tours have different witnesses.
            l1 = recon_gaps(wit_pid.get(pid), espn_pid.get(pid), wit_fields, sep="/")
            if wit_pid.get(pid) and espn_pid.get(pid):
                l1_col = ("⚠ " + l1 + f" ({witness}/ESPN)") if l1 else f"✓ clean ({witness}/ESPN)"
            else:
                l1_col = ""   # only one provisional feed had this player → nothing to cross-check
            # L2: official cricsheet ↔ the provisional cut, once cricsheet posts.
            #     Value reads 'was→corrected' (provisional → official) so the revision is obvious.
            if cs_pid:
                if pid in cs_pid:
                    # Baseline is the L1-RECONCILED cut, matching the gate. (This used to read
                    # raw prov_pid, so the column could shout "revised" on a value your own
                    # approved L1 override had already corrected — column contradicting gate.)
                    l2 = l2_pairs.get(pid) or recon_gaps(recon_prov.get(pid), cs_pid[pid],
                                                         RECON_L2, sep="→")
                    l2_col = ("⚠ revised: " + l2) if l2 else "✓ complete"
                elif pid in recon_prov and recon_prov[pid].get("played"):
                    # Distinguish a genuine non-selection from a name-match failure: an
                    # unmatched official row in the same match means it's identity, not benching.
                    l2_col = ("⛔ identity unresolved — unmatched on official card"
                              if pid in id_zeroed and id_orphans else
                              "⚠ revised: not in official XI")
                else:
                    l2_col = "✓ complete"
            else:
                l2_col = "⏳ pending official"
            if d:
                # Unknown-role leftovers: use the role you set in Needs Review if any, else a
                # best-guess from their stats (never a bare "?"). Role drives SR/Econ penalties.
                role_out = role if role != "?" else (ROLE_OVERRIDE.get(norm(name)) or guess_role(d))
                s = score(d, role_out) if attributed else {k: "" for k in
                                                           ("bat", "bowl", "field", "sr", "eco",
                                                            "xi", "total")}
                sr = round(d["r"] / d["b"] * 100, 1) if d["b"] else ""
                econ = round(d["runs_conceded"] / (d["balls"] / 6), 2) if d["balls"] else ""
                dots_out = d["dots"] if dots_final else ""  # never fill dots from a no-dots source
                rows.append([label, mdate, short, pid, full, role_out, "Y",
                             d["r"], d["b"], d["4s"], d["6s"], sr, d["dismissal"],
                             round(d["balls"] / 6, 1) if d["balls"] else "",
                             d["maidens"], dots_out, d["runs_conceded"], d["w"], econ,
                             d["catches"], d["stumpings"], d["runouts"],
                             s["bat"], s["bowl"], s["field"], s["sr"], s["eco"], s["xi"],
                             s["total"], src, in_squad, d.get("bat_order") or "",
                             l1_col, l2_col, match_status, recon_flag, player_recon.get(pid, ""),
                             recon_state_col,
                             _points_delta(mk, pid, s["total"]) if attributed else ""])
                if attributed and match_status in ("COMPLETED", "COMPLETED_FLAGGED"):
                    record_settlement(mk, CURRENT_TOUR, label, mdate, short, pid, full,
                                      s["total"], match_status, src,
                                      perf=d, field_sources=override_sources.get(pid))
            else:
                rows.append([label, mdate, short, pid, full, role, "N"] + [""] * 22 +
                            [src, in_squad, "", l1_col, l2_col, match_status, recon_flag,
                             player_recon.get(pid, ""), recon_state_col,
                             _points_delta(mk, pid, 0)])
                # Freeze non-players at 0 too: a contest scored them 0, so a later run that
                # gives them points is just as much a change from the settled state.
                if match_status in ("COMPLETED", "COMPLETED_FLAGGED"):
                    record_settlement(mk, CURRENT_TOUR, label, mdate, short, pid, full,
                                      0, match_status, src,
                                      perf={"played": False},
                                      field_sources=override_sources.get(pid))

        for short, name, role in team_players:
            emit(short, name, role, assigned.get((short, name)),
                 slot_src.get((short, norm(name)), "Y"))
        # players who featured but matched no squad name -> show for manual review,
        # attributing their team (ESPN roster first, then the parsed team) so it's never a bare "?".
        # NOTE this loop is empty on any tour whose rows carry an ESPN athlete.id: such a row gets a
        # minted `ci:` pid, so it is never a no-pid leftover. It is kept for the cricapi-only /
        # cricsheet-only paths, where a row genuinely can have no id — the un-attributable case it
        # used to cover is now handled by find_unattributed below.
        for d in leftover.values():
            tfull = team_map.get(norm(d["name"])) or best_team(d["name"], team_map) or d.get("team", "")
            short = short_of(tfull) or "?"
            emit(short, d["name"], "?", d, "N")
        # THE UN-ATTRIBUTABLE PERFORMANCES. Published, named, and visibly not scored (see emit's
        # `attributed` docstring). Team is "?" because we genuinely do not know it — and "?" is
        # inert downstream: the draft groups a match's rows by team pair, so a "?" row joins no
        # contest and cannot move money while the question is open.
        for _pid, _v in unattributed:
            emit("?", _v.get("name", ""), "?", _v, "N", attributed=False)
        # DUPLICATE-PID detector: two distinct squad/feed rows resolving to one pid IN THIS MATCH
        # (the original "two Wyatt rows" symptom). Surface for a Yes/No instead of letting one
        # player silently shadow another. Distinct names only (same name twice is just the feed).
        pid_rows = {}
        for short, name, role in team_players:
            p = resolve_pid(name)
            if p: pid_rows.setdefault(p, set()).add(norm(name))
        for d in leftover.values():
            p = resolve_pid(d["name"])
            if p: pid_rows.setdefault(p, set()).add(norm(d["name"]))
        for p, names in pid_rows.items():
            if len(names) > 1:
                ANOMALIES.append({"tour": CURRENT_TOUR, "kind": "duplicate_pid", "pid": p,
                                  "display": PID2DISP.get(p, p), "context": label,
                                  "names": sorted(names),
                                  "finding": "2+ rows share this id in one match: " + ", ".join(sorted(names))})
    print(f"sources: {n_cs} cricsheet(official), {n_espn} cricapi+ESPN, {n_api} cricapi-only", file=sys.stderr)

    # ── Toss-time announced XI (matches NOT yet ended) ───────────────────────────
    # After the toss (~30 min pre-play) ESPN posts each side's playing XI. We write it
    # as Played=Y rows with blank stats so the draft app's getLastPlayedXI shows the
    # REAL announced XI for the upcoming match instead of last-match's. Once the match
    # ends, the next run replaces these with full-stat rows. Best-effort: if ESPN hasn't
    # posted the XI yet, we simply write nothing (no harm).
    # Gate on ESPN having the XI (the real signal), not cricapi's lagging toss flags.
    # Only not-STARTED matches near today: started-but-not-ended ones are now SCORED above
    # (the `live` set), so writing SCHEDULED lineup rows for them too would duplicate.
    pending = [m for m in matches if is_fmt(m) and not m.get("matchStarted")
               and not m.get("matchEnded") and near_today(m)]
    n_toss = 0
    for j, m in enumerate(sorted(pending, key=lambda x: x.get("dateTimeGMT", x.get("date", ""))), 1):
        teams = m.get("teams", []); mdate = m.get("date", "")
        try:
            ev = espn_event_id(mdate, teams)
            xi = espn_xi(ev) if ev else {}
        except Exception:
            xi = {}
        if not xi:
            continue
        # Toss result (if posted) goes into Source so the app can show it without a schema change.
        toss = espn_toss(ev)
        src = "ESPN announced XI (toss)" + (f" · {toss}" if toss else "")
        label = f"Match {len(ended) + len(live) + j} — " + " v ".join(name2short.get(norm(t), t) for t in teams)
        xi_perf = {}
        for k, v in xi.items():
            p = blank_perf(v["name"]); p["played"] = True; xi_perf[k] = p
        team_players = []
        for tname in teams:
            short = name2short.get(norm(tname))
            if short:
                team_players += [(short, n, r) for n, r in squads[short]["players"]]
        assigned, leftover, _ = match_squad_to_perf(team_players, xi_perf)
        for short, name, role in team_players:
            played = "Y" if (short, name) in assigned else "N"
            pid = resolve_pid(name) or ""
            full = PID2DISP.get(pid, name) if pid else name
            rows.append([label, mdate, short, pid, full, role, played] + [""] * 22 +
                        [src, "Y", "", "", "", "SCHEDULED", "", "", "", ""])
        tmap = {}
        try:
            tmap = espn_team_map(ev)
        except Exception:
            pass
        for d in leftover.values():
            tfull = tmap.get(norm(d["name"])) or d.get("team", "")
            pid = resolve_pid(d["name"]) or ""
            full = PID2DISP.get(pid, d["name"]) if pid else d["name"]
            rows.append([label, mdate, short_of(tfull) or "?", pid, full, "?", "Y"] + [""] * 22 +
                        [src, "N", "", "", "", "SCHEDULED", "", "", "", ""])
        n_toss += 1
    if n_toss:
        print(f"toss XI written for {n_toss} not-ended match(es)", file=sys.stderr)

    # ── Freeze a fully-completed tournament ──────────────────────────────────────
    # Once every match has ENDED and been resolved from cricsheet (immutable gold source),
    # with nothing live or upcoming, the tab is final and will never change again. Record
    # the series id so future runs skip its cricapi poll entirely. Strict guards keep this
    # from firing mid-tournament: require the `ends` date in the past AND full cricsheet
    # coverage (n_cs == every fmt match) — a cricsheet outage this run just defers the freeze.
    matches_fmt = [m for m in matches if is_fmt(m)]
    try:
        ended_past = bool(tour.get("ends")) and date.today() > date.fromisoformat(tour["ends"])
    except ValueError:
        ended_past = False
    if (ended_past and matches_fmt and not live and not pending
            and len(ended) == len(matches_fmt) and n_cs == len(ended)):
        mark_frozen(WC_SERIES)
        print(f"tour fully resolved ({n_cs}/{len(matches_fmt)} cricsheet-official) -> FROZEN; "
              f"future runs skip the cricapi poll for this tour", file=sys.stderr)

    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows -> {out_csv}", file=sys.stderr)

    # Per-tour CI log of feed names the registry didn't cover (fuzzy hits + genuine leftovers).
    # NOTE: REVIEW/UNMATCHED_LOG accumulate across tours (the cross-tour "Needs Review" sheet tab
    # is written once in main()), so we filter to THIS tour here and do NOT clear the accumulators.
    tour_items = dedup_review([r for r in REVIEW if r["tour"] == CURRENT_TOUR])
    if tour_items:
        log_path = os.path.join(os.path.dirname(__file__), "registry",
                                f"UNMATCHED_{re.sub(r'[^a-z0-9]+', '_', GSHEET_TAB.lower()).strip('_')}.log")
        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            open(log_path, "w").write(
                "\n".join(f"{r['kind']:13} {r['feed']}  ({r['team']})"
                          + (f" -> {r['suggestion']}" if r['suggestion'] else "") for r in tour_items) + "\n")
        except Exception:
            pass
        print(f"⚠ {len(tour_items)} feed name(s) need review -> registry/{os.path.basename(log_path)} "
              f"(fix no-code in the 'Player Aliases' sheet tab, or registry/manual_aliases.json)", file=sys.stderr)

    if GSHEET_ID:
        write_to_gsheet(cols, rows)

def load_tours():
    """Tours to process. Driven by tours.json (multi-tour); falls back to a single
    tour from env vars for backward compatibility."""
    path = os.environ.get("TOURS_JSON", os.path.join(os.path.dirname(__file__), "tours.json"))
    here = os.path.dirname(__file__)
    if os.path.exists(path):
        tours = json.load(open(path))
        for t in tours:
            t["squads_path"] = os.path.join(here, t["squads"]) if t.get("squads") else ""
            t["out_csv"] = re.sub(r"[^a-z0-9]+", "_", t["tab"].lower()).strip("_") + ".csv"
        return tours
    return [{"name": "default", "cricapi_series": WC_SERIES, "espn_series": ESPN_SERIES,
             "tab": GSHEET_TAB, "squads_path": SQUADS_JSON, "out_csv": OUT}]

# Days after a tour's last match to keep refreshing; after this the tour is dormant — no API
# calls, no writes, tab kept as-is. The old 21 kept ~6 tours "active", draining the daily
# cricapi budget before the still-live tour was even reached. A fully cricsheet-resolved tour
# now freezes IMMEDIATELY (see mark_frozen below) regardless of this window, so grace only
# governs tours cricsheet never fully covers; 5 is aggressive (cricsheet can lag ~5-7d), so a
# match whose ball-by-ball posts late may be frozen on provisional (ESPN/cricapi) numbers.
FREEZE_GRACE_DAYS = int(os.environ.get("FREEZE_GRACE_DAYS", "5"))

def is_active(tour):
    """A tour stays live until `ends` + grace; then it's dormant (skipped entirely)."""
    e = tour.get("ends")
    if not e:
        return True
    try:
        return date.today() <= date.fromisoformat(e) + timedelta(days=FREEZE_GRACE_DAYS)
    except ValueError:
        return True

# Series ids of tournaments that are 100% done (every match ENDED and resolved from cricsheet,
# the immutable gold source). We stop polling cricapi for these entirely — the tab is already
# final, so a fetch would only burn the daily hit budget. Distinct from the grace window above:
# this triggers as soon as a tour is genuinely complete, not on a fixed calendar buffer.
# Re-enter the poll by deleting a tour's id here (or the whole file).
FROZEN_PATH = os.path.join(os.path.dirname(__file__), "registry", "frozen_tours.json")

def load_frozen():
    try:
        return set(json.load(open(FROZEN_PATH)).get("frozen", []))
    except Exception:
        return set()

def mark_frozen(series_id):
    """Add a series id to the frozen ledger (idempotent; the workflow commits the file so the
    flag survives the ephemeral runner)."""
    cur = load_frozen()
    if series_id in cur:
        return
    cur.add(series_id)
    os.makedirs(os.path.dirname(FROZEN_PATH), exist_ok=True)
    json.dump({"frozen": sorted(cur)}, open(FROZEN_PATH, "w"), indent=2)

def in_toss_window():
    """True if now is within any match's [toss−30m, start+15m] window. Reads committed
    start times (toss_windows.json) so the frequent tick costs ZERO API when idle.
    Regenerate that file from the app's matches.json whenever the schedule changes."""
    try:
        windows = json.load(open(os.path.join(os.path.dirname(__file__), "toss_windows.json")))
    except Exception:
        return True  # missing file -> don't silently go dark; let the tick run
    now = datetime.now(timezone.utc)
    for s in windows:
        try:
            st = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            continue
        if st - timedelta(minutes=30) <= now <= st + timedelta(minutes=15):
            return True
    return False

TOUR_CONTROL_TAB = "TOUR CONTROL"   # human cricapi gate: only tours marked "yes" here are polled

def sync_tour_control(tours):
    """Human approval gate for cricapi spend. Maintains the 'TOUR CONTROL' GSheet tab (one row per
    tour, a 'Poll cricapi?' cell = yes/no) and returns {cricapi_series: decision}. A tour that is
    NOT 'yes' is skipped ENTIRELY (0 cricapi hits, no sheet write) — so a freshly auto-ingested tour
    never burns the budget until Nishant approves it. Append-only, so it NEVER clobbers his edits:
    new tours are added as 'pending'; currently-ACTIVE tours are seeded 'yes' on first creation so
    live scoring isn't interrupted (flip any to 'no' to discard + save hits). GSheet-only — costs
    ZERO cricapi. Returns None when the sheet is unavailable (local / no creds) → caller polls all
    (back-compat). Note: the draft's LIVE ESPN H2H is keyless, so a discarded tour still shows live
    points in the app for free — the gate only stops the bot's cricapi/sheet points."""
    sh = open_gsheet()
    if sh is None:
        return None
    header = ["Tour", "Tab", "cricapi_series", "Poll cricapi? (yes/no)", "Notes"]
    try:
        ws = sh.worksheet(TOUR_CONTROL_TAB)
        existing = ws.get_all_values()
    except Exception:
        ws, existing = None, []
    # Preserve every existing row's decision (never rewrite the human's cells).
    ctrl, seen = {}, set()
    if existing and len(existing) > 1:
        hdr = existing[0]
        si = hdr.index("cricapi_series") if "cricapi_series" in hdr else 2
        di = next((i for i, h in enumerate(hdr) if h.lower().startswith("poll")), 3)
        for row in existing[1:]:
            if len(row) > si and row[si].strip():
                sid = row[si].strip()
                ctrl[sid] = (row[di].strip().lower() if len(row) > di else "")
                seen.add(sid)
    first_time = ws is None
    new_rows = []
    for t in tours:
        sid = t.get("cricapi_series", "")
        if not sid or sid in seen:
            continue
        dec = "yes" if (first_time and is_active(t)) else "pending"
        ctrl[sid] = dec
        new_rows.append([t.get("name", ""), t.get("tab", ""), sid, dec, ""])
        seen.add(sid)
    try:
        if ws is None:
            ws = sh.add_worksheet(title=TOUR_CONTROL_TAB, rows=max(len(new_rows) + 10, 20), cols=len(header))
            ws.update(range_name="A1", values=[header] + new_rows, value_input_option="RAW")
            print(f"created '{TOUR_CONTROL_TAB}' tab ({len(new_rows)} tours; active→yes, rest→pending)", file=sys.stderr)
        elif new_rows:
            ws.append_rows(new_rows, value_input_option="RAW")
            print(f"'{TOUR_CONTROL_TAB}': added {len(new_rows)} new tour(s) as 'pending' (approve to poll)", file=sys.stderr)
    except Exception as e:
        print(f"tour control tab: {e}", file=sys.stderr)
    return ctrl

def _tour_approved(ctrl, series_id):
    """A tour is polled only if its control cell reads yes/y (case-insensitive)."""
    return (ctrl.get(series_id) or "").strip().lower() in ("yes", "y")

def main():
    if not KEY:
        sys.exit("Set CRICKET_API_KEY env var.")
    if FREQUENT and not ON_DEMAND and not in_toss_window():
        print("frequent tick: no match in toss window — nothing to do", file=sys.stderr)
        return
    # No-code manual fixes (no-op locally / without creds): load the persistent alias store,
    # then apply any rows you marked 'Yes' in Needs Review. Then process tours; finally persist
    # new aliases and republish the review queue.
    global RECON_OVERRIDES, SETTLEMENTS, CB_STORE
    SETTLEMENTS = _load_settlements()   # the frozen "before"; only ever added to
    load_sheet_aliases()
    load_new_players()             # merge sheet-added players' identity into the registry (before reads)
    read_review_confirmations()    # 'New' registers a player into NEW_PLAYERS_DATA (+ identity)
    read_anomaly_confirmations()   # record Yes/No on identity anomalies (read-only on live identity)
    read_recon_approvals()         # record recon 'Correct Value' answers -> recon_overrides.json
    read_needs_cricinfo()          # consume filled cricinfo ids -> manual_ci_bridges.json
    promote_new_players()          # ...and APPLY them: slug:/uncapped: -> ci: (closes the loop)
    # PID2DISP is the live registry's pid set — an approval keyed outside it is orphaned (see guard).
    RECON_OVERRIDES = overrides_by_match(_load_overrides(), known_pids=set(PID2DISP))
    # Whole-match ESPN-card decisions -> the event ids parse_espn is allowed to score short. Read
    # from the SAME committed ledger as every other approval, so the answer survives the ephemeral
    # runner and the row can be dropped from the tab without the decision coming undone.
    for _ovs in RECON_OVERRIDES.values():
        for _o in _ovs:
            if _o.get("scope") == "espn_card" and _o.get("source") == "S2":
                ESPN_CARD_SCORE_ANYWAY.add(str(_o.get("pid", "")).split(":", 1)[-1])
    # The DERIVED cricbuzz_id -> cricinfo_id bridge, loaded ONCE. Read-only here: deriving new
    # pairs is `python3 registry/cricbuzz_bridge.py --derive`, deliberately kept out of the
    # scoring run so a run can never mutate identity while it is publishing points.
    if _cb_bridge is not None:
        try:
            CB_STORE = _cb_bridge.load_store()
            _nb = len(CB_STORE.get("bridge", {}))
            print(f"cricbuzz bridge: {_nb} derived cricbuzz→cricinfo pair(s), "
                  f"{len(CB_STORE.get('revoked', {}))} revoked", file=sys.stderr)
        except Exception as e:
            # Never fatal: a missing/unreadable bridge just means cricapi stays the witness.
            CB_STORE = None
            print(f"cricbuzz bridge unavailable ({e}) — cricapi remains the L1 witness "
                  f"on every tour", file=sys.stderr)
    elif _CB_IMPORT_ERR:
        print(f"cricbuzz module not importable ({_CB_IMPORT_ERR}) — cricapi remains the L1 "
              f"witness on every tour", file=sys.stderr)
    tours = load_tours()
    tours_ok, tours_failed = 0, []
    # Process still-running tours FIRST (latest `ends` first) so a live tour never starves on
    # the cricapi daily budget behind already-finished ones (tours.json order otherwise put the
    # live tour last, so the cap could be hit before its series_info was even fetched -> stale).
    tours.sort(key=lambda t: t.get("ends", ""), reverse=True)
    frozen = load_frozen()
    control = sync_tour_control(tours)   # human cricapi approval gate (None = no creds → poll all)
    print(f"{len(tours)} tour(s): {', '.join(t['name'] for t in tours)}", file=sys.stderr)
    for t in tours:
        # Human gate FIRST (0 API): skip any tour not marked "yes" in the TOUR CONTROL sheet, so a
        # discarded / not-yet-approved tour never spends cricapi (nor writes its sheet tab).
        # The gate exists to ration the cricapi DAILY QUOTA, so it applies only to tours that
        # actually spend it. An ESPN-only tour has a blank cricapi_series, which meant
        # sync_tour_control wrote it no row (nothing to approve) and _tour_approved("") then read
        # "pending" FOREVER — a permanently un-approvable tour, skipped on every single run and
        # never scored. Blank series id => zero cricapi spend => nothing for a human to ration.
        espn_only = not (t.get("cricapi_series") or "").strip()
        if control is not None and not espn_only and not _tour_approved(control, t.get("cricapi_series")):
            print(f"-- {t['name']}: not approved in TOUR CONTROL "
                  f"(poll={control.get(t.get('cricapi_series')) or 'pending'}) — skipped (0 API)", file=sys.stderr)
            continue
        if not espn_only and t.get("cricapi_series") in frozen:
            print(f"-- {t['name']}: frozen (fully resolved, cricsheet-official) — skipped (0 API)", file=sys.stderr)
            continue
        if not is_active(t):
            print(f"-- {t['name']}: dormant (ended {t.get('ends')}, frozen) — skipped", file=sys.stderr)
            continue
        try:
            run_tour(t)
            tours_ok += 1
        except SystemExit as e:           # one tour aborting must not kill the others
            print(f"!! tour '{t.get('name')}' skipped: {e}", file=sys.stderr)
            tours_failed.append((t.get("name"), f"skipped: {e}"))
        except Exception as e:
            import traceback
            print(f"!! tour '{t.get('name')}' error: {e}", file=sys.stderr)
            traceback.print_exc()
            tours_failed.append((t.get("name"), repr(e)))
    # ── Phase-0 quota instrumentation ───────────────────────────────────────────
    # Log how many cricapi hits THIS run spent + each key's day-cumulative usage (hitsToday/
    # hitsLimit) as cricapi reported it. Diagnostic goals: (a) a 5-min tick must read 0 hits;
    # (b) if hitsToday jumps between our runs by MORE than we spent, a 2nd consumer shares the
    # keys; (c) watching hitsToday across the day reveals the real reset window.
    _mode = "on-demand" if ON_DEMAND else ("frequent-tick" if FREQUENT else "full")
    _usage = " ".join(f"#{k+1}={v.get('today','?')}/{v.get('limit','?')}"
                      for k, v in sorted(API_QUOTA.items()))
    if tours_failed:
        print(f"!! {len(tours_failed)} tour(s) FAILED to process "
              f"({tours_ok} ok): " + "; ".join(f"{n}: {e}" for n, e in tours_failed),
              file=sys.stderr)
    print(f"[quota] mode={_mode} keys={len(API_KEYS)} cricapi_hits_this_run={_CRICAPI_HITS}"
          + (f" | key usage: {_usage}" if _usage else " | (no live cricapi hit this run)"),
          file=sys.stderr)

    # Refresh the quota/health snapshot in ALL modes (full, frequent, on-demand) so the
    # draft app's "hits left today" gauge stays current whenever the sheet is touched.
    try:
        write_status_tab("on-demand" if ON_DEMAND else ("frequent" if FREQUENT else "full"))
    except Exception as e:
        print(f"status tab: {e}", file=sys.stderr)
    # The settled baseline is frozen in EVERY mode (a match can first go COMPLETED on any run,
    # including an on-demand one — miss that moment and its "before" is gone for good).
    if SETTLE_NEW:
        print(f"[settlement] froze {SETTLE_NEW} new baseline row(s) "
              f"({len(SETTLEMENTS)} total)", file=sys.stderr)
        save_settlements()
    if CS_LEARNED:
        _save_cricsheet_learned()
    if not FREQUENT:
        sync_player_aliases()   # persist auto + confirmed aliases into the Player Aliases store
        _save_new_players(NEW_PLAYERS_DATA)   # persist New + auto-added players (workflow commits it)
        write_review_tab()      # publish remaining unmatched players (closest-match + Yes/No)
        write_anomaly_tab()     # publish detected merges/dupes + the audit of past splits (Yes/No)
        write_recon_tab()       # publish L1/L2 feed disagreements to approve (pick Correct Value)
        write_needs_cricinfo_tab()  # identity gaps -> the SAME tab build_registry uses (drop the id)
        write_settlement_tab()  # publish the frozen settled baseline the draft app diffs against

    # FAIL THE RUN if any tour didn't process. Placed last so every sheet/ledger write above still
    # happens (the tours that DID work must still publish, and the persist step is `if: always()`).
    # Until now a per-tour exception was caught, logged and forgotten, and the workflow exited 0 —
    # so "all three live tours crashed" looked exactly like "a healthy run" on the Actions page. A
    # NameError in api() hid there for an hour while the sheet silently went stale.
    # ── ESPN cards held past the retry budget ────────────────────────────────────────────────
    # Placed with the tour-failure exit, and for the same reason: every sheet and ledger write
    # above must still happen. This is the CUTOFF. Before it existed a permanently-short ESPN card
    # meant a match that retried forever, published nothing and appeared nowhere — the only trace
    # was one line of stderr in a green workflow run. A green run must never mean "one match is
    # quietly unscored". Answering the row (either way) clears it; a light tick never fails,
    # because it does not write the Recon tab the message points at.
    _esc = [h for h in ESPN_HOLDS.values()
            if h.get("escalated")
            and (h.get("match_key", ""), f"espn:{h.get('event', '')}", "ESPN CARD") not in RECON_ACK]
    for h in _esc:
        print(f"⛔ ESPN CARD HELD — {h.get('tour')} · {h.get('match')} ({h.get('date')}): "
              f"ball-by-ball {h.get('got')} of the scorecard's {h.get('expected')} deliveries, "
              f"{(h.get('hours') or 0):.0f}h after start. NOT scored, NOT settled, NOT published. "
              f"Answer its 'ESPN CARD' row in the '{RECON_TAB}' tab "
              f"(S2 = score the short card anyway and flag it · S1 = keep holding).",
              file=sys.stderr)
    # ── Performances nobody could attribute to a team ────────────────────────────────────────
    # Summarised HERE because the per-match line is buried in thousands of lines of tour log, and
    # each of these is a match sitting on hold: results hidden in the app, nothing settling. It
    # does NOT fail the run — deliberately. `unsourced` and `unresolved` hold a match the same way
    # and do not, because the hold IS the enforcement and the repair is a squad edit, not a
    # run-blocking decision. (The ESPN-card gate exits non-zero only because for that match
    # NOTHING publishes at all and there is a binary S1/S2 answer waiting.)
    # Deduped on (tour, feed) the same way the tab is: the same man un-attributable in three
    # matches of one tour is ONE thing to fix, not three lines of noise.
    _unattr = dedup_review([r for r in REVIEW if r.get("unattributed")])
    if _unattr:
        print(f"⛔ {len(_unattr)} performance(s) could not be attributed to a team — those matches "
              f"are HELD (not settled, results hidden). Give each a team in the '{REVIEW_TAB}' tab "
              f"(answer 'New' with the Team column filled), or add a "
              f"registry/team_aliases.json entry if the feed's team name is the problem: "
              + "; ".join(f"{r['feed']} [{r['tour']}, roster team {r['team']}]" for r in _unattr),
              file=sys.stderr)
    if tours_failed:
        sys.exit(f"{len(tours_failed)} tour(s) failed to process: "
                 + "; ".join(n for n, _ in tours_failed))
    if _esc and not FREQUENT:
        sys.exit(f"{len(_esc)} match(es) held on an incomplete ESPN card, unanswered past the "
                 f"{ESPN_HOLD_GRACE_H:.0f}h budget: "
                 + "; ".join(f"{h.get('match')} ({h.get('got')}/{h.get('expected')})" for h in _esc))

_GSHEET = None
def open_gsheet():
    """Open the Google spreadsheet once (cached). None if GSHEET_ID/creds are missing."""
    global _GSHEET
    if _GSHEET is not None:
        return _GSHEET
    creds = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not GSHEET_ID or not creds:
        return None
    try:
        import gspread
        _GSHEET = gspread.service_account_from_dict(json.loads(creds)).open_by_key(GSHEET_ID)
    except Exception as e:
        print(f"gspread open failed: {e}", file=sys.stderr)
        _GSHEET = None
    return _GSHEET

STATUS_TAB = "STATUS"   # bot-written quota/freshness snapshot (2 cols: Metric | Value).
                        # The draft app reads this to show "hits left today" next to its
                        # "Refresh live points" button. Not a data tab — never merged into points.

def write_status_tab(mode):
    """Write a small quota/freshness snapshot the draft app reads. Best-effort — a failure
    here must never break a run. cricapi free tier = 100 hits/day per key; we know each key's
    day-cumulative usage from the `info` blocks captured this run (unqueried keys are unknown,
    so hits_* are only emitted once at least one live hit gave us real numbers)."""
    sh = open_gsheet()
    if not sh:
        return
    import gspread
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    keys = max(1, len(API_KEYS))
    rows = [["Metric", "Value"], ["updated_utc", now], ["keys", str(keys)], ["mode", mode]]
    # ESPN cards the completeness gate refused this run. This is the AT-A-GLANCE surface: it is
    # written in EVERY mode (full, frequent, on-demand), unlike the Recon tab, so a hold shows up
    # within one 5-minute tick instead of waiting for the next full run.
    _held = [h for h in ESPN_HOLDS.values() if not h.get("approved")]
    if ESPN_HOLDS:
        rows += [["espn_cards_held", str(len(_held))],
                 ["espn_cards_escalated", str(sum(1 for h in _held if h.get("escalated")))],
                 ["espn_cards_held_detail",
                  "; ".join(f"{h.get('match', h.get('event', '?'))} "
                            f"{h.get('got')}/{h.get('expected')}"
                            for h in sorted(ESPN_HOLDS.values(),
                                            key=lambda x: x.get("date", "")))[:480]]]
    # The L1 second witness, per match. "cricbuzz configured but witnessing 0 matches" is the
    # failure mode that would otherwise be invisible — the tour keeps scoring off ESPN and only
    # the per-row Source string says the cross-check never happened.
    if CB_DIAG:
        _wit = [d for d in CB_DIAG.values() if d.get("bridged")]
        rows += [["cricbuzz_matches_witnessed", str(len(_wit))],
                 ["cricbuzz_matches_unavailable", str(len(CB_DIAG) - len(_wit))],
                 ["cricbuzz_players_unbridged", str(sum(d.get("unbridged", 0)
                                                        for d in CB_DIAG.values()))],
                 ["cricbuzz_detail",
                  "; ".join(d.get("note", "") for d in CB_DIAG.values() if d.get("note"))[:480]]]
    if API_QUOTA:   # at least one live hit this run -> real numbers (else leave hits_* blank)
        used = sum(API_QUOTA.get(i, {}).get("today", 0) for i in range(keys))
        limit_total = sum(API_QUOTA.get(i, {}).get("limit", 100) for i in range(keys))
        rows += [["hits_used", str(used)],
                 ["hits_limit", str(limit_total)],
                 ["hits_left", str(max(0, limit_total - used))]]
    try:
        try:
            ws = sh.worksheet(STATUS_TAB)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=STATUS_TAB, rows=20, cols=2)
        ws.clear()
        ws.update(range_name="A1", values=rows, value_input_option="RAW")
    except Exception as e:
        print(f"write_status_tab failed: {e}", file=sys.stderr)

ALIASES_TAB = "Player Aliases"   # user-editable: Feed Name -> Correct Player (no-code fix)
REVIEW_TAB = "Needs Review"      # bot-written: players that need a human glance
ANOMALY_TAB = "Identity Anomalies"  # bot-written: detected merges/duplicates + the audit of past splits
SPLITS_PATH = os.path.join(os.path.dirname(__file__), "registry", "identity_splits.json")
RECON_TAB = "Recon Review"          # bot-written: cricapi/ESPN (L1) + cricsheet (L2) disagreements to approve
OVERRIDES_PATH = os.path.join(os.path.dirname(__file__), "registry", "recon_overrides.json")
NEW_PLAYERS_PATH = os.path.join(os.path.dirname(__file__), "registry", "new_players.json")
# ── Settlement snapshot (the missing "before") ────────────────────────────────
# The sheet is REWRITTEN in place every run, so the numbers a contest was actually settled on
# survive nowhere: L2 recon compares cricsheet against a LIVE RE-COMPUTATION of the provisional
# cut, not against what was on screen when money changed hands. Anything that moves points
# without cricsheet's involvement — a scorer fix (the Hundred bowler-balls bug: Gleeson 4 -> 145),
# an ESPN backfill, a registry change — is therefore invisible to reconciliation by construction.
# This file is the fix: the FIRST time a match is published as COMPLETED/COMPLETED_FLAGGED we
# record every player's points, and never overwrite it. WRITE-ONCE is the whole point — a
# baseline you can revise is not a baseline.
SETTLEMENT_PATH = os.path.join(os.path.dirname(__file__), "registry", "settlement_snapshots.json")
SETTLEMENT_TAB = "SETTLEMENT AUDIT"    # bot-written: the frozen settled baseline the draft app diffs against
CRICSHEET_LEARNED_PATH = os.path.join(os.path.dirname(__file__), "registry",
                                      "cricsheet_learned_aliases.json")
SETTLEMENTS = {}        # (match_key, pid) -> frozen record, loaded once + only ever added to
SETTLE_NEW = 0          # how many rows this run froze (for the log)

def _load_settlements():
    try:
        data = json.load(open(SETTLEMENT_PATH))
    except Exception:
        return {}
    out = {}
    for r in data.get("settlements", []):
        out[(r.get("match_key", ""), r.get("pid", ""))] = r
    return out

def save_settlements():
    """Persist the frozen baseline. Only ever grows — CI commits it like recon_overrides."""
    try:
        rows = sorted(SETTLEMENTS.values(), key=lambda r: (r.get("date", ""), r.get("match_key", ""),
                                                          r.get("full", "")))
        json.dump({"note": "WRITE-ONCE settlement baseline: each player's points the FIRST time "
                           "their match was published COMPLETED/COMPLETED_FLAGGED. Never edited "
                           "or overwritten — it is the record of what a contest was settled on, "
                           "which the draft app diffs the live sheet against. 'provenance' is "
                           "'live' when frozen by a normal run, 'seed' when reconstructed from a "
                           "pre-cricsheet run for a match that completed before this existed.",
                   "settlements": rows},
                  open(SETTLEMENT_PATH, "w"), indent=1, ensure_ascii=False)
    except Exception as e:
        print(f"could not update settlement_snapshots.json: {e}", file=sys.stderr)

# The reconciled-L1 fields the frozen record carries. RECON_L2 is what L2 compares, plus the
# inputs that drive derived bonuses (SR needs `b`, econ needs `balls`) — a baseline missing those
# would let a match "reconcile clean" while the scored total moved, which is the hole points_gap()
# was written to cover.
# `dismissed` is here because it is a SCORING INPUT (the −2 duck), not decoration: `dismissal` is
# the human-readable text and freezing the text while dropping the flag is the same
# written-but-never-read asymmetry this file keeps getting bitten by. Storing it costs nothing —
# record_settlement keeps any non-None value, so False is preserved as False.
# ⚠ MEASURED, PRE-EXISTING, NOT FIXED HERE: points_gap() cannot actually score a frozen baseline
# today. record_settlement writes `{f: perf[f] for f in SETTLED_FIELDS if perf.get(f) is not None}`,
# which DROPS every zero/False, and score() indexes with p["b"] / p["dots"] / ... — so on all 671
# field-carrying rows in registry/settlement_snapshots.json score() raises KeyError and the L2
# points backstop returns "backstop failed — unverified" for every one of them. Fixing that means
# changing how the baseline is rehydrated (default the missing keys), which moves L2 output on
# settled matches — an owner call, not a side effect of a parser fix.
# EVERY FIELD score() INDEXES MUST BE FROZEN. `lbwb` (the +8 bowled/lbw bonus) and `dro` (which
# splits a run-out 12 vs 6) were scored correctly on every published number but were NEVER COPIED
# into the snapshot — the mirror image of this project's usual bug: read, but never written.
# Consequence: settled_baseline() handed score() a dict missing keys it indexes, so the points
# backstop could not re-score the baseline and could not answer "has this number moved since we
# settled?" — ~867 rows carry a review flag nobody can resolve and ~20 matches sit at L2_PENDING
# permanently. Points were never wrong; the AUDIT of them was blind.
# Rows frozen from here carry both. Rows frozen BEFORE this still lack them and are left alone
# deliberately: zero-filling an old baseline would understate it by 8 per bowled/lbw wicket and
# mis-split every run-out, MANUFACTURING an L2 gap on a match that settled correctly and inviting
# someone to "correct" a right number. _hydrate_baseline names the missing fields instead, so those
# rows read honestly unverifiable rather than silently wrong.
SETTLED_FIELDS = RECON_L2 + ["b", "balls", "played", "bat_order", "dismissal", "dismissed",
                             "lbwb", "dro"]

def record_settlement(match_key, tour, label, mdate, team, pid, full, points, status, source,
                      perf=None, field_sources=None):
    """Freeze one player's settled state. WRITE-ONCE per (match_key, pid): a later run that
    re-scores the match (cricsheet posting, an approved revision, a scorer fix) must NOT touch
    this — the delta between it and the live sheet is exactly what the audit surface shows.

    Stores the reconciled-L1 PERF, not just the total. The points-only record could not serve as
    the L2 baseline (you cannot diff a field against an int), which is exactly why L2 fell back to
    RECOMPUTING the provisional cut on every run — and that recomputation is what invented the
    phantom `dots 0→N` revisions and then wrote them over settled points. With the fields frozen
    here, L2 reads the baseline instead of rebuilding it.

    `field_sources` records WHICH source each overridden field came from (S1 cricapi / S2 ESPN /
    Manual), so the record explains itself: an audit can show not just what the settled number was
    but why. Fields with no entry were never disputed.

    Back-compat: legacy rows have no `fields` key. They stay valid for the total-level audit; the
    field-level baseline is simply unavailable for them (see the re-seed in RECON_DEV_PLAN)."""
    global SETTLE_NEW
    if not pid or (match_key, pid) in SETTLEMENTS:
        return
    rec = {
        "match_key": match_key, "tour": tour, "match": label, "date": mdate, "team": team,
        "pid": pid, "full": full, "points": points, "status": status, "source": source,
        "frozen_at": _today_iso(), "provenance": "live"}
    if perf is not None:
        rec["fields"] = {f: perf.get(f) for f in SETTLED_FIELDS if perf.get(f) is not None}
        if field_sources:
            rec["field_sources"] = dict(field_sources)
    SETTLEMENTS[(match_key, pid)] = rec
    SETTLE_NEW += 1

def _points_delta(match_key, pid, live_total):
    """How far this player's CURRENT points have moved from what was SETTLED, or "" if nothing
    moved / nothing was settled yet.

    Signed on purpose: '-8' and '+141' are different stories. Gleeson settled at 4 and now reads
    145; a bare 'changed' flag would have made that indistinguishable from a 1-point rounding
    drift, which is how it went unnoticed for a fortnight."""
    rec = SETTLEMENTS.get((match_key, pid))
    if not rec:
        return ""
    try:
        d = int(live_total) - int(rec.get("points") or 0)
    except (TypeError, ValueError):
        return ""
    return f"{d:+d}" if d else ""

def settled_baseline(match_key, pid):
    """The frozen reconciled-L1 perf for one player, or None if this row predates field-level
    freezing. THIS is what L2 must compare cricsheet against — never a recomputation."""
    rec = SETTLEMENTS.get((match_key, pid))
    return (rec or {}).get("fields")

def _today_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def load_sheet_aliases():
    """Read the 'Player Aliases' tab (Feed Name | Correct Player | Source) and merge it into the
    runtime alias map. This is the persistent alias store — the bot auto-adds high-confidence
    matches + your confirmed ones, and you can hand-add any row. Created if missing."""
    sh = open_gsheet()
    if sh is None:
        return
    import gspread
    try:
        ws = sh.worksheet(ALIASES_TAB)
    except gspread.WorksheetNotFound:
        try:
            ws = sh.add_worksheet(title=ALIASES_TAB, rows=200, cols=3)
            ws.update(range_name="A1", values=[["Feed Name", "Correct Player", "Source"]],
                      value_input_option="RAW")
        except Exception as e:
            print(f"could not create '{ALIASES_TAB}' tab: {e}", file=sys.stderr)
        return
    try:
        vals = ws.get_all_values()
        pairs = [(r[0].strip(), r[1].strip()) for r in vals[1:]
                 if len(r) >= 2 and r[0].strip() and r[1].strip()]
        n = add_sheet_aliases(pairs)
        if n:
            print(f"Loaded {n} alias(es) from the '{ALIASES_TAB}' sheet tab.", file=sys.stderr)
    except Exception as e:
        print(f"could not read '{ALIASES_TAB}' tab: {e}", file=sys.stderr)

def read_review_confirmations():
    """Read the 'Needs Review' tab BEFORE processing. For every row you marked 'Yes' in the
    'Correct?' column, treat Feed Name -> Closest Match as a confirmed alias: apply it now AND
    queue it to be saved into Player Aliases (so it sticks). Also remember every Yes/No so the
    next rewrite preserves your answers."""
    sh = open_gsheet()
    if sh is None:
        return
    import gspread
    try:
        ws = sh.worksheet(REVIEW_TAB)
    except gspread.WorksheetNotFound:
        return
    try:
        rows = ws.get_all_values()
    except Exception as e:
        print(f"could not read '{REVIEW_TAB}' tab: {e}", file=sys.stderr)
        return
    if not rows:
        return
    hdr = {c.strip(): i for i, c in enumerate(rows[0])}
    # tolerate the old header without "New"/"Role" so a mid-season schema change doesn't lose answers
    ci = next((hdr[k] for k in ("Correct? (Yes/New)", "Correct? (Yes/No/New)",
                                "Correct? (Yes/No)", "Correct?") if k in hdr), 5)
    ti, fi, si = hdr.get("Tour", 0), hdr.get("Feed Name", 2), hdr.get("Closest Match", 3)
    tmi, ri = hdr.get("Team", 1), hdr.get("Role", -1)
    applied = 0
    for r in rows[1:]:
        if len(r) <= max(ci, fi, si):
            continue
        feed, closest, ans = r[fi].strip(), r[si].strip(), r[ci].strip().lower()
        if not feed:
            continue
        key = (r[ti].strip() if len(r) > ti else "", feed)
        PRIOR_CONFIRM[key] = r[ci].strip()
        PRIOR_CLOSEST[key] = closest          # preserve any name you typed across rewrites
        if ri >= 0 and len(r) > ri and r[ri].strip():
            PRIOR_ROLE[key] = r[ri].strip()   # preserve any role you set
            ro = r[ri].strip().upper()        # and use it to score this player (SR/Econ depend on it)
            if ro in ("WK", "BAT", "AR", "BOWL"):
                ROLE_OVERRIDE[norm(feed)] = ro
        # "New" -> register a genuinely-new player (sheet-driven): create a global identity +
        # this tour's membership so they resolve + count + become draftable from now on.
        if ans == "new" or closest.lower() == "new":
            disp = closest if (closest and closest.lower() != "new") else feed   # type the real name
            # Reuse an EXISTING identity if either the feed spelling OR the real name you typed
            # already resolves (so marking an existing player "New" LINKS the spelling to their
            # real pid instead of minting a duplicate); only mint a slug for a truly-new player.
            pid = resolve_pid(feed) or resolve_pid(disp) or slugify(disp)
            team = r[tmi].strip() if len(r) > tmi else ""
            role = ((r[ri].strip() if (ri >= 0 and len(r) > ri) else "") or guess_role({"name": feed}))
            register_new_player(pid=pid, display=disp, feed=feed, team=team,
                                role=role, tour=key[0], source="new")
            ACK.add(norm(feed)); applied += 1; continue
        # "No" (bad guess) -> acknowledge: stop re-flagging.
        if ans in ("no", "n"):
            ACK.add(norm(feed)); continue
        # "Yes" -> map Feed -> Closest Match (saved to Player Aliases so it sticks).
        if ans in ("y", "yes") and closest:
            pid = ALIAS2PID.get(norm(closest))
            if pid:
                ALIAS2PID[norm(feed)] = pid
                CONFIRMED.append((feed, closest))
                ACK.add(norm(feed)); applied += 1
    if applied:
        print(f"Applied {applied} confirmed (Yes) alias(es) from '{REVIEW_TAB}'.", file=sys.stderr)

def _load_splits():
    try:
        return json.load(open(SPLITS_PATH))
    except Exception:
        return {"splits": []}

def _load_overrides():
    try:
        return json.load(open(OVERRIDES_PATH))
    except Exception:
        return {"overrides": []}

def _save_overrides(data):
    try:
        json.dump(data, open(OVERRIDES_PATH, "w"), indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"could not update recon_overrides.json: {e}", file=sys.stderr)

# ── Sheet-driven NEW players (identity once + per-tour membership; no build_registry needed) ──
def slugify(name):
    """A stable, tour-agnostic pid for a brand-new player: 'slug:jane-maguire'. A later
    build_registry run can upgrade this to the player's real cricsheet_id."""
    return "slug:" + re.sub(r"\s+", "-", norm(name)).strip("-")

def _load_new_players():
    try:
        return json.load(open(NEW_PLAYERS_PATH))
    except Exception:
        return {"players": []}

def _save_new_players(data):
    try:
        json.dump(data, open(NEW_PLAYERS_PATH, "w"), indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"could not update new_players.json: {e}", file=sys.stderr)

def load_new_players():
    """Load new_players.json into NEW_PLAYERS_DATA and merge each entry's identity (pid + aliases
    + display) into the runtime registry (ALIAS2PID/PID2DISP) — so a sheet-added player resolves
    THIS run with no build_registry rebuild. Membership is injected per-match in run_tour."""
    global NEW_PLAYERS_DATA
    NEW_PLAYERS_DATA = _load_new_players()
    for e in NEW_PLAYERS_DATA.get("players", []):
        pid, disp = e.get("pid"), e.get("display")
        if not pid:
            continue
        PID2DISP.setdefault(pid, disp or pid)
        for a in list(e.get("aliases", [])) + ([disp] if disp else []):
            if a:
                ALIAS2PID.setdefault(norm(a), pid)
    return NEW_PLAYERS_DATA.get("players", [])

def register_new_player(pid, display, feed, team, role, tour, source):
    """Add/merge a player into NEW_PLAYERS_DATA (saved + committed at run end) and reflect their
    identity in the runtime registry so resolution works immediately. Deduped by pid; aliases +
    tours accumulate. `source`: 'new' (you marked New) | 'auto' (silent-drop auto-add)."""
    players = NEW_PLAYERS_DATA.setdefault("players", [])
    e = next((x for x in players if x.get("pid") == pid), None)
    if e is None:
        e = {"pid": pid, "display": display, "aliases": [], "team": team,
             "role": role, "tours": [], "source": source}
        players.append(e)
    al = set(e.get("aliases", []))
    if feed:
        al.add(norm(feed))
    e["aliases"] = sorted(a for a in al if a)
    if tour and tour not in e.setdefault("tours", []):
        e["tours"].append(tour)
    PID2DISP.setdefault(pid, display or pid)
    for a in e["aliases"] + ([display] if display else []):
        if a:
            ALIAS2PID.setdefault(norm(a), pid)
    return e

def find_silent_drops(perf, assigned, team_players):
    """Played feed players that resolve to a pid but NO squad slot claimed it — the silent-drop
    set (otherwise lost: not assigned, and not a no-pid `leftover`). Returns [(pid, perf)]."""
    claimed = set()
    for d in assigned.values():
        p = resolve_perf_pid(d)
        if p:
            claimed.add(p)
    for (_, n, _) in team_players:
        p = resolve_pid(n)
        if p:
            claimed.add(p)
    out, seen = [], set()
    for k, v in perf.items():
        if not v.get("played"):
            continue
        p = resolve_perf_pid(v)
        if p and p not in claimed and p not in seen:
            seen.add(p)
            out.append((p, v))
    return out

def find_unattributed(perf, assigned, team_players, leftover):
    """Played feed rows that reach the END of the pipeline as NO row at all. The re-armed check.

    WHAT WENT WRONG. The tolerant fallback that made an un-attributable player VISIBLE was the
    `leftover` emit — feed entries the matcher could not resolve to any pid, published with team
    "?" and `In Squad List = N` for a human to look at. `resolve_perf_pid` now mints
    `ci:<espn athlete.id>` for anyone with an id, so on an ESPN-sourced tour `unresolved` is empty
    ⇒ `leftover` is empty ⇒ that whole emit is DEAD CODE. MEASURED 14 Aug 2026: "In Squad List = Y"
    on 2228/2228 rows across the Hundred M/W + CPL tabs, zero N; the same tab carried 9 "?" review
    rows on 11 Aug. The one surviving path for "played, but in no squad slot" is the auto-add, and
    its failure branch is `if es not in match_shorts: continue` — a bare drop whose comment still
    claims "the leftover pass still emits him for review". It does not. Nothing does. So a player
    the roster cannot attribute to either side leaves NO row, NO review entry and NO log line: his
    performance is simply gone, and the match publishes COMPLETED and freezes money without it.

    This check deliberately measures the OUTCOME, not the cause. It does not ask "did the auto-add
    fall through?" or "was he in leftover?" — either question re-breaks the moment someone adds a
    new way to fall out of the pipeline, which is exactly how the previous check died. It asks the
    only question that matters: emit() iterates `team_players` and publishes
    `assigned[(short,name)]`, plus every `leftover` row — so which PLAYED feed rows are in neither?

    Identity, not object identity: `merge_perf` returns a NEW dict when two feed spellings fold
    into one pid (it returns the object itself only for a single row), so an `id()`-based diff
    would report a merged player as dropped. So a row with a pid is judged ON THE PID ALONE — that
    survives the merge, and it does not silence a real drop just because someone already on a slot
    normalises to the same name. Only a row with NO id falls back to the name, which is the sole
    handle a `leftover` row (or a fuzzy-matched one) has.

    `resolve_perf_pid` is safe to call again here: every entry in `perf` has already been through
    it inside `match_squad_to_perf`, so the minting branch's `if norm(nm) not in ALIAS2PID` guard
    makes these pure lookups (`find_silent_drops` relies on the same property).

    Returns [(pid, perf)] — pid "" when the row has no id at all."""
    slots = {(sh, nm) for sh, nm, _ in team_players}
    consumed_pids, consumed_names = set(), set()
    for key, d in assigned.items():
        # emit() only ever looks up slots that exist in team_players; an `assigned` entry keyed
        # on anything else would never be published, so it must not count as consumed.
        if key not in slots or not d:
            continue
        p = resolve_perf_pid(d)
        if p:
            consumed_pids.add(p)
        consumed_names.add(norm(d.get("name", "")))
    for d in leftover.values():
        p = resolve_perf_pid(d)
        if p:
            consumed_pids.add(p)
        consumed_names.add(norm(d.get("name", "")))
    out, seen = [], set()
    for k, v in perf.items():
        if not v.get("played"):
            continue
        # match_squad_to_perf drops junk entries ("Player Not Found", "sub") from its pool before
        # it does anything else, so they are in neither `assigned` nor `leftover` BY DESIGN. Not
        # re-applying that filter here would turn every one of them into a match-blocking row.
        if is_junk(v.get("name", k)):
            continue
        p = resolve_perf_pid(v) or ""
        nn = norm(v.get("name", "") or k)
        if (p in consumed_pids) if p else (nn and nn in consumed_names):
            continue
        key = p or nn
        if key in seen:
            continue
        seen.add(key)
        out.append((p, v))
    return out

def overrides_by_match(data, known_pids=None):
    """Index APPROVED overrides by match_key -> [override dicts] for O(1) apply.

    ORPHAN GUARD (added 7 Aug 2026 after 83 of 131 approvals were found dead): overrides are keyed
    by pid, so an identity migration silently invalidates every stored approval it doesn't re-key.
    The failure is invisible and permanent — the approval stops applying, the L2 baseline falls back
    to raw cricapi, and the SAME review row reappears every single run no matter how many times it
    is answered. That is what the 25 Jul `ci:` migration did to this file. Never let it be quiet:
    an approval on a pid the registry doesn't know is SHOUTED, not skipped."""
    idx = {}
    orphans = []
    for o in data.get("overrides", []):
        if o.get("status") == "approved" and o.get("match_key"):
            pid = o.get("pid")
            # Not every override is keyed on a PLAYER. "*" is the ALL-L1 whole-match seed and
            # "espn:<eventId>" the whole-match ESPN-card decision; the orphan check asks "does the
            # registry still know this player?", which is meaningless for both. (The "*" case was
            # already latent — every match-level seed would have been shouted as an orphan. It has
            # never fired only because the committed ledger currently holds 0 match-scope rows.)
            if (known_pids is not None and pid and pid != "*"
                    and not pid.startswith("espn:") and pid not in known_pids):
                orphans.append(pid)
            idx.setdefault(o["match_key"], []).append(o)
    if orphans:
        uniq = sorted(set(orphans))
        print(f"⛔ RECON OVERRIDES ORPHANED: {len(orphans)} approved override(s) on "
              f"{len(uniq)} pid(s) the registry no longer knows — these approvals are NOT being "
              f"applied and their review rows will reappear every run. Re-key "
              f"registry/recon_overrides.json (registry/pid_map.json maps old->new): "
              f"{', '.join(uniq[:10])}{' …' if len(uniq) > 10 else ''}", file=sys.stderr)
    return idx

def _approval_to_override(match_key, pid, param, correct, manual):
    """Turn one Recon Review answer into an override record (or None if blank/unset).
    `correct` is the 'Correct Value' cell (S1/S2/Manual); `manual` the 'Manual Value' cell.
    Pure — unit-testable without the sheet."""
    correct = (correct or "").strip()
    if not correct:
        return None
    src = correct.upper()
    if param == "ALL L1":               # match-level seed: use a whole feed
        if src not in ("S1", "S2"):
            return None
        return {"match_key": match_key, "scope": "match", "source": src,
                "pid": "*", "field": "ALL_L1", "status": "approved"}
    if param == "ESPN CARD":
        # A WHOLE-MATCH decision about the ESPN feed itself, keyed on the ESPN event
        # (pid = "espn:<eventId>"), not on a player. S2 = score the short card anyway, permanently
        # flagged; S1 = keep holding. Either answer stops the run failing: the gate's contract is
        # that a human decided, not that the bot picked for him.
        return {"match_key": match_key, "scope": "espn_card", "pid": pid,
                "source": ("S2" if src == "S2" else "S1"), "status": "approved"}
    if param == "L2":                   # accept official (S2) or keep provisional (S1)
        return {"match_key": match_key, "scope": "l2", "pid": pid,
                "source": ("S2" if src == "S2" else "S1"), "status": "approved"}
    if param == "ID":
        # Identity break. S2 = "the official card is right, he genuinely didn't feature" -> the 0
        # stands and the hold is released. S1 = keep the held provisional value while the registry
        # alias is added. Same 'l2' scope, so the existing hold/approval plumbing applies.
        return {"match_key": match_key, "scope": "l2", "pid": pid,
                "source": ("S2" if src == "S2" else "S1"), "status": "approved"}
    if param == "ID-ORPHAN":
        # Informational only: the real fix is a registry alias/bridge, not a per-match override.
        # Answering just acknowledges it so the row stops reappearing.
        return None
    field = LABEL2FIELD.get(param, param)   # player-level: a single stat field
    o = {"match_key": match_key, "scope": "player", "pid": pid, "field": field, "status": "approved"}
    if src == "MANUAL":
        try:
            o["value"] = int(float((manual or "").strip()))
        except (TypeError, ValueError):
            return None    # 'Manual' chosen but no value typed yet -> not actionable
        o["source"] = "Manual"
    else:
        o["source"] = src
    return o

def read_anomaly_confirmations():
    """Read the 'Identity Anomalies' tab BEFORE processing. Record every Yes/No answer (preserved
    across rewrites in PRIOR_ANOMALY), acknowledge answered DETECTED anomalies so they stop
    re-flagging (ANOMALY_ACK), and persist PAST-SPLIT decisions into registry/identity_splits.json
    (status confirmed | undo-requested). IMPORTANT: this NEVER mutates live identity
    (ALIAS2PID/players.json/the points tabs) — a confirmed split/undo is applied out-of-band by
    build_registry.py + a commit, exactly like the Player Aliases -> registry fold. Read-only on points."""
    sh = open_gsheet()
    if sh is None:
        return
    import gspread
    try:
        ws = sh.worksheet(ANOMALY_TAB)
    except gspread.WorksheetNotFound:
        return
    try:
        rows = ws.get_all_values()
    except Exception as e:
        print(f"could not read '{ANOMALY_TAB}' tab: {e}", file=sys.stderr)
        return
    if not rows:
        return
    hdr = {c.strip(): i for i, c in enumerate(rows[0])}
    ti = hdr.get("Type", 1)
    pi = hdr.get("Player ID", 2)
    ai = next((hdr[k] for k in ("Different players? (Yes/No)", "Different players?",
                                "Correct? (Yes/No)") if k in hdr), 6)
    splits = _load_splits()
    sp_by_key = {f"split:{s['id']}": s for s in splits.get("splits", [])}
    acks = splits.setdefault("acks", [])          # persisted DETECTED-anomaly answers (durable ACKs)
    ack_by_key = {(a.get("type"), a.get("pid")): a for a in acks}
    changed = False
    # Seed ACKs from the committed ledger FIRST: a detected anomaly answered on a PAST run must stay
    # acknowledged even though its sheet row was dropped (the ephemeral CI runner has no other memory,
    # and re-reading the sheet can't help once the row is gone). This is what stops re-surfacing.
    for (atyp, apid), a in ack_by_key.items():
        ANOMALY_ACK.add((atyp, apid))
        PRIOR_ANOMALY.setdefault((atyp, apid), a.get("answer", ""))
    for r in rows[1:]:
        if len(r) <= max(ti, pi, ai):
            continue
        typ, pid, ans = r[ti].strip(), r[pi].strip(), r[ai].strip().lower()
        if not pid or not ans:
            continue
        key = (typ, pid)
        PRIOR_ANOMALY[key] = r[ai].strip()
        ANOMALY_ACK.add(key)                      # answered -> stop re-flagging detected anomalies
        s = sp_by_key.get(pid)                    # past-split rows: persist the decision
        if s is not None:
            newst = ("confirmed" if ans in ("y", "yes")
                     else "undo-requested" if ans in ("n", "no") else s.get("status"))
            if newst != s.get("status"):
                s["status"] = newst; changed = True
        else:                                     # detected anomaly -> persist the ACK durably
            prev = ack_by_key.get(key)
            if prev is None:
                rec = {"type": typ, "pid": pid, "answer": r[ai].strip()}
                acks.append(rec); ack_by_key[key] = rec; changed = True
            elif prev.get("answer") != r[ai].strip():
                prev["answer"] = r[ai].strip(); changed = True
    if changed:
        try:
            json.dump(splits, open(SPLITS_PATH, "w"), indent=2, ensure_ascii=False)
            print(f"Recorded split decisions from '{ANOMALY_TAB}' into identity_splits.json.", file=sys.stderr)
        except Exception as e:
            print(f"could not update identity_splits.json: {e}", file=sys.stderr)

def read_recon_approvals():
    """Read the 'Recon Review' tab BEFORE processing. For every row with a 'Correct Value':
    record the answer (PRIOR_RECON/PRIOR_MANUAL), ack it (RECON_ACK, so it's DROPPED from the
    tab next run — a resolved row disappears), and add its override to recon_overrides.json.
    That file is the durable ledger: the workflow COMMITS it back to git after the run, so the
    correction survives the ephemeral runner even though the row is gone. Read-only on points."""
    sh = open_gsheet()
    if sh is None:
        return
    import gspread
    try:
        ws = sh.worksheet(RECON_TAB)
    except gspread.WorksheetNotFound:
        return
    try:
        rows = ws.get_all_values()
    except Exception as e:
        print(f"could not read '{RECON_TAB}' tab: {e}", file=sys.stderr)
        return
    if not rows:
        return
    h = {c.strip(): i for i, c in enumerate(rows[0])}
    pi = h.get("Player ID", 3); pm = h.get("Param", 5)
    ci = h.get("Correct Value", 8); mi = h.get("Manual Value", 9); ki = h.get("Match Key", 11)
    data = _load_overrides()   # committed ledger (persisted across runs by the workflow)
    have = {(o.get("match_key"), o.get("pid"), o.get("field"), o.get("scope"))
            for o in data.get("overrides", [])}
    cell = lambda r, i: (r[i].strip() if i is not None and i < len(r) else "")
    added = registered = 0
    for r in rows[1:]:
        mk, pid, param, correct = cell(r, ki), cell(r, pi), cell(r, pm), cell(r, ci)
        manual = cell(r, mi)
        if not mk or not correct:
            continue
        PRIOR_RECON[(mk, pid, param)] = correct
        if manual:
            PRIOR_MANUAL[(mk, pid, param)] = manual
        RECON_ACK.add((mk, pid, param))   # answered -> dropped from the tab next write
        o = _approval_to_override(mk, pid, param, correct, manual)
        if o:
            sig = (o.get("match_key"), o.get("pid"), o.get("field"), o.get("scope"))
            if sig not in have:
                data["overrides"].append(o); have.add(sig); added += 1
    _save_overrides(data)   # the workflow commits this back to git after the run
    print(f"Recon: {added} new override(s) this run; {len(data['overrides'])} total persisted.", file=sys.stderr)

def sync_player_aliases():
    """Persist the bot's high-confidence auto-matches + your confirmed-Yes rows into the
    'Player Aliases' store (append-only, deduped) so they resolve deterministically forever
    and can be folded into the committed registry (fold_review_aliases.py)."""
    sh = open_gsheet()
    if sh is None:
        return
    import gspread
    try:
        ws = sh.worksheet(ALIASES_TAB)
    except gspread.WorksheetNotFound:
        return
    try:
        existing = ws.get_all_values()
        have = {norm(r[0]) for r in existing[1:] if r and r[0].strip()}
        new = []
        for feed, correct in CONFIRMED:
            if norm(feed) not in have:
                new.append([feed, correct, "confirmed"]); have.add(norm(feed))
        for feed, correct in AUTO_ALIASES:
            if norm(feed) not in have:
                new.append([feed, correct, "auto"]); have.add(norm(feed))
        if new:
            ws.append_rows(new, value_input_option="RAW")
            print(f"Saved {len(new)} alias(es) to the '{ALIASES_TAB}' tab.", file=sys.stderr)
    except Exception as e:
        print(f"could not update '{ALIASES_TAB}' tab: {e}", file=sys.stderr)

def open_review_items():
    """The STILL-OPEN 'Needs Review' rows. Pure, so the ACK rule below is testable without gspread.

    Drop anything already resolved (Yes/New) so the tab only ever shows open items — EXCEPT an
    un-attributable performance. ACK is answered against a NAME ("stop asking me whether this feed
    spelling is that squad player"); an unattributed row is open against a MATCH that is being HELD.
    If a 'New' answer carried a team the squad matcher still can't resolve, the ACK would delete the
    only actionable row while the match stayed LIVE forever with nothing left to click — a guard on
    one side without its mirror, which is the recurring shape of defect in this file. Such a row
    lives or dies on whether the player is still un-attributable, and on nothing else."""
    return [r for r in dedup_review([r for r in REVIEW if r["kind"] == "review"])
            if r.get("unattributed") or norm(r["feed"]) not in ACK]

def write_review_tab():
    """Publish the still-unmatched players to 'Needs Review' as: closest-match guess + a
    'Correct?' column you answer Yes (guess is right) or New (real player the squad list is
    missing). A Yes/New is applied next run (then the row drops off). Prior answers are
    preserved. Rewritten each full run."""
    sh = open_gsheet()
    if sh is None:
        return
    import gspread
    header = ["Tour", "Team", "Feed Name", "Closest Match", "Role", "Correct? (Yes/New)"]
    items = open_review_items()
    if items:
        rows = [[r["tour"], r["team"], r["feed"],
                 PRIOR_CLOSEST.get((r["tour"], r["feed"])) or r["suggestion"],   # keep any name you typed
                 PRIOR_ROLE.get((r["tour"], r["feed"])) or r.get("role", ""),     # keep any role you set
                 PRIOR_CONFIRM.get((r["tour"], r["feed"]), "")] for r in items]
    else:
        rows = [["—", "", "All players matched cleanly 🎉", "", "", ""]]
    try:
        try:
            ws = sh.worksheet(REVIEW_TAB)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=REVIEW_TAB, rows=len(rows) + 10, cols=len(header) + 1)
        ws.clear()
        ws.update(range_name="A1", values=[header] + rows, value_input_option="RAW")
        print(f"Wrote {len(items)} review item(s) to the '{REVIEW_TAB}' tab "
              f"(answer 'Correct?' Yes/New; applied next run).", file=sys.stderr)
    except Exception as e:
        print(f"could not write '{REVIEW_TAB}' tab: {e}", file=sys.stderr)

def write_anomaly_tab():
    """Publish identity anomalies to the 'Identity Anomalies' tab: (1) the AUDIT of past splits
    (registry/identity_splits.json) still awaiting your vet, and (2) any merge/duplicate the bot
    DETECTED this run. Each row has a 'Different players? (Yes/No)' column — Yes = they really are
    distinct people (keep/do the split), No = same person (undo / it's a legit merge). Only OPEN
    items show (vetted/acked drop off), mirroring Needs Review. Separate tab — never touches the
    Needs Review / Player Aliases no-code flow."""
    sh = open_gsheet()
    if sh is None:
        return
    import gspread
    header = ["Tour", "Type", "Player ID", "Display", "Players / Names Involved",
              "Bot Finding", "Different players? (Yes/No)", "Status"]
    rows = []
    # (1) past splits — show only those not yet confirmed (pending vet or undo-requested)
    for s in _load_splits().get("splits", []):
        if s.get("status") == "confirmed":
            continue
        key = ("past split", f"split:{s['id']}")
        players = s.get("players", [])
        rows.append(["—", "past split", f"split:{s['id']}",
                     " ↔ ".join(p.get("display", "") for p in players),
                     " | ".join(f"{p.get('pid')}={p.get('display')}" for p in players),
                     f"Separated on {s.get('ts','')} — {s.get('reason','')}. "
                     "'Yes' = correct (different people, keep split). 'No' = same person (undo).",
                     PRIOR_ANOMALY.get(key, ""), s.get("status", "")])
    # (2) detected this run — dedup by (kind, pid), drop ones already answered
    by_key = {}
    for a in ANOMALIES:
        by_key.setdefault((a["kind"], a["pid"]), a)
    for (kind, pid), a in by_key.items():
        typ = "false merge" if kind == "false_merge" else "duplicate id"
        if (typ, pid) in ANOMALY_ACK:
            continue
        rows.append([a.get("tour", "—"), typ, pid, a.get("display", ""),
                     ", ".join(a.get("names", [])),
                     a.get("finding", "") + "  ('Yes' = different players → split; 'No' = same person → ignore)",
                     PRIOR_ANOMALY.get((typ, pid), ""), "detected this run"])
    if not rows:
        rows = [["—", "—", "", "No open identity anomalies 🎉", "", "", "", ""]]
    try:
        try:
            ws = sh.worksheet(ANOMALY_TAB)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=ANOMALY_TAB, rows=len(rows) + 10, cols=len(header) + 1)
        ws.clear()
        ws.update(range_name="A1", values=[header] + rows, value_input_option="RAW")
        print(f"Wrote {len(rows)} row(s) to the '{ANOMALY_TAB}' tab "
              f"(answer 'Different players?' Yes/No).", file=sys.stderr)
    except Exception as e:
        print(f"could not write '{ANOMALY_TAB}' tab: {e}", file=sys.stderr)

def _save_cricsheet_learned():
    """Persist spellings learned from cricsheet person ids. Not a guess store — every entry was
    resolved by ID, so it is safe to treat as permanent. Committed by the workflow like the other
    registry side-files; build_registry can fold these into aliases on its next run."""
    try:
        try:
            data = json.load(open(CRICSHEET_LEARNED_PATH))
        except Exception:
            data = {"note": "Feed spellings resolved via a cricsheet person id (info.registry.people) "
                            "whose name the alias table didn't know. ID-anchored, never fuzzy — this "
                            "is how cricsheet's initials form ('PWH de Silva') stays joined to the "
                            "announced name ('Wanindu Hasaranga').", "aliases": {}}
        data.setdefault("aliases", {}).update(
            {n: {"pid": p, "display": PID2DISP.get(p, p)} for n, p in CS_LEARNED.items()})
        json.dump(data, open(CRICSHEET_LEARNED_PATH, "w"), indent=1, ensure_ascii=False)
        print(f"[identity] learned {len(CS_LEARNED)} cricsheet spelling(s) by id", file=sys.stderr)
    except Exception as e:
        print(f"could not update cricsheet_learned_aliases.json: {e}", file=sys.stderr)

NEEDS_CRICINFO_TAB = "Needs Cricinfo ID"
CI_BRIDGES_PATH = os.path.join(os.path.dirname(__file__), "registry", "manual_ci_bridges.json")

def promote_new_players():
    """Upgrade sheet-added players from a placeholder `slug:` pid to their real `ci:` id.

    WHY THIS HAS TO EXIST. Identity lives in two stores that never met:
      · registry/players.json  — built by build_registry from the SQUAD files, `ci:`-anchored.
      · registry/new_players.json — written at RUNTIME when a player turns up who is in no squad
        (Needs Review "New", or an auto-added silent drop). `slugify()` mints `slug:<name>`.
    `build_registry.py` contains zero references to new_players.json, so a `slug:` minted here was
    never revisited — filling the player's cricinfo id into the Needs Cricinfo ID tab produced a
    bridge that build_registry applied to squad names only, and the slug survived forever. That is
    why Calvin Harrison, Sean Dickson, Matthew Revis, Andrew Tye and Saif Zaib stayed on slug pids
    while their official-card rows sat unjoinable beside them.

    Promotes ONLY on a human-asserted bridge (an id someone typed into the tab). Deliberately does
    NOT promote by matching the global registry's aliases: that path can merge two people silently,
    which is the failure this whole exercise exists to stop."""
    try:
        bridges = json.load(open(CI_BRIDGES_PATH))
    except Exception:
        return 0
    name2ci = {}
    for key, e in bridges.items():
        cid = str(e.get("cricinfo_id") or key.split(":", 1)[-1])
        for n in e.get("names", []):
            name2ci.setdefault(n, cid)
    changed = 0
    for e in NEW_PLAYERS_DATA.get("players", []):
        pid = e.get("pid", "")
        if not pid.startswith(("slug:", "uncapped:")):
            continue
        cands = {name2ci[n] for n in
                 [norm(e.get("display", ""))] + [norm(a) for a in e.get("aliases", [])]
                 if n in name2ci}
        if len(cands) != 1:
            if len(cands) > 1:      # two ids claim this player — never guess between them
                print(f"  promote: {e.get('display')!r} matches {len(cands)} cricinfo ids "
                      f"{sorted(cands)} — left on {pid} for a human", file=sys.stderr)
            continue
        new_pid = f"ci:{cands.pop()}"
        print(f"  promote: {e.get('display'):24} {pid} -> {new_pid}", file=sys.stderr)
        e["pid"] = new_pid
        ALIAS2PID[norm(e.get("display", ""))] = new_pid
        for a in e.get("aliases", []):
            ALIAS2PID[norm(a)] = new_pid
        PID2DISP[new_pid] = e.get("display") or new_pid
        changed += 1
    if changed:
        _save_new_players(NEW_PLAYERS_DATA)
        print(f"[identity] promoted {changed} sheet-added player(s) off placeholder pids",
              file=sys.stderr)
    # Anything still on a placeholder has no id yet — put it in front of a human. This is what
    # makes the loop self-closing: mint slug -> row appears in the tab -> id filled -> promoted.
    # Without it a slug is invisible until it happens to break a scorecard.
    stale = [e for e in NEW_PLAYERS_DATA.get("players", [])
             if e.get("pid", "").startswith(("slug:", "uncapped:"))]
    for e in stale:
        NEEDS_CRICINFO.append({
            "player": e.get("display", ""), "current_pid": e["pid"],
            "tour": (e.get("tours") or [""])[0], "team": e.get("team", ""),
            "closest_guess": "sheet-added player, still on a placeholder id — "
                             "fill the cricinfo id to anchor them permanently"})
    if stale:
        print(f"[identity] {len(stale)} player(s) still on placeholder pids -> "
              f"'{NEEDS_CRICINFO_TAB}'", file=sys.stderr)
    return changed

def read_needs_cricinfo():
    """Consume the ids a human filled into 'Needs Cricinfo ID' -> registry/manual_ci_bridges.json.

    THE MISSING HALF. The tab was only ever WRITTEN — build_registry pushed unresolved players to
    it and `tour_sync_finalize` appended more, but nothing anywhere read `cricinfo_id_FILL_HERE`
    back. So filling it in did precisely nothing, and the "self-maintaining identity loop" in the
    docs was self-maintaining in one direction only.

    A filled id is a HUMAN identity assertion, which is the gate — but we still refuse the two ways
    it can be wrong:
      · a non-numeric id (typo / pasted URL) is rejected, never written;
      · an id already held by a DIFFERENT-looking player is NOT silently merged. That is the
        Dale->Glenn class: one keystroke would fuse two people permanently. It goes to Identity
        Anomalies for a Yes/No instead.
    Bridges are recorded, not applied mid-run: `build_registry` re-anchors properly on the next
    build (which is also what re-stamps the draft's pids), so identity never changes underfoot
    while a run is scoring."""
    sh = open_gsheet()
    if sh is None:
        return 0
    import gspread
    try:
        ws = sh.worksheet(NEEDS_CRICINFO_TAB)
        rows = ws.get_all_values()
    except gspread.WorksheetNotFound:
        return 0
    except Exception as e:
        print(f"needs-cricinfo read: {e}", file=sys.stderr)
        return 0
    if len(rows) < 2:
        return 0
    hdr = [h.strip() for h in rows[0]]
    def col(name, default=-1):
        return hdr.index(name) if name in hdr else default
    ci_i, pl_i, pid_i = col("cricinfo_id_FILL_HERE"), col("player"), col("current_pid")
    if ci_i < 0 or pl_i < 0:
        return 0
    try:
        bridges = json.load(open(CI_BRIDGES_PATH))
    except Exception:
        bridges = {}
    # cricinfo id -> (pid, entry) for the merge guard below.
    known_ci = {}
    try:
        _reg = json.load(open(os.path.join(os.path.dirname(__file__), "registry", "players.json")))
        for _pid, _e in _reg.get("players", {}).items():
            _c = _e.get("cricinfo_id")
            if _c:
                known_ci.setdefault(str(_c), (_pid, _e))
    except Exception:
        pass
    added = registered = 0
    for r in rows[1:]:
        raw = (r[ci_i].strip() if len(r) > ci_i else "")
        player = (r[pl_i].strip() if len(r) > pl_i else "")
        cur_pid = (r[pid_i].strip() if pid_i >= 0 and len(r) > pid_i else "")
        if not raw or not player:
            continue
        cid = re.sub(r"\D", "", raw)          # tolerate a pasted profile URL
        if not cid:
            print(f"  needs-cricinfo: '{raw}' for {player!r} is not a cricinfo id — ignored",
                  file=sys.stderr)
            continue
        # Refuse a silent merge into someone who is already anchored to this id under a name that
        # isn't plausibly the same person.
        # REVERSE guard: the name you are bridging may ALREADY resolve to somebody else. That is
        # how "Kiran Carlson" ended up bound to Liam Dawson (ci:211855) — same team, so the Needs
        # Review "New" flow's closest-match linked them and every Carlson row scored as Dawson.
        # Filling in Carlson's real id would otherwise leave BOTH mappings live.
        prior = resolve_pid(player)
        _prior_disp = PID2DISP.get(prior, "") if prior else ""
        if (prior and prior != f"ci:{cid}"
                and not same_person_plausible(_prior_disp, player)
                and not long_form_plausible(_prior_disp, player)):
            ANOMALIES.append({
                "tour": CURRENT_TOUR or "(needs-cricinfo)", "kind": "false_merge",
                "pid": prior, "display": PID2DISP.get(prior, prior),
                "context": NEEDS_CRICINFO_TAB, "names": [player, PID2DISP.get(prior, "")],
                "finding": f"{player!r} is being bridged to cricinfo {cid}, but it currently "
                           f"resolves to {PID2DISP.get(prior)!r} ({prior}) — one of the two is a "
                           f"smear; the old alias must be removed or the points stay misattributed"})
            print(f"  needs-cricinfo: {player!r} already resolves to {prior} "
                  f"({PID2DISP.get(prior)!r}) — flagged, bridge still recorded", file=sys.stderr)
        owner = known_ci.get(cid)
        if (owner and not same_person_plausible(owner[1].get("display", ""), player)
                and not long_form_plausible(owner[1].get("display", ""), player)):
            ANOMALIES.append({
                "tour": CURRENT_TOUR or "(needs-cricinfo)", "kind": "false_merge",
                "pid": owner[0], "display": owner[1].get("display", ""),
                "context": NEEDS_CRICINFO_TAB, "names": [player, owner[1].get("display", "")],
                "finding": f"cricinfo {cid} was filled in for {player!r} but is already "
                           f"{owner[1].get('display')!r} — refusing to merge two people on one id"})
            print(f"  needs-cricinfo: REFUSED {player!r} -> ci:{cid} (already "
                  f"{owner[1].get('display')!r}) — sent to Identity Anomalies", file=sys.stderr)
            continue
        key = f"ci:{cid}"
        # An official-card-only row (current_pid "cs:<hash>") belongs to nobody: no squad slot, no
        # new_players entry. A bridge alone can't help them — build_registry only anchors squad
        # names, and promote_new_players only re-pids existing entries — so their points stayed
        # unjoinable even after you filled the id (KNA Bandara). Give them an entry to be anchored
        # ON, so the loop closes for every row type, not just squad-adjacent ones.
        if cur_pid.startswith("cs:") and not any(
                x.get("pid") == key for x in NEW_PLAYERS_DATA.get("players", [])):
            register_new_player(pid=key, display=player, feed=player, team="",
                                role="?", tour=CURRENT_TOUR or "", source="needs-cricinfo")
            registered += 1
            print(f"  needs-cricinfo: registered {player!r} as {key} "
                  f"(was official-card-only, in no squad)", file=sys.stderr)
        e = bridges.setdefault(key, {"cricinfo_id": cid, "names": []})
        # Only a slug:/uncapped: pid carries a real NAME to recover ("slug:calvin-harrison" ->
        # "calvin harrison"). A cs: pid carries a cricsheet HASH, which is not a name and must
        # never be registered as an alias.
        extra = (norm(cur_pid.split(":", 1)[-1].replace("-", " "))
                 if cur_pid.startswith(("slug:", "uncapped:")) else "")
        for nm in (norm(player), extra):
            if nm and nm not in e["names"]:
                e["names"].append(nm); added += 1
        e["names"] = sorted(set(e["names"]))
    if registered:
        _save_new_players(NEW_PLAYERS_DATA)
    if added:
        try:
            json.dump(bridges, open(CI_BRIDGES_PATH, "w"), indent=1, ensure_ascii=False)
            print(f"[identity] recorded {added} name(s) into manual_ci_bridges.json from "
                  f"'{NEEDS_CRICINFO_TAB}' — run build_registry to apply", file=sys.stderr)
        except Exception as e:
            print(f"could not write manual_ci_bridges.json: {e}", file=sys.stderr)
    return added

PENDING_CI_PATH = os.path.join(os.path.dirname(__file__), "registry", "needs_cricinfo_pending.json")

def pending_registry_gaps():
    """build_registry's unresolved-squad-name snapshot, READ-ONLY, as 'Needs Cricinfo ID' rows.

    WHAT WENT WRONG (measured 14 Aug 2026). TWO functions write that tab and neither knew the
    other's input. `tour_sync_finalize.write_needs_cricinfo_tab()` pushes
    registry/needs_cricinfo_pending.json and runs ONLY on tour INGEST; the writer below pushes
    RUNTIME discoveries and its docstring said it "deliberately does NOT touch
    needs_cricinfo_pending.json". The stated concern was real but was about WRITING that file —
    build_registry rewrites it wholesale, so a second writer would clobber it. It was implemented
    as never READING it either: one guard applied a direction too wide, and the file became
    write-once-read-never on every path except a tour ingest that had already happened. Cost:
    23 CPL squad names sat in the file since ingest while the live tab showed 52 rows, 8 of them
    CPL and every one of those 8 from the runtime path (2 minted `ci:`, 6 `cb:` cricbuzz-bridge).
    Nobody was ever asked about the 23.

    This still never writes the file — build_registry keeps sole ownership. Two suppressions, both
    resting on POSITIVE evidence (never on an absence, which is how a queue goes silent):
      · ANSWERED — a filled-in id is recorded by `read_needs_cricinfo` into manual_ci_bridges.json,
        NOT back into the pending file (build_registry only rewrites that on the next build). So an
        answered player stays in the snapshot for days; without this check he is re-asked on every
        run from the moment his row is deleted from the tab.
      · ALREADY ANCHORED — the name now resolves through the live registry to a real pid
        (not slug:/uncapped:/cs:). Exact alias lookup in the registry's own index, no fuzzy match:
        if points are already being attributed to that pid, asking for the id again is noise.
    An unreadable bridges file or an empty registry FAILS OPEN (surface the row) — an absence must
    not read as "already handled"."""
    try:
        pending = json.load(open(PENDING_CI_PATH))
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"needs-cricinfo: pending file unreadable ({e}) — "
              f"registry gaps NOT surfaced this run", file=sys.stderr)
        return []
    if not isinstance(pending, list):
        return []
    try:
        answered = {n for e in json.load(open(CI_BRIDGES_PATH)).values()
                    for n in (e.get("names") or [])}
    except Exception:
        answered = set()          # FAIL OPEN — see docstring
    rows, skipped = [], 0
    for p in pending:
        pid = str(p.get("current_pid") or "").strip()
        name = str(p.get("player") or "").strip()
        if not pid or not name:
            continue
        # A slug:/uncapped: pid carries a recoverable NAME ("uncapped:odean-smith" -> "odean
        # smith"); a cs: pid carries a cricsheet HASH, which is not a name (read_needs_cricinfo
        # draws the same line when it records aliases).
        slug_name = (norm(pid.split(":", 1)[-1].replace("-", " "))
                     if pid.startswith(("slug:", "uncapped:")) else "")
        if answered & {norm(name), slug_name} - {""}:
            skipped += 1
            continue
        anchored = ALIAS2PID.get(norm(name))
        if anchored and not anchored.startswith(("slug:", "uncapped:", "cs:")):
            skipped += 1
            continue
        # ⛔ DO NOT ASK ABOUT A PLAYER WHO HAS NOT PLAYED. build_registry lists every squad name it
        # could not anchor, but for an UNCAPPED player that is not a gap — it is simply too early.
        # He is not in people.csv (uncapped), may have no cricinfo page yet, and needs no id: his
        # `uncapped:` pid joins correctly because the sheet and the draft carry the SAME placeholder.
        # The instant he debuts, ESPN's athlete.id IS his cricinfo id and the promotion is automatic.
        # Measured on CPL: of 23 surfaced, 21 had never played (nothing findable, by anyone) and 2
        # already had an id ESPN had handed us. The correct number of questions for a human was 0.
        # So ask only about someone who PLAYED and still has no id — genuinely rare, and real.
        if norm(name) not in APPEARED and slug_name not in APPEARED:
            skipped += 1
            continue
        rows.append({"player": name, "current_pid": pid, "tour": p.get("tour", ""),
                     "team": p.get("team", ""),
                     "closest_guess": (f"squad name build_registry could not anchor"
                                       + (f" · closest offline record: {p['closest_guess']}"
                                          if p.get("closest_guess") else ""))})
    if rows or skipped:
        print(f"[identity] registry pending file: {len(rows)} unresolved squad name(s) to surface"
              + (f", {skipped} already answered/anchored" if skipped else ""), file=sys.stderr)
    return rows

def write_needs_cricinfo_tab():
    """Push identity gaps into the SAME 'Needs Cricinfo ID' tab build_registry/tour_sync_finalize
    use — one place, one habit: drop the cricinfo id in the last column and `manual_ci_bridges`
    picks it up on the next build.

    TWO SOURCES since 14 Aug 2026: gaps found while SCORING (NEEDS_CRICINFO) plus build_registry's
    pending file (`pending_registry_gaps`, read-only — see there for the 23 that never reached a
    human). Runtime rows go first so they win the dedupe: they carry live per-match context
    ("official card only (Match 3 …)") that the ingest-time snapshot cannot.

    Appends only (dedupe by current_pid), so a filled-in id is never clobbered and a re-run never
    duplicates a row. `tour_sync_finalize`'s twin is append-only against the same key, so the two
    writers cannot clobber each other on the TAB either — whichever runs first appends, the other
    sees the row present and skips."""
    queue = list(NEEDS_CRICINFO) + pending_registry_gaps()
    if not queue:
        return
    sh = open_gsheet()
    if sh is None:
        print(f"  (needs-cricinfo tab skipped — no creds; {len(queue)} pending)", file=sys.stderr)
        return
    import gspread
    header = ["player", "current_pid", "tour", "team", "closest_guess", "cricinfo_id_FILL_HERE"]
    try:
        try:
            ws = sh.worksheet(NEEDS_CRICINFO_TAB)
            existing = ws.get_all_values()
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=NEEDS_CRICINFO_TAB, rows=200, cols=len(header))
            ws.update(range_name="A1", values=[header], value_input_option="RAW")
            existing = [header]
        # Dedupe key resolved BY HEADER NAME. `read_needs_cricinfo` reads its columns by name while
        # this writer read column index 1 blind — so a human inserting one column ahead of
        # current_pid would make every existing row unrecognisable and re-append all 52, including
        # the 45 that already carry a filled-in id. Same tab, two column conventions, one of them
        # silent.
        hdr = [h.strip() for h in (existing[0] if existing else header)]
        pid_i = hdr.index("current_pid") if "current_pid" in hdr else -1
        if pid_i < 0:
            print(f"'{NEEDS_CRICINFO_TAB}': no 'current_pid' column in the header — refusing to "
                  f"append ({len(queue)} gap(s) held). A dedupe key we cannot read cannot promise "
                  f"no duplicates, and duplicating an answered row loses the answer.",
                  file=sys.stderr)
            return
        have = {(r[pid_i].strip() if len(r) > pid_i else "") for r in existing[1:]}
        seen, rows, from_pending = set(), [], 0
        for i, e in enumerate(queue):
            key = str(e.get("current_pid") or "").strip()
            if not key or key in have or key in seen:
                continue
            seen.add(key)
            from_pending += (i >= len(NEEDS_CRICINFO))
            rows.append([e.get("player", ""), key, e.get("tour", ""), e.get("team", ""),
                         e.get("closest_guess", ""), ""])
        if rows:
            ws.append_rows(rows, value_input_option="RAW")
            print(f"'{NEEDS_CRICINFO_TAB}': appended {len(rows)} identity gap(s) for a human to "
                  f"fill ({from_pending} from the registry's pending file)", file=sys.stderr)
        else:
            print(f"'{NEEDS_CRICINFO_TAB}': nothing new ({len(queue)} already listed)",
                  file=sys.stderr)
    except Exception as e:
        print(f"needs-cricinfo tab: {e}", file=sys.stderr)

def write_settlement_tab():
    """Publish the frozen settled baseline so the draft app can diff the live sheet against it.

    Read-only for humans — there is no 'Correct Value' column, deliberately. Anything editable
    isn't a settlement record. The app joins on Match Key + Player ID and renders the delta."""
    sh = open_gsheet()
    if sh is None:
        return
    import gspread
    header = ["Match Key", "Tour", "Match", "Date", "Team", "Player ID", "Full Name",
              "Settled Points", "Settled Status", "Settled Source", "Frozen At", "Provenance"]
    rows = [[r.get("match_key", ""), r.get("tour", ""), r.get("match", ""), r.get("date", ""),
             r.get("team", ""), r.get("pid", ""), r.get("full", ""), r.get("points", ""),
             r.get("status", ""), r.get("source", ""), r.get("frozen_at", ""),
             r.get("provenance", "live")]
            for r in sorted(SETTLEMENTS.values(),
                            key=lambda x: (x.get("date", ""), x.get("match_key", ""), x.get("full", "")))]
    if not rows:
        rows = [["—", "no settled matches recorded yet", "", "", "", "", "", "", "", "", "", ""]]
    try:
        try:
            ws = sh.worksheet(SETTLEMENT_TAB)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=SETTLEMENT_TAB, rows=len(rows) + 50, cols=len(header) + 1)
        ws.clear()
        ws.update(range_name="A1", values=[header] + rows, value_input_option="RAW")
        ws.freeze(rows=1)
        print(f"'{SETTLEMENT_TAB}': {len(rows)} frozen baseline row(s)", file=sys.stderr)
    except Exception as e:
        print(f"settlement tab: {e}", file=sys.stderr)

def write_recon_tab():
    """Publish OPEN Recon Review items: one row per (player, differing field) you haven't
    resolved yet. Pick 'Correct Value' (S1 / S2 / Manual) and the row DROPS next run — the
    approval is persisted to registry/recon_overrides.json (committed back by the workflow), so
    the correction sticks even though the row is gone. Rewritten each full run."""
    sh = open_gsheet()
    if sh is None:
        return
    import gspread
    # S1 IS POSITIONAL: it is "the tour's second witness", cricapi by default and cricbuzz on any
    # tour with `cricbuzz_series` set. One header serves every tour in the run, so build_recon_rows
    # also stamps the feed name into the S1 CELL of a non-cricapi row — a stored approval records
    # only "S1", and a number with no feed beside it is unanswerable.
    header = ["Tour", "Match", "Date", "Player ID", "Full Name", "Param",
              "S1 = 2nd witness: cricbuzz where the tour enables it, else cricapi (L1) / held provisional (L2)",
              "S2 = ESPN (L1) / OFFICIAL cricsheet (L2)",
              "Correct Value", "Manual Value", "Status", "Match Key"]
    status_text = {"player": "⚠ pick a value", "l2": "official revision — approve to apply",
                   "id": "⛔ IDENTITY — fix the registry alias, or S2 to accept the official card",
                   "espn": "⛔ ESPN CARD INCOMPLETE — this match is NOT scored until you answer"}
    seen, rows = set(), []
    for r in RECON_REVIEW:
        key = (r["match_key"], r.get("pid", ""), r.get("param", ""))
        if key in seen or key in RECON_ACK:   # already answered -> dropped (resolved)
            continue
        seen.add(key)
        rows.append([r.get("tour", ""), r.get("match", ""), r.get("date", ""), r.get("pid", ""),
                     r.get("full", ""), r.get("param", ""), str(r.get("s1", "")), str(r.get("s2", "")),
                     PRIOR_RECON.get(key, ""), PRIOR_MANUAL.get(key, ""),
                     status_text.get(r.get("tier", ""), "⚠ pick a value"), r["match_key"]])
    n_pending = len(rows)
    if not rows:
        rows = [["—", "All feeds reconciled cleanly 🎉", "", "", "", "", "", "", "", "", "", ""]]
    try:
        try:
            ws = sh.worksheet(RECON_TAB)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=RECON_TAB, rows=len(rows) + 10, cols=len(header) + 1)
        ws.clear()
        ws.update(range_name="A1", values=[header] + rows, value_input_option="RAW")
        # Native dropdown on 'Correct Value' (col I). Best-effort — if the gspread version lacks
        # add_validation, degrade silently to free-text (readback accepts S1/S2/Manual as text).
        try:
            from gspread.utils import ValidationConditionType
            ws.add_validation(f"I2:I{1 + len(rows)}", ValidationConditionType.one_of_list,
                              ["S1", "S2", "Manual"], strict=False, showCustomUi=True)
        except Exception as e:
            print(f"(recon dropdown skipped: {e})", file=sys.stderr)
        print(f"Wrote {n_pending} open recon item(s) to '{RECON_TAB}' "
              f"(pick 'Correct Value' to resolve; resolved rows drop next run).", file=sys.stderr)
    except Exception as e:
        print(f"could not write '{RECON_TAB}' tab: {e}", file=sys.stderr)

def write_to_gsheet(cols, rows):
    """Write the points rows into this tour's tab via the shared service-account handle."""
    sh = open_gsheet()
    if sh is None:
        print("GSHEET_ID set but GOOGLE_SERVICE_ACCOUNT_JSON missing — skipping sheet write.", file=sys.stderr)
        return
    import gspread
    # A wipe from a failed upstream fetch is prevented earlier (run_tour aborts before here).
    # Reaching here with 0 rows means a valid series with no completed T20Is yet -> clean header.
    try:
        ws = sh.worksheet(GSHEET_TAB)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=GSHEET_TAB, rows=len(rows) + 10, cols=len(cols) + 2)
    ws.clear()
    ws.update(range_name="A1", values=[cols] + rows, value_input_option="RAW")
    # Highlight loudly (decision 3): red-fill rows of a match whose official revision is pending
    # approval, so a revised-but-not-yet-approved result can't hide. Best-effort formatting.
    try:
        from gspread.utils import rowcol_to_a1
        si = cols.index("Match Status")
        last_col = re.sub(r"\d+", "", rowcol_to_a1(1, len(cols)))
        red = {"backgroundColor": {"red": 1, "green": 0.8, "blue": 0.8}}
        flagged = [i + 2 for i, row in enumerate(rows)
                   if len(row) > si + 1 and row[si] == "COMPLETED_FLAGGED"
                   and "revision" in row[si + 1]]
        if flagged:
            ws.batch_format([{"range": f"A{n}:{last_col}{n}", "format": red} for n in flagged])
    except Exception as e:
        print(f"(recon highlight skipped: {e})", file=sys.stderr)
    print(f"Wrote {len(rows)} rows to Google Sheet tab '{GSHEET_TAB}'.", file=sys.stderr)

if __name__ == "__main__":
    main()
