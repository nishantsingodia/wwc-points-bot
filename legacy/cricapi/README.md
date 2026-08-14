# cricapi — archived 14 Aug 2026

Retired as the L1 second witness. **Not imported by the live scorer.**

## Why

| | cricapi | Cricbuzz |
|---|---|---|
| L1 fields cross-checked | 4 | **14** |
| `dots` / `maidens` | supplies neither | supplies both |
| dismissal type, fielder attribution | no | yes |
| run-out attribution | no | yes (ESPN gives 0/24 fielder ids) |
| accuracy vs cricsheet | 33/56 (59%) on disputed fields | 1098/1098 exact on the compared corpus |
| quota | 100/day, the binding constraint on run frequency | none |

`dots` and `maidens` both score points and had **no second opinion at all** under cricapi — that is
the gap this replacement closes, not merely a like-for-like swap.

## What still depends on it

Eight ENDED tours were settled with cricapi as their witness, and their `recon_overrides` rows say
so: Women's T20 WC 2026, Australia tour of Bangladesh, Major League Cricket 2026, India tour of
Ireland, India tour of England, Ireland vs West Indies Women's ODI, New Zealand vs West Indies Men's
ODI, India tour of Zimbabwe.

They are dormant — `is_active()` skips a tour past its `ends`, so nothing polls them. But
**re-scoring one would need this module back.** That is why this is an archive and not a delete.

## Files

- `cricapi.py` — `api()` and `parse_match()` lifted verbatim, standalone.
- `wc_fps_to_csv.at-removal.py` — the complete scorer as it stood at removal. Diff against the live
  file to see exactly what came out.

## If you revive it

⚠ **`S1` is a SLOT, not a source.** It means "this tour's second witness" — cricapi everywhere until
13 Aug, Cricbuzz on LPL / Hundred M+W / CPL after. Every L1 row in `registry/recon_overrides.json`
now carries `witness` naming the feed the human actually approved. A revival must respect that stamp
or it will silently reinterpret 935 approvals.

Measured when the switch was made: of the 10 already-approved S1 rows, 8 moved to Cricbuzz and
Cricbuzz's number was **identical to cricapi's on 8/8** — 0 fantasy points moved, out of a 158-FP
surface had they disagreed. The stamp exists so that can never be a matter of luck again.
