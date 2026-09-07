"""The edges of the three-feed model — chiefly: WHAT IF CRICSHEET HAS NOT LANDED?

That is the normal state of every match for hours or days after it ends, and it is when money
settles, so it is the case that must not have a hole in it. The rule the whole design rests on is
that a MAJORITY is an answer — and the trap sitting right next to it is treating a feed that said
nothing as if it had agreed with a zero. Two cards are a question. One card is not even that.
"""
import json
import sys

import pytest

from test_needs_cricinfo_tab import _FakeSheet, _FakeWS, fake_gspread  # noqa: F401

OLD_HEADER = ["Tour", "Match", "Date", "Player ID", "Full Name", "Param",
              "S1 = 2nd witness: cricbuzz (L1) / held provisional (L2)",
              "S2 = ESPN (L1) / OFFICIAL cricsheet (L2)",
              "Correct Value", "Manual Value", "Status", "Match Key"]
NEW_HEADER = ["Tour", "Match", "Date", "Player ID", "Full Name", "Field",
              "ESPN", "Cricbuzz", "Cricsheet", "What the sources say",
              "Correct Value", "Manual Value", "Status", "Match Key"]


# ── cricsheet has NOT landed ─────────────────────────────────────────────────────────────────

def test_two_feeds_never_auto_close_anything(wcmod):
    """The load-bearing one. Before cricsheet posts there are only ever ESPN and Cricbuzz, and
    two cards that disagree are a QUESTION — there is no third opinion to make it a majority.
    If this ever returned a value, the bot would silently pick a side on live, unsettled money."""
    for e, c in ((25, 35), (0, 14), (75, 74), (1, 0)):
        assert wcmod.feed_concurrence({"espn": e, "cricbuzz": c, "cricsheet": None})[0] is None


def test_two_feeds_that_AGREE_still_settle_it(wcmod):
    """The mirror, and it must NOT be broken by the rule above: ESPN and Cricbuzz concurring is
    a real cross-check and always was — it is what lets a match reach L1_DONE and freeze before
    cricsheet exists. Refusing it would hold every clean match open forever."""
    v, verdict, who = wcmod.feed_concurrence({"espn": 40, "cricbuzz": 40, "cricsheet": None})
    assert v == 40 and set(who) == {"espn", "cricbuzz"} and "all 2 agree" in verdict


def test_an_l1_row_leaves_the_cricsheet_column_EMPTY_not_zero(wcmod, perf):
    """"Not in yet" and "measured nothing" are different claims and must not render the same."""
    rows = wcmod.build_recon_rows("mk", "lbl", "d", "T", {"p": "_"},
                                  {"p": perf(r=33)}, {"p": perf(r=40)})
    assert len(rows) == 1
    assert rows[0]["cricsheet"] == ""
    assert rows[0]["espn"] == "runs 40" and rows[0]["cricbuzz"] == "runs 33"
    assert "no Cricsheet to break the tie" in rows[0]["verdict"]


def test_a_none_from_the_witness_never_becomes_a_row(wcmod, perf):
    """Cricbuzz writes None for a field it could not establish. Comparing that as 0 would
    fabricate a disagreement — and would then render the literal word "None" in a feed column."""
    rows = wcmod.build_recon_rows("mk", "lbl", "d", "T", {"p": "_"},
                                  {"p": perf(maidens=None)}, {"p": perf(maidens=2)})
    assert rows == []


# ── cricsheet HAS landed, but the witness has not ────────────────────────────────────────────

def test_no_witness_means_two_feeds_and_a_named_missing_card(wcmod):
    """9 of 13 tours have no cricbuzz_series, so this is the majority of the corpus. It stays a
    human question, and the verdict says WHICH card would have settled it."""
    v, verdict, _ = wcmod.feed_concurrence({"espn": 5, "cricbuzz": None, "cricsheet": 8})
    assert v is None and verdict == "ESPN 5 vs Cricsheet 8 — no Cricbuzz to break the tie"


def test_one_player_missing_from_the_witness_falls_back_to_two(wcmod):
    """A tour CAN have Cricbuzz while a given player is unbridged on it. Same shape as no witness
    at all — never a silent majority of one card against itself."""
    assert wcmod.feed_concurrence({"espn": 0, "cricbuzz": None, "cricsheet": 3})[0] is None


def test_a_single_surviving_source_settles_nothing(wcmod):
    v, verdict, _ = wcmod.feed_concurrence({"espn": None, "cricbuzz": None, "cricsheet": 7})
    assert v is None and "single source (Cricsheet only)" in verdict


def test_nothing_measured_it_at_all(wcmod):
    v, verdict, who = wcmod.feed_concurrence({"espn": None, "cricbuzz": None, "cricsheet": None})
    assert v is None and who == () and verdict == "no source measured this"


# ── the direction of the majority matters ────────────────────────────────────────────────────

def test_only_a_majority_CONTAINING_cricsheet_may_auto_close(wcmod):
    """Both are 2-of-3, and they are treated differently on purpose — see the commit. This test
    exists so that asymmetry can never be 'tidied up' into symmetry by accident."""
    backed, _, who_backed = wcmod.feed_concurrence({"espn": 25, "cricbuzz": 35, "cricsheet": 35})
    assert backed == 35 and "cricsheet" in who_backed          # auto-closes
    lone, _, who_lone = wcmod.feed_concurrence({"espn": 75, "cricbuzz": 75, "cricsheet": 74})
    assert lone == 75 and "cricsheet" not in who_lone          # still asked


def test_a_genuine_zero_is_a_value_and_can_win(wcmod):
    """ABSENCE is not a value; a measured 0 very much is. Conflating them is this file's most
    expensive recurring bug and it must not be re-introduced by the concurrence rule."""
    v, _, who = wcmod.feed_concurrence({"espn": 0, "cricbuzz": 1, "cricsheet": 0})
    assert v == 0 and set(who) == {"espn", "cricsheet"}


def test_columns_survive_an_empty_field_list(wcmod):
    assert wcmod.three_feed_columns({}, []) == ("", "", "", "")


# ── the tab reader, across the rename ────────────────────────────────────────────────────────

def _read_with(wcmod, monkeypatch, tmp_path, rows):
    p = tmp_path / "ov.json"
    p.write_text(json.dumps({"overrides": []}))
    monkeypatch.setattr(wcmod, "OVERRIDES_PATH", str(p))
    monkeypatch.setattr(wcmod, "open_gsheet", lambda: _FakeSheet(_FakeWS(rows)))
    wcmod.RECON_ACK.clear(); wcmod.PRIOR_RECON.clear()
    wcmod.read_recon_approvals()
    return json.loads(p.read_text())["overrides"]


def test_an_answer_typed_into_the_OLD_sheet_still_lands(wcmod, monkeypatch, tmp_path, fake_gspread):
    """THE MIGRATION HAZARD. read_recon_approvals runs BEFORE write_recon_tab, so on the first
    run after the rename it is handed the old 12-column sheet with answers already in it. Losing
    them is exactly the failure this whole change exists to stop."""
    row = ["T", "M", "2026-09-02", "ci:1", "P", "L2", "was 21", "now 29",
           "S2", "", "status", "2026-09-02::a|b"]
    ovs = _read_with(wcmod, monkeypatch, tmp_path, [OLD_HEADER, row])
    assert len(ovs) == 1 and ovs[0]["pid"] == "ci:1" and ovs[0]["source"] == "S2"


def test_a_named_answer_in_the_new_sheet_lands_as_the_same_decision(
        wcmod, monkeypatch, tmp_path, fake_gspread):
    row = ["T", "M", "2026-09-02", "ci:1", "P", "L2", "conc 21", "conc 29", "conc 29",
           "2 of 3", "Cricsheet", "", "status", "2026-09-02::a|b"]
    ovs = _read_with(wcmod, monkeypatch, tmp_path, [NEW_HEADER, row])
    assert len(ovs) == 1
    assert ovs[0]["source"] == "S2"          # ledger vocabulary, unchanged
    assert ovs[0]["answer"] == "Cricsheet"   # what the human actually chose
    assert wcmod._l2_takes_official(ovs[0]["source"])


def test_a_header_it_cannot_understand_is_REFUSED_not_guessed(
        wcmod, monkeypatch, tmp_path, fake_gspread):
    """Positional fallbacks are gone. Guessing at a header it does not recognise is how a column
    shift turns one man's answer into another man's override."""
    ovs = _read_with(wcmod, monkeypatch, tmp_path,
                     [["something", "entirely", "different"], ["a", "b", "c"]])
    assert ovs == []


# ── freezing `innings` ───────────────────────────────────────────────────────────────────────

def test_innings_is_frozen_only_where_it_exists(wcmod, perf):
    """Red ball carries the splits; white ball has no such key and must not grow an empty one —
    `perf.get(f) is not None` is what keeps this free for every other format."""
    white = perf(played=True, r=40, b=25)
    assert "innings" not in {f: white.get(f) for f in wcmod.SETTLED_FIELDS
                             if white.get(f) is not None}
    red = perf(played=True, r=40, b=25)
    red["innings"] = [{"r": 40}]
    frozen = {f: red.get(f) for f in wcmod.SETTLED_FIELDS if red.get(f) is not None}
    assert frozen["innings"] == [{"r": 40}]


def test_points_gap_without_a_settled_record_still_re_scores(wcmod, perf):
    """A row that predates field-level freezing has no settled total to read. The backstop must
    fall back to a re-score rather than going quiet — a silent backstop is worse than none."""
    wcmod.CURRENT_FMT = "T20"
    try:
        a = perf(played=True, r=10, b=10)
        b = perf(played=True, r=50, b=10)
        assert wcmod.points_gap(a, b, "BAT", a_total=None).startswith("pts ")
    finally:
        wcmod.CURRENT_FMT = None
