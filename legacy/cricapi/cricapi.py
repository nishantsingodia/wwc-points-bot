"""cricapi — ARCHIVED 14 Aug 2026. Not imported by the live scorer.

WHY IT WAS RETIRED. cricapi was the L1 second witness: the feed ESPN's numbers were checked
against. It cross-checked FOUR fields and supplied neither `dots` nor `maidens`, so those two —
both of which score points — had no second opinion at all. Cricbuzz replaced it on 13-14 Aug and
cross-checks FOURTEEN, including dots, maidens, dismissal type and fielder attribution. Measured on
the two matches where both were compared against cricsheet: Cricbuzz was byte-exact on 1098/1098
field comparisons; cricapi's own historical record against cricsheet was 33/56 (59%) on disputed
fields. It also cost a 100/day quota that was the binding constraint on how often the bot could run.

WHAT STILL DEPENDS ON IT. Eight ENDED tours were settled with cricapi as their witness and their
recon_overrides rows say so:
  Women's T20 WC 2026 · Australia tour of Bangladesh · Major League Cricket 2026 ·
  India tour of Ireland · India tour of England · Ireland vs West Indies Women's ODI ·
  New Zealand vs West Indies Men's ODI · India tour of Zimbabwe
They are dormant (is_active() skips a tour past its `ends`), so nothing polls them — but RE-SCORING
one would need this module back. That is the entire reason this directory exists rather than a
delete.

HOW TO REVIVE. wc_fps_to_csv.at-removal.py in this directory is the complete scorer as it stood at
removal — diff against it to see exactly what came out. The functions below are lifted verbatim so
they can be re-imported without unpicking that diff.

⚠ If you do revive it, remember what the ledger now records: `witness` names the FEED a human
approved, because "S1" is a SLOT, not a source. An S1 row approved in the cricapi era means cricapi;
the same slot today means Cricbuzz. Do not let a revival silently reinterpret 935 approvals.
"""
import os, sys, json, time, urllib.request, urllib.parse

API = "https://api.cricapi.com/v1"
API_KEYS = [k.strip() for k in (
    os.environ.get("CRICKET_API_KEY", "").split(",") + [os.environ.get("CRICKET_API_KEY2", "")]
) if k.strip()]



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
