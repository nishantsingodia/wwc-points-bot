<!-- SUPERSEDED BANNER — added 24 Aug 2026 -->
> ⚠️ **HISTORICAL — kept for rationale, not as current truth.** This document assumes cricapi as the incumbent being evaluated against. Its conclusion — adopt Cricbuzz — was acted on, and cricapi was then removed outright. Every feed is keyless and unmetered now, so any quota reasoning here is moot. **Current architecture: `CLAUDE.md`. Trust the code over this file.**

# Free Cricket Data Source Evaluation — 13 Aug 2026

Handoff context. Question asked: **is there any other free provider that supplies every field
our Dream11 scorer needs?** Answer: **no single provider does — but Cricbuzz (2 endpoints) now
covers 100% of the fields, and ESPN's cricinfo-side API has been shut.**

Everything below is marked **[VERIFIED]** (I fetched it and checked the bytes),
**[INFERRED]** (reasoned, not tested), or **[UNVERIFIED]** (assumed, needs testing).
Do not promote an INFERRED line to fact without re-testing.

---

## 1. What the scorer actually needs

From `wwc-draft/lib/d11-score.ts` (the `Perf` type). Fields split into two tiers, and the split
is the whole story:

**Easy — every provider has these:** runs, balls, 4s, 6s, dismissed flag, bowl balls/runs/wickets.

**Hard — these disqualify almost everyone:**

| Field | Why it's hard |
|---|---|
| `bowlLbwBowled` | needs the *dismissal type* per wicket (8 pts each) |
| `catches` / `stumpings` | must be attributed to the **fielder**, not the batter |
| `runOuts` direct vs assisted | needs *how many fielders* were involved (12 vs 6 pts) |
| `bowlDots` | per-bowler dot count |
| `bowlMaidens` | per-bowler maidens |

A conventional scorecard endpoint cannot carry the middle three. It renders `"c Kohli b Bumrah"`
as **text on the batter's row** — no fielder-keyed structure. So the real question is not
"which provider has good data" but **"which free source exposes typed dismissals with fielder
IDs?"** That list is very short.

---

## 2. Provider verdicts

| Provider | Free? | Verdict |
|---|---|---|
| **Cricsheet** | fully | Has 100% of it. Cost is latency (T+1 to T+several days). This is why it's our L2 truth. |
| **Cricbuzz** | yes, unofficial | **Now covers 100% of fields via 2 endpoints.** See §4. **[VERIFIED]** |
| **ESPNcricinfo** | — | **Cricinfo-side API is DEAD to us.** See §3. `site.api.espn.com` (what we use today) still works and stays our live source for dots/maidens. |
| **cricapi / CricketData.org** | 100 hits/day | Already used. Quota is our binding constraint; more endpoints ≠ more budget. |
| **Sportmonks** | real free tier | Free plan = T20I + BBL + CSA T20 Challenge **only**. Zero coverage of Hundred / LPL / CPL / MLC / WWC. Dead on arrival. **[VERIFIED via docs]** |
| **Entity Sport, Roanuz, Sportradar, Goalserve** | no | Trial-only or $125+/mo. |
| **CricLive, EliteSportAPI, Sciflare** | "free tier" | Sciflare free = 100 hits *total*. Unverifiable depth, new vendors. Not for money-settling data. |
| **Crex** | no | **Not a provider at all** — see §5. |

---

## 3. ⚠️ ESPNcricinfo commentary API is bot-blocked — do not build on it

This reverses the obvious first instinct (use ESPN commentary since we already speak `ci:` IDs).
Four independent lines of evidence:

1. **[VERIFIED]** `hs-consumer-api.espncricinfo.com/v1/pages/match/comments` → **403 Akamai**
   with browser UA *and* `Referer: https://www.espncricinfo.com/`. Tested repeatedly.
2. **[VERIFIED]** `hsapi.espncricinfo.com` (used by the 197★ `outside-edge/python-espncricinfo`,
   updated Feb 2026) — **does not resolve at all**. DNS dead.
3. **[VERIFIED]** `machina-sports/sports-skills`, a June 2026 design doc from an independent team:
   *"ESPNcricinfo's own APIs are not viable: `hs-consumer-api.espncricinfo.com` is Akamai
   bot-blocked (403 even with browser UA); the legacy `matches/engine/match/{id}.json` is dead."*
4. **[VERIFIED]** `albtree/cricreadR` README: *"After a 10 month hiatus due to an ESPNCricInfo API
   permission change... Due to new costs involved, only select competitions will be accessible."*
   They ended up paying.

`outside-edge/python-espncricinfo` also has `get_comms_json()` stubbed to `return None`, and its
scorecard parser exposes dismissal only as a **string** (`dismissalText.long`) — no fielder IDs.
i.e. it gives us exactly what we already have.

**`site.api.espn.com` is unaffected and still works.** Only the cricinfo-side hosts are shut.

**UA gotcha, easy to get backwards:**
- `site.api.espn.com` → **rejects** `Mozilla/5.0`, wants a bot UA (see `reference_espn_waf_ua`)
- `www.cricbuzz.com` → **requires** a browser UA; a bot UA gets 403

---

## 4. Cricbuzz — the working recipe **[VERIFIED]**

Two fetches per match. No API key, no quota, no auth encountered.

### 4a. Scorecard — dismissals, fielding, maidens

```
GET https://www.cricbuzz.com/live-cricket-scorecard/{matchId}/{any-slug}
Headers: User-Agent: <browser UA>
```

Cricbuzz is a **Next.js App Router** app now. The old `/api/cricket-match/...` endpoints
302-redirect to the live-scores page (dead), and the app hosts `apiserver.cricbuzz.com` /
`mapps.cricbuzz.com` **do not resolve**. Data ships as RSC flight chunks in the HTML:

```js
self.__next_f.push([1,"...escaped JSON..."])
```

Parse = concatenate the chunks → unicode-unescape → locate the `scoreCard` array.

Real records pulled from match 143971:

```json
{"batId":45502,"batName":"Noah Thain","runs":19,"balls":25,"dots":17,"fours":1,"sixes":1,
 "strikeRate":76,"outDesc":"c A Lyth b George Hill","bowlerId":15239,
 "fielderId1":6511,"fielderId2":0,"fielderId3":0,"wicketCode":"CAUGHT"}

{"bowlerId":18179,"bowlName":"Jack White","overs":5,"maidens":1,"runs":20,"wickets":0,
 "economy":4,"no_balls":0,"wides":1,"dots":0,"balls":30}
```

`wicketCode` values observed: `BOWLED`, `CAUGHT`, `CAUGHTBOWLED`, `LBW`, `RUNOUT`.
(`STUMPED` **[INFERRED]** — schema clearly supports it, not seen in my sample.)

**Direct vs assisted run-out is solved by fielder count.** Verified sample:
`"run out (Shafiqullah Ghafari/O Robinson)"` → `fielderId1:14720, fielderId2:12793`.
One ID = direct (12 pts), two = assisted (6 pts).

**`CAUGHTBOWLED` is its own code** — handle it explicitly (catch credit *and* wicket to the
same bowler) or you'll silently drop catch points.

#### 🚨 TRAP: bowler `dots` is always 0

The bowler record **has** a `dots` field and it is **always `0`**. **[VERIFIED across 14 matches]**,
including matches with both innings bowled. It is a dead schema field.

The populated `dots` you see (17, 39, 10…) is on the **batter** record — balls faced without
scoring. That is *not* what the D11 rule needs.

An existing open-source scraper (`Abdulgsk/cricket-contest/lib/scrapers/cricbuzz-scorecard.ts`)
does `dots: num(w.dots)` on the bowling line and therefore inherits a silently-zero dots column.
Do not copy that.

### 4b. mcenter commentary — derives the missing bowler dots

```
GET https://www.cricbuzz.com/api/mcenter/{matchId}/full-commentary/{inningsId}
Headers: User-Agent: <browser UA>, Referer: https://www.cricbuzz.com/
```

**[VERIFIED]** 200, clean JSON, no auth. ~175KB / 277 balls for one innings. This is a real JSON
API — *not* flight-payload scraping. Undocumented; found by diffing a commit in
`rsumit123/ipl_live` (`1572ebe`, Apr 2026).

Per-ball shape:

```json
{"ballNbr":242,"overNumber":40.2,"inningsId":1,"event":"WICKET","legalRuns":0,"totalRuns":0,
 "batsmanStriker":{"batId":1458155,"batName":"Charlie Bennett","batRuns":15,"batBalls":27,...},
 "bowlerStriker":{"bowlId":15239,"bowlName":"George Hill","bowlOvs":5.2,"bowlMaidens":1,
                  "bowlRuns":14,"bowlWkts":2,"bowlWides":1,"bowlNoballs":0,"bowlEcon":2.6}}
```

Also carries `matchDetails.matchHeader` with `matchFormat` (`"ODI"` — feeds our ODI/T20/HUN
branch), `state` (`"Complete"`), `complete: true`, toss and result. That's a more authoritative
match state than our current path has.

#### Deriving bowler dots

Count legal deliveries where `totalRuns == 0`, grouped by `bowlId`. **Legality must come from
the running extras counters, not a regex on commentary text** — regex gave 7/12 reconciliation,
counters gave 11/12:

```python
prev = {}   # bowlId -> (bowlWides, bowlNoballs)
for e in sorted(commentaryList, key=lambda x: x.get('ballNbr', 0)):
    b = e.get('bowlerStriker') or {}
    bid = b.get('bowlId')
    if not bid or e.get('ballNbr', 0) == 0:
        continue
    w, nb = b.get('bowlWides', 0), b.get('bowlNoballs', 0)
    pw, pnb = prev.get(bid, (0, 0))
    legal = (w == pw and nb == pnb)      # extras counters didn't move => legal ball
    prev[bid] = (w, nb)
    if not legal:
        continue
    balls[bid] += 1
    if e.get('totalRuns', 0) == 0:
        dots[bid] += 1
```

**[VERIFIED]** result on match 143971 (both innings), derived balls vs the scorecard's `balls`:

```
bowler               drv_balls card_balls  drv_dots
Dominic Bess                54         54        30
Jack White                  48         48        35
Ben Coad                    48         48        33
Jafer Chohan                48         48        20
Simon Harmer                46         46        27
Charlie Bennett             36         36        22
Mackenzie Jones             35         36        25   <-- off by -1
George Hill                 32         32        24
Mitchell Killeen            30         30        17
Zaman Akhter                24         24        11
Matthew Critchley           18         18         5
Adam Lyth                   12         12         7

balls reconcile: 11/12
```

**Known open bug:** the −1 is an edge case in legal-ball detection — **[INFERRED]** a wide on the
first ball of a spell, where `prev` has no prior value to diff against. Seed `prev[bid]` from the
bowler's first-seen counters rather than `(0,0)`. Not yet fixed.

### 4c. The reconcile IS the healthcheck

Derived legal balls per bowler **must equal** the scorecard's `balls`. Assert this every match.

This matters more than it looks. Both endpoints are undocumented and unversioned — Cricbuzz owes
us no stability. The classic failure mode (cf. the ESPN WAF incident) is a **silent** one: the
parse returns nothing and it reads as "no data available" rather than "the parser broke." A
per-match checksum converts that into a loud failure.

---

## 5. Crex — not a provider, don't bother **[VERIFIED]**

`crex.live` now redirects to `crex.com`. Its front-end calls `api.goscorer.com/api/v3`,
`crexweb.crickapi.com`, `oc.crickapi.com` — i.e. **Crex is a UI on top of GoScorer / crickapi.com**,
a commercial scoring vendor. Scraping it means scraping someone else's licensed feed.

Technically it's also a far worse target than Cricbuzz:

| | Cricbuzz | Crex |
|---|---|---|
| Rendering | Next.js SSR — data in the HTML | Angular CSR — **nothing** in the HTML (0 hits for `maidens`/`dots`/`wicketCode`/`fielder`) |
| Auth | none | `authorization` header required |
| Routes | stable URL paths | computed inside a 682KB minified bundle |

Backends are alive and routing (`{"error":"Route not found",...}`) but every plausible REST shape
404s and no static key exists in the bundle → runtime handshake you'd have to replicate on every
rebuild. **Field coverage is entirely UNVERIFIED** — likely good given its Dream11 orientation,
but that's product-positioning inference, not evidence.

---

## 6. GitHub survey — what exists

**Useful:**
- `rsumit123/ipl_live` @ `1572ebe` — **the find.** Source of the mcenter endpoint. No license, so
  don't lift code; a URL isn't copyrightable. Take the endpoint, write our own client.
- `Abdulgsk/cricket-contest` → `lib/scrapers/cricbuzz-scorecard.ts` — TypeScript, same stack as
  wwc-draft, independently derived the flight-payload technique and extracts `wicketCode` +
  `fielderIds[]`. **No license file (all rights reserved) → read as a spec, do not copy.**
  Also carries the zero-dots bug (§4a).

**Broken / irrelevant:**
- `tarun7r/Cricket-API` (52★, Apr 2025) — BeautifulSoup over legacy `cb-*` CSS classes.
  **[VERIFIED]** 0 hits for five of its selectors against live Cricbuzz HTML. The Next.js
  migration deleted that entire class vocabulary.
- `tarun7r/cricket-mcp-server` (14★, MIT, Aug 2025) — same author, same technique, same breakage.
- `xoraus/LiveCricScore` (0★, Java, Jan 2024) — CWC-2023 Spring Boot demo. No scorecard depth.
- `outside-edge/python-espncricinfo` (197★) — see §3.

**Note:** 328 repos reference `hs-consumer-api`, which is why it looks like the obvious answer in
any search. They predate the block. Don't be misled by the volume.

---

## 7. The real cost, and what's still open

**The blocker is identity, not data.** Cricbuzz's `batId` / `bowlerId` / `fielderId` are
**Cricbuzz profile IDs**. Our spine is `ci:` cricinfo IDs (see the 25 Jul 2026 migration). Using
Cricbuzz means a `cricbuzz_id ↔ cricinfo_id` bridge **in the shared registry** — not a local
per-app alias map (see `feedback_identity_in_shared_registry`).

**[VERIFIED]** `fielderId` does **not** resolve from the scorecard payload alone — I tested it:
**0 of 2** fielder IDs were resolvable from the payload's own id→name maps. A slip fielder who
hasn't batted or bowled yet is a bare integer with no name anywhere in the response. Without the
bridge, **fielding points land on nobody.**

The fallback — parsing names out of `outDesc` — is fuzzy name matching, i.e. precisely what
corrupted 20 live rows (see `NAME_MATCH_AND_ISSUES_CRITICAL.md`). **Do not go there.**
`Abdulgsk/cricket-contest` solved it the right way: a squad scraper storing Cricbuzz profile IDs
so scoring joins on ID and never on name.

**Open items:**
- [ ] `cricbuzz_id ↔ ci:` bridge in the shared registry — the actual work
- [ ] Playing XI / `played` flag — `matchDetails.matchHeader` looks like it carries it
      **[UNVERIFIED]**; otherwise a second fetch of `/cricket-match-squads/{matchId}`
- [ ] Fix the −1 legal-ball edge case (§4b)
- [ ] Confirm `STUMPED` wicketCode against a real stumping **[INFERRED only]**
- [ ] Decide scope: bot L1 recon vs live-only (§8)

**ToS:** scraping is against Cricbuzz's terms. Private friends' app at low volume → practical
risk is an IP block, not legal action. But it can vanish without notice, hence §4c.

---

## 8. Recommendation

**Do NOT add Cricbuzz as a 4th L1 feed to the bot.** The identity-bridge cost plus undocumented-
endpoint risk isn't worth it on numbers that settle money, when cricsheet already gives all of
this correctly at L2.

**DO consider it for the live provisional path only** — `wwc-draft/lib/d11-score.ts:22-23`, where
`bowlLbwBowled`, `catches`, `stumpings` and `runOuts` are currently hardcoded to `0` and we
knowingly under-credit fielding. That's the one genuine gap, and Cricbuzz is now the only free
thing that closes it.

**Worth internalising:** the `machina-sports` team researched this independently in June 2026 and
landed on **ESPN site API for live-ish + Cricsheet for ball-by-ball truth** — our exact current
architecture. Our stack is not a compromise we settled for; it's what people converge on.
cricapi + ESPN + cricsheet is at the ceiling of what free sources give a fantasy scorer.
