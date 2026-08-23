"""Cricbuzz wired in as the L1 SECOND WITNESS — the integration, pinned on real cached payloads.

ONE match, BOTH feeds, no network:
  Cricbuzz cb157138   tests/fixtures/cricbuzz/      (scorecard flight page + both innings mcenter)
  ESPN     ev1537349  tests/fixtures/espn_l1/       (trimmed playbyplay + summary, real numbers)
They are the same LPL 2026 fixture (19th Match, 1 Aug 2026). The bridge that joins them is the
committed registry/cricbuzz_bridge.json, DERIVED from performance fingerprints and the dismissal
join — never from names.

WHAT THIS FILE DEFENDS, in order of how much it would cost to get wrong:

1. THE PID SPACES ARE NOT THE SAME. `ci:<bridged cricinfo id>` is not the key ESPN's own row got.
   On this very pair of matches ESPN athlete.id 1364327 resolves to ci:784375, because 1364327 is
   not a registry key and the registry carries that spelling under a different cricinfo id
   (people.csv has both). Keying Cricbuzz on ci:<id> put one human under two pids and posted him
   to the Recon tab as "present in cricbuzz only" — an IDENTITY artifact in the VALUE tab, which
   CLAUDE.md rule E forbids. The join must go through the pid ESPN's row already carries.
2. AN ABSENCE IS NOT A ZERO. Cricbuzz writes None (never 0) for a field it could not establish —
   `maidens` on The Hundred is hard-ignored as corrupt (a verbatim copy of `dots` on 13/13
   bowlers). Compared as 0 it would raise a phantom row per bowler and, via an approved
   whole-match S1 seed, write a fabricated 0 over a real figure.
3. FAIL SAFE. Every Cricbuzz failure mode must leave the tour scoring off ESPN and SAY SO.
4. NO FLOOD. Widening L1 from 4 fields to 14 must not manufacture rows: measured 0 disagreements
   across 48 player-rows on the two matches with cached payloads.
"""
import json
import os

import pytest

import cricbuzz as cb
from registry import cricbuzz_bridge as brg


CB_FIX = os.path.join(os.path.dirname(__file__), "fixtures", "cricbuzz")
ESPN_FIX = os.path.join(os.path.dirname(__file__), "fixtures", "espn_l1")
LPL_CB, LPL_EV = 157138, "1537349"
HUNDRED_CB = 144893


@pytest.fixture
def cb_cache(monkeypatch):
    """Point cricbuzz's disk cache at the committed fixtures so nothing dials out."""
    monkeypatch.setattr(cb, "CACHE", CB_FIX)


@pytest.fixture
def espn_fixture(wcmod, monkeypatch):
    """Serve the real trimmed ESPN payloads to every espn_get call parse_espn makes."""
    with open(os.path.join(ESPN_FIX, f"espn_{LPL_EV}_playbyplay.json")) as fh:
        pbp = json.load(fh)
    with open(os.path.join(ESPN_FIX, f"espn_{LPL_EV}_summary.json")) as fh:
        summary = json.load(fh)
    monkeypatch.setattr(wcmod, "espn_get",
                        lambda path, cache=True, **kw:
                        pbp if path == "playbyplay" else summary if path == "summary" else {})
    perf, _ = wcmod.parse_espn(LPL_EV)
    return {k: v for k, v in perf.items() if v["played"]}


@pytest.fixture
def witness(wcmod, monkeypatch, cb_cache):
    """Cricbuzz active as this tour's witness, with the match id already resolved.

    resolve_match_id is stubbed — not because it is unreliable (measured 22/22 unique, 0 collisions
    on LPL 2026) but because it reads a 276KB series page we deliberately do not commit. Its own
    behaviour is covered in tests/test_cricbuzz.py.
    """
    monkeypatch.setattr(wcmod, "CB_SERIES", "12316")
    monkeypatch.setattr(wcmod, "CB_STORE", brg.load_store())
    monkeypatch.setattr(cb, "resolve_match_id", lambda *a, **k: LPL_CB)
    return wcmod


# ── 1. the join is to the pid ESPN's own row got ──────────────────────────────────────────────

def test_cricbuzz_rows_land_on_the_pid_espn_already_resolved(witness, espn_fixture):
    """Not ci:<bridged id> — the pid resolve_perf_pid gave ESPN's row for the same athlete.

    Both sides of a comparison have to be the same key or the union in compute_l1_gaps reports the
    same person twice, once per feed, as two one-sided 'gaps'."""
    wc = witness
    cb_pid, note, diag = wc.cb_match_perf("2026-08-01", ["Colombo Kaps", "Galle Gallants"],
                                          espn_fixture)
    assert cb_pid, f"cricbuzz produced no witness view: {note}"
    espn_pid = wc._by_pid(espn_fixture)
    assert set(cb_pid) <= set(espn_pid), (
        "cricbuzz rows landed on pids ESPN's own rows did not use — every one of those becomes a "
        "phantom 'present in cricbuzz only' row: " + str(sorted(set(cb_pid) - set(espn_pid))))
    assert diag["bridged"] == 24 and diag["unbridged"] == 0


def test_the_duplicate_cricinfo_id_case_does_not_split_the_player(witness, espn_fixture):
    """ESPN athlete.id 1364327 resolves to ci:784375 (people.csv carries BOTH ids for the spelling).
    Keying cricbuzz on ci:1364327 is what produced the phantom row this test exists to stop."""
    wc = witness
    ids = {str(v.get("espn_id")): wc.resolve_perf_pid(v) for v in espn_fixture.values()}
    split = {eid: pid for eid, pid in ids.items() if pid and pid != f"ci:{eid}"}
    # If ESPN's own ids and the registry ever agree completely this assertion is vacuous, not
    # wrong — so it only asserts the INVARIANT, on whatever splits the data actually contains.
    cb_pid, _, _ = wc.cb_match_perf("2026-08-01", ["Colombo Kaps", "Galle Gallants"], espn_fixture)
    for eid, pid in split.items():
        assert f"ci:{eid}" not in cb_pid, (
            f"cricbuzz keyed athlete {eid} as ci:{eid} while ESPN's row for the same person is on "
            f"{pid} — one human, two pids")


# ── 2. the whole point: a real two-feed comparison that does not flood ────────────────────────

def test_fourteen_field_comparison_is_clean_on_a_real_match(witness, espn_fixture):
    """MEASURED: 48/48 player-rows agree on all 14 fields across cb157138+cb157061. Widening L1
    from cricapi's 4 fields to Cricbuzz's 14 must add cross-checking, not Recon noise."""
    wc = witness
    cb_pid, _, _ = wc.cb_match_perf("2026-08-01", ["Colombo Kaps", "Galle Gallants"], espn_fixture)
    espn_pid = wc._by_pid(espn_fixture)
    gaps = wc.compute_l1_gaps(cb_pid, espn_pid, fields=wc.RECON_L1, witness="cricbuzz")
    assert gaps == {}, f"cricbuzz and ESPN disagreed on a match measured exact: {gaps}"
    rows = wc.build_recon_rows("mk", "lbl", "2026-08-01", "LPL", gaps, cb_pid, espn_pid,
                               fields=wc.RECON_L1, witness="cricbuzz")
    assert rows == []


def test_the_fields_cricapi_could_never_check_are_actually_compared(witness, espn_fixture):
    """A comparison that silently compares nothing looks identical to a clean one. Perturb each
    field beyond cricapi's old four and assert every single one raises a row.

    INVARIANT UPDATED with the cricapi removal: these ten fields USED to be absent from RECON_L1
    (which was cricapi's ["r","w","4s","6s"] — all cricapi could carry). Cricbuzz witnesses the
    whole card, so RECON_L1 is now the 14-field set and every one of them MUST be in it. The
    assertion therefore flips direction: presence in RECON_L1 is what proves the widening is
    real, and the perturbation below proves the comparison actually reads the field."""
    wc = witness
    cb_pid, _, _ = wc.cb_match_perf("2026-08-01", ["Colombo Kaps", "Galle Gallants"], espn_fixture)
    espn_pid = wc._by_pid(espn_fixture)
    for field in ("dots", "maidens", "runs_conceded", "balls", "b", "catches", "stumpings",
                  "runouts", "dro", "lbwb"):
        assert field in wc.RECON_L1, (
            f"{field} is not in the widened L1 set — cricbuzz witnesses it and it is no "
            "longer cross-checked at L1")
        pid = next(p for p, v in cb_pid.items() if v.get(field) is not None)
        poisoned = {p: (dict(v) if p != pid else dict(v, **{field: (v.get(field) or 0) + 7}))
                    for p, v in cb_pid.items()}
        gaps = wc.compute_l1_gaps(poisoned, espn_pid, fields=wc.RECON_L1, witness="cricbuzz")
        assert pid in gaps, f"a 7-unit error in `{field}` was not detected — the field is unchecked"


# ── 3. an absence is never a value ───────────────────────────────────────────────────────────

def test_the_hundred_maidens_are_absent_and_never_compared(wcmod, cb_cache):
    """CB's HUN `maidens` is a verbatim copy of `dots` (13/13 bowlers on cb144893, sum 68, against
    cricsheet's 0). cricbuzz.py returns None; the L1 layer must SKIP it, not read it as 0."""
    m = cb.parse_match(HUNDRED_CB)
    bowlers = [p for p in m.perf.values() if p["bowled"]]
    assert bowlers and all(p["maidens"] is None for p in bowlers), (
        "a HUN bowler carries a maidens VALUE — at +12 a maiden that is ~816 fabricated points")
    assert wcmod._l1_field_material("maidens", None, 3) is False
    assert wcmod._l1_field_material("maidens", 0, 3) is True    # a real 0 still disagrees
    assert wcmod.recon_gaps({"maidens": None}, {"maidens": 3}, ["maidens"]) == ""
    assert wcmod._l1_pair_gaps({"maidens": None}, {"maidens": 3}, ["maidens"]) == []


def test_a_whole_match_s1_seed_never_writes_an_absent_value(wcmod):
    """'Use S1 for this whole match' expands to every L1 field. An absent cricbuzz `maidens` must
    be left alone — writing 0 there would zero a real ESPN figure on the owner's own approval."""
    wit = {"p": {"r": 40, "maidens": None, "dots": 9}}
    espn = {"p": {"r": 41, "maidens": 2, "dots": 9}}
    scored = {"p": {"r": 41, "maidens": 2, "dots": 9}}
    idx = {"M": [{"scope": "match", "source": "S1", "status": "approved"}]}
    wcmod.apply_recon_overrides(scored, wit, espn, {"p": "runs 40/41"}, "M", idx,
                                fields=wcmod.RECON_L1)
    assert scored["p"]["r"] == 40, "the approved S1 value was not applied"
    assert scored["p"]["maidens"] == 2, (
        "an ABSENT cricbuzz maidens overwrote ESPN's real figure with 0")


# ── 4. identity never reaches the Recon tab ──────────────────────────────────────────────────

def test_an_unbridged_cricbuzz_player_never_enters_the_witness_view(witness, espn_fixture):
    """The bridge is the only admission route. An unresolvable cricbuzz id is an identity gap, and
    a value tab cannot answer 'who is this?'."""
    wc = witness
    # With an EMPTY bridge nothing is admitted at all -> no witness view, and it says so out loud
    # rather than returning {} (which would read as "cricbuzz saw nobody play").
    wc.CB_STORE = {"_schema": 1, "bridge": {}, "revoked": {}}
    cb_pid, note, diag = wc.cb_match_perf("2026-08-01", ["Colombo Kaps", "Galle Gallants"],
                                          espn_fixture)
    assert cb_pid is None and "cannot witness" in note
    assert diag["bridged"] == 0 and diag["unbridged"] == diag["cb_players"]


def test_the_run_out_only_fielder_goes_to_needs_cricinfo_id(witness, espn_fixture, monkeypatch):
    """The measured residual no layer can bridge (0.06/match): a substitute whose ONLY contribution
    is fielding a run out. ESPN populates dismissal.fielder 0/19 for run outs, so nobody else will
    ever raise him — he belongs in the identity tab, with his cricbuzz id."""
    wc = witness
    monkeypatch.setattr(wc, "NEEDS_CRICINFO", [])
    wc.CB_STORE = {"_schema": 1, "bridge": {}, "revoked": {}}
    wc.cb_match_perf("2026-08-01", ["Colombo Kaps", "Galle Gallants"], espn_fixture)
    # (resolve_perf_pid also queues ESPN players who are in no squad — a different, pre-existing
    # class. Only the cb:-keyed rows are cricbuzz's.)
    rows = [r for r in wc.NEEDS_CRICINFO if r["current_pid"].startswith("cb:")]
    assert rows, "an unbridged fielder produced no identity row anywhere"
    assert any("ro " in r["closest_guess"] or "ct " in r["closest_guess"] for r in rows)
    assert not any(r.get("param") for r in rows), "an identity row leaked a Recon param"


def test_espn_only_players_are_not_reported_when_the_witness_is_cricbuzz(wcmod):
    """'ESPN has a row, the witness does not' is an identity fact for Cricbuzz (he was not bridged),
    and cold-start bridge coverage is 0% on a season's first two matches — reporting it would post
    22 unanswerable rows per match. The MIRROR direction stays: cricbuzz seeing a performance ESPN
    missed is a real value fact about ESPN."""
    wit = {"a": {"r": 30, "b": 20}}
    espn = {"a": {"r": 30, "b": 20}, "b": {"r": 44, "b": 25}}
    assert wcmod.compute_l1_gaps(wit, espn, witness="cricbuzz") == {}
    # cricapi keeps the old behaviour exactly
    g = wcmod.compute_l1_gaps(wit, espn, witness="cricapi")
    assert "present in ESPN only" in g["b"]
    # and the mirror is reported for BOTH witnesses
    g2 = wcmod.compute_l1_gaps({"a": {"r": 30, "b": 20}, "c": {"r": 12, "b": 9}},
                               {"a": {"r": 30, "b": 20}}, witness="cricbuzz")
    assert "present in cricbuzz only" in g2["c"]


# ── 5. fail safe: cricbuzz can never take a match away ────────────────────────────────────────

@pytest.mark.parametrize("boom,expect", [
    (cb.CricbuzzUnavailable("HTTP 403"), "cricbuzz match"),
    (cb.CricbuzzNoContent("HTTP 204 with an EMPTY body"), "cricbuzz match"),
    (cb.CricbuzzNoScorecard("no scoreCard yet"), "cricbuzz match"),
    (cb.CricbuzzParseError("RSC prop renamed"), "cricbuzz match"),
])
def test_every_cricbuzz_failure_degrades_to_espn_and_says_so(witness, espn_fixture, monkeypatch,
                                                             boom, expect):
    wc = witness

    def explode(*a, **k):
        raise boom
    monkeypatch.setattr(cb, "parse_match", explode)
    cb_pid, note, _ = wc.cb_match_perf("2026-08-01", ["Colombo Kaps", "Galle Gallants"],
                                       espn_fixture)
    assert cb_pid is None, "a cricbuzz failure returned a perf dict — {} would read as 'nobody played'"
    assert expect in note and str(boom).split(" ")[0] in note


def test_an_unresolvable_match_id_is_never_guessed(witness, espn_fixture, monkeypatch):
    """resolve_match_id returns None on 0 hits or a same-day double-header of the same two sides."""
    monkeypatch.setattr(cb, "resolve_match_id", lambda *a, **k: None)
    cb_pid, note, _ = witness.cb_match_perf("2026-08-01", ["Colombo Kaps", "Galle Gallants"],
                                            espn_fixture)
    assert cb_pid is None and "no unique cricbuzz match" in note


def test_a_missing_witness_flags_the_match_single_feed(wcmod):
    """The published row must say the cross-check never happened — a silently-absent witness is
    indistinguishable from a passing one."""
    st, flag = wcmod.classify_match_status(False, True, {}, {}, False, witness_present=False,
                                           witness="cricbuzz")
    assert st == "COMPLETED_FLAGGED"
    assert "single feed" in flag and "cricbuzz" in flag


def test_cricbuzz_off_leaves_cricapi_behaviour_byte_identical(wcmod, monkeypatch):
    """A tour with no `cricbuzz_series` must behave exactly as it does today."""
    monkeypatch.setattr(wcmod, "CB_SERIES", "")
    assert wcmod.cb_witness_active() is False
    assert wcmod.cb_match_perf("2026-08-01", ["A", "B"], {}) == (None, "", {})
    capi = {"x": {"r": 40, "w": 1, "4s": 3, "6s": 1}}
    espn = {"x": {"r": 40, "w": 2, "4s": 3, "6s": 1}}
    assert wcmod.compute_l1_gaps(capi, espn) == {"x": "wkts 1/2"}


def test_a_bridge_that_will_not_load_leaves_cricapi_as_the_witness(wcmod, monkeypatch):
    monkeypatch.setattr(wcmod, "CB_SERIES", "12316")
    monkeypatch.setattr(wcmod, "CB_STORE", None)
    assert wcmod.cb_witness_active() is False
    cb_pid, note, _ = wcmod.cb_match_perf("2026-08-01", ["A", "B"], {})
    assert cb_pid is None and "bridge store is empty" in note


# ── 6. the two hosts' User-Agents must never be unified ──────────────────────────────────────

def test_the_espn_user_agent_is_identical_in_every_module(wcmod):
    """ESPN 403s a browser UA and every fetcher swallows it, so a 'tidy' here is indistinguishable
    from 'ESPN has no data' — the failure that cost a day. Two modules carry this constant; if they
    ever drift, one of them is silently getting 403s."""
    assert brg.ESPN_UA == wcmod.ESPN_UA
    assert "Mozilla" not in wcmod.ESPN_UA and "github.com" in wcmod.ESPN_UA


def test_the_cricbuzz_user_agent_never_leaks_into_the_espn_one(wcmod):
    assert cb.CB_UA != wcmod.ESPN_UA and brg.CB_UA != wcmod.ESPN_UA
    assert cb.CB_UA == brg.CB_UA, "two different cricbuzz UAs — pick one and share it"
