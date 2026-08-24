<!-- SUPERSEDED BANNER — added 24 Aug 2026 -->
> ⚠️ **HISTORICAL — kept for rationale, not as current truth.** This document assumes the cricapi↔ESPN dots investigation. cricapi is gone; Cricbuzz supplies dots and maidens at L1 on the tours that have it. Every feed is keyless and unmetered now, so any quota reasoning here is moot. **Current architecture: `CLAUDE.md`. Trust the code over this file.**

# Recon / dots investigation — handover, 7 Aug 2026

Written so this survives you being offline.

> ## ⚠️ STATUS UPDATE — 7 Aug 2026, later the same day
> **This document was written before the work shipped. It is now LIVE.** Four commits are pushed
> to `main` and CI runs from them:
> - `2fc2cf3` ESPN id anchor · single merge implementation · completeness gate · orphan guard + re-key
> - `5ebd25f` ESPN-only player keeps its full record · ghost players are compared (union, not cricapi's keys)
> - `491f499` settlement freezes the reconciled-L1 perf **per field** + `field_sources` (S1/S2/Manual)
> - `2d7dcce` L2 reads `settled_baseline()` — the recompute path is no longer the baseline
>
> **205 tests** (was 176). Any statement below that this work is "uncommitted", that
> `record_settlement` is points-only, that the ESPN-only player still scores 4, or that
> `compute_l1_gaps` iterates `capi_pid` alone, is **superseded** — kept for the historical record.
> Still genuinely unfixed: `xcheck` never read, `L1_RUN_TOL=1`, dead `espn_dots()`,
> ESPN `limit=600` with no pagination, and the Phase 1.1/1.2 state-model split.


---

## How to read this document (added 7 Aug, after the owner-locked spec)

This file has two halves, written hours apart:

- **The top half** ("The one-line story" → "Your counter-point on cricapi-as-source-of-truth") was
  written *before* the two audits landed. Several of its claims were later refuted.
- **The bottom half** ("AUDIT RESULTS", both workflows landed) is the corrected picture.

Rather than delete the refuted claims — the history is the reason the code looks the way it does —
every superseded statement is **marked inline** with a `⚠ SUPERSEDED` / `⚠ CORRECTED` block that
points at the correction. If you read a claim in the top half with no such block, it survived.

The authority over both halves is the **owner-locked recon spec of 7 Aug 2026**. Its two rules that
this document originally got wrong:

- **Rule B (the baseline).** The L2 baseline is **the reconciled L1 value** — exactly what was
  published and frozen as base points once all approved L1 overrides were applied. That approval may
  be S1 (cricapi), S2 (ESPN) **or Manual**, so the baseline value can come from either feed or from a
  hand-typed number. It is *not* ESPN's value, *not* cricapi's value, and *not* a value recomputed
  from raw feeds on a later run. **Base points freeze the moment a match goes COMPLETED / "L1 recon
  done", and the baseline must be READ FROM THAT FROZEN RECORD.** Recomputing the provisional cut on
  every run is the root cause of the phantom `dots 0→N` rows and of the corrupted settled points
  below — see the correction under bug #1.
- **Rule D + rule C (single-source fields).** `dots` and `maidens` come from **ESPN only**. At L1
  there is no second number, so there is **no L1 comparison** for them and their presence does not
  block COMPLETED — but an ESPN value that is *absent* for a bowler who bowled is **unconsumed
  data**, and unconsumed data holds the match **LIVE** with a named row in the Recon tab.

Two further spec rules that re-classify things claimed here:

- **Rule E — identity never appears in the Recon tab.** Recon Review answers "which value is
  right?"; identity answers "who is this?". The "unresolvable / identity split" rows counted below
  are *identity* work, not recon work, and belong in the **"Needs Cricinfo ID"** tab. No new tab is
  needed: ESPN's `athlete.id` **is** the cricinfo id (`build_registry.py:336`), so "Needs ESPN PID"
  and "Needs Cricinfo ID" are the same tab.
- **Rule F.1 — `CLAUDE.md`'s old header line "dots only from cricsheet" was stale and wrong.** Dots
  come from **ESPN** in the provisional cut and from cricsheet once official. cricapi never supplies
  them. That header was corrected the same day; this document has always had it right.

---

## The one-line story

`dots` are supplied by **ESPN only** (cricapi carries none). `blank_perf()` zero-initialises
every stat, so **"no ESPN row" and "genuinely zero dots" are the same value: 0**. Everything
below is a consequence of that single collapse.

> Under the locked spec this collapse has a name: it turns **unconsumed data into a silent zero**,
> which rule D forbids. The correct behaviour for "ESPN has no row for a bowler who bowled" is
> *hold the match LIVE and name him in the Recon tab* — never publish the 0.

---

## What was actually wrong (4 distinct bugs, one root)

### 1. Phantom `dots 0→N` rows in Recon Review — ⚠ MITIGATED, **not** fixed (was: "FIXED")
The L2 baseline was rebuilt by `_by_pid()` (strict id-only) while the **published** number came
from `match_squad_to_perf()` (squad-anchored, id-first, fuzzy fallback). Two matchers, allowed to
disagree, and disagreement read as "the value changed".

Verified against the pre-cricsheet snapshot: for LPL Match 2 the sheet **already displayed**
3, 9, 9, 7, 9, 8, 1, 7, 3 dots. The `0` never happened.

Of the 59 rows in your Recon Review tab I could classify: **12 phantom, 10 real, 37 unresolvable**
(player had no published row to compare — identity split).

> **⚠ CORRECTED — locked spec rule B.** The diagnosis above is right about *how* the phantom rows
> were produced, but the fix I shipped (make both paths use one matcher, `merge_espn_into`) only
> makes the two **recomputations** agree. The spec forbids recomputing at all: the L2 baseline must
> be the **frozen reconciled-L1 record** — the published number after approved overrides, whatever
> feed or manual entry it came from. Any live rebuild of the provisional cut can drift again the
> moment a matcher, a feed backfill or the registry changes, and it *discards the owner's approval*
> (bug #4 is exactly that failure mode arriving by a different door). So:
>
> - the class of phantom row is **narrowed**, not closed;
> - the real fix — **read the baseline from the frozen record** (`registry/settlement_snapshots.json`
>   / the `SETTLEMENT AUDIT` tab already exist as the write-once store) — is **not done** and is now
>   the top item on the merged open list at the end of this file.
>
> **⚠ Rule E.** The 37 "unresolvable — identity split" rows are not recon rows at all. They are
> "who is this?" and belong in the **Needs Cricinfo ID** tab, held at their provisional value. The
> spec's discriminator, which uses ESPN as the third feed instead of asking you: *ESPN saw him play
> this match* → identity failure → Needs Cricinfo ID, hold his provisional value. *ESPN did not see
> him either* → he genuinely did not play → score as DNP, not an anomaly.

### 2. The L2 hold wrote the fabricated 0 into the live sheet — CAUSE FIXED
The hold policy is correct ("never silently revise a settled number") but it was pinning a
fabrication. LPL Match 2 lost **105 points** across 10 bowlers while badged `COMPLETED`.
Self-heals on the next run now that the baseline is correct.

> **⚠ CORRECTED — refuted by the dots audit (bottom half).** "Self-heals on the next run" is
> **wrong**. Fixing `build_provisional_cut` does **not** repair numbers already published: the held
> rows stay held until they are approved S2 (or the hold stops pinning a fabricated value). 95 dot
> points remain withheld from the LPL settled sheet, and The Hundred's 785 bowling points were never
> awarded at all. See "Dots — the confirm you asked for".
>
> The *policy* is also right for the wrong reason as written here. Under rule C, `dots`/`maidens`
> **are** L2-reconciled against cricsheet — they are not exempt from the hold. What made the hold
> destructive was that it pinned a **recomputed** baseline instead of the **frozen reconciled-L1**
> one (rule B). With a frozen baseline the hold is safe and correct.

### 3. Matches published COMPLETED with bowlers scored on dots=0 — GATE FIXED (one direction only)
`classify_match_status` only asked "did two feeds agree on r/w/4s/6s?". Dots weren't compared,
so their absence was invisible. Confirmed rows that went COMPLETED this way:

| match | status at flip | bowler | overs | dots |
|---|---|---|---|---|
| LPL M2 KR v DS | COMPLETED | Shaheen Afridi | 3.0 | 0 |
| LPL M2 KR v DS | COMPLETED | Vishwa Lahiru | 4.0 | 0 |
| LPL M4 DS v JK | COMPLETED | Traveen Mathews | 3.0 (3 wkts, 116 pts) | 0 |
| LPL M4 DS v JK | COMPLETED | Vishwa Lahiru | 2.0 | 0 |
| MLC M1 TSK v SEO | completed | Dasun Shanaka | 2.7 | 0 |

4 overs with 0 dots is not cricket. T20 scoring: `dot=1`, `maiden=12` — and maidens come from
ESPN too, so a lost row drops both.

> **⚠ CLARIFIED — locked spec rule C.** "Dots weren't compared" reads as though dots *should* be
> L1-compared. They should not, and cannot: cricapi supplies none, so there is no second number and
> **no L1 comparison exists for dots or maidens**. ESPN's value is simply accepted, and its presence
> does not block COMPLETED. What was missing is a **completeness** check, not a comparison —
> "a bowler bowled and ESPN gave us nothing" is *unconsumed data* (rule D) and holds the match LIVE.
> That is what the `unsourced` gate now does, and it is the right shape.
>
> **⚠ INCOMPLETE.** The gate only fires in one direction (cricapi-has-bowler / ESPN-missing). The
> opposite direction — a player **ESPN has and cricapi does not** — is never compared and still
> publishes COMPLETED. That is the live bug in "🔴 NEW LIVE BUG" below, and it is a direct rule-D
> violation.

### 4. 83 of 131 stored approvals were DEAD — FIXED
`recon_overrides.json` is pid-keyed and was **never re-keyed** by the 25 Jul `ci:` migration.
Approvals silently stopped applying → the L2 baseline fell back to raw cricapi → **the same row
reappeared every run no matter how many times you answered it** (this is the "E Perry 38→64
keeps propping up" you spotted).

82 re-keyed via `pid_map.json`. Backup: `registry/recon_overrides.json.bak-prekey-20260807`.
Each carries `_rekeyed_from`.

> **⚠ Note — this is rule B in miniature.** "The L2 baseline fell back to raw cricapi" is only
> possible because the baseline is **recomputed from raw feeds** instead of read from the frozen
> reconciled record. Re-keying the approvals restores them, but any future key change, feed
> backfill or matcher change can silently discard an approval again by the same mechanism. The
> frozen-record fix closes the class; the re-key closes today's instance.
>
> **⚠ Follow-up found by the audit:** the re-key wrote new rows **without removing the originals** —
> `registry/recon_overrides.json` now has **13 duplicate rows**. Harmless for apply (both rows name
> the same source, and the code resolves **last-wins**) but it corrupts any count taken off the file.

---

## Why only dots failed, and not all their points

Your question, and it's the right one. A player's row is assembled from two feeds:

```
cricapi row  →  runs, balls, 4s, 6s, wickets, conc, catches, dismissal …  (everything)
ESPN row     →  dots, maidens                                             (and nothing else)
```

- **cricapi/squad side fails** → player scores 0 across the board, or gets no row. Loud and total.
  (This is the Hasaranga class.)
- **ESPN side fails** → everything cricapi supplied is still correct. Only ESPN's *unique*
  contribution collapses. ESPN uniquely contributes dots and maidens. Nothing else.

So it isn't "the player broke" — it's "one of his two sources broke, and that source's entire
unique contribution is dots."

> **⚠ CORRECTED — the "and nothing else" is too strong, in both directions.**
>
> 1. **ESPN is a full scorecard source, not a dots sidecar.** The emit path falls back to it
>    wholesale (`elif espn_perf: perf = api_perf if api_perf else espn_perf`) because cricapi's
>    `match_scorecard` returns "not found" for most franchise-league matches. It also **backfills
>    bowler `balls`** when cricapi omits the `overs` field — which it always does on 100-ball cards
>    (`merge_espn_into`, wc_fps_to_csv.py:1279-1281). On The Hundred, a lost ESPN row therefore
>    zeroes the **entire** bowling block (`balls > 0` gates wicket/dot/maiden/econ) — that is the
>    Gleeson 4-for → 4-pts bug, not a dots-only loss.
> 2. **In the other direction the "nothing else" is literally coded, and it is a bug.** For a player
>    ESPN has and cricapi does not, `merge_espn_into`'s `elif e.get("played")` branch builds him from
>    `blank_perf` and copies **only dots + maidens** off a *full* ESPN record
>    (wc_fps_to_csv.py:1288-1290). Measured: **published 4 pts vs 110 pts actually earned.**
>
> The correct one-liner is: *dots and maidens are the only fields for which ESPN is the **sole**
> supplier — but ESPN is capable of supplying the whole card, and the merge throws that away.*

---

## Changes made (SHIPPED — see the status update at the top)

`wc_fps_to_csv.py`
- `match_squad_to_perf(..., quiet=False)` — suppresses global side effects so the matcher can be
  re-run for the baseline without double-reporting anomalies.
- `merge_espn_into()` — **single** merge implementation, used by both emit and baseline, so they
  cannot drift apart again. Returns `(xcheck, unsourced)`.
  - ⚠ but `xcheck` is **assigned and never read** — stored at :1267/:1743/:1748, loaded only at
    :1286/:1291 inside the function itself. A detected `runs_conceded` conflict is computed and
    thrown away. See "The base's REAL problem".
  - ⚠ and its `elif e.get("played")` branch copies only dots+maidens off a full ESPN record — the
    110-point bug above.
- `build_provisional_cut()` — baseline built with the SAME matcher emit uses.
  - **⚠ SUPERSEDED by locked spec rule B.** A same-matcher **recomputation** is still a
    recomputation. The baseline must be read from the frozen reconciled-L1 record. Treat
    `build_provisional_cut` as a stopgap that removes today's drift, not as the architecture.
- `classify_match_status(..., unsourced=())` — holds LIVE when any player was scored on a field no
  feed supplied. `unsourced` beats every other non-cricsheet verdict. **This is rule D, and it is
  correct** — but see bug #3: it fires in one direction only.
- `overrides_by_match(data, known_pids=None)` — **orphan guard**: shouts when an approval is keyed
  to a pid the registry doesn't know. Never silently drops the override.
- **ESPN id anchor** (the pid fix): `parse_espn` / `espn_dots` / `espn_xi` now carry
  `athlete.id`, and `resolve_perf_pid` resolves `ci:<athlete.id>` **directly** against the
  registry. ESPN's `athlete.id` IS the cricinfo id (`build_registry.py:336`). No fuzzy, no alias
  table, no namesake risk. This is the ESPN half of the cricsheet id-anchoring work — it was
  never done, which is why name matching was still load-bearing for dots.
  - This is also what makes rule E cheap: an ESPN-anchored pid *is* a cricinfo id, so the identity
    queue is one tab, not two.

`registry/recon_overrides.json` — 82 approvals re-keyed. (⚠ 13 duplicate rows left behind; see #4.)

`tests/` — **205 passing** (was 176). New coverage: the completeness gate, `merge_espn_into`
unsourced detection, baseline-recovers-dots regression, orphan guard, ESPN id anchor.

---

## Open — needs you *(pre-audit list; see the merged list at the end for the current one)*

1. **4 orphaned override pids** the guard caught that `pid_map` couldn't fix:
   `ci:1150021`, `ci:459508`, `ci:859899`, `slug:fabian-allen`.
   `pid_map` points at ids the live registry doesn't contain. Needs a registry lookup, not a script.
   — ⚠ rule E: this is **identity** work. It belongs in the **Needs Cricinfo ID** tab, not in Recon
   Review.

2. **A design call I deliberately did not make for you.** ~~Your rule ("if you can't consume the
   data, don't move to COMPLETED") contradicts locked decision #4 in `RECON_REVIEW_WORKFLOW.md`
   ("single-feed → COMPLETED but FLAGGED"). I implemented **your** rule, so a cricapi-only match
   now holds LIVE. Risk: if ESPN never appears for a match it holds forever and never settles.
   Decide whether that case needs an explicit "settle without dots" approval as an escape hatch.~~

   **✅ DECIDED 7 Aug 2026 by the owner-locked spec (rule D).** A cricapi-only match has no ESPN, so
   it has no dots — that is unconsumed data, so it **stays LIVE** with a named row in the Recon tab.
   `RECON_REVIEW_WORKFLOW.md` locked decision 4 ("COMPLETED but FLAGGED") is **superseded, dated
   7 Aug 2026**; it is to be marked superseded there, not deleted, because it explains why the code
   was shaped the way it was. The implementation already matches the decision.

   The spec grants **no** "settle without dots" escape hatch. The forever-LIVE risk is real but is
   an owner call on the specific match if it ever bites — not a default the code may take.

3. **`unknown ≠ zero`** — the root fix. `blank_perf` must distinguish "not supplied" from "zero".
   Deliberately NOT done unsupervised: it threads through `score()` and every feed parser in a
   system that settles real money. — This is rule D expressed in the data model, and it is still the
   single highest-leverage change in the file.

4. **Committing.** The re-key only takes effect once committed — CI runs from the repo.
   Note CI also writes this file ("chore: persist sheet decisions"), so **rebase before pushing**
   or an overlapping push loses approvals.

---

## Your counter-point on cricapi-as-source-of-truth

> ## ⚠ SUPERSEDED — read the correction directly under the numbers before you use them
>
> **The conclusion I drew in this section ("ESPN wins 43-10, so cricapi-as-base is backwards") was
> REFUTED by the source-of-truth audit the same day.** Measured against cricsheet ground truth,
> **cricapi is the more accurate feed overall (33/56, 59%)**. The 43-10 tally is real but measures
> something else entirely. **Do NOT flip the base to ESPN.** The numbers are kept here because they
> are still the right evidence for a *different* claim — that the recon process works.

You asked why cricapi is the silent base when L1 exists precisely to reconcile the two.
Your own adjudications, mined from `recon_overrides.json` (scope player+match):

```
ESPN (S2)        43   77%
cricapi (S1)     10   18%
Manual            3    5%

runs  cricapi 4 | ESPN 12
wkts  cricapi 5 | ESPN 11
4s    cricapi 1 | ESPN 10
6s    cricapi 0 | ESPN 10
```

You overruled cricapi 4 times out of 5. Caveat: these are only cases that *disagreed*, so it's a
biased sample. A full audit was running when you boarded — results appended below when it lands.

> Note the `Manual 3`. Under rule B those three are as much a legitimate baseline as either feed's
> value: **the frozen reconciled-L1 value can be a hand-typed number**, and the baseline must carry
> it. Any rule of the form "the baseline is ESPN's value" or "the baseline is cricapi's value"
> silently destroys them.

~~Independent of the counts: cricapi's failures are **silent by construction** (a frozen mid-innings
scorecard looks exactly like a valid final one — that was Match 30), while ESPN's failures tend to
be **loud** (no event id → no data at all). For a system that settles money, a silently-wrong
number is worse than an absent one.~~

> **⚠ CORRECTED — half of this survived, half is refuted.**
>
> **Survives:** cricapi's frozen-mid-innings failure *is* silent by construction, and Match 30 is the
> proof. It is why completion is time-based rather than trusting `matchEnded`.
>
> **Refuted:** "ESPN's failures tend to be loud" is not true of the fields that matter. ESPN's
> fielding and run-out credits are **regex-parsed out of commentary text** (:920-929), and its
> commentary fetch is hard-capped at `limit=600` with **no pagination** (:850) — an ODI is 600 legal
> balls **plus** extras, so a long innings silently truncates. Those are silent-wrong failures too.
> The asymmetry argument does not carry the base decision; the cricsheet-measured accuracy below
> does, and it points the other way.

---

## AUDIT RESULTS

*(Two adversarially-verified workflows were still running at handover: `dots-scoring-audit` and
`source-of-truth-audit`. Results appended here when they complete.)* — **both landed; see below.**

---

# AUDIT RESULTS (both workflows landed, 7 Aug)

Every finding below was attacked by two independent refuters. **Both headline claims were
refuted.** What follows is the corrected picture, not the agents' first answers.

## ⚠️ CORRECTION to the "ESPN wins 43-10" claim in the section above

That number is real but **it cannot measure the base choice**, and I drew the wrong conclusion
from it.

When L1 flags a disagreement, `apply_recon_overrides` stamps the human's pick straight from the
RAW feed dict (`_resolve_override_value`, wc_fps_to_csv.py:1424-1433). The published number is
therefore **identical whichever feed was the base**. The 43-10 tally measures
`P(ESPN right | feeds already disagreed AND a human adjudicated)` — it says the recon works. It
says nothing about which feed should hold the default seat.

> **Rule B follow-on, and this is the important one.** `_resolve_override_value` resolves S1/S2 out
> of the **live** feed dicts at run time, and Manual out of the stored value. That is fine at L1, at
> the moment of freezing. It is **not** a baseline: re-resolving `S2` against a feed that has since
> been backfilled produces a *different* number than the one that was published and settled. The
> frozen record must store the **resolved value**, not the instruction to go re-resolve it.

**Measured against cricsheet ground truth (57 disputed fields, 42 player-matches):**

| field | winner | record |
|---|---|---|
| runs | **cricapi** | 24/32 (75%) |
| wkts | **cricapi** | 7/11 (64%) |
| 4s | ESPN | 5/7 (71%) |
| 6s | ESPN | 6/6 (100%) |
| **overall** | **cricapi** | **33/56 (59%)** |

So ESPN is **not** the better feed overall — the opposite of what I told you. In fantasy points
(the currency that settles money) it's much closer: cricapi 444 FP of error vs ESPN 312, and
catastrophes ≥30 FP are **7 vs 7**. Essentially a coin flip.

**Do NOT flip the base to ESPN.** Nothing measured supports it.

**Your own accuracy: 30/30 = 100%.** Every L1 call you made matches cricsheet. An earlier agent
claimed you'd mis-settled 120 points on wickets; that was refuted — it came from counting
duplicate override rows first-wins instead of last-wins (the code resolves last-wins).

**Why cricapi holds the seat, legitimately:** it's a whole-card snapshot with a structured
`catching` block, whereas ESPN's fielding/run-out credits are regex-parsed out of commentary
text (:920-929) and its commentary fetch is hard-capped at `limit=600` with **no pagination**
(:850) — a real risk on ODIs (600 legal balls + extras).

**Your instinct was still right about the intent.** The honest answer: the base is a placeholder
for the ~94% of field-slots where both feeds say the same thing. Something has to be the dict you
merge into. Where they disagree materially, the match is held LIVE until you pick.

## The base's REAL problem (this part of your point stands)

The base silently decides the fields **L1 never compares at all**: `catches`, `runs_conceded`,
`runouts`, `balls`, `lbwb` — **22%+ of scoring exposure**, resolved with no human ever shown a
disagreement.

Worse, the disagreement IS detected and then discarded: `xcheck` is computed in
`merge_espn_into` and **never read** — AST walk confirms STORE at [1267,1743,1748], LOAD only at
[1286,1291], both inside the function. A `runs_conceded` conflict is found and thrown in the bin.

> Under rule D this is unconsumed data by another name: the bot received a second opinion, could not
> attribute it, and dropped it silently. A detected `runs_conceded` conflict should surface as a
> named Recon row and hold the match LIVE, not vanish.

## 🔴 NEW LIVE BUG — violates your rule 1 directly

**An ESPN-only player is never compared, never flagged, and the match still publishes COMPLETED.**

`compute_l1_gaps` iterates `for pid in capi_pid` (:1369). A player cricapi never listed is not in
that dict, so no gap exists. `merge_espn_into`'s elif branch builds him from `blank_perf` copying
only dots/maidens. The new `unsourced` gate does **not** fire — it only detects the opposite
direction (cricapi-has-bowler / ESPN-missing). Verified by executing the real module:

```
published 4 pts   vs   110 pts actually earned      → status ('COMPLETED', '')
```

He gets the +4 XI bonus and nothing else. This is "skip scoring a player" happening today.

> Rule D again, in its purest form: full ESPN data arrived, 106 points of it were not consumed, and
> the match went COMPLETED with a silent near-zero. It should have stayed LIVE with his name in the
> Recon tab.

## Dots — the confirm you asked for

**630 of 661 published bowler-rows (95.3%) had dots scored.** So dots WERE working for the
overwhelming majority. But:

**The L2 hold changed already-settled numbers.** The hold iterates `for field in RECON_L2`
(:1809-1817), which includes `dots`, and writes the buggy baseline back over **cricsheet's exact
figures**. The bug is self-fulfilling: the fake 0 creates the L2 gap, and the gap then imposes the
fake 0. So the bad baseline did not merely generate phantom review rows — **it changed published,
settled fantasy points.**

- **95 dot points** withheld from the LPL settled sheet (cricsheet's own figures, sitting
  unapproved in Recon Review)
- **72 dot points** destroyed in LPL Match 2 alone — the "official" cut was WORSE than the
  provisional one people saw
- 14 impossible zeros remain, 13 of them L2-hold victims

**Fixing `build_provisional_cut` alone does NOT repair the already-published numbers.** The 14
held rows must be approved S2, ~~or the hold must stop covering ESPN-only fields when cricsheet is
the scorer (cricsheet is strictly better there — holding against it is never right)~~.

> **⚠ CORRECTED — the struck escape route is not sanctioned by the locked spec.** Rule C is explicit
> that at L2 **cricsheet reconciles `dots`/`maidens` against the reconciled-L1 baseline**, exactly
> like every other field. They are single-source at **L1** only; they are not exempt from L2 review,
> and the hold must keep covering them.
>
> The defect was never "the hold covers dots". It was **rule B**: the hold pinned a *recomputed,
> fabricated* baseline instead of the *frozen reconciled-L1* one. With a frozen baseline the hold
> does the right thing — it shows you `4 → 145` and waits for your S2 approval instead of silently
> overwriting either number.
>
> So the repair is two parts, both required: (a) freeze the baseline; (b) clear the 14 already-held
> rows by approving S2, since a fix to (a) cannot retroactively unhold them.

## Other confirmed findings

- **The Hundred's seeded settlement baseline scored 0 dots on 100% of its bowler-rows.**
  785 bowling points never awarded (441 Men's M1 + 344 Women's M1). Gleeson settled at **4**;
  the same row now reads **145**.
- **The Hundred Women's baseline is silently asserting "nothing changed"** on a match whose real
  swing is ~+344. `seed_settlements.py` pass 1 resolved no pids for that tour, so pass 2 froze
  TODAY's numbers with provenance `unknown` (0 seed / 158 live / 287 unknown, vs 30 seed for Men's).
  — ⚠ a frozen record with provenance `unknown` is **not** a rule-B baseline. It has to be re-seeded
  from the genuinely-published cut, or the freeze is just the recomputation bug with a timestamp.
- **921 fantasy points sit on pid-less ghost rows the draft app cannot join** — 386 of them
  Hasaranga's across 6 LPL matches. +37 in Hundred M. — ⚠ rule E: **identity**, → Needs Cricinfo ID,
  hold the provisional value. Not Recon Review rows.
- `L1_RUN_TOL=1` hides up to **7 points** per row; 535 (runs, balls) combinations move ≥3 pts.
- `espn_dots()` (:780) has **zero call sites** — dead code.
- ESPN `playbyplay` is fetched with `limit=600` and **no pagination** (:782, :850). An ODI is 600
  *legal* balls **plus** extras, so the tail of a long innings is silently dropped — and dots and
  maidens are counted off exactly that feed.
- `registry/recon_overrides.json` has **13 duplicate rows** — the 7 Aug re-key wrote new rows
  without removing the originals. Harmless for apply (same source, last-wins) but corrupts any count.

## Not delivered

Both design agents failed: **org monthly spend limit**. The L1-dots design and the
provenance-merge design were not produced.

---

# Merged open list (authoritative as of the 7 Aug owner-locked spec)

One list, so nobody has to reconcile the two halves above.

**Architecture — do these first**

1. **Freeze the L2 baseline (rule B).** Read the baseline from the write-once frozen record
   (`registry/settlement_snapshots.json` / `SETTLEMENT AUDIT`), storing the **resolved** value after
   approved overrides — S1, S2 **or Manual**. Stop recomputing the provisional cut on every run.
   Closes the phantom-row class, the dead-approval class and the settled-points-corruption class in
   one move. `build_provisional_cut` stays only as the seeder for matches with no frozen record.
2. **Re-seed The Hundred Women's frozen record**, which is currently `unknown` provenance and
   asserts "nothing changed" on a ~+344 swing.
3. **`unknown ≠ zero` in `blank_perf`** (rule D in the data model). Threads through `score()` and
   every parser — supervised change, real money.

**Live defects**

4. **ESPN-only player scores 4 instead of 110** — `merge_espn_into`'s `elif e.get("played")` branch
   copies only dots+maidens off a full ESPN record (:1288-1290); `compute_l1_gaps` iterates
   `for pid in capi_pid` (:1369) so he is never compared; the `unsourced` gate misses this direction.
   Must hold the match LIVE with a named Recon row.
5. **`xcheck` is discarded** — STORE :1267/:1743/:1748, LOAD only :1286/:1291. A detected
   `runs_conceded` conflict is thrown away instead of surfacing.
6. **Un-hold the 14 rows the fabricated baseline pinned** (approve S2). 95 dot points in LPL, 785
   bowling points in The Hundred; Gleeson 4 → 145.
7. **ESPN `playbyplay` pagination** — `limit=600`, no paging (:782, :850). ODIs truncate silently.
8. **`L1_RUN_TOL=1`** hides up to 7 points per row across 535 (runs, balls) combinations. Decide the
   tolerance deliberately.
9. **Delete `espn_dots()`** (:780) — zero call sites.
10. **De-duplicate `registry/recon_overrides.json`** — 13 duplicate rows from the re-key.

**Identity queue (rule E — NOT Recon Review)**

11. **4 orphaned override pids**: `ci:1150021`, `ci:459508`, `ci:859899`, `slug:fabian-allen`.
12. **921 pid-less ghost points** (386 Hasaranga across 6 LPL matches, +37 Hundred M) and the 37
    "unresolvable" rows from the Recon tab. All → **Needs Cricinfo ID**, provisional value held.
    Apply the ESPN discriminator (ESPN saw him play → identity failure; ESPN didn't → DNP) rather
    than asking a human first.

**Doc corrections mandated by the locked spec**

13. ~~`CLAUDE.md` header "dots only from cricsheet"~~ → **DONE 7 Aug**: the header now states three
    feeds with ESPN as the only source of `dots`/`maidens` (cricsheet supplies them at L2; cricapi
    never) (rule F.1).
14. `RECON_REVIEW_WORKFLOW.md` locked decision 4 ("single-feed → COMPLETED but FLAGGED") → mark
    **superseded 7 Aug 2026** (rule D: no ESPN ⇒ no dots ⇒ unconsumed ⇒ stays LIVE). Mark it, do not
    delete it — it explains the shape of the code.

**Process**

15. **Commit.** The re-key is inert until committed (CI runs from the repo), and CI also writes this
    file ("chore: persist sheet decisions") — **rebase before pushing** or an overlapping push loses
    approvals.
