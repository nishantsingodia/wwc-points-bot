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
    # A pending squad name only reaches the tab once the player has actually PLAYED — before his
    # debut there is no cricinfo id anywhere to ask for. These tests are about the DEDUPE and
    # ANSWERED rules, so mark the cohort as having appeared; the pre-debut rule has its own test.
    wcmod.APPEARED.clear()
    for e in pending:
        nm = str(e.get("player") or "")
        if nm:
            wcmod.APPEARED.add(wcmod.norm(nm))
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

    # 20, not 23: Mavendra Dindyal, Glenn Phillips and Dwaine Pretorius have since been ANCHORED to
    # real cricinfo ids (ci:1394274 / ci:823509 / ci:327830), so asking about them again would be
    # asking a question we have already answered. The fixture is a 13 Aug snapshot; the rule that
    # skips an anchored name is what makes the difference, and it is the rule worth pinning.
    anchored = {"Mavendra Dindyal", "Glenn Phillips", "Dwaine Pretorius"}
    assert len(ws.appended) == 23 - len(anchored)
    assert not (anchored & {r[0] for r in ws.appended}), "re-asked an already-anchored player"
    live_pids = {r[1].strip() for r in tab[1:]}
    assert [r for r in ws.appended if r[1] in live_pids] == []   # NONE of the 52 re-appended
    assert all(r[5] == "" for r in ws.appended)            # the fill column is never written to


def test_second_run_appends_nothing(wcmod, monkeypatch, tmp_path, fake_gspread):
    """Idempotence is the whole promise: the queue is append-only, so a re-run must be a no-op."""
    pending = json.load(open(os.path.join(FIX, "needs_cricinfo_pending_20260813.json")))
    ws = _wire(wcmod, monkeypatch, tmp_path, _live_tab(), pending)
    wcmod.write_needs_cricinfo_tab()
    assert len(ws.appended) == 20      # 23 minus the 3 since anchored — see the test above
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
    assert len(ws.appended) == 20      # 23 minus the 3 since anchored — see the first test


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
    wcmod.APPEARED.clear()
    wcmod.APPEARED.add(wcmod.norm("Test Person"))   # so a row is produced at all — see below
    finalize.write_needs_cricinfo_tab()
    assert len(ws.appended) == 1
    assert p.read_text() == before                  # THE POINT: the pending file is untouched


def test_tour_ingest_asks_nothing_because_nobody_has_played_yet(wcmod, monkeypatch, tmp_path,
                                                                fake_gspread):
    """A pleasant consequence of the pre-debut rule. tour_sync_finalize runs at INGEST, before a
    ball is bowled, so every unanchored squad name is by definition pre-debut and there is nothing
    a human could look up. The tab stays empty until someone actually plays."""
    import tour_sync_finalize as finalize
    p = tmp_path / "pending.json"
    p.write_text(json.dumps([{"player": "Fresh Signing", "current_pid": "uncapped:fresh-signing",
                              "tour": "T", "team": "X", "closest_guess": ""}]))
    monkeypatch.setattr(wcmod, "PENDING_CI_PATH", str(p))
    monkeypatch.setattr(wcmod, "CI_BRIDGES_PATH", str(tmp_path / "bridges.json"))
    ws = _FakeWS([HEADER])
    monkeypatch.setattr(wcmod, "open_gsheet", lambda: _FakeSheet(ws))
    wcmod.APPEARED.clear()
    finalize.write_needs_cricinfo_tab()
    assert ws.appended == []


# ── the pre-debut rule: do not ask a human for an id that does not exist ─────────────────────
def test_a_player_who_has_not_played_is_NOT_asked_about(wcmod, monkeypatch, tmp_path,
                                                        fake_gspread):
    """build_registry lists every squad name it could not anchor — but for an UNCAPPED player that
    is not a gap, it is just too early. He is absent from people.csv BECAUSE he is uncapped, may
    have no cricinfo page at all, and needs no id: his `uncapped:` pid joins fine because the sheet
    and the draft carry the SAME placeholder. The moment he debuts, ESPN's athlete.id IS his
    cricinfo id. Measured on CPL: of 23 surfaced, 21 had never played and 2 already had an id ESPN
    had given us — the correct number of questions for a human was ZERO.
    """
    pending = [{"player": "Never Played", "current_pid": "uncapped:never-played",
                "tour": "Caribbean Premier League 2026", "team": "MTBAR"}]
    ws = _wire(wcmod, monkeypatch, tmp_path, [["player", "current_pid", "tour", "team",
                                               "closest_guess", "cricinfo_id_FILL_HERE"]], pending)
    wcmod.APPEARED.clear()                      # he has not turned out
    wcmod.write_needs_cricinfo_tab()
    assert ws.appended == [], "asked a human for an id that does not exist yet"


def test_the_same_player_IS_asked_once_he_has_played(wcmod, monkeypatch, tmp_path, fake_gspread):
    """The mirror: a man who PLAYED and still has no id is a real question, and must be asked."""
    pending = [{"player": "Did Play", "current_pid": "uncapped:did-play",
                "tour": "Caribbean Premier League 2026", "team": "MTBAR"}]
    ws = _wire(wcmod, monkeypatch, tmp_path, [["player", "current_pid", "tour", "team",
                                               "closest_guess", "cricinfo_id_FILL_HERE"]], pending)
    wcmod.APPEARED.clear()
    wcmod.APPEARED.add(wcmod.norm("Did Play"))
    wcmod.write_needs_cricinfo_tab()
    assert len(ws.appended) == 1 and ws.appended[0][0] == "Did Play"


def test_note_appearance_only_counts_players_who_actually_featured(wcmod):
    wcmod.APPEARED.clear()
    wcmod.note_appearance({"a": {"name": "Played Man", "played": True},
                           "b": {"name": "Benched Man", "played": False}})
    assert wcmod.norm("Played Man") in wcmod.APPEARED
    assert wcmod.norm("Benched Man") not in wcmod.APPEARED


# ── an answered question must stop being asked ───────────────────────────────────────────────
class _DelWS(_FakeWS):
    def __init__(self, rows):
        super().__init__(rows)
        self.deleted = []

    def delete_rows(self, i):
        self.deleted.append(i)
        del self.rows[i - 1]


def _wire_read(wcmod, monkeypatch, tmp_path, rows):
    ws = _DelWS(rows)
    monkeypatch.setattr(wcmod, "open_gsheet", lambda: _FakeSheet(ws))
    b = tmp_path / "bridges.json"; b.write_text("{}")
    monkeypatch.setattr(wcmod, "CI_BRIDGES_PATH", str(b))
    return ws


def test_an_answered_row_is_retired_from_the_tab(wcmod, monkeypatch, tmp_path, fake_gspread):
    """The writer is append-only, so nothing ever removed an answered row: 45 of 75 rows on the
    live tab were filled in and still being shown, burying the few that needed a human. The answer
    is not lost — it lives in manual_ci_bridges.json, which is what build_registry anchors from."""
    ws = _wire_read(wcmod, monkeypatch, tmp_path, [
        HEADER,
        ["Answered Player", "uncapped:answered-player", "T", "X", "", "1234567"],
        ["Open Player", "uncapped:open-player", "T", "X", "", ""],
    ])
    wcmod.read_needs_cricinfo()
    assert ws.deleted == [2], "the answered row was not retired"
    assert [r[0] for r in ws.rows] == ["player", "Open Player"], "retired the wrong row"
    assert "1234567" in open(wcmod.CI_BRIDGES_PATH).read(), "answer was dropped, not stored"


def test_an_unanswered_row_is_never_retired(wcmod, monkeypatch, tmp_path, fake_gspread):
    ws = _wire_read(wcmod, monkeypatch, tmp_path,
                    [HEADER, ["Open Player", "uncapped:open-player", "T", "X", "", ""]])
    wcmod.read_needs_cricinfo()
    assert ws.deleted == [] and len(ws.rows) == 2


def test_a_bad_id_is_not_retired_so_it_can_be_corrected(wcmod, monkeypatch, tmp_path,
                                                        fake_gspread):
    """A typo is not an answer. Retiring it would silently swallow the correction."""
    ws = _wire_read(wcmod, monkeypatch, tmp_path,
                    [HEADER, ["Typo Player", "uncapped:typo-player", "T", "X", "", "not-an-id"]])
    wcmod.read_needs_cricinfo()
    assert ws.deleted == [], "a row with an unusable id was retired"


# ── a question nobody can answer must not sit in the queue ───────────────────────────────────
def test_a_stranded_pre_debut_row_is_retired(wcmod, monkeypatch, tmp_path, fake_gspread):
    """The pre-debut rule stops NEW pre-debut names being written, but rows appended before it
    existed were stranded: 23 uncapped CPL squad names sat unanswered, 21 of them unanswerable —
    an uncapped player who has not played has no cricinfo id ANYWHERE. Leaving them buries the
    rows that do need a human."""
    tab = [HEADER,
           ["Never Played", "uncapped:never-played", "T", "X", "", ""],
           ["Did Play", "uncapped:did-play", "T", "X", "", ""]]
    ws = _DelWS(tab)
    monkeypatch.setattr(wcmod, "open_gsheet", lambda: _FakeSheet(ws))
    p = tmp_path / "pending.json"; p.write_text("[]")
    monkeypatch.setattr(wcmod, "PENDING_CI_PATH", str(p))
    b = tmp_path / "bridges.json"; b.write_text("{}")
    monkeypatch.setattr(wcmod, "CI_BRIDGES_PATH", str(b))
    wcmod.NEEDS_CRICINFO[:] = []
    wcmod.APPEARED.clear()
    wcmod.APPEARED.add(wcmod.norm("Did Play"))          # one of the two turned out
    wcmod.write_needs_cricinfo_tab()
    assert ws.deleted == [2], "the unanswerable row was not retired"
    assert [r[0] for r in ws.rows] == ["player", "Did Play"], "retired the wrong row"


def test_an_ANSWERED_pre_debut_row_is_left_for_the_reader(wcmod, monkeypatch, tmp_path,
                                                          fake_gspread):
    """Retiring an answered row here would throw the answer away — read_needs_cricinfo consumes it
    into manual_ci_bridges first, and only then retires it."""
    tab = [HEADER, ["Never Played", "uncapped:never-played", "T", "X", "", "1234567"]]
    ws = _DelWS(tab)
    monkeypatch.setattr(wcmod, "open_gsheet", lambda: _FakeSheet(ws))
    p = tmp_path / "pending.json"; p.write_text("[]")
    monkeypatch.setattr(wcmod, "PENDING_CI_PATH", str(p))
    b = tmp_path / "bridges.json"; b.write_text("{}")
    monkeypatch.setattr(wcmod, "CI_BRIDGES_PATH", str(b))
    wcmod.NEEDS_CRICINFO[:] = []
    wcmod.APPEARED.clear()
    wcmod.write_needs_cricinfo_tab()
    assert ws.deleted == [], "an answered row was retired before its answer was consumed"


def test_nothing_is_retired_when_no_match_was_scored(wcmod, monkeypatch, tmp_path, fake_gspread):
    """APPEARED empty means no card was read this run — not that nobody played. Retiring then
    would empty the whole tab on any run that scored nothing."""
    tab = [HEADER, ["Never Played", "uncapped:never-played", "T", "X", "", ""]]
    ws = _DelWS(tab)
    monkeypatch.setattr(wcmod, "open_gsheet", lambda: _FakeSheet(ws))
    p = tmp_path / "pending.json"; p.write_text("[]")
    monkeypatch.setattr(wcmod, "PENDING_CI_PATH", str(p))
    b = tmp_path / "bridges.json"; b.write_text("{}")
    monkeypatch.setattr(wcmod, "CI_BRIDGES_PATH", str(b))
    wcmod.NEEDS_CRICINFO[:] = []
    wcmod.APPEARED.clear()
    wcmod.write_needs_cricinfo_tab()
    assert ws.deleted == []
