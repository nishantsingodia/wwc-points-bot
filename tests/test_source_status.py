"""The `Source` column must describe THE SCENARIO THAT ACTUALLY HAPPENED.

It used to be assembled by three `status +=` sites straddling the Cricbuzz fetch, and the first of
them asserted "dots unverified, awaiting cricsheet" before the witness that verifies dots had run.
Nothing downstream could contradict it, so a real CPL cell (Match 15, MTANT v MTGUY, 24 Aug 2026)
read "dots unverified, awaiting cricsheet · cricbuzz cross-checked (19 players)" — two halves that
are never both true. Every bowler in that match carried `L1 Recon: ✓ clean (cricbuzz/ESPN)`.
"""
import pytest
from wc_fps_to_csv import source_status

BOTH = {"bridged": 19, "unbridged": 3, "dots_source": "commentary", "maidens_source": "card"}


def s(**kw):
    a = dict(cs_path=None, super_over=False, wit_pid={"ci:1": {}}, cb_diag=BOTH,
             cb_note="", cb_series="12123")
    a.update(kw)
    return source_status(**a)


def test_cricsheet_is_official_and_claims_nothing_pending():
    out = s(cs_path="x.json")
    assert out == "cricsheet · official"
    assert "provisional" not in out and "cricbuzz" not in out


def test_a_witnessed_match_does_not_call_its_dots_unverified():
    """The exact CPL M15 regression: both clauses in one cell."""
    out = s()
    assert "unverified" not in out
    assert "cricbuzz cross-checked (19 players)" in out
    assert "dots/maidens cross-checked" in out


def test_the_unbridged_residual_is_named_not_hidden():
    assert "19 players), 3 unbridged" in s()
    assert "unbridged" not in s(cb_diag={**BOTH, "unbridged": 0})


@pytest.mark.parametrize("absent,expected", [
    ("dots_source", "dots unverified"),            # cricbuzz commentary gate failed
    ("maidens_source", "maidens unverified"),      # The Hundred: cricbuzz copies dots into maidens
])
def test_only_the_field_that_actually_lacks_a_witness_is_named(absent, expected):
    out = s(cb_diag={**BOTH, absent: None})
    assert expected in out
    # ...and the field that DID get witnessed is not smeared with it.
    other = "maidens" if absent == "dots_source" else "dots"
    assert f"{other} unverified" not in out


def test_no_cricbuzz_card_says_unavailable_never_cross_checked_zero():
    """`witness == "cricbuzz"` was a tautology, so this arm was unreachable and a match with no
    card advertised "cricbuzz cross-checked (0 players)" — a cross-check that never happened
    reading exactly like one that passed."""
    out = s(wit_pid={}, cb_diag={"bridged": 0}, cb_note="no bridged player")
    assert "⚠ cricbuzz unavailable (no bridged player)" in out
    assert "cross-checked" not in out
    assert "dots/maidens unverified" in out


def test_a_tour_with_no_cricbuzz_configured_stays_silent_about_it():
    out = s(wit_pid={}, cb_diag={}, cb_series="")
    assert "cricbuzz" not in out
    assert "dots/maidens unverified" in out


def test_super_over_exclusion_survives_the_rebuild():
    assert "super-over excl" in s(super_over=True)


def test_absence_is_never_read_as_a_witness():
    """A missing key and an explicit None must both mean "no witness" — the repo's costliest
    recurring bug is an absence presenting as a value."""
    assert "dots/maidens unverified" in s(cb_diag={"bridged": 5})
