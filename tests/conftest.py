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
    """
    alias = dict(wc.ALIAS2PID)
    learned = dict(wc.CS_LEARNED)
    needs = list(wc.NEEDS_CRICINFO)
    yield
    wc.ALIAS2PID.clear(); wc.ALIAS2PID.update(alias)
    wc.CS_LEARNED.clear(); wc.CS_LEARNED.update(learned)
    wc.NEEDS_CRICINFO[:] = needs
