"""The Grader sub-agent.

Judges a learner's answer against the card's back, explains the verdict, and
assigns the SM-2 quality grade 0-5. Pure reasoning — it calls no tools and
performs no scheduling arithmetic; ``grade_card`` in study-mcp does that.

Every failure path defaults to *not* passing. A card wrongly resurfaced costs the
learner a few seconds; a card wrongly retired means they never see the material
again, and the tutor has quietly failed at its one job.
"""

import logging

from pydantic import BaseModel, Field, ValidationError

from llm_json import extract_json, message_text

logger = logging.getLogger(__name__)

DEFAULT_FAIL_QUALITY = 2
"""Safe default. Below SM-2's passing grade of 3, so the card comes back tomorrow."""

PASSING_GRADE = 3
MIN_QUALITY = 0
MAX_QUALITY = 5

_FALLBACK_EXPLANATION = (
    "I could not grade that reliably, so I've kept this card in your review "
    "queue. Let's try it again."
)

GRADER_PROMPT = """You grade a student's flashcard answer.

You are given the question, the correct answer, and the student's answer. Judge
whether the student demonstrated recall of the key idea.

Grading scale (SM-2 quality):
- 5: perfect, immediate recall
- 4: correct with slight hesitation or imprecise wording
- 3: correct in substance but incomplete
- 2: partly right, missed the key idea
- 1: mostly wrong but shows a trace of recall
- 0: no answer, or entirely wrong

Judge meaning, not wording — a correct answer phrased differently is still
correct. Do not reward confident-sounding but wrong answers.

Explain in one or two sentences, addressed to the student, saying *why*.

Reply with JSON only:
{"is_correct": true, "explanation": "...", "quality": 4}"""


class Verdict(BaseModel):
    """A validated grading verdict."""

    is_correct: bool = False
    explanation: str = Field(default=_FALLBACK_EXPLANATION)
    quality: int = Field(ge=MIN_QUALITY, le=MAX_QUALITY)


def _safe_default(explanation: str = _FALLBACK_EXPLANATION) -> dict:
    return {
        "is_correct": False,
        "explanation": explanation,
        "quality": DEFAULT_FAIL_QUALITY,
    }


def grade_answer(
    question: str,
    correct_answer: str,
    student_answer: str,
    llm,
) -> dict:
    """Grade one answer.

    Args:
        question: The card's front.
        correct_answer: The card's back.
        student_answer: What the learner said or typed.
        llm: A chat model exposing ``.invoke(messages)``. Injected so tests fake it.

    Returns:
        ``{"is_correct": bool, "explanation": str, "quality": int}`` with quality
        in 0-5. Never raises — any failure yields the safe default, which
        resurfaces the card.
    """
    if not student_answer or not student_answer.strip():
        return {
            "is_correct": False,
            "explanation": "You didn't answer this one, so it stays in the queue.",
            "quality": 0,
        }

    messages = [
        {"role": "system", "content": GRADER_PROMPT},
        {
            "role": "user",
            "content": (
                f"Question: {question}\n"
                f"Correct answer: {correct_answer}\n"
                f"Student's answer: {student_answer}"
            ),
        },
    ]

    try:
        response = llm.invoke(messages)
    except Exception:
        logger.exception("grading LLM call failed")
        return _safe_default()

    payload = extract_json(message_text(response))
    if not isinstance(payload, dict):
        logger.warning("grader returned unparseable output")
        return _safe_default()

    try:
        verdict = Verdict(**payload)
    except (ValidationError, TypeError):
        logger.warning("grader returned an invalid verdict: %s", payload)
        return _safe_default()

    # The model can contradict itself — say "incorrect" yet grade a 5, or vice
    # versa. The numeric grade drives scheduling, so let it settle the question.
    is_correct = verdict.quality >= PASSING_GRADE

    return {
        "is_correct": is_correct,
        "explanation": verdict.explanation or _FALLBACK_EXPLANATION,
        "quality": int(verdict.quality),
    }
