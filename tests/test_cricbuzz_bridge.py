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
GH = "cb145302_espn1521224.json"   # Hundred W: the fielder-attribution dispute that revoked
                                   # cb11101 Grace Harris — CB "c Grace Harris", ESPN "c Higham"


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


def test_a_layer_conflict_inside_one_match_never_reaches_the_fact_log():
    """Layer B contradicting Layer A about one cricbuzz id in one match used to emit BOTH claims,
    on the reasoning that hiding one would hide the disagreement. What it actually hid was the
    KIND of disagreement: the two feeds name different FIELDERS for one catch, which is a value
    question, and letting it into the identity log revoked a player whose id was never in doubt.

    REAL PAYLOAD, not a forgery — Hundred W cb145302/espn1521224: Cricbuzz's card says
    "A Capsey c Grace Harris b Charis Pavely", ESPN's says "c Higham" (ci:874201). Layer A had
    Grace Harris (cb11101) as ci:381268 from eight matches; the single dismissal claim revoked
    all of it and put her on "Needs Cricinfo ID" with nothing a human could answer.
    """
    _, cb, espn = load(GH)
    a = cbb.layer_a(cb, espn)
    b, unjoined = cbb.layer_b(cb, espn, a)
    assert a["11101"] == "381268"                     # Layer A, from figures unique on both sides
    assert "11101" not in b                           # Layer B does not get to overrule it
    disputes = cbb.fielder_disputes(unjoined)
    assert [d["cb_fielder"] for d in disputes] == ["11101"]
    assert disputes[0]["layer_a"] == "381268" and disputes[0]["layer_b"] == "874201"
    assert "Grace Harris" in disputes[0]["desc"] and "Higham" in disputes[0]["espn_desc"]
    confs, diag = derive(GH)
    assert diag["layer_conflicts"] == []              # empty BY CONSTRUCTION now
    assert diag["fielder_disputes"] and len(diag["fielder_disputes"]) == 1
    # and the fact log carries exactly one claim for her, the fingerprint one
    hers = [c for c in confs if c["cricbuzz_id"] == "11101"]
    assert [(c["cricinfo_id"], c["method"]) for c in hers] == [("381268", cbb.METHOD_FINGERPRINT)]
    bridge, revoked = cbb.compile_bridge(confs)
    assert bridge["11101"]["cricinfo_id"] == "381268" and "11101" not in revoked


def test_the_mirror_guard_refuses_a_fielder_ci_layer_a_gave_to_someone_else():
    """The mirror of the guard above, read from the ESPN end: the cricinfo fielder ESPN names is
    already Layer A's for a DIFFERENT cricbuzz id, so accepting would put two cricbuzz ids on one
    human. Measured 0 of 49 new Layer-B pairs across the 92-match corpus, i.e. it costs nothing
    today — it exists because a guard on one side and not its mirror is how this class comes back.
    Constructed by REDIRECTING a real dismissal, so nothing about the pairing itself is invented.
    """
    _, cb, espn = load(M12)
    a = cbb.layer_a(cb, espn)
    # cb22666 (Malsha Tharupathi) is the one fielder in this match Layer A never reaches, and he
    # takes both of his catches off bridged batsmen — i.e. exactly the pair Layer B exists for.
    assert "22666" not in a and cbb.layer_b(cb, espn, a)[0]["22666"] == "1282475"
    stolen = a["7884"]                                # a human Layer A has already spoken for
    forged = copy.deepcopy(espn)
    for e in forged["dismissals"]:
        if e["fielder"] == "1282475":
            e["fielder"] = stolen                     # ESPN now credits that already-paired human
    b, unjoined = cbb.layer_b(cb, forged, a)
    assert "22666" not in b                           # refused, not two cricbuzz ids on one human
    assert any(u.get("cb_fielder") == "22666" and "already gave ci:%s" % stolen in u["reason"]
               for u in unjoined)
    assert len(cbb.fielder_disputes(unjoined)) == 2   # both of his catches, each named


def test_the_guards_cost_none_of_the_pairs_layer_b_exists_for():
    """The guards must not touch the payoff. MEASURED per fixture, and pinned: no Layer-B pair
    contradicts Layer A any more, and every genuinely-new pair (the ones Layer A can never reach —
    a substitute, a pure fielder) survives. Corpus-wide the same measurement is 587 Layer-B pairs
    = 534 restating Layer A + 49 new + 4 refused disputes, i.e. the guards cost 0 of the 49."""
    expected = {M12: (22, 5, 1), M19: (22, 7, 1), M05: (24, 7, 0),
                M10: (24, 10, 1), GH: (18, 3, 0)}
    for name, (n_a, n_b, n_new) in expected.items():
        _, cb, espn = load(name)
        a = cbb.layer_a(cb, espn)
        b, _ = cbb.layer_b(cb, espn, a)
        assert (len(a), len(b), sum(1 for k in b if k not in a)) == (n_a, n_b, n_new), name
        for cb_id, ci in b.items():
            assert a.get(cb_id) in (None, ci), name    # never contradicts A


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


# ══ the corpus: reading the bot's own cache, and joining the pin ledger ════════════════════════
def _bot_pbp(items, page_count=1, count=None):
    return {"commentary": {"pageCount": page_count,
                           "count": len(items) if count is None else count,
                           "items": items}}


def _write_page(d, series, ev, page, payload):
    (d / ("espn_%s_playbyplay_event_%s_limit_500_page_%d.json" % (series, ev, page))).write_text(
        json.dumps(payload), encoding="utf-8")


def test_bot_cache_pbp_is_read_under_the_bots_own_key(tmp_path):
    """The two modules cached the SAME bytes under different names, so every --derive re-fetched
    ESPN. That is why the corpus was hand-driven and fell 11 matches behind the pin ledger."""
    _write_page(tmp_path, "1521193", "1521218", 1, _bot_pbp([{"id": 1}, {"id": 2}]))
    got = cbb.espn_pbp_from_bot_cache("1521218", "1521193", str(tmp_path))
    assert [i["id"] for i in got["commentary"]["items"]] == [1, 2]
    # not cached is None — "the caller may fetch", never an empty card
    assert cbb.espn_pbp_from_bot_cache("9999999", "1521193", str(tmp_path)) is None


def test_bot_cache_pbp_refuses_a_truncated_card(tmp_path):
    """An absence must never present as a value. A card short of ESPN's own count, or missing a
    page ESPN says exists, changes the very fingerprints identity is derived from — so it raises
    rather than deriving from what is there. Mirrors parse_espn's ball-count gate."""
    _write_page(tmp_path, "S", "E1", 1, _bot_pbp([{"id": 1}], count=9))
    with pytest.raises(cbb.BridgeError):
        cbb.espn_pbp_from_bot_cache("E1", "S", str(tmp_path))

    _write_page(tmp_path, "S", "E2", 1, _bot_pbp([{"id": 1}], page_count=2, count=2))
    with pytest.raises(cbb.BridgeError):          # page 2 of 2 is not on disk
        cbb.espn_pbp_from_bot_cache("E2", "S", str(tmp_path))
    _write_page(tmp_path, "S", "E2", 2, _bot_pbp([{"id": 2}], page_count=2, count=2))
    got = cbb.espn_pbp_from_bot_cache("E2", "S", str(tmp_path))
    assert [i["id"] for i in got["commentary"]["items"]] == [1, 2]

    # a zero-byte cache file is corruption, not "no deliveries"
    (tmp_path / "espn_S_playbyplay_event_E3_limit_500_page_1.json").write_text("", encoding="utf-8")
    with pytest.raises(cbb.BridgeError):
        cbb.espn_pbp_from_bot_cache("E3", "S", str(tmp_path))


def test_bot_cache_pbp_dedups_espn_duplicate_items(tmp_path):
    """ev1537345 shipped 259 items with 255 unique ids. The scorer's dots inflated on it; a
    fingerprint would not (normalize takes a max), but the two readers must see the same card."""
    _write_page(tmp_path, "S", "E", 1, _bot_pbp([{"id": 7}, {"id": 7}, {"id": 8}], count=3))
    got = cbb.espn_pbp_from_bot_cache("E", "S", str(tmp_path))
    assert [i["id"] for i in got["commentary"]["items"]] == [7, 8]


def test_pairs_come_from_the_pin_ledger_not_from_hand_typed_args(tmp_path):
    """`--from-map` is the join that did not exist: 94 pinned pairs, 83 derived, and six of the
    twelve `cb:` rows on the identity tab were ONE undderived match (cb154370)."""
    mp = tmp_path / "map.json"
    mp.write_text(json.dumps({"pins": {
        "12123|2026-08-14|a+b": {"cricbuzz_match_id": "154370", "series_id": "12123",
                                 "date": "2026-08-14", "espn_events": ["1534184"]},
        "11493|2026-07-21|c+d": {"cricbuzz_match_id": "144662", "series_id": "11493",
                                 "date": "2026-07-21", "espn_events": ["1521231"]},
        "99999|2026-01-01|e+f": {"cricbuzz_match_id": "1", "series_id": "99999",
                                 "date": "2026-01-01", "espn_events": ["2"]},
        "12123|2026-08-16|g+h": {"cricbuzz_match_id": "154392", "series_id": "12123",
                                 "date": "2026-08-16", "espn_events": []},
    }}), encoding="utf-8")
    tr = tmp_path / "tours.json"
    tr.write_text(json.dumps([{"cricbuzz_series": "12123", "espn_series": "8623"},
                              {"cricbuzz_series": "11493", "espn_series": "1521176"},
                              {"espn_series": "1483859"}]), encoding="utf-8")
    got = cbb.pairs_from_match_map(str(mp), str(tr))
    assert got == [("144662", "1521231", "11493", "1521176", "2026-07-21"),
                   ("154370", "1534184", "12123", "8623", "2026-08-14")]
    # a cricbuzz series no tour claims is NAMED and dropped, not silently paired to the wrong
    # ESPN series; and a pin with no ESPN event yields no pair (it cannot be derived yet).
    assert cbb.pairs_from_match_map(str(mp), str(tr), series="12123") == [
        ("154370", "1534184", "12123", "8623", "2026-08-14")]


def test_the_espn_league_segment_is_the_series_id(tmp_path, monkeypatch):
    """`{ESPN_BASE}/{ESPN_SERIES}/playbyplay` is the only shape ESPN answers. Falling back to the
    legacy `--league lanka-premier-league` for a Hundred event returned HTTP 500, which the caller
    would have logged as "ESPN has no play-by-play for this match"."""
    seen = []
    monkeypatch.setattr(cbb, "fetch_cb_scorecard", lambda mid, cache: open(
        os.path.join(FIX, "flight_page.html"), encoding="utf-8").read())

    def fake(event_id, league, cache_dir):
        seen.append(league)
        return {"commentary": {"items": []}}
    monkeypatch.setattr(cbb, "fetch_espn_pbp", fake)
    cbb._load_pair("157061", "1521218", None, None, "lanka-premier-league",
                   espn_series="1521193", bot_cache=str(tmp_path))
    assert seen == ["1521193"]


# ══ the identity row a human has to answer ════════════════════════════════════════════════════
def test_every_unresolved_status_hands_the_human_a_cricbuzz_profile_url():
    """`detail` is interpolated verbatim into the "Needs Cricinfo ID" row. It used to read
    "no confirmation on any bridged match" — true, and unanswerable: no way even to see who the
    cricbuzz id belongs to. VERIFIED 16 Aug 2026 that /profiles/<id>/x serves the right player."""
    st = cbb.build_store([conf("11101", "381268", "m1"), conf("11101", "874201", "m1",
                                                              cbb.METHOD_DISMISSAL),
                          conf("50458", "1356971", "m2")])
    rev = cbb.resolve(st, "11101")
    assert rev.status == cbb.REVOKED
    assert "cricbuzz.com/profiles/11101/x" in rev.detail
    assert "ci:381268" in rev.detail and "ci:874201" in rev.detail
    assert "cricketers/x-381268" in rev.detail        # both candidates, both linked

    unk = cbb.resolve(st, "99999")
    assert unk.status == cbb.UNKNOWN and "cricbuzz.com/profiles/99999/x" in unk.detail
    assert "--from-map" in unk.detail                 # and what to run about it

    low = cbb.resolve(st, "50458", cbb.PURPOSE_CREATE)
    assert low.status == cbb.INSUFFICIENT_TIER
    assert "candidate ci:1356971" in low.detail and "cricketers/x-1356971" in low.detail
    assert "cricbuzz.com/profiles/50458/x" in low.detail
    assert cbb.resolve(st, "50458").status == cbb.OK  # crosscheck still fine at tier 1


# ══ the owner's answer, which had nowhere to land ══════════════════════════════════════════════
def test_an_answered_cb_row_becomes_a_confirmation_not_a_name_alias():
    """`read_needs_cricinfo` files a filled-in id into manual_ci_bridges as `ci:<id> -> [NAME]`,
    and builds its extra alias only for slug:/uncapped: pids — so a `cb:` row's cricbuzz id was
    DISCARDED and the bridge went on saying UNKNOWN. Measured 16 Aug 2026: cb:12163 and cb:10693
    were both answered on the live tab and both still resolved UNKNOWN."""
    st = cbb.build_store([conf("999", "111", "m1")])
    assert cbb.resolve(st, "12163").status == cbb.UNKNOWN
    st, changed = cbb.adopt(st, "12163", "633660", source="owner, Needs Cricinfo ID tab")
    assert changed
    r = cbb.resolve(st, "12163")
    assert (r.status, r.cricinfo_id, r.tier) == (cbb.OK, "633660", 1)
    # ...and it is a FACT in the log, so the store stays a pure function of it
    assert cbb.build_store(cbb.confirmations_log(st)) == st
    assert cbb.resolve(st, "12163", cbb.PURPOSE_CREATE).status == cbb.INSUFFICIENT_TIER
    st2, again = cbb.adopt(st, "12163", "633660")
    assert again is False and st2 == st              # idempotent


def test_a_manual_answer_cannot_alone_authorise_creating_points():
    """Tier counts DISTINCT MATCHES and every manual answer collapses into one slot, so a
    hand-typed id is a cross-check and never, by itself, licence to CREATE a Cricbuzz-only field
    (a run-out fielding credit ESPN structurally cannot supply)."""
    st = cbb.build_store([])
    st, _ = cbb.adopt(st, "1", "100")
    st, _ = cbb.adopt(st, "1", "100", source="a second answer, same pair")
    assert st["bridge"]["1"]["tier"] == 1
    assert cbb.resolve(st, "1", cbb.PURPOSE_CREATE).status == cbb.INSUFFICIENT_TIER
    st, _ = cbb.adopt(cbb.build_store([conf("1", "100", "cb1/espn1")]), "1", "100")
    assert st["bridge"]["1"]["tier"] == 2            # one derived match + the answer = 2


def test_a_manual_answer_does_not_outrank_derived_evidence():
    """One keystroke must not overwrite N matches of both-sides-unique fingerprints. It
    contradicts, and BOTH are refused — the existing, loud semantic."""
    st = cbb.build_store([conf("11101", "381268", "m%d" % i) for i in range(8)])
    st, _ = cbb.adopt(st, "11101", "874201", source="a typo")
    assert cbb.resolve(st, "11101").status == cbb.REVOKED
    assert set(st["revoked"]["11101"]["claims"]) == {"381268", "874201"}


def test_adopt_refuses_an_absence_wearing_the_clothes_of_an_answer():
    st = cbb.build_store([])
    for cb_id, ci_id in (("0", "1"), ("x", "1"), ("1", ""), ("1", "abc"), ("1", "0")):
        with pytest.raises(cbb.BridgeError):
            cbb.adopt(st, cb_id, ci_id)
    # a pasted profile URL is tolerated — that is a format, not an absence
    st, _ = cbb.adopt(st, "1", "https://www.espncricinfo.com/cricketers/x-633660")
    assert st["bridge"]["1"]["cricinfo_id"] == "633660"


def test_provenance_survives_every_reprojection():
    """`merge_confirmations` and `compile_bridge`/`confirmations_log` each RE-PROJECT a
    confirmation, so a field none of them names is deleted on the next recompile. `source` was
    dropped by two of the three when it was first added."""
    st = cbb.build_store([])
    st, _ = cbb.adopt(st, "1", "100", source="who said so")
    for _ in range(3):
        st = cbb.build_store(cbb.confirmations_log(st))
    assert st["bridge"]["1"]["confirmations"][0]["source"] == "who said so"
    # a derived confirmation gains no empty `source` key — that would rewrite the whole file
    plain = cbb.build_store([conf("2", "200", "m1")])
    assert plain["bridge"]["2"]["confirmations"] == [
        {"match": "m1", "method": cbb.METHOD_FINGERPRINT, "date": "2026-07-25"}]


def test_the_committed_store_is_a_pure_function_of_its_own_log():
    """The shipped file, not a synthetic one: regenerating it from its own confirmations must
    reproduce it exactly, or `--derive` is not reproducible and the file is hand-editable in
    practice whatever the header says."""
    st = cbb.load_store()
    assert cbb.build_store(cbb.confirmations_log(st)) == st
    assert st["revoked"] == {}, "a revoked pair is an unanswerable identity row — none should ship"


def test_the_derive_corpus_does_not_lag_the_pin_ledger():
    """THE ROOT CAUSE of six of the twelve `cb:` rows on "Needs Cricinfo ID", pinned on the two
    committed ledgers so it cannot come back silently.

    The pin ledger knew 94 cb-match ⇄ ESPN-event pairings; `--derive` took hand-typed `--pair`
    args, and nothing joined them — so 83 were derived and 11 were not. cb154370 (CPL Guyana v
    Jamaica, 14 Aug) was one of the 11, and it alone carried Glenn Phillips, Imran Tahir, Shamar
    Joseph, Mohammad Nabi, Shai Hope and Quentin Sampson. Measured end to end on the cached pair:
    cb_match_perf went from **10/22 bridged with 6 identity rows** to **22/22 with 0**.

    The one permitted exception is a fixture with nothing to derive: espn1521203 / cb145088,
    The Hundred Women's 26 July, "Match abandoned without a ball bowled" — ESPN's play-by-play
    carries 1 item and Cricbuzz serves a page with no `scoreCard` at all.
    """
    store = cbb.load_store()
    derived = {m for r in store["bridge"].values() for m in r["matches"]}
    derived |= {c["match"] for r in store.get("revoked", {}).values()
                for c in r["confirmations"]}
    with open(os.path.join(os.path.dirname(__file__), "..", "registry",
                           "cricbuzz_match_map.json"), encoding="utf-8") as fh:
        pins = json.load(fh)["pins"]
    want = {cbb.match_key(p["cricbuzz_match_id"], ev)
            for p in pins.values() for ev in (p.get("espn_events") or [])}
    assert want, "the pin ledger carries no ESPN event ids at all"
    assert want - derived == {"cb145088/espn1521203"}, sorted(want - derived)
    # and every pin must carry its event, or the pairing cannot enter the corpus at all
    assert [k for k, p in pins.items() if not p.get("espn_events")] == []
