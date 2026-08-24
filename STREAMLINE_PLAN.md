<!-- SUPERSEDED BANNER — added 24 Aug 2026 -->
> ⚠️ **HISTORICAL — kept for rationale, not as current truth.** This document assumes three feeds including cricapi. Every feed is keyless and unmetered now, so any quota reasoning here is moot. **Current architecture: `CLAUDE.md`. Trust the code over this file.**

# Streamlining the points bot — gaps, POC results, and the target architecture

Written 11 Aug 2026. Answers: *how do I get zero hiccups, how do player ids stay in sync across
three feeds, how do new players get added, what is the manual recon process, what are the
guardrails.*

---

## 1. The actual root cause of the last three weeks

Not bad code. **Three feeds with three different identity systems, and the weakest one is load-bearing.**

| feed | identity | reliability | role today |
|---|---|---|---|
| **cricsheet** | own person id → `crosswalk.json` → cricinfo id | authoritative, 1–5 day lag | official (L2) |
| **ESPN** | `athlete.id` **IS the cricinfo id** | keyless, unlimited, covers franchise leagues | dots/XI + fallback |
| **cricapi** | **names only — no stable id** | 100/day quota; empty cards on franchise leagues | *primary base* |

Two feeds hand us a cricinfo id for free. The third gives us names — and it is the one we made
primary. Every identity bug this month (Hasaranga, Tharindu/Milan, Dale/Glenn, Carlson/Dawson)
lives in the fuzzy name-matching that exists *only* to serve cricapi.

cricapi is also the feed that keeps failing: quota exhaustion, stub scorecards, and — confirmed by
the run diagnostics on 10 Aug — **it now returns no player data at all for Hundred/LPL fixtures it
has ids for**.

---

## 2. POC results (measured, not argued)

### 2a. Can ESPN alone score a match?
Hundred M28 (9 Aug) and M26 (8 Aug), the matches cricapi returned nothing for:
22 players each, complete batting + bowling + dots + catches + stumpings. **Yes.**

### 2b. Does ESPN carry identity?
23 of 24 players carry `espn_id`, which **is** the cricinfo id. Identity by construction, no fuzzy
matching. **Yes.**

### 2c. Is ESPN accurate? — 18 LPL matches vs cricsheet ground truth

| field | cricsheet | ESPN | verdict |
|---|---|---|---|
| maidens | 7 | 7 | ✅ exact |
| dots | 1441 | 1377 | ⚠️ 95.6% — consistent with the known `limit=600` no-pagination defect |
| **run-outs** | **20** | **0** | ❌ **ESPN run-out parsing is completely broken** |

**Run-outs are ~1.1 per match at 6 pts (assisted) / 12 (direct) — roughly 8–12 fantasy points per
match going to nobody. This is live right now on every ESPN-only match.**

### 2d. Is cricapi worth keeping for accuracy?
The owner's own measurement (57 disputed fields): cricapi right 59% of fields — but in **fantasy
points**, cricapi contributed **444 FP of error vs ESPN's 312**. On the thing that settles money,
ESPN is already the better single source.

---

## 3. Target architecture

```
ESPN (live, id-anchored, keyless)  ──►  provisional points
                                            │
cricsheet (official, id-anchored) ──────────┴──►  ONE reconciliation (L2)  ──►  settled
```

**cricapi drops out of scoring entirely.** It stays only for match discovery, and even that is
optional — you already offered to put the ESPN series id in a GSheet, which removes the last
dependency.

### What this deletes
- **All fuzzy name matching in the scoring path.** Both feeds carry cricinfo ids; matching becomes
  a dictionary lookup. The entire Hasaranga/Carlson/Phillips bug class becomes structurally
  impossible, not just guarded against.
- **L1 recon.** It exists solely to arbitrate cricapi vs ESPN. With one live feed there is nothing
  to arbitrate — no 381-row tabs, no `S1/S2` for feed disputes. **Your manual load drops to L2
  only**, which is ~6 rows a week of genuine cricsheet revisions.
- **Quota as a constraint.** ESPN is keyless.
- **The franchise-league blind spot**, which is where cricapi fails and where your tours live.

### What it costs — stated honestly
No second live opinion before cricsheet lands. That sounds worse than it is: the cross-check was
never what made money safe — **the cricsheet gate is**. You already don't settle before cricsheet.
And per 2d, the second opinion we're giving up is the one with *more* points error.

### Prerequisites — do NOT flip before these
1. **Fix ESPN run-out parsing** (blocking; ~10 pts/match currently lost).
2. **Fix ESPN dots pagination** (`limit=600` → paginate; ~4% of dots).
3. Re-run the 18-match comparison; require exact maidens/run-outs and ≥99% dots before flipping.

---

## 4. How player ids stay in sync (your question, answered)

After the flip there is exactly **one** id — the **cricinfo id** — and every feed maps to it
without guessing:

| feed | how it maps | guessing involved |
|---|---|---|
| ESPN | `athlete.id` **is** the cricinfo id | none |
| cricsheet | `registry.people` name → cs id → `crosswalk.json` → cricinfo id | none |
| draft app | `players-raw.json.pid` = `ci:<cricinfoId>` | none |
| auction | same registry via `sync-registry` | none |

The crosswalk is people.csv-derived (18253/18253 unique). **Nothing is inferred from spelling.**
That is the whole answer to "how will a player's id be synced across the three".

---

## 5. New player who isn't in the squad

Today this mints a placeholder `slug:` pid and needs your intervention. After the flip it mostly
disappears, because ESPN and cricsheet both supply the id directly:

1. Player appears in a feed → id comes with them → registered as `ci:<id>` automatically.
2. Only if the id is genuinely unknown to `crosswalk.json` does a row appear in
   **Needs Cricinfo ID**, pre-filled with the derived id and the espncricinfo URL.
3. You confirm → `manual_ci_bridges.json` → auto-promoted on the next run.

That loop is already built and working (it consumed your 7 ids on 1 Aug).

---

## 6. Recon after the flip

**L1: gone.** One live feed, nothing to arbitrate.

**L2 (the only one left):** cricsheet vs the frozen ESPN value, both id-anchored.
- `S2` = take cricsheet's number. `S1` = keep what was settled. Both count as decided.
- Volume: ~6 rows/week of genuine revisions, versus the hundreds you just waded through.
- The settled value is **held** until you answer, so nothing moves under you.

---

## 7. Guardrails (what makes a hiccup visible instead of silent)

Already shipped this week:
- `COMPLETED` can never return to `LIVE` (ratcheted off the write-once settlement baseline).
- A single-sourced match is flagged, naming the missing feed.
- A tour that fails to process now **fails the workflow** — a green tick can no longer hide a
  stale sheet (this hid a NameError for an hour).
- Identity questions go to **Needs Cricinfo ID**, never the recon tab.
- Write-once settlement baseline + `/audit` proves whether anything moved since you settled.

Still to add:
- **`SAFE TO SETTLE` badge** — collapses the 5-point checklist in `RUNBOOK.md` into one green
  light per match. This is the single highest-value thing left for real-money use.
- A weekly ESPN-vs-cricsheet accuracy report, so feed drift is caught by a number rather than by
  you noticing something looks wrong.

---

## 8. Recommended order

| # | work | why now |
|---|---|---|
| 1 | Fix ESPN run-outs | **live scoring bug**, ~10 pts/match, affects settled results today |
| 2 | Fix ESPN dots pagination | ~4% of dots; same class |
| 3 | Re-run the 18-match POC; require exact match | proof before trusting |
| 4 | Flip: ESPN primary, cricapi out of scoring | deletes L1 + all fuzzy matching |
| 5 | ESPN series id via GSheet for new tours | removes the last cricapi dependency |
| 6 | `SAFE TO SETTLE` badge | one green light instead of a checklist |

Steps 1–3 are worth doing **regardless** of whether you flip — they are live bugs in the ESPN path
you already depend on.
