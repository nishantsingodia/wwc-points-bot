#!/usr/bin/env python3
"""
tour_sync — auto-discover cricket tours starting soon and generate the draft-app +
points-bot artifacts for them, so a new tour appears in wwc-draft and gets scored by
the bot with NO manual code edits.

Pipeline (run daily from GH Actions):
  discover (cricapi /currentMatches, filtered by watchlist)
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
FMT_BUCKET = {"t20": "T20", "t20i": "T20", "odi": "ODI"}
DISCOVERY_WINDOW_DAYS = int(os.environ.get("SYNC_WINDOW_DAYS", "4"))

ROLE_MAP = {
    "wk-batsman": "WK", "wicketkeeper batter": "WK", "wicketkeeper": "WK", "wk": "WK",
    "batsman": "BAT", "batter": "BAT", "top order batter": "BAT",
    "batting allrounder": "AR", "bowling allrounder": "AR", "allrounder": "AR", "all rounder": "AR",
    "bowler": "BOWL",
}
ROLE_EFPPM = {"BAT": 45.0, "WK": 45.0, "AR": 50.0, "BOWL": 45.0}
ROLE_ORDER = {"WK": 0, "BAT": 1, "AR": 2, "BOWL": 3}
ORD = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th", 6: "6th", 7: "7th"}


# ── cricapi layer ────────────────────────────────────────────────────────────
def _key():
    k = os.environ.get("CRICKET_API_KEY", "")
    if not k:  # local convenience: borrow the auction app's key for dry-runs
        p = os.path.expanduser("~/cricket-auction-helper/.env.local")
        if os.path.exists(p):
            m = re.search(r"CRICKET_API_KEY=([^\n\"]+)", open(p).read())
            if m:
                k = m.group(1)
    return k.strip().split(",")[0].strip().strip('"')

def capi(path, **params):
    url = f"{API}/{path}?" + urllib.parse.urlencode({**params, "apikey": _key()})
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


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


# ── the generator: one (series, format) -> all artifacts ────────────────────────
def gen_tour(series_info, squads_by_matchid, fmt, gender, state):
    """series_info = cricapi series_info['data']; squads_by_matchid = {matchId: match_squad['data']}."""
    info = series_info["info"]
    ml = [m for m in series_info["matchList"] if FMT_BUCKET.get((m.get("matchType") or "").lower()) == fmt]
    ml.sort(key=lambda m: m.get("dateTimeGMT") or m.get("date"))
    if not ml:
        return None

    fmt_label = "ODI" if fmt == "ODI" else "T20I"
    # team shortnames from teamInfo (fall back to first-3 of name)
    ti = {t["name"]: t.get("shortname") or t["name"][:3].upper() for m in ml for t in m.get("teamInfo", [])}
    teams = ml[0]["teams"]
    shorts = {t: ti.get(t, t[:3].upper()) for t in teams}
    code = {t: mint_code(gender, fmt, shorts[t], state["codes"]) for t in teams}

    gl = "M" if gender == "male" else "W"
    tour_name = f"{info['name']} ({fmt_label})"
    tab = f"{shorts[teams[0]]} v {shorts[teams[1]]} {fmt_label} POINTS".upper()

    # ---- matches ----
    matches, toss = [], []
    for i, m in enumerate(ml, 1):
        t1, t2 = m["teams"]
        dt = m.get("dateTimeGMT")
        matches.append({
            "matchNum": state["next_match_num"],
            "key": f"AUTO_{gl}_{shorts[t1]}_{shorts[t2]}_{fmt_label}{i}_{mmm_dd(dt)}",
            "gender": gl,
            "team1": code[t1], "team2": code[t2],
            "label": f"{ORD.get(i, str(i)+'th')} {fmt_label}: {shorts[t1]} v {shorts[t2]}",
            "date": to_ist_iso(dt),
        })
        toss.append(to_utc_z(dt))
        state["next_match_num"] += 1

    # ---- squads (union of match_squad across this format's matches) ----
    # {teamName: {playerName: role}}
    roster = {t: {} for t in teams}
    for m in ml:
        sq = squads_by_matchid.get(m["id"])
        if not sq:
            continue
        for team in sq:
            tn = team.get("teamName")
            if tn not in roster:
                continue
            for p in team.get("players", []):
                roster[tn][p["name"]] = norm_role(p.get("role"))

    players, squads_json, team_codes = [], {}, {}
    for t in teams:
        c = code[t]
        team_codes[c] = {"flag": state["name_flag"].get(t.lower(), "🏏"), "name": t}
        squads_json[c] = {"name": t, "players": []}
        ordered = sorted(roster[t].items(), key=lambda kv: (ROLE_ORDER[kv[1]], kv[0]))
        for sn, (pname, role) in enumerate(ordered, 1):
            players.append({
                "id": state["next_pid_id"], "name": pname, "country": t, "role": role,
                "squad_number": sn, "team_code": c, "efppm": ROLE_EFPPM[role],
                "pid": resolve_pid(pname, state["reg_alias"]),
            })
            squads_json[c]["players"].append([pname, role])
            state["next_pid_id"] += 1

    ends = ml[-1].get("date") or info.get("enddate")
    squads_path = re.sub(r"[^a-z0-9]+", "_", tour_name.lower()).strip("_") + "_squads.json"
    tours_entry = {
        "cricapi_series": info["id"], "ends": ends, "espn_series": "",  # date-based lineup lookup
        "gender": gender, "name": tour_name, "squads": squads_path, "tab": tab,
    }
    return {
        "tour_name": tour_name, "codes": code,
        "matches": matches, "players": players, "team_codes": team_codes,
        "tours_entry": tours_entry, "squads_json": squads_json,
        "squads_path": squads_path, "toss_windows": toss,
    }


# ── discovery ───────────────────────────────────────────────────────────────────
def discover(window_days):
    """Return {series_id: {name, teams, genders}} for in-scope series with a match in the window."""
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=window_days)
    cm = capi("currentMatches", offset=0).get("data", [])
    hits = {}
    for m in cm:
        dt = m.get("dateTimeGMT")
        if not dt:
            continue
        try:
            when = datetime.fromisoformat(dt.replace("Z", "")).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if not (now - timedelta(days=2) <= when <= horizon):
            continue
        ok, _ = in_scope(m.get("name", ""), m.get("teams", []))
        if not ok:
            continue
        sid = m.get("series_id")
        if sid:
            hits[sid] = {"name": m.get("name", ""), "teams": m.get("teams", [])}
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
        if sheet_id:
            tab = urllib.parse.quote(t["tours_entry"]["tab"])
            pt.append(f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={tab}&headers=1")
        applied.append(t["tour_name"])
    _dump(f"{DRAFT}/data/matches.json", dm)
    _dump(f"{DRAFT}/data/players-raw.json", dp)
    _dump(f"{DRAFT}/data/team-codes.json", dc)
    _dump(f"{DRAFT}/data/points-tabs.json", pt)
    _dump(f"{BOT}/tours.json", tj)
    _dump(f"{BOT}/toss_windows.json", sorted(set(tw)))
    return applied

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true", help="write artifacts into the draft + bot repo files")
    ap.add_argument("--from-saved", help="dir with capi_series_info.json + capi_match_squad.json (no quota)")
    ap.add_argument("--emit", help="write generated artifacts to this dir")
    args = ap.parse_args()
    state = load_state()

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
    else:
        found = discover(DISCOVERY_WINDOW_DAYS)
        print(f"discover: {len(found)} in-scope series in next {DISCOVERY_WINDOW_DAYS}d", file=sys.stderr)
        for sid, meta in found.items():
            if sid in state["existing_series"]:
                print(f"  skip (already ingested): {meta['name'][:50]}", file=sys.stderr)
                continue
            si = capi("series_info", id=sid).get("data")
            if not si:
                continue
            gender = infer_gender(si["info"]["name"], si["matchList"][0]["teams"])
            sq_by = {}
            for m in si["matchList"]:
                if FMT_BUCKET.get((m.get("matchType") or "").lower()) and m.get("hasSquad"):
                    r = capi("match_squad", id=m["id"])
                    if r.get("status") == "success":
                        sq_by[m["id"]] = r.get("data", [])
            for fmt in ("ODI", "T20"):
                t = gen_tour(si, sq_by, fmt, gender, state)
                if t and t["tours_entry"]["tab"] not in state["existing_tabs"]:
                    tours.append(t)

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
