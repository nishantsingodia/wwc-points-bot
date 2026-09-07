"""A tour whose NAME CONTAINS an already-ingested tour's name is a DIFFERENT tour.

Both regressions here shipped together on 7 Sep 2026 and between them lost WCPL 2026 completely:
the women's Caribbean Premier League contains the men's, so it was read as already-ingested and
never entered, while the status tab — cleared and rewritten every run — deleted the typed name as
fast as it could be typed. Neither logged anything; both runs went green.
"""
import importlib.util
import sys

import pytest


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    argv, sys.argv = sys.argv, [name]
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    finally:
        sys.argv = argv
    return mod


ts = _load("tour_sync_t", "tour_sync.py")
tst = _load("tour_status_t", "tour_status.py")

# (typed name, ingested name, denote the same tour?)
PAIRS = [
    # THE BUG: the women's league name contains the men's one in full.
    ("Women's Caribbean Premier League 2026", "Caribbean Premier League 2026", False),
    ("Women's Big Bash League 2026", "Big Bash League 2026", False),
    # The reverse direction must not collapse either.
    ("Caribbean Premier League 2026", "Women's Caribbean Premier League 2026", False),
    # NEXT SEASON is a different tour, even though the key is year-agnostic.
    ("Caribbean Premier League 2027", "Caribbean Premier League 2026", False),
    # ...but a name typed WITHOUT the year still matches the year stamped on at ingest.
    ("Namibia T20I Tri-Series", "Namibia T20I Tri-Series 2026", True),
    # The "(Men T20I)" / "(CPL)" parenthetical is noise, not identity.
    ("India tour of Zimbabwe 2026", "India tour of Zimbabwe 2026 (T20I)", True),
    ("Caribbean Premier League (CPL) 2026", "Caribbean Premier League 2026", True),
    # Genders of one competition are two tours with two squads and two ESPN ids.
    ("The Hundred Women's Competition 2026", "The Hundred Men's Competition 2026", False),
]


@pytest.mark.parametrize("typed,ingested,same", PAIRS)
def test_ingest_matches_tours_by_equality_not_containment(typed, ingested, same):
    assert ts.same_tour(typed, ingested) is same


@pytest.mark.parametrize("typed,ingested,same", PAIRS)
def test_the_status_tab_agrees_with_the_ingest(typed, ingested, same):
    """The two rules must never disagree: one decides whether a typed name is ingested, the other
    whether it SURVIVES on the tab. If they diverge, a name is erased before it can be built."""
    assert tst._same_tour(typed, ingested) is same


def test_same_tour_is_symmetric():
    """Identity is a relation, not a direction. The old rule was directional — `en in key` fired
    while `key in en` did not — which is precisely how "more specific name" became "duplicate"."""
    for typed, ingested, _ in PAIRS:
        assert ts.same_tour(typed, ingested) == ts.same_tour(ingested, typed)


# ── the format ESPN states for a women's league ──────────────────────────────
@pytest.mark.parametrize("card,want", [
    ("Women's T20", "T20"),      # ESPN's actual spelling — the apostrophe is the whole bug
    ("Women’s T20", "T20"),  # curly apostrophe
    ("Womens T20", "T20"),
    ("Women's ODI", "ODI"),
    ("Twenty20", "T20"),
])
def test_espn_womens_class_card_is_read(card, want):
    assert ts._fmt_stated({"espn_class_card": card}) == want


def test_a_womens_domestic_t20_league_class_is_measured():
    """WCPL 20898 reports classId 17. An unmeasured id is left unresolved, and for a league whose
    fixtures carry no class and whose name has no format token that means every match is DROPPED."""
    assert ts.ESPN_LEAGUE_CLASS["17"] == "T20"
