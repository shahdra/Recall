"""Shared fixtures: moto-mocked DynamoDB tables mirroring the Terraform schema.

The key schemas here must stay in step with infra/terraform/dynamodb.tf. If they
drift, these tests pass while production breaks. They are duplicated from
services/study-mcp/tests/conftest.py because the two services share no code — see
the module docstring in reminder.py for why.
"""

import boto3
import pytest
from moto import mock_aws

REGION = "us-east-1"


@pytest.fixture
def ddb(monkeypatch):
    """A mocked DynamoDB resource with Recall's Cards and LearnerProfile tables.

    Decks is not created: the reminder never reads it.
    """
    # Credentials must be set before any boto3 client is built, or moto's
    # interception can fall through to a real credential lookup.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)

    with mock_aws():
        resource = boto3.resource("dynamodb", region_name=REGION)

        resource.create_table(
            TableName="Cards",
            KeySchema=[
                {"AttributeName": "deck_id", "KeyType": "HASH"},
                {"AttributeName": "card_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "deck_id", "AttributeType": "S"},
                {"AttributeName": "card_id", "AttributeType": "S"},
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "due_date", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "due-index",
                    "KeySchema": [
                        {"AttributeName": "user_id", "KeyType": "HASH"},
                        {"AttributeName": "due_date", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        resource.create_table(
            TableName="LearnerProfile",
            KeySchema=[{"AttributeName": "user_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "user_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        yield resource


@pytest.fixture
def add_learner(ddb):
    """Register a learner in LearnerProfile."""

    def _add(user_id: str):
        ddb.Table("LearnerProfile").put_item(Item={"user_id": user_id})

    return _add


@pytest.fixture
def add_card(ddb):
    """Put a card with a given owner and due date."""
    counter = {"n": 0}

    def _add(user_id: str, due_date: str, deck_id: str = "deck-1"):
        counter["n"] += 1
        ddb.Table("Cards").put_item(
            Item={
                "deck_id": deck_id,
                "card_id": f"card-{counter['n']}",
                "user_id": user_id,
                "due_date": due_date,
                "front": "q",
                "back": "a",
            }
        )

    return _add
