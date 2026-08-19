"""The re-key ledger: an append-only record that one pid became another.

This is the primitive whose ABSENCE caused the defect. `pid` is a primary key in nine stores but
is a mutable label, and nothing recorded that one had moved — so `record_settlement`'s write-once
guard on `(match_key, pid)` could not fire when the key itself moved (Gus Atkinson: 9 matches,
618 points, settled twice), and the draft's pid-authoritative `matchPlayerInXI` judged a player
who PLAYED to be absent (Joshua James, Amari Goodridge: a real XI slot silently deleted).

The tests that matter here are the REFUSALS. A ledger that resolves a contradiction by picking a
side would perform a silent wrong merge inside the money baseline — worse than having no ledger,
because it would look authoritative while doing it.
"""
import json

import pytest

from registry import identity_changes as IC


@pytest.fixture
def led(tmp_path):
    return str(tmp_path / "identity_changes.json")


def _rec(path, *edges, reason="test", evidence="fixture"):
    for a, b in edges:
        IC.record(a, b, reason, evidence, at="2026-08-19", path=path)


# ── the happy path ──────────────────────────────────────────────────────────────────────────
def test_a_recorded_change_is_followed(led):
    _rec(led, ("uncapped:joshua-james", "ci:1209191"))
    assert IC.canonical_pid("uncapped:joshua-james", led) == "ci:1209191"


def test_a_pid_that_never_moved_is_returned_unchanged(led):
    _rec(led, ("a", "b"))
    assert IC.canonical_pid("ci:823509", led) == "ci:823509"


def test_a_chain_is_followed_to_the_end(led):
    """slug: -> uncapped: -> ci: really happens; a reader must land on the pid in force today."""
    _rec(led, ("slug:x", "uncapped:x"), ("uncapped:x", "ci:99"))
    assert IC.canonical_pid("slug:x", led) == "ci:99"
    assert IC.compile_map(led) == {"slug:x": "ci:99", "uncapped:x": "ci:99"}


def test_recording_is_idempotent(led):
    assert IC.record("a", "b", "r", "e", at="x", path=led) is True
    assert IC.record("a", "b", "r", "e", at="x", path=led) is False
    assert len(IC.load(led)["changes"]) == 1


def test_the_log_is_append_only_and_keeps_provenance(led):
    IC.record("a", "b", "promote", "cricinfo x-123 confirmed by owner", at="2026-08-19", path=led)
    IC.record("c", "d", "bridge", "fingerprint cb:15810", at="2026-08-19", path=led)
    got = IC.load(led)["changes"]
    assert [c["from"] for c in got] == ["a", "c"], "order preserved, nothing rewritten"
    assert got[0]["evidence"] == "cricinfo x-123 confirmed by owner"
    assert got[0]["reason"] == "promote"


# ── the refusals, which are the point ───────────────────────────────────────────────────────
def test_a_fork_is_REFUSED_not_resolved(led):
    """Two claims about what one pid became. Picking either would be a silent wrong merge inside
    the write-once money baseline. The pid must come back untouched."""
    _rec(led, ("ci:900", "ci:111"), ("ci:900", "ci:222"))
    assert IC.canonical_pid("ci:900", led) == "ci:900"
    assert IC.forks(led) == {"ci:900": ["ci:111", "ci:222"]}
    assert "ci:900" not in IC.compile_map(led)


def test_a_fork_LATER_in_a_chain_stops_the_walk_there(led):
    """The walk must not sail past a contradiction just because it started somewhere clean."""
    _rec(led, ("a", "b"), ("b", "c1"), ("b", "c2"))
    assert IC.canonical_pid("a", led) == "b"
    assert IC.canonical_pid("b", led) == "b"


def test_a_cycle_returns_the_input_rather_than_looping(led):
    _rec(led, ("a", "b"), ("b", "a"))
    assert IC.canonical_pid("a", led) == "a"
    assert IC.canonical_pid("b", led) == "b"


def test_a_longer_cycle_also_terminates(led):
    _rec(led, ("a", "b"), ("b", "c"), ("c", "a"))
    for p in ("a", "b", "c"):
        assert IC.canonical_pid(p, led) == p


def test_a_merge_without_evidence_is_refused(led):
    """An unexplained identity merge is unauditable a month later — which is exactly when it will
    be read, by someone deciding whether a settled number is real."""
    with pytest.raises(ValueError):
        IC.record("a", "b", "promote", "", at="x", path=led)
    with pytest.raises(ValueError):
        IC.record("a", "b", "", "some evidence", at="x", path=led)


def test_a_self_edge_is_a_noop(led):
    assert IC.record("a", "a", "r", "e", at="x", path=led) is False
    assert IC.load(led)["changes"] == []


# ── absence must not present as a value ─────────────────────────────────────────────────────
def test_a_missing_ledger_reads_as_empty_not_as_an_error(led):
    assert IC.load(led)["changes"] == []
    assert IC.canonical_pid("anything", led) == "anything"


def test_a_CORRUPT_ledger_is_NOT_an_empty_ledger(led, tmp_path):
    """The failure mode that keeps recurring in this repo: `except Exception: return {}`. A
    truncated ledger read as empty would let a re-key proceed with no record — the very thing
    this file exists to prevent — and the run would stay green."""
    with open(led, "w") as f:
        f.write('{"note": "x", "changes": [{"from": "a", "to": "b"},')   # truncated mid-array
    with pytest.raises(ValueError):
        IC.load(led)


def test_a_ledger_of_the_wrong_SHAPE_is_refused_too(led):
    with open(led, "w") as f:
        json.dump({"note": "x", "changes": {"a": "b"}}, f)               # dict, not a list
    with pytest.raises(ValueError):
        IC.load(led)


# ── determinism: every view is a pure function of the log ───────────────────────────────────
def test_compile_is_order_independent_and_clock_free(led, tmp_path):
    edges = [("a", "b"), ("b", "c"), ("x", "y")]
    _rec(led, *edges)
    fwd = IC.compile_map(led)
    rev = str(tmp_path / "rev.json")
    _rec(rev, *reversed(edges))
    assert IC.compile_map(rev) == fwd, "a re-derive in a different order must reproduce exactly"
    assert fwd == {"a": "c", "b": "c", "x": "y"}


def test_views_can_be_computed_from_an_in_memory_log_with_no_file_at_all(led):
    """Keeps the pure functions testable and lets a caller reason about a hypothetical merge
    before writing anything down."""
    changes = [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}]
    assert IC.canonical_pid("a", changes=changes) == "c"
    assert IC.compile_map(changes=changes) == {"a": "c", "b": "c"}


# ── the real cases ──────────────────────────────────────────────────────────────────────────
def test_the_live_cases_fold_correctly(led):
    """The three CPL splits the owner confirmed, plus the Atkinson re-key. Merging toward the
    `ci:` id, which is the anchored rung of the pid ladder."""
    _rec(led,
         ("uncapped:joshua-james", "ci:1209191"),
         ("uncapped:amari-goodridge", "ci:1342545"),
         ("uncapped:odean-smith", "ci:820691"),
         ("ci:1126982", "ci:1039481"))
    assert IC.canonical_pid("uncapped:joshua-james", led) == "ci:1209191"
    assert IC.canonical_pid("uncapped:amari-goodridge", led) == "ci:1342545"
    assert IC.canonical_pid("uncapped:odean-smith", led) == "ci:820691"
    assert IC.canonical_pid("ci:1126982", led) == "ci:1039481"
    assert IC.forks(led) == {}


def test_dale_and_glenn_are_NOT_merged_by_this_ledger(led):
    """ci:823509 / ci:902447 are TWO REAL HUMANS whose points were smeared onto one pid. The
    ledger must have nothing to say about them — recording a change here would merge two people
    to fix a bookkeeping error. Pinned so nobody 'completes the set' later."""
    _rec(led, ("uncapped:joshua-james", "ci:1209191"))
    assert IC.canonical_pid("ci:823509", led) == "ci:823509"
    assert IC.canonical_pid("ci:902447", led) == "ci:902447"
