"""Tests for the Prometheus instrumentation.

"Healthy" for Recall is not just "the process is up" — it is "the tutor is
actually teaching". So alongside request latency and error rate, these metrics
carry product signal: cards generated, answers graded, accuracy, and the failure
modes that matter (LLM fallbacks, agent iteration caps, tool errors).
"""

import base64

import app as app_module
import metrics as metrics_module


def _metrics_text(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    return response.text


def test_metrics_endpoint_is_exposed(client):
    assert "python_info" in _metrics_text(client) or "http_request" in _metrics_text(client)


def test_all_custom_metrics_are_registered(client):
    text = _metrics_text(client)
    for name in (
        "recall_cards_generated_total",
        "recall_quizzes_graded_total",
        "recall_quiz_correct_total",
        "recall_llm_failures_total",
        "recall_llm_fallbacks_total",
        "recall_agent_iterations",
        "recall_transcription_failures_total",
        "recall_tool_errors_total",
        "recall_decks_created_total",
    ):
        assert name in text, f"{name} missing from /metrics"


def test_transcription_metric_is_provider_neutral(client):
    """Renamed from recall_whisper_failures_total when we moved to Deepgram."""
    text = _metrics_text(client)
    assert "recall_transcription_failures_total" in text
    assert "whisper" not in text.lower()


def test_cards_generated_counter_increments(client, monkeypatch):
    monkeypatch.setattr(
        app_module,
        "generate_cards",
        lambda material, llm, **kw: [
            {"front": "Q1", "back": "A1", "topic": "t"},
            {"front": "Q2", "back": "A2", "topic": "t"},
        ],
    )
    before = metrics_module.read_counter("recall_cards_generated_total")
    client.post("/decks", json={"user_id": "u1", "title": "T", "text": "material"})
    assert metrics_module.read_counter("recall_cards_generated_total") == before + 2


def test_decks_created_counter_increments(client, monkeypatch):
    monkeypatch.setattr(app_module, "generate_cards", lambda material, llm, **kw: [])
    before = metrics_module.read_counter("recall_decks_created_total")
    client.post("/decks", json={"user_id": "u1", "title": "T", "text": "material"})
    assert metrics_module.read_counter("recall_decks_created_total") == before + 1


def test_grading_counters_track_correct_and_total(client, monkeypatch):
    monkeypatch.setattr(
        app_module,
        "grade_answer",
        lambda q, a, s, llm: {"is_correct": True, "explanation": "y", "quality": 5},
    )
    graded_before = metrics_module.read_counter("recall_quizzes_graded_total")
    correct_before = metrics_module.read_counter("recall_quiz_correct_total")

    client.post(
        "/session/answer",
        json={
            "user_id": "u1", "deck_id": "d1", "card_id": "c1",
            "card_front": "Q", "card_back": "A", "student_answer": "A",
        },
    )
    assert metrics_module.read_counter("recall_quizzes_graded_total") == graded_before + 1
    assert metrics_module.read_counter("recall_quiz_correct_total") == correct_before + 1


def test_wrong_answer_increments_graded_but_not_correct(client, monkeypatch):
    monkeypatch.setattr(
        app_module,
        "grade_answer",
        lambda q, a, s, llm: {"is_correct": False, "explanation": "n", "quality": 1},
    )
    graded_before = metrics_module.read_counter("recall_quizzes_graded_total")
    correct_before = metrics_module.read_counter("recall_quiz_correct_total")

    client.post(
        "/session/answer",
        json={
            "user_id": "u1", "deck_id": "d1", "card_id": "c1",
            "card_front": "Q", "card_back": "A", "student_answer": "nope",
        },
    )
    assert metrics_module.read_counter("recall_quizzes_graded_total") == graded_before + 1
    assert metrics_module.read_counter("recall_quiz_correct_total") == correct_before


def test_accuracy_is_derivable_from_the_two_counters(client, monkeypatch):
    """The dashboard computes accuracy as correct/graded; both must move together."""
    answers = [True, False, True, True]
    calls = {"n": 0}

    def grade(q, a, s, llm):
        verdict = answers[calls["n"] % len(answers)]
        calls["n"] += 1
        return {"is_correct": verdict, "explanation": "x", "quality": 5 if verdict else 1}

    monkeypatch.setattr(app_module, "grade_answer", grade)
    graded_before = metrics_module.read_counter("recall_quizzes_graded_total")
    correct_before = metrics_module.read_counter("recall_quiz_correct_total")

    for _ in answers:
        client.post(
            "/session/answer",
            json={
                "user_id": "u1", "deck_id": "d1", "card_id": "c1",
                "card_front": "Q", "card_back": "A", "student_answer": "x",
            },
        )

    graded = metrics_module.read_counter("recall_quizzes_graded_total") - graded_before
    correct = metrics_module.read_counter("recall_quiz_correct_total") - correct_before
    assert graded == 4
    assert correct == 3


def test_agent_iterations_histogram_observes_chat_turns(client, monkeypatch):
    from conftest import FakeLLM, FakeMessage

    monkeypatch.setattr(app_module, "LLM", FakeLLM([FakeMessage(content="done")]))
    before = metrics_module.read_histogram_count("recall_agent_iterations")
    client.post("/chat", json={"user_id": "u1", "message": "hi"})
    assert metrics_module.read_histogram_count("recall_agent_iterations") == before + 1


def test_capped_agent_run_is_counted(client, monkeypatch):
    from conftest import FakeLLM, FakeMessage

    # A model that only ever asks for tools drives the loop into its cap.
    monkeypatch.setattr(
        app_module,
        "LLM",
        FakeLLM([FakeMessage(tool_calls=[{"name": "get_due_cards", "args": {}, "id": "c1"}])]),
    )
    before = metrics_module.read_counter("recall_agent_capped_total")
    client.post("/chat", json={"user_id": "u1", "message": "loop forever"})
    assert metrics_module.read_counter("recall_agent_capped_total") == before + 1


def test_llm_failure_is_counted(client, monkeypatch):
    from conftest import FakeLLM

    monkeypatch.setattr(app_module, "LLM", FakeLLM([RuntimeError("bedrock down")]))
    before = metrics_module.read_counter("recall_llm_failures_total")
    client.post("/chat", json={"user_id": "u1", "message": "hi"})
    assert metrics_module.read_counter("recall_llm_failures_total") > before


def test_transcription_failure_is_counted(client, monkeypatch):
    monkeypatch.setattr(app_module, "VOICE_CLIENT", object())
    monkeypatch.setattr(app_module, "transcribe", lambda audio, client, **kw: "")
    before = metrics_module.read_counter("recall_transcription_failures_total")
    client.post("/transcribe", json={"audio_b64": base64.b64encode(b"audio").decode()})
    assert metrics_module.read_counter("recall_transcription_failures_total") == before + 1


def test_successful_transcription_is_not_counted_as_failure(client, monkeypatch):
    monkeypatch.setattr(app_module, "VOICE_CLIENT", object())
    monkeypatch.setattr(app_module, "transcribe", lambda audio, client, **kw: "heard it")
    before = metrics_module.read_counter("recall_transcription_failures_total")
    client.post("/transcribe", json={"audio_b64": base64.b64encode(b"audio").decode()})
    assert metrics_module.read_counter("recall_transcription_failures_total") == before


def test_tool_errors_are_counted(client, monkeypatch):
    from conftest import FakeLLM, FakeMessage

    monkeypatch.setattr(
        app_module,
        "LLM",
        FakeLLM(
            [
                FakeMessage(tool_calls=[{"name": "no_such_tool", "args": {}, "id": "c1"}]),
                FakeMessage(content="recovered"),
            ]
        ),
    )
    before = metrics_module.read_counter("recall_tool_errors_total")
    client.post("/chat", json={"user_id": "u1", "message": "hi"})
    assert metrics_module.read_counter("recall_tool_errors_total") > before


def test_read_counter_returns_zero_for_unknown_metric():
    assert metrics_module.read_counter("recall_does_not_exist_total") == 0.0
