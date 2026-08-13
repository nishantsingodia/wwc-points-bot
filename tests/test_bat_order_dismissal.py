"""Batting order + the dismissed flag must come from the batter's OWN ESPN scorecard line.

Two bugs, one payload (rosters[].roster[].linescores[].statistics.batting):

E1  `bat_order` existed in blank_perf and as a "Bat Order" sheet column but the ESPN path never
    filled it, so every ESPN-sourced tour published it blank. `batting.order` is right there.
    Re-measured against cricsheet on 29 matches (LPL 8 / Hundred men 8 / Hundred women 8 / NZ-WI
    ODI 5), id-joined: 485 players carried an order, 485 exact, 0 disagreements.

E2  ESPN's ball-by-ball attaches a dismissal to the item whose `batsman` is the STRIKER, not the
    batter who was actually out. So on a run out the striker was flagged dismissed (and could be
    flagged twice) while the real victim never was. Measured on the 5 completed CPL matches
    (ESPN_SERIES 8623):
        ev1534179  wrongly flagged Mohammad Hassan Khan;  missed Keemo Paul + Shayan Jahangir
        ev1534180  wrongly flagged Matthew Breetzke       (see below — NOT a run out)
        ev1534181  wrongly flagged Fabian Allen;          missed Jahmar Hamilton
        ev1534182  wrongly flagged Hunain Shah;           missed Vitel Lawes  -> 1 BOGUS DUCK
        ev1534183  clean
    and against cricsheet on the same 29 matches: 19 wrong flags before, 0 after (the 2 residual
    diffs are retired-hurt, where cricsheet — not ESPN — is the one calling it a dismissal).

    ⚠ CONTRADICTS THE ORIGINAL BRIEF, and the payload is the evidence: ev1534180 has NO run out.
    Joshua Da Silva RETIRED HURT (dismissalCard 'retired not out', shortText 'retired hurt') while
    Breetzke was on strike. So the misattribution class is "any dismissal item whose affected
    batter is not the striker" — run outs AND retireds — and Da Silva must NOT be marked dismissed.
    Correct outcome there is 12 -> 11 dismissed, not 12.

Bowler wicket credit is deliberately untouched: run-outs are excluded from it via `not_bowler_wkt`,
which was already right (13 bowler wickets + 2 run-outs = 15 = ESPN's own header on ev1534179).
"""
import pytest


# ── payload builders, shaped exactly like the cached CPL summary/playbyplay ────────────────
def _bat_ls(order, card, short, period=1):
    return {"period": period,
            "statistics": {"batting": {"order": order,
                                       "outDetails": {"dismissalCard": card, "shortText": short}}}}


def _bowl_ls(balls, period=2):
    return {"period": period,
            "statistics": {"bowling": {"overallLhb": {"balls": 0}, "overallRhb": {"balls": balls}}}}


def _player(pid, name, linescores, starter=True):
    return {"starter": starter, "subbedIn": False,
            "athlete": {"id": str(pid), "fullName": name}, "linescores": linescores}


def _summary(players, team="Test XI"):
    return {"rosters": [{"team": {"displayName": team}, "roster": players}]}


def _ball(i, striker, bowler, runs=0, dismissal=None, period=1):
    """One delivery. `dismissal` = (type, victim_name) — victim may differ from the striker,
    which is the whole point: that is how ESPN emits a run out."""
    it = {"id": str(i), "playType": {"id": "1", "description": "run" if runs else "no run"},
          "period": period, "scoreValue": runs, "text": "", "shortText": "", "preText": "",
          "over": {"number": i // 6},
          "bowler": {"athlete": {"id": "900", "fullName": bowler}},
          "batsman": {"athlete": {"id": str(abs(hash(striker)) % 9999), "fullName": striker}}}
    if dismissal:
        typ, victim = dismissal
        it["dismissal"] = {"dismissal": True, "type": typ,
                           "batsman": {"athlete": {"fullName": victim}},
                           "fielder": {"athlete": {}}}
        it["shortText"] = f"{bowler} to {striker}, OUT"
    return it


def _install(wcmod, monkeypatch, items, summary):
    pbp = {"commentary": {"count": len(items), "pageIndex": 1, "pageCount": 1, "items": items}}

    def fake(path, cache=True, **params):
        return pbp if path == "playbyplay" else summary
    monkeypatch.setattr(wcmod, "espn_get", fake)


# ── E1: batting order ─────────────────────────────────────────────────────────────────────
def test_bat_order_is_populated_from_the_scorecard(wcmod, monkeypatch):
    summary = _summary([
        _player(1, "Opener One", [_bat_ls(1, "c", "c Keeper b Bowl")]),
        _player(2, "Opener Two", [_bat_ls(2, "not out", "not out")]),
        _player(3, "Number Three", [_bat_ls(3, "run out", "run out (Fielder)")]),
        _player(900, "Test Bowler", [_bowl_ls(6)]),
    ])
    items = [_ball(i, "Opener One", "Test Bowler") for i in range(6)]
    _install(wcmod, monkeypatch, items, summary)

    perf, _ = wcmod.parse_espn("ev-order")

    assert perf[wcmod.norm("Opener One")]["bat_order"] == 1
    assert perf[wcmod.norm("Opener Two")]["bat_order"] == 2
    assert perf[wcmod.norm("Number Three")]["bat_order"] == 3


def test_did_not_bat_keeps_bat_order_zero(wcmod, monkeypatch):
    """ESPN emits `batting` only for the innings a player actually batted in — a DNB tail-ender
    has no entry at all, and 0 must keep meaning 'unknown/DNB', never a fabricated position."""
    summary = _summary([
        _player(1, "Opener One", [_bat_ls(1, "not out", "not out")]),
        _player(900, "Test Bowler", [_bowl_ls(6)]),          # bowled, never batted
        _player(7, "Bench Warmer", []),                      # in the XI, no linescores at all
    ])
    items = [_ball(i, "Opener One", "Test Bowler") for i in range(6)]
    _install(wcmod, monkeypatch, items, summary)

    perf, _ = wcmod.parse_espn("ev-dnb")

    assert perf[wcmod.norm("Test Bowler")]["bat_order"] == 0
    assert perf[wcmod.norm("Bench Warmer")]["bat_order"] == 0
    assert perf[wcmod.norm("Bench Warmer")]["played"] is True   # still in the XI -> +4


def test_super_over_batting_card_is_ignored(wcmod, monkeypatch):
    """D11 awards no super-over points and the ball-by-ball loop already skips period>2, so the
    batting card must too — else a super-over duck/order leaks in through the other endpoint."""
    summary = _summary([
        _player(1, "Opener One", [_bat_ls(1, "not out", "not out", period=1),
                                  _bat_ls(2, "c", "c Keeper b Bowl", period=3)]),
        _player(900, "Test Bowler", [_bowl_ls(6)]),
    ])
    items = [_ball(i, "Opener One", "Test Bowler") for i in range(6)]
    _install(wcmod, monkeypatch, items, summary)

    perf, _ = wcmod.parse_espn("ev-superover")

    p = perf[wcmod.norm("Opener One")]
    assert p["bat_order"] == 1, "super-over batting position overwrote the real innings"
    assert p["dismissed"] is False, "a super-over dismissal was scored"


# ── E2: the dismissed flag ────────────────────────────────────────────────────────────────
def test_run_out_marks_the_victim_not_the_striker(wcmod, monkeypatch):
    """CPL ev1534179 in miniature: Hendricks on strike, Shayan Jahangir run out at the other end."""
    summary = _summary([
        _player(1, "Reeza Raphael Hendricks", [_bat_ls(2, "not out", "not out")]),
        _player(2, "Shayan Jahangir", [_bat_ls(1, "run out", "run out (Joseph)")]),
        _player(900, "Test Bowler", [_bowl_ls(6)]),
    ])
    items = [_ball(i, "Reeza Raphael Hendricks", "Test Bowler") for i in range(5)]
    items.append(_ball(5, "Reeza Raphael Hendricks", "Test Bowler",
                       dismissal=("run out", "Shayan Jahangir")))
    _install(wcmod, monkeypatch, items, summary)

    perf, _ = wcmod.parse_espn("ev-runout")

    victim = perf[wcmod.norm("Shayan Jahangir")]
    striker = perf[wcmod.norm("Reeza Raphael Hendricks")]
    assert victim["dismissed"] is True, "the run-out victim was not marked dismissed"
    assert victim["dismissal"] == "run out (Joseph)"
    assert striker["dismissed"] is False, (
        "the STRIKER on a run-out ball was flagged dismissed — the bug that wrongly flagged "
        "Mohammad Hassan Khan / Matthew Breetzke / Fabian Allen / Hunain Shah across the CPL set"
    )
    assert striker["dismissal"] == ""


def test_no_bogus_duck_for_the_striker_on_a_run_out_ball(wcmod, monkeypatch):
    """CPL ev1534182: Hunain Shah was 0 not out and took a −2 duck he never earned."""
    summary = _summary([
        _player(10, "Hunain Shah", [_bat_ls(10, "not out", "not out")]),
        _player(9, "Vitel Orlando Lawes", [_bat_ls(9, "run out", "run out (Clarke)")]),
        _player(900, "Test Bowler", [_bowl_ls(6)]),
    ])
    items = [_ball(i, "Hunain Shah", "Test Bowler") for i in range(5)]
    items.append(_ball(5, "Hunain Shah", "Test Bowler",
                       dismissal=("run out", "Vitel Orlando Lawes")))
    _install(wcmod, monkeypatch, items, summary)

    perf, _ = wcmod.parse_espn("ev-duck")

    shah = perf[wcmod.norm("Hunain Shah")]
    assert shah["r"] == 0 and shah["b"] >= 1
    assert shah["dismissed"] is False
    # The scorer's duck gate is `dismissed and r == 0` (no ball threshold — deliberate, so a
    # non-striker run out for 0 off 0 balls still takes it). So the flag is the whole penalty.
    assert wcmod.score(shah, "BAT", fmt="T20")["bat"] == 0, "the bogus −2 duck is still applied"
    assert wcmod.score(perf[wcmod.norm("Vitel Orlando Lawes")], "BAT", fmt="T20")["bat"] == -2


def test_retired_hurt_is_not_a_dismissal(wcmod, monkeypatch):
    """CPL ev1534180 exactly: no run out in the match at all — Joshua Da Silva retired hurt while
    Breetzke was on strike. ESPN spells it dismissalCard 'retired not out' / shortText 'retired
    hurt'. Neither man is dismissed; the old code flagged Breetzke."""
    summary = _summary([
        _player(4, "Matthew Paul Breetzke", [_bat_ls(4, "not out", "not out")]),
        _player(3, "Joshua Da Silva", [_bat_ls(3, "retired not out", "retired hurt")]),
        _player(900, "Test Bowler", [_bowl_ls(6)]),
    ])
    items = [_ball(i, "Matthew Paul Breetzke", "Test Bowler") for i in range(5)]
    items.append(_ball(5, "Matthew Paul Breetzke", "Test Bowler", runs=1,
                       dismissal=("retired not out (hurt)", "Joshua Da Silva")))
    _install(wcmod, monkeypatch, items, summary)

    perf, _ = wcmod.parse_espn("ev-retired")

    assert perf[wcmod.norm("Matthew Paul Breetzke")]["dismissed"] is False
    assert perf[wcmod.norm("Joshua Da Silva")]["dismissed"] is False, (
        "retired hurt scored as a dismissal — it would hand a −2 duck to a batter who is not out"
    )
    assert perf[wcmod.norm("Joshua Da Silva")]["bat_order"] == 3


def test_retired_out_is_a_dismissal(wcmod, monkeypatch):
    """'retired out' (2 occurrences in the cached payloads) IS out — only 'retired not out' isn't."""
    summary = _summary([
        _player(5, "Retired Out Batter", [_bat_ls(5, "retired out", "retired out")]),
        _player(900, "Test Bowler", [_bowl_ls(6)]),
    ])
    items = [_ball(i, "Retired Out Batter", "Test Bowler") for i in range(6)]
    _install(wcmod, monkeypatch, items, summary)

    perf, _ = wcmod.parse_espn("ev-retired-out")

    assert perf[wcmod.norm("Retired Out Batter")]["dismissed"] is True


def test_dismissal_text_is_the_scorecard_form(wcmod, monkeypatch):
    """The Dismissal column the owner reads during recon must be the scorecard string
    ('c †Pooran b Gleeson'), HTML entities decoded — not the commentary headline
    ('Seales to Hendricks, OUT'), which names the striker and so was actively misleading."""
    summary = _summary([
        _player(1, "Caught Batter", [_bat_ls(1, "c", "c &dagger;Pooran b Gleeson")]),
        _player(2, "Bowled Batter", [_bat_ls(2, "bowled", " b Lennox")]),
        _player(3, "Safe Batter", [_bat_ls(3, "not out", "not out")]),
        _player(900, "Test Bowler", [_bowl_ls(6)]),
    ])
    items = [_ball(i, "Caught Batter", "Test Bowler") for i in range(5)]
    items.append(_ball(5, "Caught Batter", "Test Bowler", dismissal=("caught", "Caught Batter")))
    _install(wcmod, monkeypatch, items, summary)

    perf, _ = wcmod.parse_espn("ev-text")

    assert perf[wcmod.norm("Caught Batter")]["dismissal"] == "c †Pooran b Gleeson"
    assert perf[wcmod.norm("Bowled Batter")]["dismissal"] == "b Lennox"
    assert perf[wcmod.norm("Safe Batter")]["dismissal"] == ""


# ── the invariant that must NOT move ──────────────────────────────────────────────────────
def test_bowler_wicket_credit_is_unchanged(wcmod, monkeypatch):
    """Bowler credit was already correct and comes from the ball-by-ball, not the batting card:
    a caught/bowled/lbw credits the bowler, a run out does not (not_bowler_wkt). 2 bowler wickets
    + 1 run out = 3 dismissed batters, exactly as on ev1534179 (13 + 2 = 15)."""
    summary = _summary([
        _player(1, "Bat One", [_bat_ls(1, "c", "c Fielder b Test Bowler")]),
        _player(2, "Bat Two", [_bat_ls(2, "bowled", " b Test Bowler")]),
        _player(3, "Bat Three", [_bat_ls(3, "run out", "run out (Fielder)")]),
        _player(4, "Bat Four", [_bat_ls(4, "not out", "not out")]),
        _player(900, "Test Bowler", [_bowl_ls(8)]),
    ])
    items = [_ball(i, "Bat One", "Test Bowler") for i in range(5)]
    items.append(_ball(5, "Bat One", "Test Bowler", dismissal=("caught", "Bat One")))
    items.append(_ball(6, "Bat Two", "Test Bowler", dismissal=("bowled", "Bat Two")))
    items.append(_ball(7, "Bat Four", "Test Bowler", dismissal=("run out", "Bat Three")))
    _install(wcmod, monkeypatch, items, summary)

    perf, _ = wcmod.parse_espn("ev-wkts")

    assert perf[wcmod.norm("Test Bowler")]["w"] == 2, "run-out leaked into bowler wicket credit"
    assert perf[wcmod.norm("Test Bowler")]["lbwb"] == 1
    assert sum(1 for v in perf.values() if v["dismissed"]) == 3
    assert perf[wcmod.norm("Bat Four")]["dismissed"] is False


def test_missing_batting_card_falls_back_loudly(wcmod, monkeypatch, capsys):
    """An absence must never present as a value. With no batting card at all we cannot know who
    was out, so warn on stderr and fall back to the ball-by-ball — keyed on `dismissal.batsman`
    (which does name the victim), never on the striker."""
    summary = _summary([_player(900, "Test Bowler", [_bowl_ls(6)])])   # bowling figures only
    items = [_ball(i, "Striker Man", "Test Bowler") for i in range(5)]
    items.append(_ball(5, "Striker Man", "Test Bowler", dismissal=("run out", "Victim Man")))
    _install(wcmod, monkeypatch, items, summary)

    perf, _ = wcmod.parse_espn("ev-nocard")

    assert "NO batting card" in capsys.readouterr().err
    assert perf[wcmod.norm("Victim Man")]["dismissed"] is True
    assert perf[wcmod.norm("Striker Man")]["dismissed"] is False
    assert perf[wcmod.norm("Striker Man")]["bat_order"] == 0


# ── the cricapi+ESPN merge ────────────────────────────────────────────────────────────────
def test_merge_backfills_bat_order_but_never_the_dismissed_flag(wcmod, perf):
    """merge_espn_into keeps cricapi as the base and copies only what cricapi cannot supply.

    bat_order backfills on a FALSY base value because 0 is blank_perf's documented "unknown/DNB"
    and ESPN emits `batting.order` only for a batter who reached the crease — so falsy really is
    an absence (same reasoning as the existing `balls` backfill for 100-ball cards).

    `dismissed` must NOT backfill: there False is a legitimate value (not out), so "cricapi says
    not out" and "cricapi said nothing" are indistinguishable, and copying ESPN over it would
    manufacture a dismissal — and a −2 duck — out of an absence.
    """
    key = ("XX", "Some Batter")
    assigned = {key: perf(name="Some Batter", played=True, r=0, b=3,
                          bat_order=0, dismissed=False, dismissal="")}
    espn = {key: perf(name="Some Batter", played=True, r=0, b=3,
                      bat_order=6, dismissed=True, dismissal="c Keeper b Bowler")}

    wcmod.merge_espn_into(assigned, espn)

    assert assigned[key]["bat_order"] == 6, "ESPN's batting position was not backfilled"
    assert assigned[key]["dismissed"] is False, "ESPN overwrote cricapi's not-out with a dismissal"
    assert assigned[key]["dismissal"] == ""


def test_merge_does_not_overwrite_a_known_bat_order(wcmod, perf):
    key = ("XX", "Some Batter")
    assigned = {key: perf(name="Some Batter", played=True, bat_order=3)}
    espn = {key: perf(name="Some Batter", played=True, bat_order=6)}

    wcmod.merge_espn_into(assigned, espn)

    assert assigned[key]["bat_order"] == 3, "cricapi's own batting position must stay authoritative"


def test_dismissed_is_frozen_in_the_settlement_record(wcmod, perf):
    """`dismissed` drives the −2 duck, so a baseline that freezes only the Dismissal TEXT cannot
    reproduce the settled score. Pins it into SETTLED_FIELDS."""
    assert "dismissed" in wcmod.SETTLED_FIELDS
    assert "bat_order" in wcmod.SETTLED_FIELDS
