"""DynamoDB persistence for decks, cards, and learner profiles.

Every function takes a ``Tables`` handle rather than reaching for a module-level
client, so tests can inject moto-mocked resources without patching boto3.

DynamoDB stores non-integer numbers as ``Decimal``; floats are converted on the
way in and back to ``float`` on the way out so callers never handle Decimal.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from boto3.dynamodb.conditions import Key

INITIAL_EASE_FACTOR = "2.5"
"""SM-2's starting ease. A string so Decimal parses it exactly, not via float."""


@dataclass
class Tables:
    """A boto3 DynamoDB resource plus the three table names Recall uses."""

    resource: Any
    cards: str = "Cards"
    decks: str = "Decks"
    profiles: str = "LearnerProfile"


def _t(tables: Tables, name: str):
    return tables.resource.Table(name)


def _to_float(value: Any) -> Any:
    return float(value) if isinstance(value, Decimal) else value


# --- Cards -------------------------------------------------------------------


def put_card(
    tables: Tables,
    deck_id: str,
    card_id: str,
    front: str,
    back: str,
    topic: str,
    due_date: str,
    user_id: str,
) -> None:
    """Write a new card with fresh SM-2 state, due immediately.

    ``last_reviewed`` is deliberately absent rather than null — it appears as a
    string the first time the card is graded, so the attribute never changes type.
    """
    _t(tables, tables.cards).put_item(
        Item={
            "deck_id": deck_id,
            "card_id": card_id,
            "user_id": user_id,
            "front": front,
            "back": back,
            "topic": topic,
            "ease_factor": Decimal(INITIAL_EASE_FACTOR),
            "interval_days": 0,
            "repetitions": 0,
            "due_date": due_date,
            "history": [],
        }
    )


def get_card(tables: Tables, deck_id: str, card_id: str) -> dict:
    """Fetch one card, with numerics normalized to Python floats/ints.

    Raises:
        KeyError: if no such card exists.
    """
    item = (
        _t(tables, tables.cards)
        .get_item(Key={"deck_id": deck_id, "card_id": card_id})
        .get("Item")
    )
    if item is None:
        raise KeyError(f"card {deck_id}/{card_id} not found")
    return _normalize_card(item)


def _normalize_card(item: dict) -> dict:
    item["ease_factor"] = float(item["ease_factor"])
    item["interval_days"] = int(item["interval_days"])
    item["repetitions"] = int(item["repetitions"])
    for entry in item.get("history", []):
        if "grade" in entry:
            entry["grade"] = int(entry["grade"])
    return item


def update_card_schedule(
    tables: Tables,
    deck_id: str,
    card_id: str,
    ease_factor: float,
    interval_days: int,
    repetitions: int,
    due_date: str,
    history_entry: dict,
) -> None:
    """Persist a post-review schedule and append the review to the card's history.

    ``if_not_exists`` guards the append so a card written by any path — not just
    ``put_card`` — can be graded without a ValidationException.
    """
    _t(tables, tables.cards).update_item(
        Key={"deck_id": deck_id, "card_id": card_id},
        UpdateExpression=(
            "SET ease_factor = :e, interval_days = :i, repetitions = :r, "
            "due_date = :d, last_reviewed = :l, "
            "history = list_append(if_not_exists(history, :empty), :h)"
        ),
        ExpressionAttributeValues={
            ":e": Decimal(str(ease_factor)),
            ":i": interval_days,
            ":r": repetitions,
            ":d": due_date,
            ":l": history_entry["ts"],
            ":h": [history_entry],
            ":empty": [],
        },
    )


def query_due_cards(tables: Tables, user_id: str, today_iso: str) -> list[dict]:
    """Cards due on or before ``today_iso`` for one learner, via the due-date GSI."""
    response = _t(tables, tables.cards).query(
        IndexName="due-index",
        KeyConditionExpression=Key("user_id").eq(user_id)
        & Key("due_date").lte(today_iso),
    )
    return [_normalize_card(item) for item in response.get("Items", [])]


def query_cards_by_deck(tables: Tables, deck_id: str) -> list[dict]:
    """Every card in one deck, regardless of due date."""
    response = _t(tables, tables.cards).query(
        KeyConditionExpression=Key("deck_id").eq(deck_id)
    )
    return [_normalize_card(item) for item in response.get("Items", [])]


# --- Decks -------------------------------------------------------------------


def put_deck(
    tables: Tables,
    user_id: str,
    deck_id: str,
    title: str,
    source_s3_key: str | None,
    card_count: int,
    created_at: str,
) -> None:
    """Write a deck's metadata. The material itself lives in S3, not here."""
    _t(tables, tables.decks).put_item(
        Item={
            "user_id": user_id,
            "deck_id": deck_id,
            "title": title,
            "source_s3_key": source_s3_key,
            "card_count": card_count,
            "created_at": created_at,
        }
    )


def list_decks(tables: Tables, user_id: str) -> list[dict]:
    """All of one learner's decks."""
    response = _t(tables, tables.decks).query(
        KeyConditionExpression=Key("user_id").eq(user_id)
    )
    return response.get("Items", [])


def set_deck_card_count(tables: Tables, user_id: str, deck_id: str, count: int) -> None:
    """Record how many cards a deck ended up with after generation."""
    _t(tables, tables.decks).update_item(
        Key={"user_id": user_id, "deck_id": deck_id},
        UpdateExpression="SET card_count = :c",
        ExpressionAttributeValues={":c": count},
    )


# --- Learner profile (long-term memory) --------------------------------------


def get_profile(tables: Tables, user_id: str) -> dict:
    """One learner's profile, or an empty scaffold if they have none yet.

    Returning a default rather than raising means a first-time learner needs no
    special-casing at the call site.
    """
    item = (
        _t(tables, tables.profiles).get_item(Key={"user_id": user_id}).get("Item")
    )
    if item is None:
        return {
            "user_id": user_id,
            "weak_topics": {},
            "preferences": {},
            "stats": {},
            "notes": "",
        }
    item["weak_topics"] = {
        topic: _to_float(rate) for topic, rate in item.get("weak_topics", {}).items()
    }
    item["stats"] = {k: _to_float(v) for k, v in item.get("stats", {}).items()}
    return item


def put_profile(tables: Tables, user_id: str, profile: dict) -> None:
    """Overwrite a learner's profile, converting floats for DynamoDB."""
    profile = dict(profile)
    profile["user_id"] = user_id
    profile["weak_topics"] = {
        topic: Decimal(str(rate))
        for topic, rate in profile.get("weak_topics", {}).items()
    }
    profile["stats"] = {
        key: Decimal(str(value)) if isinstance(value, float) else value
        for key, value in profile.get("stats", {}).items()
    }
    _t(tables, tables.profiles).put_item(Item=profile)
