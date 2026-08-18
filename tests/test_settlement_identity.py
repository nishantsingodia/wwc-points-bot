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
    """The backstop must catch a total that moves even when every LISTED field agrees.

    This used to demonstrate the hole with `b` (balls faced), which was genuinely absent from
    RECON_L2 — so recon_gaps was blind while the strike-rate component moved. That hole is now
    closed: RECON_L2 covers all 14 scoring fields, so the owner gets three levels of recon on every
    parameter a point is awarded on, and cricsheet can overrule an L1 answer on any of them.

    The backstop still matters, because it is the guard against the NEXT field somebody forgets to
    list. Demonstrated here with a field deliberately excluded from the comparison list rather than
    one missing from RECON_L2 — the property under test is "the total is checked independently of
    the field list", not "this particular field is unlisted".
    """
    before = wcmod.blank_perf("X")
    before.update(played=True, r=30, b=15, dismissed=True)
    after = dict(before, b=30)
    partial = [f for f in wcmod.RECON_L2 if f != "b"]        # pretend someone forgot to list it
    assert wcmod.recon_gaps(before, after, partial, sep="\u2192") == ""   # blind, as it would be
    assert wcmod.points_gap(before, after, "BAT", sep="\u2192") != ""     # the backstop still sees it
    # and with the real list, the field itself is now compared too
    assert wcmod.recon_gaps(before, after, wcmod.RECON_L2, sep="\u2192") == "faced 15\u219230"

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


# ── 6. The identity loop actually closes (the "why isn't this automatic?" gap) ──
def test_promote_new_players_applies_a_filled_bridge(wcmod, monkeypatch, tmp_path):
    """A sheet-added player is minted with a placeholder `slug:` pid, and build_registry never
    looks at new_players.json — so before this, filling their cricinfo id changed nothing and the
    slug survived forever. Promotion is what turns a filled id into a real anchor."""
    bridges = tmp_path / "b.json"
    bridges.write_text('{"ci:859899": {"cricinfo_id": "859899", '
                       '"names": ["calvin harrison", "cg harrison"]}}')
    monkeypatch.setattr(wcmod, "CI_BRIDGES_PATH", str(bridges))
    monkeypatch.setattr(wcmod, "NEW_PLAYERS_PATH", str(tmp_path / "np.json"))
    monkeypatch.setattr(wcmod, "NEW_PLAYERS_DATA", {"players": [
        {"pid": "slug:calvin-harrison", "display": "Calvin Harrison",
         "aliases": ["calvin harrison"], "team": "TR", "tours": ["T"], "source": "new"}]})
    monkeypatch.setattr(wcmod, "NEEDS_CRICINFO", [])
    assert wcmod.promote_new_players() == 1
    assert wcmod.NEW_PLAYERS_DATA["players"][0]["pid"] == "ci:859899"
    assert wcmod.resolve_pid("Calvin Harrison") == "ci:859899"
    assert wcmod.NEEDS_CRICINFO == []          # resolved -> nothing left to ask


def test_promote_never_guesses_between_two_ids(wcmod, monkeypatch, tmp_path):
    """Two bridges claiming one player is ambiguous. Leave the placeholder and say so — picking
    one would fuse two people, which is the exact failure this work exists to prevent."""
    bridges = tmp_path / "b.json"
    bridges.write_text('{"ci:1": {"cricinfo_id": "1", "names": ["x player"]},'
                       ' "ci:2": {"cricinfo_id": "2", "names": ["x alias"]}}')
    monkeypatch.setattr(wcmod, "CI_BRIDGES_PATH", str(bridges))
    monkeypatch.setattr(wcmod, "NEW_PLAYERS_PATH", str(tmp_path / "np.json"))
    monkeypatch.setattr(wcmod, "NEW_PLAYERS_DATA", {"players": [
        {"pid": "slug:x-player", "display": "X Player", "aliases": ["x alias"],
         "team": "T", "tours": ["T"], "source": "new"}]})
    monkeypatch.setattr(wcmod, "NEEDS_CRICINFO", [])
    assert wcmod.promote_new_players() == 0
    assert wcmod.NEW_PLAYERS_DATA["players"][0]["pid"] == "slug:x-player"


def test_unpromoted_placeholders_are_surfaced_for_a_human(wcmod, monkeypatch, tmp_path):
    """A placeholder with no bridge must reach the Needs Cricinfo ID tab, or it stays invisible
    until it silently breaks a scorecard."""
    b = tmp_path / "b.json"; b.write_text("{}")
    monkeypatch.setattr(wcmod, "CI_BRIDGES_PATH", str(b))
    monkeypatch.setattr(wcmod, "NEW_PLAYERS_PATH", str(tmp_path / "np.json"))
    monkeypatch.setattr(wcmod, "NEW_PLAYERS_DATA", {"players": [
        {"pid": "slug:nobody", "display": "Nobody", "aliases": [], "team": "T",
         "tours": ["Tour X"], "source": "new"}]})
    monkeypatch.setattr(wcmod, "NEEDS_CRICINFO", [])
    wcmod.promote_new_players()
    assert [r["current_pid"] for r in wcmod.NEEDS_CRICINFO] == ["slug:nobody"]


# ── 7. The merge guard must admit full-form names without admitting namesakes ──
import pytest as _pytest

@_pytest.mark.parametrize("a,b,expected", [
    # Sri Lankan full form: village/family prefix first, announced name buried mid-string.
    # cricsheet says 'M Shiraz' = cricinfo 801817 = 'Mohommed Shiraz' — a correct human answer
    # that the first-token check refused until long_form_plausible existed.
    ("Mohommed Shiraz", "Katupulle Gedara Mohamed Shiraz Sahab", True),
    ("Kusal Mendis", "Balapuwaduge Kusal Gimhan Mendis", True),
    # ...but a shared surname alone is NOT enough. These are the merges that cost real points.
    ("Dale Phillips", "Glenn Phillips", False),
    ("Tharindu Rathnayake", "Milan Priyanath Rathnayake", False),
    ("Liam Dawson", "Kiran Carlson", False),
    ("Wanindu Hasaranga", "PWH de Silva", False),
])
def test_long_form_plausible(wcmod, a, b, expected):
    assert wcmod.long_form_plausible(a, b) is expected
    assert wcmod.long_form_plausible(b, a) is expected      # symmetric


def test_s1_is_a_decision_not_an_open_item(wcmod):
    """Choosing S1 means 'I saw cricsheet's revision and rejected it' — the value is held, but the
    match must stop nagging. Treating S1 as unresolved left it COMPLETED_FLAGGED with no way to
    clear it, which teaches you to ignore the flag."""
    l2_pairs = {"p1": "ct 0→1"}
    assert wcmod.player_recon_markers({}, l2_pairs, {"p1": "S1"}) == {}   # decided
    assert wcmod.player_recon_markers({}, l2_pairs, {"p1": "S2"}) == {}   # decided
    assert wcmod.player_recon_markers({}, l2_pairs, {}) == {"p1": "⚠ official revision"}


# ── Field-level settlement baseline (Phase 1.3) ─────────────────────────────
# The points-only record could not serve as the L2 baseline — you cannot diff a field against an
# int — which is exactly why L2 fell back to RECOMPUTING the provisional cut, and that
# recomputation invented the phantom `dots 0→N` revisions and wrote them over settled points.
import pytest


@pytest.fixture(autouse=True)
def _clean_settlements(wcmod):
    saved = dict(wcmod.SETTLEMENTS)
    wcmod.SETTLEMENTS.clear()
    yield
    wcmod.SETTLEMENTS.clear()
    wcmod.SETTLEMENTS.update(saved)


def test_settlement_freezes_reconciled_fields(wcmod, perf):
    d = perf("A", r=56, b=38, played=True, balls=24, dots=9, runs_conceded=30, w=2, **{"4s": 8})
    wcmod.record_settlement("MK", "T", "M1", "2026-08-07", "DS", "ci:1", "A",
                            118, "COMPLETED", "src", perf=d,
                            field_sources={"r": "S2", "w": "Manual"})
    base = wcmod.settled_baseline("MK", "ci:1")
    assert base["r"] == 56 and base["dots"] == 9 and base["w"] == 2
    assert base["b"] == 38 and base["balls"] == 24        # SR/econ inputs, or the total can move
    rec = wcmod.SETTLEMENTS[("MK", "ci:1")]
    assert rec["field_sources"] == {"r": "S2", "w": "Manual"}
    assert rec["points"] == 118


def test_settlement_is_still_write_once(wcmod, perf):
    a = perf("A", r=56, played=True)
    wcmod.record_settlement("MK", "T", "M1", "d", "DS", "ci:1", "A", 60, "COMPLETED", "s", perf=a)
    b = perf("A", r=999, played=True)
    wcmod.record_settlement("MK", "T", "M1", "d", "DS", "ci:1", "A", 999, "COMPLETED", "s", perf=b)
    assert wcmod.settled_baseline("MK", "ci:1")["r"] == 56    # first write wins, forever
    assert wcmod.SETTLEMENTS[("MK", "ci:1")]["points"] == 60


def test_legacy_points_only_row_has_no_field_baseline(wcmod):
    # Rows frozen before field-level freezing must stay valid for the total-level audit.
    wcmod.record_settlement("MK", "T", "M1", "d", "DS", "ci:1", "A", 60, "COMPLETED", "s")
    assert wcmod.settled_baseline("MK", "ci:1") is None
    assert wcmod.SETTLEMENTS[("MK", "ci:1")]["points"] == 60


def test_settled_baseline_absent_for_unknown_player(wcmod):
    assert wcmod.settled_baseline("nope", "ci:999") is None


def test_override_sources_are_captured(wcmod, perf):
    p = perf("A", r=38, b=26, played=True, **{"4s": 5})
    capi = {"x": {"r": 38, "w": 0, "4s": 5, "6s": 0}}
    espn = {"x": {"r": 57, "w": 0, "4s": 8, "6s": 0}}
    l1 = wcmod.compute_l1_gaps(capi, espn)
    idx = {"M": [{"match_key": "M", "scope": "player", "pid": "x", "field": "r",
                  "source": "S2", "status": "approved"}]}
    srcs = {}
    wcmod.apply_recon_overrides({"x": p}, capi, espn, l1, "M", idx, sources_out=srcs)
    assert p["r"] == 57
    assert srcs == {"x": {"r": "S2"}}


# ── L2 reads the frozen baseline (Phase 1.4) ────────────────────────────────
def test_frozen_baseline_makes_l2_silent_where_recompute_lied(wcmod, perf):
    """The phantom `dots 0→N` regression, at the level the fix actually lives.

    The published row carried dots=9 (ESPN, accepted at L1 — nothing else supplies dots).
    cricsheet later confirms 9. Comparing against the FROZEN value is silent, which is correct.
    Comparing against a recomputation that lost the ESPN row yields 'dots 0→9' — a revision of a
    number that was never on screen, which the hold then wrote over cricsheet's correct figure."""
    published = perf("Bowler", played=True, balls=24, runs_conceded=30, w=2, dots=9)
    wcmod.record_settlement("MK", "T", "M1", "d", "DS", "ci:1", "Bowler",
                            96, "COMPLETED", "cricapi + ESPN dots/XI", perf=published)
    official = dict(published)                       # cricsheet agrees

    frozen = wcmod.settled_baseline("MK", "ci:1")
    assert wcmod.recon_gaps(frozen, official, wcmod.RECON_L2, sep="→") == ""     # silent ✓

    broken_recompute = dict(published); broken_recompute["dots"] = 0             # lost ESPN row
    assert "dots 0→9" in wcmod.recon_gaps(broken_recompute, official,
                                          wcmod.RECON_L2, sep="→")               # the old bug


def test_frozen_baseline_preserves_a_manual_approval(wcmod, perf):
    """Why the baseline is the RECONCILED value, not any feed's raw value.

    The owner typed 50 by hand (neither cricapi's 38 nor ESPN's 57). If L2 compared against
    'ESPN's value' the approval would be silently discarded and 50→57 would surface as a bogus
    revision every run."""
    published = perf("Bat", played=True, r=50, b=30, **{"4s": 8})
    wcmod.record_settlement("MK", "T", "M1", "d", "DS", "ci:2", "Bat",
                            70, "COMPLETED", "src", perf=published,
                            field_sources={"r": "Manual"})
    frozen = wcmod.settled_baseline("MK", "ci:2")
    assert frozen["r"] == 50
    assert wcmod.SETTLEMENTS[("MK", "ci:2")]["field_sources"]["r"] == "Manual"
    official = dict(published)
    assert wcmod.recon_gaps(frozen, official, wcmod.RECON_L2, sep="→") == ""


def test_frozen_baseline_still_flags_a_genuine_revision(wcmod, perf):
    # The gate must not go blind — a real cricsheet correction still surfaces.
    published = perf("Bowler", played=True, balls=24, runs_conceded=30, w=2, dots=9)
    wcmod.record_settlement("MK", "T", "M1", "d", "DS", "ci:3", "Bowler",
                            96, "COMPLETED", "src", perf=published)
    official = dict(published); official["w"] = 3
    g = wcmod.recon_gaps(wcmod.settled_baseline("MK", "ci:3"), official, wcmod.RECON_L2, sep="→")
    assert "wkts 2→3" in g


# ── 8. A feed with NO data is not a disagreement ─────────────────────────────
def test_cricapi_stub_card_is_not_381_disagreements(wcmod):
    """cricapi returns a STUB for franchise leagues: every player listed, every stat zero. Read as
    observed zeros it becomes one 'disagreement' per player per field — 381 rows across 12
    Hundred/LPL matches asking a human to arbitrate 'runs 0/34'. The ESPN side already had this
    guard; cricapi never did. One feed having nothing is a MATCH-level fact."""
    def perf(**kw):
        p = wcmod.blank_perf(kw.pop("name", "X")); p.update(played=True, **kw); return p
    capi = {"p1": perf(name="Sean Dickson"), "p2": perf(name="Joe Clarke")}       # all-zero stub
    espn = {"p1": perf(name="Sean Dickson", r=34, b=20, **{"4s": 3}),
            "p2": perf(name="Joe Clarke", r=25, b=18, **{"4s": 3})}
    assert wcmod.compute_l1_gaps(capi, espn) == {}

    # ...but a REAL cricapi card that disagrees must still be flagged — the guard must not become
    # a blanket mute. A duck off 2 balls is activity, not a stub.
    capi_real = {"p1": perf(name="Sean Dickson", r=30, b=20, **{"4s": 3})}
    espn_real = {"p1": perf(name="Sean Dickson", r=34, b=20, **{"4s": 3})}
    assert "p1" in wcmod.compute_l1_gaps(capi_real, espn_real)
    duck = {"p1": perf(name="D Uck", r=0, b=2)}
    assert wcmod._perf_has_activity(duck["p1"]) is True


# ── 9. COMPLETED must never return to LIVE ───────────────────────────────────
def test_a_settled_match_is_never_un_published(wcmod):
    """The spec says COMPLETED never returns to LIVE, but nothing enforced it — status was
    recomputed from scratch each run, so a newly-appearing gap silently retracted a settled
    result. On 7-9 Aug that un-published 12 Hundred/LPL matches (410 frozen baseline rows) days
    after they were settled: results vanished from the app and the recon tab filled with hundreds
    of rows for matches nobody thought were open.

    A new gap on a settled match is worth FLAGGING. It is never worth retracting the result."""
    args = dict(cs_path=None, espn_present=True, l1_gaps={"p": "x"}, unresolved={"p": "x"},
                l2_dirty=False)
    # never published before -> LIVE is right, there is nothing to protect
    assert wcmod.classify_match_status(**args)[0] == "LIVE"
    # already published -> flag it, do NOT retract
    st, flag = wcmod.classify_match_status(**args, already_completed=True)
    assert st == "COMPLETED_FLAGGED" and "pending recon approval" in flag

    # same for the unsourced branch, which is the stricter of the two
    u = dict(cs_path=None, espn_present=True, l1_gaps={}, unresolved={}, l2_dirty=False,
             unsourced=("p1",))
    assert wcmod.classify_match_status(**u)[0] == "LIVE"
    assert wcmod.classify_match_status(**u, already_completed=True)[0] == "COMPLETED_FLAGGED"


# ── 10. An empty cricapi card is not a final card ────────────────────────────
def test_empty_scorecard_is_evicted_not_frozen(wcmod, monkeypatch, tmp_path):
    """api() persists on status=="success", but cricapi answers SUCCESS with an EMPTY scorecard
    for franchise leagues it hasn't populated. Once matchEnded flips, that blank is cached as the
    immutable final and cricapi is never asked again — the Hundred had real cricapi cards in July
    and none from 7 Aug for exactly this reason. We froze a blank and stopped listening."""
    monkeypatch.setattr(wcmod, "CACHE", str(tmp_path))
    fp = wcmod._cache_file("match_scorecard", {"id": "m1"})
    open(fp, "w").write('{"status":"success","data":{}}')
    assert wcmod.evict_empty_scorecard("m1") is True
    import os
    assert not os.path.exists(fp)                 # gone -> next run re-asks
    assert wcmod.evict_empty_scorecard("m1") is False   # idempotent, no crash when absent


def test_single_feed_is_flagged_whichever_feed_is_missing(wcmod):
    """'ESPN absent' was flagged 'unverified — single feed'; 'cricapi absent' published as plain
    COMPLETED, indistinguishable from a two-feed-agreed match. Same one-sided-guard disease. A
    single-sourced number must never look verified."""
    base = dict(cs_path=None, l1_gaps={}, unresolved={}, l2_dirty=False)
    st, flag = wcmod.classify_match_status(espn_present=False, capi_present=True, **base)
    assert st == "COMPLETED_FLAGGED" and "cricapi only" in flag
    st, flag = wcmod.classify_match_status(espn_present=True, capi_present=False, **base)
    assert st == "COMPLETED_FLAGGED" and "ESPN only" in flag
    # both present and clean -> genuinely COMPLETED
    assert wcmod.classify_match_status(espn_present=True, capi_present=True, **base) == ("COMPLETED", "")


# ── 11. api() must actually be able to build a URL ───────────────────────────
def test_api_builds_a_url_with_its_params(wcmod, monkeypatch, tmp_path):
    """A refactor that extracted the cache path deleted the line building `qs` — which api() still
    used for the URL. Every cricapi call raised NameError for an hour, run_tour caught it per-tour,
    and the workflow reported SUCCESS while all three live tours did nothing. No existing test
    touched api(), because none of them make a request.

    This one does, with urlopen faked: it fails loudly if the URL can't be assembled."""
    import io, urllib.request
    monkeypatch.setattr(wcmod, "CACHE", str(tmp_path))
    monkeypatch.setattr(wcmod, "API_KEYS", ["TESTKEY"])
    monkeypatch.setattr(wcmod, "_key_idx", 0)
    monkeypatch.setattr(wcmod, "TICK_CACHE_ONLY", False)
    seen = {}

    class _Resp:
        def __enter__(self): return io.BytesIO(b'{"status":"success","data":[]}')
        def __exit__(self, *a): return False

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda url, timeout=None: (seen.__setitem__("url", url), _Resp())[1])
    d = wcmod.api("series_info", cache=False, persist=False, id="abc123")
    assert d.get("status") == "success"
    assert "series_info" in seen["url"] and "id=abc123" in seen["url"]
    assert "apikey=TESTKEY" in seen["url"]


# ── 12. ESPN run-outs: read them from `summary`, not `playbyplay` ────────────
def test_espn_runouts_parses_fielders_from_summary(wcmod, monkeypatch):
    """playbyplay's dismissal.fielder is ALWAYS empty for a run out, and neither shortText nor
    text names the fielders — measured over 24 LPL events: 19 run-out items, 0 with a fielder, 0
    parseable. Caught worked (214/214), so the gap was invisible: run-outs scored ZERO for
    everyone, ~1.1/match at 6 pts assisted / 12 direct. Verified after the fix against cricsheet
    over 18 LPL matches: 20 v 20, agreement 18/18.

    `summary` carries it structured, and is already fetched under the same cache key."""
    payload = {"rosters": [{"roster": [
        {"linescores": [{"statistics": {"batting": {"outDetails": {
            "dismissalCard": "run out",
            "fielders": [{"athlete": {"id": "704693", "fullName": "Lahiru Udara"}},
                         {"athlete": {"id": "955235", "fullName": "Nuwan Thushara"}}]}}}}]},
        {"linescores": [{"statistics": {"batting": {"outDetails": {
            "dismissalCard": "run out",
            "fielders": [{"athlete": {"id": "999", "fullName": "Solo Fielder"}}]}}}}]},
        {"linescores": [{"statistics": {"batting": {"outDetails": {
            "dismissalCard": "caught",       # must be ignored — caught is handled elsewhere
            "fielders": [{"athlete": {"id": "111", "fullName": "Someone Else"}}]}}}}]},
    ]}]}
    monkeypatch.setattr(wcmod, "espn_get", lambda *a, **k: payload)
    outs = wcmod.espn_runouts("evt")
    assert len(outs) == 2                                  # the caught row is not a run out
    assert [n for n, _ in outs[0]["fielders"]] == ["Lahiru Udara", "Nuwan Thushara"]
    assert outs[0]["fielders"][0][1] == "704693"           # athlete.id IS the cricinfo id
    assert len(outs[1]["fielders"]) == 1                   # -> scores dro (direct hit)


def test_espn_runouts_tolerates_a_missing_fielder_block(wcmod, monkeypatch):
    """A run out with no fielder block must yield nothing rather than a phantom credit."""
    monkeypatch.setattr(wcmod, "espn_get", lambda *a, **k: {"rosters": [{"roster": [
        {"linescores": [{"statistics": {"batting": {"outDetails": {
            "dismissalCard": "run out", "fielders": []}}}}]}]}]})
    assert wcmod.espn_runouts("evt") == []
    monkeypatch.setattr(wcmod, "espn_get", lambda *a, **k: {})
    assert wcmod.espn_runouts("evt") == []


# ── 13. A partial ESPN fetch must never be scored ────────────────────────────
def test_parse_espn_refuses_a_failed_page(wcmod, monkeypatch):
    """espn_get returns {} on ANY failure (502, timeout, WAF). Before this guard the pagination
    loop simply appended nothing and ended, producing a TRUNCATED innings that scored as complete.
    Measured live: the Hundred Women's sample came back 197 balls / 221 runs / 80 dots short of
    cricsheet — every field down ~14% — purely because one event 502'd. Wrong, silent, and it
    looks complete. Returning an empty perf makes the caller treat ESPN as unavailable, so the
    match retries instead of publishing."""
    monkeypatch.setattr(wcmod, "espn_get", lambda *a, **k: {})
    assert wcmod.parse_espn("evt") == ({}, False)


def test_parse_espn_refuses_a_short_page_count(wcmod, monkeypatch):
    """ESPN reports `count`; if we assembled fewer deliveries than that, pages went missing."""
    payload = {"commentary": {"pageCount": 1, "count": 250,
                              "items": [{"id": i} for i in range(100)]}}
    monkeypatch.setattr(wcmod, "espn_get", lambda *a, **k: payload)
    monkeypatch.setattr(wcmod, "espn_xi", lambda *a, **k: {})
    monkeypatch.setattr(wcmod, "espn_runouts", lambda *a, **k: [])
    assert wcmod.parse_espn("evt") == ({}, False)


def test_parse_espn_accepts_a_complete_fetch(wcmod, monkeypatch):
    """The guard must not fire on a healthy response, or every match becomes unsourced."""
    payload = {"commentary": {"pageCount": 1, "count": 2,
                              "items": [{"id": 1}, {"id": 2}]}}
    monkeypatch.setattr(wcmod, "espn_get", lambda *a, **k: payload)
    monkeypatch.setattr(wcmod, "espn_xi", lambda *a, **k: {})
    monkeypatch.setattr(wcmod, "espn_runouts", lambda *a, **k: [])
    perf, _ = wcmod.parse_espn("evt")
    assert isinstance(perf, dict)          # scored, not refused
