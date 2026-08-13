"""Tests for the daily reminder digest.

The interesting cases are the boundary (a card due exactly today counts, tomorrow's
does not), the no-publish path when nothing is due, and the aggregate across several
learners — the three things the CronJob's usefulness rests on.
"""

from datetime import date, timedelta

import boto3
import pytest
from moto import mock_aws

import reminder

TOPIC = "arn:aws:sns:us-east-1:123456789012:recall-reminders"


def _iso(offset_days: int) -> str:
    return (date.today() + timedelta(days=offset_days)).isoformat()


@pytest.fixture
def sns_spy(monkeypatch):
    """Capture publish() calls instead of hitting SNS.

    A stub rather than moto's SNS mock: what matters is the exact message text and
    whether publish was called at all, and a spy asserts both directly.
    """
    calls = []

    class _Client:
        def publish(self, **kwargs):
            calls.append(kwargs)
            return {"MessageId": "stub"}

    monkeypatch.setattr(boto3, "client", lambda *a, **k: _Client())
    monkeypatch.setattr(reminder, "SNS_TOPIC_ARN", TOPIC)
    return calls


# --- list_learner_ids ------------------------------------------------------


def test_lists_every_learner(ddb, add_learner):
    add_learner("u1")
    add_learner("u2")

    assert sorted(reminder.list_learner_ids(ddb)) == ["u1", "u2"]


def test_no_learners_yields_empty_list(ddb):
    assert reminder.list_learner_ids(ddb) == []


# --- count_due_cards ------------------------------------------------------


def test_counts_cards_due_today_and_earlier(ddb, add_card):
    add_card("u1", _iso(-3))
    add_card("u1", _iso(0))

    assert reminder.count_due_cards(ddb, "u1", _iso(0)) == 2


def test_excludes_cards_due_in_the_future(ddb, add_card):
    add_card("u1", _iso(0))
    add_card("u1", _iso(1))
    add_card("u1", _iso(30))

    # The boundary is inclusive of today and exclusive of tomorrow — the whole
    # point of an .lte() on a lexicographically sorted ISO date.
    assert reminder.count_due_cards(ddb, "u1", _iso(0)) == 1


def test_counts_are_per_learner(ddb, add_card):
    add_card("u1", _iso(0))
    add_card("u2", _iso(0))
    add_card("u2", _iso(-1))

    assert reminder.count_due_cards(ddb, "u1", _iso(0)) == 1
    assert reminder.count_due_cards(ddb, "u2", _iso(0)) == 2


def test_counts_across_decks(ddb, add_card):
    # The GSI partitions on user_id, so a learner's cards from different decks
    # must still come back in one query.
    add_card("u1", _iso(0), deck_id="deck-a")
    add_card("u1", _iso(0), deck_id="deck-b")

    assert reminder.count_due_cards(ddb, "u1", _iso(0)) == 2


# --- build_message --------------------------------------------------------


def test_message_pluralises_correctly():
    assert "1 card due" in reminder.build_message(1, 1, "2026-08-13")
    assert "1 learner " in reminder.build_message(1, 1, "2026-08-13")
    assert "3 cards due" in reminder.build_message(3, 2, "2026-08-13")
    assert "2 learners" in reminder.build_message(3, 2, "2026-08-13")


def test_message_includes_the_date():
    assert "2026-08-13" in reminder.build_message(3, 2, "2026-08-13")


# --- main -----------------------------------------------------------------


def test_publishes_digest_spanning_several_learners(
    ddb, add_learner, add_card, sns_spy
):
    add_learner("u1")
    add_learner("u2")
    add_card("u1", _iso(0))
    add_card("u2", _iso(-1))
    add_card("u2", _iso(0))

    assert reminder.main() == 0

    assert len(sns_spy) == 1
    assert sns_spy[0]["TopicArn"] == TOPIC
    assert "3 cards due today across 2 learners" in sns_spy[0]["Message"]


def test_does_not_publish_when_nothing_is_due(ddb, add_learner, add_card, sns_spy):
    add_learner("u1")
    add_card("u1", _iso(5))

    assert reminder.main() == 0
    # An empty digest every morning is noise, and noise is what makes the one
    # message that matters get ignored.
    assert sns_spy == []


def test_does_not_publish_when_there_are_no_learners(ddb, sns_spy):
    assert reminder.main() == 0
    assert sns_spy == []


def test_counts_only_learners_with_cards_due(ddb, add_learner, add_card, sns_spy):
    add_learner("u1")
    add_learner("u2")  # registered but has nothing due
    add_card("u1", _iso(0))

    assert reminder.main() == 0
    # "across 1 learner", not 2 — a learner with nothing due is not part of the
    # count the digest reports.
    assert "1 card due today across 1 learner" in sns_spy[0]["Message"]


def test_missing_topic_arn_is_a_failure(ddb, monkeypatch):
    monkeypatch.setattr(reminder, "SNS_TOPIC_ARN", "")

    # Non-zero so the CronJob records a failure: an unset topic is a broken
    # deployment, and reporting success would hide it forever.
    assert reminder.main() == 1


def test_scan_pagination_is_followed(ddb, add_learner, monkeypatch):
    """A learner on the second Scan page must still be counted.

    Guards the pagination loop: a Scan returns at most 1MB, and reading only the
    first page would silently under-report as soon as the table outgrew it.
    """
    add_learner("u1")
    add_learner("u2")

    table = ddb.Table("LearnerProfile")
    real_scan = table.scan
    calls = {"n": 0}

    def fake_scan(**kwargs):
        # Limit=1 forces one item per page, so a correct loop must call twice.
        calls["n"] += 1
        return real_scan(**{**kwargs, "Limit": 1})

    monkeypatch.setattr(table, "scan", fake_scan)
    # Hand the code under test this same patched Table object.
    monkeypatch.setattr(ddb, "Table", lambda name: table)

    assert sorted(reminder.list_learner_ids(ddb)) == ["u1", "u2"]
    assert calls["n"] == 2
