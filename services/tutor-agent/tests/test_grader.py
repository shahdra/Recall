from fakes import FakeLLM

from grader import DEFAULT_FAIL_QUALITY, grade_answer


def test_correct_answer_high_quality():
    llm = FakeLLM(['{"is_correct":true,"explanation":"right","quality":5}'])
    out = grade_answer("Q", "A", "A", llm)
    assert out["is_correct"] is True
    assert out["quality"] == 5
    assert out["explanation"] == "right"


def test_wrong_answer_low_quality():
    llm = FakeLLM(['{"is_correct":false,"explanation":"not quite","quality":1}'])
    out = grade_answer("Q", "A", "banana", llm)
    assert out["is_correct"] is False
    assert out["quality"] == 1


def test_invalid_grade_defaults_to_resurface():
    """A grade outside 0-5 must not be trusted as a pass."""
    llm = FakeLLM(['{"is_correct":true,"quality":99}'])
    out = grade_answer("Q", "A", "A", llm)
    assert out["quality"] == DEFAULT_FAIL_QUALITY
    assert out["is_correct"] is False


def test_malformed_output_defaults_safely():
    llm = FakeLLM(["garbage"])
    out = grade_answer("Q", "A", "wrong", llm)
    assert out["quality"] == DEFAULT_FAIL_QUALITY
    assert out["is_correct"] is False
    assert out["explanation"]


def test_llm_exception_defaults_safely():
    llm = FakeLLM([RuntimeError("bedrock down")])
    out = grade_answer("Q", "A", "A", llm)
    assert out["quality"] == DEFAULT_FAIL_QUALITY
    assert out["is_correct"] is False


def test_default_quality_is_below_sm2_passing_grade():
    """The safe default must actually cause SM-2 to reschedule the card."""
    assert DEFAULT_FAIL_QUALITY < 3


def test_is_correct_reconciled_with_quality():
    """A passing grade with is_correct=false is contradictory; quality wins."""
    llm = FakeLLM(['{"is_correct":false,"explanation":"hmm","quality":5}'])
    out = grade_answer("Q", "A", "A", llm)
    assert out["is_correct"] is True


def test_low_quality_forces_is_correct_false():
    llm = FakeLLM(['{"is_correct":true,"explanation":"hmm","quality":1}'])
    out = grade_answer("Q", "A", "A", llm)
    assert out["is_correct"] is False


def test_blank_answer_is_graded_zero_without_calling_model():
    llm = FakeLLM(['{"is_correct":true,"quality":5}'])
    out = grade_answer("Q", "A", "   ", llm)
    assert out["quality"] == 0
    assert out["is_correct"] is False
    assert llm.calls == 0


def test_json_in_prose_is_parsed():
    llm = FakeLLM(
        ['Let me grade that.\n```json\n{"is_correct":true,"explanation":"yes","quality":4}\n```']
    )
    out = grade_answer("Q", "A", "A", llm)
    assert out["quality"] == 4


def test_question_and_answers_reach_the_model():
    llm = FakeLLM(['{"is_correct":true,"explanation":"ok","quality":4}'])
    grade_answer("What is ATP?", "energy currency", "the energy molecule", llm)
    sent = str(llm.received[0])
    assert "ATP" in sent
    assert "energy currency" in sent
    assert "the energy molecule" in sent


def test_missing_explanation_gets_a_fallback_string():
    llm = FakeLLM(['{"is_correct":true,"quality":4}'])
    out = grade_answer("Q", "A", "A", llm)
    assert out["explanation"]


def test_quality_is_an_int_not_a_float():
    """SM-2 and DynamoDB both expect an integer grade."""
    llm = FakeLLM(['{"is_correct":true,"explanation":"ok","quality":4.0}'])
    out = grade_answer("Q", "A", "A", llm)
    assert isinstance(out["quality"], int)
