"""The cricbuzz↔ESPN MATCH pin — registry/cricbuzz_match_map.py + cricbuzz.resolve_match_id.

WHAT WENT WRONG (the reason this file exists)
  Players have a shared key (ESPN's athlete.id IS the cricinfo id) and, where they don't, a
  derived bridge with a confirmations log. MATCHES had neither: `resolve_match_id` paired on
  normalised team names + date and RE-DERIVED ON EVERY RUN, with nothing recording that the pair
  had ever been made. A rename upstream could therefore re-pair or un-pair an already-SETTLED
  match and no ledger would show it. Measured cost of exactly that, in one week:
    ESPN "St Lucia Kings"  vs Cricbuzz "Saint Lucia Kings"  — 2 of 5 completed CPL matches lost
                                                              their second witness;
    ESPN "MI London (Men)" vs Cricbuzz "MI London"          — the Hundred Men's lost ALL 31.
  Both surfaced only as "no unique cricbuzz match", which reads as Cricbuzz not carrying the
  fixture, and sent the diagnosis the wrong way twice.

NO TEST HERE TOUCHES THE NETWORK. The series pages in tests/fixtures/cricbuzz_map/ are the REAL
cached Cricbuzz pages for LPL 12316 / Hundred 11493 (men) + 11504 (women) / CPL 12123, trimmed to
a handful of matchInfo blocks and re-wrapped in Cricbuzz's own RSC flight-chunk syntax. Every
match id, date and team name below is genuine Cricbuzz data.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import cricbuzz as cb                                          # noqa: E402
from registry import cricbuzz_match_map as mm                  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "cricbuzz_map")

# Ground truth, cross-checked against CLAUDE.md's own two derivation matches
# (CB 157138 = cricinfo 1537349, CB 157061 = cricinfo 1537342).
LPL = "12316"
LPL_M1 = ("2026-07-17", ["Galle Gallants", "Jaffna Kings"], 156948)   # ESPN event 1537331
HUN_M, HUN_W = "11493", "11504"
# 21 Jul 2026: the men's and the women's MI London v Sunrisers Leeds are the SAME two franchises
# on the SAME day. wc_fps_to_csv.match_key_of strips the gender qualifier, so those two collapse
# onto one key there — the collision that had just disabled the completed-ratchet's LIVE arm on
# 60 of 92 matches. The pin key carries the (gender-specific) cricbuzz series id instead.
DH_DATE = "2026-07-21"
DH_TEAMS_M = ["MI London (Men)", "Sunrisers Leeds (Men)"]
DH_TEAMS_W = ["MI London (Women)", "Sunrisers Leeds (Women)"]
DH_CB_M, DH_CB_W = 144662, 145011
CPL = "12123"
CPL_SLK = ("2026-08-14", ["St Lucia Kings", "Antigua and Barbuda Falcons"], 154381)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Point the reader at the committed fixture pages and at a throwaway map file."""
    monkeypatch.setattr(cb, "CACHE", FIX)
    monkeypatch.setattr(cb, "MATCH_MAP_PATH", str(tmp_path / "cricbuzz_match_map.json"))
    monkeypatch.setattr(cb.urllib.request, "urlopen", _boom)
    cb.reset_map_cache()
    del cb.PIN_ALERTS[:]
    yield str(tmp_path / "cricbuzz_match_map.json")
    cb.reset_map_cache()
    del cb.PIN_ALERTS[:]


def _boom(*a, **k):
    raise AssertionError("a test tried to hit the network")


def store_of(path):
    return mm.load_store(path)


def alerts(kind):
    return [a for a in cb.pin_alerts() if a["kind"] == kind]


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 1. FIRST DERIVE WRITES A PIN — with provenance a human can audit
# ═════════════════════════════════════════════════════════════════════════════════════════════
def test_first_derive_writes_a_pin_with_provenance(env):
    date, teams, cb_id = LPL_M1
    assert cb.resolve_match_id(LPL, date, teams, espn_event="1537331") == cb_id

    st = store_of(env)
    key = "12316|2026-07-17|galle gallants+jaffna kings"
    assert list(st["pins"]) == [key]
    pin = st["pins"][key]
    assert pin["cricbuzz_match_id"] == str(cb_id)
    assert pin["series_id"] == LPL and pin["date"] == date
    assert pin["teams"] == ["galle gallants", "jaffna kings"]
    assert pin["espn_events"] == ["1537331"]
    # provenance: HOW it was derived, and what cricbuzz itself called the fixture
    conf, = pin["confirmations"]
    assert conf["method"] == "teams+date"
    assert conf["cb_desc"] == "1st Match"
    assert conf["cb_date"] == "2026-07-17"
    assert st["revoked"] == {}


def test_pin_file_is_deterministic_and_idempotent(env):
    date, teams, _ = LPL_M1
    cb.resolve_match_id(LPL, date, teams, espn_event="1537331")
    first = open(env, encoding="utf-8").read()
    cb.reset_map_cache()
    cb.resolve_match_id(LPL, date, teams, fresh=True, espn_event="1537331")
    assert open(env, encoding="utf-8").read() == first, "a re-run must produce an EMPTY diff"


def test_pins_and_revoked_are_a_pure_function_of_the_log(env):
    for d, t, _ in (LPL_M1, ("2026-07-18", ["Kandy Royals", "Dambulla Sixers"], 156955)):
        cb.resolve_match_id(LPL, d, t)
    st = store_of(env)
    rebuilt = mm.build_store(mm.confirmations_log(st))
    assert json.dumps(rebuilt, sort_keys=True) == json.dumps(st, sort_keys=True)


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 2. A SECOND RUN READS THE PIN — no re-derivation, no page, no network
# ═════════════════════════════════════════════════════════════════════════════════════════════
def test_second_run_reads_the_pin_without_re_deriving(env, monkeypatch):
    date, teams, cb_id = LPL_M1
    assert cb.resolve_match_id(LPL, date, teams) == cb_id

    cb.reset_map_cache()                                  # a fresh process, same committed file
    def never(*a, **k):
        raise AssertionError("the series page was fetched for an already-pinned match")
    monkeypatch.setattr(cb, "series_matches", never)
    assert cb.resolve_match_id(LPL, date, teams) == cb_id


def test_a_pinned_tour_reads_the_series_page_once_per_process(env, monkeypatch):
    """31 matches used to cost 31 fetches of the same ~280 KB page from an undocumented endpoint,
    because the bot passes fresh=True for every match cricsheet has not settled yet."""
    real, calls = cb.cb_fetch, []
    monkeypatch.setattr(cb, "cb_fetch", lambda url, k, **kw: (calls.append(url), real(url, k))[1])
    for d, t in (("2026-07-17", ["Galle Gallants", "Jaffna Kings"]),
                 ("2026-07-18", ["Kandy Royals", "Dambulla Sixers"]),
                 ("2026-08-01", ["Colombo Kaps", "Galle Gallants"])):
        assert cb.resolve_match_id(LPL, d, t, fresh=True)
    assert len(calls) == 1, calls


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 3. A CONTRADICTING DERIVATION IS REFUSED — BOTH claims, never last-wins
# ═════════════════════════════════════════════════════════════════════════════════════════════
def _repaired(monkeypatch, series_id, cb_id_from, cb_id_to):
    """Simulate an upstream re-pair: the same fixture, served under a DIFFERENT cricbuzz id.

    Synthetic in one field only — the fixture dicts are the real ones off the cached page and
    only `match_id` is moved. That is the shape a schedule correction or a re-used slug produces,
    and it is the event the pin exists to catch. Returns the undo, because monkeypatch.undo()
    would also revert the `env` fixture's map path (same monkeypatch object) and quietly point
    the test back at the COMMITTED map.
    """
    real_fn = cb.series_matches
    real = real_fn(series_id)
    moved = [dict(m, match_id=cb_id_to) if m["match_id"] == cb_id_from else m for m in real]
    monkeypatch.setattr(cb, "series_matches", lambda sid, fresh=False: moved)
    return lambda: monkeypatch.setattr(cb, "series_matches", real_fn)


def test_a_different_derivation_refuses_both_and_says_so(env, monkeypatch):
    date, teams, cb_id = LPL_M1
    assert cb.resolve_match_id(LPL, date, teams) == cb_id
    key = "12316|2026-07-17|galle gallants+jaffna kings"

    cb.reset_map_cache()
    _repaired(monkeypatch, LPL, cb_id, 999001)
    # fresh=True is what the bot passes for any match cricsheet has not settled — that is when
    # the re-derivation, and therefore the contradiction check, actually happens.
    assert cb.resolve_match_id(LPL, date, teams, fresh=True) is None

    st = store_of(env)
    assert key not in st["pins"], "the contradicted pin must NOT survive as the winner"
    rec = st["revoked"][key]
    assert set(rec["claims"]) == {str(cb_id), "999001"}
    assert "claimed by 2 cricbuzz matches" in rec["reason"]
    assert "--forget" in rec["remedy"]
    a, = alerts("contradiction")
    assert "156948" in a["detail"] and "999001" in a["detail"]


def test_a_revoked_key_stays_refused_on_every_later_run(env, monkeypatch):
    date, teams, cb_id = LPL_M1
    cb.resolve_match_id(LPL, date, teams)
    cb.reset_map_cache()
    heal = _repaired(monkeypatch, LPL, cb_id, 999001)
    cb.resolve_match_id(LPL, date, teams, fresh=True)

    cb.reset_map_cache()
    heal()                                                 # upstream heals; the file does not
    del cb.PIN_ALERTS[:]
    assert cb.resolve_match_id(LPL, date, teams) is None, "self-healing here would be last-wins"
    assert alerts("revoked")


def test_forget_is_the_only_way_back(env, monkeypatch):
    date, teams, cb_id = LPL_M1
    cb.resolve_match_id(LPL, date, teams)
    cb.reset_map_cache()
    heal = _repaired(monkeypatch, LPL, cb_id, 999001)
    cb.resolve_match_id(LPL, date, teams, fresh=True)
    key = "12316|2026-07-17|galle gallants+jaffna kings"

    st, n = mm.forget(store_of(env), key)
    assert n == 2, "both claims are dropped — the human decides, the file does not"
    assert st["pins"] == {} and st["revoked"] == {}
    mm.save_store(st, env)
    cb.reset_map_cache()
    heal()
    assert cb.resolve_match_id(LPL, date, teams) == cb_id


def test_one_cricbuzz_match_claimed_by_two_fixtures_revokes_all(env):
    """The mirror direction. One of the two would be cross-checked against a card that is not its
    own — 22 players of pure disagreement against money."""
    st = mm.empty_store()
    st, _ = mm.record(st, "12316|2026-07-17|galle gallants+jaffna kings", 156948,
                      espn_event="1537331")
    st, _ = mm.record(st, "12316|2026-07-30|colombo kaps+kandy royals", 156948,
                      espn_event="1537344")
    assert st["pins"] == {}
    assert len(st["revoked"]) == 2
    for rec in st["revoked"].values():
        assert "claimed by 2 different fixtures" in rec["reason"]
        assert rec["collides_with"]


def test_two_espn_events_under_one_key_is_refused(env):
    """Our own key is ambiguous: a same-day double-header between the same two sides INSIDE one
    (gender-specific) series. Refusing is the whole point — the pin must not smuggle back the
    guess resolve_match_id has always declined to make."""
    st = mm.empty_store()
    st, _ = mm.record(st, "12316|2026-07-17|a+b", 156948, espn_event="111")
    st, _ = mm.record(st, "12316|2026-07-17|a+b", 156948, espn_event="222")
    assert st["pins"] == {}
    rec, = st["revoked"].values()
    assert "2 ESPN events" in rec["reason"]


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 4. AN ABSENCE IS NOT A CONTRADICTION — the pin is what survives a rename
# ═════════════════════════════════════════════════════════════════════════════════════════════
def test_a_derivation_that_finds_nothing_leaves_the_pin_standing(env, monkeypatch):
    """The exact failure this file exists for: Cricbuzz renames a team, nothing matches any more,
    and the old code reported 'no unique cricbuzz match' — silently dropping the cross-check on a
    match whose points are already settled."""
    date, teams, cb_id = LPL_M1
    assert cb.resolve_match_id(LPL, date, teams) == cb_id

    cb.reset_map_cache()
    renamed = [dict(m, teams=["Galle Marauders", "Jaffna Monarchs"]) if m["match_id"] == cb_id
               else m for m in cb.series_matches(LPL)]
    monkeypatch.setattr(cb, "series_matches", lambda sid, fresh=False: renamed)
    assert cb.resolve_match_id(LPL, date, teams, fresh=True) == cb_id
    assert store_of(env)["revoked"] == {}
    a, = alerts("held")
    assert "stands" in a["detail"]


def test_an_unreachable_series_page_leaves_the_pin_standing(env, monkeypatch):
    date, teams, cb_id = LPL_M1
    assert cb.resolve_match_id(LPL, date, teams) == cb_id
    cb.reset_map_cache()

    def dead(*a, **k):
        raise cb.CricbuzzUnavailable("HTTP 403 for the series page")
    monkeypatch.setattr(cb, "series_matches", dead)
    assert cb.resolve_match_id(LPL, date, teams, fresh=True) == cb_id
    assert alerts("held")


def test_unpinned_still_raises_on_an_unreachable_series_page(env, monkeypatch):
    """Unpinned, the caller must see exactly what it always did — cb_match_perf turns this into
    'cricbuzz series N unreachable', which is a different, honest message from 'no fixture'."""
    def dead(*a, **k):
        raise cb.CricbuzzUnavailable("HTTP 403 for the series page")
    monkeypatch.setattr(cb, "series_matches", dead)
    with pytest.raises(cb.CricbuzzUnavailable):
        cb.resolve_match_id(LPL, LPL_M1[0], LPL_M1[1])


def test_a_rename_on_our_side_is_survived_via_the_espn_event(env, monkeypatch):
    """The ESPN event id is an id, not a name: our key moves when a team is renamed, the anchor
    does not. This is why the hook that passes espn_event= is worth applying."""
    date, teams, cb_id = LPL_M1
    assert cb.resolve_match_id(LPL, date, teams, espn_event="1537331") == cb_id
    cb.reset_map_cache()
    # our feed now spells one side differently AND cricbuzz has moved on too, so nothing derives
    monkeypatch.setattr(cb, "series_matches", lambda sid, fresh=False: [])
    assert cb.resolve_match_id(LPL, date, ["Galle Gallants CC", "Jaffna Kings"],
                               espn_event="1537331") == cb_id


def test_a_rename_is_NAMED_instead_of_reported_as_a_missing_fixture(env):
    """'no unique cricbuzz match' sent the diagnosis the wrong way twice. When a sibling pin sits
    on the same date with one team in common, say RENAME — but never resolve on it."""
    date, teams, _ = LPL_M1
    cb.resolve_match_id(LPL, date, teams)
    assert cb.resolve_match_id(LPL, date, ["Galle Marauders", "Jaffna Kings"]) is None
    a, = alerts("unpaired")
    assert "RENAMED" in a["detail"] and "team_aliases.json" in a["detail"]


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 5. THE OLD REFUSAL SEMANTICS ARE UNTOUCHED
# ═════════════════════════════════════════════════════════════════════════════════════════════
def test_an_ambiguous_fixture_returns_none_and_is_never_pinned(env):
    """CPL playoffs: three 'TBC v TBC' fixtures on consecutive days. Their normalised team set is
    identical and the ±1-day tolerance overlaps them, so >1 fixture matches — refused, exactly as
    a same-day double-header between the same two sides must be."""
    assert cb.resolve_match_id(CPL, "2026-09-17", ["TBC", "TBC"]) is None
    assert store_of(env)["pins"] == {}
    a, = alerts("unpaired")
    assert "AMBIGUOUS" in a["detail"]


def test_an_unknown_fixture_returns_none(env):
    assert cb.resolve_match_id(CPL, "2026-08-07", ["Barbados Tridents", "Guyana Amazon Warriors"]) is None
    assert store_of(env)["pins"] == {}


def test_the_two_naming_conventions_that_broke_it_still_pair(env):
    """ESPN 'St Lucia Kings' vs Cricbuzz 'Saint Lucia Kings'; ESPN 'MI London (Men)' vs 'MI
    London'. Both cost real matches their second witness; both must resolve AND pin."""
    date, teams, cb_id = CPL_SLK
    assert cb.resolve_match_id(CPL, date, teams) == cb_id
    assert cb.resolve_match_id(HUN_M, DH_DATE, DH_TEAMS_M) == DH_CB_M


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 6. THE HUNDRED'S SAME-DAY, SAME-FRANCHISE M/W DOUBLE-HEADER
# ═════════════════════════════════════════════════════════════════════════════════════════════
def test_the_key_separates_a_mens_and_womens_double_header(env):
    """The cricbuzz SERIES id is gender-specific (11493 men / 11504 women) and it is part of the
    key, so the two halves cannot collide — unlike wc_fps_to_csv.match_key_of, which strips the
    gender qualifier and had just disabled the completed-ratchet's LIVE arm on 60 of 92 matches."""
    assert cb.resolve_match_id(HUN_M, DH_DATE, DH_TEAMS_M, espn_event="1521231") == DH_CB_M
    assert cb.resolve_match_id(HUN_W, DH_DATE, DH_TEAMS_W, espn_event="1521197") == DH_CB_W

    st = store_of(env)
    assert sorted(st["pins"]) == ["11493|2026-07-21|mi london+sunrisers leeds",
                                  "11504|2026-07-21|mi london+sunrisers leeds"]
    assert st["revoked"] == {}, "two genders is not a contradiction"
    # the team slugs are IDENTICAL — only the series id keeps them apart
    a, b = (st["pins"][k] for k in sorted(st["pins"]))
    assert a["teams"] == b["teams"]
    assert a["cricbuzz_match_id"] != b["cricbuzz_match_id"]


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 7. THE SAME FIXTURE UNDER TWO KEYS (the ±1-day date convention) IS NOT A CONTRADICTION
# ═════════════════════════════════════════════════════════════════════════════════════════════
def test_a_one_day_date_convention_difference_merges_rather_than_revokes(env):
    """cricapi's matchList date and ESPN's event date differ by a day on evening starts, and the
    two call sites pass different ones. Cricbuzz cannot be carrying both fixtures — resolve_match_id
    would have seen 2 hits and pinned neither — so this is one fixture, twice."""
    assert cb.resolve_match_id(LPL, "2026-07-17", ["Galle Gallants", "Jaffna Kings"]) == 156948
    assert cb.resolve_match_id(LPL, "2026-07-18", ["Galle Gallants", "Jaffna Kings"]) == 156948
    st = store_of(env)
    assert st["revoked"] == {} and len(st["pins"]) == 2
    for rec in st["pins"].values():
        assert rec["also_keyed_as"], "the two keys must cross-reference each other"


def test_two_days_apart_is_a_contradiction(env):
    """Outside the resolver's own tolerance the same-fixture argument evaporates: two keys on one
    card is then a real collision and both are refused."""
    st = mm.empty_store()
    st, _ = mm.record(st, "12316|2026-07-17|galle gallants+jaffna kings", 156948)
    st, _ = mm.record(st, "12316|2026-07-20|galle gallants+jaffna kings", 156948)
    assert st["pins"] == {} and len(st["revoked"]) == 2


def test_same_fixture_needs_evidence_not_a_name_guess():
    """A renamed team is admitted only on a shared ESPN event id — an id, never a name."""
    assert mm.same_fixture("12316|2026-07-17|a+b", ["9"], "12316|2026-07-17|a+c", ["9"])
    assert not mm.same_fixture("12316|2026-07-17|a+b", [], "12316|2026-07-17|a+c", [])
    assert not mm.same_fixture("12316|2026-07-17|a+b", ["9"], "11493|2026-07-17|a+b", ["8"])


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 8. THE STORE ITSELF
# ═════════════════════════════════════════════════════════════════════════════════════════════
def test_a_truncated_map_file_is_refused_not_read_as_empty(tmp_path):
    """An unreadable map is NOT 'nothing is pinned' — that would silently un-pin every settled
    match and hand the next rename its opening."""
    p = tmp_path / "m.json"
    p.write_text("{}")
    with pytest.raises(mm.MatchMapError):
        mm.load_store(str(p))


def test_a_missing_map_file_starts_empty(tmp_path):
    st = mm.load_store(str(tmp_path / "nope.json"))
    assert st["pins"] == {} and st["revoked"] == {} and st["_schema"] == mm.SCHEMA


def test_lookup_names_every_outcome(env):
    st = mm.empty_store()
    assert mm.lookup(st, "12316|2026-07-17|a+b").status == mm.UNPINNED
    st, _ = mm.record(st, "12316|2026-07-17|a+b", 156948, espn_event="1537331")
    assert mm.lookup(st, "12316|2026-07-17|a+b").status == mm.PINNED
    hit = mm.lookup(st, "12316|2026-07-17|a+z", espn_event="1537331")
    assert hit.status == mm.PINNED_BY_EVENT and hit.cricbuzz_match_id == "156948"
    # an event id from ANOTHER series must not reach across
    assert mm.lookup(st, "11493|2026-07-17|a+z", espn_event="1537331").status == mm.UNPINNED


def test_no_clock_is_read_anywhere():
    """Determinism: re-deriving a season must reproduce the file byte for byte, so nothing in the
    store may read the wall clock (the same rule registry/cricbuzz_bridge.py holds itself to)."""
    src = open(mm.__file__, encoding="utf-8").read()
    body = src.split('"""', 2)[2]                      # skip the module docstring
    for banned in ("datetime.now", "date.today", "time.time", "utcnow"):
        assert banned not in body, banned


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 9. THE COMMITTED MAP — what actually ships
# ═════════════════════════════════════════════════════════════════════════════════════════════
def test_the_committed_map_covers_the_four_live_tours():
    st = mm.load_store()
    by_series = {}
    for rec in st["pins"].values():
        by_series[rec["series_id"]] = by_series.get(rec["series_id"], 0) + 1
    for series in ("12316", "11493", "11504", "12123"):      # LPL / Hundred M / Hundred W / CPL
        assert by_series.get(series, 0) > 0, series
    assert st["revoked"] == {}
    assert all(rec["espn_events"] for rec in st["pins"].values()), \
        "every backfilled pin carries its ESPN event id — the rename-proof anchor"


def test_the_committed_map_pins_the_two_matches_the_bridge_was_derived_on():
    """CLAUDE.md's own ground truth: CB 157138 = cricinfo 1537349, CB 157061 = cricinfo 1537342."""
    st = mm.load_store()
    pairs = {rec["cricbuzz_match_id"]: rec["espn_events"] for rec in st["pins"].values()}
    assert pairs["157138"] == ["1537349"]
    assert pairs["157061"] == ["1537342"]


def test_the_workflows_commit_the_map():
    """A pin written into a file the workflow never commits is written-but-never-read — this
    repo's most-repeated bug shape, and it would silently re-derive every run."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for wf in ("wwc-points.yml", "live-lineup.yml", "on-demand-refresh.yml"):
        text = open(os.path.join(root, ".github", "workflows", wf), encoding="utf-8").read()
        assert "registry/cricbuzz_match_map.json" in text, wf


def test_last_refusal_names_the_cause_of_every_none(env):
    """The caller prints one line for a refused pairing. Today it can only say 'no unique
    cricbuzz match', which reads as 'Cricbuzz has no such fixture' — the misreading that cost two
    diagnoses in a week. Every path that returns None must leave a nameable reason behind."""
    assert cb.resolve_match_id(CPL, "2026-09-17", ["TBC", "TBC"]) is None
    assert "AMBIGUOUS" in cb.last_refusal()

    assert cb.resolve_match_id(CPL, "2026-08-14", ["St Lucia Kings", "Nowhere XI"]) is None
    assert "TEAM NAMES DO NOT MEET" in cb.last_refusal() or "no fixture" in cb.last_refusal()

    # and it is per-call: a success must not leave the previous refusal lying around
    assert cb.resolve_match_id(*(CPL,) + CPL_SLK[:2]) == CPL_SLK[2]
    assert cb.last_refusal() == ""
