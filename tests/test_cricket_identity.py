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
def test_glenn_vs_dale_and_the_limit_of_strategy_5():
    """CPL Match 6, and the sharpest thing these fixtures record.

    With BOTH Phillipses present the model is right: strategy 5 needs a UNIQUE surname, two
    candidates share it, so it falls through to the correct answer via the earlier strategies.

    With Glenn ABSENT it returns DALE — verified identical in the TypeScript package
    (fuzzyMatchName("Glenn Dominic Phillips", ["Dale Phillips"]) === "Dale Phillips"), so this is
    the model's real behaviour and this port is faithful, not broken.

    ⚠ THE CALLER OWNS THIS RISK. Strategy 5 asks "is this surname unique in the candidate set",
    which is only a safe question when the set CONTAINS the right person. Hand it an incomplete
    squad and the "unique" surname belongs to whoever is left. That is exactly how Glenn's 99-point
    innings reached Dale: Glenn was unmatchable (no cricinfo id), so the only Phillips left was
    Dale. The matcher did what it was designed to do.

    So the protection is upstream, never here: do not call this with an id-bearing row (the bot now
    refuses to), and do not call it with a candidate set you know is missing people.
    """
    assert fuzzy_match_name("Glenn Dominic Phillips",
                            ["Dale Phillips", "Glenn Phillips"]) == "Glenn Phillips"
    assert fuzzy_match_name("Glenn Dominic Phillips", ["Dale Phillips"]) == "Dale Phillips"


def test_two_hopes_are_not_interchangeable():
    assert fuzzy_match_name("Shai Hope", ["Shai Hope", "Kyle Hope"]) == "Shai Hope"
    assert fuzzy_match_name("Hope", ["Shai Hope", "Kyle Hope"]) is None
