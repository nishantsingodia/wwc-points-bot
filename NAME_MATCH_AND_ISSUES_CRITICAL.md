# Name-Match & Points Issues — Critical Resolution Plan

**Status:** PROPOSED — awaiting your go-ahead on scope/sequencing.
**Scope:** All three projects — `wwc-points-bot` (Python → Google Sheet), `wwc-draft` (Next.js draft), `cricket-auction-helper` (Next.js auction).
**Author:** Claude, 2026-06-21. Built on first-hand investigation of LIVE sheet data + a cross-repo code audit.

---

## ✅ Status — IMPLEMENTED 2026-06-22

Built and verified locally across all three repos. **Identity is GLOBAL, not per-tour** —
a player is resolved ONCE and reused in every future tour (you flagged this; the design
below reflects it).

**What shipped**
- **`wwc-points-bot`** (`fix/unified-name-match`, builds on merged `fix/scoring-reconciliation`):
  - `registry/players.json` — ONE global identity file, keyed on stable `pid`
    (`cricsheet_id` when known, else `espn:<id>` / `slug:<name>`). 310 players, 242 with
    `cricsheet_id`. Aliases = every feed spelling. `registry/manual_aliases.json` holds the
    handful of genuinely-unlinkable same-player spellings (reviewed once).
  - `build_registry.py` — harvests identity from the auction DB (`cricsheet_id` + cricsheet
    initials), ESPN rosters (`espn_id`), cached cricapi, and all 3 repos' existing alias maps.
    Idempotent + additive; per-tour squad files supply only membership.
  - `wc_fps_to_csv.py` — deterministic `pid` lookup first (merges split spellings), junk-name
    filter, **emits a `Player ID` column + canonical name**; fuzzy only as a logged fallback
    (`registry/UNMATCHED_*.log`).
- **`wwc-draft`** (`fix/id-based-points-join`): `players-raw.json` backfilled with `pid`
  (320/326); points joined by **Player ID** first (fuzzy fallback); `getLastPlayedXI` dedupes
  by pid + keeps first real bat order; self-heal dedupes by pid. Typecheck clean.
- **`cricket-auction-helper`** (committed locally): `src/lib/registry/` (registry copy + resolver);
  both pool builders resolve announced names → `cricsheet_id` registry-first (fuzzy fallback).
  Additive / read-only at match time — the never-rebuild-a-live-pool rule is unaffected. Typecheck clean.

**Verified vs LIVE data** (cricapi+ESPN path): WWC phantom-with-points rows **13 → 2**
(the 2 are a genuine non-squad player, correctly surfaced); **Wyatt's 195 back on her row**;
Nimasha / Athapaththu / Tajinder collapse to one `pid` each; `Player ID` populated on 540/542 rows;
join keys align between `players-raw.json` and the sheet.

**Adding a tour now** (the once-and-for-all workflow): drop the squad file → `python3 build_registry.py`
(auto-harvests ESPN/DB, extends the GLOBAL registry, never re-does known players) → eyeball
`registry/UNMAPPED_<tour>.txt` (a handful) → add any genuinely-unlinkable spelling to
`registry/manual_aliases.json` → re-run. Run `registry/backfill_draft_pids.py` to push pids into the draft.

**Remaining (deploy):** push `wwc-points-bot` `fix/unified-name-match` → main so CI regenerates
the sheet with `Player ID` (next scheduled run once cricapi's transient outage clears); deploy the
draft branch after reconciling the unrelated other-session WIP in that repo. The auction is local-only.
**Note:** `cricket-auction-helper/data/wc_fps_to_csv.py` is now a STALE copy — `wwc-points-bot` is canonical; delete or re-sync it.

---

## 0. TL;DR — what's wrong and what I recommend

**One root cause, three symptoms.** Player identity is resolved by **fuzzy name-matching in three independent places** (two TypeScript copies + one Python copy), and **no stable player ID flows between the points bot and the draft**. So every match re-gambles on whether a feed's spelling crosses a similarity threshold. When it misses, the same player splits into two rows; when two players share a surname, they collide; when a feed emits junk ("Player Not Found"), it becomes a phantom player.

**This is live in your sheet right now — 20 corrupted rows** (13 WWC + 7 MLC), several carrying big scores. Examples I pulled from the live tabs today:

| Tab · Match | What the sheet shows | Reality |
|---|---|---|
| WWC · ENG v SL | `Danni Wyatt-Hodge` → **Played=N, blank points**; phantom `DN Wyatt` → **195 pts** | Her century is credited to a phantom row, not to her |
| MLC · WAF v MINY | `Tajinder Singh` (4 pts) **+** phantom `Tajinder Dhillon` (28 pts, bat #4) | One player (Tajinder Singh Dhillon), stats split across two rows |
| WWC · NZ v SL | `Nimasha Madushani` (squad) **+** phantom `Nimasha Meepage` (36 pts) | One player (ESPN id 1380033, "Nimasha Madushani Meepage") |
| WWC · SA v PAK | phantom `Player Not Found` (44 pts) | cricapi junk string emitted as a player |
| MLC · LAKR v SFU | phantoms `Andre Fletcher` (86), `Dinesh Chandimal` (20), `Russell Peterson` (12) | All three are LAKR squad players, orphaned |

**My recommendation:** Stop matching on names. Introduce **one per-tour Player Registry (a committed JSON), anchored on a stable ID (`cricsheet_id`, which the draft and auction already use), listing every feed spelling of every player.** Build it once per tour — mostly automatically by harvesting ESPN (free) + cricsheet — review a handful of stragglers, done. Then:

1. The **points bot** resolves feed names against the registry **deterministically** (no threshold), emits a **`Player ID` column** and the canonical name.
2. The **draft** joins points **by ID, not name** — killing the whole class of "grey 0 / missing / wrong order" bugs.
3. The **auction** generates its alias maps from the same registry.

**Display-name decision (you delegated this to me):** keep your **squad / announced names** as the displayed name (e.g. "Danni Wyatt-Hodge", "Nimasha Madushani"). The registry carries the feed variants underneath. Rationale: the draft's `players-raw.json` and the auction DB already key and display on these names; changing them risks orphaning existing `draft_picks` / `team_selections` / auction-pool rows (a documented hazard). Identity = the stable ID; names = display only; variants = registry. This is the lowest-risk choice and scales cleanly to many automated tours.

---

## 1. The root cause in one diagram

```
                ┌─────────────────────────────────────────────┐
   FEEDS        │ cricapi (full names) · ESPN (fullName +      │
                │ displayName + athlete_id) · cricsheet         │
                │ (INITIALS: "DN Wyatt", "AC Jayangani")        │
                └───────────────────────┬─────────────────────┘
                                        │  fuzzy name match (threshold 84)
                                        ▼
   POINTS BOT   wc_fps_to_csv.py: match_squad_to_perf()  ── miss ──▶ player splits
   (Python)     ├─ squad row  (In Squad=Y)                          into TWO rows
                └─ leftover   (In Squad=N)  ◀── phantom / duplicate
                                        │
                                        ▼  Google Sheet  (NO player-id column)
                                        │
                                        ▼  fuzzy name match AGAIN (a 3rd time)
   DRAFT (TS)   lib/points.ts fuzzyLookupPoints() + getLastPlayedXI()
                ├─ wrong points / grey 0 / "missing" player
                └─ wrong XI order (last duplicate row wins)

   AUCTION (TS) build-*-pool.ts: a 2nd TS fuzzy copy + per-builder NAME_ALIASES
```

**Why it keeps recurring:**
- Three matchers (`wwc-draft/lib/fuzzy-name-match.ts`, `cricket-auction-helper/src/lib/fuzzy-name-match.ts`, and Python `match_squad_to_perf`) can each miss independently, and drift apart.
- The cure so far has been **hand-adding one alias per broken player** (`"charlotte dean"→"charlie dean"`, `"chamari athapaththu"→"AC Jayangani"`, `MLC_NAME_ALIASES`, …) — whack-a-mole, in 4 different files.
- **cricsheet (the "official, exact" source) is the WORST for names** — it uses initials (`DN Wyatt`, `RMVD Gunaratne`). The moment cricsheet posts and overrides cricapi, squad players that matched fine suddenly orphan. (This is exactly why "Wyatt" looked fine in the older local CSV but is split in the live sheet now.)
- There is **no stable ID in the CSV**, so the draft has to fuzzy-match a third time — and `players-raw.json` already holds `cricsheet_id` it can't use.

---

## 2. The unified fix — Player Registry + stable ID

### 2.1 The single source of truth: `registry/players.json` (GLOBAL)

ONE committed global file — identity is permanent, not per-tour (squads change per tour;
the player doesn't). Keyed on a stable `pid` (`cricsheet_id` when known), with **every**
normalized spelling each feed uses. A player resolved in one tour is reused in all future
tours — zero rework. Per-tour `registry/tours/<slug>.json` carries only team membership.

```json
{
  "tour": "Women's T20 WC 2026",
  "players": [
    {
      "pid": 1234567,                       // cricsheet_id (stable across feeds & repos)
      "espn_id": 5678,                       // captured live from ESPN rosters (free)
      "team": "ENG",
      "role": "BAT",
      "display": "Danni Wyatt-Hodge",        // what every surface shows
      "aliases": [                            // every norm() spelling any feed produces
        "danni wyatt hodge",
        "danielle nicole wyatt hodge",        // ESPN fullName
        "dn wyatt hodge", "dn wyatt",          // cricsheet initials
        "danni wyatt"
      ]
    }
  ]
}
```

### 2.2 Build it once per tour — mostly automatically

New script `build_registry.py` (lives in `wwc-points-bot`, the data hub):

1. **Seed** from the squad file (`display`, `team`, `role`).
2. **Harvest spellings** from every available source:
   - **ESPN rosters** — `fullName` + `displayName` + **`athlete.id`**. Free, unlimited, available the moment a squad is announced. (ESPN's `displayName` is the broadcast standard and usually equals your squad name.)
   - **cricsheet `info.registry.people`** — the initials-form names + the cricsheet `people_id` (= your `pid`). Available once a match posts (1–5 day lag).
   - **cricapi scorecard names** — from cached scorecards now, topped up when the daily quota resets.
3. **Map** each harvested spelling to a squad player using the existing fuzzy logic but accept **only high-confidence** matches; everything else → `registry/UNMAPPED_<tour>.txt` for a one-time 60-second human eyeball (will be a handful).
4. **Idempotent + additive** — re-running only ADDS new spellings, never drops. Safe to run every CI cycle, so new players/spellings get absorbed automatically instead of breaking.

> This harvester is the thing that replaces today's manual "add one ALIAS per broken player" across four files.

### 2.3 Consume the registry everywhere

**A. Points bot (`wc_fps_to_csv.py`)**
- Build a `norm(spelling) → pid` index from the registry. `match_squad_to_perf` does a **deterministic lookup first** (no threshold, no greedy collisions). Fuzzy stays only as a fallback for names not yet in the registry — and a miss is **logged to UNMAPPED**, never silently emitted as a phantom row.
- **Junk filter:** drop `"Player Not Found"`, empty-norm names, stray punctuation.
- **Emit two columns:** `Player ID` (the `pid`) and keep `Full Name` = registry `display`. → the sheet always shows "Danni Wyatt-Hodge" / "Chamari Athapaththu" even when cricsheet supplied `DN Wyatt` / `AC Jayangani`, and the stats attach to the squad slot (no more phantom squad-player rows).
- Genuine non-squad players (real late call-ups not yet in the registry) still get a row, but clearly flagged + listed in UNMAPPED so you decide whether to add them.

**B. Draft (`wwc-draft`)**
- The CSV now has `Player ID`. **Join points by ID** (`players-raw.json.id == Player ID`), not fuzzy name — `lib/points.ts` `getMatchPointsForMatch` / `fuzzyLookupPoints` and the results route. Kills the "grey 0 / missing / wrong points" class (BUGS.md #5) outright.
- `getLastPlayedXI` (`lib/points.ts:121-155`): **dedupe by (team, id), prefer `Played=Y`** — kills "Sunny Patel on top though he didn't play" and the "last duplicate row wins the bat order" bug.
- Keep fuzzy as a back-compat fallback only for old rows without an ID (and old `draft_picks` keyed on synthetic `s|TEAM|ROLE|Name`).

**C. Auction (`cricket-auction-helper`)**
- Pool builders (`build-womens-pool.ts`, `build-mlc-pool.ts`) consult the registry's alias sets instead of per-builder `NAME_ALIASES` / `MLC_NAME_ALIASES`. Anchor on `cricsheet_id` (already in the DB).
- **Guardrail preserved:** keep `INSERT OR IGNORE` (additive). NEVER rebuild/wipe an in-progress `auction_pool` (the registry is read-only at runtime; this is a hard rule).

**D. The three fuzzy copies**
- They stay (Python ≠ TS, can't share code directly), but become rarely-invoked fallbacks. Their alias maps are **generated from the one registry** so there's a single source. A CI check flags drift between the registry, the squad files, and the generated alias maps.

---

## 3. How this maps to the three things you asked for

### Ask #1 — Draft KK5V8L: team-creation issues
| Symptom | Root cause | Fix |
|---|---|---|
| **Total tour points don't reflect right** | Points joined by fuzzy name against a sheet that has split/duplicate rows; some players match the wrong row or none | Join by `Player ID`; registry guarantees one row per player with the right name |
| **Tajinder Dhillon "missing"** | Squad calls him "Tajinder Singh"; feed also emits "Tajinder Dhillon" → split into two rows (4 pts vs 28 pts); draft's `players-raw.json` "Tajinder Dhillon" can't match the canonical "Tajinder Singh" row (different surname) | Registry maps both spellings → one `pid`; draft joins by id |
| **Sunny Patel shows on top, didn't play** | `getLastPlayedXI` lets the last CSV row win, and Patel-surname collisions (Monank/Sunny/Smit/Nensi) confuse fuzzy ordering | Dedupe `getLastPlayedXI` by (team,id) preferring `Played=Y`; order by `Bat Order` keyed on id |
| **Adding new/replacement players** | Documented but fragile (manual `players-raw.json` edit + redeploy; name variants break fuzzy) | Add player to squad + registry → harvester resolves spellings automatically; keep the "only ADD ids, never change/delete" rule (BUGS.md #6) |

### Ask #2 — GSheet points reconciliation
- After the registry + ID column land, **regenerate every tab** and run a reconciliation pass: every squad player appears on exactly **one** row, with the **canonical name** and **reconciled points** (cricsheet/cricapi/ESPN cross-checked, as today). The 20 known-corrupted rows get verified one by one (Wyatt's 195 back on her row; Tajinder merged to 32-ish; Fletcher/Chandimal/Peterson/Nimasha merged; junk gone).
- Folds in the other chat's scoring fixes (Charlotte Dean split, duck-outside-gate, LBW-from-text) — see §6.

### Ask #3 — Name-match fixed once and for all, across all 3 projects
- The registry is the **shared identity layer**. The points bot writes the `Player ID`; the draft and auction consume the same `cricsheet_id`-anchored identity. New spellings are **data** (auto-harvested), not code edits in four files.

---

## 4. Phased rollout

> **Quota note:** cricapi's daily limit is exhausted today (108/100), so a full LIVE regen of the sheet must wait until it resets (tomorrow, UTC midnight). Everything that uses ESPN (free) or cached/committed data can proceed now. This is the same blocker the other chat hit.

**Phase 0 — Stop the bleeding (today; no cricapi needed)**
- Points bot: junk-name filter (removes the 2 live `Player Not Found` rows).
- Draft: dedupe `getLastPlayedXI` by (team,name) preferring `Played=Y` (fixes Sunny-on-top now, even before the ID join).
- Merge the other chat's `fix/scoring-reconciliation` to main so CI runs corrected scoring (or stack on it — see §7).

**Phase 1 — Registry + ID column (the core)**
- `build_registry.py`; commit `registry/<tour>.json` (harvest ESPN now; backfill cricsheet/cricapi as they post / quota resets).
- Points bot: deterministic registry lookup; emit `Player ID` + canonical `Full Name`; eliminate phantom squad-player rows; write UNMAPPED report.

**Phase 2 — Consume the ID in the apps**
- Draft: join points by `Player ID`; align `players-raw.json` ids to registry pids; fuzzy as back-compat fallback only.
- Auction: generate alias maps from the registry; keep additive pool writes.

**Phase 3 — Scale + automate (for the many tours coming)**
- New-tour onboarding flow: drop a squad file → run `build_registry.py <tour>` → review the short UNMAPPED list. (Optionally a research agent that drafts the squad file + runs the harvester.)
- CI: registry↔squad↔alias drift check; surface UNMAPPED each run so a new spelling is a 1-line review, never a silent dup.

**Phase 4 — Reconciliation & verification**
- Full live regen; diff old vs new sheet; assert **0 phantom squad-player rows**; spot-check the 20 known-corrupted rows; confirm draft totals/order/missing-players resolved in KK5V8L.

---

## 5. Scaling to many automated tours

- **Per-tour cost drops to:** 1 squad file + `build_registry.py <tour>` (auto-harvest) + eyeball a handful of unmapped names. No per-player alias edits, no four-file sync.
- **Auction** benefits from the registry-generated aliases (replaces the hardcoded `NAME_ALIASES`/`MLC_NAME_ALIASES` per builder). The audit also flags a future refactor (a `tours/registry.ts` to replace hardcoded `isMLC`/`isWomensWC` branches) — worth doing if tour count grows past ~10.
- **Draft** onboarding (CLAUDE.md Steps 0–7) stays, but Step "fix names so they match the sheet" disappears — the ID join makes it moot.

---

## 6. Coordination with the other chat (`fix/scoring-reconciliation`)

That branch (commit `9cb1481`, not yet merged to main) already fixes, in `wc_fps_to_csv.py`:
- `"charlotte dean"→"charlie dean"` ALIAS + canonicalize-on-read (cricapi internal split) — **same disease, manual cure**; the registry generalizes it.
- LBW/bowled bonus from dismissal text when cricapi nulls the bowler.
- Duck applied outside the `b>0/r>0` gate.
- `⏳ provisional` flags on cricapi+ESPN rows.

**These are correct and should land** — the registry doesn't replace the scoring fixes, only the identity layer. Open items it flagged (split-detector, mirror-sync, full squad sweep blocked by quota) are **subsumed** by this plan: the harvester IS the split-detector, and the registry IS the once-and-for-all map. Decision needed: merge that branch first, or stack this work on top of it (§7 / open question).

**Mirror file:** `cricket-auction-helper/data/wc_fps_to_csv.py` is ~3 days behind `wwc-points-bot/wc_fps_to_csv.py`. The registry work should re-sync them (or, better, make the auction stop carrying a copy and import shared scoring rules).

---

## 7. Guardrails (hard rules carried from the audit + memory)

1. **Never rebuild/wipe an in-progress `auction_pool`** — purses are debited at sale time; wiping desyncs money. Registry is read-only at runtime; pool writes stay additive (`INSERT OR IGNORE`).
2. **Never change or delete a player `id` mid-tournament** — `draft_picks` / `team_selections` reference it (BUGS.md #6). Only ADD.
3. **Don't rename canonical display names blindly** — old draft rows / DISPLAY_NAME_MAP / auction pool depend on them. Variants go in the registry, not by renaming.
4. **Don't match points on the `Match N` label** — teams + date, as today (BUGS.md #4). Unchanged.
5. **Surface ambiguity, never silently guess** — registry makes each spelling map to exactly one id; fuzzy fallback logs rather than guesses.
6. **Read before destructive actions; verify against live state** — this plan was built from live sheet data, not assumptions.

---

## 8. Open decisions for you

1. **Branch strategy:** merge `fix/scoring-reconciliation` to main first, then build Phase 1+ on a fresh branch — OR stack Phase 0–2 directly on `fix/scoring-reconciliation` so it all ships together? (I lean: **merge theirs first** to get scoring fixes live, then stack identity work — cleaner history, and the other chat can finish independently.)
2. **`pid` source for players with no cricsheet record yet** (brand-new players before cricsheet posts): use ESPN id + a temporary tour-local id, backfilled to `cricsheet_id` once cricsheet posts. (Recommended; additive registry handles it.)
3. **Auction mirror:** keep the duplicated `wc_fps_to_csv.py` in `cricket-auction-helper/data/` (and re-sync), or stop duplicating and share the scoring rules? (I lean: stop duplicating long-term.)
4. **Scope of this pass:** do all of Phases 0–4 now, or land Phase 0–1 (stops the bleeding + fixes the sheet) and schedule 2–4? (I lean: 0–1 now, 2 right after, 3–4 as the tour-count grows.)

---

*This file is the cross-project master plan. It lives in `wwc-points-bot/` (the data hub); copy/symlink into `wwc-draft` and `cricket-auction-helper` if you want it visible there too.*
