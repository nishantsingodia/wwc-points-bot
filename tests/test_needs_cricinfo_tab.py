"""'Needs Cricinfo ID' must be a VIEW over every pending identity gap, whichever process found it.

WHAT WENT WRONG (measured 14 Aug 2026). The tab had TWO writers with two sources and neither read
the other's:
  · tour_sync_finalize.write_needs_cricinfo_tab — reads registry/needs_cricinfo_pending.json, runs
    ONLY on tour INGEST;
  · wc_fps_to_csv.write_needs_cricinfo_tab      — runtime scoring discoveries, and its docstring
    said it "deliberately does NOT touch needs_cricinfo_pending.json" — implemented as never
    READING it either.
Cost: 23 CPL squad names sat in the pending file from the 13 Aug ingest onward while the live tab
showed 52 rows — 8 of them CPL, every one from the runtime path (2 `ci:`, 6 `cb:`). Nobody was ever
asked about the 23. The suppression concern behind the original exclusion was real but was about
WRITING that file (build_registry rewrites it wholesale); one guard applied a direction too wide.

The two fixtures are the REAL artefacts of that day, so this is a regression test and not a mock:
  · fixtures/needs_cricinfo_pending_20260813.json — the 23, as build_registry left them;
  · fixtures/needs_cricinfo_tab_20260814.json     — the live tab, 52 rows, 45 with a filled id
    (JSON, not CSV: `*.csv` is gitignored here, so a .csv fixture would pass locally and vanish
    in CI — the same written-but-never-read shape this file exists to test for).
No network: gspread is stubbed, open_gsheet is monkeypatched.
"""
import json
import os
import sys
import types

import pytest

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
HEADER = ["player", "current_pid", "tour", "team", "closest_guess", "cricinfo_id_FILL_HERE"]


@pytest.fixture
def fake_gspread(monkeypatch):
    mod = types.ModuleType("gspread")

    class WorksheetNotFound(Exception):
        pass

    mod.WorksheetNotFound = WorksheetNotFound
    monkeypatch.setitem(sys.modules, "gspread", mod)
    return mod


class _FakeWS:
    """A worksheet that remembers what was appended — append_rows is the only mutation the writer
    is allowed to make. There is deliberately no cell-write API here: a writer that tried to
    overwrite a human's filled-in id would fail this test with AttributeError."""

    def __init__(self, rows):
        self.rows = [list(r) for r in rows]
        self.appended = []

    def get_all_values(self):
        return [list(r) for r in self.rows]

    def append_rows(self, rows, **kw):
        self.appended.extend(rows)
        self.rows.extend(list(r) for r in rows)

    def update(self, **kw):                      # only used when the tab is created from scratch
        self.rows = [list(r) for r in kw.get("values", [])]


class _FakeSheet:
    def __init__(self, ws):
        self.ws = ws

    def worksheet(self, name):
        if self.ws is None:
            raise sys.modules["gspread"].WorksheetNotFound(name)
        return self.ws

    def add_worksheet(self, **kw):
        self.ws = _FakeWS([])
        return self.ws


def _live_tab():
    with open(os.path.join(FIX, "needs_cricinfo_tab_20260814.json")) as f:
        return [list(r) for r in json.load(f)["rows"]]


def _wire(wcmod, monkeypatch, tmp_path, tab_rows, pending, bridges=None):
    """Point the writer at a fake sheet + a temp pending/bridges pair, and return the worksheet."""
    ws = _FakeWS(tab_rows) if tab_rows is not None else None
    monkeypatch.setattr(wcmod, "open_gsheet", lambda: _FakeSheet(ws))
    p = tmp_path / "pending.json"
    p.write_text(json.dumps(pending))
    monkeypatch.setattr(wcmod, "PENDING_CI_PATH", str(p))
    b = tmp_path / "bridges.json"
    b.write_text(json.dumps(bridges if bridges is not None else {}))
    monkeypatch.setattr(wcmod, "CI_BRIDGES_PATH", str(b))
    wcmod.NEEDS_CRICINFO[:] = []
    return ws


# ── the 23 reach the human, and the 52 already there are left alone ──────────────────────────
def test_the_23_pending_reach_the_tab_and_none_of_the_live_52_are_reappended(
        wcmod, monkeypatch, tmp_path, fake_gspread):
    tab = _live_tab()
    pending = json.load(open(os.path.join(FIX, "needs_cricinfo_pending_20260813.json")))
    assert len(tab) == 53 and len(pending) == 23          # 1 header + 52 live rows
    filled = sum(1 for r in tab[1:] if len(r) > 5 and r[5].strip())
    assert filled == 45                                    # 45 answers a re-append would duplicate

    ws = _wire(wcmod, monkeypatch, tmp_path, tab, pending)
    wcmod.write_needs_cricinfo_tab()

    assert len(ws.appended) == 23                          # every pending gap reached the payload
    live_pids = {r[1].strip() for r in tab[1:]}
    assert [r for r in ws.appended if r[1] in live_pids] == []   # NONE of the 52 re-appended
    assert all(r[5] == "" for r in ws.appended)            # the fill column is never written to


def test_second_run_appends_nothing(wcmod, monkeypatch, tmp_path, fake_gspread):
    """Idempotence is the whole promise: the queue is append-only, so a re-run must be a no-op."""
    pending = json.load(open(os.path.join(FIX, "needs_cricinfo_pending_20260813.json")))
    ws = _wire(wcmod, monkeypatch, tmp_path, _live_tab(), pending)
    wcmod.write_needs_cricinfo_tab()
    assert len(ws.appended) == 23
    ws.appended.clear()
    wcmod.write_needs_cricinfo_tab()
    assert ws.appended == []


def test_runtime_row_wins_the_dedupe_over_the_same_pid_from_the_pending_file(
        wcmod, monkeypatch, tmp_path, fake_gspread):
    """Both sources can hold the same current_pid. The runtime row carries live per-match context
    ('official card only (Match 3 …)') the ingest-time snapshot cannot, so it must go first and the
    file's copy must not add a second row."""
    pending = [{"player": "Test Person", "current_pid": "uncapped:test-person",
                "tour": "T", "team": "X", "closest_guess": "snapshot guess"}]
    ws = _wire(wcmod, monkeypatch, tmp_path, [HEADER], pending)
    wcmod.NEEDS_CRICINFO[:] = [{"player": "Test Person", "current_pid": "uncapped:test-person",
                                "tour": "T", "team": "X", "closest_guess": "runtime context"}]
    wcmod.write_needs_cricinfo_tab()
    assert len(ws.appended) == 1
    assert ws.appended[0][4] == "runtime context"


# ── the two suppressions, and the direction they must fail in ────────────────────────────────
def test_an_answered_player_is_not_asked_again(wcmod, monkeypatch, tmp_path, fake_gspread):
    """A filled-in id is recorded into manual_ci_bridges.json, NOT back into the pending file
    (build_registry only rewrites that on the next build). Without this check the player is
    re-asked on every run from the moment his row is deleted from the tab."""
    pending = [{"player": "Odean Smith", "current_pid": "uncapped:odean-smith",
                "tour": "CPL", "team": "MTJAM", "closest_guess": "OF Smith"}]
    bridges = {"ci:600000": {"cricinfo_id": "600000", "names": ["odean smith"]}}
    ws = _wire(wcmod, monkeypatch, tmp_path, [HEADER], pending, bridges)
    wcmod.write_needs_cricinfo_tab()
    assert ws.appended == []


def test_the_slug_name_also_counts_as_answered(wcmod, monkeypatch, tmp_path, fake_gspread):
    """read_needs_cricinfo records BOTH spellings it can recover — the typed player name and the
    one carried inside a slug:/uncapped: pid. The suppression has to accept either, or a player
    answered under the slug spelling is asked again under his display name."""
    pending = [{"player": "Odean F Smith", "current_pid": "uncapped:odean-smith",
                "tour": "CPL", "team": "MTJAM", "closest_guess": ""}]
    bridges = {"ci:600000": {"cricinfo_id": "600000", "names": ["odean smith"]}}
    ws = _wire(wcmod, monkeypatch, tmp_path, [HEADER], pending, bridges)
    wcmod.write_needs_cricinfo_tab()
    assert ws.appended == []


def test_a_name_already_anchored_to_a_real_pid_is_not_asked(wcmod, monkeypatch, tmp_path,
                                                            fake_gspread):
    pending = [{"player": "Anchored Person", "current_pid": "uncapped:anchored-person",
                "tour": "CPL", "team": "MTJAM", "closest_guess": ""}]
    ws = _wire(wcmod, monkeypatch, tmp_path, [HEADER], pending)
    monkeypatch.setitem(wcmod.ALIAS2PID, wcmod.norm("Anchored Person"), "ci:123456")
    wcmod.write_needs_cricinfo_tab()
    assert ws.appended == []


def test_a_placeholder_anchor_is_not_an_answer(wcmod, monkeypatch, tmp_path, fake_gspread):
    """slug:/uncapped:/cs: are placeholders, not identity. Treating one as 'already anchored' is
    how a queue goes silent: EVERY row in the pending file resolves to its own uncapped: pid."""
    pending = [{"player": "Odean Smith", "current_pid": "uncapped:odean-smith",
                "tour": "CPL", "team": "MTJAM", "closest_guess": ""}]
    for placeholder in ("uncapped:odean-smith", "slug:odean-smith", "cs:9e707e02"):
        ws = _wire(wcmod, monkeypatch, tmp_path, [HEADER], pending)   # a fresh, empty tab each time
        monkeypatch.setitem(wcmod.ALIAS2PID, wcmod.norm("Odean Smith"), placeholder)
        wcmod.write_needs_cricinfo_tab()
        assert len(ws.appended) == 1, f"{placeholder} was read as an answer"


def test_unreadable_bridges_file_fails_open(wcmod, monkeypatch, tmp_path, fake_gspread):
    """An ABSENCE must never read as 'already handled'. A missing/corrupt bridges file means we
    know nothing about who has been answered — so surface the row, don't swallow it."""
    pending = [{"player": "Odean Smith", "current_pid": "uncapped:odean-smith",
                "tour": "CPL", "team": "MTJAM", "closest_guess": ""}]
    ws = _wire(wcmod, monkeypatch, tmp_path, [HEADER], pending)
    monkeypatch.setattr(wcmod, "CI_BRIDGES_PATH", str(tmp_path / "does-not-exist.json"))
    wcmod.write_needs_cricinfo_tab()
    assert len(ws.appended) == 1


def test_missing_pending_file_is_not_an_error(wcmod, monkeypatch, tmp_path, fake_gspread):
    ws = _wire(wcmod, monkeypatch, tmp_path, [HEADER], [])
    monkeypatch.setattr(wcmod, "PENDING_CI_PATH", str(tmp_path / "gone.json"))
    wcmod.write_needs_cricinfo_tab()
    assert ws.appended == []


# ── the dedupe key must be read the way the reader reads it ──────────────────────────────────
def test_dedupe_survives_a_column_inserted_before_current_pid(wcmod, monkeypatch, tmp_path,
                                                              fake_gspread):
    """read_needs_cricinfo resolves its columns BY HEADER NAME; a writer that reads index 1 blind
    would see every existing row as unrecognised and re-append all 52 — 45 of them answered."""
    tab = _live_tab()
    shifted = [["notes"] + tab[0]] + [[""] + r for r in tab[1:]]
    pending = json.load(open(os.path.join(FIX, "needs_cricinfo_pending_20260813.json")))
    ws = _wire(wcmod, monkeypatch, tmp_path, shifted, pending)
    wcmod.write_needs_cricinfo_tab()
    live_pids = {r[2].strip() for r in shifted[1:]}
    assert [r for r in ws.appended if r[1] in live_pids] == []
    assert len(ws.appended) == 23


def test_no_current_pid_column_holds_the_queue_instead_of_duplicating_it(
        wcmod, monkeypatch, tmp_path, fake_gspread):
    """A dedupe key we cannot read cannot promise no duplicates, and duplicating an answered row
    loses the answer. Refuse to append, loudly."""
    pending = json.load(open(os.path.join(FIX, "needs_cricinfo_pending_20260813.json")))
    ws = _wire(wcmod, monkeypatch, tmp_path, [["who", "id", "tour"]], pending)
    wcmod.write_needs_cricinfo_tab()
    assert ws.appended == []


# ── one tab, one writer ──────────────────────────────────────────────────────────────────────
def test_tour_ingest_and_the_scoring_run_share_one_implementation(wcmod, monkeypatch,
                                                                  fake_gspread):
    """tour_sync_finalize must DELEGATE, not carry a second copy. Two implementations of one tab
    is what produced the 23-row blind spot in the first place."""
    import tour_sync_finalize as finalize
    calls = []
    monkeypatch.setattr(wcmod, "write_needs_cricinfo_tab", lambda: calls.append(1))
    wcmod.NEEDS_CRICINFO[:] = [{"player": "leftover", "current_pid": "slug:leftover"}]
    finalize.write_needs_cricinfo_tab()
    assert calls == [1]
    assert wcmod.NEEDS_CRICINFO == []      # ingest merges no runtime discoveries


def test_finalize_never_writes_the_pending_file(wcmod, monkeypatch, tmp_path, fake_gspread):
    """build_registry owns registry/needs_cricinfo_pending.json outright — it rewrites it wholesale
    on every build. A second writer there would clobber it; that concern was the reason the two
    writers were kept apart, and it stays honoured: the tab is a READ-ONLY view of that file."""
    import tour_sync_finalize as finalize
    p = tmp_path / "pending.json"
    payload = [{"player": "Test Person", "current_pid": "uncapped:test-person",
                "tour": "T", "team": "X", "closest_guess": ""}]
    p.write_text(json.dumps(payload))
    before = p.read_text()
    monkeypatch.setattr(wcmod, "PENDING_CI_PATH", str(p))
    monkeypatch.setattr(wcmod, "CI_BRIDGES_PATH", str(tmp_path / "bridges.json"))
    ws = _FakeWS([HEADER])
    monkeypatch.setattr(wcmod, "open_gsheet", lambda: _FakeSheet(ws))
    finalize.write_needs_cricinfo_tab()
    assert len(ws.appended) == 1
    assert p.read_text() == before
