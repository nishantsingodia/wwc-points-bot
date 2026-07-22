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
- **The Hundred scores as T20:** `scoringFormatOf` maps everything non-ODI → T20 (no separate Hundred
  D11 match scorer exists anywhere; "no SR/econ/maiden" is the auction VALUATION engine only). is_fmt
  admits "hundred" match types.

## ⚠️ After tour-sync auto-adds a tour, FINISH the setup — it is NOT fully automatic
`tour_sync.py` writes the tours.json entry + a squads file but does NOT: set espn_series, anchor
identity, or backfill the draft. A half-set-up tour either can't compute points (no ESPN) or its
points can't JOIN the draft (BLANK Player IDs → every player shows "—", H2H totals wrong). This bit me
on The Hundred (22 Jul). Before treating a tour-sync'd tour as live, do ALL of:
1. **Set `espn_series`** in tours.json (grab the numeric id from the espncricinfo series URL, e.g.
   `.../the-hundred-men-s-competition-2026-1521176`). Empty ⇒ NO ESPN fallback ⇒ franchise-league
   points never populate (cricsheet lags, cricapi scorecard empty).
2. **`python3 build_registry.py "<exact tours.json name>"`** — anchors squad names → pids. WITHOUT it
   the CSV emits BLANK Player IDs and the draft (which joins by pid) shows nothing.
3. **`python3 identity_healthcheck.py "<tour name>"`** — the GATE. Triage fixable-miss/dup blockers
   (a `fixable-miss` on slug: still JOINS if both sides slug-match, but anchor it properly when safe —
   never rush a bridge for a namesake: the Dale→Glenn merge is the mistake to avoid).
4. **`python3 registry/backfill_draft_pids.py`** then **deploy wwc-draft** — so the draft roster
   carries the SAME pids the sheet now emits. Ship the bot registry push and the draft deploy together.
