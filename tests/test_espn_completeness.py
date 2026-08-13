"""ESPN can return an EMPTY card that looks complete — parse_espn must refuse to score it.

Observed live on CPL ev 1534183 (13 Aug 2026): a 200 whose whole body was

    {"commentary": {"count": 1, "pageCount": 1, "items": [<pre-match preamble item>]}}

for a match ESPN's own scoreboard called Final. It is internally consistent, so every guard that
compares items against ESPN's self-reported `count` waves it through — and the match published with
all 22 players Played=Y, every stat 0 and a bare +4 XI bonus. With settlement recording live, that
is the number that would have frozen as the money baseline.

The defence is the SCORECARD ball-count cross-check: a different endpoint and a different field
family, so it cannot go blank in the same breath as the ball-by-ball. These tests pin that.
"""
import pytest


PREAMBLE = {
    "id": "999999999999999",
    "playType": {"id": "2", "description": "no run"},
    "period": 2,
    "preText": "<strong>8:57 pm</strong> Hello and welcome back, folks!",
    "text": "", "shortText": "", "scoreValue": 0,
    "bowler": {"athlete": {}},          # <- no bowler: not a delivery
    "batsman": {"athlete": {}},
}


def _delivery(i, runs=1):
    return {
        "id": str(i),
        "playType": {"id": "1", "description": "run" if runs else "no run"},
        "period": 1,
        "scoreValue": runs,
        "text": "", "shortText": "", "preText": "",
        "over": {"number": i // 6},
        "bowler": {"athlete": {"id": "111", "fullName": "Test Bowler"}},
        "batsman": {"athlete": {"id": "222", "fullName": "Test Batter"}},
    }


def _summary_with_balls(n):
    """A scorecard whose bowling figures account for n deliveries."""
    return {
        "rosters": [{
            "team": {"displayName": "Test XI"},
            "roster": [{
                "starter": True,
                "athlete": {"id": "111", "fullName": "Test Bowler"},
                "linescores": [{"statistics": {
                    "bowling": {"overallLhb": {"balls": 0}, "overallRhb": {"balls": n}},
                }}],
            }],
        }],
    }


def _install(wcmod, monkeypatch, pbp, summary):
    def fake(path, cache=True, **params):
        return pbp if path == "playbyplay" else summary
    monkeypatch.setattr(wcmod, "espn_get", fake)


def test_empty_card_that_looks_complete_is_refused(wcmod, monkeypatch):
    """count=1 / items=[preamble] against a scorecard saying 236 balls -> refuse, don't score."""
    pbp = {"commentary": {"count": 1, "pageIndex": 1, "pageCount": 1, "items": [PREAMBLE]}}
    _install(wcmod, monkeypatch, pbp, _summary_with_balls(236))

    perf, super_over = wcmod.parse_espn("1534183")

    assert perf == {}, (
        "an ESPN card with 0 deliveries but a 236-ball scorecard was SCORED — this is the "
        "all-players-on-+4 bug that would freeze as a settlement baseline"
    )
    assert super_over is False


def test_truncated_card_is_refused(wcmod, monkeypatch):
    """A card short of the scorecard's ball count is a partial fetch, not a short innings."""
    items = [_delivery(i) for i in range(200)]
    pbp = {"commentary": {"count": 200, "pageIndex": 1, "pageCount": 1, "items": items}}
    _install(wcmod, monkeypatch, pbp, _summary_with_balls(236))

    perf, _ = wcmod.parse_espn("ev-truncated")

    assert perf == {}, "200 of 236 deliveries scored as though the innings were complete"


def test_complete_card_scores(wcmod, monkeypatch):
    """The guard must not block a genuinely complete match."""
    items = [_delivery(i) for i in range(236)]
    pbp = {"commentary": {"count": 236, "pageIndex": 1, "pageCount": 1, "items": items}}
    _install(wcmod, monkeypatch, pbp, _summary_with_balls(236))

    perf, _ = wcmod.parse_espn("ev-complete")

    assert perf, "a complete card was refused"
    bowler = perf[wcmod.norm("Test Bowler")]
    assert bowler["balls"] == 236
    assert bowler["runs_conceded"] == 236


def test_extras_count_toward_the_ball_total(wcmod, monkeypatch):
    """ESPN's scorecard `balls` counts wides and no-balls, so the check must too.

    Measured on CPL ev 1534183: 226 legal + 10 wides = 236 = the scorecard's total. Counting only
    LEGAL deliveries made the guard reject all five completed CPL matches.
    """
    items = [_delivery(i) for i in range(226)]
    for i in range(10):
        w = _delivery(1000 + i, runs=1)
        w["playType"] = {"id": "3", "description": "wide"}
        items.append(w)
    pbp = {"commentary": {"count": 236, "pageIndex": 1, "pageCount": 1, "items": items}}
    _install(wcmod, monkeypatch, pbp, _summary_with_balls(236))

    perf, _ = wcmod.parse_espn("ev-extras")

    assert perf, "a complete card was refused because wides were excluded from the ball count"
    assert perf[wcmod.norm("Test Bowler")]["balls"] == 226   # legal balls only, for economy


def test_scorecard_without_bowling_figures_does_not_block(wcmod, monkeypatch):
    """No bowling figures = the check is unavailable, not failed. Must still score."""
    items = [_delivery(i) for i in range(120)]
    pbp = {"commentary": {"count": 120, "pageIndex": 1, "pageCount": 1, "items": items}}
    _install(wcmod, monkeypatch, pbp, {"rosters": []})

    perf, _ = wcmod.parse_espn("ev-nofigures")

    assert perf, "an unverifiable card must still score (it is unverified, not wrong)"
