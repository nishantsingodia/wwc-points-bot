#!/usr/bin/env python3
"""
tour_sync — add cricket tours and generate the draft-app + points-bot artifacts for them, so a
new tour appears in wwc-draft and gets scored by the bot with NO manual code edits.

FEED: ESPN ONLY. cricapi is gone from this module — no key, no quota, no rotation, and nothing
here can re-introduce it on a future tour. ESPN is the primary/only base feed; Cricbuzz is the
L1 second witness (resolved into `cricbuzz_series` at ingest, but only when it can be VALIDATED);
cricsheet is the L2 arbiter, later, at scoring time.

Pipeline (run daily from GH Actions):
  pick tours — Column A of the TOUR CONTROL / TOUR STATUS tab (--from-status-sheet; the path CI
               runs), one name on the CLI (--espn-tour), or ESPN watchlist search (--discover)
    -> per tour: name -> ESPN league id -> fixtures (dated scoreboard scan) -> FULL squads
       (event summary.squads)
    -> tours.json ids: espn_series = the ESPN league id; cricbuzz_series = a PROPOSED Cricbuzz id
       that we then VALIDATE against its own fixture DATES (never adopted on a name alone)
    -> generate draft artifacts (data/matches.json, data/players-raw.json, data/team-codes.json)
       + bot artifacts (tours.json, <tour>_squads.json, toss_windows.json)
    -> validate + commit both repos + trigger the Vercel deploy hook (done by the workflow)

This module is the GENERATOR. The commit/deploy wiring lives in the GH Actions workflow.
Squad squad_number is a role-group seed; it self-corrects from the sheet's Bat Order after
match 1. efppm is a role-based pick-guide seed. pids resolve via the registry (returning
players) else slug:, upgraded post-match by build_registry.py / backfill_draft_pids.py.

Usage (a discovery path is REQUIRED — there is no default feed to fall back on):
  python3 tour_sync.py --dry-run --from-status-sheet          # what CI runs, dry
  python3 tour_sync.py --dry-run --espn-tour 'India tour of Zimbabwe 2026'
  python3 tour_sync.py --dry-run --discover                   # ESPN watchlist search
  python3 tour_sync.py --apply  --from-status-sheet           # write both repos
  python3 tour_sync.py --emit OUTDIR --from-status-sheet      # artifacts to OUTDIR, no repo writes
"""
import argparse, json, os, re, sys, time, urllib.error, urllib.request, urllib.parse
from datetime import datetime, timedelta, timezone

DRAFT = os.environ.get("DRAFT_REPO", os.path.expanduser("~/wwc-draft"))
BOT = os.path.dirname(os.path.abspath(__file__))
IST = timezone(timedelta(hours=5, minutes=30))

# ── Watchlist (the curation guardrail; edit here to broaden/narrow) ──────────────
# A series is in-scope if it is a bilateral between two MAJOR_TEAMS, OR its name
# matches a MAJOR_LEAGUE pattern. Everything else (associate ICC qualifiers, U19,
# domestic 2nd-XI, obscure local leagues) is skipped.
MAJOR_TEAMS = {
    "india", "australia", "england", "pakistan", "south africa", "new zealand",
    "sri lanka", "bangladesh", "west indies", "afghanistan", "ireland", "zimbabwe",
}
MAJOR_LEAGUES = [
    "indian premier league", "the hundred", "big bash", "pakistan super league",
    "caribbean premier league", "sa20", "international league t20", "ilt20",
    "major league cricket", "lanka premier league", "bangladesh premier league",
    "super smash", "vitality blast", "county championship t20", "cpl", "psl", "bbl",
    "womens premier league", "wbbl", "the women's hundred",
]
# hard denylist (name substrings) — never ingest even if teams look major
DENY = ["u19", "under-19", "under 19", "unofficial", "development", "emerging",
        "xi ", "2nd xi", "a-team", "academy", "invitation", "warm-up", "warm up",
        "practice", "legends", "masters"]
# Event summaries scanned for squads when building a tour from ESPN. One summary yields only the
# 2 teams in that match, so an N-team league needs several; capped so a long league can't fan out
# into a hundred calls (7 franchises are covered in ~4-6 events).
MAX_SQUAD_EVENTS = 25

# ── FORMAT VOCABULARY — WHO SPEAKS WHAT ───────────────────────────────────────────
# ESPN (the only feed here) states a fixture's format on competitions[0].class:
#   · class.eventType        — NORMALIZED. Exactly "T20" | "ODI" | "Test" in every series
#                              measured. THIS is what we key on.
#   · class.generalClassCard — the human label, NOT normalized: "Twenty20" | "T20I" |
#                              "Women T20" | "ODI" | "One-Day Internationals" | "Test".
#                              Fallback only, for a series that leaves eventType blank.
# MEASURED 20 Aug 2026 against six live series: CPL 8623 (T20/Twenty20), Hundred M 1521176
# (T20/Twenty20), ENG-PAK Test 23806 (Test/Test), NZ-WI ODI 1538619 (ODI/ODI), IND-ENG T20I
# 1496489 (T20/T20I), WWC 1483859 (T20/Women T20).
# cricapi's `matchType` vocabulary ("t20i" / "hundred" / null) is GONE. Do NOT re-add it here.
# The BOT speaks a third vocabulary — its SCORING formats "T20"/"ODI"/"HUN"/"TEST" — and gen_tour
# is the one place that translates (score_fmt), so the value written to tours.json is the bot's,
# while everything in this module is ESPN's.
# ESPN buckets The Hundred as eventType "T20" (verified on 1521176), so a Hundred fixture rides
# the T20 discovery bucket and gen_tour re-labels the TOUR as "HUN" for scoring.
# ⚠ class can be MISSING/blank on some series. That is exactly the failure that once admitted
# 0 of 34 matches for a whole competition, so a blank falls back — in order — to the fixture's
# own description, then the SERIES-level declared format (_declared_fmt), then a LOUD drop.
ESPN_FMT_BUCKET = {
    # class.eventType (normalized)
    "t20": "T20", "odi": "ODI",
    # class.generalClassCard variants, for a blank eventType
    "twenty20": "T20", "t20i": "T20", "women t20": "T20", "womens t20": "T20",
    "one-day internationals": "ODI", "women odi": "ODI", "womens odi": "ODI", "list a": "ODI",
}
# Formats ESPN states plainly that this module deliberately does NOT ingest (it only mints
# ODI/T20 tours). Known-and-skipped, so the drop is quiet instead of a "vocabulary changed" alarm.
ESPN_FMT_SKIP = {"test", "first class", "fc", "4-day", "youth test"}
# ESPN league-level class ids (scoreboard leagues[].classId) — how a series whose per-fixture
# class block is blank can still be bucketed. MEASURED VALUES ONLY: an id that is not in this map
# is left UNRESOLVED rather than guessed, because a guessed series format mis-buckets a whole
# competition. "SKIP" = stated, and not a format we ingest.
ESPN_LEAGUE_CLASS = {
    "2": "ODI",    # One-Day Internationals      (measured: NZ v WI 1538619)
    "3": "T20",    # T20 Internationals          (measured: IND v ZIM 24301, IND v ENG 1496489)
    "6": "T20",    # domestic Twenty20           (measured: CPL 8623, Hundred M 1521176)
    "10": "T20",   # Women's T20 Internationals  (measured: WWC 1483859)
    "1": "SKIP",   # Test                        (measured: ENG v PAK 23806, with 11)
    "11": "SKIP",  # First class                 (measured: ENG v PAK 23806, with 1)
}

def _fmt_stated(m):
    """The format ESPN STATES for this one fixture — 'T20' | 'ODI' | None. No series fallback.

    Reads ESPN's vocabulary only (eventType, then generalClassCard, then ESPN's own event
    description e.g. "3rd T20I"). Returns None both for "ESPN said Test" and for "ESPN said
    nothing" — use _fmt_skipped to tell those apart."""
    for tok in ((m.get("espn_event_type") or ""), (m.get("espn_class_card") or "")):
        tok = tok.strip().lower()
        if tok in ESPN_FMT_BUCKET:
            return ESPN_FMT_BUCKET[tok]
        if tok in ESPN_FMT_SKIP:
            return None
    nm = (m.get("name") or "").lower()      # ESPN's description: "3rd T20I" / "12th Match"
    if "test" in nm:
        return None
    if "odi" in nm or "one-day" in nm:
        return "ODI"
    if "hundred" in nm or "t20" in nm or "100" in nm:
        return "T20"
    return None

def _fmt_skipped(m):
    """True when ESPN states a format we deliberately do not ingest (Test / first class)."""
    toks = [(m.get("espn_event_type") or "").strip().lower(),
            (m.get("espn_class_card") or "").strip().lower()]
    return any(t in ESPN_FMT_SKIP for t in toks) or "test" in (m.get("name") or "").lower()

def _fmt_of(m):
    """Bucket one ESPN fixture row into this module's discovery format ('T20'/'ODI'), or None.

    ESPN's own class wins; a Test-class fixture is dropped on purpose; and only a fixture ESPN
    left UNCLASSIFIED falls back to `declared_fmt`, the series-level format _espn_matchlist
    stamped on every row. None here means the caller must log the drop loudly."""
    f = _fmt_stated(m)
    if f or _fmt_skipped(m):
        return f
    return m.get("declared_fmt") or None

def _declared_fmt(class_ids, tour_name, matchlist):
    """The SERIES-level format ('T20'/'ODI'/None) + the reason, for fixtures ESPN left unclassified.

    Order, most authoritative first — every step reads ESPN's vocabulary or our own tour name,
    never cricapi's:
      1. the LEAGUE's own classId (ESPN_LEAGUE_CLASS; measured ids only),
      2. what its SIBLING fixtures state (a series is nearly always single-format),
      3. the tour's DECLARED format from its name ("hundred"/"t20" -> T20, "odi" -> ODI,
         "test" -> not ingested here).
    None means "no honest answer available" — the caller then DROPS those fixtures loudly rather
    than guessing. A guess here is what silently admitted 0 of 34 matches for a whole competition."""
    mapped = {ESPN_LEAGUE_CLASS[c] for c in class_ids if c in ESPN_LEAGUE_CLASS}
    real = mapped - {"SKIP"}
    if len(real) == 1:
        return real.pop(), f"ESPN league classId {sorted(class_ids)}"
    if mapped == {"SKIP"}:
        return None, f"ESPN league classId {sorted(class_ids)} is a format we do not ingest"
    sib = [f for f in (_fmt_stated(m) for m in matchlist) if f]
    if sib and len(set(sib)) == 1:
        return sib[0], f"all {len(sib)} fixture(s) ESPN DID classify say {sib[0]}"
    n = (tour_name or "").lower()
    if "test" in n:
        return None, "the tour's own name says Test — not ingested here"
    if "hundred" in n or "t20" in n:
        return "T20", "the tour's declared format (from its name)"
    if "odi" in n or "one-day" in n:
        return "ODI", "the tour's declared format (from its name)"
    return None, ("neither ESPN's league classId, its other fixtures, nor the tour name declares "
                  "a format")
DISCOVERY_WINDOW_DAYS = int(os.environ.get("SYNC_WINDOW_DAYS", "4"))
# Search tokens for --discover. These go to ESPN's search endpoint (site.web.api, keyless and
# unmetered — the old "fewer terms to save cricapi quota" reason is gone; the list stays short
# because every extra term costs a scoreboard probe per in-scope hit). ESPN matches substrings and
# candidates are de-duped by league id. "hundred" catches both the men's and women's Hundred.
# Override with SYNC_SEARCH_TERMS=a,b,c to scope a manual run (e.g. SYNC_SEARCH_TERMS=hundred).
_LEAGUE_SEARCH_TERMS = [
    "hundred", "indian premier league", "big bash", "pakistan super league",
    "caribbean premier league", "sa20", "international league t20",
    "major league cricket", "lanka premier league", "bangladesh premier league",
    "super smash", "vitality blast", "womens premier league", "wbbl",
]
# ALSO search each major TEAM by name — this is how a pre-live BILATERAL is caught at all: ESPN's
# search ranks "India tour of Zimbabwe" into a search for "india", and in_scope() then reads BOTH
# teams from the league name and confirms the major-vs-major bilateral. ~12 extra keyless searches
# per run. Dedup is by ESPN league id.
# ⚠ ESPN search is genuinely weak at DISCOVERY: it buries this season's edition behind older
# editions of the same tour, so --discover is best-effort and Column A (--from-status-sheet) stays
# the path CI runs. Nothing here silently substitutes for a name typed into the sheet.
_DEFAULT_SEARCH_TERMS = _LEAGUE_SEARCH_TERMS + sorted(MAJOR_TEAMS)
SEARCH_TERMS = ([s.strip() for s in os.environ["SYNC_SEARCH_TERMS"].split(",") if s.strip()]
                if os.environ.get("SYNC_SEARCH_TERMS") else _DEFAULT_SEARCH_TERMS)

ROLE_MAP = {
    "wk-batsman": "WK", "wicketkeeper batter": "WK", "wicketkeeper": "WK", "wk": "WK",
    "batsman": "BAT", "batter": "BAT", "top order batter": "BAT", "bat": "BAT",
    "batting allrounder": "AR", "bowling allrounder": "AR", "allrounder": "AR", "all rounder": "AR",
    "ar": "AR", "bowler": "BOWL", "bowl": "BOWL",   # short codes = the auction seed's role format
}
ROLE_EFPPM = {"BAT": 45.0, "WK": 45.0, "AR": 50.0, "BOWL": 45.0}
ROLE_ORDER = {"WK": 0, "BAT": 1, "AR": 2, "BOWL": 3}
ORD = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th", 6: "6th", 7: "7th"}


# ── ESPN series-id resolution (fills tours_entry.espn_series) ────────────────────
# The points bot pulls the live XI AND (critically) the full fallback scorecard from ESPN,
# scoped by espn_series. tour-sync historically left it BLANK → no ESPN fallback → franchise-
# league points never populate (cricsheet lags days behind — the 22 Jul Hundred bug). We resolve
# it here: ESPN search → candidate league ids → VALIDATE each
# by hitting its dated scoreboard and matching the fixture's teams, so we never write a wrong id.
# Unresolved → "" (caller flags loud: the verify gate fails + the Tour Ingest Review tab lists it).
ESPN_SITE = "https://site.api.espn.com/apis/site/v2/sports/cricket"
ESPN_SEARCH = "https://site.web.api.espn.com/apis/common/v3/search"
# site.api.espn.com's WAF 403s browser-impersonating User-Agents — a bare "Mozilla/5.0" is
# rejected while curl's, urllib's default and an honest bot UA all pass (site.web.api, the search
# host, accepts anything, which is why tour discovery kept working while every scoreboard/summary
# call silently returned {}). Identify ourselves properly; do NOT put "Mozilla" back.
ESPN_UA = "wwc-points-bot/1.0 (+https://github.com/nishantsingodia/wwc-points-bot)"

_ESPN_FAILED = set()

def _espn_get(url, tries=3):
    """GET + parse an ESPN endpoint, retrying transient failures (5xx / network).

    The retry matters more here than the usual "it'll pass next run": a tour's fixture list is
    written ONCE at ingest and apply_to_repos never extends it, so a single 502 mid-scan drops
    that day's match permanently. One did — CI built 34 matches where a clean scan builds 35."""
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ESPN_UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:
            transient = not isinstance(e, urllib.error.HTTPError) or e.code >= 500
            if transient and i < tries - 1:
                time.sleep(1.5 * (i + 1))
                continue
            # Report each distinct failure ONCE. A silently-empty {} is how the 403 went
            # unnoticed: the scoreboard scan found no fixtures and the tour just "didn't exist".
            key = f"{type(e).__name__}:{getattr(e, 'code', '')}"
            if key not in _ESPN_FAILED:
                _ESPN_FAILED.add(key)
                print(f"  espn: fetch failed after {i + 1} tr{'y' if i == 0 else 'ies'} "
                      f"({e}) — e.g. {url[:110]}", file=sys.stderr)
            return {}
    return {}

def _espn_search_league_ids(query):
    """League ids ESPN's search returns for a query (best-effort — the caller validates each).

    Thin view over _espn_search_leagues so there is exactly ONE ESPN search client in this file."""
    return [lid for lid, _name in _espn_search_leagues(query)]

def resolve_espn_series(tour_name, fixture_teams, fixture_gmt):
    """Resolve the ESPN series id for a tour, VALIDATED against the dated scoreboard: search ESPN,
    then for each candidate league id check that its scoreboard on the fixture date carries the
    fixture's two teams (gender/rebrand-folded via _team_key). Returns the confirmed id, or ""
    if nothing validates — never a guess. `fixture_teams` = the first real match's 2 canonical
    teams; `fixture_gmt` = that match's dateTimeGMT."""
    try:
        d = datetime.fromisoformat((fixture_gmt or "").replace("Z", "")).strftime("%Y%m%d")
    except ValueError:
        return ""
    want = {_team_key(t) for t in fixture_teams if t}
    if len(want) < 2:
        return ""
    terms, seen_t = [], set()
    for term in (tour_name,
                 re.sub(r"\b(competition|20\d\d)\b", "", tour_name, flags=re.I),
                 " ".join(fixture_teams[:2])):
        term = re.sub(r"\s+", " ", term or "").strip()
        if term and term.lower() not in seen_t:
            seen_t.add(term.lower()); terms.append(term)
    tried = set()
    for term in terms:
        for cid in _espn_search_league_ids(term):
            if cid in tried:
                continue
            tried.add(cid)
            sb = _espn_get(f"{ESPN_SITE}/{cid}/scoreboard?dates={d}")
            for e in sb.get("events", []):
                ev = {_team_key(c.get("team", {}).get("displayName", ""))
                      for c in e.get("competitions", [{}])[0].get("competitors", [])}
                if want <= ev:                       # both fixture teams present in this ESPN event
                    return cid
    return ""


# ── helpers ───────────────────────────────────────────────────────────────────
def _clean_tour_name(s):
    """Drop the format/gender parenthetical gen_tour appends ("(Men T20I)"/"(T20I)"/"(ODI)") so a
    hand-typed name resolves on ESPN and de-dups against the ingested "<name> (T20I)". Only strips
    the parenthetical — never real words (a real league name may legitimately contain 'Women')."""
    return re.sub(r"\s+", " ", re.sub(r"\([^)]*\)", "", s or "")).strip()

def norm(s):
    return re.sub(r"[^a-z ]", "", (s or "").lower()).strip()

def norm_role(r):
    return ROLE_MAP.get((r or "").strip().lower(), "BAT")

def infer_gender(name, teams):
    blob = (name + " " + " ".join(teams)).lower()
    return "female" if "women" in blob or " (w)" in blob else "male"

def in_scope(series_name, teams):
    n = series_name.lower()
    if any(d in n for d in DENY):
        return False, None
    if any(lg in n for lg in MAJOR_LEAGUES):
        return True, "league"
    # bilateral: both teams (strip "Women"/"A") are major
    def base(t): return t.lower().replace(" women", "").replace(" cricket team", "").strip()
    bases = [base(t) for t in teams if t]
    if len(bases) == 2 and all(b in MAJOR_TEAMS for b in bases):
        return True, "bilateral"
    # /series-search path has no team list -> infer a bilateral from the series NAME
    if not bases:
        named = {mt for mt in MAJOR_TEAMS if re.search(r"\b" + re.escape(mt) + r"\b", n)}
        if len(named) >= 2:
            return True, "bilateral"
    return False, None

def to_ist_iso(dt_gmt):
    # ESPN event dates arrive as "2026-07-14T12:00Z" / "...T12:00:00" (UTC)
    dt = datetime.fromisoformat(dt_gmt.replace("Z", "")).replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).strftime("%Y-%m-%dT%H:%M:00+05:30")

def to_utc_z(dt_gmt):
    dt = datetime.fromisoformat(dt_gmt.replace("Z", "")).replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:00Z")

def mmm_dd(dt_gmt):
    return datetime.fromisoformat(dt_gmt.replace("Z", "")).strftime("%b%d")


# ── existing repo state (for next ids, collision-free codes, dedupe) ────────────
def load_state():
    dm = json.load(open(f"{DRAFT}/data/matches.json"))
    dp = json.load(open(f"{DRAFT}/data/players-raw.json"))
    tc = json.load(open(f"{DRAFT}/data/team-codes.json"))
    tours = json.load(open(f"{BOT}/tours.json"))
    # name -> flag (so a known nation reuses its emoji)
    name_flag = {}
    for c, v in tc.items():
        if v.get("name") and v.get("flag"):
            name_flag.setdefault(v["name"].lower(), v["flag"])
    # registry alias -> pid (cricsheet_id) for returning-player resolution
    reg_alias = {}
    rp = f"{DRAFT}/lib/registry-players.json"
    if os.path.exists(rp):
        reg = json.load(open(rp)).get("players", {})
        for pid, e in reg.items():
            for a in e.get("aliases", []) + [e.get("display", "")]:
                if a:
                    reg_alias[norm(a)] = e.get("cricsheet_id") or pid
    return {
        "matches": dm, "players": dp, "team_codes": tc,
        # espn_series is the identity of an ingested tour now (there is no cricapi_series index).
        "existing_espn": {str(t.get("espn_series")) for t in tours if t.get("espn_series")},
        "existing_tabs": {t.get("tab") for t in tours},
        "codes": set(tc.keys()),
        "next_match_num": max((m["matchNum"] for m in dm), default=0) + 1,
        "next_pid_id": max((p["id"] for p in dp), default=10000) + 1,
        "name_flag": name_flag, "reg_alias": reg_alias,
    }

def mint_code(gp, fmt, short, taken):
    """Mint a <=6-char draft team code, collision-free.

    ⚠ The retry TRUNCATES THE BASE, not the counter. `f"{base}{i}"[:6]` looked equivalent but hangs
    forever when base is already 6 chars long: the suffix is the part that gets cut, so `code` never
    changes and `while code in taken` never terminates. A 6-char base needs a 4-char `short`, which
    the ESPN path cannot produce (3-char shorts) but the auction seed can (`name[:4].upper()`), and
    this function runs in an unattended scheduled job."""
    gl = "M" if gp == "male" else "W"
    fl = "O" if fmt == "ODI" else "T"
    base = f"{gl}{fl}{short}".upper()[:6]
    code, i = base, 1
    while code in taken:
        i += 1
        if i > 999:            # the whole 6-char space for this base is taken: say so, don't spin
            raise RuntimeError(f"mint_code: no free code left for {short!r} (base {base!r}) — "
                               f"{len(taken)} codes already taken")
        suf = str(i)
        code = f"{base[:6 - len(suf)]}{suf}"
    taken.add(code)
    return code

def resolve_pid(name, reg_alias):
    return reg_alias.get(norm(name)) or "slug:" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


# ── league squads from the auction seed (an OPTIONAL curated override for ESPN's) ────
# The auction app maintains curated, identity-anchored squads per league. extract_auction_
# squads.mjs emits them as [{export, gender, teams:[{name, short, players:[{name,role}]}]}].
# build_league_squads picks the seed export that best covers a discovered series' teams.
LEAGUE_TEAM_ALIASES = {   # normalized feed team name -> normalized canonical (rebrands etc.)
    "manchester originals": "manchester super giants",
}
# Merge the bot's canonical franchise-rename aliases (registry/team_aliases.json) so tour_sync folds
# ESPN's feed names to the SAME canonical the bot SCORES with. Without this the two alias sources
# diverge: a rebrand the feed lags on (e.g. LPL 2025→2026, Manchester Originals→Super Giants)
# attaches at ingest but the bot's short_of() misses at completion → the sheet label falls back to
# the raw feed name → the draft can't attach → 0 COMPLETED points. One source = no divergence.
try:
    _ta = json.load(open(os.path.join(BOT, "registry", "team_aliases.json"))).get("aliases", {})
    for _canon, _variants in _ta.items():
        for _v in _variants:
            LEAGUE_TEAM_ALIASES.setdefault(norm(_v), norm(_canon))
except Exception as _e:
    print(f"  (team_aliases.json merge skipped: {_e})", file=sys.stderr)

def _team_key(name):
    # strip gender qualifiers so ESPN's "MI London", "MI London (Men)"/"(Women)" and
    # "X Women" all collapse to one key (needed for ESPN event matching + league seed lookup).
    n = re.sub(r"\b(men|women)\b", "", norm(name)).strip()
    n = re.sub(r"\s+", " ", n)
    return LEAGUE_TEAM_ALIASES.get(n, n)

def build_league_squads(seeds, feed_teams, gender):
    """Return the league_squads dict gen_tour expects, or None if no seed covers >=2 of the
    series' teams. Both a rebrand alias and the new name collapse onto ONE canonical team.

    `feed_teams` are ESPN team displayNames (the only feed left)."""
    real = [t for t in feed_teams if norm(t) not in TBC_NAMES]
    want = {_team_key(t) for t in real}
    best, best_hit = None, 0
    for s in seeds:
        if gender == "female" and s.get("gender") == "male":
            continue
        if gender == "male" and s.get("gender") == "female":
            continue
        seed_by_key = {_team_key(t["name"]): t for t in s.get("teams", [])}
        hit = len(want & set(seed_by_key))
        if hit > best_hit:
            best, best_hit = seed_by_key, hit
    if not best or best_hit < 2:
        return None
    canon, squads = {}, {}
    for t in real:
        st = best.get(_team_key(t))
        if not st:
            continue
        canon[t] = st["name"]                       # canonical = the seed's team name
        squads[st["name"]] = {"short": st.get("short") or st["name"][:4].upper(),
                              "players": [[p["name"], p.get("role", "BAT")] for p in st.get("players", [])]}
    return {"canon": canon, "squads": squads} if len(squads) >= 2 else None


# ── ESPN tour creation (KEYLESS — the only creation path) ───────────────────────
# ESPN carries all three pieces keylessly: discovery (search), fixtures (scoreboard, single-date),
# and FULL squads (summary.squads — the pre-match squad, distinct from `rosters` = the announced XI
# at toss). These build the series_info + league_squads pair gen_tour consumes, so artifact-building
# is shared by every entry point. espn_series = the ESPN league id; a tour's SECOND witness
# (cricbuzz_series) is resolved separately in gen_tour and is absent unless it validates.
def _espn_role(pos):
    p = (pos or "").lower()
    if "wk" in p or "keeper" in p:
        return "WK"
    if p.startswith("ar") or "all" in p:
        return "AR"
    if p.startswith("bl") or "bowl" in p:
        return "BOWL"
    return "BAT"

def _espn_search_leagues(query):
    """[(league_id, displayName), ...] from ESPN search — name comes straight from the result so
    discovery needs no extra call to name a candidate."""
    d = _espn_get(f"{ESPN_SEARCH}?limit=15&sport=cricket&query={urllib.parse.quote(query)}")
    out, seen = [], set()
    def walk(o):
        if isinstance(o, dict):
            if o.get("type") == "league" and o.get("id") and str(o["id"]) not in seen:
                seen.add(str(o["id"]))
                out.append((str(o["id"]), o.get("displayName") or o.get("name") or ""))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(d)
    return out

def _espn_event_teams(e):
    comps = (e.get("competitions") or [{}])[0].get("competitors", [])
    return [c.get("team", {}).get("displayName", "") for c in comps if c.get("team")]

def _espn_event_class(e):
    """BOTH format fields ESPN puts on a competition's `class` block, lowercased:
    (eventType, generalClassCard). See the FORMAT VOCABULARY block for what each says.

    Read both, because name-sniffing only rescues bilaterals (description "1st T20I"); a franchise
    league's description is "4th Match", so an unread/blank class buckets to None and gen_tour drops
    the ENTIRE fixture list (CPL 2026 ingested as 0 matches)."""
    cls = (e.get("competitions") or [{}])[0].get("class") or {}
    return ((cls.get("eventType") or "").strip().lower(),
            (cls.get("generalClassCard") or "").strip().lower())

def _espn_scan_day(lid, day, matchlist, event_ids, class_ids=None):
    """Append one date's ESPN fixtures → ESPN-shaped rows. Returns True if any were added.

    Also collects the LEAGUE-level class ids (scoreboard leagues[].classId) into `class_ids` — the
    series-level answer to "what format is this?" for a series whose per-fixture class is blank."""
    sb = _espn_get(f"{ESPN_SITE}/{lid}/scoreboard?dates={day}")
    if class_ids is not None:
        for lg in (sb.get("leagues") or []):
            for cid in (lg.get("classId") or []):
                class_ids.add(str(cid))
    hit = False
    for e in sb.get("events", []):
        teams = _espn_event_teams(e)
        if len(teams) != 2:
            continue
        hit = True
        ev_type, card = _espn_event_class(e)
        matchlist.append({
            "id": e.get("id"), "teams": teams, "dateTimeGMT": e.get("date"),
            "date": (e.get("date") or "")[:10],
            "espn_event_type": ev_type, "espn_class_card": card,
            "name": e.get("description") or e.get("shortName") or "",
        })
        event_ids.append(e.get("id"))
    return hit

def _stamp_declared_fmt(matchlist, class_ids, name, lid):
    """Stamp the SERIES-level format fallback on every row and report the format census LOUDLY.

    Nothing downstream can tell "this league plays T20" from "ESPN forgot to say", so the count of
    unclassified fixtures is printed either way: bucketed (with the reason) or dropped."""
    declared, why = _declared_fmt(class_ids, name, matchlist)
    blank = [m for m in matchlist if _fmt_stated(m) is None and not _fmt_skipped(m)]
    for m in matchlist:
        m["declared_fmt"] = declared
    census = {}
    for m in matchlist:
        k = _fmt_of(m) or ("skipped(Test/FC)" if _fmt_skipped(m) else "UNKNOWN")
        census[k] = census.get(k, 0) + 1
    print(f"  espn: {name or lid} — {len(matchlist)} fixture(s) by format {census}", file=sys.stderr)
    if blank and declared:
        print(f"  espn: {len(blank)} of {len(matchlist)} fixture(s) carry NO ESPN class — bucketed "
              f"as {declared} because {why}", file=sys.stderr)
    elif blank:
        print(f"  ⚠ espn: {len(blank)} of {len(matchlist)} fixture(s) carry NO ESPN class and "
              f"{why} — they will be DROPPED. Fix by declaring the format in the tour name "
              f"(e.g. '... T20' / '... ODI') rather than letting them vanish.", file=sys.stderr)

def _espn_matchlist(lid, now, name="", span_days=60, back_days=30):
    """Scan the ESPN scoreboard day-by-day (it only accepts a single date) around `now` across the
    tour's span → ESPN-shaped matchList + the event ids, date-ordered. Keyless.

    The window must cover the WHOLE season in BOTH directions, because `apply_to_repos` skips a
    tour whose tab already exists — the fixture list written at ingest is never extended or
    backfilled by a later run:
      * forward (`span_days`): at 25 this truncated CPL 2026 (41 days) to its first 22 matches,
        dropping the playoffs. Stops after 6 empty days once a match has been seen, so a short
        bilateral still costs only ~its own length in calls.
      * backward (`back_days`): the scan used to start at `now`, so a tour added MID-SEASON lost
        every match already played (CPL 2026 was ingested on day 4, silently missing matches 1-3).
        Stops after 6 consecutive empty days unconditionally — a tour that hasn't started yet has
        nothing behind it, so this costs 6 calls in the common "added a few days early" case, and
        the gap between seasons stops it from reaching last year's edition of the same league id.
    """
    matchlist, event_ids, class_ids = [], [], set()
    empty = 0
    for i in range(1, back_days + 1):
        if _espn_scan_day(lid, (now - timedelta(days=i)).strftime("%Y%m%d"),
                          matchlist, event_ids, class_ids):
            empty = 0
        elif (empty := empty + 1) >= 6:
            break
    seen, empty = bool(matchlist), 0
    for i in range(span_days):
        if _espn_scan_day(lid, (now + timedelta(days=i)).strftime("%Y%m%d"),
                          matchlist, event_ids, class_ids):
            seen, empty = True, 0
        elif seen and (empty := empty + 1) >= 6:
            break
    order = sorted(range(len(matchlist)), key=lambda k: matchlist[k]["dateTimeGMT"] or "")
    matchlist, event_ids = [matchlist[k] for k in order], [event_ids[k] for k in order]
    if matchlist:
        _stamp_declared_fmt(matchlist, class_ids, name, lid)
    return matchlist, event_ids

def _espn_squads(lid, event_id):
    """Full squads from an ESPN event summary → {espn_team_displayName: [(name, role), ...]}."""
    sm = _espn_get(f"{ESPN_SITE}/{lid}/summary?event={event_id}")
    out = {}
    for sq in (sm.get("squads") or []):
        team = (sq.get("team") or {}).get("displayName", "")
        players = [((a.get("displayName") or a.get("fullName") or "").strip(),
                    _espn_role((a.get("position") or {}).get("abbreviation")))
                   for a in (sq.get("athletes") or [])]
        players = [(n, r) for n, r in players if n]
        if team and players:
            out[team] = players
    return out

def espn_discover(now, horizon):
    """Discover in-scope tours via ESPN search (keyless). Bilaterals are found by searching each
    major TEAM name (in_scope reads both teams from the series name); leagues by the watchlist.
    Returns {league_id: name}. Keyless, and the only auto-discovery this module has."""
    hits, floor = {}, now - timedelta(days=2)
    for term in SEARCH_TERMS:
        for lid, name in _espn_search_leagues(term):
            if lid in hits:
                continue
            ok, kind = in_scope(name, [])
            if not ok:
                continue
            evs = _espn_get(f"{ESPN_SITE}/{lid}/scoreboard").get("events", [])  # next/current event
            when = None
            if evs:
                try:
                    when = datetime.fromisoformat((evs[0].get("date") or "").replace("Z", "")).replace(tzinfo=timezone.utc)
                except ValueError:
                    pass
            if when is not None and not (floor <= when <= horizon):
                continue          # not starting/running in the window
            hits[lid] = name
            print(f"  espn/search[{term!r}]: KEEP {name!r} (lid {lid}, {kind}, next={evs[0].get('date','?')[:10] if evs else '?'})", file=sys.stderr)
    return hits

def espn_build(lid, name, now, horizon, state, seeds=None):
    """Build a tour ENTIRELY from ESPN → (series_info, gender, league_squads) for gen_tour, or None.

    espn_series = lid. `seeds` (--auction-squads) is an OPTIONAL curated override for ESPN's squads,
    adopted only when a seed covers EVERY team in the fixture list — a partially-covering seed would
    silently drop the uncovered teams' matches (gen_tour's canonical() returns None for them), which
    is a worse outcome than ESPN's own squads."""
    matchlist, event_ids = _espn_matchlist(lid, now, name)
    def _soon(m):
        try:
            return datetime.fromisoformat((m["dateTimeGMT"] or "").replace("Z", "")).replace(tzinfo=timezone.utc) <= horizon
        except Exception:
            return True
    if not matchlist or not any(_soon(m) for m in matchlist):
        return None
    # Squads live on the EVENT summary, so one event only ever yields the 2 teams playing it.
    # Merge across events until every team in the fixture list is covered — stopping at the first
    # event with squads collapses an N-team league into a 2-team bilateral (CPL 2026 ingested as
    # "JAM v BAR", 2 of 7 franchises, 2 of 39 matches).
    want = {t for m in matchlist for t in m["teams"]}
    sqmap = {}
    for ev in event_ids[:MAX_SQUAD_EVENTS]:
        for team, players in _espn_squads(lid, ev).items():
            sqmap.setdefault(team, players)
        if want <= set(sqmap):
            break
    if len(sqmap) < 2:
        print(f"  espn: {name!r} — squads not posted yet (skip; will catch on a later run)", file=sys.stderr)
        return None
    if missing := want - set(sqmap):
        # Not fatal: gen_tour's canonical() drops an unmapped team, so those fixtures fall out.
        # Loud, because it silently shrinks the tour.
        print(f"  espn: {name!r} — no squad posted for {len(missing)} team(s): "
              f"{', '.join(sorted(missing))} — their matches will be dropped", file=sys.stderr)
    gender = "female" if re.search(r"\bwomen\b", name, re.I) else "male"
    squads = {t: {"short": (re.sub(r"[^A-Za-z]", "", t)[:3] or t[:3]).upper(),
                  "players": [list(p) for p in players]}
              for t, players in sqmap.items()}
    league_squads = {"canon": {t: t for t in sqmap}, "squads": squads}
    if seeds:
        seeded = build_league_squads(seeds, sorted(want), gender)
        if seeded and want <= set(seeded["canon"]):
            print(f"  squads: using the auction seed ({len(seeded['squads'])} teams, curated + "
                  f"identity-anchored) instead of ESPN's — it covers every team in the fixture list",
                  file=sys.stderr)
            league_squads = seeded
        elif seeded:
            print(f"  squads: auction seed covers only {len(seeded['canon'])} of {len(want)} teams "
                  f"— keeping ESPN's squads (a partial seed would DROP the uncovered teams' "
                  f"matches)", file=sys.stderr)
    # ESPN's LEAGUE name is season-less ("Caribbean Premier League"), but gen_tour derives the tour
    # name AND the sheet tab from it — so next season would mint the same tab, and apply_to_repos
    # skips a tab that already exists (the 2027 edition would silently never ingest). Stamp the
    # season year the fixtures actually fall in, as the hand-written entries do ("... League 2026").
    if not re.search(r"\b(19|20)\d{2}\b", name):
        years = [m["date"][:4] for m in matchlist if (m.get("date") or "")[:4].isdigit()]
        if years:
            name = f"{name} {max(set(years), key=years.count)}"
    # No feed id beyond ESPN's: `espn_id` IS the tour's series identity now.
    series_info = {"info": {"name": name, "espn_id": str(lid)}, "matchList": matchlist}
    return series_info, gender, league_squads

def espn_add_named(query, now, horizon, state, espn_id="", seeds=None):
    """Resolve a tour on ESPN and build it KEYLESS → [tour dicts] (empty if not found).
    Shared by --espn-tour (one name) and --from-status-sheet (each Column-A name Nishant typed).

    `espn_id` short-circuits the name search. Name resolution searches ESPN and then VALIDATES each
    candidate against dated scoreboards — correct, but it can still return UNRESOLVED on an
    oddly-named tour, and that FAILS the ingest gate. Supplying the id (from the ESPN series URL,
    e.g. .../the-hundred-men-s-competition-2026-1521176) removes the only step that can defeat the
    automation. It is still VERIFIED before use: an id that serves no fixtures is refused rather
    than written into tours.json, because a wrong id is worse than an unresolved one — it ingests a
    tour whose matches will never appear."""
    clean = _clean_tour_name(query)          # "India tour of Zimbabwe 2026 (Men T20I)" -> "...2026"
    qn = norm(clean)
    if espn_id:
        built = espn_build(espn_id, clean, now, horizon, state, seeds)
        if not built:
            print(f"  espn-add: the espn_series {espn_id} you supplied for {query!r} serves NO "
                  f"fixtures — refusing it (a wrong id ingests a tour whose matches never appear). "
                  f"Check the number in the ESPN series URL, or clear the cell to let the name "
                  f"search run.", file=sys.stderr)
            return []
        si, gender, lg = built
        out = []
        for fmt in ("ODI", "T20"):
            t = gen_tour(si, fmt, gender, state, lg)
            if t and t["tours_entry"]["tab"] not in state["existing_tabs"]:
                out.append(t)
        return out
    cands = _espn_search_leagues(clean)
    if not cands and (bare := re.sub(r"\s*\b(19|20)\d{2}\b", "", clean).strip()) != clean:
        # A season-less league ("Caribbean Premier League") is a ZERO-result search once a year is
        # appended, and writing the year is the natural instinct when typing a tour into Column A.
        # norm() already strips digits so the matching below is year-agnostic — only the ESPN query
        # itself chokes on it. Retry bare rather than reporting "no ESPN league matched".
        print(f"  espn-add: no hit for {clean!r} — retrying as {bare!r}", file=sys.stderr)
        cands = _espn_search_leagues(bare)
    target = (next((c for c in cands if norm(c[1]) == qn), None)
              or next((c for c in cands if qn in norm(c[1]) or norm(c[1]) in qn), None)
              or (cands[0] if cands else None))
    if not target:
        print(f"  espn-add: no ESPN league matched {query!r} — check the name", file=sys.stderr)
        return []
    lid, name = target
    print(f"  espn-add: {query!r} → ESPN league {lid} ({name!r})", file=sys.stderr)
    built = espn_build(lid, name, now, horizon, state, seeds)
    if not built:
        return []
    si, gender, lg = built
    out = []
    for fmt in ("ODI", "T20"):
        t = gen_tour(si, fmt, gender, state, lg)
        if t and t["tours_entry"]["tab"] not in state["existing_tabs"]:
            out.append(t)
    return out

def status_sheet_new_names(state):
    """Read the 'TOUR STATUS' tab's Column A and return the tour names Nishant typed that are NOT
    yet ingested. This is the 'add a tour = type its name in Column A' input. GSheet-only (no
    match feed at all); returns [] without creds (local)."""
    gid = os.environ.get("GSHEET_ID", "") or os.environ.get("SYNC_SHEET_ID", "")
    creds = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not (gid and creds):
        return []
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        sh = gspread.authorize(Credentials.from_service_account_info(
            json.loads(creds), scopes=["https://www.googleapis.com/auth/spreadsheets"])).open_by_key(gid)
    except Exception as e:
        print(f"  from-status-sheet: sheet open failed: {e}", file=sys.stderr)
        return []
    # Add a tour by typing its name in Column A of EITHER control tab (Nishant used TOUR CONTROL).
    # An OPTIONAL `espn_series` column lets you paste the id yourself. That matters: resolution
    # searches ESPN and then VALIDATES each candidate against dated scoreboards, which is right but
    # can still come back UNRESOLVED on an oddly-named tour — and that FAILS the ingest gate. A
    # pasted id skips the search entirely, so the one step that can defeat the automation stops
    # being able to. Read BY HEADER NAME, never by index, so inserting a column cannot silently
    # start reading the wrong one.
    col_a, ids = [], {}
    for tab in ("TOUR CONTROL", "TOUR STATUS"):
        try:
            rows = sh.worksheet(tab).get_all_values()
        except Exception:
            continue
        if not rows:
            continue
        hdr = [h.strip().lower() for h in rows[0]]
        # PREFER the human's paste-in column. Since the header sync, "espn_series" is ALSO the
        # bot-written report column C, and a first-match-wins lookup would read that instead — it
        # is blank on a row he just typed, so his pasted id would be silently ignored and the tour
        # would fall back to name resolution, which is the one step that can fail the ingest gate.
        _cands = [i for i, h in enumerate(hdr)
                  if h.startswith("espn_series") or h.startswith("espn series")]
        ei = next((i for i in _cands if "optional" in hdr[i]), _cands[0] if _cands else -1)
        for r in rows[1:]:
            nm = (r[0] if r else "").strip()
            if not nm:
                continue
            col_a.append(nm)
            if ei >= 0 and len(r) > ei:
                v = re.sub(r"\D", "", (r[ei] or ""))     # tolerate a pasted ESPN URL
                if v:
                    ids[norm(_clean_tour_name(nm))] = v
    existing = {norm(_clean_tour_name(t.get("name", ""))) for t in json.load(open(f"{BOT}/tours.json"))}
    out, seen = [], set()
    for raw in col_a:
        c = (raw or "").strip()
        key = norm(_clean_tour_name(c))          # ignore the (Men T20I)/(ODI) suffix for matching
        if not key or key in seen:
            continue
        seen.add(key)
        if key in existing or any(key in en or en in key for en in existing):
            continue                              # already ingested
        out.append((c, ids.get(key, "")))
    return out


# ── the L1 SECOND WITNESS: a VALIDATED cricbuzz_series, or none at all ──────────
def _plus_minus_a_day(day):
    """{day-1, day, day+1} as YYYY-MM-DD. Feeds disagree about which calendar day a match belongs
    to (a 19:30 IST start is already tomorrow in UTC), so a same-day-only comparison would reject
    a perfectly correct series id."""
    try:
        d = datetime.strptime(day, "%Y-%m-%d")
    except (TypeError, ValueError):
        return set()
    return {(d + timedelta(days=k)).strftime("%Y-%m-%d") for k in (-1, 0, 1)}

def resolve_cricbuzz_series(tour_name, matchlist):
    """PROPOSE and then VALIDATE a Cricbuzz series id for a tour → (series_id_str, why).

    ESPN is the base feed; Cricbuzz is the L1 SECOND WITNESS the bot reconciles ESPN against. A
    tour ingested without one has NO second witness at all until cricsheet (L2) publishes days
    later, so we try to resolve it here — but we only adopt an id we can check.

    cricbuzz.resolve_series_id is a NAME match, and its own docstring says it PROPOSES only: "the
    result belongs in tours.json ... written once by a human who opened the URL". Rule 3 (never let
    a name decide identity) is about PLAYERS and still holds absolutely; a series is not a person.
    But a wrong series id is a whole-tour mis-ingest, so the proposal is validated against
    Cricbuzz's OWN fixture list on DATES — never on team names: at least 2 of ESPN's fixture dates
    (±1 day) must appear among the proposed series' fixtures. Anything less returns "" plus a loud
    reason, and the caller then reports the tour as having no L1 witness. Never a guess.

    Cheap and safe to call twice per tour (once per format): cricbuzz.series_matches is memoised
    per process and the archive index is disk-cached; every fetch either returns bytes or raises,
    so an outage can never look like "this series has no fixtures"."""
    espn_dates = {(m.get("date") or "") for m in matchlist if m.get("date")}
    years = [d[:4] for d in espn_dates if d[:4].isdigit()]
    if not years:
        return "", "no dated ESPN fixture to anchor the Cricbuzz year — cannot even look"
    year = max(set(years), key=years.count)
    clean = _clean_tour_name(tour_name)
    try:
        import cricbuzz
    except Exception as e:                      # never fatal: ingest proceeds without an L1 id
        return "", f"cricbuzz module unavailable ({e})"
    try:
        prop = cricbuzz.resolve_series_id(clean, int(year))
    except Exception as e:                      # CricbuzzUnavailable/ParseError — raises, never lies
        return "", f"cricbuzz archive {year} unavailable ({e})"
    if not prop:
        return "", (f"cricbuzz proposed nothing unambiguous for {clean!r} ({year}) — a bilateral's "
                    f"in-house name usually shares no vocabulary with Cricbuzz's slug; resolve it "
                    f"by hand (cricbuzz.series_candidates) and paste cricbuzz_series into tours.json")
    sid, slug = prop
    try:
        fixtures = cricbuzz.series_matches(sid)
    except Exception as e:
        return "", f"cricbuzz series {sid} ({slug}) could not be VERIFIED ({e}) — not adopting it"
    cb_dates = set()
    for f in fixtures:
        cb_dates |= _plus_minus_a_day(cricbuzz.fixture_date(f))
    hit = len(espn_dates & cb_dates)
    # A MAJORITY of dates, not just two: Cricbuzz lists every fixture of a tour (all formats), so a
    # correct series id covers essentially all of ESPN's dates, while two coincidental overlaps are
    # easy for a different tour running the same week. Rejecting is safe — the tour still ingests,
    # loudly, with no L1 witness; adopting a wrong series id would be a whole-tour mis-witness.
    need = min(len(espn_dates), max(2, -(-6 * len(espn_dates) // 10)))
    if hit < need:
        return "", (f"cricbuzz {sid} ({slug}) REJECTED: only {hit} of {len(espn_dates)} ESPN "
                    f"fixture date(s) appear among its {len(fixtures)} fixtures (needed {need}) — "
                    f"a name match that does not line up on dates is the wrong series")
    return str(sid), (f"cricbuzz {sid} ({slug}) validated — {hit}/{len(espn_dates)} ESPN fixture "
                      f"dates matched its {len(fixtures)} fixtures")


# ── the generator: one (series, format) -> all artifacts ────────────────────────
TBC_NAMES = {"tbc", "tba", "to be confirmed", "to be decided", "winner", ""}

def gen_tour(series_info, fmt, gender, state, league_squads):
    """Build one (series, format) tour's artifacts.

    Handles BOTH 2-team bilaterals and N-team leagues: teams are the union across the whole
    fixture list (not just match 1), and TBC/knockout placeholders are skipped.

    series_info   : {"info": {"name", "espn_id"}, "matchList": [rows from _espn_matchlist]}.
                    ESPN is the only feed; there is no cricapi shape here any more.
    league_squads : REQUIRED. The tour's roster AND its team-naming authority — ESPN's squads
                    (espn_build) or the curated auction seed (build_league_squads):
                      {"canon":  {feed_team_name: canonical_name, ...},   # collapses aliases
                                 # (e.g. Manchester Originals -> ...Super Giants) and excludes
                                 # TBC; any name absent here is treated as TBC and DROPPED.
                       "squads": {canonical_name: {"short": str,
                                                   "players": [[name, role], ...]}}}  # ordered
    """
    if not league_squads or not league_squads.get("squads"):
        # Was an optional arg while cricapi could supply /match_squad rosters. It cannot any more,
        # and the old fallback built a tour with ZERO players. Refuse loudly instead.
        raise ValueError("gen_tour requires league_squads: ESPN's summary.squads (or the auction "
                         "seed) is the only roster source now, and a tour with no roster settles "
                         "every player at zero")
    info = series_info["info"]
    # DISCOVERY vs SCORING format split: ESPN classes The Hundred as eventType "T20" (verified on
    # league 1521176), so its fixtures ride the ("ODI","T20") discovery loop instead of being
    # dropped (the 22 Jul empty-list bug). But it must be SCORED on its own D11 ruleset (no
    # SR/econ/maiden — see _score_hundred in the bot), so the format WRITTEN into
    # tours.json/matches.json is "HUN". Everything downstream (bot CURRENT_FMT, draft
    # scoreFormatOf) keys off this written value, not the ESPN discovery bucket.
    score_fmt = "HUN" if "hundred" in (info.get("name") or "").lower() else fmt
    ml = [m for m in series_info["matchList"] if _fmt_of(m) == fmt]
    ml.sort(key=lambda m: m.get("dateTimeGMT") or m.get("date") or "")
    if not ml:
        # Loud drop: an in-scope series yielding zero matches for this format is nearly always a
        # format-vocabulary miss (ESPN leaving class blank — the CPL-as-0-matches bug), so print
        # exactly what ESPN said, in ESPN's own words, rather than "nothing to do".
        all_ml = series_info["matchList"]
        cls = sorted({f"{m.get('espn_event_type') or '∅'}/{m.get('espn_class_card') or '∅'}"
                      for m in all_ml})
        print(f"  gen_tour: '{info.get('name')}' -> 0 {fmt} matches "
              f"(ESPN eventType/generalClassCard={cls}, series fallback="
              f"{all_ml[0].get('declared_fmt') if all_ml else None!r}) — dropped", file=sys.stderr)
        return None

    def canonical(name):
        """Map a raw ESPN team name to its canonical team, or None to drop it (TBC/unmapped)."""
        return league_squads["canon"].get(name)             # None if unmapped/TBC

    # union of real teams across ALL matches, in first-appearance order
    teams, seen = [], set()
    for m in ml:
        for cn in (m.get("teams") or []):
            c = canonical(cn)
            if c and c not in seen:
                seen.add(c); teams.append(c)
    if len(teams) < 2:
        return None
    league = len(teams) > 2

    gl = "M" if gender == "male" else "W"
    shorts = {t: league_squads["squads"][t]["short"] for t in teams}
    code = {t: mint_code(gender, fmt, shorts[t], state["codes"]) for t in teams}

    if league:
        tour_name = info["name"]                          # e.g. "The Hundred Men's Competition 2026"
        base = re.sub(r"\bcompetition\b", "", info["name"], flags=re.I)
        tab = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 ]", "", base)).strip().upper() + " POINTS"
        prefix = "".join(w[0] for w in re.findall(r"[A-Za-z]+", info["name"]))[:6].upper()
    else:
        fmt_label = "ODI" if fmt == "ODI" else "T20I"
        tour_name = f"{info['name']} ({fmt_label})"
        tab = f"{shorts[teams[0]]} v {shorts[teams[1]]} {fmt_label} POINTS".upper()

    # ---- matches (skip TBC/unresolved knockouts) ----
    matches, toss, mi = [], [], 0
    for m in ml:
        raw = (m.get("teams") or [None, None])[:2]
        if len(raw) < 2:
            continue
        c1, c2 = canonical(raw[0] or ""), canonical(raw[1] or "")
        if not c1 or not c2 or c1 == c2:
            continue
        mi += 1
        dt = m.get("dateTimeGMT")
        if league:
            key = f"{prefix}_{gl}{mi}_{code[c1]}_{code[c2]}_{mmm_dd(dt)}"
            label = f"Match {mi}: {code[c1]} v {code[c2]}"
        else:
            key = f"AUTO_{gl}_{shorts[c1]}_{shorts[c2]}_{fmt_label}{mi}_{mmm_dd(dt)}"
            label = f"{ORD.get(mi, str(mi)+'th')} {fmt_label}: {shorts[c1]} v {shorts[c2]}"
        matches.append({
            "matchNum": state["next_match_num"], "key": key, "gender": gl,
            "team1": code[c1], "team2": code[c2], "label": label, "date": to_ist_iso(dt),
            # Explicit format so the draft never has to sniff it from the key. Multi-team ODI
            # events (WC/tri-series) take the league key branch which omits the "ODI" tag, so a
            # key-regex would mis-score them as T20 — this field is authoritative. "HUN" for The
            # Hundred (scored on its own ruleset), NOT the "T20" discovery bucket.
            "format": score_fmt,
        })
        toss.append(to_utc_z(dt))
        state["next_match_num"] += 1
    if not matches:
        return None

    # ---- rosters (ordered — preserves the squad source's XI-first order) ----
    players, squads_json, team_codes = [], {}, {}
    for t in teams:
        c = code[t]
        team_codes[c] = {"flag": state["name_flag"].get(t.lower(), "🏏"), "name": t}
        squads_json[c] = {"name": t, "players": []}
        items = [(p, norm_role(r)) for p, r in league_squads["squads"][t]["players"]]
        for sn, (pname, role) in enumerate(items, 1):
            players.append({
                "id": state["next_pid_id"], "name": pname, "country": t, "role": role,
                "squad_number": sn, "team_code": c, "efppm": ROLE_EFPPM[role],
                "pid": resolve_pid(pname, state["reg_alias"]),
            })
            squads_json[c]["players"].append([pname, role])
            state["next_pid_id"] += 1

    ends = ml[-1].get("date") or info.get("enddate")
    squads_path = re.sub(r"[^a-z0-9]+", "_", tour_name.lower()).strip("_") + "_squads.json"
    # espn_series — the BASE feed id, so a blank one means the tour has no feed at all. Every
    # builder in this file already knows it (info["espn_id"]); resolve_espn_series is the validated
    # fallback for any caller that hands us a series_info without one (search -> candidate league
    # ids -> confirm the fixture's two teams on that id's dated scoreboard; "" rather than a guess).
    espn_id = (info.get("espn_id") or "").strip()
    if not espn_id:
        for m in ml:
            raw = (m.get("teams") or [None, None])[:2]
            cc = [canonical(x or "") for x in raw]
            if len(cc) == 2 and cc[0] and cc[1] and cc[0] != cc[1]:
                espn_id = resolve_espn_series(tour_name, cc, m.get("dateTimeGMT"))
                break
    if not espn_id:
        print(f"  ⚠ {tour_name}: espn_series UNRESOLVED — ESPN is the ONLY base feed, so this tour "
              f"would be ingested with no feed whatsoever. tour_sync_finalize's verify gate fails "
              f"on this; paste the id from the ESPN series URL into Column A's espn_series cell.",
              file=sys.stderr)
    # cricbuzz_series — the L1 SECOND WITNESS. Written only when it VALIDATES on fixture dates;
    # the key is omitted entirely otherwise, so nothing downstream can mistake a guess for an id.
    cb_id, cb_why = resolve_cricbuzz_series(tour_name, ml)
    print(f"  cricbuzz: {cb_why}", file=sys.stderr)
    tours_entry = {
        "ends": ends, "espn_series": espn_id, "format": score_fmt, "gender": gender,
        "name": tour_name, "squads": squads_path, "tab": tab,
    }
    if cb_id:
        tours_entry["cricbuzz_series"] = cb_id
    else:
        print(f"  ⚠⚠ NO L1 SECOND WITNESS for '{tour_name}'. ESPN is now the ONLY source for it: "
              f"every number is single-sourced until cricsheet (L2) publishes, and an ESPN gap "
              f"cannot be caught by anything. Resolve the Cricbuzz series by hand and add "
              f"\"cricbuzz_series\" to its tours.json entry.", file=sys.stderr)
    return {
        "tour_name": tour_name, "codes": code,
        "matches": matches, "players": players, "team_codes": team_codes,
        "tours_entry": tours_entry, "squads_json": squads_json,
        "squads_path": squads_path, "toss_windows": toss,
        "no_l1_witness": not cb_id,
    }


def _load(p):
    return json.load(open(p, encoding="utf-8"))

def _dump(p, obj):
    json.dump(obj, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.load(open(p, encoding="utf-8"))  # re-parse: fail loudly on any corruption

def apply_to_repos(tours):
    """Append each generated tour into the draft + bot files. Idempotent-ish: skips a
    tour whose tab is already registered. Re-parses every file it writes."""
    if not tours:
        return []
    dm = _load(f"{DRAFT}/data/matches.json")
    dp = _load(f"{DRAFT}/data/players-raw.json")
    dc = _load(f"{DRAFT}/data/team-codes.json")
    pt = _load(f"{DRAFT}/data/points-tabs.json")
    # The draft resolves the ESPN event (live XI + live points) via SERIES_BY_GENDER =
    # data/espn-series.json. It MUST list the tour's espn_series or getEspnLineup /
    # getLiveMatchPoints return null → no lineups, 0 live points (the Hundred bug). Keep it
    # in lockstep with the bot's tours.json espn_series, per gender (W/M).
    es_path = f"{DRAFT}/data/espn-series.json"
    es = _load(es_path) if os.path.exists(es_path) else {"W": [], "M": []}
    tj = _load(f"{BOT}/tours.json")
    tw = _load(f"{BOT}/toss_windows.json")
    have_tabs = {t.get("tab") for t in tj}
    sheet_id = os.environ.get("SYNC_SHEET_ID", "")
    applied = []
    for t in tours:
        if t["tours_entry"]["tab"] in have_tabs:
            continue
        dm.extend(t["matches"])
        dp.extend(t["players"])
        dc.update(t["team_codes"])
        tj.append(t["tours_entry"])
        tw.extend(t["toss_windows"])
        _dump(f"{BOT}/{t['squads_path']}", t["squads_json"])
        # Register the tour's ESPN series in the draft (W/M), dedup, only if resolved.
        espn_id = (t["tours_entry"].get("espn_series") or "").strip()
        if espn_id:
            gkey = "W" if t["tours_entry"].get("gender") == "female" else "M"
            es.setdefault(gkey, [])
            if espn_id not in es[gkey]:
                es[gkey].append(espn_id)
        # Only register a points tab the bot will actually WRITE. Registering one it won't is not
        # merely useless: gviz answers an UNKNOWN sheet name with HTTP 200 carrying the FIRST SHEET
        # of the spreadsheet (verified — ?sheet=ZZZ_BOGUS returned byte-identical bytes to
        # ?sheet=CPL...POINTS), so the draft merges an unrelated board into its points pool on
        # every request. It once served a WWC auction-budget board as CPL points.
        #
        # ⚠ The CONDITION here was "has a cricapi_series", which was correct only while a tour
        # without one went unscored. An ESPN-only tour IS scored, so that test would have
        # permanently withheld CPL's tab: the bot would compute and publish every CPL point and the
        # draft would never read one of them. The real question is "will the bot write this tab",
        # and the bot writes a tab for any tour it can source a scorecard for — which, with cricapi
        # gone, is exactly "has an espn_series".
        if sheet_id and (t["tours_entry"].get("espn_series") or "").strip():
            tab = urllib.parse.quote(t["tours_entry"]["tab"])
            pt.append(f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={tab}&headers=1")
        elif sheet_id:
            print(f"  points tab NOT registered for {t['tour_name']}: no espn_series, so the bot "
                  f"can source no scorecard and will never write the tab (an unknown gviz tab "
                  f"silently returns another sheet)", file=sys.stderr)
        applied.append(t["tour_name"])
    _dump(f"{DRAFT}/data/matches.json", dm)
    _dump(f"{DRAFT}/data/players-raw.json", dp)
    _dump(f"{DRAFT}/data/team-codes.json", dc)
    _dump(f"{DRAFT}/data/points-tabs.json", pt)
    _dump(es_path, es)
    _dump(f"{BOT}/tours.json", tj)
    _dump(f"{BOT}/toss_windows.json", sorted(set(tw)))
    return applied

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true", help="write artifacts into the draft + bot repo files")
    ap.add_argument("--emit", help="write generated artifacts to this dir")
    ap.add_argument("--auction-squads", help="JSON from extract_auction_squads.mjs. Used INSTEAD of "
                    "ESPN's squads for a league it covers COMPLETELY (curated, identity-anchored "
                    "rosters); ignored for a partial cover, which would drop the uncovered teams.")
    # ---- the three ways in. All three are ESPN-only and keyless; there is no default. ----
    ap.add_argument("--from-status-sheet", action="store_true",
                    help="THE path CI runs: read the tour names typed in Column A of the "
                         "'TOUR CONTROL'/'TOUR STATUS' GSheet tabs and ESPN-add any that aren't "
                         "ingested yet (an optional espn_series column skips name resolution).")
    ap.add_argument("--espn-tour", help="add ONE named tour: name -> ESPN league id -> fixtures + "
                    "full squads. e.g. --espn-tour 'India tour of Zimbabwe 2026'")
    ap.add_argument("--discover", action="store_true",
                    help="ESPN watchlist auto-discovery (search -> league id -> fixtures). "
                         "BEST-EFFORT only: ESPN's search buries a near-term bilateral behind older "
                         "editions of the same tour, so it complements Column A, never replaces it.")
    args = ap.parse_args()
    if not (args.from_status_sheet or args.espn_tour or args.discover):
        # Deliberately an error, not a default. The old default ran cricapi discovery; with that
        # gone, quietly picking a path (or quietly doing nothing) is how a missed tour hides.
        ap.error("pick a path: --from-status-sheet (what CI runs), --espn-tour NAME, or --discover")
    state = load_state()
    seeds = json.load(open(args.auction_squads)) if args.auction_squads else []
    if seeds:
        print(f"auction squads: {sum(len(s.get('teams',[])) for s in seeds)} teams across "
              f"{len(seeds)} seed(s)", file=sys.stderr)

    tours = []
    now = datetime.now(timezone.utc)
    if args.from_status_sheet:
        # The tour names Nishant typed in Column A → ESPN-add each. Keyless end to end.
        horizon = now + timedelta(days=45)
        names = status_sheet_new_names(state)
        print(f"from-status-sheet: {len(names)} new name(s) in Column A: "
              f"{[n for n, _ in names]}", file=sys.stderr)
        for nm, espn_id in names:
            if espn_id:
                print(f"  '{nm}': using the espn_series {espn_id} you supplied — skipping "
                      f"name resolution", file=sys.stderr)
            tours += espn_add_named(nm, now, horizon, state, espn_id=espn_id, seeds=seeds)
    elif args.espn_tour:
        horizon = now + timedelta(days=45)
        tours += espn_add_named(args.espn_tour, now, horizon, state, seeds=seeds)
    else:
        horizon = now + timedelta(days=DISCOVERY_WINDOW_DAYS)
        found = espn_discover(now, horizon)
        print(f"espn-discover: {len(found)} in-scope tour(s) in next {DISCOVERY_WINDOW_DAYS}d "
              f"(keyless)", file=sys.stderr)
        for lid, name in found.items():
            if lid in state["existing_espn"]:
                print(f"  skip (already ingested): {name[:50]}", file=sys.stderr)
                continue
            built = espn_build(lid, name, now, horizon, state, seeds)
            if not built:
                continue
            si, gender, lg = built
            for fmt in ("ODI", "T20"):
                t = gen_tour(si, fmt, gender, state, lg)
                if t and t["tours_entry"]["tab"] not in state["existing_tabs"]:
                    tours.append(t)
                elif t:
                    print(f"  skip (tab exists): {t['tours_entry']['tab']}", file=sys.stderr)

    # ---- output ----
    for t in tours:
        print(f"\n=== {t['tour_name']} ===  codes={t['codes']}")
        print(f"  matches ({len(t['matches'])}):")
        for m in t["matches"]:
            print(f"    {m['label']:24} {m['date']}   key={m['key']}")
        for c, sq in t["squads_json"].items():
            pids = sum(1 for p in t["players"] if p["team_code"] == c and not p["pid"].startswith("slug:"))
            print(f"  {c} ({t['team_codes'][c]['flag']} {sq['name']}): {len(sq['players'])} players, {pids} registry-pid'd")
        print(f"  feeds: espn_series={t['tours_entry'].get('espn_series') or 'UNRESOLVED'}  "
              f"cricbuzz_series={t['tours_entry'].get('cricbuzz_series') or 'NONE (no L1 witness)'}")
        print(f"  tours.json: {json.dumps(t['tours_entry'], ensure_ascii=False)}")

    # A tour with no cricbuzz_series has no SECOND witness at all now that cricapi is gone — ESPN
    # is both the base feed and the only feed, so an ESPN gap or error has nothing to contradict it
    # until cricsheet (L2) publishes days later. Never let that be a log line nobody reads.
    blind = [t["tour_name"] for t in tours if t.get("no_l1_witness")]
    if blind:
        print(f"\n⚠⚠ {len(blind)} of {len(tours)} tour(s) have NO L1 SECOND WITNESS "
              f"(no cricbuzz_series): {blind}")
        print("   ESPN is single-sourced for these. Resolve each Cricbuzz series by hand "
              "(python3 -c \"import cricbuzz; print(cricbuzz.series_candidates('<tour>', <year>))\") "
              "and add \"cricbuzz_series\" to its tours.json entry.")

    if args.emit:
        os.makedirs(args.emit, exist_ok=True)
        json.dump(tours, open(f"{args.emit}/generated.json", "w"), ensure_ascii=False, indent=2)
        print(f"\nwrote {args.emit}/generated.json", file=sys.stderr)
    if args.apply and not args.dry_run:
        applied = apply_to_repos(tours)
        print(f"\n[apply] wrote {len(applied)} tour(s): {applied}", file=sys.stderr)
        # machine-readable summary for the workflow (commit msg / notify)
        print("::applied::" + json.dumps(applied))
    elif args.dry_run:
        print("\n[dry-run] no repo files written.", file=sys.stderr)


if __name__ == "__main__":
    main()
