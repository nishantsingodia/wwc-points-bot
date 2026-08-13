"""cricbuzz.py — the traps that make this feed dangerous, pinned against REAL cached payloads.

Fixtures in tests/fixtures/cricbuzz/ are the genuine Cricbuzz responses for
  CB 157138  = LPL, 19th Match, 1 Aug 2026  (= cricinfo/ESPN 1537349)
  CB 144893  = The Hundred Men's, 23rd Match (= cricinfo/ESPN 1521253)
re-wrapped in Cricbuzz's own RSC flight-chunk syntax and trimmed to the fields the parser reads.
Every number asserted below was verified field-by-field against the cricsheet ball-by-ball for
those two matches: 24/24 and 22/22 player-rows exact. NO TEST HERE TOUCHES THE NETWORK.

The four things being defended:
  1. dots       — legal-runs-OFF-THE-BAT == 0. The `totalRuns == 0` recipe drops byes/leg-byes,
                  which cricsheet and D11 still score as the bowler's dot.
  2. maidens    — a verbatim copy of `dots` on The Hundred. Must never reach a scorer.
  3. DNB        — Cricbuzz emits an all-zero batting row where ESPN omits the player. That is an
                  ABSENCE, not 0 off 0.
  4. transport  — a 403 / timeout / HTTP-204 must be impossible to mistake for "no data".
"""
import io
import json
import os
import urllib.error

import pytest

import cricbuzz as cb


FIX = os.path.join(os.path.dirname(__file__), "fixtures", "cricbuzz")
LPL_T20 = 157138          # cricinfo 1537349
HUNDRED = 144893          # cricinfo 1521253


@pytest.fixture
def cache(monkeypatch):
    """Point the module's disk cache at the committed fixtures — so nothing dials out."""
    monkeypatch.setattr(cb, "CACHE", FIX)
    return FIX


@pytest.fixture
def no_network(monkeypatch):
    """Any attempt to open a socket in these tests is a test bug; make it loud."""
    def boom(*a, **k):
        raise AssertionError("a test tried to hit the network")
    monkeypatch.setattr(cb.urllib.request, "urlopen", boom)


# ---------------------------------------------------------------------------------------------
# 1. THE DOT RULE
# ---------------------------------------------------------------------------------------------
# cricsheet's own count for CB 157138, keyed by Cricbuzz bowler id. The card's `dots` column reads
# 0 for every one of them (dead field on T20), so these can ONLY come from the commentary.
CS_DOTS_157138 = {
    "cb:8983": 12,    # James Neesham
    "cb:8367": 12,    # Mohammad Nawaz
    "cb:18515": 8,    # Tharindu Ratnayake
    "cb:15751": 10,   # Akif Javed
    "cb:15667": 8,    # Sachindu Colombage
    "cb:12071": 9,    # Mujeeb Ur Rahman
    "cb:46926": 8,    # Eshan Malinga
    "cb:22656": 7,    # Wanuja Sahan
    "cb:10940": 6,    # Kamindu Mendis
    "cb:22786": 1,    # Ripon Mondol
    "cb:39678": 7,    # Milan Priyanath Rathnayake
    "cb:22666": 6,    # Malsha Tharupathi
}


def test_dots_match_cricsheet_exactly(cache, no_network):
    m = cb.parse_match(LPL_T20)
    assert m.dots_source == "commentary"
    got = {k: p["dots"] for k, p in m.perf.items() if p["bowled"]}
    assert got == CS_DOTS_157138
    assert m.warnings == []


def test_card_dots_column_is_dead_on_t20(cache, no_network):
    """Reading the scorecard's bowler `dots` column on a T20 hands you 0 dots for everyone.

    12/12 bowler rows on this card read dots=0 while the true count is 94. At +1 a dot that is a
    silent -94 fantasy points across the match, spread over exactly the players a fantasy scorer
    is most sensitive to. This is why _dots_for_match derives instead of reading.
    """
    _hdr, card = cb.parse_scorecard_html(cb.scorecard_html(LPL_T20))
    col = [v.get("dots") for inn in card
           for v in inn["bowlTeamDetails"]["bowlersData"].values()]
    assert col and set(col) == {0}, "the T20 card started carrying real dots — re-measure the rule"
    assert sum(CS_DOTS_157138.values()) == 94


def test_total_runs_recipe_undercounts_byes(cache, no_network):
    """Pin the measured cost of the WRONG dot recipe so nobody 'simplifies' back to it.

    `totalRuns == 0` treats a delivery that went for byes or leg-byes as a run conceded, but those
    are the keeper's leak: cricsheet charges the bowler nothing and scores it a dot. Measured on
    this match alone: 8 of 12 bowlers wrong, 12 dots (= 12 fantasy points) lost. Across CB 157138
    + CB 157061 it is 12 of 27 bowlers right and -21 dots.
    """
    entries = cb.commentary_innings(LPL_T20, 1) + cb.commentary_innings(LPL_T20, 2)
    right = cb.derive_bowling_from_commentary(entries)["dots"]

    wrong, prev = {}, {}
    for e in entries:
        if not e.get("ballNbr"):
            continue
        b = e["bowlerStriker"]
        bid = b["bowlId"]
        w, nb = b.get("bowlWides", 0) or 0, b.get("bowlNoballs", 0) or 0
        legal = (w, nb) == prev.get(bid, (0, 0))
        prev[bid] = (w, nb)
        if legal and not (e.get("totalRuns", 0) or 0):
            wrong[bid] = wrong.get(bid, 0) + 1

    assert {"cb:%d" % k: v for k, v in right.items()} == CS_DOTS_157138
    off = {k: (wrong.get(k, 0), v) for k, v in right.items() if wrong.get(k, 0) != v}
    assert len(off) == 8
    assert sum(v for v, _ in off.values()) - sum(v for _, v in off.values()) == -12


def test_commentary_is_returned_oldest_first(cache, no_network):
    """Cricbuzz ships newest-first and ballNbr TIES across a wide and the ball that follows it.

    Sorting by ballNbr therefore reorders extras against their own delivery and breaks the
    cumulative wides/no-balls diff the legal/illegal test depends on. timestamp order (i.e. a plain
    reverse) is the only safe ordering; this asserts the module returns it that way.
    """
    entries = cb.commentary_innings(LPL_T20, 1)
    balls = [e["ballNbr"] for e in entries if e.get("ballNbr")]
    assert balls[0] == 1 and balls[-1] == 120
    assert len(balls) != len(set(balls)), "ballNbr no longer ties — re-check the ordering rule"


# ---------------------------------------------------------------------------------------------
# 2. THE HUNDRED'S CORRUPT `maidens`
# ---------------------------------------------------------------------------------------------
def test_hundred_maidens_are_hard_ignored(cache, no_network):
    """CB 144893's maidens column is a byte-for-byte copy of its dots column, 13/13 bowlers.

    cricsheet records ZERO maidens in that match; Cricbuzz claims 68. The Hundred scorer awards no
    maiden points so it happens to cost nothing today — but at the T20/ODI rate that is 68 x 12 =
    816 fabricated fantasy points, and an L1 comparison against ESPN would light up on every bowler.
    The field must never be read for a HUN match; maidens stay None (absent), never 0.
    """
    _hdr, card = cb.parse_scorecard_html(cb.scorecard_html(HUNDRED))
    rows = [v for inn in card for v in inn["bowlTeamDetails"]["bowlersData"].values()]
    assert len(rows) == 13
    assert all(v["maidens"] == v["dots"] for v in rows), "the corruption changed shape — re-measure"
    assert sum(v["maidens"] for v in rows) == 68

    m = cb.parse_match(HUNDRED)
    assert m.fmt == "HUN"
    assert m.maidens_source is None
    assert {p["maidens"] for p in m.perf.values() if p["bowled"]} == {None}
    assert any("HARD-IGNORED" in w for w in m.warnings)


def test_hundred_dots_come_from_the_card(cache, no_network):
    """mcenter is HTTP 204 on The Hundred, and there the card's dots ARE real and correct.

    Verified against cricsheet 1521253: all 13 bowler rows exact. So the format flips which source
    is trustworthy for BOTH fields at once — dots card-only, maidens never — and a single shared
    code path reading "the card" or "the commentary" would be wrong on one format or the other.
    """
    m = cb.parse_match(HUNDRED)
    assert m.dots_source == "card"
    assert sum(p["dots"] for p in m.perf.values() if p["bowled"]) == 68


def test_t20_maidens_come_from_the_card(cache, no_network):
    m = cb.parse_match(LPL_T20)
    assert m.maidens_source == "card"
    assert {p["maidens"] for p in m.perf.values() if p["bowled"]} == {0}   # cricsheet agrees: 0


# ---------------------------------------------------------------------------------------------
# 3. DID NOT BAT
# ---------------------------------------------------------------------------------------------
# Cricbuzz prints the whole batting order, so four of these five arrive as all-zero batting rows;
# Eshan Malinga (cb:46926) is the fifth and arrives with NO batting row at all — Galle used a
# 12th player and the card lists only 11. Both are the same state and cricsheet agrees on all five.
DNB_157138 = {"cb:18515", "cb:15751", "cb:15667", "cb:12071", "cb:46926"}


def test_did_not_bat_is_a_state_not_a_zero(cache, no_network):
    m = cb.parse_match(LPL_T20)
    dnb = {k for k, p in m.perf.items() if not p["batted"]}
    assert dnb == DNB_157138
    for k in dnb:
        p = m.perf[k]
        assert p["played"] is True, "a DNB player is still in the XI — he must score the +4"
        assert (p["r"], p["b"], p["4s"], p["6s"]) == (0, 0, 0, 0)
        assert p["dismissed"] is False


def test_did_not_bat_has_no_bat_fingerprint(cache, no_network):
    """The bridge normalization: a DNB must not present as a real 0-off-0 innings.

    Uncorrected, every did-not-bat on the Cricbuzz side collides with every other one while having
    no counterpart at all on the ESPN side (ESPN omits them), and measured bridge coverage falls
    98% -> 75%.
    """
    m = cb.parse_match(LPL_T20)
    assert all(cb.bat_fingerprint(m.perf[k]) is None for k in DNB_157138)
    real = m.perf["cb:8422"]                       # Dasun Shanaka 21 (8), 1x4, 2x6 — cricsheet
    assert cb.bat_fingerprint(real) == (21, 8, 1, 2)


def test_a_genuine_duck_is_not_a_did_not_bat(cache, monkeypatch):
    """0 off 0 WITH a dismissal is a real innings (-2 duck, and a fingerprint); 0 off 0 without one
    is an absence. The discriminator is outDesc/wicketCode, which Cricbuzz always fills for a real
    dismissal — so a batter run out backing up at the non-striker's end is still `batted`.
    """
    _hdr, card = cb.parse_scorecard_html(cb.scorecard_html(LPL_T20))
    inn = card[0]
    row = dict(next(iter(inn["batTeamDetails"]["batsmenData"].values())))
    row.update(runs=0, balls=0, fours=0, sixes=0, outDesc="run out (Perera)", wicketCode="RUNOUT",
               fielderId1=999, fielderId2=0, fielderId3=0)
    inn["batTeamDetails"]["batsmenData"] = {"bat_1": row}
    inn["bowlTeamDetails"]["bowlersData"] = {}
    del card[1:]

    monkeypatch.setattr(cb, "scorecard_html", lambda _mid, fresh=False: "")
    monkeypatch.setattr(cb, "parse_scorecard_html", lambda _html: ({"matchFormat": "T20"}, card))
    m = cb.parse_match(1)

    p = m.perf["cb:%d" % row["batId"]]
    assert p["batted"] is True and p["dismissed"] is True and p["dismissal"] == "run out"
    assert cb.bat_fingerprint(p) == (0, 0, 0, 0)
    assert m.perf["cb:999"]["dro"] == 1     # a lone fielder on a run-out = direct hit, +12


# ---------------------------------------------------------------------------------------------
# 4. A FETCH FAILURE IS NOT "NO DATA"  — the project's #1 bug class
# ---------------------------------------------------------------------------------------------
def _urlopen_raising(exc):
    def fake(req, timeout=None):
        raise exc
    return fake


class _Resp(io.BytesIO):
    """Minimal urlopen context manager."""
    def __init__(self, body, status=200):
        io.BytesIO.__init__(self, body)
        self.status = status

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_403_raises_and_is_not_empty_data(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "CACHE", str(tmp_path))
    err = urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
    monkeypatch.setattr(cb.urllib.request, "urlopen", _urlopen_raising(err))
    with pytest.raises(cb.CricbuzzUnavailable):
        cb.cb_fetch("https://www.cricbuzz.com/x", "probe.html")
    assert os.listdir(str(tmp_path)) == [], "a FAILURE was written to the cache as if it were data"


def test_timeout_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "CACHE", str(tmp_path))
    monkeypatch.setattr(cb.urllib.request, "urlopen", _urlopen_raising(OSError("timed out")))
    with pytest.raises(cb.CricbuzzUnavailable):
        cb.cb_fetch_json("https://www.cricbuzz.com/api/x", "probe.json")


def test_http_204_is_no_content_not_zero_deliveries(tmp_path, monkeypatch):
    """Cricbuzz answers mcenter with 204 + an EMPTY BODY for every Hundred match.

    urllib does not raise on a 204, so `json.load(resp)` throws a JSONDecodeError far from the
    call site — and one `except: return {}` later that reads as "this innings had no deliveries",
    i.e. zero dots for every bowler on a format where dots are a scored field.
    """
    monkeypatch.setattr(cb, "CACHE", str(tmp_path))
    monkeypatch.setattr(cb.urllib.request, "urlopen", lambda req, timeout=None: _Resp(b"", 204))
    with pytest.raises(cb.CricbuzzNoContent):
        cb.commentary_innings(144893, 1)
    assert os.listdir(str(tmp_path)) == []


def test_no_commentary_leaves_dots_absent_never_zero(cache, monkeypatch):
    """A T20 whose commentary is unreachable must publish NO dots, not a confident zero.

    Under the locked recon model an absent single-source field is unconsumed data: the match stays
    LIVE with a named row. A 0 would sail through L1 and freeze as the settled number.
    """
    def boom(_mid, _inn, fresh=False):
        raise cb.CricbuzzUnavailable("HTTP 503")
    monkeypatch.setattr(cb, "commentary_innings", boom)

    m = cb.parse_match(LPL_T20)
    assert m.dots_source is None
    assert {p["dots"] for p in m.perf.values() if p["bowled"]} == {None}
    assert any("ABSENT" in w for w in m.warnings)
    # everything that does NOT depend on the commentary is still fully scored
    assert m.perf["cb:10934"]["r"] == 58


def test_truncated_commentary_is_caught_by_the_ball_count(cache, monkeypatch):
    """The completeness cross-check: derived legal balls (mcenter) vs card balls (scorecard).

    Two endpoints, two field families — a half-served commentary feed cannot agree with the card
    by accident. This is the Cricbuzz analogue of espn_expected_balls, and it exists because a
    truncated feed is the failure mode that looks complete: it would simply under-count dots.
    """
    real = cb.commentary_innings

    def half(mid, inn, fresh=False):
        entries = real(mid, inn, fresh=fresh)
        return entries[:len(entries) // 2]
    monkeypatch.setattr(cb, "commentary_innings", half)

    m = cb.parse_match(LPL_T20)
    bowlers = [p for p in m.perf.values() if p["bowled"]]
    assert any(p["dots"] is None for p in bowlers), "a truncated feed produced confident dot counts"
    assert any("commentary incomplete" in w for w in m.warnings)


def test_empty_cache_file_is_refused(tmp_path, monkeypatch, no_network):
    """A 0-byte cache file is corruption, not content — parsing "" yields an empty scorecard."""
    monkeypatch.setattr(cb, "CACHE", str(tmp_path))
    open(os.path.join(str(tmp_path), "cb_probe.html"), "w").close()
    with pytest.raises(cb.CricbuzzUnavailable):
        cb.cb_fetch("https://www.cricbuzz.com/x", "probe.html")


def test_unparseable_page_raises_with_a_reason(cache, no_network):
    """A Cricbuzz prop rename must scream, not look like a match that has not started."""
    with pytest.raises(cb.CricbuzzParseError):
        cb.flight_payload("<html><body>no flight chunks here</body></html>")
    with pytest.raises(cb.CricbuzzParseError):
        cb.parse_scorecard_html('<script>self.__next_f.push([1,"{\\"other\\":1}"])</script>')


# ---------------------------------------------------------------------------------------------
# 5. SHAPE + CONTENT CONTRACTS
# ---------------------------------------------------------------------------------------------
def test_perf_shape_is_a_superset_of_blank_perf(wcmod):
    """A Cricbuzz row must be diffable against an ESPN/cricapi row field-for-field.

    If wc_fps_to_csv.blank_perf grows a scored field and this does not, the L1 comparison silently
    stops covering it — the "written but never read" half of this repo's recurring bug class.
    """
    theirs = set(wcmod.blank_perf("x").keys()) - {"espn_id"}
    ours = set(cb.blank_cb_perf("x", 1).keys())
    assert theirs <= ours, "blank_perf gained fields cricbuzz.blank_cb_perf does not carry: %s" % (
        sorted(theirs - ours),)


def test_no_name_keyed_accessor(cache, no_network):
    """Identity is by Cricbuzz id only. A norm(name)->row map here would hand callers the one
    resolution route this project forbids (it corrupted 20 live rows)."""
    m = cb.parse_match(LPL_T20)
    assert all(k.startswith("cb:") for k in m.perf)
    assert not hasattr(m, "by_name")


def test_fielding_and_dismissals_match_cricsheet(cache, no_network):
    """Fielder attribution comes from fielderId1..3, never from parsing `outDesc` for a name.

    Cross-checked against cricsheet for CB 157138: catches, stumpings, run-outs and the
    direct-vs-assisted split are all exact.
    """
    m = cb.parse_match(LPL_T20)
    got = {k: (p["catches"], p["stumpings"], p["runouts"], p["dro"])
           for k, p in m.perf.items() if p["catches"] or p["stumpings"] or p["runouts"]}
    assert got == {
        "cb:10979": (0, 1, 0, 0),    # Sam Harper       st
        "cb:23186": (2, 0, 0, 0),    # Thomas Rogers    2 ct
        "cb:13694": (1, 0, 0, 0),    # Janith Liyanage
        "cb:9500": (1, 0, 0, 0),     # Chamika Karunaratne (c&b)
        "cb:8983": (1, 0, 0, 0),     # James Neesham
        "cb:9495": (0, 1, 0, 0),     # Sadeera Samarawickrama
        "cb:18515": (1, 0, 1, 0),    # Tharindu Ratnayake — assisted run-out, so NOT a direct hit
        "cb:46926": (2, 0, 1, 0),    # Eshan Malinga     — same run-out, the other half
    }
    ro = [d for d in m.dismissals if d["type"] == "run out"]
    assert len(ro) == 1 and len(ro[0]["fielder_cb_ids"]) == 2
    assert all(p["dro"] == 0 for p in m.perf.values()), "2 fielders shared it: +6 each, not +12"


def test_playing_xi_is_complete(cache, no_network):
    """24 players, matching cricsheet exactly — Cricbuzz's batting card lists the full order
    (including the impact substitute), so nobody in the XI is invisible for the +4."""
    m = cb.parse_match(LPL_T20)
    assert len(m.perf) == 24
    assert all(p["played"] for p in m.perf.values())
    assert all(p["name"] for p in m.perf.values()), "a player resolved to an id with no name"


def test_header_accessors(cache, no_network):
    m = cb.parse_match(LPL_T20)
    assert (m.match_id, m.fmt, m.complete, m.series_id) == (157138, "T20", True, 12316)
    assert m.teams == ["Colombo Kaps", "Galle Gallants"]


def test_module_imports_without_side_effects(monkeypatch):
    """wc_fps_to_csv discipline: importing must not fetch, create a cache dir, or read a file.

    The whole suite imports wc_fps_to_csv, and a module that dialled out at import time would make
    every test in the repo network-dependent.
    """
    import importlib

    def boom(*a, **k):
        raise AssertionError("cricbuzz.py performed I/O at import time")
    monkeypatch.setattr(cb.urllib.request, "urlopen", boom)
    monkeypatch.setattr(cb.os, "makedirs", boom)
    importlib.reload(cb)
    assert cb.CACHE


def test_missing_scorecard_is_not_a_parse_failure():
    """An upcoming fixture (header present, no scoreCard) must be distinguishable from an outage."""
    page = ('<script>self.__next_f.push([1,%s])</script>'
            % json.dumps('{"matchHeader":{"matchId":1,"state":"Preview"}}'))
    with pytest.raises(cb.CricbuzzNoScorecard):
        cb.parse_scorecard_html(page)
