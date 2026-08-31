# Adding a tournament's points

This bot can track **multiple tournaments at once** — each one writes its own tab in
the Google Sheet, all from the single 2-hourly run. Tours are listed in **`tours.json`**.

> ## ⚠️ Updated 24 Aug 2026 — the two-paths split below is OBSOLETE
>
> cricapi was removed on 20 Aug 2026 (`ab8583d`). **There is no longer an A-path/B-path
> distinction, and `cricapi_series` no longer exists** — it was deleted from all 13 tours in
> `tours.json`. The single thing a tour needs in order to be scored is **`espn_series`**, which
> Tour Sync resolves automatically.
>
> **The one way in:** type the name in Column A of the `TOUR CONTROL` tab. The daily Tour Sync
> workflow (`gh workflow run tour-sync.yml -f dry_run=false`) resolves it on ESPN, builds fixtures
> + full squads, commits both repos and deploys the draft. Then flip the tour's `TOUR CONTROL` row
> to `yes` — that human gate is what makes the bot score it and write its points tab.
>
> The gate is keyed on the tour **NAME**, not on a series id. That change is what killed the old
> failure mode where a blank `cricapi_series` got no approval row at all, so the tour was
> permanently un-approvable and silently skipped on every run.
>
> **What a tour still needs, in order:**
> 1. `espn_series` — non-blank, or it cannot be scored at all (the ingest verify gate fails on this).
> 2. `TOUR CONTROL` = `yes` — the human approval gate.
> 3. `cricbuzz_series` — the L1 second witness on all 14 fields. **The thumb rule is that a tour
>    ships with all THREE feeds — ESPN + cricbuzz L1 + cricsheet L2 — so treat this as required, not
>    optional.** Without it every match publishes `COMPLETED_FLAGGED · "single feed (ESPN only)"`
>    and cricsheet at L2 is the first cross-check it ever gets. You do not normally set it by hand:
>    `tour_sync.resolve_cricbuzz_series` proposes it at ingest and adopts it only after validating
>    the id against cricbuzz's own fixture DATES (ETPL 2026 → 12870, 21/21 dates matched). It stays
>    in the committed `tours.json`, never a sheet cell, because flipping it is a settled-points
>    mover. ⛔ Do NOT add it to a tour that already has approved S1 overrides — see CLAUDE.md.
>    Every tour's three feeds are reported per-row in the **`Feeds`** column of `TOUR CONTROL`.
> 4. `format` — `T20` (default) / `ODI` / `HUN` / `TEST`. Drives which scorer runs.
>
> The rest of this file is the manual flow, still valid except that step 1 asks for one id, not two.

## The flow — when Nishant says *"add &lt;tour&gt;'s points"*

**All Claude needs from you is the tournament name** (plus squads only if it's for an
auction). Claude does the rest:

1. **Find the series ID** (one, not two — the cricketdata/cricapi lookup is gone)
   - **ESPN/cricinfo** — everything: match list, scorecards, ball-by-ball, XI, team attribution.
     Find the series page on espncricinfo; the URL is `.../series/<slug>-<SERIES_ID>/...` and the
     number is the id. (A web search for "espncricinfo &lt;tour&gt; scorecard" surfaces it.)
     `tour_sync.py`'s `resolve_espn_series` does this automatically and VALIDATES the candidate
     against its dated scoreboard by team-match, so prefer letting it resolve rather than pasting.
   - **Cricbuzz (the L1 witness — required in practice)** — normally resolved and date-validated
     for you by `tour_sync.resolve_cricbuzz_series`. Only hand-resolve it (the series page URL on
     cricbuzz.com carries the id, or `cricbuzz.series_candidates`) when that comes back empty, and
     only on a tour with no approved S1 overrides yet.
2. **Confirm two things with you**
   - **Tab name** (default: a short tour name).
   - **Squads?** — *full squad list* (needed for ownership / an auction → players get `In Squad List = Y` and DNP rows) **or** *featured-players-only* (no list; the sheet lists whoever actually played). For a full list, Claude sources the announced squads into `<tour>_squads.json` (same format as `squads.json`).
3. **Register the tour** — append one object to `tours.json`:
   ```json
   {
     "name": "Men's T20 WC 2026",
     "espn_series": "<espn series id>",
     "cricbuzz_series": "<cricbuzz series id — the L1 witness; auto-resolved at ingest>",
     "tab": "MT20WC POINTS",
     "squads": "mt20wc_squads.json",
     "format": "T20"
   }
   ```
   - `squads` is optional — omit it for featured-players-only mode.
4. **Build the player registry** (identity — do this once per tour, locally):
   ```bash
   python3 build_registry.py "<tour name substring>"   # extends the GLOBAL registry/players.json
   cat registry/UNMAPPED_<tab-slug>.txt                 # eyeball the handful it couldn't resolve
   ```
   - Identity is **global & permanent** — players already in `registry/players.json` from
     a prior tour are reused automatically (zero rework). The harvester only adds new
     players / new spellings (ESPN ids + cricsheet ids + every feed spelling).
   - For a name the auto-matcher genuinely can't link (a player whose feeds use unrelated
     names, e.g. "Tajinder Singh" = "Tajinder Dhillon"), add one line to
     `registry/manual_aliases.json` and re-run. This is the **once-and-for-all** map.
   - If the draft app uses this tour, push the ids into it:
     `python3 registry/backfill_draft_pids.py` (adds `pid` to wwc-draft `players-raw.json`).
   - **Register this tour's `espn_series` in the draft app's `data/espn-series.json`** under its gender (`"W"`/`"M"`), same id as here. **Auto-ingest does this for you** (`tour_sync.apply_to_repos`); it's only manual for a hand-added tour. The draft fetches the announced XI **and scores the LIVE H2H** straight from ESPN (`getEspnLineup` / `getLiveMatchPoints`); if the series id is missing there, lineups fall back to the sheet AND live points show 0 (the 22 Jul Hundred bug). Completed points still come from the sheet either way.
   - **Sync the draft's registry mirror** (`lib/registry-players.json` ← `registry/players.json`) so the draft can resolve an ESPN player to our pid for live scoring. **Auto-ingest does this** (`tour_sync_finalize`); manual only for a hand-added tour (`cp registry/players.json ../wwc-draft/lib/registry-players.json`). A stale mirror = ESPN players don't join the roster = 0 live points.
   - Commit `registry/players.json` (+ `manual_aliases.json`) — CI reads the committed file.
5. **Deploy + verify** — commit & push; trigger a run (🏏 WWC button, or `gh workflow run wwc-points.yml`); confirm the new tab fills, the **`Player ID`** column is populated, and totals look right (Source column clean, no phantom `In Squad List = N` rows for squad players). The CI run also prints any registry gaps (`UNMATCHED_*.log`) as a warning.
6. **You wire the leaderboard** — add your ownership / C×2-VC×1.5 / leaderboard tabs that
   reference the new points tab. The points tab stays the sacrosanct raw layer.

## tours.json reference

| field | required | meaning |
|-------|----------|---------|
| `name` | yes | label (shown in logs) |
| ~~`cricapi_series`~~ | — | **REMOVED 20 Aug 2026.** Deleted from every tour; the field is dead. |
| `cricbuzz_series` | yes in practice | cricbuzz series id = the L1 second witness on all 14 fields. Auto-resolved and date-validated at ingest. Absent ⇒ single-feed (ESPN only) until cricsheet. |
| `cricsheet_archive` | no | pins this tour's cricsheet zip (`"cpl_json.zip"`), or `"none"` when cricsheet does not cover the league. Omit and `cricsheet_archives.py` resolves it every run. |
| `espn_series` | yes* | ESPN/cricinfo series id (dot-balls, +4 XI, team attribution). *Omit only if you accept no dots/XI. |
| `tab` | yes | Google Sheet tab to write (created if missing) |
| `gender` | yes | `male` or `female` — so cricsheet matches the right files |
| `format` | no | `"T20"` (default) or `"ODI"` — selects the match filter + scoring ruleset |
| `squads` | no | filename of a squad JSON in this repo; omit for featured-players-only |
| `ends` | no | last match date `YYYY-MM-DD`. After `ends` + 21 days the tour **auto-freezes**: no API calls, no writes, the tab is kept with its final data. Omit to run forever. |

By default only **T20s** are scored — T20Is **and** franchise leagues like MLC (a tour can
mix formats; Tests are always ignored). **ODIs are now supported**: set `format: "ODI"` on a
tour and its match filter selects ODIs instead of T20s and the ODI scoring ruleset is used.
A tour scores exactly one format (its `format`, defaulting to `"T20"`).

## Completed / old tours
A tour stops being polled **21 days after its `ends` date** (configurable via the
`FREEZE_GRACE_DAYS` env var) — by then cricsheet has posted the official data, so the tab is
final and frozen. No more API calls or sheet rewrites for it. To retire a tour sooner just
remove its entry from `tours.json` (the tab stays in the Sheet); to revive it, restore the
entry or bump `ends`.

Each tour writes its tab independently; if one tour's feed fails, the others still run,
and a failing tour **never blanks its tab** (the run aborts before writing).

## How a tour is scored (same for all)

Source priority per completed match, recorded in the **Status** column:
1. **cricsheet** (`official`) — exact everything; overrides when posted (lags ~days).
2. **ESPN** (`provisional`) — full scorecard + ball-by-ball, cross-checked against Cricbuzz at L1
   where the tour has a `cricbuzz_series`
   dots and the +4 in-XI; runs/wickets cross-checked (mismatches flagged).
3. *(there is no third tier — if ESPN has no card the match is SKIPPED and retried next run,
   never published as a misleading all-zero COMPLETED row)*
Super-overs excluded; feed joins tolerate ±1 day; same-surname / cross-source
disagreements / unknown players are flagged in Status rather than silently guessed.

### ODI scoring ruleset

The pipeline above is identical for ODIs — only the **point rules** change when a tour opts in with
`format: "ODI"` (see the tours.json reference). ODI differs from the T20 default:
- **Duck −3** (T20 is −2).
- **Dot balls +1 per 3 dots.**
- **Maiden over +4** (T20 is +12).
- **Wicket hauls at 4w / 5w / 6w = +4 / +8 / +12** (T20's hauls trigger at 3w / 4w / 5w).
- **Strike-rate bands** (min 20 balls): >140 / 120.1–140 / 100–120 bonuses; 40–50 / 30–39.99 / <30 penalties.
- **Economy bands** (min 5 overs): <2.5 / 2.5–3.49 / 3.5–4.5 bonuses; 7–8 / 8.01–9 / >9 penalties.

First ODI tour: *Ireland vs West Indies Women's ODI 2026* (3-match women's bilateral).

## Player identity — the global registry (read this before touching name matching)

Players are matched by a **stable identity (`pid`)**, not by fuzzy name. `registry/players.json`
is ONE global, permanent file (keyed on **`ci:<cricinfoId>`**; fallback `cs:`/`uncapped:`; `cricsheet_id`
derived from `registry/crosswalk.json`) listing **every feed spelling** of every player. Built by
`build_registry.py` which resolves each name to a cricinfo id (manual bridge → exact people.csv → fuzzy
null-on-ambiguity → ESPN roster athlete.id, which IS the cricinfo id), never fabricating an identity on
ambiguity. The bot:
- resolves each feed/squad name → `pid` **deterministically** (no per-match fuzzy gamble),
  **merging** stats the feed split across two spellings (e.g. cricsheet "DN Wyatt" + "Danni Wyatt");
- emits a **`Player ID`** column + the canonical name, so the draft joins by id, not name;
- drops junk feed names ("Player Not Found", empty);
- falls back to fuzzy **only** for names not yet in the registry, and **logs** every fallback +
  every genuine non-squad leftover to `registry/UNMATCHED_*.log` (surfaced in CI) so the gap can
  be closed once. Identity is global → a player resolved in one tour is resolved in all future ones.

### Fixing the rare unmatched player — NO code needed

Three tabs in the Google Sheet make manual fixes self-serve:
- **`Needs Review`** (bot-written each run): each unresolved player as `Tour | Team | Feed Name |
  Closest Match | Correct? (Yes/No)`. The bot names the **closest squad player** it can find; you
  just type **Yes** (it's that player) or **No** (it isn't) in the last column. On the next run a
  **Yes** is applied automatically — the alias is saved to `Player Aliases` and the row drops off.
  A blank Closest Match means no plausible squad player → it's genuinely not in your squad (type
  No, or add them to the squad file if they should be draftable). Your Yes/No answers are preserved.
  *Catches the under-matching failure: a feed name that matched nobody.*
- **`Identity Anomalies`** (bot-written each run): the OPPOSITE failure — two *different* players
  merged into one id (false-merge) or one id on two rows in a match (duplicate), **plus the audit of
  past splits**, as `… | Different players? (Yes/No)`. **Yes** = they're distinct (keep/do the split);
  **No** = same person (undo/ignore). READ-ONLY on live identity — your answer is recorded into
  `registry/identity_splits.json`; the actual split/undo is applied via `build_registry.py` out-of-band.
  Mental model: Needs Review = *"who is this unmatched name?"*; Identity Anomalies = *"are these really the same person?"*.
- **`Player Aliases`** (the alias store): `Feed Name | Correct Player | Source`. The bot auto-fills
  high-confidence matches (`auto`) and your confirmed ones (`confirmed`); you can also hand-add any
  row. Read + applied at the start of every run. No commit, no laptop.

To make fixes **permanent + shared with the draft/auction** (which read the committed registry,
not the sheet), run `python3 registry/fold_review_aliases.py` — it auto-folds the **confident**
`name alias` rows from the Needs Review tab into `registry/manual_aliases.json` (leaving the
`not in squad` rows for human judgment). Then `python3 build_registry.py` + commit. Doing this
each tour shrinks Needs Review to just the genuinely-ambiguous handful.

## Gotchas
- ~~**cricsheet's `t20s` archive only holds internationals**, so a league's own archive must be
  added to the download step in `.github/workflows/wwc-points.yml`.~~ **AUTOMATIC since 31 Aug 2026.**
  The first half is still true — `t20s`/`odis`/`tests` hold internationals only and a league lives
  in its own archive — but nothing is hand-listed any more. `cricsheet_archives.py` reads
  `tours.json`, resolves each tour against cricsheet's live index (format archive by `format`,
  league archive by name, gender-checked and ranked so the men's CPL can never pick up the
  women's), downloads what this run needs, and leaves `cricsheet_resolved.json` behind for the
  sheet to report. Two consequences: adding a tour needs **no YAML edit**, and a league cricsheet
  has not published yet — a brand-new one always — starts reconciling **by itself** on the first
  run after the archive appears. `"cricsheet_archive": "cpl_json.zip"` on a tours.json entry pins
  it; `"none"` says cricsheet does not cover this league and silences the report.
  *(Wiring this surfaced that `tests_json.zip` was never in the old hand-kept list, so the ENG v PAK
  Test tour had no L2 source at all.)*
- **Squad name aliases**: the single place is now `registry/manual_aliases.json` (then re-run
  `build_registry.py`) — NOT the inline `ALIAS` dict in `wc_fps_to_csv.py` (kept only for legacy
  feed-internal split canonicalization like "charlotte dean"→"charlie dean"). The registry is
  the once-and-for-all map; most names are auto-resolved from the auction DB / ESPN / cricsheet.
- ~~**API budget**: cricketdata free = 100 hits/day.~~ **No longer applies** — every feed is
  keyless and unmetered since 20 Aug 2026. Completed-match scorecards are still cached (they are
  immutable, and it saves wall-clock), but there is no hit budget to stay inside. Adding a tour
  costs run time, not quota.
