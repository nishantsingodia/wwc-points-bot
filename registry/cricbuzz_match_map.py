#!/usr/bin/env python3
"""registry/cricbuzz_match_map.py — our match ⇄ cricbuzz match id, PINNED ONCE and read thereafter.

WHY THIS FILE EXISTS
  Players have a shared key (ESPN's athlete.id IS the cricinfo id) AND, where they don't, a
  DERIVED bridge with a confirmations log (registry/cricbuzz_bridge.py). MATCHES had neither.
  `cricbuzz.resolve_match_id` paired a fixture on normalised team names + date and RE-DERIVED IT
  ON EVERY RUN, with nothing recording that the pair had ever been made. So a rename on either
  side could silently re-pair or un-pair an already-SETTLED match and no ledger would show it.

  Not hypothetical — two naming conventions broke it in one week, both of them reported as
  "no unique cricbuzz match", which reads like Cricbuzz not having the fixture at all:
    ESPN "St Lucia Kings"  vs Cricbuzz "Saint Lucia Kings"  — cost 2 of 5 completed CPL matches
                                                              their second witness;
    ESPN "MI London (Men)" vs Cricbuzz "MI London"          — cost the Hundred Men's ALL 31.
  Both are now folded in `cricbuzz._bare_slug`, and that fold is exactly the kind of fix that
  works until the next convention appears. A PIN is the durable half: once a pairing has been
  derived it is recorded with its provenance and read back, so the NEXT rename cannot quietly
  take a settled match's cross-check away.

THE KEY, AND WHY IT CARRIES THE SERIES ID
  key = "<cricbuzz_series>|<our date>|<slug>+<slug>"   (team slugs sorted, `cricbuzz._slug`)

  The series id is IN the key on purpose. `wc_fps_to_csv.match_key_of` strips gender qualifiers,
  so The Hundred's men's and women's fixtures between the same franchises on the same day collapse
  onto ONE key there — the collision class that had just disabled the completed-ratchet's LIVE arm
  on 60 of 92 matches. A Cricbuzz SERIES is gender-specific (the Hundred is 11493 men / 11504
  women), so keying on it keeps the two double-header halves apart by construction, not by luck.
  Pinned today: 11493|2026-07-21|mi london+sunrisers leeds  ->  cb 144662  (men)
                11504|2026-07-21|mi london+sunrisers leeds  ->  cb 145011  (women)

WHAT IS STORED
  `confirmations` is an append-only fact log; `pins` and `revoked` are a PURE FUNCTION of it
  (`compile_pins` ∘ `confirmations_log`). No clock is read anywhere — every date in the file is a
  MATCH date out of a payload or off the caller's fixture — so re-deriving a season reproduces the
  file byte for byte. Regenerate; never hand-edit.

CONTRADICTION — FOUR DIRECTIONS, ALL REFUSING BOTH SIDES (never last-wins)
  key    -> 2 cricbuzz matches   the pairing MOVED. One of the two derivations is wrong and the
                                 evidence does not say which, so both are refused.
  cb id  -> 2 keys               two of our fixtures pointing at one Cricbuzz card, i.e. one of
                                 them would be cross-checked against a card that is not its own.
                                 EXCEPT where the two keys are demonstrably the SAME FIXTURE (see
                                 `same_fixture`), which is what a ±1-day date convention or a
                                 renamed team produces.
  ESPN event -> 2 cb matches     THE MIRROR OF THE ONE ABOVE, and the one that actually bites: one
                                 of OUR fixtures (proved by an id, not a name) paired to two
                                 different Cricbuzz cards. A rename that RE-pairs lands here and
                                 nowhere else — the slug moves so direction 1 cannot see it, the
                                 cb ids differ so direction 2 cannot either. Written last, after
                                 reproducing it: two keys, one ESPN event, cb154347 vs cb999002,
                                 both pinned, zero revoked.
  key    -> 2 ESPN events        our own key is ambiguous: a genuine same-day double-header
                                 between the same two sides INSIDE one (gender-specific) series.
                                 That is the one ambiguity `resolve_match_id` has always refused
                                 rather than guessed; the pin must not smuggle a guess back in.

  A revoked key STAYS revoked until a human runs `--forget <key>`. Self-healing here would be
  last-wins wearing a disguise.

⛔ AN ABSENCE IS NOT A CONTRADICTION.
  If a later derivation finds NO unique fixture (0 hits, or an ambiguous >1) for a key that is
  already pinned, the PIN STANDS. "Cricbuzz's team spelling changed so nothing matched" is an
  absence of evidence, and treating it as evidence against the pin would hand the rename exactly
  the power the pin exists to take away. Only a DIFFERENT id contradicts.

CLI
  python3 registry/cricbuzz_match_map.py --report
  python3 registry/cricbuzz_match_map.py --backfill --all [--write]
  python3 registry/cricbuzz_match_map.py --backfill --tour "Lanka Premier League 2026" --write
  python3 registry/cricbuzz_match_map.py --verify [--tour NAME]      # re-derive, report, no writes
  python3 registry/cricbuzz_match_map.py --forget "12316|2026-07-15|colombo kaps+jaffna kings"
"""
import argparse
import datetime
import glob
import json
import os
import sys
from collections import defaultdict, namedtuple

REG_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(REG_DIR)
MAP_PATH = os.path.join(REG_DIR, "cricbuzz_match_map.json")

SCHEMA = 1

# Statuses. Every failure mode gets its own NAME so a caller cannot mistake "we refused this pair"
# for "there is no pair" — the distinction the whole recon model is built on.
PINNED = "pinned"                  # the key itself is pinned
PINNED_BY_EVENT = "pinned_by_event"  # matched through the ESPN event id, not the key
UNPINNED = "unpinned"              # never derived; the caller should derive and record
REVOKED = "revoked"                # contradicted; refused on purpose until a human decides

Pin = namedtuple("Pin", "cricbuzz_match_id status detail key")

# The resolver's own date tolerance (cricbuzz._dates_for spreads a fixture over UTC/venue-local
# ±1 day). Two keys whose dates are within it CANNOT be two distinct fixtures of the same pair:
# if Cricbuzz really carried both, resolve_match_id would have seen 2 hits and refused to pin
# either. So the tolerance here and the tolerance there must stay the same number.
SAME_FIXTURE_DAYS = 1


class MatchMapError(Exception):
    """A refusal we want LOUD. Never downgrade one of these to a silent empty result."""


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 1. Keys
# ══════════════════════════════════════════════════════════════════════════════════════════════
def make_key(series_id, date, team_slugs):
    """"<series>|<date>|<slug>+<slug>". `team_slugs` must ALREADY be normalised.

    Normalisation lives in cricbuzz._slug and is deliberately NOT duplicated here: a second copy
    would drift from the resolver's, and a key built by one and read by the other would miss —
    silently, looking exactly like "we never pinned this match".
    """
    slugs = sorted({s for s in (team_slugs or []) if s})
    return "%s|%s|%s" % (str(series_id or ""), str(date or ""), "+".join(slugs))


def parse_key(key):
    """-> (series_id, date, [slug, ...]). Slugs are [a-z0-9 ] only, so '|' and '+' cannot occur
    inside one and this round-trips."""
    parts = str(key).split("|")
    if len(parts) != 3:
        raise MatchMapError("malformed key %r (want '<series>|<date>|<slug>+<slug>')" % key)
    return parts[0], parts[1], [s for s in parts[2].split("+") if s]


def _date_delta_days(a, b):
    """|a-b| in days, or None if either date is unparseable (absence, not zero)."""
    try:
        da = datetime.date.fromisoformat(a)
        db = datetime.date.fromisoformat(b)
    except (TypeError, ValueError):
        return None
    return abs((da - db).days)


def same_fixture(key_a, events_a, key_b, events_b):
    """Are two keys the SAME real-world fixture recorded twice? Evidence only, never a guess.

    Two things legitimately produce a second key for one fixture:
      • a DATE CONVENTION difference — cricapi's matchList date vs ESPN's event date differ by a
        day on evening starts, and the two call sites pass different ones;
      • a RENAME/alias change on our side — the team slug moves, so the key moves with it.

    The first is provable from the key alone (same series, same team set, dates inside the
    resolver's own ±1-day tolerance). The second is NOT — a changed name is exactly what we
    refuse to reason about — so it is admitted only on the strength of a shared ESPN EVENT ID,
    which is an id, not a name. Without one, two keys on one Cricbuzz card are a contradiction
    and both are refused (loudly, fail-safe) rather than one being picked.
    """
    shared = set(events_a or []) & set(events_b or [])
    if shared:
        return True
    sa, da, ta = parse_key(key_a)
    sb, db, tb = parse_key(key_b)
    if sa != sb or ta != tb:
        return False
    delta = _date_delta_days(da, db)
    return delta is not None and delta <= SAME_FIXTURE_DAYS


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 2. The fact log
# ══════════════════════════════════════════════════════════════════════════════════════════════
def _conf_sort_key(c):
    return (str(c["key"]), str(c["cricbuzz_match_id"]), str(c.get("method", "")),
            str(c.get("espn_event", "")))


def merge_confirmations(existing, new):
    """Union, deduped on (key, cricbuzz_match_id, method, espn_event), deterministically ordered.

    A CONFLICTING claim is KEPT, not overwritten — that is what makes the contradiction visible.
    An overwrite here would be last-wins with extra steps, and last-wins is how a rename gets to
    move a settled match's pairing without anybody being told.
    """
    seen, out = set(), []
    for c in sorted(list(existing) + list(new), key=_conf_sort_key):
        k = _conf_sort_key(c)
        if k in seen:
            continue
        seen.add(k)
        out.append({"key": str(c["key"]),
                    "cricbuzz_match_id": str(c["cricbuzz_match_id"]),
                    "method": str(c.get("method", "")),
                    "espn_event": str(c.get("espn_event", "") or ""),
                    "cb_desc": str(c.get("cb_desc", "") or ""),
                    "cb_date": str(c.get("cb_date", "") or "")})
    return out


def _pin_record(key, cb_id, confs):
    series_id, date, teams = parse_key(key)
    events = sorted({c["espn_event"] for c in confs if c.get("espn_event")})
    return {"cricbuzz_match_id": str(cb_id), "series_id": series_id, "date": date,
            "teams": teams, "espn_events": events, "also_keyed_as": [],
            "confirmations": [{"method": c["method"], "espn_event": c["espn_event"],
                               "cb_desc": c["cb_desc"], "cb_date": c["cb_date"]}
                              for c in confs]}


def _revoked_record(key, reason, confs, extra=None):
    series_id, date, teams = parse_key(key)
    rec = {"reason": reason, "series_id": series_id, "date": date, "teams": teams,
           "remedy": ("decide which pairing is right, then "
                      "`python3 registry/cricbuzz_match_map.py --forget <key>` the wrong one"),
           "confirmations": [{"cricbuzz_match_id": c["cricbuzz_match_id"], "method": c["method"],
                              "espn_event": c["espn_event"], "cb_desc": c["cb_desc"],
                              "cb_date": c["cb_date"]} for c in confs]}
    if extra:
        rec.update(extra)
    return rec


def compile_pins(confirmations):
    """PURE function of the fact log → ({pins}, {revoked}). See the three directions in the
    module header; all three refuse BOTH sides."""
    by_key = defaultdict(list)
    for c in confirmations:
        by_key[str(c["key"])].append(c)

    pins, revoked = {}, {}

    # ── direction 1: one of our matches claimed by two Cricbuzz matches ───────────────────────
    for key in sorted(by_key):
        confs = sorted(by_key[key], key=_conf_sort_key)
        claims = defaultdict(list)
        for c in confs:
            claims[str(c["cricbuzz_match_id"])].append(c)
        if len(claims) > 1:
            revoked[key] = _revoked_record(
                key, "this match is claimed by %d cricbuzz matches (%s) — the pairing MOVED; "
                     "refusing both, never last-wins"
                     % (len(claims), ", ".join("cb%s" % c for c in sorted(claims))),
                confs, {"claims": {cb: sorted({c["method"] for c in cs})
                                   for cb, cs in sorted(claims.items())}})
            continue
        cb_id = next(iter(claims))
        # ── direction 3: our own key is ambiguous — two ESPN events under one key ────────────
        events = sorted({c["espn_event"] for c in confs if c.get("espn_event")})
        if len(events) > 1:
            revoked[key] = _revoked_record(
                key, "one key, %d ESPN events (%s) — a same-day double-header between the same "
                     "two sides inside one series; the key cannot tell them apart"
                     % (len(events), ", ".join(events)),
                confs, {"espn_events": events})
            continue
        pins[key] = _pin_record(key, cb_id, confs)

    # ── direction 2: one Cricbuzz match claimed by two of our matches ─────────────────────────
    by_cb = defaultdict(list)
    for key, rec in pins.items():
        by_cb[rec["cricbuzz_match_id"]].append(key)
    for cb_id, keys in sorted(by_cb.items()):
        if len(keys) < 2:
            continue
        keys = sorted(keys)
        # Partition into same-fixture groups. One group => the same fixture recorded under two
        # keys (a date convention or a rename) => keep both, cross-referenced. More than one
        # group => genuinely different fixtures on one card => refuse every one of them.
        groups = []
        for k in keys:
            ev = pins[k]["espn_events"]
            for g in groups:
                if any(same_fixture(k, ev, o, pins[o]["espn_events"]) for o in g):
                    g.append(k)
                    break
            else:
                groups.append([k])
        if len(groups) == 1:
            for k in keys:
                pins[k]["also_keyed_as"] = [o for o in keys if o != k]
            continue
        for k in keys:
            rec = pins.pop(k)
            confs = [dict(c, key=k, cricbuzz_match_id=cb_id) for c in rec["confirmations"]]
            revoked[k] = _revoked_record(
                k, "cricbuzz match cb%s is claimed by %d different fixtures (%s) — one of them "
                   "would be cross-checked against a card that is not its own; refusing all"
                   % (cb_id, len(keys), "; ".join(keys)),
                merge_confirmations(confs, []),
                {"collides_with": [o for o in keys if o != k]})

    # ── direction 4: one ESPN event pointing at two Cricbuzz matches ──────────────────────────
    # THE MIRROR OF DIRECTION 2, AND THE ONE THAT ACTUALLY BITES. Direction 2 catches "two of our
    # keys, one cricbuzz card"; this catches "one of OUR fixtures (proved by its ESPN event id),
    # two cricbuzz cards" — which is what a rename produces when it re-pairs rather than un-pairs:
    # the team slug moves, so the new key is NOT the old key and direction 1 never fires, and the
    # cricbuzz ids differ, so direction 2 never fires either. Both pins then stand and a lookup by
    # the new key silently returns the NEW pairing for a match whose points are already settled —
    # the exact failure this whole file exists to make impossible. Verified missing before this
    # block existed: two keys, one ESPN event, cb 154347 vs cb 999002, both pinned, 0 revoked.
    by_event = defaultdict(set)
    for key, rec in pins.items():
        for ev in rec.get("espn_events") or []:
            by_event[ev].add(key)
    for ev, keys in sorted(by_event.items()):
        ids = {pins[k]["cricbuzz_match_id"] for k in keys if k in pins}
        if len(ids) < 2:
            continue
        for k in sorted(keys):
            if k not in pins:
                continue
            rec = pins.pop(k)
            confs = [dict(c, key=k, cricbuzz_match_id=rec["cricbuzz_match_id"])
                     for c in rec["confirmations"]]
            revoked[k] = _revoked_record(
                k, "ESPN event %s — one fixture — is paired to %d cricbuzz matches (%s); the "
                   "pairing was RE-DERIVED to something else. Refusing all"
                   % (ev, len(ids), ", ".join("cb%s" % i for i in sorted(ids))),
                merge_confirmations(confs, []),
                {"espn_event": ev, "collides_with": sorted(k2 for k2 in keys if k2 != k)})
    return pins, revoked


def confirmations_log(store):
    """Flatten the per-pin provenance back into the fact log compile_pins eats. The file stores
    each confirmation exactly ONCE, under its pin; this is the reader."""
    out = []
    for key, rec in (store.get("pins") or {}).items():
        for c in rec.get("confirmations", []):
            out.append(dict(c, key=key, cricbuzz_match_id=rec["cricbuzz_match_id"]))
    for key, rec in (store.get("revoked") or {}).items():
        for c in rec.get("confirmations", []):
            out.append(dict(c, key=key))
    return merge_confirmations(out, [])


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 3. The store
# ══════════════════════════════════════════════════════════════════════════════════════════════
def empty_store():
    return build_store([])


def build_store(confirmations):
    pins, revoked = compile_pins(confirmations)
    series = sorted({rec["series_id"] for rec in pins.values()})
    return {
        "_schema": SCHEMA,
        "_anchor": ("our match ('<cricbuzz series>|<date>|<team slug>+<team slug>') -> the "
                    "cricbuzz match id it was FIRST derived to, with provenance. The series id is "
                    "part of the key because it is gender-specific (the Hundred: 11493 men / "
                    "11504 women), which is what keeps a men's and women's double-header between "
                    "the same franchises on the same day apart."),
        "_rules": ("PINS ARE READ, NOT RE-DERIVED — that is the point: a rename upstream cannot "
                   "move or drop a pairing that is already recorded. A later derivation that "
                   "finds NOTHING does not disturb a pin (absence is not evidence). A later "
                   "derivation that finds a DIFFERENT cricbuzz match revokes BOTH claims; so does "
                   "one cricbuzz match claimed by two different fixtures, and one key carrying "
                   "two ESPN events. Never last-wins. A revoked key stays revoked until a human "
                   "runs --forget."),
        "_note": ("each pin's `confirmations` list IS the fact log; pins/revoked are a pure "
                  "function of it (compile_pins o confirmations_log), and no clock is read "
                  "anywhere. Regenerate, never hand-edit."),
        "counts": {"confirmations": len(merge_confirmations(confirmations, [])),
                   "pinned": len(pins), "revoked": len(revoked), "series": len(series)},
        "series": series,
        "pins": pins,
        "revoked": revoked,
    }


def load_store(path=MAP_PATH):
    if not os.path.exists(path):
        return empty_store()
    with open(path, encoding="utf-8") as fh:
        store = json.load(fh)
    if not isinstance(store, dict) or "pins" not in store:
        # A map file we cannot read is NOT "nothing is pinned" — that would silently un-pin every
        # settled match and let a rename through the moment the file got truncated.
        raise MatchMapError("%s is not a cricbuzz match map (no `pins` key)" % path)
    return store


def save_store(store, path=MAP_PATH):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(store, fh, indent=1, ensure_ascii=False, sort_keys=False)
        fh.write("\n")
    return path


def lookup(store, key, espn_event=None):
    """The ONLY sanctioned read path. Returns a Pin whose `status` NAMES the outcome — never a
    bare None a caller can mistake for 'no pairing exists'."""
    revoked = store.get("revoked") or {}
    if key in revoked:
        return Pin(None, REVOKED, revoked[key]["reason"], key)
    pins = store.get("pins") or {}
    rec = pins.get(key)
    if rec:
        return Pin(rec["cricbuzz_match_id"], PINNED,
                   "pinned on %s (%s)" % (rec["date"], ", ".join(
                       c["method"] for c in rec.get("confirmations", [])) or "no method recorded"),
                   key)
    # The ESPN event id is the rename-proof anchor: an id, not a name. If our team spelling or
    # our date moved, the KEY moves with it, but the event id does not.
    ev = str(espn_event or "")
    if ev:
        series_id = parse_key(key)[0]
        for k in sorted(pins):
            r = pins[k]
            if r["series_id"] == series_id and ev in (r.get("espn_events") or []):
                return Pin(r["cricbuzz_match_id"], PINNED_BY_EVENT,
                           "pinned under %s, matched on ESPN event %s (our key moved — a team "
                           "was renamed or the date convention changed)" % (k, ev), k)
        for k in sorted(revoked):
            for c in revoked[k].get("confirmations", []):
                if c.get("espn_event") == ev and parse_key(k)[0] == series_id:
                    return Pin(None, REVOKED, "ESPN event %s belongs to revoked key %s: %s"
                               % (ev, k, revoked[k]["reason"]), k)
    return Pin(None, UNPINNED, "no pairing has ever been derived for this match", key)


def record(store, key, cricbuzz_match_id, method="teams+date", espn_event="",
           cb_desc="", cb_date=""):
    """Append one confirmation and recompile. Returns (store, changed).

    Idempotent: recording a confirmation the log already carries changes nothing and rewrites
    nothing, so a run that pairs the same 31 matches it paired yesterday produces an EMPTY diff.
    """
    if int(cricbuzz_match_id or 0) <= 0:
        # `series_matches` reads matchId through _int(), which yields 0 for a missing or
        # unparseable one. Pinning "0" would put an ABSENCE in the file wearing the clothes of a
        # value: str("0") is truthy, int("0") is falsy, and the two disagree in the caller.
        raise MatchMapError("refusing to pin %r to a non-positive cricbuzz match id %r"
                            % (key, cricbuzz_match_id))
    conf = {"key": key, "cricbuzz_match_id": str(cricbuzz_match_id), "method": method,
            "espn_event": str(espn_event or ""), "cb_desc": cb_desc or "", "cb_date": cb_date or ""}
    log = confirmations_log(store)
    merged = merge_confirmations(log, [conf])
    if merged == log:
        return store, False
    return build_store(merged), True


def forget(store, key):
    """Drop every confirmation for `key` — the human's revocation lever. Returns (store, n).

    This is the ONLY way a pin or a revocation leaves the file. Deliberately not automatic:
    a self-healing revocation is last-wins in a costume.
    """
    log = confirmations_log(store)
    kept = [c for c in log if c["key"] != key]
    return build_store(kept), len(log) - len(kept)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# 4. CLI — report / backfill / verify / forget
# ══════════════════════════════════════════════════════════════════════════════════════════════
def _tours(path=None):
    with open(path or os.path.join(REPO_DIR, "tours.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _cached_espn_fixtures(espn_series, cache_dir):
    """Completed fixtures for a series out of the ALREADY-CACHED ESPN scoreboards. No network.

    Mirrors wc_fps_to_csv._espn_event_to_match for the three fields a pin needs (date, team
    display names, event id) and takes only what ESPN calls `post`, because that is the only
    class of match the bot ever asks Cricbuzz about: cb_match_perf is skipped on LIVE matches and
    on the frequent tick, so an UPCOMING fixture is never pinned at runtime and must not be
    pinned here either — its date is still free to move, and a moved date is what manufactures a
    second key.
    """
    out = {}
    pat = os.path.join(cache_dir, "espn_%s_scoreboard_dates_*.json" % espn_series)
    for fp in sorted(glob.glob(pat)):
        try:
            with open(fp, encoding="utf-8") as fh:
                sb = json.load(fh)
        except (OSError, ValueError):
            continue
        for e in sb.get("events") or []:
            comp = (e.get("competitions") or [{}])[0]
            teams = [c.get("team", {}).get("displayName", "") for c in comp.get("competitors", [])]
            state = (((e.get("status") or {}).get("type") or {}).get("state") or "").lower()
            if len(teams) != 2 or not all(teams) or state != "post":
                continue
            out[str(e.get("id"))] = {"date": (e.get("date") or "")[:10], "teams": teams,
                                     "espn_event": str(e.get("id"))}
    return sorted(out.values(), key=lambda m: (m["date"], m["espn_event"]))


def _reader():
    """cricbuzz.py, imported LAZILY and only by the CLI.

    The store must not depend on the reader: cricbuzz.py imports this module, and a module-level
    import back would be a cycle. It also keeps the store loadable (and hand-inspectable) on a
    box where the scraper is mid-edit.
    """
    if REPO_DIR not in sys.path:
        sys.path.insert(0, REPO_DIR)
    import cricbuzz
    return cricbuzz


def _backfill(store, tours, cache_dir, verbose=True):
    cb = _reader()
    total = {"pinned": 0, "already": 0, "unpaired": 0, "no_fixtures": 0}
    for tour in tours:
        cb_series = str(tour.get("cricbuzz_series") or "").strip()
        espn_series = str(tour.get("espn_series") or "").strip()
        if not cb_series:
            continue
        fixtures = _cached_espn_fixtures(espn_series, cache_dir) if espn_series else []
        if not fixtures:
            # "0 fixtures" is a CACHE MISS, not an empty tour — say which, or the backfill reports
            # a clean zero for a tour it never actually looked at.
            total["no_fixtures"] += 1
            print("  %-42s NO cached ESPN scoreboards for series %s in %s — nothing to backfill"
                  % (tour.get("name", "?")[:42], espn_series or "(unset)", cache_dir),
                  file=sys.stderr)
            continue
        n_new = n_old = n_miss = 0
        for f in fixtures:
            key = make_key(cb_series, f["date"], [cb._slug(t) for t in f["teams"]])
            hit = lookup(store, key, f["espn_event"])
            if hit.status in (PINNED, PINNED_BY_EVENT) and hit.key == key:
                n_old += 1
                continue
            fixture, why, _near = cb.derive_match(cb_series, f["date"], f["teams"])
            if not fixture:
                n_miss += 1
                if verbose:
                    print("     unpaired  %s  (%s)" % (key, why), file=sys.stderr)
                continue
            store, changed = record(store, key, fixture["match_id"], method="teams+date",
                                    espn_event=f["espn_event"], cb_desc=fixture["desc"],
                                    cb_date=cb.fixture_date(fixture))
            n_new += 1 if changed else 0
            n_old += 0 if changed else 1
        print("  %-42s cb series %-6s  +%d pinned, %d already, %d unpaired  (of %d completed)"
              % (tour.get("name", "?")[:42], cb_series, n_new, n_old, n_miss, len(fixtures)),
              file=sys.stderr)
        total["pinned"] += n_new
        total["already"] += n_old
        total["unpaired"] += n_miss
    return store, total


def _verify(store, tours, only=None):
    """Re-derive every pinned key from the (cached) series pages and report. WRITES NOTHING."""
    cb = _reader()
    agree = absent = differ = 0
    for key, rec in sorted((store.get("pins") or {}).items()):
        series_id, date, teams = parse_key(key)
        if only and series_id != only:
            continue
        fixture, why, _near = cb.derive_match(series_id, date, teams)
        got = str(fixture["match_id"]) if fixture else None
        if got == rec["cricbuzz_match_id"]:
            agree += 1
        elif got is None:
            absent += 1
            print("  ABSENT      %s  pin cb%s stands (%s)" % (key, rec["cricbuzz_match_id"], why))
        else:
            differ += 1
            print("  CONTRADICTS %s  pin cb%s vs derived cb%s"
                  % (key, rec["cricbuzz_match_id"], got))
    print("verify: %d agree, %d unconfirmable (pin stands), %d CONTRADICTED" % (agree, absent, differ))
    return differ


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--map", default=MAP_PATH)
    ap.add_argument("--cache", default=os.environ.get("WC_CACHE_DIR", "/tmp/wc_api_cache"))
    ap.add_argument("--tours", default=None)
    ap.add_argument("--tour", default=None, help="restrict to one tour name")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--forget", default=None, metavar="KEY")
    ap.add_argument("--write", action="store_true", help="persist (backfill only writes with it)")
    a = ap.parse_args(argv)

    store = load_store(a.map)
    tours = [t for t in _tours(a.tours) if not a.tour or t.get("name") == a.tour]

    if a.backfill:
        if not a.all and not a.tour:
            ap.error("--backfill needs --all or --tour")
        store, tot = _backfill(store, tours, a.cache)
        print("backfill: +%d pinned, %d already pinned, %d unpaired, %d tours with no cache"
              % (tot["pinned"], tot["already"], tot["unpaired"], tot["no_fixtures"]),
              file=sys.stderr)
        if a.write:
            print("wrote %s" % save_store(store, a.map), file=sys.stderr)
        else:
            print("DRY RUN — pass --write to persist", file=sys.stderr)

    if a.forget:
        store, n = forget(store, a.forget)
        print("forget %s: dropped %d confirmation(s)" % (a.forget, n), file=sys.stderr)
        if a.write:
            print("wrote %s" % save_store(store, a.map), file=sys.stderr)
        else:
            print("DRY RUN — pass --write to persist", file=sys.stderr)

    if a.verify:
        only = None
        if a.tour:
            only = str((tours[0] if tours else {}).get("cricbuzz_series") or "") or None
        return 1 if _verify(store, tours, only) else 0

    if a.report or not (a.backfill or a.forget or a.verify):
        pins = store.get("pins") or {}
        by_series = defaultdict(int)
        for rec in pins.values():
            by_series[rec["series_id"]] += 1
        print("pins: %d over %d series" % (len(pins), len(by_series)))
        for sid in sorted(by_series):
            print("   series %-6s %3d pinned" % (sid, by_series[sid]))
        ev = sum(1 for r in pins.values() if r.get("espn_events"))
        print("   %d/%d carry an ESPN event id (the rename-proof anchor)" % (ev, len(pins)))
        multi = {k: r for k, r in pins.items() if r.get("also_keyed_as")}
        if multi:
            print("   %d key(s) are the same fixture under a second key:" % len(multi))
            for k in sorted(multi):
                print("      %s  ==  %s" % (k, ", ".join(multi[k]["also_keyed_as"])))
        rev = store.get("revoked") or {}
        print("revoked: %d" % len(rev))
        for k in sorted(rev):
            print("   %s\n      %s" % (k, rev[k]["reason"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
