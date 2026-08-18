"""Integrity of registry/recon_overrides.json — the committed ledger of the owner's money
decisions. Two stale-ledger classes are pinned here, both of the "written but never read" shape:

  1. AN APPROVAL KEYED ON A PID NOTHING RESOLVES TO. It never applies, and because nothing
     applies, the SAME review row is regenerated every run — answering it does not make it go
     away. The 25 Jul `ci:` migration did this to 83 of 131 approvals; the 7 Aug re-key fixed
     those and left one behind (slug:fabian-allen), because a *promotion* is an identity
     migration too and promote_new_players() rewrites only registry/new_players.json.

  2. "S1" IS A SLOT, NOT A FEED. It means "this tour's second witness" — cricapi until 13 Aug
     2026, Cricbuzz on any tour with `cricbuzz_series` set. Turning Cricbuzz on re-pointed the
     slot underneath 10 already-approved rows without editing a byte of them. `witness` names
     the feed the human actually answered about; `witness_value` pins the number it carried.
"""
import json
import os

import pytest

REG = os.path.join(os.path.dirname(__file__), "..", "registry")


def _load(name):
    with open(os.path.join(REG, name)) as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def ledger():
    return _load("recon_overrides.json")["overrides"]


@pytest.fixture(scope="module")
def known_pids():
    """Every pid a real run knows: the built registry PLUS the sheet-added players main() loads
    before the orphan guard. Read straight off disk rather than by calling load_new_players(),
    which mutates module-global identity for every later test."""
    pids = set(_load("players.json").get("players", {}))
    pids |= {e.get("pid") for e in _load("new_players.json").get("players", []) if e.get("pid")}
    return pids


# ── 1. no approval may be keyed on a pid nothing resolves to ─────────────────────────────────
def test_no_orphaned_approval(ledger, known_pids):
    """`*` is the whole-match ALL-L1 seed and `espn:<eventId>` the whole-match ESPN-card
    decision; neither names a player, so neither is an identity question."""
    orphans = sorted({o["pid"] for o in ledger
                      if o.get("status") == "approved" and o.get("pid")
                      and o["pid"] != "*" and not o["pid"].startswith("espn:")
                      and o["pid"] not in known_pids})
    assert orphans == [], (
        "approvals keyed on pid(s) nothing resolves to — they are NOT applying and their review "
        "rows regenerate every run; re-key via registry/pid_map.json: %s" % orphans)


def test_fabian_allen_rekey_lands_on_the_pid_every_feed_resolves_him_to(wcmod, ledger):
    """The re-key has to make the approval APPLY, not just quieten the orphan warning. The
    approval is only consulted through l2_approved_pids(), which is keyed by the pid the L2 gap
    itself carries — so the two must be the same key, from all three id paths."""
    mk = "2026-06-29::los angeles knight riders|seattle orcas"
    assert wcmod.resolve_pid("Fabian Allen") == "ci:670013"                       # squad name
    assert wcmod.resolve_perf_pid({"name": "Fabian Allen",
                                   "espn_id": "670013"}) == "ci:670013"           # ESPN athlete.id
    assert wcmod.resolve_perf_pid({"name": "FA Allen",
                                   "cs_id": "b52ffbbd"}) == "ci:670013"           # cricsheet id
    idx = wcmod.overrides_by_match({"overrides": ledger})
    assert wcmod.l2_approved_pids(mk, idx).get("ci:670013") == "S2"


def test_fabian_allen_approval_releases_the_l2_hold(wcmod, ledger):
    """End-to-end on the two functions the L2 hold gates on. Live MLC tab still shows his open
    revision as 'dots 0→12'; unapproved, the bot pins the provisional 0 OVER cricsheet's 12 and
    marks him '⚠ official revision' forever."""
    mk, pid = "2026-06-29::los angeles knight riders|seattle orcas", "ci:670013"
    base, offi = wcmod.blank_perf("Fabian Allen"), wcmod.blank_perf("Fabian Allen")
    base["dots"], offi["dots"] = 0, 12
    l2_pairs = {pid: wcmod.recon_gaps(base, offi, wcmod.RECON_L2, sep="→")}
    assert l2_pairs == {pid: "dots 0→12"}

    appr = wcmod.l2_approved_pids(mk, wcmod.overrides_by_match({"overrides": ledger}))
    assert appr.get(pid) == "S2"                      # -> the hold is skipped, cricsheet publishes
    assert wcmod.player_recon_markers(set(), l2_pairs, appr) == {}

    stale = {mk: [{"match_key": mk, "scope": "l2", "pid": "slug:fabian-allen",
                   "source": "S2", "status": "approved"}]}
    appr0 = wcmod.l2_approved_pids(mk, stale)
    assert appr0.get(pid) != "S2"                     # what the dead slug did: hold + re-flag
    assert wcmod.player_recon_markers(set(), l2_pairs, appr0) == {pid: "⚠ official revision"}


# ── 2. the source must be recorded by NAME, never by slot position ───────────────────────────
L1_SCOPES = ("player", "match")
FEED_NAMES = {"cricapi", "cricbuzz", "espn"}


def test_every_l1_approval_names_its_feed(ledger):
    """An L1 row with no `witness` is unreadable the moment the S1 slot is re-pointed: it asserts
    "the second witness was right" without saying who that was."""
    unnamed = [o for o in ledger
               if o.get("scope") in L1_SCOPES and o.get("source") in ("S1", "S2")
               and not o.get("witness")]
    assert unnamed == [], "L1 approval(s) with no `witness` feed name: %s" % unnamed


def test_l1_feed_names_are_real_feeds(ledger):
    bad = sorted({o["witness"] for o in ledger
                  if o.get("scope") in L1_SCOPES and o.get("witness") not in FEED_NAMES
                  and o.get("witness")})
    assert bad == [], bad


def test_s1_approvals_pin_the_number_they_were_answering(ledger):
    """`witness` alone still forces a re-ask on every re-point. The pinned number is what lets a
    re-point be silent when the feeds agree (measured: 8/8 identical on the Cricbuzz flip) and
    LOUD when they do not."""
    for o in ledger:
        if o.get("scope") in L1_SCOPES and o.get("source") == "S1":
            assert isinstance(o.get("witness_value"), int), o


def test_s2_at_l1_is_espn_everywhere(ledger):
    """ESPN has never been swapped out of the S2 slot; naming it makes that a checkable fact
    rather than an assumption the next feed migration gets to discover."""
    for o in ledger:
        if o.get("scope") in L1_SCOPES and o.get("source") == "S2":
            assert o["witness"] == "espn", o


def test_l2_and_espn_card_rows_carry_no_feed_name(ledger):
    """S1/S2 name CHOICES there, not feeds — L2 is "take cricsheet" vs "keep the held provisional
    value", espn_card is "score the short card anyway" vs "keep holding". Stamping a feed on one
    would invent a meaning it has never had."""
    stamped = [o for o in ledger if o.get("scope") in ("l2", "espn_card") and o.get("witness")]
    assert stamped == [], stamped


# ── 3. list ORDER decides a contradicted cell, so a new contradiction must never be quiet ────
CONTRADICTED = {
    ("2026-07-21::mi london|sunrisers leeds", "ci:1184099", "w", "player"),
    ("2026-07-21::mi london|sunrisers leeds", "ci:1120320", "w", "player"),
    ("2026-07-18::colombo kaps|galle gallants", "ci:1282475", "w", "player"),
    ("2026-07-18::colombo kaps|galle gallants", "ci:1282476", "w", "player"),
}


def test_every_s1_approval_pins_the_number_it_answered(ledger):
    """Renamed from "the ten cricapi-era approvals": the count is not the invariant.

    A hardcoded set of 10 broke the moment the owner answered an 11th S1 row on the live sheet —
    which is the system working, not a regression. The RULE is what matters: an S1 answer records a
    SLOT whose feed can move, so it must also pin the NUMBER it was agreeing to, or re-reading it
    later is guesswork. _approval_to_override now stamps both at write time, so this can only fail
    if that stops happening.
    """
    s1 = [o for o in ledger if o.get("scope") == "player" and o.get("source") == "S1"]
    assert s1, "no S1 approvals in the ledger at all — the fixture is stale"
    unpinned = [o for o in s1 if not isinstance(o.get("witness_value"), int)]
    assert unpinned == [], unpinned
    assert all(o.get("witness") in ("cricapi", "cricbuzz") for o in s1), \
        [o for o in s1 if o.get("witness") not in ("cricapi", "cricbuzz")]

def test_contradicted_cells_are_exactly_the_known_four(ledger):
    """apply_recon_overrides writes resolved[(pid, field)] in list order, so when one cell holds
    two approved answers the LAST one silently wins. The 7 Aug re-key manufactured 13 repeated
    signatures — 9 harmless (identical answers), 4 holding an older S2 (on the pre-migration pid)
    against a newer S1 (on the ci: pid). The newer S1 rows sit later and win today; each is worth
    30-34 FP, so a reorder is a settled-points mover. Deciding them is the OWNER's call — this
    test only makes sure the set cannot grow, or flip, without someone noticing."""
    seen, contra = {}, set()
    for o in ledger:
        if o.get("status") != "approved":
            continue
        sig = (o.get("match_key"), o.get("pid"), o.get("field"), o.get("scope"))
        answer = (o.get("source"), o.get("value"))
        if sig in seen and seen[sig] != answer:
            contra.add(sig)
        seen[sig] = answer
    assert contra == CONTRADICTED, (
        "the set of cells holding two DIFFERENT approved answers changed; which one applies is "
        "decided by position in the file: %s" % sorted(contra ^ CONTRADICTED))


def test_the_contradicted_cells_still_resolve_to_the_newer_s1(ledger):
    """Pins WHICH answer wins, not just that a conflict exists — the failure mode is a tool that
    dedupes or sorts the ledger and moves 4 settled scores without a diff anyone reads."""
    last = {}
    for o in ledger:
        sig = (o.get("match_key"), o.get("pid"), o.get("field"), o.get("scope"))
        if sig in CONTRADICTED:
            last[sig] = o
    assert sorted(last) == sorted(CONTRADICTED)
    for sig, o in last.items():
        assert o["source"] == "S1" and o.get("witness") == "cricapi", (sig, o)
