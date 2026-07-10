"""Parity / regression tests for the ODI Dream11 scorer added Jul 2026.

Pins THREE things:
  1. The bot's `_score_odi` (wc_fps_to_csv.py) against HAND-COMPUTED expected totals
     (per Nishant's ODI screenshot: duck -3, dot=floor(dots/3), maiden +4, hauls
     4w/5w/6w=+4/+8/+12, SR min-20-balls bands, econ min-30-balls bands, milestones
     highest-only, +4 XI).
  2. The bot's `_score_t20` STILL returns the pre-split T20 values (proves the ODI
     split didn't change T20 behaviour).
  3. Cross-scorer AGREEMENT: the auction ETL's `compute_fantasy_points_odi`
     (cricket-auction-helper/data/etl_cricsheet.py) equals the bot's `_score_odi`
     total for every ODI fixture. The ETL ALWAYS adds +4 XI; the bot only adds +4
     when played=True — so we score the bot with played=True and compare directly.

All fixtures use the `perf`/`wcmod` fixtures from conftest.py.
"""
import os
import sys
import importlib

import pytest


# ── Import the auction ETL's ODI scorer (a DIFFERENT repo) ───────────────────
# It sits at ../../cricket-auction-helper/data/etl_cricsheet.py relative to this
# test. Import is side-effect-free (main() is __main__-gated; module level only
# defines constants + functions). If the sibling repo isn't present, skip the
# cross-scorer checks rather than error the whole file.
_ETL_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "cricket-auction-helper", "data"))
etl = None
if os.path.isdir(_ETL_DIR):
    sys.path.insert(0, _ETL_DIR)
    try:
        etl = importlib.import_module("etl_cricsheet")
    except Exception:  # pragma: no cover - defensive
        etl = None


def _to_etl(p):
    """Map a bot perf dict -> the ETL's perf-dict key names."""
    return {
        "bat_runs": p["r"], "bat_balls": p["b"],
        "bat_4s": p["4s"], "bat_6s": p["6s"],
        "bat_dismissed": p["dismissed"],
        "bowl_balls": p["balls"], "bowl_runs": p["runs_conceded"],
        "bowl_wickets": p["w"], "bowl_maidens": p["maidens"],
        "bowl_dots": p["dots"], "bowl_lbw_bowled": p["lbwb"],
        "catches": p["catches"], "stumpings": p["stumpings"],
        "run_outs": p["runouts"], "direct_run_outs": p["dro"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# ODI full-total fixtures with HAND-COMPUTED expected totals (played=True so the
# +4 XI is included — matches the ETL which always adds +4).
# Each tuple: (id, kwargs-for-perf, role, expected_total, human-derivation)
# ─────────────────────────────────────────────────────────────────────────────
ODI_FIXTURES = [
    # Century + SR>140: 100 + 10*4 + 2*6 = 152 ; +16 (100 milestone) = 168 ;
    #   SR = 100/60*100 = 166.7 > 140 -> +6 ; XI +4  => 178
    ("century_sr", dict(r=100, b=60, played=True, **{"4s": 10, "6s": 2}), "BAT", 178),

    # 5-wkt haul: 5*30=150 + 2*8(lbwb)=16 + (18//3=6)*1=6 + 1*4(maiden)=4 = 176 ;
    #   +8 (5w haul) = 184 ; econ 40/10=4.0 -> +2 ; XI +4  => 190
    ("haul5", dict(w=5, balls=60, runs_conceded=40, lbwb=2, dots=18, maidens=1,
                   played=True), "BOWL", 190),

    # 6-wkt haul: 6*30=180 + 3*8=24 + (24//3=8)=8 + 2*4=8 = 220 ;
    #   +12 (6w haul) = 232 ; econ 20/9=2.22 <2.5 -> +6 ; XI +4  => 242
    ("haul6", dict(w=6, balls=54, runs_conceded=20, lbwb=3, dots=24, maidens=2,
                   played=True), "BOWL", 242),

    # Dot-floor: 10 dots -> 10//3 = 3 pts. balls=24 (<30) so NO econ. XI +4  => 7
    ("dotfloor10", dict(balls=24, runs_conceded=20, dots=10, played=True), "BOWL", 7),

    # Maiden: (6//3=2) dots=2 + 1*4(maiden)=4 = 6 ; econ 30/5=6.0 -> neutral gap
    #   (4.5<econ<7) -> 0 ; XI +4  => 10
    ("maiden", dict(balls=30, runs_conceded=30, dots=6, maidens=1, played=True), "BOWL", 10),

    # Duck (BAT) dismissed for 0 off 5: batting block enters (b>0) -> 0 runs ;
    #   duck -3 ; XI +4  => 1
    ("duck_bat", dict(r=0, b=5, dismissed=True, played=True), "BAT", 1),

    # Duck 0-off-0 run-out (WK): batting block skipped (b==0 & r==0) ; duck -3
    #   still applies (outside the gate) ; XI +4  => 1
    ("duck_runout_0off0", dict(r=0, b=0, dismissed=True, played=True), "WK", 1),

    # All-rounder combo: bat 55 + 4*4 + 1*6 = 77 ; +8 (50 milestone) = 85 ;
    #   SR 55/50*100=110 (>=100) -> +2 ; bowl 2*30=60 + (15//3=5)=5 = 65 (no haul) ;
    #   econ 36/8=4.5 (<=4.5) -> +2 ; field 1 catch=8 ; XI +4
    #   => 85 + 2 + 65 + 2 + 8 + 4 = 166
    ("allrounder", dict(r=55, b=50, w=2, balls=48, runs_conceded=36, dots=15,
                        catches=1, played=True, **{"4s": 4, "6s": 1}), "AR", 166),
]


@pytest.mark.parametrize("cid,kw,role,expected",
                         ODI_FIXTURES, ids=[f[0] for f in ODI_FIXTURES])
def test_odi_totals_bot(perf, wcmod, cid, kw, role, expected):
    """Bot `_score_odi` total == hand-computed expected."""
    s = wcmod._score_odi(perf(**kw), role)
    assert s["total"] == expected, f"{cid}: got {s}"


@pytest.mark.skipif(etl is None, reason="cricket-auction-helper ETL not importable")
@pytest.mark.parametrize("cid,kw,role,expected",
                         ODI_FIXTURES, ids=[f[0] for f in ODI_FIXTURES])
def test_odi_bot_equals_etl(perf, wcmod, cid, kw, role, expected):
    """CROSS-SCORER: ETL `compute_fantasy_points_odi` == bot `_score_odi` total.
    ETL always adds +4 XI; bot fixtures use played=True, so totals must match."""
    bot_total = wcmod._score_odi(perf(**kw), role)["total"]
    etl_total = etl.compute_fantasy_points_odi(_to_etl(perf(**kw)), role)
    assert etl_total == bot_total == expected, (
        f"{cid}: bot={bot_total} etl={etl_total} expected={expected}")


# ─────────────────────────────────────────────────────────────────────────────
# ODI strike-rate band EDGES (need b>=20; BOWL exempt). Assert on the isolated
# `sr` sub-component (milestone/base don't matter for the band assertion).
#   >140 +6 / >120 +4 / >=100 +2 / 40-50 -2 / 30-39.99 -4 / <30 -6
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("r,b,expected_sr,note", [
    (29, 20, 6, "145.0 -> >140"),
    (28, 20, 4, "140.0 -> NOT >140, >120 (boundary is +4)"),
    (25, 20, 4, "125.0 -> just above 120 (=='120.1' tier)"),
    (24, 20, 2, "120.0 -> NOT >120, >=100 (boundary is +2)"),
    (20, 20, 2, "100.0 -> >=100 (inclusive boundary)"),
    (10, 20, -2, "50.0  -> 40..50 (upper boundary)"),
    (8, 20, -2, "40.0  -> 40..50 (lower boundary)"),
    (7, 20, -4, "35.0  -> 30..39.99"),
    (6, 20, -4, "30.0  -> 30..39.99 (lower boundary)"),
    (5, 20, -6, "25.0  -> <30"),
])
def test_odi_sr_bands(perf, wcmod, r, b, expected_sr, note):
    assert wcmod._score_odi(perf(r=r, b=b, played=True), "BAT")["sr"] == expected_sr, note


def test_odi_sr_floor_under_20_balls(perf, wcmod):
    # 19 balls -> no SR band even at a huge SR; 20 balls -> band applies.
    assert wcmod._score_odi(perf(r=40, b=19, played=True), "BAT")["sr"] == 0
    assert wcmod._score_odi(perf(r=40, b=20, played=True), "BAT")["sr"] == 6  # SR 200


def test_odi_sr_not_applied_to_bowler(perf, wcmod):
    assert wcmod._score_odi(perf(r=40, b=20, played=True), "BOWL")["sr"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# ODI economy band EDGES (need balls>=30). balls=60 (10 overs) -> econ = rc/10.
#   <2.5 +6 / <3.5 +4 / <=4.5 +2 / 7-8 -2 / >8&<=9 -4 / >9 -6
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("rc,expected_eco,note", [
    (20, 6, "2.0  -> <2.5"),
    (25, 4, "2.5  -> NOT <2.5, <3.5 (boundary is +4)"),
    (35, 2, "3.5  -> NOT <3.5, <=4.5 (boundary is +2)"),
    (45, 2, "4.5  -> <=4.5 (inclusive boundary)"),
    (60, 0, "6.0  -> neutral gap (4.5<econ<7)"),
    (70, -2, "7.0  -> 7..8 (lower boundary)"),
    (80, -2, "8.0  -> 7..8 (upper boundary is -2)"),
    (90, -4, "9.0  -> 8<econ<=9 (upper boundary is -4)"),
    (100, -6, "10.0 -> >9"),
])
def test_odi_econ_bands(perf, wcmod, rc, expected_eco, note):
    assert wcmod._score_odi(perf(balls=60, runs_conceded=rc, played=True), "BOWL")["eco"] == expected_eco, note


def test_odi_econ_floor_under_30_balls(perf, wcmod):
    # 29 balls -> no econ band; 30 balls -> band applies.
    assert wcmod._score_odi(perf(balls=29, runs_conceded=10, played=True), "BOWL")["eco"] == 0
    assert wcmod._score_odi(perf(balls=30, runs_conceded=10, played=True), "BOWL")["eco"] == 6  # econ 2.0


# ─────────────────────────────────────────────────────────────────────────────
# T20 REGRESSION — `_score_t20` must still return the pre-split values. Also
# confirm the dispatcher routes "T20" to `_score_t20`.
# ─────────────────────────────────────────────────────────────────────────────
def test_t20_fifty_off_30_unchanged(perf, wcmod):
    # 50*1=50 ; +8 (50 milestone, highest-only) = 58 ; SR 50/30*100=166.7 >150 -> +4 ;
    #   XI +4  => 66
    p = perf(r=50, b=30, played=True)
    s = wcmod._score_t20(p, "BAT")
    assert s["bat"] == 58 and s["sr"] == 4 and s["total"] == 66
    # dispatcher default + explicit "T20" both route here
    assert wcmod.score(perf(r=50, b=30, played=True), "BAT", "T20")["total"] == 66


def test_t20_five_wkt_haul_unchanged(perf, wcmod):
    # 5*30=150 + 10*1(dots, per-dot in T20) + 1*12(maiden T20) = 172 ; +12 (5w) = 184 ;
    #   econ 20/(24/6)=5.0 -> +4 ; XI +4  => 192
    s = wcmod._score_t20(perf(w=5, balls=24, runs_conceded=20, dots=10, maidens=1,
                              played=True), "BOWL")
    assert s["bowl"] == 184 and s["eco"] == 4 and s["total"] == 192


def test_t20_duck_unchanged(perf, wcmod):
    # T20 duck is -2 (ODI is -3). +4 XI => 2
    s = wcmod._score_t20(perf(r=0, b=3, dismissed=True, played=True), "BAT")
    assert s["bat"] == -2 and s["total"] == 2


def test_t20_vs_odi_differ_where_expected(perf, wcmod):
    """Sanity: the two scorers really DO diverge on the ODI-specific rules."""
    # duck: T20 -2 vs ODI -3
    duck = perf(r=0, b=3, dismissed=True, played=True)
    assert wcmod._score_t20(duck, "BAT")["bat"] == -2
    assert wcmod._score_odi(duck, "BAT")["bat"] == -3
    # maiden: T20 +12 vs ODI +4 ; dots: T20 +1/dot vs ODI +1/3dots
    bowl = perf(balls=30, runs_conceded=30, dots=6, maidens=1, played=True)
    assert wcmod._score_t20(bowl, "BOWL")["bowl"] == 6 * 1 + 1 * 12   # 18
    assert wcmod._score_odi(bowl, "BOWL")["bowl"] == (6 // 3) * 1 + 1 * 4  # 6
