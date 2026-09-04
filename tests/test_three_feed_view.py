"""The Recon Review tab shows THREE NAMED FEEDS, and a majority is an answer.

Owner's model, stated 4 Sep 2026: "I don't treat cricsheet as source of truth, I see L1 & L2
combined — 3 sources as source of truth."

What the tab used to do, and why it produced the complaint that started this: ETPL Match 11
(Belfast Wolves v Edinburgh Castle Rockers, 2 Sep) shipped on a TRUNCATED ESPN card. Cricbuzz had
the full card and disagreed on ~30 field-comparisons. cricsheet later confirmed Cricbuzz on every
single one. The tab still asked the owner to adjudicate all of them — against a baseline nobody
had ever been shown — under two POSITIONAL headers ("S1", "S2") whose meaning changed depending
on whether the row was L1 or L2. A player of the draft read it correctly anyway and asked the
obvious question: "why is L2 saying 25 when L1 already said 35?"
"""


def test_a_majority_is_an_answer(wcmod):
    v, verdict, who = wcmod.feed_concurrence({"espn": 25, "cricbuzz": 35, "cricsheet": 35})
    assert v == 35 and set(who) == {"cricbuzz", "cricsheet"}
    assert "2 of 3: 35 (Cricbuzz+Cricsheet)" in verdict and "ESPN says 25" in verdict


def test_unanimity_says_so(wcmod):
    v, verdict, who = wcmod.feed_concurrence({"espn": 9, "cricbuzz": 9, "cricsheet": 9})
    assert v == 9 and len(who) == 3 and "all 3 agree" in verdict


def test_no_majority_is_left_for_a_human(wcmod):
    v, verdict, _ = wcmod.feed_concurrence({"espn": 1, "cricbuzz": 2, "cricsheet": 3})
    assert v is None and verdict == "all three differ — no majority"


def test_two_feeds_that_disagree_do_not_settle_anything(wcmod):
    """The whole point of a THIRD source. Two cards at odds is a question, not a majority —
    this is every tour with no cricbuzz_series (9 of 13), and the L1 stage of every other one."""
    v, verdict, _ = wcmod.feed_concurrence({"espn": 20, "cricbuzz": None, "cricsheet": 24})
    assert v is None
    # NAME the missing card. "all sources differ" would be a lie on the 9 of 13 tours that have
    # only ever had two, and it hides the actual reason the row is unanswerable by machine.
    assert verdict == "ESPN 20 vs Cricsheet 24 — no Cricbuzz to break the tie"
    assert wcmod.feed_concurrence({"espn": 25, "cricbuzz": 35, "cricsheet": None})[1] == \
        "ESPN 25 vs Cricbuzz 35 — no Cricsheet to break the tie"


def test_one_feed_alone_never_settles_anything(wcmod):
    v, verdict, _ = wcmod.feed_concurrence({"espn": 9, "cricbuzz": None, "cricsheet": None})
    assert v is None and "single source (ESPN only)" in verdict


def test_absence_is_not_a_value(wcmod):
    """A feed that did not measure a field must not be counted as agreeing with a 0 — the most
    expensive recurring bug in this file, and the tab is not where it gets to come back."""
    v, _, who = wcmod.feed_concurrence({"espn": 0, "cricbuzz": None, "cricsheet": 0})
    assert v == 0 and set(who) == {"espn", "cricsheet"}     # a real 0 still counts
    assert wcmod.feed_concurrence({"espn": None, "cricbuzz": None, "cricsheet": None})[0] is None


def test_the_columns_align_field_for_field(wcmod):
    """Santner, ETPL Match 11 — the row the complaint was actually about."""
    feeds = {"dots":           {"espn": 2,  "cricbuzz": 9,  "cricsheet": 9},
             "runs_conceded":  {"espn": 21, "cricbuzz": 29, "cricsheet": 29},
             "balls":          {"espn": 12, "cricbuzz": 24, "cricsheet": 24}}
    e, c, s, v = wcmod.three_feed_columns(feeds, ["dots", "runs_conceded", "balls"])
    assert e == "dots 2 · conc 21 · bowled 12"
    assert c == "dots 9 · conc 29 · bowled 24"
    assert s == "dots 9 · conc 29 · bowled 24"      # cricbuzz and cricsheet, identical
    assert v.count("2 of 3") == 3


def test_a_feed_with_no_number_renders_a_dot_not_a_zero(wcmod):
    e, c, s, _ = wcmod.three_feed_columns({"maidens": {"espn": 1, "cricbuzz": None,
                                                       "cricsheet": 1}}, ["maidens"])
    assert (e, c, s) == ("maid 1", "maid ·", "maid 1")


# ── the answer vocabulary ────────────────────────────────────────────────────────────────────

def test_taking_the_official_card_is_recognised_under_both_names(wcmod):
    """1000+ ledger rows say "S2"; every new one says "cricsheet". Understanding only one of the
    two would silently un-answer half the owner's decisions."""
    assert wcmod._l2_takes_official("S2") and wcmod._l2_takes_official("cricsheet")
    assert wcmod._l2_takes_official("Cricsheet")
    assert not wcmod._l2_takes_official("S1")
    assert not wcmod._l2_takes_official("espn")
    assert not wcmod._l2_takes_official(None)


def test_a_named_answer_still_closes_the_row(wcmod):
    """The fix for the resurrecting rows must survive the rename."""
    for named in ("Cricsheet", "ESPN", "Cricbuzz", "Manual"):
        assert wcmod.recon_answered("ci:1", {"ci:1": named}, "mk", "L2", set()) is True
