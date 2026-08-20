"""Characterization tests for recon_gaps() + the RECON_L1/L2 field sets (wc_fps_to_csv.py:843-867)."""


def _p(**kw):
    base = {"r": 0, "w": 0, "4s": 0, "6s": 0, "dots": 0, "maidens": 0,
            "runs_conceded": 0, "catches": 0, "stumpings": 0, "runouts": 0}
    base.update(kw)
    return base


def test_clean_when_equal(wcmod):
    a = _p(r=50, w=2)
    assert wcmod.recon_gaps(a, dict(a), wcmod.RECON_L1) == ""


def test_single_field_gap_L1(wcmod):
    assert wcmod.recon_gaps(_p(r=1), _p(r=2), wcmod.RECON_L1) == "runs 1/2"


def test_multi_field_gap_preserves_field_order(wcmod):
    # RECON_L1 order is r, b, 4s, 6s, w, ... -> runs still before wkts.
    got = wcmod.recon_gaps(_p(r=1, w=1), _p(r=2, w=2), wcmod.RECON_L1)
    assert got == "runs 1/2; wkts 1/2"


def test_missing_side_returns_empty(wcmod):
    assert wcmod.recon_gaps({}, _p(r=2), wcmod.RECON_L1) == ""
    assert wcmod.recon_gaps(_p(r=2), {}, wcmod.RECON_L1) == ""


def test_L1_and_L2_both_catch_dots(wcmod):
    """INVARIANT CHANGED with the cricapi removal, and this is the point of the change.

    While cricapi was the second witness, L1 could only ever compare the four fields it carried
    (r/w/4s/6s), so a dots disagreement had NO second opinion until cricsheet posted — the exact
    blindness that let LPL bowlers publish COMPLETED scored on dots=0. Cricbuzz carries the whole
    card, so `dots` is now an L1 field and a disagreement is caught in the provisional window.
    The L2 half is unchanged: cricsheet has always compared dots.
    """
    a, b = _p(dots=9), _p(dots=5)
    assert "dots" in wcmod.RECON_L1, "Cricbuzz witnesses dots — L1 must compare them"
    assert wcmod.recon_gaps(a, b, wcmod.RECON_L1) == "dots 9/5"
    assert wcmod.recon_gaps(a, b, wcmod.RECON_L2, sep="→") == "dots 9→5"


def test_L2_arrow_and_label_mapping(wcmod):
    # runs_conceded -> "conc", arrow separator for L2 (was -> corrected).
    got = wcmod.recon_gaps(_p(runs_conceded=26), _p(runs_conceded=32), wcmod.RECON_L2, sep="→")
    assert got == "conc 26→32"
