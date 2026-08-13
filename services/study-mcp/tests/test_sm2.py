import pytest

from sm2 import PASSING_GRADE, schedule

PASSING_GRADES = (3, 4, 5)


def test_wrong_answer_resets_interval_to_one():
    out = schedule(ease_factor=2.5, interval_days=15, repetitions=3, quality=1)
    assert out["interval_days"] == 1
    assert out["repetitions"] == 0


@pytest.mark.parametrize("quality,expected", [(3, 1), (4, 2), (5, 4)])
def test_first_correct_interval_rewards_the_grade(quality, expected):
    """A better first recall earns a longer first gap, unlike stock SM-2."""
    out = schedule(ease_factor=2.5, interval_days=0, repetitions=0, quality=quality)
    assert out["interval_days"] == expected
    assert out["repetitions"] == 1


@pytest.mark.parametrize("quality,expected", [(3, 6), (4, 7), (5, 9)])
def test_second_correct_interval_rewards_the_grade(quality, expected):
    out = schedule(ease_factor=2.5, interval_days=1, repetitions=1, quality=quality)
    assert out["interval_days"] == expected
    assert out["repetitions"] == 2


def test_barely_passing_keeps_stock_sm2_ramp():
    """Grade 3 is the conservative path: exactly SM-2's own 1 -> 6.

    This is what the rewards for 4 and 5 are visible against, so it is pinned
    separately from the tables above.
    """
    first = schedule(ease_factor=2.5, interval_days=0, repetitions=0, quality=3)
    assert first["interval_days"] == 1
    second = schedule(ease_factor=2.5, interval_days=1, repetitions=1, quality=3)
    assert second["interval_days"] == 6


@pytest.mark.parametrize("repetitions,interval", [(0, 0), (1, 1), (2, 6), (5, 40)])
def test_higher_passing_grade_never_shortens_the_interval(repetitions, interval):
    """Monotonic in the grade at every streak position.

    The property that makes the reward meaningful: answering better must never
    bring a card back sooner. Checked past the early tables too, where the
    interval comes from the ease factor instead.
    """
    intervals = [
        schedule(
            ease_factor=2.5, interval_days=interval, repetitions=repetitions, quality=q
        )["interval_days"]
        for q in PASSING_GRADES
    ]
    assert intervals == sorted(intervals)


def test_third_correct_still_multiplies_by_ease():
    """The grade-dependent tables must not leak past the second review."""
    for quality in PASSING_GRADES:
        out = schedule(
            ease_factor=2.0, interval_days=10, repetitions=2, quality=quality
        )
        assert out["interval_days"] == 20  # round(10 * 2.0), whatever the grade


@pytest.mark.parametrize("quality", [0, 1, 2])
def test_failing_grade_returns_tomorrow_regardless(quality):
    """The pass/fail boundary stays sharp: no failing grade earns a longer gap."""
    assert quality < PASSING_GRADE
    out = schedule(ease_factor=2.5, interval_days=30, repetitions=4, quality=quality)
    assert out["interval_days"] == 1
    assert out["repetitions"] == 0


@pytest.mark.parametrize("repetitions", [0, 1])
def test_out_of_range_grade_falls_back_to_sm2(repetitions):
    """A grade past the tables schedules conservatively rather than raising."""
    out = schedule(ease_factor=2.5, interval_days=1, repetitions=repetitions, quality=9)
    assert out["interval_days"] == (1 if repetitions == 0 else 6)


def test_third_correct_multiplies_by_ease():
    out = schedule(ease_factor=2.5, interval_days=6, repetitions=2, quality=5)
    assert out["interval_days"] == 15  # round(6 * 2.5)
    assert out["repetitions"] == 3


def test_ease_factor_floored_at_1_3():
    out = schedule(ease_factor=1.3, interval_days=1, repetitions=1, quality=0)
    assert out["ease_factor"] >= 1.3


def test_ease_increases_on_perfect_recall():
    out = schedule(ease_factor=2.5, interval_days=6, repetitions=2, quality=5)
    assert out["ease_factor"] > 2.5
