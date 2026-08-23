"""The auto-anchor: a `ci:<cricinfo_id>` pid implies a cricsheet id via the crosswalk.

Before this existed, a player who lived ONLY in registry/new_players.json (the sheet-added and
silent-drop paths — build_registry.py never reads that file) had no cricsheet_id anywhere. So the
moment the OFFICIAL cricsheet card arrived, resolve_perf_pid's CS2PID lookup missed, the row fell
through to the name fallback, the name fallback correctly refused to guess, and Recon Review grew
an IDENTITY row for a player we already knew by id. Every debut produced a fresh one.
"""
import wc_fps_to_csv as wc


def test_a_ci_pid_is_anchored_to_its_cricsheet_id(monkeypatch):
    monkeypatch.setattr(wc, "CI2CS", {"999001": "deadbeef"})
    monkeypatch.setattr(wc, "CI_ALT", {})
    monkeypatch.setattr(wc, "CS2PID", {})
    assert wc.anchor_ci_pid("ci:999001") == "deadbeef"
    assert wc.CS2PID["deadbeef"] == "ci:999001"


def test_an_alternate_cricinfo_profile_id_folds_to_the_primary(monkeypatch):
    # people.csv carries alternate profile ids (_2/_3). A pid on the alternate must still find the
    # one cricsheet id, or the anchor silently misses exactly the players most likely to be new.
    monkeypatch.setattr(wc, "CI2CS", {"999001": "deadbeef"})
    monkeypatch.setattr(wc, "CI_ALT", {"999002": "999001"})
    monkeypatch.setattr(wc, "CS2PID", {})
    assert wc.anchor_ci_pid("ci:999002") == "deadbeef"
    # The pid stored is the pid as GIVEN, not the primary-id spelling. Rewriting a pid is the
    # identity-change ledger's job (_record_identity_change) — doing it silently here would fork
    # every pid-keyed store, which is the exact failure the ledger exists to make visible.
    assert wc.CS2PID["deadbeef"] == "ci:999002"


def test_a_placeholder_pid_is_never_anchored(monkeypatch):
    # slug:/uncapped: pids assert no verified identity. Anchoring one would fabricate an id link.
    monkeypatch.setattr(wc, "CI2CS", {"999001": "deadbeef"})
    monkeypatch.setattr(wc, "CS2PID", {})
    for pid in ("slug:some-name", "uncapped:some-name", "", None):
        assert wc.anchor_ci_pid(pid) is None
    assert wc.CS2PID == {}


def test_an_existing_anchor_wins(monkeypatch):
    # build_registry-derived mappings are authoritative; the auto-anchor only fills gaps.
    monkeypatch.setattr(wc, "CI2CS", {"999001": "deadbeef"})
    monkeypatch.setattr(wc, "CI_ALT", {})
    monkeypatch.setattr(wc, "CS2PID", {"deadbeef": "ci:111111"})
    wc.anchor_ci_pid("ci:999001")
    assert wc.CS2PID["deadbeef"] == "ci:111111"


def test_an_unknown_cricinfo_id_anchors_nothing(monkeypatch):
    monkeypatch.setattr(wc, "CI2CS", {})
    monkeypatch.setattr(wc, "CI_ALT", {})
    monkeypatch.setattr(wc, "CS2PID", {})
    assert wc.anchor_ci_pid("ci:404404") is None
    assert wc.CS2PID == {}


def test_load_new_players_anchors_every_ci_pid_it_loads(monkeypatch, tmp_path):
    np_file = tmp_path / "new_players.json"
    np_file.write_text('{"players": [{"pid": "ci:999001", "display": "A Debutant",'
                       ' "aliases": ["a debutant"]},'
                       ' {"pid": "slug:nobody", "display": "No Body"}]}')
    monkeypatch.setattr(wc, "NEW_PLAYERS_PATH", str(np_file))
    monkeypatch.setattr(wc, "CI2CS", {"999001": "deadbeef"})
    monkeypatch.setattr(wc, "CI_ALT", {})
    monkeypatch.setattr(wc, "CS2PID", {})
    monkeypatch.setattr(wc, "CS_ANCHORED", [])
    wc.load_new_players()
    # This is the whole point: the official card's cricsheet id now finds the pid, so
    # resolve_perf_pid never reaches the name fallback and no IDENTITY row is raised.
    assert wc.CS2PID == {"deadbeef": "ci:999001"}
    assert wc.CS_ANCHORED == [("ci:999001", "deadbeef")]


def test_the_real_registry_anchors_the_players_that_were_raising_identity_rows():
    # Regression against live data: these Hundred players existed only in new_players.json.
    wc.load_new_players()
    for cs, pid in [("e871a7a1", "ci:297482"),    # Ben William Sanderson
                    ("c672e80e", "ci:301648"),    # Jordan Clark
                    ("74a274cc", "ci:512907"),    # Thomas George Helm
                    ("d7ff1adc", "ci:1164537"),   # Charli Rae Knott
                    ("af24d8de", "ci:1488180"),   # Manny Lumsden
                    ("27fc5808", "ci:1143809"),   # Henry Thomas Crocombe
                    ("492ff0c2", "ci:1276980"),   # Ripon Mondol
                    ("7f232d46", "ci:1137283"),   # Thomas Fraser Rogers
                    ("a05d0552", "ci:748793")]:   # Joe James Weatherley
        assert wc.CS2PID.get(cs) == pid, f"cricsheet {cs} should anchor to {pid}"


def test_the_crosswalk_inversion_is_a_bijection():
    # CI2CS is built by inverting cs2ci. If two cricsheet ids ever claimed one cricinfo id, the
    # inversion would silently drop one — which is the merge-two-people failure mode.
    assert len(wc.CI2CS) == len(wc.CS2CI)
