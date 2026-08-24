<!-- SUPERSEDED BANNER — added 24 Aug 2026 -->
> ⚠️ **HISTORICAL — kept for rationale, not as current truth.** This document assumes a cricapi-primary architecture and an open 'should we adopt Cricbuzz?' question. Cricbuzz was adopted (13 Aug) and cricapi was removed (20 Aug, `ab8583d`). Every feed is keyless and unmetered now, so any quota reasoning here is moot. **Current architecture: `CLAUDE.md`. Trust the code over this file.**

{'summary': 'Consolidate every open item across both repos into one tracked master plan, and settle the Cricbuzz question', 'agentCount': 7, 'logs': [], 'result': {'plan': '# THE MASTER PLAN — wwc-points-bot + wwc-draft
**Baseline: BOT `5d0e6f2` (224 tests green) · APP `cd5ea4c` (5 suites + 8 integration green). Every line ref below was read in source this session unless marked SUSPECTED.**

Read this once, top to bottom. Then work Section 6 only. Sections 1–5 are the evidence; Section 7 is what you can stop thinking about; Section 8 is what you hand to a friend.

**The one framing that makes the last two weeks make sense:** there are two severity classes and they have been mixed together in every doc so far.
- **MONEY-FINAL** — a wrong number that gets frozen into `registry/settlement_snapshots.json` and settles cash. Write-once (`wc_fps_to_csv.py:2792`). Un-fixable after the fact today.
- **LIVE-DISPLAY** — a wrong number on the in-play H2H that the completed sheet overwrites anyway. Annoying, not money.

Everything below is tagged. Do not spend a session on a LIVE-DISPLAY item while a MONEY-FINAL one is open.

---

## 1. BLEEDING NOW — ranked by fantasy points at risk

| # | What | Size | file:line | Effort | Class |
|---|---|---|---|---|---|
| **1** | **CPL 2026 scores literally nothing.** It is the only ESPN-only tour (`tours.json`: `cricapi_series:""`, `espn_series:8623`). `run_tour` sets `WC_SERIES=tour["cricapi_series"]` (`wc_fps_to_csv.py:1770`) → `api("series_info", id="")` (`:1791`) → `sys.exit("series_info fetch failed or empty")` (`:1796-1797`). Meanwhile the app carries **35 CPL matches** (`wwc-draft/data/matches.json`) as draftable. **PROVEN.** | **100% of every CPL contest** (~800–1000 FP/contest, ×35 matches) | `wc_fps_to_csv.py:1770,1791,1796` | 1 day (S5) | MONEY-FINAL |
| **2** | **1562 of 3119 settlement rows frozen at `COMPLETED_FLAGGED`, write-once.** LPL Match 21 GG v JK froze off a cricapi-only card (`source: "cricapi · limited (no dots/XI — ESPN unavailable) · ⏳ provisional"`). `record_settlement` early-returns on an existing key. **PROVEN (measured: 3119 rows / 56 matches / 3 tours; 1557 COMPLETED / 1562 FLAGGED).** | **~200+ FP on that one match** (dots ~80 + XI 22×4); unbounded across the rest | freeze trigger `wc_fps_to_csv.py:2295,2306`; write-once `:2792` | ½ day | MONEY-FINAL |
| **3** | **82 draft players carry `slug:` pids that no points row can ever have → they settle at ZERO, permanently.** 883 `ci:` + 82 `slug:` in `wwc-draft/data/players-raw.json` (75 CPL + 7 OIND — Rohit, Gill, Kohli, KL Rahul, Kuldeep…). On a completed match `useLive=false`, so a pid\'d player missing under that pid returns **null with no name fallback**. **PROVEN.** Root cause is *not* the app — see #4. | a CPL/OIND XI ≈ **entirely zero** | `wwc-draft/lib/points.ts:69-70` | ½ day, after S5 | MONEY-FINAL |
| **4** | **Root cause of #3: ESPN-only tours skip identity anchoring entirely.** `tour_sync_finalize.py:153-158` skips `build_registry`, `:213-216` skips `identity_healthcheck`, `:223-226` exempts them from the pid-coverage gate. So an ESPN-added tour ships with `resolve_pid`\'s fallback `slug:` pids (`tour_sync.py:364`) and the verify gate says OK. **PROVEN.** | same as #3, and it is the default for **every** tour after the cricapi flip | `tour_sync_finalize.py:153-158,213-216,223-226` | ½ day | MONEY-FINAL |
| **5** | **78% of settled rows (2448/3119) carry no `fields`** → they still take `_l2_baseline`\'s recompute path, so the phantom `dots 0→N` class RECON_DEV_PLAN 1.4 was written to kill is live on most of the corpus. The code shipped; the data was never re-seeded. **PROVEN (measured: 671 with `fields`).** | recurring false deltas on ~2400 rows | data, not code — `seed_settlements.py` pass | ½ day | MONEY-FINAL |
| **6** | **Fielder identity is thrown away → real players silently score 0.** `parse_espn` extracts `fld_id` at `:1064` then keys the row by **name** at `:1067/:1069`; `espn_xi:916` carries `espn_id` but `blank_perf(e["name"])` discards it at `:1090`. **Caught in the act:** Virandeep Singh (LPL M1, `ci:633660`, fully registered, alias `virandeep singh`, draft_id 10357) took an unassisted run-out; ESPN returns fullName `"Virandeep Singh Jagjit Singh"`, `norm()` ≠ alias, id discarded → **frozen at `points: 0`**, and 0 across all 11 LPL matches he appears in. **PROVEN.** | **12 FP write-once** on the measured case; class-wide across every sub/fielder/XI-only row | `wc_fps_to_csv.py:1064,1067,1069,1090`; `dro=12` at `:89` applied `:1170` | 2–3 h | MONEY-FINAL |
| **7** | **+36 FP of missing run-out credit on 2 NZ v WI ODIs** (ev 1538627, 1538628) — known, needs the hand-fix. Compounded by #8: those tours have **no settlement baseline at all**. **PROVEN.** | **+36 FP** | rescore | 1 h | MONEY-FINAL |
| **8** | **CPL and the IND-ENG ODIs have ZERO settlement rows** (snapshot counter shows only LPL 1074 / Hundred M 1042 / Hundred W 1003). Anything already paid on them is **unauditable**. **PROVEN.** | n/a — it\'s an audit hole, not a number | `registry/settlement_snapshots.json` | decision, ½ day if baselining | MONEY-FINAL |
| **9** | **Duck / dismissal stamped on the playbyplay STRIKER, not the victim.** ev1537331 item 216030: `shortText "Akif Javed to Wiese, OUT"`, `type "run out"` — cricsheet\'s `player_out` is **C Wickramasinghe** (the non-striker). The duck rule at `:1150-1152` fires on exactly this case, so the −2 lands on the wrong man. 7/17 wrong in sample. **PROVEN.** | **±4 FP per occurrence** | `wc_fps_to_csv.py:1051`, duck `:1150-1152` | 1–2 h (fold into #6) | MONEY-FINAL |
| **10** | **`summary` has no fetch-failure guard** — the exact bug `785dec8` fixed on `playbyplay`, on the other endpoint. `espn_get` returns `{}` on 502/timeout/WAF-403 (`:811`), indistinguishable from "no data"; the refuse-to-score guard at `:949-970` covers playbyplay only. On a blip: run-outs→0, XI→empty → `if v["played"]` at `:1934` **discards the whole ESPN perf set**, team attribution dies — and per #2 it can then freeze. Given ESPN WAF 403s are a known live failure in this stack, treat as imminent. **PROVEN (code); SUSPECTED (not yet observed in prod).** | Hundred-Women\'s 3/10-fields shape, ~100+ FP/match | `wc_fps_to_csv.py:865,879,916` (+ guard pattern at `:949-970`) | ~15 lines | MONEY-FINAL |
| **11** | **Duplicate `(match,pid)` rows behave differently in two app paths:** `getMatchPointsForMatch` does `result.set(pid, pts)` = **last-wins** (`points.ts:573-574`), while `getTourPoints` uses `add()` = **sums** → a duplicated row **double-counts on the selection board** but not on the match card. **PROVEN.** | a duplicated row = 2× that player\'s tour total on the board | `wwc-draft/lib/points.ts:573-574` vs `:~610` | 1 h | MONEY-FINAL (board) |
| **12** | **App live path drops ≈41 FP/match.** `lib/espn.ts:535 bowlLbwBowled:0` (29.3/match) and `:538 runOuts:0` (9.5/match), plus the direct-RO uplift and caught-and-bowled gap. All four are in `summary.rosters[].roster[].linescores[].statistics.batting.outDetails` — **the object the app already holds at `lib/espn.ts:467`, zero extra HTTP.** (`grep -c outDetails lib/espn.ts` = 0.) **PROVEN.** *Correction to the brief: `catches`/`stumpings` are NOT hardcoded — `lib/espn.ts:536-537` already reads them.* | ≈41 FP/match, H2H-flipping in play; overwritten on completion | `wwc-draft/lib/espn.ts:535,538` | 45–60 min (run-outs) + 1 h (lbw/bowled from outDetails) | **LIVE-DISPLAY** |
| **13** | **`isPidKey` was never updated for the `ci:` migration** — `/^(espn:\\|slug:)/ \\|\\| /^[0-9a-f]{8}$/` (`lib/players.ts:22-23`). Every current sheet key is `ci:…`, so the "exclude pid keys from fuzzy NAME matching" filter (`points.ts:48`, `:306`) now excludes **nothing** and feeds ~900 `ci:` strings into the fuzzy matcher\'s candidate pool. **PROVEN stale; impact SUSPECTED** (fuzzy is null-on-ambiguity, so the likely symptom is a legitimate name fallback returning null). | low, but it is a live landmine | `wwc-draft/lib/players.ts:22-23` | 15 min | MONEY-FINAL |
| **14** | **`bat_order` never set on the ESPN path.** Set at `:728` (cricsheet) and `:1283` (cricapi), absent in `parse_espn`. Data exists at `statistics.batting.order` and matched cricsheet **60/60, 0 diffs**. **PROVEN.** | display/ordering only | `wc_fps_to_csv.py` `parse_espn` | 30 min | LIVE-DISPLAY |

**Two-minute check to do before anything else:** open the **TOUR CONTROL** tab and look for the CPL row. The tour-loop approval gate keys on `cricapi_series` (`wc_fps_to_csv.py:2599`), which is `""` for CPL. If there\'s no approved row, CPL has been skipped at 0 API **silently**; if there is one, `run_tour` has been `sys.exit`-ing and `tours_failed` makes the whole workflow exit non-zero (`:2664-2666`). Which of the two you\'ve been living with tells you whether your workflow has been quietly lying or loudly red. (Mechanism PROVEN; which branch is live — SUSPECTED.)

---

## 1b. THE ITEM NOBODY RAISED — read this before you fix anything

**B2 — There is no way to supersede a settled row, so every fix in this plan will look like a regression.**

#6, #7, #9, #14 and the E9 re-run all change points on matches **already frozen**. The moment they ship: the sheet shows the corrected number, the baseline still holds the wrong one, and `_points_delta` (`wc_fps_to_csv.py:2807`) prints a non-zero delta for **every corrected player** — indistinguishable from real drift on the app\'s audit tab (`wwc-draft/app/draft/[code]/results/page.tsx:364-378 ReconBanner`). Hundreds of false "changed" rows will bury the handful of real ones, and you will be back in whack-a-mole with worse signal than you have now. **PROVEN by reading; no sweep raised it.**

Two ways out, pick one:
- **(a)** add `supersede_settlement(match_key, pid, reason_code)` + a `superseded_by` / `reason` field, so a deliberate correction is machine-distinguishable from drift; or
- **(b)** settle and close every affected contest *before* the scorer fixes land, then re-baseline from clean.

**Effort ½–1 day. Nothing else in this plan is safe to ship until this exists.** This is step 0.

---

## 2. BLOCKS THE CRICAPI REMOVAL — dependency order

The honest status: `grep -n "espn_only\\|ESPN-only" wc_fps_to_csv.py` returns **nothing**. The scorer has no ESPN-only path at all — cricapi is load-bearing at five separate points:

| gate | file:line | what breaks when cricapi leaves |
|---|---|---|
| series fetch | `wc_fps_to_csv.py:1770,1791,1796-1797` | hard `sys.exit` per tour → **no rows written, ever** |
| tour approval | `:2599` (`_tour_approved(control, cricapi_series)`) | every tour keys on `""` → never approved |
| freeze set | `:2603` (`t["cricapi_series"] in frozen`) | freeze bookkeeping keys on a blank |
| completion | `:1857-1863` `is_over` reads `matchEnded`/`matchStarted` — **both cricapi-only keys** | `ended=[]`, `live=[]` (`:1864-1869`), `to_score=[]` (`:1885`) → **total blackout, every contest reads 0** |
| discovery | `:2001-2003` `if es not in match_shorts: continue` — joins on team **display names**; `espn_series` is never used for discovery (`:816-827`) | one name miss = that match scores **nothing**, silently (R1) |
| identity | `tour_sync_finalize.py:153-158,213-216,223-226` | no `build_registry`, no healthcheck, no coverage gate → `slug:` pids → **zero settle** |

All six **PROVEN**. Note that **`is_over` and the identity skip are listed in none of ESPN_ONLY_MIGRATION.md\'s B1–B4/R1** — the migration doc\'s blocker list is incomplete in the two places that cause total loss.

**Dependency order (each depends on all above it):**

1. **F0 — supersede/baseline** (§1b). Without it you cannot tell a fix from a regression, so you cannot verify anything that follows.
2. **F1 — discovery on event ids** (R1). Join on `espn_series` → scoreboard event ids, never on display names. Everything downstream needs a reliable match list.
3. **F2 — evidence-based completion** (`espn_match_state`, §5). Replaces `is_over`. Zero extra HTTP (same `summary` cache key as `espn_xi:916`).
4. **F3 — `summary` fetch guard** (#10). Must land before F2/F4 lean harder on `summary`.
5. **F4 — identity on ids** (#6): resolve fielder/XI rows by `espn_id`/`cricsheet_id` before name; **plus** run `build_registry` + healthcheck + the coverage gate for ESPN-only tours (#4). Without this half, the flip converts every tour into a `slug:` tour.
6. **F5 — re-key the tour loop** off `tour["name"]`/`espn_series` instead of `cricapi_series` (approval gate `:2599`, frozen set `:2603`, `run_tour` `:1770/1791`).
7. **F6 — flip.** Not before one **live** match has been observed through F2 (see §5\'s honest gap).

---

## 3. WILL BITE ON THE NEXT TOUR — ordered by likelihood

| # | Defect | file:line | Verdict |
|---|---|---|---|
| **T1** | **ESPN-only tours skip `build_registry`, `identity_healthcheck` and the pid-coverage gate** — the tour ships green with `slug:` pids. Already happened (CPL, 75 players). | `tour_sync_finalize.py:153-158,213-216,223-226`; slug mint `tour_sync.py:364` | **PROVEN — certainty 100%, it already fired** |
| **T2** | **Next season of a league can never be added via Column A.** `status_sheet_new_names` compares `norm(_clean_tour_name(name))` against existing tours, and `norm` is `re.sub(r"[^a-z ]", …)` — **it strips digits** (`tour_sync.py:280-281`). "Caribbean Premier League 2027" normalises identical to 2026 → matched as "already ingested" → silently skipped (`tour_sync.py:668-674`). | `tour_sync.py:280-281, 668-674` | **PROVEN — fires at the next season of any existing league.** (The *tab*-naming half of this was fixed at `espn_build:596-601`; the Column-A half was not) |
| **T3** | **Men\'s and women\'s bilateral between the same two teams collide on one tab.** `tab = f"{shorts[t0]} v {shorts[t1]} {fmt_label} POINTS"` — no gender letter, while team *codes* do carry one (`mint_code`). `apply_to_repos` skips an existing tab → the second tour never ingests. Today\'s "IRE v WI W ODI POINTS" only got its W from a cricapi shortname; the ESPN path derives shorts as `re.sub(r"[^A-Za-z]","",t)[:3]` (`espn_build:590-592`) → "India Women" → **IND**. | `tour_sync.py:744-747`, `espn_build:590-592` | **PROVEN — fires the first time you add a women\'s bilateral post-flip** |
| **T4** | **Fixture list is written once and never extended.** `_espn_matchlist` scans back 30 / forward 60 days with a 6-empty-day stop, and the code\'s own comment states a tour whose tab exists is skipped forever (`:492-494`). Playoffs announced late, or a season longer than the window, are permanently missing. CPL was already truncated once (22 of 41 days). | `tour_sync.py:488-517` | **PROVEN — recurs every long league** |
| **T5** | **`mint_code` infinite-loops on a 6-char collision.** `base = f"{gl}{fl}{short}".upper()[:6]`; the retry is `code = f"{base}{i}"[:6]` — when `len(base)==6` the suffix is truncated away, `code` never changes, `while code in taken` never terminates. Reachable via the cricapi shortname path (`shorts = ti.get(t, t[:3].upper())`, 4-char shortnames), not via the ESPN path (3-char shorts → 5-char base). | `tour_sync.py:352-361` | **PROVEN mechanism; trigger conditional on a 4-char shortname collision** |
| **T6** | **Missing squads silently shrink a tour.** `espn_build:584-588` warns then proceeds; `canonical()` returns None for unmapped teams and `gen_tour` drops those fixtures. Loud in the log, invisible in the sheet. | `tour_sync.py:584-588, 712-720` | **PROVEN** |
| **T7** | **Column-A ingest only mints ODI and T20.** `espn_add_named` loops `for fmt in ("ODI","T20")`. Anything else typed in Column A yields nothing (though `gen_tour` prints a loud "0 {fmt} matches — dropped"). | `tour_sync.py:629-635`, `gen_tour:702-708` | **PROVEN, low severity (loud)** |
| **T8** | **Adding a cricapi series id to an ESPN-added tour sends ~59% of players to zero** — the squads were minted from ESPN team/player names and `slug:` pids; re-scoring against cricapi\'s name space mass-misses. **Do not use "just add the cricapi id" as the CPL shortcut.** | mechanism consistent with `short_of()` at `:1782-1786` + the name table | **SUSPECTED — figure from the sweep, not re-measured. Verify with a dry-run before trusting either way** |
| **T9** | **Only the last tour\'s identity gaps reach "Needs Cricinfo ID"** — `write_needs_cricinfo_tab()` reads `registry/needs_cricinfo_pending.json`, which `build_registry` appears to overwrite per tour, clobbering earlier tours\' pending lists. | `tour_sync_finalize.py:100,240` | **SUSPECTED — 5-min check: run `build_registry.py` for two tours in a row and diff the pending file** |

---

## 4. THE CRICBUZZ DECISION

**No — not for the bot, not for the app, not as a tiebreak. Do not build it.** DATA_SOURCE_EVAL_20260813.md §8 recommends it off a gap that is roughly **2× larger than the real one**: it claims all four of `bowlLbwBowled`/`catches`/`stumpings`/`runOuts` are hardcoded to zero in the app, but `wwc-draft/lib/espn.ts:536-537` already reads `caught`/`stumped` (measured: `sum(caught)=207`, `sum(stumped)=5` over 24 LPL matches, `stumped` matching the `st` dismissal cards 5/5). Everything Cricbuzz\'s `wicketCode`+`fielderId1..3` recipe would give you is already in `summary.rosters[].roster[].linescores[].statistics.batting.outDetails` — counted over 481 dismissal records: `c` 214/214 with a fielder athlete id, `bowled` 56/56 and `lbw` 32/32 with a bowler id, `st` 5/5, `run out` 19/19 with the direct-vs-assisted split from `len(fielders)`. The bot already mines this block at `wc_fps_to_csv.py:879-914`, and the app already holds the object at `lib/espn.ts:467` — **zero extra HTTP requests, and ESPN\'s `athlete.id` IS the cricinfo id, so the join is free** where Cricbuzz would introduce a fourth identity space to reconcile (you have spent two weeks on the consequences of having three). The one use case that could justify a third opinion — a wicket-level disagreement between ESPN and cricsheet — was investigated as E9 and **there is no disagreement**: ESPN header, cricsheet, and ESPN ball-by-ball agreed 16/16/16 and 15/15/15 (§9 below). Spend the Cricbuzz day on #12 instead: same points, in data you already have.

---

## 5. THE COMPLETION RULE

Replaces `is_over` (`wc_fps_to_csv.py:1857-1863`) and the 8h/12h `OVER_HRS` clock (`:1847`). Lives next to `espn_xi:916` / `espn_runouts:879`, same `summary` payload, same `espn_get` cache key (`:797-801`) → **zero additional network requests.**

**Principle: time may only ever raise an alarm. It may never advance a state.** Today the 8h clock is a *promoter* — a long rain break pushes an in-play match past `start+8h` into `ended` (`:1864`), it scores off a partial card, `is_live` is False so `:2161` doesn\'t force LIVE, and `record_settlement` freezes the partial **write-once, forever** (SUSPECTED — structurally reachable, not yet observed; the new predicate is what blocks it).

**COMPLETE(match) ⟺ all of:**
- **(a)** `header.competitions[0].status.type.state == "post"` — *measured `post` on 66/66 finished; scoreboard cache shows `pre` (1) / `post` (74)*
- **(b)** `status.type.description ∈ {"Result", "No result", "Abandoned"}` — *measured `Result` ×65, `No result` ×1; `Abandoned` ×1 and `Scheduled` ×1 on the scoreboard*
- **(c)** both batting innings present (`linescores` filtered **`isBatting == true`**) and each innings `description ∈ {"complete", "target reached", "all out"}` — *measured 140 / 72 / 50, **zero other values***; **relax to 0–1 innings** when (b) is `No result`/`Abandoned` (measured: 1 innings ×1)
- **(d)** exactly one `competitors[].winner == "true"` — **the STRING**, not a bool; skip for no-result/abandoned/tie

**Five traps, all measured, all of which have already burned a naive implementation:**
1. `status.type.completed` — **the key does not exist** in ESPN\'s cricket payload. `t.get("completed")` is falsy on a finished match.
2. `competitors[].winner` is the **string** `\'true\'`. `c.get("winner") is True` is False on every match.
3. Innings `description` is a **3-value vocabulary**, not a boolean — `== "complete"` reads False on 4 of every 5 innings.
4. `endDate` is the **scheduled final-day boundary**, off by up to 2 days (ev 1521231 = `2026-07-23T23:59Z` for a 21 Jul match). **Never anchor the cutoff on it.**
5. `header.competitions[].competitors[].linescores[]` carries a **`0/0` mirror row per competitor** — any header check must filter `isBatting:true`. (Also: `notes[type==\'ballsperover\']` is `\'5\'` on the Hundred, so `overs 19.4` = 99 balls, not 118.)

**The cutoff (the other half of E8):**
- Anchor on the match\'s **own scheduled start** (`matches.json` / ESPN `date`), never `endDate`.
- `COMPLETE ∧ cricsheet-resolved` → **SETTLE + FREEZE**.
- `COMPLETE ∧ no cricsheet ∧ age < 7d` → **PROVISIONAL_COMPLETE**: publish points, **do not freeze**.
- `COMPLETE ∧ no cricsheet ∧ age ≥ 7d` → **ESPN_FINAL**: settle and freeze, stamped `l1_source=espn_only` in the settlement record so it\'s visibly a second-best baseline.
- `¬COMPLETE ∧ age > OVER_HRS` → **ALARM only** (log + a flag on the tour status tab). Never advances state. This is what kills the rain-break freeze.

**Honest gap:** all 66 cached samples are *finished* matches, so the `pre`/`in` half of the state vocabulary is **SUSPECTED**. **Observe one live match through the new predicate before flipping F6.** That is the whole cost of being sure.

---

## 5b. E9 IS DIAGNOSED — and it is not an ESPN defect

ESPN header, cricsheet, and ESPN playbyplay **all agree**: ev1537331 = 16/16/16, ev1537334 = 15/15/15. "Ours" is `Σ bowler wickets + Σ run-out FIELDER CREDITS`, and `:1077-1086` does `rp["runouts"] += 1` **per fielder** — so a 2-man assist inflates the count by 1 (ev1537334) and a dropped substitute deflates it by 1 (ev1537331 — that\'s #6, Virandeep). One mechanism, both directions. **PROVEN.** Per-fielder credit is *correct scoring* (6 pts each); it is only wrong as a **wicket count**.

- **Fix:** change the check to `len(pbp items with dismissal.dismissal)`, then **re-run the 24-event sweep**. ~20 min + rerun. **Do not close E9 on the old figure** — the sweep\'s "2 of 24 mismatch" number is self-invalidated by its own double-counting.
- **Why it matters beyond tidiness:** the current metric *masks* real misses — a genuinely missing wicket nets to zero against a 2-man run-out.
- **Keep one open sub-item:** `espn_runouts` appends only `if fl:` (`:911`) — a run-out with **no listed fielder is dropped silently**, costing 6/12 pts *and* the wicket. Not observed; **SUSPECTED**; add a loud warn, 15 min.

---

## 6. SEQUENCED EXECUTION PLAN

Eight sessions. **Do not reorder S0 or S1.** Each step: what changes → how it\'s verified → how to roll back.

Before S0, once: `cp registry/settlement_snapshots.json registry/settlement_snapshots.$(date +%F).bak.json` and commit it. That file is your money ledger and it is a file, so every rollback below is `git restore` on it.

---

### S0 — "make fixes distinguishable from regressions" (½–1 day) · **BLOCKING**
1. Add `supersede_settlement(match_key, pid, reason_code)` + `superseded_by`/`reason` fields; `record_settlement` (`:2792`) keeps its write-once early-return, supersede is the only other door in. *(Or: close every affected contest first and re-baseline — pick one, don\'t half-do both.)*
2. Teach the app\'s audit banner to render superseded rows as **"corrected: <reason>"**, not as drift (`wwc-draft/app/draft/[code]/results/page.tsx:364-378`).
3. Re-seed `fields` for the 2448 rows that lack them (#5), same migration pass.
4. **Decide** #8: baseline CPL + the IND-ENG ODIs, or declare them unauditable in writing.

**Verify:** re-run the scorer on an untouched completed match → **0 unexplained deltas**; `python3 -c` count of rows with `fields` → **3119/3119**; every superseded row carries a `reason_code`; bot `pytest` green (224+); app `npm test` + `npm run test:integration` green.
**Rollback:** `git restore registry/settlement_snapshots.json` from the `.bak` commit; revert the code commit.

---

### S1 — stop the bleed (½ day)
1. **#2:** gate the settlement freeze on `COMPLETED` only (or on L1-done) — `:2295`, `:2306`. A `COMPLETED_FLAGGED` match must never freeze.
2. **#10:** copy the `785dec8` refuse-to-score guard (`:949-970`) onto `summary` at its three readers — `espn_team_map:865`, `espn_runouts:879`, `espn_xi:916`.
3. Supersede LPL Match 21\'s frozen rows once #2\'s rescore is available.

**Verify:** new unit test — a match in `COMPLETED_FLAGGED` does **not** call `record_settlement`; a second test injects `{}` from `espn_get` and asserts the run refuses to score rather than writing zeros. Rescore LPL M21 and diff vs cricsheet: dots + XI restored, ~200 FP recovered.
**Rollback:** single-commit revert; the freeze gate is 3 lines.

---

### S2 — identity on ids, one pass (1 day)
Merge #6 + #9 + #14 + E6 + E7 + B3-silent-drop. **They are one bug — do not plan them as five.**
1. `parse_espn`: key fielder rows on `fld_id` (`:1064`) → resolve `espn_id`/`cricsheet_id` **before** name; stop discarding it at `:1067/:1069`.
2. `blank_perf` (`:1090`): carry `espn_id` from `espn_xi:916`.
3. Take the dismissal victim from `summary.outDetails` (keyed on the dismissed player\'s own roster row), not the playbyplay striker (`:1051`) — fixes the duck misfire at `:1150-1152`.
4. Set `bat_order` from `statistics.batting.order` in `parse_espn`.
5. **Decide the rule first** — *does a substitute fielder who never appears in an XI score in this app?* **Yes** → team-assign a sub to the fielding side and credit them (2–3 h). **No** → suppress deliberately and exclude from the wicket count (30 min). Today\'s behaviour is an accident either way.
6. A known player who played but sits in no squad slot must produce a **flagged row**, never a silent drop.

**Verify against cricsheet:** re-run the 24 LPL events + the 2 ODIs and assert field-exact on catches / stumpings / run-outs / lbw-bowled / bat_order (baseline: lbw/bowled 18/18, catches 28/28, run-outs 20/20, bat_order 60/60 with 0 diffs). Named assertions: **Virandeep Singh = 12 FP in LPL M1**; the duck flag matches cricsheet `player_out` 17/17; **zero silent drops** — every unmatched player emits a flagged row.
**Rollback:** revert. Settlements are safe because S0 made corrections explicit.

---

### S3 — the completion rule + E9 close-out (1 day)
1. Implement `espn_match_state()` per §5; replace `is_over` (`:1857-1863`). Put it behind `COMPLETION_MODE=time|evidence`, default `evidence`.
2. Implement the 7-day cutoff + `PROVISIONAL_COMPLETE` / `ESPN_FINAL` states; `l1_source` stamped on the settlement record.
3. Change the wicket check to `len(pbp items with dismissal.dismissal)` and **re-run the 24-event sweep**; add the loud warn for a fielder-less run-out (`:911`).

**Verify:** replay the 66 cached summaries — **COMPLETE on 65, the No-result branch on 1, zero matches advanced by time alone**. Synthetic rain-break test: an in-play match at `start+9h` must stay LIVE and must not settle. Wicket-count sweep expected **24/24** — record the new number, retire the old one. **Then watch one live match end** before F6.
**Rollback:** `COMPLETION_MODE=time`.

---

### S4 — discovery + tour-loop keying (½ day)
1. **R1:** join discovery on `espn_series` → event ids, not team display names (`:2001-2003`, `:816-827`).
2. Re-key the approval gate (`:2599`) and frozen set (`:2603`) off `tour["name"]`/`espn_series` instead of `cricapi_series`.

**Verify:** for each of the 12 tours in `tours.json`, assert discovered event count == fixture count in `matches.json` (CPL must show 35). Assert every tour appears in the TOUR CONTROL tab under its new key.
**Rollback:** revert; keep the name-join as a logged fallback for one cycle so a regression is visible rather than silent.

---

### S5 — make ESPN-only tours real (1 day) · **this is the CPL fix**
1. `run_tour`: build the match list from ESPN when `cricapi_series` is blank, instead of `sys.exit` (`:1770`, `:1791`, `:1796-1797`).
2. `tour_sync_finalize.py`: **run `build_registry` and `identity_healthcheck` and apply the pid-coverage gate for ESPN-only tours** (`:153-158`, `:213-216`, `:223-226`). Post-flip this is the *only* kind of tour there is.
3. Re-run `build_registry` for CPL and the OIND leg → replaces `slug:` with `ci:`.

**Verify:** CPL end-to-end — 35 matches scored, pid coverage ≥ `SYNC_MIN_PID_COVERAGE`, `slug:` count in `wwc-draft/data/players-raw.json` goes **82 → 0**; cross-check 2 CPL matches field-by-field vs cricsheet. `npm run check:tours` must now be **clean**, not merely exit 0 (today it prints `MTGUY 24% · MTBAR 31% · …` and passes anyway — **make that a hard fail**).
**Rollback:** revert; CPL returns to writing nothing, which is where it already is.

---

### S6 — app, money-final (½ day)
1. **#3/#13:** re-key the 82 (falls out of S5); update `isPidKey` to include `ci:` (`lib/players.ts:22-23`).
2. **#11:** make duplicate `(match,pid)` handling **one** rule across both paths — recommend **max-wins** in `getMatchPointsForMatch` (`points.ts:573-574`) and **de-dup-then-sum** in `getTourPoints`. Add a test with a deliberately duplicated row.
3. **Kill the second scorer for good:** `app/api/draft/[code]/results/route.ts:170` still computes per-player rows with its own `lookupPlayerPoints` and only reuses `calcSelectionPoints` for the audit compare (`:259`). After building rows, assert its own XI sum `=== calcSelectionPoints(sel, ppu, scoringMap, useLive)` and `console.error` on mismatch. **30 min, and it converts every future drift from a silent money bug into a log line.** *(The 199-pt BACKUP_INTELLIGENCE hole is already CLOSED at `lib/contest-scoring.ts:28-40` — do not re-fix it.)*

**Verify:** `npm test` + `npm run test:integration`; settle-audit a past CPL/OIND contest and confirm no player sums to 0 for want of a pid; the new assert stays silent across a full replay.
**Rollback:** revert.

---

### S7 — app, live display (½–1 day) · lowest priority, do it last
Derive **all four** fielding stats from `outDetails` in `lib/espn.ts` (the object already in hand at `:467`) and **stop reading `get("caught")`/`get("stumped")`** — one self-consistent derivation. Fixes `:535` `bowlLbwBowled:0` (+29.3/match), `:538` `runOuts:0` (+9.5/match), the direct-RO uplift (`d11-score.ts:67-74` pays every run-out at the assisted rate of 6, +5.0/match), and the caught-and-bowled gap (ESPN\'s `caught` stat is 207 vs 214 `c` cards; 9 cards have `fielders[0].athlete.id == bowler.id`, +~2.3/match). Free in the same read: `order: get("battingPosition")` (`espn.ts:486`).

**Verify:** the same 24 cached summaries — app live totals must match the bot\'s completed totals within the known provisional gaps; `sum(caught)` derived from `outDetails` = **214**, not 207.
**Rollback:** revert; live is provisional and the sheet overwrites it, so this is the safest change in the plan.

---

### S8 — tour setup hardening (½ day) · before the next tour, whenever that is
T1 (done in S5) · T2 season-key: stop stripping digits in the Column-A dedup (`tour_sync.py:668-674`) · T3 gender letter in the bilateral tab (`:744-747`) · T4 re-scan window / allow fixture-list extension on an existing tab · T5 `mint_code` suffix that survives truncation (`:352-361`) · T6 make a missing squad a **gate failure**, not a warning · T7 loud already, leave · T8 **measure before believing** · T9 5-min check on `needs_cricinfo_pending.json` append-vs-overwrite.
**Verify:** one dry-run per defect — add a fake "CPL 2027" to Column A and assert it ingests; add a women\'s bilateral between two teams that already have a men\'s tour and assert two distinct tabs.

---

## 7. WHAT IS ALREADY SAFE — stop worrying about these

**Fixed and verified this week (do not re-open):** ESPN run-outs (0→20 from summary `outDetails`); the dots over-count (no-balls read as legal + duplicate commentary items); ODI `limit=600` truncation → pagination; a partial/failed fetch now refuses to score; the cricsheet `non_boundary` all-run-4s ground-truth bug; the 5-min tick now persists ledgers; a gviz unknown tab is now validated; COMPLETED never returns to LIVE; an explicit S1 counts as resolved; identity resolves cricsheet rows by cricsheet person id; the Needs-Cricinfo read+promote loop; a tour crash now fails the workflow (`:2664-2666`).

**Docs that say "open" but are actually SHIPPED in `5d0e6f2`** — this is the single biggest source of wasted attention in your current doc set. Delete or annotate these lines:

| Doc claim | Reality |
|---|---|
| RECON_DEV_PLAN 2.1 "ESPN-only player loses everything but dots — 4 pts vs 110 earned" | **FIXED** — `merge_espn_into` `elif e.get("played")` now does `np = dict(e)` (`:1455-1458`) |
| RECON_DEV_PLAN 2.2 "`compute_l1_gaps` iterates `capi_pid` only" | **FIXED** — `:1537` iterates the union |
| RECON_DEV_PLAN 1.3 "settlement record is points-only" | **SHIPPED** — `record_settlement:2772` stores `rec["fields"]` over `SETTLED_FIELDS` (`:2798`) + `field_sources` |
| RECON_DEV_PLAN 1.4 "L2 recomputes the baseline (phantom dots)" | **SHIPPED** — `_l2_baseline()` reads the frozen record; the L2 hold at `:2110-2118` uses it. *(Code shipped; the **data** re-seed is #5 — that part is real)* |
| RECON_DEV_PLAN 1.1 "new `classify_recon_state()`" | **SHIPPED** at `:1608` |
| RECON_DEV_PLAN 6 / RUNBOOK:128 "playbyplay `limit=600`, no pagination" | **FIXED** in the live path (`:951` `limit=1000` + page loop + refuse-partial `:949-970`). The `limit=600` at `:831` is inside **dead** `espn_dots()` |
| STREAMLINE_PLAN §3 prerequisites 1 & 2; §2c "ESPN run-outs 20 vs 0 — completely broken" | **Both done.** Stale |
| ESPN_ONLY_MIGRATION §5.1 "28 rescore candidates" | Self-resolving — the 18-Aug ESPN-only matches carry the fix via in-place rewrite |
| DATA_SOURCE_EVAL open items ("−1 legal-ball edge case", "confirm STUMPED") | **Not bot defects** — properties of a Cricbuzz parser that does not exist in this repo. Zero exposure |

**Also already safe / already correct:**
- **`classify_match_status:1662` is not the freeze path.** `:1657 if unsourced:` fires first, so a cricapi-only match never reaches `:1662`. The real path is `unsourced ∧ already_completed → COMPLETED_FLAGGED` (`:1660`) → `record_settlement` (`:2295`). Same outcome — but patch the right line.
- **`override_sources` is not broken.** It\'s populated at `:2057-2059` and passed at `:2298`/`:2310`; "0/3119" just means no override landed on a newly-settled row. A 10-min assertion, not a defect.
- **The app has one scorer for the contest total.** `lib/contest-scoring.ts:28-40` has the BACKUP_INTELLIGENCE branch and is shared by lobby (`app/lobby/page.tsx:295,434,493,538`), match hub (`app/match/[key]/page.tsx:166`) and audit (`app/audit/page.tsx:128`). The 199-pt hole is **closed**. What remains is the results route\'s parallel row-builder (S6.3) — a guard, not a rewrite.
- **The app already reads catches and stumpings live** (`lib/espn.ts:536-537`, scored at `lib/d11-score.ts:69-76`).
- **ESPN\'s data quality is not the problem.** Measured after the fixes: ODI 10/10 fields exact; LPL T20 exact bar 2 runs in 6138; Hundred M 9/10; Hundred W 7/10 (one delivery). lbw/bowled 18/18, catches 28/28, stumpings exact, run-outs 20/20, batting order 60/60. `athlete.id` **is** the cricinfo id. Every remaining bug in this plan is **ours**.
- **Tests are green on both repos** at these HEADs: bot `224 passed`; app 5 suites + 8/8 integration on a fresh `db/test.db`.

---

## 8. THE STANDING OPS SOP

Hand this to a friend who has never seen the code. Absolute paths. If a step says STOP, stop.

### SOP-A — Add a tour
1. Type the tour name into **Column A** of the **TOUR CONTROL** (or TOUR STATUS) tab of the points Google Sheet. Include the year.
2. Dry run first: `cd /Users/nishant-singodia/wwc-points-bot && python3 tour_sync.py --dry-run --from-status-sheet`. Read the log. If it says *"no ESPN league matched"*, the name is wrong — fix Column A, don\'t fight the code.
3. Apply: `python3 tour_sync.py --apply --from-status-sheet` (or run the **tour-sync** GitHub workflow with `dry_run=false`).
4. Anchor + gate: `python3 tour_sync_finalize.py \'["<tour name>"]\'`. **If the VERIFY GATE fails, STOP** — do not commit, do not deploy. It is telling you the tour would ship half-wired.
5. Check the **TOUR INGEST REVIEW** tab: espn_series resolved, PID coverage ≥ threshold, squad size sane, match count == the real fixture list.
6. In the app: `cd /Users/nishant-singodia/wwc-draft && npm run check:tours` — **no team may sit below the coverage threshold.** (Today it prints low numbers and still exits 0; after S5 it must fail.)
7. Confirm `wwc-draft/data/espn-series.json` lists the new `espn_series` under the right gender key (`M`/`W`). If it doesn\'t, lineups and live points silently don\'t resolve.
8. Deploy the app the usual way: **commit + deploy + push** — `vercel --prod` ships the whole working tree, so `git status` must be clean first.
9. **Known traps until S8 lands:** a second season of an existing league typed into Column A is silently ignored (T2); a women\'s bilateral against the same teams as a men\'s one collides on the tab (T3); the fixture list is written once and never extended (T4).

### SOP-B — Add or fix a player
1. Never add a local alias in an app. **All identity fixes go in the shared registry** — `registry/manual_aliases.json` / bridges, then re-run `python3 build_registry.py "<tour name>"`.
2. `python3 identity_healthcheck.py "<tour name>"` — it\'s advisory, but read the blockers.
3. If a player has no cricinfo id: they land on the **"Needs Cricinfo ID"** sheet tab. Fill the id there; the read+promote loop picks it up. *(Until T9 is checked, do one tour at a time — an earlier tour\'s pending list may be getting clobbered.)*
4. `python3 registry/backfill_draft_pids.py`, then sync the app mirror `wwc-draft/lib/registry-players.json`.
5. Sanity: the player\'s pid must start with `ci:`. **A `slug:` pid means they will score zero forever** (`wwc-draft/lib/points.ts:69-70`). Check with:
   `python3 -c "import json;p=json.load(open(\'/Users/nishant-singodia/wwc-draft/data/players-raw.json\'));print(sum(1 for x in p if str(x.get(\'pid\',\'\')).startswith(\'slug:\')))"` → **must print 0.**
6. Any pid-keyed **data file** needs re-keying too on an identity migration, not just the runtime shims (this orphaned the player-photo map for four days).

### SOP-C — Weekly recon
1. Read the full-run log: `N/M completed, K in-progress` (`wc_fps_to_csv.py:1874`). If a tour reports 0 matches, that tour is broken — do not assume it had no fixtures.
2. Open the **Recon Review** tab. Work every row with a **Recon Flag**. Resolved rows vanish and stay resolved (git-persisted).
3. Check `UNMAPPED_<tour>.txt` before any contest goes live. A name in there is a player who will score nothing.
4. Confirm every active tour wrote rows **this week**. A tour that silently stops writing looks identical to a tour with no fixtures — this is how CPL disappeared.
5. Live points only move once cricapi flips `matchStarted` (ESPN "live" is not enough, and cricapi can lag hours on bilaterals). Only the 2-hourly full run refreshes `series_info`.
6. Every scorer run that writes the sheet needs **cricsheet available**, or it downgrades cricsheet-resolved COMPLETED matches back to LIVE.

### SOP-D — Before settling real money
Run every line. Any ❌ = **do not pay out yet.**
1. `python3 tour_status.py` — the tour shows all matches COMPLETED, none stuck LIVE.
2. Recon: **zero open flags** for every match in the contest.
3. Every match in the contest is **cricsheet-resolved** (`Source` says cricsheet), or is ≥7 days old and explicitly stamped `l1_source=espn_only` (post-S3).
4. **No player in either XI reads exactly 0** unless you can name why (didn\'t play / genuinely 0). A pid miss and a genuine duck look identical on the results page — this is the failure mode that costs money.
5. Settlement audit: **zero unexplained `Points Delta`**. After S0, a non-zero delta must carry a `reason_code` — anything else is drift and blocks the payout.
6. The contest\'s tour has settlement rows at all (`registry/settlement_snapshots.json`). **CPL and the IND-ENG ODIs currently have none** — until S0.4, anything settled there is unauditable.
7. Both scoring paths agree: the app\'s results-route XI total == `calcSelectionPoints` (post-S6.3 this asserts itself; until then, eyeball the audit tab).
8. Take the ledger backup before any rescore: `cp registry/settlement_snapshots.json registry/settlement_snapshots.$(date +%F).bak.json`.

---

### The three things to do first, if you only have one evening
1. **Two minutes:** check the TOUR CONTROL tab for a CPL row (§1) — it tells you whether the pipeline has been silently skipping or loudly failing.
2. **S0** — supersede + re-seed. Without it every fix below looks like a regression and you are back in whack-a-mole with worse signal.
3. **S1** — the freeze gate and the `summary` guard. Three lines and fifteen lines, and together they stop new money from being frozen wrong.

Everything else can wait a week without costing you anything you can\'t recover.', 'checked': '# QC\'d MASTER LIST — verified against BOT `5d0e6f2` / APP `cd5ea4c`

Everything below I re-read in source or measured this session. Sweep claims I could not reproduce are demoted or corrected explicitly.

---

## PART 0 — CORRECTIONS TO THE SWEEPS (fix these before the owner reads them)

| Sweep claim | QC verdict |
|---|---|
| app-open: "live scorer hardcodes catches/stumpings/runOuts/lbwBowled to 0" | **HALF WRONG.** `lib/espn.ts:536-537` already reads `caught`/`stumped`. Only `:535 bowlLbwBowled:0` and `:538 runOuts:0` are live. Both app-open and decide:cricbuzz caught this; the *task brief* is what\'s stale. |
| bot-open N2: cricapi-only freeze reaches `classify_match_status:1662` | **MECHANISM WRONG, CONCLUSION RIGHT.** `:1657 if unsourced:` fires *before* `:1662 if not espn_present:` — a cricapi-only match has all players unsourced, so it never reaches `:1662`. The real freeze path is `unsourced AND already_completed → COMPLETED_FLAGGED` (`:1660`) → `record_settlement` (`:2295`). Same outcome, different line. Fix the line ref or the item is unpatchable. |
| bot-open N2 sized at "~150-300 FP/match, hypothetical" | **IT ALREADY HAPPENED — upgrade to P0 with a named match.** See B1 below. |
| bot-open N5 "cause SUSPECTED / could be a bug" | **Not a bug.** `override_sources` is populated at `:2057-2059` (`sources_out=override_sources`) and passed at `:2298`/`:2310`. 0/3119 simply means no override landed on a newly-settled row. Demote to a 10-min assertion, not a P1. |
| decide:cricbuzz "≈41 FP/match the app is dropping" | **TRUE BUT MIS-CLASSED.** That is the app\'s **provisional live** path only; the sheet overwrites it on completion. It affects in-play H2H display, **not settlement**. The bot\'s +36 FP on 2 ODIs *is* money-final. The sweeps conflate two severity classes throughout — the plan must separate `MONEY-FINAL` from `LIVE-DISPLAY`. |
| bot-open Part A (9 "false open" doc items) | **Spot-checked 4, all correct.** `merge_espn_into:1455-1458`, `compute_l1_gaps:1537`, `_l2_baseline`, `limit=1000`+page loop `:951`. Keep Part A — it is the most useful thing in the five sweeps. |
| E9 sweep\'s "2 of 24 mismatch" LPL figure | **Self-invalidated** by its own D2 (the metric double-counts assisted run-outs). Do not quote the figure anywhere until re-run. |

---

## PART 1 — MONEY-FINAL, ALREADY CORRUPTED (P0)

**B1. 1562 of 3119 settlement rows are frozen at `COMPLETED_FLAGGED`, write-once, and at least one match froze off a cricapi-only card with no dots and no XI. PROVEN (measured).**
`registry/settlement_snapshots.json`: 3119 rows / 56 matches / 3 tours (LPL 1074, Hundred M 1042, Hundred W 1003); status split **1557 COMPLETED / 1562 COMPLETED_FLAGGED**. LPL **Match 21 GG v JK** rows carry `source: "cricapi · limited (no dots/XI — ESPN unavailable) · ⏳ provisional"`. `record_settlement` (`wc_fps_to_csv.py:2792`) is `if (match_key,pid) in SETTLEMENTS: return` — that number can never be corrected.
Size: dots ~80/innings @1pt + XI 22×4 = **~200+ FP frozen low on that one match**, unbounded across the other flagged rows.
Root cause = the `already_completed` freeze trigger at `:2295`/`:2306` accepting `COMPLETED_FLAGGED`. **Fix: gate the freeze on `COMPLETED` only, or on L1-done. 3 lines + a re-baseline (B2). Effort ½ day.**

**B2. THE MISSED ITEM — there is no way to supersede a settled row, so every fix in this plan will look like a regression. PROVEN, and no sweep raised it.**
E1/E2/E7/E9-substitute/the +36 FP ODI hand-fix all *change points on matches already frozen*. After they ship: the sheet shows the corrected number, the baseline keeps the wrong one, and `_points_delta` (`:2807`) prints a non-zero delta for **every corrected player** — indistinguishable from a real regression on the app\'s audit tab (`app/draft/[code]/results/page.tsx:364-378 ReconBanner`). Hundreds of false "changed" rows will bury the real ones.
**This must be sequenced first, not last.** Two options: (a) add `supersede_settlement(reason_code)` + a `superseded_by` field so a deliberate fix is distinguishable from drift, or (b) settle every affected contest *before* the scorer fixes land. **Effort ½–1 day. Nothing else in the plan is safe to ship until this exists.**

**B3. 2448 of 3119 settled rows (78%) carry no `fields` → they still hit `_l2_baseline`\'s recompute path. PROVEN (measured: 671 with `fields`).**
The phantom-`dots 0→N` class the RECON_DEV_PLAN 1.4 fix was written to kill is still live on 78% of the corpus, because the code shipped and the data was never re-seeded. **Effort ½ day, data-only. Combine with B2 — same migration pass.**

**B4. CPL and the IND-ENG ODIs have ZERO settlement rows. PROVEN (tour counter shows only LPL + Hundred M/W).**
These are exactly the tours carrying the 82 orphan pids (A1) and the +36 FP run-out gap. Whatever has been paid on them has **no baseline and no audit trail at all**. Decide before the next payout: baseline them, or accept them as unauditable and say so out loud.

---

## PART 2 — MONEY-FINAL, WILL CORRUPT THE NEXT MATCH (P0)

**B5. `is_over` reads cricapi-only keys → the moment cricapi leaves, the tour writes ZERO rows. PROVEN (read `:1857-1863`).**
```python
def is_over(m):
    if m.get("matchEnded"): return True
    if not m.get("matchStarted"): return False
```
Both keys are absent on an ESPN-derived record. `ended=[]`, `live=[]` (`:1864-1869`), `to_score=[]` (`:1885`). **Every contest reads 0 for every player.** This is a total blackout and it is listed in **none** of B1–B4/R1 in ESPN_ONLY_MIGRATION.md.
Bundled with the same fix: the 8h clock (`OVER_HRS`, `:1847`) is a **promoter** — a long rain break pushes an in-play match past `start+8h` into `ended`, it scores off a partial card, and `record_settlement` freezes the partial **write-once** (SUSPECTED — structurally reachable, not yet observed).
**Fix = design:completion\'s `espn_match_state()`. Its measured traps are the valuable part and all check out against the sweep\'s own data: `status.type.completed` KEY DOES NOT EXIST (so `t.get("completed")` is falsy on a finished match); `competitors[].winner` is the STRING `\'true\'`; innings `description` is a 3-value vocabulary `{complete, target reached, all out}`; `endDate` is the scheduled final-day boundary, off by up to 2 days — do not anchor the 7-day cutoff on it; `ballsperover` is `\'5\'` on the Hundred. Zero extra HTTP (same `summary` cache key as `espn_xi:916`). Effort 1 day incl. tests.**
⚠ One honest gap: all 66 samples are finished matches, so the `pre`/`in` vocabulary is **SUSPECTED**. Observe one live match before flipping.

**B6. `summary` has no fetch-failure guard — the exact bug `785dec8` fixed on `playbyplay`, on the other endpoint. PROVEN (read `:865`, `:879`, `:916`).**
`espn_get` returns `{}` on 502/timeout/WAF-403 (`:811`), indistinguishable from "no data". The refuse-to-score guard at `:949-970` covers playbyplay only. On a summary blip: run-outs → 0 (~8-12 FP), XI → empty → `espn_perf` filtered `if v["played"]` at `:1934` **discards the entire ESPN perf set**, and team attribution (`espn_team_map`) dies. This is the Hundred-Women\'s 3/10-fields shape, and per B1 it can then **freeze**. Given [ESPN blocks browser UAs] is a known live failure mode in this stack, treat as imminent. **Effort ~15 lines.**

**B7. Match discovery joins on team DISPLAY NAMES, not the series id (R1). PROVEN (`:2001-2003 es not in match_shorts: continue`; `espn_series` never used for discovery, `:816-827`).** A name miss scores **nothing** for that match, silently. **Effort 2-3h — join on `espn_series` event ids.**

---

## PART 3 — ONE ROOT CAUSE, FOUR SYMPTOMS (merge into a single work item)

**B8. ESPN `athlete.id` is discarded on every non-batting row → known players are silently dropped or credited to the wrong `ci:`. PROVEN.**
This is **E7 + E6 + B3 + E9-D1 as one bug.** Do not plan them as four.
- `parse_espn` extracts `fld_id` at `:1064` and **drops it** at `:1067`/`:1069` (`get(fld)["catches"] += 1`, name-keyed).
- `espn_xi:916` carries `espn_id` but `blank_perf(e["name"])` at `:1090` discards it.
- Fall-through to the name table can credit a **different** `ci:` (E6).
- Once cricapi leaves, a played-but-unslotted player is dropped with **no row and no flag** (B3).
**Caught in the act, PROVEN, with money on it:** Virandeep Singh (LPL M1, `ci:633660`, fully registered — alias `virandeep singh`, draft_id 10357) took an unassisted run-out. ESPN gives `outDetails.fielders=[{id:633660}]`; cricsheet confirms `substitute:true`. `espn_runouts` returns fullName `"Virandeep Singh Jagjit Singh"`, `norm()` ≠ the alias, id discarded → **frozen at `points: 0`** in the snapshot. I measured him at **0 across all 11 LPL matches he appears in**. `dro=12` (`:89`, applied `:1170`) = **12 FP lost, write-once**.
**Decide the rule first: does a substitute fielder who never appears in an XI score in this app?** Yes → resolve fielder rows on `espn_id`/`cricsheet_id` before name + team-assign a sub from the fielding side (**2-3h + test**). No → suppress deliberately and exclude from the wicket count (**30 min**). Today\'s behaviour is an accident either way.

**B9. E9 is DIAGNOSED and is not an ESPN defect — it is our metric. PROVEN, and I accept the diagnosis.**
ESPN header, cricsheet, and ESPN playbyplay all agree (16/16/16 and 15/15/15). "Ours" = `Σ bowler w + Σ run-out FIELDER CREDITS`, and `:1077-1086` does `rp["runouts"] += 1` **per fielder** — so a 2-man assist inflates by 1 (ev1537334) and a dropped substitute deflates by 1 (ev1537331, = B8). **Per-fielder credit is correct scoring (6 each); it is only wrong as a wicket count.** The danger is that it *masks* real misses — a genuinely missing wicket nets to zero against a 2-man run-out.
**Fix: change the check to `len(pbp items with dismissal.dismissal)`, then RE-RUN the 24-event sweep. Effort ~20 min + rerun. Do not close E9 on the old figure.**
Also from the same read, **keep both traps in the plan**: (i) `header.competitions[].competitors[].linescores[]` carries a `0/0` mirror row per competitor — any header check must filter `isBatting:true`; (ii) `espn_runouts` appends only `if fl:` (`:911`) — a run-out with no listed fielder is dropped silently, costing 6/12 pts **and** the wicket. Not observed; **SUSPECTED**; add a loud warn, 15 min.

**B10. E2 — `dismissed`/`dismissal` stamped on the playbyplay STRIKER, not the victim. PROVEN (`:1051`), confirmed on real data.**
ev1537331 item 216030: `shortText "Akif Javed to Wiese, OUT"`, `type "run out"`, but cricsheet\'s `player_out` is **C Wickramasinghe** (the non-striker). 7/17 wrong in the earlier sample. Harmless for the wicket count, **but the duck rule at `:1150-1152` is written to fire on exactly this case** — a non-striker run out for 0 — so the −2 lands on the striker. **±4 FP per occurrence. Fix: take the victim from `summary.outDetails`, which is keyed on the dismissed player\'s own roster row — same pass as B8. Effort 1-2h; fold into B8.**

**B11. E1 — `bat_order` never set on the ESPN path. PROVEN (`:728` cricsheet, `:1283` cricapi, absent in `parse_espn`).** Data exists at `statistics.batting.order`, matched cricsheet 60/60. **Trivial, ~30 min. Fold into B8\'s pass.**

---

## PART 4 — APP (`wwc-draft`)

**A1. P0 — 82 players carry `slug:` pids that cannot exist in the points sheet → they settle at ZERO. PROVEN cause; magnitude pending one curl.**
`data/players-raw.json` = 965 (883 `ci:` + 82 `slug:`). I independently confirmed the bot side: `registry/players.json` = 679 entries and contains **no Kohli, no Rohit Sharma, no Bumrah, no Gill**. The 82 = 75 CPL (MTGUY 13, MTJAM 12, MTSTK 12, MTANT 11, MTBAR 11, MTSTL 9, MTTRI 7) + 7 OIND.
`lib/points.ts:69-70`: `if (pid && pointsMap.has(pid)) return …; if (pid && !liveFallback) return null;` — on a completed match `useLive=false`, so a pid\'d player missing under that pid returns **null with no name fallback, permanently**. No code path emits `slug:rohit-sharma`.
Exposure: a CPL XI drafted from these squads ≈ a **full XI total**; an ODI XI with Kohli+Rohit+Bumrah ≈ **150-250 pts**. Compounded by B4 (those tours have no settlement baseline to catch it).
**Verify now (5 min): curl one CPL tab and diff its `Player ID` column against the 82. Fix (20 min): re-run `build_registry.py` with CPL+OIND rosters → `registry/backfill_draft_pids.py` → re-copy `lib/registry-players.json`. Same root cause as the tour-setup defect "build_registry harvests zero ESPN athletes for men\'s franchise tours" — fix once, upstream.**

**A2. P0 — `anyStats` gate missing on lobby + match hub → a 50-pt phantom H2H before a ball is bowled. PROVEN.**
The gate exists **only** at `app/api/draft/[code]/results/route.ts:87`. `lib/live-points.ts:31-33` returns `live.points` whenever `opts.live` and the fetch returned anything, with no `anyStats` check — and `lib/espn.ts` does return the map with `anyStats=false`. Callers: `app/lobby/page.tsx:224`, `app/match/[key]/page.tsx:134`. Between XI posting and first ball, `espn.ts:504 played:true` gives every starter +4. **9×4 + C 4×2 + VC 4×1.5 = 50.0 pts/team**, plus a false leader whenever XI sizes differ — while the results page correctly shows nothing. **3 lines.**

**A3. P1 — the two scorers have ALREADY drifted again, on the `liveFallback` argument. PROVEN — this is new, and it is the more important half of the "two scorers" question.**
The 199-pt BACKUP_INTELLIGENCE hole is **CLOSED** — `lib/contest-scoring.ts:28-40` reads the frozen `effectiveLineup` + cascaded C/VC. Do not re-report it.
But `calcSelectionPoints` is `(sel, ppu, matchPts)` — 3 params — and calls `lookupPlayerPoints(p.pid, p.displayName, p.name, matchPts)` with `liveFallback` **defaulting to false**. The results route at `:170` passes `lookupPlayerPoints(..., scoringMap, useLive)`. **During a live match, lobby/hub and the results page compute different totals for any player whose pid didn\'t resolve in the ESPN map.** The route\'s own per-player loop is still an independent second implementation (`:170` vs the shared `calcSelectionPoints` used only for the audit compare at `:259`).
**Fix (30 min): pass `liveFallback` through `calcSelectionPoints`, then in the results route assert its own XI sum `=== calcSelectionPoints(...)` and `console.error` on mismatch. That single assertion converts every future drift from a silent money bug into a log line — worth more than the fix itself.**

**A4. P1 — port the bot\'s `outDetails` read into the app\'s live scorer. PROVEN; LIVE-DISPLAY class, not money-final.**
`lib/espn.ts:535 bowlLbwBowled:0` and `:538 runOuts:0` (whose comment "live feed doesn\'t reliably attribute run-outs" is **false** — `grep -c outDetails lib/espn.ts` = 0, yet the same `summary` object is in hand at `:467`, and `espn.ts:483-508` already loops `rosters[].roster[]` and flattens `linescores`).
Measured over 24 cached LPL summaries — schema is complete: `c` 214/214 have both `bowler.id` and one `fielders[].athlete.id`; `bowled` 56/56 and `lbw` 32/32 have `bowler.id`; `st` 5/5; `run out` 19/19 with fielders (10 direct / 9 assisted). `athlete.id` **is** the `ci:` id, so the join is free. **Zero extra HTTP.**
Live-display gap: lbw/bowled 88 events = 704 pts (**29.3/match**); run-outs 228 (**9.5**); direct-RO uplift, since `d11-score.ts:67-74` pays every RO at the assisted rate of 6 — 120 (**5.0**).
**Genuine new sub-finding, not a duplicate:** ESPN\'s `caught` stat total is **207 vs 214 `c` cards**, and 9 cards have `fielders[0].athlete.id == bowler.id` — the `caught` stat **omits the bowler-as-catcher**. The bot already handles this at `:1064`; the app does not (~2.3/match).
**Fix by construction: derive all four from `outDetails` and stop reading `get("caught")`/`get("stumped")` — one self-consistent derivation. Needs a pre-pass `Map<athleteId,{lbwb,catches,st,ro,dro}>` before the loop at `:501-548`, plus `directRunOuts` on `Perf` (`d11-score.ts:22-38`) and `fielding()` (`:67-74`). Free rider: `battingPosition` is present (n=564) → `PlayerLine.order` (`espn.ts:486`) is a one-liner = the app\'s E1. Effort 2-3h.**
One decision: a substitute fielder can appear in `outDetails.fielders` with no `starter||subbedIn` roster row (`espn.ts:512`) — **must match whatever you decide in B8.**

**A5. P2 — `isPidKey` never updated for the `ci:` migration. PROVEN, but honestly low-impact.**
`lib/players.ts:22` `/^(espn:|slug:)/.test(k) || /^[0-9a-f]{8}$/.test(k)` — `ci:` is not matched. Consequence: at `points.ts:48` and `players.ts:306`, `ci:` keys are passed into `fuzzyMatchName` as candidate **names**. Exact-pid lookup still works, and "Rohit Sharma" won\'t fuzzy-match "ci:34102", so I could not construct a wrong-points failure. **It defeats the stated invariant and is a landmine, not an active leak. 1 line — do it, but do not sell it as a money bug (the sweep implied more than the code supports).**

**A6. P2 — duplicate `(match,pid)` rows resolve last-wins rather than max-wins.** Carried from the brief, **not independently verified this session — SUSPECTED.** Verify before planning effort.

---

## PART 5 — WHAT ALL FIVE SWEEPS MISSED

1. **B2 (supersede/re-baseline) — the biggest gap.** Five sweeps proposed fixes to frozen numbers; none noticed that write-once means the fixes cannot land in the baseline and will masquerade as regressions. **Nothing ships before this.**
2. **Sequencing.** No sweep gave an order. The forced order is: **B2 → B5 → B6 → B8(+B10, B11, B9) → B7 → cricapi removal.** Specifically: B5 must precede the cricapi flip (else blackout); B8 must precede B3 being meaningful; A1 must precede the next CPL/OIND settlement.
3. **B4 — CPL and ODI have no baseline at all.** Every sweep measured the 3 tours that *do*; none flagged the two that don\'t, which are the same two carrying A1\'s orphans and the +36 FP hand-fix.
4. **No pre-payout GO/NO-GO gate exists.** `npm run check:tours` exits **0** while printing `MTGUY 24% · MTBAR 31% · MTSTK 33% …` — it informs, it does not block. The owner asked for a plan where nothing slips; the deliverable that actually achieves that is **one script, run before each settlement, that hard-fails on: any XI player whose pid is absent from `lib/registry-players.json`; any match frozen `COMPLETED_FLAGGED`; any settled row lacking `fields`; any non-zero `Points Delta`.** ~2h, and it retires most of this list as a recurring risk. (App-side twin of the tour-setup defect "the pid-coverage gate counts an unanchored player as resolved".)
5. **The 2 ODI hand-fixes (ev 1538627/1538628, +36 FP) have no owner and no mechanism.** They are not in `settlement_snapshots.json` (PROVEN — those tours are absent), so they *can* still be fixed cleanly — but only if applied **before** those tours get baselined. Time-boxed window; put it first.
6. **Test coverage of the freeze path is unverified.** 224 bot tests pass, but I did not confirm any covers `record_settlement` / `classify_match_status` on the FLAGGED branch. **SUSPECTED gap** — make a regression test for B1 part of B1\'s definition of done.

---

## PART 6 — VERDICT ON CRICBUZZ (concur, with the reasoning tightened)

**No, nowhere.** Cricbuzz adds **zero fields ESPN lacks** and is strictly worse on the one field that is single-sourced (its bowler `dots` is a dead-zero column, derivable only from commentary at 11/12 reconcile; ESPN\'s dots are exact vs cricsheet). Its `wicketCode`+`fielderId1..3` recipe is field-for-field what `outDetails` already gives us, except ESPN\'s ids **are** `ci:` ids. The completion signal Cricbuzz was wanted for is present in ESPN\'s own `summary` (`header.competitions[0].status.type.state == "post"`, 66/66) — that\'s B5. And the one honest pro-Cricbuzz argument, "independent third opinion for E9", is dead: **E9 is now diagnosed as an intra-ESPN metric artifact (B9), not a data disagreement.** cricsheet at L2 remains the arbiter. **DATA_SOURCE_EVAL_20260813.md §8 should be marked superseded**, since its sizing was computed off a gap ~2× larger than the real one (it counted `catches`/`stumpings` as missing when `espn.ts:536-537` already reads them).', 'found': ['## BOT REPO SWEEP — deduplicated open items

Verified against HEAD (`5d0e6f2`, clean tree bar 3 untracked files). `224 passed in 1.13s`. All line refs `/Users/nishant-singodia/wwc-points-bot/wc_fps_to_csv.py` unless stated.

---

## PART A — FALSE OPEN. Already fixed in HEAD; do not put these in the plan

These are still written as open in the docs the owner was told to read. Each one I read in HEAD and confirmed shipped. This is the largest single source of wasted attention in the current doc set.

| Doc says open | Reality in HEAD |
|---|---|
| `RECON_DEV_PLAN` **Phase 2.1** "ESPN-only player loses everything but dots — published 4 pts vs 110 earned" | **FIXED.** `merge_espn_into` `elif e.get("played")` branch now does `np = dict(e)` (:1455-1458), copying the full ESPN record. |
| `RECON_DEV_PLAN` **Phase 2.2** "`compute_l1_gaps` iterates `capi_pid` only" | **FIXED.** :1537 now iterates the union; docstring states it explicitly. |
| `RECON_DEV_PLAN` **Phase 1.3** "settlement record is points-only; cannot serve as a field-level baseline" | **SHIPPED.** `record_settlement` (:2772) stores `rec["fields"]` over `SETTLED_FIELDS` (:2798) plus `field_sources`. |
| `RECON_DEV_PLAN` **Phase 1.4** "L2 recomputes the baseline — root cause of phantom `dots 0→N`" | **SHIPPED.** `_l2_baseline()` reads the frozen record; the L2 hold at :2110-2118 holds `base = _l2_baseline(pid)`, not a recompute. |
| `RECON_DEV_PLAN` **Phase 1.1** "new `classify_recon_state()`" | **SHIPPED** at :1608. |
| `RECON_DEV_PLAN` Phase 6 / `RUNBOOK:128` "ESPN playbyplay `limit=600`, no pagination" | **FIXED in the live path** (:951, `limit=1000` + page loop + refuse-partial :949-970). The `limit=600` at :831 is inside **dead** `espn_dots()`. Re-reporting this as a scoring bug is wrong. |
| `STREAMLINE_PLAN` §3 prerequisites 1 & 2 (run-outs, dots pagination) — "do NOT flip before these" | **Both done.** §2c\'s "ESPN run-outs 20 vs 0 — completely broken" is stale. |
| `ESPN_ONLY_MIGRATION` §5.1 "28 rescore candidates" | Self-resolves — the 18-Aug ESPN-only matches already carry the fix via in-place rewrite. |
| `DATA_SOURCE_EVAL` open items: "−1 legal-ball edge case", "confirm `STUMPED`" | **Not bot defects.** Both are properties of a Cricbuzz parser that does not exist in this repo, and §8 recommends *not* adding Cricbuzz to the bot. Zero current exposure. |

**Nothing in your known-open list (E1/E2/E6/E7/E8/E9/B3/R1) is fixed.** I confirmed each is live: `bat_order` set only at :728 (cricsheet) and :1283 (cricapi), never in `parse_espn`; `pb["dismissed"]` on the playbyplay striker at :1051; `fld_id` extracted :1064 and dropped at :1067/:1069, `blank_perf(e["name"])` at :1090; `is_over` time-based at :1847/:1857; `es not in match_shorts: continue` at :2001-2003; `espn_event_id` name-join at :816-827 with `espn_series` never used for discovery.

---

## PART B — NEW open items (not in your list, not duplicates)

### 🔴 N1. The `summary` payload has NO fetch-failure guard — the same bug `785dec8` just fixed, on the other endpoint · PROVEN · ~15 lines
`espn_get` returns `{}` on any failure (:811 — 502, timeout, WAF 403), indistinguishable from "no data". Commit `785dec8` added a refuse-to-score guard for **playbyplay** (:949-970). It was **not** added to `summary`, which three functions read with no check: `espn_team_map:865`, `espn_runouts:879`, `espn_xi:916`.

Failure: playbyplay succeeds, summary blips. Match scores as complete with **run-outs = 0** (~1.1/match, **8-12 FP**) and **no XI credit** (22 × +4 = **88 FP**). Worse, `espn_perf` is then filtered `if v["played"]` at :1934 — with an empty `espn_xi` the entire ESPN perf set is discarded. This is exactly the shape of the Hundred Women\'s 3/10-fields incident, and it now sits on the primary feed\'s only source of run-outs, XI and (via B3) team attribution.

### 🔴 N2. A cricapi-only match still publishes COMPLETED_FLAGGED **and freezes a settlement** — violates locked rule 4 · PROVEN · ~10 lines
`classify_match_status:1663` returns `("COMPLETED_FLAGGED", "⚠ unverified — single feed (cricapi only)")`. The locked model says no ESPN ⇒ no dots ⇒ unconsumed ⇒ **LIVE**. Then `emit` freezes it: `if match_status in ("COMPLETED", "COMPLETED_FLAGGED"): record_settlement(...)` at **:2295 and :2306**.

Failure: a match where `dots_final = False` (:1967) and the Dots column is blanked (:2285) still gets a **write-once** baseline frozen. Dots are 1 pt each, ~80 per innings — the frozen number is systematically low by **~150-300 FP/match**, and write-once means it can never be corrected. This is the single most dangerous open item: it corrupts the record the whole audit surface depends on.

### 🔴 N3. Settlement freezes on the COMPLETED publish, not at L1-done · PROVEN · ~½ day
:2295/:2306. Last unshipped piece of the locked freeze model (`RUNBOOK:130`). Compounds N2 — the looser trigger is what lets a FLAGGED match freeze at all.

### 🟠 N4. 2448 of 3119 settlement rows have no field-level record → the phantom-dots class is still live on 78% of settled rows · PROVEN (measured now) · ~½ day, data only
`registry/settlement_snapshots.json`: 3119 rows, **671** carry `fields`. Every row without it falls through `_l2_baseline`\'s legacy branch to a **recompute** — the exact path `RECON_DEV_PLAN` 1.4 exists to kill. The fix shipped; the data was never re-seeded, so the bug is still live for everything settled before it.

### 🟠 N5. `field_sources` is populated on **0 of 3119** rows · PROVEN (measured); cause SUSPECTED · 1h to confirm
The "why is the frozen number what it is" audit trail is empty everywhere. Benign explanation: no override has landed on a *newly* settled row since the change shipped. Malign explanation: `override_sources` (:2054, passed at :2298/:2309) never gets filled. Confirm before building Phase 5 on top of it.

### 🟠 N6. Settlement coverage is 56 matches across exactly 3 tours · PROVEN (measured now)
Baselined tours: LPL 2026 (1074 rows), Hundred Men\'s (1042), Hundred Women\'s (1003). **WWC, MLC, CPL, every bilateral and every ODI have zero baseline rows.** `/audit` can never prove those didn\'t move, and `RUNBOOK` §5 check 5 fires on all of them.

**Direct consequence for the 2 ODI hand-fixes in your list (+36 FP, ev 1538627/1538628): they land in `NO_BASELINE`, so the audit page will NOT flag them.** If those contests were already paid, that is a manual payout adjustment across 4 players, and nothing in the system will remind you.

Provenance split, re-measured: `live` 1906 / `unknown` **1027** / `seed` 186. The 1027 read `NO_BASELINE` forever unless re-seeded.

### 🟠 N7. Identity is still routed into Recon Review — violates locked rule 5 · PROVEN · ~30 lines
`"param": "ID"` row emitted at **:2225** (ack at :2207, resolver at :3062). `RECON_DEV_PLAN` 1.5 specifies deleting this and applying the ESPN-saw-him-play discriminator. 37 stranded rows sit in the value tab with no matching published row — they are the reason the tab is too big to use.

### 🟠 N8. `xcheck` is computed and never read · PROVEN · ~20 lines
Full symbol census: STORE :1427, :1446; RETURN :1459; call site :2022; init :2017. **Zero LOADs after :2022.** A detected cricapi-vs-ESPN conflict on `r` / `w` / `runs_conceded` is found and binned. `runs_conceded` drives econ — a 30-run conflict is ~6-10 FP, silently arbitrated. (Listed in `RECON_DEV_PLAN` 2.3 and `CLAUDE.md`; confirmed still true, so it belongs in the plan, not the "already known, already fixed" pile.)

### 🟠 N9. `L1_RUN_TOL = 1` — a runs-based tolerance on a points-based problem · PROVEN · ~20 lines
:1510, applied at :1518 via :1573 and :1760. A 1-run difference is never flagged, but 1 run can cross a milestone (49→50 = +8) and an SR band. `RECON_DEV_PLAN` measured 535 (runs, balls) combos moving ≥3 pts, up to 7 pts/row. `points_gap()` already exists at :1219 — swap the comparator.

### 🟡 N10. L1 compares only 4 of ~14 scoring fields · PROVEN · ~1 day, gated on N8
`RECON_L1 = ["r", "w", "4s", "6s"]` (:1358). Never L1-compared: `catches` (+8), `stumpings` (+12), `runouts` (+6/+12), `lbwb` (+8), `runs_conceded` (econ), `balls`, `dots`, `maidens`. `RECON_DEV_PLAN` Phase 6 puts this at **22%+ of scoring exposure resolved silently**. Ship N8 first to size the row volume.

### 🟡 N11. The truncation guard compares RAW items to `count`, before dedup · PROVEN code, SUSPECTED exposure · 1 line
:970 `if _expected is not None and len(items) < _expected` runs on `items`; the dedup to `_uniq` happens at :974-983. ESPN is known to emit duplicates (ev 1537345: 259 raw / 255 unique). **N duplicates mask N genuinely missing deliveries.** One-line fix — dedup, then compare — on the guard that protects the feed you are about to make primary.

### 🟡 N12. `emit` resolves the Player ID by NAME ONLY · PROVEN · 1 line
:2248 `pid = resolve_pid(name) or (resolve_pid(d["name"]) if d else "") or ""`. It never calls `resolve_perf_pid`, so an ESPN `athlete.id` already in the perf dict is not used. For a player the registry has never seen, the row emits with a **blank Player ID** → unjoinable by the draft app → **the entire row\'s points land on nobody**. Distinct from E6/E7 (those are the parse path; this is the emit path, and it is the one that produces the blank cell). Same class as the 20 corrupted rows in `NAME_MATCH_AND_ISSUES_CRITICAL.md`.

### 🟡 N13. Substitute fielder loses his credit · PROVEN, probably correct — decide and document
`espn_xi:922` sets `played` only for `starter or subbedIn`; :1934 filters `espn_perf` on `v["played"]`. A pure sub fielder credited with a run-out has it deleted (6-12 FP). 0 occurrences in 17 credits, and a non-XI sub isn\'t draftable. Recommend documenting the decision rather than changing behaviour — otherwise it gets "discovered" as a bug later.

### 🟡 N14. Dead code that will actively mislead the ESPN-only flip · PROVEN · 10 min
- `espn_dots` :829 — 0 call sites. **This is the only remaining `limit=600` no-pagination site**, and it is what `RUNBOOK:128` / `RECON_DEV_PLAN` Phase 6 now unintentionally point at. Delete before someone "fixes" the wrong function.
- `crosscheck` :1094 — 0 call sites.
- `ESPN_ONLY_FIELDS` :1413 — assigned, 0 reads.
- `RECON_L1_SINGLE` :1606 — assigned, 0 reads. **Note:** the locked "single-source fields get no L1 recon" rule is therefore not implemented — it is merely inert, because `dots`/`maidens` happen not to be in `RECON_L1`. Add `dots`/`maidens` to L1 (N10) without wiring this and you reintroduce the phantom-dots tab.

### 🟡 N15. Registry hygiene, measured now · PROVEN · ~1h
- **13 duplicate** `(match_key, pid, field)` rows in `registry/recon_overrides.json` (935 total) — e.g. `2026-07-22::southern brave|welsh fire` / `ci:669365` / `r`, `4s`, `6s`.
- **4 override pids absent from `players.json`**: `ci:1150021`, `ci:459508`, `ci:859899`, `slug:fabian-allen`. Their approvals resolve to nobody.
- Clean: `players.json` 679/679 `ci:`, `needs_cricinfo_pending` = 0, `new_players.json` 39 (25 new / 10 auto / 4 needs-cricinfo), `frozen_tours` 1.

### 🟡 N16. Repo hygiene · PROVEN · 2 min
`DATA_SOURCE_EVAL_20260813.md` is **untracked** — the entire Cricbuzz evaluation is uncommitted and one `git clean` from gone. Also untracked: `registry/recon_overrides.json.bak-prekey-20260807`, `.DS_Store`.

### 🟠 N17. Pre-flip debt must be cleared before the cricapi migration · PROVEN
`ESPN_ONLY_MIGRATION` §5.4: **204 CHANGED rows / 2508 abs FP / 43 matches**. Work to zero *before* the flip or migration damage is indistinguishable from pre-existing drift. This is Step 0 of that plan and it is not started.

### 🟡 N18. MLC M32/M33 run-outs read 0 for the whole match · SUSPECTED · ~2h
Same class as the two ODI hand-fixes already on your list. No MLC cricsheet archive extracted locally, so unverified; ~8-12 FP each, **ceiling +24 FP**. Extract the archive and check before settling MLC.

### 🟠 N19. The two guardrails `STREAMLINE_PLAN` §7 names as "still to add" are still missing · ~1-2 days
- **`SAFE TO SETTLE` badge** — collapses `RUNBOOK` §5\'s five-point checklist into one green light per match. Called out as "the single highest-value thing left for real-money use". Right now a five-step manual checklist is the only thing between a settle and a wrong payout.
- **Weekly ESPN-vs-cricsheet accuracy report** — so feed drift is caught by a number, not by noticing something looks off.

### 🟢 N20. cricapi\'s empty Hundred/LPL cards still not root-caused · `RUNBOOK:123`
Cache eviction for empty cards shipped; "does cricapi\'s series feed carry these fixtures at all" is unanswered. **Recommend explicitly deferring** — it becomes moot on the ESPN-only flip. Sequence it as "don\'t spend time here".

### 🟢 N21. Doc drift · 15 min
`RUNBOOK:110` says 218 tests, `CLAUDE.md` says 205; HEAD is **224**. `RUNBOOK` §7 and `RECON_DEV_PLAN` Phase 6 still list run-outs/pagination as open. Fixing the docs is the cheapest way to stop Part A recurring.

---

## Sequencing (highest FP-at-risk first)

1. **N2 + N3** — stop freezing unverified baselines. Write-once means every day of delay is permanent damage. (~1 day)
2. **N1** — summary guard. Same 15 lines protects run-outs, XI and B3\'s team attribution. (~15 lines)
3. **N4 + N5 + N6** — re-seed field-level baselines, confirm `field_sources`, decide what to do about the 118 unbaselined matches. Do the 2 ODI hand-fixes here, as an isolated commit, knowing `/audit` won\'t flag them. (~1 day)
4. **N12, N11, N14** — one-liners on the identity and truncation guards. (~1h total)
5. **N8 → N9 → N10** — make L1 actually arbitrate. (~1.5 days)
6. **N7, N15, N16, N21** — tab hygiene, data hygiene, doc truth. (~2h)
7. **N17** before any flip; **N19** before the next settlement round.', '## DRAFT APP (`/Users/nishant-singodia/wwc-draft`) — verified against HEAD `cd5ea4c`

**Test runs (all three, this HEAD):**
- `npm test` — **PASS.** 5 suites green (effective-lineup, points, d11-score, settlement-audit 28/28, points-tab-guard 6/6).
- `npm run test:integration` — **PASS.** Fresh `db/test.db` migrates clean, 8/8 (effective-XI freeze round-trip).
- `npm run check:tours` — **PASS (exit 0), but it prints the P0 below and does not fail on it:** `MTGUY 24% · MTBAR 31% · MTSTK 33% · MTJAM 40% · MTANT 45% · MTSTL 50% · OIND 53% · MTTRI 61%` name-only resolution. See D3.

---

### Answering the two specific questions

**Q: is there still more than one scorer?** — **One-and-a-half, and it has already drifted again.**
- `lib/contest-scoring.ts:28-40` **now has the BACKUP_INTELLIGENCE branch** (reads the frozen `effectiveLineup` + cascaded C/VC). The 199-pt hole is **CLOSED** — do not re-report. It\'s genuinely shared by lobby (`app/lobby/page.tsx:295,434,493,538`), match hub (`app/match/[key]/page.tsx:166`), audit (`app/audit/page.tsx:128`).
- But `app/api/draft/[code]/results/route.ts:170` still computes per-player rows with its **own** `lookupPlayerPoints` call, and only reuses `calcSelectionPoints` for the audit compare (`:259`). Two independent implementations of the same sum survive — **and they have already diverged on a new argument** (D2). `app/draft/[code]/results/page.tsx:102 calcXITotal` is a third summation but only re-adds the route\'s `fantasyPoints`, so it can\'t drift on the scoring rule (only on `isBackup`).
- **Durable fix (30 min):** in the results route, after building rows, assert its own XI sum `=== calcSelectionPoints(sel, ppu, scoringMap, useLive)` and `console.error` on mismatch. Converts every future drift from a silent money bug into a log line.

**Q: does the live scorer really hardcode fielding to 0?** — **Partly wrong; the claim splits three ways.**
- **catches/stumpings: ALREADY READ.** `lib/espn.ts:536-537` `catches: get("caught"), stumpings: get("stumped")`, scored at `lib/d11-score.ts:69-76` (catch 8, 3-catch +4, stumping 12). **Do not re-report.**
- **runOuts: STILL 0 — and the excuse comment is now false.** `lib/espn.ts:538` `runOuts: 0, // live feed doesn\'t reliably attribute run-outs to a fielder`. The bot reads them out of **the same `summary` payload this file already holds**: `wwc-points-bot/wc_fps_to_csv.py:889,903` → `rosters[].roster[].linescores[].statistics.batting.outDetails`. `grep -c outDetails lib/espn.ts` = **0**. `lib/espn.ts:483-508` already loops `rosters[].roster[]` and flattens `p.linescores` — it\'s a second pass over data in hand, **no extra fetch**. Size: 6 pts assisted / 12 direct; the bot\'s own recovery was 20/20 run-outs = +36 FP on two ODIs; 1-3 per live match = 6-24 pts on one player, doubled if captain. **PROVEN. Effort 45-60 min** (incl. mirroring the fielder name→pid resolution).
- **bowlLbwBowled: STILL 0** (`lib/espn.ts:534`). The bot\'s 18/18 comes from ball-by-ball/commentary, **not** the summary → needs a second network call per refresh. 8 pts/wicket, ~2-5 per innings = 16-40 pts/match spread across bowlers. **Effort 2-3 h — recommend DEFER**, run-outs are far higher value per hour.

---

### Findings

**D3 — P0, PROVEN in code / magnitude needs one sheet check. 82 players carry pids that cannot exist in the points sheet → they settle at ZERO.**
`data/players-raw.json` = 965 players: **883 `ci:` + 82 `slug:`**. `lib/registry-players.json` = 679 entries, **100% `ci:`, 100% with `espn_id`**. `/Users/nishant-singodia/wwc-points-bot/registry/players.json` = 679, **zero `slug:` pids**, and has **no entry for Rohit Sharma or Virat Kohli**. The 82 orphans: **MTGUY 13, MTJAM 12, MTSTK 12, MTANT 11, MTBAR 11, MTSTL 9, MTTRI 7 (75 CPL) + OIND 7** (`slug:rohit-sharma`, `slug:shubman-gill`, `slug:virat-kohli`, `slug:kl-rahul`, `slug:kuldeep-yadav`, `slug:jasprit-bumrah`, …).
`lib/points.ts:64-65`: `if (pid && pointsMap.has(pid)) return …; if (pid && !liveFallback) return null;` — on a **completed** match the route passes `useLive=false`, so a pid\'d player missing under that pid returns **null with no name fallback, permanently**. No code path can emit `slug:rohit-sharma`, so if the CPL/OIND tabs key by `ci:` these 82 show grey `0.0` and settle at 0. A CPL XI is drafted almost entirely from these squads → up to a **full XI total (hundreds of pts)**; an ODI XI with Kohli+Rohit+Bumrah ≈ **150-250 pts**.
**Verify before the next CPL/OIND settlement:** curl a CPL tab, diff its `Player ID` column against the 82 `slug:` pids. **Fix (20 min):** re-run `build_registry.py` with CPL + OIND rosters → `registry/backfill_draft_pids.py` → re-copy `lib/registry-players.json`. Same root cause as the tour-setup defect "build_registry harvests zero ESPN athletes for men\'s franchise tours".
**Companion (20 min):** make `check:tours` **fail**, not inform, when a team\'s players carry pids absent from `lib/registry-players.json` — that one gate catches this class before money moves. (App-side twin of "the pid-coverage gate counts an unanchored player as resolved".)

**D6 — P0, PROVEN. `anyStats` gate missing on lobby + match hub → a 50-pt phantom H2H before a ball is bowled.**
The gate exists **only** at `app/api/draft/[code]/results/route.ts:87` (`useLive = !!liveScore && liveScore.anyStats`). `lib/live-points.ts:31-33` returns `live.points` whenever `opts.live` and the fetch returned anything — **no anyStats check** — and `lib/espn.ts:697` does return the map with `anyStats=false`. Callers: `app/lobby/page.tsx:224` (`live: true`), `app/match/[key]/page.tsx:134`. Between XI posting and first ball, `lib/espn.ts:504` `played: true` gives every starter the +4 XI credit (`lib/d11-score.ts` `T20.xi=4`). **Measured size: 9×4 + C 4×2 + VC 4×1.5 = 50.0 pts per team** — a full fake scoreline on the lobby card and hub H2H while the results page correctly shows nothing (and a false leader whenever XI sizes differ or a pick is missing). **Effort 3 lines** inside `getMatchPointsMap` so every caller inherits it.

**D2 — P1, PROVEN, STILL OPEN. `liveFallback` only on the results route.**
Only caller passing it: `app/api/draft/[code]/results/route.ts:170` → `lookupPlayerPoints(p?.pid, displayName, p?.name, scoringMap, useLive)`. `lib/contest-scoring.ts:49` calls with 4 args → defaults `false` (`lib/points.ts:67`). So on a LIVE match, lobby/hub/audit score strictly by pid while the results page name-falls-back. The players who miss are exactly D3\'s 82 (`lib/espn.ts:545-548` emits only registry-pid / `espn:<id>` / name, and `resolveAthletePid`→`lib/registry.ts:51` can only ever produce a `ci:` pid). Same 150-250 pt (ODI) to full-XI (CPL) divergence, live. **Effort 5 min** (thread `live` into `calcSelectionPoints`), but D3 is the real fix.

**D4 — P1, PROVEN, smaller than feared. `isPidKey` never updated for the `ci:` migration.**
`lib/players.ts:22-24` `/^(espn:|slug:)/ || /^[0-9a-f]{8}$/`. **Measured: `isPidKey("ci:597806") === false`** — 883/965 pids. So `lib/points.ts:48` (`fuzzyLookupPoints`) and `lib/players.ts:306` (XI/bat-order join) both hand every `ci:` key to the **name** matcher. **Measured: `normName("ci:597806") === "ci"`** — all `ci:` keys collapse to one identical junk token. Probe result: `fuzzyMatchName("Smriti Mandhana", [3 ci: keys + "Smriti Mandhana"])` still returns the right name; `"Ci Smith"` / `"C Ci"` → null — **no false credit reproduced**. Exposure is a name normalizing to `"ci"` (or any matcher change) grabbing an **arbitrary** player\'s score — unbounded, 0 to 100+ pts. **Fix 1 line** (`/^(ci:|cs:|uncapped:|espn:|slug:)/`) + a unit test. Do it regardless: it sits directly on the fuzzy path that `liveFallback` deliberately re-opens.

**D5 — P2, asymmetry PROVEN in code / occurrence SUSPECTED. Duplicate `(match,pid)` rows: last-wins in one place, SUMMED in the other.**
`lib/points.ts:571-572` (`getMatchPointsForMatch`) `result.set(name, pts); result.set(pid, pts)` = **last-wins**. `lib/points.ts:608` (`getTourPoints`) `const add = (k,v) => result.set(k, (result.get(k) ?? 0) + v)` = **sums**. Same duplicated sheet row → whichever came last in a contest score, **double** on the draft/selection board\'s tour total. Neither is "max"; no dedup guard anywhere in `lib/points.ts`. A duplicated 90-pt row shows **180** on the board. **Fix 15 min: dedupe on `(match,pid)` taking the last row AND `console.error` the collision** — silently taking max would hide a bot-side duplicate.

**D8 — P3, PROVEN, still unfixed since 24 Jul. LineupRefresh has no match-state guard.**
`app/draft/[code]/results/page.tsx:571` renders `<LineupRefresh …>` unconditionally right after the ReconBanner (`:566`), unlike the board page. Shows a lineup-refresh CTA on LIVE/COMPLETED. 0 FP, trust cost only. **Effort 2 min:** wrap in `{!data.started && (…)}`.

**Hygiene — P3.** 16 untracked one-off scripts in the tree (`scripts/_v0..v9.ts`, `_vlib.ts`, `fix-lpl-*.ts`, `fix-pushap-*.ts`, `inspect-team.ts`). Per the deploy gotcha `vercel --prod` ships the whole working tree. Clean or gitignore before the next deploy. 5 min.

---

**Verified FIXED at HEAD — do not re-report as open:** `calcSelectionPoints` BACKUP_INTELLIGENCE branch (`lib/contest-scoring.ts:28`); catches/stumpings read from ESPN (`lib/espn.ts:536-537`); gviz unknown-tab validation (`lib/points.ts:79-96`, test 6/6); ESPN browser-UA 403 (`55ff7a5`); phantom wickets from `dismissals` (`lib/espn.ts:518-522`).

**Priority:** P0 D3 + D6 (~25 min + one sheet verification) → P1 D2, D4 (~10 min) → P2 D5, D7-runOuts (~1 h) → P3 D7-lbwBowled, D8, hygiene. Plus the 30-min cross-scorer assertion, which is the only durable answer to "two scorers".', '## VERDICT: Cricbuzz is not needed anywhere. The premise in DATA_SOURCE_EVAL_20260813.md §8 is wrong on two counts.

**Measurement set:** 24 cached ESPN `summary` payloads (LPL T20, events 1537331–1537354) at `/private/tmp/claude-501/-Users-nishant-singodia/f36700e2-70e2-4403-9da5-40a03f07ecbc/scratchpad/sums/`. Everything below marked PROVEN was counted from those bytes this session.

---

### 1. §8\'s factual error: catches/stumpings are NOT hardcoded to 0 in the app

§8 says all four of `bowlLbwBowled`/`catches`/`stumpings`/`runOuts` are `0`. Against the current code that is stale — `/Users/nishant-singodia/wwc-draft/lib/espn.ts:536-537` already reads them:

```ts
catches: get("caught"),
stumpings: get("stumped"),
```

Only **two** are zero: `lib/espn.ts:535` (`bowlLbwBowled: 0`) and `:538` (`runOuts: 0`). **PROVEN** the `caught`/`stumped` stats are real and populated: across the 24 matches, `sum(caught)=207`, `sum(stumped)=5`, and `stumped` matches the `st` dismissal cards **5/5 exact**. §8\'s recommendation was sized off a gap ~2× larger than the real one.

### 2. site.api.espn.com supplies all four, in the payload the app ALREADY fetches — PROVEN

`summary` → `rosters[].roster[].linescores[].statistics.batting.outDetails` (the block `wc_fps_to_csv.py:879-914` already mines for run-outs). Schema counted over 481 dismissal records:

| card | n | `bowler.id` present | `fielders[]` w/ `athlete.id` |
|---|---|---|---|
| `c` | 214 | **214/214** | **214/214** (1 fielder) |
| `bowled` | 56 | **56/56** | 0 (n/a) |
| `lbw` | 32 | **32/32** | 0 (n/a) |
| `st` | 5 | **5/5** | **5/5** |
| `run out` | 19 | 0 (n/a) | **19/19** — 10 with len==1, 9 with len==2 |
| `not out` / `retired*` | 82 | n/a | n/a |

So: lbw/bowled credited to the **bowler by athlete id**; catches/stumpings to the **fielder by athlete id**; run-outs with the direct-vs-assisted split from `len(fielders)`. Cricbuzz\'s `wicketCode`+`fielderId1..3` recipe is field-for-field the same information, except ESPN\'s ids **are already `ci:` ids** (`athlete.id` = cricinfo id) so the join is free.

**Zero extra HTTP requests.** `fetchLiveMatchPointsInner` already has this object in hand at `lib/espn.ts:467`.

**Points the app\'s live path is dropping today (24 matches, PROVEN):**

| gap | events | pts | per match |
|---|---|---|---|
| lbw/bowled bonus (`espn.ts:535`) | 88 | 704 | **29.3** |
| run-outs (`espn.ts:538`) | 19 (10 direct + 9 assisted) | 228 | **9.5** |
| caught-and-bowled catches | 7–9 | ~56 | **~2.3** |
| direct-RO uplift (`d11-score.ts:67-74` pays every RO at the assisted rate of 6) | 10 | 120 | **5.0** |

**≈41 provisional FP per match**, concentrated: a bowler with three bowled/lbw is 24 pts light; a direct-run-out fielder is 12 pts light. That is H2H-flipping on a single pick.

The caught-and-bowled item is a genuine new finding, not a duplicate: the `caught` stat total is **207 vs 214 `c` cards**, and **9** of those cards have `fielders[0].athlete.id == bowler.id`. ESPN\'s `caught` stat omits the bowler-as-catcher — the same class of bug the bot already handles for cricapi at `wc_fps_to_csv.py:1064`. **Fix by construction:** derive all four from `outDetails` and stop reading `get("caught")`/`get("stumped")` entirely — one self-consistent derivation, discrepancy gone.

**Free bonus in the same read:** `battingPosition` is present in the flattened stats (n=564, sum=3150) — the app\'s E1 equivalent (`PlayerLine.order`, `espn.ts:486`) is a one-liner, `get("battingPosition")`.

**Effort:** 2–3h. `outDetails` credits a *different* roster entry than the one carrying it, so it needs a pre-pass building `Map<athleteId, {lbwb, catches, st, ro, dro}>` before the scoring loop at `espn.ts:501-548`, plus `directRunOuts` added to `Perf` (`d11-score.ts:22-38`) and to `fielding()` (`:67-74`). One decision to make: a substitute fielder can appear in `outDetails.fielders` without a `starter||subbedIn` roster row (`espn.ts:512`) — credit by id anyway, or skip. Add the §4c-style self-check for free: assert derived catches+stumpings == `caught`+`stumped`+c&b per match, log on mismatch.

### 3. Verdicts

**(a) Bot L1 — NO.** Concur with §8, but its reasoning undersells it. Cricbuzz adds **zero fields** ESPN lacks, and is strictly *worse* on the one field that is single-sourced: its bowler `dots` is a dead-zero column, derivable only from commentary at **11/12 reconcile** (§4b), while ESPN\'s dots are exact vs cricsheet. The only honest pro-Cricbuzz argument is "independent third opinion for E9" (ESPN header wickets disagreeing with ESPN\'s own ball-by-ball by ±1, 30 pts). Reject it anyway: E9 is an *intra-ESPN* inconsistency, diagnosable internally by comparing the summary `wickets` stat sum (sum=307 over 24 matches, present per-bowler) against the playbyplay-derived count — a ~1h assertion, not a new provider. cricsheet at L2 remains the arbiter.

**(b) App live path — NO. Port the bot\'s `outDetails` read instead.** This is the whole of §8\'s case and it dissolves: ESPN closes 100% of the gap, from a payload already fetched, keyed on ids we already speak, with no new host, no new UA regime, no unversioned-RSC-payload risk, no ToS exposure.

**(c) Completion-state signal — NO.** ESPN carries the equivalent of Cricbuzz\'s `state:"Complete"`/`complete:true` in the same summary: `header.competitions[0].status.type` = `{id:"1", description:"Result", detail:"Final", state:"post"}` — **PROVEN present on 24/24**. That directly replaces the time-based rule at `wc_fps_to_csv.py:1847` (`OVER_HRS = 12 if ODI else 8`) / `:1857-1863` (`is_over`), which degrades to pure wall-clock once cricapi\'s `matchEnded` leaves. Two caveats, stated honestly: (i) all 24 samples are completed matches, so the `pre`/`in` vocabulary is **SUSPECTED** (ESPN\'s standard scoreboard states) and must be observed on a live match before E8 flips; (ii) a rain abandon also lands in `post`, so the rule must be `state=="post"` **AND** an evidence check, with `description` (`"Result"` vs No Result/Abandoned) captured — never `post` alone. Effort ~2h including the 7-day cricsheet cutoff.

### 4. The bridge cost, weighed honestly

Using Cricbuzz anywhere requires `cricbuzz_id ↔ ci:` in the shared registry against ~18k cricinfo ids. §7 already proved the payload can\'t bootstrap it: `fielderId` was **0/2 resolvable** — a slip fielder who hasn\'t batted or bowled is a bare integer with no name anywhere in the response. So the bridge means a per-team, per-tour squad scraper joined on **names**, i.e. re-entering the exact failure mode that corrupted 20 live rows. Call it 3–4 days to build, then permanent per-tour maintenance, on two undocumented unversioned endpoints that owe us nothing — to buy fields ESPN already gives us for free on ids that are already our primary key.

**Recommendation: drop Cricbuzz from the plan entirely.** Keep DATA_SOURCE_EVAL_20260813.md as the record of *why* (it is a good negative result and stops this being re-researched), but amend §8 — and file the ESPN `outDetails` port as the item that actually closes the live-path gap.', '# E8 — COMPLETION + CUTOFF RULE (implementable spec)

All line refs `/Users/nishant-singodia/wwc-points-bot/wc_fps_to_csv.py` unless stated. **PROVEN** = read in source or measured this session against the 66 cached ESPN `summary` payloads in `/tmp/wc_api_cache` + 75 cached scoreboard events.

---

## 0. WHAT ACTUALLY BREAKS WHEN CRICAPI LEAVES (PROVEN, and worse than "8h is sloppy")

`is_over` at `:1857-1863`:
```python
def is_over(m):
    if m.get("matchEnded"): return True
    if not m.get("matchStarted"): return False
    h = hours_since_start(m)
    return h is not None and h >= OVER_HRS
```
**Both** branches read cricapi-only keys. `matchEnded` and `matchStarted` do not exist on an ESPN-derived match record. So `is_over` → `False` for every match, and `live` at `:1868-1869` also requires `m.get("matchStarted")` → also `False`. `ended = []`, `live = []`, `to_score = []` at `:1885`.

**Failure: total blackout — zero rows written for the tour, every contest reads 0 pts for every player.** Not a rounding error. This is the single highest-severity item in the whole ESPN-only migration and it is currently listed nowhere as a blocker (B1–B4 + R1 don\'t cover it). Effort to notice in prod: one 4-hourly run.

Second, live today and independent of the migration: the 8h clock is a **promoter**. A match that resumes after a long rain break crosses `start+8h` while still in play → it lands in `ended` (`:1864`), gets scored off a partial ESPN card, `is_live` is False so `:2161` does not force LIVE, and `record_settlement` at `:2295` freezes that partial card **write-once, forever**. Size: unbounded — up to a full innings of a player\'s points. SUSPECTED (not yet observed in the archive), structurally reachable, and the new predicate\'s part C is precisely what blocks it.

---

## 1. THE PREDICATE — what declares a match COMPLETE

Four assertions, conjunctive, all evidence. **Time may only ever raise an alarm; it may never advance a state.**

### Measured basis (new this session, 66 cached `summary` payloads)

| signal | measurement | verdict |
|---|---|---|
| `header.competitions[0].status.type.state` | `post` on 66/66 finished; scoreboard cache shows `pre` (1) and `post` (74) | **USE — the primary flag** |
| `status.type.completed` | **`None` — the key does not exist** in ESPN\'s cricket payload | **TRAP. `t.get("completed")` is falsy on a finished match** |
| `status.type.description` | `Result` ×65, `No result` ×1; scoreboard also `Abandoned` ×1, `Scheduled` ×1 | USE for the abandoned branch |
| innings `description` | `complete` 140, `target reached` 72, `all out` 50 — **zero other values** | **This is why "== \'complete\'" read False on 4/5. It is a 3-value vocabulary, not a boolean** |
| `competitors[].winner` | the **string** `\'true\'` / `\'false\'`, not a bool | **TRAP. `c.get("winner") is True` is False on every match** |
| `endDate` | ev 1521231 = `2026-07-23T23:59Z` for a **21 Jul** match | **NOT a completion timestamp — it is the scheduled final-day boundary. Off by up to 2 days. Do not anchor the cutoff on it** |
| `notes[type==\'ballsperover\']` | `\'5\'` on Hundred events | `overs 19.4` = **99** balls, not 118 |
| batting linescore rows/match | 2 ×65, **1 ×1** (the No-result event) | abandoned path must tolerate 0–1 innings |

### The code — goes next to `espn_xi:916` / `espn_runouts:879`

Same `summary` payload, same `espn_get` cache key (`:797-801`) → **zero additional network requests.**

```python
# ── E8: evidence-based completion ───────────────────────────────────────────
TERMINAL_INNINGS = {"complete", "all out", "target reached"}   # measured: the FULL vocabulary
NO_RESULT_DESCS  = {"no result", "abandoned", "cancelled", "canceled"}

def _balls_per_over(summary):
    for n in summary.get("notes") or []:
        if (n.get("type") or "").lower() == "ballsperover":
            try:    return int(str(n.get("text") or "").strip())
            except ValueError: pass
    return 6                       # Hundred summaries carry \'5\' (PROVEN ev 1521231/1521232)

def _overs_to_balls(ov, bpo):
    try:    ov = float(ov)
    except (TypeError, ValueError): return None
    whole = int(ov)
    return whole * bpo + int(round((ov - whole) * 10))

def espn_match_state(event_id, parsed_perf, fresh=False):
    """(\'COMPLETE\'|\'IN_PLAY\'|\'NOT_CLOSED\'|\'ABANDONED\', reason). Evidence only, never the clock."""
    d  = espn_get("summary", cache=not fresh, event=event_id)
    hc = ((d.get("header") or {}).get("competitions") or [{}])[0]
    t  = ((hc.get("status") or {}).get("type") or {})
    if not d or not t:
        return "NOT_CLOSED", "espn summary unavailable"
    state = (t.get("state") or "").lower()
    desc  = (t.get("description") or "").lower()
    if state != "post":                       # A. ESPN has not called it
        return "IN_PLAY", f"espn state={state or \'?\'}"
    if desc in NO_RESULT_DESCS:               # terminal, but no cricket to score
        return "ABANDONED", t.get("description") or "no result"

    # B. every innings reached a terminal description. Filter isBatting (the non-striking side
    #    mirrors the same description with runs=0) and period<=2 (parse_espn drops super overs).
    inns = [l for c in (hc.get("competitors") or []) for l in (c.get("linescores") or [])
            if l.get("isBatting") and (l.get("period") or 0) <= 2]
    if not inns:
        return "NOT_CLOSED", "no batting linescore"
    for l in inns:
        if (l.get("description") or "").lower() not in TERMINAL_INNINGS:
            return "IN_PLAY", f"innings {l.get(\'period\')} = {l.get(\'description\')!r}"

    # C. ARITHMETIC CLOSURE — our parse must reconcile to ESPN\'s OWN header totals.
    #    `wickets` is DELIBERATELY EXCLUDED — see E9 below.
    bpo   = _balls_per_over(d)
    want4 = sum(int(l.get("fours") or 0) for l in inns)
    want6 = sum(int(l.get("sixes") or 0) for l in inns)
    wantb = sum(_overs_to_balls(l.get("overs"), bpo) or 0 for l in inns)
    got4  = sum(int(p.get("4s")    or 0) for p in parsed_perf.values())
    got6  = sum(int(p.get("6s")    or 0) for p in parsed_perf.values())
    gotb  = sum(int(p.get("balls") or 0) for p in parsed_perf.values())   # legal balls BOWLED
    if (got4, got6, gotb) != (want4, want6, wantb):
        return "NOT_CLOSED", (f"parse≠header: 4s {got4}/{want4} 6s {got6}/{want6} "
                              f"balls {gotb}/{wantb}")
    return "COMPLETE", ""
```

**Why C is the load-bearing assertion, not A.** A alone is one vendor flag replacing another vendor flag — exactly the mistake `matchEnded` was. C says *our numbers close against ESPN\'s own independently-computed header*, which is the same class of guard as the existing `len(items) < count` refusal at `:969-973`. It is what makes a truncated page, a mid-innings snapshot, or a resumed-after-8h match fail to complete instead of settling wrong.

### Why `wickets` is excluded — this is measured, not laziness

E9: header wickets vs ESPN\'s own ball-by-ball differ by 1 in **both** directions (ev1537331 hdr 16 / pbp 15; ev1537334 hdr 15 / pbp 16). Adding `wickets` to the closure check would have left both matches permanently `NOT_CLOSED` — **2 of 66 = 3% of all matches hang forever, and neither would ever settle.** Containment instead:

- completion proceeds;
- emit a **match-scope** Recon row (`param="WKT_COUNT"`, scope `match` — `apply_recon_overrides` already supports match-level seeds) so nothing goes unconsumed per the locked model;
- `Recon Flag` = `⚠ header/pbp wicket count differs by N`.
- Sizing: a wicket is 30 FP, so an unadjudicated one is 30 FP on one bowler plus any haul-tier knock-on. E9 itself stays UNDIAGNOSED — this is containment, not the fix.

---

## 2. WHERE IT GOES + WHEN WE STOP POLLING ESPN

### The ledger — `registry/match_states.json`, alongside `frozen_tours.json` (`:2473`)

```
{match_key: {"state": ..., "completed_at": iso, "cs_settled_at": iso, "waived_at": iso,
             "event": "1521231", "first_seen": iso}}
```
**Monotonic.** `RANK = {"IN_PLAY":0, "NOT_CLOSED":0, "COMPLETE":1, "ABANDONED":1, "CS_SETTLED":2, "L2_WAIVED":2}` — never write a lower rank. This enforces "COMPLETED never returns to LIVE" at the *evidence* layer, where `already_completed` (`:2141`, derived from `SETTLEMENTS`) enforces it at the status layer. Both are needed: ESPN legitimately flips `state` back to `in` for a super over or a DLS correction. CI commits the file like `recon_overrides.json`.

### Rewiring — 4 sites

| site | today | becomes |
|---|---|---|
| `:1857-1863` `is_over` | cricapi flags + 8h/12h clock | **delete.** `OVER_HRS:1847`, `hours_since_start:1848-1856` demoted to the alarm in §5 |
| `:1864-1869` `ended` / `live` | `is_over` / `matchStarted && !is_over && near_today` | `ended` = ledger rank ≥ 1; `live` = ESPN `state == "in"` **or** ledger rank 0 with a scorecard present. `near_today` deleted — the ±1-day window is a clock too, and it is what made an unflagged finished match *vanish* |
| `:1925` `espn_fresh = not cs_path` | refetches ESPN **fresh on every 4-hourly run, forever**, for any completed match cricsheet hasn\'t posted | `espn_fresh = LEDGER.get(mk, {}).get("state") not in ("COMPLETE","ABANDONED","CS_SETTLED","L2_WAIVED")` — **this one line is "stop polling ESPN".** It also closes a live hole: today a late ESPN roster/stat edit re-enters a settled match\'s scoring path days after money moved |
| `:1896` `fetch_fresh` | `is_live or not m.get("matchEnded")` | drops with cricapi |

Chicken-and-egg is fine: while the ledger is rank 0 we fetch fresh and evaluate; the run that flips it to COMPLETE is the last fresh fetch; every later run reads cache.

### Poll ladder by state

- **pre** — 5-min tick (`live-lineup.yml`, `*/5`) only inside `in_toss_window()` (`:2492`). Unchanged.
- **in / NOT_CLOSED** — every tick + every 4-hourly full run, `fresh=True`.
- **COMPLETE / ABANDONED** — **ESPN is never fetched fresh again.** The 4-hourly run (`wwc-points.yml`, `0 */4`) re-checks only the cricsheet index (`:1870`). The 5-min tick skips the match entirely.
- **CS_SETTLED / L2_WAIVED** — nothing fetched at all.
- **Tour freeze** `:2402-2407` currently demands `n_cs == len(ended)`, so a tour where cricsheet never posts one match **can never freeze**. Change to: every fmt match\'s ledger state ∈ `{CS_SETTLED, L2_WAIVED, ABANDONED}`. The cutoff in §3 is what makes that reachable.

---

## 3. THE "CRICSHEET NEVER ARRIVED" CUTOFF

```python
CS_CUTOFF_DAYS = 7      # from OUR completed_at stamp, never from ESPN endDate (measured: endDate
                        # is the scheduled final-day boundary, off by up to 2 days)
```

`completed_at` = the UTC timestamp of the first run whose predicate returned `COMPLETE`. Our own stamp, written once into the ledger, auditable.

At `now - completed_at > 7d` and still no cricsheet card:

- **Recon State**: `L1_DONE` → new terminal **`L2_WAIVED`**, label `"✅ settled — official card never posted"`. Add to `RECON_STATE_LABEL:1627-1628`, return it from `classify_recon_state:1608` (new `waived` arg, checked after the `if not cs_path` branch), and add the union member + parse in the app at `lib/points.ts:226-230` / `:288`.
- **Match Status**: **unchanged — still `COMPLETED`.** The cutoff touches only the recon axis.
- **Points**: **unchanged, and this is the whole point.** Base points froze at L1_DONE. The waiver moves zero rupees; it only stops the bot waiting. If it ever moved a number, the design is wrong.
- **Tour**: now freezable.

**Late cricsheet after a waiver** — the waiver is a *stop-waiting* flag, not a lock. Still run L2 against the frozen baseline; state becomes `L2_LATE`; any diff files an **advisory** Recon row. It cannot auto-rewrite settled points — write-once at `record_settlement:2772` already guarantees that, and the CLAUDE.md rule "READ the baseline from the frozen record, never recompute" keeps the comparison honest.

**7 days is a policy number, not a measured one.** State it as such and put it in one constant. cricsheet\'s observed lag is 1–5 days (CLAUDE.md), so 7 is ~1.4× the worst observed; if the archive shows a 9-day tail, move the constant, not the code.

---

## 4. INTERACTION WITH THE WRITE-ONCE BASELINE + THE TWO-AXIS MODEL

**(a) The predicate is an INPUT to `is_live`, not a replacement for the status.** This is the layering that must not be collapsed:

```
espn_match_state()  →  is the cricket over?        (evidence about the world)
classify_match_status()  →  may we publish it?     (evidence about our data)
classify_recon_state()   →  how verified is it?    (the second axis)
```
A match can be `COMPLETE` on the field and still held at `LIVE` by `unsourced` / `unresolved` at `:1656-1669`. That is correct and must stay — "nothing goes unconsumed" outranks "the cricket finished". The only change at `:2161-2163` is what feeds `is_live`.

**(b) The freeze point is wrong today — 1-line fix, 2 sites.** CLAUDE.md locks the freeze at the **L1-done transition**. The code fires at `:2295` and `:2306` on:
```python
if match_status in ("COMPLETED", "COMPLETED_FLAGGED"):
```
`COMPLETED_FLAGGED` is also returned for `id_break` (`:1653`), single-feed (`:1663`, `:1665`), and — once `already_completed` — for `unsourced` (`:1660`) and `unresolved` (`:1669`). Several of those are `L1_OPEN`, i.e. **base points are being frozen while L1 is still open**, which is exactly the class of bug the locked model exists to kill. Change both call sites to:
```python
if recon_state != "L1_OPEN":
```
`recon_state` is already computed two lines earlier at `:2147`. PROVEN by reading `classify_match_status:1630-1670` against `classify_recon_state:1608-1625`.

**(c) Two ratchets, deliberately.** `SETTLEMENTS` (`:2141`) answers "did this ever publish?"; the ledger answers "did the cricket ever finish?". They can disagree legitimately — a match `COMPLETE` on the field but never published because a player has no dot-ball source. Keeping them separate is what stops the `COMPLETED_FLAGGED`-means-four-things regression from reappearing in a new column.

**(d) App side.** `lib/points.ts:224` `MatchStatus` union and `:288` parser need `L2_WAIVED` on the recon axis only; `MatchStatus` itself is unchanged. `MATCH_DATE_GUARD_MS:478` (36h) and the future-start gate stay — they are display guards on a different axis and this change does not touch them.

---

## 5. THE CLOCK\'S ONLY REMAINING JOB — the stuck alarm

A match ESPN never closes (dead event id, R1 name miss, WAF) would sit `NOT_CLOSED` forever, silently. So keep `hours_since_start` (`:1848-1856`) purely as an alarm:

```python
if ledger_rank(mk) == 0 and hours_since_start(m) > 48:
    print(f"  ⛔ STUCK: {label} — {hrs:.0f}h past start, still {state} ({reason})", file=sys.stderr)
    TOUR_REVIEW.append(...)   # TOUR INGEST REVIEW tab
```
It stays `LIVE` (no silent zeros — the locked rule) and it screams. **It never promotes.** Losing that distinction is how we got here.

---

## 6. EFFORT

| work | lines | est |
|---|---|---|
| `espn_match_state` + `_balls_per_over` + `_overs_to_balls` | ~55 | 2h |
| ledger: load / monotonic advance / save / CI commit | ~40 | 1.5h |
| rewire `ended`/`live`/`espn_fresh`/tour-freeze; delete `is_over`+`near_today` | ~20 net (mostly deletions) | 1h |
| cutoff + `L2_WAIVED` + `L2_LATE` + labels + `lib/points.ts` union | ~25 bot / ~10 app | 1.5h |
| freeze-point fix at `:2295`/`:2306` | 2 | 15m |
| E9 containment: match-scope `WKT_COUNT` recon row | ~15 | 45m |
| stuck alarm | ~8 | 30m |
| tests — fixtures already exist: 65 Result + 1 No-result + 1 Abandoned + 1 Scheduled in `/tmp/wc_api_cache`; assert 65 COMPLETE, 1 ABANDONED, 1 IN_PLAY, plus a hand-truncated payload → NOT_CLOSED, plus a HUN bpo=5 overs case | ~120 | 3h |

**~1.5 days.** Ship it **before** the cricapi removal, not with it — the predicate is testable today against the cached corpus while `matchEnded` is still there to disagree with, and every disagreement is free evidence.', '## E9 — DIAGNOSED. Root cause found. ESPN\'s header is CORRECT in both matches; **our derived count is wrong in both directions**, from a single mechanism.

### Verdict
There is **no wicket-level data defect in ESPN and no missing or extra dismissal**. All four hypothesised suspects (retired hurt/out, obstructing the field, hit wicket, run-out also credited to a bowler, dismissal off a no-ball) are **ruled out** — see census below. The ±1 is an artifact of how "ours" is defined: `Σ bowler wickets + Σ run-out FIELDER CREDITS`, where the second term counts *fielders*, not *dismissals*.

### Evidence (three independent sources, all agreeing with the header)
| source | ev1537331 | ev1537334 |
|---|---|---|
| ESPN header `linescores[].wickets` (isBatting rows) | 6 + 10 = **16** | 10 + 5 = **15** |
| cricsheet ground truth | **16** | **15** |
| ESPN playbyplay items with `dismissal.dismissal` | **16** | **15** |
| "ours" (`Σ w` + `Σ runouts`) | 15 | 16 |

Dismissal-kind census from `summary` outDetails — ev1537331 `{c:14, bowled:1, run out:1, not out:2}` (18 = 22 batters − 4 unused); ev1537334 `{c:10, bowled:2, st:1, lbw:1, run out:1, not out:3}`. No retired, no hit wicket, no obstructing, no bowler credited a run out, no dismissal off a no-ball. Ball-by-ball is complete — 16 and 15 dismissal items, exactly matching truth.

### The exact arithmetic
`/Users/nishant-singodia/wwc-points-bot/wc_fps_to_csv.py:1055-1060` correctly excludes the run out from the bowler\'s `w`, so `Σ w` = items − run-outs → **15** and **14**. Then `:1077-1086` does `rp["runouts"] += 1` **once per fielder**:

- **ev1537334 → 14 + 2 = 16 (over by 1).** The single run out is David Wiese, `run out (Sahibzada Farhan/†Dickwella)` — **two** fielders. The assist is double-counted as a wicket. (cricsheet: inn2 14.3, `M Theekshana` bowling, `extras {legbyes:1}`, fielders `[\'Sahibzada Farhan\',\'N Dickwella\']`.)
- **ev1537331 → 15 + 0 = 15 (under by 1).** The single run out is C Wickramasinghe, `run out (sub [Virandeep Singh])` — **one** fielder, but a **substitute** who appears in neither ESPN roster, so his row never survives to the emitted rows. (cricsheet: inn2 16.2, `Akif Javed` bowling, `fielders:[{"name":"Virandeep Singh","substitute":true}]`, and `info.players` lists him in **neither** team.)

One mechanism, both directions. `PROVEN`.

---

### Three real defects this surfaced

**D1 — substitute-fielder run-out credit is lost. REAL MONEY, PROVEN, 12 FP.**
LPL Match 1 (2026-07-17 JK v GG). Both feeds credit it: ESPN `outDetails.fielders = [{Virandeep Singh Jagjit Singh, id 633660}]`; cricsheet `{"name":"Virandeep Singh","substitute":true}`. Unassisted → `dro=1` → **12 pts** (`wc_fps_to_csv.py:89` `dro=12`, applied at `:1170`). He is a fully registered, draftable player — `/Users/nishant-singodia/wwc-points-bot/registry/players.json`: `cricinfo_id/espn_id 633660, cricsheet_id 415d30b6, alias "virandeep singh", draft_id 10357, auction_id 1984`. Yet `/Users/nishant-singodia/wwc-points-bot/registry/settlement_snapshots.json` has him **frozen at `points: 0`**, `COMPLETED_FLAGGED`, `source "cricsheet · official"`. Two independent causes drop him:
- ESPN path: `espn_runouts` returns fullName `"Virandeep Singh Jagjit Singh"` (`:906-911`); `parse_espn` keys the row by that name (`get(nm)`, `:1082`) — `norm()` of it ≠ alias `"virandeep singh"`, and the `espn_id` it carries is discarded downstream. **This is E7 → B3 silent-drop, caught in the act.**
- cricsheet path (`:778-781`): `get(fi["name"])` gets the right name, but he is in neither team\'s `info.players`, so the row has no team and dies at the squad join.

*Decide the rule first:* does a sub fielder who never appears in the XI score at all in this app? If **yes** → fix via E7 (resolve fielder rows on `espn_id`/`cricsheet_id` before name) plus team-assign a sub from the fielding side of the innings: **2–3h with a test**. If **no** → suppress the credit deliberately and keep it out of the wicket count: **~30 min**. Today\'s behaviour is an accident either way.

**D2 — the reconciliation metric itself is wrong. PROVEN, ~20 min.**
`Σ w + Σ runouts` inflates by `(#fielders − 1)` on every assisted run out. Per-fielder credit is *correct scoring* (6 each, `ro=6`) — it is only wrong as a wicket count. Worse, it **masks** real misses: a genuinely missing wicket in a match that also has a 2-man run out nets to zero. Change the check to `len(pbp items with dismissal.dismissal)`, then **re-run the 24-event LPL sweep** — the current "2 of 24 mismatch" figure is not trustworthy in either direction.

**D3 — E2 confirmed on real data, with a duck-penalty exposure. PROVEN, 1–2h.**
ev1537331 item id `216030`, over 16.3, `shortText "Akif Javed to Wiese, OUT"`, `type "run out"`, `batsman = David Wiese` — but cricsheet\'s `player_out` is **C Wickramasinghe** (the non-striker). `wc_fps_to_csv.py:1051` stamps `dismissed`/`dismissal` on the wrong player. Harmless here (Wiese was genuinely out at 18.4; Wickramasinghe made 24 off 10). But the duck rule at `:1150-1152` is explicitly written to fire on *a non-striker run out for 0* — precisely the case E2 gets wrong, so the −2 lands on the striker instead. Exposure **±4 FP per occurrence**. Fix: take the victim from `summary` outDetails, which is keyed on the dismissed player\'s own roster row, in the same `espn_runouts` pass.

### Two traps to note while fixing
- `header.competitions[].competitors[].linescores[]` carries **two** entries per competitor — the team\'s own innings plus a `0/0` mirror of the opponent\'s. Any header check must filter `isBatting: true` or it double-counts/reads zeros. Not currently a bug.
- `espn_runouts` appends only `if fl:` (`wc_fps_to_csv.py:~912`) — a run out with **no** listed fielder is dropped silently, costing both the 6/12 pts and the wicket. Not observed in these two matches; `SUSPECTED`. Add a loud warn, ~15 min.

### Key file:line refs
`/Users/nishant-singodia/wwc-points-bot/wc_fps_to_csv.py` — `:879-915` espn_runouts (`:906-911` fullName, `:~912` `if fl:` guard) · `:1047-1060` dismissal stamp + bowler exclusion (`:1051` = E2) · `:1077-1086` per-fielder runouts loop (**the ±1**) · `:761-781` cricsheet path · `:89,97,106` `dro=12, ro=6` · `:1150-1152` duck · `:1170` fielding points.
Fetched payloads for re-checking: `/private/tmp/claude-501/-Users-nishant-singodia/f36700e2-70e2-4403-9da5-40a03f07ecbc/scratchpad/e9_sum_1537331.json`, `.../e9_sum_1537334.json` (ESPN series id for LPL 2026 is **1537330**, not 1537 — a wrong series path 403s).']}, 'workflowProgress': [{'type': 'workflow_phase', 'index': 1, 'title': 'Gather'}, {'type': 'workflow_phase', 'index': 2, 'title': 'Verify'}, {'type': 'workflow_phase', 'index': 3, 'title': 'Plan'}, {'type': 'workflow_agent', 'index': 1, 'label': 'sweep:bot-open', 'phaseIndex': 1, 'phaseTitle': 'Gather', 'agentId': 'a68746dbacc57baaf', 'model': 'claude-opus-5[1m]', 'state': 'done', 'startedAt': 1786599825563, 'queuedAt': 1786599825514, 'attempt': 1, 'lastToolName': 'Bash', 'lastToolSummary': 'cd /Users/nishant-singodia/wwc-points-bot && echo "=== para…', 'promptPreview': 'CONTEXT. Owner settles REAL MONEY. He asked for "a proper plan which doesn\'t let anything slip".
Repos: BOT=/Users/nishant-singodia/wwc-points-bot (wc_fps_to_csv.py = scorer), APP=/Users/nishant-singodia/wwc-draft (Next.js draft app).

READ THESE FIRST — they contain most of the open items already:
  /Users/nishant-singodia/wwc-points-bot/CLAUDE.md            (locked recon model, 7 Aug)
  /Users/n…', 'lastProgressAt': 1786600221823, 'tokens': 109018, 'toolCalls': 22, 'durationMs': 396260, 'resultPreview': '## BOT REPO SWEEP — deduplicated open items

Verified against HEAD (`5d0e6f2`, clean tree bar 3 untracked files). `224 passed in 1.13s`. All line refs `/Users/nishant-singodia/wwc-points-bot/wc_fps_to_csv.py` unless stated.

---

## PART A — FALSE OPEN. Already fixed in HEAD; do not put these in the plan

These are still written as open in the docs the owner was told to read. Each one I read in HE…'}, {'type': 'workflow_agent', 'index': 2, 'label': 'sweep:app-open', 'phaseIndex': 1, 'phaseTitle': 'Gather', 'agentId': 'af0cf7ea73d439212', 'model': 'claude-opus-5[1m]', 'state': 'done', 'startedAt': 1786599825564, 'queuedAt': 1786599825514, 'attempt': 1, 'lastToolName': 'Bash', 'lastToolSummary': 'sed -n \'98,115p\' "app/draft/[code]/results/page.tsx"; echo …', 'promptPreview': 'CONTEXT. Owner settles REAL MONEY. He asked for "a proper plan which doesn\'t let anything slip".
Repos: BOT=/Users/nishant-singodia/wwc-points-bot (wc_fps_to_csv.py = scorer), APP=/Users/nishant-singodia/wwc-draft (Next.js draft app).

READ THESE FIRST — they contain most of the open items already:
  /Users/nishant-singodia/wwc-points-bot/CLAUDE.md            (locked recon model, 7 Aug)
  /Users/n…', 'lastProgressAt': 1786600184139, 'tokens': 72976, 'toolCalls': 27, 'durationMs': 358575, 'resultPreview': '## DRAFT APP (`/Users/nishant-singodia/wwc-draft`) — verified against HEAD `cd5ea4c`

**Test runs (all three, this HEAD):**
- `npm test` — **PASS.** 5 suites green (effective-lineup, points, d11-score, settlement-audit 28/28, points-tab-guard 6/6).
- `npm run test:integration` — **PASS.** Fresh `db/test.db` migrates clean, 8/8 (effective-XI freeze round-trip).
- `npm run check:tours` — **PASS (exi…'}, {'type': 'workflow_agent', 'index': 3, 'label': 'decide:cricbuzz', 'phaseIndex': 1, 'phaseTitle': 'Gather', 'agentId': 'a9bdf5f21745109a3', 'model': 'claude-opus-5[1m]', 'state': 'done', 'startedAt': 1786599825565, 'queuedAt': 1786599825514, 'attempt': 1, 'lastToolName': 'Bash', 'lastToolSummary': 'cd /private/tmp/claude-501/-Users-nishant-singodia/f36700e2…', 'promptPreview': 'CONTEXT. Owner settles REAL MONEY. He asked for "a proper plan which doesn\'t let anything slip".
Repos: BOT=/Users/nishant-singodia/wwc-points-bot (wc_fps_to_csv.py = scorer), APP=/Users/nishant-singodia/wwc-draft (Next.js draft app).

READ THESE FIRST — they contain most of the open items already:
  /Users/nishant-singodia/wwc-points-bot/CLAUDE.md            (locked recon model, 7 Aug)
  /Users/n…', 'lastProgressAt': 1786600143963, 'tokens': 81045, 'toolCalls': 23, 'durationMs': 318398, 'resultPreview': '## VERDICT: Cricbuzz is not needed anywhere. The premise in DATA_SOURCE_EVAL_20260813.md §8 is wrong on two counts.

**Measurement set:** 24 cached ESPN `summary` payloads (LPL T20, events 1537331–1537354) at `/private/tmp/claude-501/-Users-nishant-singodia/f36700e2-70e2-4403-9da5-40a03f07ecbc/scratchpad/sums/`. Everything below marked PROVEN was counted from those bytes this session.

---

### 1.…'}, {'type': 'workflow_agent', 'index': 4, 'label': 'design:completion', 'phaseIndex': 1, 'phaseTitle': 'Gather', 'agentId': 'a5effb57a14b3a91a', 'model': 'claude-opus-5[1m]', 'state': 'done', 'startedAt': 1786599825565, 'queuedAt': 1786599825514, 'attempt': 1, 'lastToolName': 'Bash', 'lastToolSummary': 'grep -rn "COMPLETED\\|Match Status\\|36" lib/points.ts | head…', 'promptPreview': 'CONTEXT. Owner settles REAL MONEY. He asked for "a proper plan which doesn\'t let anything slip".
Repos: BOT=/Users/nishant-singodia/wwc-points-bot (wc_fps_to_csv.py = scorer), APP=/Users/nishant-singodia/wwc-draft (Next.js draft app).

READ THESE FIRST — they contain most of the open items already:
  /Users/nishant-singodia/wwc-points-bot/CLAUDE.md            (locked recon model, 7 Aug)
  /Users/n…', 'lastProgressAt': 1786600169598, 'tokens': 74663, 'toolCalls': 21, 'durationMs': 344033, 'resultPreview': '# E8 — COMPLETION + CUTOFF RULE (implementable spec)

All line refs `/Users/nishant-singodia/wwc-points-bot/wc_fps_to_csv.py` unless stated. **PROVEN** = read in source or measured this session against the 66 cached ESPN `summary` payloads in `/tmp/wc_api_cache` + 75 cached scoreboard events.

---

## 0. WHAT ACTUALLY BREAKS WHEN CRICAPI LEAVES (PROVEN, and worse than "8h is sloppy")

`is_over` at…'}, {'type': 'workflow_agent', 'index': 5, 'label': 'diagnose:E9', 'phaseIndex': 1, 'phaseTitle': 'Gather', 'agentId': 'a7f79bc309bc8f084', 'model': 'claude-opus-5[1m]', 'state': 'done', 'startedAt': 1786599825565, 'queuedAt': 1786599825514, 'attempt': 1, 'lastToolName': 'Bash', 'lastToolSummary': 'cd /Users/nishant-singodia/wwc-points-bot && grep -n "dro" …', 'promptPreview': 'CONTEXT. Owner settles REAL MONEY. He asked for "a proper plan which doesn\'t let anything slip".
Repos: BOT=/Users/nishant-singodia/wwc-points-bot (wc_fps_to_csv.py = scorer), APP=/Users/nishant-singodia/wwc-draft (Next.js draft app).

READ THESE FIRST — they contain most of the open items already:
  /Users/nishant-singodia/wwc-points-bot/CLAUDE.md            (locked recon model, 7 Aug)
  /Users/n…', 'lastProgressAt': 1786600238556, 'tokens': 73412, 'toolCalls': 23, 'durationMs': 412991, 'resultPreview': "## E9 — DIAGNOSED. Root cause found. ESPN's header is CORRECT in both matches; **our derived count is wrong in both directions**, from a single mechanism.

### Verdict
There is **no wicket-level data defect in ESPN and no missing or extra dismissal**. All four hypothesised suspects (retired hurt/out, obstructing the field, hit wicket, run-out also credited to a bowler, dismissal off a no-ball) are…"}, {'type': 'workflow_agent', 'index': 6, 'label': 'verify:dedupe', 'phaseIndex': 2, 'phaseTitle': 'Verify', 'agentId': 'aba22d9ff8f4d11fa', 'model': 'claude-opus-5[1m]', 'state': 'done', 'startedAt': 1786600238565, 'queuedAt': 1786600238564, 'attempt': 1, 'lastToolName': 'Bash', 'lastToolSummary': 'cd /Users/nishant-singodia/wwc-draft && grep -rn "Match Sta…', 'promptPreview': 'CONTEXT. Owner settles REAL MONEY. He asked for "a proper plan which doesn\'t let anything slip".
Repos: BOT=/Users/nishant-singodia/wwc-points-bot (wc_fps_to_csv.py = scorer), APP=/Users/nishant-singodia/wwc-draft (Next.js draft app).

READ THESE FIRST — they contain most of the open items already:
  /Users/nishant-singodia/wwc-points-bot/CLAUDE.md            (locked recon model, 7 Aug)
  /Users/n…', 'lastProgressAt': 1786600606171, 'tokens': 64457, 'toolCalls': 13, 'durationMs': 367606, 'resultPreview': '# QC\'d MASTER LIST — verified against BOT `5d0e6f2` / APP `cd5ea4c`

Everything below I re-read in source or measured this session. Sweep claims I could not reproduce are demoted or corrected explicitly.

---

## PART 0 — CORRECTIONS TO THE SWEEPS (fix these before the owner reads them)

| Sweep claim | QC verdict |
|---|---|
| app-open: "live scorer hardcodes catches/stumpings/runOuts/lbwBowled t…'}, {'type': 'workflow_agent', 'index': 7, 'label': 'master-plan', 'phaseIndex': 3, 'phaseTitle': 'Plan', 'agentId': 'a879d430af426a972', 'model': 'claude-opus-5[1m]', 'state': 'done', 'startedAt': 1786600606181, 'queuedAt': 1786600606179, 'attempt': 1, 'lastToolName': 'Bash', 'lastToolSummary': 'cd /Users/nishant-singodia/wwc-points-bot; grep -n "tours_f…', 'promptPreview': 'CONTEXT. Owner settles REAL MONEY. He asked for "a proper plan which doesn\'t let anything slip".
Repos: BOT=/Users/nishant-singodia/wwc-points-bot (wc_fps_to_csv.py = scorer), APP=/Users/nishant-singodia/wwc-draft (Next.js draft app).

READ THESE FIRST — they contain most of the open items already:
  /Users/nishant-singodia/wwc-points-bot/CLAUDE.md            (locked recon model, 7 Aug)
  /Users/n…', 'lastProgressAt': 1786601633772, 'tokens': 72376, 'toolCalls': 20, 'durationMs': 1027591, 'resultPreview': '# THE MASTER PLAN — wwc-points-bot + wwc-draft
**Baseline: BOT `5d0e6f2` (224 tests green) · APP `cd5ea4c` (5 suites + 8 integration green). Every line ref below was read in source this session unless marked SUSPECTED.**

Read this once, top to bottom. Then work Section 6 only. Sections 1–5 are the evidence; Section 7 is what you can stop thinking about; Section 8 is what you hand to a friend.

**…'}], 'totalTokens': 547947, 'totalToolCalls': 149}