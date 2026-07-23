# wwc-points-bot — Working Notes

D11 fantasy-points feed → auto-updating Google Sheet (GitHub Actions + service account;
cricsheet + cricapi cross-checked, dots only from cricsheet). Also produces the **shared player
registry** (`registry/players.json`) consumed by the auction (`sync-registry`) and the draft.

## ⛔ Player identity / name-matching — READ before touching it
This is the cross-project spine (auction + draft + bot all resolve names through the registry).
Hard-won rules from the LPL 2026 saga (18 Jul):

- **The registry is the single source of truth for identity.** `build_registry.py` anchors every
  squad name to a stable `cricsheet_id` (pid). Fixes belong here — in `manual_aliases.json` or the
  bridge sources `load_bridges()` reads (auction `mlc-2026.ts`/`lpl-2026.ts` alias+display maps,
  draft `DISPLAY_NAME_MAP`) — NOT in a per-app local alias map (those don't propagate).
  A new tour whose identity file isn't wired into `load_bridges()` silently falls outside the system.
- **`build_tour` reuse gotcha:** a slug-pinned entry from an earlier (pre-ESPN) build is REUSED and
  won't re-anchor unless it still lacks a cricsheet_id AND a bridge/DB match now exists (the fix that
  took LPL from 58→17 unmapped). Re-run: `python3 build_registry.py "<tour name>"` (filter matches
  `tours.json` name — "lanka premier league", not "lpl").
- **A fuzzy match is a HYPOTHESIS — `given_compatible` is the same-person gate.** It rejects
  Dale→Glenn once no bridge forces it. But it CANNOT validate SL initials-forms (Kusal Mendis =
  BKG Mendis) because local data has no full/common name. Never promote a fuzzy/generated map to the
  registry unverified — I once bridged "Dale Phillips"→"GD Phillips" and merged Glenn's 274-match
  record into Dale.
- **Phantom-duplicate rows break anchoring.** Two identical-name rows (a real one + auction phantoms)
  make the matcher's `(score - runner) >= 4` tie-breaker reject both → a star stays unanchored
  (Wanindu/Zahir/Akif/Chameera). Dedup the auction DB, then rebuild.

## GATE before any tour goes live (run in all three apps' setup)
```
python3 identity_healthcheck.py "<tour name>"     # exit 1 on blockers
```
- BLOCKER **dup-cricsheet** — one cricsheet_id under >1 pid (merge/split corruption).
- BLOCKER **fixable-miss** — an exact-name DB record WITH data exists but the squad name is
  unanchored (add a bridge / dedup phantoms + rebuild).
- INFO **unmapped** — no record; genuinely uncapped. Triage once.
- REVIEW **name-mismatch** — anchored SL initials-forms; eyeball for a wrong namesake. NOTE: the
  wrong-*namesake* class can't be auto-verified locally — a future enhancement is an ESPN
  `key_cricinfo` full-name cross-ref (people.csv already carries the id).

## Registry files
- `registry/players.json` — the global registry (pid-keyed; cricsheet_id, else espn:/slug: fallback).
- `registry/manual_aliases.json` — hand-curated `{match, add}` spellings the matcher can't link.
- `registry/team_aliases.json` — TEAM analog of manual_aliases: feed team-name variant → canonical
  franchise name (canon_team). e.g. cricapi feeds LPL's 2025 names, squads/ESPN use 2026. See below.
- `registry/frozen_tours.json` — series ids of fully-resolved tours the bot stops polling (quota).
- `registry/UNMAPPED_<tour>.txt` — per-tour list of no-cricsheet_id squad players (the defect report; triage it).
- `registry/identity_splits.json` — force wrongly-merged identities apart.

## Live-data source fallbacks (autopilot — the 22 Jul LPL/Hundred saga)
The bot MUST produce points even when a feed is unreliable. Per-match source chain: cricsheet
(official, when posted) → ESPN full scorecard → cricapi. Mechanisms in `wc_fps_to_csv.py`:
- **Completion is time-based, NOT cricapi's `matchEnded` flag.** `is_over(m)` = matchEnded OR
  (matchStarted AND started > OVER_HRS ago: T20 8h / ODI 12h). cricapi leaves matchEnded=False for
  DAYS on franchise feeds (LPL, Hundred) — without this a finished match is scored "live" then
  VANISHES once it ages out of the ±1-day near_today window. `ended`/`live` both use is_over.
- **ESPN is a FULL scorecard source, not just dots/XI.** `elif espn_perf: perf = api_perf if
  api_perf else espn_perf`. cricapi's match_scorecard returns "not found" for most franchise-league
  matches; ESPN (keyless) carries them — so a tour needs its `espn_series` set (see below).
- **No-data guard:** a match with no scorecard in ANY source is skipped (retried next run), never
  emitted as a misleading COMPLETED row where everyone scores just the +4 XI bonus.
- **Central team identity:** `canon_team` (registry/team_aliases.json) + `team_key` strips gender
  qualifiers `(Men)`/`(Women)`/`Men`/`Women`. Ingestion resolves every feed team name to the squad's
  canonical name via `canon_team` + `short_of`, so cricapi "MI London Women" → squad "MI London" and
  ESPN "MI London (Men)" all collapse to one key. Fixes franchise-name + gender-suffix mismatches.
- **The Hundred has its OWN scorer (`_score_hundred`, CURRENT_FMT `HUN`)** — NOT T20. Same core scale
  as T20 (run+1, four+4, six+6, wicket+30, dot+1, duck −2, fielding, +4 XI) but The Hundred awards
  **NO strike-rate, NO economy and NO maiden**, and wicket hauls tier from a 2-for (2w+4 / 3w+8 /
  4w+12 / 5w+16). Mirrors the auction ETL's `compute_fantasy_points_hundred` + the draft's
  `d11-score.ts` HUN branch. Set via `tours.json` `"format": "HUN"` (`tour_sync` writes it — but
  note cricapi buckets the Hundred under "T20" for *discovery* only; the SCORING format is HUN).
  `is_fmt` still admits "hundred" matchTypes on the non-ODI branch (match admission is format-agnostic
  between T20/HUN — only the scorer differs).
  ⚠️ Bowler balls: cricapi omits the `overs` field on 100-ball cards, so the ESPN merge backfills
  bowler `balls` (else the `balls>0` bowling gate zeroes every wicket — the Gleeson 4-for → 4-pts bug).

## Auto-ingest: the full new-tour pipeline (hardened 22 Jul — was manual, now automatic)
`tour_sync.py` + `tour_sync_finalize.py` + `.github/workflows/tour-sync.yml` now do the WHOLE
new-tour setup end-to-end. This used to need a manual rescue and caused the LPL/Hundred "every
player shows —" bug (a half-wired tour can't compute points without ESPN, and can't JOIN the draft
with BLANK Player IDs). What now runs automatically:
- **espn_series** — auto-resolved in `tour_sync.py` (`resolve_espn_series`: ESPN search → VALIDATE
  each candidate league id against its dated scoreboard by team-match → the confirmed id, never a
  guess; unresolved ⇒ "" which the gate then rejects). Fixes franchise leagues where cricsheet lags
  + cricapi's scorecard is empty and ESPN is the only live source.
- **identity** — `tour_sync_finalize.py` runs `build_registry` → `backfill_draft_pids` so the sheet
  AND the draft carry the SAME pid (join works even on `slug:` fallbacks — sameness is all that
  matters). The 61MB auction DB is gitignored (absent in CI), so `build_registry.open_pool_con()`
  falls back to a committed players export (`registry/auction_players.json.gz`, ≈0.2MB) — the
  `players` table (name→cricsheet_id + country/gender) is the ONLY thing anchoring needs. Regenerate
  it locally with `python3 registry/export_players_pool.py` whenever the auction player set materially
  changes, then commit the .gz. This is what lets cricapi auto-tours anchor in CI.
- **DRAFT LIVE POINTS (added 23 Jul)** — the draft scores a LIVE match's H2H in-app from ESPN
  (`lib/d11-score.ts` + `getLiveMatchPoints`), zero cricapi/bot. Its two prerequisites are now
  auto-wired so a new tour "just works": (1) `apply_to_repos` writes the tour's `espn_series` into
  the draft's `data/espn-series.json` per gender (was manual in `lib/espn.ts` → the Hundred showed 0);
  (2) `tour_sync_finalize` copies `registry/players.json` → draft `lib/registry-players.json` (the
  mirror `resolveEspnPid` reads for the ESPN→pid join — stale mirror = 0 live points). The draft's own
  ESPN code is now gender-safe (teamKey strips men+women), resolves by ESPN id → common `displayName`
  → shared cricket-identity fuzzy fallback, and is format-aware (ODI vs T20/Hundred).
- **VERIFY GATE** — finalize FAILS the workflow BEFORE any commit/deploy if a new tour has an
  unresolved `espn_series`, pid coverage < `SYNC_MIN_PID_COVERAGE` (0.80), the registry-mirror sync
  failed, OR the tour's `espn_series` is missing from the draft's `espn-series.json[gender]` (live
  points wouldn't resolve). The draft build also runs `npm run check:tours` (unknown team codes / a
  gender with no ESPN series). Every silent-failure mode behind the LPL/Hundred bugs now screams.
  Advisory (still-joins) `fixable-miss` healthcheck blockers do NOT fail the gate — but never rush a
  bridge for a namesake (the Dale→Glenn merge is the mistake to avoid).
- **TOUR INGEST REVIEW** tab (GSheet) — per-tour espn / coverage / health / verdict for a glance.

PREREQUISITE — `TOUR_SYNC_API_KEY` must be a GENUINELY DEDICATED cricapi key (its own free 100/day).
Discovery needs only ~20 hits/day, but if the key is SHARED with the auction/points pool it gets
exhausted and discovery fails LOUD ("all N key(s) quota-blocked — NOT reporting '0 tours'") — correct
(never silently ingest nothing) but it blocks the run. Cron is 00:10 UTC (right after the daily reset)
for exactly this reason. A shared/exhausted key is the #1 reason a run won't fire.

IF THE GATE FAILS: read the TOUR INGEST REVIEW tab / workflow log. espn UNRESOLVED → set it by hand
(id from the espncricinfo series URL, e.g. `.../the-hundred-men-s-competition-2026-1521176`) + add a
`registry/team_aliases.json` entry if cricapi vs ESPN names diverge; low coverage → build_registry
didn't take (check the squad file / auction DB). Fix, then re-run the workflow (idempotent — skips
already-ingested tours).
