#!/usr/bin/env python3
"""
Women's T20 WC 2026 -> Dream11 fantasy-points sheet (CSV).

Pulls every COMPLETED WC match scorecard from cricketdata.org, computes D11 T20
points mirroring src/lib/fantasy-points/{rules,calculator}.ts EXACTLY, and writes
one flat CSV (Match column) listing ALL squad players of both playing teams, with
raw stats + a points breakdown for manual review. DNP players get blank stats.

Caveats (data the API scorecard does NOT provide -> set to 0):
  - bowling dot-balls (D11 +1/dot)         -> not available from scorecard feed
  - direct vs assisted run-outs            -> all run-outs scored as assisted (6)
Everything else (runs/4s/6s/milestones/SR, wickets/lbw-bowled/maidens/hauls/econ,
catches/stumpings, +4 in-XI) matches the app's calculator.

Usage:
  CRICKET_API_KEY=<key> python3 data/wc_fps_to_csv.py [out.csv]
"""
import os, sys, json, re, csv, time, glob, unicodedata, urllib.request
from difflib import SequenceMatcher

API = "https://api.cricapi.com/v1"
KEY = os.environ.get("CRICKET_API_KEY", "").strip()
WC_SERIES = "f3e5c7dd-332c-4893-9067-aa2bfe6d2b85"  # ICC Women's T20 World Cup 2026
SQUAD_TS = os.path.join(os.path.dirname(__file__), "..", "src", "lib", "squads", "womens-t20-wc-2026.ts")
SQUADS_JSON = os.path.join(os.path.dirname(__file__), "squads.json")  # standalone fallback
CACHE = os.environ.get("WC_CACHE_DIR", "/tmp/wc_api_cache")
CRICSHEET_DIR = os.environ.get("CRICSHEET_DIR", "/tmp/t20scan")  # extracted cricsheet JSONs
OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/wc_fantasy_points.csv"
# Google Sheet write target (CI). If both set, also writes cells into the tab.
GSHEET_ID = os.environ.get("GSHEET_ID", "").strip()
GSHEET_TAB = os.environ.get("GSHEET_TAB", "WWC T20 POINTS").strip()

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
    for short, name, role in team_players:
        nn = norm(name)
        nt = nn.split(); ln, fi = nt[-1], nt[0][0]
        for pk in pool:
            ak = ALIAS.get(pk, pk)
            pt = ak.split(); pl, pf = pt[-1], pt[0][0]
            if ak == nn:
                sc = 100.0
            elif pl == ln and pf == fi:
                sc = 92.0
            else:
                sc = SequenceMatcher(None, nn, ak).ratio() * 100
                if pl == ln: sc += 8
                if pt[0] == nt[0]: sc += 6
            pairs.append((sc, short, name, pk))
    pairs.sort(key=lambda x: -x[0])
    assigned, used_sq, used_pk = {}, set(), set()
    for sc, short, name, pk in pairs:
        if sc < 84 or (short, name) in used_sq or pk in used_pk:
            continue
        assigned[(short, name)] = pool[pk]
        used_sq.add((short, name)); used_pk.add(pk)
    leftover = {k: v for k, v in pool.items() if k not in used_pk}
    return assigned, leftover

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
    if os.path.exists(SQUADS_JSON):
        raw = json.load(open(SQUADS_JSON))
        return {k: {"name": v["name"], "players": [tuple(p) for p in v["players"]]}
                for k, v in raw.items()}
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
    return dict(name=name, r=0, b=0, **{"4s": 0, "6s": 0}, dismissed=False,
                dismissal="", balls=0, runs_conceded=0, w=0, lbwb=0, dots=0,
                maidens=0, catches=0, stumpings=0, runouts=0, dro=0, played=False)

def team_key(teams):
    """Date-independent team identity: normalized names with 'women' dropped."""
    return frozenset(norm(t.replace("Women", "").replace("women", "")) for t in teams)

# ---- cricsheet ball-by-ball (mirror of etl_cricsheet.py) -> EXACT dots/maidens/XI ----
def load_cricsheet_index(dirpath):
    """(date, team_key) -> json path, for completed female T20s."""
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
        if info.get("gender") != "female" or not info.get("dates"):
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
    for plist in info.get("players", {}).values():   # known playing XI -> +4 each
        for n in plist:
            get(n)["played"] = True
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
        if p["r"] >= 100: bat += R["m100"]
        elif p["r"] >= 75: bat += R["m75"] + R["m50"] + R["m25"]
        elif p["r"] >= 50: bat += R["m50"] + R["m25"]
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
    for inn in d.get("data", {}).get("scorecard", []):
        for bt in inn.get("batting", []):
            pl = get(bt["batsman"]["name"]); pl["played"] = True
            pl["r"] += bt.get("r", 0) or 0; pl["b"] += bt.get("b", 0) or 0
            pl["4s"] += bt.get("4s", 0) or 0; pl["6s"] += bt.get("6s", 0) or 0
            dis = (bt.get("dismissal") or "").lower()
            dtext = (bt.get("dismissal-text") or "")
            if dtext and "not out" not in dtext.lower() and dtext.lower() != "not out":
                pl["dismissed"] = True; pl["dismissal"] = dtext
            # credit lbw/bowled bonus to the bowler
            if ("bowled" in dis or "lbw" in dis) and bt.get("bowler"):
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
                        fp = get(fn); fp["played"] = True
                        fp["runouts"] += 1
                        if direct:
                            fp["dro"] += 1
        for bw in inn.get("bowling", []):
            pl = get(bw["bowler"]["name"]); pl["played"] = True
            pl["balls"] += overs_to_balls(bw.get("o", 0))
            pl["runs_conceded"] += bw.get("r", 0) or 0
            pl["w"] += bw.get("w", 0) or 0
            pl["maidens"] += bw.get("m", 0) or 0
        for ct in inn.get("catching", []):
            if not ct.get("catcher", {}).get("name"):
                continue
            pl = get(ct["catcher"]["name"]); pl["played"] = True
            pl["catches"] += ct.get("catch", 0) or 0
            pl["stumpings"] += ct.get("stumped", 0) or 0
            # run-outs come from dismissal-text parsing (direct vs assisted), not here
    return perf

def main():
    if not KEY:
        sys.exit("Set CRICKET_API_KEY env var.")
    squads = load_squads()
    # map normalized full team name -> short code
    name2short = {norm(v["name"]): k for k, v in squads.items()}

    info = api("series_info", cache=False, id=WC_SERIES)
    matches = info.get("data", {}).get("matchList", [])
    ended = [m for m in matches if m.get("matchEnded")]
    cs_idx = load_cricsheet_index(CRICSHEET_DIR)
    print(f"{len(ended)}/{len(matches)} matches completed | cricsheet female matches indexed: {len(cs_idx)}", file=sys.stderr)

    cols = ["Match", "Date", "Team", "Full Name", "Role", "Played",
            "Runs", "Balls", "4s", "6s", "SR", "Dismissal",
            "Overs", "Maidens", "Dots", "Runs Conceded", "Wickets", "Econ",
            "Catches", "Stumpings", "Run Outs",
            "Pts Bat", "Pts Bowl", "Pts Field", "Pts SR", "Pts Econ", "Pts XI",
            "Fantasy Points", "Source", "In Squad List"]
    rows = []
    n_cs = n_api = 0
    for mi, m in enumerate(sorted(ended, key=lambda x: x.get("dateTimeGMT", x.get("date", ""))), 1):
        teams = m.get("teams", [])
        date = m.get("date", "")
        label = f"Match {mi} — " + " v ".join(name2short.get(norm(t), t) for t in teams)

        # Pull BOTH sources. cricsheet = authoritative (exact dots, ball-by-ball).
        # cricapi = live coverage. They are cross-checked, never blindly substituted.
        cs_path = cs_idx.get((date, team_key(teams)))
        cs_perf = {k: v for k, v in parse_cricsheet(cs_path)[0].items() if v["played"]} if cs_path else {}
        try:
            api_perf = {k: v for k, v in parse_match(m["id"]).items() if v["played"]} if m.get("id") else {}
        except Exception:
            api_perf = {}

        if cs_perf:
            perf = cs_perf; n_cs += 1; dots_final = True
            if api_perf:
                diffs = crosscheck(cs_perf, api_perf)
                source = "cricsheet ✓xchecked" if not diffs else f"cricsheet ({len(diffs)} stat diffs vs cricapi)"
                if diffs:
                    print(f"  [{label}] {len(diffs)} cross-check diffs:", file=sys.stderr)
                    for nm, fld, cv, av in diffs[:8]:
                        print(f"      {nm}: {fld} cricsheet={cv} cricapi={av}", file=sys.stderr)
            else:
                source = "cricsheet"
        else:
            perf = api_perf; n_api += 1; dots_final = False
            source = "cricapi (dots pending — awaiting cricsheet)"

        team_players = []
        for tname in teams:
            short = name2short.get(norm(tname))
            if short:
                team_players += [(short, n, r) for n, r in squads[short]["players"]]
        assigned, leftover = match_squad_to_perf(team_players, perf)

        def emit(short, name, role, d, in_squad):
            if d:
                s = score(d, role)
                sr = round(d["r"] / d["b"] * 100, 1) if d["b"] else ""
                econ = round(d["runs_conceded"] / (d["balls"] / 6), 2) if d["balls"] else ""
                dots_out = d["dots"] if dots_final else ""  # never fill dots from cricapi
                rows.append([label, date, short, name, role, "Y",
                             d["r"], d["b"], d["4s"], d["6s"], sr, d["dismissal"],
                             round(d["balls"] / 6, 1) if d["balls"] else "",
                             d["maidens"], dots_out, d["runs_conceded"], d["w"], econ,
                             d["catches"], d["stumpings"], d["runouts"],
                             s["bat"], s["bowl"], s["field"], s["sr"], s["eco"], s["xi"],
                             s["total"], source, in_squad])
            else:
                rows.append([label, date, short, name, role, "N"] + [""] * 22 +
                            [source, in_squad])

        for short, name, role in team_players:
            emit(short, name, role, assigned.get((short, name)), "Y")
        # players who featured but matched no squad name -> show for manual review
        for d in leftover.values():
            emit("?", d["name"], "?", d, "N")
    print(f"sources: {n_cs} cricsheet, {n_api} cricapi", file=sys.stderr)

    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows -> {OUT} | sources: {n_cs} cricsheet, {n_api} cricapi", file=sys.stderr)

    if GSHEET_ID:
        write_to_gsheet(cols, rows)

def write_to_gsheet(cols, rows):
    """Write cells directly into the Google Sheet tab via a service account.
    Reads creds JSON from env GOOGLE_SERVICE_ACCOUNT_JSON (CI secret)."""
    import gspread
    creds = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not creds:
        print("GSHEET_ID set but GOOGLE_SERVICE_ACCOUNT_JSON missing — skipping sheet write.", file=sys.stderr)
        return
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
