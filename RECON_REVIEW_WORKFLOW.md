# Recon Review — human-in-the-loop feed reconciliation

> Feature spec + operator guide. Sibling to `NAME_MATCH_AND_ISSUES_CRITICAL.md`.
> Touches **wwc-points-bot** (`wc_fps_to_csv.py`) and **wwc-draft** (`lib/points.ts`).

## Why this exists

On 2026-06-28, Match 30 (AUS v IND, Women's T20 WC, Lord's) showed wrong fantasy points on the
draft app's **Completed Matches** page — Shree Charani's 2 wickets scored **43**, Ellyse Perry's
fifty scored **79**. The match had been scored off a **cricapi scorecard frozen mid-innings**
(Perry logged 38\* not-out instead of her actual 56 out; Charani 1 wkt instead of 2; Gardner
33 vs 53\*; Wareham 0 vs 4\*). The bot's L1 recon (cricapi ↔ ESPN) **detected every discrepancy**
and wrote a `⚠` flag — but the points still **displayed the wrong cricapi number**, and the app
marked the match **Completed** (final-looking) while the data was provisional. ESPN/reality was
correct (cross-checked against ICC, Olympics.com, IBTimes).

The fix is **human-in-the-loop**, not auto-trust-a-feed: surface feed disagreements as
**approvable rows** (mirroring the Identity Anomalies pattern), keep a match **LIVE until its
L1 discrepancies are approved**, and never silently revise a result.

## The four rules (locked with the owner)

1. **Any L1 discrepancy holds the match LIVE** until approved. L1-clean (both feeds agree) →
   auto-COMPLETED.
2. **Resolve UX:** per-player rows with a `Correct Value` dropdown (`S1` cricapi / `S2` ESPN /
   `Manual`+value). A **systemic** whole-match freeze collapses to ONE **match-level** row with
   per-feed team totals as evidence and a single dropdown that cascades the chosen feed to every
   differing player (so today's "all players wrong" case is one click, not 31).
3. **L2 / official cricsheet:** if official == the reconciled value → auto-apply silently. If
   official ≠ reconciled → require approval again **and highlight loudly** (red-fill); the last-
   approved value is **held** (shown) until you approve the revision.
4. **Single-feed (cricapi-only, no ESPN):** allow COMPLETED but **FLAG** it unverified.

## How it works

### Bot (`wc_fps_to_csv.py`)

- **Two new CSV/sheet columns** (after `L1 Recon`/`L2 Recon`): **`Match Status`**
  (`LIVE` | `COMPLETED` | `COMPLETED_FLAGGED`, or `SCHEDULED` for toss rows) and **`Recon Flag`**
  (human reason). Computed once per match (`classify_match_status`).
- **`Recon Review` sheet tab** (`write_recon_tab`, mirrors `write_anomaly_tab`):
  `Tour | Match | Date | Player ID | Full Name | Param | Source 1 (cricapi) | Source 2 (ESPN) |
  Correct Value | Manual Value | Status | Match Key`. The `Correct Value` cell is a native
  dropdown (`S1`/`S2`/`Manual`) via gspread `add_validation` (degrades to free-text if the
  gspread version lacks it).
- **Systemic detection** (pre-pass before the per-player emit): `systemic = ≥4 players differ
  OR ≥40% of compared players OR per-team run-totals differ` → one match-level row; else
  per-player rows. (Tunable via `RECON_SYSTEMIC_MIN` / `RECON_SYSTEMIC_FRAC` env.)
- **Approval readback** (`read_recon_approvals`, before processing): records each `Correct Value`
  into `registry/recon_overrides.json` (`_approval_to_override`), preserves answers across the
  tab's rewrite (`PRIOR_RECON`/`PRIOR_MANUAL`), and acks answered rows (`RECON_ACK`).
- **Apply** (`apply_recon_overrides`, before scoring): overrides are written onto the perf dict,
  then `score()` recomputes **every derived bonus** (SR/econ/milestone/haul) from the corrected
  raw fields — no special recompute code needed. A **match-level seed** expands to all differing
  players; **player-level overrides win** over the seed.
- **Override key = `date :: sorted(team_key(teams))[:: pid :: field]`** — the stable, order-
  independent match identity, **never** the renumbered "Match N" label.
- **L2 baseline = the L1-reconciled value** (`reconciled_provisional`): cricsheet is compared
  against the provisional cut **with your approved L1 override applied**, NOT raw cricapi. So an
  official figure that *confirms* an approved correction (you picked ESPN's 2 wkts; cricsheet also
  says 2) is **silent** — only a genuine change from what was shown is flagged. (Comparing against
  raw cricapi would false-flag every match you correctly fixed.)
- **L2 hold:** in the cricsheet path, until an official revision is approved (`source S2`), the
  perf dict is pinned back to that last-approved (reconciled) value (inverts the usual "cricsheet
  overrides all" — commented loudly in the code). Flagged rows are red-filled in `write_to_gsheet`.

### Draft app (`lib/points.ts`)

- Reads the new columns **by name** (`statusByLabel`); the columns are **optional** — absent ⇒
  legacy "scored ⇒ completed" behavior (no regression on tours without recon).
- The two completion deciders both gate on status: **`getCompletedMatchKeys`** (lobby/schedule)
  and **`isMatchCompleted`** (match page) only count a match done when `Match Status` is
  `COMPLETED`/`COMPLETED_FLAGGED` (or absent). A scored `LIVE` match stays Live.
- **`getMatchStatusFor`** feeds the results route → page badges: amber **"⏳ Provisional —
  awaiting reconciliation"** (LIVE+scored), red **"⚠ Official revision pending"** (L2), or
  **"⚠ Unverified (single feed)"**.

## Operator guide

When a match has a feed disagreement it appears in the **Recon Review** tab and stays **Live**
in the draft app (results hidden).

1. Open the **Recon Review** tab. For a **whole-match** row, the `Source 1`/`Source 2` cells show
   each feed's team totals (e.g. `AUS 130/4 · IND 170/6` vs `AUS 171/4 · IND 170/6`) — pick the
   correct feed in `Correct Value` (usually `S2` ESPN when cricapi froze). For a **per-player**
   row, pick `S1`/`S2`, or `Manual` and type the number in `Manual Value`.
2. Click **🏏 WWC ▸ Refresh now** (or wait for the 2-hourly run). The bot reads your answer,
   recomputes the points, sets `Match Status = COMPLETED`, and the draft app flips the match to
   Completed within ~45s.
3. When official cricsheet posts later: if it matches, nothing happens; if it differs, the row
   reappears (red-filled) as an **official revision** for you to approve.

## 2026-07-29 — what cricsheet landing on LPL + both Hundreds exposed

LPL and The Hundred (M+W) flipped to `cricsheet · official` **days after their contests were
settled**. Three holes let settled numbers move without the gate noticing.

### 1. Identity was invisible to the L2 gate (the money bug)
`l2_pairs` only iterates pids **present on the official card**, so a player whose cricsheet
spelling didn't resolve produced no gap, no flag and no review row — his squad row simply read 0.
LPL Match 6 (DS v KR, 21 Jul) and Match 11 (GG v KR, 25 Jul) published **`COMPLETED` with an empty
flag** while Wanindu Hasaranga — **captain**, so ×2 — scored 114 and 90 that reached nobody.

Root cause: cricsheet writes initials form (`PWH de Silva`; his surname really is de Silva) and the
alias table can't be expected to enumerate those. **Fix = resolve cricsheet rows by ID, not name.**
Every cricsheet file carries `info.registry.people` (name → cricsheet person id) and the registry
stores each player's `cricsheet_id` (derived from the verified cricinfo id), so `resolve_perf_pid`
now resolves id-first and *teaches* the alias table on the way through
(`registry/cricsheet_learned_aliases.json`). Measured on the real archives: **353/360 LPL and
405/418 Hundred** played rows resolve by id alone.

This also proved the id anchor is not optional: the Hundred Women's data contains **two different
"E Jones"** (cs `971cb321` = cricinfo 858807, cs `4cf60e73` = cricinfo 1100812). Name matching
merges them; ids cannot.

New gate (`identity_break` + `classify_match_status(..., id_break=True)`): a player who played
provisionally but is absent from the official card, while that card carries an unresolvable row, is
an identity failure — the provisional value is **HELD** (never zeroed on a name-match miss), the
match goes `COMPLETED_FLAGGED · ⚠ identity unresolved on official card`, and `ID` / `ID-ORPHAN`
rows appear in Recon Review carrying the derived cricinfo id so the fix is one paste.

### 2. A moved total could hide behind "✓ complete"
`RECON_L2` doesn't list balls faced/bowled, yet those drive SR and economy — so Dickwella went
69 → 63 with the column reading clean. `points_gap()` now compares the **scored total** as a
backstop, catching any field nobody thought to enumerate. It fails **loud** (`pts ?→? backstop
failed — unverified`) rather than returning "no change" if it can't score a row.

### 3. There was no record of what was settled
L2 compares cricsheet against a **live re-computation** of the provisional cut, not against what
was on screen when money changed hands — so a scorer fix is invisible to it. The Hundred's bowler
`balls` fix (`a653743`, 25 Jul) moved Match 1 by **+433** (Gleeson 4 → 145) with L2 honestly
reporting `✓ complete`, because cricsheet agreed with the *fixed* numbers.

`registry/settlement_snapshots.json` + the **`SETTLEMENT AUDIT`** tab fix this: **write-once** per
`(match_key, pid)`, frozen the first time a match publishes COMPLETED. A baseline you can revise is
not a baseline. `seed_settlements.py` reconstructs it for matches already settled (186 rows from a
real pre-cricsheet run, provenance `seed`); everything else is marked `unknown` so the app says
"no settled baseline recorded" instead of implying a verified zero delta.

### Draft-app surface
`lib/settlement-audit.ts` diffs the baseline against the live sheet and, critically, splits rows:

- **PENDING** — L2 recon not finished. The bot is *holding* the settled value, so nothing has
  moved. A to-do list (approve in Recon Review / fix a registry alias).
- **CHANGED** — L2 recon finished and the number already differs from settlement. The re-settle list.

Surfaced at `/audit` (roll-up by tour), as an **Audit** tab on the results page, and as a badge on
the lobby's Completed tab (gold `⏳ recon open` vs red `⚠ revised −N pts`). Both sides are scored by
the same `calcSelectionPoints`, so "then" and "now" cannot drift the way the lobby and results
totals once did.

### Also fixed
- `kth ratnayake` was attached to **Milan** Ratnayake (Colombo Kaps) but cricsheet says it's
  **Tharindu** Rathnayake (Galle Gallants, cs `4eb02f2e`) — so Tharindu's 134/15/23 were credited
  to a different player in a different franchise, and his own rows read 0.
- `Dale Phillips` existed under **two** registry entries; the one `resolve_pid` picked
  (`ci:823509`, which also carried `gd phillips` = *Glenn* Phillips) is not the pid the draft
  stamped, so his LPL rows silently scored 0. cricsheet's `DN Phillips` → cs `be0c9f5b` → cricinfo
  **902447** is the real one.
- **New blocker `split-identity`** in `identity_healthcheck.py`: one draft player under >1 registry
  pid — the mirror of `dup-cricsheet`, previously unchecked. It immediately caught the Phillips
  split *and* `Shaheen Shah Afridi` carrying `draft_id 10627`, which is **Sikandar Raza** in the
  draft. Since `backfill_draft_pids.py` prefers `draft_id` over the name, one run would have
  stamped Shaheen's pid onto Sikandar's slot; that script now refuses ambiguous draft_id mappings.
  (NB: a pid legitimately maps to *several* draft rows — the draft adds a new row per tour and
  keeps old ids — so never "reconcile" draft_id from a pid→id dict.)
- The `L2 Recon` column computed against raw `prov_pid` while the gate used `recon_prov`, so it
  could shout "revised" at a value an approved L1 override had already corrected.

## Tests

- Bot: `pytest -q` (`tests/`) — `score()`, `recon_gaps()`, name-match, **`classify_match_status` +
  `apply_recon_overrides` + systemic detection + approval mapping**, and the **Match 30**
  regression (LIVE → approve "use ESPN" → Charani **73** / Perry **118** → COMPLETED).
- App: `npm test` (`scripts/test-points.ts`) — lookups + the gate (scored+LIVE ⇒ not completed;
  scored+COMPLETED/_FLAGGED ⇒ completed; **column-absent ⇒ legacy**).
- Both wired into `.github/workflows/test.yml` (push/PR), separate from the sheet-writing jobs.
