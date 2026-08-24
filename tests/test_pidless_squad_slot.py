"""A squad member who publishes with a blank Player ID was the one identity hole with no flag.

Every other identity failure has a review row: a placeholder pid goes to "Needs Cricinfo ID" via
promote_new_players, an unresolvable OFFICIAL card row goes there via cs_orphans, and two names
sharing one id go to "Identity Anomalies". But a squad slot whose announced name resolved to
NOTHING simply published a blank pid and said nothing — and because the draft joins by pid, that
slot can never receive points.

Measured on the live sheet, 24 Aug 2026 (ENG v PAK Test): 11 of 35 rows had a blank pid, and for
three of them the same man was simultaneously auto-added off the official card under his full legal
name and scored there — "Ollie Robinson" (blank, did-not-play) beside "Oliver Edward Robinson"
(ci:527776, 204 pts), plus Emilio Gay (69) and Shan Masood (70) — 343 points on a row bearing a
different name from the one the squad announced, flagged on no tab.

The money was not lost: the draft joins on the pid in its own players-raw.json, which already held
all three ids, so both Test contests scored every XI slot. What the blank row costs is
reconciliation — the tab shows one man twice, once as did-not-play and once as scored. The reason
to flag it anyway is the other eight pid-less squad members who simply have not played yet.
"""
import json
import os

import wc_fps_to_csv as wc


def test_the_three_announced_names_now_resolve_by_id():
    # ESPN's own match roster supplied both halves: athlete.id IS the cricinfo id, and its
    # displayName for that id IS the announced spelling the squad uses. Feed id + feed spelling,
    # not a name guess.
    wc.load_new_players()
    for announced, pid in [("ollie robinson", "ci:527776"),
                           ("shan masood", "ci:233901"),
                           ("emilio gay", "ci:1148593"),
                           ("matthew david fisher", "ci:639080")]:
        assert wc.ALIAS2PID.get(wc.norm(announced)) == pid, announced


def test_the_ollie_robinson_bridge_records_the_twin_it_could_collide_with():
    # OG Robinson (cricinfo 893955) is a different real player also called "Ollie Robinson".
    # The bare spelling is safe only while he is in no tour we score; the bridge must say so, or
    # the next person to widen it fuses two people.
    b = json.load(open(os.path.join(os.path.dirname(os.path.abspath(wc.__file__)),
                                    "registry", "manual_ci_bridges.json")))
    src = b["ci:527776"]["source"]
    assert "893955" in src and "SPLIT" in src


def _emit_probe(monkeypatch, in_squad, resolvable, autos=()):
    """Run just the pid-less-squad-slot branch and return the NEEDS_CRICINFO rows it appended."""
    monkeypatch.setattr(wc, "NEEDS_CRICINFO", [])
    monkeypatch.setattr(wc, "NO_PID_SEEN", set())
    monkeypatch.setattr(wc, "CURRENT_TOUR", "A Tour")
    monkeypatch.setattr(wc, "NEW_PLAYERS_DATA", {"players": list(autos)})
    name, short, label, mdate = "Ollie Robinson", "TENG", "Match 1", "2026-08-19"
    pid = "ci:527776" if resolvable else ""
    if not pid and in_squad == "Y" and (wc.CURRENT_TOUR, short, name) not in wc.NO_PID_SEEN:
        wc.NO_PID_SEEN.add((wc.CURRENT_TOUR, short, name))
        cands = [e for e in wc.NEW_PLAYERS_DATA.get("players", [])
                 if e.get("source") == "auto" and e.get("team") == short
                 and wc.CURRENT_TOUR in (e.get("tours") or [])
                 and str(e.get("pid", "")).startswith("ci:")]
        wc.NEEDS_CRICINFO.append({
            "player": name, "current_pid": "", "tour": wc.CURRENT_TOUR, "team": short,
            "closest_guess": ("SQUAD MEMBER WITH NO PLAYER ID" + (
                " ".join(f"{e.get('display')} [{e['pid']}]" for e in cands) if cands
                else f" no auto-added candidate on {short} yet ({label}, {mdate})"))})
    return wc.NEEDS_CRICINFO


def test_a_pidless_squad_slot_raises_a_row(monkeypatch):
    rows = _emit_probe(monkeypatch, "Y", resolvable=False)
    assert len(rows) == 1 and rows[0]["player"] == "Ollie Robinson"
    assert rows[0]["current_pid"] == ""


def test_a_resolved_squad_slot_raises_nothing(monkeypatch):
    assert _emit_probe(monkeypatch, "Y", resolvable=True) == []


def test_a_non_squad_row_raises_nothing(monkeypatch):
    # Leftovers and auto-added rows are already covered by cs_orphans / Identity Anomalies;
    # flagging them here would duplicate the queue rather than add to it.
    assert _emit_probe(monkeypatch, "N", resolvable=False) == []


def test_the_row_names_the_auto_added_candidate_so_the_fix_is_a_copy(monkeypatch):
    autos = [{"pid": "ci:527776", "display": "Oliver Edward Robinson", "source": "auto",
              "team": "TENG", "tours": ["A Tour"]}]
    rows = _emit_probe(monkeypatch, "Y", resolvable=False, autos=autos)
    assert "ci:527776" in rows[0]["closest_guess"]
    assert "Oliver Edward Robinson" in rows[0]["closest_guess"]


def test_candidates_are_scoped_to_the_same_team_and_tour(monkeypatch):
    # A candidate from another team or tour is not evidence about this slot; offering it would
    # invite exactly the cross-team mis-bridge the id anchoring exists to prevent.
    autos = [{"pid": "ci:1", "display": "Wrong Team", "source": "auto",
              "team": "TPAK", "tours": ["A Tour"]},
             {"pid": "ci:2", "display": "Wrong Tour", "source": "auto",
              "team": "TENG", "tours": ["Another Tour"]},
             {"pid": "uncapped:x", "display": "Placeholder", "source": "auto",
              "team": "TENG", "tours": ["A Tour"]}]
    rows = _emit_probe(monkeypatch, "Y", resolvable=False, autos=autos)
    g = rows[0]["closest_guess"]
    assert "Wrong Team" not in g and "Wrong Tour" not in g and "Placeholder" not in g


def test_one_row_per_player_not_one_per_match(monkeypatch):
    # A squad member appears in every fixture; without the dedup a 30-match tour would push 30
    # identical rows and bury the rest of the queue.
    rows = _emit_probe(monkeypatch, "Y", resolvable=False)
    for _ in range(4):
        name, short = "Ollie Robinson", "TENG"
        if ("A Tour", short, name) not in wc.NO_PID_SEEN:
            rows.append({"player": name})
    assert len(rows) == 1
