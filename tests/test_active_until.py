"""`active_until` — an explicit per-tour extension past dormant AND frozen.

A tour is skipped by two independent mechanisms, both of which exist to ration cricapi's 100/day
budget rather than for correctness: `dormant` (ends + FREEZE_GRACE_DAYS) and `frozen_tours.json`
(every match resolved off cricsheet). Neither can be reached again once it applies — so a scoring
fix made AFTER a tour closes never lands on it, which is precisely when such fixes arrive, because
the official cards only exist to find bugs with once the tour is over.

Raising FREEZE_GRACE_DAYS is the wrong lever: it is global, so reaching one finished tour wakes
every finished tour. FREEZE_GRACE_DAYS' own comment records that regression — "the old 21 kept ~6
tours active, draining the daily cricapi budget before the still-live tour was even reached."
"""
from datetime import date, timedelta

import pytest


def _t(**kw):
    base = {"name": "T", "ends": "2020-01-01", "cricapi_series": "abc"}
    base.update(kw)
    return base


def test_a_long_dead_tour_is_dormant_by_default(wcmod):
    assert wcmod.is_active(_t()) is False


def test_active_until_in_the_future_holds_a_dead_tour_open(wcmod):
    far = (date.today() + timedelta(days=30)).isoformat()
    assert wcmod.is_active(_t(active_until=far)) is True
    assert wcmod._held_open(_t(active_until=far)) is True


def test_active_until_in_the_PAST_does_not_hold_it_open(wcmod):
    """The field must expire on its own — a hold left behind forever silently re-scores a settled
    tour on every run, which is the cost the skip exists to avoid."""
    assert wcmod._held_open(_t(active_until="2020-06-01")) is False
    assert wcmod.is_active(_t(active_until="2020-06-01")) is False


def test_today_is_inclusive(wcmod):
    assert wcmod._held_open(_t(active_until=date.today().isoformat())) is True


def test_a_malformed_date_is_IGNORED_not_treated_as_open(wcmod, capsys):
    """An unparseable override must not silently hold a tour open forever — say so and fall back
    to the normal window."""
    assert wcmod._held_open(_t(active_until="next Tuesday")) is False
    assert "not an ISO date" in capsys.readouterr().err


def test_an_absent_field_changes_nothing(wcmod):
    for v in (None, "", "   "):
        assert wcmod._held_open(_t(active_until=v)) is False
    assert wcmod.held_open_until(_t()) == ""


def test_a_still_live_tour_is_unaffected(wcmod):
    far = (date.today() + timedelta(days=30)).isoformat()
    assert wcmod.is_active(_t(ends=far)) is True


def test_a_tour_with_no_end_date_is_always_active(wcmod):
    assert wcmod.is_active({"name": "T"}) is True


def test_the_three_held_tours_are_configured_and_dated(wcmod):
    """Pins the live config: the tours held open for the Dream11 cross-check, and the fact that
    each carries an EXPIRY. If this fails because a hold was removed, that is fine — delete the
    case. If it fails because a hold has no date, that is the leak this test is for."""
    import json
    d = json.load(open("tours.json"))
    ts = d if isinstance(d, list) else d["tours"]
    held = {t["name"]: t["active_until"] for t in ts if t.get("active_until")}
    assert set(held) == {
        "Lanka Premier League 2026",
        "The Hundred Men's Competition 2026",
        "The Hundred Women's Competition 2026",
    }, held
    for nm, until in held.items():
        date.fromisoformat(until)          # raises if not a real date
