# wwc-points-bot

A serverless Python pipeline that computes Dream11-style T20 fantasy points for every squad player, match by match, across multiple live tournaments at once — and writes each one into its own tab of a Google Sheet, on a schedule, with no machine left running.

> **Demo:** there is no public web app — the "product surface" is the Google Sheet it writes and the architecture below. The output sheet is private (it backs a private friends' league), so this README describes the data model and pipeline rather than linking live cells.

---

## Why I built it

I run a whole fantasy-cricket setup with friends — a live player draft *and* an auction, scored on Dream11 points across whatever's on, often **several tournaments at once**. Underneath all of it sits one deceptively hard question: *what did every player actually score?*

For a season I was the human ETL — refreshing scorecards mid-match, keying numbers into a sheet, and refereeing arguments about whose total was right. Nothing off-the-shelf solved it: the free cricket APIs disagree with each other, none of them carry clean dot balls or maidens, and none cover the niche tours we actually play (women's World Cups, smaller bilateral series) reliably. So I built the boring, load-bearing piece the rest of my fantasy apps sit on — a points layer that updates itself while a match is live, keeps running with my laptop shut, upgrades to the official numbers the moment they land, and (the part I care about most) **tells you when two sources disagree instead of silently picking one**.

---

## What it does

- **Scores every squad player, every completed T20** against the **full** Dream11 T20 ruleset — a faithful reimplementation, not an approximation. It covers batting (runs, boundaries, 25/50/75/100 milestones, strike-rate bonus/penalty, ducks), bowling (wickets, LBW/bowled bonus, dot balls, maidens, 3/4/5-wicket hauls, economy bonus/penalty), fielding (catches, 3-catch bonus, stumpings, direct vs assisted run-outs), and the +4 for being in the playing XI.
- **Tracks several tournaments in parallel.** Tours are declared in `tours.json`; each one writes its own sheet tab (e.g. a women's World Cup and three men's bilateral/franchise T20 series at once). Adding a tour is config, not code.
- **Runs itself.** A GitHub Actions cron does a full recompute every 2 hours; a second, 5-minute workflow does a near-zero-cost "lineup" tick that only does real work inside a match's toss window. There's also a one-click **Refresh** button inside the Sheet (an Apps Script that dispatches the workflow).
- **Carries its own audit trail.** Every row records which source it came from and ships two reconciliation columns so disagreements and later corrections are visible rather than silent (details below).
- **Emits a stable `Player ID` per row**, so downstream apps join on identity, not on a fuzzy name string.
- **It's infrastructure, not a toy.** The same feed — and the same global player identity — powers my [fantasy draft app](https://github.com/nishantsingodia/wwc-draft) and [auction helper](https://github.com/nishantsingodia/cricket-auction-helper). One points layer, one identity, three apps.

---

## How it's built

**Stack:** Python 3.11 (standard library — `urllib`, `json`, `csv`, `difflib`; the only third-party dep is `gspread`), GitHub Actions for scheduling/compute, a Google service account for Sheet writes, and three free data feeds — [cricsheet](https://cricsheet.org) ball-by-ball, [cricapi](https://cricketdata.org), and ESPN's public cricket endpoints.

A few decisions I'd call out:

**1. A source hierarchy with explicit provisional vs. official states.** cricsheet ball-by-ball is the gold source — it's the only feed with exact dot balls and maidens, so when it posts (a 1–5 day lag) it *overrides everything*. Until then, a match is scored from the cricapi scorecard as the base, with ESPN ball-by-ball injected for the dots and the in-XI bonus that cricapi can't supply. The row is explicitly flagged **provisional** in that window, and a `dots_final` flag means an unverified source never fabricates a `0` in the dots column — it leaves it blank. When cricsheet later posts, the row silently upgrades to official.

**2. A two-stage reconciliation trail (`L1 Recon` / `L2 Recon`).** Rather than trust one feed, every points tab carries two cross-check columns. **L1** compares the two live feeds (cricapi ↔ ESPN) on the fields they both carry — runs, wickets, fours, sixes — and prints `✓ clean` or a `⚠` gap. **L2** compares the official cricsheet figure against what the provisional row had scored, reading as `was → corrected` so any post-match revision is legible. The point is that source disagreement and official corrections are *surfaced in the product*, not buried.

**3. A global, ID-anchored Player Registry — the keystone.** The recurring failure mode in cricket data is the same player appearing under different spellings across feeds ("Smriti Mandhana" / "S Mandhana" / "SS Mandhana"), which silently splits one person's stats across two rows. `build_registry.py` builds one permanent `registry/players.json` keyed on a stable `pid` (the cricsheet id when known), listing every feed spelling as an alias. The bot then resolves each feed name to a `pid` by dictionary lookup, not by gambling on fuzzy match per match; fuzzy matching survives only as a *logged* fallback that's surfaced as a CI warning so registry gaps get closed. The builder is rebuild-safe by construction — a `given_compatible` guard refuses to merge two people who merely share a surname (the bug that once collapsed two different "…Singh" players into one), so re-running it can only *add* identities and spellings, never re-corrupt them. Identity is global: resolve a player once and every future tournament reuses it.

**4. No-code corrections from inside the Sheet.** The rare case that needs a human — a name that matched nobody, or two people that look merged — is written back to dedicated review tabs (`Needs Review`, `Identity Anomalies`) with a closest-match guess and a `Yes/No` cell. The next run reads those answers and persists them as aliases. So the one manual step in the whole system is editable by a non-engineer in a spreadsheet, with the live identity table kept read-only.

**Operational guardrails worth noting:** completed-match scorecards are cached between runs (they're immutable) to stay under cricapi's 100-hits/day free cap, with optional key failover; the run *aborts before touching the Sheet* if the feed returns empty, so a bad fetch can never wipe good data; super-over deliveries are excluded; and feed-to-feed date joins tolerate a ±1-day offset to absorb timezone differences.

---

## Run it locally

It runs as a plain script. With a cricapi key it computes points and writes a CSV; without Sheet credentials it simply skips the Sheet write, so it's safe to run on a laptop:

```bash
pip install -r requirements.txt
CRICKET_API_KEY=<your-cricketdata.org-key> python wc_fps_to_csv.py out.csv
```

Point it at a different tournament with no code change via `SERIES_ID` / `ESPN_SERIES_ID` / `SQUADS_JSON` env vars, or add it to `tours.json`. See **[SETUP.md](SETUP.md)** for the GitHub Actions + Google service-account wiring and **[TOURS.md](TOURS.md)** for the add-a-tournament workflow.

---

## Honest limitations / scope

- **It's personal-scale.** Built for one friends' league, not a multi-tenant product — config is committed JSON, the "database" is a Google Sheet, and the whole thing leans on free API tiers (cricapi's 100/day cap is a real constraint the caching is designed around).
- **The registry needs occasional human tending.** New tournaments bring new name spellings; the bot flags them, but someone still has to confirm the review-tab guesses. It degrades gracefully (fuzzy fallback + a CI warning) rather than failing, but it isn't fully hands-off across a brand-new tour.
- **Provisional rows can move.** Numbers scored before cricsheet posts are explicitly marked provisional and can be revised when the official ball-by-ball lands. That's by design and made visible, but it means a leaderboard built on the freshest data is not yet the final word.
- **Coverage is what the feeds cover.** It handles T20 internationals and a couple of franchise leagues that publish to cricsheet; a format or competition that isn't in cricsheet loses the gold source and stays on the provisional path indefinitely.
- **C/VC multipliers are out of scope here.** This is deliberately the clean raw points layer; the ×2 / ×1.5 captain logic lives in the consuming leaderboard, not in this feed.

---

Built by **Nishant Singodia** — Director of Product (fintech / payments / platform), IIT Kharagpur B.Tech, based in Mumbai. I lead product, and I still like shipping the 0→1 myself.

[GitHub](https://github.com/nishantsingodia) · [LinkedIn](https://www.linkedin.com/in/nishantsingodia) · [nishantsingodia@gmail.com](mailto:nishantsingodia@gmail.com)
