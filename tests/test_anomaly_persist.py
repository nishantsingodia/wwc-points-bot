"""Identity-anomaly answers must PERSIST across runs (committed identity_splits.json), so an
answered anomaly stops re-surfacing even after its sheet row is dropped on the ephemeral runner."""
import json
import sys
import types
import pytest

_HEADER = ["Tour", "Type", "Player ID", "Display", "Players / Names Involved",
           "Bot Finding", "Different players? (Yes/No)", "Status"]


class _FakeWS:
    def __init__(self, rows):
        self._rows = rows

    def get_all_values(self):
        return self._rows


class _FakeSheet:
    def __init__(self, tabs):
        self._tabs = tabs

    def worksheet(self, name):
        if name not in self._tabs:
            raise sys.modules["gspread"].WorksheetNotFound(name)
        return _FakeWS(self._tabs[name])


@pytest.fixture
def fake_gspread(monkeypatch):
    mod = types.ModuleType("gspread")

    class WorksheetNotFound(Exception):
        pass

    mod.WorksheetNotFound = WorksheetNotFound
    monkeypatch.setitem(sys.modules, "gspread", mod)
    return mod


def _tab(*data_rows):
    return {"Identity Anomalies": [_HEADER] + [list(r) for r in data_rows]}


def _read(wcmod, monkeypatch, splits_file, tabs):
    monkeypatch.setattr(wcmod, "SPLITS_PATH", str(splits_file))
    monkeypatch.setattr(wcmod, "ANOMALY_TAB", "Identity Anomalies")
    monkeypatch.setattr(wcmod, "ANOMALY_ACK", set())      # fresh process each run
    monkeypatch.setattr(wcmod, "PRIOR_ANOMALY", {})
    monkeypatch.setattr(wcmod, "open_gsheet", lambda: _FakeSheet(tabs))
    wcmod.read_anomaly_confirmations()


def test_detected_anomaly_ack_persists_and_reseeds(wcmod, monkeypatch, fake_gspread, tmp_path):
    f = tmp_path / "identity_splits.json"
    f.write_text('{"splits": []}')

    # RUN 1 — you answer a detected false-merge "No" (same person, stop flagging)
    _read(wcmod, monkeypatch, f, _tab(
        ["WWC", "false merge", "f9d99806", "X", "a, b", "...", "No", "detected this run"]))
    assert ("false merge", "f9d99806") in wcmod.ANOMALY_ACK
    acks = json.load(open(f)).get("acks", [])
    assert any(a["pid"] == "f9d99806" and a["answer"] == "No" for a in acks)  # written to the ledger

    # RUN 2 — the row has been DROPPED from the sheet (answered), but the ack must survive
    _read(wcmod, monkeypatch, f, _tab())                  # sheet now has NO data rows
    assert ("false merge", "f9d99806") in wcmod.ANOMALY_ACK  # re-seeded from the file -> won't re-surface


def test_past_split_status_persists(wcmod, monkeypatch, fake_gspread, tmp_path):
    f = tmp_path / "identity_splits.json"
    f.write_text(json.dumps({"splits": [
        {"id": 1, "status": "applied-pending-vet", "players": [
            {"pid": "p1", "display": "A"}, {"pid": "p2", "display": "B"}]}]}))
    # you confirm the past split "Yes" (they really are different people)
    _read(wcmod, monkeypatch, f, _tab(
        ["—", "past split", "split:1", "A ↔ B", "p1=A | p2=B", "...", "Yes", "applied-pending-vet"]))
    s = json.load(open(f))["splits"][0]
    assert s["status"] == "confirmed"                     # persisted -> drops off next run (committed)
