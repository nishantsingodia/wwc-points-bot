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
- `registry/UNMAPPED_<tour>.txt` — per-tour list of no-cricsheet_id squad players (the defect report; triage it).
- `registry/identity_splits.json` — force wrongly-merged identities apart.
