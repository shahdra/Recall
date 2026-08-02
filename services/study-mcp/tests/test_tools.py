"""Tests for the MCP tool logic.

These call the ``_``-prefixed logic functions directly, so they exercise the
storage + SM-2 wiring without going through the MCP transport. The real
transport is covered by the Phase 4 integration suite.
"""

import pytest

import app


@pytest.fixture(autouse=True)
def wired(tables, monkeypatch):
    """Point the module at moto-backed tables and freeze 'today'."""
    monkeypatch.setattr(app, "TABLES", tables)
    monkeypatch.setattr(app, "_today_iso", lambda: "2026-06-01")
    return tables


def test_create_deck_returns_id():
    out = app._create_deck("u1", "Bio", None)
    assert out["deck_id"]


def test_add_card_returns_id_and_is_due_today():
    app._create_deck("u1", "Bio", None, deck_id="d1")
    out = app._add_card("d1", "u1", "Q", "A", "bio", card_id="c1")
    assert out["card_id"] == "c1"
    due = app._get_due_cards("u1")
    assert {c["card_id"] for c in due["cards"]} == {"c1"}


def test_grade_card_wrong_answer_due_tomorrow():
    app._create_deck("u1", "Bio", None, deck_id="d1")
    app._add_card("d1", "u1", "Q", "A", "bio", card_id="c1")
    out = app._grade_card("d1", "c1", quality=1)
    assert out["due_date"] == "2026-06-02"  # +1 day
    assert out["interval_days"] == 1


def test_grade_card_correct_grows_interval():
    app._create_deck("u1", "Bio", None, deck_id="d1")
    app._add_card("d1", "u1", "Q", "A", "bio", card_id="c1")
    out = app._grade_card("d1", "c1", quality=5)
    assert out["interval_days"] == 1
    assert out["due_date"] == "2026-06-02"


def test_grade_card_second_correct_jumps_to_six_days():
    app._create_deck("u1", "Bio", None, deck_id="d1")
    app._add_card("d1", "u1", "Q", "A", "bio", card_id="c1")
    app._grade_card("d1", "c1", quality=5)
    out = app._grade_card("d1", "c1", quality=5)
    assert out["interval_days"] == 6
    assert out["due_date"] == "2026-06-07"


def test_grade_card_clamps_out_of_range_quality():
    """A quality outside 0-5 must not corrupt the schedule."""
    app._create_deck("u1", "Bio", None, deck_id="d1")
    app._add_card("d1", "u1", "Q", "A", "bio", card_id="c1")
    out = app._grade_card("d1", "c1", quality=99)
    assert out["interval_days"] >= 1


def test_grade_card_missing_card_raises():
    with pytest.raises(KeyError):
        app._grade_card("nope", "nope", quality=5)


def test_graded_card_leaves_the_due_queue():
    app._create_deck("u1", "Bio", None, deck_id="d1")
    app._add_card("d1", "u1", "Q", "A", "bio", card_id="c1")
    app._grade_card("d1", "c1", quality=5)
    assert app._get_due_cards("u1")["cards"] == []


def test_list_decks_returns_created_decks():
    app._create_deck("u1", "Bio", None, deck_id="d1")
    app._create_deck("u1", "Chem", None, deck_id="d2")
    out = app._list_decks("u1")
    assert {d["deck_id"] for d in out["decks"]} == {"d1", "d2"}


def test_get_progress_computes_accuracy_from_history():
    app._create_deck("u1", "Bio", None, deck_id="d1")
    app._add_card("d1", "u1", "Q1", "A1", "bio", card_id="c1")
    app._add_card("d1", "u1", "Q2", "A2", "chem", card_id="c2")
    app._grade_card("d1", "c1", quality=5)  # correct
    app._grade_card("d1", "c2", quality=1)  # wrong
    out = app._get_progress("u1")
    assert out["total_reviews"] == 2
    assert out["accuracy"] == pytest.approx(0.5)


def test_get_progress_flags_weak_topics():
    app._create_deck("u1", "Bio", None, deck_id="d1")
    app._add_card("d1", "u1", "Q", "A", "mitosis", card_id="c1")
    app._grade_card("d1", "c1", quality=0)
    out = app._get_progress("u1")
    assert out["weak_topics"]["mitosis"] == pytest.approx(1.0)


def test_get_progress_empty_for_new_user():
    out = app._get_progress("nobody")
    assert out["total_reviews"] == 0
    assert out["accuracy"] == 0.0
    assert out["weak_topics"] == {}


def test_update_profile_persists_notes_and_weak_topics():
    app._update_profile("u1", notes="mixes up mitosis", weak_topics={"mitosis": 0.7})
    out = app._get_profile("u1")
    assert out["notes"] == "mixes up mitosis"
    assert out["weak_topics"]["mitosis"] == pytest.approx(0.7)


def test_get_profile_default_for_new_user():
    out = app._get_profile("brand-new")
    assert out["user_id"] == "brand-new"
    assert out["weak_topics"] == {}


def test_mcp_exposes_expected_tools():
    """The agent discovers these names over MCP; renaming one breaks the agent."""
    import asyncio

    names = {t.name for t in asyncio.run(app.mcp.list_tools())}
    assert {
        "create_deck",
        "add_card",
        "get_due_cards",
        "grade_card",
        "get_progress",
        "list_decks",
        "get_profile",
        "update_profile",
    } <= names
