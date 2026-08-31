"""registry/build_crosswalk.py — the spine's generator, and the tripwire for it going stale.

The crosswalk is documented as "a pure function of people.csv", but for five weeks there was no
committed way to recompute it: built by hand 25 Jul 2026, then silently 170 mappings behind. The
cost was not abstract — `Ali Usman` (bb194908 -> 681099) was the last identity_healthcheck blocker
on the live ENG v PAK squad, and `ZN Carter` / `M Dindyal` sat on the "Needs Cricinfo ID" tab
reported to the owner as unanswerable. All three were answerable offline the whole time.

Every test here is offline. The staleness check deliberately does NOT fetch the register: a test
that needs the network is a test that gets skipped, and this is the one that must not be.
"""
import json
import os

import pytest

from registry import build_crosswalk as bc

REG = os.path.join(os.path.dirname(__file__), "..", "registry")


def row(cs, ci, alt2="", alt3="", name=""):
    return {"identifier": cs, "key_cricinfo": ci, "key_cricinfo_2": alt2,
            "key_cricinfo_3": alt3, "name": name}


# ── the build is pure ────────────────────────────────────────────────────────────────────────
def test_the_build_is_deterministic():
    """Same input, byte-identical output — or a re-run produces spurious diffs and nobody re-runs
    it, which is exactly how the file rotted."""
    rows = [row("aaaa1111", "100"), row("bbbb2222", "200"), row("cccc3333", "300")]
    a = bc.build(rows)
    b = bc.build(list(reversed(rows)))
    assert a[0] == b[0], "cs2ci depends on row ORDER"
    assert a[1] == b[1], "ci_alt depends on row ORDER"


def test_alternate_profiles_fold_onto_the_primary():
    cs2ci, ci_alt, coll = bc.build([row("aaaa1111", "100", alt2="101", alt3="102")])
    assert cs2ci == {"aaaa1111": "100"}
    assert ci_alt == {"101": "100", "102": "100"}
    assert coll == []


def test_a_row_with_no_cricinfo_id_contributes_nothing():
    """cricsheet registers players before cricinfo does. An empty key is not a mapping."""
    cs2ci, ci_alt, _ = bc.build([row("aaaa1111", ""), row("", "100"), row("bbbb2222", "200")])
    assert cs2ci == {"bbbb2222": "200"} and ci_alt == {}


def test_an_alternate_equal_to_its_own_primary_is_not_recorded():
    """Folding an id onto itself is a no-op that makes `fold_ci` look like it did something."""
    _, ci_alt, _ = bc.build([row("aaaa1111", "100", alt2="100")])
    assert ci_alt == {}


# ── refusals: the spine is never corrupted quietly ───────────────────────────────────────────
def test_one_cricinfo_id_under_two_cricsheet_ids_is_REPORTED_not_collapsed():
    """`build_registry.load_crosswalk` inverts this map and relies on uniqueness. Last-wins here
    is how one human ends up wearing another's cricsheet id."""
    cs2ci, _, coll = bc.build([row("aaaa1111", "100", name="First"),
                               row("bbbb2222", "100", name="Second")])
    assert len(coll) == 1 and coll[0][0] == "100"
    assert cs2ci == {"aaaa1111": "100"}, "the second claim must not overwrite the first"


def test_a_collision_refuses_to_write(tmp_path, capsys):
    people = tmp_path / "people.csv"
    people.write_text("identifier,name,unique_name,key_cricinfo,key_cricinfo_2,key_cricinfo_3\n"
                      "aaaa1111,First,First,100,,\nbbbb2222,Second,Second,100,,\n")
    out = tmp_path / "crosswalk.json"
    rc = bc.main(["--people", str(people), "--out", str(out), "--write"])
    assert rc == 1 and not out.exists(), "a colliding register was written anyway"


def test_a_shrink_refuses_to_write(tmp_path):
    """cricsheet can serve a partial register; an absence is not a deletion."""
    out = tmp_path / "crosswalk.json"
    out.write_text(json.dumps({"cs2ci": {"aaaa1111": "100", "bbbb2222": "200"}, "ci_alt": {}}))
    people = tmp_path / "people.csv"
    people.write_text("identifier,name,unique_name,key_cricinfo,key_cricinfo_2,key_cricinfo_3\n"
                      "aaaa1111,First,First,100,,\n")
    assert bc.main(["--people", str(people), "--out", str(out), "--write"]) == 1
    assert json.load(open(out))["cs2ci"] == {"aaaa1111": "100", "bbbb2222": "200"}, "clobbered"
    # ...and is permitted when the owner says the removal is real
    assert bc.main(["--people", str(people), "--out", str(out), "--write",
                    "--allow-shrink"]) == 0
    assert json.load(open(out))["cs2ci"] == {"aaaa1111": "100"}


def test_a_dry_run_writes_nothing(tmp_path):
    out = tmp_path / "crosswalk.json"
    people = tmp_path / "people.csv"
    people.write_text("identifier,name,unique_name,key_cricinfo,key_cricinfo_2,key_cricinfo_3\n"
                      "aaaa1111,First,First,100,,\n")
    assert bc.main(["--people", str(people), "--out", str(out)]) == 0
    assert not out.exists()


# ── the tripwire: the committed spine must cover what the registry already claims ─────────────
def test_the_committed_crosswalk_covers_every_cricsheet_id_the_registry_uses():
    """OFFLINE staleness detector. `build_registry` DERIVES each player's cricsheet_id from the
    crosswalk, so any cricsheet_id in players.json that the crosswalk cannot map is a spine that
    has drifted from the registry built on top of it — the condition that left Ali Usman's
    cricsheet_id null and fired a false `fixable-miss` blocker on a live tour."""
    cx = json.load(open(os.path.join(REG, "crosswalk.json")))
    cs2ci = cx["cs2ci"]
    players = json.load(open(os.path.join(REG, "players.json")))["players"]
    orphan = {pid: e["cricsheet_id"] for pid, e in players.items()
              if e.get("cricsheet_id") and e["cricsheet_id"] not in cs2ci}
    assert orphan == {}, f"cricsheet_id(s) the crosswalk cannot map: {orphan}"


def test_the_three_mappings_the_stale_spine_was_missing_are_present():
    """Named, because each one cost real work: a false blocker on the ENG v PAK squad and two tab
    rows reported to the owner as unanswerable. Regenerate with
    `python3 registry/build_crosswalk.py --write` if this fails."""
    cs2ci = json.load(open(os.path.join(REG, "crosswalk.json")))["cs2ci"]
    for cs, ci, who in (("bb194908", "681099", "Ali Usman"),
                        ("f52fc698", "1500753", "ZN Carter"),
                        ("b76a2178", "1394274", "M Dindyal")):
        assert cs2ci.get(cs) == ci, f"{who}: {cs} -> {cs2ci.get(cs)}, expected {ci}"


def test_the_committed_crosswalk_inverts_uniquely():
    """`CI2CS = {ci: cs for cs, ci in CS2CI.items()}` is built by inversion in two modules and
    documented as unique. Assert it, rather than trusting a comment."""
    cs2ci = json.load(open(os.path.join(REG, "crosswalk.json")))["cs2ci"]
    seen, dupes = {}, []
    for cs, ci in cs2ci.items():
        if ci in seen:
            dupes.append((ci, seen[ci], cs))
        seen[ci] = cs
    assert dupes == [], f"cricinfo id(s) under >1 cricsheet id: {dupes[:5]}"


def test_an_alternate_is_never_also_a_primary():
    """`fold_ci` maps an alternate onto a primary. An id that is BOTH would fold a real player
    onto someone else — the shape cricsheet created when it split 1528646 (Mohit Sharma) out into
    a primary of its own, which is why that stale ci_alt entry had to go."""
    cx = json.load(open(os.path.join(REG, "crosswalk.json")))
    primaries = set(cx["cs2ci"].values())
    both = sorted(set(cx["ci_alt"]) & primaries)
    assert both == [], f"id(s) that are both an alternate and a primary: {both}"
