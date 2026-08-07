# Recon / scoring — dev plan

Spec is the owner's model (7 Aug). Everything below implements it.

---

## THE SPEC

### 1. Two independent axes, not one status

`Match Status` and `Recon State` are **orthogonal**. Cramming both into one column is what made
`COMPLETED_FLAGGED` mean four different things.

```
LIVE
 ├─ any data unconsumed          → stay LIVE + row in Recon tab
 ├─ any L1 gap unresolved        → stay LIVE + row in Recon tab
 └─ L1 done AND all consumed     → COMPLETED · "L1 recon done"
                                    ⇒ BASE POINTS FROZEN HERE

COMPLETED  (never returns to LIVE)
 ├─ cricsheet not posted yet     → "L1 recon done"
 ├─ cricsheet posted, diffs open → "L2 recon pending"
 └─ cricsheet posted, all clear  → "L2 recon done"
```

### 2. The L2 baseline is the **reconciled L1 value**

The number cricsheet is compared against at L2 is **exactly what was published and frozen as base
points at the L1-done transition, after all approved L1 overrides were applied**.

An approval can be **S1** (cricapi), **S2** (ESPN) or **Manual** (hand-typed), so the baseline value
may come from either feed or from neither. It is therefore **not** "ESPN's value", **not** cricapi's
value, and **not** a value recomputed from the raw feeds on a later run.

> ⚠️ **Corrected 7 Aug 2026.** An earlier draft of this plan said *"The L2 baseline for a
> single-source field is ESPN's L1 value, by definition."* That is **WRONG**. It silently discards
> the owner's approval — a Manual or S1 adjudication on that field is overwritten by whatever ESPN
> happens to return on the next run. The rule above replaces it everywhere.

**The architectural consequence — this is the part that changes what we build.** Because base points
freeze at L1-done, the baseline must be **READ FROM THE FROZEN RECORD, never recomputed**. Today it
is recomputed: `reconciled_provisional()` (:1481) rebuilds the provisional cut from live cricapi +
ESPN on every run and L2 compares against that (:1789). That recomputation is the root cause of the
phantom `dots 0→N` review rows — it wasted the owner's time *and* corrupted settled points. The fix
is not "use ESPN's dots". The fix is **stop recomputing**.

### 3. Single-source fields get NO L1 recon

`dots`, `maidens` — ESPN only; cricapi carries neither; cricsheet carries both.

- **At L1 there is no second number**, so there is no comparison to make. ESPN's value is accepted
  and does **not** block COMPLETED.
- **ESPN's value ABSENT** for a bowler who bowled ⇒ that is unconsumed data ⇒ the match stays LIVE
  (rule 4).
- **At L2, cricsheet reconciles them against the reconciled-L1 baseline** (rule 2) — the first and
  only validation these fields ever get.

"Accepted at L1" is scoped to *these two fields, because nothing else supplies them*. It is **not** a
statement that ESPN is the better feed — see the guard in Phase 2.1.

### 4. Nothing goes unconsumed

If the bot receives data it cannot attribute to a player, or a player it cannot score fully, the
match **stays LIVE** and the item appears in the Recon tab. No silent zeros, no dropped rows.

Corollary (supersedes `RECON_REVIEW_WORKFLOW.md` locked decision 4, 7 Aug 2026): a **cricapi-only**
match has no ESPN, therefore no dots, therefore unconsumed data — it stays **LIVE**, not "COMPLETED
but FLAGGED".

### 5. Identity never appears in the Recon tab

Recon answers *"which value is right?"*. Identity answers *"who is this?"*. Different question,
different tab. See the routing rule below.

---

## Answering the identity question

**No new tab.** ESPN's `athlete.id` **IS** the cricinfo id (`build_registry.py:336`), so a
"Needs ESPN PID" tab and the existing **Needs Cricinfo ID** tab are the same tab. One destination.

**Current routing is half-right.** `wc_fps_to_csv.py:1898` sends a non-`ci:` player to Needs
Cricinfo ID — correct. But the `else` branch at **:1913 emits a `param: "ID"` row into Recon
Review** for a player who is already `ci:`-anchored yet missing from the official card. That's the
mis-routing you're seeing, and it explains the **37 rows in your tab with no matching published
row** — they are identity, sitting in the value tab.

**The fix — use the third feed to answer it instead of asking you.** We now have ESPN's athlete id
per match, so "did he actually play?" is answerable without a human:

| ESPN saw him play this match? | meaning | route |
|---|---|---|
| **yes** | he played; cricsheet just spells him differently and his `cs_id` didn't resolve | **identity failure** → Needs Cricinfo ID, hold his provisional value |
| **no** | neither ESPN nor cricsheet has him — he genuinely didn't play | not an anomaly; score him as DNP |

That empties `param: "ID"` out of Recon Review entirely. Recon Review is left with **only** value
decisions — which is what makes it small enough to actually use.

---

## PHASE 0 — Ship what's done · ✅ DONE 7 Aug (`2fc2cf3`) · low risk

Shipped. All 834 approvals preserved through the rebase (CI had pushed 18 commits); 833 now on
`ci:` pids, 1 unmappable (`slug:fabian-allen`).

- `git pull --rebase` **first** — CI writes `recon_overrides.json` every run
- re-run the re-key if CI added rows since
- commit + push

---

## PHASE 1 — Implement the state model · ~2 days · medium risk

### 1.1 Split the axes
- New `classify_recon_state()` → `L1_OPEN` | `L1_DONE` | `L2_PENDING` | `L2_DONE`
- `classify_match_status()` (:1395) reduces to: `LIVE` until (L1 done **AND** all consumed), then
  `COMPLETED`. Forever. It never un-completes.
- New sheet column `Recon State`, written after the existing recon columns
- Draft app reads it **by name, optional** — absent ⇒ current behaviour, so older tours keep working

### 1.2 Single-source fields stop blocking L1
- `RECON_L1_SINGLE = ["dots", "maidens"]` — accepted from ESPN, never L1-compared
- **Present** ⇒ proceed to COMPLETED
- **Absent** (bowler bowled, no ESPN row) ⇒ that's rule 4 — LIVE + Recon tab row
- **No ESPN at all** (cricapi-only match) ⇒ same thing at match scale: LIVE. This is the branch that
  currently returns `COMPLETED_FLAGGED`
- Their L2 baseline is **whatever the frozen L1 record holds** (§1.3) — normally ESPN's accepted
  value, but a **Manual** approval on `dots`/`maidens` wins and must survive. Do **not** re-read
  ESPN for the baseline.
  ⚠️ An **S1** approval on `dots`/`maidens` is not implementable and must be rejected at input:
  `_resolve_override_value` resolves S1 out of `capi_pid`, whose rows are `blank_perf`-derived and
  carry no dots — so approving S1 would silently write **0**, not "cricapi's value"

### 1.3 Freeze the reconciled L1 record — per field, not just the total
This is the load-bearing change, and it is bigger than the old plan admitted.

`registry/settlement_snapshots.json` is **points-only**: `record_settlement()` (:2430) stores one
`points` int per `(match_key, pid)`. A single total cannot serve as the baseline for a *field-level*
L2 comparison — which is precisely why L2 fell back to recomputing one.

- Extend the frozen record to the **reconciled L1 perf**: every `RECON_L2` field (:1199) plus `b`,
  `balls`, `played`, `bat_order`, `dismissal`, plus the scored total already stored
- Store, per overridden field, **which source the approval came from** (`S1` / `S2` / `Manual`) so
  the record is self-explaining and an audit can show *why* the frozen number is what it is
- **Trigger:** freeze on the **L1-done transition**. It currently fires on any COMPLETED publish
  (:1982, :1991, on `COMPLETED` *or* `COMPLETED_FLAGGED`), which is a different and looser moment
- Keep WRITE-ONCE. Everything about the file's value comes from never being rewritten
- Back-compat: a points-only legacy row stays valid for the total-level audit; the field-level
  baseline is simply unavailable for it (see Phase 3 re-seed)

### 1.4 L2 reads the frozen record — delete the recompute path
- `reconciled_provisional()` (:1481) stops being the L2 baseline; the L2 comparison at :1789 reads
  the frozen L1 record instead
- `build_provisional_cut()` / `merge_espn_into()` stay exactly where they are — they build the
  **emit** path (the live/provisional cut people see before L1 is done). Only the *baseline* stops
  being derived from them
- A match with no frozen record yet (still LIVE) simply has no L2 comparison — correct by
  construction: cricsheet can't revise a number that was never published
- This is what kills the phantom `dots 0→N` class permanently. Not a tolerance, not a special case
  for dots — the comparison is defined against a stored value, so it cannot invent a difference

### 1.5 Route identity out of Recon Review
- Delete the `param: "ID"` emit at :1913
- Apply the ESPN-saw-him-play discriminator above
- One-off: move the 37 stranded rows to Needs Cricinfo ID

---

## PHASE 2 — Nothing unconsumed · ~1 day · medium risk

Three live violations of rule 4:

### 2.1 ESPN-only player loses everything but dots 🔴
`merge_espn_into`'s `elif e.get("played")` branch (:1287) builds a fresh `blank_perf` and copies
**only** `dots` and `maidens`. ESPN's runs, balls, wickets, boundaries are discarded. Measured:
**published 4 pts vs 110 earned.**
→ Copy ESPN's full record when it has a real performance.

**Guard — this is not a base flip.** It applies only where **cricapi has no row at all**, so there
is nothing to flip away from; it is filling a hole, not preferring a feed. Measured against
cricsheet on 57 disputed fields, cricapi is right **33/56 (59%)** overall — runs 75%, wickets 64%
to cricapi; 4s 71%, 6s 100% to ESPN. In fantasy points the two are near a coin flip (cricapi 444
error vs ESPN 312; catastrophes ≥30 pts, 7 v 7). **Do not flip the base to ESPN.**

### 2.2 ESPN-only player is never even compared
`compute_l1_gaps` iterates `for pid in capi_pid` (:1369), so a player cricapi never listed produces
no gap and no flag.
→ Iterate `capi_pid | espn_pid`; present-in-one/absent-in-other is a reportable gap.

### 2.3 `xcheck` is assigned and never read
AST walk: STORE at [1267, 1743, 1748], LOAD only at [1286, 1291] — both inside the function. A
detected `runs_conceded` conflict is found and binned.
→ Route it into the Recon tab.

Plus the general guard: any feed row that resolves to no player, or any squad player with a
partially-sourced score, holds the match LIVE with a named row.

---

## PHASE 3 — Repair already-settled data · ~1 day · low risk (data only)

- **14 held LPL rows — 95 dot points withheld.** The L2 hold loop iterates `RECON_L2` (:1814), which
  **includes `dots`/`maidens`**, and wrote a *recomputed* baseline back over cricsheet's exact
  figures. ONE fix, not two — take the held value from the frozen record (Phase 1.4). Excluding
  `dots`/`maidens` from the hold is NOT permitted under rule C: they are single-source at L1, so
  cricsheet is their first validation and therefore exactly what the approval gate exists for. And
  take the held value from the frozen record (Phase 1.4) rather than a fresh recompute. Verify after
- **The Hundred: 785 bowling points never awarded** (441 Men's M1 + 344 Women's M1). The seeded
  baseline scored **0 dots on 100% of bowler-rows**; Gleeson settled at 4, the row now reads 145.
  **Needs your call — see Decision B**
- **Hundred Women's baseline is lying by omission** — 0 seed / 158 live / 287 `unknown`.
  `seed_settlements.py` pass 1 resolved no pids, so pass 2 froze *today's* numbers as "settled".
  It now asserts "nothing changed" on a match whose real swing is ~+344. Re-seed from the 22-Jul cut
  — and re-seed into the **field-level** record shape from §1.3, or the same class recurs the moment
  L2 needs a per-field baseline
- **921 pts on pid-less ghost rows** the draft cannot join (386 Hasaranga). Should largely resolve
  once the ESPN id anchor is committed — verify, then backfill
- **Dedupe `recon_overrides.json`** — 13 duplicate rows from the re-key
- **4 orphaned pids** — `ci:1150021`, `ci:459508`, `ci:859899`, `slug:fabian-allen`

---

## PHASE 4 — `unknown` ≠ `zero` · ~2 days · HIGH risk

The root defect. `blank_perf()` (:634) zero-initialises everything, so "not supplied" and "genuinely
zero" are the same value. Rule 4 cannot be enforced structurally until they differ.

**Per-field provenance sidecar**, not `None` sentinels — `None` would propagate into `score()` and
break arithmetic in ~50 places; a sidecar leaves `score()` and all 205 tests untouched.

**The sidecar must be frozen with the record.** Provenance is not a runtime-only convenience: the
frozen L1 record from §1.3 stores, per field, both the reconciled value *and* where it came from
(`cricapi` / `espn` / `approved:S1|S2|Manual` / `unsupplied`). That is what lets L2 answer "is this
field even comparable?" from stored state instead of re-deriving it from feeds that have since
moved. Recomputing provenance would reintroduce exactly the bug §2 exists to kill.

Then "unconsumed" becomes a *derived* property rather than a hand-maintained set that has to guess
direction — which is exactly how 2.1 slipped past the gate added last week.

**Verification:** a full run's output must be byte-identical to the previous run until the gate
starts consuming provenance.

---

## PHASE 5 — Points delta + drill-in · ~2 days · medium risk

`Points Delta` per match and per player: **L1-frozen total vs L2-official total**. The frozen
**total** already exists (write-once snapshots); the frozen **per-field values** the drill-in rows
need arrive with §1.3 — Phase 5 depends on it, and cannot be built first.

```
┌─────────────────────────────────────────┐
│ Match 2 · KR v DS          ✅ COMPLETED │
│ 🔵 L2 recon pending · 2 items · −17 pts › │
└─────────────────────────────────────────┘
        ↓ tap
┌─────────────────────────────────────────┐
│ L1 frozen → L2 official                 │
│ Nuwan Thushara      80 → 72      −8     │
│   wickets     3 → 2   (L1: approved S2) │
│ Maheesh Theekshana  51 → 42      −9     │
│   runs_conc  28 → 37                    │
│ Net −17       [Approve all] [Review]    │
└─────────────────────────────────────────┘
```

Note what is **absent** from that mock: the old `dots 8 → 0` rows. Under the corrected baseline they
cannot occur — the 8 is read from the frozen record, so cricsheet confirming 8 is silent and only a
genuine official revision appears. Showing the approval source inline (`L1: approved S2`) is what
makes an owner decision visibly durable rather than something the next run can quietly undo.

Drill-in reuses `app/audit/page.tsx` **scoped to one match** — that scoping is what fixes the
"too complicated" problem, not a rewrite.

---

## PHASE 6 — Hardening · ~1 day · low risk

- **`L1_RUN_TOL = 1`** (:1342) hides up to 7 points/row; 535 (runs, balls) combos move ≥3 pts. Make
  the tolerance **points-based, not runs-based** — `points_gap()` (:1219) already does this for L2
- **Fields L1 never compares** — `catches`, `runs_conceded`, `runouts`, `balls`, `lbwb`: 22%+ of
  scoring exposure, resolved silently. Extend once 2.3 shows the row volume is manageable
- **ESPN `limit=600`, no pagination** (:850, and :782 in the dead helper) — an ODI is 600 legal balls
  *plus extras*, so commentary is likely truncated and nobody has verified which end gets cut. A
  truncated tail means missing dots, which under rule 4 must read as unconsumed, not as zero
- **`espn_get`'s bare `except: return {}`** turns a network blip into "feed says nothing", scored
  as zeros. Distinguish *fetch failed* from *genuinely empty*
- **`espn_dots()` (:780) is dead** — zero call sites. Delete

---

## Decisions still open

**B. The Hundred's 785 unawarded bowling points**, on contests already settled. Leave settled and
fix forward / recompute and tell your friends / show both. A people question, not a code one.

**Sequencing.** Full plan ≈ 8 days. Fastest path to safe: **Phase 0 → 2.1 → 2.2 → 1.3 → 1.4 →
Phase 3**, about 2½ days. 1.3/1.4 moved into the fast path deliberately: Phase 3's LPL repair is
undone by the next CI run unless the baseline is being read from the frozen record rather than
recomputed, so repairing data without them is repairing it twice.

*(Your model resolved the other decisions: single-source handling answers A; "stays LIVE until
consumed" answers C; identity routing is answered above.)*
