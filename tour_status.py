#!/usr/bin/env python3
"""
tour_status — end-to-end, per-tour visibility across the WHOLE pipeline (bot + draft + sheet).

For every tour in tours.json it answers, in one place: is it registered, do squads exist, did the
draft get it (matches/players/team-codes/espn-series), is the points tab live, and — the bit that
silently breaks — do the SHEET's team tokens and Player IDs actually MAP to the draft's codes/pids.
Prints a table to stdout (always, so it shows in CI logs) and writes a "TOUR STATUS" GSheet tab when
creds are present. Read-only: no cricapi (sheet via public gviz), never mutates a repo.

Run:  DRAFT_REPO=~/wwc-draft python3 tour_status.py
Env:  DRAFT_REPO, GSHEET_ID (+ GOOGLE_SERVICE_ACCOUNT_JSON to write the tab), SHEET_ID override.
"""
import json, os, re, sys, urllib.request, urllib.parse

BOT = os.path.dirname(os.path.abspath(__file__))
DRAFT = os.environ.get("DRAFT_REPO", os.path.expanduser("~/wwc-draft"))


def _norm(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _load(p, default=None):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


def _sheet_id():
    if os.environ.get("SHEET_ID"):
        return os.environ["SHEET_ID"]
    for u in _load(f"{DRAFT}/data/points-tabs.json", []) or []:
        m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", u)
        if m:
            return m.group(1)
    return os.environ.get("GSHEET_ID", "")


def _team_code_aliases():
    """Parse TEAM_CODE_ALIASES out of the draft's lib/players.ts (draft code -> [sheet tokens])."""
    try:
        src = open(f"{DRAFT}/lib/players.ts", encoding="utf-8").read()
        block = re.search(r"TEAM_CODE_ALIASES[^=]*=\s*\{(.*?)\}", src, re.S).group(1)
        out = {}
        for code, arr in re.findall(r"(\w+)\s*:\s*\[([^\]]*)\]", block):
            out[code] = [x.strip().strip("\"'") for x in arr.split(",") if x.strip()]
        return out
    except Exception:
        return {}


def _read_tab(sheet_id, tab):
    """Read one points tab via public gviz CSV -> list of dict rows (or [] if unavailable)."""
    if not sheet_id:
        return []
    url = (f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv"
           f"&sheet={urllib.parse.quote(tab)}&headers=1")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        text = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    except Exception:
        return []
    import csv, io
    rows = list(csv.DictReader(io.StringIO(text)))
    # gviz returns the DEFAULT sheet (or an error page) for a non-existent tab name — guard against
    # that false-positive: a real points tab always carries these columns.
    cols = set(rows[0].keys()) if rows else set()
    if not ({"Full Name", "Player ID", "Fantasy Points"} & cols):
        return []
    return rows


def main():
    tours = _load(f"{BOT}/tours.json", []) or []
    dmatches = _load(f"{DRAFT}/data/matches.json", []) or []
    dplayers = _load(f"{DRAFT}/data/players-raw.json", []) or []
    dcodes = _load(f"{DRAFT}/data/team-codes.json", {}) or {}
    despn = _load(f"{DRAFT}/data/espn-series.json", {}) or {}
    dmirror = (_load(f"{DRAFT}/lib/registry-players.json", {}) or {}).get("players", {})
    control = _load(f"{DRAFT}/../tour_control.json")  # optional local cache; sheet is source of truth
    aliases = _team_code_aliases()
    sid = _sheet_id()

    team_names = {c: v.get("name", "") for c, v in dcodes.items()}
    roster_pids = {p.get("pid") for p in dplayers if p.get("pid")}
    roster_codes = {p.get("team_code") for p in dplayers}

    def token_to_code(token):
        """Does a sheet Team token resolve to a draft team code? (exact / full-name / alias)."""
        t = _norm(token)
        for c in dcodes:
            if t == _norm(c) or t == _norm(team_names.get(c, "")):
                return c
            if any(t == _norm(a) for a in aliases.get(c, [])):
                return c
        return None

    header = ["Tour", "Fmt/Gender", "Bot tours.json", "espn_series", "cricapi_series",
              "Squads (bot)", "Draft matches", "Draft players", "Draft espn-series",
              "Registry mirror", "Points tab (sheet)", "Sheet→draft TEAM", "Sheet→draft PID",
              "Verdict / gaps"]
    rows = []
    for t in tours:
        name, tab = t.get("name", ""), t.get("tab", "")
        gender = t.get("gender", "")
        fmt = (t.get("format") or "T20").upper()
        espn = (t.get("espn_series") or "").strip()
        capi = (t.get("cricapi_series") or "").strip()
        gaps = []

        # squads (bot)
        sq = _load(f"{BOT}/{t.get('squads','')}", {}) if t.get("squads") else {}
        sq_n = sum(len(v.get("players", [])) for v in (sq or {}).values())
        squads_cell = f"Y ({sq_n})" if sq_n else "✗"
        if not sq_n:
            gaps.append("no squads")

        # draft espn-series registration (drives live points + lineups)
        gkey = "W" if gender == "female" else "M"
        espn_in_draft = espn and espn in (despn.get(gkey) or [])
        if espn and not espn_in_draft:
            gaps.append(f"espn_series not in draft espn-series[{gkey}]")
        if not espn:
            gaps.append("espn_series blank (live pts won't resolve)")

        # points tab + sheet<->draft mapping (the silent-break checks)
        sheet_rows = _read_tab(sid, tab) if tab else []
        tab_cell = f"Y ({len(sheet_rows)})" if sheet_rows else "✗ (no data)"
        # distinct sheet Team tokens + Player IDs
        s_tokens = sorted({(r.get("Team") or "").strip() for r in sheet_rows if r.get("Team")})
        s_pids = [(r.get("Player ID") or "").strip() for r in sheet_rows if r.get("Player ID")]
        # team-code mapping
        if s_tokens:
            mapped = [tok for tok in s_tokens if token_to_code(tok)]
            team_cell = f"{len(mapped)}/{len(s_tokens)}"
            unmapped = [tok for tok in s_tokens if not token_to_code(tok)]
            if unmapped:
                gaps.append("team tokens unmapped: " + ",".join(unmapped[:4]))
            tour_codes = {token_to_code(tok) for tok in s_tokens if token_to_code(tok)}
        else:
            team_cell = "—"
            tour_codes = set()
        # pid mapping (sheet Player ID -> draft roster pid)
        if s_pids:
            joined = sum(1 for p in s_pids if p in roster_pids)
            pct = round(100 * joined / len(s_pids))
            pid_cell = f"{joined}/{len(s_pids)} ({pct}%)"
            if pct < 90:
                gaps.append(f"pid join {pct}%")
        else:
            pid_cell = "—"

        # draft matches / players for this tour's codes (from the mapped sheet tokens)
        dm_n = sum(1 for m in dmatches if m.get("team1") in tour_codes or m.get("team2") in tour_codes)
        dp_n = sum(1 for p in dplayers if p.get("team_code") in tour_codes)
        dm_cell = str(dm_n) if tour_codes else "?"
        dp_cell = str(dp_n) if tour_codes else "?"
        if tour_codes and dm_n == 0:
            gaps.append("no draft matches")

        # registry mirror: are the tour's squad names resolvable in the draft mirror?
        mirror_cell = "?"
        if sq_n:
            alias2pid = {}
            for pid, e in dmirror.items():
                for a in e.get("aliases", []) or []:
                    alias2pid.setdefault(_norm_name(a), pid)
            names = [p[0] if isinstance(p, list) else p for v in sq.values() for p in v.get("players", [])]
            hit = sum(1 for n in names if _norm_name(n) in alias2pid)
            mirror_cell = f"{hit}/{len(names)}"
            if names and hit / len(names) < 0.8:
                gaps.append("mirror stale/thin")

        verdict = "✅ READY" if not gaps else "⚠ " + "; ".join(gaps)
        rows.append([name[:34], f"{fmt}/{gkey}", "Y", espn or "✗", capi or "—(ESPN-only)",
                     squads_cell, dm_cell, dp_cell, "Y" if espn_in_draft else "✗",
                     mirror_cell, tab_cell, team_cell, pid_cell, verdict])

    # ---- print (always) ----
    print(f"\nTOUR STATUS — {len(rows)} tour(s)  (sheet_id={'set' if sid else 'MISSING'})\n")
    widths = [max(len(str(r[i])) for r in ([header] + rows)) for i in range(len(header))]
    def fmt_row(r):
        return " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(r))
    print(fmt_row(header))
    print("-+-".join("-" * w for w in widths))
    for r in rows:
        print(fmt_row(r))

    # ---- write TOUR STATUS tab (best-effort) ----
    _write_tab(header, rows)


def _norm_name(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", "", s.lower())).strip()


def _write_tab(header, rows):
    gid = os.environ.get("GSHEET_ID", "")
    creds = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not (gid and creds):
        print("\n(TOUR STATUS tab not written — no GSheet creds; table above is the report)", file=sys.stderr)
        return
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        sh = gspread.authorize(Credentials.from_service_account_info(
            json.loads(creds), scopes=["https://www.googleapis.com/auth/spreadsheets"])).open_by_key(gid)
        try:
            ws = sh.worksheet("TOUR STATUS")
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title="TOUR STATUS", rows=len(rows) + 10, cols=len(header))
        ws.clear()
        ws.update(range_name="A1", values=[header] + rows, value_input_option="RAW")
        print("wrote TOUR STATUS tab", file=sys.stderr)
    except Exception as e:
        print(f"(TOUR STATUS tab write failed: {e})", file=sys.stderr)


if __name__ == "__main__":
    main()
