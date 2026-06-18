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
from datetime import date, timedelta

def date_variants(d):
    """A date plus ±1 day (ISO strings) — to absorb timezone differences between feeds."""
    try:
        b = date.fromisoformat(d)
        return [d, (b - timedelta(days=1)).isoformat(), (b + timedelta(days=1)).isoformat()]
    except ValueError:
        return [d]

API = "https://api.cricapi.com/v1"
KEY = os.environ.get("CRICKET_API_KEY", "").strip()
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

# API spelling -> squad spelling, for cases below the fuzzy threshold.
ALIAS = {
    "kavisha dilhari": "kaveesha dilhari",
    "sugandika kumari": "sugandika dasanayaka",
}

def match_squad_to_perf(team_players, pool):
    """team_players: [(short,name,role)]; pool: {normname: perf}.
    Greedy 1-1 best-match. Returns {(short,name): perf or None}, plus leftover pool."""
    pairs = []
    by_target = {}   # (short,name) -> sorted [(score, pk)]
    for short, name, role in team_players:
        nn = norm(name)
        nt = nn.split(); ln, fi = nt[-1], nt[0][0]
        cand = []
        for pk in pool:
            ak = ALIAS.get(pk, pk)
            pt = ak.split(); pl, pf = pt[-1], pt[0][0]
            if ak == nn:
                sc = 100.0
            elif set(nt).issubset(set(pt)):   # all squad-name tokens present (ESPN's verbose names)
                sc = 95.0
            elif pl == ln and pf == fi:
                sc = 92.0
            elif pl == ln and max((SequenceMatcher(None, a, b).ratio()
                                   for a in nt for b in pt), default=0) >= 0.85:
                sc = 90.0   # same surname + a near-matching given name (Kaveesha~Kavisha)
            else:
                sc = SequenceMatcher(None, nn, ak).ratio() * 100
                if pl == ln: sc += 8
                if pt[0] == nt[0]: sc += 6
            pairs.append((sc, short, name, pk))
            cand.append((sc, pk))
        by_target[(short, name)] = sorted(cand, key=lambda x: -x[0])
    pairs.sort(key=lambda x: -x[0])
    assigned, used_sq, used_pk = {}, set(), set()
    for sc, short, name, pk in pairs:
        if sc < 84 or (short, name) in used_sq or pk in used_pk:
            continue
        assigned[(short, name)] = pool[pk]
        used_sq.add((short, name)); used_pk.add(pk)
    # ambiguity: an assigned player whose top-2 *different* candidates score within 6 pts
    # (e.g. same-surname siblings) — caller should flag it rather than trust silently.
    ambiguous = set()
    for t, cands in by_target.items():
        if t in assigned and len(cands) >= 2 and cands[0][0] >= 84 and (cands[0][0] - cands[1][0]) < 6:
            ambiguous.add(t)
    leftover = {k: v for k, v in pool.items() if k not in used_pk}
    return assigned, leftover, ambiguous

def best_team(name, team_map):
    """Fuzzy-match a player name to an ESPN roster {norm_name: team} and return the team
    (handles verbose ESPN spellings). '' if no confident match."""
    nn = norm(name); nt = nn.split(); ln, fi = nt[-1], nt[0][0]
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

def api(path, cache=True, **params):
    """GET with optional caching. Scorecards are cached (immutable once ended);
    series_info is NOT (so re-runs detect newly-completed matches)."""
    os.makedirs(CACHE, exist_ok=True)
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    key = re.sub(r"[^a-z0-9]", "_", f"{path}_{qs}".lower())
    fp = os.path.join(CACHE, key + ".json")
    if cache and os.path.exists(fp):
        return json.load(open(fp))
    url = f"{API}/{path}?apikey={KEY}&{qs}"
    with urllib.request.urlopen(url, timeout=30) as r:
        data = json.load(r)
    if data.get("status") == "success":
        json.dump(data, open(fp, "w"))
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
                maidens=0, catches=0, stumpings=0, runouts=0, dro=0, played=False)

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
        for over in inn.get("overs", []):
            legal = over_runs = 0; over_bowler = None
            for dl in over.get("deliveries", []):
                rb = dl.get("runs", {}).get("batter", 0); rt = dl.get("runs", {}).get("total", 0)
                ex = dl.get("extras", {}); is_wide = "wides" in ex; is_nb = "noballs" in ex
                legald = not is_wide and not is_nb
                if over_bowler is None: over_bowler = dl["bowler"]
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
        if p["dismissed"] and p["r"] == 0 and role != "BOWL":
            bat += R["duck"]
        if p["b"] >= 10 and role != "BOWL":
            sr = p["r"] / p["b"] * 100
            if sr > 170: sr_pts += 6
            elif sr > 150: sr_pts += 4
            elif sr >= 130: sr_pts += 2
            elif 60 <= sr <= 70: sr_pts += -2
            elif 50 <= sr < 60: sr_pts += -4
            elif sr < 50: sr_pts += -6
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
        for bt in inn.get("batting", []):
            pl = get(bt["batsman"]["name"]); pl["played"] = True; setteam(pl, bat_team)
            pl["r"] += bt.get("r", 0) or 0; pl["b"] += bt.get("b", 0) or 0
            pl["4s"] += bt.get("4s", 0) or 0; pl["6s"] += bt.get("6s", 0) or 0
            dis = (bt.get("dismissal") or "").lower()
            dtext = (bt.get("dismissal-text") or "")
            if dtext and "not out" not in dtext.lower() and dtext.lower() != "not out":
                pl["dismissed"] = True; pl["dismissal"] = dtext
            # credit lbw/bowled bonus to the bowler
            if ("bowled" in dis or "lbw" in dis) and bt.get("bowler"):
                setteam(get(bt["bowler"]["name"]), bowl_team)
                get(bt["bowler"]["name"])["lbwb"] += 1
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
    global WC_SERIES, ESPN_SERIES, SQUADS_JSON, GSHEET_TAB
    WC_SERIES = tour["cricapi_series"]
    ESPN_SERIES = tour.get("espn_series", "")
    SQUADS_JSON = tour.get("squads_path", "")
    GSHEET_TAB = tour["tab"]
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

    info = api("series_info", cache=False, id=WC_SERIES)
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
    print(f"{len(ended)}/{len(matches)} matches completed | cricsheet female matches indexed: {len(cs_idx)}", file=sys.stderr)

    cols = ["Match", "Date", "Team", "Full Name", "Role", "Played",
            "Runs", "Balls", "4s", "6s", "SR", "Dismissal",
            "Overs", "Maidens", "Dots", "Runs Conceded", "Wickets", "Econ",
            "Catches", "Stumpings", "Run Outs",
            "Pts Bat", "Pts Bowl", "Pts Field", "Pts SR", "Pts Econ", "Pts XI",
            "Fantasy Points", "Source", "In Squad List"]
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
            if espn_perf:
                n_espn += 1; dots_final = True
                status = "cricapi + ESPN dots/XI" + (" · super-over excl" if super_over else "")
            else:
                n_api += 1; dots_final = False
                status = "cricapi · limited (no dots/XI — ESPN unavailable)"

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
            if (short, name) in ambiguous:
                src += " · ⚠ name-match?"
            if (short, name) in xcheck:
                src += " · ⚠ differs vs ESPN"
            if d:
                # unknown-role leftovers: infer BOWL if they only bowled, so duck/SR isn't misapplied
                srole = role if role != "?" else ("BOWL" if d.get("balls", 0) > 0 and d.get("b", 0) == 0 else "BAT")
                s = score(d, srole)
                sr = round(d["r"] / d["b"] * 100, 1) if d["b"] else ""
                econ = round(d["runs_conceded"] / (d["balls"] / 6), 2) if d["balls"] else ""
                dots_out = d["dots"] if dots_final else ""  # never fill dots from a no-dots source
                rows.append([label, mdate, short, name, role, "Y",
                             d["r"], d["b"], d["4s"], d["6s"], sr, d["dismissal"],
                             round(d["balls"] / 6, 1) if d["balls"] else "",
                             d["maidens"], dots_out, d["runs_conceded"], d["w"], econ,
                             d["catches"], d["stumpings"], d["runouts"],
                             s["bat"], s["bowl"], s["field"], s["sr"], s["eco"], s["xi"],
                             s["total"], src, in_squad])
            else:
                rows.append([label, mdate, short, name, role, "N"] + [""] * 22 +
                            [src, in_squad])

        for short, name, role in team_players:
            emit(short, name, role, assigned.get((short, name)), "Y")
        # players who featured but matched no squad name -> show for manual review,
        # attributing their team (ESPN roster first, then the parsed team) so it's never a bare "?".
        for d in leftover.values():
            tfull = team_map.get(norm(d["name"])) or best_team(d["name"], team_map) or d.get("team", "")
            short = short_of(tfull) or "?"
            emit(short, d["name"], "?", d, "N")
    print(f"sources: {n_cs} cricsheet(official), {n_espn} cricapi+ESPN, {n_api} cricapi-only", file=sys.stderr)

    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows -> {out_csv}", file=sys.stderr)

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

def main():
    if not KEY:
        sys.exit("Set CRICKET_API_KEY env var.")
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

def write_to_gsheet(cols, rows):
    """Write cells directly into the Google Sheet tab via a service account.
    Reads creds JSON from env GOOGLE_SERVICE_ACCOUNT_JSON (CI secret)."""
    import gspread
    creds = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not creds:
        print("GSHEET_ID set but GOOGLE_SERVICE_ACCOUNT_JSON missing — skipping sheet write.", file=sys.stderr)
        return
    # Note: a wipe from a failed upstream fetch is prevented earlier (run_tour aborts before
    # we get here). Reaching here with 0 rows means the series is valid but has no completed
    # T20Is yet -> we DO write a clean header (e.g. clears stale rows from a format mis-tag).
    gc = gspread.service_account_from_dict(json.loads(creds))
    sh = gc.open_by_key(GSHEET_ID)
    try:
        ws = sh.worksheet(GSHEET_TAB)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=GSHEET_TAB, rows=len(rows) + 10, cols=len(cols) + 2)
    ws.clear()
    ws.update(range_name="A1", values=[cols] + rows, value_input_option="RAW")
    print(f"Wrote {len(rows)} rows to Google Sheet tab '{GSHEET_TAB}'.", file=sys.stderr)

if __name__ == "__main__":
    main()
