"""Characterization tests for the Dream11 scorer `score(p, role)` (wc_fps_to_csv.py:724-764).
Green on CURRENT code — these pin the scoring contract so any future change is caught.
Covers men's + women's identically (the scorer is gender-agnostic; tour differences are
squads/series, not rules)."""
import pytest


# ── Batting: runs + boundaries + milestones (highest-only) ──────────────────
def test_pure_batting_no_milestone(perf, wcmod):
    s = wcmod.score(perf(r=20, b=15, played=True, **{"4s": 2, "6s": 1}), "BAT")
    # 20 runs + 2*4 + 1*6 = 34 ; SR 133.3 -> +2 ; XI +4
    assert s["bat"] == 34
    assert s["sr"] == 2
    assert s["total"] == 40


@pytest.mark.parametrize("runs,expected_milestone", [
    (24, 0), (25, 4), (49, 4), (50, 8), (74, 8), (75, 12), (99, 12), (100, 16),
])
def test_milestone_boundaries_highest_only(perf, wcmod, runs, expected_milestone):
    # b == r => SR 100 (neutral, no SR pts) so the milestone is isolated.
    s = wcmod.score(perf(r=runs, b=runs, played=True), "BAT")
    assert s["sr"] == 0
    assert s["bat"] == runs + expected_milestone, f"runs={runs}"
    # no double-count: a century is +16 only, never 16+12+8+4
    if runs == 100:
        assert s["bat"] == 116


# ── Strike-rate tiers (need >= 10 balls; BOWL role is exempt) ────────────────
def test_sr_floor_under_10_balls(perf, wcmod):
    assert wcmod.score(perf(r=20, b=9, played=True), "BAT")["sr"] == 0
    assert wcmod.score(perf(r=20, b=10, played=True), "BAT")["sr"] == 6  # SR 200


@pytest.mark.parametrize("r,b,expected_sr", [
    (18, 10, 6),    # 180  -> >170
    (17, 10, 4),    # 170  -> not >170, >150 (boundary)
    (16, 10, 4),    # 160  -> >150
    (15, 10, 2),    # 150  -> not >150, >=130 (boundary)
    (13, 10, 2),    # 130  -> >=130 (inclusive boundary)
    (12, 10, 0),    # 120  -> neutral band
    (7, 10, -2),    # 70   -> 60..70
    (6, 10, -2),    # 60   -> 60..70 boundary
    (5, 10, -4),    # 50   -> 50..60
    (4, 10, -6),    # 40   -> <50
])
def test_sr_tiers(perf, wcmod, r, b, expected_sr):
    assert wcmod.score(perf(r=r, b=b, played=True), "BAT")["sr"] == expected_sr


def test_sr_not_applied_to_bowler(perf, wcmod):
    # Same line, BOWL role -> no SR pts (line 733 `role != "BOWL"`).
    assert wcmod.score(perf(r=18, b=10, played=True), "BOWL")["sr"] == 0


# ── Duck (-2) for BAT/WK/AR dismissed for 0; never for BOWL ──────────────────
def test_duck_for_batters(perf, wcmod):
    assert wcmod.score(perf(r=0, b=3, dismissed=True, played=True), "BAT")["bat"] == -2


def test_duck_off_zero_balls_runout(perf, wcmod):
    # b=0,r=0 but dismissed (backing up) still a duck for non-BOWL (line 741-744 intent).
    assert wcmod.score(perf(r=0, b=0, dismissed=True, played=True), "WK")["bat"] == -2


def test_no_duck_for_bowler(perf, wcmod):
    assert wcmod.score(perf(r=0, b=2, dismissed=True, played=True), "BOWL")["bat"] == 0


# ── Bowling: wickets + hauls (highest-only); econ floor at 12 balls ─────────
@pytest.mark.parametrize("w,expected_haul", [(2, 0), (3, 4), (4, 8), (5, 12)])
def test_wicket_hauls(perf, wcmod, w, expected_haul):
    # balls=24, rc=32 => econ 8 (neutral, no econ pts) -> haul isolated.
    s = wcmod.score(perf(w=w, balls=24, runs_conceded=32, played=True), "BOWL")
    assert s["eco"] == 0
    assert s["bowl"] == w * 30 + expected_haul


def test_econ_floor_under_12_balls(perf, wcmod):
    assert wcmod.score(perf(balls=11, runs_conceded=5, played=True), "BOWL")["eco"] == 0


@pytest.mark.parametrize("rc,expected_eco", [
    (8, 6),    # econ 4.0  -> <5
    (10, 4),   # econ 5.0  -> not <5, <6 (boundary)
    (12, 2),   # econ 6.0  -> not <6, <=7
    (14, 2),   # econ 7.0  -> <=7 (boundary)
    (16, 0),   # econ 8.0  -> neutral
    (20, -2),  # econ 10.0 -> 10..11
    (22, -2),  # econ 11.0 -> 10..11 (boundary, NOT the 11< branch)
    (24, -4),  # econ 12.0 -> 11<econ<=12 (boundary)
    (26, -6),  # econ 13.0 -> >12
])
def test_econ_tiers(perf, wcmod, rc, expected_eco):
    assert wcmod.score(perf(balls=12, runs_conceded=rc, played=True), "BOWL")["eco"] == expected_eco


# ── Fielding ────────────────────────────────────────────────────────────────
def test_fielding(perf, wcmod):
    assert wcmod.score(perf(catches=2, played=True), "WK")["field"] == 16
    assert wcmod.score(perf(catches=3, played=True), "WK")["field"] == 28  # 3*8 + 4 bonus
    assert wcmod.score(perf(stumpings=1, played=True), "WK")["field"] == 12
    assert wcmod.score(perf(runouts=2, dro=1, played=True), "BAT")["field"] == 18  # 12 + 6


# ── +4 XI appearance ────────────────────────────────────────────────────────
def test_xi_bonus(perf, wcmod):
    assert wcmod.score(perf(played=True), "BAT")["xi"] == 4
    assert wcmod.score(perf(played=False), "BAT")["xi"] == 0


# ── Cross-tour smoke: a men's-style and women's-style all-round line ─────────
def test_allrounder_line(perf, wcmod):
    # 40(30b, 4x4, 1x6) + 2 wkts in 24 balls for 30, 1 catch.
    s = wcmod.score(perf(r=40, b=30, w=2, balls=24, runs_conceded=30, catches=1,
                         played=True, **{"4s": 4, "6s": 1}), "AR")
    # bat: 40 + 16 + 6 + m25(4) = 66 ; sr 133.3 -> +2 ; bowl 60 ; eco 7.5 -> 0 ; field 8 ; xi 4
    assert s["bat"] == 66
    assert s["sr"] == 2
    assert s["bowl"] == 60
    assert s["field"] == 8
    assert s["total"] == 66 + 2 + 60 + 8 + 4
