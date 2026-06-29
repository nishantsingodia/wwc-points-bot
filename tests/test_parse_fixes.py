"""Tests for the feed-parser data-quality fixes:
- caught-&-bowled catch credit in cricapi parse_match (cricapi's `catching` array omits it),
- robust retirement/obstruction guard + 'leg before wicket' in parse_espn (the De Lange 2/3 bug)."""


def _scorecard(batting, bowling, catching):
    return {"data": {"scorecard": [{
        "inning": "Australia Women Inning 1",
        "batting": batting, "bowling": bowling, "catching": catching,
    }]}}


def test_caught_and_bowled_credits_bowler(wcmod, monkeypatch):
    fake = _scorecard(
        batting=[
            {"batsman": {"name": "Beth Mooney"}, "r": 20, "b": 15, "4s": 2, "6s": 0,
             "dismissal": "caught", "dismissal-text": "c & b Sophie Ecclestone"},
            {"batsman": {"name": "Ellyse Perry"}, "r": 30, "b": 20, "4s": 3, "6s": 0,
             "dismissal": "caught", "dismissal-text": "c Heather Knight b Sophie Ecclestone"},
        ],
        bowling=[{"bowler": {"name": "Sophie Ecclestone"}, "o": 4, "r": 25, "w": 2, "m": 0}],
        catching=[{"catcher": {"name": "Heather Knight"}, "catch": 1, "stumped": 0}],
    )
    monkeypatch.setattr(wcmod, "api", lambda *a, **k: fake)
    perf = wcmod.parse_match("x")
    ecc = perf[wcmod.norm("Sophie Ecclestone")]
    assert ecc["catches"] == 1   # the caught-&-bowled catch (cricapi's `catching` omits it)
    assert ecc["w"] == 2
    # the regular catch (Knight, off Perry) is NOT double-credited to the bowler
    assert perf[wcmod.norm("Heather Knight")]["catches"] == 1


def test_caught_and_bowled_same_name_form(wcmod, monkeypatch):
    fake = _scorecard(
        batting=[{"batsman": {"name": "A B"}, "r": 5, "b": 4, "4s": 0, "6s": 0,
                  "dismissal": "caught", "dismissal-text": "c Megan Schutt b Megan Schutt"}],
        bowling=[{"bowler": {"name": "Megan Schutt"}, "o": 2, "r": 10, "w": 1, "m": 0}],
        catching=[],
    )
    monkeypatch.setattr(wcmod, "api", lambda *a, **k: fake)
    perf = wcmod.parse_match("x")
    assert perf[wcmod.norm("Megan Schutt")]["catches"] == 1


def test_normal_catch_not_credited_to_bowler(wcmod, monkeypatch):
    # a plain catch (catcher != bowler) must NOT add a catch to the bowler (no double count)
    fake = _scorecard(
        batting=[{"batsman": {"name": "A B"}, "r": 5, "b": 4, "4s": 0, "6s": 0,
                  "dismissal": "caught", "dismissal-text": "c Alyssa Healy b Megan Schutt"}],
        bowling=[{"bowler": {"name": "Megan Schutt"}, "o": 2, "r": 10, "w": 1, "m": 0}],
        catching=[{"catcher": {"name": "Alyssa Healy"}, "catch": 1, "stumped": 0}],
    )
    monkeypatch.setattr(wcmod, "api", lambda *a, **k: fake)
    perf = wcmod.parse_match("x")
    assert perf[wcmod.norm("Megan Schutt")]["catches"] == 0
    assert perf[wcmod.norm("Alyssa Healy")]["catches"] == 1


def test_espn_retirement_not_a_wicket_and_lbw_bonus(wcmod, monkeypatch):
    commentary = {"commentary": {"items": [
        {"playType": {"description": "no run"},
         "bowler": {"athlete": {"fullName": "Caroline de Lange"}},
         "batsman": {"athlete": {"fullName": "Some Batter"}}, "scoreValue": 0,
         "dismissal": {"dismissal": True, "type": "retired not out (hurt)"}, "shortText": "retired hurt"},
        {"playType": {"description": "no run"},
         "bowler": {"athlete": {"fullName": "Caroline de Lange"}},
         "batsman": {"athlete": {"fullName": "Another Batter"}}, "scoreValue": 0,
         "dismissal": {"dismissal": True, "type": "leg before wicket"}, "shortText": "lbw"},
    ]}}
    monkeypatch.setattr(wcmod, "espn_get", lambda *a, **k: commentary)
    monkeypatch.setattr(wcmod, "espn_xi", lambda *a, **k: {})
    perf, _ = wcmod.parse_espn("evt")
    dl = perf[wcmod.norm("Caroline de Lange")]
    assert dl["w"] == 1       # only the lbw counts; the "retired not out (hurt)" does NOT
    assert dl["lbwb"] == 1    # "leg before wicket" now triggers the +8 bonus
