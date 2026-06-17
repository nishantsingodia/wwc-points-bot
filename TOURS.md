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
4. **Deploy + verify** — commit & push; trigger a run (🏏 WWC button, or `gh workflow run wwc-points.yml`); confirm the new tab fills and the totals look right (Source column clean).
5. **You wire the leaderboard** — add your ownership / C×2-VC×1.5 / leaderboard tabs that
   reference the new points tab. The points tab stays the sacrosanct raw layer.

## tours.json reference

| field | required | meaning |
|-------|----------|---------|
| `name` | yes | label (shown in logs) |
| `cricapi_series` | yes | cricketdata series id (primary scorecard) |
| `espn_series` | yes* | ESPN/cricinfo series id (dot-balls, +4 XI, team attribution). *Omit only if you accept no dots/XI. |
| `tab` | yes | Google Sheet tab to write (created if missing) |
| `squads` | no | filename of a squad JSON in this repo; omit for featured-players-only |

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

## Gotchas
- **cricsheet only covers what's in its `t20s` archive** (men's & women's T20Is). An ODI
  tour would need a different cricsheet archive — tell Claude; the cricapi+ESPN path still
  works regardless.
- **Squad name aliases**: if a feed spells a name very differently, add it to `ALIAS` in
  `wc_fps_to_csv.py` (rare; most are handled by fuzzy matching).
- **API budget**: cricketdata free = 100 hits/day. Completed-match scorecards are cached,
  so each extra tour adds only ~its new matches/day — comfortably within budget.
