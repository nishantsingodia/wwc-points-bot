"""Regressions for the 29 Jul 2026 post-mortem: what cricsheet landing did to already-settled
LPL / Hundred matches.

Three distinct holes, all of which let points move on a match the app badged final:
  1. IDENTITY  — cricsheet writes initials form ("PWH de Silva"), the alias table only knew
     "Wanindu Hasaranga", so his squad row read 0 while his 114-point captain innings emitted
     as an unjoinable blank-pid orphan. `l2_pairs` iterates cricsheet pids only, so the gate
     never saw it: LPL Match 6 stayed COMPLETED with an EMPTY flag.
  2. BACKSTOP  — balls faced/bowled aren't in RECON_L2, so a change there moved the SR/econ
     points while the recon column still read "✓ complete" (Dickwella 69 -> 63).
  3. BASELINE  — nothing recorded what a contest was settled on, so a scorer fix (the Hundred
     bowler-balls bug: Gleeson 4 -> 145) changed history invisibly.
"""
import pytest


# ── 1. Identity: resolve cricsheet rows by ID, not by spelling ───────────────
def test_cricsheet_id_resolves_initials_form(wcmod, monkeypatch):
    """The structural fix. cricsheet's own info.registry.people gives name -> person id, and the
    registry stores cricsheet_id per player, so the initials form resolves WITHOUT any alias."""
    # A synthetic initials spelling, so this exercises the ID path even though the three real
    # LPL cases are now ALSO bridged by name (see test_hasaranga_and_mathews_aliases_are_permanent).
    monkeypatch.setitem(wcmod.CS2PID, "zz9test01", "ci:784379")
    monkeypatch.setitem(wcmod.PID2DISP, "ci:784379", "Wanindu Hasaranga")
    # The spelling is unknown to the alias table...
    assert wcmod.resolve_pid("QQX de Testcase") is None
    # ...but the cricsheet id resolves it anyway, and teaches the alias table on the way through.
    assert wcmod.resolve_perf_pid({"name": "QQX de Testcase", "cs_id": "zz9test01"}) == "ci:784379"
    assert wcmod.resolve_pid("QQX de Testcase") == "ci:784379"    # learned, id-anchored
    assert wcmod.CS_LEARNED.get("qqx de testcase") == "ci:784379"


def test_resolve_perf_pid_falls_back_to_name_without_cs_id(wcmod, monkeypatch):
    """cricapi/ESPN carry no cricsheet id — those feeds must still resolve by name."""
    monkeypatch.setitem(wcmod.ALIAS2PID, "some player", "ci:1")
    assert wcmod.resolve_perf_pid({"name": "Some Player"}) == "ci:1"
    assert wcmod.resolve_perf_pid({"name": "Nobody At All"}) is None


def test_unknown_cricsheet_id_does_not_invent_identity(wcmod):
    """An id we don't hold must resolve to NOTHING (-> flagged), never a fabricated pid.
    Mirrors the registry's null-on-ambiguity discipline: no guessing, ever."""
    assert wcmod.resolve_perf_pid({"name": "Q Unknown", "cs_id": "not-an-id"}) is None


def test_parse_cricsheet_stamps_person_ids(wcmod, tmp_path):
    import json
    f = tmp_path / "m.json"
    f.write_text(json.dumps({
        "info": {"dates": ["2026-07-21"], "gender": "male", "teams": ["A", "B"],
                 "players": {"A": ["PWH de Silva"], "B": ["T Mathew"]},
                 "registry": {"people": {"PWH de Silva": "a97c8ec2", "T Mathew": "c13e8df5"}}},
        "innings": []}))
    perf = wcmod.parse_cricsheet(str(f))[0]
    assert perf[wcmod.norm("PWH de Silva")]["cs_id"] == "a97c8ec2"
    assert perf[wcmod.norm("T Mathew")]["cs_id"] == "c13e8df5"


# ── 2. The gate that was blind ───────────────────────────────────────────────
def test_identity_break_detects_zeroed_and_orphan(wcmod):
    prov = {"ci:784379": {"played": True, "r": 40, "w": 2}}
    cs_pid = {}                                     # official card lost him
    orphans = [{"name": "PWH de Silva", "played": True}]
    zeroed, names = wcmod.identity_break(prov, cs_pid, orphans)
    assert zeroed == ["ci:784379"]
    assert names == ["PWH de Silva"]


def test_identity_break_ignores_genuine_non_selection(wcmod):
    """A squad player who never played is not an identity break — no orphan, nothing to link."""
    prov = {"ci:1": {"played": False}}
    assert wcmod.identity_break(prov, {}, []) == ([], [])


def test_lpl_match6_no_longer_completes_clean(wcmod):
    """THE regression. LPL Match 6 (DS v KR, 21 Jul): every compared field agreed, so l2_dirty was
    False and the match published COMPLETED with an empty flag — while Hasaranga (C, 114 pts) read
    0. It must never again be possible to badge that match final and unflagged."""
    st, flag = wcmod.classify_match_status(cs_path="lpl.json", espn_present=True, l1_gaps={},
                                          unresolved={}, l2_dirty=False, id_break=True)
    assert st == "COMPLETED_FLAGGED"
    assert flag and "identity" in flag.lower()
    # and with no identity break the old clean path is untouched
    assert wcmod.classify_match_status("lpl.json", True, {}, {}, False) == ("COMPLETED", "")


# ── 3. Points backstop: a moved total can't hide behind "✓ complete" ─────────
def test_points_gap_catches_change_in_an_uncompared_field(wcmod):
    """balls faced isn't in RECON_L2, so recon_gaps sees nothing — yet the SR component moves.
    30 off 15 = SR 200 (+6); 30 off 30 = SR 100 (no band). Same runs, different points."""
    before = wcmod.blank_perf("X")
    before.update(played=True, r=30, b=15, dismissed=True)
    after = dict(before, b=30)
    assert wcmod.recon_gaps(before, after, wcmod.RECON_L2, sep="→") == ""   # blind, as designed
    assert wcmod.points_gap(before, after, "BAT") == "pts 44→38"            # backstop catches it
    assert wcmod.score(before, "BAT")["sr"] - wcmod.score(after, "BAT")["sr"] == 6  # the SR band


def test_points_gap_silent_when_total_unchanged(wcmod):
    p = wcmod.blank_perf("Y")
    p.update(played=True, r=10, b=10)
    assert wcmod.points_gap(p, dict(p), "BAT") == ""
    assert wcmod.points_gap(p, None, "BAT") == ""        # missing side -> nothing to say


def test_points_gap_never_silently_disables_itself(wcmod):
    """A malformed perf dict must NOT read as clean — a backstop that quietly switches off
    would report '✓ complete' on precisely the players it failed to check."""
    gap = wcmod.points_gap({"garbage": True}, {"garbage": False}, "BAT")
    assert "unverified" in gap


# ── 4. Settlement baseline is WRITE-ONCE ────────────────────────────────────
def test_settlement_is_write_once(wcmod, monkeypatch):
    monkeypatch.setattr(wcmod, "SETTLEMENTS", {})
    monkeypatch.setattr(wcmod, "SETTLE_NEW", 0)
    mk = "2026-07-21::dambulla sixers|kandy roar"
    wcmod.record_settlement(mk, "LPL 2026", "Match 6 — DS v KR", "2026-07-21", "KR",
                            "ci:784379", "Wanindu Hasaranga", 114, "COMPLETED", "cricapi + ESPN")
    # cricsheet lands and re-scores him to 0 — the BASELINE must not budge
    wcmod.record_settlement(mk, "LPL 2026", "Match 6 — DS v KR", "2026-07-21", "KR",
                            "ci:784379", "Wanindu Hasaranga", 0, "COMPLETED", "cricsheet · official")
    rec = wcmod.SETTLEMENTS[(mk, "ci:784379")]
    assert rec["points"] == 114                     # what the contest was settled on
    assert rec["source"].startswith("cricapi")
    assert rec["provenance"] == "live"


def test_settlement_skips_rows_without_a_pid(wcmod, monkeypatch):
    """A blank pid can't be joined by the app, so freezing it would just be noise."""
    monkeypatch.setattr(wcmod, "SETTLEMENTS", {})
    wcmod.record_settlement("mk", "T", "M", "2026-07-21", "KR", "", "PWH de Silva",
                            114, "COMPLETED", "cricsheet")
    assert wcmod.SETTLEMENTS == {}


# ── 5. Registry data integrity: the misattribution that cost real points ─────
def test_kth_ratnayake_is_tharindu_not_milan(wcmod):
    """cricsheet writes Tharindu Rathnayake (Galle Gallants) as 'KTH Ratnayake' (cs 4eb02f2e).
    That alias was attached to MILAN Ratnayake (a Colombo Kaps player, cs afe830a2) — so
    Tharindu's LPL scores (134 / 15 / 23 pts) were credited to a different player in a different
    franchise, and Tharindu's own rows read 0 'not in official XI'. A data regression, so pin it:
    build_registry must never re-smear these two."""
    tharindu = wcmod.resolve_pid("KTH Ratnayake")
    milan = wcmod.resolve_pid("RMMP Rathnayake")
    assert tharindu == wcmod.resolve_pid("Tharindu Rathnayake")
    assert milan == wcmod.resolve_pid("Milan Ratnayake")
    assert tharindu != milan, "two different people must not share an id"
    assert wcmod.PID2DISP[tharindu] == "Tharindu Rathnayake"
    assert wcmod.PID2DISP[milan] == "Milan Ratnayake"


def test_hasaranga_and_mathews_aliases_are_permanent(wcmod):
    """The three LPL breaks, pinned as registry data (id-verified via cricsheet registry.people).
    The cs-id path resolves them anyway, but the alias makes them resolvable from ANY feed."""
    assert wcmod.resolve_pid("PWH de Silva") == wcmod.resolve_pid("Wanindu Hasaranga")
    assert wcmod.resolve_pid("T Mathew") == wcmod.resolve_pid("Traveen Mathews")
    assert wcmod.resolve_pid("RMMP Rathnayake") == wcmod.resolve_pid("Milan Ratnayake")


def test_cricinfo_hint_makes_the_gap_fillable(wcmod, monkeypatch):
    """An identity gap is only actionable if it names the id to fill. The hint is derived from the
    people.csv crosswalk (cricsheet id -> cricinfo id), never guessed from the spelling."""
    monkeypatch.setitem(wcmod.CS2CI, "f655d740", "859899")
    h = wcmod.cricinfo_hint({"name": "CG Harrison", "cs_id": "f655d740"})
    assert "859899" in h and "cricketers/x-859899" in h
    # unknown id -> say so plainly rather than inventing one
    assert "zzzz" in wcmod.cricinfo_hint({"name": "X", "cs_id": "zzzz"})
    assert wcmod.cricinfo_hint({"name": "X"}) == "no cricsheet id"


def test_unresolved_official_flags_blank_pid_rows(wcmod, monkeypatch):
    monkeypatch.setitem(wcmod.ALIAS2PID, "known player", "ci:9")
    cs_perf = {"known player": {"name": "Known Player", "played": True},
               "q unknown": {"name": "Q Unknown", "played": True},
               "did not play": {"name": "Did Not Play", "played": False}}
    out = wcmod.unresolved_official(cs_perf)
    assert [v["name"] for v in out] == ["Q Unknown"]     # played + unresolvable only
