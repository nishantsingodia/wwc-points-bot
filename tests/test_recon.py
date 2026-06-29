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
    # RECON_L1 order is r, w, 4s, 6s -> runs before wkts.
    got = wcmod.recon_gaps(_p(r=1, w=1), _p(r=2, w=2), wcmod.RECON_L1)
    assert got == "runs 1/2; wkts 1/2"


def test_missing_side_returns_empty(wcmod):
    assert wcmod.recon_gaps({}, _p(r=2), wcmod.RECON_L1) == ""
    assert wcmod.recon_gaps(_p(r=2), {}, wcmod.RECON_L1) == ""


def test_L1_ignores_dots_but_L2_catches_them(wcmod):
    a, b = _p(dots=9), _p(dots=5)
    assert wcmod.recon_gaps(a, b, wcmod.RECON_L1) == ""            # dots not in L1
    assert wcmod.recon_gaps(a, b, wcmod.RECON_L2, sep="→") == "dots 9→5"


def test_L2_arrow_and_label_mapping(wcmod):
    # runs_conceded -> "conc", arrow separator for L2 (was -> corrected).
    got = wcmod.recon_gaps(_p(runs_conceded=26), _p(runs_conceded=32), wcmod.RECON_L2, sep="→")
    assert got == "conc 26→32"
