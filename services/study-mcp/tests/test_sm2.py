import pytest

from sm2 import schedule


def test_wrong_answer_resets_interval_to_one():
    out = schedule(ease_factor=2.5, interval_days=15, repetitions=3, quality=1)
    assert out["interval_days"] == 1
    assert out["repetitions"] == 0


def test_first_correct_sets_interval_to_one():
    out = schedule(ease_factor=2.5, interval_days=0, repetitions=0, quality=5)
    assert out["interval_days"] == 1
    assert out["repetitions"] == 1


def test_second_correct_sets_interval_to_six():
    out = schedule(ease_factor=2.5, interval_days=1, repetitions=1, quality=5)
    assert out["interval_days"] == 6
    assert out["repetitions"] == 2


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
