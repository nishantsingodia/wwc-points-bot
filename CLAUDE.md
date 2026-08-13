# wwc-points-bot — Working Notes

D11 fantasy-points feed → auto-updating Google Sheet (GitHub Actions + service account). THREE
feeds: **cricapi** (base card) + **ESPN** (full scorecard; the only LIVE source of `dots`/`maidens` — cricsheet supplies them too, at L2)
cross-checked at L1, then **cricsheet** (official) reconciling at L2. Also produces the **shared
player registry** (`registry/players.json`) consumed by the auction (`sync-registry`) and the draft.

## ⛔ RECON — the owner-locked model (7 Aug 2026). Authoritative; do NOT re-derive it.
**`Match Status` and `Recon State` are two INDEPENDENT axes.** Never encode recon progress inside
the status — that is what made `COMPLETED_FLAGGED` mean four different things.
```
LIVE       any data unconsumed  |  any L1 gap unresolved → stay LIVE + a NAMED row in the Recon tab
           L1 done AND all consumed → COMPLETED, "L1 recon done"   ⇒⇒ BASE POINTS FREEZE HERE ⇐⇐
COMPLETED  (never returns to LIVE)   cricsheet not posted yet      → "L1 recon done"
           cricsheet posted, diffs open → "L2 recon pending"  |  all clear → "L2 recon done"
```
- **The L2 baseline is THE RECONCILED L1 VALUE** — exactly what was published and frozen as base
  points after ALL approved L1 overrides were applied. An approval may be S1 (cricapi), S2 (ESPN) or
  Manual, so the baseline can come from EITHER feed or from a hand-typed number. It is **NOT**
  "ESPN's value", NOT cricapi's, NOT a value recomputed from raw feeds on a later run.
  ⇒ **READ the baseline from the frozen record; never recompute the provisional cut.** That
  recomputation is the root cause of the phantom `dots 0→N` review rows that corrupted settled points.
- **Single-source fields = `dots` + `maidens`, ESPN ONLY** (cricapi supplies neither; cricsheet
  supplies both). No second number at L1 ⇒ **no L1 comparison**; ESPN's value is accepted and does
  NOT block COMPLETED. ESPN's value ABSENT for a bowler who bowled = unconsumed data ⇒ stays LIVE.
  L2 reconciles these against the reconciled-L1 baseline above.
- **Nothing goes unconsumed.** Data the bot can't attribute, or a player it can't fully score ⇒ match
  stays LIVE + a NAMED row in the Recon tab. No silent zeros, no dropped players. Corollary: a
  single-feed (cricapi-only, no ESPN) match has no dots ⇒ unconsumed ⇒ **LIVE**. This SUPERSEDES the
  old "COMPLETED but FLAGGED" decision, which `classify_match_status` still returns
  (`wc_fps_to_csv.py:1418`).
- **Identity NEVER appears in the Recon tab.** Recon answers "which value is right?"; identity
  answers "who is this?" → the **"Needs Cricinfo ID"** tab. No new tab is needed: ESPN's `athlete.id`
  IS the cricinfo id (`build_registry.py:336`), so "Needs ESPN PID" and "Needs Cricinfo ID" are the
  same tab. Discriminator — use ESPN as the third feed instead of asking the human: ESPN saw him play
  this match ⇒ he played, cricsheet just spells him differently ⇒ IDENTITY failure ⇒ Needs Cricinfo
  ID, HOLD his provisional value. ESPN didn't see him either ⇒ he genuinely didn't play ⇒ score DNP,
  not an anomaly.
- **Neither feed is "better" — do NOT flip the base to ESPN.** Measured against cricsheet ground
  truth, 57 disputed fields / 42 player-matches:

  | field | winner | right |
  |---|---|---|
  | runs | **cricapi** | 24/32 (75%) |
  | wkts | **cricapi** | 7/11 (64%) |
  | 4s | ESPN | 5/7 (71%) |
  | 6s | ESPN | 6/6 (100%) |
  | **overall** | **cricapi** | **33/56 (59%)** |

  In fantasy points (what settles money) it's near a coin flip: cricapi 444 FP of error vs ESPN 312;
  catastrophes ≥30 FP are 7 v 7. The owner's own L1 adjudications: **30/30 correct** vs cricsheet.

## ⛔ Player identity / name-matching — READ before touching it
This is the cross-project spine (auction + draft + bot all resolve names through the registry).
**Anchor = the ESPNcricinfo id** (migrated 25 Jul 2026 from cricsheet_id). The id is UNIQUE
(people.csv `key_cricinfo`, 18253/18253), invariant, and verifiable at cricinfo.com/cricketers/x-<id>.

- **The registry is the single source of truth for identity.** `build_registry.py` resolves every
  squad name to a **cricinfo id** and keys the entry `ci:<cricinfoId>`. The `cricsheet_id` is DERIVED
  from `registry/crosswalk.json` (people.csv-derived cs↔ci) — so cs and ci always point to the SAME
  person by construction. Fallback ladder: `ci:` → `cs:<cricsheetId>` (in cricsheet, no cricinfo id)
  → `uncapped:<slug>` (in neither; FLAGGED). In practice everyone has a cricinfo id, so cs:/uncapped:
  are near-empty.
- **Fixes go in `registry/manual_ci_bridges.json`** (human-verified announced-name → cricinfo id) —
  the permanent, authoritative alias source. Also the legacy `load_bridges()` sources (auction
  `mlc-2026.ts`/`lpl-2026.ts` maps, draft `DISPLAY_NAME_MAP`) are still consulted. NOT a per-app local
  map (those don't propagate).
- **`resolve_ci` NEVER guesses on ambiguity.** Cascade: manual bridge → legacy TS bridge → exact
  people.csv → FUZZY (null-on-ambiguity, via cricket-identity semantics) → ESPN roster athlete.id
  (= the cricinfo id). An ambiguous fuzzy match resolves to NOTHING → the player goes to HOLD, not a
  fabricated `slug:`. This is what stops the Jo-Gardner-into-Ashleigh class. `given_compatible` +
  first-initial gate reject Dale→Glenn; the id is only ever COPIED from the crosswalk, never invented.
- **`build_tour` is idempotent + safe.** Existing players are REUSED by alias (a rebuild reproduces
  the same `ci:` set — verified). A NEW/unresolved player is written to `registry/needs_cricinfo_pending.json`
  → the **"Needs Cricinfo ID"** GSheet tab (see below), where a human drops the id → `manual_ci_bridges`
  picks it up next build. Re-run: `python3 build_registry.py "<tour name>"`.
- **Wrong cricsheet anchors happen too** (Milan Ratnayake was fuzzy-matched to KTH Ratnayake). Deriving
  `cricsheet_id` from the verified cricinfo id (not the fuzzy match) fixes this class. **Phantom-duplicate
  auction rows** still hurt exact/fuzzy lookups — dedup the auction DB, then rebuild.

## Settled results can move — the settlement baseline (29 Jul 2026)
The points sheet is REWRITTEN in place every run. Legacy behaviour: L2 recon compared cricsheet
against a LIVE re-computation of the provisional cut — NOT against what was on screen when money was
settled — so a scorer fix / ESPN backfill / registry change moved settled numbers invisibly to
reconciliation. **Per the locked spec above that recomputation IS the bug**: base points freeze at
L1-done and the L2 baseline must be READ from the frozen record.
- `registry/settlement_snapshots.json` + the **`SETTLEMENT AUDIT`** sheet tab = **WRITE-ONCE** record
  of each player's points the first time their match published COMPLETED. Never edit it; the draft
  app diffs the live sheet against it (`/audit`, results "Audit" tab, lobby Completed badge). It
  currently fires on any COMPLETED/`COMPLETED_FLAGGED` publish (`wc_fps_to_csv.py:2394`); the locked
  freeze point is the **L1-done transition** — this is the store the L2 baseline must be read from.
- **cricsheet rows resolve by ID, not name** (`resolve_perf_pid` + `CS2PID`). cricsheet writes
  initials form (`PWH de Silva` = Wanindu Hasaranga) — name matching zeroed him on two matches the
  app badged COMPLETED with no flag. Two different "E Jones" exist in the Hundred Women's data; only
  ids tell them apart.
- An unresolved official-card identity now HOLDS the provisional value + flags
  (`⚠ identity unresolved on official card`) instead of silently scoring 0 — and routes to the
  **"Needs Cricinfo ID"** tab, never the Recon tab (rule E above).
- `points_gap()` compares the scored TOTAL as a backstop, so a change in a field not listed in
  `RECON_L2` (balls faced/bowled → SR/econ) can't read as "✓ complete".
- Full post-mortem + the Hasaranga/Tharindu/Dale-Phillips cases: `RECON_REVIEW_WORKFLOW.md`.
  Verified-but-unfixed recon defects (`xcheck` never read; `L1_RUN_TOL=1` hiding 7 pts/row; dead `espn_dots()`;
  ESPN playbyplay `limit=600` with no pagination): `RECON_DEV_PLAN.md`. Don't rediscover them.

## GATE before any tour goes live (run in all three apps' setup)
```
python3 identity_healthcheck.py "<tour name>"     # exit 1 on blockers
```
- BLOCKER **dup-cricinfo** — one cricinfo id under >1 pid (merge/split corruption).
- BLOCKER **fixable-miss** — an exact-name DB record WITH data exists but the squad name is unanchored
  (add a `manual_ci_bridges` entry / dedup phantoms + rebuild).
- INFO **needs-review** — no offline record; the id exists on cricinfo.com but not yet in our register
  → it lands in the "Needs Cricinfo ID" tab for a human to fill.
- REVIEW **name-mismatch** — anchored initials-forms (Kusal Mendis = BKG Mendis); eyeball for a wrong
  namesake. The cricinfo-id anchor + null-on-ambiguity make silent wrong-namesake merges structurally hard.

## Registry files
- `registry/players.json` — the global registry (**`ci:<cricinfoId>`-keyed**; `cs:`/`uncapped:` fallback;
  `cricsheet_id` derived from the crosswalk).
- `registry/crosswalk.json` — people.csv-derived `cricsheet_id ↔ cricinfo_id` (+ `_2/_3` alternates). The spine.
- `registry/manual_ci_bridges.json` — human-verified announced-name → cricinfo id (permanent aliases).
- `registry/needs_cricinfo_pending.json` — players `build_registry` couldn't resolve → pushed to the
  "Needs Cricinfo ID" GSheet tab by `tour_sync_finalize.write_needs_cricinfo_tab` (self-maintaining review loop).
- `registry/pid_map.json` — the one-shot migration map (old pid → `ci:`); the draft's `lib/pid-map.json`
  shim reads a copy so the sheet's pre-migration Player IDs still join.
- `registry/manual_aliases.json` — hand-curated `{match, add}` spellings the matcher can't link.
- `registry/team_aliases.json` — TEAM analog: feed team-name variant → canonical franchise name (canon_team).
- `registry/frozen_tours.json` — series ids of fully-resolved tours the bot stops polling (quota).
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
  AND the draft carry the SAME `ci:` pid (join works even on a `cs:`/`uncapped:` fallback — sameness is
  all that matters). The 61MB auction DB is gitignored (absent in CI), so `build_registry.open_pool_con()`
  falls back to a committed players export (`registry/auction_players.json.gz`, ≈0.2MB) — the
  `players` table (name→cricsheet_id→cricinfo_id via the crosswalk + country/gender) is what anchoring needs. Regenerate
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

## ⛔ NEVER send a browser User-Agent to ESPN (found 10 Aug 2026, cost a day of debugging)
`site.api.espn.com` **403s browser-impersonating User-Agents**: `Mozilla/5.0` → 403, a full Chrome
UA → 403, `curl/8.7.1` → 200, urllib's default → 200, an honest bot UA (`ESPN_UA`) → 200. Every
fetcher swallows the error and returns `{}`/`null`, so a blanket 403 is **indistinguishable from
"ESPN has no data"** — nothing goes red. It was live in `wc_fps_to_csv.espn_get` (dots + maidens,
the single-source fields; announced XI; the full-scorecard fallback that carries franchise
leagues), `build_registry` (roster `athlete.id` IS the cricinfo id → resolvable players pushed to
"Needs Cricinfo ID"), `tour_sync`, and the draft's `lib/espn.ts` (XI + the whole live H2H scorer).
Symptom in the log: `sources: N cricsheet(official), 0 cricapi+ESPN, 0 cricapi-only`.
`site.web.api.espn.com` (SEARCH) accepts anything, so tour *discovery* keeps working while every
fixture/scorecard call comes back empty — a very misleading combination. Both Python fetchers now
warn-once on a distinct transport failure. **Do NOT put "Mozilla" back**, and when ESPN data goes
quietly missing check the UA + status code BEFORE cricapi quota / cricsheet lag / identity.

## ⛔ ESPN can serve an EMPTY scorecard that looks complete (found 13 Aug 2026)
`playbyplay` intermittently returns HTTP 200 whose whole body is
`{"commentary": {"count": 1, "pageCount": 1, "items": [<the pre-match "Hello and welcome back"
preamble>]}}` — for a match ESPN's own scoreboard calls Final. It is INTERNALLY CONSISTENT, so
every guard that compares items against ESPN's self-reported `count` passes it, and the match
scores with all 22 players `Played=Y`, every stat 0 and a bare +4 XI bonus. With settlement
recording live that is what FREEZES as the money baseline. Seen on CPL ev 1534183.
- **It is NOT a page-size threshold.** It first showed at `limit=1000` while 600/500/300 returned
  the real 236 items — but a re-probe of the same events minutes later was fine at 1000, and
  `limit=100` had 502'd in the same window. It is ESPN-side flakiness: any limit can hit it. Do not
  "fix" it by tuning the limit.
- **The defence is `espn_expected_balls()`** — the SCORECARD's per-bowler
  `overallLhb.balls + overallRhb.balls`, a different endpoint and field family, so it cannot go
  blank in the same breath. `parse_espn` refuses the match if the ball-by-ball is short of it.
  That total counts wides and no-balls (226 legal + 10 wides = 236 on ev 1534183), so the check
  counts EVERY delivery — deliberately not filtered by `legal`, since a completeness check must not
  depend on the extras-parsing it exists to validate. Pinned by `tests/test_espn_completeness.py`.
- Swept `settlement_snapshots.json` (3119 rows / 87 settled matches) for the signature: **0 hits.**
  No settled money was affected — CPL was the only exposed tour and it was not being scored at all.

## New player who is in NO squad — resolved by id, never by name (13 Aug 2026)
A mid-tournament signing or injury replacement appears in the XI but in no squad list. He used to
publish with a **blank Player ID**: unjoinable by the draft, invisible to every pid-keyed check.
Now `resolve_perf_pid` mints `ci:<athlete.id>` — derived, not guessed, since ESPN's athlete.id IS
the cricinfo id — and queues him to **"Needs Cricinfo ID"** with his cricinfo URL for a human to
fold into the squad. Two guards matter: the id must be a positive integer (else fall through), and
**a name that already resolves keeps its existing pid** — minting `ci:` over an `uncapped:`/`cs:`
placeholder would put one person under two pids, which is the split-identity blocker.
Live: Rivaldo A Clarke (ci:1275938), Kevlon Alston Anderson (ci:1209188), CPL ev 1534182.
Two supporting fixes: `espn_xi` now carries `espn_id` into `blank_perf` (it was discarded, so an
XI-only player could only ever be found by name), and the SILENT-DROP auto-add now attributes the
team via ESPN's roster map + `best_team` instead of `v["team"]` — `parse_espn` never sets `team`,
so on any ESPN-sourced tour that whole auto-add was dead code and every silent drop hit `continue`.

## The KEYLESS ESPN tour path (Column A of TOUR CONTROL) — what it does and does NOT do
Typing a tour name in **Column A of `TOUR CONTROL` (or `TOUR STATUS`)** is the no-code way to add a
tour: `tour_sync.py --from-status-sheet` reads both tabs, skips names already in `tours.json`, and
ESPN-adds the rest (name → league id → fixtures → full squads), keyless. Hardened 10 Aug 2026 when
CPL 2026 became the **first franchise league** through it (every prior league — MLC, LPL, Hundred —
came via cricapi; only 2-team bilaterals had used this path), which exposed four silent failures:
- **Format**: `_fmt_of` name-sniffs when `matchType` is blank, which the ESPN path left blank. A
  bilateral survives ("3rd T20I"); a league's "4th Match" does not → every fixture bucketed to
  `None` and gen_tour dropped the whole list. Now read from `competitions[0].class.eventType`.
- **Squads** hang off the EVENT summary, so one event yields only the 2 teams playing it — stopping
  at the first event collapsed 7 franchises into a 2-team bilateral. Now merged across events.
- **Look-back**: the scan started at `now`, losing every already-played match on a mid-season add.
  Now scans backwards too (6-empty-day stop).
- **Write-once**: `apply_to_repos` skips a tour whose `tab` already exists, so the fixture list is
  **never extended or backfilled**. Hence the 60-day forward window, the back-scan, and the retry
  on transient 5xx — one 502 mid-scan permanently costs that day's match (it did: 34 vs 35).
Two consequences worth knowing before promising anything:
- ~~**An ESPN-added tour has `cricapi_series: ""` → the bot does NOT score it.**~~ **FIXED 13 Aug
  2026 — an ESPN-only tour is now scored like any other.** What used to happen: the match LIST came
  only from cricapi's `series_info`, so a blank series id aborted at the series_info guard; and
  `_tour_approved("")` read "pending" forever because `sync_tour_control` writes no TOUR CONTROL row
  for a blank id, making the tour permanently un-approvable. CPL 2026 was live from 7 Aug with 35
  fixtures draftable and scored NOTHING. Now: `espn_match_list()` builds the fixture list from the
  league scoreboard (same shape as a cricapi matchList, minus the cricapi `id` — the scoring loop
  already guards `if m.get("id")` and falls through to the ESPN scorecard), and the TOUR CONTROL
  gate is skipped for a blank series id because that gate exists to ration the cricapi QUOTA, which
  such a tour does not spend. `tour_sync_finalize` no longer exempts these tours from
  `build_registry` / `identity_healthcheck` / the 0.80 pid-coverage gate either — the draft's
  COMPLETED join is pid-based for EVERY tour, so a tour shipping on placeholder pids settles at ZERO.
- **TBA knockout fixtures are dropped by design** and, per write-once above, never backfilled —
  playoffs must be hand-added once the qualifiers are known.
Naming: ESPN league names are season-less ("Caribbean Premier League"), and a year appended to the
search query returns ZERO results — `espn_add_named` now retries without it, so "Caribbean Premier
League (CPL) 2026" in Column A resolves fine. `espn_series` for these is the site-API **league** id
(8623), not the 7-digit series id (1534175); ESPN's scoreboard endpoint accepts either.
