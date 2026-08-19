"""The record that one pid BECAME another — the primitive this repo has been missing.

WHY IT EXISTS
=============
`pid` is used as a primary key in nine append-only stores (settlement_snapshots, recon_overrides,
new_players, players, pid_map, the draft's three mirrors, and the sheet's Player ID column). But a
pid is NOT an identity — it is a mutable, non-injective LABEL for a human, recomputed every run
from whatever the feeds happened to spell. `resolve_perf_pid` mints one, `promote_new_players`
rewrites one, a `manual_ci_bridges` edit plus a `build_registry` run moves one.

Nothing anywhere recorded that a pid had ever changed. So when the label for one human moved or
forked, every pid-keyed store forked with it — silently, because each store's guards are keyed on
the pid too. Two symptoms, one defect, measured over the live settlement store (9 pid pairs,
0 false positives, ~800 duplicated points + ~220 split):

  · SEQUENTIAL fork (the label moved). `record_settlement` is write-once on `(match_key, pid)`.
    The key moved, so the guard could not fire, and a SECOND baseline row was minted for a match
    already settled. Gus Atkinson: 9 matches, 618 points, frozen 08-14 under ci:1126982 and again
    08-16 under ci:1039481 — identical points both times.
  · SIMULTANEOUS fork (two labels at once). The points land on one pid, the draft holds the other,
    and `matchPlayerInXI` — pid-authoritative by design — judges a player who PLAYED to be not in
    the XI. BACKUP_INTELLIGENCE then substitutes him out and the frozen XI shrinks. Joshua James
    (CPL M7) and Amari Goodridge (CPL M9) each cost a real contest a real player.

With this ledger the whole re-key class stops needing a detector: it becomes a join.

THE SHAPE, AND WHY
==================
`changes` is the FACT LOG and is APPEND-ONLY. Everything else is a pure function of it —
`compile_map()` and `canonical_pid()` read no clock, no environment and no other file, so a
re-derive reproduces byte-for-byte. That is deliberately the same discipline as
`registry/cricbuzz_match_map.json` (`confirmations` is the log; `pins`/`revoked` are compiled from
it) and for the same reason: a store that is edited in place cannot be audited after the fact, and
this one is the audit trail for money.

⛔ A FORK IS REFUSED, NEVER RESOLVED LAST-WINS. If the log says A became B and also that A became
C, that is a contradiction about a human's identity and `canonical_pid` returns A UNCHANGED rather
than picking one. Choosing would be exactly the silent wrong merge the whole exercise exists to
stop — and it would do it inside the money baseline. Forks surface via `forks()` for a human.

⛔ THIS FILE NEVER DELETES. A superseded pid keeps working forever: already-published sheet rows,
already-frozen settlements and already-drafted teams all still carry it, and they are all
immutable. `canonical_pid` is how a READER folds them together; it is not permission to rewrite
them. `registry/settlement_snapshots.json` in particular stays WRITE-ONCE — corrections there are
additive and are the owner's call, never this module's.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CHANGES_PATH = os.path.join(HERE, "identity_changes.json")

_NOTE = ("APPEND-ONLY record that one pid became another. `changes` is the fact log; every other "
         "view (compile_map, canonical_pid) is a pure function of it and reads no clock. Never "
         "edit or delete an entry — a superseded pid stays valid forever, because the rows already "
         "published under it are immutable. Regenerate views, never this list.")


def load(path=None):
    p = path or CHANGES_PATH
    # ABSENT and UNREADABLE are different answers and must not share a branch. No file yet is a
    # legitimately empty ledger (first run). A file that exists but will not parse is a CORRUPT
    # ledger, and `except Exception: return {}` — this repo's most-repeated bug shape — would let
    # a re-key proceed with no record, in a green run, which is the exact failure this module
    # exists to prevent. Truncation is realistic: json.dump writes in place, non-atomically.
    if not os.path.exists(p):
        return {"note": _NOTE, "changes": []}
    try:
        with open(p) as f:
            d = json.load(f)
    except Exception as e:
        raise ValueError(f"identity_changes: {p} exists but will not parse ({e}) — refusing to "
                         f"treat a corrupt ledger as an empty one") from e
    if not isinstance(d, dict) or not isinstance(d.get("changes"), list):
        # An absence must not present as a value: a corrupt ledger is NOT an empty ledger, and
        # silently returning {} here would let a re-key proceed with no record — the exact failure
        # this file exists to prevent. Refuse loudly instead.
        raise ValueError(f"identity_changes: {path or CHANGES_PATH} is unreadable or malformed — "
                         f"refusing to treat a corrupt ledger as an empty one")
    return d


def record(old_pid, new_pid, reason, evidence, at=None, path=None):
    """Append `old_pid -> new_pid`. Idempotent: re-recording the identical edge is a no-op.

    `evidence` is required and is prose for a human — WHY we believe these are one person. An
    entry without it is unauditable a month later, which is when it will actually be read.
    `at` is passed in rather than read from the clock so callers stay testable and deterministic.
    """
    old_pid, new_pid = str(old_pid or "").strip(), str(new_pid or "").strip()
    if not old_pid or not new_pid:
        raise ValueError("identity_changes.record: both pids are required")
    if old_pid == new_pid:
        return False
    if not (reason and evidence):
        raise ValueError(f"identity_changes.record: {old_pid} -> {new_pid} needs a reason AND "
                         f"evidence — an unexplained identity merge is not auditable")
    d = load(path)
    for c in d["changes"]:
        if c.get("from") == old_pid and c.get("to") == new_pid:
            return False                      # already recorded; append-only means no duplicates
    d["note"] = _NOTE
    d["changes"].append({"from": old_pid, "to": new_pid, "at": at or "",
                         "reason": reason, "evidence": evidence})
    with open(path or CHANGES_PATH, "w") as f:
        json.dump(d, f, indent=1, ensure_ascii=False)
    return True


def _edges(path=None, changes=None):
    src = changes if changes is not None else load(path)["changes"]
    out = {}
    for c in src:
        f, t = c.get("from"), c.get("to")
        if f and t:
            out.setdefault(f, set()).add(t)
    return out


def forks(path=None, changes=None):
    """{pid: [the several pids it is claimed to have become]} — contradictions needing a human."""
    return {f: sorted(ts) for f, ts in _edges(path, changes).items() if len(ts) > 1}


def canonical_pid(pid, path=None, changes=None):
    """Follow the chain to the pid in force today. Returns `pid` unchanged when it has not moved,
    when the chain forks, or when the chain cycles — never a guess, never a partial walk."""
    edges = _edges(path, changes)
    seen, cur = {pid}, pid
    while True:
        nxt = edges.get(cur)
        if not nxt or len(nxt) > 1:           # end of chain, or a fork -> refuse
            return cur if len(seen) > 1 else pid
        step = next(iter(nxt))
        if step in seen:                      # A -> B -> A: a cycle is a contradiction, not a loop
            return pid
        seen.add(step)
        cur = step


def compile_map(path=None, changes=None):
    """{every superseded pid: the pid in force today}. This is what `pid_map.json` holds, derived
    rather than accumulated — so it can be regenerated and can never drift from the log."""
    edges = _edges(path, changes)
    out = {}
    for f in edges:
        c = canonical_pid(f, path, changes)
        if c != f:
            out[f] = c
    return out
