"""The re-armed un-attributable-performance check.

WHAT WENT WRONG (measured 14 Aug 2026). `resolve_perf_pid` mints `ci:<espn athlete.id>` for any
row carrying an id, so on an ESPN-sourced tour `match_squad_to_perf`'s `unresolved` set is always
empty ⇒ `leftover` is always empty ⇒ the tolerant fallback that published a "?"-team row for review
is DEAD CODE. Evidence: `In Squad List = Y` on 2228/2228 rows across the Hundred M/W + CPL tabs
(zero N), where the same tab carried 9 "?" review rows on 11 Aug. The only surviving path for
"played but in no squad slot" — the auto-add — drops such a player with `if es not in match_shorts:
continue`: no row, no review entry, no log line, and the match still publishes and freezes money.

These tests pin the replacement, and — deliberately — each positive case is paired with the proof
that the OLD surface would have shown nothing for it (`leftover == {}`), plus a negative control
that the check stays silent on a normal match. A check that cannot report "clean" is not a check.
"""
import csv
import json

import pytest


@pytest.fixture(autouse=True)
def _isolate(wcmod, monkeypatch):
    monkeypatch.setattr(wcmod, "ALIAS2PID", dict(wcmod.ALIAS2PID))
    monkeypatch.setattr(wcmod, "PID2DISP", dict(wcmod.PID2DISP))
    monkeypatch.setattr(wcmod, "NEW_PLAYERS_DATA", {"players": []})
    monkeypatch.setattr(wcmod, "REVIEW", [])
    monkeypatch.setattr(wcmod, "UNMATCHED_LOG", set())
    monkeypatch.setattr(wcmod, "ANOMALIES", [])
    monkeypatch.setattr(wcmod, "SETTLEMENTS", {})
    monkeypatch.setattr(wcmod, "RECON_REVIEW", [])
    monkeypatch.setattr(wcmod, "RECON_OVERRIDES", {})
    monkeypatch.setattr(wcmod, "ROLE_OVERRIDE", {})


def _p(wcmod, name, pid=None, **over):
    p = wcmod.blank_perf(name)
    p["played"] = True
    p.update(over)
    if pid:
        wcmod.ALIAS2PID[wcmod.norm(name)] = pid
    return p


# ── find_unattributed: the outcome-shaped check ──────────────────────────────
def test_fires_on_a_played_perf_that_reached_no_slot(wcmod):
    """THE CASE. A pid'd player ESPN's roster cannot put on either side: the auto-add's
    `es not in match_shorts` branch drops him, so he is in neither `assigned` nor `leftover`."""
    star = _p(wcmod, "Squad Star", "ci:900001", r=50)
    ghost = _p(wcmod, "Roster Ghost", "ci:900002", r=44, w=2)
    perf = {wcmod.norm("Squad Star"): star, wcmod.norm("Roster Ghost"): ghost}
    team_players = [("AAA", "Squad Star", "BAT")]
    assigned = {("AAA", "Squad Star"): star}

    # The OLD surface is blind to him: he resolves to a pid, so he is not a no-pid leftover.
    _, leftover, _ = wcmod.match_squad_to_perf(team_players, dict(perf), quiet=True)
    assert leftover == {}, "precondition: the leftover review path is dead for a pid'd row"

    out = wcmod.find_unattributed(perf, assigned, team_players, {})
    assert [(pid, v["name"]) for pid, v in out] == [("ci:900002", "Roster Ghost")]


def test_silent_on_a_clean_match(wcmod):
    """NEGATIVE CONTROL. Every played row landed on a slot ⇒ nothing reported."""
    a = _p(wcmod, "Squad Star", "ci:900001", r=50)
    b = _p(wcmod, "Other Star", "ci:900003", r=20)
    dnp = _p(wcmod, "Bench Sitter", "ci:900004", played=False)
    perf = {wcmod.norm("Squad Star"): a, wcmod.norm("Other Star"): b,
            wcmod.norm("Bench Sitter"): dnp}
    team_players = [("AAA", "Squad Star", "BAT"), ("BBB", "Other Star", "BOWL"),
                    ("BBB", "Bench Sitter", "BAT")]
    assigned = {("AAA", "Squad Star"): a, ("BBB", "Other Star"): b}
    assert wcmod.find_unattributed(perf, assigned, team_players, {}) == []


def test_a_leftover_row_is_consumed_not_double_reported(wcmod):
    """A no-pid row IS published by the leftover emit — it must not also be reported here, or the
    same performance would appear twice and hold a match that is already showing it."""
    nameless = wcmod.blank_perf("Totally Unknown Person")
    nameless["played"] = True
    assert wcmod.resolve_pid("Totally Unknown Person") is None
    perf = {wcmod.norm("Totally Unknown Person"): nameless}
    assert wcmod.find_unattributed(perf, {}, [], leftover=dict(perf)) == []


def test_a_merged_spelling_is_not_reported(wcmod):
    """merge_perf returns a NEW dict when two feed spellings fold into one pid, so an id()-based
    diff would report the merged player as dropped. Matching on pid must survive that."""
    a = _p(wcmod, "Danni Wyatt", "ci:900005", r=30)
    b = _p(wcmod, "DN Wyatt", "ci:900005", r=0, catches=1)
    perf = {wcmod.norm("Danni Wyatt"): a, wcmod.norm("DN Wyatt"): b}
    team_players = [("AAA", "Danni Wyatt", "BAT")]
    assigned, leftover, _ = wcmod.match_squad_to_perf(team_players, dict(perf), quiet=True)
    merged = assigned[("AAA", "Danni Wyatt")]
    assert merged is not a and merged is not b, "precondition: merge_perf built a new dict"
    assert wcmod.find_unattributed(perf, assigned, team_players, leftover) == []


def test_junk_rows_never_hold_a_match(wcmod):
    """match_squad_to_perf drops 'Player Not Found'/'sub' before it matches anything, so they are
    in neither assigned nor leftover BY DESIGN. Re-reporting them would block every match."""
    junk = wcmod.blank_perf("Player Not Found")
    junk["played"] = True
    assert wcmod.find_unattributed({"x": junk}, {}, [], {}) == []


def test_an_assigned_slot_emit_never_visits_is_still_unattributed(wcmod):
    """emit() iterates team_players; an `assigned` entry keyed on anything else is never published,
    so it must not count as consumed."""
    orphan = _p(wcmod, "Phantom Slot", "ci:900006", r=11)
    perf = {wcmod.norm("Phantom Slot"): orphan}
    assigned = {("ZZZ", "Phantom Slot"): orphan}     # ZZZ is in no team_players tuple
    assert [p for p, _ in wcmod.find_unattributed(perf, assigned, [], {})] == ["ci:900006"]


# ── the gate: an un-attributable performance holds the match ─────────────────
def test_gate_holds_the_match_live(wcmod):
    st, flag = wcmod.classify_match_status(False, True, {}, {}, False,
                                           unattributed=[("ci:1", {"name": "X"})])
    assert st == "LIVE" and "not attributed to a team" in flag


def test_gate_respects_the_completed_ratchet(wcmod):
    """COMPLETED never returns to LIVE — a newly-found drop on a settled match is FLAGGED."""
    st, flag = wcmod.classify_match_status(False, True, {}, {}, False, already_completed=True,
                                           unattributed=[("ci:1", {"name": "X"})])
    assert st == "COMPLETED_FLAGGED" and "not attributed to a team" in flag


def test_gate_beats_the_cricsheet_early_return(wcmod):
    """cricsheet posting cannot un-drop a player the pipeline never attributed, so the check is
    tested ahead of the cs_path branch — which otherwise returns COMPLETED without looking."""
    assert wcmod.classify_match_status("cs.json", True, {}, {}, False) == ("COMPLETED", "")
    assert wcmod.classify_match_status("cs.json", True, {}, {}, False,
                                       unattributed=[("ci:1", {"name": "X"})])[0] == "LIVE"


def test_gate_is_silent_when_nothing_is_unattributed(wcmod):
    assert wcmod.classify_match_status(False, True, {}, {}, False, unattributed=()) \
        == ("COMPLETED", "")


def test_recon_state_opens_l1(wcmod):
    assert wcmod.classify_recon_state(False, {}, set(), {}, {}) == "L1_DONE"
    assert wcmod.classify_recon_state(False, {}, set(), {}, {},
                                      unattributed=[("ci:1", {})]) == "L1_OPEN"


# ── NON-REGRESSION, exhaustively rather than by sampling ────────────────────
# The new branch sits AHEAD of the `cs_path` early return, and a rehearsal cannot reach that
# branch (no cricsheet dir locally on any of the four live tours: "cricsheet matches indexed: 0").
# So prove the stronger claim directly — with `unattributed` empty, the gate is IDENTICAL to the
# pre-change one on every combination of its inputs, cricsheet path included.
def _legacy_status(cs_path, espn_present, l1_gaps, unresolved, l2_dirty, id_break=False,
                   unsourced=(), already_completed=False, capi_present=True, witness="cricapi"):
    """Verbatim pre-change body (wc_fps_to_csv.py @ b3057d9)."""
    if cs_path:
        if id_break:
            return ("COMPLETED_FLAGGED", "⚠ identity unresolved on official card")
        return ("COMPLETED_FLAGGED", "⚠ official revision pending") if l2_dirty else ("COMPLETED", "")
    if unsourced:
        n = len(unsourced)
        msg = f"{n} player{'' if n == 1 else 's'} scored without a dot-ball source"
        return (("COMPLETED_FLAGGED", "⚠ " + msg) if already_completed
                else ("LIVE", "⏳ " + msg))
    if not espn_present:
        return ("COMPLETED_FLAGGED", f"⚠ unverified — single feed ({witness} only)")
    if not capi_present:
        return ("COMPLETED_FLAGGED",
                f"⚠ unverified — single feed (ESPN only, {witness} had no card)")
    if unresolved:
        n = len(unresolved)
        msg = f"pending recon approval ({n} player{'' if n == 1 else 's'})"
        return (("COMPLETED_FLAGGED", "⚠ " + msg) if already_completed
                else ("LIVE", "⏳ " + msg))
    return ("COMPLETED", "")


def test_empty_unattributed_is_byte_identical_to_the_old_gate(wcmod):
    import itertools
    n = 0
    for (cs, espn, gaps, unres, dirty, idb, uns, done, capi, wit) in itertools.product(
            ["", "cs.json"], [False, True], [{}, {"p": "g"}], [{}, {"p": "g"}, {"p": "g", "q": "g"}],
            [False, True], [False, True], [(), {("A", "X")}], [False, True], [False, True],
            ["cricapi", "cricbuzz"]):
        kw = dict(id_break=idb, unsourced=uns, already_completed=done,
                  capi_present=capi, witness=wit)
        assert wcmod.classify_match_status(cs, espn, gaps, unres, dirty, **kw) \
            == _legacy_status(cs, espn, gaps, unres, dirty, **kw)
        assert wcmod.classify_match_status(cs, espn, gaps, unres, dirty, unattributed=(), **kw) \
            == _legacy_status(cs, espn, gaps, unres, dirty, **kw)
        n += 1
    assert n == 1536


def test_empty_unattributed_leaves_recon_state_identical(wcmod):
    import itertools
    for (cs, unres, uns, pairs, appr) in itertools.product(
            ["", "cs.json"], [{}, {"p": "g"}], [set(), {("A", "X")}],
            [{}, {"ci:1": "d"}], [{}, {"ci:1": "S2"}]):
        # `unres and not cs`, not `unres`: 16 Aug 2026, an unresolved L1 gap stopped opening L1
        # once the official card is in — the Recon tab refuses to queue that row on a cricsheet
        # run and an answer to it would overwrite cricsheet with a provisional feed, so it was an
        # open question with no row and no safe answer. See classify_recon_state. This test's own
        # claim is unchanged: passing `unattributed=()` must be identical to not passing it.
        legacy = ("L1_OPEN" if ((unres and not cs) or uns) else
                  "L1_DONE" if not cs else
                  ("L2_PENDING" if any(p not in appr for p in pairs) else "L2_DONE"))
        assert wcmod.classify_recon_state(cs, unres, uns, pairs, appr) == legacy
        assert wcmod.classify_recon_state(cs, unres, uns, pairs, appr, unattributed=()) == legacy


# ── write_review_tab: an ACK must not silence a match-blocking row ───────────
def test_ack_cannot_silence_an_unattributed_row(wcmod, monkeypatch):
    """ACK answers a NAME ('stop asking about this match guess'); this row is open against a MATCH
    that is being held. Letting ACK drop it would delete the only actionable row while the match
    stayed LIVE forever with nothing to click."""
    monkeypatch.setattr(wcmod, "ACK", {wcmod.norm("Roster Ghost"), wcmod.norm("Fuzzy Name")})
    monkeypatch.setattr(wcmod, "REVIEW", [
        {"tour": "T", "team": "?", "feed": "Roster Ghost", "kind": "review",
         "suggestion": "", "role": "BAT", "unattributed": True},
        {"tour": "T", "team": "AAA", "feed": "Fuzzy Name", "kind": "review",
         "suggestion": "Real Name", "role": "BAT"},
    ])
    feeds = [r["feed"] for r in wcmod.open_review_items()]
    assert feeds == ["Roster Ghost"], "ACKed fuzzy row drops; the unattributed row must not"


def test_ack_still_silences_an_ordinary_review_row(wcmod, monkeypatch):
    """NEGATIVE CONTROL for the exemption: it must be narrow. An ACKed ordinary row still drops."""
    monkeypatch.setattr(wcmod, "ACK", {wcmod.norm("Fuzzy Name")})
    monkeypatch.setattr(wcmod, "REVIEW", [
        {"tour": "T", "team": "AAA", "feed": "Fuzzy Name", "kind": "review",
         "suggestion": "Real Name", "role": "BAT"}])
    assert wcmod.open_review_items() == []


# ── END-TO-END through run_tour: the row, the flag, the hold, the review entry ──
SQUADS = {
    "AAA": {"name": "Alpha Kings", "players": [["Alpha One", "BAT"], ["Alpha Two", "BOWL"]]},
    "BBB": {"name": "Beta Giants", "players": [["Beta One", "BAT"], ["Beta Two", "BOWL"]]},
}


def _wire(wcmod, monkeypatch, tmp_path, espn_perf, team_map):
    sq = tmp_path / "squads.json"
    sq.write_text(json.dumps(SQUADS))
    monkeypatch.setattr(wcmod, "GSHEET_ID", "")
    monkeypatch.setattr(wcmod, "record_settlement", lambda *a, **k: None)
    monkeypatch.setattr(wcmod, "_save_new_players", lambda *a, **k: None)
    monkeypatch.setattr(wcmod, "mark_frozen", lambda *a, **k: None)
    monkeypatch.setattr(wcmod, "load_cricsheet_index", lambda *a, **k: {})
    monkeypatch.setattr(wcmod, "espn_event_id", lambda mdate, teams: "9999")
    monkeypatch.setattr(wcmod, "parse_espn", lambda ev, fresh=False: (espn_perf, False))
    monkeypatch.setattr(wcmod, "espn_team_map", lambda ev, fresh=False: team_map)
    monkeypatch.setattr(wcmod, "espn_xi", lambda ev: {})
    monkeypatch.setattr(wcmod, "espn_match_list", lambda tour, names: [{
        "id": "", "name": "Alpha Kings vs Beta Giants", "matchType": "t20",
        "teams": ["Alpha Kings", "Beta Giants"], "date": "2026-08-01",
        "dateTimeGMT": "2026-08-01T10:00:00Z", "matchStarted": True, "matchEnded": True}])
    out = tmp_path / "out.csv"
    return {"name": "Test Tour", "tab": "TEST", "cricapi_series": "", "espn_series": "1",
            "cricbuzz_series": "999", "squads_path": str(sq), "gender": "male",
            "format": "T20", "out_csv": str(out)}, out


def _roster_perf(wcmod):
    rows = {}
    for nm, pid in (("Alpha One", "ci:910001"), ("Alpha Two", "ci:910002"),
                    ("Beta One", "ci:910003"), ("Beta Two", "ci:910004")):
        rows[wcmod.norm(nm)] = _p(wcmod, nm, pid, r=20, b=15)
    return rows


def test_end_to_end_unattributed_row_and_hold(wcmod, monkeypatch, tmp_path):
    """THE PROOF IT FIRES, through the real call site. A 5th man plays whose ESPN roster team is a
    club in neither squad — precisely what `es not in match_shorts` used to swallow."""
    perf = _roster_perf(wcmod)
    perf[wcmod.norm("Roster Ghost")] = _p(wcmod, "Roster Ghost", "ci:910005", r=77, b=40, w=3)
    team_map = {wcmod.norm(n): t for n, t in
                (("Alpha One", "Alpha Kings"), ("Alpha Two", "Alpha Kings"),
                 ("Beta One", "Beta Giants"), ("Beta Two", "Beta Giants"),
                 ("Roster Ghost", "Gamma Nomads"))}     # a team no squad knows
    tour, out = _wire(wcmod, monkeypatch, tmp_path, perf, team_map)
    wcmod.run_tour(tour)

    rows = list(csv.DictReader(open(out)))
    ghost = [r for r in rows if r["Full Name"] == "Roster Ghost"]
    assert len(ghost) == 1, "the drop is no longer silent"
    g = ghost[0]
    assert g["Played"] == "Y" and g["Runs"] == "77"      # the performance is visible
    assert g["Fantasy Points"] == "" and g["Pts Bat"] == ""   # ...and NOT scored: blank, never 0
    assert g["Team"] == "?" and g["In Squad List"] == "N"
    assert "UNATTRIBUTED" in g["Source"]
    assert g["Player Recon"] == "⛔ unattributed — no team, not scored"
    # the whole match is held, and the flag names him on every row
    assert {r["Match Status"] for r in rows} == {"LIVE"}
    assert all("not attributed to a team" in r["Recon Flag"] and "Roster Ghost" in r["Recon Flag"]
               for r in rows)
    assert {r["Recon State"] for r in rows} == {wcmod.RECON_STATE_LABEL["L1_OPEN"]}
    # and he is queued where the question can actually be answered
    assert [(r["feed"], r["team"]) for r in wcmod.REVIEW if r.get("unattributed")] \
        == [("Roster Ghost", "Gamma Nomads")]


def test_end_to_end_clean_match_is_unaffected(wcmod, monkeypatch, tmp_path):
    """NEGATIVE CONTROL on the same wiring: attribute the same man to a real side and the match
    publishes normally, with no review row, no hold and no "?" team."""
    perf = _roster_perf(wcmod)
    perf[wcmod.norm("Roster Ghost")] = _p(wcmod, "Roster Ghost", "ci:910005", r=77, b=40, w=3)
    team_map = {wcmod.norm(n): t for n, t in
                (("Alpha One", "Alpha Kings"), ("Alpha Two", "Alpha Kings"),
                 ("Beta One", "Beta Giants"), ("Beta Two", "Beta Giants"),
                 ("Roster Ghost", "Beta Giants"))}
    tour, out = _wire(wcmod, monkeypatch, tmp_path, perf, team_map)
    wcmod.run_tour(tour)

    rows = list(csv.DictReader(open(out)))
    g = [r for r in rows if r["Full Name"] == "Roster Ghost"]
    assert len(g) == 1 and g[0]["Team"] == "BBB"
    assert float(g[0]["Fantasy Points"]) > 0
    assert g[0]["In Squad List"] == "auto", "auto-added from the feed, not from the squad file"
    assert not any(r.get("unattributed") for r in wcmod.REVIEW)
    assert all(r["Match Status"].startswith("COMPLETED") for r in rows)
    assert all("not attributed" not in r["Recon Flag"] for r in rows)


def test_in_squad_list_distinguishes_squad_from_feed(wcmod, monkeypatch, tmp_path):
    """The column read Y on 2228/2228 rows and so carried no information. It now records the
    slot's PROVENANCE: Y = the announced squad file, auto = only the feed ever said so."""
    perf = _roster_perf(wcmod)
    perf[wcmod.norm("Roster Ghost")] = _p(wcmod, "Roster Ghost", "ci:910005", r=77, b=40)
    team_map = {wcmod.norm(n): t for n, t in
                (("Alpha One", "Alpha Kings"), ("Alpha Two", "Alpha Kings"),
                 ("Beta One", "Beta Giants"), ("Beta Two", "Beta Giants"),
                 ("Roster Ghost", "Alpha Kings"))}
    tour, out = _wire(wcmod, monkeypatch, tmp_path, perf, team_map)
    wcmod.run_tour(tour)
    got = {r["Full Name"]: r["In Squad List"] for r in list(csv.DictReader(open(out)))}
    assert got["Alpha One"] == "Y" and got["Beta Two"] == "Y"
    assert got["Roster Ghost"] == "auto"
    assert len(set(got.values())) > 1, "a column that reads Y on every row is not a column"
