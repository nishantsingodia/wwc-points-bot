"""Characterization tests for identity helpers: norm, _cricsheet_match, same_person_plausible
(wc_fps_to_csv.py:69-71, 175-198)."""


def test_norm_diacritics_case_punct(wcmod):
    assert wcmod.norm("Élise  Perry!") == "elise perry"


def test_norm_keeps_digits_and_spaces_hyphens(wcmod):
    # INTENTIONAL Py-vs-JS divergence: Python norm keeps digits and turns hyphens into
    # SPACES ("wyatt hodge"), whereas cricket-identity's normName strips hyphens by joining
    # ("wyatthodge"). A "make them identical" refactor must update this test deliberately.
    assert wcmod.norm("Wyatt-Hodge") == "wyatt hodge"
    assert wcmod.norm("MS Dhoni 7") == "ms dhoni 7"


def test_cricsheet_initials_match(wcmod):
    assert wcmod._cricsheet_match("Danni Wyatt", "DN Wyatt") is True
    assert wcmod._cricsheet_match("Smriti Mandhana", "SS Mandhana") is True


def test_cricsheet_no_match_on_different_surname(wcmod):
    assert wcmod._cricsheet_match("Danni Wyatt", "XY Smith") is False


def test_same_person_plausible_true_cases(wcmod):
    assert wcmod.same_person_plausible("Smriti Mandhana", "S Mandhana") is True
    assert wcmod.same_person_plausible("Sune Luus", "S Luus") is True


def test_same_person_plausible_rejects_surname_smear(wcmod):
    # The bug that once merged two different "...Singh" players -> must stay False.
    assert wcmod.same_person_plausible("Tajinder Singh", "Kunwarjeet Singh") is False


# ── ESPN id anchor: the ESPN half of the id-first identity work ─────────────
# ESPN athlete.id IS the cricinfo id (build_registry.py:336). parse_espn used to keep only
# fullName, so an ESPN row could only be found by NAME — and a spelling the alias table didn't
# know took its dots/maidens down with it, silently, as 0 (cricapi supplies neither).
def test_espn_id_resolves_where_name_cannot(wcmod):
    known = wcmod.resolve_pid("Mohommed Shiraz")
    assert known and known.startswith("ci:")
    unknown_spelling = "Zzz Unknown Spelling Of Shiraz"
    assert wcmod.resolve_pid(unknown_spelling) is None      # name alone: hopeless
    row = wcmod.blank_perf(unknown_spelling, espn_id=known.split(":")[1])
    assert wcmod.resolve_perf_pid(row) == known             # id alone: exact


def test_espn_id_not_in_registry_mints_an_id_anchored_pid(wcmod):
    """CONTRACT CHANGED (13 Aug 2026). This used to assert the id was IGNORED.

    Ignoring it published the row with a BLANK Player ID, which the draft can never join and no
    pid-keyed check can see — a played, scoring player falling out of the system silently. Live:
    Rivaldo A Clarke and Kevlon Alston Anderson, both in Barbados' XI for CPL ev 1534182 and in no
    squad list we hold (mid-tournament signings — the case that has no other answer).

    Nothing is guessed: ESPN's athlete.id IS the cricinfo id, so `ci:<athlete.id>` is derived, not
    invented. The player is also queued to 'Needs Cricinfo ID' for a human to fold into the squad.
    """
    row = wcmod.blank_perf("Totally Unknown Person", espn_id="999999999")
    assert wcmod.resolve_perf_pid(row) == "ci:999999999"
    assert any(e["current_pid"] == "ci:999999999" for e in wcmod.NEEDS_CRICINFO), \
        "a minted identity must be surfaced for a human, never minted silently"


def test_espn_id_must_look_like_a_cricinfo_id(wcmod):
    """The safety property the old test was really protecting: don't mint from a non-id.

    A cricinfo id is a positive integer. Anything else must fall through to the name path rather
    than produce something pid-SHAPED that means nothing.
    """
    for bogus in ("abc", "0", "12x", "-5", " "):
        row = wcmod.blank_perf("Totally Unknown Person", espn_id=bogus)
        assert wcmod.resolve_perf_pid(row) is None, f"minted a pid from espn_id={bogus!r}"


def test_blank_perf_espn_id_defaults_empty(wcmod):
    p = wcmod.blank_perf("X")
    assert p["espn_id"] == ""
    assert wcmod.resolve_perf_pid(p) is None                # no id, unknown name -> no guess


def test_cs_id_still_wins_over_espn_id(wcmod):
    # cricsheet is the official card; its id must remain the primary anchor.
    cs_pid = next(iter(wcmod.CS2PID.items()), None)
    if not cs_pid:
        return
    cs, pid = cs_pid
    row = wcmod.blank_perf("Some Name", espn_id="999999999")
    row["cs_id"] = cs
    assert wcmod.resolve_perf_pid(row) == pid
