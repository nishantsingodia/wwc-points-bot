"""Tests for the NEW recon-review logic: match-status predicate, override apply + recompute,
systemic detection, and the approval->override mapping. Includes the Match 30 regression loop
(LIVE -> approve 'use ESPN' -> COMPLETED with recomputed points)."""
import pytest


# ── classify_match_status (the 4 locked decisions) ──────────────────────────
def test_clean_l1_is_completed(wcmod):
    assert wcmod.classify_match_status(False, True, {}, {}, False) == ("COMPLETED", "")


def test_any_unresolved_gap_holds_live(wcmod):
    st, flag = wcmod.classify_match_status(False, True, {"p": "runs 1/2"}, {"p": "runs 1/2"}, False)
    assert st == "LIVE"
    assert "pending recon approval (1 player)" in flag


def test_resolved_gap_completes(wcmod):
    # gap detected (l1_gaps) but resolved by an approval (empty unresolved) -> COMPLETED
    assert wcmod.classify_match_status(False, True, {"p": "x"}, {}, False)[0] == "COMPLETED"


def test_single_feed_completed_but_flagged(wcmod):
    st, flag = wcmod.classify_match_status(False, False, {}, {}, False)
    assert st == "COMPLETED_FLAGGED"
    assert "single feed" in flag


def test_cricsheet_clean_completed(wcmod):
    assert wcmod.classify_match_status(True, True, {}, {}, False) == ("COMPLETED", "")


def test_l2_revision_flagged(wcmod):
    st, flag = wcmod.classify_match_status(True, True, {}, {}, True)
    assert st == "COMPLETED_FLAGGED"
    assert "official revision pending" in flag


# ── match_key_of: stable + order-independent ────────────────────────────────
def test_match_key_order_independent(wcmod):
    a = wcmod.match_key_of("2026-06-28", ["Australia Women", "India Women"])
    b = wcmod.match_key_of("2026-06-28", ["India Women", "Australia Women"])
    assert a == b and a.startswith("2026-06-28::")


# ── compute_l1_gaps / feed_team_totals / totals_differ ──────────────────────
def test_compute_l1_gaps_only_flags_differences(wcmod):
    capi = {"a": {"r": 38, "w": 0, "4s": 5, "6s": 0}, "b": {"r": 10, "w": 0, "4s": 1, "6s": 0}}
    espn = {"a": {"r": 57, "w": 0, "4s": 8, "6s": 0}, "b": {"r": 10, "w": 0, "4s": 1, "6s": 0}}
    gaps = wcmod.compute_l1_gaps(capi, espn)
    assert set(gaps) == {"a"} and "runs 38/57" in gaps["a"]


def test_feed_team_totals_and_diff(wcmod):
    assigned = {("AUS", "Perry"): {"r": 38, "w": 0}, ("AUS", "Gardner"): {"r": 33, "w": 0},
                ("IND", "Charani"): {"r": 0, "w": 1}}
    tot = wcmod.feed_team_totals(assigned)
    assert tot["AUS"]["r"] == 71 and tot["IND"]["w"] == 1
    assert wcmod.totals_differ(tot, wcmod.feed_team_totals({("AUS", "Perry"): {"r": 57, "w": 0}})) is True
    assert wcmod.totals_differ(tot, tot) is False


# ── apply_recon_overrides + recompute ───────────────────────────────────────
def test_match_seed_uses_espn_and_recomputes(perf, wcmod):
    charani = perf("Shree Charani", w=1, balls=18, runs_conceded=26, dots=9, played=True)
    perry = perf("Ellyse Perry", r=38, b=26, catches=1, balls=6, dots=3, played=True, **{"4s": 5})
    capi = {"cha": {"r": 0, "w": 1, "4s": 0, "6s": 0}, "per": {"r": 38, "w": 0, "4s": 5, "6s": 0}}
    espn = {"cha": {"r": 0, "w": 2, "4s": 0, "6s": 0}, "per": {"r": 57, "w": 0, "4s": 8, "6s": 0}}
    l1 = wcmod.compute_l1_gaps(capi, espn)
    idx = {"M": [{"match_key": "M", "scope": "match", "source": "S2", "status": "approved"}]}
    applied = wcmod.apply_recon_overrides({"cha": charani, "per": perry}, capi, espn, l1, "M", idx)
    assert applied == {"cha", "per"}
    assert charani["w"] == 2 and perry["r"] == 57 and perry["4s"] == 8
    # re-scoring after override picks up the corrected raw stats + derived bonuses
    assert wcmod.score(charani, "BOWL")["total"] == 73
    assert wcmod.score(perry, "AR")["total"] == 118


def test_player_override_wins_over_match_seed(perf, wcmod):
    p = perf(r=38, b=26, played=True, **{"4s": 5})
    capi = {"x": {"r": 38, "w": 0, "4s": 5, "6s": 0}}
    espn = {"x": {"r": 57, "w": 0, "4s": 8, "6s": 0}}
    l1 = wcmod.compute_l1_gaps(capi, espn)
    idx = {"M": [
        {"match_key": "M", "scope": "match", "source": "S2", "status": "approved"},
        {"match_key": "M", "scope": "player", "pid": "x", "field": "r",
         "source": "Manual", "value": 50, "status": "approved"},
    ]}
    wcmod.apply_recon_overrides({"x": p}, capi, espn, l1, "M", idx)
    assert p["r"] == 50    # manual player override wins over the match seed's 57
    assert p["4s"] == 8    # 4s still from the match seed


def test_no_overrides_is_noop(perf, wcmod):
    p = perf(r=38, played=True)
    assert wcmod.apply_recon_overrides({"x": p}, {}, {}, {}, "M", {}) == set()
    assert p["r"] == 38


# ── build_recon_rows: systemic (1 row) vs isolated (per-player) ──────────────
def test_systemic_when_4plus_players_differ(wcmod):
    unresolved = {f"p{i}": "runs 1/2" for i in range(4)}
    rows = wcmod.build_recon_rows("M", "Match 30", "2026-06-28", "WWC", unresolved,
                                  {}, {}, {}, {}, n_compared=20)
    assert len(rows) == 1 and rows[0]["tier"] == "match" and rows[0]["full"] == "— WHOLE MATCH —"


def test_systemic_when_team_totals_differ(wcmod):
    unresolved = {"p0": "runs 1/2"}   # only 1 player, but team totals diverge -> systemic
    rows = wcmod.build_recon_rows("M", "lbl", "d", "T", unresolved,
                                  {"p0": {"r": 1}}, {"p0": {"r": 2}},
                                  {"AUS": {"r": 100, "w": 0}}, {"AUS": {"r": 171, "w": 0}}, n_compared=11)
    assert rows[0]["tier"] == "match"


def test_isolated_disagreement_is_per_player(wcmod):
    unresolved = {"x": "wkts 1/2"}
    capi = {"x": {"r": 0, "w": 1, "4s": 0, "6s": 0}}
    espn = {"x": {"r": 0, "w": 2, "4s": 0, "6s": 0}}
    rows = wcmod.build_recon_rows("M", "lbl", "d", "T", unresolved, capi, espn,
                                  {"IND": {"r": 50, "w": 5}}, {"IND": {"r": 50, "w": 5}}, n_compared=11)
    assert len(rows) == 1 and rows[0]["tier"] == "player"
    assert rows[0]["param"] == "wkts" and rows[0]["s1"] == 1 and rows[0]["s2"] == 2


# ── _approval_to_override mapping ───────────────────────────────────────────
def test_approval_match_seed(wcmod):
    o = wcmod._approval_to_override("M", "", "ALL L1", "S2", "")
    assert o["scope"] == "match" and o["source"] == "S2"


def test_approval_player_feed(wcmod):
    o = wcmod._approval_to_override("M", "x", "wkts", "S2", "")
    assert o == {"match_key": "M", "scope": "player", "pid": "x", "field": "w",
                 "source": "S2", "status": "approved"}


def test_approval_player_manual(wcmod):
    o = wcmod._approval_to_override("M", "x", "runs", "Manual", "57")
    assert o["source"] == "Manual" and o["value"] == 57 and o["field"] == "r"


def test_approval_l2(wcmod):
    o = wcmod._approval_to_override("M", "x", "L2", "S2", "")
    assert o["scope"] == "l2" and o["source"] == "S2"


def test_player_recon_markers(wcmod):
    # which players the draft UI should flag, resolution-aware
    m = wcmod.player_recon_markers({"a": "runs 1/2", "b": "wkts 1/2"}, {}, {})
    assert m == {"a": "⏳ unreconciled", "b": "⏳ unreconciled"}
    # an unapproved L2 revision is flagged; an approved (S2) one is not
    assert wcmod.player_recon_markers({}, {"c": "runs 57→56"}, {})["c"] == "⚠ official revision"
    assert wcmod.player_recon_markers({}, {"c": "x"}, {"c": "S2"}) == {}


def _p2(**kw):
    base = {"r": 0, "w": 0, "4s": 0, "6s": 0, "dots": 0, "maidens": 0,
            "runs_conceded": 0, "catches": 0, "stumpings": 0, "runouts": 0}
    base.update(kw)
    return base


def test_l2_compares_against_reconciled_not_raw_cricapi(wcmod):
    # cricapi froze Charani at 1 wkt; ESPN had 2; you approved S2 (ESPN). cricsheet later CONFIRMS
    # 2. L2 must be SILENT — comparing official(2) to the reconciled(2), not raw cricapi(1).
    prov = {"cha": _p2(w=1, dots=9, runs_conceded=26)}
    capi = {"cha": {"r": 0, "w": 1, "4s": 0, "6s": 0}}
    espn = {"cha": {"r": 0, "w": 2, "4s": 0, "6s": 0}}
    cs = {"cha": _p2(w=2, dots=9, runs_conceded=26)}
    l1 = wcmod.compute_l1_gaps(capi, espn)
    idx = {"M": [{"match_key": "M", "scope": "match", "source": "S2", "status": "approved"}]}
    recon = wcmod.reconciled_provisional(prov, capi, espn, l1, "M", idx)
    assert recon["cha"]["w"] == 2                          # approved correction is in the baseline
    assert wcmod.recon_gaps(recon["cha"], cs["cha"], wcmod.RECON_L2, sep="→") == ""  # silent ✓
    # the OLD (buggy) comparison against raw cricapi WOULD have falsely flagged a revision:
    assert "wkts 1→2" in wcmod.recon_gaps(prov["cha"], cs["cha"], wcmod.RECON_L2, sep="→")


def test_l2_flags_when_official_differs_from_reconciled(wcmod):
    # you approved 57 (ESPN); cricsheet says 56 -> a genuine change from what was shown -> flag.
    prov = {"per": _p2(r=38, **{"4s": 5})}
    capi = {"per": {"r": 38, "w": 0, "4s": 5, "6s": 0}}
    espn = {"per": {"r": 57, "w": 0, "4s": 8, "6s": 0}}
    cs = {"per": _p2(r=56, **{"4s": 8})}
    l1 = wcmod.compute_l1_gaps(capi, espn)
    idx = {"M": [{"match_key": "M", "scope": "match", "source": "S2", "status": "approved"}]}
    recon = wcmod.reconciled_provisional(prov, capi, espn, l1, "M", idx)
    assert recon["per"]["r"] == 57
    assert "runs 57→56" in wcmod.recon_gaps(recon["per"], cs["per"], wcmod.RECON_L2, sep="→")


def test_overrides_by_match_indexes_only_approved(wcmod):
    data = {"overrides": [
        {"match_key": "A", "scope": "match", "source": "S2", "status": "approved"},
        {"match_key": "A", "scope": "player", "pid": "x", "field": "r", "status": "pending"},
        {"match_key": "B", "scope": "l2", "pid": "y", "source": "S2", "status": "approved"},
    ]}
    idx = wcmod.overrides_by_match(data)
    assert set(idx) == {"A", "B"} and len(idx["A"]) == 1 and idx["A"][0]["source"] == "S2"


def test_approval_blank_and_manual_without_value_are_none(wcmod):
    assert wcmod._approval_to_override("M", "x", "runs", "", "") is None
    assert wcmod._approval_to_override("M", "x", "runs", "Manual", "") is None


# ── Match 30 regression: LIVE -> approve 'use ESPN' -> COMPLETED ─────────────
def test_match30_live_then_completed(perf, wcmod):
    capi = {"cha": {"r": 0, "w": 1, "4s": 0, "6s": 0}, "per": {"r": 38, "w": 0, "4s": 5, "6s": 0}}
    espn = {"cha": {"r": 0, "w": 2, "4s": 0, "6s": 0}, "per": {"r": 57, "w": 0, "4s": 8, "6s": 0}}
    l1 = wcmod.compute_l1_gaps(capi, espn)
    # systemic detection -> one match-level row
    assert wcmod.build_recon_rows("M", "AUS v IND", "2026-06-28", "WWC", l1, capi, espn,
                                  {"AUS": {"r": 130, "w": 4}}, {"AUS": {"r": 171, "w": 4}},
                                  n_compared=22)[0]["tier"] == "match"
    # pre-approval: every gap unresolved -> LIVE
    assert wcmod.classify_match_status(False, True, l1, l1, False)[0] == "LIVE"
    # approve 'use ESPN' for the whole match -> all gaps resolved -> COMPLETED
    pbp = {"cha": perf(w=1, balls=18, runs_conceded=26, dots=9, played=True),
           "per": perf(r=38, b=26, catches=1, balls=6, dots=3, played=True, **{"4s": 5})}
    idx = {"M": [{"match_key": "M", "scope": "match", "source": "S2", "status": "approved"}]}
    applied = wcmod.apply_recon_overrides(pbp, capi, espn, l1, "M", idx)
    unresolved = {p: g for p, g in l1.items() if p not in applied}
    assert wcmod.classify_match_status(False, True, l1, unresolved, False) == ("COMPLETED", "")
    assert wcmod.score(pbp["cha"], "BOWL")["total"] == 73
    assert wcmod.score(pbp["per"], "AR")["total"] == 118
