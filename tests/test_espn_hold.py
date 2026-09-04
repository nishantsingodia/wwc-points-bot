"""The CUTOFF on a refused ESPN card — what happens when the ball-count gate keeps firing.

The gate itself is pinned by test_espn_completeness.py: parse_espn refuses a ball-by-ball shorter
than ESPN's own scorecard. This file pins what happens NEXT, which was the hole: the refusal
returned a bare `{}`, indistinguishable from "ESPN has no data", so the caller skipped the match
and retried it FOREVER with no cutoff. On an ESPN-only tour (CPL) that meant a match absent from
the sheet entirely, visible only as one line of stderr inside a green workflow run.

Measured before writing this: over the cached ESPN corpus (123 bodies / 71 distinct events /
6 series / T20+ODI+HUN) the ball-by-ball delivery count equals the scorecard's ball total EXACTLY
on 71/71 events. So a fire is never noise — which is why the answer is "hold, name it and make a
human decide", not "tolerate a margin".
"""
import pytest

from test_espn_completeness import PREAMBLE, _delivery, _summary_with_balls, _install


@pytest.fixture(autouse=True)
def _clean(wcmod):
    wcmod.ESPN_HOLDS.clear()
    wcmod.ESPN_CARD_SCORE_ANYWAY.clear()
    yield
    wcmod.ESPN_HOLDS.clear()
    wcmod.ESPN_CARD_SCORE_ANYWAY.clear()


def _short_card(wcmod, monkeypatch, ev, got=200, exp=236):
    items = [_delivery(i) for i in range(got)]
    pbp = {"commentary": {"count": got, "pageIndex": 1, "pageCount": 1, "items": items}}
    _install(wcmod, monkeypatch, pbp, _summary_with_balls(exp))
    return wcmod.parse_espn(ev)


# ── a refusal must be NAMED, not silent ──────────────────────────────────────────────────

def test_refusal_records_a_named_hold(wcmod, monkeypatch):
    """`{}` alone cannot be told apart from 'ESPN has no data'. The hold is what tells them apart."""
    perf, _ = _short_card(wcmod, monkeypatch, "ev-held")
    assert perf == {}
    h = wcmod.ESPN_HOLDS.get("ev-held")
    assert h, "the card was refused but nothing recorded WHY — the caller cannot name the hold"
    assert (h["got"], h["expected"], h["kind"]) == (200, 236, "short_vs_scorecard")


def test_empty_card_is_held_with_zero_deliveries(wcmod, monkeypatch):
    """The CPL ev 1534183 signature: 200 OK, one pre-match preamble item, scorecard says 236."""
    pbp = {"commentary": {"count": 1, "pageIndex": 1, "pageCount": 1, "items": [PREAMBLE]}}
    _install(wcmod, monkeypatch, pbp, _summary_with_balls(236))
    perf, _ = wcmod.parse_espn("1534183")
    assert perf == {}
    assert wcmod.ESPN_HOLDS["1534183"]["got"] == 0


def test_a_healed_card_releases_the_hold(wcmod, monkeypatch):
    """A hold that is never cleared keeps a healed match red forever — the written-but-never-read
    mirror of the bug the gate exists for. Refuse, then serve the full card: the hold must go."""
    _short_card(wcmod, monkeypatch, "ev-heals")
    assert "ev-heals" in wcmod.ESPN_HOLDS
    items = [_delivery(i) for i in range(236)]
    _install(wcmod, monkeypatch,
             {"commentary": {"count": 236, "pageIndex": 1, "pageCount": 1, "items": items}},
             _summary_with_balls(236))
    perf, _ = wcmod.parse_espn("ev-heals")
    assert perf, "a complete card was refused"
    assert "ev-heals" not in wcmod.ESPN_HOLDS, "the hold survived a good parse -> permanently red"


def test_unverifiable_card_is_not_a_hold(wcmod, monkeypatch):
    """No bowling figures = the check is unavailable, not failed. Must not manufacture a hold."""
    items = [_delivery(i) for i in range(120)]
    _install(wcmod, monkeypatch,
             {"commentary": {"count": 120, "pageIndex": 1, "pageCount": 1, "items": items}},
             {"rosters": []})
    perf, _ = wcmod.parse_espn("ev-nofigures")
    assert perf
    assert wcmod.ESPN_HOLDS == {}


# ── the cutoff itself ────────────────────────────────────────────────────────────────────

def test_escalation_waits_out_the_retry_budget(wcmod):
    """Within the budget it is a quiet retry; past it, a named row and a red run."""
    grace = wcmod.ESPN_HOLD_GRACE_H
    assert not wcmod._espn_hold_escalated(1.0, is_live=False, approved=False)
    assert not wcmod._espn_hold_escalated(wcmod.OVER_HRS_MAX + grace - 0.5,
                                          is_live=False, approved=False)
    assert wcmod._espn_hold_escalated(wcmod.OVER_HRS_MAX + grace + 0.1,
                                      is_live=False, approved=False)


def test_a_live_match_never_escalates(wcmod):
    """A live card is legitimately still filling, and parse_espn fetches the scorecard AFTER the
    ball-by-ball — so mid-innings the scorecard can be a ball or two ahead. That is a fetch-ordering
    race, not a defect, and must never raise a row or fail a run."""
    assert not wcmod._espn_hold_escalated(999, is_live=True, approved=False)


def test_an_unknown_clock_never_escalates(wcmod):
    """hours_since_start is None on an unparseable date. An unknown clock is not an expired one —
    escalating on it would be an absence presenting as a value."""
    assert not wcmod._espn_hold_escalated(None, is_live=False, approved=False)


def test_an_approved_card_never_escalates(wcmod):
    """The human already answered; keep flagging, stop nagging."""
    assert not wcmod._espn_hold_escalated(999, is_live=False, approved=True)


def test_hold_row_names_the_match_and_carries_both_numbers(wcmod):
    """'a NAMED row' means the owner can act on it without opening a workflow log."""
    row = wcmod._espn_hold_row({"event": "1534183", "kind": "short_vs_scorecard",
                                "got": 200, "expected": 236, "hours": 21.4,
                                "tour": "Caribbean Premier League 2026",
                                "match": "Match 6 — TKR v BR", "date": "2026-08-12",
                                "match_key": "2026-08-12::br|tkr"})
    assert row["param"] == "ESPN CARD"
    assert row["pid"] == "espn:1534183"
    assert row["tier"] == "espn"
    assert row["match"] == "Match 6 — TKR v BR"
    # The numbers live in the NAMED ESPN column now — it is ESPN's ball-by-ball against ESPN's
    # own scorecard, so no other feed has anything to say and their columns stay empty.
    assert "200" in row["espn"] and "236" in row["espn"] and "36 missing" in row["espn"]
    assert row["cricbuzz"] == "" and row["cricsheet"] == ""
    assert "Hold = keep holding" in row["verdict"]


# ── the human's answer, and what it does ─────────────────────────────────────────────────

def test_approval_roundtrips_into_an_override(wcmod):
    o = wcmod._approval_to_override("2026-08-12::br|tkr", "espn:1534183", "ESPN CARD", "S2", "")
    assert o == {"match_key": "2026-08-12::br|tkr", "scope": "espn_card",
                 "pid": "espn:1534183", "source": "S2", "status": "approved",
                 "answer": "S2",         # what the human typed, verbatim
                 "witness": "espn"}      # the slot names its feed, so it cannot drift silently
    assert wcmod._approval_to_override("k", "espn:1", "ESPN CARD", "S1", "")["source"] == "S1"
    assert wcmod._approval_to_override("k", "espn:1", "ESPN CARD", "", "") is None


def test_the_named_answers_mean_the_same_as_the_old_letters(wcmod):
    """The owner types a FEED NAME now. The ledger keeps storing letters, so every historical
    reader keeps working — but the name he chose is stamped alongside, and a nonsense answer is
    refused rather than silently coerced into one of them."""
    score_it = wcmod._approval_to_override("k", "espn:1", "ESPN CARD", "ESPN", "")
    assert score_it["source"] == "S2" and score_it["answer"] == "ESPN"
    hold = wcmod._approval_to_override("k", "espn:1", "ESPN CARD", "Hold", "")
    assert hold["source"] == "S1" and hold["answer"] == "Hold"
    take_official = wcmod._approval_to_override("k", "ci:1", "L2", "Cricsheet", "")
    assert take_official["source"] == "S2" and take_official["answer"] == "Cricsheet"
    assert wcmod._approval_to_override("k", "ci:1", "L2", "ESPN", "")["source"] == "S1"
    assert wcmod._approval_to_override("k", "ci:1", "runs", "Cricbuzz", "")["source"] == "S1"
    assert wcmod._approval_to_override("k", "ci:1", "runs", "ESPN", "")["source"] == "S2"
    assert wcmod._approval_to_override("k", "ci:1", "L2", "banana", "") is None


def test_approved_short_card_scores_but_stays_flagged(wcmod, monkeypatch):
    """S2 = 'score it anyway'. It must publish — and it must NEVER publish looking clean."""
    wcmod.ESPN_CARD_SCORE_ANYWAY.add("ev-approved")
    perf, _ = _short_card(wcmod, monkeypatch, "ev-approved")
    assert perf, "an approved short card still refused to score"
    h = wcmod.ESPN_HOLDS.get("ev-approved")
    assert h and h.get("approved") is True, (
        "the shortfall was forgotten once approved — the row would publish with no trace that "
        "36 deliveries are missing"
    )


def test_non_player_override_pids_are_not_shouted_as_orphans(wcmod, capsys):
    """The orphan guard asks 'does the registry still know this PLAYER?'. `espn:<id>` is an event
    and `*` is the whole-match L1 seed; neither is a player. ('*' was already latent — every
    match-level seed would have been reported orphaned.)"""
    data = {"overrides": [
        {"status": "approved", "match_key": "k", "pid": "espn:1534183", "scope": "espn_card"},
        {"status": "approved", "match_key": "k", "pid": "*", "scope": "match"},
    ]}
    idx = wcmod.overrides_by_match(data, known_pids={"ci:1"})
    assert len(idx["k"]) == 2
    assert "ORPHANED" not in capsys.readouterr().err
