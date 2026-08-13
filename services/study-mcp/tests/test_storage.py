import pytest

import storage


def test_put_and_get_card_initializes_sm2_state(tables):
    storage.put_card(
        tables, "d1", "c1", "front", "back", "bio", due_date="2026-01-01", user_id="u1"
    )
    card = storage.get_card(tables, "d1", "c1")
    assert card["ease_factor"] == pytest.approx(2.5)
    assert card["repetitions"] == 0
    assert card["interval_days"] == 0
    assert card["front"] == "front"
    assert card["history"] == []


def test_get_card_missing_raises_keyerror(tables):
    with pytest.raises(KeyError):
        storage.get_card(tables, "nope", "nope")


def test_query_due_cards_returns_only_due(tables):
    storage.put_card(
        tables, "d1", "c1", "f", "b", "bio", due_date="2026-01-01", user_id="u1"
    )
    storage.put_card(
        tables, "d1", "c2", "f", "b", "bio", due_date="2099-01-01", user_id="u1"
    )
    due = storage.query_due_cards(tables, "u1", today_iso="2026-06-01")
    ids = {c["card_id"] for c in due}
    assert ids == {"c1"}


def test_query_due_cards_scopes_to_user(tables):
    storage.put_card(
        tables, "d1", "c1", "f", "b", "bio", due_date="2026-01-01", user_id="u1"
    )
    storage.put_card(
        tables, "d2", "c2", "f", "b", "bio", due_date="2026-01-01", user_id="u2"
    )
    due = storage.query_due_cards(tables, "u1", today_iso="2026-06-01")
    assert {c["card_id"] for c in due} == {"c1"}


def test_update_card_schedule_appends_history_on_fresh_card(tables):
    """A card straight out of put_card has an empty history; append must not fail."""
    storage.put_card(
        tables, "d1", "c1", "f", "b", "bio", due_date="2026-01-01", user_id="u1"
    )
    storage.update_card_schedule(
        tables,
        "d1",
        "c1",
        ease_factor=2.6,
        interval_days=6,
        repetitions=2,
        due_date="2026-06-07",
        history_entry={"ts": "2026-06-01", "grade": 5, "was_correct": True},
    )
    card = storage.get_card(tables, "d1", "c1")
    assert card["ease_factor"] == pytest.approx(2.6)
    assert card["interval_days"] == 6
    assert card["due_date"] == "2026-06-07"
    assert card["last_reviewed"] == "2026-06-01"
    assert len(card["history"]) == 1
    assert card["history"][0]["grade"] == 5


def test_update_card_schedule_accumulates_history(tables):
    storage.put_card(
        tables, "d1", "c1", "f", "b", "bio", due_date="2026-01-01", user_id="u1"
    )
    for day, grade in (("2026-06-01", 5), ("2026-06-07", 2)):
        storage.update_card_schedule(
            tables,
            "d1",
            "c1",
            ease_factor=2.5,
            interval_days=1,
            repetitions=0,
            due_date=day,
            history_entry={"ts": day, "grade": grade, "was_correct": grade >= 3},
        )
    card = storage.get_card(tables, "d1", "c1")
    assert [h["grade"] for h in card["history"]] == [5, 2]


def test_put_and_list_decks(tables):
    storage.put_deck(
        tables,
        "u1",
        "d1",
        "Biology",
        source_s3_key="uploads/u1/x.pdf",
        card_count=3,
        created_at="2026-06-01",
    )
    decks = storage.list_decks(tables, "u1")
    assert len(decks) == 1
    assert decks[0]["title"] == "Biology"
    assert decks[0]["card_count"] == 3


def test_get_profile_returns_empty_default_when_absent(tables):
    profile = storage.get_profile(tables, "brand-new-user")
    assert profile["user_id"] == "brand-new-user"
    assert profile["weak_topics"] == {}
    assert profile["stats"] == {}


def test_put_then_get_profile_round_trips(tables):
    storage.put_profile(
        tables,
        "u1",
        {
            "weak_topics": {"mitosis": 0.6},
            "preferences": {"tone": "encouraging"},
            "stats": {"total_reviews": 10},
            "notes": "confuses mitosis and meiosis",
        },
    )
    profile = storage.get_profile(tables, "u1")
    assert profile["weak_topics"]["mitosis"] == pytest.approx(0.6)
    assert profile["notes"] == "confuses mitosis and meiosis"


def test_query_cards_by_deck(tables):
    storage.put_card(
        tables, "d1", "c1", "f", "b", "bio", due_date="2026-01-01", user_id="u1"
    )
    storage.put_card(
        tables, "d1", "c2", "f", "b", "bio", due_date="2026-01-01", user_id="u1"
    )
    storage.put_card(
        tables, "d2", "c3", "f", "b", "bio", due_date="2026-01-01", user_id="u1"
    )
    cards = storage.query_cards_by_deck(tables, "d1")
    assert {c["card_id"] for c in cards} == {"c1", "c2"}
