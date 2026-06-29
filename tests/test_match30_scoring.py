"""Match 30 (AUS v IND, WWC 2026) scoring anchor — the real incident that motivated the
recon-review feature. These assert score() on the cricapi (buggy) vs ESPN (correct) perf
dicts. Pure score() calls, so green on CURRENT code; the recon detection / status / override
behaviour is covered in test_match_status.py once that logic lands.

Live-sheet bug: Charani's 2 wkts showed 43, Perry's fifty showed 79 — both scored off a
cricapi scorecard frozen mid-innings (Charani 1 wkt; Perry 38* not-out)."""


def test_charani_cricapi_reproduces_bug(perf, wcmod):
    # cricapi (frozen): 1 wkt, 3 overs for 26, 9 dots -> 30 + 9 + 4(XI) = 43.
    s = wcmod.score(perf("Shree Charani", w=1, balls=18, runs_conceded=26, dots=9, played=True), "BOWL")
    assert s["total"] == 43


def test_charani_espn_corrected(perf, wcmod):
    # ESPN (real): 2 wkts (the 2nd is Perry's wicket) -> 60 + 9 + 4 = 73. econ 8.67 neutral.
    s = wcmod.score(perf("Shree Charani", w=2, balls=18, runs_conceded=26, dots=9, played=True), "BOWL")
    assert s["total"] == 73


def test_perry_cricapi_reproduces_bug(perf, wcmod):
    # cricapi (frozen): 38* off 26 (5x4), 1 catch, 1 over (3 dots) -> 79.
    s = wcmod.score(perf("Ellyse Perry", r=38, b=26, catches=1, balls=6, dots=3,
                         played=True, **{"4s": 5}), "AR")
    # bat 62 (38 + 20 + m25 4) ; sr 146 -> +2 ; bowl 3 ; field 8 ; xi 4
    assert s["total"] == 79


def test_perry_espn_corrected(perf, wcmod):
    # L1 override fixes the flagged fields (runs 38->57, 4s 5->8); balls stays cricapi's 26
    # (balls is not an L1 field), so SR reads high (219) until cricsheet/L2 corrects to 56-off-38.
    s = wcmod.score(perf("Ellyse Perry", r=57, b=26, catches=1, balls=6, dots=3,
                         played=True, **{"4s": 8}), "AR")
    # bat 97 (57 + 32 + m50 8) ; sr 219 -> +6 ; bowl 3 ; field 8 ; xi 4 = 118
    assert s["total"] == 118
