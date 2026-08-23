import json
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


# ── the scoreboard cache must never freeze a fixture that has not finished ───────────────────
# espn_match_list treats any day older than yesterday as immutable and serves it from cache. That
# holds only once every fixture on the day is FINAL. espn_get's write is unconditional (`cache`
# gates the READ), so the forward scan for upcoming fixtures used to persist future days with
# their events in state "pre"; two days later the same day was read back from cache, and
# _espn_event_to_match derived matchStarted = state in ("in","post") = False. The match had been
# played and the bot could never see it finish.
# MEASURED 2026-08-23: CPL 20 Aug and 21 Aug were both `post` on ESPN and both absent from the
# sheet — 29 fixtures listed, only 12 called completed.

def _sb(*states):
    return {"events": [{"id": str(i), "status": {"type": {"state": s}}}
                       for i, s in enumerate(states)]}


def test_unsettled_returns_only_the_unfinished_states(wcmod):
    assert wcmod._scoreboard_unsettled(_sb("post", "post")) == []
    assert wcmod._scoreboard_unsettled(_sb("post", "pre")) == ["pre"]
    assert wcmod._scoreboard_unsettled(_sb("in")) == ["in"]
    assert wcmod._scoreboard_unsettled({"events": []}) == []      # empty is a separate guard


def _run(wcmod, monkeypatch, tmp_path, payload, cache=True):
    """Call espn_get for a scoreboard day against a private cache dir."""
    monkeypatch.setattr(wcmod, "CACHE", str(tmp_path))
    calls = []

    class _R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): calls.append(1); return json.dumps(payload).encode()

    monkeypatch.setattr(wcmod.urllib.request, "urlopen", lambda *a, **k: _R())
    monkeypatch.setattr(wcmod.time, "sleep", lambda *a: None)
    got = wcmod.espn_get("scoreboard", cache=cache, dates="20260820")
    return got, calls, list(tmp_path.iterdir())


def test_a_day_with_an_unfinished_fixture_is_not_cached(wcmod, monkeypatch, tmp_path):
    _, _, files = _run(wcmod, monkeypatch, tmp_path, _sb("pre"))
    assert files == [], "a fixture that has not finished must never be frozen into the cache"


def test_a_finished_day_is_cached(wcmod, monkeypatch, tmp_path):
    _, _, files = _run(wcmod, monkeypatch, tmp_path, _sb("post", "post"))
    assert len(files) == 1, "an all-final day is genuinely immutable and should be cached"


def test_an_already_poisoned_pre_entry_is_refetched_not_trusted(wcmod, monkeypatch, tmp_path):
    # Simulate a cache written by an older build: the day is on disk holding a "pre" event.
    monkeypatch.setattr(wcmod, "CACHE", str(tmp_path))
    key = wcmod.re.sub(r"[^a-z0-9]", "_",
                       f"espn_{wcmod.ESPN_SERIES}_scoreboard_dates=20260820".lower())
    (tmp_path / (key + ".json")).write_text(json.dumps(_sb("pre")))
    got, calls, _ = _run(wcmod, monkeypatch, tmp_path, _sb("post"), cache=True)
    assert calls, "a cached day holding an unfinished fixture must be RE-FETCHED, not trusted"
    assert wcmod._scoreboard_unsettled(got) == [], "and the fresh copy is the finished one"
