"""The wicket and the catch must come from the batter's OWN scorecard line, not the ball-by-ball.

WHAT WENT WRONG — The Hundred Women's ESPN event 1521204 (Trent Rockets v London Spirit,
2026-07-26). ESPN's playbyplay item for over 19 ball 1 is, by its own commentary text, "Jones
looks to go downtown ... the simplest of catches for Harris at long on" off Charis Pavely. Its
`dismissal` block instead reads type='retired out', batsman=Georgia Elwiss, fielder={} — that is
Elwiss's retirement, which happened on the PREVIOUS delivery (over 18 ball 5, where ESPN's block
is empty). `not_bowler_wkt` correctly declines to credit a retired-out, so the real caught went
with it:
    Charis Pavely  wickets 1 -> 0   (-30 FP)
    Grace Harris   catches 1 -> 0   ( -8 FP)
Four witnesses say the ball-by-ball is the one that is wrong: ESPN's OWN scorecard
(`outDetails` = 'c Harris b Pavely', bowler id 1340525, fielders[0] id 381268), ESPN's own bowling
figures (Pavely wickets=1) and the item's own `over.wickets`, cricsheet (ov18.0, caught, fielder
GM Harris), and cricbuzz — whose L1 override is what rescued the settled number
(settlement_snapshots field_sources {'w': 'S1'}).

This is the SAME striker-vs-victim misattribution the `dismissed` flag was already moved off the
ball-by-ball for; the mirror was only half applied, reaching the batter but never the bowler or
the fielder.
"""
import pytest

from tests.test_bat_order_dismissal import _ball, _bowl_ls, _install, _player, _summary


def _bat_ls_full(order, card, short, bowler=None, fielders=(), period=1):
    """A batting linescore shaped like the real payload — outDetails carries `bowler` and
    `fielders` with athlete ids, which is what the fix reads."""
    det = {"dismissalCard": card, "shortText": short}
    if bowler:
        det["bowler"] = {"id": str(bowler[0]), "fullName": bowler[1]}
    if fielders:
        det["fielders"] = [{"athlete": {"id": str(i), "fullName": n},
                            "isKeeper": 0, "isSubstitute": int(sub)}
                           for i, n, sub in fielders]
    return {"period": period,
            "statistics": {"batting": {"order": order, "outDetails": det}}}


def test_scorecard_beats_a_ball_by_ball_that_names_the_wrong_dismissal(wcmod, monkeypatch):
    """ev1521204 in miniature: the delivery that took the wicket carries someone else's
    retired-out. The card must still credit the bowler and the catcher."""
    summary = _summary([
        _player(1, "E Jones", [_bat_ls_full(8, "c", "c Harris b Pavely",
                                            bowler=(900, "Charis Pavely"),
                                            fielders=[(381268, "Grace Harris", False)])]),
        _player(2, "G Elwiss", [_bat_ls_full(7, "retired out", "retired out")]),
        _player(900, "Charis Pavely", [_bowl_ls(5, period=2)]),
    ])
    items = [_ball(i, "G Elwiss", "Charis Pavely") for i in range(4)]
    # the poisoned item: the striker AND the dismissal both name Elwiss, type 'retired out'
    items.append(_ball(4, "G Elwiss", "Charis Pavely", dismissal=("retired out", "G Elwiss")))
    _install(wcmod, monkeypatch, items, summary)

    perf, _ = wcmod.parse_espn("ev-1521204-shaped")

    assert perf[wcmod.norm("Charis Pavely")]["w"] == 1, "the bowler lost a real wicket"
    assert perf[wcmod.norm("Grace Harris")]["catches"] == 1, "the catcher lost a real catch"
    assert perf[wcmod.norm("Charis Pavely")]["lbwb"] == 0
    assert perf[wcmod.norm("E Jones")]["dismissed"] is True
    assert perf[wcmod.norm("G Elwiss")]["dismissed"] is True   # retired out IS a dismissal


def test_a_card_that_names_no_bowler_keeps_the_ball_by_balls_credit(wcmod, monkeypatch, capsys):
    """An ABSENCE must never present as a VALUE. A scorecard line that says 'c' but carries no
    bowler object cannot be allowed to zero a wicket the ball-by-ball did see — it falls back,
    and says so."""
    summary = _summary([
        _player(1, "Bat One", [_bat_ls_full(1, "c", "c Fielder b Test Bowler")]),   # no bowler key
        _player(900, "Test Bowler", [_bowl_ls(6, period=2)]),
    ])
    items = [_ball(i, "Bat One", "Test Bowler") for i in range(5)]
    items.append(_ball(5, "Bat One", "Test Bowler", dismissal=("caught", "Bat One")))
    _install(wcmod, monkeypatch, items, summary)

    perf, _ = wcmod.parse_espn("ev-no-bowler-on-card")

    assert perf[wcmod.norm("Test Bowler")]["w"] == 1, "silently dropped a wicket"
    assert "names no bowler" in capsys.readouterr().err


def test_a_substitute_fielder_gets_the_catch_but_not_the_xi_bonus(wcmod, monkeypatch):
    """ev1521240: 'c sub (EG Barnard) b Ellis'. The card hands us athlete id 578769 for a man
    with no roster entry at all — previously reachable only by name similarity. He takes the
    catch; he is NOT in the XI, so no +4."""
    summary = _summary([
        _player(1, "Aiden Markram", [_bat_ls_full(1, "c", "c sub (EG Barnard) b Ellis",
                                                  bowler=(900, "Test Bowler"),
                                                  fielders=[(578769, "EG Barnard", True)])]),
        _player(900, "Test Bowler", [_bowl_ls(6, period=2)]),
    ])
    items = [_ball(i, "Aiden Markram", "Test Bowler") for i in range(5)]
    items.append(_ball(5, "Aiden Markram", "Test Bowler", dismissal=("caught", "Aiden Markram")))
    _install(wcmod, monkeypatch, items, summary)

    perf, _ = wcmod.parse_espn("ev-substitute-catch")

    sub = perf[wcmod.norm("EG Barnard")]
    assert sub["catches"] == 1
    assert sub["espn_id"] == "578769", "the sub must be ID-anchored, never name-resolved"
    assert sub["played"] is False, "+4 in-XI bonus for a substitute fielder"


@pytest.mark.parametrize("short,text,legal", [
    # the marker ESPN really uses on an outcome-labelled no-ball -> illegal delivery
    ("Ferguson to Chaudhary, (no ball) 1 run", "full outside off", False),
    # COMMENTARY PROSE saying it was NOT a no-ball -> must stay legal (ev1521204 ov9.4)
    ("Elwiss to Kapp, no run", "A brief check to see if it is a No ball but nothing doing "
                               "on that count", True),
    # ev1521213: "Check ongoing for a No Ball on height. But nothing doing"
    ("Elwiss to Sutherland, SIX", "Check ongoing for a No Ball on height. But nothing doing", True),
    # ev1521232: a no-ball earlier in the over mentioned in passing
    ("Wood to Salt, FOUR", "Those five balls - well, six including the no-ball - changed it", True),
])
def test_no_ball_detection_reads_the_marker_not_the_commentary(wcmod, monkeypatch, short, text,
                                                               legal):
    """WHAT WENT WRONG: is_nb searched shortText + text, i.e. the whole commentary paragraph. A
    commentator SAYING "no ball" flipped a legal delivery to illegal — the bowler lost a legal
    ball (and its dot), and on a run/four/six the batter lost a run to the penalty-stripping
    `sv - 1`. 17 such deliveries across the 101 cached events (10 with a points effect, +24 FP;
    the other 7 were already-illegal wides). Measured against cricsheet on the 52 events with an
    official twin: all 61 real no-balls are caught by the shortText marker alone, so the prose
    search was 0/61 load-bearing and 17/17 wrong."""
    it = _ball(0, "Bat One", "Test Bowler", runs=1)
    it["shortText"] = short
    it["text"] = text
    summary = _summary([_player(900, "Test Bowler", [_bowl_ls(1, period=2)])])
    _install(wcmod, monkeypatch, [it], summary)

    perf, _ = wcmod.parse_espn("ev-nb-" + ("legal" if legal else "nb"))

    assert perf[wcmod.norm("Test Bowler")]["balls"] == (1 if legal else 0)
    assert perf[wcmod.norm("Bat One")]["r"] == (1 if legal else 0)
