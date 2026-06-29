# WWC T20 Points — auto-updating Google Sheet

Computes **Dream11 T20 fantasy points** for every Women's T20 WC 2026 squad player,
per match, and **writes them straight into your Google Sheet**. Runs in GitHub
Actions (no laptop needed) — on a schedule *and* from a button inside the Sheet.

**Data fidelity**
- **Cricsheet** (ball-by-ball) is the authoritative source → exact dots, maidens,
  run-outs, +4 in-XI. It posts internationals ~1–3 days after the match.
- **cricapi** gives live coverage for matches cricsheet hasn't posted yet
  (`Source` column says `cricapi (dots pending)`; **dots stay blank** until
  cricsheet confirms — never a fake 0).
- When both exist, overlapping stats are **cross-checked** and any disagreement is
  flagged in the `Source` column.

---

## One-time setup (~15 min)

### 1. Create the GitHub repo
```bash
cd ~/wwc-points-bot
git init && git add -A && git commit -m "WWC points bot"
gh repo create wwc-points-bot --public --source=. --push   # or create on github.com and push
```

### 2. Google service account (lets the bot write your Sheet)
1. https://console.cloud.google.com → create a project (any name).
2. APIs & Services ▸ Library ▸ enable **Google Sheets API**.
3. APIs & Services ▸ Credentials ▸ **Create credentials ▸ Service account**. Name it, Create.
4. Open the service account ▸ **Keys** ▸ Add key ▸ **JSON** → downloads a `.json`.
5. Copy the service account **email** (looks like `name@project.iam.gserviceaccount.com`).
6. Open your Google Sheet ▸ **Share** ▸ paste that email ▸ **Editor** ▸ Send.

### 3. GitHub repo secrets
Repo ▸ Settings ▸ Secrets and variables ▸ Actions ▸ **New repository secret**:
- `CRICKET_API_KEY` — your cricketdata.org key
- `GSHEET_ID` — the long id in the sheet URL (`/spreadsheets/d/<THIS>/edit`)
- `GOOGLE_SERVICE_ACCOUNT_JSON` — paste the **entire** contents of the JSON key file

### 4. The in-Sheet "Refresh" button
1. In the Sheet: **Extensions ▸ Apps Script**. Delete the stub, paste `apps_script.gs`, Save.
2. **Project Settings ▸ Script properties ▸ Add**:
   - `GH_OWNER` = your GitHub username
   - `GH_REPO`  = `wwc-points-bot`
   - `GH_PAT`   = a GitHub **fine-grained PAT** (github.com ▸ Settings ▸ Developer settings ▸
     Fine-grained tokens) scoped to this repo with **Actions: Read and write**
3. Reload the Sheet → a **🏏 WWC** menu appears → **Refresh points now**.

---

## Using it
- **Button:** 🏏 WWC ▸ Refresh points now → triggers a run; the tab updates in ~1–2 min.
- **Automatic:** runs every 2 hours regardless (edit the `cron` in
  `.github/workflows/wwc-points.yml` to change).
- The data lands in the **`WWC T20 POINTS`** tab (override with a `GSHEET_TAB` secret).
  Build your leaderboard on *other* tabs that reference it, so this tab stays the
  clean raw layer.

## Columns
`Match · Date · Team · Player ID · Full Name · Role · Played · Runs · Balls · 4s · 6s · SR ·
Dismissal · Overs · Maidens · Dots · Runs Conceded · Wickets · Econ · Catches ·
Stumpings · Run Outs · Pts Bat · Pts Bowl · Pts Field · Pts SR · Pts Econ · Pts XI ·
Fantasy Points · Source · In Squad List · Bat Order · L1 Recon · L2 Recon ·
Match Status · Recon Flag`

- **`Match Status`** — `LIVE` / `COMPLETED` / `COMPLETED_FLAGGED` (`SCHEDULED` for toss rows).
  The draft app gates "match completed" on this: a match with an unresolved cricapi↔ESPN (L1)
  disagreement stays **LIVE** until you approve a value in the **`Recon Review`** tab. See
  **`RECON_REVIEW_WORKFLOW.md`**.
- **`Recon Flag`** — the human reason (e.g. `⏳ pending recon approval`, `⚠ official revision
  pending`, `⚠ unverified — single feed`).

- **`Player ID`** — stable player identity (`pid`) from the global registry
  (`registry/players.json`). The draft app joins points on this, not on the name. See `TOURS.md`.
- **`Full Name`** — canonical name (the registry resolves every feed spelling to one identity,
  so the same player never splits into two rows).

Points exclude C/VC — apply your ×2 / ×1.5 in the leaderboard tab.
