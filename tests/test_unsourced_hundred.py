"""The unsourced-dots guard must key on `_bowled`, not `balls` — or it is dead on The Hundred.

cricapi omits the `overs` field on 100-ball cards, so EVERY Hundred bowler arrives with balls=0.
Both unsourced guards tested `v.get("balls")`, which is therefore falsy for every one of them: on a
cricapi-only Hundred match (ESPN unavailable) NO bowler was recorded as missing his dots, the match
was allowed to publish COMPLETED, and record_settlement froze it.

That freeze is WRITE-ONCE — the record of what money was settled on — so those baselines can never
be corrected in place. It happened: 279 rows frozen on a cricapi-only card on 4-5 Aug, measured at
+617 FP against cricsheet across 8 settled matches.

`dots` and `maidens` are ESPN-ONLY at L1 (cricapi supplies neither), so a bowler with no ESPN row
has UNCONSUMED data. Per the locked recon model that keeps the match LIVE with a named row — never
a silent zero, and never a settlement freeze.

`_bowled` is the predicate the SCORER already uses to decide whether to award bowling points, so
guarding with it means the guard and the score cannot disagree about whether a man bowled.
"""
import pytest


def _hundred_bowler(wcmod, **over):
    """A Hundred bowler as cricapi delivers him: no `overs` field, so balls=0, but real wickets."""
    p = wcmod.blank_perf("Test Bowler")
    p.update(played=True, balls=0, w=2, runs_conceded=31)
    p.update(over)
    return p


def test_bowled_sees_a_hundred_bowler_that_balls_cannot(wcmod):
    """The core of it: balls=0 is falsy, but the man plainly bowled."""
    p = _hundred_bowler(wcmod)
    assert not p["balls"], "precondition: cricapi gives a Hundred bowler no ball count"
    assert wcmod._bowled(p), "_bowled must recognise a bowler from his wickets/runs alone"


def test_unsourced_guard_is_not_dead_on_the_hundred(wcmod):
    """The guard expression itself, exactly as run_tour builds it."""
    assigned = {("HUN", "Test Bowler"): _hundred_bowler(wcmod)}

    dead = {k for k, v in assigned.items() if v and v.get("balls")}
    live = {k for k, v in assigned.items() if v and wcmod._bowled(v)}

    assert dead == set(), "the old balls-keyed guard saw nothing — this is the bug"
    assert live == set(assigned), "the _bowled guard must flag him as unsourced"


def test_a_flagged_bowler_keeps_the_match_LIVE(wcmod):
    """Unconsumed single-source data must NOT publish COMPLETED, because COMPLETED freezes money."""
    unsourced = {("HUN", "Test Bowler")}

    status = wcmod.classify_match_status(
        cs_path=False, espn_present=False, l1_gaps="", unresolved=set(), l2_dirty=False,
        unsourced=unsourced, already_completed=False, witness_present=True,
    )
    assert "LIVE" in str(status).upper(), (
        f"a bowler with no dots source published as {status!r} — that is the write-once freeze "
        f"that put 279 rows on the settlement baseline off a cricapi-only card"
    )


def test_widening_only_a_bowler_with_balls_still_flags(wcmod):
    """Proves the change WIDENS the guard rather than swapping which bowlers it catches."""
    p = _hundred_bowler(wcmod, balls=24)
    assert wcmod._bowled(p)
    assert {k for k, v in {("X", "B"): p}.items() if v and wcmod._bowled(v)} == {("X", "B")}


def test_a_pure_batter_is_never_flagged_unsourced(wcmod):
    """The guard must not drag in someone who never bowled — that would strand every match LIVE."""
    p = wcmod.blank_perf("Test Batter")
    p.update(played=True, r=44, b=30)
    assert not wcmod._bowled(p)
    assert {k for k, v in {("X", "B"): p}.items() if v and wcmod._bowled(v)} == set()
