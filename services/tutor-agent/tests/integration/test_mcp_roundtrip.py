"""Agent-to-study-mcp integration over the real MCP transport.

The course requires testing the agent against its own MCP server using the
actual protocol, not a stub. So every call here crosses a real HTTP request to a
separately-running server process and lands in DynamoDB Local. Only the LLM is
absent — these tests are about the transport and persistence, and a model would
add nondeterminism without adding coverage.

Run with DynamoDB Local up:

    docker run -d --rm --name recall-ddb -p 8001:8000 amazon/dynamodb-local
    pytest tests/integration/test_mcp_roundtrip.py -m integration -v
"""

import json

import pytest

pytestmark = pytest.mark.integration

EXPECTED_TOOLS = {
    "create_deck",
    "add_card",
    "get_due_cards",
    "grade_card",
    "get_progress",
    "list_decks",
    "get_profile",
    "update_profile",
}


async def call(tools, name, **kwargs):
    """Invoke an MCP tool and unwrap its content blocks.

    MCP replies with ``[{"type": "text", "text": "{...}"}]`` rather than a bare
    value. A failure arrives as a *successful* response whose text begins "Error
    calling tool", so this raises on that rather than returning an empty dict —
    the same trap that once made the API report success for failed writes.
    """
    raw = await tools[name].ainvoke(kwargs)

    if isinstance(raw, list):
        text = "\n".join(
            block.get("text", "")
            for block in raw
            if isinstance(block, dict) and block.get("type") == "text"
        )
    else:
        text = raw

    if isinstance(text, str) and text.startswith("Error calling tool"):
        raise AssertionError(f"MCP tool {name} failed: {text}")

    return json.loads(text) if isinstance(text, str) else text


# --- discovery ----------------------------------------------------------------


async def test_agent_discovers_every_tool_over_real_mcp(mcp_tools):
    """The names the agent binds must match what the server actually exposes."""
    assert EXPECTED_TOOLS <= set(mcp_tools)


async def test_discovered_tools_carry_usable_schemas(mcp_tools):
    """A tool with no argument schema cannot be called by a model."""
    schema = mcp_tools["add_card"].args_schema
    fields = schema["properties"] if isinstance(schema, dict) else schema.model_fields
    for required in ("deck_id", "user_id", "front", "back", "topic"):
        assert required in fields


# --- the round trip the plan requires -----------------------------------------


async def test_add_then_due_then_grade_over_mcp(mcp_tools):
    """add_card -> lands in DynamoDB -> get_due_cards returns it -> grade_card reschedules."""
    deck = await call(mcp_tools, "create_deck", user_id="rt1", title="Biology")
    assert deck["deck_id"]

    added = await call(
        mcp_tools,
        "add_card",
        deck_id=deck["deck_id"],
        user_id="rt1",
        front="What is ATP?",
        back="The energy currency of the cell",
        topic="bio",
    )
    assert added["card_id"]

    due = await call(mcp_tools, "get_due_cards", user_id="rt1")
    assert len(due["cards"]) == 1
    card = due["cards"][0]
    assert card["card_id"] == added["card_id"]
    assert card["front"] == "What is ATP?"
    # A new card starts with fresh SM-2 state.
    assert float(card["ease_factor"]) == pytest.approx(2.5)
    assert int(card["repetitions"]) == 0

    graded = await call(
        mcp_tools,
        "grade_card",
        deck_id=deck["deck_id"],
        card_id=added["card_id"],
        quality=5,
    )
    # A perfect first recall earns 4 days (sm2.FIRST_INTERVALS), not SM-2's 1.
    assert graded["interval_days"] == 4
    assert graded["due_date"]


async def test_graded_card_leaves_the_due_queue(mcp_tools):
    deck = await call(mcp_tools, "create_deck", user_id="rt2", title="Chem")
    added = await call(
        mcp_tools, "add_card", deck_id=deck["deck_id"], user_id="rt2",
        front="Q", back="A", topic="chem",
    )
    assert len(( await call(mcp_tools, "get_due_cards", user_id="rt2"))["cards"]) == 1

    await call(
        mcp_tools, "grade_card",
        deck_id=deck["deck_id"], card_id=added["card_id"], quality=5,
    )
    assert (await call(mcp_tools, "get_due_cards", user_id="rt2"))["cards"] == []


async def test_sm2_interval_grows_across_successive_correct_grades(mcp_tools):
    """The 4 -> 9 -> 24 day progression, driven entirely over MCP.

    The first two come from sm2.FIRST_INTERVALS/SECOND_INTERVALS at quality 5,
    which reward a perfect recall rather than using SM-2's flat 1 -> 6 ramp.
    The third is the ease multiplier taking over: three perfect grades have
    raised ease to 2.7, and round(9 * 2.7) = 24. The unit test sees a different
    third value because it passes a fixed ease rather than letting it accumulate
    across reviews.
    """
    deck = await call(mcp_tools, "create_deck", user_id="rt3", title="Physics")
    added = await call(
        mcp_tools, "add_card", deck_id=deck["deck_id"], user_id="rt3",
        front="Q", back="A", topic="physics",
    )

    intervals = []
    eases = []
    for _ in range(3):
        graded = await call(
            mcp_tools, "grade_card",
            deck_id=deck["deck_id"], card_id=added["card_id"], quality=5,
        )
        intervals.append(graded["interval_days"])
        eases.append(float(graded["ease_factor"]))

    assert intervals == [4, 9, 24]
    # Ease must rise monotonically, which is what produced the 24.
    assert eases == sorted(eases)
    assert eases[0] > 2.5


async def test_lapse_resets_the_interval(mcp_tools):
    """A well-known card that is missed must come back tomorrow."""
    deck = await call(mcp_tools, "create_deck", user_id="rt4", title="History")
    added = await call(
        mcp_tools, "add_card", deck_id=deck["deck_id"], user_id="rt4",
        front="Q", back="A", topic="history",
    )
    for _ in range(3):
        await call(
            mcp_tools, "grade_card",
            deck_id=deck["deck_id"], card_id=added["card_id"], quality=5,
        )

    lapsed = await call(
        mcp_tools, "grade_card",
        deck_id=deck["deck_id"], card_id=added["card_id"], quality=1,
    )
    assert lapsed["interval_days"] == 1


# --- progress and memory ------------------------------------------------------


async def test_progress_is_computed_from_real_review_history(mcp_tools):
    deck = await call(mcp_tools, "create_deck", user_id="rt5", title="Mixed")
    first = await call(
        mcp_tools, "add_card", deck_id=deck["deck_id"], user_id="rt5",
        front="Q1", back="A1", topic="alpha",
    )
    second = await call(
        mcp_tools, "add_card", deck_id=deck["deck_id"], user_id="rt5",
        front="Q2", back="A2", topic="beta",
    )

    await call(mcp_tools, "grade_card", deck_id=deck["deck_id"],
               card_id=first["card_id"], quality=5)
    await call(mcp_tools, "grade_card", deck_id=deck["deck_id"],
               card_id=second["card_id"], quality=0)

    progress = await call(mcp_tools, "get_progress", user_id="rt5")
    assert progress["total_reviews"] == 2
    assert float(progress["accuracy"]) == pytest.approx(0.5)
    assert "beta" in progress["weak_topics"]
    assert "alpha" not in progress["weak_topics"]


async def test_profile_round_trips_across_calls(mcp_tools):
    """The write half of long-term memory, over the real transport."""
    await call(
        mcp_tools, "update_profile",
        user_id="rt6",
        notes="mixes up mitosis and meiosis",
        weak_topics={"mitosis": 0.8},
    )
    profile = await call(mcp_tools, "get_profile", user_id="rt6")
    assert profile["notes"] == "mixes up mitosis and meiosis"
    assert float(profile["weak_topics"]["mitosis"]) == pytest.approx(0.8)


async def test_new_learner_gets_an_empty_profile_not_an_error(mcp_tools):
    profile = await call(mcp_tools, "get_profile", user_id="never-seen-before")
    assert profile["weak_topics"] == {}
    assert profile["stats"] == {}


async def test_optional_null_argument_is_accepted_over_mcp(mcp_tools):
    """Regression: `source_s3_key: str = None` generated a schema rejecting null,
    so a deck made from pasted text failed validation before the tool body ran.
    """
    deck = await call(
        mcp_tools, "create_deck", user_id="rt7", title="Pasted", source_s3_key=None
    )
    assert deck["deck_id"]


async def test_decks_are_scoped_per_learner(mcp_tools):
    await call(mcp_tools, "create_deck", user_id="rt8-a", title="Mine")
    await call(mcp_tools, "create_deck", user_id="rt8-b", title="Theirs")

    mine = await call(mcp_tools, "list_decks", user_id="rt8-a")
    assert [deck["title"] for deck in mine["decks"]] == ["Mine"]


async def test_due_cards_are_scoped_per_learner(mcp_tools):
    deck_a = await call(mcp_tools, "create_deck", user_id="rt9-a", title="A")
    deck_b = await call(mcp_tools, "create_deck", user_id="rt9-b", title="B")
    await call(mcp_tools, "add_card", deck_id=deck_a["deck_id"], user_id="rt9-a",
               front="mine", back="A", topic="t")
    await call(mcp_tools, "add_card", deck_id=deck_b["deck_id"], user_id="rt9-b",
               front="theirs", back="B", topic="t")

    due = await call(mcp_tools, "get_due_cards", user_id="rt9-a")
    assert [card["front"] for card in due["cards"]] == ["mine"]


# --- the agent loop driving real MCP tools ------------------------------------


class ScriptedMessage:
    """A model reply with a fixed set of tool calls."""

    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class ScriptedLLM:
    """Replays fixed replies, so the loop's behavior is what varies, not the model.

    Defined here rather than imported from the unit-test conftest: this package
    has its own conftest, which shadows that module name.
    """

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0
        self.received = []

    def invoke(self, messages, **kwargs):
        self.received.append(messages)
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return reply

    def bind_tools(self, tools, **kwargs):
        self.bound_tools = tools
        return self


async def test_react_loop_drives_real_mcp_tools(mcp_tools):
    """The hand-written loop must work against real async MCP tools.

    This is the case that broke in Task 3.6: MCP tools are async-only, so the
    loop's sync ``invoke`` raised NotImplementedError while every unit test
    passed against fakes that offered a sync path.
    """
    from agent_loop import arun_agent

    await call(mcp_tools, "create_deck", user_id="loop-user", title="Loop")

    llm = ScriptedLLM(
        [
            ScriptedMessage(
                tool_calls=[
                    {"name": "list_decks", "args": {"user_id": "loop-user"}, "id": "c1"}
                ]
            ),
            ScriptedMessage(content="You have one deck."),
        ]
    )

    result = await arun_agent(
        [{"role": "user", "content": "what decks do I have?"}], llm, mcp_tools
    )

    assert result["tools_called"] == ["list_decks"]
    assert result["tool_errors"] == 0
    assert result["response"] == "You have one deck."
    # The tool's real result must have been fed back to the model.
    assert "Loop" in str(llm.received[1])
