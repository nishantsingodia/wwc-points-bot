#!/usr/bin/env python3
"""Resolve and download the cricsheet (L2) archives that THIS run's tours actually need.

WHY THIS EXISTS
The download step in `.github/workflows/wwc-points.yml` used to be a hand-written list of curl
lines — one per league, added by editing YAML. That made cricsheet the ONLY one of the three feeds
that a new tour could not get on its own: ESPN resolves at ingest, Cricbuzz resolves and validates
at ingest (`tour_sync.resolve_cricbuzz_series`), and cricsheet sat there waiting for someone to
remember. A tour whose zip nobody added scores PROVISIONAL off ESPN for its whole run and the L2
reconciliation never fires — the numbers are never made official.

It is also SELF-HEALING, which a pinned list cannot be. cricsheet publishes a brand-new league's
archive only once that league exists in its data — the European T20 Premier League had no archive
at all on 28 Aug 2026, its first week. A hardcoded list frozen at ingest would leave that tour on
ESPN-provisional forever. This resolves against cricsheet's live index on EVERY run, so the archive
starts being downloaded the day cricsheet publishes it, with no edit anywhere.

WHAT IT DOES
  tours.json  ->  the set of archives this run needs  ->  download + unzip into $CRICSHEET_DIR
    · base archive per FORMAT in play: T20 -> t20s, ODI -> odis, TEST -> tests.
      (The internationals zips do NOT contain league matches — that is the whole reason leagues
      need their own.)
    · league archive per tour whose name matches a cricsheet league (CPL, LPL, MLC, The Hundred…).
    · `"cricsheet_archive": "cpl_json.zip"` on a tours.json entry PINS that tour's archive and
      skips name resolution. `"none"` says "cricsheet does not cover this league" and silences the
      report for it.

Name matching is deliberately conservative. cricsheet's index lists ~1100 archives and most are
TEAMS ("Lions", "Falcons", "Sunrisers"), so a naive substring match would happily map a tour onto a
club. A candidate must match on the tour's league name after stripping the season year and the
"Men's/Women's Competition" tail, must be at least MIN_LEAGUE_CHARS long, must not CONTRADICT the
tour's gender, and is then ranked exact > cricsheet-more-general > cricsheet-more-specific, with an
explicitly gender-matching row outranking a neutral one. Anything unmatched is REPORTED, not
guessed: a wrong archive is worse than a missing one, because it resolves matches to another
league's cards. (Longest-wins was tried first and picked "Women's Caribbean Premier League" for the
men's CPL — the women's name simply contains the men's.)

Exit code is 0 even when an archive is missing — cricsheet being slow, down, or simply not covering
a league must never fail the points run (matches stay PROVISIONAL and the next run retries).
Only an unreadable tours.json is fatal.

Usage:
    python3 cricsheet_archives.py [--dir cricsheet] [--dry-run]
"""

import argparse
import html
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

INDEX_URL = "https://cricsheet.org/downloads/"
DL_BASE = "https://cricsheet.org/downloads/"
# cricsheet's own UA policy is unremarkable, but be an honest citizen like every other fetcher here.
UA = "wwc-points-bot/1.0 (+https://github.com/nishantsingodia/wwc-points-bot)"

# A cricsheet league name shorter than this is almost always a team or an abbreviation and is far
# too easy to hit by accident ("Lions", "Titans", "Spirit" are all real rows in that index).
MIN_LEAGUE_CHARS = 10

# The internationals archive for each format a tour can declare. HUN has no international archive —
# the Hundred is a league and is resolved by name like any other.
FORMAT_ARCHIVE = {"T20": "t20s_json.zip", "ODI": "odis_json.zip", "TEST": "tests_json.zip"}


def _norm(s):
    """Lowercase alphanumerics + single spaces. Drops punctuation and the possessive in
    "Men's", so "The Hundred Men's Competition" and "the hundred mens competition" agree."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (s or "").lower())).strip()


def _league_key(tour_name):
    """The tour name reduced to its LEAGUE identity: season year and the gender/competition tail
    removed. "The Hundred Men's Competition 2026" -> "the hundred". "Caribbean Premier League
    2026" -> "caribbean premier league"."""
    s = re.sub(r"\b(19|20)\d{2}\b", " ", tour_name or "")
    s = re.sub(r"\((?:[^)]*)\)", " ", s)                       # "(Men T20I)" and friends
    s = re.sub(r"\b(men|mens|women|womens|man|woman)s?\b", " ", s, flags=re.I)
    s = re.sub(r"\b(competition|tournament|season|edition)\b", " ", s, flags=re.I)
    return _norm(s)


def fetch_index(timeout=60):
    """cricsheet's download table -> {normalised league name: 'code_json.zip'}.

    Parsed from the table's `<td class="name">` cell plus the FIRST *_json.zip link in that row —
    which is the combined (all-gender) archive. Taking the combined one matters for the Hundred:
    men's and women's live in one `hnd_json.zip`, and the gendered `hnd_male_json.zip` /
    `hnd_female_json.zip` would each cover only half the tours that need it.
    """
    req = urllib.request.Request(INDEX_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        page = r.read().decode("utf-8", "replace")
    out = {}
    # Each row: <td class="name"> NAME </td> ... <a href="/downloads/<code>_json.zip">
    for m in re.finditer(r'<td class="name">(.*?)</td>(.*?)</tr>', page, re.S):
        name = html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).strip()
        zips = re.findall(r'href="/downloads/([A-Za-z0-9_%\-\'&;]+_json\.zip)"', m.group(2))
        if not name or not zips:
            continue
        # First link in the row = the combined archive (gendered variants follow it).
        key = _norm(name)
        if key and key not in out:
            out[key] = zips[0]
    return out


def resolve_for_tour(tour, index):
    """-> (archive_or_empty, why). Honours a pin; otherwise name-matches against the index."""
    pin = str(tour.get("cricsheet_archive") or "").strip()
    if pin.lower() in ("none", "-", "n/a"):
        return "", "pinned 'none' — cricsheet does not cover this league"
    if pin:
        return pin, f"pinned in tours.json ({pin})"
    key = _league_key(tour.get("name", ""))
    if not key:
        return "", "tour has no usable name"
    female = (tour.get("gender") or "").lower().startswith("f")
    # RANKED, not longest-wins. Longest-wins picked "Women's Caribbean Premier League" for the
    # men's CPL — the women's name simply CONTAINS the men's, so it scored higher on length while
    # being the wrong tournament. An archive from the wrong competition is the worst outcome
    # available here: every match resolves against another event's cards.
    #   3  exact                      — "caribbean premier league" == "caribbean premier league"
    #   2  cricsheet name ⊂ tour key  — cricsheet is the more general label
    #   1  tour key ⊂ cricsheet name  — cricsheet is MORE specific, i.e. it names a competition
    #                                   the tour did not; only ever a last resort.
    best = None
    for cs_name, zipname in index.items():
        if len(cs_name) < MIN_LEAGUE_CHARS:
            continue
        # A cricsheet row is gender-NEUTRAL unless it says otherwise, and a neutral row serves both
        # genders — `hnd_json.zip` is literally one combined archive holding the men's and women's
        # Hundred. So reject only an EXPLICIT contradiction (that is what kills the women's CPL for
        # a men's tour); never infer "male" from the mere absence of "women", which would have left
        # the women's Hundred with no archive at all.
        cs_female = bool(re.search(r"\bwomens?\b|\bwoman\b", cs_name))
        cs_male = bool(re.search(r"\bmens?\b|\bman\b", cs_name)) and not cs_female
        if (cs_female and not female) or (cs_male and female):
            continue
        # An explicitly gender-matching row outranks a neutral one even on a weaker text score: for
        # a women's tour, "Women's Caribbean Premier League" is the right archive and the neutral
        # "Caribbean Premier League" (men's data) is the wrong one, however exactly it matches.
        gmatch = 1 if (cs_female and female) or (cs_male and not female) else 0
        if cs_name == key:
            score = 3
        elif cs_name in key:
            score = 2
        elif key in cs_name:
            score = 1
        else:
            continue
        cand = (gmatch, score, len(cs_name), cs_name, zipname)
        if best is None or cand > best:
            best = cand
    if best:
        gmatch, score, _, cs_name, zipname = best
        note = "" if score == 3 and not gmatch else f" (score {score}, gender-specific={bool(gmatch)})"
        return zipname, f"matched cricsheet league {cs_name!r}{note}"
    return "", "no cricsheet archive for this league (yet) — will retry next run"


def plan(tours, index):
    """-> (ordered archive list, per-tour report rows)."""
    need, rows = [], []
    for t in tours:
        fmt = (t.get("format") or "T20").upper()
        base = FORMAT_ARCHIVE.get(fmt, "")
        arch, why = resolve_for_tour(t, index)
        # A league tour needs its own archive; an international one is covered by the format zip.
        # We take BOTH when a league match exists and the format has a base archive, because the
        # format zip is cheap, shared across tours, and already downloaded for someone else.
        for a in (base, arch):
            if a and a not in need:
                need.append(a)
        rows.append({"tour": t.get("name", ""), "format": fmt,
                     "base": base, "league": arch, "why": why})
    return need, rows


def download(archives, dest, dry_run=False):
    """curl+unzip each archive into `dest`. Never raises: a failed archive is reported and the
    run continues on ESPN/Cricbuzz (matches stay PROVISIONAL until a later run gets it)."""
    os.makedirs(dest, exist_ok=True)
    ok, failed = [], []
    for a in archives:
        if dry_run:
            print(f"  would download {a}")
            ok.append(a)
            continue
        tmp = os.path.join(dest, f".{a}")
        try:
            subprocess.run(["curl", "-fsSL", "--retry", "3", "--retry-delay", "5",
                            "--max-time", "300", "-A", UA, "-o", tmp, DL_BASE + a], check=True)
            subprocess.run(["unzip", "-o", "-q", tmp, "*.json", "-d", dest], check=True)
            ok.append(a)
            print(f"  ✓ {a}")
        except Exception as e:
            failed.append(a)
            print(f"  ✗ {a}: {e}", file=sys.stderr)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    return ok, failed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tours", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                    "tours.json"))
    ap.add_argument("--dir", default=os.environ.get("CRICSHEET_DIR", "cricsheet"))
    ap.add_argument("--report", default=os.environ.get("CRICSHEET_REPORT",
                                                       "cricsheet_resolved.json"),
                    help="where to leave the per-tour resolution for the scoring run's sheet report")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    tours = json.load(open(args.tours))          # only a broken tours.json is fatal
    try:
        index = fetch_index()
        print(f"cricsheet index: {len(index)} archives", file=sys.stderr)
    except Exception as e:
        # No index = no name resolution. Fall back to the format archives so the internationals
        # still reconcile; leagues wait for the next run. Better than downloading nothing.
        print(f"cricsheet index unavailable ({e}) — falling back to format archives only",
              file=sys.stderr)
        index = {}

    need, rows = plan(tours, index)
    width = max((len(r["tour"]) for r in rows), default=10)
    for r in rows:
        got = r["league"] or "—"
        print(f"  {r['tour']:{width}}  {r['format']:4}  base={r['base'] or '—':16} "
              f"league={got:24} {r['why']}", file=sys.stderr)
    missing = [r for r in rows if not r["league"] and r["format"] == "HUN"]
    for r in missing:
        print(f"  ⚠ {r['tour']}: format HUN has NO international archive and no league match — "
              f"this tour has NO L2 source at all", file=sys.stderr)

    print(f"downloading {len(need)} archive(s) into {args.dir}: {', '.join(need)}", file=sys.stderr)
    ok, failed = download(need, args.dir, dry_run=args.dry_run)
    print(f"cricsheet: {len(ok)} ok, {len(failed)} failed", file=sys.stderr)

    # Leave the resolution behind for the scoring run, which reports each tour's three feeds into
    # the TOUR CONTROL tab. Writing it here rather than re-resolving there keeps the cricsheet
    # index to ONE fetch per run and means the sheet reports what was actually downloaded, not a
    # second opinion that could disagree with it.
    try:
        report = {r["tour"]: {"archive": r["league"] or r["base"] or "",
                              "league": r["league"], "base": r["base"],
                              "why": r["why"],
                              "downloaded": (r["league"] or r["base"] or "") in ok}
                  for r in rows}
        with open(args.report, "w") as fh:
            json.dump(report, fh, indent=1, sort_keys=True)
        print(f"wrote {args.report}", file=sys.stderr)
    except Exception as e:
        print(f"could not write {args.report} ({e}) — the sheet's Feeds column will show '?'",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
