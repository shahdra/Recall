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
    A pass grows the interval 1 -> 6 -> interval * ease, so a well-known card
    fades to roughly 15, 37, then 90 days.
    """
    if quality < PASSING_GRADE:
        repetitions = 0
        interval_days = 1
    else:
        if repetitions == 0:
            interval_days = 1
        elif repetitions == 1:
            interval_days = 6
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
