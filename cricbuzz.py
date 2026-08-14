#!/usr/bin/env python3
"""Cricbuzz reader — fetch + parse one match into the per-player perf shape wc_fps_to_csv uses,
so Cricbuzz can stand in as the L1 SECOND WITNESS in place of cricapi.

Measured against cricsheet on the two LPL matches with cached payloads (CB 157138 = cricinfo
1537349, CB 157061 = cricinfo 1537342): **48/48 player-rows byte-exact on all 14 scored fields**
(bat r/b/4s/6s, bowl balls/runs/wickets/lbw+bowled/maidens, catches/stumpings/runouts/direct-runouts,
dismissal type) plus 27/27 bowler rows exact on derived dots. That is why this module exists: it
covers five fields cricapi cannot supply at ALL (dots, maidens, dismissal type, fielder attribution,
direct-vs-assisted run-outs) and it has no quota.

⛔ WHAT THIS MODULE DOES NOT DO — IDENTITY.
It keys every player by Cricbuzz's own numeric player id ("cb:<id>"), and there is deliberately NO
name-keyed accessor. Cricbuzz ids are not in people.csv (key_cricbuzz is 0.3% populated — 8 of our
679 players) and Wikidata has no Cricbuzz property, so the cb->cricinfo bridge cannot come from a
lookup table; it has to be DERIVED (performance fingerprint + dismissal join) by a separate bridge
module. Exporting a norm(name)->row dict here would hand every caller the one resolution route this
project forbids (it corrupted 20 live rows). `bat_fingerprint()` / `bowl_fingerprint()` below are
the normalization that bridge must share.

TRANSPORT CONTRACT — read this before using anything here.
Every fetch either returns real bytes or RAISES. Nothing in this module returns {} / [] / 0 on a
failure. That is the project's #1 bug class (a 403 that reads as "the feed has no data"), and on
Cricbuzz it is worse than on ESPN because Cricbuzz answers a perfectly ordinary request with
HTTP 204 + an empty body (see CB_NO_COMMENTARY_FORMATS) — urllib does not raise on a 204, so the
naive `json.load(r)` throws a JSONDecodeError that a bare `except: return {}` turns into
"this match had no deliveries", i.e. zero dots for everyone.
"""
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request

# ⛔⛔ USER-AGENT — THE TWO HOSTS THIS PROJECT SCRAPES HAVE OPPOSITE PROFILES. DO NOT UNIFY THEM.
#   site.api.espn.com  403s a browser UA. wc_fps_to_csv.ESPN_UA must stay an honest bot UA
#                      (CLAUDE.md, "NEVER send a browser User-Agent to ESPN" — cost a day).
#   www.cricbuzz.com   MEASURED 13 Aug 2026 on /live-cricket-scorecard/157138/x:
#                        browser UA -> 200, 609293 B
#                        bot UA     -> 200, 609293 B   (byte-identical)
#                        no UA hdr  -> 200, 609293 B   (byte-identical)
#                      i.e. there is NO UA gate today. The widely-repeated claim that Cricbuzz
#                      REQUIRES a browser UA and 403s a bot UA is REFUTED by that measurement —
#                      do not propagate it as fact.
# A browser UA is still the default here, defensively: it is the string a Cricbuzz WAF is least
# likely to ever start rejecting, and it costs nothing. The load-bearing rule is the DIRECTION:
# this constant must never be passed to an ESPN fetcher, and ESPN_UA must never be "tidied" into
# this one. Keep two constants in two modules on purpose.
CB_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36")
CB_HOST = "https://www.cricbuzz.com"

# Same convention as wc_fps_to_csv.CACHE so one WC_CACHE_DIR covers every feed.
CACHE = os.environ.get("WC_CACHE_DIR", "/tmp/wc_api_cache")

# mcenter (ball-by-ball) returns HTTP 204 + an EMPTY BODY for every Hundred match — verified today
# on CB 144893 innings 1 (204, 0 bytes) against CB 157138 innings 1 (200, 103073 bytes), and by an
# earlier sweep of 144893/144904/144915/145335. It is not an outage and not a retry candidate; the
# endpoint simply does not serve the 100-ball format. See _dots_for_match for what we do instead.
CB_NO_COMMENTARY_FORMATS = frozenset({"HUN"})

# Formats whose scorecard `maidens` field is CORRUPT — see the trap comment in parse_scorecard.
CB_MAIDENS_CORRUPT_FORMATS = frozenset({"HUN"})

# Deliveries per over, used ONLY to rebuild a missing ball count from `overs`. The Hundred's card
# writes overs=1.9 for 19 balls, so a hardcoded 6 would silently under-count every bowler.
_BALLS_PER_OVER = {"HUN": 10}

# Cricbuzz wicketCode -> the cricsheet/wc_fps_to_csv dismissal vocabulary. Verified: all six codes
# that occur in our data map cleanly, and dismissal type matched cricsheet 48/48.
CB_WICKET_CODE = {
    # Cricbuzz spells retired hurt RETD_HURT; unknown codes are passed through lowercased, which
    # would have produced the non-vocabulary "retd_hurt" downstream. Retired hurt is NOT a
    # dismissal and earns the bowler nothing — same rule as ESPN's "retired not out".
    "RETD_HURT": "retired hurt",
    "RETIRED_HURT": "retired hurt",
    "BOWLED": "bowled",
    "CAUGHT": "caught",
    "CAUGHTBOWLED": "caught and bowled",
    "LBW": "lbw",
    "RUNOUT": "run out",
    "STUMPED": "stumped",
    "HITWICKET": "hit wicket",
    "RETIREDHURT": "retired hurt",
    "RETIREDOUT": "retired out",
    "OBSTRUCTING": "obstructing the field",
    "HITBALLTWICE": "hit the ball twice",
}


class CricbuzzError(Exception):
    """Base: something went wrong talking to or reading Cricbuzz. Never means 'no data'."""


class CricbuzzUnavailable(CricbuzzError):
    """We could not obtain the bytes (DNS, timeout, 4xx/5xx, empty body).

    Distinct from 'Cricbuzz says nobody scored' on purpose. A caller that swallows this and
    publishes zeroes has reintroduced the exact failure this project keeps paying for.
    """


class CricbuzzNoContent(CricbuzzUnavailable):
    """HTTP 2xx with an EMPTY body — Cricbuzz's 204 on mcenter for The Hundred.

    Its own class because it is STRUCTURAL, not transient: retrying a Hundred innings forever
    will never produce commentary. Callers branch on it; they must not treat it as zero deliveries.
    """


class CricbuzzNoScorecard(CricbuzzError):
    """The page is a Cricbuzz scorecard page we can read, but it carries no `scoreCard` yet.

    Its own class so "this match has not been scored yet" cannot be confused with "our extraction
    broke". Both would otherwise surface as the same CricbuzzParseError, and the first is routine
    (an upcoming fixture) while the second is an outage that must page a human.
    """


class CricbuzzParseError(CricbuzzError):
    """We got bytes and could not read them — the RSC flight payload changed shape.

    Cricbuzz is an undocumented scrape behind a Next.js app; a prop rename breaks extraction
    instantly. Raising (with a specific reason) is the whole defence: a parser that returned []
    here would look exactly like a match that has not started.
    """


_FAILED = set()


def _warn_once(exc, url):
    """Report each DISTINCT Cricbuzz transport failure once — mirrors wc_fps_to_csv._espn_warn_once.

    Keyed by exception class + HTTP code, so a blanket 403 or a blanket 204 says so on the first
    occurrence instead of 400 identical lines, and a *different* failure later still gets printed.
    """
    key = "%s:%s" % (type(exc).__name__, getattr(exc, "code", ""))
    if key not in _FAILED:
        _FAILED.add(key)
        print("  cricbuzz: fetch failed (%s) — e.g. %s" % (exc, url[:110]), file=sys.stderr)


def _cache_path(key):
    return os.path.join(CACHE, "cb_" + re.sub(r"[^A-Za-z0-9_.-]", "_", key))


def cb_fetch(url, cache_key, fresh=False, referer=True):
    """GET `url`, disk-cached under WC_CACHE_DIR. Returns the body as text, or RAISES.

    A FAILURE IS NEVER CACHED. The cache file is written only after a 2xx with a non-empty body,
    so a 403/timeout/204 can never be replayed later as if it were the match's real content — the
    quiet-poisoning variant of the "absence presents as a value" bug.
    """
    fp = _cache_path(cache_key)
    if not fresh and os.path.exists(fp):
        try:
            with open(fp, encoding="utf-8") as fh:
                body = fh.read()
        except OSError as exc:                       # unreadable cache is not "no data" either
            raise CricbuzzUnavailable("cache read failed for %s: %s" % (fp, exc))
        if body:
            return body
        # A zero-byte cache file should be impossible (we never write one). If one exists, it is
        # corruption, not content — refuse it rather than parse "" into an empty scorecard.
        raise CricbuzzUnavailable("empty cache file %s — refusing to read it as data" % fp)

    headers = {"User-Agent": CB_UA}
    if referer:
        headers["Referer"] = CB_HOST + "/"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        _warn_once(exc, url)
        raise CricbuzzUnavailable("HTTP %s for %s" % (exc.code, url))
    except Exception as exc:                          # URLError, socket timeout, ssl, ...
        _warn_once(exc, url)
        raise CricbuzzUnavailable("%s for %s" % (exc, url))

    body = raw.decode("utf-8", "replace")
    if not body.strip():
        # HTTP 204 (or a 200 with a blank body). urllib does NOT raise here, so without this check
        # the caller's json.load() blows up somewhere far away and reads as "no deliveries".
        exc = CricbuzzNoContent("HTTP %s with an EMPTY body for %s" % (status, url))
        _warn_once(exc, url)
        raise exc

    os.makedirs(CACHE, exist_ok=True)
    try:
        with open(fp, "w", encoding="utf-8") as fh:
            fh.write(body)
    except OSError as exc:                            # a cache we cannot write is not fatal
        print("  cricbuzz: cache write failed (%s)" % exc, file=sys.stderr)
    time.sleep(0.3)                                   # be a polite scraper; CB is undocumented
    return body


def cb_fetch_json(url, cache_key, fresh=False):
    """cb_fetch + json.loads. A body that is not JSON is a CricbuzzParseError, never {}."""
    body = cb_fetch(url, cache_key, fresh=fresh)
    try:
        return json.loads(body)
    except ValueError as exc:
        raise CricbuzzParseError("not JSON (%s) from %s — first 120 bytes: %r"
                                 % (exc, url, body[:120]))


# ---------------------------------------------------------------------------------------------
# RSC flight payload — Cricbuzz is a Next.js app; the scorecard JSON is smuggled into the HTML as
# a sequence of self.__next_f.push([1,"<js string literal>"]) chunks that must be concatenated
# BEFORE searching. The literal we want has never straddled a chunk boundary in the payloads
# examined, but nothing guarantees that, which is exactly why we join first and search after.
# ---------------------------------------------------------------------------------------------
_PUSH = re.compile(r'self\.__next_f\.push\(\[1,\s*"((?:[^"\\]|\\.)*)"\s*\]\)')


def flight_payload(html):
    """Concatenate every RSC flight chunk. Raises CricbuzzParseError if there are none.

    Each chunk is a JS string literal, which is also a JSON string — decoding it as JSON is the
    correct, non-lossy path. `unicode_escape` is the fallback only; it mangles non-ASCII names
    (it decodes bytes as latin-1), so it must never be the primary.
    """
    chunks = _PUSH.findall(html)
    if not chunks:
        raise CricbuzzParseError("no self.__next_f.push chunks in %d bytes of HTML — Cricbuzz "
                                 "changed its page shell, or this is not a scorecard page"
                                 % len(html))
    out = []
    for chunk in chunks:
        try:
            out.append(json.loads('"' + chunk + '"'))
        except ValueError:
            try:
                out.append(chunk.encode("utf-8", "surrogatepass").decode("unicode_escape"))
            except Exception:
                pass                                  # one undecodable chunk; the guards below catch
    return "".join(out)


def _balanced(text, i):
    """Return the balanced [...] / {...} literal starting at text[i], string-aware. None if unclosed.

    String-aware because the payload is full of commentary text containing braces and brackets;
    a naive depth counter closes the literal in the middle of a sentence.
    """
    depth, j, instr, esc = 0, i, False, False
    while j < len(text):
        c = text[j]
        if instr:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = False
        elif c == '"':
            instr = True
        elif c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
        j += 1
    return None                                       # unbalanced => payload truncated


def _extract(payload, key, opener):
    """Pull the JSON value of `"key"` out of the concatenated flight payload."""
    k = payload.find('"%s"' % key)
    if k < 0:
        raise CricbuzzParseError("key %r absent from the flight payload (%d chars)"
                                 % (key, len(payload)))
    b = payload.find(opener, k)
    if b < 0:
        raise CricbuzzParseError("key %r present but no %r follows it" % (key, opener))
    lit = _balanced(payload, b)
    if lit is None:
        raise CricbuzzParseError("unbalanced literal after %r — payload truncated?" % key)
    try:
        return json.loads(lit)
    except ValueError as exc:
        raise CricbuzzParseError("json decode failed for %r: %s" % (key, exc))


def scorecard_html(match_id, fresh=False):
    """Raw scorecard HTML. The URL slug is decorative — /x works (verified 200 on 157138)."""
    return cb_fetch("%s/live-cricket-scorecard/%s/x" % (CB_HOST, match_id),
                    "sc_%s.html" % match_id, fresh=fresh)


def parse_scorecard_html(html):
    """(match_header, score_card) out of a scorecard page. Raises on anything unreadable.

    The header is extracted FIRST on purpose: if it parses, our flight extraction is working and a
    missing `scoreCard` means the match simply has no card yet (CricbuzzNoScorecard), not that
    Cricbuzz renamed a prop under us (CricbuzzParseError). Collapsing the two would make an
    upcoming fixture indistinguishable from an outage.
    """
    payload = flight_payload(html)
    header = _extract(payload, "matchHeader", "{")
    try:
        card = _extract(payload, "scoreCard", "[")
    except CricbuzzParseError as exc:
        if "absent" in str(exc):
            raise CricbuzzNoScorecard("match %s has no scoreCard yet (state=%r)"
                                      % (header.get("matchId"), header.get("state")))
        raise
    if not isinstance(card, list):
        raise CricbuzzParseError("scoreCard is %s, expected list" % type(card).__name__)
    return header, card


def commentary_innings(match_id, innings_id, fresh=False):
    """Ball-by-ball for one innings, OLDEST-FIRST.

    ⛔ ORDERING. Cricbuzz ships this list NEWEST-FIRST and `ballNbr` TIES across a wide and the
    legal ball that follows it (measured: 5/8/3/8 duplicate ballNbr values in the four cached LPL
    innings). Sorting by ballNbr therefore reorders extras against their own delivery and breaks
    the legal/illegal counter diff below — an earlier probe did exactly that and mis-derived a
    bowler's first ball of every spell. `timestamp` IS monotonic once reversed (verified on all
    four innings), so a plain reverse() is both correct and sufficient.

    Raises CricbuzzNoContent on The Hundred (mcenter is a 204 there).
    """
    data = cb_fetch_json("%s/api/mcenter/%s/full-commentary/%s" % (CB_HOST, match_id, innings_id),
                         "cm_%s_%s.json" % (match_id, innings_id), fresh=fresh)
    blocks = data.get("commentary")
    if not isinstance(blocks, list):
        raise CricbuzzParseError("mcenter %s/%s: no 'commentary' list" % (match_id, innings_id))
    for blk in blocks:
        if blk.get("inningsId") == innings_id:
            return list(reversed(blk.get("commentaryList") or []))
    if len(blocks) == 1 and blocks[0].get("inningsId") is None:
        return list(reversed(blocks[0].get("commentaryList") or []))
    # Do NOT fall back to blocks[0] when it carries a DIFFERENT inningsId. Returning innings 2's
    # deliveries for a request for innings 1 double-counts one innings and drops the other, and
    # every downstream number still looks plausible.
    raise CricbuzzParseError("mcenter %s/%s: no block for innings %s (got %s)"
                             % (match_id, innings_id, innings_id,
                                [b.get("inningsId") for b in blocks]))


def derive_bowling_from_commentary(entries):
    """Per-bowlId legal balls + DOTS from ONE INNINGS of ball-by-ball.

    ⚠ One innings at a time. The bowlWides/bowlNoballs counters restart at 0 every innings, so
    concatenating two innings makes the first ball of a bowler's second spell read as "the counter
    went DOWN" — the legality test below only checks for equality, so it is flagged illegal and his
    ball count drops by one. Harmless in a T20/ODI (nobody bowls in both innings), a real
    undercount in a TEST. _dots_for_match therefore loops per innings.

    ⛔ THE DOT RULE — legal delivery AND **no runs off the bat**.
    `legalRuns` is Cricbuzz's runs-off-the-bat counter (verified: a wide reads legalRuns=0 /
    totalRuns=1; "byes, 1 run" reads legalRuns=0 / totalRuns=1; a boundary of byes reads
    legalRuns=0 / totalRuns=4). Using `totalRuns == 0` instead — the recipe in the earlier eval
    doc — throws away every delivery that went for byes or leg-byes, which cricsheet and Dream11
    still score as a DOT for the bowler because those runs are the keeper's leak, not his.
    Measured cost of the wrong recipe on the two cached LPL matches: 12/27 bowler rows correct
    (-21 dots = 21 fantasy points under-credited); with `legalRuns == 0`: 27/27 exact vs cricsheet.
    This is the same definition wc_fps_to_csv already uses on ESPN, where `bcharged` subtracts
    byes/leg-byes before testing for a dot.

    LEGAL/ILLEGAL is taken from the per-bowler cumulative `bowlWides`/`bowlNoballs` counters: a
    delivery is illegal iff one of them moved. Cross-checked against the commentary text
    ("wide"/"no ball" in commText or the bold format value) on all 504 cached deliveries —
    0 disagreements — so the two independent signals corroborate each other.
    """
    balls, dots, conceded, names = {}, {}, {}, {}
    prev = {}
    for e in entries:
        if not e.get("ballNbr"):
            continue                                   # over-break / preamble rows, not deliveries
        b = e.get("bowlerStriker") or {}
        bid = b.get("bowlId")
        if not bid:
            continue
        names[bid] = b.get("bowlName") or ""
        w, nb = b.get("bowlWides", 0) or 0, b.get("bowlNoballs", 0) or 0
        pw, pnb = prev.get(bid, (0, 0))                # counters are innings-cumulative from 0
        legal = (w == pw and nb == pnb)
        prev[bid] = (w, nb)
        if not legal:
            continue
        balls[bid] = balls.get(bid, 0) + 1
        conceded[bid] = conceded.get(bid, 0) + (e.get("totalRuns", 0) or 0)
        if not (e.get("legalRuns", 0) or 0):
            dots[bid] = dots.get(bid, 0) + 1
    # `conceded` here is totalRuns off LEGAL deliveries only — it is NOT the bowler's figure and
    # must never be compared to the card's `runs`. It drops the runs off wides/no-balls (which ARE
    # charged to him) and includes byes/leg-byes (which are NOT). Measured on the earlier probe:
    # Jack White derived 21 vs card 27. It is exposed for diagnostics; runs_conceded comes from
    # the card. Do not try to extend the ball-count checksum to it.
    return {"balls": balls, "dots": dots, "conceded": conceded, "names": names}


# ---------------------------------------------------------------------------------------------
# Per-player record. Field names are the wc_fps_to_csv.blank_perf contract so a caller can diff a
# Cricbuzz row against an ESPN/cricapi row field-for-field; tests/test_cricbuzz.py asserts the
# superset relationship so a change to blank_perf cannot silently desync this.
# ---------------------------------------------------------------------------------------------
def blank_cb_perf(name, cb_id):
    return dict(
        name=name, cb_id=int(cb_id), team="", team_id=0,
        r=0, b=0, **{"4s": 0, "6s": 0},
        dismissed=False, dismissal="",
        balls=0, runs_conceded=0, w=0, lbwb=0,
        dots=None,        # None until a source is proven — see _dots_for_match. NEVER default 0.
        maidens=None,     # None until a source is proven — corrupt on HUN. NEVER default 0.
        catches=0, stumpings=0, runouts=0, dro=0,
        played=False, bat_order=0,
        # ---- Cricbuzz-specific, needed by the identity bridge and by direct/assisted run-outs ----
        batted=False,     # False for a did-not-bat row: see the DNB trap in parse_match
        bowled=False,
        is_captain=False, is_keeper=False,
        wides=0, noballs=0,
        runout_fielder_counts=[],   # one entry per run-out involvement: how many fielders shared it
        balls_derived=False,        # ball count rebuilt from `overs` rather than read from `balls`
    )


def bat_fingerprint(p):
    """(runs, balls, 4s, 6s) for the bridge — or None if the player DID NOT BAT.

    ⛔ MANDATORY NORMALIZATION. Cricbuzz emits an all-zero batting row for a did-not-bat player;
    ESPN omits the player entirely. Treating that row as a real 0-off-0 innings makes it collide
    with every other DNB on the Cricbuzz side while having no counterpart at all on the ESPN side,
    and measured bridge coverage collapses 98% -> 75%. Any consumer of these records must go
    through this function rather than reading p["r"]/p["b"] directly.
    """
    if not p.get("batted"):
        return None
    return (p["r"], p["b"], p["4s"], p["6s"])


def bowl_fingerprint(p):
    """(balls, runs_conceded, wickets, maidens) for the bridge — or None if he did not bowl.

    `maidens` is None on The Hundred (corrupt field, hard-ignored), which makes the 4-tuple
    unusable there; callers should fall back to the 3-tuple rather than substituting 0.
    """
    if not p.get("bowled"):
        return None
    return (p["balls"], p["runs_conceded"], p["w"], p["maidens"])


class CricbuzzMatch(object):
    """One parsed match. `perf` is keyed by "cb:<cricbuzzPlayerId>" — NOT by name (see module doc)."""

    def __init__(self, match_id, header, perf, dismissals, warnings,
                 dots_source, maidens_source, innings):
        self.match_id = int(match_id)
        self.header = header
        self.perf = perf
        self.dismissals = dismissals
        self.warnings = warnings
        self.dots_source = dots_source          # "commentary" | "card" | None  (None => no dots)
        self.maidens_source = maidens_source    # "card" | None                (None => no maidens)
        self.innings = innings

    # -- convenience readers over the header, so callers never guess at Cricbuzz's key names --
    @property
    def fmt(self):
        """T20 / ODI / TEST / HUN. Cricbuzz labels The Hundred "HUN" — same token this repo uses."""
        return (self.header.get("matchFormat") or "").upper()

    @property
    def complete(self):
        return bool(self.header.get("complete"))

    @property
    def state(self):
        return self.header.get("state") or ""

    @property
    def status(self):
        return self.header.get("status") or ""

    @property
    def series_id(self):
        return self.header.get("seriesId")

    @property
    def teams(self):
        return [(self.header.get("team1") or {}).get("name") or "",
                (self.header.get("team2") or {}).get("name") or ""]

    @property
    def start_ms(self):
        return self.header.get("matchStartTimestamp")

    def __repr__(self):
        return ("<CricbuzzMatch %s %s %s players=%d dots=%s maidens=%s warnings=%d>"
                % (self.match_id, self.fmt, self.state, len(self.perf),
                   self.dots_source, self.maidens_source, len(self.warnings)))


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _dots_for_match(match_id, fmt, card, perf, cb_key, warnings, fresh=False):
    """Fill in per-bowler `dots`, and return the source string (or None if we have none).

    TWO DIFFERENT SOURCES, because the card's `dots` field means different things by format:

      T20 / ODI / TEST — the bowler `dots` column is a DEAD FIELD: 0 for every bowler, always
        (27/27 bowler rows across the two cached LPL matches; an earlier sweep saw the same on 14
        matches). Reading it hands you a silent -N points per bowler, so dots MUST be derived from
        mcenter ball-by-ball with the legalRuns==0 rule.

      HUN — mcenter is a 204 (no ball-by-ball exists), and the card's `dots` IS populated and
        correct: verified against cricsheet on CB 144893, where Cricbuzz's per-bowler dots equal
        cricsheet's runs-off-the-bat-zero count. So the card is the only source, and it is a good
        one — but there is then NO independent derivation to check it against.

    If neither source works, dots stay None. They do NOT become 0: per the locked recon model an
    absent single-source field is UNCONSUMED DATA and must keep the match LIVE with a named row,
    not score as a bowler who bowled a maiden-less spell of no dots.
    """
    bowler_rows = [v for inn in card
                   for v in ((inn.get("bowlTeamDetails") or {}).get("bowlersData") or {}).values()]
    card_dots = [_int(v.get("dots"), 0) for v in bowler_rows]

    if fmt in CB_NO_COMMENTARY_FORMATS:
        if bowler_rows and not any(card_dots):
            # On HUN the card is our ONLY dots source; all-zero means it has gone the way of the
            # T20 card. Refuse rather than publish a zero for every bowler.
            warnings.append("dots: %s card reports 0 dots for all %d bowlers and mcenter is 204 "
                            "for this format — no dots source, leaving them ABSENT"
                            % (fmt, len(bowler_rows)))
            return None
        for inn in card:
            for v in ((inn.get("bowlTeamDetails") or {}).get("bowlersData") or {}).values():
                p = perf.get(cb_key(v.get("bowlerId")))
                if p is not None:
                    p["dots"] = (p["dots"] or 0) + _int(v.get("dots"), 0)
        return "card"

    if bowler_rows and any(card_dots):
        # Non-HUN cards have never carried real dots. If that changes we want to know, not to
        # quietly keep deriving (or quietly start trusting the column).
        warnings.append("dots: the %s scorecard's bowler `dots` column is NON-ZERO (%s) — it has "
                        "always been a dead field on this format; still deriving from commentary"
                        % (fmt, sorted(set(card_dots))))

    derived_balls, derived_dots = {}, {}
    innings_ids = sorted({_int(inn.get("inningsId")) for inn in card if inn.get("inningsId")})
    for inn_id in innings_ids:
        try:
            entries = commentary_innings(match_id, inn_id, fresh=fresh)
        except CricbuzzError as exc:
            warnings.append("dots: innings %d commentary unavailable (%s) — dots left ABSENT "
                            "for every bowler in it" % (inn_id, exc))
            return None
        d = derive_bowling_from_commentary(entries)
        for bid, n in d["balls"].items():
            derived_balls[bid] = derived_balls.get(bid, 0) + n
        for bid, n in d["dots"].items():
            derived_dots[bid] = derived_dots.get(bid, 0) + n

    # ⛔ COMPLETENESS CROSS-CHECK, same idea as espn_expected_balls: the derived ball count comes
    # from mcenter, the reference comes from the scorecard — two endpoints, two field families, so
    # a truncated or half-served commentary feed cannot agree with the card by accident. A bowler
    # whose counts disagree gets dots=None (unconsumed) rather than a plausible-looking undercount.
    ok = 0
    for inn in card:
        for v in ((inn.get("bowlTeamDetails") or {}).get("bowlersData") or {}).values():
            bid = v.get("bowlerId")
            p = perf.get(cb_key(bid))
            if p is None:
                continue
            want = p["balls"]
            got = derived_balls.get(bid)
            if got is None or (want is not None and got != want):
                warnings.append("dots: bowler cb:%s derived %s legal balls from commentary but the "
                                "card says %s — commentary incomplete, dots left ABSENT"
                                % (bid, got, want))
                p["dots"] = None
                continue
            p["dots"] = derived_dots.get(bid, 0)
            ok += 1
    return "commentary" if ok else None


def parse_match(match_id, fresh=False):
    """Fetch + parse one Cricbuzz match into a CricbuzzMatch. Raises on an unreadable scorecard.

    Everything a Dream11 scorer needs, per player: bat runs/balls/4s/6s/dismissed+type;
    bowl balls/runs/wickets/maidens/dots; fielding catches/stumpings/run-outs (with the fielder
    count per run-out so direct-vs-assisted is derivable); and the playing XI.
    """
    header, card = parse_scorecard_html(scorecard_html(match_id, fresh=fresh))
    fmt = (header.get("matchFormat") or "").upper()
    warnings = []
    perf = {}

    def cb_key(pid):
        return "cb:%d" % int(pid)

    def get(pid, name=None):
        pid = _int(pid)
        if not pid:
            return None                                # id 0 == "no fielder recorded"
        k = cb_key(pid)
        if k not in perf:
            perf[k] = blank_cb_perf(name or "", pid)
        if name and not perf[k]["name"]:
            perf[k]["name"] = name
        perf[k]["played"] = True                       # appearing anywhere on the card == played
        return perf[k]

    dismissals = []
    innings_meta = []
    bpo = _BALLS_PER_OVER.get(fmt, 6)

    for inn in card:
        inn_id = _int(inn.get("inningsId"))
        bat_td = inn.get("batTeamDetails") or {}
        bowl_td = inn.get("bowlTeamDetails") or {}
        bat_team = bat_td.get("batTeamName") or ""
        bat_team_id = _int(bat_td.get("batTeamId"))
        bowl_team = bowl_td.get("bowlTeamName") or ""
        bowl_team_id = _int(bowl_td.get("bowlTeamId"))
        innings_meta.append({
            "innings_id": inn_id, "bat_team": bat_team, "bowl_team": bowl_team,
            "runs": _int((inn.get("scoreDetails") or {}).get("runs")),
            "wickets": _int((inn.get("scoreDetails") or {}).get("wickets")),
            "balls": _int((inn.get("scoreDetails") or {}).get("ballNbr")),
            "extras": inn.get("extrasData") or {},
        })

        # ---------------- batting ----------------
        for slot, v in (bat_td.get("batsmenData") or {}).items():
            p = get(v.get("batId"), v.get("batName"))
            if p is None:
                continue
            p["team"], p["team_id"] = bat_team, bat_team_id
            p["is_captain"] = p["is_captain"] or bool(v.get("isCaptain"))
            p["is_keeper"] = p["is_keeper"] or bool(v.get("isKeeper"))
            m = re.search(r"(\d+)$", str(slot))        # keys are "bat_1", "bat_2", ...
            if m and not p["bat_order"]:
                p["bat_order"] = int(m.group(1))
            runs, balls_faced = _int(v.get("runs")), _int(v.get("balls"))
            out_desc = (v.get("outDesc") or "").strip()
            code = (v.get("wicketCode") or "").strip().upper()
            # ⛔ DID-NOT-BAT. Cricbuzz lists the FULL batting order, emitting an all-zero row for
            # everyone who never came in; ESPN and cricapi simply omit them. An all-zero row is
            # therefore an ABSENCE, not "0 runs off 0 balls" — and the two are not interchangeable:
            # a real 0(0) (run out backing up) carries a -2 duck and a fingerprint, a DNB carries
            # neither. Conflating them collapsed the identity bridge from 98% to 75%.
            # The discriminator is the one Cricbuzz actually gives us: no balls, no runs, and an
            # EMPTY outDesc/wicketCode (a genuine 0(0) always has a dismissal).
            did_not_bat = (balls_faced == 0 and runs == 0 and not out_desc and not code)
            if not did_not_bat:
                p["batted"] = True
                p["r"] += runs
                p["b"] += balls_faced
                p["4s"] += _int(v.get("fours"))
                p["6s"] += _int(v.get("sixes"))
            if code:
                p["dismissed"] = True
                p["dismissal"] = CB_WICKET_CODE.get(code, code.lower())
                if code not in CB_WICKET_CODE:
                    warnings.append("unknown Cricbuzz wicketCode %r for cb:%s — passed through "
                                    "lowercased; add it to CB_WICKET_CODE" % (code, v.get("batId")))
            # Fielders are given as IDs (fielderId1..3). ⛔ Never parse `outDesc` for a name:
            # it is a display string ("c Rogers b Nawaz") and the only forbidden identity route.
            fids = [_int(v.get("fielderId%d" % i)) for i in (1, 2, 3)]
            fids = [f for f in fids if f]
            bowler_id = _int(v.get("bowlerId"))
            if code == "CAUGHT" and fids:
                get(fids[0])["catches"] += 1
            elif code == "CAUGHTBOWLED" and bowler_id:
                get(bowler_id)["catches"] += 1         # caught off own bowling: the bowler catches
            elif code == "STUMPED" and fids:
                get(fids[0])["stumpings"] += 1
            elif code == "RUNOUT":
                for f in fids:
                    rp = get(f)
                    rp["runouts"] += 1
                    rp["runout_fielder_counts"].append(len(fids))
                    if len(fids) == 1:
                        rp["dro"] += 1                 # unassisted == direct hit (+12 not +6)
            if code in ("BOWLED", "LBW") and bowler_id:
                get(bowler_id)["lbwb"] += 1            # the +8 bonus, derived from the batter row
            if code:
                dismissals.append({
                    "innings_id": inn_id, "batter_cb_id": _int(v.get("batId")),
                    "type": CB_WICKET_CODE.get(code, code.lower()), "code": code,
                    "bowler_cb_id": bowler_id, "fielder_cb_ids": fids, "out_desc": out_desc,
                })

        # ---------------- bowling ----------------
        for v in (bowl_td.get("bowlersData") or {}).values():
            p = get(v.get("bowlerId"), v.get("bowlName"))
            if p is None:
                continue
            p["team"], p["team_id"] = bowl_team, bowl_team_id
            p["bowled"] = True
            p["is_captain"] = p["is_captain"] or bool(v.get("isCaptain"))
            p["is_keeper"] = p["is_keeper"] or bool(v.get("isKeeper"))
            if v.get("balls") is not None:
                p["balls"] += _int(v.get("balls"))
            elif v.get("overs") is not None:
                # Rebuild from `overs` with the FORMAT's balls-per-over. The Hundred writes
                # overs=1.9 for 19 balls, so the reflexive *6 would under-count every bowler.
                ov = float(v.get("overs") or 0)
                whole = int(ov)
                p["balls"] += whole * bpo + int(round((ov - whole) * 10))
                p["balls_derived"] = True
                warnings.append("bowler cb:%s has no `balls` field — rebuilt %d from overs=%s "
                                "at %d balls/over" % (v.get("bowlerId"), p["balls"], ov, bpo))
            else:
                p["balls"] = None
                warnings.append("bowler cb:%s has neither `balls` nor `overs` — ball count ABSENT "
                                "(economy/SR must not be computed for him)" % v.get("bowlerId"))
            p["runs_conceded"] += _int(v.get("runs"))
            p["w"] += _int(v.get("wickets"))
            p["wides"] += _int(v.get("wides"))
            p["noballs"] += _int(v.get("no_balls"))

    # ---------------- maidens ----------------
    # ⛔ THE HUNDRED SHIPS A CORRUPT `maidens`. On CB 144893 the card's maidens column is a
    # VERBATIM COPY of its dots column on 13/13 bowlers (9/9, 6/6, 4/4, 2/2, 4/4, 10/10, 3/3, 7/7,
    # 4/4, 4/4, 10/10, 5/5, 0/0) while cricsheet records ZERO maidens in the match. At +12 a maiden
    # that is a fabricated ~7 maidens, ~84 points, per bowler. It is harmless only by luck — the
    # Hundred scorer awards no maiden points — so this must be blocked HERE, in the reader, before
    # any shared code path can read the field for a HUN match. maidens stay None (absent), never 0:
    # a 0 would silently pass an L1 comparison against ESPN.
    maidens_source = None
    if fmt in CB_MAIDENS_CORRUPT_FORMATS:
        warnings.append("maidens: HARD-IGNORED for format %s — Cricbuzz copies the dots column "
                        "into it (13/13 bowlers on CB 144893 vs cricsheet's 0 maidens)" % fmt)
    else:
        pairs = []
        for inn in card:
            for v in ((inn.get("bowlTeamDetails") or {}).get("bowlersData") or {}).values():
                p = perf.get(cb_key(v.get("bowlerId")))
                if p is None:
                    continue
                p["maidens"] = (p["maidens"] or 0) + _int(v.get("maidens"))
                pairs.append((_int(v.get("maidens")), _int(v.get("dots"))))
        maidens_source = "card"
        # The corruption is currently format-scoped, but it is a scorer-side bug and could spread.
        # Flag any non-HUN card whose maidens column also mirrors its dots column.
        if len(pairs) >= 3 and any(d for _m, d in pairs) and all(m == d for m, d in pairs):
            warnings.append("maidens: on this %s card maidens == dots for all %d bowlers — that is "
                            "the Hundred corruption signature; treat maidens as UNTRUSTED"
                            % (fmt, len(pairs)))

    dots_source = _dots_for_match(match_id, fmt, card, perf, cb_key, warnings, fresh=fresh)

    # None means ABSENT, and a player who never bowled is not absent — he is a known zero. Leaving
    # him at None would make every batter look like unconsumed data and drown the real signal (a
    # BOWLER whose dots/maidens we could not establish), which is the one thing None is for here.
    for p in perf.values():
        if not p["bowled"]:
            p["dots"], p["maidens"] = 0, 0

    # Innings-internal checksum that also works on HUN (no commentary needed): the bowlers' ball
    # counts must add up to the innings ball count the card itself reports.
    for meta, inn in zip(innings_meta, card):
        rows = ((inn.get("bowlTeamDetails") or {}).get("bowlersData") or {}).values()
        tot = sum(_int(v.get("balls")) for v in rows if v.get("balls") is not None)
        if meta["balls"] and tot and tot != meta["balls"]:
            warnings.append("innings %s: bowler ball counts sum to %d but scoreDetails.ballNbr is "
                            "%d — the card disagrees with itself"
                            % (meta["innings_id"], tot, meta["balls"]))

    if not perf:
        # A card with innings but no players is not "everyone scored nothing" — it is a card we
        # failed to read. Say so; the caller must not publish 22 players on a bare XI bonus.
        warnings.append("NO PLAYERS parsed from a card with %d innings (state=%r) — refuse to "
                        "treat this as a scored match" % (len(card), header.get("state")))

    return CricbuzzMatch(match_id, header, perf, dismissals, warnings,
                         dots_source, maidens_source, innings_meta)


# ---------------------------------------------------------------------------------------------
# ID RESOLUTION.  See the module's report for measured reliability — in short:
#   match id  from (series, date, teams)  = STRUCTURED and reliable (ids come out of the series
#             page's own JSON; the only ambiguity is a same-day double-header of the same pair).
#   series id from a tour name            = NAME-SIMILARITY over the year's archive index, i.e. a
#             PROPOSAL. Resolve once, have a human confirm it, store it — exactly how espn_series
#             is handled in tours.json. Never auto-adopt.
# ---------------------------------------------------------------------------------------------
# Feed-specific spellings of the SAME word. Not fuzzy matching — these are naming conventions that
# differ between providers, so folding them is exact-matching after normalization, not guessing.
# Found live: ESPN writes "St Lucia Kings", Cricbuzz writes "Saint Lucia Kings". resolve_match_id
# requires the team set to be a subset, so those two never met and 2 of 5 completed CPL matches
# silently lost their second witness — reported as "no unique cricbuzz match", which reads like
# Cricbuzz lacking the fixture rather than a spelling difference.
_WORD_FOLD = {"saint": "st", "and": "", "&": ""}
# Gender qualifiers ESPN carries and Cricbuzz does not: "MI London (Men)" vs "MI London". Dropping
# them cannot cross genders, because a Cricbuzz SERIES is already gender-specific (the Hundred is
# 11493 men / 11504 women), so the two never share a fixture list. Without this the Hundred Men's
# paired 0 of 31 completed matches while the Women's paired 31 of 32 — a difference caused purely
# by which side spells the qualifier, and reported as "no unique cricbuzz match" either way.
_GENDER_TOK = frozenset(("men", "mens", "women", "womens"))

# The SHARED team registry — registry/team_aliases.json, the team analog of manual_ci_bridges.
# Consulted here so a REBRANDED franchise still pairs: LPL 2026 renamed Galle Marvels -> Galle
# Gallants, Kandy Falcons -> Kandy Royals, Colombo Strikers -> Colombo Kaps, and feeds disagree
# about which era they are in. Generic string folds cannot fix a rebrand — only the curated map can,
# and keeping it in ONE place is why it exists (a local copy in this module would drift the moment
# someone adds a variant to the registry).
_TEAM_CANON = None

def _team_canon():
    global _TEAM_CANON
    if _TEAM_CANON is None:
        _TEAM_CANON = {}
        try:
            _p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "registry", "team_aliases.json")
            for canon, variants in (json.load(open(_p)).get("aliases") or {}).items():
                for v in list(variants) + [canon]:
                    _TEAM_CANON[_bare_slug(v)] = _bare_slug(canon)
        except Exception:
            _TEAM_CANON = {}
    return _TEAM_CANON


def _bare_slug(s):
    """Normalization only — no alias lookup (used to KEY the alias map, so it cannot recurse)."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s.lower())).strip()
    return " ".join(w for w in (_WORD_FOLD.get(t, t) for t in s.split())
                    if w and w not in _GENDER_TOK)


def _slug(s):
    b = _bare_slug(s)
    return _team_canon().get(b, b)


# Words that carry no discriminating power in either our tour names or Cricbuzz's slugs.
_STOP = frozenset(("the", "of", "and", "s", "in", "at", "a", "competition", "tour", "series",
                   "cup", "trophy", "cricket", "icc"))
# Format/qualifier tokens OUR tour names carry that Cricbuzz's slugs never do
# ("New Zealand vs West Indies Men's ODI 2026" vs "west-indies-tour-of-new-zealand-2026").
_FMT_NOISE = frozenset(("t20i", "t20is", "odi", "odis", "test", "tests", "vs", "v", "hundred100",
                        "wc", "worldcup", "men", "mens"))


def _words(text):
    """Token set with a light plural strip, applied to BOTH sides so it cannot skew a comparison.

    Needed because Cricbuzz slugs glue the possessive on ("the-hundred-mens-competition-2026")
    while our tour names separate it ("The Hundred Men's Competition 2026").
    """
    out = set()
    for w in _slug(text.replace("-", " ")).split():
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.add(w)
    return out


def series_matches(series_id, fresh=False):
    """Every fixture Cricbuzz lists for a series, as dicts.

    The URL slug is decorative: /cricket-series/12316/x/matches returns the LPL page (verified 200,
    correct <title>), so we never need to know Cricbuzz's slug for a series — only its id.

    MEMOISED PER PROCESS, including the `fresh` path. The bot resolves one match at a time and
    passes fresh=True for every match cricsheet has not settled, so a 31-match tour was
    re-fetching the SAME ~280 KB page 31 times from an undocumented endpoint in one run. The
    fixture list is assigned when the season is scheduled; it does not change between two matches
    of one run. `reset_map_cache()` clears it.
    """
    memo = (CACHE, str(series_id))
    if memo in _SERIES_MEMO:
        return _SERIES_MEMO[memo]
    html = cb_fetch("%s/cricket-series/%s/x/matches" % (CB_HOST, series_id),
                    "series_%s_matches.html" % series_id, fresh=fresh)
    payload = flight_payload(html)
    want = str(series_id)
    out, seen = [], set()
    # The page carries a global "currentMatchesList" (every live series) alongside this series'
    # own matchDetailsMap blocks, so filter by seriesId rather than trusting position.
    for m in re.finditer(r'\{"matchId":', payload):
        lit = _balanced(payload, m.start())
        if not lit:
            continue
        try:
            mi = json.loads(lit)
        except ValueError:
            continue
        if str(mi.get("seriesId")) != want or mi.get("matchId") in seen:
            continue
        seen.add(mi.get("matchId"))
        out.append({
            "match_id": _int(mi.get("matchId")),
            "series_id": _int(mi.get("seriesId")),
            "desc": mi.get("matchDesc") or "",
            "fmt": (mi.get("matchFormat") or "").upper(),
            "start_ms": _int(mi.get("startDate")),
            "state": mi.get("state") or "",
            "status": mi.get("status") or "",
            "teams": [(mi.get("team1") or {}).get("teamName") or "",
                      (mi.get("team2") or {}).get("teamName") or ""],
            "team_short": [(mi.get("team1") or {}).get("teamSName") or "",
                           (mi.get("team2") or {}).get("teamSName") or ""],
            "venue": (mi.get("venueInfo") or {}).get("ground") or "",
            "tz": (mi.get("venueInfo") or {}).get("timezone") or "",
        })
    if not out:
        # Not memoised: an unreadable page is not a fixture list of zero, and caching it would
        # make one bad fetch look like "this series has no matches" for the rest of the run.
        raise CricbuzzParseError("no fixtures with seriesId=%s on the series page — wrong id, or "
                                 "Cricbuzz renamed the matchInfo block" % series_id)
    _SERIES_MEMO[memo] = out
    return out


def _dates_for(ms, tz):
    """Every plausible calendar date for an epoch-ms start: UTC, venue-local, and ±1 day.

    Feeds disagree on the timezone a match "belongs" to (cricsheet uses local, some feeds UTC), and
    a 19:30 IST start is already the next day in UTC. wc_fps_to_csv tolerates ±1 day for the same
    reason; matching that here keeps the two resolvers from disagreeing about the same fixture.
    """
    if not ms:
        return set()
    base = ms / 1000.0
    offs = [0.0]
    m = re.match(r"([+-])(\d{1,2}):?(\d{2})", tz or "")
    if m:
        sign = 1 if m.group(1) == "+" else -1
        offs.append(sign * (int(m.group(2)) * 3600 + int(m.group(3)) * 60))
    out = set()
    for off in offs:
        for day in (-86400, 0, 86400):
            out.add(time.strftime("%Y-%m-%d", time.gmtime(base + off + day)))
    return out


def fixture_date(m):
    """Cricbuzz's OWN calendar date for a fixture — venue-local when it states a timezone.

    Provenance only. What actually MATCHES is `_dates_for`'s ±1-day spread; this is the single
    date a human reads in the pin file to see which fixture we paired to.
    """
    ms = m.get("start_ms")
    if not ms:
        return ""
    off, mt = 0, re.match(r"([+-])(\d{1,2}):?(\d{2})", m.get("tz") or "")
    if mt:
        off = (1 if mt.group(1) == "+" else -1) * (int(mt.group(2)) * 3600 + int(mt.group(3)) * 60)
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000.0 + off))


def derive_match(series_id, date, teams, fresh=False):
    """DERIVE the cricbuzz fixture for a (series, date, teams) triple. -> (fixture, why, near).

    `fixture` is the series-page dict, or None when the pairing is not UNIQUE — 0 hits or >1.
    `why` then says which, in words that name the actual cause. That matters: both naming
    conventions that broke this in one week surfaced only as "no unique cricbuzz match", which
    reads like Cricbuzz not carrying the fixture at all, and sent the diagnosis in the wrong
    direction twice (ESPN "St Lucia Kings" vs Cricbuzz "Saint Lucia Kings" cost 2 of 5 completed
    CPL matches their second witness; ESPN "MI London (Men)" vs "MI London" cost the Hundred
    Men's all 31). `near` carries the fixtures that ALMOST matched so the operator can see the
    two spellings side by side.

    Refusal semantics are unchanged and deliberate: >1 hit is a genuine same-day double-header
    between the same two sides, and it stays refused rather than guessed.
    """
    want = frozenset(_slug(t) for t in teams if t)
    hits, same_date, same_teams = [], [], []
    for m in series_matches(series_id, fresh=fresh):
        names = frozenset(_slug(t) for t in m["teams"] if t)
        shorts = frozenset(_slug(t) for t in m["team_short"] if t)
        team_ok = want <= names or want <= shorts
        date_ok = (not date) or date in _dates_for(m["start_ms"], m["tz"])
        if team_ok and date_ok:
            hits.append(m)
        elif team_ok:
            same_teams.append(m)
        elif date_ok:
            same_date.append(m)
    if len(hits) == 1:
        return hits[0], "", []
    ours = "+".join(sorted(want))
    if len(hits) > 1:
        return (None,
                "AMBIGUOUS: %d cricbuzz fixtures match %s on %s (%s) — refusing rather than "
                "guessing; that is a same-day double-header between the same two sides"
                % (len(hits), ours, date, ", ".join("cb%s %r" % (h["match_id"], h["desc"])
                                                    for h in hits)),
                hits)
    if same_teams:
        return (None,
                "cricbuzz HAS this fixture but on a different date: %s — we asked for %s. A "
                "schedule change, or the two feeds disagree by more than the ±1 day tolerance"
                % (", ".join("cb%s %r on %s" % (m["match_id"], m["desc"], fixture_date(m))
                             for m in same_teams), date),
                same_teams)
    if same_date:
        return (None,
                "TEAM NAMES DO NOT MEET: we asked for [%s]; cricbuzz lists %s on %s. That is a "
                "spelling convention, not a missing fixture — fold it in cricbuzz._WORD_FOLD or "
                "registry/team_aliases.json"
                % (ours, ", ".join("cb%s [%s]" % (m["match_id"],
                                                  "+".join(sorted(_slug(t) for t in m["teams"] if t)))
                                   for m in same_date), date),
                same_date)
    return None, "cricbuzz series %s lists no fixture for %s on %s" % (series_id, ours, date), []


# ── THE PIN. registry/cricbuzz_match_map.py + registry/cricbuzz_match_map.json ────────────────
# Players have a shared key (ESPN's athlete.id IS the cricinfo id) and, where they don't, a
# DERIVED bridge with a confirmations log. Matches had NEITHER: the pairing above was re-derived
# from names on every run and nothing recorded that it had ever been made, so a rename upstream
# could silently re-pair or un-pair an already-SETTLED match with no ledger showing it. The map
# is the durable half — read the module header there for the key, the three contradiction
# directions and why an absence never revokes a pin.
MATCH_MAP_PATH = os.environ.get(
    "WC_CB_MATCH_MAP",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "registry", "cricbuzz_match_map.json"))

PIN_ALERTS = []          # every pin event this process refused, held or recorded — see pin_alerts()
_LAST_REFUSAL = [""]     # why the most recent resolve_match_id returned None — see last_refusal()
_MAP_MOD = []            # [module] or [None]; [] == not tried yet
_MAP_STORE = {}          # path -> store, loaded once per process
_SERIES_MEMO = {}        # (cache, series) -> fixtures, so N matches cost ONE series page


def _map_mod():
    """The store module, or None if it cannot be imported (then pinning is simply off).

    Import by NAME first so a test that does `import registry.cricbuzz_match_map` shares this
    exact module object (one sys.modules entry, one store cache). The path fallback only exists
    so cricbuzz.py stays runnable from a directory where `registry` is not importable.
    """
    if _MAP_MOD:
        return _MAP_MOD[0]
    mod = None
    try:
        from registry import cricbuzz_match_map as mod    # noqa: F401  (rebound below)
    except Exception:
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "cricbuzz_match_map",
                os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "registry", "cricbuzz_match_map.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as exc:
            print("  cricbuzz: match map unavailable (%s) — pairings will be re-derived every "
                  "run, which is what a rename upstream needs to move one" % exc, file=sys.stderr)
            mod = None
    _MAP_MOD.append(mod)
    return mod


def _map_store(mm, path=None):
    p = path or MATCH_MAP_PATH
    if p not in _MAP_STORE:
        _MAP_STORE[p] = mm.load_store(p)
    return _MAP_STORE[p]


def reset_map_cache():
    """Drop the per-process module/store/series caches. For tests and for a CLI that rewrites
    the file underneath us."""
    del _MAP_MOD[:]
    _MAP_STORE.clear()
    _SERIES_MEMO.clear()


def pin_alerts():
    """Everything the pin refused, held or recorded this run — the caller's loud surface."""
    return list(PIN_ALERTS)


def last_refusal():
    """WHY the most recent resolve_match_id returned None; "" if it did not refuse.

    Set at the top of every call, so it can only ever describe that call. Exists because the one
    thing the caller currently prints — "no unique cricbuzz match for <date> <teams>" — reads as
    "Cricbuzz does not carry this fixture", and that sent the diagnosis the wrong way for both of
    the naming conventions that broke the pairing this week. The cause is knowable here; it just
    had no way out of the function.
    """
    return _LAST_REFUSAL[0]


def _pin_alert(kind, key, detail, loud=True):
    rec = {"kind": kind, "key": key, "detail": detail}
    PIN_ALERTS.append(rec)
    if loud:
        mark = {"contradiction": "⛔", "revoked": "⛔"}.get(kind, "⚠")
        print("  %s cricbuzz match pin [%s] %s: %s" % (mark, kind, key, detail), file=sys.stderr)
    return rec


def _rename_hint(mm, store, series_id, date, slugs):
    """Name the RENAME when a derivation finds nothing but a sibling pin sits on the same date.

    Diagnosis only — it never resolves anything. Resolving on a partial team match would be a
    guess about identity, which is the one thing this project does not do.
    """
    delta = mm.SAME_FIXTURE_DAYS
    for k, rec in sorted((store.get("pins") or {}).items()):
        if rec["series_id"] != str(series_id) or not (set(rec["teams"]) & set(slugs)):
            continue
        d = mm._date_delta_days(rec["date"], date or "")
        if d is not None and d <= delta and set(rec["teams"]) != set(slugs):
            return ("a pin already exists for [%s] on %s (cb%s) — one of those team names was "
                    "RENAMED on one side; add the variant to registry/team_aliases.json"
                    % ("+".join(rec["teams"]), rec["date"], rec["cricbuzz_match_id"]))
    return ""


def resolve_match_id(series_id, date, teams, fresh=False, espn_event=None, record=True,
                     map_path=None):
    """Cricbuzz match id for a (series, date, teams) triple, or None if it is not unique.

    READ THE PIN, THEN DERIVE — never the other way round. A pairing that has been derived once
    is recorded in registry/cricbuzz_match_map.json with its provenance and read back thereafter,
    so a rename on either side cannot quietly move or drop the cross-check on a match whose
    points are already settled. `fresh=True` (which the bot passes for any match cricsheet has
    not settled) re-derives and CONFIRMS the pin; a settled match resolves from the file with no
    network at all.

    The refusal contract is unchanged: None on 0 or >1 hits, so a genuine same-day double-header
    between the same two sides stays refused rather than guessed. Three new ways to get None, all
    of them LOUD (see pin_alerts()) and all of them fail-safe — the caller loses a cross-check,
    never a match:
      • the key is REVOKED (a contradiction a human has not yet decided);
      • this run derived a DIFFERENT id than the pin — both claims are refused, never last-wins;
      • the ESPN event id belongs to a revoked key.

    `espn_event` is the rename-proof anchor: an id, not a name. Pass it and a pin survives a team
    being renamed on OUR side (the key moves, the event id does not). Without it the pin is keyed
    on names, which is better than nothing (it survives a rename on CRICBUZZ's side) but not
    rename-proof.
    """
    _LAST_REFUSAL[0] = ""
    slugs = sorted({_slug(t) for t in teams if t})
    mm = _map_mod()
    if mm is None:                                   # no store module: exactly the old behaviour
        fixture, why, _near = derive_match(series_id, date, teams, fresh=fresh)
        if fixture:
            return fixture["match_id"]
        _LAST_REFUSAL[0] = why
        return None

    store = _map_store(mm, map_path)
    key = mm.make_key(series_id, date, slugs)
    hit = mm.lookup(store, key, espn_event)
    if hit.status == mm.REVOKED:
        a = _pin_alert("revoked", hit.key,
                       hit.detail + " — no cross-check until `--forget` clears it")
        _LAST_REFUSAL[0] = "pin REVOKED: " + a["detail"]
        return None
    pinned = int(hit.cricbuzz_match_id) if hit.cricbuzz_match_id else None
    if pinned and not fresh:
        return pinned

    try:
        fixture, why, _near = derive_match(series_id, date, teams, fresh=fresh)
    except CricbuzzError as exc:
        # ⛔ AN ABSENCE IS NOT A CONTRADICTION. A series page we could not fetch says nothing
        # about the pairing; the pin stands. Unpinned, the caller sees the same exception it
        # always did.
        if pinned:
            _pin_alert("held", hit.key, "cb%d stands — the series page was unreachable (%s)"
                       % (pinned, exc))
            return pinned
        raise

    if pinned:
        if fixture is None:
            _pin_alert("held", hit.key, "cb%d stands — this run could not re-derive it: %s"
                       % (pinned, why))
            return pinned
        if str(fixture["match_id"]) != str(pinned):
            # ⛔ CONTRADICTION. Record the new claim under the PINNED key so the store compiles
            # both into a revocation, and refuse. Never last-wins: the evidence says one of the
            # two derivations is wrong and does not say which.
            store, changed = mm.record(store, hit.key, fixture["match_id"], method="teams+date",
                                       espn_event=espn_event or "", cb_desc=fixture["desc"],
                                       cb_date=fixture_date(fixture))
            if changed and record:
                _persist(mm, store, map_path)
            a = _pin_alert("contradiction", hit.key,
                           "pinned to cb%d, this run derived cb%s — BOTH refused. %r on %s. "
                           "Decide which is right, then --forget the wrong claim"
                           % (pinned, fixture["match_id"], fixture["desc"],
                              fixture_date(fixture)))
            _LAST_REFUSAL[0] = "pin CONTRADICTED: " + a["detail"]
            return None

    if fixture is None:
        hint = _rename_hint(mm, store, series_id, date, slugs)
        a = _pin_alert("unpaired", key, why + ((" · " + hint) if hint else ""), loud=bool(hint))
        _LAST_REFUSAL[0] = a["detail"]
        return None

    if record:
        store, changed = mm.record(store, key, fixture["match_id"], method="teams+date",
                                   espn_event=espn_event or "", cb_desc=fixture["desc"],
                                   cb_date=fixture_date(fixture))
        if changed:
            _MAP_STORE[map_path or MATCH_MAP_PATH] = store
            _persist(mm, store, map_path)
    return fixture["match_id"]


_PERSIST_FAILED = []


def _persist(mm, store, map_path=None):
    """Write the map. A failure is reported ONCE and is never fatal — the pin is a durability
    improvement, and losing it must not cost the run its points.

    ⚠ The file only survives the run if the workflow COMMITS it: `registry/cricbuzz_match_map.json`
    is in the LEDGERS list of wwc-points.yml / live-lineup.yml / on-demand-refresh.yml. Drop it
    from there and every run re-derives from scratch — the file would be written and never read,
    which is this repo's most-repeated bug shape.
    """
    _MAP_STORE[map_path or MATCH_MAP_PATH] = store
    try:
        mm.save_store(store, map_path or MATCH_MAP_PATH)
    except OSError as exc:
        if not _PERSIST_FAILED:
            _PERSIST_FAILED.append(exc)
            print("  cricbuzz: could not write the match map (%s) — this run's pairings will not "
                  "persist" % exc, file=sys.stderr)


def series_index(year, fresh=False):
    """{series_id: [slug, ...]} for a calendar year, from Cricbuzz's own archive index page.

    ⛔ A SERIES ID HAS MORE THAN ONE SLUG. The 2026 archive links series 12123 as BOTH
    `cpl-2026` and `caribbean-premier-league-2026`. Keeping only the first one seen (the obvious
    `setdefault`) made "Caribbean Premier League 2026" resolve to nothing, because the short
    marketing slug shares no words with the tour name. Keep them all; match against the union.
    """
    html = cb_fetch("%s/cricket-scorecard-archives/%s" % (CB_HOST, year),
                    "archive_%s.html" % year, fresh=fresh)
    out = {}
    for sid, slug in re.findall(r"cricket-series/(\d+)/([a-z0-9-]+)", html):
        out.setdefault(int(sid), [])
        if slug not in out[int(sid)]:
            out[int(sid)].append(slug)
    if not out:
        raise CricbuzzParseError("no cricket-series links on the %s archive page" % year)
    for sid in out:
        out[sid].sort(key=len, reverse=True)          # longest (most informative) first
    return out


def series_candidates(tour_name, year, fresh=False):
    """Ranked [(extra_words, series_id, slug)] whose slug contains every meaningful tour token.

    Ranked by how many EXTRA words the Cricbuzz slug carries — an exact token-set match beats a
    superset, which is what separates "india-tour-of-england-2026" from
    "india-women-tour-of-england-2026" for the men's tour.

    GENDER IS A HARD FILTER, NOT A SCORE. Our names say "Men's ODI"; Cricbuzz leaves men's tours
    unmarked and marks only women's ("...-women-tour-of-..."). So "men" is dropped as noise, and
    instead: a women's tour must match a slug containing "women", and a tour that is not women's
    must NOT. Scoring gender would happily rank the women's competition first for the men's tour,
    which is a whole-tournament mis-ingest.
    """
    stripped = re.sub(r"\([^)]*\)", " ", tour_name or "")     # "(CPL)", "(Men T20I)" — pure noise
    toks = {t for t in _words(stripped) if t not in _STOP and t not in _FMT_NOISE}
    if not toks:
        return []
    is_women = "women" in _words(tour_name)
    out = []
    for sid, slugs in series_index(year, fresh=fresh).items():
        best = None
        for slug in slugs:
            words = _words(slug)
            if ("women" in words) != is_women or not toks <= words:
                continue
            cand = (len(words - toks - _STOP), sid, slug)
            if best is None or cand < best:
                best = cand
        if best:
            out.append(best)
    return sorted(out)


def resolve_series_id(tour_name, year, fresh=False):
    """PROPOSE a Cricbuzz series id for a tour name. Returns (series_id, slug) or None.

    ⚠ This is a NAME match — the ONLY one in this module. It is acceptable here because a tour is
    not a person: a wrong series id fails loudly and immediately (its fixtures have the wrong teams
    on the wrong dates, and resolve_match_id then returns None for every match) rather than
    silently paying the wrong player. Rule 3 is about player identity and still holds absolutely.

    It PROPOSES only: None unless there is a single best candidate that STRICTLY beats the runner-up.
    The result belongs in tours.json beside `espn_series`, written once by a human who opened the
    URL — never adopted at runtime from this function's return value alone.

    MEASURED reliability on the 15 tours in tours.json: see the module report. It resolves the
    franchise leagues cleanly and misses bilateral tours whose in-house name ("New Zealand vs West
    Indies Men's ODI 2026") shares almost no vocabulary with Cricbuzz's ("west-indies-tour-of-
    new-zealand-2026"). Use series_candidates() to eyeball those.
    """
    hits = series_candidates(tour_name, year, fresh=fresh)
    if not hits:
        return None
    if len(hits) > 1 and hits[0][0] == hits[1][0]:
        return None                                   # tie => ambiguous => never guess
    return (hits[0][1], hits[0][2])


if __name__ == "__main__":                             # no side effects on import — see conftest
    for arg in sys.argv[1:]:
        M = parse_match(int(arg))
        print(M)
        for w in M.warnings:
            print("   ! " + w)
        for k, p in sorted(M.perf.items(), key=lambda kv: (-kv[1]["r"], kv[1]["name"])):
            print("   %-10s %-26s %3s(%3s) 4s=%s 6s=%s | %s-%s-%s-%s d=%s m=%s | c%s s%s ro%s dro%s%s"
                  % (k, p["name"][:26], p["r"] if p["batted"] else "DNB", p["b"],
                     p["4s"], p["6s"], p["balls"], p["runs_conceded"], p["w"], p["lbwb"],
                     p["dots"], p["maidens"], p["catches"], p["stumpings"], p["runouts"],
                     p["dro"], "  " + p["dismissal"] if p["dismissed"] else ""))
