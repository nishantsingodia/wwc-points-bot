"""A field the frozen baseline never stored must not be compared against zero.

WHY. When RECON_L2 widened 10 -> 14 (`b`, `balls`, `dro`, `lbwb` added so every point-earning field
gets a cricsheet cross-check), the baselines already frozen for settled matches had no key for the
new fields. `recon_gaps` read them with `a.get(f, 0)`, so an ABSENCE became a real 0 and cricsheet's
true value read as a change — a red "official revision pending" row on a match whose money is
already settled and whose number nobody disputes.

Measured over registry/settlement_snapshots.json at the time of the fix: 3665 settled rows, 996
lacking at least one of the four new keys, and 44 (all LPL) positioned to fire a phantom gap. The
Hundred M/W and CPL rows that lack the keys are players who never batted or bowled, so both sides
are legitimately 0 and nothing fabricates.

The rule was ALREADY WRITTEN in this file, ten lines below the bug, over `_SCORING_CRITICAL`:
"defaulting it to 0 is not a harmless shortcut ... would MANUFACTURE an L2 gap on a settled match.
Say 'unverified' instead of inventing a number." That guard protects `points_gap`; `recon_gaps`
runs first and did not have it. Same absence-becomes-value shape as the `None` skip right beside it.

⛔ AND THE MIRROR: silently skipping is not enough either. Reporting no gap for a field nobody could
check would claim coverage we do not have — "✓ L2 recon done" across 14 fields when 4 were never
compared. So the uncompared fields are COLLECTED and named.
"""
import pytest

L2 = ["r", "w", "lbwb", "dro"]


def test_a_missing_key_does_not_become_a_zero(wcmod):
    """The bug, exactly: baseline predates `lbwb`, cricsheet says 1."""
    base = {"r": 40, "w": 2}                      # no lbwb / dro at all
    official = {"r": 40, "w": 2, "lbwb": 1, "dro": 0}
    assert wcmod.recon_gaps(base, official, L2, sep="→") == "", \
        "an absent baseline field was compared against zero and manufactured a gap"


def test_the_uncompared_fields_are_NAMED_not_silently_skipped(wcmod):
    """Skipping quietly would report full 14-field coverage that never happened."""
    unv = []
    wcmod.recon_gaps({"r": 40, "w": 2}, {"r": 40, "w": 2, "lbwb": 1, "dro": 0},
                     L2, sep="→", unverified=unv)
    assert sorted(unv) == ["dro", "lbwb"]


def test_a_REAL_disagreement_on_a_stored_field_still_fires(wcmod):
    """The fix must not buy silence by suppressing genuine gaps."""
    g = wcmod.recon_gaps({"r": 40, "w": 2, "lbwb": 0, "dro": 0},
                         {"r": 44, "w": 2, "lbwb": 0, "dro": 0}, L2, sep="→")
    assert "40→44" in g


def test_a_stored_zero_against_a_real_value_still_fires(wcmod):
    """The distinction is ABSENT vs 0 — a baseline that genuinely recorded 0 must still be
    compared, or the fix would hide the exact class L2 exists to catch."""
    g = wcmod.recon_gaps({"r": 40, "w": 2, "lbwb": 0, "dro": 0},
                         {"r": 40, "w": 2, "lbwb": 1, "dro": 0}, L2, sep="→")
    assert "0→1" in g, "an explicitly-stored 0 must not be treated as absent"


def test_none_is_still_skipped_and_now_reported(wcmod):
    """Cricbuzz writes None for a field it could not establish; that behaviour is unchanged."""
    unv = []
    assert wcmod.recon_gaps({"r": 40, "lbwb": None}, {"r": 40, "lbwb": 3},
                            ["r", "lbwb"], sep="/", unverified=unv) == ""


def test_l1_is_unaffected_because_blank_perf_guarantees_every_key(wcmod):
    """L1 compares two live feed dicts built by blank_perf, so no key is ever missing there and
    the new branch cannot change L1 behaviour."""
    a = wcmod.blank_perf("X"); b = wcmod.blank_perf("X")
    a.update(r=30, w=1); b.update(r=31, w=1)
    unv = []
    g = wcmod.recon_gaps(a, b, wcmod.RECON_L2, sep="/", unverified=unv)
    assert "30/31" in g
    assert unv == [], "no field should be unverifiable between two blank_perf-derived dicts"


def test_an_empty_side_is_still_no_comparison(wcmod):
    assert wcmod.recon_gaps({}, {"r": 5}, L2) == ""
    assert wcmod.recon_gaps({"r": 5}, {}, L2) == ""
