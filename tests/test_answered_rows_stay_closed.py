"""Two "written but never read" defects that made the Recon Review tab re-ask answered questions.

Both were reported by a player of the draft, not by the bot: "why do you keep opening Walter &
Tongue, they've resolved it twice", and the same for three identity rows.

  1. AN S1 ANSWER WAS NOT AN ANSWER. Both queue sites tested the literal string "S2", so only
     "take cricsheet's number" ever closed a row. "S1 = keep what was settled" persisted to
     registry/recon_overrides.json as approved and was then ignored by the tab. RECON_ACK is
     rebuilt from the SHEET each run, so the row vanished in the run it was typed and came back
     — blank — on the next, forever.

  2. THE TEST BASELINE COULD NOT RE-SCORE ITSELF. points_gap re-scored the frozen baseline, but
     `fields` carried no per-innings splits, so score() fell into _score_test's aggregate
     fallback and awarded tiers the published number never had. Every one of the 10 ENG v PAK
     review rows was a phantom `pts X→X-4`, against a "was" value nobody was ever shown.
"""
import json
import os

import pytest

REG = os.path.join(os.path.dirname(__file__), "..", "registry")


# ── 1. answered is answered ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("source", ["S2", "S1", "Manual"])
@pytest.mark.parametrize("param", ["L2", "ID"])
def test_every_stored_answer_closes_the_row(wcmod, source, param):
    """S1 and Manual are decisions the ledger stores. All three must close the row."""
    assert wcmod.recon_answered("ci:1", {"ci:1": source}, "mk", param, set()) is True


@pytest.mark.parametrize("param", ["L2", "ID"])
def test_silence_leaves_the_row_open(wcmod, param):
    assert wcmod.recon_answered("ci:1", {}, "mk", param, set()) is False


def test_this_runs_sheet_answer_also_closes_it(wcmod):
    """RECON_ACK is the in-run path: answered on the sheet, not yet in the ledger."""
    assert wcmod.recon_answered("ci:1", {}, "mk", "L2", {("mk", "ci:1", "L2")}) is True


def test_an_answer_on_another_match_does_not_close_this_one(wcmod):
    assert wcmod.recon_answered("ci:1", {}, "mk", "L2", {("other", "ci:1", "L2")}) is False


def test_walter_and_tongue_are_answered_in_the_committed_ledger(wcmod):
    """THE REPORTED CASE. Both were answered S1 on Hundred M Match 30 and kept re-appearing."""
    with open(os.path.join(REG, "recon_overrides.json")) as fh:
        ledger = json.load(fh)["overrides"]
    mk = "2026-08-11::manchester super giants|sunrisers leeds"
    appr = wcmod.l2_approved_pids(mk, {mk: [o for o in ledger if o["match_key"] == mk]})
    for pid in ("ci:909225", "ci:857975"):
        assert appr.get(pid) == "S1", f"{pid} is not S1 in the ledger — re-point this test"
        assert wcmod.recon_answered(pid, appr, mk, "L2", set()) is True


# ── 2. the Test baseline must not be re-scored ───────────────────────────────────────────────

def _frozen(match_key, full):
    with open(os.path.join(REG, "settlement_snapshots.json")) as fh:
        for r in json.load(fh)["settlements"]:
            if r["match_key"] == match_key and r["full"] == full:
                return r
    return None


def test_innings_is_frozen_so_a_red_ball_baseline_can_score_itself(wcmod):
    assert "innings" in wcmod.SETTLED_FIELDS


def test_a_test_baseline_without_innings_rescores_to_the_wrong_number(wcmod):
    """CHARACTERIZATION of the cause — not a thing to preserve.

    Azan Awais, 1st Test: 9 runs and 1 four across two innings, one of them a duck he was out
    for. Per innings that duck costs -4; on the match aggregate (r=9) it is forgiven. The
    settled — and correct, and cricsheet-confirmed — total is 13.
    """
    rec = _frozen("2026-08-19::england|pakistan", "Azan Awais")
    assert rec is not None and rec["points"] == 13
    base, missing = wcmod._hydrate_baseline(rec["fields"])
    assert missing == (), "nothing looks absent, which is exactly why this went unnoticed"
    assert wcmod.score(base, "BAT", fmt="TEST")["total"] == 17     # the phantom "was" value


def test_the_settled_total_is_believed_over_a_rescore(wcmod):
    """THE FIX. Same baseline, same official card, but anchored on what was actually settled."""
    rec = _frozen("2026-08-19::england|pakistan", "Azan Awais")
    base, _ = wcmod._hydrate_baseline(rec["fields"])
    official = dict(base, innings=[{**base, "r": 0, "4s": 0, "b": 6, "dismissed": True},
                                   {**base, "r": 9, "4s": 1, "b": 9, "dismissed": True}])
    role = "BAT"
    assert wcmod.score(official, role, fmt="TEST")["total"] == 13   # cricsheet agrees with settled

    wcmod.CURRENT_FMT = "TEST"
    try:
        assert wcmod.points_gap(base, official, role) == "pts 17→13"          # old: phantom
        assert wcmod.points_gap(base, official, role, a_total=13) == ""       # new: silent
    finally:
        wcmod.CURRENT_FMT = None


def test_a_real_total_move_is_still_caught(wcmod):
    """The backstop must not go quiet — that is worse than a phantom."""
    wcmod.CURRENT_FMT = "TEST"
    try:
        a = wcmod.blank_perf("x"); a.update(played=True, r=10, b=20)
        b = wcmod.blank_perf("x"); b.update(played=True, r=40, b=60)
        assert wcmod.points_gap(a, b, "BAT", a_total=14) == "pts 14→48"
    finally:
        wcmod.CURRENT_FMT = None
