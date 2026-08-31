"""TOUR CONTROL must show all three feeds, and its header must be able to CHANGE.

Two things this pins, both of which failed silently in production:

1. THE HEADER WAS FROZEN. The only header write in sync_tour_control was guarded by "is there no
   column starting with espn_series?" — a test the column it was itself adding then satisfied
   forever. It fired once on 14 Aug 2026; the 20 Aug rename (cricapi_series -> espn_series,
   "Poll cricapi?" -> "Score this tour?") shipped after that and NEVER reached the live tab, which
   still read "Poll cricapi?" on 28 Aug with cricapi deleted from the codebase eight days earlier.
   Any column added later was equally unreachable. So the header must sync on ANY difference.

2. THE THREE FEEDS WERE INVISIBLE. ESPN is the base card, Cricbuzz the L1 second witness, cricsheet
   the L2 official source. A tour missing L1 publishes every match COMPLETED_FLAGGED "single feed
   (ESPN only)"; one missing L2 is never made official at all. Neither gap appeared anywhere the
   owner looks — only in a run log.
"""
import pytest


class _FakeWS:
    """gspread's update(range_name=..) writes THAT RANGE ONLY. Replacing the whole sheet would hide
    the thing under test: that decisions below row 1 survive a header sync."""

    def __init__(self, rows, title="TOUR CONTROL"):
        self.rows = [list(r) for r in rows]
        self.title = title

    def get_all_values(self):
        return [list(r) for r in self.rows]

    def update(self, range_name=None, values=None, value_input_option=None):
        a1 = (range_name or "A1").split(":")[0]
        col = ord(a1[0]) - ord("A")
        row0 = int(a1[1:] or 1) - 1
        for i, row in enumerate(values or []):
            while len(self.rows) <= row0 + i:
                self.rows.append([])
            target = self.rows[row0 + i]
            for j, v in enumerate(row):
                while len(target) <= col + j:
                    target.append("")
                target[col + j] = v

    def append_rows(self, rows, value_input_option=None):
        self.rows.extend([list(r) for r in rows])


class _FakeSheet:
    def __init__(self, ws):
        self._ws = ws

    def worksheet(self, title):
        return self._ws

    def worksheets(self):
        return [self._ws]

    def add_worksheet(self, title=None, rows=None, cols=None):
        return self._ws


STALE = ["Tour", "Tab", "cricapi_series", "Poll cricapi? (yes/no)", "Notes",
         "espn_series (optional)"]


def _tour(name="Lanka Premier League 2026", **over):
    t = {"name": name, "tab": "LPL 2026 POINTS", "espn_series": "1537330",
         "cricbuzz_series": "12316", "gender": "male", "ends": "2099-01-01"}
    t.update(over)
    return t


def _wire(wcmod, monkeypatch, ws, report=None):
    monkeypatch.setattr(wcmod, "open_gsheet", lambda: _FakeSheet(ws))
    monkeypatch.setattr(wcmod, "_load_cricsheet_report", lambda: report or {})


def test_a_stale_header_is_rewritten_and_the_owners_decisions_survive(wcmod, monkeypatch):
    ws = _FakeWS([STALE, ["Lanka Premier League 2026", "LPL 2026 POINTS", "old-uuid", "yes", "", ""]])
    _wire(wcmod, monkeypatch, ws)
    ctrl = wcmod.sync_tour_control([_tour()])

    assert ws.rows[0][2] == "espn_series", "column C still advertises the deleted cricapi feed"
    assert ws.rows[0][3].startswith("Score this tour?"), "the decision column was never renamed"
    assert "cricapi" not in " ".join(ws.rows[0]).lower()
    assert ws.rows[1][3] == "yes", "the header sync overwrote a human decision"
    assert ctrl[wcmod.re.sub(r"[^a-z0-9]+", "", "lanka premier league 2026")] == "yes"


def test_the_header_sync_keeps_a_column_the_owner_added_himself(wcmod, monkeypatch):
    ws = _FakeWS([STALE + ["My own notes"], ["Lanka Premier League 2026", "LPL 2026 POINTS",
                                             "u", "yes", "", "", "keep me"]])
    _wire(wcmod, monkeypatch, ws)
    wcmod.sync_tour_control([_tour()])
    # Feeds must find a slot of its OWN, not evict whatever already sat in the 7th cell.
    assert ws.rows[0][6] == "My own notes", "a human's extra column was overwritten by Feeds"
    assert ws.rows[1][6] == "keep me", "his data no longer lines up under his own header"
    fi = next(i for i, h in enumerate(ws.rows[0]) if h.startswith("Feeds"))
    assert fi > 6 and "12316" in ws.rows[1][fi]


def test_all_three_feeds_are_reported_into_the_tab(wcmod, monkeypatch):
    ws = _FakeWS([STALE, ["Lanka Premier League 2026", "LPL 2026 POINTS", "u", "yes", "", ""]])
    _wire(wcmod, monkeypatch, ws,
          report={"Lanka Premier League 2026": {"archive": "lpl_json.zip", "downloaded": True}})
    wcmod.sync_tour_control([_tour()])
    feeds = ws.rows[1][len(STALE)]                      # the new Feeds column, one past the old end
    assert "1537330" in feeds and "12316" in feeds and "lpl_json.zip" in feeds
    assert "✗" not in feeds, f"a complete tour reported a gap: {feeds}"


@pytest.mark.parametrize("tour_over, report, missing", [
    ({"cricbuzz_series": ""}, {"archive": "lpl_json.zip", "downloaded": True}, "L1"),
    ({}, {"archive": "", "downloaded": False}, "L2"),
    ({"espn_series": ""}, {"archive": "lpl_json.zip", "downloaded": True}, "ESPN"),
])
def test_a_missing_feed_is_shouted_not_omitted(wcmod, tour_over, report, missing):
    """The gap has to be VISIBLE. A tour quietly listing two feeds reads like a healthy one."""
    cell = wcmod._feeds_cell(_tour(**tour_over), {"Lanka Premier League 2026": report})
    assert "✗" in cell, f"{missing} missing but nothing flagged it: {cell}"
    assert missing in cell


def test_no_cricsheet_report_says_unknown_rather_than_claiming_a_gap(wcmod):
    """A local run has no download step, so L2 is UNKNOWN. Rendering that as ✗ would train the
    owner to ignore the very marker that means 'this tour is never reconciled'."""
    cell = wcmod._feeds_cell(_tour(), {})
    assert "L2 ?" in cell and "✗" not in cell
