"""Cricbuzz enumerates, ESPN confirms.

ESPN cannot answer "what starts next week?" — no fixtures endpoint, and a keyword search that
ranks historical editions first. Cricbuzz's schedule pages can, and they carry the category, the
formats, the dates, the teams and the L1 series id. These tests pin the FILTER, because a
discovery feed's whole job is deciding what NOT to ingest.
"""
import importlib.util
import sys
from datetime import datetime, timedelta, timezone

import pytest


def _load():
    spec = importlib.util.spec_from_file_location("ts_disc", "tour_sync.py")
    mod = importlib.util.module_from_spec(spec)
    argv, sys.argv = sys.argv, ["ts_disc"]
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.argv = argv
    return mod


ts = _load()
NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


def _series(name, category, **kw):
    base = {
        "series_id": kw.get("sid", 999), "name": name, "category": category,
        "formats": kw.get("formats", ["T20"]),
        "teams": kw.get("teams", ["India", "West Indies"]),
        "start": int((NOW + timedelta(days=kw.get("in_days", 1))).timestamp()),
        "end": None, "matches": 3,
    }
    return base


def _run(monkeypatch, rows, resolve=lambda *a: "8888"):
    import cricbuzz
    monkeypatch.setattr(cricbuzz, "upcoming_all", lambda *a, **k: rows)
    monkeypatch.setattr(ts, "resolve_espn_series", resolve)
    return ts.cricbuzz_discover(NOW, NOW + timedelta(days=ts.CB_WINDOW_DAYS))


@pytest.mark.parametrize("name,category,kw,kept", [
    ("West Indies tour of India, 2026", "International", {}, True),
    ("West Indies Women tour of Zimbabwe, 2026", "Women", {}, True),
    # Women is a PEER of International, not a subset — dropping it drops the women's game.
    ("Caribbean Premier League 2026", "League", {}, True),
    # A league we run no auction for is not worth a draft slot.
    ("CSA T20 Challenge 2026", "League", {}, False),
    # Cricbuzz files County/Irani/A-tours as Domestic; never ingested.
    ("County Championship Division One 2026", "Domestic", {}, False),
    # ...but it files SOME A/U19 sides under Women or International, so DENY still has to fire.
    ("Australia A Women tour of India 2026", "Women", {}, False),
    ("Australia U19 tour of India 2026", "International", {}, False),
    # Test-only series: the format sniff cannot ingest them, so they must not be half-added.
    ("England tour of Pakistan 2026", "International", {"formats": ["TEST"]}, False),
    # Outside the window in both directions.
    ("Some Tour 2027", "International", {"in_days": 400}, False),
    ("Some Old Tour 2026", "International", {"in_days": -30}, False),
    # A knockout-only shell with no named teams yet cannot be resolved or built.
    ("North American Cup 2026", "International", {"teams": []}, False),
])
def test_the_discovery_filter(monkeypatch, name, category, kw, kept):
    out = _run(monkeypatch, [_series(name, category, **kw)])
    assert bool(out) is kept


def test_the_a_in_india_is_not_an_a_team():
    """The denylist regex needs a real word boundary: "indiA TOUR OF england" contains the literal
    "a tour of", and a naive substring would deny every India tour ever played."""
    assert not ts.CB_SECOND_XI_RE.search("india tour of england 2026")
    assert not ts.CB_SECOND_XI_RE.search("australia tour of south africa 2026")
    assert ts.CB_SECOND_XI_RE.search("australia a tour of india 2026")
    assert ts.CB_SECOND_XI_RE.search("australia a women tour of india 2026")


def test_an_unconfirmed_espn_id_is_not_ingested(monkeypatch):
    """ESPN is the only BASE feed. A tour Cricbuzz knows about but ESPN cannot confirm on the
    fixture date has no scorecard source at all, so it must not be written."""
    out = _run(monkeypatch, [_series("Quadrangular T20I Series in Nigeria, 2026", "International")],
               resolve=lambda *a: "")
    assert out == []


def test_the_cricbuzz_id_is_carried_out_of_discovery(monkeypatch):
    """The L1 witness comes free here. Re-deriving it later by NAME is strictly worse evidence than
    the series id discovery already used as its key — and 10 of 19 live tours have no L1 at all."""
    out = _run(monkeypatch, [_series("West Indies tour of India, 2026", "International", sid=11902)])
    assert out == [("8888", "West Indies tour of India 2026", "11902")]


def test_a_feed_failure_is_never_reported_as_no_tours(monkeypatch, capsys):
    """Returning [] on an exception is the silent-swallow shape this module has been bitten by
    twice. It must say so out loud."""
    import cricbuzz

    def boom(*a, **k):
        raise RuntimeError("503")

    monkeypatch.setattr(cricbuzz, "upcoming_all", boom)
    assert ts.cricbuzz_discover(NOW, NOW + timedelta(days=10)) == []
    assert "NOT reporting 0 tours" in capsys.readouterr().err
