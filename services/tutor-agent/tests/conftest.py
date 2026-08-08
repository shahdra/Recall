"""Shared test doubles for tutor-agent.

The LLM is always faked in unit tests: the point is to pin down how our code
handles model output — including malformed output — not to test the model.
"""

import json

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

import app as app_module


class FakeMessage:
    """Stands in for a LangChain AIMessage."""

    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class FakeLLM:
    """Replays a fixed list of responses, repeating the last one once exhausted.

    Repeating (rather than raising) keeps retry-path tests honest: a test that
    expects one retry still passes if the code retries twice, so assertions on
    ``calls`` are what pin the retry count down.
    """

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.received = []

    def invoke(self, messages, **kwargs):
        self.received.append(messages)
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        if isinstance(response, Exception):
            raise response
        if isinstance(response, FakeMessage):
            return response
        return FakeMessage(content=response)

    def bind_tools(self, tools, **kwargs):
        self.bound_tools = tools
        return self


@pytest.fixture
def fake_llm():
    def _build(responses):
        return FakeLLM(responses)

    return _build


# --- shared app fixtures ---

BUCKET = "recall-test-bucket"


class FakeMCPTool:
    """Stands in for a LangChain tool created from an MCP tool.

    Deliberately mirrors the real adapter's contract: async-only ``ainvoke``
    (sync ``invoke`` raises, as StructuredTool does), returning MCP content
    blocks rather than a bare JSON string. An earlier version of this fake
    offered a sync ``invoke`` returning plain JSON, and that mismatch let a
    NotImplementedError reach production code with every test passing.
    """

    def __init__(self, name, result):
        self.name = name
        self._result = result
        self.calls = []

    def invoke(self, args):
        raise NotImplementedError("StructuredTool does not support sync invocation.")

    async def ainvoke(self, args):
        self.calls.append(args)
        result = self._result(args) if callable(self._result) else self._result
        text = result if isinstance(result, str) else json.dumps(result)
        return [{"type": "text", "text": text, "id": "lc_fake"}]


@pytest.fixture
def mcp_tools():
    """A registry mimicking the study-mcp tools the agent discovers."""
    state = {"cards": {}, "decks": {}, "counter": 0}

    def create_deck(args):
        state["counter"] += 1
        deck_id = f"d{state['counter']}"
        state["decks"][deck_id] = {"title": args.get("title"), "cards": []}
        return {"deck_id": deck_id}

    def add_card(args):
        state["counter"] += 1
        card_id = f"c{state['counter']}"
        state["cards"][card_id] = {
            "card_id": card_id,
            "deck_id": args["deck_id"],
            "front": args["front"],
            "back": args["back"],
            "topic": args.get("topic", "general"),
        }
        return {"card_id": card_id}

    def get_due_cards(args):
        return {"cards": list(state["cards"].values())}

    def grade_card(args):
        return {"interval_days": 1, "due_date": "2026-06-02", "ease_factor": 2.5}

    return {
        "create_deck": FakeMCPTool("create_deck", create_deck),
        "add_card": FakeMCPTool("add_card", add_card),
        "get_due_cards": FakeMCPTool("get_due_cards", get_due_cards),
        "grade_card": FakeMCPTool("grade_card", grade_card),
        "get_progress": FakeMCPTool(
            "get_progress", {"total_reviews": 4, "accuracy": 0.75, "weak_topics": {"bio": 0.5}}
        ),
        "list_decks": FakeMCPTool("list_decks", {"decks": []}),
        "get_profile": FakeMCPTool(
            "get_profile",
            {"user_id": "u1", "weak_topics": {}, "preferences": {}, "stats": {}, "notes": ""},
        ),
        "update_profile": FakeMCPTool("update_profile", {"ok": True}),
    }


@pytest.fixture
def client(monkeypatch, mcp_tools):
    """A TestClient with S3 mocked and the agent's externals replaced.

    Startup discovery is stubbed out: without this the app would attempt a real
    connection to study-mcp, which both fails and costs a DNS timeout per test.
    """
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)

        async def fake_discover():
            return mcp_tools

        monkeypatch.setattr(app_module, "S3_BUCKET", BUCKET)
        monkeypatch.setattr(app_module, "_discover_mcp_tools", fake_discover)
        monkeypatch.setattr(app_module, "_build_llm_or_none", lambda: FakeLLM(["ok"]))
        monkeypatch.setattr(app_module, "_build_voice_or_none", lambda: None)
        monkeypatch.setattr(app_module, "_s3_client", lambda: s3)

        # raise_server_exceptions=False makes TestClient behave like a real HTTP
        # client: unhandled exceptions reach our error handler and come back as
        # a 500 response, instead of propagating into the test and hiding it.
        with TestClient(app_module.app, raise_server_exceptions=False) as c:
            c.mcp_tools = mcp_tools
            yield c


