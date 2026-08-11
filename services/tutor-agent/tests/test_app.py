"""Endpoint tests for the tutor-agent API.

Everything external is faked: the LLM, the study-mcp tool registry, and S3 (moto).
What is under test is the wiring — that an upload becomes a deck, that an answer
becomes a graded card with a new due date, and that failures come back as clean
JSON rather than tracebacks.
"""

import base64
import json

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

import app as app_module
from fakes import FakeLLM, FakeMCPTool, FakeMessage

# --- health -------------------------------------------------------------------


def test_health_is_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_reports_dependency_state(client):
    body = client.get("/health").json()
    assert "mcp_tools" in body
    assert body["mcp_tools"] >= 4


# --- demo clock ---------------------------------------------------------------
#
# The default tool registry has no clock tools, mirroring a production study-mcp
# started without RECALL_DEMO_MODE. That is what makes "off" the default here.


def test_health_reports_demo_mode_off_without_the_tools(client):
    body = client.get("/health").json()
    assert body["demo_mode"] is False
    assert body["simulated_date"] is None


def test_advance_clock_is_404_without_demo_mode(client):
    """A build with the tool absent must refuse, not silently no-op."""
    response = client.post("/demo/advance-clock", json={"days": 1})
    assert response.status_code == 404
    assert response.json()["code"] == "demo_disabled"


def test_reset_clock_is_404_without_demo_mode(client):
    assert client.post("/demo/reset-clock").status_code == 404


def _add_clock_tools(client, monkeypatch):
    """Register fake clock tools, as a demo-mode study-mcp would expose."""
    state = {"offset": 0}

    def advance(args):
        state["offset"] += args.get("days", 1)
        return {
            "demo_mode": True,
            "offset_days": state["offset"],
            "simulated_date": f"2026-08-{10 + state['offset']:02d}",
            "real_date": "2026-08-10",
        }

    def reset(args):
        state["offset"] = 0
        return {
            "demo_mode": True,
            "offset_days": 0,
            "simulated_date": "2026-08-10",
            "real_date": "2026-08-10",
        }

    tools = dict(client.mcp_tools)
    tools["advance_clock"] = FakeMCPTool("advance_clock", advance)
    tools["reset_clock"] = FakeMCPTool("reset_clock", reset)
    tools["get_clock"] = FakeMCPTool("get_clock", lambda args: reset(args))
    monkeypatch.setattr(app_module, "MCP_TOOLS", tools)
    return state


def test_health_reports_demo_mode_when_the_tools_exist(client, monkeypatch):
    _add_clock_tools(client, monkeypatch)
    body = client.get("/health").json()
    assert body["demo_mode"] is True
    assert body["simulated_date"] == "2026-08-10"


def test_advance_clock_passes_through_and_accumulates(client, monkeypatch):
    _add_clock_tools(client, monkeypatch)
    first = client.post("/demo/advance-clock", json={"days": 1}).json()
    assert first["offset_days"] == 1
    second = client.post("/demo/advance-clock", json={"days": 1}).json()
    assert second["offset_days"] == 2
    assert second["simulated_date"] == "2026-08-12"


def test_advance_clock_defaults_to_one_day(client, monkeypatch):
    _add_clock_tools(client, monkeypatch)
    body = client.post("/demo/advance-clock", json={}).json()
    assert body["offset_days"] == 1


def test_reset_clock_passes_through(client, monkeypatch):
    _add_clock_tools(client, monkeypatch)
    client.post("/demo/advance-clock", json={"days": 5})
    body = client.post("/demo/reset-clock").json()
    assert body["offset_days"] == 0


# --- POST /decks --------------------------------------------------------------


def test_create_deck_from_pasted_text(client, monkeypatch):
    monkeypatch.setattr(
        app_module,
        "generate_cards",
        lambda material, llm, **kw: [
            {"front": "Q1", "back": "A1", "topic": "bio"},
            {"front": "Q2", "back": "A2", "topic": "bio"},
        ],
    )
    response = client.post(
        "/decks",
        json={"user_id": "u1", "title": "Biology", "text": "mitochondria make ATP"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["card_count"] == 2
    assert body["deck_id"]


def test_create_deck_persists_cards_via_mcp(client, monkeypatch):
    monkeypatch.setattr(
        app_module,
        "generate_cards",
        lambda material, llm, **kw: [{"front": "Q", "back": "A", "topic": "t"}],
    )
    client.post("/decks", json={"user_id": "u1", "title": "T", "text": "material here"})
    assert len(client.mcp_tools["add_card"].calls) == 1


def test_create_deck_stores_pdf_in_s3(client, monkeypatch):
    monkeypatch.setattr(
        app_module,
        "generate_cards",
        lambda material, llm, **kw: [{"front": "Q", "back": "A", "topic": "t"}],
    )
    pdf = (
        b"%PDF-1.4\n"  # extract_text is stubbed below, so contents need not parse
    )
    monkeypatch.setattr(app_module, "extract_text", lambda data, ct: "extracted text")
    response = client.post(
        "/decks",
        json={
            "user_id": "u1",
            "title": "Notes",
            "file_b64": base64.b64encode(pdf).decode(),
            "content_type": "application/pdf",
        },
    )
    assert response.status_code == 200
    assert response.json()["source_s3_key"].startswith("uploads/u1/")


def test_create_deck_rejects_empty_request(client):
    response = client.post("/decks", json={"user_id": "u1", "title": "T"})
    assert response.status_code == 400
    assert "error" in response.json()


def test_create_deck_surfaces_ingest_error_as_400(client, monkeypatch):
    from ingest import IngestError

    def boom(data, content_type):
        raise IngestError("That PDF is password-protected.")

    monkeypatch.setattr(app_module, "extract_text", boom)
    response = client.post(
        "/decks",
        json={
            "user_id": "u1",
            "title": "T",
            "file_b64": base64.b64encode(b"junk").decode(),
            "content_type": "application/pdf",
        },
    )
    assert response.status_code == 400
    body = response.json()
    assert "password" in body["error"]
    assert "Traceback" not in json.dumps(body)


def test_create_deck_warns_when_no_cards_generated(client, monkeypatch):
    monkeypatch.setattr(app_module, "generate_cards", lambda material, llm, **kw: [])
    response = client.post("/decks", json={"user_id": "u1", "title": "T", "text": "material"})
    assert response.status_code == 200
    body = response.json()
    assert body["card_count"] == 0
    assert body.get("warning")


def test_create_deck_rejects_bad_base64(client):
    response = client.post(
        "/decks",
        json={
            "user_id": "u1",
            "title": "T",
            "file_b64": "!!!not-base64!!!",
            "content_type": "application/pdf",
        },
    )
    assert response.status_code == 400


# --- POST /session/start ------------------------------------------------------


def test_session_start_returns_due_cards(client, monkeypatch):
    monkeypatch.setattr(
        app_module,
        "generate_cards",
        lambda material, llm, **kw: [{"front": "Q", "back": "A", "topic": "t"}],
    )
    client.post("/decks", json={"user_id": "u1", "title": "T", "text": "material"})
    response = client.post("/session/start", json={"user_id": "u1"})
    assert response.status_code == 200
    body = response.json()
    assert len(body["cards"]) == 1
    assert body["cards"][0]["front"] == "Q"


def test_session_start_includes_profile(client):
    response = client.post("/session/start", json={"user_id": "u1"})
    assert response.status_code == 200
    assert "profile" in response.json()


def test_session_start_handles_no_due_cards_as_success(client):
    response = client.post("/session/start", json={"user_id": "u1"})
    assert response.status_code == 200
    assert response.json()["cards"] == []
    assert response.json().get("message")


# --- POST /session/answer -----------------------------------------------------


def test_answer_grades_and_reschedules(client, monkeypatch):
    monkeypatch.setattr(
        app_module,
        "grade_answer",
        lambda q, a, s, llm: {"is_correct": True, "explanation": "right", "quality": 5},
    )
    response = client.post(
        "/session/answer",
        json={
            "user_id": "u1",
            "deck_id": "d1",
            "card_id": "c1",
            "card_front": "Q",
            "card_back": "A",
            "student_answer": "A",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["is_correct"] is True
    assert body["due_date"] == "2026-06-02"
    assert body["explanation"] == "right"


def test_answer_calls_grade_card_with_the_quality(client, monkeypatch):
    monkeypatch.setattr(
        app_module,
        "grade_answer",
        lambda q, a, s, llm: {"is_correct": False, "explanation": "no", "quality": 1},
    )
    client.post(
        "/session/answer",
        json={
            "user_id": "u1",
            "deck_id": "d1",
            "card_id": "c1",
            "card_front": "Q",
            "card_back": "A",
            "student_answer": "wrong",
        },
    )
    call = client.mcp_tools["grade_card"].calls[0]
    assert call["quality"] == 1


def test_answer_requires_card_identifiers(client):
    response = client.post("/session/answer", json={"user_id": "u1", "student_answer": "x"})
    assert response.status_code == 422


# --- POST /chat ---------------------------------------------------------------


def test_chat_runs_the_agent_loop(client, monkeypatch):
    monkeypatch.setattr(
        app_module, "LLM", FakeLLM([FakeMessage(content="Nothing due today.")])
    )
    response = client.post("/chat", json={"user_id": "u1", "message": "what's due?"})
    assert response.status_code == 200
    body = response.json()
    assert body["response"] == "Nothing due today."
    assert body["iterations"] == 1


def test_chat_exposes_tools_to_the_model(client, monkeypatch):
    llm = FakeLLM([FakeMessage(content="done")])
    monkeypatch.setattr(app_module, "LLM", llm)
    client.post("/chat", json={"user_id": "u1", "message": "hi"})
    bound = {t.name for t in llm.bound_tools}
    assert "get_due_cards" in bound
    # Sub-agents are exposed to the orchestrator as tools.
    assert "generate_cards" in bound
    assert "grade_answer" in bound


def test_chat_injects_the_learner_profile_into_the_system_prompt(client, monkeypatch):
    """Long-term memory: what the tutor learned last session must reach this one."""
    client.mcp_tools["get_profile"]._result = {
        "user_id": "u1",
        "weak_topics": {"mitosis": 0.8},
        "preferences": {},
        "stats": {},
        "notes": "confuses mitosis with meiosis",
    }
    llm = FakeLLM([FakeMessage(content="ok")])
    monkeypatch.setattr(app_module, "LLM", llm)

    client.post("/chat", json={"user_id": "u1", "message": "quiz me"})

    system_prompt = llm.received[0][0]["content"]
    assert "mitosis" in system_prompt
    assert "confuses mitosis with meiosis" in system_prompt


def test_chat_survives_an_unavailable_profile(client, monkeypatch):
    """A tutor with no memory still tutors."""
    tools = dict(client.mcp_tools)
    tools.pop("get_profile")
    monkeypatch.setattr(app_module, "MCP_TOOLS", tools)
    monkeypatch.setattr(app_module, "LLM", FakeLLM([FakeMessage(content="still here")]))

    response = client.post("/chat", json={"user_id": "u1", "message": "hi"})
    assert response.status_code == 200
    assert response.json()["response"] == "still here"


def test_answer_refreshes_profile_memory(client, monkeypatch):
    """The write half of memory: grading an answer updates the profile."""
    monkeypatch.setattr(
        app_module,
        "grade_answer",
        lambda q, a, s, llm: {"is_correct": False, "explanation": "no", "quality": 1},
    )
    client.post(
        "/session/answer",
        json={
            "user_id": "u1",
            "deck_id": "d1",
            "card_id": "c1",
            "card_front": "Q",
            "card_back": "A",
            "student_answer": "wrong",
        },
    )
    updates = client.mcp_tools["update_profile"].calls
    assert len(updates) == 1
    assert updates[0]["weak_topics"] == {"bio": 0.5}
    assert updates[0]["stats"]["total_reviews"] == 4


def test_answer_still_succeeds_if_memory_refresh_fails(client, monkeypatch):
    """A failed memory write must not undo an already-graded answer."""
    monkeypatch.setattr(
        app_module,
        "grade_answer",
        lambda q, a, s, llm: {"is_correct": True, "explanation": "yes", "quality": 5},
    )

    async def boom(args):
        raise RuntimeError("dynamo down")

    client.mcp_tools["update_profile"].ainvoke = boom

    response = client.post(
        "/session/answer",
        json={
            "user_id": "u1",
            "deck_id": "d1",
            "card_id": "c1",
            "card_front": "Q",
            "card_back": "A",
            "student_answer": "A",
        },
    )
    assert response.status_code == 200
    assert response.json()["is_correct"] is True


def test_chat_reports_llm_failure_gracefully(client, monkeypatch):
    monkeypatch.setattr(app_module, "LLM", FakeLLM([RuntimeError("bedrock down")]))
    response = client.post("/chat", json={"user_id": "u1", "message": "hi"})
    assert response.status_code == 200
    body = response.json()
    assert body["llm_failed"] is True
    assert "Traceback" not in body["response"]


# --- POST /transcribe ---------------------------------------------------------


def test_transcribe_returns_text(client, monkeypatch):
    monkeypatch.setattr(app_module, "VOICE_CLIENT", object())
    monkeypatch.setattr(app_module, "transcribe", lambda audio, client, **kw: "spoken words")
    response = client.post(
        "/transcribe", json={"audio_b64": base64.b64encode(b"audio").decode()}
    )
    assert response.status_code == 200
    assert response.json()["text"] == "spoken words"


def test_transcribe_returns_empty_text_when_voice_disabled(client, monkeypatch):
    monkeypatch.setattr(app_module, "VOICE_CLIENT", None)
    response = client.post(
        "/transcribe", json={"audio_b64": base64.b64encode(b"audio").decode()}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == ""
    assert body.get("message")


def test_transcribe_rejects_bad_base64(client, monkeypatch):
    monkeypatch.setattr(app_module, "VOICE_CLIENT", object())
    response = client.post("/transcribe", json={"audio_b64": "!!!nope!!!"})
    assert response.status_code == 400


# --- errors -------------------------------------------------------------------


def test_unhandled_error_returns_structured_json(client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("dynamo exploded")

    monkeypatch.setattr(app_module, "generate_cards", boom)
    response = client.post("/decks", json={"user_id": "u1", "title": "T", "text": "material"})
    assert response.status_code == 500
    body = response.json()
    assert body["error"]
    assert body["request_id"]
    assert "dynamo exploded" not in json.dumps(body)  # no internals leaked


def test_missing_mcp_tool_is_reported_not_crashed(client, monkeypatch):
    monkeypatch.setattr(app_module, "MCP_TOOLS", {})
    response = client.post("/session/start", json={"user_id": "u1"})
    assert response.status_code == 503
    assert "error" in response.json()


def test_mcp_tool_error_text_is_not_treated_as_success(client):
    """FastMCP reports tool failures as a *successful* response whose text is
    "Error calling tool '...': ...". Parsing that as an empty dict made /decks
    report card_count: 15 while every write had actually failed — the API
    claiming success for work that never happened.
    """

    async def failing(args):
        return [
            {
                "type": "text",
                "text": (
                    "Error calling tool 'create_deck': An error occurred "
                    "(ResourceNotFoundException) when calling the PutItem "
                    "operation: Cannot do operations on a non-existent table"
                ),
            }
        ]

    client.mcp_tools["create_deck"].ainvoke = failing

    response = client.post(
        "/decks", json={"user_id": "u1", "title": "T", "text": "material"}
    )
    assert response.status_code == 502
    body = response.json()
    assert body["code"] == "mcp_error"


def test_tool_error_detection_is_case_and_prefix_specific(client):
    """A card whose legitimate content mentions an error must not trip the check."""
    from app import _unwrap_tool_result

    legit = [{"type": "text", "text": '{"front":"What is an Error calling tool?"}'}]
    assert _unwrap_tool_result(legit) == {"front": "What is an Error calling tool?"}
