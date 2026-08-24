# Handoff — wwc-points-bot / wwc-draft, 2026-08-24

Self-contained. Assumes no prior conversation. Every number below was measured, not estimated;
where something is inferred it says so. Supersedes the 2026-08-20 handoff.

## The system

- **`~/wwc-points-bot`** — Python (`wc_fps_to_csv.py`, ~6000 lines). Scores D11 fantasy points into
  a Google Sheet via GitHub Actions. Owns the identity registry in `registry/`.
- **`~/wwc-draft`** — Next.js draft app. Reads the sheet by gviz, joins points **by `Player ID`
  (pid)**, never by name. Vercel + Turso.
- **`~/cricket-auction-helper`** — Next.js auction app. Its `src/lib/squads/*.ts` files are the SEED
  that `tour_sync` reads, so an identity fixed only in the bot gets re-broken by the next sync.
- **Real money** is settled on these numbers with one friend. The owner's standard is zero margin of
  error, and he has explicitly rejected sampling/thresholds to reduce recon volume.

### Feeds — cricapi was REMOVED 2026-08-20 (`ab8583d`)
| role | feed | notes |
|---|---|---|
| base / provisional | **ESPN** `site.api.espn.com` | full scorecard + ball-by-ball. `athlete.id` **IS** the cricinfo id. Keyless. |
| L1 second witness | **Cricbuzz** `www.cricbuzz.com` | whole card ⇒ all 14 `RECON_L1` fields get a second number. **Per tour**, via `cricbuzz_series`. |
| L2 official | **cricsheet** | gold source, 1–5 day lag. Reconciles against the FROZEN L1 baseline. |

**No API keys exist anywhere.** `CRICKET_API_KEY`, `CRICKET_API_KEY2`, `TOUR_SYNC_API_KEY` are
deleted secrets; `resolve-series.yml` is deleted; `cricapi_series` is removed from all 13 tours;
`frozen_tours.json` is re-keyed onto `espn_series`. Old code is read-only in `legacy/cricapi/`.
**There is no quota to ration** — dormancy and `frozen_tours` now only save wall-clock.

⚠️ **A tour with no `cricbuzz_series` has NO second witness** — nothing to fall back to. Every match
publishes `COMPLETED_FLAGGED · "single feed (ESPN only)"` and cricsheet at L2 is its first
cross-check. **9 of 13 tours are in this state**, including the live ENG v PAK Test (35/35 rows
flagged, L1 column blank on every one). Only LPL 12316, CPL 12123, Hundred M 11493, Hundred W 11504
have L1. This is the standing gap against "nothing goes without L1 & L2".

## ⛔ Constraints — violating any of these is worse than doing nothing

- **NEVER send a browser/Mozilla User-Agent to `site.api.espn.com`.** Its WAF allowlist keys on the
  substring `github.com`. A browser UA gets a 403 every fetcher swallows, so it is indistinguishable
  from "ESPN has no data". `www.cricbuzz.com` is the OPPOSITE — it wants a browser UA. Never unify.
- **Name matching is FORBIDDEN as an identity decider.** It corrupted 20 live rows once. It returns
  None on ambiguity BY DESIGN. Names may only ever *raise a question*.
- **Identity fixes go in the SHARED registry** (`registry/manual_ci_bridges.json`,
  `team_aliases.json`), never a per-app local map. And fix the auction squad seed too, or
  `tour_sync` re-breaks it.
- **`registry/settlement_snapshots.json` is WRITE-ONCE** — the record of what money was settled on.
  Never edit or delete a row. Corrections are ADDITIVE and are the owner's decision.
- **NEVER run `wc_fps_to_csv.py` locally without a populated `CRICSHEET_DIR`** — it downgrades
  cricsheet-resolved COMPLETED matches back to LIVE across every tour.
- **The bot runs in Actions off `origin/main`** — commit AND push or the change does not exist.
- **In `wwc-draft`: `git pull --rebase`, stage ONLY files you touched, NEVER `git add -A`** —
  another session commits there concurrently.
- **gh**: personal repos push as `nishantsingodia` — `gh auth switch --user nishantsingodia` first.
- **Never pipe pytest through `tail` in an `&&` chain** — it masks the exit code.
- ⛔ **Do NOT add `cricbuzz_series` to a tour that already has approved S1 overrides.**
  `recon_overrides.json` stores only the LETTER "S1", not which feed it was, so switching the
  witness retroactively changes what those approvals resolve to. That moves settled points.

## Recon model (owner-locked, do NOT re-derive)

`Match Status` and `Recon State` are two INDEPENDENT axes. See `CLAUDE.md` for the locked spec.
The two facts most often got wrong:
1. **The L2 baseline is the FROZEN reconciled-L1 value**, read from `settlement_snapshots.json` —
   never a recomputation of the provisional cut. Recomputing is the root cause of the phantom
   `dots 0→N` revisions that once overwrote cricsheet's correct figures.
2. **Base points freeze at the L1_DONE transition**, which is NOT the same event as "published
   COMPLETED". The completed-publish ratchet is its own ledger (`completed_matches.json`).

## Shipped 2026-08-20 → 08-24 — do NOT redo

| commit | what |
|---|---|
| `cabba80` | Malformed `manual_ci_bridges.json` entry (a bare `"Name": "id"` string) had killed EVERY scoring run for ~2.5 days. Loader now names and skips a bad entry instead of dying. |
| `ab8583d` | **cricapi removed** (PR #1). Zero executable refs; keys/workflow deleted; `frozen_tours.json` re-keyed; TOUR CONTROL gate re-keyed onto the tour NAME (which is what makes an ESPN-only tour approvable at all). |
| `902a41a` | Scoreboard cache poisoning — an unsettled scoreboard was cached and never re-fetched. Read-heal + write-guard. **Recovered +4,604 FP / 136 rows.** |
| `77d75c2` | Every cricbuzz pin was being created UNANCHORED — `cb_match_perf` never forwarded `espn_event` to `resolve_match_id`. |
| `cb39b26` | **Auto-anchor**: `anchor_ci_pid` folds a `ci:<id>` pid through `crosswalk.json` to its cricsheet id and indexes `CS2PID`. Players living only in `new_players.json` (which `build_registry` never reads) had no cricsheet id, so the official card missed and raised a false IDENTITY row on every debut. `CS2PID` 737 → 766, 56 pids anchored. |
| `a8bc145` | **`complete_baseline`**: a frozen baseline missing a scoring-critical key was worse than none — it won over the recompute but could not be scored, so the TOTAL read `pts ?→?`. Fills only holes (`dismissed` self-derived from the frozen `dismissal` text; `lbwb`/`dro` from the recompute) and ONLY if the result re-scores to the settled total. 1086 rows across 4 tours. |
| `4fa7387` | **Split the two Matthew Fishers.** MD (ci:639080, cs 8f2dfebf, England) v MJ (ci:1129635, cs 7068c81e, New Zealand). Three squads announced the bare "Matthew Fisher", which resolved to MJ. Squads now name the man; each full name is bridged; `NAME2CI["matthew fisher"]` is now **None** so the bare form raises a question. |
| `106c2c2` `4b573e8` | **Gate on a pid-less squad slot** — the one identity failure with no review row anywhere. 11 of 35 Test rows published a blank Player ID silently. Now raises a Needs Cricinfo ID row naming the auto-added candidates on the same team. Three bridged from the ESPN roster (Ollie Robinson ci:527776, Emilio Gay ci:1148593, Shan Masood ci:233901). |
| wwc-draft `d88dd33` | Draft rows 10454 (London Spirit) + 10799 (England Test) carried the New Zealander's pid. pushap had FIELDED 10454 in contest 160 — the exact match MD Fisher played — so the slot scored 0 instead of 38. |
| auction `b8b5c80` | Same Fisher fix in the auction squad seed, so the next `tour_sync` cannot re-introduce it. |
| `7d20c01` `b393795` | *(another session, 24 Aug)* The Source column asserted "dots unverified, awaiting cricsheet · cricbuzz cross-checked" — two halves never both true, because the dots clause was written ~40 lines BEFORE the Cricbuzz fetch. Now one `source_status()` called after everything is known. Also fixed `if witness == "cricbuzz"` comparing against a literal assigned 4 lines above (always true ⇒ a match with NO Cricbuzz card advertised "cricbuzz cross-checked (0 players)"), and deleted the dead `RECON_L1_SINGLE`. |

**Verified live on the 2026-08-24 08:41 run:** Recon Review **92 → 19 rows**, zero IDENTITY rows,
zero `pts ?→?`. Log shows `auto-anchored 56 new_players pid(s)` and
`COMPLETED 22/24 partial frozen baseline(s) … each verified to re-score to its settled total`.

**Test state: 592 pass, 4 fail.** All four pre-existing and data-driven, not code:
`test_cricbuzz_match_map::test_the_committed_map_covers_the_four_live_tours`,
`test_cricbuzz_bridge::test_the_derive_corpus_does_not_lag_the_pin_ledger` (unanchored CPL pin data),
and 2 × `test_identity_split_detector::test_a_long_legal_name_over_a_squad_placeholder_raises_the_question`
(the placeholder twins those params expect were bridged away by another session).

⚠️ **A second session is committing in this repo.** Check `git log` before assuming a change is
yours, and `git pull --rebase` + stage only your own files.

## ⚠️ CLOSED — do not re-raise these

- **"Duplicate settled rows / Atkinson 618 FP / Dale-Glenn / Gardner / Kumara."** All benign or
  imaginary. The published tabs carry **exactly one row per player per match** — checked all five —
  so **no money was ever double-counted**. The extra rows live only in the write-once audit ledger
  and are the expected residue of a pid CORRECTION: the guard is `(match_key, pid)`, so a corrected
  pid settles the same match a second time. Four real cases (Atkinson 618, Usman Khan 74, Fisher 38,
  Nathan Edward −1); three already fixed. Dale/Glenn and Gardner/Kumara are **not duplicates at all**
  — different people.
  ⛔ **The trap:** `settlement_snapshots.json`'s `full` field is the display label FROZEN at settle
  time. Group rows by name and a pid whose label was corrected looks exactly like two people, so the
  detector turns past FIXES into fresh findings. Resolve identity by **cricsheet id** only.
  `{played: false}` is byte-identical for every non-playing squad member, so "identical fields" is
  also a bad signal on its own — require points > 0.
- **The `ESPN_ONLY_MIGRATION.md` blocker table (B1–B4, R1).** All fixed before the flip; the line
  refs have drifted. That file made an earlier session report four live blockers that did not exist.
  It now carries a DONE banner.
- **`scratchpad/cbbridge_patch.md`** — the file does not exist. `adopt()` is wired and
  `cricbuzz_bridge.json` is in LEDGERS in all 3 workflows.

## OPEN WORK — suggested order

### 1. Give the 9 witness-less tours an L1 witness
The single biggest correctness gap. Find each tour's cricbuzz series id and set `cricbuzz_series`.
⛔ Read the S1-letter constraint above first — for a tour with existing approved S1 overrides this
moves settled points, so it is the owner's call per tour. The ENG v PAK Test has no approvals yet,
so it is the safe one to start with.

### 2. The 8 remaining pid-less Test squad members
Babar Azam, Shoaib Bashir, Sajid Khan, Aamir Jamal, Ubaid Shah, Awais Zafar, Ghazi Ghori (+ Ollie
Pope/Brydon Carse are fine). They have not played, so they cost nothing today — but the day one
plays he splits into a blank squad row plus a scored auto row. The new gate now lists them in
**Needs Cricinfo ID** each run. Bridge them from the ESPN roster (`athlete.id` IS the cricinfo id),
never from a name.

### 3. LPL's 986 no-`fields` rows still compare against a RECOMPUTED baseline
22 of 24 matches. Each logs `24 player(s) settled before field-level freezing`. The recompute is
faithful for LPL specifically — an independent re-score reproduced the published total exactly
(30,408 FP) — so this is a provenance weakness, not a wrong number. The 88 field-frozen rows are
fixed (`a8bc145`). Sub-bug: the stderr line misnames the condition for rows never settled at all.

### 4. Two refused baseline completions — real findings, awaiting approval
```
Eshan Malinga        completing scores 55, settled on 49   (+6)
Thomas Fraser Rogers completing scores 33, settled on 27   (+6)
```
Both +6 = one run-out, and both appear in Recon Review as `ro 0→1`. So the published number missed
a run-out that cricsheet credits. Approving those two L1/L2 rows applies it.

### 5. Settlement `corrections` array — audit hygiene, NOT money
Now that the sheet is known never to double-count, this is about making `/audit` reconcile cleanly.
Append `{match_key, superseded_pid, canonical_pid, reason, ts}` and fold at READ time in
`wwc-draft/lib/points.ts`. Note `getSettledRowsForMatch` builds its map with plain `out.set()` —
last-wins, no `pickDupRow` — so a same-name pair silently overwrites today.

### 6. Ledger persistence can silently drop in a GREEN run
`.github/workflows/wwc-points.yml` (+2 siblings): `git pull --rebase --autostash ... || true` then
push. On a rebase conflict in a ledger JSON, `|| true` swallows it, HEAD reverts to origin, push
prints `Everything up-to-date`, exit 0, workflow green — and the run's ledger is gone. Conflicts are
the NORMAL outcome when two overlapping runs append to the same sorted array, and `live-lineup`
ticks every 5 min. `settlement_snapshots.json` and `completed_matches.json` share the string, so
both witnesses die together — **this is how a COMPLETED match returns to LIVE.**

### 7. `record_completed` fires before the row exists
It runs before `emit` and long before `write_to_gsheet`, whose `ws.clear()`/`ws.update()` are
unguarded; `main` catches the tour exception and `save_completed()` still persists. Reproduced with
a gspread 429: 0 rows published, ratchet stamped, next run publishes `COMPLETED_FLAGGED` with an
un-scored performance while the draft treats it as final.

### 8. `build_registry.py` is unusably slow and almost never runs
Observed **>35 min with no progress output** (24 Aug). It runs ONLY from `tour-sync.yml`, on manual
dispatch, and only when a tour was applied — so a new bridge does NOT reach `players.json` on the
4-hourly schedule. Workaround in use: put the alias in `registry/new_players.json` too, which
`load_new_players()` reads at startup. Worth profiling and adding progress output.

### 9. Smaller, still open
- **Confirm the Will Jacks guard** — `layer_b` should refuse a join contradicting Layer A in the
  same match. Never verified.
- **`lib/pid-map.json` maps `slug:matthew-fisher` → `ci:1129635`.** Which man that slug meant is not
  recoverable from the file; guessing repeats the original mistake.
- **`data/player-photos.json` has a photo under `ci:1129635`.** If it is MD's face it is on the
  wrong pid — needs a human to look at the image.
- **60 published CPL rows carry `uncapped:` pids** (14 players, 0 of whom have played). Latent only.
- **Numbers/scoring lens never ran** — there is still NO independent check on the point arithmetic.
  Run it as a plain agent, not inside a workflow.
- **Rewire `build_registry.best_match`** onto the shared model (last private scorer).
- **Delete the legacy `SequenceMatcher` fallback in `best_team`** — 20 correct / 0 wrong on 365
  probes, and those 20 are only 3 people (13 = a normalisation bug in STORED aliases, 7 =
  `Shahnawaz Dhani`/`Dahani`, which belongs in `manual_aliases.json`). Fix those and it deletes free.
- **Remove `active_until`** from LPL + both Hundreds once Dream11 agrees (held tours re-score every run).
- **Stale cricapi comments remain inside `wc_fps_to_csv.py`** (e.g. the `live` list comment near
  `is_over`). Harmless but misleading.

## Needs the OWNER (do not guess these)

1. **19 Recon Review rows** unanswered — all `L2 · official revision — approve to apply`, all single
   real field diffs. Includes the two +6 run-outs in item 4.
2. **Which tours may get a Cricbuzz witness** (item 1) — per tour, because of the S1-letter problem.
3. **CPL playoffs** must be hand-added (TBA fixtures are dropped by design, never backfilled).
4. **`data/player-photos.json`** — is the `ci:1129635` photo MD or MJ Fisher?

## Verification commands

```bash
cd ~/wwc-points-bot && python3 -m pytest -q          # expect 585 pass, 4 known data fails

# prove no cricapi remains in the live path
grep -rn "cricapi" --include='*.py' --include='*.yml' . | grep -v legacy/ | grep -v '^\./tests/'

# which tours have an L1 witness
python3 -c "
import json
for t in json.load(open('tours.json'))['tours']:
    print(f\"{t['name'][:44]:46} espn={t.get('espn_series') or '-':9} cb={t.get('cricbuzz_series') or '-- NONE'}\")"

# identity, the ONLY safe way: by cricsheet id, never by label
python3 -c "
import json
cx=json.load(open('registry/crosswalk.json')); ci2cs={v:k for k,v in cx['cs2ci'].items()}
P=json.load(open('registry/players.json'))['players']
for pid in ('ci:902447','ci:823509'):
    print(pid, ci2cs.get(pid[3:]), (P.get(pid) or {}).get('display'))"

# read the live sheet, no credentials needed
SID=$(grep -o 'spreadsheets/d/[A-Za-z0-9_-]*' ~/wwc-draft/.env.local | head -1 | sed 's#.*/d/##')
curl -s "https://docs.google.com/spreadsheets/d/$SID/gviz/tq?tqx=out:csv&sheet=Recon%20Review&headers=1"

# the last bot run's log
gh run list --workflow=wwc-points.yml -L 3
gh run view <id> --log | grep -E "auto-anchored|partial frozen|NOT completed|sources:|EMPTY"
```

## Reading order for a fresh session

1. `CLAUDE.md` — the locked recon model, the identity spine, and the CURRENT feed architecture.
   Non-negotiable, and the only doc kept in sync with the code.
2. This file.
3. `RUNBOOK.md` — the settle-money checklist and the incident table.
4. `TOURS.md` — how a tour gets added and what it needs.
5. `wwc-draft/CLAUDE.md` — only if touching the app.

Everything else in this repo (`MASTER_PLAN.md`, `ESPN_ONLY_MIGRATION.md`, `RECON_*.md`,
`NAME_MATCH_AND_ISSUES_CRITICAL.md`, `STREAMLINE_PLAN.md`, `DATA_SOURCE_EVAL_20260813.md`) is
HISTORY and carries a superseded banner. Trust the code over all of it.
