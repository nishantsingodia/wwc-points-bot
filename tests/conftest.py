"""Shared pytest fixtures. Imports the bot module (no network: main() only runs under
__main__; module-level just defines functions + loads the local registry JSON)."""
import os
import sys
import importlib

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
wc = importlib.import_module("wc_fps_to_csv")


@pytest.fixture
def wcmod():
    """The wc_fps_to_csv module under test."""
    return wc


@pytest.fixture
def perf():
    """Factory for a player-perf dict: starts from blank_perf, applies overrides.

        p = perf(r=56, b=38, played=True, **{"4s": 8})
    """
    def _make(name="Test Player", **over):
        p = wc.blank_perf(name)
        p.update(over)
        return p
    return _make


@pytest.fixture(autouse=True)
def _isolate_learned_identity():
    """Undo the identity a test taught the module.

    resolve_perf_pid LEARNS: an id-anchored resolution writes the feed spelling into the global
    ALIAS2PID so later rows in the same run (a cricapi row for the same person, say) resolve too.
    That is right in production and wrong across tests — one test minting a pid for
    "Totally Unknown Person" made a test in ANOTHER file see that name as known, and it failed
    with a resolution it never asked for. Snapshot and restore, so test order cannot matter.

    parse_espn REFUSES a short card by recording an ESPN_HOLDS entry, so any test that exercises
    the completeness gate leaves a hold behind for the next file to trip over. CB_STORE/CB_SERIES
    are the same shape of problem from the other direction: a Cricbuzz test that installs a bridge
    would make an unrelated test's compute_l1_gaps take the cricbuzz branch. Same reasoning,
    same fix.
    """
    alias = dict(wc.ALIAS2PID)
    learned = dict(wc.CS_LEARNED)
    needs = list(wc.NEEDS_CRICINFO)
    holds = dict(wc.ESPN_HOLDS)
    approved = set(wc.ESPN_CARD_SCORE_ANYWAY)
    cb_series, cb_store, cb_diag = wc.CB_SERIES, wc.CB_STORE, dict(wc.CB_DIAG)
    yield
    wc.ALIAS2PID.clear(); wc.ALIAS2PID.update(alias)
    wc.CS_LEARNED.clear(); wc.CS_LEARNED.update(learned)
    wc.NEEDS_CRICINFO[:] = needs
    wc.ESPN_HOLDS.clear(); wc.ESPN_HOLDS.update(holds)
    wc.ESPN_CARD_SCORE_ANYWAY.clear(); wc.ESPN_CARD_SCORE_ANYWAY.update(approved)
    wc.CB_SERIES, wc.CB_STORE = cb_series, cb_store
    wc.CB_DIAG.clear(); wc.CB_DIAG.update(cb_diag)
