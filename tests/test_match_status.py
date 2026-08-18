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


# ── compute_l1_gaps + materiality tolerance ─────────────────────────────────
def test_compute_l1_gaps_only_flags_material(wcmod):
    capi = {"a": {"r": 38, "w": 0, "4s": 5, "6s": 0}, "b": {"r": 10, "w": 0, "4s": 1, "6s": 0}}
    espn = {"a": {"r": 57, "w": 0, "4s": 8, "6s": 0}, "b": {"r": 10, "w": 0, "4s": 1, "6s": 0}}
    gaps = wcmod.compute_l1_gaps(capi, espn)
    assert set(gaps) == {"a"} and "runs 38/57" in gaps["a"]


def test_l1_field_material(wcmod):
    assert wcmod._l1_field_material("r", 105, 106) is False   # 1-run blip ignored
    assert wcmod._l1_field_material("r", 100, 105) is True    # >1 run flagged
    assert wcmod._l1_field_material("w", 1, 2) is True         # wickets always
    assert wcmod._l1_field_material("4s", 5, 6) is True        # boundaries always
    assert wcmod._l1_field_material("r", 50, 50) is False      # equal -> never


def test_compute_l1_gaps_ignores_one_run_blip(wcmod):
    # Wyatt 105 vs 106 (1 run, identical otherwise) does NOT hold the match; Charani 1/2 wkts does.
    capi = {"wyatt": {"r": 105, "w": 0, "4s": 9, "6s": 1}, "cha": {"r": 0, "w": 1, "4s": 0, "6s": 0}}
    espn = {"wyatt": {"r": 106, "w": 0, "4s": 9, "6s": 1}, "cha": {"r": 0, "w": 2, "4s": 0, "6s": 0}}
    assert set(wcmod.compute_l1_gaps(capi, espn)) == {"cha"}


def test_compute_l1_gaps_skips_espn_without_ballbyball(wcmod):
    # ESPN lacks ball-by-ball for the match (all-zero placeholders) -> do NOT flag every player
    # as cricapi-vs-0. A player ESPN DID ball-track (b>0) is still compared.
    capi = {"a": {"r": 50, "b": 30, "w": 0, "4s": 5, "6s": 0},
            "b": {"r": 16, "b": 12, "w": 0, "4s": 2, "6s": 0}}
    espn = {"a": {"r": 0, "b": 0, "balls": 0, "w": 0, "4s": 0, "6s": 0},   # no ball-by-ball
            "b": {"r": 20, "b": 12, "w": 0, "4s": 2, "6s": 0}}              # tracked, differs
    assert set(wcmod.compute_l1_gaps(capi, espn)) == {"b"}   # 'a' skipped (ESPN blank), 'b' flagged


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


# ── build_recon_rows: one row per (player, MATERIAL field), no whole-match collapse ──
def test_build_recon_rows_per_player_handles_mixed_match(wcmod):
    # a mixed match (Match-23 class): each differing player gets its own row so the user can
    # pick S1 for one and S2 for another — no single whole-match pick is forced.
    unresolved = {"ferdous": "runs 33/40", "cha": "wkts 1/2"}
    capi = {"ferdous": {"r": 33, "w": 0, "4s": 2, "6s": 0}, "cha": {"r": 0, "w": 1, "4s": 0, "6s": 0}}
    espn = {"ferdous": {"r": 40, "w": 0, "4s": 2, "6s": 0}, "cha": {"r": 0, "w": 2, "4s": 0, "6s": 0}}
    rows = wcmod.build_recon_rows("M", "IND v BAN", "d", "WWC", unresolved, capi, espn)
    assert all(r["tier"] == "player" for r in rows)            # NO whole-match collapse
    got = {(r["pid"], r["param"]): (r["s1"], r["s2"]) for r in rows}
    assert got[("ferdous", "runs")] == (33, 40)
    assert got[("cha", "wkts")] == (1, 2)


def test_build_recon_rows_skips_one_run_blip(wcmod):
    # a 1-run-only diff yields NO row (materiality); the wicket diff does
    unresolved = {"wyatt": "_", "cha": "_"}
    capi = {"wyatt": {"r": 105, "w": 0, "4s": 9, "6s": 1}, "cha": {"r": 0, "w": 1, "4s": 0, "6s": 0}}
    espn = {"wyatt": {"r": 106, "w": 0, "4s": 9, "6s": 1}, "cha": {"r": 0, "w": 2, "4s": 0, "6s": 0}}
    rows = wcmod.build_recon_rows("M", "lbl", "d", "T", unresolved, capi, espn)
    assert len(rows) == 1 and rows[0]["pid"] == "cha" and rows[0]["param"] == "wkts"


# ── _approval_to_override mapping ───────────────────────────────────────────
def test_approval_match_seed(wcmod):
    o = wcmod._approval_to_override("M", "", "ALL L1", "S2", "")
    assert o["scope"] == "match" and o["source"] == "S2"


def test_approval_player_feed(wcmod):
    o = wcmod._approval_to_override("M", "x", "wkts", "S2", "")
    assert o == {"match_key": "M", "scope": "player", "pid": "x", "field": "w",
                 "source": "S2", "status": "approved",
                 # S2 has always meant ESPN; naming the feed makes that a checkable fact rather
                 # than an assumption the next witness migration gets to discover. "S1" already
                 # changed meaning once (cricapi -> cricbuzz) under 10 live approvals.
                 "witness": "espn"}


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
    # one per-player row per differing player (Charani wkts, Perry runs+4s) — no whole-match collapse
    rows = wcmod.build_recon_rows("M", "AUS v IND", "2026-06-28", "WWC", l1, capi, espn)
    assert rows and all(r["tier"] == "player" for r in rows)
    assert {(r["pid"], r["param"]) for r in rows} >= {("cha", "wkts"), ("per", "runs")}
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


# ── Completeness gate: absence of a source is NOT a zero ────────────────────
# Regression for the LPL M2/M4 class — bowlers published COMPLETED with dots=0 because cricapi
# carries no dots, the ESPN row never matched, and L1 only compares r/w/4s/6s so nothing objected.
def test_unsourced_bowler_holds_live(wcmod):
    st, flag = wcmod.classify_match_status(False, True, {}, {}, False, unsourced={("DS", "X")})
    assert st == "LIVE"
    assert "without a dot-ball source" in flag


def test_unsourced_beats_single_feed_flag(wcmod):
    # cricapi-only match: previously COMPLETED_FLAGGED. Nobody supplied dots, so it must HOLD.
    st, _ = wcmod.classify_match_status(False, False, {}, {}, False, unsourced={("DS", "X")})
    assert st == "LIVE"


def test_no_unsourced_leaves_gate_unchanged(wcmod):
    assert wcmod.classify_match_status(False, True, {}, {}, False, unsourced=()) == ("COMPLETED", "")


def test_cricsheet_path_never_unsourced(wcmod):
    # cricsheet carries every field, so a stale unsourced set must not hold an official card.
    assert wcmod.classify_match_status(True, True, {}, {}, False, unsourced={("DS", "X")})[0] \
        == "COMPLETED"


# ── merge_espn_into: one merge implementation, and it reports what it could not source ──
def test_merge_reports_bowler_with_no_espn_row(wcmod, perf):
    assigned = {("DS", "A"): perf("A", balls=24, runs_conceded=30, played=True),
                ("DS", "B"): perf("B", balls=24, runs_conceded=20, played=True)}
    espn = {("DS", "B"): perf("B", balls=24, runs_conceded=20, dots=9, played=True)}
    xcheck, unsourced = wcmod.merge_espn_into(assigned, espn)
    assert unsourced == {("DS", "A")}                  # 4 overs, no ESPN row -> NOT a genuine 0
    assert assigned[("DS", "B")]["dots"] == 9          # matched bowler still gets his dots
    assert xcheck == set()


def test_merge_ignores_non_bowlers(wcmod, perf):
    # a batter with no ESPN row has no ESPN-only field at stake -> not 'unsourced'
    assigned = {("DS", "A"): perf("A", r=40, b=30, played=True)}
    _, unsourced = wcmod.merge_espn_into(assigned, {})
    assert unsourced == set()


# ── build_provisional_cut: the baseline must be built by the SAME matcher as emit ──
def test_baseline_recovers_dots_via_squad_matcher(wcmod):
    """The phantom `dots 0→N` regression.

    ESPN spells him 'Shaheen Afridi', the squad says 'Shaheen Shah Afridi'. The old strict id-only
    index lost the ESPN row and kept cricapi's dots=0, so cricsheet's real 6 read as an official
    revision of a value that was never on screen. The squad-anchored matcher finds him."""
    team_players = [("DS", "Shaheen Shah Afridi", "BOWL")]
    api = {"shaheen shah afridi": wcmod.blank_perf("Shaheen Shah Afridi")}
    api["shaheen shah afridi"].update(balls=18, runs_conceded=24, w=1, played=True)
    espn = {"shaheen afridi": wcmod.blank_perf("Shaheen Afridi")}
    espn["shaheen afridi"].update(balls=18, runs_conceded=24, w=1, dots=6, played=True)

    prov, unsourced = wcmod.build_provisional_cut(team_players, api, espn)
    pid = wcmod.resolve_pid("Shaheen Shah Afridi")
    assert prov[pid]["dots"] == 6      # was 0 under the old id-only index -> phantom revision
    assert unsourced == set()
    # and L2 against the official card is now correctly SILENT
    cs = dict(prov[pid])
    assert wcmod.recon_gaps(prov[pid], cs, wcmod.RECON_L2, sep="→") == ""


def test_baseline_flags_bowler_with_no_espn_row_at_all(wcmod):
    team_players = [("DS", "Nuwan Thushara", "BOWL")]
    api = {"nuwan thushara": wcmod.blank_perf("Nuwan Thushara")}
    api["nuwan thushara"].update(balls=24, runs_conceded=30, w=2, played=True)
    prov, unsourced = wcmod.build_provisional_cut(team_players, api, {})
    assert unsourced == {wcmod.resolve_pid("Nuwan Thushara")}


def test_baseline_matcher_is_quiet(wcmod):
    """Rebuilding the baseline must not re-report anomalies the emit path already reported."""
    before = len(wcmod.ANOMALIES), len(wcmod.REVIEW), len(wcmod.AUTO_ALIASES)
    team_players = [("DS", "Nuwan Thushara", "BOWL")]
    api = {"someone entirely unknown": wcmod.blank_perf("Someone Entirely Unknown")}
    api["someone entirely unknown"].update(balls=24, played=True)
    wcmod.build_provisional_cut(team_players, api, {})
    assert (len(wcmod.ANOMALIES), len(wcmod.REVIEW), len(wcmod.AUTO_ALIASES)) == before


# ── Orphan guard: a pid-keyed approval must never rot silently ──────────────
# 83 of 131 stored approvals were found dead after the 25 Jul `ci:` migration — they stopped
# applying, so the L2 baseline fell back to raw cricapi and the same row reappeared every run.
def test_orphaned_override_is_shouted(wcmod, capsys):
    data = {"overrides": [
        {"match_key": "A", "scope": "player", "pid": "deadbeef", "field": "r",
         "source": "S2", "status": "approved"},
    ]}
    wcmod.overrides_by_match(data, known_pids={"ci:12345"})
    assert "RECON OVERRIDES ORPHANED" in capsys.readouterr().err


def test_known_pid_is_silent(wcmod, capsys):
    data = {"overrides": [
        {"match_key": "A", "scope": "player", "pid": "ci:12345", "field": "r",
         "source": "S2", "status": "approved"},
    ]}
    wcmod.overrides_by_match(data, known_pids={"ci:12345"})
    assert "ORPHANED" not in capsys.readouterr().err


def test_guard_is_opt_in_and_never_drops_overrides(wcmod):
    data = {"overrides": [
        {"match_key": "A", "scope": "player", "pid": "deadbeef", "field": "r",
         "source": "S2", "status": "approved"},
    ]}
    # no known_pids -> legacy behaviour, and an orphan is still INDEXED (warn, never silently drop)
    assert len(wcmod.overrides_by_match(data)["A"]) == 1
    assert len(wcmod.overrides_by_match(data, known_pids={"ci:1"})["A"]) == 1


# ── Rule: nothing goes unconsumed ───────────────────────────────────────────
def test_espn_only_player_keeps_full_record(wcmod, perf):
    """The 4-vs-110 regression.

    cricapi had no line for him; ESPN had a full one. The old code copied ONLY dots+maidens onto a
    fresh blank_perf and binned the rest, so he scored the bare +4 XI bonus. ESPN is a FULL
    scorecard source, not a dots side-channel."""
    espn_row = perf("Big Innings", r=88, b=44, played=True, catches=1, **{"4s": 9, "6s": 4})
    assigned = {}
    wcmod.merge_espn_into(assigned, {("DS", "Big Innings"): espn_row})
    got = assigned[("DS", "Big Innings")]
    assert got["r"] == 88 and got["b"] == 44 and got["4s"] == 9 and got["6s"] == 4
    assert got["catches"] == 1 and got["played"] is True
    # and it must actually SCORE, not collect the XI bonus alone
    assert wcmod.score(got, "BAT")["total"] > 100


def test_espn_only_bare_xi_player_still_gets_xi_only(wcmod, perf):
    # A genuine in-XI player who never batted or bowled must NOT be inflated — only +4.
    espn_row = perf("Did Not Bat", played=True)
    assigned = {}
    wcmod.merge_espn_into(assigned, {("DS", "Did Not Bat"): espn_row})
    assert wcmod.score(assigned[("DS", "Did Not Bat")], "BAT")["total"] == wcmod.R["xi"]


def test_l1_flags_espn_only_player(wcmod, perf):
    capi = {"a": {"r": 40, "b": 30, "w": 0, "4s": 4, "6s": 0}}
    espn = {"a": {"r": 40, "b": 30, "w": 0, "4s": 4, "6s": 0},
            "ghost": {"r": 62, "b": 31, "w": 0, "4s": 6, "6s": 2}}
    gaps = wcmod.compute_l1_gaps(capi, espn)
    assert "ghost" in gaps and "ESPN only" in gaps["ghost"]
    assert "a" not in gaps                       # feeds agree on him


def test_l1_flags_cricapi_only_player_when_espn_covers_match(wcmod):
    capi = {"a": {"r": 40, "b": 30, "w": 0, "4s": 4, "6s": 0},
            "missing": {"r": 55, "b": 33, "w": 0, "4s": 5, "6s": 1}}
    espn = {"a": {"r": 40, "b": 30, "w": 0, "4s": 4, "6s": 0}}
    gaps = wcmod.compute_l1_gaps(capi, espn)
    assert "missing" in gaps and "cricapi only" in gaps["missing"]


def test_l1_does_not_flag_everyone_when_espn_has_no_coverage(wcmod):
    """Noise guard. ESPN absent for the WHOLE match is one match-level fact (handled by the gate),
    not N per-player rows — that would bury the Recon tab."""
    capi = {"a": {"r": 40, "b": 30, "w": 0, "4s": 4, "6s": 0},
            "b": {"r": 12, "b": 9, "w": 0, "4s": 1, "6s": 0},
            "c": {"r": 0, "b": 0, "balls": 24, "w": 2, "4s": 0, "6s": 0}}
    assert wcmod.compute_l1_gaps(capi, {}) == {}
    # ...also when ESPN returns bare placeholders rather than nothing
    placeholders = {k: {"r": 0, "b": 0, "balls": 0, "w": 0, "4s": 0, "6s": 0} for k in capi}
    assert wcmod.compute_l1_gaps(capi, placeholders) == {}


# ── Recon State: the second axis (Phase 1.1) ────────────────────────────────
def test_recon_state_l1_open_when_gaps_unresolved(wcmod):
    assert wcmod.classify_recon_state(False, {"p": "runs 1/2"}, set(), {}, {}) == "L1_OPEN"


def test_recon_state_l1_open_when_data_unconsumed(wcmod):
    # feeds agree, but a bowler had no dots source -> not done, whatever L1 says
    assert wcmod.classify_recon_state(False, {}, {("DS", "X")}, {}, {}) == "L1_OPEN"


def test_recon_state_l1_done_before_cricsheet(wcmod):
    assert wcmod.classify_recon_state(False, {}, set(), {}, {}) == "L1_DONE"


def test_recon_state_l2_pending_on_unapproved_difference(wcmod):
    assert wcmod.classify_recon_state(True, {}, set(), {"ci:1": "dots 9→8"}, {}) == "L2_PENDING"


def test_recon_state_l2_done_when_approved(wcmod):
    assert wcmod.classify_recon_state(True, {}, set(), {"ci:1": "x"}, {"ci:1": "S2"}) == "L2_DONE"


def test_recon_state_l2_done_when_cricsheet_agrees(wcmod):
    assert wcmod.classify_recon_state(True, {}, set(), {}, {}) == "L2_DONE"


def test_recon_state_is_independent_of_match_status(wcmod):
    """The whole point of the second axis: COMPLETED and L2_PENDING coexist.

    One column cannot carry two lifecycles — trying to is what made COMPLETED_FLAGGED mean
    'unverified single feed' OR 'official revision pending' OR 'identity unresolved', with no way
    for the app to tell which."""
    l2 = {"ci:1": "dots 9→8"}
    assert wcmod.classify_recon_state(True, {}, set(), l2, {}) == "L2_PENDING"
    assert wcmod.classify_match_status(True, True, {}, {}, True)[0] == "COMPLETED_FLAGGED"
    # ...and an L1-open match is LIVE on BOTH axes
    assert wcmod.classify_recon_state(False, {"p": "g"}, set(), {}, {}) == "L1_OPEN"
    assert wcmod.classify_match_status(False, True, {"p": "g"}, {"p": "g"}, False)[0] == "LIVE"


def test_every_recon_state_has_a_label(wcmod):
    for st in ("L1_OPEN", "L1_DONE", "L2_PENDING", "L2_DONE"):
        assert st in wcmod.RECON_STATE_LABEL and wcmod.RECON_STATE_LABEL[st]


# ── Points delta ────────────────────────────────────────────────────────────
def test_points_delta_signed_and_blank_when_unmoved(wcmod, perf):
    wcmod.SETTLEMENTS[("MKD", "ci:9")] = {"points": 80}
    try:
        assert wcmod._points_delta("MKD", "ci:9", 72) == "-8"
        assert wcmod._points_delta("MKD", "ci:9", 145) == "+65"
        assert wcmod._points_delta("MKD", "ci:9", 80) == ""      # unmoved -> quiet
        assert wcmod._points_delta("MKD", "ci:nobody", 50) == ""  # never settled -> quiet
    finally:
        wcmod.SETTLEMENTS.pop(("MKD", "ci:9"), None)
