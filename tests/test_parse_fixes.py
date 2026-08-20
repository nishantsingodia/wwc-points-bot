"""Tests for the feed-parser data-quality fixes:
- robust retirement/obstruction guard + 'leg before wicket' in parse_espn (the De Lange 2/3 bug).

The caught-&-bowled catch-credit tests that used to live here exercised the cricapi scorecard
parser (`parse_match`) and its `api()` transport, both DELETED when cricapi was dropped as a feed.
The INVARIANT is not gone with them — it moved to `_apply_dismissal_credit`, which reads the
batter's own ESPN scorecard line. ESPN is now the ONLY base card, so that path is re-tested below
rather than left as a claim: it had no coverage of its own while cricapi's parser carried these."""


def test_espn_retirement_not_a_wicket_and_lbw_bonus(wcmod, monkeypatch):
    commentary = {"commentary": {"items": [
        {"playType": {"description": "no run"},
         "bowler": {"athlete": {"fullName": "Caroline de Lange"}},
         "batsman": {"athlete": {"fullName": "Some Batter"}}, "scoreValue": 0,
         "dismissal": {"dismissal": True, "type": "retired not out (hurt)"}, "shortText": "retired hurt"},
        {"playType": {"description": "no run"},
         "bowler": {"athlete": {"fullName": "Caroline de Lange"}},
         "batsman": {"athlete": {"fullName": "Another Batter"}}, "scoreValue": 0,
         "dismissal": {"dismissal": True, "type": "leg before wicket"}, "shortText": "lbw"},
    ]}}
    monkeypatch.setattr(wcmod, "espn_get", lambda *a, **k: commentary)
    monkeypatch.setattr(wcmod, "espn_xi", lambda *a, **k: {})
    perf, _ = wcmod.parse_espn("evt")
    dl = perf[wcmod.norm("Caroline de Lange")]
    assert dl["w"] == 1       # only the lbw counts; the "retired not out (hurt)" does NOT
    assert dl["lbwb"] == 1    # "leg before wicket" now triggers the +8 bonus


# ── caught-&-bowled / catch credit on the ESPN path (_apply_dismissal_credit) ────────────────
# The rule: a "c" dismissal credits the BOWLER a wicket and the FIELDER a catch. On a caught-and-
# bowled those are the SAME player, who must get both. A plain catch must never give the bowler a
# catch. Fielders must NOT be marked played — 11 of the fielders credited across the cached corpus
# are substitutes, and `played` is the +4 in-XI bonus (ev1521240 "c sub (EG Barnard) b Ellis").

def _credit(wcmod, card_entry):
    """Run _apply_dismissal_credit over one scorecard line and return {norm_name: perf}."""
    perf = {}

    def get(n, espn_id=""):
        k = wcmod.norm(n)
        if k not in perf:
            perf[k] = wcmod.blank_perf(n, espn_id=espn_id)
        return perf[k]

    base = {"name": "The Batter", "espn_id": "900", "order": 3, "dismissed": True,
            "dismissal": "c", "card": "c", "fielders": [], "period": 1, "bowler": None}
    base.update(card_entry)
    wcmod._apply_dismissal_credit("ev-test", get, {wcmod.norm(base["name"]): base}, {})
    return perf


def test_caught_and_bowled_credits_the_bowler_both_wicket_and_catch(wcmod):
    perf = _credit(wcmod, {"card": "c", "bowler": ("Sam Bowler", "111"),
                           "fielders": [("Sam Bowler", "111")]})
    b = perf[wcmod.norm("Sam Bowler")]
    assert b["w"] == 1, "the bowler must get the wicket"
    assert b["catches"] == 1, "on a caught-and-bowled the bowler IS the catcher"


def test_plain_catch_does_not_credit_the_bowler_with_a_catch(wcmod):
    perf = _credit(wcmod, {"card": "c", "bowler": ("Sam Bowler", "111"),
                           "fielders": [("Fred Fielder", "222")]})
    b, f = perf[wcmod.norm("Sam Bowler")], perf[wcmod.norm("Fred Fielder")]
    assert (b["w"], b["catches"]) == (1, 0), "bowler gets the wicket, NOT the catch"
    assert f["catches"] == 1
    assert not f["played"], "a fielder may be a substitute — crediting +4 in-XI would be wrong"


def test_bowled_credits_the_lbw_bowled_bonus(wcmod):
    perf = _credit(wcmod, {"card": "bowled", "bowler": ("Sam Bowler", "111"), "fielders": []})
    b = perf[wcmod.norm("Sam Bowler")]
    assert (b["w"], b["lbwb"]) == (1, 1), "bowled/lbw carries the +8 bonus as well as the wicket"


def test_a_not_out_batter_credits_nobody(wcmod):
    perf = _credit(wcmod, {"card": "", "dismissed": False,
                           "bowler": ("Sam Bowler", "111"), "fielders": [("Fred Fielder", "222")]})
    assert all(v["w"] == 0 and v["catches"] == 0 for v in perf.values()), \
        "a not-out line must not manufacture a wicket or a catch"
