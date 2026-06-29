"""Characterization tests for identity helpers: norm, _cricsheet_match, same_person_plausible
(wc_fps_to_csv.py:69-71, 175-198)."""


def test_norm_diacritics_case_punct(wcmod):
    assert wcmod.norm("Élise  Perry!") == "elise perry"


def test_norm_keeps_digits_and_spaces_hyphens(wcmod):
    # INTENTIONAL Py-vs-JS divergence: Python norm keeps digits and turns hyphens into
    # SPACES ("wyatt hodge"), whereas cricket-identity's normName strips hyphens by joining
    # ("wyatthodge"). A "make them identical" refactor must update this test deliberately.
    assert wcmod.norm("Wyatt-Hodge") == "wyatt hodge"
    assert wcmod.norm("MS Dhoni 7") == "ms dhoni 7"


def test_cricsheet_initials_match(wcmod):
    assert wcmod._cricsheet_match("Danni Wyatt", "DN Wyatt") is True
    assert wcmod._cricsheet_match("Smriti Mandhana", "SS Mandhana") is True


def test_cricsheet_no_match_on_different_surname(wcmod):
    assert wcmod._cricsheet_match("Danni Wyatt", "XY Smith") is False


def test_same_person_plausible_true_cases(wcmod):
    assert wcmod.same_person_plausible("Smriti Mandhana", "S Mandhana") is True
    assert wcmod.same_person_plausible("Sune Luus", "S Luus") is True


def test_same_person_plausible_rejects_surname_smear(wcmod):
    # The bug that once merged two different "...Singh" players -> must stay False.
    assert wcmod.same_person_plausible("Tajinder Singh", "Kunwarjeet Singh") is False
