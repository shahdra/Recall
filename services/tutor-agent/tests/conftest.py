"""Shared test doubles for tutor-agent.

The LLM is always faked in unit tests: the point is to pin down how our code
handles model output — including malformed output — not to test the model.
"""

import pytest


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
