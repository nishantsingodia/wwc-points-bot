{'summary': 'Prove ESPN supplies every scoring input, then design the full cricapi removal + backfill plan', 'agentCount': 7, 'logs': [], 'result': {'plan': '# ESPN‑ONLY SCORING — THE MIGRATION PLAN

**Repo:** `/Users/nishant-singodia/wwc-points-bot` · scorer `wc_fps_to_csv.py` · app `/Users/nishant-singodia/wwc-draft`
All line refs are `wc_fps_to_csv.py` unless stated. **PROVEN** = read in source or measured against cricsheet. **UNCERTAIN** = stated as such.

---

## HEADLINE VERDICT — DO NOT FLIP TODAY

The *arithmetic* is ready: across 4 cached matches / 3 formats / 86 id‑matched rows, ESPN reproduced cricsheet **exactly on every scoring field** — 48 wickets, 18 lbw/bowled, 28 catches, 11 run‑out fielder credits, 538 dots, total points delta **0 in all four matches** (1115/1115, 860/860, 1277/1277, 1452/1452).

The *plumbing* is not. There are **4 blocking defects, all PROVEN by code, all currently masked by cricapi**. Removing cricapi promotes every one of them from dormant to live. All four fixes read from the `summary` payload that `espn_runouts` and `espn_xi` already fetch under the same cache key — **zero additional network requests**.

| # | Blocker | Site | What breaks the day cricapi leaves |
|---|---|---|---|
| **B1** | `dismissed` sourced from the wrong player | `:1049‑1052` | Ducks silently missed. ~1 wrong duck per 25 matches |
| **B2** | `bat_order` never set by ESPN at all | `parse_espn:943‑1092` | 100% blank → churns `SETTLED_FIELDS` for **every batter in every match** |
| **B3** | Silent‑drop auto‑add dies | `:2001‑2003` | A known player who plays but sits in no squad slot is dropped with **no row, no flag, no review** |
| **B4** | `espn_id` discarded at 3 sites | `:1067`, `:1069`, `:1090` | Blank Player ID → row unjoinable by the draft app; fuzzy name matching still reachable |

Plus one structural risk found this session, **R1** (§2.4): match discovery joins on **team display names**, not the series id — and with cricapi gone a name miss means the match scores *nothing at all*.

**After B1–B4 are fixed and R1 is hardened, ESPN‑only is safe.** Estimated work: ~120 lines net (mostly deletions).

---

# 1. THE SCORING LOGIC AFTER CRICAPI

Two payloads, one cache key each, per match:

- **`playbyplay`** — every delivery. Source of all batting/bowling accumulation.
- **`summary`** — rosters, XI, batting order, dismissal cards, run‑out fielders.

`score()` dispatches at `:1109` → `_score_t20:1133` / `_score_hundred:1175` / `_score_odi:1208`.

### Per field: source → computation → the guard that stops it being silently wrong

**`r` runs** — playbyplay `commentary[].scoreValue` where `playType.description ∈ {run, four, six}`, minus the no‑ball penalty (`:1040‑1043`).
*Guard:* no‑ball detection is a regex over **both** `shortText` and `text` (`:1032‑1036`), so a penalty run is never charged to the batter. **PROVEN** 362/362, 249/249, 149/149, 502/502.

**`b` balls faced** — `+1` per delivery unless wide (`:1039`). **PROVEN** 241/241, 201/201, 150/150, 594/594.

**`4s` / `6s`** — `description == "four"/"six"` (`:1044‑1047`).
*Guard:* cricsheet\'s `non_boundary` flag (overthrows that reach the rope) is **not** a boundary; ESPN is correct here and cricsheet\'s raw field is the one that misleads. Fixed in `4558579`. **PROVEN** 37/37, 15/15, 19/19, 13/13, 6/6, 40/40, 10/10.

**`balls` bowled** — legal deliveries only, same no‑ball/wide detection. **PROVEN** 240/240, 200/200, 150/150, 592/592.

**`runs_conceded`** — `scoreValue` with byes/leg‑byes stripped, no‑ball penalty **kept** (`:1053‑1056`). **PROVEN** 378/378, 257/257, 158/158, 520/520.

**`w` wickets** — every dismissal *except* run out / retired\\* / obstructing / hit wicket is the bowler\'s (`:1054‑1062`). Hit wicket credits the wicket but **not** the lbw/bowled bonus (`:1060‑1061`). **PROVEN** 48/48.

**`lbwb`** (+8) — `dismissal.type ∈ {bowled, lbw, leg before wicket}` (`:1058‑1059`).
*Guard:* ESPN spells it out as `"leg before wicket"` where cricsheet says `lbw`; **both spellings are handled**. **PROVEN** 18/18 events (5 lbw + 13 bowled).

**`dots`** — legal delivery with `bcharged == 0` (`:1057`).
*Guard:* **this is the field the pagination guard exists for.** ESPN\'s playbyplay default page is short; the dead relic `espn_dots:829` still asks for `limit=600` and would silently under‑count a 605‑ball ODI. The live path `parse_espn` requests `limit=1000` and **loops pages** (`:951`), then **refuses to score a partial or failed fetch** (`:953‑957`, commit `785dec8`) — it returns no data rather than a plausible‑looking low number. Verified on the 605‑delivery ODI ev `1538624`: 594 faced / 592 bowled, exact. A second guard dedups repeated commentary items (`:963‑976`).
*Standing exposure:* dots are **single‑sourced** — cricapi never had them, so this is unchanged by the migration. cricsheet at L2 is the only validator. **PROVEN** 0 diffs (79/79, 84/84, 81/81, 294/294).

**`maidens`** — over‑aggregation over the deduped delivery stream (`:1074‑1076`). Same mechanism cricsheet uses.
*Guard:* pagination guard again — a dropped page fabricates maidens. **CONFIRMED but THIN: only 1 maiden in the 4‑match sample.**

**`catches`** (+8, +4 at 3) — playbyplay `dismissal.fielder.athlete` for `caught`; `caught and bowled` credits the bowler (`:1063‑1067`). **PROVEN** 28/28.

**`stumpings`** (+12) — same path, `type == "stumped"` (`:1068‑1069`). **CONFIRMED but THIN: 2 events.**

**`runouts` (+6) and `dro` (+12)** — **deliberately NOT taken from playbyplay.** ESPN\'s `dismissal.fielder` is *always empty* for a run out; the code says so explicitly at `:1070`. They come from the **summary** payload via `espn_runouts:879` → `rosters[].roster[].linescores[].statistics.batting.outDetails.fielders[].athlete`. `dro` fires when `len(fielders) == 1` (`:1085‑1086`).
*Guard:* the credit is keyed on `athlete.id`, not name — this is the one path that already does identity correctly (`:1083`). **PROVEN** 11 fielder credits over 6 run‑outs, fielder **sets** exact including four 2‑man assists; plus a prior 17/17 LPL sweep. `dro` is **THIN — 1 positive** in this sample.

**`played`** (+4 XI) — summary `rosters[].roster[]` where `starter or subbedIn` (`espn_xi:916`, `:922`). **PROVEN** 24/24, 22/22, 22/22, 22/22.

**SR / econ / hauls / milestones** — pure functions of the above. **PROVEN**: points delta 0 in all 4 matches.

**`role`** — *not a feed field.* Squad registry → `ROLE_OVERRIDE` → else `guess_role:226` from the stat line. Unaffected by the migration.

---

# 2. DO WE GET ALL THE DATA FROM CRICINFO?

**Yes — every input `score()` consumes has a verified ESPN path. Nothing is unavailable.** But three things are wrong *in how we read it*, and one thing about *finding* the match is fragile.

### 2.1 Field table

| field | ESPN payload → path | verdict |
|---|---|---|
| `r`, `b`, `4s`, `6s` | playbyplay | **CONFIRMED** 0 diffs |
| `balls`, `runs_conceded`, `w`, `lbwb` | playbyplay | **CONFIRMED** 0 diffs |
| `dots` | playbyplay `bcharged==0` | **CONFIRMED** 0 diffs (single‑sourced) |
| `maidens` | playbyplay over‑aggregation | **CONFIRMED — THIN** (1) |
| `catches` | playbyplay `dismissal.fielder` | **CONFIRMED** 28/28 |
| `stumpings` | playbyplay `dismissal.fielder` | **CONFIRMED — THIN** (2) |
| `runouts` | **summary** `outDetails.fielders[]` | **CONFIRMED** 11/11 |
| `dro` | **summary**, `len(fielders)==1` | **CONFIRMED — THIN** (1) |
| `played` | **summary** `starter or subbedIn` | **CONFIRMED** 0 diffs |
| `role` | registry, not a feed | n/a |
| **`dismissed`** | **currently playbyplay — WRONG PLAYER** | **REFUTED — B1** |
| **`bat_order`** | **currently nothing — 100% absent** | **REFUTED — B2** |
| **`espn_id`** | present in payload, **discarded at 3 sites** | **REFUTED — B4** |

### 2.2 B1 — `dismissed` is attributed to the striker, not the victim (PROVEN)

`:1051` stamps the flag on the playbyplay item\'s **striker**:

```python
pb = get(bt, bt_id); pb["dismissed"] = True; pb["dismissal"] = it.get("shortText", typ)
```

When the **non‑striker** is out (run out backing up, retired out), ESPN still carries the striker in `batsman`. Raw playbyplay, ev `1521199`:

```
type=run out   batsman=Nadine de Klerk        <- cricsheet: the victim was CE Dean
type=bowled    batsman=Nadine de Klerk        <- de Klerk\'s own real dismissal
type=run out   batsman=Katie Louise George    <- cricsheet: the victim was J Groves
type=caught    batsman=Katie Louise George    <- George\'s own real dismissal
```

**The failure mode is a silent MISS, not a phantom.** The flag lands on a striker who is usually dismissed later anyway, so it is idempotently absorbed — the real victim is simply **never marked out**. In ev `1521199` both victims read `espn=False / cs=True` with zero phantoms. Any "phantom count" audit *understates* this.

Exposure over the already‑extracted local archives (508 matches, no ESPN calls):

```
LPL_T20:  141 matches, 133 run-out/retired,  66 non-striker ->  3 MISSED ducks,  3 phantom (upper bound)
HUNDRED:  367 matches, 346 run-out/retired, 143 non-striker -> 10 MISSED ducks,  4 phantom (upper bound)
```

≈ **20 wrong duck applications per 508 matches (1 per 25)**, ±2 pts (±3 ODI). Points delta was 0 in the 4‑match sample only because every mis‑flagged batter happened to have `r>0` — **luck, not correctness**. Second‑order: `:1051` also writes the display string `p["dismissal"]` to the wrong batter, and `dismissal ∈ SETTLED_FIELDS` (`:2770`) — a wrong string gets **frozen into the settlement record**.

Today this is invisible because cricapi\'s card supplies `dismissed` at L1 and `merge_espn_into:1415` never overwrites it.

**Fix (independently verified twice):** read from summary `rosters[].roster[].linescores[].statistics.batting.outDetails.dismissalCard`, keyed on `athlete.id`. That block hangs off the **dismissed batter\'s own roster entry**, so attribution cannot be wrong. Card vocabulary observed: `c`, `bowled`, `lbw`, `st`, `run out`, `retired out`, `not out`, `""` (did not bat). **0 mismatches / 86 rows / 22 dismissals / 6 run‑outs / 1 retired‑out.**

### 2.3 B2 — `bat_order` is 100% absent (PROVEN)

`parse_espn` never assigns it; only `parse_cricsheet:728` and cricapi\'s `parse_match:1283` do. Measured: **67 of 67 rows** that cricsheet gives a batting position read **0** from ESPN. `bat_order ∈ SETTLED_FIELDS:2770`, so the blank→filled transition when cricsheet lands at L2 **churns the settlement record for every batter in every match** — not an edge case.

**Fix:** summary `statistics.batting.order` (1‑based). **0 mismatches / 86 rows** across T20 + Hundred + ODI. Same payload, free.

### 2.4 R1 — match discovery is a name join, and it is now the only door (NEW, this session)

`espn_event_id:816‑827` scans the **scoreboard for the date** and matches on `team_key(...)` = team **display names** (`:675‑679`), tolerating ±1 day. It does **not** use `espn_series`, even though all 12 tours in `tours.json` carry one (`1483859`, `1537330`, `1521176`, `1521193`, …).

Today a name miss is survivable: `perf = api_perf if api_perf else espn_perf` (`:1949‑1968`) falls back to cricapi\'s card. **After the flip, a name miss means `espn_perf == {}`, the no‑data guard at `:1943‑1948` fires, and the match is `continue`d — it never appears on the sheet at all**, with only a stderr line as warning.

**UNCERTAIN — I did not measure the ESPN team‑name miss rate.** `canon_team` + gender‑qualifier stripping already handle the known Hundred M/W collision. But this is structurally a single point of failure over real money and must be hardened before the flip (§6, Step 5).

### 2.5 One thing that is genuinely dropped — and is probably correct

`espn_xi:922` sets `played=True` only for `starter or subbedIn`. `:1998` then filters `espn_perf` to `if v["played"]`. A **pure substitute fielder** (12th man) credited with a run‑out therefore gets his credit deleted. **PROVEN by code; 0 occurrences** in 17 fielder credits across 5 cached events — and a non‑XI sub is not draftable anyway, so the drop is almost certainly *correct* behaviour. Concussion subs are safe (`subbedIn`). Noting it so it is not discovered later as a surprise.

---

# 3. NEW PLAYER ADDITION

Current registry state, read from disk: **`registry/players.json` = 679 pids, 679 of them `ci:`** — zero `cs:`, zero `uncapped:`, zero missing a cricinfo id. `crosswalk.json` = 18253 `cs2ci`. `manual_ci_bridges.json` = 112. `needs_cricinfo_pending.json` = **0 pending**. The ladder is already a single rung.

### (a) A player added to a squad before a tour — WORKS, unchanged by the flip

`build_registry.build_tour:375` → `resolve_ci:316`. Cross‑tour alias reuse first (`:399‑409`), else the 5‑step cascade (`:321‑345`): manual bridge → legacy TS bridge → exact `people.csv` (country/gender‑scoped, unique‑only) → fuzzy (**null on ambiguity**) → ESPN roster `athlete.id`. Resolved → `pid = ci:<id>`; unresolved → `uncapped:<slug>`.

**You see:** stderr `build_registry.py:454` `"<tour>: N slots | reused R | NEEDS-REVIEW K | ESPN harvested E"`, a row in `registry/UNMAPPED_<slug>.txt`, and a row in the **Needs Cricinfo ID** tab.
**You do:** type the numeric id into `cricinfo_id_FILL_HERE`. That is the only sanctioned channel.
**Self‑heals:** `read_needs_cricinfo:3397` (called `:2583`) strips non‑digits (tolerates a pasted URL), runs two merge guards (`:3466‑3491`), appends to `manual_ci_bridges`. Next build hits branch (a) and the player anchors permanently. Idempotent.

### (b) A player who turns up mid‑match, in no squad

**b1 — registry knows them (a `ci:` pid exists): ⛔ BLOCKER B3.**

They resolve to a pid, so they are *not* a no‑pid leftover and would vanish. The rescue is `find_silent_drops:2998` → `register_new_player(source="auto")` (the 10 `auto` rows in `new_players.json`). It short‑circuits at **`:2001‑2003`**:

```python
es = short_of(v.get("team", "")) or ""
if es not in match_shorts:
    continue  # can\'t safely attribute to a team — leave it (rare; blank feed team)
```

**`parse_espn` never sets `team`.** `blank_perf:669` defaults `team=""`; only the cricsheet parser (`:720`) and cricapi\'s (`:1278`) assign it. Today `merge_perf:399` (`out["team"] = a.get("team") or b.get("team")`) inherits it **from cricapi**. On an ESPN‑only match every entry has `team == ""` → `continue`.

**Result under ESPN‑only: a known player who plays but sits in no squad slot is silently dropped — no row, no flag, no review.** Fix is one line: `team_map` is already fetched at **`:1934`** and is exactly `{norm(name): team displayName}`.

**b2 — registry does not know them: works, with a rough edge.**
`resolve_perf_pid` → `None` → `leftover` → emitted `:2316‑2319` with `in_squad="N"`, plus a **Needs Review** row with a `closest_squad` suggestion (`:503‑509`). You type `New`; `:2900‑2910` mints **`slug:<name>`** — *even though ESPN handed us `athlete.id`, which is the cricinfo id.* `promote_new_players:3331` upgrades `slug:`→`ci:` only on a human‑typed bridge, and anything still on a placeholder is pushed to Needs Cricinfo ID (`:3384‑3394`).
**Improvement available:** on the ESPN path, mint `ci:<athlete.id>` directly and skip the manual round‑trip entirely. Deferred — not a blocker.

**Emit‑path gap (existing):** `emit:2248` uses `resolve_pid(name)` (name‑only), not `resolve_perf_pid`. It works only because `resolve_perf_pid:326‑330` teaches `ALIAS2PID` during matching — and that teaching is itself gated on `pid in PID2DISP` (`:324`). For an id the registry has never seen, nothing is learned and the row emits with a **blank Player ID**, unjoinable by the draft.

### (c) B4 — the id is in hand and thrown away (PROVEN)

| site | code | drops |
|---|---|---|
| `:1090` | `perf[k] = blank_perf(e["name"])` | `espn_xi` returns `e["espn_id"]` (`:926`) — discarded. Every XI‑only player |
| `:1067` | `get(fld)["catches"] += 1` | `fld_id` is extracted at `:1062` and **never used**. Every pure fielder |
| `:1069` | `get(fld)["stumpings"] += 1` | same |

The run‑out path (`:1083`) does it correctly — which is why this looked fine. Measured **2 of 88 rows (2.3%)**, 1 each in 2 of 4 matches:

```
ev 1521199  \'Kathryn Emma Bryce\'   espn_id=\'\'  -- summary carries id 914675, starter=True
ev 1538624  \'Amir Anthony Jangoo\'  espn_id=\'\'  -- summary carries id 820681, catches=1
```

This is exactly the class of failure that produced the 20 corrupted live rows in the unified name‑match incident. **Fix: pass the id through at all three sites.**

---

# 4. GSHEET TAB CHANGES

### 4.1 Tab fates (11 tabs)

| Tab | Written by | After the flip |
|---|---|---|
| **`<per‑tour points>`** | `write_to_gsheet:3644` | survives; **2 columns die, 4 change meaning** |
| **STATUS** | `write_status_tab:2689` | **mostly dead** — 3 of 7 rows are cricapi quota |
| **Player Aliases** | `sync_player_aliases:3195` | survives, **goes quiet** |
| **Needs Review** | `write_review_tab:3225` | survives, goes quiet (near‑permanent "matched cleanly 🎉") |
| **Identity Anomalies** | `write_anomaly_tab:3257` | **unchanged** |
| **Recon Review** | `write_recon_tab:3599` | survives, **halves** — L1 rows gone, S1/S2 headers must be rewritten |
| **Needs Cricinfo ID** | `:3541` + `tour_sync_finalize.py:99` | unchanged — becomes **the only identity tab** |
| **SETTLEMENT AUDIT** | `write_settlement_tab:3569` | **unchanged**, all 12 columns |
| **TOUR CONTROL** | `sync_tour_control:2511` | survives but **no longer a quota gate** |
| **TOUR INGEST REVIEW** | `tour_sync_finalize.py:70` | unchanged (already ESPN‑centric) |
| **TOUR STATUS** | `tour_status.py:227` | survives, 1 column becomes advisory |

### 4.2 Points tab — the columns that change

Column list `:1873‑1884`. (The committed CSVs on disk carry only 37 columns, stopping at `Player Recon` — they predate `Recon State`/`Points Delta`. **The code is the authority.**)

| # | Column | Before → After |
|---|---|---|
| 16 | **Dots** | blank when `dots_final=False` (cricapi‑only match, `:2287`) → **always populated**; the blank branch is unreachable. *Improves* |
| 30 | **Source** | **5 strings → 2** (see below) |
| 32 | **Bat Order** | ⚠ **goes blank on every provisional row unless B2 is fixed** |
| 33 | **L1 Recon** | `capi_pid` always empty ⇒ else‑branch ⇒ permanently `""`. **DEAD — delete the column** |
| 34 | **L2 Recon** | survives; baseline sharpens to "cricsheet vs frozen ESPN value" |
| 35/36 | **Match Status / Recon Flag** | vocabulary **8 strings → 5** |
| 37 | **Player Recon** | `⏳ unreconciled` dies (3 markers, not 4) |
| 38 | **Recon State** | same 4 labels, 2 relabelled — **do not rename the labels**, see 4.4 |
| all others | unchanged in meaning |

**`Source` (`:1949‑1968`, `:2361`) — verbatim before → after:**

| Before | After |
|---|---|
| `cricsheet · official` | **survives verbatim** |
| `cricapi + ESPN dots/XI · ⏳ provisional (dots unverified, awaiting cricsheet)` | **dead** |
| `ESPN scorecard (cricapi empty) · ⏳ provisional (dots unverified, awaiting cricsheet)` | **survives, reworded** → `ESPN scorecard · ⏳ provisional (awaiting cricsheet)` |
| `cricapi · limited (no dots/XI — ESPN unavailable) · ⏳ provisional (awaiting cricsheet)` | **DEAD** — that `else` branch is unreachable; no‑ESPN now means the no‑data guard skips the match |
| `ESPN announced XI (toss)` / `· super-over excl` | **survive unchanged** |

**Drop `(cricapi empty)` deliberately.** Today it reads as a warning; after the flip it would be on 100% of rows and would train you to ignore the Source column.

### 4.3 Recon Review headers (`:3608‑3615`)

Both headers are dual‑purpose today. Only the right half survives:

- `S1 = cricapi (L1) / held provisional (L2)` → **`S1 = held provisional (frozen ESPN value)`**
- `S2 = ESPN (L1) / OFFICIAL cricsheet (L2)` → **`S2 = OFFICIAL cricsheet`**

### 4.4 Do NOT rename the recon states

`classify_recon_state:1608‑1625` emits `L1_OPEN` / `L1_DONE` / `L2_PENDING` / `L2_DONE`. These are a **published contract**: `/Users/nishant-singodia/wwc-draft/lib/points.ts:245‑246` hard‑codes the labels and `app/draft/[code]/results/page.tsx:360,393` renders them. Keep the four names; redefine only the inputs. Renaming breaks the app.

### 4.5 Your new weekly routine, in plain English

**What disappears from your week:**
- No more cricapi quota watching. The three quota rows on STATUS stop meaning anything; the TOUR CONTROL per‑tour approval gate stops being a rationing decision.
- The **L1 Recon** column goes permanently blank — stop reading it. There is no second live feed to disagree with, so there is nothing to arbitrate before cricsheet arrives.
- **Needs Review** and **Player Aliases** go near‑silent, because ESPN hands us the cricinfo id directly instead of a name to guess at.

**What you do instead — three checks, in order:**

1. **Needs Cricinfo ID** — the only identity tab that still matters. Any row here means a player has no anchor. Type the number, done. Currently 0 pending.
2. **Recon Review, L2 rows only** — cricsheet has landed and disagrees with what was published. Approve `S2` (official) unless you have a reason. This is where corrections to already‑published numbers happen.
3. **SETTLEMENT AUDIT / `/audit`** — the money view. Unchanged. `Results flipped` must stay **0**.

**The one new thing to watch:** a match that appears in the schedule but **never gets a row**. That is R1 — ESPN team‑name mismatch — and it is now silent‑by‑default. Step 5 of the migration turns it into a loud alert; until then, eyeball the match count per tour.

---

# 5. PAST MATCHES — BACKFILL OR NOT

### 5.1 The honest scope

**174 completed matches across 11 tabs. 146 of them (84%) already publish cricsheet\'s numbers.** Keeping cricsheet as the L2 arbiter reproduces those exactly — the ESPN L1 value is not what is on the sheet. **The shipped ESPN fixes are invisible there.**

Fix timeline (`git log`): `855379e` 11 Aug (run‑outs from summary), `4558579` 12 Aug (dots/no‑ball/pagination + the `non_boundary` bug), `785dec8` 12 Aug (refuse partial fetch).

The rescore‑candidate set is **28 matches, not 174**. Of those, the 18‑Aug‑dated ESPN‑only Hundred/LPL matches **already carry the fix** — the in‑place sheet rewrite propagated it (LPL M23 = 2 run‑outs; HndM M24/25/26 = 2 each; HndW M27/M29 = 4 each). **No backfill needed there.** They also cannot be settled yet: cricsheet has posted nothing for the Hundred after 2026‑08‑06.

### 5.2 What is genuinely still wrong — 2 matches, +36 FP (PROVEN)

Verified against the already‑extracted `cs_odi` archives:

| event | sheet | date | finding |
|---|---|---|---|
| `1538626` | NZ v WI **M3** | 17 Jul | cricsheet has **0 run‑outs** → sheet correct, **no change** |
| `1538627` | **M4** | 19 Jul | `MW Forde` run out by `MJ Santner` **alone** → direct hit → **Santner +12 FP**. Sheet shows 0 |
| `1538628` | **M5** | 21 Jul | `MJ Santner` (Hope + Seales), `KDC Clarke` (Greaves + Hope) → 4 assisted credits × 6 → **Hope +12, Seales +6, Greaves +6 = +24 FP**. Sheet shows 0 |

Pipeline sanity check: sheet M2 (cricapi‑mix) shows 3 run‑out credits; `1538625` has 2 run‑outs / 3 fielder credits — **exact**.

**UNCERTAIN:** **MLC M33** (17 Jul, ESPN‑only, 1451 FP) and **MLC M32** (16 Jul, cricapi‑mix) also show whole‑match `Run Outs = 0`. No MLC cricsheet archive is extracted locally, so I did not verify. At the measured ~1.1 run‑outs/T20 the expectation is ~8–12 FP each.

**Total proven defect available to fix: +36 FP, 2 matches, 4 players. Ceiling including MLC: ~+60 FP, 4 matches.**

### 5.3 RECOMMENDATION: fix 2 matches by hand. Do NOT bulk‑rescore history.

Rescoring the 146 cricsheet‑settled matches from ESPN would be a **regression**: this week\'s own measurements bound the difference at ODI 10/10 exact, LPL T20 **2 runs in 6138** (0.03%), Hundred M balls −2, Hundred W one delivery. Over 18 LPL matches that is ~2 FP total — and you would be replacing the **official arbiter** with a feed measured 2 runs worse. There is no version of that trade that helps.

### 5.4 What it does to already‑settled money — read this part twice

**The baseline cannot be overwritten.** `record_settlement:2774` opens `if not pid or (match_key, pid) in SETTLEMENTS: return` — write‑once is enforced by key presence. A rescore physically cannot touch it. **That is precisely why a backfill surfaces as re‑settle work instead of vanishing:**

`_points_delta:2809` → signed `Points Delta` → `wwc-draft/lib/settlement-audit.ts:149 groupFor()` → `app/audit/page.tsx:154` "Result changed" tile, `:156` "Results flipped", `:318` *"⚠ L2 recon done — changed vs L1 settlement"*, `:323` *"Already applied: these differ from what the contest was settled on."*

**Two hard facts about the two matches I want you to fix:**

1. NZ v WI M ODI has **0 of 5 matches baselined** — the settlement snapshot only starts 2026‑07‑22, and M4/M5 are 19/21 Jul. So the change lands in `NO_BASELINE`, **not** `CHANGED`. **The audit page will not flag it.** You must decide by hand whether those two contests were already settled and paid. If they were, that is a manual payout adjustment of +12 and +24 FP across 4 players.
2. Do it **before** you flip, as an isolated edit, so the delta is attributable to the run‑out fix and not to the migration.

**Pre‑existing drift you will otherwise blame on this migration.** Measured today, before anyone touches anything — the bot\'s own `Points Delta` column: **269 rows / 3809 abs FP / 63 matches** (LPL 97/1771/23, HndM 87/921/20, HndW 85/1117/20). An independent snapshot↔sheet join reproduces it at 272/63, so the join is validated. Bucketed by `/audit`\'s real logic:

| bucket | rows | abs FP | matches |
|---|---|---|---|
| **CHANGED** (the re‑settle list) | **204** | **2508** | **43** |
| PENDING (bot holding settled value) | 17 | 126 | 7 |
| NO_BASELINE (`provenance: "unknown"`, the 29‑Jul seed) | 1012 | 1389 | — |
| CLEAN | 1702 | 0 | — |

**Clear this list before the flip, or you will never be able to tell migration damage from pre‑existing drift.** Also note **89 of 174 completed matches have no baseline row at all** (all of WWC, all of MLC, all bilaterals), and 1012 snapshot rows carry `provenance: "unknown"`. Genuinely provable settlement coverage today is ~1900 rows / ~55 matches — a third of the season.

---

# 6. THE MIGRATION ITSELF

**There is no `--dry-run` flag** (checked: no `argparse`, no dry‑run env var). The safe rehearsal is `GSHEET_ID="" OUT=/tmp/scratch.csv` — produces the CSV without writing the sheet.

**The only irreversible artifact is `registry/settlement_snapshots.json`** (write‑once at `:2774`). A bad run that *freezes a wrong value* cannot be undone by `git revert`. **Back that file up before every stage. This is the single most important line in this plan.**

Run every stage in a **window with no live match**.

---

### Step 0 — Clear the decks
Work the existing **204 CHANGED rows / 43 matches** to zero. Fix the 2 proven ODI run‑out matches (§5.2) as an isolated commit.
**Gate:** `/audit` "Results flipped" = 0; CHANGED list empty or explicitly signed off.
**Rollback:** n/a — no code change.

### Step 1 — Fix B1 `dismissed` (source from summary `outDetails.dismissalCard`)
**Gate:** re‑run the harness on the 4 cached events — dismissal flags must be **86/86 vs cricsheet, 0 mismatches**, and the mis‑flag pairs (Wellalage/Hridoy ev 1537345, Tongue/Noor ev 1521233, Dean+Groves ev 1521199) must all resolve correctly. Total points delta stays 0/0/0/0.
**Rollback:** `git revert`. cricapi is still in the pipeline, so L1 still supplies `dismissed` — zero live exposure.

### Step 2 — Fix B2 `bat_order` (summary `statistics.batting.order`)
**Gate:** **86/86 exact vs cricsheet**, 0 rows reading 0 where cricsheet has a position.
**Rollback:** `git revert`. Column returns to cricapi\'s value.

### Step 3 — Fix B4 identity (pass `espn_id` at `:1067`, `:1069`, `:1090`)
**Gate:** **0 of 88 rows** with a blank `espn_id`; Bryce (914675) and Jangoo (820681) both resolve. Then a full run: `registry/players.json` still 679 pids, still 679 `ci:`, no new `slug:`/`uncapped:` entries, Identity Anomalies unchanged.
**Rollback:** `git revert`.

### Step 4 — Fix B3 silent‑drop team attribution (`:2001‑2003` ← `team_map` from `:1934`)
**Gate:** on an ESPN‑only match, force a known player out of the squad list and confirm `AUTO-ADD:` fires on stderr and `new_players.json` gains a `source:"auto"` row. Confirm no *spurious* auto‑adds on a clean match.
**Rollback:** `git revert`. Until this ships, cricapi\'s `team` masks the bug — this step is the last one that is safe to defer.

### Step 5 — Harden R1 discovery, **still with cricapi in place**
Scope `espn_event_id` by the tour\'s `espn_series` (present for all 12 tours) instead of relying on the date scoreboard + name join alone, and **turn the no‑data skip at `:1943‑1948` into a loud alert** — a row on STATUS or TOUR STATUS naming the unmapped match. A silently missing match is the worst possible failure over real money.
**Gate:** run all 12 tours with cricapi **still on**; every match that cricapi scores must also resolve an ESPN event id. **Expected: 174/174. Any miss here is a match that would have gone dark after the flip — fix it before proceeding.**
**Rollback:** `git revert`. This is the highest‑value step and it is measurable *before* you lose the safety net.

### Step 6 — THE FLIP
Delete, in one commit:

| symbol | lines | note |
|---|---|---|
| `parse_match` | `1257‑1351` | the only `match_scorecard` consumer (`:1261`); takes the NULL‑bowler regex rescue `1291‑1303`, the `c & b` rescue `1304‑1317`, the run‑out fielder text parsing `1318‑1330` with it |
| `evict_empty_scorecard` | `543‑562` | only caller `:1348` |
| `api_perf` construction | `1894‑1913` | incl. the two cricapi log lines |
| `crosscheck` | `1094‑1107` | **already dead**, 0 callers |
| `espn_dots` | `829‑852` | **already dead**; the `limit=600` no‑pagination relic — delete before someone "fixes" the wrong function |
| `RECON_L1` | `1358` | all 4 fields already in `RECON_L2` |
| `L1_RUN_TOL` + `_l1_field_material` | `1510‑1519` | closes the `RUNBOOK:127` "hiding up to 7 pts/row" defect by removing the concept |
| `compute_l1_gaps` | `1537‑1584` | call site `:2039` |
| `_resolve_override_value` | `1673‑1680` | body is literally "S1 = cricapi, S2 = ESPN" |
| `apply_recon_overrides` | `1682‑1722` | call sites `:2058`, `:1747`. **The L2 hold does NOT use it** — that is inline at `2110‑2119` reading `l2_approved_pids:1724` |
| `reconciled_provisional` | `1740‑1748` | collapses to `dict(prov_pid)`; replace at `:2065`, `:2086`, `:2098`, `:2265`, `:2268` |
| `build_recon_rows` | `1750‑1765` | call site `:2166` |
| `xcheck` | `1424/1446/1459`, `2017`, `2022` | assigned and **never read** — the `CLAUDE.md:103` defect |
| `ESPN_ONLY_FIELDS` `1413`, `RECON_L1_SINGLE` `1606` | | already dead |

Keep `_perf_has_activity:1526`, `_espn_has_ballbyball:1531` — the latter is now the **primary** completeness predicate. Simplify `classify_match_status:1630‑1671`: drop the dead `l1_gaps` param (never referenced in the body), drop `capi_present` (`:1631`, `1664‑1665`) or its flag fires on **every** match and becomes noise, drop the unreachable `espn_present` branch (`1662‑1663`). Keep `cs_path`/`id_break`/`l2_dirty`/`unresolved` and the already‑completed ratchet untouched. Keep the four `classify_recon_state` labels (§4.4).

Keep the cricapi **discovery** call at `:1792` (`series_info`).

**Gate:** run all 12 tours with `GSHEET_ID=""` to scratch CSVs and diff against the last pre‑flip CSVs. **Expected diff: zero rows on all 146 cricsheet‑sourced matches. Any non‑zero diff on a cricsheet match is a bug in the deletion, not a feed difference — stop.** Then confirm the 24 ESPN‑only matches change only in the `Source` string and the now‑populated `Bat Order`.
**Rollback:** `git revert` the single commit; **restore `registry/settlement_snapshots.json` from the Step‑6 backup.** cricapi credentials must stay live and unrotated for 30 days.

### Step 7 — Sheet cosmetics
Delete the dead **L1 Recon** column, reword `Source`, rewrite the Recon Review S1/S2 headers, prune the 3 cricapi quota rows from STATUS.
**Gate:** load `/audit` and a live contest results page in the draft app — `lib/points.ts:245‑246` and `results/page.tsx:360,393` must still render recon state correctly. **This is the app‑contract check; do not skip it.**
**Rollback:** column edits are cosmetic and re‑runnable.

### Step 8 — Watch, don\'t touch
Run one full tournament week ESPN‑only. Track: matches‑with‑rows vs matches‑in‑schedule (R1 canary), L2 disagreement rate when cricsheet lands, `Results flipped` = 0.
**Gate for calling it done:** one full week, zero silent match drops, L2 delta within the measured 0.03% band.

---

## What I could not prove

- **Maidens (1 sample), stumpings (2), direct‑hit `dro` (1)** — each 0 diffs but statistically thin. The *mechanism* is identical to cricsheet\'s in each case, and L2 will catch drift, but call these CONFIRMED‑THIN, not settled.
- **ESPN team‑name miss rate (R1)** — unmeasured. Step 5 measures it while cricapi is still a safety net. **Do not skip Step 5.**
- **MLC M32/M33 run‑outs** — no local cricsheet archive extracted; ~8–12 FP each expected, unverified.
- **The 1012 `provenance: "unknown"` snapshot rows** — I did not attempt to reconstruct their true baseline. They will read `NO_BASELINE` in `/audit` forever unless reseeded.

**Bottom line: the numbers are right, the plumbing is not — yet.** Four fixes, all in a payload you already fetch, all verified against cricsheet, plus one discovery hardening that must be measured while cricapi is still there to catch the miss. Do those and ESPN‑only is safe. Flip today and you go live with a duck bug that silently misses ~1 in 25 matches, a settlement column that churns on every batter, and a class of player that disappears without a trace.', 'verdict': '# REFUTATION AUDIT — ESPN-only scoring

**Method.** 4 cached matches, 3 formats, 88 ESPN perf rows / 86 matched to cricsheet **by cricinfo id** (not name). All ESPN reads cached, zero network. Harness: `/private/tmp/claude-501/-Users-nishant-singodia/f36700e2-70e2-4403-9da5-40a03f07ecbc/scratchpad/refute.py`, `refute2.py`.

| ev | series | fmt | date | teams | cricsheet |
|---|---|---|---|---|---|
| 1521233 | 1521176 | HUN-M | 2026-07-23 | London Spirit / Man SG | cs_hnd/1521233.json |
| 1521199 | 1521193 | HUN-W | 2026-07-23 | London Spirit / Man SG | cs_hnd/1521199.json |
| 1537345 | 1537330 | T20 | 2026-07-28 | Colombo Kaps / Jaffna Kings | cs_lpl/1537345.json |
| 1538624 | 1538619 | ODI | 2026-07-11 | WI / NZ | cs_odi/1538624.json |

---

## PER-FIELD VERDICT

| field | verdict | evidence (my run, not inherited) |
|---|---|---|
| `r` | **CONFIRMED** | 0 diffs / 86 rows / 4 matches |
| `b` | **CONFIRMED** | 0 diffs |
| `4s` / `6s` | **CONFIRMED** | 0 diffs |
| `balls` | **CONFIRMED** | 0 diffs |
| `runs_conceded` | **CONFIRMED** | 0 diffs |
| `w` | **CONFIRMED** | 0 diffs, **48 wickets** in sample |
| `lbwb` | **CONFIRMED** | 0 diffs, **18 lbwb events** (5 lbw + 13 bowled). ESPN emits `type="leg before wicket"`; handled at `:1058` |
| `dots` | **CONFIRMED** | 0 diffs (single-sourced, so cricsheet is the only check) |
| `catches` | **CONFIRMED** | 0 diffs, **28 catches** |
| `runouts` | **CONFIRMED** | 0 diffs, **11 fielder-credits over 6 run-outs**; fielder sets exact incl. four 2-man assists |
| `played` | **CONFIRMED** | 0 diffs |
| `maidens` | **CONFIRMED (THIN)** | 0 diffs but only **1 maiden** in 4 matches |
| `stumpings` | **CONFIRMED (THIN)** | 0 diffs but only **2 stumpings** |
| `dro` | **CONFIRMED (THIN)** | 0 diffs but only **1 direct hit** (Ibrahim Zadran, single fielder Neesham) |
| **`dismissed`** | **REFUTED** | **6 wrong flags in 3 of 4 matches** |
| **`bat_order`** | **REFUTED** | **0 for 100% of rows** — 67 rows that should be non-zero |
| **`espn_id` (identity)** | **REFUTED** | **2 of 88 rows carry no id** — the id was in the same payload |

Total-points delta was **0 in all four matches** (1115/1115, 860/860, 1277/1277, 1452/1452) — but that is luck, not correctness; see below.

---

## REFUTATION 1 — `dismissed` is worse-shaped than reported (PROVEN)

`wc_fps_to_csv.py:1051` stamps `dismissed` on the playbyplay item\'s **striker**:
```python
pb = get(bt, bt_id); pb["dismissed"] = True; pb["dismissal"] = it.get("shortText", typ)
```
The prior agent called this "mis-attribution — wrong player marked out." **The real failure mode is a silent MISS.** Raw playbyplay for ev 1521199:
```
type=run out   batsman=Nadine de Klerk        <- cricsheet says the victim was CE Dean
type=bowled    batsman=Nadine de Klerk        <- de Klerk\'s own real dismissal
type=run out   batsman=Katie Louise George    <- cricsheet says the victim was J Groves
type=caught    batsman=Katie Louise George    <- George\'s own real dismissal
```
The flag lands on a striker who is usually dismissed later anyway, so it is idempotently absorbed and **no phantom appears — the actual victim is simply never marked out.** In ev 1521199 both run-out victims (Dean, Groves) read `espn=False / cs=True` with zero visible phantoms. A "phantom count" audit therefore *understates* this bug.

Exposure, computed over the local cricsheet archives (508 matches, no ESPN calls):
```
LPL_T20:  141 matches, 133 run-out/retired,  66 non-striker ->  3 MISSED ducks,  3 phantom (upper bound)
HUNDRED:  367 matches, 346 run-out/retired, 143 non-striker -> 10 MISSED ducks,  4 phantom (upper bound)
```
≈**20 wrong duck applications / 508 matches (1 per 25)**, ±2 pts (±3 ODI). Live today it is masked because cricapi\'s card supplies `dismissed` at L1 and `merge_espn_into` never overwrites it. **Removing cricapi promotes it from dormant to live.**

Second-order: `:1051` also writes `p["dismissal"]` (the display string) to the wrong batter, and `dismissal` is in `SETTLED_FIELDS` (`:2770`) — a wrong string gets frozen into the settlement record and churns at L2.

**Fix I independently verified:** source `dismissed` from summary `rosters[].roster[].linescores[].statistics.batting.outDetails.dismissalCard`, keyed on `athlete.id`. Card vocabulary observed: `c`, `bowled`, `lbw`, `st`, `run out`, `retired out`, `not out`, `""`(did not bat). **0 mismatches / 86 rows / 22 dismissals / 6 run-outs / 1 retired-out.**

---

## REFUTATION 2 — `bat_order` is not "a gap to fix", it is 100% absent (PROVEN)

`parse_espn` never sets it. Measured: **67 of 67 rows that cricsheet gives a batting position read 0 from ESPN.** With cricapi gone, the sheet\'s batting-position column goes blank on every provisional row, and because `bat_order ∈ SETTLED_FIELDS:2770`, the blank→filled transition when cricsheet lands **churns settled records for every batter in every match** — not an edge case.

**Fix verified independently:** summary `statistics.batting.order` (1-based). **0 mismatches / 86 rows.**

---

## REFUTATION 3 — the identity claim is FALSE; fuzzy matching is still reachable (PROVEN)

The prior agent\'s §5: *"Identity — ESPN athlete.id is carried into every perf."* **Refuted.** Three code sites drop an id that is already in hand:

| site | code | what it drops |
|---|---|---|
| `wc_fps_to_csv.py:1090` | `perf[k] = blank_perf(e["name"])` | `espn_xi()` returns `e["espn_id"]` (`:926`) and it is discarded — every XI-only player |
| `wc_fps_to_csv.py:1067` | `get(fld)["catches"] += 1` | `fld_id` is extracted at `:1062` and **never used** — every pure fielder |
| `wc_fps_to_csv.py:1069` | `get(fld)["stumpings"] += 1` | same |

(The run-out path at `:1083` does it correctly — which is why it looked fine.)

Measured, 2 of 88 rows (2.3%), 1 each in 2 of 4 matches:
```
ev 1521199  \'Kathryn Emma Bryce\'  espn_id=\'\'  -- summary carries id 914675, starter=True
ev 1538624  \'Amir Anthony Jangoo\' espn_id=\'\'  -- summary carries id 820681, catches=1
```
Both cricsheet rows resolve to `ci:914675` / `ci:820681` — the **exact same ids ESPN was holding and threw away.** They currently resolve anyway, but only because `ALIAS2PID` already contains ESPN\'s long-form spelling; without an id, `resolve_perf_pid:332` falls through to `resolve_pid(name)` — **the fuzzy path.** A debutant or sub fielder not yet in the registry lands there with an ESPN long name (`"Rajapaksha Vidana Pathiranalage Kamil Mishara"`-class).

This directly refutes `STREAMLINE_PLAN.md:3` — *"All fuzzy name matching in the scoring path... becomes a dictionary lookup... structurally impossible."* It is not structurally impossible today; it is one line of dropped data away, on the +4 XI bonus and the +8 catch.

Related fragility (not a defect found in-sample): `espn_runouts` credits fielders via `get(nm)` **by normalised name**, not by `athlete.id`, even though it has the id. Summary and playbyplay `fullName` agreed on all 11 credits here, so no duplicate/phantom rows appeared (row counts 22/22/24/22 exactly matched cricsheet, zero duplicate `espn_id`). A single spelling divergence would create an orphan row.

---

## UNCERTAIN — do not treat as cleared

- `maidens` (1 event), `stumpings` (2), `dro` (1 positive) are exact but **too thin to certify**. `dro` is a ±6 pt swing (12 vs 6) and depends on ESPN listing *every* fielder — if ESPN omits one of a 2-man assist, an assisted run-out silently becomes a direct hit. Not observed; not disproved.
- `retired hurt` / `retired not out` never appeared. `parse_cricsheet:761` sets `dismissed=True` for *all* wicket kinds including retired hurt; ESPN\'s `dismissalCard` vocabulary for those is unsampled. A duck could differ.
- Super-over handling (`period > 2`) — no super over in sample.
- **Out of scope but load-bearing:** live scoring still starts only when cricapi flips `matchStarted` (RUNBOOK §7). Removing cricapi from *scoring* does not remove it from the *live trigger*; it becomes the new single point of failure.

---

## BOTTOM LINE

**NOT YET SAFE FOR MONEY.** The prior agent\'s headline — *"ESPN supplies every input `score()` consumes — nothing is missing"* — is **directionally right on the 13 numeric scoring fields and wrong on three counts.** Fifteen fields are confirmed exact against cricsheet by id across three formats; the two that decide *who* a number belongs to and *whether a duck applies* are broken, and the identity guarantee the whole migration rests on has three holes.

Precisely what is missing, all three inside the **already-fetched, already-cached `summary` payload** (zero extra requests):

1. **BLOCKING —** `dismissed` must come from `outDetails.dismissalCard` keyed on `athlete.id`, not playbyplay `batsman` (`wc_fps_to_csv.py:1051`). ~20 wrong ducks / 508 matches, and it is a *miss*, not a swap, so it will not show up as a phantom in any audit that only looks for extra flags.
2. **BLOCKING —** `bat_order` must come from `statistics.batting.order`. It is 100% absent, and it is a `SETTLED_FIELDS` member — leaving it will churn every settled batter row when cricsheet lands.
3. **BLOCKING —** stop discarding `athlete.id` at `:1090`, `:1067`, `:1069`; key `espn_runouts` credits on the id it already has, not on `norm(name)`. Until this lands, "no fuzzy matching in the scoring path" is false and the Hasaranga/Carlson class is still reachable.

Fixes 1 and 2 are verified by my own independent harness at **0 mismatches / 86 rows / 4 matches / 3 formats**. Fix 3 is a mechanical id plumb-through.

After those three, I would still require a wider sweep before flipping — specifically ≥20 maidens, ≥10 stumpings and ≥5 direct-hit run-outs against cricsheet — because those three fields are currently certified on 1, 2 and 1 events respectively, and each is a 4–12 point swing.', 'found': ['## VERDICT

**ESPN supplies every input `score()` consumes — nothing is missing.** But two fields are currently taken from the *wrong ESPN payload*, and one is not taken at all. Both are fixable inside `summary`, which is already fetched under the same cache key (zero extra requests).

---

## 1. Every field the scorers read

`score()` dispatch `/Users/nishant-singodia/wwc-points-bot/wc_fps_to_csv.py:1109` → `_score_t20:1133`, `_score_hundred:1175`, `_score_odi:1208`. `_bowled` gate at `:1116`. Perf schema at `blank_perf:665-672`.

Consumed keys: `r, b, 4s, 6s, dismissed, balls, runs_conceded, w, lbwb, dots, maidens, catches, stumpings, runouts, dro, played` + `role` (registry, not a feed). **`bat_order` is NOT read by any scorer** — it is a display column (`:2292`) and a `SETTLED_FIELDS` member (`:2770`).

---

## 2. FIELD TABLE

| field | ESPN source (payload → path) | verified? | residual risk |
|---|---|---|---|
| `r` runs | playbyplay `commentary[].scoreValue` when `playType.description ∈ {run,four,six}`, minus no-ball penalty (`:1040-1043`) | **PROVEN** 4/4 matches exact (362/362, 249/249, 149/149, 502/502) | none |
| `b` balls faced | playbyplay, `+1` unless wide (`:1039`) | **PROVEN** 241/241, 201/201, 150/150, 594/594 | none |
| `4s` / `6s` | playbyplay `description=="four"/"six"` (`:1044-1047`) | **PROVEN** 37/37, 15/15, 19/19, 13/13, 6/6, 40/40, 10/10 | cricsheet `non_boundary` overthrows — already handled, ESPN is right |
| **`dismissed`** (duck −2/−3) | **currently** playbyplay `dismissal.dismissal` credited to `batsman` (`:1049-1052`) → **WRONG for non-striker dismissals**. **Correct source = summary `rosters[].roster[].linescores[].statistics.batting.outDetails.dismissalCard`** | **MISMATCH PROVEN**, then **FIXED-SOURCE PROVEN** | see §3 — the one real defect |
| `balls` bowled | playbyplay, legal deliveries only; `is_nb` detected via `shortText`+`text` regex (`:1032-1036`) | **PROVEN** 240/240, 200/200, 150/150, 592/592 | none |
| `runs_conceded` | playbyplay `scoreValue`, byes/leg-byes stripped, no-ball penalty kept (`:1053-1056`) | **PROVEN** 378/378, 257/257, 158/158, 520/520 | none |
| `w` wickets | playbyplay, excluding run out / retired* / obstructing / hit wicket for the bowler (`:1054-1062`) | **PROVEN** 11/11, 13/13, 11/11, 13/13 | none |
| **`lbwb`** (+8) | playbyplay `dismissal.type ∈ {bowled, lbw, leg before wicket}` (`:1058-1059`) | **PROVEN** 4/4, 6/6, 4/4, 4/4 — exact in all 4 | ESPN spells "lbw" out; both spellings handled |
| `dots` | playbyplay: legal ball with `bcharged==0` (`:1057`) | **PROVEN** 79/79, 84/84, 81/81, 294/294 | single-sourced (cricapi never had it) — unchanged by migration |
| `maidens` | playbyplay over-aggregation (`:1074-1076`) | **PROVEN** 1/1, 0/0, 0/0, 0/0 | thin sample (1 maiden); mechanism identical to cricsheet\'s |
| **`catches`** (+8, +4 at 3) | playbyplay `dismissal.fielder.athlete` for `caught`; `caught and bowled` credits bowler (`:1063-1067`) | **PROVEN** 7/7, 7/7, 6/6, 8/8 | none |
| **`stumpings`** (+12) | playbyplay `dismissal.fielder.athlete`, `type=="stumped"` (`:1068-1069`) | **PROVEN** 0/0, 0/0, 1/1, 1/1 | thin sample (2 events) but exact |
| **`runouts`** (+6) | summary `outDetails.fielders[].athlete` via `espn_runouts:879` — playbyplay `dismissal.fielder` is always empty here | **PROVEN** 3/3, 4/4, 4/4, 0/0 (+ prior 17/17 LPL sweep) | none |
| **`dro`** (+12 direct) | same, `len(fielders)==1` (`:1085-1086`) | **PROVEN** 1/1, 0/0, 0/0, 0/0 | direct-vs-assisted has only 1 positive in this sample; prior sweep had 17/17 |
| `played` (+4 XI) | summary `rosters[].roster[].starter or subbedIn` via `espn_xi:916` | **PROVEN** 24/24, 22/22, 22/22, 22/22 | none |
| SR / econ / hauls / milestones | pure functions of `r,b,balls,runs_conceded,w` above | **PROVEN** — total points delta **0** in all 4 matches | none |
| `role` (gates SR + duck) | **not a feed field** — squad registry, `ROLE_OVERRIDE`, else `guess_role:226` from stats | n/a | unaffected by removing cricapi |
| **`bat_order`** (display + settled) | **parse_espn supplies NOTHING → 0 for every player.** Available at summary `statistics.batting.order` | **GAP PROVEN**, fix **PROVEN 62/62** | non-scoring; see §4 |

**Nothing ESPN cannot supply.** Every scoring input has a verified ESPN path.

---

## 3. THE ONE REAL DEFECT — `dismissed` is mis-attributed (PROVEN)

`wc_fps_to_csv.py:1049-1051` stamps `dismissed=True` on `bt` — the **striker** on that delivery. When the **non-striker** is the one out (run out backing up, retired out), ESPN\'s playbyplay item still carries the striker in `batsman`, so the wrong player is marked out **and the real victim is marked not-out**.

Ground-truth pairs found:
- LPL ev 1537345, cricsheet ov15 `out=DN Wellalage kind=\'retired out\' striker=Ibrahim Zadran` → ESPN marked **Md Towhid Hridoy** dismissed, Wellalage not.
- Hundred-M ev 1521233, ov19 `out=JC Tongue kind=\'run out\' striker=Noor Ahmad` → ESPN marked Noor Ahmad, Tongue not.
- Hundred-W ev 1521199: **Charlotte Dean** and **Josephine Groves** both `espn=False / cs=True`.

3 of 4 matches affected. **Points delta was 0 only because every mis-flagged batter had `r>0`** — pure luck, not correctness. Exposure measured over the local archives:

```
LPL_T20:  141 matches, 124 run-outs, 66 non-striker dismissals -> 3 ducks MISSED, 3 phantom ducks ADDED
HUNDRED:  367 matches, 338 run-outs, 143 non-striker dismissals -> 10 ducks MISSED, 4 phantom ducks ADDED
```
**20 wrong duck applications in 508 matches (~1 per 25 matches), ±2 pts each (±3 in ODI).** For a real-money settlement that is a non-zero error rate, and it is invisible today because cricapi\'s scorecard (`parse_match:1290`) supplies `dismissed` at L1 — `merge_espn_into:1415` never overwrites it. **Removing cricapi promotes this bug from dormant to live.**

**FIX (verified):** read `dismissed` from summary `rosters[].roster[].linescores[].statistics.batting.outDetails.dismissalCard` — that block hangs off the *dismissed batter\'s own* roster entry, so attribution cannot be wrong. Measured across all 4 matches / 3 formats: **62/62 batters exact vs cricsheet, 0 mismatches.** Same payload/cache key `espn_runouts` already uses → no extra request.

---

## 4. `bat_order` gap (non-scoring, but real)

`parse_espn` never sets it (only cricsheet `:728-729` and cricapi `:1283-1284` do). With cricapi gone, the sheet\'s batting-position column goes blank for every provisional row, and `bat_order` is in `SETTLED_FIELDS:2770` — a blank→filled transition when cricsheet lands at L2 would churn settled records.

**FIX (verified):** summary `statistics.batting.order`, 1-based. Measured **62/62 exact vs cricsheet** across T20 + Hundred + ODI. Free — same payload.

---

## 5. Things that are already safe

- **Truncation** — `parse_espn:936-961` paginates and refuses to score a short/failed fetch. Confirmed on the 605-ball ODI ev 1538624: 594 balls faced / 592 bowled, exact.
- **Duplicate commentary** — dedup at `:963-976` fired live in this run (`ev 1537345: dropped 4 duplicate commentary item(s)`) and the match still matched cricsheet exactly on all 16 scoring fields.
- **Identity** — ESPN `athlete.id` is the cricinfo id and is carried into every perf (`blank_perf:665`, `:1078-1082`).

## 6. UNCERTAIN / not proven here

- `maidens` (1 event) and `stumpings` (2 events) are exact but thinly sampled in these 4 matches.
- `dro` direct-vs-assisted has 1 positive here; relies on the prior 17/17 LPL sweep.
- The live path still depends on cricapi\'s `matchStarted` flip to start scoring (per RUNBOOK/discovery). Removing cricapi from *scoring* does not by itself remove that gate — out of scope of this audit, but it must be re-pointed at ESPN or it becomes the new single point of failure.
- Name→pid resolution for summary-only fields was not re-tested end-to-end (my harness used a surname matcher; 6 LPL / 1 Hundred rows fell through it). Production resolves by `espn_id`, so this is a harness artifact — but the summary-sourced `dismissed`/`bat_order` fix must key on `athlete.id`, not name.

## RECOMMENDATION

Migration is **safe**, conditional on two changes in `parse_espn` before cutover, both inside the already-fetched `summary` payload:

1. Source `dismissed` from `outDetails.dismissalCard` (keyed by the roster athlete\'s id), not from playbyplay\'s `batsman`. **Blocking** — it is a real-money scoring error.
2. Source `bat_order` from `statistics.batting.order`. **Strongly recommended** — prevents settled-field churn.

Files: `/Users/nishant-singodia/wwc-points-bot/wc_fps_to_csv.py` (`:879` `espn_runouts`, `:929` `parse_espn`, `:1049-1052` the defect, `:1109-1250` the scorers). Verification harnesses: `/private/tmp/claude-501/-Users-nishant-singodia/f36700e2-70e2-4403-9da5-40a03f07ecbc/scratchpad/verify_fields.py`, `verify2.py`, `verify3.py`.', '# Removing cricapi from SCORING — exact change plan

Read: `CLAUDE.md`, `RUNBOOK.md`, `STREAMLINE_PLAN.md`. All line refs are `/Users/nishant-singodia/wwc-points-bot/wc_fps_to_csv.py` unless stated. Verdicts marked **PROVEN** were confirmed by reading the code; **UNCERTAIN** = inferred.

---

## 1. The complete cricapi surface in the scorer

**PROVEN — there are exactly two cricapi calls in the whole file:**

| line | call | role |
|---|---|---|
| `1261` | `api("match_scorecard", id=mid, …)` inside `parse_match` | **SCORING** |
| `1792` | `api("series_info", …, id=WC_SERIES)` inside `run_tour` | **DISCOVERY** |

`parse_match` has exactly one caller: `1908`. So the entire scoring dependency is one function and one call site. Everything else in the list below is *derived state* built from `api_perf`.

---

## 2. Verdict table

### 2a. The cricapi scorecard ingest — DELETE

| symbol | lines | verdict | reason |
|---|---|---|---|
| `parse_match` | `1257–1351` (95) | **DELETE** | Sole consumer of `match_scorecard`. Carries the whole cricapi-only bug surface: NULL-bowler regex rescue (`1291–1303`), the `c & b` catcher rescue (`1304–1317`), run-out fielder text parsing (`1318–1330`), malformed combined team labels (`1277`). All of it exists only because cricapi has no ids and a lossy card. |
| `evict_empty_scorecard` | `543–562` (20) | **DELETE** | Only caller is `1348`. Exists solely to un-cache cricapi stub cards. |
| `api_perf` construction | `1894–1913` (20) | **DELETE** | Includes the "no match id" log (`1901–1907`) and the "returned NO player data" log (`1909–1911`). |
| `crosscheck(cs, api)` | `1094–1107` (14) | **DELETE** | **Already dead** — zero callers (grepped `*.py`, incl. tests). Free win. |
| `espn_dots` | `829–852` (24) | **DELETE** | **Already dead** — zero callers. It is the `limit=600` no-pagination relic named in `CLAUDE.md:103`; the live path is `parse_espn` (`929–1092`), which paginates at `951` and refuses partial fetches at `953–957`. Delete it before someone "fixes" the wrong function. |

### 2b. The L1 layer — DELETE almost entirely

| symbol | lines | verdict | reason |
|---|---|---|---|
| `RECON_L1 = ["r","w","4s","6s"]` | `1358` | **DELETE** | It is the *intersection of two live feeds*. With one feed there is no intersection. All four fields are already in `RECON_L2` (`1359`), so nothing is lost at L2. |
| `L1_RUN_TOL` + `_l1_field_material` | `1510–1519` (10) | **DELETE** | Tolerance for a cricapi-vs-ESPN run blip. Also closes the known defect (`RUNBOOK:127`: "`L1_RUN_TOL=1` hiding up to 7 pts/row") by removing the concept rather than tuning it. |
| `compute_l1_gaps` | `1537–1584` (48) | **DELETE** | Pure feed-vs-feed arbitration. `capi_covers_match` (`1561`), `espn_covers_match` (`1554`), the "present in ESPN only" arm (`1576–1579`) and the "present in cricapi only" arm (`1580–1583`) all become vacuous. Call site `2039`. |
| `_resolve_override_value` | `1673–1680` (8) | **DELETE** | Its entire body is "S1 = cricapi feed, S2 = ESPN feed". |
| `apply_recon_overrides` | `1682–1722` (41) | **DELETE** | Handles only `scope in {"match","player"}` = L1. **PROVEN**: the L2 hold does *not* use it — the L2 hold is inline at `2110–2119` and reads `l2_approved_pids` (`1724`). Call sites `2058`, `1747`. |
| `reconciled_provisional` | `1740–1748` (9) | **DELETE** | Collapses to `dict(prov_pid)` once `apply_recon_overrides` is a no-op. Replace every use with `prov_pid` (call sites `2065`, `2086` via `_l2_baseline`, `2098`, `2265`, `2268`). |
| `build_recon_rows` | `1750–1765` (16) | **DELETE** | Emits `tier:"player"` rows — one per (player, differing field) between the two feeds. Zero such rows exist after the flip. Call site `2166`. |
| `xcheck` | returned at `1424/1446/1459`, assigned `2017`, `2022` | **DELETE** | **PROVEN never read** — grep shows it is assigned at `2022` and never referenced again. This is the `CLAUDE.md:103` "verified-but-unfixed: `xcheck` never read" defect. It dies with cricapi. |
| `ESPN_ONLY_FIELDS` | `1413` | **DELETE** | **Already dead** (defined, zero uses). |
| `RECON_L1_SINGLE` | `1606` | **DELETE** | **Already dead** (defined, zero uses). Its concept ("fields no second feed can validate") becomes *every* field, so the constant is meaningless. |
| `_perf_has_activity` | `1526–1529` | **KEEP** | Still the "did the feed observe anything, or is this a +4 placeholder" test — needed for the ESPN coverage gate. |
| `_espn_has_ballbyball` | `1531–1535` | **KEEP** | Same; it is now the *primary* completeness predicate, not a cross-check helper. |

### 2c. The two classifiers

**`classify_match_status` (`1630–1671`) — SIMPLIFY, ~42 → ~25 lines.**

- `l1_gaps` param — **DELETE. PROVEN unused**: the body (`1650–1671`) never references it. Dead parameter today.
- `capi_present` (`1631`, branch `1664–1665`) — **DELETE**. `"⚠ unverified — single feed (ESPN only, cricapi had no card)"` would fire on **every** provisional match after the flip. Leaving it in turns the flag into noise and breaks `RUNBOOK` §5.3.
- `espn_present` (`1662–1663`) — **DELETE the branch**. `"⚠ unverified — single feed (cricapi only)"` becomes unreachable: with cricapi gone, `not espn_perf and not cs_perf` is caught earlier by the no-data guard at `1945–1948`, which `continue`s. Keep the *parameter* only if you want a belt-and-braces assert; otherwise drop it (14 test refs, see §5).
- `unsourced` (`1657–1661`) — **KEEP the branch, redefine the input** (see §2d).
- `cs_path` / `id_break` / `l2_dirty` (`1650–1653`), `unresolved` (`1666–1670`), `already_completed` ratchet — **KEEP UNCHANGED**. These are cricsheet-and-identity logic, untouched by the flip.

**`classify_recon_state` (`1608–1625`) — KEEP the four states, redefine one input.**

Do **not** rename `L1_OPEN`/`L1_DONE`/`L2_PENDING`/`L2_DONE`. They are a published contract: `/Users/nishant-singodia/wwc-draft/lib/points.ts:245–246` hard-codes the labels and `app/draft/[code]/results/page.tsx:360,393` renders them. Renaming breaks the app for a cosmetic gain.

What changes is only what can put you in `L1_OPEN` (`1621`): today `unresolved` (feed disagreement) OR `unsourced`. After the flip `unresolved` carries **identity only**, and `unsourced` becomes the ESPN-completeness signal.

### 2d. Source selection and the merge — SIMPLIFY

| lines | verdict | change |
|---|---|---|
| `1915–1968` (54) | **SIMPLIFY → ~25** | Three branches collapse to two. `1960` `perf = api_perf if api_perf else espn_perf` → `perf = espn_perf`. `1962` `base_src` → constant `"ESPN scorecard"`. `1965–1968` (the cricapi-only branch) **DELETE** — with it goes `n_api` and `dots_final`. |
| `dots_final` `1951/1961/1967`, used `2285` | **DELETE** | Both surviving branches set it `True`. `2285` becomes `d["dots"]`. |
| `n_api` `1886`, `1967`, `2336` | **DELETE** | Log line `2336` becomes `sources: {n_cs} cricsheet(official), {n_espn} ESPN(provisional)`. Note `RUNBOOK:113` greps `sources:` — update the runbook string too. |
| `1945` no-data guard | **KEEP, tighten** | `not cs_perf and not _has(api_perf) and not _has(espn_perf)` → drop the `api_perf` term. **This guard is now the single load-bearing safety net**: `parse_espn` returns `{}` on any failed/short page (`953–957`), which lands here and skips the match for a retry instead of publishing a truncated card. |
| `merge_espn_into` `1415–1459` (45) | **SIMPLIFY → ~12, rename** | Its whole job was overlaying ESPN onto a cricapi base. Gone: the `dots`/`maidens` overlay (`1434–1435`), the Hundred `balls` backfill (`1442–1443` — ESPN *is* the source now), the `xcheck` compare (`1444–1446`), the "cricapi has no line" adoption (`1447–1458`). What survives is the `unsourced` scan (`1428–1430`) — and that changes meaning entirely (below). Recommend deleting the function and inlining the new gate. |
| `build_provisional_cut` `1461–1490` (30) | **SIMPLIFY → ~18** | `1472` drops `capi_assigned`; the prov cut = `match_squad_to_perf(team_players, espn_perf, quiet=True)[0]`. Keep the pid-mapping tail (`1481–1490`) verbatim — it is what stopped the phantom `dots 0→N` class, and it is still needed on cricsheet runs where `assigned` already holds official figures. |
| `capi_pid` `1975`, `2058`, `2065`, `2146`, `2166`, `2253–2254` | **DELETE** every occurrence | `_by_pid(api_perf)`. `espn_pid`/`cs_pid` **KEEP**. |
| emit L1 column `2250–2257` | **SIMPLIFY** | `l1_col` always `""`. See §4 for why the *column* stays. |

**The `unsourced` semantics change — read this twice.** Today `unsourced` = "this bowler bowled but no ESPN row exists, so dots/maidens were scored as an assumed 0". With ESPN as the base, a bowler with `balls > 0` has an ESPN row *by construction*, so the old computation returns the empty set always — and the gate silently stops gating. Do **not** just delete it; re-point it at the two failures that can still happen:

1. `parse_espn` returned `{}` (partial/failed fetch) → already handled by the `1945` skip.
2. A squad player who is in the announced XI but has **no** ESPN line at all, or an ESPN line that resolves to no pid → unconsumed data → hold LIVE.

That preserves `CLAUDE.md`\'s "Nothing goes unconsumed" invariant, which is the rule the flip most easily breaks by accident.

### 2e. Recon Review tab and approvals

| symbol | lines | verdict |
|---|---|---|
| `player_recon_markers` | `1730–1738` | **SIMPLIFY** — the `"⏳ unreconciled"` arm (`1734`) was the L1 marker; keep only the `"⚠ official revision"` L2 arm plus the identity markers added at `2152–2158`. |
| `_approval_to_override` `"ALL L1"` branch | `3053–3057` | **DELETE** — match-level "use whole feed S1/S2" seed. |
| `_approval_to_override` player-field branch | `3072+` (`field = LABEL2FIELD[param]`, `scope:"player"`) | **DELETE** — per-field L1 arbitration. |
| `_approval_to_override` `"L2"` / `"ID"` / `"ID-ORPHAN"` branches | `3059–3070` | **KEEP UNCHANGED** |
| `write_recon_tab` header | `3609–3612` | **EDIT** — `"S1 = cricapi (L1) / held provisional (L2)"` → `"S1 = keep the settled (ESPN) value"`; `"S2 = ESPN (L1) / OFFICIAL cricsheet (L2)"` → `"S2 = take the official cricsheet number"`. |
| `status_text` `"player"` tier | `3613–3614` | **DELETE** the `player` key; `l2` and `id` stay. |
| `RECON_ACK`, `PRIOR_RECON`, `overrides_by_match`, `l2_approved_pids` | `218`, `1724–1728`, `3020` | **KEEP** |

Existing `scope:"match"` / `scope:"player"` records in `registry/recon_overrides.json` become inert. **Archive them, do not delete** — they are the audit trail proving *why* a settled number is what it is. `overrides_by_match` (`3020`) will keep warning about them; add a filter rather than dropping the file.

---

## 3. What the recon model collapses to

```
BEFORE                                   AFTER
cricapi ─┐                               ESPN ──► provisional (id-anchored, complete-or-refused)
         ├─ L1 arbitrate (S1/S2) ─► prov          │
ESPN ────┘                                        │  base points FREEZE at publish
         │                                        ▼
cricsheet ─ L2 (S1/S2) ────────► settled  cricsheet ─ L2 (S1/S2) ──► settled
```

**L1 stops being an arbitration and becomes an admission gate.** There is no "which of two numbers is right?" question left. What remains at the L1 stage is two *yes/no* questions the code already asks:

- **Completeness** — did ESPN return a whole scorecard? Answered by `parse_espn`\'s page-count refusal (`953–957`) + the no-data skip (`1945`). Not human-facing: a match either publishes or retries.
- **Identity** — does every played player resolve to a pid? Already routed to **Needs Cricinfo ID**, never Recon (`CLAUDE.md` rule E, code at `2188–2241`).

So the Recon Review tab goes from **3 tiers to 2**: `l2` (official revision) + `id` (identity break). `player` tier disappears. `"ALL L1"` disappears. Human load = `STREAMLINE_PLAN` §6\'s ~6 rows/week. `S1/S2` survives with exactly one meaning: **S1 = keep what was settled, S2 = take cricsheet**.

`Recon State` keeps four values but effectively runs `L1_DONE → L2_PENDING → L2_DONE`, with `L1_OPEN` reserved for identity/completeness holds.

---

## 4. What must NOT be deleted

**Discovery — `api("series_info")` at `1792` KEEPS.** `matchList` supplies fields nothing else in the bot provides: `id`, `teams`, `date`, `dateTimeGMT`, `matchStarted`, `matchEnded`, `matchType`, `name`. Everything downstream reads them:

- `is_fmt` `1818–1831` (format admission, incl. the HUN/T20 branch)
- `near_today` `1833–1840`, `hours_since_start` `1848–1856`, `is_over` `1857–1863`, `OVER_HRS` `1847` — the time-based completion fallback that exists *because* cricapi\'s `matchEnded` is unreliable
- `ended` / `live` sets `1864–1869`
- `pending` toss list `2347–2348`
- tour freeze `2399–2408` → `mark_frozen(WC_SERIES)`

Also keep: `api()` itself `563–635`, the key-rotation/quota block `42–65`, `_CRICAPI_HITS` instrumentation `624–628` + `2620–2640`, the STATUS tab `2689–2739`, `TOUR CONTROL` `2509–2565` (it now gates one hit per tour per run instead of one per match — quota stops being a constraint but the approval gate is still the right shape), `load_frozen`/`mark_frozen` `2475–2489`, the abort guard `1795–1798` (**critical** — without it a cricapi outage wipes the sheet).

**Toss windows** `in_toss_window` `2491–2507` — **PROVEN zero cricapi**: reads `toss_windows.json`. Untouched.

**Toss-time announced XI** `2338–2391` — **already pure ESPN** (`espn_xi`, `espn_toss`, `espn_team_map`). Its only cricapi coupling is the `pending` fixture list, which stays.

**Squads** `load_squads` `637–664` — from `SQUADS_JSON`, never cricapi. Untouched.

**The entire cricsheet + L2 + settlement layer** — `parse_cricsheet` `703–787`, `RECON_L2` `1359`, `recon_gaps` `1365`, `points_gap` `1379` (the backstop), `unresolved_official` `1492`, `identity_break` `1586`, `_l2_baseline` `2075–2081`, the L2 hold `2110–2119`, the identity hold `2124–2136`, `SETTLED_FIELDS` `2770`, `record_settlement` `2772`, `settled_baseline` `2820`, `_points_delta` `2804`. **Zero changes.** Keep `record_settlement`\'s `field_sources` param (`2773`, `2799–2800`) even though it will always be empty going forward — historical records carry it.

**The `L1 Recon` column** in `cols` (`1879`) — **KEEP the column, blank the content.** Removing it shifts every positional index in the emitted rows (`2286–2294`, `2300–2303`, `2376–2377`, `2387–2388`) and in the draft app\'s sheet parser. Blank it now; drop it in a separate, deliberate schema change.

---

## 5. Lines touched, and the risks

**Estimate (`wc_fps_to_csv.py`, 3680 lines today):**

| bucket | delete | rewrite |
|---|---|---|
| cricapi ingest (`parse_match`, `evict_empty_scorecard`, `api_perf`) | ~135 | — |
| already-dead (`crosscheck`, `espn_dots`, 2 constants) | ~40 | — |
| L1 layer (`compute_l1_gaps`, overrides, `build_recon_rows`, tolerance) | ~135 | — |
| classifiers | ~20 | ~30 |
| `run_tour` source chain + merge + recon block | ~110 | ~70 |
| approvals / tab / headers | ~25 | ~15 |
| **total** | **~465** | **~115** |

Net roughly **−400 lines**, and `run_tour` drops from ~670 to ~500.

**Tests — 93 references across 4 files** (`grep` counts): `tests/test_match_status.py` **67**, `tests/test_settlement_identity.py` **14**, `tests/test_recon.py` **8**, `tests/test_parse_fixes.py` **4**. `test_match_status.py` is mostly L1 arbitration and will largely be deleted, not fixed. `RUNBOOK:110` says 218 tests; expect a substantial rewrite, and budget for it — do not let a red suite ride.

### Risks, highest first

1. **Re-scoring already-settled matches. This is the money risk.** Every non-cricsheet match previously published `COMPLETED` off cricapi\'s numbers will, on the first post-flip run, be recomputed from ESPN — and the sheet is rewritten in place every run. The `already_completed` ratchet (`2141`) prevents un-*publishing*, not value movement. **Required mitigation:** for any `match_key` already in `SETTLEMENTS` and not yet cricsheet-resolved, read the frozen values and do not rebase to ESPN. This must be an explicit, tested migration branch, not an emergent side effect. **UNCERTAIN**: I did not verify how many such matches exist right now.

2. **`RUNBOOK` §5.3 inverts.** It currently instructs: *"If it says `ESPN scorecard (cricapi empty)` or carries `⚠ unverified — single feed`, do not settle it."* After the flip that describes **100% of provisional matches**. The signal must move entirely onto `Recon State == L2_DONE` + `Source == "cricsheet · official"`. Ship the `RUNBOOK` edit and the draft-app copy edit **in the same commit** as the code, or the owner\'s checklist silently stops discriminating.

3. **Live matches still wait on cricapi\'s `matchStarted`.** `live` (`1868`) reads it, so the known behaviour in `feedback_live_points_need_cricapi_started` is **unchanged** by this work. Removing cricapi from scoring does not fix it; only moving discovery to the ESPN scoreboard (`STREAMLINE_PLAN` step 5) does.

4. **Measured residual error, from your own data (do not re-measure).** ODI 10/10 exact. LPL T20 exact except 2 runs in 6138. Hundred Men 9/10 (balls −2, on a logged fetch timeout). **Hundred Women 7/10 — dots/wickets/balls each −1, i.e. one missing delivery.** That last one is not yet at the "exact" bar `STREAMLINE_PLAN` §3 sets as the prerequisite. Either pin it or flip with it documented as a known, bounded gap.

5. **ESPN becomes a single point of failure with no second opinion.** The WAF/UA trap (`CLAUDE.md:203–215`) is now capable of zeroing the entire bot rather than degrading it. Before flipping, make an ESPN transport failure **fail the run loudly** rather than produce an empty `espn_perf` that the `1945` guard quietly skips for every match.

6. **Draft-app copy.** `/Users/nishant-singodia/wwc-draft/app/draft/[code]/results/page.tsx:1128` reads `"cricapi & ESPN disagree on this player — points not yet reconciled."` — a state that can no longer occur. `lib/points.ts:417` comments the bot\'s match-numbering as "cricapi/espn". Cosmetic, but the first one lies to the owner.

7. **Low risk, worth noting:** the Hundred `balls` backfill (`1442–1443`) exists because cricapi omits `overs` on 100-ball cards. Deleting it is correct — ESPN counts deliveries directly — but the `balls > 0` bowling gate in `_score_t20`/`_score_hundred` is what turned the Gleeson 4-for into 4 points. Add a regression test on a Hundred bowler before deleting.', 'I have the complete picture. Writing the trace now.

**Read first:** `/Users/nishant-singodia/wwc-points-bot/CLAUDE.md`, `RUNBOOK.md`, `STREAMLINE_PLAN.md`. Then walked `build_registry.py` (498 L, full), `wc_fps_to_csv.py` (targeted: 283–357, 425–512, 665–673, 855–1092, 1492–1600, 1925–2020, 2118–2336, 2870–3018, 3320–3568), `tour_sync_finalize.py:99–140`. Empirical checks used **cached** ESPN summaries only (6 events, Hundred Men series 1521176) — no fresh fetches, no re-download of cricsheet.

---

# Baseline numbers (PROVEN, read from disk today)

| store | value |
|---|---|
| `registry/players.json` | **679 pids, 679 are `ci:`** — 0 `cs:`, 0 `uncapped:`, 0 entries lacking `cricinfo_id` |
| `registry/crosswalk.json` | 18253 `cs2ci` + 104 `ci_alt` |
| `registry/manual_ci_bridges.json` | 112 bridge entries |
| `registry/needs_cricinfo_pending.json` | **0 pending** |
| `registry/new_players.json` | 39 players, **0 on placeholder pids**; source: `new`=25, `auto`=10, `needs-cricinfo`=4 |
| 6 cached ESPN summaries | roster = **22/22 every event** — ESPN carries exactly the two XIs, **zero** non-starter/non-subbedIn entries |
| run-out fielders, 5 of those events | **17 fielders, 17/17 `starter=True`, 17/17 `ci:<id>` already in `players.json`** |

The identity ladder is, in practice, already a single rung. That matters for every case below.

---

# (a) A player added to a squad before a tour

**Path:** `build_registry.build_tour` (`build_registry.py:375`) → `resolve_ci` (`:316`) → `needs_cricinfo_pending.json` → `tour_sync_finalize.write_needs_cricinfo_tab` (`tour_sync_finalize.py:99`) → operator → `manual_ci_bridges.json` → next build.

**Pid.** `build_tour:399-409` tries cross-tour reuse by alias first (`by_alias.get(ns)`), else `resolve_ci` runs the 5-step cascade at `:321-345`: manual bridge → legacy TS bridge → exact people.csv (country/gender-scoped, unique-only) → fuzzy (null-on-ambiguity) → ESPN roster `athlete.id`. Result → `pid = f"ci:{ci}"`; unresolved → `pid = f"uncapped:{slug}"` (`:409`).

**Operator sees.** stderr line `build_registry.py:454`: `<tour>: N slots | reused R | NEEDS-REVIEW K | ESPN harvested E`; the row in `registry/UNMAPPED_<slug>.txt` (`:452`); and the **Needs Cricinfo ID** GSheet row, deduped by `current_pid`, columns `player | current_pid | tour | team | closest_guess | cricinfo_id_FILL_HERE`.

**Operator does.** Types the numeric id into `cricinfo_id_FILL_HERE`. Nothing else works — `manual_ci_bridges.json` is the only sanctioned assertion channel.

**Self-heal.** `read_needs_cricinfo` (`wc_fps_to_csv.py:3397`, called at `:2583`) strips non-digits (`:3455`, tolerates a pasted URL), runs two merge guards (`:3466-3491`), writes `manual_ci_bridges[ci:<id>].names += [norm(player), recovered-slug-name]`. Next `build_registry` run hits branch (a) of `resolve_ci` and the player anchors permanently. `build_tour` is idempotent — `by_alias` reuse at `:400` reproduces the same `ci:` set.

**Status: PROVEN and currently clean** — 0 pending rows, 679/679 anchored.

---

# (b) A player who turns up mid-match, in no squad

Two sub-cases, and **one of them is broken on the ESPN-only path.**

### b1 — registry already knows them (a `ci:` pid exists)

`match_squad_to_perf:443-446` resolves the feed entry via `resolve_perf_pid` into `pid_pool`. No squad slot claims it, and because it *has* a pid it is **not** in `leftover` (`:500` — `leftover` is built from `unresolved` only). Without a rescue it would vanish entirely.

The rescue is `find_silent_drops` (`:2998`) called at `:2000`, then `register_new_player(..., source="auto")` at `:2008`. That is the 10 `auto` rows in `new_players.json`.

**⛔ It short-circuits on ESPN data.** `wc_fps_to_csv.py:2001-2003`:

```python
es = short_of(v.get("team", "")) or ""
if es not in match_shorts:
    continue  # can\'t safely attribute to a team — leave it (rare; blank feed team)
```

`parse_espn` **never sets `team`** on a perf entry. `blank_perf` (`:669`) defaults `team=""`, and nothing in `parse_espn` (`:943-1092`) assigns it — only the cricsheet parser (`:720`) and the cricapi parser (`:1278`) do. Today `merge_perf:399` (`out["team"] = a.get("team") or b.get("team")`) inherits the team **from cricapi**. On an ESPN-only match `perf = espn_perf` (`:1959`) and every entry has `team == ""` → `short_of("") == ""` → `continue`.

**Consequence under ESPN-only: the silent-drop auto-add never fires. A known player who plays but sits in no squad slot is dropped with no row, no flag, no review.** The fix is one line — `team_map` is already fetched at `:1935` and is exactly `{norm(name): team displayName}`.

**Status: PROVEN by code. This is a blocker for the flip.**

### b2 — registry does not know them

`resolve_perf_pid` returns `None` → `unresolved` → `leftover` → emitted at `:2316-2319` with `in_squad="N"`, and a **Needs Review** row is appended at `:503-509` with `closest_squad` suggestion.

Operator types `New` (or the real name) in the `Correct? (Yes/New)` column. `wc_fps_to_csv.py:2900-2910` mints **`slug:<name>`** via `slugify` (`:2943`) — *even though ESPN handed us `athlete.id`, which is the cricinfo id.* `promote_new_players` (`:3331`) then upgrades `slug:`→`ci:` **only** on a human-typed bridge, and anything still on a placeholder is pushed to Needs Cricinfo ID at `:3384-3394`. Loop closes: 0 placeholders remain today.

**Note the emit-path gap:** `emit` at `:2248` uses `resolve_pid(name)` (name-only), **not** `resolve_perf_pid`. It only works because `resolve_perf_pid:326-330` teaches `ALIAS2PID` during matching — and that teaching is itself gated on `pid in PID2DISP` (`:324`). For an id the registry has never seen, nothing is learned and the row emits with a **blank Player ID**, unjoinable by the draft.

---

# (c) A substitute fielder credited a run-out

**This is the sharpest finding. Under ESPN-only the credit is silently deleted.**

The chain:

1. `espn_runouts` (`:879`) reads `rosters[].roster[].linescores[].statistics.batting.outDetails.fielders` and returns `[(name, athlete.id)]`. Correct, as measured — its docstring (`:893-895`) records 17/17 vs cricsheet including substitutes.
2. `parse_espn:1078-1086` credits them: `rp = get(nm)` → creates the perf entry, sets `espn_id`, `runouts += 1`, `dro += 1` if unassisted.
3. **`get(nm)` does not set `played`.** Compare `:1050` (`pb["played"] = True` for a batter) and `:1064` (`pw["played"] = True` for a bowler) — the run-out block at `:1080-1086` has no such assignment.
4. `espn_xi` (`:916`) is the only other setter, and it gates on `p.get("starter") or p.get("subbedIn")` (`:924`).
5. **`wc_fps_to_csv.py:1934`:** `espn_perf = {k: v for k, v in espn_perf.items() if v["played"]}` — the entry is **deleted before matching**.

So: a fielder who is in neither XI takes a run-out → ESPN names them → the bot credits them → line 1934 removes the row. **6 pts (assisted) or 12 pts (direct) vanish. No emitted row, no Needs Review row (`:502` requires `v.get("played")`), no Needs Cricinfo row, no anomaly.** Silent, and on the ESPN-only path there is no cricapi twin to backstop it.

Note the roster measurement above: ESPN rosters carry **exactly 22 entries, zero non-starters**, in all 6 cached events. So a genuine 12th man is not in the roster at all — line 1934 is the *only* gate they meet, and they always fail it.

If they *are* in the squad file: `resolve_pid(name)` on the squad row finds the pid, `assigned` claims the perf, `emit` scores it — field points land, `xi = R["xi"] if p["played"] else 0` (`:1171`) correctly gives no +4. That case is fine. Also cosmetic: `emit:2286` hardcodes the Played column to `"Y"` whenever `d` is non-None, so such a row would read Played=Y with xi=0.

**Status: PROVEN as code behavior. NOT OBSERVED in the sample** — 17/17 run-out fielders in the 5 cached events were starters. Frequency is UNCERTAIN; impact per occurrence is 6–12 FP with zero operator surface. Fix: set `played = True` in the run-out credit block, or exempt `runouts > 0` from the `:1934` filter.

---

# (d) A player whose cricinfo id is not in `crosswalk.json`

**The crosswalk is used as a WHITELIST for ESPN ids, not as a translator. This is the design fact that most constrains the flip.**

`build_registry.fold_ci` (`:303-306`):
```python
return x if x in PRIMARY else CI_ALT.get(x)      # PRIMARY = set(CS2CI.values())
```
`resolve_ci` branch (e) at `:341-344` locates the athlete by name, then does `ci = fold_ci(em.get("espn_id"))` and **returns nothing if `fold_ci` is None**. So a real, correct ESPN `athlete.id` that is absent from people.csv-derived `crosswalk.json` is **discarded**. `resolve_ci` returns `(None, None)` → `build_tour:409` mints `uncapped:<slug>` → `needs_cricinfo_pending.json` → Needs Cricinfo ID tab.

At scoring time the same gate exists in a different form. `resolve_perf_pid:321-331`:
```python
pid = f"ci:{str(eid).strip()}"
if pid in PID2DISP:   # ← gated on the REGISTRY, not the crosswalk
```
An id the registry has not seen falls through to `resolve_pid(name)` → almost certainly `None` → `unresolved` → `leftover` → **emitted with a blank Player ID**.

**Operator sees.** Only a Needs Review row (b2 path), and only if `played=True`. **Never a Needs Cricinfo ID row from the scoring path** — `NEEDS_CRICINFO.append` occurs at exactly three sites: `:2212` and `:2238`, both inside `if cs_path and not is_live:` (`:2188`), and `:3387` (the stale-placeholder sweep). **The Needs Cricinfo ID tab is fed by the cricsheet branch and by `build_registry`. Nothing on the ESPN path can reach it.** So this player is invisible in the identity tab until cricsheet posts, 1–5 days later.

**Operator does.** Answers `New` in Needs Review (mints `slug:`), then fills the id into Needs Cricinfo ID once a row appears. Two tabs, two actions, for a player whose correct id was in the ESPN payload from ball one.

**Self-heal.** Works — `read_needs_cricinfo` → bridge → `promote_new_players` → `ci:` — but only after cricsheet lands or after `promote_new_players:3387` sweeps the placeholder.

**Status: PROVEN.** For a genuine debutant not in cricsheet\'s people.csv this is the whole cost: we hold a verifiable cricinfo id and throw it away, then ask a human to retype it.

---

# What changes once cricapi is gone

## Becomes unnecessary — delete

| machinery | file:line | why it dies |
|---|---|---|
| **`load_bridges` + `read_ts_alias_map`** (auction `mlc-2026.ts`/`lpl-2026.ts`, draft `DISPLAY_NAME_MAP`) | `build_registry.py:122-154`, consumed `:326-329` | These exist to bridge *announced names* to *cricsheet DB spellings*. Both surviving feeds are id-carrying. |
| **Fuzzy DB branch of `resolve_ci`** + `best_match`/`score_pair`/`given_compatible`/`_initials_ok` on that path | `build_registry.py:69-119, 334-339` | Nothing left to guess for. |
| **Fuzzy fallback in `match_squad_to_perf`** (the whole `pairs`/`used_sq` block) | `wc_fps_to_csv.py:466-497` | Every feed row would carry `espn_id` or `cs_id`. |
| **`closest_squad`** + the Needs Review `New`/`Yes`/`No` flow + `slugify` | `:238-256`, `:2900-2920`, `:2943` | `slug:` should never be minted again — ESPN supplies the id. |
| **`promote_new_players`** | `:3331` | Its entire purpose is upgrading `slug:` off placeholders. Already 0 in flight. |
| **`ALIAS` / `AUTO_ALIASES` / Player-Aliases-tab plumbing, `add_sheet_aliases`** | `:268-281` | Name→pid tables exist for the nameless feed. |
| **`crosscheck`, L1 recon, `RECON_L1`, `L1_RUN_TOL`, `l1_col`** | `:1094-1106`, `:2253-2257` | One live feed; nothing to arbitrate. Matches `STREAMLINE_PLAN.md §3`. |
| **`_by_pid(api_perf)` / `capi_pid` / `capi_covers_match`** | `:1971` and callers | — |

## Must be kept

- **`crosswalk.json`** — still required, but its role inverts: it is the **cricsheet→cricinfo translator** (`CS2CI`, `resolve_perf_pid:304-315`). It must **stop** acting as a validity whitelist for ESPN ids (`fold_ci:303-306`).
- **`manual_ci_bridges.json` + `read_needs_cricinfo` + the Needs Cricinfo ID tab** — still the only channel for the cricsheet-spelling-vs-ESPN-id mismatch and for genuine crosswalk gaps. 112 entries earned.
- **`identity_splits.json`, `team_aliases.json`, `canon_team`/`team_key`** — team identity is still name-based on both feeds; unaffected.
- **`find_silent_drops`** — *more* necessary, not less (see b1).
- **The two merge guards in `read_needs_cricinfo`** (`:3466-3491`) — they defend against operator typos, not feed weakness.

## Must be fixed before the flip — ranked

1. **`wc_fps_to_csv.py:2001-2003` — ESPN perfs carry no team, so the silent-drop auto-add never fires.** `team_map` is already in hand at `:1935`. Blocker: case (b) fails open, silently. *(This is the RUNBOOK §2 shape #2: an absence read as a value.)*
2. **`wc_fps_to_csv.py:1934` + `:1080-1086` — a run-out-only fielder has `played=False` and is filtered out before matching.** 6–12 FP, zero surface. *(Shape #1: a guard applied to batters and bowlers but not to fielders.)*
3. **`build_registry.py:343-344` (`fold_ci` gating branch e).** ESPN\'s `athlete.id` **is** the cricinfo id — accept it directly when the crosswalk has no row, and record the crosswalk gap rather than discarding the id. Today the flip\'s headline benefit ("identity by construction, no fuzzy matching") is switched off for exactly the players who need it: debutants.
4. **Route ESPN-path identity gaps to Needs Cricinfo ID.** `NEEDS_CRICINFO.append` is unreachable outside `if cs_path and not is_live:` (`:2188`). With cricsheet as the only other feed, an unknown player is invisible in the identity tab for 1–5 days. *(Shape #3: written but never read — here, a tab reachable from only one of two feeds.)*
5. **`wc_fps_to_csv.py:2248` — `emit` resolves by name (`resolve_pid`), not by id (`resolve_perf_pid`).** It works only because matching side-effects `ALIAS2PID` at `:326-330`, itself gated on `pid in PID2DISP` (`:324`). Under ESPN-only, make `emit` id-first so an unknown-but-id-carrying player can never emit a blank Player ID.

**Honest summary:** ~60% of the identity machinery is cricapi-only scaffolding and can go. But three of the five items above are places where the code **currently discards an id ESPN already gave it** and falls back to the name path that is about to be deleted. Removing cricapi without fixing 1–3 first would make cases (b) and (c) *worse*, not better — they fail silently, and silence is exactly what the money rule cannot absorb.', '# How the Google Sheet changes when cricapi leaves scoring

Everything below is read from code. `FILE:LINE` given for each claim. **PROVEN** = read in source this session. **UNCERTAIN** = flagged explicitly.

---

## A. Every tab the bot writes (11)

| # | Tab | Written by | Fate after the flip |
|---|---|---|---|
| 1 | **`<per-tour points tab>`** (`GSHEET_TAB`, e.g. `WWC T20 POINTS`, `LPL 2026`) | `write_to_gsheet` `wc_fps_to_csv.py:3644` | **survives, 2 columns die, 3 change meaning** |
| 2 | **STATUS** | `write_status_tab` `wc_fps_to_csv.py:2689` | **mostly dead** — 3 of its 7 rows are cricapi quota |
| 3 | **Player Aliases** | `sync_player_aliases` `wc_fps_to_csv.py:3195`, header `:2842` | **survives, goes quiet** (near-zero new rows) |
| 4 | **Needs Review** | `write_review_tab` `wc_fps_to_csv.py:3225` | **survives, goes quiet** (permanent "matched cleanly 🎉" row) |
| 5 | **Identity Anomalies** | `write_anomaly_tab` `wc_fps_to_csv.py:3257` | **survives unchanged** |
| 6 | **Recon Review** | `write_recon_tab` `wc_fps_to_csv.py:3599` | **survives, halves** — L1 rows gone, S1/S2 headers must be rewritten |
| 7 | **Needs Cricinfo ID** | `wc_fps_to_csv.py:3541` **and** `tour_sync_finalize.py:99` | **survives unchanged, becomes the only identity tab** |
| 8 | **SETTLEMENT AUDIT** | `write_settlement_tab` `wc_fps_to_csv.py:3569` | **survives unchanged** — all 12 columns intact |
| 9 | **TOUR CONTROL** | `sync_tour_control` `wc_fps_to_csv.py:2511` | **survives, changes meaning** — no longer a quota gate |
| 10 | **TOUR INGEST REVIEW** | `tour_sync_finalize.py:70` | **survives unchanged** (already ESPN-centric) |
| 11 | **TOUR STATUS** | `tour_status.py:227` | **survives, 1 column becomes advisory** |

---

## B. Points tab — all 39 columns, BEFORE → AFTER

Column list is `wc_fps_to_csv.py:1873-1884`. (Note: the committed CSVs on disk — `lpl_2026_points.csv`, `the_hundred_mens_2026_points.csv` — carry only **37** columns, stopping at `Player Recon`; they predate `Recon State` / `Points Delta`. The code is the authority.)

| # | Column | Before (cricapi in) | After (ESPN only) | Verdict |
|---|---|---|---|---|
| 1–7 | Match, Date, Team, Player ID, Full Name, Role, Played | mixed feeds | ESPN/cricsheet | **unchanged** |
| 8–11 | Runs, Balls, 4s, 6s | cricapi base, ESPN fallback | ESPN | **unchanged meaning** |
| 12 | SR | computed `r/b` | same | **unchanged** |
| 13 | Dismissal | feed string | ESPN/cricsheet | **unchanged** |
| 14 | Overs | `balls/6` | same | **unchanged** |
| 15 | Maidens | ESPN-only already (`RECON_L1_SINGLE`, `:1605`) | ESPN-only | **unchanged** |
| 16 | Dots | ESPN-only; **blank** when `dots_final=False` (cricapi-only match, `:2287`) | always populated — the blank branch is unreachable | **improves; blank case dies** |
| 17–22 | Runs Conceded, Wickets, Econ, Catches, Stumpings, Run Outs | cricapi base | ESPN (run-outs from the SUMMARY payload) | **unchanged meaning** |
| 23–29 | Pts Bat/Bowl/Field/SR/Econ/XI, Fantasy Points | scorer output | same | **unchanged** |
| 30 | **Source** | 5 possible strings | **2 strings** | **changes — see C1** |
| 31 | In Squad List | squad join | same | **unchanged** |
| 32 | **Bat Order** | `d.get("bat_order")` `:2292`; set only in `parse_cricsheet` (`:728`) and `parse_match`=cricapi (`:1283`) | **goes blank on every pre-cricsheet match** — **no ESPN parser assigns `bat_order`** | ⚠ **REGRESSION — see C8** |
| 33 | **L1 Recon** | `⚠ <gaps> (cricapi/ESPN)` / `✓ clean` / `""` `:2277-2282` | `capi_pid` always empty ⇒ else-branch ⇒ **permanently `""`** | **DEAD — delete** |
| 34 | **L2 Recon** | cricsheet vs L1-reconciled cut `:2284-2300` | cricsheet vs frozen ESPN value | **survives; baseline definition sharpens** |
| 35 | **Match Status** | 2 values, 8 flag strings | 2 values, 5 flag strings | **survives — see C3** |
| 36 | **Recon Flag** | 8 strings | 5 strings | **vocabulary shrinks — see C3** |
| 37 | **Player Recon** | 4 markers | 3 markers | **`⏳ unreconciled` dies — see C4** |
| 38 | **Recon State** | 4 labels | 4 labels, 2 relabelled | **changes meaning — see C5** |
| 39 | Points Delta | settled-vs-live movement `_points_delta` | same | **unchanged** |

---

## C. The specific items you named

### C1. `Source` column strings — `wc_fps_to_csv.py:1949-1968`, `:2361`

| Before (verbatim) | After |
|---|---|
| `cricsheet · official` | **survives verbatim** |
| `cricapi + ESPN dots/XI · ⏳ provisional (dots unverified, awaiting cricsheet)` | **dead** — becomes the ESPN string |
| `ESPN scorecard (cricapi empty) · ⏳ provisional (dots unverified, awaiting cricsheet)` | **survives, reworded** → drop `(cricapi empty)`; suggest `ESPN scorecard · ⏳ provisional (awaiting cricsheet)` |
| `cricapi · limited (no dots/XI — ESPN unavailable) · ⏳ provisional (awaiting cricsheet)` | **DEAD** — the `else: perf = api_perf` branch (`:1966-1968`) is unreachable; no-ESPN now means the no-data guard (`:1943-1947`) skips the match |
| `ESPN announced XI (toss)` (+ toss detail) `:2361` | **survives unchanged** |
| `· super-over excl` suffix | **survives unchanged** |

Net: the Source column goes from 5 strings to **2** (plus the toss/pre-match string). Important for the operator: `(cricapi empty)` currently reads as a *warning*; after the flip it would be on 100% of rows and must be removed or it trains him to ignore it.

### C2. `Recon Review` — the `S1` / `S2` headers, `wc_fps_to_csv.py:3608-3615`

Current header (verbatim):
```
["Tour","Match","Date","Player ID","Full Name","Param",
 "S1 = cricapi (L1) / held provisional (L2)",
 "S2 = ESPN (L1) / OFFICIAL cricsheet (L2)",
 "Correct Value","Manual Value","Status","Match Key"]
```
Both headers are **dual-purpose today** (L1 meaning ∥ L2 meaning). After the flip only the right half of each survives:
- `S1 = cricapi (L1) / held provisional (L2)` → **`S1 = held provisional (frozen ESPN value)`**
- `S2 = ESPN (L1) / OFFICIAL cricsheet (L2)` → **`S2 = OFFICIAL cricsheet`**

Other Recon Review changes:
- `Param` column: today it carries an L1 field name (`runs`/`wkts`/`4s`/`6s` from `RECON_L1 = ["r","w","4s","6s"]`, `:1358`) **or** the literal `"L2"` (`:2262`). After the flip **every row\'s Param is `L2`** → the column becomes constant, and the field detail moves into the S1/S2 cells. Candidate for deletion.
- `status_text` (`:3613-3615`): `"player": "⚠ pick a value"` is the L1 tier → **dead**. `"l2"` and `"id"` survive.
- `build_recon_rows` (`:1755`) — the whole function is L1-only (it iterates `RECON_L1` over `capi_pid` vs `espn_pid`) → **dead code**.
- ⚠ **Correctness item:** `_resolve_override_value` (`:1673-1680`) resolves `S1` by reading `capi_pid[...]`. With cricapi gone that dict is empty, so it silently falls through to `o.get("value")`. It happens to behave, but the S1 semantics must be **explicitly rewritten to read the frozen settlement record** (`registry/settlement_snapshots.json` / SETTLEMENT AUDIT) — per CLAUDE.md\'s locked rule "READ the baseline from the frozen record; never recompute". Leaving it implicit is exactly the "absence read as a value" bug class the RUNBOOK names.
- Volume: the STREAMLINE_PLAN estimate holds — **hundreds of L1 rows → ~6 L2 rows/week**.

### C3. `Match Status` + `Recon Flag` values — `classify_match_status`, `wc_fps_to_csv.py:1620-1666`

`Match Status` itself keeps **exactly two values**: `COMPLETED` and `COMPLETED_FLAGGED` (plus the forced `LIVE` at `:2162`). The vocabulary change is entirely in `Recon Flag`:

| Recon Flag (verbatim) | Trigger | After |
|---|---|---|
| `""` (clean COMPLETED) | cricsheet posted, agrees | **survives** |
| `⚠ identity unresolved on official card` | `id_break` | **survives** |
| `⚠ official revision pending` | `l2_dirty` | **survives** |
| `⏳ N players scored without a dot-ball source` / `⚠ …` | `unsourced` | **survives — becomes the main LIVE-holding reason** |
| `⚠ unverified — single feed (cricapi only)` | `not espn_present` | **DEAD** — no ESPN now means no match at all |
| **`⚠ unverified — single feed (ESPN only, cricapi had no card)`** | `not capi_present` | ⚠ **MUST BE DELETED.** `capi_present=bool(capi_pid)` at `:2236` will be `False` on **every match**, so this flag would fire on 100% of rows and, being a `COMPLETED_FLAGGED`, would make the RUNBOOK §5 settle-check fail everywhere. **This is the single highest-risk line for the flip.** |
| `⏳/⚠ pending recon approval (N players)` | `unresolved` (L1 gaps) | **DEAD** — no L1 |
| `🔴 in progress` (`:2162`) | live match | **survives** |

The red-fill highlight in `write_to_gsheet` (`:3665-3676`) keys on `COMPLETED_FLAGGED` + `"revision" in Recon Flag` → **survives unchanged**, and gets *cleaner*, because the two single-feed flags stop competing with it.

### C4. `Player Recon` values — `player_recon_markers`, `wc_fps_to_csv.py:1730-1740`, `:2155`, `:2158`

| Marker | After |
|---|---|
| `⏳ unreconciled` (unresolved L1) | **DEAD** |
| `⚠ official revision` (unapproved L2) | **survives** |
| `⛔ no dot-ball source` (`:2155`) | **survives** |
| `⛔ identity unresolved` (`:2158`) | **survives** |

Consumed by the app at `wwc-draft/lib/points.ts:733` and `:907` — no app change needed, the string just stops appearing.

### C5. `Recon State` values — `classify_recon_state`, `wc_fps_to_csv.py:1607-1626`

The **column survives and stays the second independent axis**, but two of its four labels are now misnamed:

| Value / label | Before | After |
|---|---|---|
| `L1_OPEN` → `⏳ L1 recon open` | feeds disagree **or** data unconsumed | Only the *unconsumed* half remains reachable (`unresolved` is always empty). **Rename**: `DATA_INCOMPLETE` / `⏳ data incomplete` |
| `L1_DONE` → `✅ L1 recon done` | L1 arbitrated, base frozen | now means "ESPN complete + frozen, cricsheet not posted". **Rename**: `PROVISIONAL_FROZEN` / `⏳ awaiting cricsheet` |
| `L2_PENDING` → `🔵 L2 recon pending` | — | **survives verbatim** |
| `L2_DONE` → `✅ L2 recon done` | — | **survives verbatim** |
| `🔴 in progress` (live override, `:2163`) | — | **survives** |

Keeping the literal string `✅ L1 recon done` after the flip would be actively misleading — it implies an arbitration that no longer happens.

### C6. `SETTLEMENT AUDIT` — `wc_fps_to_csv.py:3578`

```
["Match Key","Tour","Match","Date","Team","Player ID","Full Name",
 "Settled Points","Settled Status","Settled Source","Frozen At","Provenance"]
```
**All 12 columns survive unchanged.** Only the *content* of `Settled Source` narrows to the 2 strings in C1. The tab stays write-once, stays read-only (no `Correct Value` by design, `:3572`), and the draft app\'s join on Match Key + Player ID (`lib/points.ts:853-855`) is untouched.

One open item carries over unchanged from RUNBOOK §7: the baseline still freezes on any `COMPLETED`/`COMPLETED_FLAGGED` publish (`:2294`, `:2306`), not at the locked L1-done transition. **The flip makes this easier, not harder** — with L1 gone, "L1 done" and "first COMPLETED publish" become the same moment, so the known defect resolves itself by construction. (**UNCERTAIN**: I did not trace `record_settlement` end-to-end to confirm no other trigger exists.)

### C7. `Needs Cricinfo ID` — `wc_fps_to_csv.py:3541` and `tour_sync_finalize.py:121`

```
["player","current_pid","tour","team","closest_guess","cricinfo_id_FILL_HERE"]
```
**Survives completely unchanged, and becomes the only identity tab in the system.** Both writers use the identical header, and it\'s append-only + deduped on `current_pid` (`tour_sync_finalize.py:127`), so no migration is needed.

Its *inflow* drops sharply: ESPN\'s `athlete.id` **is** the cricinfo id (`build_registry.py:336`), so an ESPN-sourced player arrives pre-identified. Remaining sources of rows: (a) a cricsheet official-card row that resolves to no pid (`:2245-2255`), (b) an ESPN athlete id genuinely absent from `crosswalk.json`. That is the loop STREAMLINE_PLAN §5 describes, and it is already built.

### C8. ⚠ `Bat Order` — the one regression I found

`Bat Order` is written from `d.get("bat_order")` (`:2292`). Grepping every assignment of `bat_order` in `wc_fps_to_csv.py` gives exactly three sites: `merge_perf` (`:398`, pass-through), `parse_cricsheet` (`:672`, `:728-729`), and `parse_match` — **the cricapi parser** (`:1283-1284`). **No ESPN parser assigns it.** So today, on an ESPN-only match, `Bat Order` is already blank; after the flip it is blank on **every match until cricsheet posts**.

It is also in `SETTLED_FIELDS` (`:2770`), so it participates in the settlement diff — a bat_order appearing at L2 could register as movement. **PROVEN** by grep; **UNCERTAIN** what the draft board does with a blank bat order (CLAUDE.md notes a related failure mode: "the board silently orders by seed instead of the scorecard"). ESPN\'s summary rosters do carry batting order positionally, so this is a small parser addition — but it must go on the pre-flip list.

### C9. `TOUR CONTROL` — `wc_fps_to_csv.py:2511-2560`

```
["Tour","Tab","cricapi_series","Poll cricapi? (yes/no)","Notes"]
```
The tab **survives and is still the master on/off switch**, but its *rationale* inverts. Today it exists to ration a 100/day cricapi quota (docstring `:2513-2520`); ESPN is keyless, so there is no budget to protect. It remains valuable as the human "don\'t score this tour yet" gate.

Required edits, and a trap:
- Column C `cricapi_series` → `espn_series`. **Safe**: lookup is `hdr.index("cricapi_series") if present else 2` (`:2534`) — it falls back positionally.
- Column D `Poll cricapi? (yes/no)` → e.g. `Score this tour? (yes/no)`. **Trap**: the detector is `h.lower().startswith("poll")` (`:2535`). Renaming away from "Poll" drops it to the positional fallback `3` — still correct **only** if the column stays 4th. Either keep a "Poll…" prefix or fix the detector.
- Bigger structural point: `run_tour` takes its **fixture list from cricapi `series_info`** (`:1791-1795`) and **hard-exits** on failure (`sys.exit`, `:1795-1797`), and `WC_SERIES = tour["cricapi_series"]` (`:1770`). Per CLAUDE.md, an ESPN-added tour with `cricapi_series: ""` **is not scored at all**. So **cricapi cannot leave the sheet entirely until fixture discovery moves to the ESPN scoreboard** — that is STREAMLINE_PLAN step 5, and until it lands, TOUR CONTROL keeps a live `cricapi_series` column. **This is the load-bearing dependency**; everything else in this document is cosmetic by comparison.

### C10. `TOUR STATUS` — `tour_status.py:100-104`

```
["Tour","Fmt/Gender","Bot tours.json","espn_series","cricapi_series","Squads (bot)",
 "Draft matches","Draft players","Draft espn-series","Registry mirror",
 "Points tab (sheet)","Sheet→draft TEAM","Sheet→draft PID","Verdict / gaps"]
```
13 of 14 columns **survive unchanged**. `cricapi_series` **changes meaning**: it stops being a scoring prerequisite and becomes an informational/discovery field — and `espn_series` becomes the column whose blankness is a **blocker**. Column A stays the keyless "type a tour name here" entry point (`tour_sync.py:657`, `--from-status-sheet`), which is unaffected.

### C11. `Needs Review` and `Identity Anomalies`

- **Needs Review** (`["Tour","Team","Feed Name","Closest Match","Role","Correct? (Yes/New)"]`, `:3234`) — **structurally survives, practically empties.** Its input is feed names the squad-anchored matcher couldn\'t place. ESPN names arrive with an `athlete.id`; cricsheet rows resolve by person id (`resolve_perf_pid`/`CS2PID`) and unresolvable ones route to Needs Cricinfo ID, not here (CLAUDE.md rule E). Expect the permanent placeholder row `["—","","All players matched cleanly 🎉","","",""]` (`:3243`). **Do not delete it** — the squad-file matching path in `build_registry` is separate and can still surface rows. **UNCERTAIN**: I did not trace every `REVIEW.append` site.
- **Player Aliases** (`["Feed Name","Correct Player","Source"]`, `:2842`) — same story; append-only store, goes quiet, keep it.
- **Identity Anomalies** (`["Tour","Type","Player ID","Display","Players / Names Involved","Bot Finding","Different players? (Yes/No)","Status"]`, `:3268`) — **fully survives unchanged**. Merges/split-audits are registry-level, not feed-level.

### C12. `STATUS` — `wc_fps_to_csv.py:2689-2715`

Rows: `updated_utc`, `keys`, `mode`, and — only `if API_QUOTA` (`:2701`) — `hits_used`, `hits_limit`, `hits_left`.

| Row | After |
|---|---|
| `updated_utc` | **survives** |
| `mode` (full / frequent / on-demand) | **survives** |
| `keys` | **dead once discovery leaves cricapi** |
| `hits_used` / `hits_limit` / `hits_left` | **dead** — and they vanish silently (the `if API_QUOTA` guard), so the draft\'s "hits left today" gauge (comment `wc_fps_to_csv.py:63`) goes blank rather than erroring. **UNCERTAIN** — my grep of the draft for the STATUS consumer errored on a shell glob and I did not retry within budget; the app-side gauge should be removed or relabelled deliberately, not left to blank out. |

The whole *reason* for the `frequent` / `on-demand` / `full` mode split was cricapi quota (per the points-bot quota-fix work). With ESPN keyless, those modes can collapse — but that is a bot-architecture change, not a sheet change.

---

## D. Pre-flip checklist implied by the above (sheet-side only)

1. **Delete the `not capi_present` branch** (`:1663`) — otherwise every match publishes `COMPLETED_FLAGGED` and RUNBOOK §5 fails universally. **Highest risk.**
2. **Add `bat_order` to the ESPN parser** (C8) — currently a silent blank.
3. **Rewrite `S1` semantics in `_resolve_override_value`** (`:1673-1680`) to read the frozen settlement record explicitly.
4. **Relabel `L1_OPEN` / `L1_DONE`** (`RECON_STATE_LABEL`, `:1626`).
5. **Drop the `L1 Recon` column** — **PROVEN safe**: grep of `wwc-draft/lib` + `wwc-draft/app` for `L1 Recon` returns **zero** hits, while `L2 Recon`, `Player Recon`, `Match Status`, `Recon Flag`, `Recon State`, `Points Delta` are all read in `lib/points.ts`.
6. **Rewrite the two Recon Review S1/S2 headers**; consider dropping `Param`.
7. **Reword the `Source` strings**; delete the `cricapi · limited` branch.
8. **Do not remove `cricapi_series` from TOUR CONTROL / `tours.json`** until fixture discovery moves to ESPN — the bot hard-exits without it (`:1795`).

---

## E. The operator\'s weekly routine — plain English, for handing to a friend

> **What the sheet is.** One tab per tournament holds every player\'s fantasy points for every match. A handful of small tabs around it are the control panel.
>
> **What changed.** We used to read live scores from two services at once — cricapi and ESPN — and whenever they disagreed, a human had to pick a winner. That was "L1 recon", and it was most of the work: hundreds of rows, and it never actually made the money safer, because nobody settles before the official data lands anyway. We now read **only ESPN** while a match is in play, and we still check everything against the **official cricsheet** data when it arrives a day or two later. Nothing about how money is settled changes.
>
> **Your week, start to finish:**
>
> 1. **Most days: do nothing.** The bot runs itself every 4 hours. A match in progress shows `🔴 in progress`. A finished match shows `⏳ awaiting cricsheet` — that is normal and needs no action. **Resist the urge to "fix" it.**
>
> 2. **Check `Needs Cricinfo ID` — this is the only tab that ever needs research, and it will be nearly always empty.** If a row appears, the last column is pre-filled with a suggested ID and a link. You confirm it. That\'s it. (This used to fire often because cricapi gave us names only; ESPN gives us the player\'s real ID, so this tab now runs almost dry.)
>
> 3. **Check `Recon Review` — expect roughly six rows a week, only after official data lands.** Each row says "the official scorecard says X, what you settled on says Y." Pick **S2** to accept the official number, or **S1** to keep what you settled. Either answer counts as decided and the row disappears next run. **Only leaving it blank keeps the match unsettled.** The old flood of rows where two live feeds bickered over a boundary is gone entirely.
>
> 4. **Before you pay anyone out**, open `/audit` in the draft app and run the 5-point check in `RUNBOOK.md` §5. Unchanged: the match must read `cricsheet · official`, must not say `⏳ recon open` or `⚠ revised`, and must have a settled baseline. **If it hasn\'t been checked against cricsheet, don\'t settle it.** That rule was always what kept the money safe — not the second live feed.
>
> 5. **Adding a tournament:** type its name into column A of `TOUR STATUS` or `TOUR CONTROL`, then approve it in `TOUR CONTROL`. Watch `TOUR INGEST REVIEW` for the verdict. If it complains about `espn_series`, paste the number from the tournament\'s espncricinfo URL.
>
> **The one-line version:** you now answer *one* question instead of two — not "which of these two live feeds do I believe?" but only "the official record disagrees with what we settled; which stands?" That question comes up about six times a week, always after the fact, and always with the official number sitting right next to yours.

---

**Not verified this session (stated so you don\'t assume otherwise):** the draft app\'s STATUS/"hits left" consumer (C12); every `REVIEW.append` call site (C11); the full set of `record_settlement` triggers (C6). Everything else above is read from the source lines cited.', '## 1. Completed matches per tour, and how much cricapi is in them

**Live sheet, read today via gviz** (11 point tabs, sheet `1um6Scv2MbFzRxTVUJsWxMxX4oxTDojJmujcFBJGDlyg`; tab list from `/Users/nishant-singodia/wwc-points-bot/tours.json`). **174 completed matches.** Column = the *currently published* `Source`:

| tour | completed | cricsheet | ESPN-only | cricapi-mix | settlement baseline? |
|---|---|---|---|---|---|
| WWC T20 | 33 | 33 | 0 | 0 | **0/33** |
| MLC 2026 | 33 | 30 | 1 | 2 | **0/33** |
| Hundred Men | 32 | 23 | 9 | 0 | 30/32 |
| Hundred Women | 31 | 22 | 9 | 0 | 29/31 |
| LPL 2026 | 24 | 22 | 2 | 0 | 24/24 |
| NZ v WI M ODI | 5 | 1 | 3 | 1 | **0/5** |
| IND v ENG T20 | 5 | 5 | 0 | 0 | **0/5** |
| AUS v BAN / ZIM v IND | 3 + 3 | 6 | 0 | 0 | 0/3, 2/3 |
| IRE v WI W ODI | 3 | 2 | 0 | 1 | **0/3** |
| IND v IRE T20 | 2 | 2 | 0 | 0 | **0/2** |
| **total** | **174** | **146** | **24** | **4** | **85/174** |

**Cricapi in the mix at the moment money froze** — `registry/settlement_snapshots.json`, per-match modal `source` at freeze (2989 rows, 83 matches, 3 tours only):

| tour | frozen on cricsheet | frozen on **cricapi-mix** | frozen on ESPN-only |
|---|---|---|---|
| LPL 2026 | 14 | **7** | 3 |
| Hundred Men | 12 | **11** | 7 |
| Hundred Women | 12 | **10** | 7 |
| **total (83)** | 38 | **28** | 17 |

PROVEN: **28 of the 83 baselined matches were settled on a number cricapi helped produce**; 17 more on ESPN-only.

**The bigger hole:** **89 of 174 completed matches have no settlement baseline row at all** (all of WWC and MLC, all bilaterals) — the baseline only starts 2026-07-22. On top of that, **1012 of 2989 snapshot rows carry `provenance: "unknown"`** (the 2026-07-29 seed), which `/audit` treats as no baseline. Genuinely provable coverage is ~1900 rows / ~55 matches.

## 2. What a rescore would actually change

**The rescore-candidate set is 28 matches, not 174.** 146/174 already publish cricsheet\'s numbers; keeping cricsheet as the L2 arbiter reproduces those numbers exactly, because ESPN\'s L1 value is not what is on the sheet. The shipped ESPN fixes are invisible there.

Fix timeline (`git log`): `855379e` 11 Aug (run-outs from summary, 0→20), `4558579` 12 Aug (dots/no-ball/pagination + the `non_boundary` ground-truth bug), `785dec8` 12 Aug (refuse partial fetch).

**Post-fix already** — the 18 Aug-dated ESPN-only Hundred/LPL matches carry run-outs on the live sheet today (LPL M23 = 2, HndM M24/25/26 = 2 each, HndW M27/M29 = 4 each). The in-place sheet rewrite already propagated the fix. **No backfill needed.** They also *cannot* be settled: cricsheet has posted nothing for the Hundred after 2026-08-06 (`cs_hnd`: 367 dated files, only 2 at ≥ 08-06, both 08-06).

**Genuinely pre-fix, ESPN-sourced, still wrong** — 5 matches, all with whole-match `Run Outs = 0`:

PROVEN against the already-extracted `cs_odi` archives:
- `1538626` (sheet **NZ v WI M3**, 17 Jul): cricsheet has **0 run-outs** → sheet is correct, **no change**.
- `1538627` (sheet **M4**, 19 Jul): `MW Forde` run out by `MJ Santner` *alone* → direct hit → **+12 FP** to Santner. Sheet has 0.
- `1538628` (sheet **M5**, 21 Jul): `MJ Santner` (Hope + Seales), `KDC Clarke` (Greaves + Hope) → 4 assisted credits × 6 → **+24 FP** (Hope +12, Seales +6, Greaves +6). Sheet has 0.
- Pipeline sanity check: sheet M2 (cricapi-mix) shows 3 run-out credits; `1538625` has 2 run-outs / 3 fielder credits — **exact**.

UNCERTAIN: **MLC M33** (17 Jul, ESPN-only, 1451 FP) and **MLC M32** (16 Jul, cricapi-mix) also show 0 run-outs. No MLC cricsheet archive is extracted locally, so I did not verify. At the measured ~1.1 run-outs/T20 the expectation is ~8–12 FP each.

**Total proven defect available to fix: +36 FP across 2 matches / 4 players. Estimated ceiling including MLC: ~+60 FP across 4 matches.**

**Magnitude if you rescored cricsheet-verified history from ESPN anyway:** this week\'s measurements already bound it — ODI 10/10 exact, LPL T20 2 runs in 6138 (0.03%), Hundred M balls −2, Hundred W one delivery. Over 18 LPL matches that is ~2 FP total, and it is a *regression* — you would be replacing the official arbiter with a feed measured 2 runs worse.

## 3. Effect on the write-once baseline and `/audit`

**The baseline is safe from rewrite.** `wc_fps_to_csv.py:2774` `record_settlement()` opens with `if not pid or (match_key, pid) in SETTLEMENTS: return` — write-once is enforced by key presence, so a rescore physically cannot touch it.

**But that is exactly why a backfill surfaces as re-settle work.** The chain:
- `wc_fps_to_csv.py:2809` `_points_delta()` → signed `Points Delta` column.
- `wwc-draft/lib/settlement-audit.ts:149` `groupFor()`: `if (reason === "NO_BASELINE") return "NO_BASELINE"; if (marker) return "PENDING"; if (reason === "IDENTITY_BREAK") return "PENDING"; return delta !== 0 ? "CHANGED" : "CLEAN";`
- `app/audit/page.tsx:154` `const changed = rows.filter(r => r.audit.changed)` drives the **"Result changed"** tile; `:156` drives **"Results flipped"** off `c.winnerChanged`; `:318` labels the bucket *"⚠ L2 recon done — changed vs L1 settlement"* and `:323` *"Already applied: these differ from what the contest was settled on."*

So: **any backfill that moves a row on a baselined match lands in `CHANGED` — the explicit re-settle list — and if it flips a head-to-head it also breaks RUNBOOK §5 point 4 ("Results flipped must be 0").**

**Current drift, measured today, before anyone touches history.** The bot\'s own `Points Delta` column (authoritative): **269 rows, 3809 abs FP, 63 matches** (LPL 97/1771/23, HndM 87/921/20, HndW 85/1117/20). My independent snapshot↔sheet join reproduces this at 272 rows / 63 matches, so the join is validated. Split by `/audit`\'s real bucket logic:

| bucket | rows | abs FP | matches |
|---|---|---|---|
| **CHANGED** (the re-settle list) | 204 | **2508** | **43** |
| PENDING (bot holding settled value) | 17 | 126 | 7 |
| NO_BASELINE (`provenance: "unknown"`) | 1012 | 1389 | — |
| CLEAN | 1702 | 0 | — |

Top movers are **identity, not scoring**: Ashleigh Gardner 181→0 (WTMSG v WTTRR, 31 Jul), Gleeson 4→145 (MTMILO v MTSUNL, 21 Jul — the Hundred bowler-balls bug), Milan Ratnayake 134→0 with Tharindu Rathnayake 0→134 in the same match, Moeen Ali 124→0, Liam Dawson 122→0, Dasun Shanaka 111→0.

**Two further costs of a broad backfill:**
1. `registry/recon_overrides.json` holds **935 approved overrides across 70 match_keys** — 871 `l2/S2`, 49 `player/S2`, 10 `player/S1`, 3 `Manual`, 2 `l2/S1`. The **15 S1/Manual** decisions were adjudications *against the old L1 value*; a rescore re-derives that side and those 15 human answers become stale.
2. **The asymmetry that should decide this:** rescoring a no-baseline match is *invisible*. `settlement-audit.ts` `reasonFor()` returns `NO_BASELINE` on `provenance === "unknown"` **whether or not the number moved**, and `app/audit/page.tsx:367` prints the same "completed before the settlement baseline existed… not proof nothing moved" line before and after. A backfill over WWC (33) + MLC (33) + the bilaterals (21) moves money numbers with zero record, by construction.

## Recommendation — **NONE for cricsheet-verified; a 5-match narrow backfill only, and only after sealing the baseline gap**

**Do not rescore the 146 cricsheet matches.** You would trade the official arbiter for a feed measured 2 runs worse over 6138, buy ~0.1 FP/match of "improvement", and add matches to a re-settle list that already stands at 43. Cost real, benefit negative.

**Do not do a blanket backfill.** The one thing that makes the system defensible for real money is that `/audit` can prove a number didn\'t move. A 174-match rescore turns that page red on the baselined half and silent on the unbaselined half — the worst of both.

**Order of work:**

1. **Nothing with an existing baseline gets rescored.** Full stop.
2. **Seal the hole first.** Back-seed a baseline for the 89 no-baseline matches and the 1012 `provenance: "unknown"` rows from the *current* sheet, tagged e.g. `provenance: "pre-backfill"`, so any later move produces a diff instead of a silent write. Without this, step 3 is unauditable.
3. **Then backfill exactly 5 matches**: NZ v WI ODI M3/M4/M5 and MLC M32/M33. Expected **+36 FP proven**, ~+20 FP uncertain, across ~6 players. All 5 currently have *zero* baseline, so this creates **0 CHANGED rows and 0 re-settlement with your friend** — provided step 2 runs first so it is on the record.
4. **The 18 Aug Hundred/LPL ESPN-only matches need nothing** — already post-fix, and unsettleable anyway until cricsheet posts past 6 Aug.
5. **The real money question is the 43 matches / 2508 FP already sitting in `CHANGED`** — that exists today, independent of the cricapi flip, and it is mostly identity, not feed choice. That is the list to work.

**Money framing:** a full backfill risks re-opening up to 63 settled matches to recover ~2 FP of measured scoring error, while the narrow backfill recovers 36 proven FP at zero re-settlement cost. The asymmetry is not close.']}, 'workflowProgress': [{'type': 'workflow_phase', 'index': 1, 'title': 'Investigate'}, {'type': 'workflow_phase', 'index': 2, 'title': 'Verify'}, {'type': 'workflow_phase', 'index': 3, 'title': 'Plan'}, {'type': 'workflow_agent', 'index': 1, 'label': 'field-completeness', 'phaseIndex': 1, 'phaseTitle': 'Investigate', 'agentId': 'a1541bf15c3b0bff9', 'model': 'claude-opus-5[1m]', 'state': 'done', 'startedAt': 1786590544601, 'queuedAt': 1786590544557, 'attempt': 1, 'lastToolName': 'Bash', 'lastToolSummary': 'cd /private/tmp/claude-501/-Users-nishant-singodia/f36700e2…', 'promptPreview': 'CONTEXT. Read /Users/nishant-singodia/wwc-points-bot/CLAUDE.md, RUNBOOK.md and STREAMLINE_PLAN.md FIRST.
Repos: BOT=/Users/nishant-singodia/wwc-points-bot (wc_fps_to_csv.py is the scorer), APP=/Users/nishant-singodia/wwc-draft (Next.js draft app).

THE DECISION: remove cricapi from SCORING entirely; ESPN (site.api.espn.com) becomes the only live
source, cricsheet stays as the official arbiter at L…', 'lastProgressAt': 1786591015124, 'tokens': 76696, 'toolCalls': 22, 'durationMs': 470522, 'resultPreview': '## VERDICT

**ESPN supplies every input `score()` consumes — nothing is missing.** But two fields are currently taken from the *wrong ESPN payload*, and one is not taken at all. Both are fixable inside `summary`, which is already fetched under the same cache key (zero extra requests).

---

## 1. Every field the scorers read

`score()` dispatch `/Users/nishant-singodia/wwc-points-bot/wc_fps_to_csv…'}, {'type': 'workflow_agent', 'index': 2, 'label': 'code-surgery', 'phaseIndex': 1, 'phaseTitle': 'Investigate', 'agentId': 'a19af404b18880a6c', 'model': 'claude-opus-5[1m]', 'state': 'done', 'startedAt': 1786590544602, 'queuedAt': 1786590544557, 'attempt': 1, 'lastToolName': 'Bash', 'lastToolSummary': "cd /Users/nishant-singodia/wwc-points-bot && sed -n '3040,3…", 'promptPreview': 'CONTEXT. Read /Users/nishant-singodia/wwc-points-bot/CLAUDE.md, RUNBOOK.md and STREAMLINE_PLAN.md FIRST.
Repos: BOT=/Users/nishant-singodia/wwc-points-bot (wc_fps_to_csv.py is the scorer), APP=/Users/nishant-singodia/wwc-draft (Next.js draft app).

THE DECISION: remove cricapi from SCORING entirely; ESPN (site.api.espn.com) becomes the only live
source, cricsheet stays as the official arbiter at L…', 'lastProgressAt': 1786590829894, 'tokens': 95699, 'toolCalls': 22, 'durationMs': 285292, 'resultPreview': '# Removing cricapi from SCORING — exact change plan

Read: `CLAUDE.md`, `RUNBOOK.md`, `STREAMLINE_PLAN.md`. All line refs are `/Users/nishant-singodia/wwc-points-bot/wc_fps_to_csv.py` unless stated. Verdicts marked **PROVEN** were confirmed by reading the code; **UNCERTAIN** = inferred.

---

## 1. The complete cricapi surface in the scorer

**PROVEN — there are exactly two cricapi calls in the wh…'}, {'type': 'workflow_agent', 'index': 3, 'label': 'new-player-flow', 'phaseIndex': 1, 'phaseTitle': 'Investigate', 'agentId': 'af4cd3faae8a402e4', 'model': 'claude-opus-5[1m]', 'state': 'done', 'startedAt': 1786590544603, 'queuedAt': 1786590544557, 'attempt': 1, 'lastToolName': 'Bash', 'lastToolSummary': "cd /Users/nishant-singodia/wwc-points-bot && python3 - <<'E…", 'promptPreview': 'CONTEXT. Read /Users/nishant-singodia/wwc-points-bot/CLAUDE.md, RUNBOOK.md and STREAMLINE_PLAN.md FIRST.
Repos: BOT=/Users/nishant-singodia/wwc-points-bot (wc_fps_to_csv.py is the scorer), APP=/Users/nishant-singodia/wwc-draft (Next.js draft app).

THE DECISION: remove cricapi from SCORING entirely; ESPN (site.api.espn.com) becomes the only live
source, cricsheet stays as the official arbiter at L…', 'lastProgressAt': 1786590840894, 'tokens': 116176, 'toolCalls': 27, 'durationMs': 296291, 'resultPreview': 'I have the complete picture. Writing the trace now.

**Read first:** `/Users/nishant-singodia/wwc-points-bot/CLAUDE.md`, `RUNBOOK.md`, `STREAMLINE_PLAN.md`. Then walked `build_registry.py` (498 L, full), `wc_fps_to_csv.py` (targeted: 283–357, 425–512, 665–673, 855–1092, 1492–1600, 1925–2020, 2118–2336, 2870–3018, 3320–3568), `tour_sync_finalize.py:99–140`. Empirical checks used **cached** ESPN sum…'}, {'type': 'workflow_agent', 'index': 4, 'label': 'sheet-behaviour', 'phaseIndex': 1, 'phaseTitle': 'Investigate', 'agentId': 'a436fab519a0283bd', 'model': 'claude-opus-5[1m]', 'state': 'done', 'startedAt': 1786590544603, 'queuedAt': 1786590544557, 'attempt': 1, 'lastToolName': 'Bash', 'lastToolSummary': "cd /Users/nishant-singodia/wwc-points-bot && awk 'NR>=600 &…", 'promptPreview': 'CONTEXT. Read /Users/nishant-singodia/wwc-points-bot/CLAUDE.md, RUNBOOK.md and STREAMLINE_PLAN.md FIRST.
Repos: BOT=/Users/nishant-singodia/wwc-points-bot (wc_fps_to_csv.py is the scorer), APP=/Users/nishant-singodia/wwc-draft (Next.js draft app).

THE DECISION: remove cricapi from SCORING entirely; ESPN (site.api.espn.com) becomes the only live
source, cricsheet stays as the official arbiter at L…', 'lastProgressAt': 1786590829730, 'tokens': 80806, 'toolCalls': 25, 'durationMs': 285127, 'resultPreview': '# How the Google Sheet changes when cricapi leaves scoring

Everything below is read from code. `FILE:LINE` given for each claim. **PROVEN** = read in source this session. **UNCERTAIN** = flagged explicitly.

---

## A. Every tab the bot writes (11)

| # | Tab | Written by | Fate after the flip |
|---|---|---|---|
| 1 | **`<per-tour points tab>`** (`GSHEET_TAB`, e.g. `WWC T20 POINTS`, `LPL 2026`) …'}, {'type': 'workflow_agent', 'index': 5, 'label': 'backfill', 'phaseIndex': 1, 'phaseTitle': 'Investigate', 'agentId': 'a31a4676e4a06dc8d', 'model': 'claude-opus-5[1m]', 'state': 'done', 'startedAt': 1786590544603, 'queuedAt': 1786590544557, 'attempt': 1, 'lastToolName': 'Bash', 'lastToolSummary': 'cd /private/tmp/claude-501/-Users-nishant-singodia/f36700e2…', 'promptPreview': 'CONTEXT. Read /Users/nishant-singodia/wwc-points-bot/CLAUDE.md, RUNBOOK.md and STREAMLINE_PLAN.md FIRST.
Repos: BOT=/Users/nishant-singodia/wwc-points-bot (wc_fps_to_csv.py is the scorer), APP=/Users/nishant-singodia/wwc-draft (Next.js draft app).

THE DECISION: remove cricapi from SCORING entirely; ESPN (site.api.espn.com) becomes the only live
source, cricsheet stays as the official arbiter at L…', 'lastProgressAt': 1786591186483, 'tokens': 99564, 'toolCalls': 30, 'durationMs': 641880, 'resultPreview': '## 1. Completed matches per tour, and how much cricapi is in them

**Live sheet, read today via gviz** (11 point tabs, sheet `1um6Scv2MbFzRxTVUJsWxMxX4oxTDojJmujcFBJGDlyg`; tab list from `/Users/nishant-singodia/wwc-points-bot/tours.json`). **174 completed matches.** Column = the *currently published* `Source`:

| tour | completed | cricsheet | ESPN-only | cricapi-mix | settlement baseline? |
|---…'}, {'type': 'workflow_agent', 'index': 6, 'label': 'verify:completeness', 'phaseIndex': 2, 'phaseTitle': 'Verify', 'agentId': 'ae140844d286a44cc', 'model': 'claude-opus-5[1m]', 'state': 'done', 'startedAt': 1786591186490, 'queuedAt': 1786591186488, 'attempt': 1, 'lastToolName': 'Bash', 'lastToolSummary': "cd /Users/nishant-singodia/wwc-points-bot && grep -n 'dis =…", 'promptPreview': 'CONTEXT. Read /Users/nishant-singodia/wwc-points-bot/CLAUDE.md, RUNBOOK.md and STREAMLINE_PLAN.md FIRST.
Repos: BOT=/Users/nishant-singodia/wwc-points-bot (wc_fps_to_csv.py is the scorer), APP=/Users/nishant-singodia/wwc-draft (Next.js draft app).

THE DECISION: remove cricapi from SCORING entirely; ESPN (site.api.espn.com) becomes the only live
source, cricsheet stays as the official arbiter at L…', 'lastProgressAt': 1786593886413, 'tokens': 93681, 'toolCalls': 29, 'durationMs': 2699923, 'resultPreview': '# REFUTATION AUDIT — ESPN-only scoring

**Method.** 4 cached matches, 3 formats, 88 ESPN perf rows / 86 matched to cricsheet **by cricinfo id** (not name). All ESPN reads cached, zero network. Harness: `/private/tmp/claude-501/-Users-nishant-singodia/f36700e2-70e2-4403-9da5-40a03f07ecbc/scratchpad/refute.py`, `refute2.py`.

| ev | series | fmt | date | teams | cricsheet |
|---|---|---|---|---|---|…'}, {'type': 'workflow_agent', 'index': 7, 'label': 'plan', 'phaseIndex': 3, 'phaseTitle': 'Plan', 'agentId': 'ac519f6760a058780', 'model': 'claude-opus-5[1m]', 'state': 'done', 'startedAt': 1786593886431, 'queuedAt': 1786593886427, 'attempt': 1, 'lastToolName': 'Bash', 'lastToolSummary': 'grep -n "sys.argv\\|argparse\\|getenv(\"SKIP\\|os.environ.get"…', 'promptPreview': 'CONTEXT. Read /Users/nishant-singodia/wwc-points-bot/CLAUDE.md, RUNBOOK.md and STREAMLINE_PLAN.md FIRST.
Repos: BOT=/Users/nishant-singodia/wwc-points-bot (wc_fps_to_csv.py is the scorer), APP=/Users/nishant-singodia/wwc-draft (Next.js draft app).

THE DECISION: remove cricapi from SCORING entirely; ESPN (site.api.espn.com) becomes the only live
source, cricsheet stays as the official arbiter at L…', 'lastProgressAt': 1786594171599, 'tokens': 54713, 'toolCalls': 8, 'durationMs': 285167, 'resultPreview': '# ESPN‑ONLY SCORING — THE MIGRATION PLAN

**Repo:** `/Users/nishant-singodia/wwc-points-bot` · scorer `wc_fps_to_csv.py` · app `/Users/nishant-singodia/wwc-draft`
All line refs are `wc_fps_to_csv.py` unless stated. **PROVEN** = read in source or measured against cricsheet. **UNCERTAIN** = stated as such.

---

## HEADLINE VERDICT — DO NOT FLIP TODAY

The *arithmetic* is ready: across 4 cached matc…'}], 'totalTokens': 617335, 'totalToolCalls': 163}