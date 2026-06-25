# wwc-points-bot

Computes **Dream11 T20 fantasy points** for every squad player, per match, across
multiple tournaments at once, and writes each tour into its own tab of a Google Sheet.
Runs in GitHub Actions (every 2h + a button in the Sheet) — no laptop needed. The
points tabs are the raw layer that the friend-leaderboard / draft app read.

## Docs
- **[SETUP.md](SETUP.md)** — one-time setup (GCP service account, secrets, the in-Sheet button).
- **[TOURS.md](TOURS.md)** — how to add a tournament (`tours.json`, squads, the registry step).
- **[NAME_MATCH_AND_ISSUES_CRITICAL.md](NAME_MATCH_AND_ISSUES_CRITICAL.md)** — the cross-project
  player-identity design + status (this repo, the draft, and the auction share it).

## Player identity — the global registry (the keystone)
Players are matched by a **stable identity (`pid`)**, not by fuzzy name. `registry/players.json`
is ONE global, permanent file (keyed on `cricsheet_id` when known) listing **every feed spelling**
of every player, built by `build_registry.py` from the auction DB + ESPN + cricsheet + cricapi.
The bot resolves each name → `pid` deterministically (merging stats the feed split across two
spellings), drops junk names, and emits a **`Player ID`** column so the draft joins by id, not name.
Fuzzy matching is only a logged fallback (`registry/UNMATCHED_*.log`, surfaced in CI). New spellings
are added once in `registry/manual_aliases.json`. Identity is global → resolve a player once, reuse
forever. `build_registry.py` is **rebuild-safe**: a `given_compatible` guard + a one-alias-per-pid
invariant mean a rebuild can never re-merge two distinct players. See TOURS.md for the add-a-tour workflow.

## No-code review tabs (fix names from the Sheet, no code)
Three sheet tabs let you correct identity without touching code — the bot reads them each full run:
- **Needs Review** — a feed player that matched *nobody*: closest-match guess + `Yes/No` (Yes links them).
- **Identity Anomalies** — the opposite: two *different* players merged into one id (or a duplicate row),
  plus the audit of past splits, with `Different players? (Yes/No)`. Read-only on live identity.
- **Player Aliases** — the persistent store the above two write into (Feed Name → Correct Player).

## Reconciliation columns (trust the numbers)
Every points tab carries **L1 Recon** (cricapi↔ESPN agreement during the provisional cut) and
**L2 Recon** (official cricsheet↔provisional once cricsheet posts: `✓ complete` / `⏳ pending official` /
`⚠ revised: was→corrected`) — so source disagreements and cricsheet revisions are visible, not silent.

## Layout
```
wc_fps_to_csv.py      points engine (cricsheet → cricapi+ESPN fallback) → CSV / Google Sheet
build_registry.py     (re)builds the global registry/players.json
tours.json            the tournaments to track (one tab each)
registry/             players.json (global identity) + manual_aliases.json + backfill_draft_pids.py
*_squads.json         per-tour squad lists (membership)
.github/workflows/    the 2-hourly CI run + the 5-min toss-window live-lineup tick
```
