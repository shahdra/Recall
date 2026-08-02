"""Tests for the hand-written ReAct loop.

The loop is deliberately not delegated to create_react_agent or AgentExecutor,
so these tests pin down the behavior we would otherwise be trusting a framework
for: tool routing, the iteration cap, and what happens when a tool explodes.
"""

from conftest import FakeLLM, FakeMessage

from agent_loop import run_agent


class FakeTool:
    """Minimal stand-in for a LangChain tool."""

    def __init__(self, name, result="tool result", raises=None):
        self.name = name
        self.result = result
        self.raises = raises
        self.calls = []

    def invoke(self, args):
        self.calls.append(args)
        if self.raises:
            raise self.raises
        return self.result


def _tool_call(name, args=None, call_id="call_1"):
    return {"name": name, "args": args or {}, "id": call_id}


def test_returns_answer_without_calling_tools():
    llm = FakeLLM([FakeMessage(content="Nothing is due today.")])
    out = run_agent([{"role": "user", "content": "hi"}], llm, {})
    assert out["response"] == "Nothing is due today."
    assert out["tools_called"] == []
    assert out["capped"] is False


def test_calls_tool_then_returns_answer():
    llm = FakeLLM(
        [
            FakeMessage(tool_calls=[_tool_call("get_due_cards", {"user_id": "u1"})]),
            FakeMessage(content="done, 2 cards"),
        ]
    )
    tools = {"get_due_cards": FakeTool("get_due_cards", "2 cards")}
    out = run_agent([{"role": "user", "content": "what's due?"}], llm, tools)
    assert out["tools_called"] == ["get_due_cards"]
    assert "done" in out["response"]
    assert tools["get_due_cards"].calls == [{"user_id": "u1"}]


def test_iteration_cap_prevents_runaway():
    always_tool = FakeLLM([FakeMessage(tool_calls=[_tool_call("noop")])])
    tools = {"noop": FakeTool("noop")}
    out = run_agent([{"role": "user", "content": "go"}], always_tool, tools, max_iterations=3)
    assert out["capped"] is True
    assert out["iterations"] == 3
    assert out["response"]  # must still say something to the learner


def test_multiple_tool_calls_in_one_turn_all_run():
    llm = FakeLLM(
        [
            FakeMessage(
                tool_calls=[
                    _tool_call("a", call_id="c1"),
                    _tool_call("b", call_id="c2"),
                ]
            ),
            FakeMessage(content="both done"),
        ]
    )
    tools = {"a": FakeTool("a"), "b": FakeTool("b")}
    out = run_agent([{"role": "user", "content": "go"}], llm, tools)
    assert out["tools_called"] == ["a", "b"]
    assert len(tools["a"].calls) == 1
    assert len(tools["b"].calls) == 1


def test_unknown_tool_is_reported_to_the_model_not_raised():
    """A hallucinated tool name must not crash the request."""
    llm = FakeLLM(
        [
            FakeMessage(tool_calls=[_tool_call("does_not_exist")]),
            FakeMessage(content="recovered"),
        ]
    )
    out = run_agent([{"role": "user", "content": "go"}], llm, {})
    assert out["response"] == "recovered"


def test_tool_exception_is_fed_back_not_raised():
    llm = FakeLLM(
        [
            FakeMessage(tool_calls=[_tool_call("boom")]),
            FakeMessage(content="handled it"),
        ]
    )
    tools = {"boom": FakeTool("boom", raises=RuntimeError("dynamo down"))}
    out = run_agent([{"role": "user", "content": "go"}], llm, tools)
    assert out["response"] == "handled it"
    assert out["tool_errors"] == 1


def test_llm_failure_returns_graceful_message():
    llm = FakeLLM([RuntimeError("bedrock 503")])
    out = run_agent([{"role": "user", "content": "go"}], llm, {})
    assert out["llm_failed"] is True
    assert "Traceback" not in out["response"]
    assert out["response"]


def test_tools_are_bound_to_the_model():
    llm = FakeLLM([FakeMessage(content="ok")])
    tools = {"a": FakeTool("a")}
    run_agent([{"role": "user", "content": "go"}], llm, tools)
    assert llm.bound_tools == [tools["a"]]


def test_conversation_history_is_preserved_and_extended():
    llm = FakeLLM(
        [
            FakeMessage(tool_calls=[_tool_call("a")]),
            FakeMessage(content="final"),
        ]
    )
    tools = {"a": FakeTool("a")}
    out = run_agent([{"role": "user", "content": "go"}], llm, tools)
    # The second call must include the tool result the first turn produced.
    assert "tool result" in str(llm.received[1])
    assert out["messages"][0]["content"] == "go"


def test_iteration_count_reflects_actual_llm_calls():
    llm = FakeLLM(
        [
            FakeMessage(tool_calls=[_tool_call("a")]),
            FakeMessage(content="done"),
        ]
    )
    out = run_agent([{"role": "user", "content": "go"}], llm, {"a": FakeTool("a")})
    assert out["iterations"] == 2


def test_empty_content_with_no_tool_calls_still_returns_something():
    llm = FakeLLM([FakeMessage(content="")])
    out = run_agent([{"role": "user", "content": "go"}], llm, {})
    assert out["response"]


def test_capped_run_reports_tools_it_did_call():
    llm = FakeLLM([FakeMessage(tool_calls=[_tool_call("a")])])
    out = run_agent(
        [{"role": "user", "content": "go"}], llm, {"a": FakeTool("a")}, max_iterations=2
    )
    assert out["capped"] is True
    assert out["tools_called"] == ["a", "a"]
