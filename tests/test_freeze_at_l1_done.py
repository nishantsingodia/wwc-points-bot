"""Base points must freeze at the L1-DONE transition, and the "COMPLETED never returns to LIVE"
ratchet must survive that narrowing.

WHAT WENT WRONG (measured against the live sheet + the write-once store, 16 Aug 2026).

1. FREEZE TOO EARLY. `record_settlement` fired on any COMPLETED/COMPLETED_FLAGGED publish. That is
   strictly earlier than L1-DONE: `classify_match_status`'s cs_path branch returns COMPLETED
   without ever inspecting `unresolved` / `unsourced`, so a match whose feeds still disagreed
   published COMPLETED and minted a WRITE-ONCE baseline off the provisional number. 913 rows / 27
   matches (Hundred M 439/13, Hundred W 359/11, CPL 115/3) are published COMPLETED today with their
   Recon State still reading "⏳ L1 recon open", and all 913 already carry a frozen baseline the
   owner's later L1 adjudication can never move.

2. THE RATCHET WAS THE SAME FACT WEARING TWO HATS.
   `already_completed = any(k[0] == mk and v['tour'] == tour for k, v in SETTLEMENTS.items())`
   works only while every COMPLETED publish also freezes a baseline. Narrowing (1) breaks exactly
   that: a match that publishes with L1 open freezes nothing, so a settlement-derived ratchet
   forgets it published, and the next new gap retracts it — the 7-9 Aug outage that un-published
   12 matches / 410 settled rows. 417 rows / 12 matches on the live sheet are published RIGHT NOW
   only because the ratchet remembers ("⚠ pending recon approval", a flag that requires
   already_completed=True).

Every test below fails on the pre-change file except the negative controls, which must pass on both.
"""
import csv
import json
import os

import pytest

from test_unattributed import SQUADS, _p, _roster_perf     # noqa: F401  (shared e2e wiring)


# ── 1. THE LEDGER ITSELF ─────────────────────────────────────────────────────────────────────
def test_ratchet_is_write_once_and_tour_scoped(wcmod, monkeypatch):
    """match_key_of strips the gender qualifier, so the Hundred's same-day M/W double-headers
    between the same franchises share one key (31 of 31 Women's keys also carry Men's rows). The
    ratchet key must carry the tour, or a Women's fixture reads 'already completed' off the Men's
    result and the LIVE arm of classify_match_status is silently disabled."""
    monkeypatch.setattr(wcmod, "COMPLETED", {})
    monkeypatch.setattr(wcmod, "SETTLEMENTS", {})
    mk = wcmod.match_key_of("2026-08-04", ["London Spirit (Men)", "Sunrisers Leeds (Men)"])
    mkw = wcmod.match_key_of("2026-08-04", ["London Spirit (Women)", "Sunrisers Leeds (Women)"])
    assert mk == mkw, "precondition: the two fixtures DO collide on match_key"

    wcmod.record_completed(mk, "MEN", "Match 20 — LS v SUN", "2026-08-04", "COMPLETED")
    assert wcmod.has_published_completed(mk, "MEN") is True
    assert wcmod.has_published_completed(mkw, "WOMEN") is False, "gender collision leaked"

    # write-once: a later, different verdict never rewrites the first publish
    wcmod.record_completed(mk, "MEN", "renamed", "2026-08-04", "COMPLETED_FLAGGED")
    rec = wcmod.COMPLETED[("MEN", mk)]
    assert rec["first_status"] == "COMPLETED" and rec["match"] == "Match 20 — LS v SUN"


def test_settlement_store_still_witnesses_pre_ledger_matches(wcmod, monkeypatch):
    """No migration, no re-keying: a settlement row can only ever have been written by a COMPLETED
    publish, so it remains valid evidence for the 95 matches / 3470 rows frozen before the ledger
    existed. The two witnesses are OR'd, and a union of two write-once stores is monotone — this
    can only remember MORE than the old code, never less."""
    monkeypatch.setattr(wcmod, "COMPLETED", {})
    monkeypatch.setattr(wcmod, "SETTLEMENTS", {
        ("2026-07-17::a|b", "ci:1"): {"tour": "LPL", "points": 40}})
    assert wcmod.has_published_completed("2026-07-17::a|b", "LPL") is True
    assert wcmod.has_published_completed("2026-07-17::a|b", "OTHER TOUR") is False
    assert wcmod.has_published_completed("2026-07-18::a|b", "LPL") is False


def test_ledger_round_trips_and_only_grows(wcmod, monkeypatch, tmp_path):
    p = tmp_path / "completed.json"
    monkeypatch.setattr(wcmod, "COMPLETED_PATH", str(p))
    monkeypatch.setattr(wcmod, "COMPLETED", {})
    wcmod.record_completed("k1", "T", "Match 1", "2026-08-01", "COMPLETED")
    wcmod.record_completed("k2", "T", "Match 2", "2026-08-02", "COMPLETED_FLAGGED")
    wcmod.save_completed()
    monkeypatch.setattr(wcmod, "COMPLETED", wcmod._load_completed())
    assert set(wcmod.COMPLETED) == {("T", "k1"), ("T", "k2")}
    assert wcmod.has_published_completed("k2", "T") is True


def test_a_missing_ledger_file_is_not_an_empty_ledger(wcmod, monkeypatch, tmp_path):
    """ABSENCE ≠ VALUE. An unreadable/absent file must not read as 'no match has ever completed' —
    the settlement witness still answers, which is why the OR is the design and not a nicety."""
    monkeypatch.setattr(wcmod, "COMPLETED_PATH", str(tmp_path / "nope.json"))
    monkeypatch.setattr(wcmod, "COMPLETED", wcmod._load_completed())
    monkeypatch.setattr(wcmod, "SETTLEMENTS", {("k", "ci:1"): {"tour": "T"}})
    assert wcmod.COMPLETED == {}
    assert wcmod.has_published_completed("k", "T") is True


def test_the_backfill_is_a_pure_function_of_the_settlement_store():
    """Regenerate, never hand-edit — the same discipline as cricbuzz_match_map.json. `first_seen`
    comes off the settlement row's own frozen_at, so no clock is read and a re-derive reproduces
    the file exactly."""
    import importlib.util
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "backfill_cm", os.path.join(root, "registry", "backfill_completed_matches.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    a, b = mod.derive(), mod.derive()
    assert a == b and a, "derivation is not deterministic (or the settlement store is empty)"
    on_disk = {(r["tour"], r["match_key"]): r for r in
               json.load(open(os.path.join(root, "registry", "completed_matches.json")))["completed"]}
    missing = set(a) - set(on_disk)
    assert not missing, f"committed ledger is behind the settlement store: {sorted(missing)[:3]}"


def test_the_workflows_commit_the_ratchet():
    """A ledger the workflow does not commit is written-but-never-read — and this one would reset
    the ratchet on EVERY run, re-arming the un-publish outage it exists to prevent."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for wf in ("wwc-points.yml", "live-lineup.yml", "on-demand-refresh.yml"):
        text = open(os.path.join(root, ".github", "workflows", wf), encoding="utf-8").read()
        assert "registry/completed_matches.json" in text, wf


def test_the_provisional_publish_that_froze_913_rows_no_longer_freezes(wcmod):
    """THE COMBINATION, pinned as a fact about the two axes. A provisional match with an
    unresolved L1 gap that has ALREADY published (the ratchet arm) publishes COMPLETED_FLAGGED
    while the recon axis says L1_OPEN. Gating the freeze on the STATUS mints a write-once baseline
    off a cut the feeds are still arguing about; gating it on the RECON axis does not."""
    gaps = {"ci:1": "runs 38/57"}
    st, flag = wcmod.classify_match_status(False, True, gaps, gaps, False, already_completed=True)
    assert st == "COMPLETED_FLAGGED" and "pending recon approval" in flag
    assert wcmod.classify_recon_state(False, gaps, (), {}, {}) == "L1_OPEN"
    # ...and answering the gap moves the same match to L1_DONE, where it freezes.
    assert wcmod.classify_recon_state(False, {}, (), {}, {}) == "L1_DONE"


def test_an_l1_gap_stops_blocking_once_the_official_card_is_in(wcmod):
    """A gap that can never be answered must never be able to block. The Recon tab only queues an
    L1 row `if unresolved and not cs_path`, and apply_recon_overrides writes into the perf dicts
    emit() scores — which on a cricsheet run hold the OFFICIAL figures, so an answer given after
    cricsheet lands would overwrite the official card with a provisional feed. Left as L1_OPEN,
    those 15 matches / 496 rows (measured 16 Aug 2026) would publish COMPLETED and never freeze a
    baseline, forever, with nothing for a human to click."""
    gaps = {"ci:1": "runs 38/57"}
    assert wcmod.classify_recon_state(False, gaps, (), {}, {}) == "L1_OPEN"      # no card yet
    assert wcmod.classify_recon_state("cs.json", gaps, (), {}, {}) == "L2_DONE"  # card in: moot
    # ...but the two things cricsheet CANNOT answer still hold L1 open on a cricsheet run.
    assert wcmod.classify_recon_state("cs.json", {}, {("A", "X")}, {}, {}) == "L1_OPEN"
    assert wcmod.classify_recon_state("cs.json", {}, (), {}, {},
                                      unattributed=[("ci:9", {})]) == "L1_OPEN"


# ── 2. THE FREEZE GATE, END TO END THROUGH run_tour ──────────────────────────────────────────
def _wire(wcmod, monkeypatch, tmp_path, espn_perf, team_map, *, capi_perf=None, xi=None):
    """Same shape as tests/test_unattributed.py's harness, but record_settlement is REAL (pointed
    at a temp store) — the freeze is the thing under test."""
    sq = tmp_path / "squads.json"
    sq.write_text(json.dumps(SQUADS))
    monkeypatch.setattr(wcmod, "GSHEET_ID", "")
    monkeypatch.setattr(wcmod, "SETTLEMENTS", {})
    monkeypatch.setattr(wcmod, "COMPLETED", {})
    monkeypatch.setattr(wcmod, "COMPLETED_PATH", str(tmp_path / "completed.json"))
    monkeypatch.setattr(wcmod, "SETTLEMENT_PATH", str(tmp_path / "settlements.json"))
    monkeypatch.setattr(wcmod, "_save_new_players", lambda *a, **k: None)
    # register_new_player mutates NEW_PLAYERS_DATA in memory and the next run INJECTS those
    # players into the squads — so without this, a test that attributes the ghost teaches the
    # module he is a Beta Giant and the next test's "un-attributable" man quietly isn't one.
    monkeypatch.setattr(wcmod, "NEW_PLAYERS_DATA", {"players": []})
    monkeypatch.setattr(wcmod, "mark_frozen", lambda *a, **k: None)
    monkeypatch.setattr(wcmod, "load_cricsheet_index", lambda *a, **k: {})
    monkeypatch.setattr(wcmod, "espn_event_id", lambda mdate, teams: "9999")
    monkeypatch.setattr(wcmod, "parse_espn", lambda ev, fresh=False: (espn_perf, False))
    monkeypatch.setattr(wcmod, "espn_team_map", lambda ev, fresh=False: team_map)
    monkeypatch.setattr(wcmod, "espn_xi", lambda ev: xi or {})
    monkeypatch.setattr(wcmod, "espn_match_list", lambda tour, names: [{
        "id": "", "name": "Alpha Kings vs Beta Giants", "matchType": "t20",
        "teams": ["Alpha Kings", "Beta Giants"], "date": "2026-08-01",
        "dateTimeGMT": "2026-08-01T10:00:00Z", "matchStarted": True, "matchEnded": True}])
    out = tmp_path / "out.csv"
    return {"name": "Freeze Tour", "tab": "TEST", "cricapi_series": "", "espn_series": "1",
            "cricbuzz_series": "", "squads_path": str(sq), "gender": "male",
            "format": "T20", "out_csv": str(out)}, out


def _clean(wcmod):
    team_map = {wcmod.norm(n): t for n, t in
                (("Alpha One", "Alpha Kings"), ("Alpha Two", "Alpha Kings"),
                 ("Beta One", "Beta Giants"), ("Beta Two", "Beta Giants"))}
    return _roster_perf(wcmod), team_map


def test_l1_done_freezes(wcmod, monkeypatch, tmp_path):
    """NEGATIVE CONTROL. A clean match reaches L1_DONE and its base points freeze exactly as they
    always did — the gate narrows, it does not stop freezing. (It cannot be RUN against the old
    file: the harness names the ledger. The behaviour it asserts is the unchanged one.)"""
    perf, team_map = _clean(wcmod)
    tour, out = _wire(wcmod, monkeypatch, tmp_path, perf, team_map)
    wcmod.run_tour(tour)
    rows = list(csv.DictReader(open(out)))
    assert {r["Recon State"] for r in rows} == {wcmod.RECON_STATE_LABEL["L1_DONE"]}
    assert all(r["Match Status"].startswith("COMPLETED") for r in rows)
    assert len(wcmod.SETTLEMENTS) == len(rows) > 0, "L1-done match did not freeze"
    assert len(wcmod.COMPLETED) == 1, "the publish was not recorded in the ratchet"


def test_l1_open_publishes_but_does_not_freeze(wcmod, monkeypatch, tmp_path, capsys):
    """THE FIX. An un-attributable performance holds the match at L1_OPEN. On the FIRST run the
    match is LIVE; once the ratchet remembers a prior COMPLETED it publishes COMPLETED_FLAGGED —
    and that publish must NOT mint a write-once baseline off a cut we are still arguing about."""
    perf, team_map = _clean(wcmod)
    perf[wcmod.norm("Roster Ghost")] = _p(wcmod, "Roster Ghost", "ci:910005", r=77, b=40, w=3)
    team_map[wcmod.norm("Roster Ghost")] = "Gamma Nomads"          # a side no squad knows
    tour, out = _wire(wcmod, monkeypatch, tmp_path, perf, team_map)

    # Pre-load the ratchet so classify_match_status takes the already-published arm.
    mk = wcmod.match_key_of("2026-08-01", ["Alpha Kings", "Beta Giants"])
    wcmod.record_completed(mk, "Freeze Tour", "Match 1", "2026-08-01", "COMPLETED")
    wcmod.run_tour(tour)

    rows = list(csv.DictReader(open(out)))
    assert {r["Match Status"] for r in rows} == {"COMPLETED_FLAGGED"}, "the ratchet did not hold"
    assert {r["Recon State"] for r in rows} == {wcmod.RECON_STATE_LABEL["L1_OPEN"]}
    assert wcmod.SETTLEMENTS == {}, "froze a baseline while L1 was open"
    assert "base points NOT frozen" in capsys.readouterr().err, "the absence was silent"


def test_the_ratchet_alone_keeps_a_published_match_published(wcmod, monkeypatch, tmp_path):
    """The hard dependency, proved in both directions. With the ledger the match stays published
    even though nothing is frozen; take the ledger away and the SAME inputs go back to LIVE —
    which is the 7-9 Aug outage, and is what a naive narrowing would have shipped."""
    perf, team_map = _clean(wcmod)
    perf[wcmod.norm("Roster Ghost")] = _p(wcmod, "Roster Ghost", "ci:910005", r=77, b=40, w=3)
    team_map[wcmod.norm("Roster Ghost")] = "Gamma Nomads"
    mk = wcmod.match_key_of("2026-08-01", ["Alpha Kings", "Beta Giants"])

    tour, out = _wire(wcmod, monkeypatch, tmp_path, perf, team_map)
    wcmod.record_completed(mk, "Freeze Tour", "Match 1", "2026-08-01", "COMPLETED")
    wcmod.run_tour(tour)
    assert {r["Match Status"] for r in list(csv.DictReader(open(out)))} == {"COMPLETED_FLAGGED"}

    tour2, out2 = _wire(wcmod, monkeypatch, tmp_path, perf, team_map)   # ratchet reset to {}
    wcmod.run_tour(tour2)
    assert {r["Match Status"] for r in list(csv.DictReader(open(out2)))} == {"LIVE"}


def test_answering_the_gap_freezes_on_the_next_run(wcmod, monkeypatch, tmp_path):
    """The loop must CLOSE. A held match that never freezes is a different bug from one that
    freezes too early — attribute the man and the very next run freezes the reconciled number."""
    perf, team_map = _clean(wcmod)
    perf[wcmod.norm("Roster Ghost")] = _p(wcmod, "Roster Ghost", "ci:910005", r=77, b=40, w=3)
    team_map[wcmod.norm("Roster Ghost")] = "Gamma Nomads"
    tour, out = _wire(wcmod, monkeypatch, tmp_path, perf, team_map)
    wcmod.run_tour(tour)
    assert wcmod.SETTLEMENTS == {} and len(wcmod.COMPLETED) == 0   # LIVE, nothing frozen

    team_map[wcmod.norm("Roster Ghost")] = "Beta Giants"           # the human answers the team
    tour2, out2 = _wire(wcmod, monkeypatch, tmp_path, perf, team_map)
    wcmod.run_tour(tour2)
    assert {r["Recon State"] for r in list(csv.DictReader(open(out2)))} \
        == {wcmod.RECON_STATE_LABEL["L1_DONE"]}
    assert len(wcmod.SETTLEMENTS) > 0, "the baseline never froze after the gap was answered"
    ghost = [v for v in wcmod.SETTLEMENTS.values() if v["full"] == "Roster Ghost"]
    assert len(ghost) == 1 and ghost[0]["points"] > 0


def test_non_players_are_not_frozen_while_l1_is_open(wcmod, monkeypatch, tmp_path):
    """The Played=N branch had its own copy of the gate. Half-freezing a match (0-point rows in,
    scored rows out) would make the audit read '11 players moved' on a match nobody settled."""
    perf, team_map = _clean(wcmod)
    del perf[wcmod.norm("Beta Two")]                              # squad member who did not play
    perf[wcmod.norm("Roster Ghost")] = _p(wcmod, "Roster Ghost", "ci:910005", r=77, b=40, w=3)
    team_map[wcmod.norm("Roster Ghost")] = "Gamma Nomads"
    tour, out = _wire(wcmod, monkeypatch, tmp_path, perf, team_map)
    mk = wcmod.match_key_of("2026-08-01", ["Alpha Kings", "Beta Giants"])
    wcmod.record_completed(mk, "Freeze Tour", "Match 1", "2026-08-01", "COMPLETED")
    wcmod.run_tour(tour)
    rows = list(csv.DictReader(open(out)))
    assert any(r["Played"] == "N" for r in rows), "precondition: a non-player is on the card"
    assert wcmod.SETTLEMENTS == {}, "a Played=N row froze while L1 was open"
