"""`best_team` must decide with the SHARED model, and must honour its refusal.

This function picks WHICH PERSON on an ESPN roster a feed name is; the team falls out of that.
Its answer is not cosmetic — the SILENT-DROP AUTO-ADD (`wc_fps_to_csv.py`, the `best_team` call
in the injection loop) uses it to choose which side a player is scored on AND persists that to
`new_players.json` as a permanent `auto` member of that team. A wrong answer puts a whole innings
on the wrong team, permanently.

TWO FAULTS, both fixed here, both of the shape this repo keeps paying for — a guard that is
claimed but not installed, and a value that is transformed before the thing that needed it raw.

1. THE REFUSAL WAS NOT INSTALLED. The docstring said the shared model "returns None when two
   roster names match a strategy, which is exactly the refusal that keeps two same-surname players
   on one roster from swapping sides." It did not: `None` fell straight through to a private
   weighted scorer where `elif kl == ln: sc = 86.0` clears the 84 threshold on SURNAME ALONE, and
   `sc > best_sc` being strict meant the FIRST same-surname entry in ESPN's roster order won.
   The answer was therefore decided by dict iteration order — Dale or Glenn depending on which
   ESPN listed first.

2. THE MODEL WAS FED PRE-NORMALISED CANDIDATES. `cricket_identity.norm_name` DELETES `[^a-z ]`
   ("Wyatt-Hodge" -> "wyatthodge") and the port's docstring calls that "what makes strategy 3
   work"; the bot's `norm` REPLACES it with a space ("wyatt hodge"). `espn_team_map` keyed on
   `norm(nm)`, so the model was handed strings a different normaliser had already flattened,
   breaking its own "both sides are normalized here, so the two can never disagree" invariant.
   Measured: 11 of 755 registry names normalise differently, and for every one of them best_team
   never consulted the model at all — it always fell to the legacy scorer.
"""
import pytest


def _tm(wcmod, pairs):
    """A TeamMap as espn_team_map builds it: norm-keyed for callers, raw kept in .raw."""
    tm = wcmod.TeamMap()
    tm.raw = dict(pairs)
    tm.update({wcmod.norm(k): v for k, v in pairs})
    return tm


# ── 1. the refusal ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("order", [
    [("Dale Phillips", "Barbados Royals"), ("Glenn Phillips", "Trinbago Knight Riders")],
    [("Glenn Phillips", "Trinbago Knight Riders"), ("Dale Phillips", "Barbados Royals")],
])
def test_a_bare_surname_on_a_two_phillips_roster_refuses_in_EITHER_order(wcmod, order):
    """The load-bearing case. Both orderings must refuse; if the answer changes with roster
    order, ESPN's JSON ordering is deciding which team a man is scored on."""
    assert wcmod.best_team("Phillips", _tm(wcmod, order)) == ""


def test_the_refusal_is_not_merely_order_independent_it_is_a_REFUSAL(wcmod):
    """Guards against 'fixing' the order-dependence by picking a stable wrong answer."""
    both = [("Dale Phillips", "Barbados Royals"), ("Glenn Phillips", "Trinbago Knight Riders")]
    assert wcmod.best_team("Phillips", _tm(wcmod, both)) not in (
        "Barbados Royals", "Trinbago Knight Riders")


def test_two_hopes_refuse_too(wcmod):
    hp = _tm(wcmod, [("Shai Hope", "West Indies"), ("Kyle Hope", "Barbados")])
    assert wcmod.best_team("Hope", hp) == ""


def test_refusing_does_not_break_the_names_that_ARE_unambiguous(wcmod):
    """The refusal must cost nothing on names the model can actually decide — otherwise the fix
    trades a wrong answer for a missing one across the whole roster."""
    both = _tm(wcmod, [("Dale Phillips", "Barbados Royals"),
                       ("Glenn Phillips", "Trinbago Knight Riders")])
    assert wcmod.best_team("Glenn Phillips", both) == "Trinbago Knight Riders"
    assert wcmod.best_team("Glenn Dominic Phillips", both) == "Trinbago Knight Riders"
    assert wcmod.best_team("G Phillips", both) == "Trinbago Knight Riders"
    assert wcmod.best_team("D Phillips", both) == "Barbados Royals"
    hp = _tm(wcmod, [("Shai Hope", "West Indies"), ("Kyle Hope", "Barbados")])
    assert wcmod.best_team("Shai Hope", hp) == "West Indies"


def test_a_lone_surname_is_answered_not_refused(wcmod):
    """Strategy 5 (surname unique in the set) must still work — refusing here would strand every
    single-surname roster entry."""
    solo = _tm(wcmod, [("Kaveesha Dilhari", "Galle Gallants"), ("Inoka Ranaweera", "Jaffna Kings")])
    assert wcmod.best_team("WK Dilhari", solo) == "Galle Gallants"


# ── 2. the normaliser mismatch ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name,team", [
    ("Danni Wyatt-Hodge", "Southern Brave"),
    ("Nat Sciver-Brunt", "Trent Rockets"),
    ("Tom Kohler-Cadmore", "Manchester Super Giants"),
    ("Lauren Winfield-Hill", "Northern Superchargers"),
    ("Lhuan-dre Pretorius", "Oval Invincibles"),
])
def test_a_hyphenated_name_reaches_the_shared_model(wcmod, name, team):
    """The EXACT spelling used to return None from the model, because the candidate had already
    been flattened by the bot's `norm`. It then landed on the legacy scorer — right by luck on
    these, but the model was structurally unreachable for every hyphenated player."""
    tm = _tm(wcmod, [(name, team), ("Someone Else", "Other Team")])
    assert wcmod.best_team(name, tm) == team


def test_the_two_normalisers_really_do_disagree(wcmod):
    """Pins the ROOT CAUSE, so this can't regress silently if either normaliser is touched.
    If this ever passes-by-equality the fix above has become unnecessary — check before deleting."""
    from cricket_identity import norm_name
    assert norm_name("Wyatt-Hodge") == "wyatthodge"      # deletes
    assert wcmod.norm("Wyatt-Hodge") == "wyatt hodge"    # replaces with a space
    assert norm_name("Wyatt-Hodge") != wcmod.norm("Wyatt-Hodge")


def test_espn_team_map_carries_both_keyings(wcmod, monkeypatch):
    """Callers look up by norm(name); the model needs the raw spelling. Both must survive, or
    fixing one of them breaks the other."""
    monkeypatch.setattr(wcmod, "espn_get", lambda *a, **k: {"rosters": [
        {"team": {"displayName": "Southern Brave"},
         "roster": [{"athlete": {"fullName": "Danni Wyatt-Hodge", "id": "1"}}]}]})
    tm = wcmod.espn_team_map("ev-x")
    assert tm[wcmod.norm("Danni Wyatt-Hodge")] == "Southern Brave"   # the caller contract
    assert tm.raw["Danni Wyatt-Hodge"] == "Southern Brave"           # the model contract


def test_a_plain_dict_still_works(wcmod):
    """Backward compatibility: best_team must not require a TeamMap. A caller passing an ordinary
    dict (as every test and any older call site does) still gets an answer, just without the raw
    spellings — it must degrade, not raise."""
    assert wcmod.best_team("Kaveesha Dilhari",
                           {"kaveesha dilhari": "Galle Gallants"}) == "Galle Gallants"
