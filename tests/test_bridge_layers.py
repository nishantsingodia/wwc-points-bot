"""Layers A2 (split fingerprint), B's name gate, and C (name) — the 25 Aug 2026 additions.

Every case here is a REAL one from the 100-match pinned corpus, named so a failure says which
real-world shape broke rather than which abstraction did.
"""
import pytest
from registry import cricbuzz_bridge as B


def card(batting=None, bowling=None, names=None, dismissals=None):
    b, w = batting or {}, bowling or {}
    return {"batting": {**{k: None for k in w}, **b},
            "bowling": {**{k: None for k in b}, **w},
            "names": names or {}, "dismissals": dismissals or []}


# ── name_agrees: the acceptance filter ────────────────────────────────────────────────────
@pytest.mark.parametrize("a,b,expected", [
    ("Jeavor Royal", "Jeavor Royal", True),          # rung 1 — 9 of 10 real rescues
    ("Darron Nedd", "Darren Nedd", True),            # rung 2 — real CPL 14 Aug variant
    ("Danielle Gibson", "Dani Gibson", True),        # rung 2 — real Hundred-W variant
    ("Milan Priyanath Rathnayake", "Pavan Rathnayake", False),   # rung 5 — THE error
    ("Amir Jangoo", "Karima Gore", False),           # unrelated
    ("Will Jacks", "Vince", False),                  # a real fielder dispute
    ("", "", False),                                 # absence is not agreement
    ("Shadab Khan", "", False),
])
def test_name_agrees(a, b, expected):
    assert B.name_agrees(a, b) is expected


def test_rung5_is_excluded_even_when_the_shared_model_would_fire():
    """The shared model RETURNS Pavan here — that is rung 5 working as specified. The filter is
    what refuses it, without forking the model."""
    from cricket_identity import fuzzy_match_name
    cands = ["Milan Rathnayaka", "Pavan Rathnayake"]
    assert fuzzy_match_name("Milan Priyanath Rathnayake", cands) == "Pavan Rathnayake"
    assert B.name_agrees("Milan Priyanath Rathnayake", "Pavan Rathnayake") is False


# ── Layer A2: split fingerprint ───────────────────────────────────────────────────────────
def test_split_pairs_on_batting_when_the_feeds_disagree_about_bowling():
    """Dani Gibson, Hundred-W 2 Aug: batting identical, bowling not. The combined key loses her."""
    cb   = card(batting={"c1": (24, 18, 3, 0)}, bowling={"c1": (12, 20, 1)}, names={"c1": "X"})
    espn = card(batting={"e1": (24, 18, 3, 0)}, bowling={"e1": (12, 25, 1)}, names={"e1": "X"})
    assert B.layer_a(cb, espn) == {}                       # combined key: lost
    assert B.layer_a2_split(cb, espn, set(), set()) == {"c1": "e1"}


def test_split_ignores_zero_ball_lines():
    """A 0-ball line carries no information and would collide with every other 0-ball line."""
    cb   = card(batting={"c1": (0, 0, 0, 0)}, names={"c1": "X"})
    espn = card(batting={"e1": (0, 0, 0, 0)}, names={"e1": "X"})
    assert B.layer_a2_split(cb, espn, set(), set()) == {}


def test_split_refuses_when_batting_and_bowling_name_different_men():
    """A contradiction is not a choice — refuse both sides, never last-wins."""
    cb   = card(batting={"c1": (10, 8, 1, 0)}, bowling={"c1": (6, 9, 1)}, names={"c1": "X"})
    espn = card(batting={"e1": (10, 8, 1, 0)}, bowling={"e2": (6, 9, 1)},
                names={"e1": "X", "e2": "Y"})
    assert B.layer_a2_split(cb, espn, set(), set()) == {}


def test_split_skips_anyone_already_placed():
    cb   = card(batting={"c1": (10, 8, 1, 0)}, names={"c1": "X"})
    espn = card(batting={"e1": (10, 8, 1, 0)}, names={"e1": "X"})
    assert B.layer_a2_split(cb, espn, {"c1"}, set()) == {}
    assert B.layer_a2_split(cb, espn, set(), {"e1"}) == {}


# ── Layer C: name, residual only ──────────────────────────────────────────────────────────
def test_name_layer_pairs_identical_teammates_the_numbers_cannot_separate():
    """Jeavor Royal / Vitel Lawes, CPL 7 Aug: same team, both 2-0-19-0. Numbers cannot tell them
    apart; the two feeds spell both identically."""
    cb   = card(bowling={"c1": (12, 19, 0), "c2": (12, 19, 0)},
                names={"c1": "Jeavor Royal", "c2": "Vitel Lawes"})
    espn = card(bowling={"e1": (12, 19, 0), "e2": (12, 19, 0)},
                names={"e1": "Jeavor Royal", "e2": "Vitel Lawes"})
    assert B.layer_a(cb, espn) == {}
    assert B.layer_c_name(cb, espn, set(), set()) == {"c1": "e1", "c2": "e2"}


def test_name_layer_refuses_a_double_claim():
    """Two cricbuzz players resolving to one ESPN id refuses BOTH."""
    cb   = card(bowling={"c1": (12, 19, 0), "c2": (6, 9, 0)},
                names={"c1": "Sam Smith", "c2": "Sam Smith"})
    espn = card(bowling={"e1": (12, 19, 0)}, names={"e1": "Sam Smith"})
    assert B.layer_c_name(cb, espn, set(), set()) == {}


def test_name_layer_never_touches_a_player_performance_already_placed():
    """The ordering that makes the Rathnayake case unreachable."""
    cb   = card(bowling={"c1": (12, 19, 0)}, names={"c1": "Milan Priyanath Rathnayake"})
    espn = card(bowling={"e1": (12, 19, 0), "e2": (6, 9, 0)},
                names={"e1": "Milan Rathnayaka", "e2": "Pavan Rathnayake"})
    a = B.layer_a(cb, espn)
    assert a == {"c1": "e1"}                                     # performance gets him right
    assert B.layer_c_name(cb, espn, set(a), set(a.values())) == {}   # name never gets a say


def test_name_layer_abstains_rather_than_guess_a_different_surname():
    """Tajinder Dhillon / Tajinder Singh — genuinely ambiguous, belongs on Needs Cricinfo ID."""
    cb   = card(batting={"c1": (5, 6, 0, 0)}, names={"c1": "Tajinder Dhillon"})
    espn = card(batting={"e1": (9, 9, 1, 0)}, names={"e1": "Tajinder Singh"})
    assert B.layer_c_name(cb, espn, set(), set()) == {}


# ── The `create` bar: counted in PERFORMANCE matches, not raw tier ────────────────────────
def _store(*confs):
    """Build a real store from (method, match) pairs — via build_store, so the record shape is
    the one resolve() actually sees rather than a hand-rolled approximation."""
    return B.build_store([{"cricbuzz_id": "99", "cricinfo_id": "123", "match": m,
                           "method": meth, "date": ""} for meth, m in confs])


def test_name_only_bridge_may_cross_check():
    st = _store((B.METHOD_NAME, "m1"))
    r = B.resolve(st, "99", B.PURPOSE_CROSSCHECK)
    assert r.status == B.OK and r.cricinfo_id == "123"


def test_name_only_bridge_may_never_create_however_many_matches():
    """Tier counts MATCHES, the wrong unit for a name: one spelling seen five times is one fact
    copied five times, not five independent observations."""
    st = _store(*[(B.METHOD_NAME, "m%d" % i) for i in range(9)])
    assert st["bridge"]["99"]["tier"] == 9
    r = B.resolve(st, "99", B.PURPOSE_CREATE)
    assert r.status == B.NAME_ONLY and r.cricinfo_id is None


@pytest.mark.parametrize("method", [B.METHOD_FINGERPRINT, B.METHOD_SPLIT, B.METHOD_DISMISSAL])
def test_two_performance_matches_may_create(method):
    st = _store((method, "m1"), (method, "m2"))
    r = B.resolve(st, "99", B.PURPOSE_CREATE)
    assert r.status == B.OK and r.cricinfo_id == "123"


def test_a_name_match_cannot_top_up_a_single_sighting_to_the_create_bar():
    """THE REAL CASE — cb:12071 -> ci:974109 in the reference pair: `fingerprint` on
    cb157061/espn1537342 and `name` on cb157138/espn1537349. Raw tier is 2, so a tier-only gate
    would clear him to CREATE a Cricbuzz-only field off ONE actual observation."""
    st = _store((B.METHOD_FINGERPRINT, "m1"), (B.METHOD_NAME, "m2"))
    assert st["bridge"]["99"]["tier"] == 2                     # tier says 2...
    assert B.resolve(st, "99", B.PURPOSE_CROSSCHECK).status == B.OK      # ...cross-check fine
    r = B.resolve(st, "99", B.PURPOSE_CREATE)
    assert r.status == B.INSUFFICIENT_TIER                     # ...but create counts 1
    assert "1 performance-confirmed match" in r.detail


def test_a_manual_answer_is_refused_on_tier_not_relabelled_as_name_only():
    """A hand-typed id is the owner's judgement, not a name match. It was already correctly
    refused for create; the reason must not become 'bridged by NAME alone'."""
    st = _store((B.METHOD_MANUAL, B.MANUAL_MATCH))
    r = B.resolve(st, "99", B.PURPOSE_CREATE)
    assert r.status == B.INSUFFICIENT_TIER
    assert "NAME alone" not in r.detail
