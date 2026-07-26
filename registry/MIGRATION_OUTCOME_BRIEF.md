# Identity migration — DONE state (for doc audit)

The player-identity system was migrated (25 Jul 2026) from cricsheet-anchored to **cricinfo-id-anchored**.
Docs across the repos still describe the OLD model and need updating. Here's what is now TRUE — flag any
doc text that contradicts it.

## NEW model (what docs should say)
- **pid = `ci:<cricinfoId>`** — the ESPNcricinfo id (= cricsheet people.csv `key_cricinfo`; UNIQUE 18253/18253,
  invariant, verifiable at cricinfo.com/cricketers/x-<id>). Fallback ladder: `ci:` → `cs:<cricsheetId>`
  (in cricsheet, no cricinfo id) → `uncapped:<slug>` (in neither; FLAGGED). In practice everyone has a
  cricinfo id, so `cs:`/`uncapped:` are ~empty.
- **cricsheet_id is DERIVED from the cricinfo id** via `registry/crosswalk.json` (cs↔ci = same person by
  construction). It is NO LONGER the pid.
- **`build_registry.py` rewritten:** resolves an announced name to a cricinfo id via cascade —
  manual bridge → legacy TS bridge → exact people.csv → FUZZY **null-on-ambiguity** → ESPN athlete.id.
  It NEVER fabricates a `slug:` on ambiguity; unresolved players go to HOLD → `uncapped:` +
  `registry/needs_cricinfo_pending.json`.
- **`registry/manual_ci_bridges.json`** = human-verified announced-name → cricinfo id (the permanent alias
  source; replaces guessing). **`registry/crosswalk.json`** = people.csv-derived cs↔ci map. Both committed.
- **`tour_sync_finalize.py`** now pushes unresolved players to a **"Needs Cricinfo ID"** tab in the points
  GSheet (`write_needs_cricinfo_tab`) — the self-maintaining review loop for future tours.
- Fuzzy matching still lives in the shared **cricket-identity** package and is used ONLY as a fallback;
  its **null-on-ambiguity** behavior is now load-bearing (prevents wrong merges like Jo→Ashleigh Gardner).
- **Draft:** `data/players-raw.json` `pid` = `ci:`; `lib/registry.ts` `resolveEspnPid` also indexes
  `cricinfo_alt` (`key_cricinfo_2/_3`); `lib/points.ts` has a `resolvePid()` SHIM reading `lib/pid-map.json`
  (old pid → `ci:`) so the sheet's pre-migration Player IDs still join through the transition.
- **Sheet:** the "Player ID" column transitions to `ci:` (bot re-emits); the draft shim covers old rows.
- **Auction:** `players.cricinfo_id` backfilled from the crosswalk; registry mirror is `ci:`.

## Now-STALE claims to hunt for in docs
- "pid is the cricsheet_id / a cricsheet hash", "keyed on cricsheet_id", "espn:/slug: fallback when unknown"
- the old `build_registry` flow that fuzzy-matches to a cricsheet_id and mints `slug:`/`espn:` on ambiguity
- "espn_id sourced from live ESPN rosters" as the identity source
- any claim that a fuzzy/DB match MINTS identity (it now only proposes; HOLD-for-review on ambiguity)
- references to the pid ladder / anchor string that omit `ci:`

## What is STILL true (don't flag)
- Points join by the stable pid (Player ID column) with fuzzy NAME as fallback — still true (pid is just `ci:` now).
- Draft integer `id` is the durable key for picks/selections — unchanged.
- Mirror discipline (registry/players.json copied to draft + auction) — unchanged.
- Monolith, no-live-auction-pool-rebuild, refresh-match-data-first — unchanged.
