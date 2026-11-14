"""FSRS-4.5 scheduler (pure Python, default weights).

Memory state per card: stability S (days until recall probability drops to 90%) and
difficulty D (1..10). Grades: 1 Again, 2 Hard, 3 Good, 4 Easy.
Reference: open-spaced-repetition/fsrs4anki wiki, "The Algorithm" (FSRS v4.5).
"""
from __future__ import annotations

import datetime as _dt
import math

W = [0.4872, 1.4003, 3.7145, 13.8206, 5.1618, 1.2298, 0.8975, 0.031, 1.6474, 0.1367,
     1.0461, 2.1072, 0.0793, 0.3246, 1.587, 0.2272, 2.8755]
DECAY = -0.5
FACTOR = 19 / 81  # makes R(S, S) == 0.9

AGAIN, HARD, GOOD, EASY = 1, 2, 3, 4


def _clamp_d(d: float) -> float:
    return min(10.0, max(1.0, d))


def retrievability(elapsed_days: float, stability: float) -> float:
    return (1 + FACTOR * elapsed_days / stability) ** DECAY


def next_interval(stability: float, retention: float = 0.9, max_days: int = 365) -> int:
    ivl = stability / FACTOR * (retention ** (1 / DECAY) - 1)
    return int(min(max_days, max(1, round(ivl))))


def init_stability(g: int) -> float:
    return max(W[g - 1], 0.1)


def init_difficulty(g: int) -> float:
    return _clamp_d(W[4] - (g - 3) * W[5])


def next_difficulty(d: float, g: int) -> float:
    d2 = d - W[6] * (g - 3)
    return _clamp_d(W[7] * init_difficulty(GOOD) + (1 - W[7]) * d2)


def recall_stability(d: float, s: float, r: float, g: int) -> float:
    hard = W[15] if g == HARD else 1.0
    easy = W[16] if g == EASY else 1.0
    return s * (1 + math.exp(W[8]) * (11 - d) * s ** (-W[9]) * (math.exp(W[10] * (1 - r)) - 1) * hard * easy)


def forget_stability(d: float, s: float, r: float) -> float:
    return min(s, W[11] * d ** (-W[12]) * ((s + 1) ** W[13] - 1) * math.exp(W[14] * (1 - r)))


def review(card: dict | None, g: int, on: _dt.date, retention: float = 0.9, max_days: int = 365) -> dict:
    """Return the new card state after grading it `g` on date `on`. `card` None = first review."""
    if g not in (AGAIN, HARD, GOOD, EASY):
        raise ValueError("grade must be 1..4")
    card = dict(card or {})
    if not card.get("s"):
        s, d = init_stability(g), init_difficulty(g)
        card.update(reps=0, lapses=0)
        r = 1.0
    else:
        elapsed = max(0, (on - _dt.date.fromisoformat(card["last"])).days)
        r = retrievability(elapsed, card["s"])
        d0 = card["d"]
        s = forget_stability(d0, card["s"], r) if g == AGAIN else recall_stability(d0, card["s"], r, g)
        d = next_difficulty(d0, g)
        if g == AGAIN:
            card["lapses"] = card.get("lapses", 0) + 1
    ivl = 1 if g == AGAIN else next_interval(s, retention, max_days)
    card.update(s=round(s, 4), d=round(d, 4), last=on.isoformat(), due=(on + _dt.timedelta(days=ivl)).isoformat(),
                reps=card.get("reps", 0) + 1, last_grade=g, r_at_review=round(r, 4))
    return card


def grade_from(score: float, confidence: int | None) -> int:
    """Map a quiz result to an FSRS grade. Confidence: 1 guess, 2 unsure, 3 sure."""
    if score < 0.5:
        return AGAIN
    if score < 0.8:
        return HARD
    return {1: HARD, 2: GOOD, 3: EASY}.get(confidence or 2, GOOD)
