"""The shared name-matching contract, ported verbatim from cricket-identity/src/index.test.ts.

These are the OWNER'S fixtures, not ours. They exist so the Python port and the TypeScript package
cannot drift: if a case here disagrees with the JS, one of the two is wrong and the JS is the
authority. Do not edit an expectation to make a change pass — change the algorithm in the
cricket-identity repo, bump it, reinstall in wwc-draft + cricket-auction-helper, and re-port.

The bot previously had eight independent SequenceMatcher call sites with hand-tuned weights and no
ambiguity floor. That is what gave Glenn Phillips's 99-point innings to Dale Phillips.
"""
import pytest

from cricket_identity import fuzzy_match_name, norm_name


# ── normName ────────────────────────────────────────────────────────────────────────────────
def test_normname_strips_diacritics_case_punctuation_hyphens():
    assert norm_name("Élise Perry") == "elise perry"
    assert norm_name("Wyatt-Hodge") == "wyatthodge"
    assert norm_name("  S.  Mandhana  ") == "s mandhana"


# ── the five strategies ─────────────────────────────────────────────────────────────────────
def test_strategy_1_exact_normalized_match():
    assert fuzzy_match_name("Sophie Ecclestone",
                            ["Sophie Ecclestone", "Nat Sciver"]) == "Sophie Ecclestone"


def test_strategy_2_surname_plus_first_initial():
    assert fuzzy_match_name("S Mandhana",
                            ["Smriti Mandhana", "Shafali Verma"]) == "Smriti Mandhana"
    assert fuzzy_match_name("A Canning", ["Ava Canning", "Ash Gardner"]) == "Ava Canning"


def test_strategy_3_surname_prefix_married_or_hyphenated():
    assert fuzzy_match_name("N Sciver",
                            ["Nat Sciver-Brunt", "Sophie Ecclestone"]) == "Nat Sciver-Brunt"


def test_strategy_4_full_name_prefix_either_direction():
    assert fuzzy_match_name("Renuka Singh",
                            ["Renuka Singh Thakur", "Deepti Sharma"]) == "Renuka Singh Thakur"
    assert fuzzy_match_name("Chamari",
                            ["Chamari Athapaththu", "Harmanpreet Kaur"]) == "Chamari Athapaththu"


def test_strategy_5_surname_unique_in_candidate_set():
    assert fuzzy_match_name("WK Dilhari",
                            ["Kaveesha Dilhari", "Inoka Ranaweera"]) == "Kaveesha Dilhari"


# ── the refusal, which is the load-bearing part ─────────────────────────────────────────────
def test_ambiguity_returns_none_never_a_guess():
    assert fuzzy_match_name("Patel", ["Sunny Patel", "Smit Patel"]) is None
    assert fuzzy_match_name("X Nobody", ["Alpha Beta", "Gamma Delta"]) is None


def test_empty_candidate_set_returns_none():
    assert fuzzy_match_name("Anyone", []) is None


# ── the case this project actually paid for ─────────────────────────────────────────────────
def test_the_model_separates_dale_from_glenn_in_every_form():
    """THE MODEL WAS NEVER THE PROBLEM. Given both Phillipses as candidates it is right every time,
    in every spelling the feeds actually produce — long form, announced form, initial, initials.
    Verified identical in the TypeScript package.

    The bot's misattribution (Glenn's 99-point innings published under Dale, Glenn as Played=N)
    came from the bot NOT USING this model: it scored name_similarity*60 + surname_similarity*40
    in closest_squad and returned a best guess with no ambiguity floor, against a squad Glenn had
    already dropped out of because he had no cricinfo id. Two faults, neither of them here — a
    private scorer, and an incomplete candidate set.
    """
    both = ["Dale Phillips", "Glenn Phillips"]
    assert fuzzy_match_name("Glenn Dominic Phillips", both) == "Glenn Phillips"
    assert fuzzy_match_name("Glenn Phillips", both) == "Glenn Phillips"
    assert fuzzy_match_name("G Phillips", both) == "Glenn Phillips"
    assert fuzzy_match_name("GD Phillips", both) == "Glenn Phillips"
    assert fuzzy_match_name("D Phillips", both) == "Dale Phillips"


def test_a_candidate_set_missing_the_right_player_is_the_callers_bug():
    """Asked to match a Phillips against a list containing only Dale, the model returns Dale — as
    it should; that is strategy 5 doing its job ("WK Dilhari" -> the only Dilhari). It is not a
    failure to tell the two apart, it is a caller handing over a list the right man is absent from.

    So the protection lives upstream, and now does: an id-bearing row can no longer reach the
    matcher at all, because an athlete id is better evidence than any name.
    """
    assert fuzzy_match_name("Glenn Dominic Phillips", ["Dale Phillips"]) == "Dale Phillips"


def test_two_hopes_are_not_interchangeable():
    assert fuzzy_match_name("Shai Hope", ["Shai Hope", "Kyle Hope"]) == "Shai Hope"
    assert fuzzy_match_name("Hope", ["Shai Hope", "Kyle Hope"]) is None
