"""cricket_identity — a FAITHFUL Python port of the shared name-matching model.

⛔ THIS IS A PORT, NOT AN IMPLEMENTATION. The algorithm lives in
   github:nishantsingodia/cricket-identity (src/index.ts) and is the owner's, built over months.
   wwc-draft and cricket-auction-helper consume the package directly. The bot cannot — it is
   Python — so this file mirrors it line for line and is pinned by the SAME fixtures
   (src/index.test.ts, reproduced verbatim in tests/test_cricket_identity.py).

   DO NOT "improve" it here. If a behaviour is wrong, fix it in the cricket-identity repo, bump
   the version, reinstall in the two JS consumers, and re-port. A change made only here silently
   re-creates the drift the package exists to prevent — that drift is what this project keeps
   paying for.

WHY THE BOT NEEDED THIS. The bot had EIGHT independent SequenceMatcher call sites with hand-tuned
weights — closest_squad scored `name_similarity * 60 + surname_similarity * 40` under a comment
reading "surname similarity dominates", and returned a best guess rather than None on ambiguity.
That is a DIFFERENT algorithm from the shared model, and the difference had a price: on CPL Match 6
it scored ESPN's "Glenn Dominic Phillips" against the squad and, because Glenn himself was
unmatchable and Dale was the only other Phillips, handed Glenn's innings to Dale — 99 FP on the
wrong man, with Glenn published as Played=N. The shared model's strategy 5 requires the surname to
be UNIQUE and returns None otherwise; weighted similarity has no such floor.

The port is deliberately literal: same strategy order, same guards, same `None` on ambiguity. Where
the TypeScript filters a list and checks `length === 1`, so does this.
"""
import re
import unicodedata

__all__ = ["norm_name", "fuzzy_match_name"]

_STRIP = re.compile(r"[^a-z ]")
_WS = re.compile(r"\s+")


def norm_name(s):
    """NFKD decompose, drop combining marks, lowercase, keep only [a-z ], collapse whitespace.

    Hyphens are REMOVED rather than turned into spaces — that is what makes strategy 3 work
    ("Wyatt-Hodge" -> "wyatthodge", so "wyatt" is a prefix of it). Mirrors normName exactly.
    """
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = _STRIP.sub("", s.lower())
    return _WS.sub(" ", s).strip()


def _surname_of(n):
    parts = [p for p in n.split(" ") if p]
    return parts[-1] if parts else ""


def _initial_of(n):
    parts = [p for p in n.split(" ") if p]
    return parts[0][0] if parts and parts[0] else ""


def fuzzy_match_name(name, candidates):
    """Best-matching candidate (the ORIGINAL un-normalized string), or None.

    Callers do not pre-normalize — both sides are normalized here, so the two can never disagree.

    Strategies, in descending confidence. Each returns only when EXACTLY ONE candidate matches;
    two matches mean ambiguity, and ambiguity returns None rather than a guess. That refusal is
    the whole point: it is what stops a benched namesake taking a slot (two Fernandos, two
    Mendises, Dale vs Glenn Phillips).

      1. exact normalized
      2. surname + first initial          "S Mandhana" -> "Smriti Mandhana"
      3. surname prefix + initial         "N Sciver"   -> "Nat Sciver-Brunt"   (min length 4)
      4. full-name prefix either way      "Chamari"    -> "Chamari Athapaththu"
      5. surname unique in the set        "WK Dilhari" -> "Kaveesha Dilhari"
    """
    candidates = list(candidates)
    if not candidates:
        return None

    n = norm_name(name)
    surname = _surname_of(n)
    initial = _initial_of(n)
    norm_c = [norm_name(c) for c in candidates]

    # 1. exact
    if n in norm_c:
        return candidates[norm_c.index(n)]

    # 2. surname + initial
    by_surname = [c for c, nc in zip(candidates, norm_c)
                  if _surname_of(nc) == surname and _initial_of(nc) == initial]
    if len(by_surname) == 1:
        return by_surname[0]

    # 3. surname prefix + initial (married / hyphenated changes). The min-length-4 guard is what
    #    stops a short surname prefixing half the field.
    by_prefix = []
    for c, nc in zip(candidates, norm_c):
        cs = _surname_of(nc)
        if (_initial_of(nc) == initial
                and (cs.startswith(surname) or surname.startswith(cs))
                and min(len(cs), len(surname)) >= 4):
            by_prefix.append(c)
    if len(by_prefix) == 1:
        return by_prefix[0]

    # 4. full-name prefix either direction (mononyms, added middle names)
    by_full = [c for c, nc in zip(candidates, norm_c)
               if nc.startswith(n) or n.startswith(nc)]
    if len(by_full) == 1:
        return by_full[0]

    # 5. surname unique in the candidate set
    by_surname_only = [c for c, nc in zip(candidates, norm_c) if _surname_of(nc) == surname]
    if len(by_surname_only) == 1:
        return by_surname_only[0]

    return None
