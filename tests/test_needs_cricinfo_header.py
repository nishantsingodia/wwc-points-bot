"""A 'Needs Cricinfo ID' tab whose ROW 1 IS BLANK eats every id the owner types.

WHAT WENT WRONG (measured 14 Aug 2026). With the worksheet present but headerless,
write_needs_cricinfo_tab appended data rows and never wrote a header. read_needs_cricinfo resolves
its columns BY NAME, so it then read the first DATA row as the header, found no
'cricinfo_id_FILL_HERE', and returned 0 — the answer discarded, silently, on every run, forever,
while the tab looked perfectly healthy:

    rows after write: [['Test Person', 'uncapped:test-person', 'T', 'X', 'g', '']]
    # a human fills the last column with 123456:
    read_needs_cricinfo added: 0   -> bridges file exists: False

Reachable whenever the ws.update that follows add_worksheet fails (rate limit) or a human clears
the tab. The repair must fire ONLY into a blank row 1 — writing a header over a live row would
destroy an answer, which is the one thing this tab is careful about.
"""
import json
import os

import pytest

from test_needs_cricinfo_tab import (FIX, HEADER, _FakeSheet, _FakeWS, _live_tab, _wire,  # noqa: F401
                                     fake_gspread)


class _RealisticWS(_FakeWS):
    """gspread's update(range_name='A1', values=[header]) writes ROW 1 ONLY. The shared fake
    replaces the whole sheet, which would hide the very thing this file has to prove: that the
    rows under a blank header survive the repair and are not re-appended as duplicates."""

    def update(self, **kw):
        vals = kw.get("values", [])
        for i, row in enumerate(vals):
            while len(self.rows) <= i:
                self.rows.append([])
            self.rows[i] = list(row)


def test_a_headerless_tab_gets_its_header_back_before_anything_is_appended(
        wcmod, monkeypatch, tmp_path, fake_gspread):
    pending = [{"player": "Test Person", "current_pid": "uncapped:test-person",
                "tour": "T", "team": "X", "closest_guess": "g"}]
    ws = _wire(wcmod, monkeypatch, tmp_path, [], pending)      # tab EXISTS, completely empty
    wcmod.write_needs_cricinfo_tab()
    assert ws.rows[0] == HEADER, "header not restored — the answers column is unreadable"
    assert ws.rows[1][1] == "uncapped:test-person"

    # ...and the loop actually CLOSES: the filled id now reads back into a bridge.
    ws.rows[1][5] = "123456"
    monkeypatch.setattr(wcmod, "CI_BRIDGES_PATH", str(tmp_path / "bridges.json"))
    monkeypatch.setattr(wcmod, "open_gsheet", lambda: _FakeSheet(ws))
    assert wcmod.read_needs_cricinfo() > 0
    assert json.load(open(tmp_path / "bridges.json"))["ci:123456"]["cricinfo_id"] == "123456"


def test_the_header_is_never_written_over_a_real_first_row(wcmod, monkeypatch, tmp_path,
                                                           fake_gspread):
    """The guard must be inert on a healthy tab: 52 live rows, 45 of them carrying an answer."""
    tab = _live_tab()
    pending = json.load(open(os.path.join(FIX, "needs_cricinfo_pending_20260813.json")))
    ws = _wire(wcmod, monkeypatch, tmp_path, tab, pending)
    wcmod.write_needs_cricinfo_tab()
    assert ws.rows[0] == tab[0] and ws.rows[1] == tab[1]


def test_rows_under_a_blank_header_are_kept_not_re_appended(wcmod, monkeypatch, tmp_path,
                                                            fake_gspread):
    """The repair restores row 1 and CARRIES THE SURVIVING ROWS OVER. Dropping them would empty
    the dedupe key set and re-append every row already on the tab — including answered ones, which
    is how a duplicate loses an answer."""
    tab = [["", "", "", "", "", ""],
           ["Already There", "uncapped:already-there", "T", "X", "g", "123"]]
    pending = [{"player": "Already There", "current_pid": "uncapped:already-there",
                "tour": "T", "team": "X", "closest_guess": "g"},
               {"player": "Brand New", "current_pid": "uncapped:brand-new",
                "tour": "T", "team": "X", "closest_guess": "g"}]
    ws = _RealisticWS(tab)
    monkeypatch.setattr(wcmod, "open_gsheet", lambda: _FakeSheet(ws))
    _wire(wcmod, monkeypatch, tmp_path, None, pending)      # temp pending/bridges paths + APPEARED
    monkeypatch.setattr(wcmod, "open_gsheet", lambda: _FakeSheet(ws))

    wcmod.write_needs_cricinfo_tab()
    assert ws.rows[0] == HEADER
    assert ws.rows[1] == ["Already There", "uncapped:already-there", "T", "X", "g", "123"]
    assert [r[1] for r in ws.appended] == ["uncapped:brand-new"], \
        "the answered row under the blank header was re-appended"
