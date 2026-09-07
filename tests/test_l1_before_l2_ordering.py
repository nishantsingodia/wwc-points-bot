"""The stages run in ORDER: L1 closes, base points freeze, THEN cricsheet is measured against them.

Restored 8 Sep 2026, reverting the 16 Aug relaxation that let cricsheet's arrival cancel an open
L1 outright. That shortcut is what let ETPL Match 11 (2 Sep) freeze its write-once baseline on a
TRUNCATED ESPN card — Maxwell recorded 25 off 13 where he made 35 off 23 — and then bill the owner
for the difference at L2, as an "official revision" from a number nobody had ever been shown.

The relaxation existed for two real reasons, and this file pins the answer to each:
  • an L1 answer given after cricsheet lands would overwrite the official card
      -> apply_recon_overrides runs on a COPY once cs_path is set (test_an_l1_answer_...)
  • L1_OPEN would be announced with no row to click, forever
      -> l1_auto_resolved closes every gap the official card has already settled, and
         build_recon_rows is called whether or not cricsheet is in.
"""
import csv
import json

import pytest

from test_unattributed import SQUADS, _p, _roster_perf                      # noqa: F401
from test_freeze_at_l1_done import _wire as _freeze_wire, _clean            # noqa: F401


def _with_feeds(wcmod, monkeypatch, tmp_path, cb_runs, cs_runs):
    """Alpha One only: ESPN says 20 (the roster default), Cricbuzz says `cb_runs`, the official
    card says `cs_runs`. Everyone else agrees across all three."""
    espn, team_map = _clean(wcmod)
    tour, out = _freeze_wire(wcmod, monkeypatch, tmp_path, espn, team_map)

    cb = {pid: dict(v) for pid, v in
          {"ci:910001": dict(espn[wcmod.norm("Alpha One")]),
           "ci:910002": dict(espn[wcmod.norm("Alpha Two")]),
           "ci:910003": dict(espn[wcmod.norm("Beta One")]),
           "ci:910004": dict(espn[wcmod.norm("Beta Two")])}.items()}
    cb["ci:910001"]["r"] = cb_runs
    monkeypatch.setattr(wcmod, "cb_witness_active", lambda: True)
    monkeypatch.setattr(wcmod, "cb_match_perf", lambda *a, **k: (cb, "", {}))

    cs = {k: dict(v) for k, v in espn.items()}
    cs[wcmod.norm("Alpha One")]["r"] = cs_runs
    key = (("2026-08-01"), wcmod.team_key(["Alpha Kings", "Beta Giants"]))
    monkeypatch.setattr(wcmod, "load_cricsheet_index", lambda *a, **k: {key: "official.json"})
    monkeypatch.setattr(wcmod, "parse_cricsheet", lambda path: (cs, {}))
    return tour, out


def test_the_official_card_breaks_the_tie_and_nothing_is_asked(wcmod, monkeypatch, tmp_path):
    """ESPN 20 / Cricbuzz 35 / cricsheet 35 — the ETPL Match 11 shape. Two of three agree, so L1
    closes itself, the match freezes ON THE MAJORITY VALUE, and no row is raised at either level.

    The freeze is the assertion that matters: before this, the same match froze the LONE ESPN
    number as its write-once baseline and could never be corrected."""
    wcmod.RECON_REVIEW[:] = []
    tour, out = _with_feeds(wcmod, monkeypatch, tmp_path, cb_runs=35, cs_runs=35)
    wcmod.run_tour(tour)

    assert wcmod.RECON_REVIEW == [], "a question two cards already agree on reached the tab"
    states = {r["Recon State"] for r in csv.DictReader(open(out))}
    assert states == {wcmod.RECON_STATE_LABEL["L2_DONE"]}
    frozen = [v for v in wcmod.SETTLEMENTS.values() if v["pid"] == "ci:910001"]
    assert len(frozen) == 1
    assert frozen[0]["fields"]["r"] == 35, "froze the lone dissenting card, not the majority"


def test_all_three_differ_holds_L1_open_and_keeps_L2_SHUT(wcmod, monkeypatch, tmp_path):
    """ESPN 20 / Cricbuzz 35 / cricsheet 99 — a genuine three-way split. This is the case the
    ordering exists for: L1 stays open, so NOTHING freezes and the L2 question is not asked yet.
    Asking both at once is how the same field got adjudicated twice, the second time against a
    baseline that only existed because the first was never answered."""
    wcmod.RECON_REVIEW[:] = []
    tour, out = _with_feeds(wcmod, monkeypatch, tmp_path, cb_runs=35, cs_runs=99)
    wcmod.run_tour(tour)

    params = [r["param"] for r in wcmod.RECON_REVIEW]
    assert "L2" not in params, "L2 opened while L1 was still unanswered"
    assert "runs" in params, "L1 gap left with no row to click — the 16 Aug failure"
    states = {r["Recon State"] for r in csv.DictReader(open(out))}
    assert states == {wcmod.RECON_STATE_LABEL["L1_OPEN"]}
    assert not any(v["pid"] == "ci:910001" for v in wcmod.SETTLEMENTS.values()), \
        "froze a baseline while its value was still being argued about"


def test_the_surviving_L1_row_shows_all_three_numbers(wcmod, monkeypatch, tmp_path):
    """An L1 row can now outlive cricsheet's arrival, so it must carry the third column — hiding
    it would be the old two-slot problem back in a new shape."""
    wcmod.RECON_REVIEW[:] = []
    tour, out = _with_feeds(wcmod, monkeypatch, tmp_path, cb_runs=35, cs_runs=99)
    wcmod.run_tour(tour)
    row = next(r for r in wcmod.RECON_REVIEW if r["param"] == "runs")
    assert (row["espn"], row["cricbuzz"], row["cricsheet"]) == ("runs 20", "runs 35", "runs 99")
    assert "all three differ" in row["verdict"]


def test_an_l1_answer_never_overwrites_the_official_card(wcmod, perf):
    """THE OBJECTION THAT FORCED THE RELAXATION, answered directly. Once cricsheet is the scorer,
    apply_recon_overrides is handed a COPY: it still reports which pids are resolved — which is
    what closes the row and releases the freeze — but the official figures are untouched."""
    official = {"ci:1": perf(played=True, r=99, b=50)}
    wit = {"ci:1": perf(played=True, r=35, b=23)}
    espn = {"ci:1": perf(played=True, r=25, b=13)}
    idx = {"mk": [{"match_key": "mk", "scope": "player", "pid": "ci:1", "field": "r",
                   "source": "S1", "status": "approved"}]}

    copy = {k: dict(v) for k, v in official.items()}
    applied = wcmod.apply_recon_overrides(copy, wit, espn, {"ci:1": "g"}, "mk", idx)

    assert applied == {"ci:1"}, "the answer no longer closes the row — the match could never freeze"
    assert official["ci:1"]["r"] == 99, "the official card was overwritten by a provisional feed"
    assert copy["ci:1"]["r"] == 35, "the baseline did not take the approved L1 value"


# ── l1_auto_resolved, directly ───────────────────────────────────────────────────────────────

def _feeds(wcmod, cb, es, cs):
    return ({"p": wcmod.blank_perf("x") | {"r": cb}}, {"p": wcmod.blank_perf("x") | {"r": es}},
            {"p": wcmod.blank_perf("x") | {"r": cs}})


def test_a_tie_the_official_card_breaks_is_dropped(wcmod):
    cb, es, cs = _feeds(wcmod, 35, 20, 35)
    assert wcmod.l1_auto_resolved({"p": "g"}, cb, es, cs, ["r"]) == {"p": {"r": 35}}


def test_a_tie_it_cannot_break_survives(wcmod):
    cb, es, cs = _feeds(wcmod, 35, 20, 99)
    assert wcmod.l1_auto_resolved({"p": "g"}, cb, es, cs, ["r"]) == {}


def test_no_official_card_for_that_player_settles_nothing(wcmod):
    cb, es, _ = _feeds(wcmod, 35, 20, 0)
    assert wcmod.l1_auto_resolved({"p": "g"}, cb, es, {}, ["r"]) == {}


def test_partly_answered_is_not_answered(wcmod):
    """One field settled, another still three-way -> he keeps his row, carrying all three
    numbers. Dropping him would close a question nobody answered."""
    cb = {"p": wcmod.blank_perf("x") | {"r": 35, "w": 3}}
    es = {"p": wcmod.blank_perf("x") | {"r": 20, "w": 1}}
    cs = {"p": wcmod.blank_perf("x") | {"r": 35, "w": 9}}
    assert wcmod.l1_auto_resolved({"p": "g"}, cb, es, cs, ["r", "w"]) == {}
