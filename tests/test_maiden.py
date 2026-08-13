"""A maiden is an over with NO RUNS CHARGED TO THE BOWLER — wides and no-balls break it.

Byes and leg-byes do NOT break a maiden (they are the keeper's leak, never debited to the bowler);
wides and no-balls DO (their penalty run is the bowler's).

Both parsers accumulated the over total only on LEGAL deliveries, so a wide/no-ball reached
`runs_conceded` but never `over_runs`. The over still reaches exactly 6 LEGAL balls — the extra is
re-bowled, so it runs to 7 or 8 deliveries — and `legal == 6 and over_runs == 0` therefore passed,
crediting a maiden that never happened. Measured against raw cricsheet deliveries:

    cs_lpl   ours=47     correct=39     PHANTOM=8     (17%)
    cs_odi   ours=16391  correct=13708  PHANTOM=2683  (16%)
    cs_hnd   ours=0      correct=0      PHANTOM=0     (the Hundred scorer awards no maiden)

Worth +12 FP each in T20. Real examples from the LPL corpus:
    1238758.json over 1  (I Udana)       8 deliveries = 6 legal + 2 wides, 2 charged  -> scored MAIDEN
    1324537.json over 0  (NLTC Perera)   7 deliveries = 6 legal + a 5-run wide        -> scored MAIDEN

⚠ Why no harness caught it: BOTH parsers carried the identical rule, so ESPN-vs-cricsheet compared
EQUAL (LPL maidens matched exactly; ODI 22 v 22). This is the "both feeds agree and are both wrong"
case — invisible to cross-feed reconciliation, visible only by recomputing from raw deliveries.
These tests are that independent check, kept permanently.
"""
import json

import pytest


# ── cricsheet ────────────────────────────────────────────────────────────────────────────
def _cs_match(deliveries, tmp_path):
    """One over by one bowler, written as a minimal cricsheet file."""
    doc = {
        "info": {"teams": ["A", "B"], "gender": "male", "dates": ["2026-08-13"],
                 "registry": {"people": {"Bowler": "b1", "Batter": "x1", "NonStriker": "x2"}}},
        "innings": [{"team": "A", "overs": [{"over": 0, "deliveries": deliveries}]}],
    }
    p = tmp_path / "m.json"
    p.write_text(json.dumps(doc))
    return str(p)


def _d(batter=0, extras=None):
    runs = {"batter": batter, "extras": sum((extras or {}).values()), "total": batter + sum((extras or {}).values())}
    out = {"batter": "Batter", "bowler": "Bowler", "non_striker": "NonStriker", "runs": runs}
    if extras:
        out["extras"] = extras
    return out


def _cs_maidens(deliveries, tmp_path, wcmod):
    perf, _ = wcmod.parse_cricsheet(_cs_match(deliveries, tmp_path))
    return perf[wcmod.norm("Bowler")]["maidens"]


def test_cs_six_dots_is_a_maiden(wcmod, tmp_path):
    assert _cs_maidens([_d() for _ in range(6)], tmp_path, wcmod) == 1


def test_cs_wide_breaks_the_maiden(wcmod, tmp_path):
    """THE REGRESSION. 6 legal dots + a wide = 7 deliveries, 1 run charged. Not a maiden."""
    balls = [_d() for _ in range(6)] + [_d(extras={"wides": 1})]
    assert _cs_maidens(balls, tmp_path, wcmod) == 0


def test_cs_no_ball_breaks_the_maiden(wcmod, tmp_path):
    balls = [_d() for _ in range(6)] + [_d(extras={"noballs": 1})]
    assert _cs_maidens(balls, tmp_path, wcmod) == 0


def test_cs_leg_byes_do_NOT_break_the_maiden(wcmod, tmp_path):
    """The other half of the rule — do not over-correct. A leg-bye is never the bowler's run."""
    balls = [_d() for _ in range(5)] + [_d(extras={"legbyes": 4})]
    assert _cs_maidens(balls, tmp_path, wcmod) == 1


def test_cs_byes_do_NOT_break_the_maiden(wcmod, tmp_path):
    balls = [_d() for _ in range(5)] + [_d(extras={"byes": 2})]
    assert _cs_maidens(balls, tmp_path, wcmod) == 1


def test_cs_the_real_udana_over(wcmod, tmp_path):
    """LPL 1238758 over 1 verbatim: 4 dots, 2 wides, a 4-run leg bye, 1 dot.

    Both halves of the rule in ONE over — the leg-bye must not break it, the wides must.
    """
    balls = ([_d() for _ in range(4)]
             + [_d(extras={"wides": 1}), _d(extras={"wides": 1})]
             + [_d(extras={"legbyes": 4}), _d()])
    perf, _ = wcmod.parse_cricsheet(_cs_match(balls, tmp_path))
    b = perf[wcmod.norm("Bowler")]
    assert b["maidens"] == 0, "the two wides must break the maiden"
    assert b["runs_conceded"] == 2, "2 wides charged; the 4 leg-byes are the keeper's, not his"
    assert b["balls"] == 6, "wides are not balls bowled, though the over ran to 8 deliveries"


# ── ESPN ─────────────────────────────────────────────────────────────────────────────────
def _espn_item(i, desc, runs=0, over=0):
    return {"id": str(i), "playType": {"description": desc}, "period": 1, "scoreValue": runs,
            "over": {"number": over}, "text": "", "shortText": "", "preText": "",
            "bowler": {"athlete": {"id": "111", "fullName": "Bowler"}},
            "batsman": {"athlete": {"id": "222", "fullName": "Batter"}}}


def _espn_maidens(items, wcmod, monkeypatch):
    summary = {"rosters": [{"team": {"displayName": "A"}, "roster": [{
        "starter": True, "athlete": {"id": "111", "fullName": "Bowler"},
        # the ball-count cross-check counts EVERY delivery, extras included
        "linescores": [{"statistics": {"bowling": {"overallRhb": {"balls": len(items)}}}}]}]}]}
    monkeypatch.setattr(wcmod, "espn_get", lambda path, cache=True, **kw:
                        ({"commentary": {"count": len(items), "pageCount": 1, "items": items}}
                         if path == "playbyplay" else summary))
    perf, _ = wcmod.parse_espn("ev-maiden")
    return perf[wcmod.norm("Bowler")]["maidens"]


def test_espn_six_dots_is_a_maiden(wcmod, monkeypatch):
    assert _espn_maidens([_espn_item(i, "no run") for i in range(6)], wcmod, monkeypatch) == 1


def test_espn_wide_breaks_the_maiden(wcmod, monkeypatch):
    items = [_espn_item(i, "no run") for i in range(6)] + [_espn_item(99, "wide", runs=1)]
    assert _espn_maidens(items, wcmod, monkeypatch) == 0


def test_espn_leg_byes_do_NOT_break_the_maiden(wcmod, monkeypatch):
    items = [_espn_item(i, "no run") for i in range(5)] + [_espn_item(99, "leg bye", runs=4)]
    assert _espn_maidens(items, wcmod, monkeypatch) == 1


def test_both_parsers_agree_on_every_maiden_case(wcmod, monkeypatch, tmp_path):
    """The parsers must not drift apart — a divergence here IS an L1 recon row on a real match."""
    cases = [
        ("six dots", [_d() for _ in range(6)],
         [_espn_item(i, "no run") for i in range(6)], 1),
        ("wide", [_d() for _ in range(6)] + [_d(extras={"wides": 1})],
         [_espn_item(i, "no run") for i in range(6)] + [_espn_item(99, "wide", runs=1)], 0),
        ("leg bye", [_d() for _ in range(5)] + [_d(extras={"legbyes": 4})],
         [_espn_item(i, "no run") for i in range(5)] + [_espn_item(99, "leg bye", runs=4)], 1),
    ]
    for name, cs_balls, espn_items, expected in cases:
        cs = _cs_maidens(cs_balls, tmp_path, wcmod)
        es = _espn_maidens(espn_items, wcmod, monkeypatch)
        assert cs == es == expected, f"{name}: cricsheet={cs} espn={es} expected={expected}"
