#!/usr/bin/env python3
"""
tour_sync_finalize — the post-generation half of auto-ingest that tour_sync.py can't do alone.

After `tour_sync.py --apply` writes a new tour's tours.json entry + squads + draft roster, THIS:
  1. build_registry.py <tour>      — anchor the squad names to pids (else the bot emits BLANK
                                      Player IDs and the draft can't join points — the 22 Jul
                                      Hundred bug).
  2. backfill_draft_pids.py        — stamp the SAME pids into the draft roster (both sides must
                                      share a pid; slug: vs cricsheet_id is fine if identical).
  2b. sync registry MIRROR         — copy registry/players.json -> draft lib/registry-players.json,
                                      the file resolveEspnPid reads for LIVE ESPN scoring/lineups.
                                      Stale mirror = ESPN players don't resolve -> 0 live points.
  3. identity_healthcheck.py <tour>— advisory triage (fixable-miss/dup); NOT fatal on its own,
                                      because a slug: fixable-miss still JOINS.
  4. Writes a "TOUR INGEST REVIEW" tab to the GSheet — the human-glance surface (best-effort).
  5. VERIFY GATE — exits non-zero (fails the workflow BEFORE commit/deploy) if any tour is unsafe
     to go live: espn_series UNRESOLVED, pid coverage below SYNC_MIN_PID_COVERAGE, the mirror sync
     failed, or the tour's espn_series is MISSING from the draft's espn-series.json (so live points
     would never resolve). Guarantees the silent-failure modes behind the LPL/Hundred bugs — blank
     espn_series, blank pids, stale mirror, unregistered draft series — can never ship green.

Usage: python3 tour_sync_finalize.py '["The Hundred Men\\'s Competition 2026", ...]'
Env: DRAFT_REPO, GSHEET_ID + GOOGLE_SERVICE_ACCOUNT_JSON (review tab; optional),
     SYNC_MIN_PID_COVERAGE (default 0.80).
"""
import json, os, re, subprocess, sys
from datetime import datetime, timezone

BOT = os.path.dirname(os.path.abspath(__file__))
MIN_COV = float(os.environ.get("SYNC_MIN_PID_COVERAGE", "0.80"))


def run(cmd):
    r = subprocess.run(cmd, cwd=BOT, capture_output=True, text=True)
    tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-3:])
    print(f"  $ {' '.join(cmd)}  (exit {r.returncode})\n{tail}", file=sys.stderr)
    return r


def pid_coverage(squad_path):
    """Fraction of the tour's squad players that resolve to a (non-blank) pid in the FRESH registry
    (imported in a subprocess so it reflects build_registry's just-written players.json). This is
    the pre-match assertion that anchoring actually ran — <MIN_COV means it didn't take."""
    code = (
        "import json, importlib.util, sys\n"
        "s=importlib.util.spec_from_file_location('b','wc_fps_to_csv.py')\n"
        "b=importlib.util.module_from_spec(s); s.loader.exec_module(b)\n"
        f"sq=json.load(open({squad_path!r}))\n"
        "names=[(p[0] if isinstance(p,list) else p) for t in sq.values() for p in t.get('players',[])]\n"
        "res=sum(1 for n in names if b.resolve_pid(n))\n"
        "print(json.dumps({'total':len(names),'resolved':res}))\n"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=BOT, capture_output=True, text=True)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return {"total": 0, "resolved": 0}


def parse_healthcheck(out):
    """Pull the summary numbers + blocker count from identity_healthcheck stdout."""
    blockers = len(re.findall(r"^\s*BLOCKER ", out, re.M))
    m = re.search(r"fixable-miss (\d+).*?unmapped (\d+)", out)
    fixable = int(m.group(1)) if m else 0
    unmapped = int(m.group(2)) if m else 0
    return blockers, fixable, unmapped


def write_review_tab(rows, stamp):
    """Best-effort: write the TOUR INGEST REVIEW tab (Metric grid, newest run on top). A failure
    here never breaks the gate — the workflow log + exit code are the primary alert."""
    if not (os.environ.get("GSHEET_ID") and os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")):
        print("  (review tab skipped — no GSheet creds)", file=sys.stderr)
        return
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
        creds = Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        sh = gspread.authorize(creds).open_by_key(os.environ["GSHEET_ID"])
        header = ["Ingested (UTC)", "Tour", "Tab", "espn_series", "Squad", "PID coverage",
                  "Health (blockers/fixable/unmapped)", "Verdict", "Action needed"]
        try:
            ws = sh.worksheet("TOUR INGEST REVIEW")
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title="TOUR INGEST REVIEW", rows=200, cols=len(header))
        existing = ws.get_all_values()
        body = existing[1:] if existing else []
        ws.clear()
        ws.update(range_name="A1", values=[header] + [[stamp] + r for r in rows] + body,
                  value_input_option="RAW")
        print("  wrote TOUR INGEST REVIEW tab", file=sys.stderr)
    except Exception as e:
        print(f"  (review tab write failed: {e})", file=sys.stderr)


def main():
    applied = json.loads(sys.argv[1]) if len(sys.argv) > 1 else []
    if not applied:
        print("finalize: nothing applied — noop")
        return
    tours = {t["name"]: t for t in json.load(open(os.path.join(BOT, "tours.json")))}
    stamp = os.environ.get("SYNC_STAMP") or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

    # 1. anchor each new tour's identity. ESPN-only tours (cricapi_series="") aren't bot-scored and
    # their draft LIVE join uses the ESPN name-match path, not the bot registry — AND build_registry
    # needs the auction DB (absent in CI). So skip it for them; only cricapi-scored tours anchor here.
    def _is_espn_only(nm):
        return not (tours.get(nm, {}).get("cricapi_series") or "").strip()
    for name in applied:
        if _is_espn_only(name):
            print(f"== {name}: ESPN-only — skip build_registry (live join uses ESPN name-match) ==", file=sys.stderr)
            continue
        print(f"== build_registry: {name} ==", file=sys.stderr)
        run([sys.executable, "build_registry.py", name])

    # 2. sync the draft roster to the (now-updated) registry — one pass covers all tours
    print("== backfill_draft_pids ==", file=sys.stderr)
    run([sys.executable, "registry/backfill_draft_pids.py"])

    # 2b. Sync the draft's ESPN-resolver registry MIRROR (lib/registry-players.json). The draft's
    # resolveEspnPid (live XI + live ESPN points) reads THIS file to map an ESPN player -> our pid;
    # backfill only stamps players-raw.json. If the mirror stays stale, ESPN's players don't resolve
    # to roster pids -> 0 live points (the 22 Jul Hundred bug). Copy the freshly-anchored registry.
    print("== sync draft registry mirror ==", file=sys.stderr)
    # One canonical repo var; lib/data fall out of it (a local operator who exports only DRAFT_REPO
    # gets consistent paths — CI still sets DRAFT_LIB/DRAFT_RAW explicitly, which win).
    draft_repo = os.environ.get("DRAFT_REPO", os.path.expanduser("~/wwc-draft"))
    draft_lib = os.environ.get("DRAFT_LIB") or os.path.join(draft_repo, "lib")
    draft_data = (os.path.dirname(os.environ["DRAFT_RAW"]) if os.environ.get("DRAFT_RAW")
                  else os.path.join(draft_repo, "data"))
    mirror_ok = True
    try:
        import shutil
        os.makedirs(draft_lib, exist_ok=True)
        shutil.copyfile(os.path.join(BOT, "registry", "players.json"),
                        os.path.join(draft_lib, "registry-players.json"))
        print(f"  synced registry mirror -> {draft_lib}/registry-players.json", file=sys.stderr)
    except Exception as e:
        mirror_ok = False
        print(f"  ⚠ registry mirror sync FAILED: {e}", file=sys.stderr)

    # Draft's per-gender ESPN series list (data/espn-series.json) — apply_to_repos should have
    # added each tour's series; we ASSERT it below so a miss fails the gate, not prod. Distinguish
    # "file unreadable" (path/env problem) from "series absent" so the gate message isn't misleading.
    es_file = os.path.join(draft_data, "espn-series.json")
    draft_series, series_readable = {}, True
    try:
        draft_series = json.load(open(es_file))
    except Exception as e:
        series_readable = False
        print(f"  ⚠ could not read draft espn-series.json at {es_file}: {e}", file=sys.stderr)

    # 3. per-tour metrics + advisory healthcheck
    rows, gate_fail = [], []
    if not mirror_ok:
        gate_fail.append("registry mirror sync failed — draft can't resolve ESPN players (0 live pts)")
    if not series_readable:
        gate_fail.append(f"could not read draft espn-series.json at {es_file} (path/env problem, not a missing series)")
    for name in applied:
        t = tours.get(name, {})
        espn = (t.get("espn_series") or "").strip()
        espn_only = _is_espn_only(name)
        squad_path = os.path.join(BOT, t.get("squads", ""))
        cov = pid_coverage(squad_path) if os.path.exists(squad_path) else {"total": 0, "resolved": 0}
        frac = (cov["resolved"] / cov["total"]) if cov["total"] else 0.0
        # identity_healthcheck needs the auction DB (absent in CI) → skip for ESPN-only tours.
        if espn_only:
            blockers, fixable, unmapped = 0, 0, 0
        else:
            hc = run([sys.executable, "identity_healthcheck.py", name])
            blockers, fixable, unmapped = parse_healthcheck(hc.stdout + hc.stderr)

        problems = []
        if not espn:
            problems.append("SET espn_series (auto-resolve failed) — franchise pts won't load")
        # pid coverage gates only cricapi-SCORED tours. An ESPN-only tour's live points resolve by
        # ESPN name-match (not the bot registry), so 30% "coverage" doesn't mean live points fail.
        if not espn_only and frac < MIN_COV:
            problems.append(f"pid coverage {frac:.0%} < {MIN_COV:.0%} — anchoring didn't take")
        # GAP-1 safety net: the draft must carry this tour's espn_series in its per-gender list,
        # or getEspnLineup / getLiveMatchPoints can't resolve the event -> no lineups, 0 live pts.
        gkey = "W" if t.get("gender") == "female" else "M"
        if espn and espn not in (draft_series.get(gkey) or []):
            problems.append(f"espn_series {espn} MISSING from draft espn-series.json[{gkey}] — live points won't resolve")
        verdict = "REVIEW" if problems else ("OK (has slug fixable-miss)" if fixable else "OK")
        rows.append([name, t.get("tab", ""), espn or "UNRESOLVED",
                     str(cov["total"]), f"{frac:.0%} ({cov['resolved']}/{cov['total']})",
                     f"{blockers}/{fixable}/{unmapped}", verdict, "; ".join(problems)])
        if problems:
            gate_fail.append(f"{name}: " + "; ".join(problems))

    write_review_tab(rows, stamp)

    # 4. VERIFY GATE — the whole point: never ship a tour that will silently show no points.
    print("\n=== TOUR INGEST VERIFY ===", file=sys.stderr)
    for r in rows:
        print(f"  {r[0][:40]:40} espn={r[2]:12} cov={r[4]:16} health(b/f/u)={r[5]:8} -> {r[6]}",
              file=sys.stderr)
    if gate_fail:
        print("\n❌ VERIFY GATE FAILED — NOT shipping (fix, then re-run):", file=sys.stderr)
        for g in gate_fail:
            print(f"   - {g}", file=sys.stderr)
        sys.exit(1)
    print("\n✅ VERIFY GATE PASSED — safe to commit + deploy.", file=sys.stderr)


if __name__ == "__main__":
    main()
