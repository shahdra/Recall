"""The SM-2 spaced-repetition algorithm.

Pure scheduling arithmetic: no I/O, no dates, no LLM. The Grader sub-agent
decides *how well* the learner recalled a card (a quality grade 0-5); this
module decides *when* the card comes back. Keeping the two separate is what
makes the schedule deterministic and exhaustively testable.

Callers translate the returned ``interval_days`` into a concrete due date.
"""

MIN_EASE_FACTOR = 1.3
"""SM-2's floor on ease. Without it, repeatedly-missed cards would collapse to
an ever-shrinking interval and nag forever."""

PASSING_GRADE = 3
"""Grades 0-2 count as a lapse and reset the repetition streak; 3-5 pass."""

# Grade-dependent early intervals, keyed by the grade just earned.
#
# SM-2 publishes a flat 1 -> 6 ramp regardless of grade, which makes a perfect 5
# and a barely-passing 3 indistinguishable where the learner actually looks: both
# say "next review in 1 day". The ease factor does record the difference, but it
# does not reach the interval until the third review, so grading reads as inert.
#
# Rewarding 4 and 5 with a longer first gap trades away some of SM-2's caution
# about first-recall evidence — a card graded 5 minutes after reading the
# material may still be in working memory — for a schedule the learner can see
# responding to how well they answered. Grade 3 deliberately keeps SM-2's exact
# 1 and 6, so "barely passing" remains the conservative path and the reward for
# 4 and 5 is visible against it.
FIRST_INTERVALS = {3: 1, 4: 2, 5: 4}
"""Days until the first review after one successful recall."""

SECOND_INTERVALS = {3: 6, 4: 7, 5: 9}
"""Days until the second review after two consecutive successful recalls."""

_SM2_FIRST = 1
_SM2_SECOND = 6
"""SM-2's own ramp, used as the fallback for a grade outside the tables."""


def schedule(
    ease_factor: float,
    interval_days: int,
    repetitions: int,
    quality: int,
) -> dict:
    """Advance one card's schedule by a single review.

    Args:
        ease_factor: How easy the card has proven so far. Starts at 2.5 and
            drifts down as the learner misses it.
        interval_days: Days waited before this review.
        repetitions: Consecutive successful reviews before this one.
        quality: How well the learner recalled it — 0 (blank) to 5 (perfect).

    Returns:
        The card's new ``ease_factor``, ``interval_days``, and ``repetitions``.

    A lapse (quality < 3) resets the streak and resurfaces the card tomorrow.
    A pass sets the first two intervals from the grade earned (see
    FIRST_INTERVALS and SECOND_INTERVALS), then grows as interval * ease: a card
    answered 5 every time runs 4 -> 9 -> 24 -> 67 -> 194 days, while one always
    answered 3 runs 1 -> 6 -> 13 -> 27 -> 52.
    """
    if quality < PASSING_GRADE:
        repetitions = 0
        interval_days = 1
    else:
        # .get with an SM-2 fallback rather than [quality]: callers clamp the
        # grade to 0-5, but a KeyError here would turn a bad grade into a failed
        # review instead of a conservatively-scheduled one.
        if repetitions == 0:
            interval_days = FIRST_INTERVALS.get(quality, _SM2_FIRST)
        elif repetitions == 1:
            interval_days = SECOND_INTERVALS.get(quality, _SM2_SECOND)
        else:
            interval_days = round(interval_days * ease_factor)
        repetitions += 1

    # SM-2's ease update: perfect recall nudges ease up, a poor grade pulls it
    # down sharply. The (5 - quality) term makes the penalty quadratic.
    ease_factor = ease_factor + (
        0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)
    )
    ease_factor = max(MIN_EASE_FACTOR, ease_factor)

    return {
        "ease_factor": ease_factor,
        "interval_days": interval_days,
        "repetitions": repetitions,
    }
