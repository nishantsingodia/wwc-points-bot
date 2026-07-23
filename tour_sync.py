#!/usr/bin/env python3
"""
tour_sync — auto-discover cricket tours starting soon and generate the draft-app +
points-bot artifacts for them, so a new tour appears in wwc-draft and gets scored by
the bot with NO manual code edits.

Pipeline (run daily from GH Actions):
  discover (cricapi /currentMatches for near-live + /series search for upcoming fixtures,
            watchlist-filtered; rotates across CRICKET_API_KEY/KEY2 and RAISES if all keys
            are quota-blocked so a dead key never silently reports "0 tours")
    -> for each new (series, format) tour: fixtures (/series_info) + squads (/match_squad)
    -> generate draft artifacts (data/matches.json, data/players-raw.json, data/team-codes.json)
       + bot artifacts (tours.json, <tour>_squads.json, toss_windows.json)
    -> validate + commit both repos + trigger the Vercel deploy hook (done by the workflow)

This module is the GENERATOR. The commit/deploy wiring lives in the GH Actions workflow.
Squad squad_number is a role-group seed; it self-corrects from the sheet's Bat Order after
match 1. efppm is a role-based pick-guide seed. pids resolve via the registry (returning
players) else slug:, upgraded post-match by build_registry.py / backfill_draft_pids.py.

Usage:
  python3 tour_sync.py --dry-run                 # live discovery, print only, touch nothing
  python3 tour_sync.py --dry-run --from-saved DIR # transform saved cricapi JSON (no quota)
  python3 tour_sync.py --emit OUTDIR              # write generated artifacts to OUTDIR (still no repo writes)
"""
import argparse, json, os, re, sys, urllib.request, urllib.parse
from datetime import datetime, timedelta, timezone

DRAFT = os.environ.get("DRAFT_REPO", os.path.expanduser("~/wwc-draft"))
BOT = os.path.dirname(os.path.abspath(__file__))
API = "https://api.cricapi.com/v1"
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
# formats we ingest (cricapi matchType); tests/other are skipped
FMT_BUCKET = {"t20": "T20", "t20i": "T20", "odi": "ODI",
              "hundred": "T20", "the hundred": "T20", "100-ball": "T20", "100 ball": "T20"}

def _fmt_of(m):
    """Bucket a cricapi match to 'T20' or 'ODI', tolerating The Hundred (matchType 'hundred')
    and the null/variable matchType cricapi emits for franchise leagues — mirrors the bot's
    is_fmt (wc_fps_to_csv.py) so a Hundred/uncategorized fixture isn't silently dropped (the
    22 Jul bug: no 'hundred' bucket → empty match list → gen_tour returned None → no ingest)."""
    mt = (m.get("matchType") or "").lower()
    if mt in FMT_BUCKET:
        return FMT_BUCKET[mt]
    nm = (m.get("name") or "").lower()
    if "test" in nm:
        return None
    if "odi" in nm:
        return "ODI"
    if "hundred" in nm or "t20" in nm or "100" in nm:
        return "T20"
    return None  # genuinely unknown — caller logs the drop loudly
DISCOVERY_WINDOW_DAYS = int(os.environ.get("SYNC_WINDOW_DAYS", "4"))
# Distinct search tokens for /series fixtures-discovery (fewer than MAJOR_LEAGUES to save
# quota; cricapi matches substrings and results are de-duped by series id). "hundred"
# catches both the men's and women's Hundred. Override with SYNC_SEARCH_TERMS=a,b,c to
# scope a manual run (e.g. SYNC_SEARCH_TERMS=hundred).
_LEAGUE_SEARCH_TERMS = [
    "hundred", "indian premier league", "big bash", "pakistan super league",
    "caribbean premier league", "sa20", "international league t20",
    "major league cricket", "lanka premier league", "bangladesh premier league",
    "super smash", "vitality blast", "womens premier league", "wbbl",
]
# ALSO search each major TEAM by name — this is how a pre-live BILATERAL is caught (the gap that
# missed "India tour of Zimbabwe 2026" starting today: /currentMatches is empty until the first
# ball, /series page-0 is future-ordered, and only leagues were searched). cricapi ranks
# "India tour of Zimbabwe" into a search for "india"; in_scope() then reads BOTH teams from the
# series name and confirms the major-vs-major bilateral. ~12 extra hits/run — the cost of catching
# a marquee bilateral ~4 days ahead instead of only once it goes live. Dedup is by series id.
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


# ── cricapi layer (key rotation + loud failures) ─────────────────────────────
# The free tier is 100 hits/day PER KEY. The points bot (wc_fps_to_csv.py) survives an
# exhausted key by rotating to CRICKET_API_KEY2; tour-sync historically used ONLY the
# first key with no failover AND swallowed a failure response as an empty result — so a
# single blocked key made discovery silently return "0 tours" while the workflow went
# green. We now (a) rotate across all keys and (b) RAISE when every key is quota-blocked,
# so a dead key fails the run visibly instead of masquerading as "nothing on today".
def _keys():
    # A DEDICATED key (TOUR_SYNC_API_KEY) wins outright and is used EXCLUSIVELY — that's the
    # whole point: discovery needs only ~15-25 hits/day, so its own free key never contends
    # with the auction app or the live points bot (which together drain the shared keys before
    # tour-sync ever runs). Keep it out of .env.local and out of the points-bot secrets.
    ded = os.environ.get("TOUR_SYNC_API_KEY", "").strip().strip('"')
    if ded:
        return [ded]
    raw = os.environ.get("CRICKET_API_KEY", "").split(",") + [os.environ.get("CRICKET_API_KEY2", "")]
    keys = [k.strip().strip('"') for k in raw if k.strip()]
    if not keys:  # local convenience: borrow the auction app's key(s) for dry-runs
        p = os.path.expanduser("~/cricket-auction-helper/.env.local")
        if os.path.exists(p):
            m = re.search(r"CRICKET_API_KEY=([^\n\"]+)", open(p).read())
            if m:
                keys = [x.strip().strip('"') for x in m.group(1).split(",") if x.strip()]
    seen, out = set(), []          # de-dupe, preserve order
    for k in keys:
        if k not in seen:
            seen.add(k); out.append(k)
    return out

API_KEYS = _keys()
_key_idx = 0

def _is_quota(d):
    r = (d.get("reason") or "").lower()
    return d.get("status") != "success" and ("limit" in r or "block" in r or "hits" in r)

def capi(path, **params):
    """Query cricapi, failing over to the next key on a quota/blocked response. Raises if
    EVERY key is quota-blocked. A non-quota non-success (e.g. a squad-less match) is returned
    as-is so tolerant callers can handle it."""
    global _key_idx
    if not API_KEYS:
        raise RuntimeError("no cricapi key (set CRICKET_API_KEY and/or CRICKET_API_KEY2)")
    data = {"status": "failure", "reason": "no api key"}
    for _ in range(len(API_KEYS)):
        url = f"{API}/{path}?" + urllib.parse.urlencode({**params, "apikey": API_KEYS[_key_idx]})
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                data = json.load(r)
        except Exception as e:
            data = {"status": "failure", "reason": f"fetch error: {e}"}
        if data.get("status") == "success" or not _is_quota(data):
            break
        info = data.get("info") or {}
        if _key_idx + 1 < len(API_KEYS):
            print(f"  capi({path}): key #{_key_idx+1} blocked "
                  f"(hits {info.get('hitsToday','?')}/{info.get('hitsLimit','?')}) "
                  f"— failing over to key #{_key_idx+2}", file=sys.stderr)
            _key_idx += 1
        else:
            break
    if _is_quota(data):
        info = data.get("info") or {}
        raise RuntimeError(f"cricapi {path}: all {len(API_KEYS)} key(s) quota-blocked "
                           f"(hits {info.get('hitsToday','?')}/{info.get('hitsLimit','?')}). "
                           f"Discovery aborted — NOT reporting '0 tours'.")
    return data


# ── ESPN series-id resolution (fills tours_entry.espn_series) ────────────────────
# The points bot pulls the live XI AND (critically) the full fallback scorecard from ESPN,
# scoped by espn_series. tour-sync historically left it BLANK → no ESPN fallback → franchise-
# league points never populate (cricsheet lags, cricapi's scorecard is often empty — the
# 22 Jul Hundred bug). We resolve it here: ESPN search → candidate league ids → VALIDATE each
# by hitting its dated scoreboard and matching the fixture's teams, so we never write a wrong id.
# Unresolved → "" (caller flags loud: the verify gate fails + the Tour Ingest Review tab lists it).
# ESPN blocks non-browser requests, so every call carries a Mozilla UA (mirrors the bot's espn_get).
ESPN_SITE = "https://site.api.espn.com/apis/site/v2/sports/cricket"
ESPN_SEARCH = "https://site.web.api.espn.com/apis/common/v3/search"

def _espn_get(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except Exception:
        return {}

def _espn_search_league_ids(query):
    """League ids ESPN's search returns for a query (best-effort — the caller validates each)."""
    d = _espn_get(f"{ESPN_SEARCH}?limit=15&sport=cricket&query={urllib.parse.quote(query)}")
    out, seen = [], set()
    def walk(o):
        if isinstance(o, dict):
            if o.get("type") == "league" and o.get("id") and str(o["id"]) not in seen:
                seen.add(str(o["id"])); out.append(str(o["id"]))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(d)
    return out

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
    # cricapi dateTimeGMT like "2026-07-14T12:00:00" (UTC, no tz)
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
        "existing_series": {t.get("cricapi_series") for t in tours},
        "existing_espn": {str(t.get("espn_series")) for t in tours if t.get("espn_series")},
        "existing_tabs": {t.get("tab") for t in tours},
        "codes": set(tc.keys()),
        "next_match_num": max((m["matchNum"] for m in dm), default=0) + 1,
        "next_pid_id": max((p["id"] for p in dp), default=10000) + 1,
        "name_flag": name_flag, "reg_alias": reg_alias,
    }

def mint_code(gp, fmt, short, taken):
    gl = "M" if gp == "male" else "W"
    fl = "O" if fmt == "ODI" else "T"
    base = f"{gl}{fl}{short}".upper()[:6]
    code, i = base, 1
    while code in taken:
        i += 1
        code = f"{base}{i}"[:6]
    taken.add(code)
    return code

def resolve_pid(name, reg_alias):
    return reg_alias.get(norm(name)) or "slug:" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


# ── league squads from the auction seed (cricapi has no franchise-league squads) ────
# The auction app maintains curated, identity-anchored squads per league. extract_auction_
# squads.mjs emits them as [{export, gender, teams:[{name, short, players:[{name,role}]}]}].
# build_league_squads picks the seed export that best covers a discovered series' teams.
LEAGUE_TEAM_ALIASES = {   # normalized cricapi team name -> normalized canonical (rebrands etc.)
    "manchester originals": "manchester super giants",
}
# Merge the bot's canonical franchise-rename aliases (registry/team_aliases.json) so tour_sync folds
# cricapi feed names to the SAME canonical the bot SCORES with. Without this the two alias sources
# diverge: a rebrand cricapi lags on (e.g. LPL 2025→2026, Manchester Originals→Super Giants) attaches
# at ingest but the bot's short_of() misses at completion → the sheet label falls back to the raw
# cricapi name → the draft can't attach → 0 COMPLETED points. One source now = no divergence.
try:
    _ta = json.load(open(os.path.join(BOT, "registry", "team_aliases.json"))).get("aliases", {})
    for _canon, _variants in _ta.items():
        for _v in _variants:
            LEAGUE_TEAM_ALIASES.setdefault(norm(_v), norm(_canon))
except Exception as _e:
    print(f"  (team_aliases.json merge skipped: {_e})", file=sys.stderr)

def _team_key(name):
    # strip gender qualifiers so cricapi "MI London", ESPN "MI London (Men)"/"(Women)" and
    # "X Women" all collapse to one key (needed for ESPN event matching + league seed lookup).
    n = re.sub(r"\b(men|women)\b", "", norm(name)).strip()
    n = re.sub(r"\s+", " ", n)
    return LEAGUE_TEAM_ALIASES.get(n, n)

def build_league_squads(seeds, cricapi_teams, gender):
    """Return the league_squads dict gen_tour expects, or None if no seed covers >=2 of the
    series' teams. Both a rebrand alias and the new name collapse onto ONE canonical team."""
    real = [t for t in cricapi_teams if norm(t) not in TBC_NAMES]
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


# ── ESPN-only tour creation (KEYLESS — no cricapi) ──────────────────────────────
# cricapi is NOT needed to CREATE a tour. ESPN carries all three pieces keylessly: discovery
# (search), fixtures (scoreboard, single-date), and FULL squads (summary.squads — the pre-match
# squad, distinct from `rosters` = the announced XI at toss). We build the SAME cricapi-shaped
# inputs gen_tour already consumes (series_info + injected league_squads) so artifact-building is
# reused verbatim. cricapi_series is left "" (espn_series = the ESPN league id); points scoring —
# gated by TOUR CONTROL — can resolve cricapi later or score from cricsheet+ESPN.
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

def _espn_matchlist(lid, now, span_days=25):
    """Scan the ESPN scoreboard day-by-day (it only accepts a single date) from `now` across a
    short tour's span → cricapi-shaped matchList + the event ids. Stops after 6 empty days once a
    match has been seen (a bilateral/short league won't span 25 days). Keyless."""
    matchlist, event_ids, seen, empty = [], [], False, 0
    for i in range(span_days):
        d = (now + timedelta(days=i)).strftime("%Y%m%d")
        evs = _espn_get(f"{ESPN_SITE}/{lid}/scoreboard?dates={d}").get("events", [])
        if not evs:
            if seen and (empty := empty + 1) >= 6:
                break
            continue
        for e in evs:
            teams = _espn_event_teams(e)
            if len(teams) != 2:
                continue
            seen, empty = True, 0
            matchlist.append({
                "id": e.get("id"), "teams": teams, "dateTimeGMT": e.get("date"),
                "date": (e.get("date") or "")[:10], "matchType": "",
                "name": e.get("description") or e.get("shortName") or "", "hasSquad": True,
            })
            event_ids.append(e.get("id"))
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
    Returns {league_id: name}. This is the cricapi-free replacement for discover()."""
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

def espn_build(lid, name, now, horizon, state):
    """Build a tour ENTIRELY from ESPN → (series_info, gender, league_squads) for gen_tour, or None.
    No cricapi. cricapi_series stays '' (espn_series = lid)."""
    matchlist, event_ids = _espn_matchlist(lid, now)
    def _soon(m):
        try:
            return datetime.fromisoformat((m["dateTimeGMT"] or "").replace("Z", "")).replace(tzinfo=timezone.utc) <= horizon
        except Exception:
            return True
    if not matchlist or not any(_soon(m) for m in matchlist):
        return None
    sqmap = {}
    for ev in event_ids:               # first event that has squads posted
        sqmap = _espn_squads(lid, ev)
        if sqmap:
            break
    if len(sqmap) < 2:
        print(f"  espn: {name!r} — squads not posted yet (skip; will catch on a later run)", file=sys.stderr)
        return None
    gender = "female" if re.search(r"\bwomen\b", name, re.I) else "male"
    squads = {t: {"short": (re.sub(r"[^A-Za-z]", "", t)[:3] or t[:3]).upper(),
                  "players": [list(p) for p in players]}
              for t, players in sqmap.items()}
    league_squads = {"canon": {t: t for t in sqmap}, "squads": squads}
    series_info = {"info": {"id": "", "name": name, "espn_id": str(lid)}, "matchList": matchlist}
    return series_info, gender, league_squads


# ── the generator: one (series, format) -> all artifacts ────────────────────────
TBC_NAMES = {"tbc", "tba", "to be confirmed", "to be decided", "winner", ""}

def gen_tour(series_info, squads_by_matchid, fmt, gender, state, league_squads=None):
    """Build one (series, format) tour's artifacts.

    Handles BOTH 2-team bilaterals and N-team leagues: teams are the union across the whole
    fixture list (not just match 1), and TBC/knockout placeholders are skipped.

    squads_by_matchid : {matchId: match_squad['data']} from cricapi (empty for most leagues).
    league_squads     : optional injected squads for a league whose squads cricapi lacks
                        (e.g. from the auction seed):
                          {"canon":  {cricapi_team_name: canonical_name, ...},   # collapses
                                     # aliases (e.g. Manchester Originals -> ...Super Giants),
                                     # excludes TBC; any name absent here is treated as TBC.
                           "squads": {canonical_name: {"short": str,
                                                       "players": [[name, role], ...]}}}  # ordered
    """
    info = series_info["info"]
    ml = [m for m in series_info["matchList"] if _fmt_of(m) == fmt]
    ml.sort(key=lambda m: m.get("dateTimeGMT") or m.get("date") or "")
    if not ml:
        # Loud drop: an in-scope series yielding zero matches for this format is usually an
        # unsupported/variable matchType (the Hundred bug) — never silently emit nothing.
        types = sorted({(m.get("matchType") or "∅") for m in series_info["matchList"]})
        print(f"  gen_tour: '{info.get('name')}' -> 0 {fmt} matches (cricapi types={types}) — dropped",
              file=sys.stderr)
        return None

    ti = {t["name"]: t.get("shortname") or t["name"][:3].upper()
          for m in ml for t in m.get("teamInfo", [])}  # cricapi shortnames

    def canonical(name):
        """Map a raw cricapi team name to its canonical team, or None to drop it (TBC/unknown)."""
        if league_squads is not None:
            return league_squads["canon"].get(name)         # None if unmapped/TBC
        return None if norm(name) in TBC_NAMES else name

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
    if league_squads is not None:
        shorts = {t: league_squads["squads"][t]["short"] for t in teams}
    else:
        shorts = {t: ti.get(t, t[:3].upper()) for t in teams}
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
            # key-regex would mis-score them as T20 — this field is authoritative.
            "format": fmt,
        })
        toss.append(to_utc_z(dt))
        state["next_match_num"] += 1
    if not matches:
        return None

    # ---- rosters: injected (ordered, preserves the curated XI-first order) or cricapi union ----
    players, squads_json, team_codes = [], {}, {}
    for t in teams:
        c = code[t]
        team_codes[c] = {"flag": state["name_flag"].get(t.lower(), "🏏"), "name": t}
        squads_json[c] = {"name": t, "players": []}
        if league_squads is not None:
            items = [(p, norm_role(r)) for p, r in league_squads["squads"][t]["players"]]
        else:
            roster = {}
            for m in ml:
                for team in (squads_by_matchid.get(m["id"]) or []):
                    if canonical(team.get("teamName") or "") == t:
                        for p in team.get("players", []):
                            roster[p["name"]] = norm_role(p.get("role"))
            items = sorted(roster.items(), key=lambda kv: (ROLE_ORDER[kv[1]], kv[0]))
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
    # espn_series: if the source already knows it (ESPN-first path passes info["espn_id"]), use it
    # verbatim. Otherwise (cricapi path) resolve it against the first REAL fixture; "" if unconfirmed
    # (the verify gate + Tour Ingest Review tab flag it). cricapi_series is "" on the ESPN-only path.
    espn_id = (info.get("espn_id") or "").strip()
    if not espn_id:
        for m in ml:
            raw = (m.get("teams") or [None, None])[:2]
            cc = [canonical(x or "") for x in raw]
            if len(cc) == 2 and cc[0] and cc[1] and cc[0] != cc[1]:
                espn_id = resolve_espn_series(tour_name, cc, m.get("dateTimeGMT"))
                break
    tours_entry = {
        "cricapi_series": info.get("id", ""), "ends": ends, "espn_series": espn_id,
        "gender": gender, "name": tour_name, "squads": squads_path, "tab": tab,
    }
    return {
        "tour_name": tour_name, "codes": code,
        "matches": matches, "players": players, "team_codes": team_codes,
        "tours_entry": tours_entry, "squads_json": squads_json,
        "squads_path": squads_path, "toss_windows": toss,
    }


# ── discovery ───────────────────────────────────────────────────────────────────
def _parse_series_date(s):
    """cricapi /series dates arrive as 'Jul 21, 2026' (sometimes ISO). Best-effort -> UTC."""
    if not s:
        return None
    for fmt in ("%b %d, %Y", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None

def _discover_current(now, horizon):
    """Near-live discovery: /currentMatches page 0, in the [now-2d, horizon] window."""
    cm = capi("currentMatches", offset=0)
    matches = cm.get("data", [])
    info = cm.get("info") or {}
    print(f"  discover/currentMatches: {len(matches)} match(es) returned "
          f"[key hits {info.get('hitsToday','?')}/{info.get('hitsLimit','?')}]", file=sys.stderr)
    hits = {}
    for m in matches:
        dt = m.get("dateTimeGMT")
        if not dt:
            continue
        try:
            when = datetime.fromisoformat(dt.replace("Z", "")).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if not (now - timedelta(days=2) <= when <= horizon):
            continue
        ok, kind = in_scope(m.get("name", ""), m.get("teams", []))
        if not ok:
            continue
        sid = m.get("series_id")
        if sid:
            hits[sid] = {"name": m.get("name", ""), "teams": m.get("teams", []), "kind": kind}
    return hits

def _discover_series(now, horizon):
    """Fixtures-based discovery: search /series for each watchlist league and keep the ones
    running or starting inside the window. /currentMatches only shows near-live games, so
    THIS is what catches a tour (e.g. The Hundred) 3-4 days ahead of its first ball."""
    hits = {}
    floor = now - timedelta(days=2)
    for term in SEARCH_TERMS:
        r = capi("series", offset=0, search=term)   # raises loudly if all keys are blocked
        for s in r.get("data", []):
            sid, name = s.get("id"), s.get("name", "")
            if not sid or sid in hits:
                continue
            ok, kind = in_scope(name, [])
            if not ok:
                continue
            start, end = _parse_series_date(s.get("startDate")), _parse_series_date(s.get("endDate"))
            in_window = start is not None and floor <= start <= horizon
            running   = start is not None and end is not None and start <= horizon and end >= floor
            unknown   = start is None                       # fail-open: unparseable dates
            if in_window or running or unknown:
                hits[sid] = {"name": name, "teams": [], "kind": kind,
                             "start": s.get("startDate"), "end": s.get("endDate")}
                print(f"  discover/series[{term!r}]: KEEP {name!r} "
                      f"start={s.get('startDate')} end={s.get('endDate')} ({kind}"
                      f"{', dates-unparsed' if unknown else ''})", file=sys.stderr)
    return hits

def discover(window_days):
    """Return {series_id: {name, teams, kind, ...}} for in-scope series active in the window,
    combining near-live (/currentMatches) and fixtures (/series search) discovery."""
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=window_days)
    hits = _discover_current(now, horizon)
    for sid, meta in _discover_series(now, horizon).items():
        hits.setdefault(sid, meta)
    return hits


def _load(p):
    return json.load(open(p, encoding="utf-8"))

def _dump(p, obj):
    json.dump(obj, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.load(open(p, encoding="utf-8"))  # re-parse: fail loudly on any corruption

def apply_to_repos(tours):
    """Append each generated tour into the draft + bot files. Idempotent-ish: skips a
    tour whose cricapi_series+tab is already registered. Re-parses every file it writes."""
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
        if sheet_id:
            tab = urllib.parse.quote(t["tours_entry"]["tab"])
            pt.append(f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={tab}&headers=1")
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
    ap.add_argument("--from-saved", help="dir with capi_series_info.json + capi_match_squad.json (no quota)")
    ap.add_argument("--emit", help="write generated artifacts to this dir")
    ap.add_argument("--auction-squads", help="JSON from extract_auction_squads.mjs; used as the "
                    "squad source for any discovered league it covers (cricapi has no league squads)")
    ap.add_argument("--source", choices=["espn", "cricapi"],
                    default=os.environ.get("SYNC_SOURCE", "cricapi"),
                    help="AUTO-discovery source. 'cricapi' (default) reliably finds near-term tours; "
                         "'espn' search is unreliable for near-term bilaterals (buries them behind "
                         "historical editions) — kept only as a keyless best-effort.")
    ap.add_argument("--espn-tour", help="KEYLESS add of ONE named tour (no cricapi): resolves the "
                    "ESPN league id from the name, then builds fixtures + full squads from ESPN. "
                    "Use when you know the tour (ESPN can't be trusted to DISCOVER it, but nails "
                    "everything else). e.g. --espn-tour 'India tour of Zimbabwe 2026'")
    args = ap.parse_args()
    state = load_state()
    seeds = json.load(open(args.auction_squads)) if args.auction_squads else []
    if seeds:
        print(f"auction squads: {sum(len(s.get('teams',[])) for s in seeds)} teams across "
              f"{len(seeds)} seed(s)", file=sys.stderr)

    tours = []
    if args.from_saved:
        si = json.load(open(f"{args.from_saved}/capi_series_info.json"))["data"]
        ms = json.load(open(f"{args.from_saved}/capi_match_squad.json")).get("data", [])
        gender = infer_gender(si["info"]["name"], si["matchList"][0]["teams"])
        # attach the one saved match_squad to every ODI match (demo: real run fetches per match)
        odi_ids = [m["id"] for m in si["matchList"] if (m.get("matchType") or "").lower() == "odi"]
        sq_by = {mid: ms for mid in odi_ids}
        for fmt in ("ODI", "T20"):
            t = gen_tour(si, sq_by, fmt, gender, state)
            if t:
                tours.append(t)
    elif args.espn_tour:
        # KEYLESS single-tour add (no cricapi): name -> ESPN league id -> fixtures + full squads.
        # Use when you KNOW the tour — ESPN can't be trusted to auto-discover a near-term bilateral
        # (its search buries it under 1990s editions), but nails everything once given the id.
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(days=45)
        qn = norm(args.espn_tour)
        cands = _espn_search_leagues(args.espn_tour)
        target = (next((c for c in cands if norm(c[1]) == qn), None)
                  or next((c for c in cands if qn in norm(c[1]) or norm(c[1]) in qn), None)
                  or (cands[0] if cands else None))
        if not target:
            print(f"espn-tour: no ESPN league matched {args.espn_tour!r}", file=sys.stderr)
        else:
            lid, name = target
            print(f"espn-tour: {args.espn_tour!r} -> ESPN league {lid} ({name!r})", file=sys.stderr)
            built = espn_build(lid, name, now, horizon, state)
            if built:
                si, gender, lg = built
                for fmt in ("ODI", "T20"):
                    t = gen_tour(si, {}, fmt, gender, state, league_squads=lg)
                    if t and t["tours_entry"]["tab"] not in state["existing_tabs"]:
                        tours.append(t)
                    elif t:
                        print(f"  skip (tab exists): {t['tours_entry']['tab']}", file=sys.stderr)
    elif args.source == "espn":
        # KEYLESS best-effort auto-discovery (unreliable for near-term bilaterals — see --espn-tour).
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(days=DISCOVERY_WINDOW_DAYS)
        found = espn_discover(now, horizon)
        print(f"espn-discover: {len(found)} in-scope tour(s) in next {DISCOVERY_WINDOW_DAYS}d (keyless)", file=sys.stderr)
        for lid, name in found.items():
            if lid in state["existing_espn"]:
                print(f"  skip (already ingested): {name[:50]}", file=sys.stderr)
                continue
            built = espn_build(lid, name, now, horizon, state)
            if not built:
                continue
            si, gender, lg = built
            for fmt in ("ODI", "T20"):
                t = gen_tour(si, {}, fmt, gender, state, league_squads=lg)
                if t and t["tours_entry"]["tab"] not in state["existing_tabs"]:
                    tours.append(t)
                elif t:
                    print(f"  skip (tab exists): {t['tours_entry']['tab']}", file=sys.stderr)
    else:
        now = datetime.now(timezone.utc)
        found = discover(DISCOVERY_WINDOW_DAYS)
        print(f"discover: {len(found)} in-scope series in next {DISCOVERY_WINDOW_DAYS}d", file=sys.stderr)
        for sid, meta in found.items():
            if sid in state["existing_series"]:
                print(f"  skip (already ingested): {meta['name'][:50]}", file=sys.stderr)
                continue
            si = capi("series_info", id=sid).get("data")
            if not si or not si.get("matchList"):
                print(f"  skip (no matchList): {meta['name'][:50]}", file=sys.stderr)
                continue
            ml = si["matchList"]
            types = sorted({(m.get("matchType") or "?").lower() for m in ml})
            def _when(m):
                try:
                    return datetime.fromisoformat((m.get("dateTimeGMT") or "").replace("Z", "")).replace(tzinfo=timezone.utc)
                except ValueError:
                    return None
            upcoming = [m for m in ml if (_when(m) is None or _when(m) >= now - timedelta(days=3))]
            print(f"  series {si['info']['name'][:45]!r}: {len(ml)} matches, types={types}, "
                  f"{len(upcoming)} now/upcoming", file=sys.stderr)
            if not upcoming:
                print(f"  skip (finished edition — no upcoming match): {si['info']['name'][:45]}", file=sys.stderr)
                continue
            gender = infer_gender(si["info"]["name"], ml[0].get("teams", []))
            series_teams = sorted({t for m in ml for t in (m.get("teams") or [])})
            lg = build_league_squads(seeds, series_teams, gender) if seeds else None
            sq_by = {}
            if lg:
                print(f"  squads: injected from auction seed ({len(lg['squads'])} teams)", file=sys.stderr)
            else:
                for m in ml:   # cricapi squads (bilaterals / leagues cricapi actually carries)
                    if _fmt_of(m) and m.get("hasSquad"):
                        r = capi("match_squad", id=m["id"])
                        if r.get("status") == "success":
                            sq_by[m["id"]] = r.get("data", [])
            for fmt in ("ODI", "T20"):
                t = gen_tour(si, sq_by, fmt, gender, state, league_squads=lg)
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
        print(f"  tours.json: {json.dumps(t['tours_entry'], ensure_ascii=False)}")

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
