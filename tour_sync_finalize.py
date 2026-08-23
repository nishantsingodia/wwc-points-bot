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
     ESPN is the only base feed now, so a blank espn_series is a tour with NO feed at all.
     A missing cricbuzz_series is reported as a WARN, not a gate failure: it means the tour has no
     L1 SECOND WITNESS (ESPN single-sourced until cricsheet publishes), which is a real risk a human
     must see — but Cricbuzz's series resolver legitimately cannot name every bilateral, and
     blocking ingest on it would stop the tour being scored at all, which is strictly worse.

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
        header = ["Ingested (UTC)", "Tour", "Tab", "espn_series", "cricbuzz_series (L1)", "Squad",
                  "PID coverage", "Health (blockers/fixable/unmapped)", "Verdict", "Action needed"]
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


def write_needs_cricinfo_tab():
    """Push build_registry's unresolved squad names to 'Needs Cricinfo ID' — by DELEGATING to the
    bot's writer, so there is exactly ONE implementation of that tab.

    WHAT WENT WRONG (measured 14 Aug 2026). This tab had TWO writers with two sources and neither
    read the other's:
      · this one   — reads registry/needs_cricinfo_pending.json, runs ONLY on tour INGEST;
      · wc_fps_to_csv.write_needs_cricinfo_tab — runtime discoveries, and (until 4bc310c) it
        "deliberately does NOT touch needs_cricinfo_pending.json", read OR write.
    Cost: 23 CPL squad names sat in the pending file from the 13 Aug ingest onward while the live
    tab showed 52 rows — 8 CPL, every one of them from the runtime path (2 `ci:`, 6 `cb:`). The
    owner was never asked about the 23. 4bc310c taught the bot's writer to READ the pending file
    (still never to write it — build_registry keeps sole ownership), which makes the tab a view
    over both sources on every full run. Two implementations of one tab is what created the gap,
    so the fix here is to delete this one, not to teach it the same tricks.

    Concretely, the copy this replaces had drifted in three ways that only ever failed silently:
      · dedupe by BLIND COLUMN INDEX (`r[1]`) while read_needs_cricinfo reads its columns BY HEADER
        NAME. One inserted column and every existing row goes unrecognised -> all 52 re-appended,
        45 of them already carrying a filled-in id.
      · no ANSWERED check: an id filled in and the row then deleted (the habit the Recon tab
        teaches — resolved rows vanish) got re-asked on the next ingest. 134 names are answered in
        manual_ci_bridges.json today.
      · no ALREADY-ANCHORED check: a name that now resolves to a real pid was still queued.

    The delegate is append-only, dedupes on current_pid, never writes a cell a human filled, and
    prints its own summary. Best-effort: an import failure costs only the ingest-time push — the
    pending file is still on disk and the next FULL bot run (2-hourly) surfaces it. Never fatal to
    the verify gate."""
    if BOT not in sys.path:
        sys.path.insert(0, BOT)
    try:
        # Imported HERE, not at module scope: build_registry has just rewritten
        # registry/players.json, and the bot loads the registry at import time. Importing earlier
        # would suppress against a stale alias index. Import is ~0.06s and writes nothing.
        import wc_fps_to_csv as bot
    except Exception as e:
        print(f"  (needs-cricinfo: could not import the bot's writer: {e} — pending file NOT "
              f"pushed at ingest; the next full bot run will surface it)", file=sys.stderr)
        return
    bot.NEEDS_CRICINFO[:] = []   # ingest has no runtime scoring discoveries to merge in
    try:
        bot.write_needs_cricinfo_tab()
    except Exception as e:
        print(f"  (needs-cricinfo tab write failed: {e})", file=sys.stderr)


def main():
    applied = json.loads(sys.argv[1]) if len(sys.argv) > 1 else []
    if not applied:
        print("finalize: nothing applied — noop")
        return
    tours = {t["name"]: t for t in json.load(open(os.path.join(BOT, "tours.json")))}
    stamp = os.environ.get("SYNC_STAMP") or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

    # 1. anchor each new tour's identity. build_registry anchors in CI via the committed players
    # export (registry/auction_players.json.gz), so it no longer needs the 61MB gitignored auction
    # DB (build_registry.open_pool_con falls back to it). EVERY tour is anchored, no exemption.
    # There used to be one: build_registry was skipped when cricapi_series was "", reasoning that
    # such a tour "isn't bot-scored and its draft LIVE join uses ESPN name-match". Both halves were
    # wrong in consequence: the draft's COMPLETED join is pid-based for EVERY tour, so a tour that
    # ships on placeholder slug: pids can never match a points row and settles at ZERO —
    # permanently, with no name fallback (lookupPlayerPoints refuses to fuzzy-fall-back for a pid'd
    # player). It shipped 82 such players: the whole CPL squad set plus Rohit Sharma, Shubman Gill,
    # Virat Kohli, KL Rahul, Kuldeep Yadav and Jasprit Bumrah on the India ODI tour.
    # That exemption is not just fixed but unrepresentable now: ESPN-only IS every tour, there is no
    # cricapi_series left to branch on, and build_registry anchors fine from ESPN rosters
    # (athlete.id IS the cricinfo id).
    for name in applied:
        print(f"== build_registry: {name} ==", file=sys.stderr)
        run([sys.executable, "build_registry.py", name])

    # 1b. RE-MIRROR the Cricbuzz bridge. build_registry rewrites registry/players.json WHOLESALE,
    # which ERASES the `cricbuzz_id`/`cricbuzz_tier` fields cricbuzz_bridge writes there — the
    # store (registry/cricbuzz_bridge.json) is durable, players.json is only its mirror. Without
    # this line the mirror silently empties after every tour sync and stays empty, which is the
    # written-but-never-restored variant of this repo's favourite bug. Idempotent, no network, and
    # non-fatal: the bot reads the STORE, not the mirror, so a failure here costs the mirror only.
    if applied:
        print("== cricbuzz bridge --apply (re-mirror after build_registry) ==", file=sys.stderr)
        if run([sys.executable, "registry/cricbuzz_bridge.py", "--apply"]).returncode:
            print("  ⚠ cricbuzz bridge re-mirror FAILED — registry/players.json carries no "
                  "cricbuzz_id fields until it is re-run. Not fatal: the bot reads the store.",
                  file=sys.stderr)

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
        cbz = str(t.get("cricbuzz_series") or "").strip()
        squad_path = os.path.join(BOT, t.get("squads", ""))
        cov = pid_coverage(squad_path) if os.path.exists(squad_path) else {"total": 0, "resolved": 0}
        frac = (cov["resolved"] / cov["total"]) if cov["total"] else 0.0
        # Run the healthcheck for EVERY tour. It was previously hard-zeroed for ESPN-only tours,
        # which reported "0 blockers" for a tour nobody had checked — a clean bill of health as an
        # artefact of not looking.
        hc = run([sys.executable, "identity_healthcheck.py", name])
        blockers, fixable, unmapped = parse_healthcheck(hc.stdout + hc.stderr)

        problems, warns = [], []
        if not espn:
            # ESPN is the ONLY base feed: no espn_series means no scorecard, no lineups, no points.
            problems.append("SET espn_series (auto-resolve failed) — with no base feed this tour "
                            "cannot be scored at all")
        if not cbz:
            # NOT a gate failure — see the module docstring. But it must be SEEN: with cricapi gone,
            # no cricbuzz_series means nothing can contradict ESPN until cricsheet (L2) publishes.
            warns.append("NO L1 SECOND WITNESS (cricbuzz_series unset) — ESPN is single-sourced "
                         "for this tour; resolve the Cricbuzz series by hand and add it")
        # pid coverage gates EVERY tour. The old exemption for ESPN-only tours is what let CPL ship
        # with 75 unanchored players: live points may resolve by ESPN name-match, but the COMPLETED
        # join — the one that settles money — is pid-based for every tour without exception.
        if frac < MIN_COV:
            problems.append(f"pid coverage {frac:.0%} < {MIN_COV:.0%} — anchoring didn't take")
        # GAP-1 safety net: the draft must carry this tour's espn_series in its per-gender list,
        # or getEspnLineup / getLiveMatchPoints can't resolve the event -> no lineups, 0 live pts.
        gkey = "W" if t.get("gender") == "female" else "M"
        if espn and espn not in (draft_series.get(gkey) or []):
            problems.append(f"espn_series {espn} MISSING from draft espn-series.json[{gkey}] — live points won't resolve")
        verdict = ("REVIEW" if problems else "WARN (no L1 witness)" if warns
                   else "OK (has slug fixable-miss)" if fixable else "OK")
        rows.append([name, t.get("tab", ""), espn or "UNRESOLVED", cbz or "NONE",
                     str(cov["total"]), f"{frac:.0%} ({cov['resolved']}/{cov['total']})",
                     f"{blockers}/{fixable}/{unmapped}", verdict, "; ".join(problems + warns)])
        if problems:
            gate_fail.append(f"{name}: " + "; ".join(problems))

    write_review_tab(rows, stamp)
    write_needs_cricinfo_tab()   # push any unresolved players to the "Needs Cricinfo ID" tab for a human

    # 4. VERIFY GATE — the whole point: never ship a tour that will silently show no points.
    print("\n=== TOUR INGEST VERIFY ===", file=sys.stderr)
    for r in rows:
        print(f"  {r[0][:40]:40} espn={r[2]:12} cricbuzz={r[3]:8} cov={r[5]:16} "
              f"health(b/f/u)={r[6]:8} -> {r[7]}", file=sys.stderr)
    blind = [r[0] for r in rows if r[3] == "NONE"]
    if blind:
        print(f"\n⚠ {len(blind)} tour(s) ingested with NO L1 SECOND WITNESS (ESPN single-sourced "
              f"until cricsheet publishes): {blind}", file=sys.stderr)
    if gate_fail:
        print("\n❌ VERIFY GATE FAILED — NOT shipping (fix, then re-run):", file=sys.stderr)
        for g in gate_fail:
            print(f"   - {g}", file=sys.stderr)
        sys.exit(1)
    print("\n✅ VERIFY GATE PASSED — safe to commit + deploy.", file=sys.stderr)


if __name__ == "__main__":
    main()
