<!-- SUPERSEDED BANNER — added 24 Aug 2026 -->
> ⚠️ **HISTORICAL — kept for rationale, not as current truth.** This document assumes S1 = cricapi. S1 is Cricbuzz now — note `recon_overrides.json` stores only the LETTER 'S1', so old approvals recorded against cricapi still read as 'S1'. Every feed is keyless and unmetered now, so any quota reasoning here is moot. **Current architecture: `CLAUDE.md`. Trust the code over this file.**

# Recon Review — human-in-the-loop feed reconciliation

> Feature spec + operator guide. Sibling to `NAME_MATCH_AND_ISSUES_CRITICAL.md`.
> Touches **wwc-points-bot** (`wc_fps_to_csv.py`) and **wwc-draft** (`lib/points.ts`).
>
> **Spec of record = the owner-locked model of 2026-08-07** (the next three sections). Where the
> older text in this file, the sibling docs, or the code disagrees with it, **the model wins and
> the other thing is the bug**. Superseded decisions are marked, not deleted — the history is why
> the code looks the way it does.
>
> `file:line` refs are against the **2026-08-07 working tree** of `wc_fps_to_csv.py` (which was
> being actively edited when this was written). Symbol names are the durable handle; grep those if
> a number has drifted.

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

## Rule A — the state model (locked 2026-08-07)

**`Match Status` and `Recon State` are two INDEPENDENT axes.** Recon progress must never be
encoded inside the match status — cramming both into one column is exactly what made
`COMPLETED_FLAGGED` mean four different things (unverified single feed / official revision
pending / identity unresolved / and, by omission, "fine").

```
LIVE
 ├─ any data unconsumed          → stay LIVE + a NAMED row in the Recon tab
 ├─ any L1 gap unresolved        → stay LIVE + a NAMED row in the Recon tab
 └─ L1 done AND all consumed     → COMPLETED, recon state "L1 recon done"
                                    ⇒⇒ BASE POINTS ARE FROZEN AT THIS MOMENT ⇐⇐

COMPLETED  (never returns to LIVE)
 ├─ cricsheet not posted yet     → "L1 recon done"
 ├─ cricsheet posted, diffs open → "L2 recon pending"
 └─ cricsheet posted, all clear  → "L2 recon done"
```

Two properties do the work:

- **COMPLETED is one-way.** A match that has published its base points never goes back to LIVE.
  Later official movement is a *recon state* change (and an audit row), not a status regression.
- **The freeze is the L1-done transition.** Everything downstream — the L2 baseline, the
  settlement audit, "what did people see when money changed hands" — is defined against the
  values frozen at that instant.

## Rule B — the baseline (this is the one the docs previously got wrong)

**The L2 baseline is THE RECONCILED L1 VALUE** — exactly what was published and frozen as base
points once ALL approved L1 overrides had been applied.

An approval may be **S1** (cricapi), **S2** (ESPN) or **Manual**, so the baseline value can come
from either feed *or* from a hand-typed number. It is therefore:

- **NOT** "ESPN's value"
- **NOT** cricapi's value
- **NOT** a value recomputed from the raw feeds on some later run

> ⛔ A previous draft of these docs said *"the L2 baseline for a single-source field is ESPN's L1
> value, by definition"*. **That is wrong** and would silently discard the owner's approval on any
> field he had adjudicated. It is corrected here and must be corrected wherever else it appears.

**Architectural consequence.** Because base points freeze at L1-done, the baseline must be
**READ FROM THE FROZEN RECORD**, never recomputed. Recomputing the provisional cut on every run
is the root cause of the phantom `dots 0→N` review rows — it wasted the owner's time *and*
corrupted settled points (see the 2026-08-07 post-mortem).

**Where the code stands.** `reconciled_provisional` (`wc_fps_to_csv.py:1517`) still *rebuilds* the
cut from raw feeds + stored overrides on every run; that is the recomputation the rule forbids.
`registry/settlement_snapshots.json` is the right idea in the wrong shape — `record_settlement`
(`:2466`) freezes only the scored **total** (`s["total"]`, called at `:2019` / `:2028`), so it
cannot answer "what was this player's frozen `dots`?". Closing rule B means freezing the
**L1-reconciled per-field perf dict**, and having L2 read *that*.

## Rule C — single-source fields: `dots` and `maidens`

`dots` and `maidens` come from **ESPN only**. cricapi supplies neither; cricsheet supplies both.
(`ESPN_ONLY_FIELDS`, `wc_fps_to_csv.py:1253`.)

- **At L1 there is no second number, so there is NO L1 comparison.** ESPN's value is accepted and
  does **not** block COMPLETED. Inventing a comparison here is what manufactured phantom rows.
- **If ESPN's value is ABSENT for a bowler who bowled, that is unconsumed data** → the match stays
  LIVE (see the next section). Absence of a source is not a zero. `blank_perf` zero-initialises
  every stat, so "no ESPN row" and "genuinely bowled 0 dots" are the *same value* — that single
  collapse is the root of the whole LPL M2/M4 class.
- **At L2, cricsheet reconciles these fields against the reconciled-L1 baseline** (the rule above).
  This is the first and only validation `dots`/`maidens` ever get.

Corollary the hold logic must respect: when **cricsheet** is the scorer it is strictly better than
ESPN on these fields, so holding a provisional value *against* cricsheet on `dots`/`maidens` is
never right. The code did exactly that and destroyed real points — see defect 1 in the 2026-08-07
post-mortem.

## Rule D — nothing goes unconsumed

If the bot receives data it cannot attribute to a player, or cannot fully score a player, the
match **stays LIVE** and a **named** row appears in the Recon tab. No silent zeros. No dropped
players. "Named" is load-bearing: a hold nobody can act on is just a stuck match.

`classify_match_status(..., unsourced=...)` (`:1424`) implements this: `unsourced` beats every
other non-cricsheet verdict, because an unresolved L1 gap is "two feeds disagree, pick one" while
an unsourced field is "nobody measured this and we scored it as 0" — and nothing downstream will
ever notice the second.

## Rule E — identity never appears in the Recon tab

**Recon Review answers "which value is right?". Identity answers "who is this?".** Different
question, different destination.

**No new tab is needed.** ESPN's `athlete.id` **IS** the cricinfo id (`build_registry.py:336`), so
a hypothetical "Needs ESPN PID" tab and the existing **"Needs Cricinfo ID"** tab are the same tab.
One destination for every identity question.

**The discriminator — use ESPN as the third feed instead of asking the human:**

| ESPN saw him play this match? | verdict | routing |
|---|---|---|
| **Yes** | he played; cricsheet just spells him differently | **IDENTITY failure** → "Needs Cricinfo ID", **HOLD** his provisional value |
| **No** | he genuinely did not play | score as **DNP** — not an anomaly, not a row |

**Where the code stands.** Routing is half-right today: a non-`ci:` player correctly goes to
`NEEDS_CRICINFO` (`wc_fps_to_csv.py:1934-1944`), but the branch immediately after it
(**`:1947-1953`**) emits a `param: "ID"` row into `RECON_REVIEW` for a player who is already
`ci:`-anchored yet missing from the official card. That is a value tab carrying an identity
question, and it is why the owner's Recon Review tab accumulated rows with no matching published
row at all. It is also the branch that should be answered by the ESPN discriminator instead of by
a human.

## The four rules (locked 2026-06-28) — decision 4 SUPERSEDED

The original contract, kept verbatim. Rules 1–3 still stand as written; read them through the
state model above, which supplies the conditions they left implicit.

1. **Any L1 discrepancy holds the match LIVE** until approved. L1-clean (both feeds agree) →
   auto-COMPLETED.
   > *Amended 2026-08-07:* an L1-clean match is **not** automatically COMPLETED — it must also be
   > fully **consumed** (rule D). "Both feeds agree on r/w/4s/6s" says nothing about the fields
   > only one feed carries.
2. **Resolve UX:** per-player rows with a `Correct Value` dropdown (`S1` cricapi / `S2` ESPN /
   `Manual`+value). A **systemic** whole-match freeze collapses to ONE **match-level** row with
   per-feed team totals as evidence and a single dropdown that cascades the chosen feed to every
   differing player (so today's "all players wrong" case is one click, not 31).
   > *Amended (whole-match collapse removed in code):* `build_recon_rows` (`:1527`) now emits ONE
   > row per (player, differing field) and **no** whole-match collapse — a match where neither feed
   > is wholly right (some players' correct value is cricapi, others ESPN — Match 23) can only be
   > resolved per-player. Match-level **seeds** still apply on the *approval* side
   > (`apply_recon_overrides`, `:1469`, `scope == "match"`), so historic seeded approvals keep
   > working; the tab just stopped offering the shortcut. `RECON_SYSTEMIC_MIN` /
   > `RECON_SYSTEMIC_FRAC` no longer exist.
3. **L2 / official cricsheet:** if official == the reconciled value → auto-apply silently. If
   official ≠ reconciled → require approval again **and highlight loudly** (red-fill); the last-
   approved value is **held** (shown) until you approve the revision.
   > *Clarified 2026-08-07:* "the reconciled value" = the **frozen** reconciled-L1 value (the
   > baseline rule above), read from the record — not re-derived. And the hold must **not** cover
   > single-source fields when cricsheet is the scorer (rule C; post-mortem defect 1).
4. ~~**Single-feed (cricapi-only, no ESPN):** allow COMPLETED but **FLAG** it unverified.~~
   > **SUPERSEDED 2026-08-07.** With no ESPN there are no `dots` and no `maidens` — that is
   > **unconsumed data**, so rule D applies and the match **stays LIVE**. "If you can't consume the
   > data, don't move to COMPLETED" beats "publish it with a badge": a flagged-but-final number
   > still settles contests, and a bowler scored on an assumed 0 dots is silently wrong by up to
   > `overs × 6` points.
   >
   > Kept visible because the code still contains the old branch:
   > `classify_match_status` returns `("COMPLETED_FLAGGED", "⚠ unverified — single feed")` at
   > `wc_fps_to_csv.py:1454`. In practice the `unsourced` gate fires first for any match with a
   > bowler, so the old branch is now mostly unreachable — but it is still there, and it is still
   > wrong.
   >
   > **Open escape hatch (owner's call, not made):** if ESPN never appears for a match it holds
   > LIVE forever and never settles. Decide whether that needs an explicit "settle without dots"
   > approval row.

## How it works

### Bot (`wc_fps_to_csv.py`)

- **Status columns** (after `L1 Recon`/`L2 Recon`): **`Match Status`** and **`Recon Flag`** (human
  reason), computed once per match (`classify_match_status`, `:1424`).
  **Per the state model these are one axis too few** — the target shape is `Match Status`
  (`LIVE` | `COMPLETED`, plus `SCHEDULED` for toss rows) **plus a separate `Recon State`**
  (`L1 recon done` | `L2 recon pending` | `L2 recon done`). Today the code still emits the
  overloaded `COMPLETED_FLAGGED`; splitting the axes is the migration this section is written
  against.
- **`Recon Review` sheet tab** (`write_recon_tab`, `:3253`, mirrors `write_anomaly_tab`):
  `Tour | Match | Date | Player ID | Full Name | Param | Source 1 (cricapi) | Source 2 (ESPN) |
  Correct Value | Manual Value | Status | Match Key`. The `Correct Value` cell is a native
  dropdown (`S1`/`S2`/`Manual`) via gspread `add_validation` (degrades to free-text if the
  gspread version lacks it). **Value questions only** — identity belongs in "Needs Cricinfo ID"
  (rule E).
- **Row emission** (`build_recon_rows`, `:1527`): ONE row per (player, differing field), only for
  **material** diffs (`_l1_field_material`, `:1352`). No whole-match collapse.
- **Approval readback** (`read_recon_approvals`, `:2804`, before processing): records each
  `Correct Value` into `registry/recon_overrides.json` (`_approval_to_override`, `:2699`),
  preserves answers across the tab's rewrite (`PRIOR_RECON`/`PRIOR_MANUAL`), and acks answered
  rows (`RECON_ACK`). `overrides_by_match` (`:2673`) carries an **orphan guard**: an approval keyed
  to a pid the registry doesn't know is shouted about, never silently dropped.
- **Apply** (`apply_recon_overrides`, `:1469`, before scoring): overrides are written onto the perf
  dict, then `score()` recomputes **every derived bonus** (SR/econ/milestone/haul) from the
  corrected raw fields — no special recompute code needed. A match-level seed expands to all
  differing players; **player-level overrides win** over the seed.
- **Override key = `date :: sorted(team_key(teams))[:: pid :: field]`** (`match_key_of`, `:1361`) —
  the stable, order-independent match identity, **never** the renumbered "Match N" label.
  ⚠️ The `pid` half is **not** migration-proof: the 25 Jul `ci:` re-key orphaned 83 of 131 stored
  approvals (post-mortem below). Any identity migration must re-key this file.
- **L2 baseline = the frozen reconciled-L1 value.** See the baseline rule. cricsheet is compared
  against **what was published and frozen**, with the approved L1 override already in it — so an
  official figure that *confirms* an approved correction (you picked ESPN's 2 wkts; cricsheet also
  says 2) is **silent**, and only a genuine change from what was shown is flagged. Comparing
  against raw cricapi would false-flag every match you correctly fixed; comparing against a
  *recomputation* false-flags every row whose matcher wobbled.
- **L2 hold:** until an official revision is approved (`source S2`), the perf dict is pinned back
  to the last-approved (reconciled) value (`:1842-1853`) — deliberately inverting the usual
  "cricsheet overrides all", commented loudly in the code. Flagged rows are red-filled in
  `write_to_gsheet`. **The required change is the baseline SOURCE, not the field set:** the hold
  must pin the FROZEN reconciled-L1 value (`settled_baseline()`), not a recomputed one.
  `dots`/`maidens` stay inside the hold — they are single-source at L1, so cricsheet is their FIRST
  validation, which is precisely what an approval gate is for. (Fixed 2026-08-07, commit 2d7dcce.)
- **Backstop** (`points_gap`, `:1219`): if no enumerated field moved, compare the **scored total**
  anyway, so a field nobody thought to list (balls faced/bowled → SR/econ) can't read as
  "✓ complete". Fails **loud** (`pts ?→? backstop failed — unverified`) rather than returning
  "no change" when it can't score a row.

### Draft app (`lib/points.ts`)

- Reads the status columns **by name** (`statusByLabel`, `lib/points.ts:205`); the columns are
  **optional** — absent ⇒ legacy "scored ⇒ completed" behavior (no regression on tours without
  recon).
- The two completion deciders both gate on status: **`getCompletedMatchKeys`** (lobby/schedule)
  and **`isMatchCompleted`** (match page) only count a match done when `Match Status` is
  `COMPLETED`/`COMPLETED_FLAGGED` (or absent). A scored `LIVE` match stays Live.
- **`getMatchStatusFor`** (`:634`) feeds the results route → page badges: amber **"⏳ Provisional —
  awaiting reconciliation"** (LIVE+scored), red **"⚠ Official revision pending"** (L2), or
  **"⚠ Unverified (single feed)"**.
- **When the axes split:** `MatchStatus` (`lib/points.ts:199`) collapses to `LIVE | COMPLETED` and
  the recon state becomes its own read. The gate simplifies — *completed is completed* — and the
  badge stops carrying four meanings. The `"⚠ Unverified (single feed)"` badge disappears with
  superseded decision 4 (that case is LIVE now). Keep the column-absent legacy path.

## Operator guide

When a match has a feed disagreement, or the bot couldn't consume something, it appears in the
**Recon Review** tab and stays **Live** in the draft app (results hidden).

1. Open the **Recon Review** tab. Each row is one player + one field: `Source 1` is cricapi,
   `Source 2` is ESPN. Pick `S1`/`S2`, or `Manual` and type the number in `Manual Value`.
2. Click **🏏 WWC ▸ Refresh now** (or wait for the 2-hourly run). The bot reads your answer,
   recomputes the points, moves the match to `COMPLETED` **and freezes the base points**, and the
   draft app flips the match to Completed within ~45s.
3. When official cricsheet posts later: if it matches, nothing happens; if it differs, the row
   reappears (red-filled) as an **official revision** for you to approve. The match does **not**
   go back to Live — it moves to recon state "L2 recon pending".
4. **Identity questions are not in this tab.** A "who is this?" problem lands in **Needs Cricinfo
   ID** with the derived cricinfo id pre-filled, so the fix is a paste, not a research task.

**Which feed to pick — do NOT default to ESPN.** Measured against cricsheet ground truth
(57 disputed fields, 42 player-matches):

| field | winner | right |
|---|---|---|
| runs | **cricapi** | 24/32 (75%) |
| wkts | **cricapi** | 7/11 (64%) |
| 4s | ESPN | 5/7 (71%) |
| 6s | ESPN | 6/6 (100%) |
| **overall** | **cricapi** | **33/56 (59%)** |

In fantasy points — the currency that settles money — it is near a coin flip: cricapi 444 FP of
error vs ESPN 312, catastrophes ≥30 FP **7 vs 7**. So: **cricapi tends to be right on runs and
wickets, ESPN on boundaries**, and the frozen-mid-innings failure (Match 30) is a *cricapi-specific
shape*, not evidence of a generally worse feed. **Do not flip the base to ESPN.** Nothing measured
supports it.

For calibration: **your own L1 adjudications are 30/30 correct against cricsheet.** The machinery
exists to put the question in front of you, not to second-guess the answer.

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

> **Corrected 2026-08-07 (rule E):** the *hold* was right, the *destination* was wrong. Identity
> questions must never appear in Recon Review — they belong in **Needs Cricinfo ID**, and the
> ESPN-saw-him-or-not discriminator answers most of them without asking a human at all. The
> `param: "ID"` rows this section describes (`wc_fps_to_csv.py:1947-1953`) are the mis-routing.
> `COMPLETED_FLAGGED` as the carrier is also superseded: identity-unresolved is a **recon state**,
> not a match status.

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

> **Extended 2026-08-07 (rule B):** this diagnosis was right and the fix stopped one step short.
> The live re-computation isn't merely *unaudited* — **it is the bug**, and an audit surface beside
> it doesn't remove it. The frozen record must **be** the L2 baseline, which means freezing the
> reconciled-L1 **per-field** values, not just `s["total"]` (`record_settlement`, `:2466`). Freeze
> point also moves: today it fires on any `COMPLETED`/`COMPLETED_FLAGGED` publish (`:2019`,
> `:2028`); the locked freeze point is the **L1-done transition**.

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

## 2026-08-07 — what the dots audit exposed

Every finding below was attacked by two independent refuters; two headline claims from the first
pass were **refuted** and do not appear here. What follows is the corrected picture.

**The one-line story:** `dots` are ESPN-only and `blank_perf` zero-initialises every stat, so
**"no ESPN row" and "genuinely zero dots" are the same value: 0**. Every defect below is a
consequence of that collapse, or of the recomputation the baseline rule now forbids.

### 1. The L2 hold changed already-settled numbers (the money bug this time)
The hold iterates `for field in RECON_L2` (`:1842-1853`), and `RECON_L2` includes `dots`
(`:1199-1200`). So it wrote a **fabricated** baseline back over **cricsheet's exact figures**. The
bug is self-fulfilling: the fake 0 creates the L2 gap, and the gap then imposes the fake 0.

- **95 dot points** withheld from the LPL settled sheet — cricsheet's own figures, sitting
  unapproved in Recon Review
- **72 dot points** destroyed in LPL Match 2 alone: the "official" cut was **worse** than the
  provisional one people actually saw
- The Hundred's **seeded settlement baseline scored 0 dots on 100% of its bowler-rows** — **785
  bowling points never awarded** (441 Men's M1 + 344 Women's M1). Gleeson settled at **4**; the
  same row now reads **145**.
- 14 impossible zeros remain, 13 of them L2-hold victims

Fixing the baseline construction alone does **not** repair already-published numbers: the held
rows must be approved `S2`. NOTE: an earlier draft proposed instead dropping `dots`/`maidens` from
the hold so cricsheet could apply silently — that is SUPERSEDED and wrong under rule C. Base points
freeze at L1-done; any L2 movement needs a human, whichever field moved.

For scale, the counterweight: **630 of 661 published bowler-rows (95.3%) had dots scored.** Dots
were working for the overwhelming majority. The damage is concentrated, not systemic.

### 2. 🔴 An ESPN-only player was never compared, never flagged, and still published COMPLETED
Violated locked rule 1 (an L1 discrepancy holds the match LIVE) and rule D directly, and it was
live on the committed bot.

`compute_l1_gaps` iterated `for pid in capi_pid`, so a player cricapi never listed produced no
gap. `merge_espn_into`'s `elif e.get("played")` branch then built him from `blank_perf` copying
**only** `dots` + `maidens` — discarding a full ESPN record's runs, wickets, boundaries,
everything. The `unsourced` gate did not save him: it detects only the opposite direction
(cricapi-has-bowler / ESPN-missing).

Verified by executing the real module:

```
published 4 pts   vs   110 pts actually earned      → status ('COMPLETED', '')
```

He got the +4 XI bonus and nothing else — "skip scoring a player", in production.

> **Status:** fixed in the **uncommitted** working tree (2026-08-07), both halves. The `elif`
> branch now takes the whole ESPN record (`np = dict(e)`, `:1287-1298`) and `compute_l1_gaps`
> iterates the **union** of both feeds (`:1377-1400`), so a one-feed-only player is reported as a
> gap rather than skipped in silence. **CI runs from the committed repo — this is not live until
> it is pushed.**

### 3. A detected conflict is computed and thrown in the bin
`xcheck` is assigned and **never read** — the 7 Aug AST walk confirmed STORE at `[1267, 1743,
1748]` and LOAD only at `[1286, 1291]`, both inside `merge_espn_into` itself. Still true in the
working tree (STORE `:1267`, `:1779`, `:1784`; LOAD `:1286`, `:1299`): the caller unpacks it into a
local and does nothing with it. A `runs_conceded` conflict between cricapi and ESPN is *found* and
discarded.

This matters because the base silently decides the fields **L1 never compares at all**: `catches`,
`runs_conceded`, `runouts`, `balls`, `lbwb` — **22%+ of scoring exposure**, resolved with no human
ever shown a disagreement. `xcheck` was the one place that noticed.

### 4. Phantom `dots 0→N` rows — the recomputation, in the flesh
The L2 baseline was rebuilt by `_by_pid()` (strict id-only) while the **published** number came
from `match_squad_to_perf()` (squad-anchored, id-first, fuzzy fallback). Two matchers, allowed to
disagree, and disagreement read as "the value changed".

Verified against the pre-cricsheet snapshot: for LPL Match 2 the sheet **already displayed**
3, 9, 9, 7, 9, 8, 1, 7, 3 dots. **The `0` never happened.** Of 59 rows in the Recon Review tab:
**12 phantom, 10 real, 37 unresolvable** (no published row to compare — those are the identity
rows sitting in the value tab, see rule E).

Mitigated by making `merge_espn_into` the **single** merge implementation used by both the emit
path and the baseline, so they cannot drift again — but the real fix is rule B: **stop rebuilding
the baseline at all.**

### 5. 83 of 131 stored approvals were DEAD
`registry/recon_overrides.json` is pid-keyed and was **never re-keyed** by the 25 Jul `ci:`
migration. Approvals silently stopped applying → the L2 baseline fell back to raw cricapi → **the
same row reappeared every run no matter how many times it was answered** (the "E Perry 38→64 keeps
propping up" symptom). 82 re-keyed via `pid_map.json` (backup:
`registry/recon_overrides.json.bak-prekey-20260807`, each carrying `_rekeyed_from`).

Generalisation, and the point worth keeping: **pid-keyed data files need re-keying on any identity
migration, not just runtime shims.** Same class as the draft's orphaned player-photo map.

Residue: 4 orphaned override pids `pid_map` couldn't fix (`ci:1150021`, `ci:459508`, `ci:859899`,
`slug:fabian-allen`) — needs a registry lookup, not a script. And 13 duplicate rows from the re-key
(harmless for apply, last-wins, but they corrupt any count).

### 6. Smaller, verified, unfixed
- **`L1_RUN_TOL=1`** (`:1350`) hides up to **7 points** per row; 535 (runs, balls) combinations
  move ≥3 pts. The tolerance was meant to skip "a single fantasy point of uncertainty" — it does
  not, because a run difference drags strike-rate with it.
- **`espn_dots()`** (`:780`) has **zero call sites** — dead code, and a decoy: it looks like the
  thing that fetches dots.
- **ESPN `playbyplay` is capped at `limit=600` with no pagination** (`:782`, `:850`). An ODI is
  600 legal balls **plus extras**. Silent truncation on exactly the format where it matters.
- **ESPN's fielding/run-out credits are regex-parsed out of commentary text** (`:915-932`) — one
  reason cricapi, with its structured `catching` block, legitimately holds the base seat.
- **921 fantasy points sit on pid-less ghost rows the draft app cannot join** — 386 of them
  Hasaranga's across 6 LPL matches, +37 in Hundred M.
- **The Hundred Women's seeded baseline silently asserts "nothing changed"** on a match whose real
  swing is ~+344: `seed_settlements.py` pass 1 resolved no pids for that tour, so pass 2 froze
  *today's* numbers with provenance `unknown` (0 seed / 158 live / 287 unknown, vs 30 seed for
  Men's).

### The root fix nobody has made yet
**`unknown ≠ zero`.** `blank_perf` must distinguish "not supplied" from "measured as 0". Everything
in this post-mortem is downstream of that one collapse. It was deliberately not done unsupervised —
it threads through `score()` and every feed parser in a system that settles real money.

## Tests

- Bot: `pytest -q` (`tests/`) — `score()`, `recon_gaps()`, name-match, **`classify_match_status` +
  `apply_recon_overrides` + approval mapping**, the completeness/`unsourced` gate,
  `merge_espn_into` unsourced detection, the baseline-recovers-dots regression, the override orphan
  guard, the ESPN id anchor, and the **Match 30** regression (LIVE → approve "use ESPN" →
  Charani **73** / Perry **118** → COMPLETED).
- App: `npm test` (`scripts/test-points.ts`) — lookups + the gate (scored+LIVE ⇒ not completed;
  scored+COMPLETED/_FLAGGED ⇒ completed; **column-absent ⇒ legacy**).
- Both wired into `.github/workflows/test.yml` (push/PR), separate from the sheet-writing jobs.

⚠️ **CI runs from the committed repo.** As of 2026-08-07 the `merge_espn_into` unification, the
`unsourced` gate, the ESPN id anchor and the re-keyed `recon_overrides.json` are in the working
tree only — none of it is affecting the live sheet until it is committed. CI also writes back to
this repo (`chore: persist sheet decisions`), so **rebase before pushing** or an overlapping push
loses approvals.
