# Sheet-driven "New" player + the silent-drop fix

> Sibling to `RECON_REVIEW_WORKFLOW.md` / `NAME_MATCH_AND_ISSUES_CRITICAL.md`.

## Why
"Needs Review" resolves feed-name → squad-player with **Yes / No / New**. **Yes** persists (Player
Aliases). **"New"** — for a real player the squad list is missing — used to be a stub (it only
ACK'd, so the player was never added, points stayed orphaned, and the row re-surfaced every run;
e.g. Jane Maguire, Aimee's sister, a replacement call-up not in `squads.json`). Separately, a
**silent-drop lapse**: a player who's in the global registry but **not in this tour's squad** would
resolve to a pid, claim no squad slot, and — because she's not a no-pid `leftover` — be **silently
dropped (no row, no flag, points gone)**.

## What "New" now does (no code edit, all from the sheet)
Mark a Needs Review row **"New"** (type the player's real name in *Closest Match*; *Role* drives
SR/econ):
1. **Global identity, once:** registers her in `registry/new_players.json` with a stable
   **`slug:<name>`** pid (or reuses her existing pid if the feed already resolves) + the feed
   spelling as an alias. `load_new_players()` merges this into `ALIAS2PID`/`PID2DISP` at startup —
   so she resolves in **every** tour, with **no `build_registry` rebuild**.
2. **Per-tour membership:** the entry's `team` + `tours` are injected into that tour's squad in
   `run_tour` → she's scored + emitted.
3. **Draftable automatically:** once the bot emits her points row, the draft app's existing
   self-heal (`getSheetRoster` + `getPlayersByTeams`) adds her to the pool — no `players-raw.json`
   edit, no integer-`id` assignment.
4. **Persists:** `new_players.json` is committed back to git by the workflow (CI runners are
   ephemeral), so it never reverts and she stops re-surfacing.

Identity is **tour-agnostic** (one pid, reused everywhere); **membership is per-tour**. A later
`build_registry.py` run folds `new_players.json` into the canonical `players.json` and upgrades the
`slug:` to her real `cricsheet_id`.

## The silent-drop fix — AUTO-ADD (with guards + audit)
`find_silent_drops()` detects a played feed player who resolves to a pid but holds no squad slot.
`run_tour` then **auto-adds** her (membership for this tour) so she counts immediately and persists
(`source:"auto"` in `new_players.json`). Guards so it can't double/mis-attribute:
- only a **played** feed entry; only when her pid is **not already a squad slot** (so exactly one
  slot — no double-count); points attribute by **exact pid** (can only go to whoever drafts *her*);
- the **false-merge detector** stays on (a bad alias still surfaces in Identity Anomalies);
- every auto-add is logged + written `source:"auto"` (visible in git, reversible by editing the file).

## Key code
`wc_fps_to_csv.py`: `slugify`, `_load/_save_new_players`, `load_new_players`, `register_new_player`,
`find_silent_drops`; `read_review_confirmations` ("New" branch); `run_tour` (membership injection +
auto-add); `main()` (load before reads, save after). Workflow: commits `registry/new_players.json`.
Tests: `tests/test_new_players.py`.
