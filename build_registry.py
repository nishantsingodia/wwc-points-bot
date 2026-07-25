#!/usr/bin/env python3
"""
build_registry.py — build/extend the GLOBAL canonical Player Registry.

Player identity is GLOBAL and PERMANENT, not per-tour. Danni Wyatt-Hodge is the same
person (cricsheet_id a139c379, ESPN 254168, the same set of spellings) in every
tournament she ever plays — only her squad/team changes per tour. So this builds:

  • registry/players.json  — ONE global file, keyed on the stable cricsheet_id (pid),
    mapping each player -> {display, all feed spellings (aliases), espn_id, ...}.
    It ACCUMULATES across every tour and every run. Once a player is in it, future
    tours resolve her instantly with zero rework; a brand-new spelling is logged once
    and added to her one global entry, helping all future tours.

  • registry/tours/<slug>.json — thin per-tour membership: team -> [pid]. (Squads DO
    change per tour; identity does not.)

  • registry/UNMAPPED_<slug>.txt — squad players whose cricsheet_id couldn't be
    auto-resolved (a one-time human eyeball; add a bridge alias, then re-run).

Identity is seeded from (a) every hand-built alias map already in the three repos
(so the hard cases like Chamari Athapaththu = "AC Jayangani" are mapped from day one),
(b) the auction DB (= cricsheet's people registry: cricsheet_id + initials spellings),
(c) ESPN rosters (espn_id + fullName/displayName), (d) cached cricapi scorecards.

Run:  python3 build_registry.py [tour-name-substring]      (default: all tours)
Idempotent + additive: re-running only ADDS new identities / spellings, never drops.
"""
import os, sys, json, re, glob, sqlite3, urllib.request, time, unicodedata
from difflib import SequenceMatcher
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
AUCTION_DB = os.environ.get("AUCTION_DB",
    "/Users/nishant-singodia/cricket-auction-helper/db/cricket-auction.db")
DRAFT_RAW = os.environ.get("DRAFT_RAW", "/Users/nishant-singodia/wwc-draft/data/players-raw.json")
AUCTION_SRC = os.environ.get("AUCTION_SRC", "/Users/nishant-singodia/cricket-auction-helper/src/lib/squads")
DRAFT_LIB = os.environ.get("DRAFT_LIB", "/Users/nishant-singodia/wwc-draft/lib")
CACHE = os.environ.get("WC_CACHE_DIR", "/tmp/wc_api_cache")
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/cricket"
REG_DIR = os.path.join(HERE, "registry")
GLOBAL_PATH = os.path.join(REG_DIR, "players.json")
THRESH = 84.0

def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s.lower())).strip()

# ---- name matching (mirrors the points-bot + adds an explicit cricsheet-initials rule) ----
def is_initials_block(tok):
    return len(tok) >= 2 and tok.isalpha() and tok == tok  # leading "DN"/"AC"/"HML" block

def cricsheet_match(squad, dbname):
    """True if `dbname` is a cricsheet-style initials name of `squad`
    (e.g. squad 'Danni Wyatt-Hodge' <-> db 'DN Wyatt')."""
    s, d = norm(squad).split(), norm(dbname).split()
    if not s or len(d) < 2:
        return False
    s_initial = s[0][0]
    s_surnames = set(s[1:]) or {s[0]}
    d_surname = d[-1]
    d_initials = d[0]
    return (d_surname in s_surnames and len(d_surname) >= 4 and d_initials[0] == s_initial)

def score_pair(a, b):
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 100.0
    if cricsheet_match(a, b) or cricsheet_match(b, a):
        return 93.0
    ta, tb = na.split(), nb.split()
    la, fa = ta[-1], ta[0][0]
    lb, fb = tb[-1], tb[0][0]
    if set(ta).issubset(set(tb)) or set(tb).issubset(set(ta)):
        return 95.0
    if la == lb and fa == fb:
        return 92.0
    if la == lb and max((SequenceMatcher(None, x, y).ratio() for x in ta for y in tb),
                        default=0) >= 0.85:
        return 90.0
    sc = SequenceMatcher(None, na, nb).ratio() * 100
    if la == lb: sc += 8
    if ta[0] == tb[0]: sc += 6
    return sc

def given_compatible(squad, cand):
    """Guard against MERGING two different people who merely share a surname (the bug that
    collapsed Tajinder Singh+Kunwarjeet Singh, Shorna+Sharmin Akter, etc.). A fuzzy DB/ESPN
    match is only allowed when the GIVEN names are plausibly the same person:
      • cricsheet initials-style name (handled by cricsheet_match: surname + first initial), OR
      • given names equal, or one is a prefix/initial of the other.
    Different FULL given names sharing a surname (kunwarjeet vs tajinder) are rejected."""
    if cricsheet_match(squad, cand) or cricsheet_match(cand, squad):
        return True
    ta, tb = norm(squad).split(), norm(cand).split()
    if not ta or not tb:
        return False
    ga, gb = ta[0], tb[0]
    if ga == gb:
        return True
    if len(ga) <= 2 or len(gb) <= 2:          # an initial form ('S Luus' vs 'Sune Luus')
        return True
    return ga.startswith(gb) or gb.startswith(ga)   # danni/daniel-style abbreviations

def best_match(name, candidates, key=lambda x: x):
    best, best_sc, runner = None, 0.0, 0.0
    for c in candidates:
        sc = score_pair(name, key(c))
        if sc > best_sc:
            runner = best_sc; best, best_sc = c, sc
        elif sc > runner:
            runner = sc
    return best, best_sc, runner

# ---- seed: import every hand-built alias map already in the repos ----
def read_ts_alias_map(path, const_name):
    """Extract "key": "value" pairs from a `const <const_name>: Record<...> = { ... }`
    block in a TS file. Returns {key: value}."""
    out = {}
    try:
        txt = open(path).read()
    except Exception:
        return out
    m = re.search(const_name + r"[^=]*=\s*\{(.*?)\n\}", txt, re.S)
    if not m:
        return out
    for k, v in re.findall(r'"([^"]+)"\s*:\s*"([^"]+)"', m.group(1)):
        out[k] = v
    return out

def load_bridges():
    """announced-name(norm) -> cricsheet/DB spelling, gathered from all repos.
    These bridge genuinely-unrelated names (Athapaththu='AC Jayangani') to the DB."""
    b = {}
    b.update(read_ts_alias_map(os.path.join(AUCTION_SRC, "build-womens-pool.ts"), "NAME_ALIASES"))
    b.update(read_ts_alias_map(os.path.join(AUCTION_SRC, "mlc-2026.ts"), "MLC_NAME_ALIASES"))
    # LPL: announced->cricsheet bridges (like MLC). LPL_NAME_ALIASES is explicit (wins); LPL_DISPLAY_NAMES
    # is the full cricsheet->announced display map, inverted here so every announced LPL name bridges to
    # its cricsheet DB spelling (BKG Mendis, AM Fernando, ...) — this is what anchors LPL to cricsheet_ids.
    b.update(read_ts_alias_map(os.path.join(AUCTION_SRC, "lpl-2026.ts"), "LPL_NAME_ALIASES"))
    for cs_name, announced in read_ts_alias_map(os.path.join(AUCTION_SRC, "lpl-2026.ts"), "LPL_DISPLAY_NAMES").items():
        b.setdefault(norm(announced), cs_name)
    # points-bot's own ALIAS (feed-spelling -> squad spelling); reverse not needed here
    # draft DISPLAY_NAME_MAP is cricsheet-name -> announced; invert so announced->cricsheet
    dmap = read_ts_alias_map(os.path.join(DRAFT_LIB, "players.ts"), "DISPLAY_NAME_MAP")
    for cs_name, announced in dmap.items():
        b.setdefault(norm(announced), cs_name)
    return {norm(k): v for k, v in b.items()}

# ---- ESPN harvest (free, unlimited) ----
def espn_get(series, path, **params):
    os.makedirs(CACHE, exist_ok=True)
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    key = re.sub(r"[^a-z0-9]", "_", f"espn_{series}_{path}_{qs}".lower())
    fp = os.path.join(CACHE, key + ".json")
    if os.path.exists(fp):
        try: return json.load(open(fp))
        except Exception: pass
    url = f"{ESPN_BASE}/{series}/{path}?{qs}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except Exception:
        return {}
    json.dump(data, open(fp, "w")); time.sleep(0.2)
    return data

def team_key(teams):
    return frozenset(norm(re.sub(r"(?i)\bwomen\b", "", t)) for t in teams)

def espn_harvest(series, squad_teams_full, dates):
    want = team_key(squad_teams_full); seen, ath = set(), []
    for d in dates:
        for e in espn_get(series, "scoreboard", dates=d.replace("-", "")).get("events", []):
            eid = e.get("id")
            if not eid or eid in seen: continue
            comps = e.get("competitions", [{}])[0].get("competitors", [])
            tn = [c.get("team", {}).get("displayName", "") for c in comps]
            if not (team_key(tn) & want): continue
            seen.add(eid)
            for team in espn_get(series, "summary", event=eid).get("rosters", []):
                tname = team.get("team", {}).get("displayName", "")
                for p in team.get("roster", []):
                    a = p.get("athlete", {})
                    fn, dn = a.get("fullName"), a.get("displayName")
                    if fn or dn:
                        ath.append({"name": fn or dn, "display": dn or fn,
                                    "espn_id": str(a.get("id")) if a.get("id") else None, "team": tname})
    return ath

def cricapi_cached_names():
    names = []
    for f in glob.glob(os.path.join(CACHE, "match_scorecard_id_*.json")):
        try: d = json.load(open(f))
        except Exception: continue
        for inn in d.get("data", {}).get("scorecard", []):
            for b in inn.get("batting", []) + inn.get("bowling", []):
                n = (b.get("batsman") or b.get("bowler") or {}).get("name")
                if n: names.append(n)
    return names

POOL_EXPORT = os.path.join(REG_DIR, "auction_players.json.gz")

def open_pool_con():
    """A sqlite connection exposing the auction `players` table (id, cricsheet_id, name,
    full_name, country, gender) — the ONLY thing db_pool needs from the auction DB.

    Prefers the live 61MB auction DB when present + usable (fresh, local dev). In CI that DB
    is gitignored/absent (the auction repo tracks only db/.gitkeep), so sqlite would open an
    empty file and `select ... from players` would crash — which is why cricapi auto-tours
    used to fail to anchor in CI. We fall back to the committed export
    registry/auction_players.json.gz (0.2MB gzipped; regenerate with
    registry/export_players_pool.py) loaded into an in-memory DB. So cricapi auto-tours anchor
    in CI WITHOUT shipping the 61MB DB to a public repo — db_pool stays byte-for-byte unchanged."""
    if os.path.exists(AUCTION_DB):
        try:
            con = sqlite3.connect(AUCTION_DB); con.row_factory = sqlite3.Row
            con.execute("select id, cricsheet_id, name, full_name, country, gender "
                        "from players limit 1").fetchone()
            print(f"players pool: live auction DB ({AUCTION_DB})", file=sys.stderr)
            return con
        except Exception as e:
            print(f"  ⚠ auction DB present but unusable ({e}); using committed export", file=sys.stderr)
    if not os.path.exists(POOL_EXPORT):
        raise SystemExit(
            f"no usable auction DB and no committed export at {POOL_EXPORT}.\n"
            f"Run (with the auction DB available): python3 registry/export_players_pool.py, "
            f"then commit registry/auction_players.json.gz.")
    import gzip
    with gzip.open(POOL_EXPORT, "rt", encoding="utf-8") as f:
        rows = json.load(f)
    con = sqlite3.connect(":memory:"); con.row_factory = sqlite3.Row
    con.execute("create table players (id, cricsheet_id, name, full_name, country, gender)")
    con.executemany(
        "insert into players (id, cricsheet_id, name, full_name, country, gender) values (?,?,?,?,?,?)",
        [(r.get("id"), r.get("cricsheet_id"), r.get("name"), r.get("full_name"),
          r.get("country"), r.get("gender")) for r in rows])
    con.commit()
    print(f"players pool: committed export {POOL_EXPORT} ({len(rows)} players; no live DB — CI mode)",
          file=sys.stderr)
    return con

def db_pool(con, gender):
    rows = [dict(r) for r in con.execute(
        "select id, cricsheet_id, name, full_name, country, gender from players")]
    if gender in ("female", "male"):
        rows = [r for r in rows if (r.get("gender") in (gender, None, ""))]
    return rows

def date_range(start, end):
    a, b = date.fromisoformat(start), date.fromisoformat(end); out = []
    while a <= b:
        out.append(a.isoformat()); a += timedelta(days=1)
    return out

def infer_start(tour):
    sid = tour.get("cricapi_series", "")
    fp = os.path.join(CACHE, "series_info_id_" + re.sub(r"[^a-z0-9]", "_", sid.lower()) + ".json")
    if os.path.exists(fp):
        try:
            ds = [m.get("date") for m in json.load(open(fp)).get("data", {}).get("matchList", []) if m.get("date")]
            if ds: return min(ds)
        except Exception: pass
    return (date.fromisoformat(tour["ends"]) - timedelta(days=45)).isoformat()

# ---- cricinfo-id crosswalk + manual bridges (the identity spine) ----
# The pid is now ci:<cricinfoId> (the ESPNcricinfo id = cricsheet people.csv key_cricinfo:
# UNIQUE 18253/18253, invariant, offline-verifiable). cricsheet_id is DERIVED from the
# cricinfo id via the crosswalk, so cs<->ci always point to the SAME person by construction.
CROSSWALK_PATH = os.path.join(REG_DIR, "crosswalk.json")
CI_BRIDGES_PATH = os.path.join(REG_DIR, "manual_ci_bridges.json")
CS2CI, CI_ALT, CI2CS, ALT_OF, PRIMARY, NAME2CI = {}, {}, {}, {}, set(), {}

def load_crosswalk():
    """registry/crosswalk.json (pure fn of people.csv): cricsheet_id<->cricinfo_id + alternates."""
    global CS2CI, CI_ALT, CI2CS, ALT_OF, PRIMARY
    if not os.path.exists(CROSSWALK_PATH):
        print("  ⚠ crosswalk.json missing — regenerate from people.csv first", file=sys.stderr); return
    cx = json.load(open(CROSSWALK_PATH))
    CS2CI = cx.get("cs2ci", {}); CI_ALT = cx.get("ci_alt", {})
    CI2CS = {ci: cs for cs, ci in CS2CI.items()}          # cricinfo -> cricsheet (reverse; unique)
    PRIMARY = set(CS2CI.values())
    ALT_OF = {}
    for alt, prim in CI_ALT.items(): ALT_OF.setdefault(prim, []).append(alt)

def load_ci_bridges():
    """registry/manual_ci_bridges.json — human-verified announced-name(norm) -> cricinfo id.
    Authoritative; the ONLY sanctioned way to assert an identity fuzzy can't (or must not guess)."""
    global NAME2CI
    if not os.path.exists(CI_BRIDGES_PATH): return
    for _pid, b in json.load(open(CI_BRIDGES_PATH)).items():
        ci = b.get("cricinfo_id")
        for nm in b.get("names", []):
            if nm and ci: NAME2CI[nm] = str(ci)

def fold_ci(x):
    """any cricinfo id (primary OR alternate _2/_3) -> the PRIMARY cricinfo id, else None."""
    x = str(x) if x else ""
    return x if x in PRIMARY else CI_ALT.get(x)

def _initials_ok(a, b):
    """given-name gate for the FUZZY path only: cricsheet-initials match (handles 'Ash'/'A Gardner')
    OR first-name first-letter equal. REJECTS 'Jo' vs 'A Gardner'. Id-authoritative paths (bridge/
    exact/ESPN-id) skip this — they don't guess the id from the name."""
    if cricsheet_match(a, b) or cricsheet_match(b, a): return True
    ta, tb = norm(a).split(), norm(b).split()
    return bool(ta and tb and ta[0][0] == tb[0][0])

def resolve_ci(sname, pool_scoped, full_pool, espn_ath, ts_bridges):
    """Deterministic announced-name -> PRIMARY cricinfo id, or (None, None) if unresolved.
    NEVER guesses on ambiguity — an ambiguous fuzzy match returns None => the player goes to
    HOLD/review, not a fabricated pid (the Jo-Gardner-into-Ashleigh class becomes impossible)."""
    ns = norm(sname)
    # a) manual human-verified bridge (authoritative)
    if ns in NAME2CI:
        ci = fold_ci(NAME2CI[ns]) or NAME2CI[ns]
        return ci, "bridge"
    # b) legacy TS bridge (announced -> cricsheet DB spelling) -> cricsheet_id -> ci
    br = ts_bridges.get(ns)
    if br:
        row = next((r for r in full_pool if norm(r.get("name","")) == norm(br) and r.get("cricsheet_id")), None)
        if row and CS2CI.get(row["cricsheet_id"]): return CS2CI[row["cricsheet_id"]], "ts-bridge"
    # c) EXACT cricsheet-register name (country/gender-scoped), unique -> ci
    exact = {CS2CI.get(r["cricsheet_id"]) for r in pool_scoped
             if norm(r.get("name","")) == ns and r.get("cricsheet_id") and CS2CI.get(r["cricsheet_id"])}
    if len(exact) == 1: return next(iter(exact)), "exact"
    # d) FUZZY DB, NULL ON AMBIGUITY (confident gap + given-compatible + initials-ok + unique person)
    dbm, sc, runner = best_match(sname, pool_scoped, key=lambda r: r.get("name",""))
    if (dbm and sc >= THRESH and (sc - runner) >= 4 and given_compatible(sname, dbm["name"])
            and _initials_ok(sname, dbm["name"]) and dbm.get("cricsheet_id")):
        ci = CS2CI.get(dbm["cricsheet_id"])
        if ci: return ci, "fuzzy"
    # e) ESPN roster athlete.id — IS the cricinfo id (id-authoritative; name only LOCATES the athlete)
    em, esc, _ = best_match(sname, espn_ath, key=lambda a: a["name"])
    if em and esc >= THRESH and given_compatible(sname, em["name"]):
        ci = fold_ci(em.get("espn_id"))
        if ci: return ci, "espn"
    return None, None

# ---- global registry helpers ----
def load_global():
    if os.path.exists(GLOBAL_PATH):
        try: return json.load(open(GLOBAL_PATH)).get("players", {})
        except Exception: pass
    return {}

def build_index(players):
    """alias-norm -> pid, cricinfo_id -> pid, cricsheet_id -> pid (for cross-tour reuse).
    Also folds every manual_ci_bridge spelling onto its ci: pid so a known name always reuses."""
    by_alias, by_ci, by_cs = {}, {}, {}
    for pid, e in players.items():
        for a in e.get("aliases", []):
            by_alias.setdefault(a, pid)
        if e.get("cricinfo_id"): by_ci.setdefault(str(e["cricinfo_id"]), pid)
        if e.get("cricsheet_id"): by_cs.setdefault(e["cricsheet_id"], pid)
    for ns, ci in NAME2CI.items():
        pid = by_ci.get(str(ci))
        if pid: by_alias.setdefault(ns, pid)
    return by_alias, by_ci, by_cs

def squad_players(path):
    raw = json.load(open(path)); out = []
    for short, v in raw.items():
        for p in v["players"]:
            out.append((short, v["name"], p[0], p[1]))
    return out

def build_tour(tour, con, draft_players, ts_bridges, players, idx):
    by_alias, by_ci, by_cs = idx
    slug = re.sub(r"[^a-z0-9]+", "_", tour["tab"].lower()).strip("_")
    spath = os.path.join(HERE, tour["squads"]) if tour.get("squads") else None
    if not spath or not os.path.exists(spath):
        print(f"  -- {tour['name']}: no squad file, skip", file=sys.stderr); return []
    squad = squad_players(spath)
    teams_full = sorted({tf for _, tf, _, _ in squad})
    gender = tour.get("gender", "female")
    pool = db_pool(con, gender)
    # Scope DB matching by COUNTRY for international tours (kills surname ambiguity). Franchise
    # tours have non-country team names -> the scoped pool is tiny -> fall back to the full pool.
    def norm_country(s): return norm(re.sub(r"(?i)\bwomen\b", "", s or ""))
    pools_by_team = {}
    for tf in teams_full:
        cg = norm_country(tf)
        scoped = [r for r in pool if norm(r.get("country") or "") == cg]
        pools_by_team[tf] = scoped if len(scoped) >= 5 else pool
    espn_ath = espn_harvest(tour.get("espn_series", ""), teams_full,
                            date_range(infer_start(tour), tour["ends"])) if tour.get("espn_series") else []
    membership, unmapped, review, n_reused = {}, [], [], 0

    for short, tfull, sname, role in squad:
        ns = norm(sname)
        # 0) CROSS-TOUR REUSE: already known globally (zero rework, idempotent)
        pid = by_alias.get(ns)
        ci = players.get(pid, {}).get("cricinfo_id") if pid else None
        if pid: n_reused += 1
        how = "reuse" if ci else None
        # resolve a cricinfo id for a NEW player OR a reused entry still lacking one
        if not ci:
            ci, how = resolve_ci(sname, pools_by_team[tfull], pool, espn_ath, ts_bridges)
            if ci: ci = fold_ci(ci) or ci
            if not pid:
                pid = (by_ci.get(str(ci)) if ci else None) or (f"ci:{ci}" if ci else f"uncapped:{ns.replace(' ','-')}")
        cs_id = CI2CS.get(str(ci)) if ci else None        # DERIVE cricsheet_id from cricinfo id

        # draft display + id (guarded so a wrong same-surname match can't contribute a name)
        dpool = [p for p in draft_players if p.get("team_code") == short]
        dm, dsc, _ = best_match(sname, dpool or draft_players, key=lambda p: p.get("name", ""))
        dm_ok = bool(dm and dsc >= THRESH and given_compatible(sname, dm.get("name", "")))
        draft_id = dm.get("id") if dm_ok else None
        display = (dm.get("name") if dm_ok else None) or sname

        e = players.get(pid, {"aliases": [], "tours": []})
        al = set(e.get("aliases", [])); al.add(ns); al.add(norm(display))
        em, esc, _ = best_match(sname, espn_ath, key=lambda a: a["name"])
        if em and esc >= THRESH and given_compatible(sname, em["name"]):
            al.add(norm(em["name"])); al.add(norm(em.get("display", "")))
        for nm in NAME2CI:                                # fold verified spellings of this ci
            if ci and NAME2CI[nm] == str(ci): al.add(nm)
        e["display"] = e.get("display") or display
        if ci:
            e["cricinfo_id"] = str(ci)
            e["cricsheet_id"] = cs_id                     # derived => cs<->ci same person
            e["espn_id"] = str(ci)
            if ALT_OF.get(str(ci)): e["cricinfo_alt"] = ALT_OF[str(ci)]
        e["draft_id"] = e.get("draft_id") or draft_id
        e["auction_id"] = e.get("auction_id")
        # INVARIANT: an alias belongs to exactly ONE pid — never re-claim another player's alias.
        e["aliases"] = sorted(a for a in al if a and by_alias.get(a, pid) == pid)
        if tour["name"] not in e["tours"]: e["tours"].append(tour["name"])
        players[pid] = e
        for a in e["aliases"]: by_alias.setdefault(a, pid)
        if ci: by_ci.setdefault(str(ci), pid)
        if cs_id: by_cs.setdefault(cs_id, pid)

        membership.setdefault(short, []).append(pid)
        if not ci:   # HOLD-for-review: no fabricated identity — surface for a human cricinfo id
            cand = best_match(sname, pools_by_team[tfull], key=lambda r: r.get("name",""))[0] or {}
            unmapped.append(f"{short:5} {sname:30} pid={pid}  (no cricinfo id -> needs review)")
            review.append({"player": sname, "team": short, "tour": tour["name"],
                           "current_pid": pid, "closest_guess": cand.get("name","")})

    os.makedirs(os.path.join(REG_DIR, "tours"), exist_ok=True)
    json.dump({"tour": tour["name"], "slug": slug, "teams": membership},
              open(os.path.join(REG_DIR, "tours", f"{slug}.json"), "w"), indent=1, ensure_ascii=False)
    open(os.path.join(REG_DIR, f"UNMAPPED_{slug}.txt"), "w").write(
        "\n".join(unmapped) + ("\n" if unmapped else ""))
    print(f"  {tour['name']}: {len(squad)} slots | reused {n_reused}"
          f" | NEEDS-REVIEW {len(review)} | ESPN harvested {len(espn_ath)}", file=sys.stderr)
    return review

def main():
    filt = sys.argv[1].lower() if len(sys.argv) > 1 else None
    tours = json.load(open(os.path.join(HERE, "tours.json")))
    con = open_pool_con()   # live auction DB locally; committed export in CI (see open_pool_con)
    draft_players = json.load(open(DRAFT_RAW))
    if isinstance(draft_players, dict): draft_players = draft_players.get("players", [])
    load_crosswalk(); load_ci_bridges()          # the cricinfo-id spine (people.csv + verified bridges)
    ts_bridges = load_bridges()
    players = load_global()
    idx = build_index(players)
    print(f"global registry: {len(players)} players | crosswalk {len(CS2CI)} | ci-bridges {len(NAME2CI)} | "
          f"ts-bridges {len(ts_bridges)} | draft roster {len(draft_players)}", file=sys.stderr)
    all_review = []
    for t in tours:
        if filt and filt not in t["name"].lower(): continue
        all_review += (build_tour(t, con, draft_players, ts_bridges, players, idx) or [])
    # Merge hand-curated aliases the auto-matcher can't link (reviewed once, permanent).
    by_alias = build_index(players)[0]
    mpath = os.path.join(REG_DIR, "manual_aliases.json")
    if os.path.exists(mpath):
        applied = 0
        for ent in json.load(open(mpath)).get("entries", []):
            pid = by_alias.get(norm(ent.get("match", "")))
            if not pid:
                print(f"  manual_alias: no player for match={ent.get('match')!r}", file=sys.stderr); continue
            al = set(players[pid].get("aliases", [])) | {norm(a) for a in ent.get("add", [])}
            players[pid]["aliases"] = sorted(a for a in al if a)
            applied += 1
        print(f"  manual_aliases merged: {applied}", file=sys.stderr)
    os.makedirs(REG_DIR, exist_ok=True)
    # unresolved players -> the "Needs Cricinfo ID" review feed (tour_sync_finalize pushes it to the GSheet tab)
    json.dump(all_review, open(os.path.join(REG_DIR, "needs_cricinfo_pending.json"), "w"),
              indent=1, ensure_ascii=False)
    json.dump({"anchor": "cricinfo_id (ci:); cs:/uncapped: fallback; cricsheet_id derived from crosswalk",
               "count": len(players), "players": players},
              open(GLOBAL_PATH, "w"), indent=1, ensure_ascii=False)
    print(f"GLOBAL registry written: {len(players)} players -> {GLOBAL_PATH} | needs-review {len(all_review)}",
          file=sys.stderr)

if __name__ == "__main__":
    main()
