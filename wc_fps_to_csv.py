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
import os, sys, json, re, csv, time, glob, unicodedata, urllib.request
from difflib import SequenceMatcher
from datetime import date, timedelta, datetime, timezone

# FREQUENT mode = the every-5-min "live lineup" tick (vs the 2-hourly full run).
# It only does work inside a match's toss window and caches series_info so the
# extra ticks don't blow cricapi's 100/day cap. Set via env in live-lineup.yml.
FREQUENT = os.environ.get("FREQUENT") == "1"

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
ESPN_SERIES = os.environ.get("ESPN_SERIES_ID", "1483859").strip()  # ICC Women's T20 WC 2026

# ---- Dream11 T20 rules (mirror of rules.ts) ----
R = dict(
    perRun=1, b4=4, b6=6, m25=4, m50=8, m75=12, m100=16, duck=-2,
    wkt=30, lbwb=8, dot=1, maiden=12, h3=4, h4=8, h5=12,
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
    alias2pid, pid2disp = {}, {}
    try:
        players = json.load(open(path)).get("players", {})
    except Exception:
        return alias2pid, pid2disp
    for pid, e in players.items():
        pid2disp[pid] = e.get("display") or pid
        for a in e.get("aliases", []):
            alias2pid.setdefault(a, pid)
    return alias2pid, pid2disp

ALIAS2PID, PID2DISP = load_registry()
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

def match_squad_to_perf(team_players, pool):
    """team_players: [(short,name,role)]; pool: {normname: perf}.
    Identity-first matching: resolve every feed entry and every squad player to a stable
    registry pid and match on that (deterministic, no threshold, no surname collisions).
    Feed entries sharing a pid are MERGED (fixes stats split across two spellings). Only
    players the registry doesn't cover yet fall to the old fuzzy matcher — and every fuzzy
    hit + every genuine leftover is logged to UNMATCHED_LOG so the registry can be grown.
    Returns {(short,name): perf or None}, leftover pool, ambiguous set (kept for callers)."""
    # Drop junk feed entries ("Player Not Found", empty, stray punctuation).
    pool = {k: v for k, v in pool.items() if k and not is_junk(v.get("name", k))}
    # 1) index feed entries by pid, merging split spellings of the same player
    pid_pool, unresolved = {}, {}
    for k, v in pool.items():
        pid = resolve_pid(v.get("name", k))
        if pid:
            pid_pool[pid] = merge_perf(pid_pool.get(pid), v)
        else:
            unresolved[k] = v
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
            UNMATCHED_LOG.add(f"AUTO   feed '{feed_nm}' -> squad '{name}' (score {sc:.0f})")
            # High-confidence fuzzy hit: auto-promote to the Player Aliases store (no user action).
            AUTO_ALIASES.append((feed_nm, name))
    leftover = {k: v for k, v in unresolved.items() if k not in used_uk}
    for k, v in leftover.items():
        if v.get("played"):
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

def api(path, cache=True, ttl=None, **params):
    """GET with optional caching. Scorecards are cached (immutable once ended);
    series_info is NOT cached in the full run (so re-runs detect newly-completed matches),
    but the frequent tick caches it with a TTL to stay under cricapi's 100/day cap.

    RESILIENCE: if the live fetch fails (network error / quota exhausted / cricapi outage)
    but a previously-cached copy exists, fall back to the STALE copy rather than failing.
    Scorecards are immutable so a stale hit is exact; a stale series_info just means a
    brand-new match is scored one cycle late — far better than freezing the whole sheet
    (the old behaviour aborted the tour, so a cricapi blip left the sheet stale anyway)."""
    os.makedirs(CACHE, exist_ok=True)
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    key = re.sub(r"[^a-z0-9]", "_", f"{path}_{qs}".lower())
    fp = os.path.join(CACHE, key + ".json")
    fresh = os.path.exists(fp) and (ttl is None or (time.time() - os.path.getmtime(fp) < ttl))
    if cache and fresh:
        return json.load(open(fp))

    def is_quota(d):
        r = (d.get("reason") or "").lower()
        return d.get("status") != "success" and ("limit" in r or "block" in r or "hits" in r)

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
        json.dump(data, open(fp, "w"))
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

def blank_perf(name):
    return dict(name=name, team="", r=0, b=0, **{"4s": 0, "6s": 0}, dismissed=False,
                dismissal="", balls=0, runs_conceded=0, w=0, lbwb=0, dots=0,
                maidens=0, catches=0, stumpings=0, runouts=0, dro=0, played=False,
                bat_order=0)  # 1-based batting position from the scorecard (0 = unknown/DNB)

def team_key(teams):
    """Date-independent team identity: normalized names with 'women' dropped."""
    return frozenset(norm(t.replace("Women", "").replace("women", "")) for t in teams)

# ---- cricsheet ball-by-ball (mirror of etl_cricsheet.py) -> EXACT dots/maidens/XI ----
def load_cricsheet_index(dirpath, gender="female"):
    """(date, team_key) -> json path, for completed T20s of the given gender
    (so a men's tour matches men's cricsheet files, not women's, and vice versa)."""
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
    def get(n):
        k = norm(n)
        if k not in perf:
            perf[k] = blank_perf(n)
        return perf[k]
    for tname, plist in info.get("players", {}).items():   # known playing XI -> +4 each
        for n in plist:
            p = get(n); p["played"] = True; p["team"] = p["team"] or tname
    for inn in d.get("innings", []):
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
                if over_bowler is None: over_bowler = dl["bowler"]
                # register striker then non-striker so openers get positions 1 & 2
                crease(dl["batter"])
                if dl.get("non_striker"): crease(dl["non_striker"])
                pb = get(dl["batter"]); pb["played"] = True
                if not is_wide: pb["b"] += 1
                pb["r"] += rb
                if rb == 4: pb["4s"] += 1
                elif rb == 6: pb["6s"] += 1
                pw = get(dl["bowler"]); pw["played"] = True
                pw["runs_conceded"] += rt
                if legald:
                    pw["balls"] += 1; legal += 1; over_runs += rt
                    if rt == 0: pw["dots"] += 1
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

def espn_get(path, cache=True, **params):
    os.makedirs(CACHE, exist_ok=True)
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    key = re.sub(r"[^a-z0-9]", "_", f"espn_{ESPN_SERIES}_{path}_{qs}".lower())
    fp = os.path.join(CACHE, key + ".json")
    if cache and os.path.exists(fp):
        return json.load(open(fp))
    url = f"{ESPN_BASE}/{ESPN_SERIES}/{path}?{qs}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except Exception:
        return {}
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
            out[k] = {"name": name, "dots": 0}
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

def espn_team_map(event_id):
    """{norm(player): team displayName} from ESPN rosters — reliable team attribution
    (cricapi's innings labels are sometimes malformed)."""
    d = espn_get("summary", event=event_id)
    out = {}
    for team in d.get("rosters", []):
        tn = team.get("team", {}).get("displayName", "")
        for p in team.get("roster", []):
            a = p.get("athlete", {})
            nm = a.get("fullName") or a.get("displayName")
            if nm and tn:
                out[norm(nm)] = tn
    return out

def espn_xi(event_id):
    """Playing XI (incl. subs that came on) from ESPN summary -> for the +4 in-XI bonus,
    even for players who didn't bat/bowl/field (e.g. a captain who wasn't needed)."""
    d = espn_get("summary", event=event_id)
    out = {}
    for team in d.get("rosters", []):
        for p in team.get("roster", []):
            a = p.get("athlete", {})
            nm = a.get("fullName") or a.get("displayName")
            if nm and (p.get("starter") or p.get("subbedIn")):
                out[norm(nm)] = {"name": nm}
    return out

def parse_espn(event_id):
    """Full scorecard from ESPN ball-by-ball (cricinfo) — exact dots/maidens/fielding,
    plus the XI from the summary. Super-over deliveries (period>2) are excluded
    (Dream11 awards no points for them). Returns (perf, super_over_seen)."""
    pbp = espn_get("playbyplay", event=event_id, limit=600)
    items = pbp.get("commentary", {}).get("items", [])
    perf, overs, super_over = {}, {}, False
    def get(n):
        k = norm(n)
        if k not in perf:
            perf[k] = blank_perf(n)
        return perf[k]
    for it in items:
        per = it.get("period")
        if per and per > 2:
            super_over = True
            continue
        desc = (it.get("playType", {}).get("description") or "").lower()
        bw = (it.get("bowler", {}).get("athlete") or {}).get("fullName")
        bt = (it.get("batsman", {}).get("athlete") or {}).get("fullName")
        try:
            sv = int(it.get("scoreValue", 0) or 0)
        except (TypeError, ValueError):
            sv = 0
        is_wide = desc == "wide"
        legal = not is_wide and "no ball" not in desc
        if bt:
            pb = get(bt); pb["played"] = True
            if not is_wide:
                pb["b"] += 1
            if desc in ("run", "four", "six"):
                pb["r"] += sv
            if desc == "four":
                pb["4s"] += 1
            elif desc == "six":
                pb["6s"] += 1
        if bw:
            pw = get(bw); pw["played"] = True
            if legal:
                pw["balls"] += 1
            if desc not in ("bye", "leg bye"):
                pw["runs_conceded"] += sv
            if legal and sv == 0:
                pw["dots"] += 1
            ok = (per, it.get("over", {}).get("number"))
            o = overs.setdefault(ok, {"legal": 0, "runs": 0, "bowler": bw})
            o["bowler"] = bw
            if legal:
                o["legal"] += 1; o["runs"] += sv
        dis = it.get("dismissal") or {}
        if dis.get("dismissal"):
            typ = (dis.get("type") or "").lower()
            if bt:
                pb = get(bt); pb["dismissed"] = True; pb["dismissal"] = it.get("shortText", typ)
            if bw and typ not in ("run out", "retired hurt", "retired not out", "obstructing the field"):
                pw = get(bw); pw["w"] += 1
                if typ in ("bowled", "lbw"):
                    pw["lbwb"] += 1
            fld = (dis.get("fielder", {}).get("athlete") or {}).get("fullName")
            if typ == "caught" and fld and norm(fld) != norm(bw or ""):
                get(fld)["catches"] += 1
            elif typ == "stumped" and fld:
                get(fld)["stumpings"] += 1
            elif typ == "run out":
                m = re.search(r"run out \(([^)]*)\)", it.get("shortText", "") or "", re.I)
                names = ([re.sub(r"(sub\b|†|\[|\])", "", x).strip() for x in m.group(1).split("/")]
                         if m else ([fld] if fld else []))
                names = [n for n in names if n]
                for n in names:
                    rp = get(n); rp["runouts"] += 1
                    if len(names) == 1:
                        rp["dro"] += 1
    for o in overs.values():
        if o["legal"] == 6 and o["runs"] == 0 and o["bowler"]:
            get(o["bowler"])["maidens"] += 1
    for k, e in espn_xi(event_id).items():   # +4 in-XI even for players with no stat line
        if k not in perf:
            perf[k] = blank_perf(e["name"])
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
def score(p, role):
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
    if p["balls"] > 0:
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

def overs_to_balls(o):
    o = float(o or 0)
    whole = int(o)
    return whole * 6 + round((o - whole) * 10)

def parse_match(mid):
    """Return {normalized_name: perf-dict} for one match's scorecard."""
    d = api("match_scorecard", id=mid)
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
    return perf

def run_tour(tour):
    """Process ONE tour (its own cricapi+ESPN series + squad list) and write its tab."""
    global WC_SERIES, ESPN_SERIES, SQUADS_JSON, GSHEET_TAB, CURRENT_TOUR
    WC_SERIES = tour["cricapi_series"]
    ESPN_SERIES = tour.get("espn_series", "")
    SQUADS_JSON = tour.get("squads_path", "")
    GSHEET_TAB = tour["tab"]
    CURRENT_TOUR = tour["name"]
    out_csv = tour.get("out_csv", OUT)
    print(f"=== Tour: {tour['name']}  ->  tab '{GSHEET_TAB}' ===", file=sys.stderr)

    squads = load_squads()
    # map normalized full team name -> short code (plus a "Women"-stripped variant,
    # since feeds vary between "Sri Lanka" and "Sri Lanka Women")
    name2short = {norm(v["name"]): k for k, v in squads.items()}
    strip_women = lambda s: norm(re.sub(r"(?i)\bwomen\b", "", s or ""))
    name2short_stripped = {strip_women(v["name"]): k for k, v in squads.items()}
    def short_of(team_full):
        return name2short.get(norm(team_full)) or name2short_stripped.get(strip_women(team_full))

    # Full run: always fresh (detect newly-ended matches). Frequent tick: cache 2h
    # so the every-5-min ticks don't spend the cricapi daily budget.
    info = api("series_info", cache=FREQUENT, ttl=(7200 if FREQUENT else None), id=WC_SERIES)
    matches = info.get("data", {}).get("matchList", [])
    # Guard: if cricapi failed/returned nothing, ABORT before touching the sheet
    # (otherwise we'd clear it and write an empty table — wiping good data).
    if info.get("status") != "success" or not matches:
        sys.exit("series_info fetch failed or empty — aborting; sheet left unchanged.")
    # T20Is only — a tour can mix formats. cricapi sometimes leaves matchType null
    # (seen on ODIs!), so trust matchType when present, else fall back to the match name.
    def is_t20(m):
        mt = (m.get("matchType") or "").lower()
        if mt:
            return "t20" in mt
        nm = (m.get("name") or "").lower()
        return "t20" in nm and "odi" not in nm and "test" not in nm
    ended = [m for m in matches if m.get("matchEnded") and is_t20(m)]
    cs_idx = load_cricsheet_index(CRICSHEET_DIR, tour.get("gender", "female"))
    print(f"{len(ended)}/{len(matches)} matches completed | cricsheet {tour.get('gender','female')} matches indexed: {len(cs_idx)}", file=sys.stderr)

    cols = ["Match", "Date", "Team", "Player ID", "Full Name", "Role", "Played",
            "Runs", "Balls", "4s", "6s", "SR", "Dismissal",
            "Overs", "Maidens", "Dots", "Runs Conceded", "Wickets", "Econ",
            "Catches", "Stumpings", "Run Outs",
            "Pts Bat", "Pts Bowl", "Pts Field", "Pts SR", "Pts Econ", "Pts XI",
            "Fantasy Points", "Source", "In Squad List", "Bat Order"]
    rows = []
    n_cs = n_espn = n_api = 0
    for mi, m in enumerate(sorted(ended, key=lambda x: x.get("dateTimeGMT", x.get("date", ""))), 1):
        teams = m.get("teams", [])
        mdate = m.get("date", "")
        label = f"Match {mi} — " + " v ".join(name2short.get(norm(t), t) for t in teams)

        # cricapi scorecard (used as fallback + an independent cross-check)
        try:
            api_perf = {k: v for k, v in parse_match(m["id"]).items() if v["played"]} if m.get("id") else {}
        except Exception:
            api_perf = {}

        # Source priority:
        #   cricsheet (official, exact everything) — when posted, overrides all
        #   else cricapi scorecard (PRIMARY base) + ESPN/cricinfo for dot-balls & the +4 XI
        #        (cricapi's scorecard tracks the official card best; ESPN fills what it lacks)
        #   else cricapi alone (limited: no dots/XI) if ESPN is unavailable
        cs_path = next((cs_idx[(d, team_key(teams))] for d in date_variants(mdate)
                        if (d, team_key(teams)) in cs_idx), None)
        espn_perf, super_over, team_map = {}, False, {}
        if cs_path:
            perf = {k: v for k, v in parse_cricsheet(cs_path)[0].items() if v["played"]}
            n_cs += 1; dots_final = True; status = "cricsheet · official"
        else:
            perf = api_perf
            ev = espn_event_id(mdate, teams)
            if ev:
                espn_perf, super_over = parse_espn(ev)
                espn_perf = {k: v for k, v in espn_perf.items() if v["played"]}
                team_map = espn_team_map(ev)
            # Not from cricsheet yet -> dots are single-sourced from ESPN with NO validator
            # (cricapi has no dots, cricsheet not posted). Flag the whole row PROVISIONAL so
            # it's clear these numbers may be revised once cricsheet posts (lags ~1-5 days),
            # which then overwrites ESPN dots with exact figures.
            if espn_perf:
                n_espn += 1; dots_final = True
                status = ("cricapi + ESPN dots/XI · ⏳ provisional (dots unverified, awaiting cricsheet)"
                          + (" · super-over excl" if super_over else ""))
            else:
                n_api += 1; dots_final = False
                status = "cricapi · limited (no dots/XI — ESPN unavailable) · ⏳ provisional (awaiting cricsheet)"

        team_players = []
        for tname in teams:
            short = name2short.get(norm(tname))
            if short:
                team_players += [(short, n, r) for n, r in squads[short]["players"]]
        assigned, leftover, ambiguous = match_squad_to_perf(team_players, perf)

        # Merge ESPN (squad-level): inject dot-balls, credit +4 in-XI to players cricapi
        # didn't list, and cross-check runs/wickets — disagreements flagged for review.
        xcheck = set()
        if espn_perf:
            espn_assigned = match_squad_to_perf(team_players, espn_perf)[0]
            for k, e in espn_assigned.items():
                base = assigned.get(k)
                if base:
                    base["dots"] = e["dots"]                      # exact dots from ESPN
                    if base.get("r", 0) != e.get("r", 0) or base.get("w", 0) != e.get("w", 0):
                        xcheck.add(k)                             # cricapi vs ESPN disagree
                elif e.get("played"):
                    np = blank_perf(e["name"]); np["played"] = True; np["dots"] = e.get("dots", 0)
                    assigned[k] = np                              # in XI, no cricapi line -> +4

        def emit(short, name, role, d, in_squad):
            src = status
            if (short, name) in xcheck:
                src += " · ⚠ differs vs ESPN"
            # Resolve identity: stable Player ID + canonical display name (so the row shows
            # ONE consistent name regardless of which feed/spelling supplied the stats, and
            # the draft can join by id instead of fuzzy-matching the name).
            pid = resolve_pid(name) or (resolve_pid(d["name"]) if d else "") or ""
            full = PID2DISP.get(pid, name) if pid else name
            if d:
                # Unknown-role leftovers: use the role you set in Needs Review if any, else a
                # best-guess from their stats (never a bare "?"). Role drives SR/Econ penalties.
                role_out = role if role != "?" else (ROLE_OVERRIDE.get(norm(name)) or guess_role(d))
                s = score(d, role_out)
                sr = round(d["r"] / d["b"] * 100, 1) if d["b"] else ""
                econ = round(d["runs_conceded"] / (d["balls"] / 6), 2) if d["balls"] else ""
                dots_out = d["dots"] if dots_final else ""  # never fill dots from a no-dots source
                rows.append([label, mdate, short, pid, full, role_out, "Y",
                             d["r"], d["b"], d["4s"], d["6s"], sr, d["dismissal"],
                             round(d["balls"] / 6, 1) if d["balls"] else "",
                             d["maidens"], dots_out, d["runs_conceded"], d["w"], econ,
                             d["catches"], d["stumpings"], d["runouts"],
                             s["bat"], s["bowl"], s["field"], s["sr"], s["eco"], s["xi"],
                             s["total"], src, in_squad, d.get("bat_order") or ""])
            else:
                rows.append([label, mdate, short, pid, full, role, "N"] + [""] * 22 +
                            [src, in_squad, ""])

        for short, name, role in team_players:
            emit(short, name, role, assigned.get((short, name)), "Y")
        # players who featured but matched no squad name -> show for manual review,
        # attributing their team (ESPN roster first, then the parsed team) so it's never a bare "?".
        for d in leftover.values():
            tfull = team_map.get(norm(d["name"])) or best_team(d["name"], team_map) or d.get("team", "")
            short = short_of(tfull) or "?"
            emit(short, d["name"], "?", d, "N")
    print(f"sources: {n_cs} cricsheet(official), {n_espn} cricapi+ESPN, {n_api} cricapi-only", file=sys.stderr)

    # ── Toss-time announced XI (matches NOT yet ended) ───────────────────────────
    # After the toss (~30 min pre-play) ESPN posts each side's playing XI. We write it
    # as Played=Y rows with blank stats so the draft app's getLastPlayedXI shows the
    # REAL announced XI for the upcoming match instead of last-match's. Once the match
    # ends, the next run replaces these with full-stat rows. Best-effort: if ESPN hasn't
    # posted the XI yet, we simply write nothing (no harm).
    # Gate on ESPN having the XI (the real signal), not cricapi's lagging toss flags.
    # Only look at not-ended matches within ±1 day of today to bound ESPN queries.
    today = date.today()
    def near_today(m):
        for ds in date_variants(m.get("date", "")):
            try:
                if abs((date.fromisoformat(ds) - today).days) <= 1:
                    return True
            except ValueError:
                pass
        return False
    pending = [m for m in matches if is_t20(m) and not m.get("matchEnded") and near_today(m)]
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
        label = f"Match {len(ended) + j} — " + " v ".join(name2short.get(norm(t), t) for t in teams)
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
                        [src, "Y", ""])
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
                        [src, "N", ""])
        n_toss += 1
    if n_toss:
        print(f"toss XI written for {n_toss} not-ended match(es)", file=sys.stderr)

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

# Days after a tour's last match to keep refreshing (lets cricsheet post its official
# data + a buffer); after this the tour is FROZEN — no API calls, no writes, tab kept as-is.
FREEZE_GRACE_DAYS = int(os.environ.get("FREEZE_GRACE_DAYS", "21"))

def is_active(tour):
    """A tour stays live until `ends` + grace; then it's dormant (skipped entirely)."""
    e = tour.get("ends")
    if not e:
        return True
    try:
        return date.today() <= date.fromisoformat(e) + timedelta(days=FREEZE_GRACE_DAYS)
    except ValueError:
        return True

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

def main():
    if not KEY:
        sys.exit("Set CRICKET_API_KEY env var.")
    if FREQUENT and not in_toss_window():
        print("frequent tick: no match in toss window — nothing to do", file=sys.stderr)
        return
    # No-code manual fixes (no-op locally / without creds): load the persistent alias store,
    # then apply any rows you marked 'Yes' in Needs Review. Then process tours; finally persist
    # new aliases and republish the review queue.
    load_sheet_aliases()
    read_review_confirmations()
    tours = load_tours()
    print(f"{len(tours)} tour(s): {', '.join(t['name'] for t in tours)}", file=sys.stderr)
    for t in tours:
        if not is_active(t):
            print(f"-- {t['name']}: dormant (ended {t.get('ends')}, frozen) — skipped", file=sys.stderr)
            continue
        try:
            run_tour(t)
        except SystemExit as e:           # one tour aborting must not kill the others
            print(f"!! tour '{t.get('name')}' skipped: {e}", file=sys.stderr)
        except Exception as e:
            print(f"!! tour '{t.get('name')}' error: {e}", file=sys.stderr)
    if not FREQUENT:
        sync_player_aliases()   # persist auto + confirmed aliases into the Player Aliases store
        write_review_tab()      # publish remaining unmatched players (closest-match + Yes/No)

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

ALIASES_TAB = "Player Aliases"   # user-editable: Feed Name -> Correct Player (no-code fix)
REVIEW_TAB = "Needs Review"      # bot-written: players that need a human glance

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
    ci = next((hdr[k] for k in ("Correct? (Yes/No/New)", "Correct? (Yes/No)", "Correct?") if k in hdr), 5)
    ti, fi, si = hdr.get("Tour", 0), hdr.get("Feed Name", 2), hdr.get("Closest Match", 3)
    ri = hdr.get("Role", -1)
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
        # "New" (a genuine non-listed player) or "No" (bad guess) -> acknowledge: stop re-flagging.
        if ans in ("no", "n", "new") or closest.lower() == "new":
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

def write_review_tab():
    """Publish the still-unmatched players to 'Needs Review' as: closest-match guess + a
    'Correct?' column you answer Yes/No. A Yes is auto-applied next run (then the row drops
    off). Your prior Yes/No answers are preserved. Rewritten each full run."""
    sh = open_gsheet()
    if sh is None:
        return
    import gspread
    header = ["Tour", "Team", "Feed Name", "Closest Match", "Role", "Correct? (Yes/No/New)"]
    # Drop anything you've already resolved (Yes/No/New) so the tab only ever shows open items.
    items = [r for r in dedup_review([r for r in REVIEW if r["kind"] == "review"])
             if norm(r["feed"]) not in ACK]
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
              f"(answer 'Correct?' Yes/No; Yes auto-applies next run).", file=sys.stderr)
    except Exception as e:
        print(f"could not write '{REVIEW_TAB}' tab: {e}", file=sys.stderr)

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
    print(f"Wrote {len(rows)} rows to Google Sheet tab '{GSHEET_TAB}'.", file=sys.stderr)

if __name__ == "__main__":
    main()
