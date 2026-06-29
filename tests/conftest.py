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
