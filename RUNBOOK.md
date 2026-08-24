# RUNBOOK — operating this for real money

Written 10 Aug 2026, after a week in which settled results moved twice. Read the first section
before deciding how much to trust a number.

---

## 1. What this system can and cannot guarantee

It reads three third-party feeds. Two are live and disagree with each other; the third is
authoritative and arrives **1–5 days late**.

**Achievable, and now enforced:**
- A number never changes silently after you've settled on it.
- A number that hasn't been verified against cricsheet is never presented as final.
- Every unverified or unresolvable case produces a named row, never a silent zero.

**NOT achievable, by construction:**
- "The number is correct the moment the match ends." Before cricsheet posts, every number is
  provisional. On a tour with a Cricbuzz witness it is at least a number two independent feeds
  agree on across all 14 fields; on the other 9 of 13 tours it is a single feed's word.
  (Historically this read "a bet on cricapi vs ESPN — cricapi right 59%, ESPN 41% on 57 disputed
  fields". cricapi is gone since 20 Aug 2026; the conclusion — no single live feed is trustworthy
  alone, and no amount of code changes that — is unchanged.)

**So the money rule is:** settle on **cricsheet-verified** matches only. Everything before that is
provisional, however confident it looks. The Settlement Audit exists to prove nothing moved
between when you settled and now — use it, don't skip it.

---

## 2. The one bug class — check this first, always

Every incident this week was the **same shape**: a guard applied to one side and not its mirror,
or an *absence* treated as a *value*.

| # | Symptom | Root cause |
|---|---|---|
| 1 | Hasaranga 114 → 0 on a match badged COMPLETED | official-card row unresolvable → treated as "didn't play" |
| 2 | Dickwella 69 → 63 reading "✓ complete" | `RECON_L2` compared 10 fields; `b`/`balls` weren't among them |
| 3 | 381 rows of `runs 0/34` | `_espn_has_ballbyball` guard existed; cricapi twin never written |
| 4 | 12 settled matches un-published | `espn_covers_match` existed; `capi_covers_match` never written |
| 5 | 13 matches single-sourced, unflagged | "ESPN absent" flagged; "cricapi absent" silent |
| 6 | Kiran Carlson scoring as Liam Dawson | closest-match linked two players; no reverse check |
| 7 | Filled cricinfo ids did nothing | tab was written, never read |
| 8 | Dale Phillips silently 0 all tournament | `dup-cricsheet` blocker existed; split-identity twin never written |

**When something looks wrong, ask in this order:**
1. Is a guard applied to one feed but not the other?
2. Is an absence (missing row, empty card, blank id) being read as a zero?
3. Is something written but never read, or read but never written?

That found all eight. It'll find the ninth faster than I did.

---

## 3. Adding a new tour

```bash
# 1. Register it, then anchor identity BEFORE any points matter
python3 build_registry.py "<tour name>"
python3 identity_healthcheck.py "<tour name>"      # exit 1 on blockers — do not proceed on a blocker
python3 registry/backfill_draft_pids.py            # sheet pid == draft pid, or points show 0

# 2. Verify the tour is fully wired
npm run check:tours          # in wwc-draft — unknown team codes / gender with no ESPN series
```

Then, in the sheet: approve the tour in **TOUR CONTROL** (nothing is polled until you do).

**Gotchas that have actually bitten:**
- ~~An ESPN-added tour has `cricapi_series: ""` → the bot does not score it.~~ **FIXED and then
  obsoleted.** `cricapi_series` no longer exists, and the TOUR CONTROL gate is keyed on the tour
  NAME, so every tour gets an approval row and can be approved. What a tour DOES still need is a
  non-blank `espn_series` (the ingest verify gate fails on a blank one) and a `yes` in TOUR CONTROL.
- A tour with no `cricbuzz_series` has **no L1 second witness** — every match publishes
  `COMPLETED_FLAGGED · "single feed (ESPN only)"`. Not a bug, but do not settle those before
  cricsheet lands. 9 of 13 tours are in this state, including the live ENG v PAK Test.
- Franchise leagues reuse 2-letter codes → add `TEAM_CODE_ALIASES` in the draft, or the board
  silently orders by seed instead of the scorecard.
- Never send a browser User-Agent to ESPN — it 403s, and every fetcher swallows it, so it looks
  exactly like "ESPN has no data".

---

## 4. Daily / per-match

Nothing, if the gates are green. The bot runs 4-hourly. Specifically **do not** pre-emptively
answer recon rows — a row that will resolve itself when cricsheet lands is not work.

Check the sheet when:
- **Needs Cricinfo ID** has rows → fill the id (it's pre-filled with the crosswalk-derived one and
  the espncricinfo URL; you're confirming, not researching). Nothing else fixes identity.
- **Recon Review** has rows → `S2` takes cricsheet's number, `S1` keeps what was settled. Both
  count as decided; only silence keeps a match pending.

---

## 5. BEFORE SETTLING MONEY — the only checklist that matters

1. Open **`/audit`** in the draft app.
2. The match must show **neither** `⏳ recon open` **nor** `⚠ revised`.
3. Its source must read `cricsheet · official`. If it says `ESPN scorecard` or carries
   `⚠ unverified — single feed`, **it is not verified — do not settle it.**
4. `Result changed` and `Results flipped` tiles must be **0** for that tour.
5. If a match shows `? no settled baseline`, that means no trustworthy "before" was recorded —
   you cannot prove it didn't move. Treat as unverified.

If all five hold, the number is cricsheet-verified and provably unchanged since it was frozen.
That is the strongest claim this system can make, and it is a strong one.

---

## 6. When something looks wrong

```bash
python3 -m pytest -q                               # 218 tests; a red suite gates nothing
python3 identity_healthcheck.py "<tour>"           # dup-cricinfo / split-identity / fixable-miss
gh run list --workflow="WWC T20 Points" --limit 5  # did the run even succeed?
gh run view <id> --log | grep -E "quota|sources:|EMPTY scorecard|promote|REFUSED"
```

Read in the log, in order: **UA/403 → cricsheet lag → identity.** The ESPN 403 failure mode is
silent and mimics "no data" — check it first, it cost a day once. (There is no "quota" step any
more; every feed is keyless since 20 Aug 2026, so `grep quota` will find nothing.)

---

## 7. Known-open (not yet fixed)

- ~~**cricapi returns nothing for recent Hundred/LPL matches.**~~ **CLOSED by removal** (20 Aug
  2026) rather than by diagnosis — the root cause was never pinned. Those tours now score off ESPN
  as the base and are cross-checked by Cricbuzz at L1, which is strictly more coverage than the
  flagged single-source state this entry described.
- `RECON_DEV_PLAN.md` lists verified-but-unfixed defects (`xcheck` never read; `L1_RUN_TOL=1`
  hiding up to 7 pts/row; ESPN playbyplay `limit=600` with no pagination). Don't rediscover them.
- The settlement baseline currently freezes on any COMPLETED publish; the locked spec wants it at
  the **L1-done** transition.
