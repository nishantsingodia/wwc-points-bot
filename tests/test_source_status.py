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


# ── The per-player half: a witnessed MATCH can still hold an unwitnessed BOWLER ──────────────
# Raised by the owner: "Shadab Khan is AR, he is bowling 4 overs." He is — and in CPL Matches 12
# and 14 he bowled his full 4 (13 and 17 dots) while UNBRIDGED, so no second feed ever saw those
# dots. dots_source is match-level; bridging is per player. 17 dots is 17 points on a settled row.
from wc_fps_to_csv import unwitnessed_bowlers


def test_a_bowler_the_witness_has_no_row_for_is_unwitnessed():
    base = {"ci:1": {"name": "Shadab Khan", "balls": 24},
            "ci:2": {"name": "Jayden Seales", "balls": 18}}
    assert unwitnessed_bowlers(base, {"ci:2": {}}) == ["Shadab Khan"]


def test_a_batter_the_witness_missed_is_not_a_dots_question():
    """Only bowlers carry dots. An unbridged batter (Shadab in M15 — he did not bowl, the chase
    ended in 9.5 overs) costs nothing and must not be named."""
    base = {"ci:1": {"name": "Shadab Khan", "balls": 0, "r": 6}}
    assert unwitnessed_bowlers(base, {}) == []


def test_absence_of_a_witness_row_is_not_agreement():
    base = {"ci:1": {"name": "A Bowler", "balls": 6}}
    assert unwitnessed_bowlers(base, None) == ["A Bowler"]
    assert unwitnessed_bowlers(None, {}) == []


def test_an_unwitnessed_bowler_is_named_even_when_the_match_dots_parsed():
    out = s(unwit_bowlers=["Shadab Khan"])
    assert "dots/maidens cross-checked" not in out      # the over-claim this fixes
    assert "1 unbridged bowler: Shadab Khan" in out
    assert "cricbuzz cross-checked (19 players)" in out  # the witness that DID run still reported


def test_several_unwitnessed_bowlers_are_counted_and_capped():
    out = s(unwit_bowlers=["A", "B", "C", "D"])
    assert "4 unbridged bowlers: A, B, C…" in out


def test_the_match_level_gap_still_wins_when_dots_never_parsed():
    """If Cricbuzz established no dots at all, say THAT — it is the broader fact."""
    out = s(cb_diag={**BOTH, "dots_source": None}, unwit_bowlers=["Shadab Khan"])
    assert "dots unverified, awaiting cricsheet" in out
    assert "unbridged bowler" not in out


def test_cpl_m15_is_genuinely_clean_because_nobody_unbridged_bowled():
    out = s(unwit_bowlers=[])
    assert "dots/maidens cross-checked · awaiting official cricsheet" in out
