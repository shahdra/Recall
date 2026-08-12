"""Shared fixtures: moto-mocked DynamoDB tables mirroring the Terraform schema.

The key schemas here must stay in step with infra/terraform/dynamodb.tf. If they
drift, these tests pass while production breaks.
"""

import boto3
import pytest
from moto import mock_aws

import storage

REGION = "us-east-1"


@pytest.fixture
def tables():
    """Create the three Recall tables in a mocked DynamoDB and yield a Tables handle."""
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name=REGION)

        ddb.create_table(
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

        ddb.create_table(
            TableName="Decks",
            KeySchema=[
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "deck_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "deck_id", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        ddb.create_table(
            TableName="LearnerProfile",
            KeySchema=[{"AttributeName": "user_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "user_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        yield storage.Tables(
            resource=ddb,
            cards="Cards",
            decks="Decks",
            profiles="LearnerProfile",
        )
