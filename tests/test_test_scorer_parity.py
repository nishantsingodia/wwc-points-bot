"""The bot's Test scorer must agree EXACTLY with the auction ETL's, on real cricsheet Tests.

Two independent implementations of the same FPS drift the moment one is edited alone -- and the ODI
scorer already has three copies (ETL, rules.ts, bot). This pins the red-ball pair to each other on
real data rather than on hand-written cases, so a change to either side that alters a single
innings' points fails here.
"""
import glob
import importlib.util
import json
import os
import sys

import pytest

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUC = os.path.join(os.path.dirname(BOT), "cricket-auction-helper")
RAW = os.path.join(AUC, "data", "raw", "tests")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def etl():
    p = os.path.join(AUC, "data", "etl_cricsheet.py")
    if not os.path.exists(p):
        pytest.skip("auction ETL not present")
    return _load(p, "etl_cricsheet")


@pytest.fixture(scope="module")
def bot():
    os.environ.setdefault("WC_SKIP_MAIN", "1")
    p = os.path.join(BOT, "wc_fps_to_csv.py")
    sys.argv = ["wc_fps_to_csv.py"]
    return _load(p, "wc_fps")


def _bot_perf(inns):
    """ETL innings counter dict -> the bot's perf shape."""
    return dict(
        r=inns["bat_runs"], b=inns["bat_balls"],
        **{"4s": inns["bat_4s"], "6s": inns["bat_6s"]},
        dismissed=bool(inns["bat_dismissed"]),
        balls=inns["bowl_balls"], runs_conceded=inns["bowl_runs"], w=inns["bowl_wickets"],
        lbwb=inns["bowl_lbw_bowled"], dots=inns["bowl_dots"], maidens=inns["bowl_maidens"],
        catches=inns["catches"], stumpings=inns["stumpings"],
        runouts=inns["run_outs"], dro=inns["direct_run_outs"],
        played=True,
    )


@pytest.mark.parametrize("role", ["BAT", "BOWL", "AR", "WK"])
def test_per_innings_parity_on_real_tests(etl, bot, role):
    files = sorted(glob.glob(os.path.join(RAW, "*.json")))
    if not files:
        pytest.skip("no cricsheet Test data staged")
    checked = 0
    for f in files[-25:]:                      # the 25 most recent Tests
        m = etl.parse_match(f)
        for perf in m["performances"].values():
            detail = perf.get("innings_detail")
            if not detail:
                continue
            # per innings
            for inns in detail:
                want = etl.compute_fantasy_points_test(inns, role)
                got_bat, got_bowl, got_field = bot._score_test_innings(_bot_perf(inns), role)
                assert got_bat + got_bowl + got_field == pytest.approx(want), (
                    f"{f} {perf.get('name','?')} innings mismatch: bot="
                    f"{got_bat + got_bowl + got_field} etl={want} inns={inns}"
                )
                checked += 1
            # Whole match, including the once-per-match XI bonus. The match row must be the FOLD of
            # the innings, exactly as parse_cricsheet/parse_espn build it -- _score_test reads the
            # untiered fielding off it, so handing it innings 1 alone would under-count catches.
            want_match = etl.score_red_ball_match(detail, role)
            splits = [_bot_perf(i) for i in detail]
            folded = _bot_perf(detail[0])
            for extra in splits[1:]:
                for k in ("r", "b", "4s", "6s", "balls", "runs_conceded", "w", "lbwb",
                          "dots", "maidens", "catches", "stumpings", "runouts", "dro"):
                    folded[k] += extra[k]
                folded["dismissed"] = folded["dismissed"] or extra["dismissed"]
            folded["innings"] = splits
            assert bot._score_test(folded, role)["total"] == pytest.approx(want_match), (
                f"{f} {perf.get('name','?')} match total mismatch"
            )
    assert checked > 200, f"parity ran on only {checked} innings — too thin to trust"


def test_substitute_fielder_does_not_get_the_xi_bonus(bot, tmp_path):
    """A pure fielder must not be marked played by the per-innings accumulator.

    Regression: routing stat accumulation through iget() originally set played=True on everyone it
    touched, which handed the +4 announced-XI bonus to a SUBSTITUTE FIELDER who only took a catch.
    Caught on 8 of 48 white-ball matches (D Nikolov, T Marumani, RA Jadeja, BC Fortuin, ...).
    """
    import json
    m = {
        "info": {
            "match_type": "T20",
            "teams": ["A", "B"],
            "dates": ["2026-01-01"],
            "players": {"A": ["Bat One"], "B": ["Bowl One"]},   # the sub is in NEITHER XI
            "registry": {"people": {}},
        },
        "innings": [{
            "team": "A",
            "overs": [{"over": 0, "deliveries": [
                {"batter": "Bat One", "bowler": "Bowl One", "non_striker": "Bat One",
                 "runs": {"batter": 0, "extras": 0, "total": 0},
                 "wickets": [{"kind": "caught", "player_out": "Bat One",
                              "fielders": [{"name": "Sub Fielder"}]}]},
            ]}],
        }],
    }
    f = tmp_path / "sub.json"
    f.write_text(json.dumps(m))
    perf, _ = bot.parse_cricsheet(str(f))
    sub = perf[bot.norm("Sub Fielder")]
    assert sub["catches"] == 1, "the catch must still be credited"
    assert sub["played"] is False, "a substitute fielder must NOT be marked played (+4 XI)"
    assert perf[bot.norm("Bowl One")]["played"] is True
