"""Fixtures for the real-MCP integration tests.

These start the actual ``study-mcp`` server as a subprocess and talk to it over
genuine MCP-over-HTTP, backed by DynamoDB Local. Nothing here is mocked except
the LLM, which the round-trip tests never need.

Why a subprocess rather than an in-process ASGI app: the requirement is to
exercise the real MCP transport, and mounting the server in-process would let
``moto``'s patching cover it — which is precisely the coverage we do not want
here. A subprocess cannot be patched, so every call is a real HTTP request
carrying a real MCP handshake.

DynamoDB Local partitions its data by access key, so the tables must be created
with the *same* credentials the subprocess uses. Getting that wrong is silent:
tables appear to exist, then the server reads a different, empty database.
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import boto3
import pytest
import urllib.error
import urllib.request

REGION = "us-east-1"
DDB_ENDPOINT = os.environ.get("RECALL_TEST_DDB_ENDPOINT", "http://localhost:8001")

# DynamoDB Local ignores the secret but namespaces data by access key id.
TEST_ACCESS_KEY = "integration"
TEST_SECRET_KEY = "integration"

STUDY_MCP_DIR = Path(__file__).resolve().parents[3] / "study-mcp"

CARDS_TABLE = "IntegrationCards"
DECKS_TABLE = "IntegrationDecks"
PROFILE_TABLE = "IntegrationLearnerProfile"

SERVER_START_TIMEOUT = 45


def _study_mcp_python() -> str:
    """The interpreter that can import fastmcp.

    Each service has its own venv, and ``sys.executable`` here is the
    tutor-agent's — which has no fastmcp. Prefer study-mcp's venv when present
    (local development) and fall back to the current interpreter, which is
    correct in CI where one environment holds both services' dependencies.
    """
    candidate = STUDY_MCP_DIR / ".venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _ddb_available() -> bool:
    try:
        request = urllib.request.Request(DDB_ENDPOINT, method="GET")
        urllib.request.urlopen(request, timeout=3)
    except urllib.error.HTTPError:
        return True  # responded, just not to a bare GET — that is fine
    except Exception:
        return False
    return True


@pytest.fixture(scope="session")
def ddb_client():
    """A DynamoDB Local client, or skip the whole suite if it is not running."""
    if not _ddb_available():
        pytest.skip(
            f"DynamoDB Local not reachable at {DDB_ENDPOINT}. Start it with:\n"
            "  docker run -d --rm --name recall-ddb -p 8001:8000 amazon/dynamodb-local"
        )
    return boto3.client(
        "dynamodb",
        region_name=REGION,
        endpoint_url=DDB_ENDPOINT,
        aws_access_key_id=TEST_ACCESS_KEY,
        aws_secret_access_key=TEST_SECRET_KEY,
    )


@pytest.fixture(scope="session")
def integration_tables(ddb_client):
    """Create the three tables, mirroring infra/terraform/dynamodb.tf.

    Dropped and recreated per session so a previous run's rows cannot make a
    later assertion pass.
    """
    definitions = [
        {
            "TableName": CARDS_TABLE,
            "KeySchema": [
                {"AttributeName": "deck_id", "KeyType": "HASH"},
                {"AttributeName": "card_id", "KeyType": "RANGE"},
            ],
            "AttributeDefinitions": [
                {"AttributeName": "deck_id", "AttributeType": "S"},
                {"AttributeName": "card_id", "AttributeType": "S"},
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "due_date", "AttributeType": "S"},
            ],
            "GlobalSecondaryIndexes": [
                {
                    "IndexName": "due-index",
                    "KeySchema": [
                        {"AttributeName": "user_id", "KeyType": "HASH"},
                        {"AttributeName": "due_date", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            "BillingMode": "PAY_PER_REQUEST",
        },
        {
            "TableName": DECKS_TABLE,
            "KeySchema": [
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "deck_id", "KeyType": "RANGE"},
            ],
            "AttributeDefinitions": [
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "deck_id", "AttributeType": "S"},
            ],
            "BillingMode": "PAY_PER_REQUEST",
        },
        {
            "TableName": PROFILE_TABLE,
            "KeySchema": [{"AttributeName": "user_id", "KeyType": "HASH"}],
            "AttributeDefinitions": [{"AttributeName": "user_id", "AttributeType": "S"}],
            "BillingMode": "PAY_PER_REQUEST",
        },
    ]

    for definition in definitions:
        name = definition["TableName"]
        try:
            ddb_client.delete_table(TableName=name)
            waiter = ddb_client.get_waiter("table_not_exists")
            waiter.wait(TableName=name, WaiterConfig={"Delay": 1, "MaxAttempts": 20})
        except ddb_client.exceptions.ResourceNotFoundException:
            pass
        ddb_client.create_table(**definition)
        ddb_client.get_waiter("table_exists").wait(
            TableName=name, WaiterConfig={"Delay": 1, "MaxAttempts": 20}
        )

    yield {"cards": CARDS_TABLE, "decks": DECKS_TABLE, "profiles": PROFILE_TABLE}

    for definition in definitions:
        try:
            ddb_client.delete_table(TableName=definition["TableName"])
        except Exception:
            pass


@pytest.fixture(scope="session")
def mcp_server(integration_tables):
    """Run the real study-mcp server as a subprocess; yield its MCP URL."""
    port = _free_port()

    env = {
        **os.environ,
        "PORT": str(port),
        "AWS_REGION": REGION,
        # Scoped to DynamoDB: the unscoped AWS_ENDPOINT_URL would apply to every
        # AWS service in boto3.
        "AWS_ENDPOINT_URL_DYNAMODB": DDB_ENDPOINT,
        "AWS_ACCESS_KEY_ID": TEST_ACCESS_KEY,
        "AWS_SECRET_ACCESS_KEY": TEST_SECRET_KEY,
        "RECALL_CARDS_TABLE": integration_tables["cards"],
        "RECALL_DECKS_TABLE": integration_tables["decks"],
        "RECALL_PROFILE_TABLE": integration_tables["profiles"],
    }
    env.pop("AWS_PROFILE", None)  # do not let a local profile override the keys
    env.pop("AWS_ENDPOINT_URL", None)

    process = subprocess.Popen(
        [_study_mcp_python(), "app.py"],
        cwd=str(STUDY_MCP_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    health = f"http://127.0.0.1:{port}/health"
    deadline = time.time() + SERVER_START_TIMEOUT
    while time.time() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise RuntimeError(f"study-mcp exited during startup:\n{output}")
        try:
            with urllib.request.urlopen(health, timeout=2) as response:
                if response.status == 200:
                    break
        except Exception:
            time.sleep(0.4)
    else:
        process.terminate()
        raise RuntimeError(f"study-mcp did not become healthy within {SERVER_START_TIMEOUT}s")

    yield f"http://127.0.0.1:{port}/mcp"

    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


@pytest.fixture
async def mcp_tools(mcp_server):
    """Tools discovered from the live server over a real MCP handshake."""
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(
        {"study": {"url": mcp_server, "transport": "streamable_http"}}
    )
    tools = await client.get_tools()
    return {tool.name: tool for tool in tools}
