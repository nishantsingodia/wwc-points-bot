# Adding a tournament's points

This bot can track **multiple tournaments at once** — each one writes its own tab in
the Google Sheet, all from the single 2-hourly run. Tours are listed in **`tours.json`**.

## The flow — when Nishant says *"add &lt;tour&gt;'s points"*

**All Claude needs from you is the tournament name** (plus squads only if it's for an
auction). Claude does the rest:

1. **Find the two series IDs**
   - **cricketdata** (match list + scorecards): `GET https://api.cricapi.com/v1/series?apikey=<KEY>&search=<name>` → copy the `id`.
   - **ESPN/cricinfo** (dot-balls, XI, team attribution): find the series page on espncricinfo — the URL is `.../series/<slug>-<SERIES_ID>/...`; the number is the id. (A web search for "espncricinfo &lt;tour&gt; scorecard" surfaces it.)
2. **Confirm two things with you**
   - **Tab name** (default: a short tour name).
   - **Squads?** — *full squad list* (needed for ownership / an auction → players get `In Squad List = Y` and DNP rows) **or** *featured-players-only* (no list; the sheet lists whoever actually played). For a full list, Claude sources the announced squads into `<tour>_squads.json` (same format as `squads.json`).
3. **Register the tour** — append one object to `tours.json`:
   ```json
   {
     "name": "Men's T20 WC 2026",
     "cricapi_series": "<cricketdata series id>",
     "espn_series": "<espn series id>",
     "tab": "MT20WC POINTS",
     "squads": "mt20wc_squads.json"
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
   - Commit `registry/players.json` (+ `manual_aliases.json`) — CI reads the committed file.
5. **Deploy + verify** — commit & push; trigger a run (🏏 WWC button, or `gh workflow run wwc-points.yml`); confirm the new tab fills, the **`Player ID`** column is populated, and totals look right (Source column clean, no phantom `In Squad List = N` rows for squad players). The CI run also prints any registry gaps (`UNMATCHED_*.log`) as a warning.
6. **You wire the leaderboard** — add your ownership / C×2-VC×1.5 / leaderboard tabs that
   reference the new points tab. The points tab stays the sacrosanct raw layer.

## tours.json reference

| field | required | meaning |
|-------|----------|---------|
| `name` | yes | label (shown in logs) |
| `cricapi_series` | yes | cricketdata series id (primary scorecard) |
| `espn_series` | yes* | ESPN/cricinfo series id (dot-balls, +4 XI, team attribution). *Omit only if you accept no dots/XI. |
| `tab` | yes | Google Sheet tab to write (created if missing) |
| `gender` | yes | `male` or `female` — so cricsheet matches the right files |
| `squads` | no | filename of a squad JSON in this repo; omit for featured-players-only |
| `ends` | no | last match date `YYYY-MM-DD`. After `ends` + 21 days the tour **auto-freezes**: no API calls, no writes, the tab is kept with its final data. Omit to run forever. |

Only **T20s** are scored — T20Is **and** franchise leagues like MLC (a tour can mix
formats; ODIs/Tests are ignored).

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
2. **cricapi + ESPN dots/XI** (`provisional`) — cricapi scorecard + ESPN ball-by-ball
   dots and the +4 in-XI; runs/wickets cross-checked (mismatches flagged).
3. **cricapi · limited** — only if ESPN is unavailable (no dots/XI).
Super-overs excluded; feed joins tolerate ±1 day; same-surname / cross-source
disagreements / unknown players are flagged in Status rather than silently guessed.

## Player identity — the global registry (read this before touching name matching)

Players are matched by a **stable identity (`pid`)**, not by fuzzy name. `registry/players.json`
is ONE global, permanent file (keyed on `cricsheet_id` when known, else `espn:`/`slug:`) listing
**every feed spelling** of every player. Built by `build_registry.py` from the auction DB
(`cricsheet_id` + cricsheet initials), ESPN rosters (`espn_id`), cached cricapi, and all the
repos' historical alias maps. The bot:
- resolves each feed/squad name → `pid` **deterministically** (no per-match fuzzy gamble),
  **merging** stats the feed split across two spellings (e.g. cricsheet "DN Wyatt" + "Danni Wyatt");
- emits a **`Player ID`** column + the canonical name, so the draft joins by id, not name;
- drops junk feed names ("Player Not Found", empty);
- falls back to fuzzy **only** for names not yet in the registry, and **logs** every fallback +
  every genuine non-squad leftover to `registry/UNMATCHED_*.log` (surfaced in CI) so the gap can
  be closed once. Identity is global → a player resolved in one tour is resolved in all future ones.

### Fixing the rare unmatched player — NO code needed

Two tabs in the Google Sheet make manual fixes self-serve:
- **`Needs Review`** (bot-written each run): the handful of feed names that didn't resolve, with
  the **type** (`name alias` = same player spelled differently; `not in squad` = a genuine
  non-listed player) and a **suggested** match + how to fix. You read this in the sheet, not in
  GitHub logs.
- **`Player Aliases`** (you edit): two columns, `Feed Name | Correct Player`. Type the feed
  spelling on the left and the correct squad player on the right; the bot reads this tab at the
  start of every run and applies it. No commit, no laptop — just a row, then tap 🏏 (or wait for
  the 2-hourly run).
- For a `not in squad` player who really should be there, add them to the squad file (they then
  get a registry entry); otherwise they harmlessly show as an extra row attributed to their team.

To make fixes **permanent + shared with the draft/auction** (which read the committed registry,
not the sheet), run `python3 registry/fold_review_aliases.py` — it auto-folds the **confident**
`name alias` rows from the Needs Review tab into `registry/manual_aliases.json` (leaving the
`not in squad` rows for human judgment). Then `python3 build_registry.py` + commit. Doing this
each tour shrinks Needs Review to just the genuinely-ambiguous handful.

## Gotchas
- **cricsheet's `t20s` archive only holds internationals** (men's & women's T20Is). A
  franchise league (e.g. MLC) or an ODI tour lives in its OWN cricsheet archive, which must
  be added to the download step in `.github/workflows/wwc-points.yml` for the *official*
  source to kick in (MLC uses `mlc_json.zip`). The cricapi+ESPN path — including exact
  dot-balls — works regardless, so a tour is fully scored even before its archive is wired.
- **Squad name aliases**: the single place is now `registry/manual_aliases.json` (then re-run
  `build_registry.py`) — NOT the inline `ALIAS` dict in `wc_fps_to_csv.py` (kept only for legacy
  cricapi-internal split canonicalization like "charlotte dean"→"charlie dean"). The registry is
  the once-and-for-all map; most names are auto-resolved from the auction DB / ESPN / cricsheet.
- **API budget**: cricketdata free = 100 hits/day. Completed-match scorecards are cached,
  so each extra tour adds only ~its new matches/day — comfortably within budget.
