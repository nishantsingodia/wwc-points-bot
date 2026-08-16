# wwc-points-bot — Working Notes

D11 fantasy-points feed → auto-updating Google Sheet (GitHub Actions + service account). THREE
feeds: **cricapi** (base card) + **ESPN** (full scorecard; the only LIVE source of `dots`/`maidens` — cricsheet supplies them too, at L2)
cross-checked at L1, then **cricsheet** (official) reconciling at L2. Also produces the **shared
player registry** (`registry/players.json`) consumed by the auction (`sync-registry`) and the draft.
**Cricbuzz** (13 Aug 2026) can replace cricapi as the L1 second witness per tour — see
"Cricbuzz as the L1 second witness" below. cricapi's code is untouched and is still the witness on
every tour that doesn't opt in.

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
- **An unresolved L1 gap opens L1 only while there is NO official card** (refined 16 Aug 2026, and
  it is a REFINEMENT of the locked model, not a re-derivation — flag it to the owner). "The two
  provisional feeds disagree, pick one" is a question the bot deliberately stops asking once
  cricsheet posts: the Recon tab only queues an L1 row `if unresolved and not cs_path`, and
  `apply_recon_overrides` writes into the very perf dicts `emit()` scores — which on a cricsheet run
  hold the OFFICIAL figures, so an answer given after cricsheet lands overwrites the official card
  with a provisional feed. Reporting `L1_OPEN` there announced an open question with no row and no
  safe answer; harmless as a column, fatal once the freeze started reading this axis (the match
  could never reach L1_DONE ⇒ published forever with no baseline and nothing to click). Measured:
  15 matches / 496 rows / 36 players. `unsourced` and `unattributed` are NOT relaxed — cricsheet
  cannot un-drop a row the pipeline never attributed, and it cannot retro-measure a field nobody saw.
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
  app diffs the live sheet against it (`/audit`, results "Audit" tab, lobby Completed badge). This
  is the store the L2 baseline must be read from.
- **FIXED 16 Aug 2026 — the freeze is at L1-DONE, not on any COMPLETED publish.** It used to fire on
  any COMPLETED/`COMPLETED_FLAGGED` publish, which is strictly earlier: `classify_match_status`'s
  cs_path branch returns COMPLETED without ever inspecting `unresolved`/`unsourced`, so a match whose
  feeds still disagreed minted a write-once baseline off the provisional cut — and write-once meant
  the owner's later (30/30-correct) L1 adjudication could never move it. Measured on the live sheet:
  **913 rows / 27 matches** were published COMPLETED with Recon State still `⏳ L1 recon open`. The
  gate is now `freeze_ok = published and recon_state != "L1_OPEN"` — the RECON axis, not the status.
  A published match with no baseline is normal and visible (the sheet's Recon State says so, the
  draft renders `NO_BASELINE`, and the run prints one line per match).
- ⛔ **`registry/completed_matches.json` is the "COMPLETED never returns to LIVE" ratchet** and is
  now a SEPARATE ledger. It used to be inferred from the settlement store
  (`any(k[0] == mk and v["tour"] == tour ...)`), which only worked while every publish also froze a
  baseline. Freezing at L1-DONE breaks that: **narrowing the freeze without this file re-opens the
  7-9 Aug bug that un-published 12 matches / 410 settled rows.** 417 rows / 12 matches were published
  on 16 Aug ONLY because the ratchet remembered. `has_published_completed` reads BOTH stores (a
  settlement row still proves a publish, so the 95 pre-ledger matches need no migration); keyed
  `(tour, match_key)` because match_key collides across the Hundred's same-day M/W double-headers.
  Backfill/regenerate with `python3 registry/backfill_completed_matches.py --write` — pure function
  of the settlement store, never hand-edited. It is in the **LEDGERS** list of all three workflows;
  drop it and the ratchet silently resets every run. A tour is also **not marked frozen** while any
  published match still owes a baseline — a dormant tour never runs again (that is how the LPL's
  1074 rows had to be restored by hand on 14 Aug).
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

## Cricbuzz as the L1 second witness (13 Aug 2026) — per-tour, fail-safe, identity-gated
`cricapi` is a poor second witness on the franchise leagues: it returns a stub card (every stat 0),
and the only fields it and ESPN both carry are `r/w/4s/6s`, so `dots`/`maidens` had NO validator at
L1 at all. Cricbuzz carries the whole card. **MEASURED** over the two LPL matches with cached
payloads (cb157138/ev1537349, cb157061/ev1537342), 48 player-rows joined by the DERIVED bridge:
**48/48 agree on all 14 fields** — `r b 4s 6s w balls runs_conceded dots maidens catches stumpings
runouts dro lbwb` (`RECON_L1_CB`). A real second opinion on ten fields that had none, with **0**
Recon rows generated. Same machinery throughout: `compute_l1_gaps` / `build_recon_rows` /
`apply_recon_overrides` / S1-S2-Manual / `recon_overrides.json`, just widened and re-pointed.
- **Turn it on per tour**: `"cricbuzz_series": "<id>"` in `tours.json` (LPL 12316, CPL 12123,
  Hundred 11493 men / 11504 women). Absent ⇒ cricapi stays the witness and behaviour is
  byte-identical to before. **Nothing is enabled today** — deliberately, see the S1 warning below.
- **FAIL SAFE, always.** Module missing, series unset, match id not uniquely resolvable, HTTP
  204/403/timeout, unreadable card, empty bridge ⇒ `cb_match_perf` returns **None** (never `{}` —
  that would read as "Cricbuzz saw nobody play"), the tour scores off ESPN exactly as today, and the
  row says `⚠ unverified — single feed (ESPN only, cricbuzz had no card)` plus a Source-column note.
  Cricbuzz can only ever ADD a cross-check; it can never take a match away.
- **⛔ THE JOIN IS TO THE PID ESPN'S OWN ROW GOT — not `ci:<bridged id>`.** They are different keys
  and assuming otherwise manufactured a phantom Recon row on the first real match pair: ESPN
  athlete.id **1364327** (V Lahiru, LPL ev1537342) resolves to **ci:784375**, because 1364327 is not
  a registry key and the registry carries that spelling under a different cricinfo id — people.csv
  has BOTH (`784375 CBRLS Kumara`, `1364327 V Lahiru`). Keying Cricbuzz on `ci:<id>` put one human
  under two pids and the union in `compute_l1_gaps` reported him as "present in cricbuzz only" — an
  IDENTITY artifact posted to the VALUE tab. The chain is now pure id→id: cricbuzz id → (derived
  bridge) → cricinfo id → ESPN's `athlete.id` → the pid ESPN's row already carries. **A duplicate
  cricinfo id is an identity question and stays one** (Needs Cricinfo ID), never a value row.
- **Identity comes ONLY from `registry/cricbuzz_bridge.json`** — derived from performance
  fingerprints + the dismissal join, never names, tier = number of distinct confirming matches
  (≥1 cross-check, ≥2 before a CB-only field may CREATE points). The bot READS it; deriving is
  `python3 registry/cricbuzz_bridge.py --derive --from-map` out of band, so a scoring run can never
  mutate identity while it publishes points. Unbridged CB players never enter the witness view.
- **⛔ RUN IT AS `--derive --from-map`. The corpus used to lag the pin ledger** (fixed 16 Aug 2026).
  The pin ledger knows every cb-match ⇄ ESPN-event pairing; `--derive` took HAND-TYPED `--pair`
  args and nothing joined them, so the corpus drifted behind and stayed there: **83 of 94 pins
  derived, 11 not**, and 6 of the 12 `cb:` rows on "Needs Cricinfo ID" were ONE of the eleven
  (cb154370, CPL Guyana v Jamaica, 14 Aug) whose whole XI Layer A pairs 22/22. `--from-map` also
  reads ESPN play-by-play out of the bot's own `WC_CACHE_DIR` (the two modules cached the same
  bytes under different names, which is why every derive re-fetched), and re-derives EVERY pin —
  a logic change reaches a stored confirmation only if its match is derived again.
  `tests/test_cricbuzz_bridge.py::test_the_derive_corpus_does_not_lag_the_pin_ledger` now fails if
  a pin with an ESPN event is left underived. The one standing exception is cb145088/espn1521203,
  Hundred W 26 Jul, *abandoned without a ball bowled* — CB serves a page with no `scoreCard`.
- **⛔ TWO FEEDS NAMING DIFFERENT FIELDERS IS A VALUE DISAGREEMENT, NOT AN IDENTITY ONE.** Layer B
  converted one into the other and it cost three players their bridge. Measured over all 92 pinned
  matches with cached payloads: 587 Layer-B pairs = **534** restating Layer A + **49** genuinely
  new (the payoff — a substitute or pure fielder Layer A can never reach) + **4** contradicting
  Layer A *inside the same match*, every one of them CB crediting a catch to X and ESPN to Y
  (`c Will Jacks`/`c Vince`, `c Lhuan-dre Pretorius`/`c Livingstone`, `c Grace Harris`/`c Higham`,
  `c Towhid Hridoy`/`c Mathew`). Each single claim revoked a player who had 8, 8, 8 and 2 matches
  of fingerprint evidence, and put Will Jacks, Lhuan-dre Pretorius and Grace Harris on the identity
  tab as unanswerable questions. `layer_b` now refuses a join that contradicts Layer A in that same
  match (and the mirror: a fielder id Layer A gave to another cricbuzz id — 0 of 49 today, there so
  the class cannot return through the other door). Cost: **0 of the 49**. The disagreement is not
  lost — it is already a per-player `catches` diff on the Recon tab, which is where a value
  question belongs. `derive_match` reports it as `fielder_disputes`; `layer_conflicts` is now empty
  by construction and stays as a tripwire.
- **A human's answer to a `cb:` row is `--adopt`, not a name alias.** `read_needs_cricinfo` files a
  filled-in id as `ci:<id> -> [NAME]` and builds its extra alias only for `slug:`/`uncapped:` pids,
  so a `cb:` row's cricbuzz id was DISCARDED — cb:12163 and cb:10693 were both answered on the live
  tab and both still resolved UNKNOWN, regenerating the same row on the player's next catch.
  `cricbuzz_bridge.adopt()` records the answer as a `manual` confirmation. It does **not** trump
  derived evidence (a contradiction revokes both, as always) and every manual answer collapses into
  one match slot, so a hand-typed id is **tier 1 — a cross-check, never on its own licence to
  CREATE points** from a Cricbuzz-only field. The hook that would call it from a run is UNAPPLIED
  (`scratchpad/cbbridge_patch.md`); ⚠ applying it makes a scoring run write the store, so
  `registry/cricbuzz_bridge.json` must join the workflows' `LEDGERS` list at the same time.
- **Cold start is real and by design**: coverage is 0% entering a season's first two matches, 48%
  at match 3, 88–100% from match 4 (measured over 16 LPL matches). Zero bridged ⇒ "cannot witness"
  ⇒ single-feed flag. Because of that, **"ESPN has a row and Cricbuzz doesn't" is NOT reported** when
  the witness is Cricbuzz — it is dominated by "not bridged yet", and 22 unanswerable rows a match
  is exactly the flood rule E exists to prevent. The mirror ("Cricbuzz saw a performance ESPN
  missed") IS reported: that is the class that published 4 pts against 110 earned.
- **⛔ ABSENCE ≠ ZERO.** Cricbuzz writes `None`, never 0, for a field it could not establish —
  `maidens` on The Hundred is hard-ignored (a verbatim copy of `dots` on 13/13 bowlers, ~816
  fabricated points), `dots` when its completeness gate fails, `balls` when a bowler row has neither
  balls nor overs. `_l1_field_material` / `_l1_pair_gaps` / `recon_gaps` all SKIP a `None`, and the
  whole-match "use S1" seed refuses to write one — otherwise an owner's own approval would zero a
  real ESPN figure. A missing KEY still defaults to 0 (blank_perf guarantees every key).
- **⚠ S1 IS POSITIONAL.** `recon_overrides.json` records `"S1"`, not which feed S1 was. Flipping
  `cricbuzz_series` on a tour that already has approved S1 overrides retroactively changes what they
  resolve to (10 such rows exist today, on MLC/Hundred/LPL matches). `run_tour` SHOUTS this at tour
  start; the Recon tab's S1 header names both feeds and a Cricbuzz row stamps the feed into its own
  S1 cell. Treat the flip as an owner decision about money, not a config tweak.
- **`build_registry.py` ERASES `cricbuzz_id`/`cricbuzz_tier` from `players.json`** (it rewrites the
  file wholesale). `cricbuzz_bridge.json` is the durable store, players.json only its mirror —
  `tour_sync_finalize` now re-runs `--apply` after `build_registry` (idempotent, no network).
- **The two hosts' User-Agents are OPPOSITE and must never be unified.** ESPN genuinely 403s a
  browser UA. Cricbuzz does **not** require one: measured 13 Aug 2026 on
  `/live-cricket-scorecard/157138/x`, browser UA / bot UA / no UA header all returned 200 and
  byte-identical 609293 B. The browser UA in `cricbuzz.py` is defensive only; the load-bearing rule
  is directional (never pass it to an ESPN fetcher, never "tidy" `ESPN_UA` into it), and
  `tests/test_cricbuzz_l1.py` pins that `ESPN_UA` is identical in every module that carries it.

## ⛔ The cricbuzz↔our-match PIN (14 Aug 2026) — matches had no shared key and no ledger
Players have a shared key (ESPN `athlete.id` IS the cricinfo id) and, where they don't, a DERIVED
bridge with a confirmations log. **MATCHES had neither**: `cricbuzz.resolve_match_id` paired on
normalised team names + date and RE-DERIVED ON EVERY RUN, with nothing recording that the pair had
ever been made — so a rename upstream could silently re-pair or un-pair an already-SETTLED match
and no ledger would show it. Two naming conventions broke it in one week, **both reported as "no
unique cricbuzz match"**, which reads as Cricbuzz not having the fixture and sent the diagnosis the
wrong way twice: ESPN `St Lucia Kings` vs Cricbuzz `Saint Lucia Kings` (−2 of 5 completed CPL
matches' second witness) and ESPN `MI London (Men)` vs `MI London` (−31 of 31 Hundred Men's).
- **`registry/cricbuzz_match_map.json`** — written on the first successful pair, read thereafter.
  Key = `<cricbuzz series>|<our date>|<slug>+<slug>`. **The series id is in the key on purpose**:
  `match_key_of` strips gender qualifiers, so the Hundred's men's and women's fixtures between the
  same franchises on the same day collapse onto one key there (the collision that had just disabled
  the completed-ratchet's LIVE arm on 60 of 92). A Cricbuzz series is gender-specific — 11493 men /
  11504 women — so all **32** same-day M/W double-headers stay apart by construction.
  Backfilled from cached payloads: **LPL 24 · Hundred M 32 · Hundred W 32 · CPL 6 = 94 pins,
  0 revoked, 94/94 carrying their ESPN event id**. `--verify` re-derives all 94: 94 agree.
- **`confirmations` is the fact log; `pins`/`revoked` are a pure function of it** (`compile_pins ∘
  confirmations_log`), no clock read anywhere, so a re-derive reproduces the file byte for byte —
  same discipline as `cricbuzz_bridge.json`. Regenerate, never hand-edit.
- **FOUR contradiction directions, all refusing BOTH sides, never last-wins.** key→2 cb matches ·
  cb match→2 keys · **ESPN event→2 cb matches** · key→2 ESPN events. The third was the one missing
  in the first draft and is the one that actually bites: a rename that RE-pairs moves the slug (so
  direction 1 is blind) and changes the cb id (so direction 2 is blind) — reproduced with two keys
  under ESPN event 1534181, cb154347 vs cb999002, **both pinned, 0 revoked**. Pass `espn_event=`
  (see the unapplied hook patch) and the pin is rename-proof; without it the key is name-derived.
- **⛔ AN ABSENCE IS NOT A CONTRADICTION.** A later derivation that finds NOTHING (0 hits, an
  ambiguous >1, a 403 on the series page) leaves the pin STANDING. Only a DIFFERENT id contradicts —
  treating "nothing matched" as evidence would hand the rename exactly the power the pin removes.
- Refusal semantics are unchanged: None on 0 or >1 hits, so a same-day double-header between the
  same two sides stays refused (real occurrence: the CPL's three `TBC v TBC` playoff fixtures).
  A revoked key stays revoked until a human runs `--forget <key> --write`; self-healing would be
  last-wins in a costume.
- The map is in the **LEDGERS** list of all three workflows. Drop it and every run re-derives from
  names — written-but-never-read, this repo's most-repeated bug shape.
- `series_matches` is memoised per process (fresh-aware): a 31-match tour was re-fetching the same
  ~280 KB page 31 times because the bot passes `fresh=True` for every match cricsheet hasn't settled.

## ⛔ A refused ESPN card is HELD, named, and CUT OFF (13 Aug 2026)
The ball-count gate above returns `{}` on refusal — indistinguishable from "ESPN has no data", so on
an ESPN-only tour the match fell through the no-data guard, `continue`d, and was **absent from the
sheet entirely**: retried forever, no cutoff, one line of stderr inside a **green** workflow run.
- **How often does it fire? Essentially never, and that is the design input.** Bounded cache-only
  sweep of 123 playbyplay bodies / 71 distinct events / 6 series / T20+ODI+HUN: `deliveries −
  scorecard_balls` is **0 on 71/71 events**, 0 over-counts, 0 unverifiable cards. So a fire is never
  routine noise — which is why the answer is "hold it and make a human decide", not "tolerate a
  margin". (The Hundred Women's match that is one delivery short of cricsheet is short in BOTH ESPN
  feeds, so delta = 0 and the gate is correctly silent: this gate measures FETCH completeness against
  ESPN's own self-report, not truth. The residual HUN-W error is a different defect.)
- **`ESPN_HOLDS[event]`** records why (`page_fetch_failed` / `short_of_self_count` /
  `short_vs_scorecard`, got, expected). A good parse **releases** it — without that a healed match
  stays red forever, the written-but-never-cleared mirror of the bug the gate exists for.
- **The cutoff is the MATCH clock, not a retry counter**: `OVER_HRS_MAX(12) + ESPN_HOLD_GRACE_H(6)`
  = 18h after scheduled start. Deliberately not a persisted counter — that lives in a file the
  workflow must commit, and a failed commit would silently reset the budget so the escalation never
  arrives. Inside the budget it is a quiet `⏳ retrying`; past it, a **named `ESPN CARD` row** in the
  Recon tab (`pid = espn:<eventId>`, whole-match, not a player) and the run **exits non-zero**.
  A LIVE match NEVER escalates: `parse_espn` fetches playbyplay first and the scorecard after, so
  mid-innings the scorecard is legitimately a ball or two ahead — a fetch-ordering race, not a defect.
  An unparseable start time never escalates either (an unknown clock is not an expired one).
- **A held match is forced back to LIVE** so base points cannot freeze on a card we refused — unless
  it has already published COMPLETED, where the ratchet wins and it is flagged instead.
- The only way a short card publishes is the owner answering **S2** on that one row
  (`scope: espn_card` in `recon_overrides.json`), and it then publishes **permanently flagged**
  (`⚠ ESPN card SHORT (200/236) — scored anyway, approved`). There is no "score it anyway after N
  tries": that is the one option that can put a wrong number in front of money with nobody deciding.
- Side fix: `overrides_by_match`'s orphan guard now skips `*` and `espn:*` pids. `*` (the ALL-L1
  whole-match seed) was already latent — every match-level seed would have been shouted as an
  orphaned pid; it has never fired only because the ledger holds 0 match-scope rows.

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

## ⛔ Un-attributable performances — the check pid-minting disarmed (14 Aug 2026)
Minting `ci:<athlete.id>` above had a cost nobody priced: the review surface for a player who
lands in **no squad slot** went dark. The old surface was the `leftover` emit — feed rows the
matcher resolved to NO pid, published with team `?` / `In Squad List = N`. A minted pid means a
row is never a no-pid leftover, so on an ESPN-sourced tour `unresolved` is empty ⇒ `leftover` is
empty ⇒ **that emit is dead code**. MEASURED: `In Squad List = Y` on **2228/2228** rows across the
Hundred M/W + CPL tabs, zero N; the same tab carried 9 `?` review rows on 11 Aug. It looked like a
win only because a separate bug (`short_of` stripping "women" but not "men") was fixed the same
week. The one surviving path, the auto-add, ends in `if es not in match_shorts: continue` — a bare
drop, with a comment claiming the leftover pass would still show him. It does not. **Reproduced:
a 77-run / 3-wicket performance published as 0 rows, 0 review entries, 0 log lines, and the match
still went COMPLETED.**
- **`find_unattributed(perf, assigned, team_players, leftover)`** re-arms it from the FAR END of
  the pipeline: not "did the auto-add fall through?" but "which PLAYED feed rows are in neither
  `assigned` (under a key `emit` actually visits) nor `leftover`?" Outcome-shaped on purpose — a
  cause-shaped check dies again the next time someone adds a new way to fall out. Matches on **pid
  AND normalised name** (`merge_perf` returns a NEW dict when two spellings fold into one pid, so
  an `id()` diff would report a merged player as dropped), and re-applies the `is_junk` filter
  (`Player Not Found`/`sub` are excluded from the pool by design and would otherwise block
  every match).
- **The match is HELD.** `classify_match_status(unattributed=…)` is tested FIRST, ahead of even
  the `cs_path` branch: cricsheet posting cannot un-drop a player the pipeline never attributed,
  so letting that early return swallow it recreates the silence. LIVE, or `COMPLETED_FLAGGED` if
  the match already published (the ratchet still wins). `classify_recon_state` → `L1_OPEN`.
- **The row is published but NOT SCORED.** `emit(..., attributed=False)`: real stats, `Played=Y`,
  team `?`, `In Squad List = N`, and every points cell **BLANK, never 0** — a 0 there is an
  absence wearing the clothes of a value. `record_settlement` is skipped, so an unattributed
  performance can never enter the WRITE-ONCE money baseline.
- **It surfaces in "Needs Review", NOT the Recon tab.** Recon answers "which of two numbers is
  right?" (S1/S2/Manual); Needs Cricinfo ID answers "who is this?" (type an id). This is neither —
  identity is certain (athlete.id IS the cricinfo id) and there is no second number, only a missing
  row. The open question is *which side was he on*, whose answer is a **team code**, and Needs
  Review is the tab that takes one: answer `New` with the Team column filled → `register_new_player`
  → `new_players.json` → the injection loop picks him up next run. The match-level obligation is met
  regardless: the gate holds the match and the **Recon Flag NAMES him on every row**, so it is not
  possible to miss by watching the wrong tab. (If you want it in the Recon tab instead, that is a
  one-line move — but an S1/S2 dropdown cannot answer "which team".)
- **ACK cannot silence it.** `open_review_items()` keeps an `unattributed` row even when ACKed:
  ACK is answered against a NAME, this row is open against a MATCH being held. Otherwise a `New`
  answer carrying a still-unresolvable team would delete the only actionable row while the match
  stayed LIVE forever with nothing to click.
- **`In Squad List` means PROVENANCE now**, not "did we have a squad" — the old meaning could only
  ever say Y, because the auto-add PERSISTS every feed-discovered player into `new_players.json`
  and the next run re-injects him as an ordinary squad member. `Y` = the announced squad file ·
  `auto` = only the feed ever put him on this side · `new` = a human typed him into Needs Review ·
  `N` = on no list anywhere (the unattributed review row). No consumer reads the column (grep: the
  draft app never mentions it), so only the values changed — header and position are untouched.
- Regression (same-day rehearsal, gsheet/settlements stubbed): **CPL 226 rows / 6 matches and the
  Hundred Men's 1008 rows / 31 matches are byte-identical before vs after except the 3 CPL cells
  that are now honestly `auto`** (Rivaldo Clarke, Kevlon Anderson, Dale Phillips). Total FP
  unchanged, every completed team-side still 11 `Played=Y`. Exposure on the live tours today is
  **0** — which is the design input: a fire is never routine noise, so the answer is "hold it and
  make a human decide", not "tolerate it". Pinned by `tests/test_unattributed.py` (16 tests,
  all 16 fail on the pre-change file), including an end-to-end `run_tour` pair — one un-attributable
  man, one attributable — so the test can report clean as well as dirty.

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
