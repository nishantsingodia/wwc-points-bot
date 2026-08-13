"""registry/cricbuzz_bridge.py — the cricbuzz_id ⇄ cricinfo_id derivation.

Every number asserted here was MEASURED on real cached payloads (see
tests/fixtures/cricbuzz_bridge/make_fixtures.py for provenance) and is pinned so a refactor that
quietly loses coverage, or quietly starts accepting a contradiction, fails loudly.
"""
import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "registry")))
import cricbuzz_bridge as cbb  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "cricbuzz_bridge")

M12 = "cb157061_espn1537342.json"   # LPL 12th match, one of the eval's two reference matches
M19 = "cb157138_espn1537349.json"   # LPL 19th match, the other
M05 = "cb156988_espn1537335.json"   # the substitute-fielder disagreement
M10 = "cb157039_espn1537340.json"   # Lizaad Williams "retd out" 0 off 0, absent from ESPN's bbb


def load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        fx = json.load(fh)
    cb = cbb.normalize_cb_card(fx["cb_score_card"])
    espn = cbb.normalize_espn_card(fx["espn_pbp"])
    return fx, cb, espn


def derive(name):
    fx, cb, espn = load(name)
    return cbb.derive_match(cb, espn, fx["cb_match_id"], fx["espn_event_id"], fx["date"])


# ══ parsing ═══════════════════════════════════════════════════════════════════════════════════
def test_flight_page_parses_to_a_scorecard():
    with open(os.path.join(FIX, "flight_page.html"), encoding="utf-8") as fh:
        card, header = cbb.parse_cb_scorecard_html(fh.read())
    assert header["matchId"] == 157061
    assert cbb.match_date(header) == "2026-07-25"      # from the payload, never from the clock
    assert card and card[0]["batTeamDetails"]["batsmenData"]


def test_unparseable_page_raises_rather_than_reading_as_nobody_played():
    with pytest.raises(cbb.BridgeError):
        cbb.parse_cb_scorecard_html("<html><body>blocked</body></html>")
    with pytest.raises(cbb.BridgeError):
        cbb.parse_cb_scorecard_html('<script>self.__next_f.push([1,"{\\"noCard\\":1}"])</script>')


def test_espn_running_totals_take_the_max_not_the_last():
    """Each commentary item carries running totals; otherBatsman/otherBowler copies go stale."""
    with open(os.path.join(FIX, "espn_running_totals.json"), encoding="utf-8") as fh:
        card = cbb.normalize_espn_card(json.load(fh))
    items = json.load(open(os.path.join(FIX, "espn_running_totals.json"), encoding="utf-8"))
    for aid, line in card["batting"].items():
        if line is None:
            continue
        seen = [(o.get("totalRuns", 0), o.get("faced", 0))
                for it in items["commentary"]["items"] for k in ("batsman", "otherBatsman")
                for o in [it.get(k) or {}] if str((o.get("athlete") or {}).get("id")) == aid]
        assert (line[0], line[1]) == max(seen, key=lambda t: (t[1], t[0]))


# ══ Layer A ═══════════════════════════════════════════════════════════════════════════════════
def test_did_not_bat_normalisation_is_load_bearing():
    """CB writes an all-zero batting row for a DNB, ESPN omits it. Materialising that row on one
    side only collapses bridging — measured 96.6% -> 73% across 16 LPL matches. Both directions
    are checked: a guard applied to one side but not its mirror is the bug class here."""
    _, cb, espn = load(M12)

    def materialise(card):
        out = dict(card)
        out["batting"] = {k: (v if v is not None else (0, 0, 0, 0))
                          for k, v in card["batting"].items()}
        return out

    good = len(cbb.layer_a(cb, espn))
    assert good == 22
    assert len(cbb.layer_a(materialise(cb), espn)) < good          # CB side unnormalised
    assert len(cbb.layer_a(cb, materialise(espn))) < good          # the MIRROR, equally fatal
    assert len(cbb.layer_a(materialise(cb), materialise(espn))) == good


def test_all_zero_batting_line_folds_to_no_information():
    assert cbb.batting_line(0, 0, 0, 0) is None
    assert cbb.batting_line(0, 1, 0, 0) == (0, 1, 0, 0)   # a duck off one ball IS information
    assert cbb.batting_line(4, 3, 1, 0) == (4, 3, 1, 0)


def test_retired_out_for_zero_off_zero_still_bridges():
    """cb157039: Cricbuzz records Lizaad Williams "retd out" 0 off 0; ESPN's ball-by-ball has no
    batting appearance for him at all. Folding the all-zero line to None on both sides means the
    feeds' disagreement about whether that innings happened cannot block his bowling fingerprint."""
    _, cb, espn = load(M10)
    assert cb["batting"]["8531"] is None and espn["batting"].get("379887") is None
    assert cbb.layer_a(cb, espn)["8531"] == "379887"


def test_layer_a_emits_only_fingerprints_unique_on_BOTH_sides():
    _, cb, espn = load(M12)
    base = cbb.layer_a(cb, espn)
    victim = "14797"
    twin = next(p for p in cb["batting"] if p != victim and cb["batting"].get(p) is not None)

    # collide two CB players -> BOTH disappear; a name could break the tie and must not be used.
    clash = copy.deepcopy(cb)
    clash["batting"][twin] = clash["batting"][victim]
    clash["bowling"][twin] = clash["bowling"][victim]
    out = cbb.layer_a(clash, espn)
    assert victim not in out and twin not in out
    assert len(out) == len(base) - 2

    # collide two ESPN players -> the CB player they both look like disappears too
    e_victim, e_twin = base[victim], base[twin]
    clash2 = copy.deepcopy(espn)
    clash2["batting"][e_twin] = clash2["batting"][e_victim]
    clash2["bowling"][e_twin] = clash2["bowling"][e_victim]
    out2 = cbb.layer_a(cb, clash2)
    assert victim not in out2 and twin not in out2


def test_names_do_not_decide_anything():
    """The property the whole module exists to have. Replace every name on both sides with a
    constant; the derived pairs must be byte-identical."""
    fx, cb, espn = load(M19)
    before, _ = cbb.derive_match(cb, espn, fx["cb_match_id"], fx["espn_event_id"], fx["date"])
    blind_cb, blind_espn = copy.deepcopy(cb), copy.deepcopy(espn)
    blind_cb["names"] = {k: "X" for k in cb["names"]}
    blind_espn["names"] = {k: "X" for k in espn["names"]}
    after, _ = cbb.derive_match(blind_cb, blind_espn, fx["cb_match_id"], fx["espn_event_id"],
                                fx["date"])
    assert before == after


def test_cricbuzz_maidens_is_never_read():
    """CB `maidens` is a verbatim copy of `dots` on The Hundred (13/13 bowlers). Corrupting it
    must change nothing. Including it was also measured to pair one FEWER player on the LPL."""
    with open(os.path.join(FIX, M12), encoding="utf-8") as fh:
        fx = json.load(fh)
    poisoned = copy.deepcopy(fx["cb_score_card"])
    for inn in poisoned:
        for row in inn["bowlTeamDetails"]["bowlersData"].values():
            row["maidens"] = 99
            row["dots"] = 99
    assert (cbb.normalize_cb_card(poisoned)["bowling"]
            == cbb.normalize_cb_card(fx["cb_score_card"])["bowling"])


# ══ the same-match gate ═══════════════════════════════════════════════════════════════════════
def test_gate_refuses_a_mis_paired_match():
    """Measured over 16x16 pairings: correct pairing 21-24 pairs, wrong pairing 0-2. Accepting a
    mis-paired match would therefore ship up to 2 confidently WRONG identities."""
    fx12, cb12, espn12 = load(M12)
    _, _, espn19 = load(M19)
    assert len(cbb.layer_a(cb12, espn19)) <= 2
    with pytest.raises(cbb.BridgeError):
        cbb.derive_match(cb12, espn19, fx12["cb_match_id"], "1537349", fx12["date"])


def test_gate_does_not_refuse_over_a_scorer_disagreement():
    """cb157039/ev1537340 disagree by one dismissal (CB emits a RETD_OUT row, ESPN does not).
    An earlier gate refused the whole match for it and threw away 23 good pairs. The
    disagreement is reported as advisory — it is a Recon question, never an identity one."""
    confs, diag = derive(M10)
    assert diag["totals_delta"] == [{"field": "dismissals", "cb": 18, "espn": 17}]
    assert diag["layer_a"] >= 20


def test_gate_refuses_a_half_finished_espn_card():
    fx, cb, espn = load(M12)
    half = copy.deepcopy(espn)
    half["totals"] = (espn["totals"][0] // 2,) + espn["totals"][1:]
    with pytest.raises(cbb.BridgeError) as e:
        cbb.derive_match(cb, half, fx["cb_match_id"], fx["espn_event_id"], fx["date"])
    assert "legal balls" in str(e.value)


# ══ Layer B ═══════════════════════════════════════════════════════════════════════════════════
def test_dismissal_join_beats_positional_alignment():
    """CB lists dismissals in BATTING order, ESPN chronologically. Pinned per the eval: cb157138
    scores 5/16 positionally and 16/16 joined on the bridged batsman."""
    _, cb, espn = load(M19)
    a = cbb.layer_a(cb, espn)
    cb_d = [d for d in cb["dismissals"] if d["code"] in cbb.CB_WICKET_TO_ESPN]
    espn_d = [d for d in espn["dismissals"] if d["bat"]]

    positional = sum(1 for i in range(min(len(cb_d), len(espn_d)))
                     if cbb.CB_WICKET_TO_ESPN[cb_d[i]["code"]] == espn_d[i]["type"]
                     and a.get(cb_d[i]["bat"]) == espn_d[i]["bat"])
    by_bat = {}
    for d in espn_d:
        by_bat.setdefault(d["bat"], []).append(d)
    joined = sum(1 for d in cb_d
                 if a.get(d["bat"])
                 and len([x for x in by_bat.get(a[d["bat"]], [])
                          if x["type"] == cbb.CB_WICKET_TO_ESPN[d["code"]]]) == 1)
    assert (positional, joined, len(cb_d)) == (5, 16, 16)


def test_layer_b_bridges_a_player_layer_a_cannot_reach():
    """The point of Layer B: it keys on the already-bridged BATSMAN, so the fielder needs no
    usable stats of his own. cb157138 is the clean case — Mujeeb Ur Rahman and Tharindu
    Ratnayake both went for 1 for 37 off 24 and neither batted, so their fingerprints COLLIDE
    and Layer A correctly refuses both. Ratnayake then took a catch, and the dismissal join
    recovers him from it."""
    _, cb, espn = load(M19)
    a = cbb.layer_a(cb, espn)
    assert cb["batting"]["12071"] is None and cb["batting"]["18515"] is None
    assert cb["bowling"]["12071"] == cb["bowling"]["18515"] == (24, 37, 1)
    assert "12071" not in a and "18515" not in a
    b, _ = cbb.layer_b(cb, espn, a)
    assert {k: v for k, v in b.items() if k not in a} == {"18515": "950405"}


def test_run_outs_cannot_be_bridged_and_say_so():
    """ESPN populates dismissal.fielder for caught 214/214 and stumped 5/5 but 0/19 for run out.
    A pure run-out fielder is the module's only irreducible residual — measured 1 in 16 matches."""
    _, cb, espn = load(M12)
    b, _ = cbb.layer_b(cb, espn, cbb.layer_a(cb, espn))
    ro_fielders = {d["fielder"] for d in cb["dismissals"] if d["code"] == "RUNOUT" and d["fielder"]}
    assert ro_fielders
    for d in espn["dismissals"]:
        if d["type"] == "run out":
            assert d["fielder"] is None
    assert all(cbb.layer_a(cb, espn).get(f) == b.get(f) or f not in b for f in ro_fielders)


def test_substitute_disagreement_is_refused_not_paired():
    """cb156988/ev1537335: ESPN and cricsheet both say BR McDermott was caught by
    "sub (Pawan Sandesh)"; Cricbuzz's card says "c Garuka Sanketh". Joining them would mint
    Garuka Sanketh's cricbuzz id onto Pawan Sandesh's cricinfo id — an identity manufactured out
    of a VALUE disagreement. Refuse, and name the reason."""
    _, cb, espn = load(M05)
    a = cbb.layer_a(cb, espn)
    b, unjoined = cbb.layer_b(cb, espn, a)
    assert a["52768"] == "1364328"                  # Layer A has him right
    assert "52768" not in b                          # Layer B does not overwrite it
    assert any("substitute disagreement" in u["reason"] for u in unjoined)
    _, diag = derive(M05)
    assert diag["layer_conflicts"] == []             # and no same-match conflict survives


def test_a_layer_conflict_inside_one_match_is_surfaced_and_revoked():
    """If Layer B ever contradicts Layer A about one cricbuzz id in one match, both claims are
    emitted on purpose: compile_bridge revokes the pair and the human sees which two cricinfo ids
    were claimed. Suppressing one silently would keep a pair that might be the wrong one."""
    _, cb, espn = load(M05)
    a = cbb.layer_a(cb, espn)
    forged = copy.deepcopy(cb)
    for d in forged["dismissals"]:
        if d["fielder"] == "52768":
            d["desc"] = d["desc"] + " (sub)"          # re-create the pre-guard disagreement
    b, _ = cbb.layer_b(forged, espn, a)
    assert b["52768"] == "1253695" != a["52768"]
    log = [conf("52768", a["52768"], "m1"), conf("52768", b["52768"], "m1",
                                                 cbb.METHOD_DISMISSAL)]
    bridge, revoked = cbb.compile_bridge(log)
    assert "52768" in revoked and "52768" not in bridge
    assert set(revoked["52768"]["claims"]) == {"1364328", "1253695"}


def test_substitute_agreed_by_both_feeds_still_bridges():
    """The guard must not throw away the 4-in-5 substitute catches both feeds agree on — those
    are the highest-value pairs Layer B produces, since a sub never bats or bowls."""
    _, cb, espn = load(M10)
    b, _ = cbb.layer_b(cb, espn, cbb.layer_a(cb, espn))
    assert b["11081"] == "629063"                    # "c (sub)Mehidy Hasan Miraz" on both sides


# ══ store: contradiction, tiering, determinism ════════════════════════════════════════════════
def conf(cb_id, ci_id, match, method=cbb.METHOD_FINGERPRINT, date="2026-07-25"):
    return {"cricbuzz_id": cb_id, "cricinfo_id": ci_id, "match": match,
            "method": method, "date": date}


def test_contradiction_refuses_both_claims_never_last_wins():
    log = [conf("1", "100", "m1"), conf("1", "999", "m2"), conf("1", "100", "m3")]
    bridge, revoked = cbb.compile_bridge(log)
    assert "1" not in bridge and "1" in revoked
    assert revoked["1"]["claims"] == {"100": ["m1", "m3"], "999": ["m2"]}
    st = {"bridge": bridge, "revoked": revoked}
    assert cbb.resolve(st, "1").status == cbb.REVOKED
    assert cbb.resolve(st, "1").cricinfo_id is None      # never the 2-of-3 majority


def test_contradiction_in_the_mirror_direction_is_caught_too():
    """Two cricbuzz ids landing on one cricinfo id is the same corruption seen from the other
    end. Checking only cb->ci would let it through — that asymmetry is the recurring bug here."""
    log = [conf("1", "100", "m1"), conf("2", "100", "m1")]
    bridge, revoked = cbb.compile_bridge(log)
    assert bridge == {}
    assert set(revoked) == {"1", "2"}
    assert revoked["1"]["collides_with"] == ["2"]


def test_tier_gates_use_not_storage():
    log = [conf("1", "100", "m1")]
    st = cbb.build_store(log)
    assert st["bridge"]["1"]["tier"] == 1
    assert cbb.resolve(st, "1", cbb.PURPOSE_CROSSCHECK).cricinfo_id == "100"
    r = cbb.resolve(st, "1", cbb.PURPOSE_CREATE)
    assert r.cricinfo_id is None and r.status == cbb.INSUFFICIENT_TIER
    st2 = cbb.build_store(cbb.merge_confirmations(log, [conf("1", "100", "m2")]))
    assert cbb.resolve(st2, "1", cbb.PURPOSE_CREATE).cricinfo_id == "100"


def test_tier_counts_distinct_matches_not_methods():
    """Two confirmations inside ONE match share that match's data, so they are not independent
    evidence and must not unlock the CREATE tier."""
    log = [conf("1", "100", "m1", cbb.METHOD_FINGERPRINT),
           conf("1", "100", "m1", cbb.METHOD_DISMISSAL)]
    st = cbb.build_store(log)
    assert st["bridge"]["1"]["tier"] == 1
    assert cbb.resolve(st, "1", cbb.PURPOSE_CREATE).status == cbb.INSUFFICIENT_TIER


def test_absence_is_named_never_a_bare_none():
    st = cbb.build_store([conf("1", "100", "m1")])
    for cb_id, want in (("9999", cbb.UNKNOWN), ("1", cbb.INSUFFICIENT_TIER)):
        r = cbb.resolve(st, cb_id, cbb.PURPOSE_CREATE)
        assert r.status == want and r.cricinfo_id is None and r.detail


def test_store_is_deterministic_and_idempotent():
    a, _ = derive(M12)
    b, _ = derive(M19)
    once = cbb.build_store(cbb.merge_confirmations(a, b))
    twice = cbb.build_store(cbb.merge_confirmations(cbb.merge_confirmations(a, b), a + b))
    assert json.dumps(once, sort_keys=True) == json.dumps(twice, sort_keys=True)
    # order of ingestion must not matter either
    other = cbb.build_store(cbb.merge_confirmations(b, a))
    assert json.dumps(once, sort_keys=True) == json.dumps(other, sort_keys=True)
    # the file stores each confirmation once, under its pair; the flat log is reconstructed
    log = cbb.confirmations_log(once)
    assert len(log) == once["counts"]["confirmations"]
    assert all(c["date"] for c in log)
    assert json.dumps(cbb.build_store(log), sort_keys=True) == json.dumps(once, sort_keys=True)


def test_no_clock_is_read():
    """A confirmation's date is the MATCH date from the payload. If anything ever calls
    time.time() the store stops reproducing and the file churns on every run."""
    import time as _t
    orig = _t.time
    _t.time = lambda: (_ for _ in ()).throw(AssertionError("cricbuzz_bridge read the clock"))
    try:
        confs, _ = derive(M12)
    finally:
        _t.time = orig
    assert {c["date"] for c in confs} == {"2026-07-25"}


# ══ the two reference matches, end to end ═════════════════════════════════════════════════════
def test_reference_matches_reproduce_measured_coverage():
    c12, d12 = derive(M12)
    c19, d19 = derive(M19)
    assert (d12["layer_a"], d12["layer_b"]) == (22, 5)
    assert (d19["layer_a"], d19["layer_b"]) == (22, 7)
    store = cbb.build_store(cbb.merge_confirmations(c12, c19))
    assert store["counts"] == {"confirmations": 46, "bridged": 38, "revoked": 0, "tier2_plus": 8}
    # 48 cricbuzz players with an observation across the two matches, 46 bridged
    assert sum(d["cb_players"] for d in (d12, d19)) == 48
    assert sum(len(d["unbridged_cb"]) for d in (d12, d19)) == 2


# ══ players.json mirror ═══════════════════════════════════════════════════════════════════════
def test_apply_to_players_json_mirrors_and_never_invents_an_entry(tmp_path):
    reg = {"anchor": "x", "count": 2, "players": {
        "ci:100": {"cricinfo_id": "100", "aliases": ["a"]},
        "ci:200": {"cricinfo_id": "200", "aliases": ["b"], "cricbuzz_id": "77", "cricbuzz_tier": 2},
    }}
    path = tmp_path / "players.json"
    path.write_text(json.dumps(reg), encoding="utf-8")
    st = cbb.build_store([conf("1", "100", "m1"), conf("1", "100", "m2"),
                          conf("5", "555", "m1")])
    res = cbb.apply_to_players_json(st, str(path))
    out = json.loads(path.read_text(encoding="utf-8"))
    assert out["players"]["ci:100"]["cricbuzz_id"] == "1"
    assert out["players"]["ci:100"]["cricbuzz_tier"] == 2
    # a stale mirror entry is CLEARED, not left behind to outlive its confirmation
    assert "cricbuzz_id" not in out["players"]["ci:200"]
    # a bridged cricinfo id the registry does not carry becomes a Needs-Cricinfo-ID row,
    # it never creates a registry entry here
    assert "ci:555" not in out["players"]
    assert [o["cricinfo_id"] for o in res["off_registry"]] == ["555"]
    rows = cbb.needs_cricinfo_rows(st, [], str(path))
    assert any(r.get("cricinfo_id") == "555" for r in rows)


def test_rekey_follows_an_identity_migration(tmp_path):
    """The store is keyed on the CRICBUZZ id and carries the cricinfo id as a VALUE, so an
    identity migration orphans the values, not the keys — exactly how the draft's pid-keyed
    player-photos.json went to 5/838 for four days."""
    pid_map = tmp_path / "pid_map.json"
    pid_map.write_text(json.dumps({"100": "ci:1000", "deadbeef": "ci:777"}), encoding="utf-8")
    st = cbb.build_store([conf("1", "100", "m1"), conf("2", "200", "m1")])
    new, changed = cbb.rekey(st, str(pid_map))
    assert changed == 1
    assert new["bridge"]["1"]["cricinfo_id"] == "1000"
    assert new["bridge"]["2"]["cricinfo_id"] == "200"


# ══ the cricbuzz.py adapter ═══════════════════════════════════════════════════════════════════
class _FakeMatch(object):
    header = {"matchStartTimestamp": 1784988000000}
    perf = {
        "cb:1": {"cb_id": 1, "name": "A", "batted": True, "r": 40, "b": 30, "4s": 4, "6s": 1,
                 "bowled": False},
        "cb:2": {"cb_id": 2, "name": "B", "batted": True, "r": 0, "b": 0, "4s": 0, "6s": 0,
                 "bowled": True, "balls": 24, "runs_conceded": 30, "w": 2, "maidens": None},
    }
    dismissals = [{"batter_cb_id": 1, "code": "CAUGHT", "out_desc": "c B b C",
                   "bowler_cb_id": 3, "fielder_cb_ids": [2]}]


def test_adapter_applies_the_same_batting_fold_and_drops_maidens():
    card = cbb.card_from_cricbuzz_match(_FakeMatch())
    assert card["batting"]["1"] == (40, 30, 4, 1)
    assert card["batting"]["2"] is None            # all-zero row folds even though batted=True
    assert card["bowling"]["2"] == (24, 30, 2)     # 3-tuple: maidens=None never leaks in
    assert card["dismissals"][0]["fielder"] == "2"
