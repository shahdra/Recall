"""Tests for the demo-only simulated clock.

The clock exists so a demo can show the SM-2 schedule playing out without
waiting days for it. What makes that honest rather than a trick is that cards
become due through the *same* query the real clock drives — so the tests that
matter most here are the ones asserting a card is not due before its scheduled
date and is due on it.

Unlike test_tools.py, these do not freeze ``_today_iso``: the offset lives inside
that function, so replacing it would bypass exactly what is under test.
"""

from datetime import date, timedelta

import pytest

import app


@pytest.fixture(autouse=True)
def demo_clock(tables, monkeypatch):
    """Point at moto tables, force demo mode on, and start from a zero offset.

    The offset is module-level mutable state, so it is reset around every test —
    a leaked offset would make unrelated tests depend on execution order.
    """
    monkeypatch.setattr(app, "TABLES", tables)
    monkeypatch.setattr(app, "DEMO_MODE", True)
    monkeypatch.setattr(app, "_clock_offset_days", 0)
    return tables


def _iso(days_ahead: int) -> str:
    return (date.today() + timedelta(days=days_ahead)).isoformat()


def test_clock_starts_at_the_real_date():
    assert app._today_iso() == _iso(0)
    state = app._clock_state()
    assert state["offset_days"] == 0
    assert state["simulated_date"] == state["real_date"]


def test_advance_moves_the_simulated_date():
    out = app._advance_clock(1)
    assert out["offset_days"] == 1
    assert out["simulated_date"] == _iso(1)
    assert app._today_iso() == _iso(1)


def test_advances_accumulate():
    """Clicking the button repeatedly must keep moving forward, not re-set to +1."""
    app._advance_clock(1)
    app._advance_clock(1)
    out = app._advance_clock(2)
    assert out["offset_days"] == 4
    assert app._today_iso() == _iso(4)


def test_reset_returns_to_the_real_date():
    app._advance_clock(30)
    out = app._reset_clock()
    assert out["offset_days"] == 0
    assert out["simulated_date"] == _iso(0)


def test_card_becomes_due_exactly_when_scheduled():
    """The point of the whole feature: advancing time surfaces due cards.

    A perfect grade schedules the card 4 days out (sm2.FIRST_INTERVALS), so it
    must be absent from the queue at +3 and present at +4. Asserted through
    _get_due_cards, the same call the study session makes.
    """
    app._create_deck("u1", "Bio", None, deck_id="d1")
    app._add_card("d1", "u1", "Q", "A", "bio", card_id="c1")

    graded = app._grade_card("d1", "c1", quality=5)
    assert graded["interval_days"] == 4

    assert app._get_due_cards("u1")["cards"] == []  # graded today, not due today

    app._advance_clock(3)
    assert app._get_due_cards("u1")["cards"] == [], "due 3 days early"

    app._advance_clock(1)
    due = app._get_due_cards("u1")["cards"]
    assert [c["card_id"] for c in due] == ["c1"], "not due on its scheduled date"


def test_grading_while_advanced_schedules_from_the_simulated_date():
    """Chained demo reviews must build on simulated time, not real time."""
    app._create_deck("u1", "Bio", None, deck_id="d1")
    app._add_card("d1", "u1", "Q", "A", "bio", card_id="c1")
    app._advance_clock(10)

    graded = app._grade_card("d1", "c1", quality=5)
    assert graded["due_date"] == _iso(14)  # 10 simulated + a 4-day interval


def test_advance_rejects_zero_and_negative_days():
    """Rewinding is reset_clock's job; a negative offset would un-due real cards."""
    for days in (0, -1, -100):
        out = app._advance_clock(days)
        assert "error" in out
        assert out["offset_days"] == 0


def test_advance_rejects_an_absurd_jump():
    out = app._advance_clock(app.MAX_CLOCK_ADVANCE_DAYS + 1)
    assert "error" in out
    assert out["offset_days"] == 0


def test_advance_allows_the_maximum():
    out = app._advance_clock(app.MAX_CLOCK_ADVANCE_DAYS)
    assert "error" not in out
    assert out["offset_days"] == app.MAX_CLOCK_ADVANCE_DAYS


class TestDemoModeDisabled:
    """With the flag off the clock must be inert, not merely hidden in the UI."""

    @pytest.fixture(autouse=True)
    def _off(self, monkeypatch):
        monkeypatch.setattr(app, "DEMO_MODE", False)

    def test_advance_refuses_and_does_not_move(self):
        out = app._advance_clock(5)
        assert out["error"] == "demo mode is disabled"
        assert out["offset_days"] == 0
        assert app._today_iso() == _iso(0)

    def test_reset_refuses(self):
        assert app._reset_clock()["error"] == "demo mode is disabled"

    def test_clock_state_reports_disabled(self):
        assert app._clock_state()["demo_mode"] is False
