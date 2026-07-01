"""Tests for the sheet-driven 'New' player feature: slugify, find_silent_drops (the lapse),
register_new_player (identity + membership), load_new_players, and the Jane Maguire regression."""
import sys
import types
import pytest


@pytest.fixture(autouse=True)
def _isolate(wcmod, monkeypatch):
    # snapshot the global registry + new-players ledger so each test's mutations are reverted
    monkeypatch.setattr(wcmod, "ALIAS2PID", dict(wcmod.ALIAS2PID))
    monkeypatch.setattr(wcmod, "PID2DISP", dict(wcmod.PID2DISP))
    monkeypatch.setattr(wcmod, "NEW_PLAYERS_DATA", {"players": []})


def test_slugify(wcmod):
    assert wcmod.slugify("Jane Maguire") == "slug:jane-maguire"
    assert wcmod.slugify("Renuka Singh Thakur") == "slug:renuka-singh-thakur"


# ── find_silent_drops: the lapse (resolved + played + no squad slot) ─────────
def test_find_silent_drops_catches_unclaimed_resolved_player(wcmod):
    wcmod.ALIAS2PID[wcmod.norm("Known Player")] = "slug:known-player"
    wcmod.ALIAS2PID[wcmod.norm("Squad Star")] = "pid:squad-star"
    perf = {
        wcmod.norm("Known Player"): {"name": "Known Player", "played": True, "r": 30},  # resolves, not in squad
        wcmod.norm("Squad Star"): {"name": "Squad Star", "played": True, "r": 50},       # in squad -> claimed
        wcmod.norm("Bench Warmer"): {"name": "Known Player B", "played": False},          # didn't play
    }
    team_players = [("IRE", "Squad Star", "BAT")]
    assigned = {("IRE", "Squad Star"): perf[wcmod.norm("Squad Star")]}
    drops = wcmod.find_silent_drops(perf, assigned, team_players)
    assert [pid for pid, _ in drops] == ["slug:known-player"]


def test_find_silent_drops_skips_squad_member(wcmod):
    # GUARD: if her pid IS already a squad slot, NOT a silent drop -> no auto-add -> no double count
    wcmod.ALIAS2PID[wcmod.norm("Known Player")] = "slug:known-player"
    perf = {wcmod.norm("Known Player"): {"name": "Known Player", "played": True}}
    team_players = [("IRE", "Known Player", "BOWL")]
    assigned = {("IRE", "Known Player"): perf[wcmod.norm("Known Player")]}
    assert wcmod.find_silent_drops(perf, assigned, team_players) == []


def test_find_silent_drops_ignores_no_pid(wcmod):
    # a played feed player with NO pid is a normal Needs-Review leftover, not a silent drop
    assert wcmod.resolve_pid("Totally Unknown Person") is None
    perf = {wcmod.norm("Totally Unknown Person"): {"name": "Totally Unknown Person", "played": True}}
    assert wcmod.find_silent_drops(perf, {}, []) == []


# ── register_new_player: identity + membership, deduped/merged ──────────────
def test_register_new_player_builds_record_and_resolves(wcmod):
    e = wcmod.register_new_player(pid="slug:jane-maguire", display="Jane Maguire", feed="J Maguire",
                                  team="IRE", role="BOWL", tour="Women's T20 WC 2026", source="new")
    assert e["pid"] == "slug:jane-maguire" and e["display"] == "Jane Maguire"
    assert "j maguire" in e["aliases"] and e["team"] == "IRE" and e["role"] == "BOWL"
    assert e["tours"] == ["Women's T20 WC 2026"] and e["source"] == "new"
    # identity reflected immediately so she resolves THIS run (feed spelling AND display)
    assert wcmod.resolve_pid("J Maguire") == "slug:jane-maguire"
    assert wcmod.resolve_pid("Jane Maguire") == "slug:jane-maguire"


def test_register_new_player_merges_by_pid(wcmod):
    wcmod.register_new_player("slug:x", "X Player", "X feed", "IRE", "BOWL", "Tour A", "new")
    wcmod.register_new_player("slug:x", "X Player", "X2 feed", "IRE", "BOWL", "Tour B", "auto")
    players = wcmod.NEW_PLAYERS_DATA["players"]
    assert len(players) == 1                                   # deduped by pid
    assert set(players[0]["tours"]) == {"Tour A", "Tour B"}    # tours accumulate
    assert {"x feed", "x2 feed"} <= set(players[0]["aliases"]) # aliases accumulate


# ── load_new_players: merges identity from the committed file ───────────────
def test_load_new_players_merges_identity(wcmod, monkeypatch, tmp_path):
    f = tmp_path / "new_players.json"
    f.write_text('{"players":[{"pid":"slug:x-y","display":"X Y","aliases":["x y feed"],'
                 '"team":"IRE","role":"BOWL","tours":["T"]}]}')
    monkeypatch.setattr(wcmod, "NEW_PLAYERS_PATH", str(f))
    wcmod.load_new_players()
    assert wcmod.resolve_pid("x y feed") == "slug:x-y"
    assert wcmod.resolve_pid("X Y") == "slug:x-y"
    assert wcmod.PID2DISP.get("slug:x-y") == "X Y"


# ── Jane Maguire regression (the headline case) ─────────────────────────────
def test_jane_maguire_new_flow(wcmod):
    assert wcmod.resolve_pid("J Maguire") is None                  # not known before
    pid = wcmod.resolve_pid("J Maguire") or wcmod.slugify("Jane Maguire")
    wcmod.register_new_player(pid=pid, display="Jane Maguire", feed="J Maguire", team="Ireland",
                              role="BOWL", tour="Women's T20 WC 2026", source="new")
    assert wcmod.resolve_pid("J Maguire") == "slug:jane-maguire"   # now resolves -> assigned + emitted next run
    e = wcmod.NEW_PLAYERS_DATA["players"][0]
    assert e["team"] == "Ireland" and e["role"] == "BOWL" and e["source"] == "new"


# ── "New" on an EXISTING player must LINK, not duplicate (surname-collision safety) ──
def test_new_reuses_existing_identity(wcmod, monkeypatch):
    # Finn Allen is already in the registry; marking a new spelling of him "New" must reuse his
    # real pid (link the alias) — NOT mint slug:finn-allen and split him in two.
    assert wcmod.resolve_pid("Finn Allen") == "bf74b130"
    gs = types.ModuleType("gspread")
    gs.WorksheetNotFound = type("WorksheetNotFound", (Exception,), {})
    monkeypatch.setitem(sys.modules, "gspread", gs)
    header = ["Tour", "Team", "Feed Name", "Closest Match", "Role", "Correct? (Yes/No/New)"]
    rows = [header, ["Major League Cricket 2026", "Texas SC", "Fin Allen", "Finn Allen", "BAT", "New"]]
    monkeypatch.setattr(wcmod, "open_gsheet",
                        lambda: type("SH", (), {"worksheet": lambda s, n: type(
                            "WS", (), {"get_all_values": lambda s: rows})()})())
    wcmod.read_review_confirmations()
    entry = next(e for e in wcmod.NEW_PLAYERS_DATA["players"] if "fin allen" in e.get("aliases", []))
    assert entry["pid"] == "bf74b130"                              # reused Finn's identity, no duplicate slug
