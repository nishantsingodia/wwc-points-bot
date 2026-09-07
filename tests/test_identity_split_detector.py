"""One human split across two pids — the mirror failure nothing in this file could see.

`false_merge`, `duplicate_pid` and `duplicate_slot` all detect OVER-merging: two names collapsed
onto one pid. The opposite — one person split across two pids — had NO surface anywhere, and it is
the one that costs points, because every store keys on pid:

  · settlement_snapshots is write-once on `(match_key, pid)`, so both halves settle separately;
  · the draft's `matchPlayerInXI` is pid-authoritative, so the half the draft is NOT holding reads
    as "did not play", BACKUP_INTELLIGENCE substitutes him out, and the frozen XI shrinks.
    Live cost: CPL M7 contest 180 froze a 10-man XI while Joshua James scored 73, and CPL M9
    contest 182 did the same while Amari Goodridge scored 29.

WHY IT WAS INVISIBLE. `resolve_perf_pid`'s guard says "NEVER let an ESPN id override a pid the
registry already holds for this name" — but it is keyed on `resolve_pid(name)`, i.e. it is a guard
against name-blind splitting that is itself NAME-keyed. It therefore cannot fire on the single
input that causes splits, a longer legal name:

    resolve_pid('Joshua James')          -> 'uncapped:joshua-james'
    resolve_pid('Joshua Michael James')  -> None        <- what the feed actually sends

⛔ THE DETECTOR ASKS, IT DOES NOT DECIDE. Names cannot settle identity in this project.
`long_form_plausible` can only raise a question — it returns False for Dale vs Glenn by design —
and the answer lands on the Identity Anomalies tab, whose Yes/No re-keys identity via
registry/identity_changes.json. The performance is still published under the minted id either way:
losing it would be strictly worse than publishing it under a pid awaiting a human.
"""
import pytest


# ── THE SPLITS THAT ARE OPEN RIGHT NOW ───────────────────────────────────────────────────────
# One list, two guards: the detector must FIND every one of these, and must find NOTHING ELSE.
# Each is a real human sitting on two pids — an `uncapped:` placeholder minted from the squad
# sheet's announced name, and a `ci:` id the bot derived from ESPN's roster (where athlete.id IS
# the cricinfo id) when the feed sent his longer legal name. Cross-checked against people.csv:
# 364343=CA Young, 671805=HG Munsey, 519070=MG Erasmus, 1278259=O Davidson. Pollard's id is
# ESPN-derived only — people.csv has no row for it yet, which is normal for a debutant.
#
# ⚠ THIS LIST IS A TO-DO, NOT A BASELINE. Merging one is not a bridge-and-forget: each
# `uncapped:` entry carries a live `draft_id`, so the merge needs a pid_map redirect and a
# registry rebuild or the draft keeps pointing at the orphaned half. Delete a line here when its
# split is actually merged — and if a SIXTH appears, test_the_detector_does_not_flood_the_tab
# fails, which is the point.
OPEN_SPLITS = [
    ("Craig Alexander Young",   "ci:364343",   "uncapped:craig-young"),
    ("Henry George Munsey",     "ci:671805",   "uncapped:george-munsey"),
    ("Merwe Gerhard Erasmus",   "ci:519070",   "uncapped:gerhard-erasmus"),
    ("Oliver Forbes Davidson",  "ci:1278259",  "uncapped:oliver-davidson"),
    ("Jakeem Javier Pollard",   "ci:1500761",  "uncapped:jakeem-pollard"),
]


@pytest.fixture
def reg(wcmod):
    """The live registry, loaded once — these assertions are about REAL data, not fixtures.

    `load_new_players` too, and that is not incidental: main() calls it before anything reads
    identity, so the pid set the detector actually runs against in production INCLUDES the
    sheet-added players. Omitting it here made this file's result depend on whether some earlier
    test in the suite happened to have loaded them — test_the_detector_does_not_flood_the_tab
    passed alone and failed in the full run, for a whole day, while the five splits it was
    correctly reporting went unread as "test pollution"."""
    wcmod.load_registry()
    wcmod.load_new_players()
    return wcmod


# ── it fires on the real splits ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("feed_name,minted,expect_twin", OPEN_SPLITS)
def test_a_long_legal_name_over_a_squad_placeholder_raises_the_question(reg, feed_name, minted,
                                                                       expect_twin):
    """Was pinned on Joshua Michael James and Amari Alexandre Goodridge. BOTH HAVE SINCE BEEN
    MERGED — their placeholders no longer exist, so the detector correctly returns "" for them and
    this guard had quietly lost its subject. Re-pointed at the splits that are open today; the
    file's choice to assert against REAL registry data rather than fixtures is deliberate (see the
    module docstring), and the cost of that choice is exactly this: the list needs re-pointing
    when the data it describes is fixed. That is a fair price for a guard that cannot pass by
    testing a mock of itself."""
    assert reg._placeholder_twin(feed_name, minted) == expect_twin


# ── it stays quiet on players who are genuinely new ─────────────────────────────────────────
@pytest.mark.parametrize("feed_name,minted", [
    ("Rivaldo A Clarke", "ci:1275938"),
    ("Kevlon Alston Anderson", "ci:1209188"),
    ("Khari Campbell", "ci:1443401"),
    ("Charli Rae Knott", "ci:1164537"),
])
def test_a_genuinely_new_signing_is_NOT_flagged(reg, feed_name, minted):
    """These four are mid-tournament additions whose ci: pids were minted CORRECTLY from ESPN's
    athlete id — they are in no squad at all, so there is no twin and nothing to ask. A handoff
    grouped them with the real splits; flagging them would send a human to merge a player with
    nobody, and 4 unanswerable rows a match is the flood the design rules exist to prevent."""
    assert reg._placeholder_twin(feed_name, minted) == ""


def test_a_player_already_merged_stays_quiet(reg):
    """Odean Smith was promoted uncapped:odean-smith -> ci:820691 and the placeholder no longer
    exists. Re-raising a question that has been answered is how a tab becomes noise."""
    assert reg._placeholder_twin("Odean Fabian Smith", "ci:820691") == ""


# ── the refusal that matters most ───────────────────────────────────────────────────────────
def test_glenn_is_never_offered_as_dales_twin(reg):
    """Dale and Glenn Phillips are TWO REAL HUMANS. long_form_plausible returns False for them by
    design; if this ever fires, the detector has become the very merge that started this project."""
    assert reg._placeholder_twin("Glenn Dominic Phillips", "ci:823509") == ""
    assert reg._placeholder_twin("Dale Phillips", "ci:902447") == ""


def test_an_anchored_ci_pid_is_never_offered_as_a_twin(reg):
    """Only placeholders can be twins. An anchored ci: entry already HAS a cricinfo id, so if it
    were the same person the id-first branches would have resolved him and the mint never reached.
    Offering one would propose merging two people who each have a verified, distinct id."""
    for nm in ("Glenn Dominic Phillips", "Joshua Michael James", "Rivaldo A Clarke"):
        t = reg._placeholder_twin(nm, "ci:999999")
        assert t == "" or t.startswith(("uncapped:", "slug:", "cs:")), t


def test_an_empty_name_asks_nothing(reg):
    assert reg._placeholder_twin("", "ci:1") == ""


# ── precision over the whole live registry ──────────────────────────────────────────────────
def test_the_detector_does_not_flood_the_tab(reg):
    """Every registry display name probed against every placeholder. Measured: 0 raise. A
    detector that fires on routine data is worse than none — it trains the owner to ignore the
    one tab where the answer moves settled money."""
    noisy = {(pid, disp, t) for pid, disp in reg.PID2DISP.items()
             if (t := reg._placeholder_twin(disp, pid))}
    known = {(m, d, u) for d, m, u in OPEN_SPLITS}
    assert noisy - known == set(), \
        f"a NEW split appeared: {sorted(noisy - known)}"
    assert known - noisy == set(), \
        f"these splits are fixed — delete them from OPEN_SPLITS: {sorted(known - noisy)}"
