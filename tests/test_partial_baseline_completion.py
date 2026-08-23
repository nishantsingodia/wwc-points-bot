"""complete_baseline: a frozen baseline missing a scoring-critical key is worse than none.

`fields` is stamped with whatever SETTLED_FIELDS held AT FREEZE TIME. A row frozen before a field
joined that list lacks it — and settled_baseline hands that partial dict to L2 in preference to the
recompute, so score() raises KeyError and the points backstop reports "pts ?→? (baseline predates
…)". The TOTAL, which is what a contest settles on, goes unverified. Measured on live data: 1086
such rows across 4 tours, including all 88 of LPL's field-frozen rows.

complete_baseline fills ONLY the missing keys, and only when the completed dict re-scores to the
total the row was settled on. Replacing the whole dict is forbidden — that is the move that
manufactured phantom `dots 0→N` revisions and wrote them over cricsheet's correct figures.
"""
import pytest
import wc_fps_to_csv as wc


CRIT = wc._SCORING_CRITICAL


def _full(**over):
    p = {k: 0 for k in CRIT}
    p["played"] = 1
    p["name"] = "A Player"
    p.update(over)
    return p


def _settle(monkeypatch, mk, pid, points):
    monkeypatch.setattr(wc, "SETTLEMENTS", {(mk, pid): {"points": points}})


def test_a_complete_baseline_is_returned_untouched(monkeypatch):
    frozen = _full(r=30, b=20, dismissed=1)
    _settle(monkeypatch, "m", "p", 0)
    base, filled, why = wc.complete_baseline("m", "p", frozen, _full(r=999), "BAT")
    assert base is frozen and filled == () and why == ""


def test_a_hole_is_filled_and_the_total_becomes_scoreable(monkeypatch):
    full = _full(r=30, b=20, w=1, lbwb=1)
    want = wc.score(full, "BOWL")["total"]
    frozen = {k: v for k, v in full.items() if k != "lbwb"}
    _settle(monkeypatch, "m", "p", want)
    base, filled, why = wc.complete_baseline("m", "p", frozen, full, "BOWL")
    assert filled == ("lbwb",) and why == ""
    assert base["lbwb"] == 1
    assert wc.score(base, "BOWL")["total"] == want


def test_every_value_the_frozen_record_captured_still_wins(monkeypatch):
    # The whole safety property: a recompute may not override a published number, only fill a hole.
    full = _full(r=30, b=20, lbwb=0)
    frozen = {k: v for k, v in full.items() if k != "lbwb"}
    _settle(monkeypatch, "m", "p", wc.score(full, "BAT")["total"])
    recompute = _full(r=77, b=99, lbwb=0)      # disagrees on r and b
    base, filled, why = wc.complete_baseline("m", "p", frozen, recompute, "BAT")
    assert filled == ("lbwb",)
    assert base["r"] == 30 and base["b"] == 20


def test_a_completion_that_does_not_reproduce_the_settled_total_is_refused(monkeypatch):
    full = _full(r=30, b=20, w=1, lbwb=1)
    frozen = {k: v for k, v in full.items() if k != "lbwb"}
    _settle(monkeypatch, "m", "p", 1)          # settled on something else entirely
    base, filled, why = wc.complete_baseline("m", "p", frozen, full, "BOWL")
    assert filled == () and base is frozen
    assert "SETTLED on 1" in why


def test_a_row_with_no_settled_total_is_refused(monkeypatch):
    full = _full(r=30, b=20)
    frozen = {k: v for k, v in full.items() if k != "lbwb"}
    monkeypatch.setattr(wc, "SETTLEMENTS", {})
    base, filled, why = wc.complete_baseline("m", "p", frozen, full, "BAT")
    assert filled == () and base is frozen and why


def test_a_recompute_that_cannot_answer_the_hole_is_refused(monkeypatch):
    frozen = {k: v for k, v in _full(r=30).items() if k != "lbwb"}
    _settle(monkeypatch, "m", "p", 0)
    partial_recompute = {k: v for k, v in _full().items() if k != "lbwb"}
    base, filled, why = wc.complete_baseline("m", "p", frozen, partial_recompute, "BAT")
    assert filled == () and base is frozen
    assert "lbwb" in why


def test_no_recompute_at_all_is_refused(monkeypatch):
    frozen = {k: v for k, v in _full(r=30).items() if k != "lbwb"}
    _settle(monkeypatch, "m", "p", 0)
    base, filled, why = wc.complete_baseline("m", "p", frozen, None, "BAT")
    assert filled == () and base is frozen and why


def test_an_absent_baseline_is_passed_straight_through(monkeypatch):
    monkeypatch.setattr(wc, "SETTLEMENTS", {})
    for frozen in (None, {}):
        assert wc.complete_baseline("m", "p", frozen, _full(), "BAT") == (frozen, (), "")


def test_completion_never_writes_to_the_settlement_store(monkeypatch):
    # settlement_snapshots.json is WRITE-ONCE. Completing a baseline is a READ-side repair: it
    # makes the total checkable without touching the record of what money was settled on.
    full = _full(r=30, b=20, lbwb=0)
    frozen = {k: v for k, v in full.items() if k != "lbwb"}
    rec = {"points": wc.score(full, "BAT")["total"], "fields": frozen}
    monkeypatch.setattr(wc, "SETTLEMENTS", {("m", "p"): rec})
    base, filled, _ = wc.complete_baseline("m", "p", rec["fields"], full, "BAT")
    assert filled == ("lbwb",)
    assert "lbwb" not in rec["fields"], "the frozen record must not be mutated in place"
    assert base is not rec["fields"]


def test_dismissed_is_derived_from_the_frozen_dismissal_text_not_a_recompute(monkeypatch):
    # `dismissal` is written "" exactly when the batter was not out, so `dismissed` is a lossless
    # function of a key the frozen record already holds. No second source is consulted, so there
    # is nothing to disagree with. 48 of LPL's 88 partial rows are exactly this shape.
    full = _full(r=0, b=4, dismissed=True)
    frozen = {k: v for k, v in full.items() if k != "dismissed"}
    frozen["dismissal"] = "c Mendis b Hasaranga"
    _settle(monkeypatch, "m", "p", wc.score(full, "BAT")["total"])
    base, filled, why = wc.complete_baseline("m", "p", frozen, None, "BAT")
    assert filled == ("dismissed",) and why == ""
    assert base["dismissed"] is True


def test_an_empty_dismissal_text_means_not_out(monkeypatch):
    full = _full(r=0, b=4, dismissed=False)
    frozen = {k: v for k, v in full.items() if k != "dismissed"}
    frozen["dismissal"] = ""
    _settle(monkeypatch, "m", "p", wc.score(full, "BAT")["total"])
    base, filled, why = wc.complete_baseline("m", "p", frozen, None, "BAT")
    # The duck penalty turns on this bit, so getting it backwards is an 8-point error on a 0-run
    # batter — which is why it is derived and then gated on the settled total, not guessed.
    assert filled == ("dismissed",) and base["dismissed"] is False


def test_the_real_lpl_partial_rows_split_into_the_two_expected_shapes():
    # Regression on live data: 48 rows need only lbwb/dro/dismissed, and 40 are DNPs
    # (played: False, 0 points) which the L2 loop never reaches at all.
    import json, collections, os
    path = os.path.join(os.path.dirname(wc.__file__), "registry", "settlement_snapshots.json")
    rows = [r for r in json.load(open(path))["settlements"]
            if "Lanka" in (r.get("tour") or "") and r.get("fields")]
    shapes = collections.Counter(tuple(k for k in CRIT if k not in r["fields"]) for r in rows)
    assert shapes[("lbwb", "dro", "dismissed")] == 48
    dnp = [s for s in shapes if len(s) == 15]
    assert len(dnp) == 1 and shapes[dnp[0]] == 40
    for r in rows:
        if tuple(k for k in CRIT if k not in r["fields"]) == dnp[0]:
            assert r["fields"].get("played") is False and r["points"] == 0
