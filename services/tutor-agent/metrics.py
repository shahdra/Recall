"""Prometheus metrics for the tutor-agent.

"Healthy" for Recall means more than "the process is up" — it means the tutor is
actually teaching. So alongside the HTTP latency and error-rate series that
``prometheus-fastapi-instrumentator`` provides, these are split into two families:

**Failure modes worth paging on.** LLM failures and fallbacks (is the primary
model degrading?), agent iteration caps (is the model going in circles?), tool
errors (is study-mcp unhealthy?), transcription failures (is voice broken?).

**Product signal.** Decks created, cards generated, answers graded, answers
correct. Accuracy is deliberately *not* a gauge — it is derived on the dashboard
as ``recall_quiz_correct_total / recall_quizzes_graded_total``, so it can be
windowed over any time range rather than being frozen at whatever the last write
happened to be.

The ``read_*`` helpers exist for tests: asserting on a counter's value is far
clearer than scraping and parsing the exposition text.
"""

import logging

from prometheus_client import REGISTRY, Counter, Histogram

logger = logging.getLogger(__name__)

# --- failure modes ------------------------------------------------------------

llm_failures = Counter(
    "recall_llm_failures_total",
    "Language-model invocations that failed, including retried attempts.",
)

llm_fallbacks = Counter(
    "recall_llm_fallbacks_total",
    "Times the primary model was abandoned for the fallback model.",
)

tool_errors = Counter(
    "recall_tool_errors_total",
    "Tool calls that failed or named a tool that does not exist.",
)

transcription_failures = Counter(
    "recall_transcription_failures_total",
    "Speech-to-text attempts that returned no usable text.",
)

agent_capped = Counter(
    "recall_agent_capped_total",
    "Agent runs that hit the iteration cap instead of answering.",
)

agent_iterations = Histogram(
    "recall_agent_iterations",
    "Model calls per agent run.",
    # A normal tutoring turn is 1-3 calls; the cap is 8. Fine buckets at the low
    # end make the everyday case legible instead of lumping it into one bar.
    buckets=(1, 2, 3, 4, 5, 6, 8, 10),
)

# --- product signal -----------------------------------------------------------

decks_created = Counter(
    "recall_decks_created_total", "Study decks created from uploaded material."
)

cards_generated = Counter(
    "recall_cards_generated_total", "Flashcards generated from study material."
)

quizzes_graded = Counter(
    "recall_quizzes_graded_total", "Answers graded, correct or not."
)

quiz_correct = Counter(
    "recall_quiz_correct_total", "Answers graded as correct."
)


def record_agent_run(result: dict) -> None:
    """Record one agent run's outcome from the dict ``arun_agent`` returns."""
    try:
        agent_iterations.observe(result.get("iterations", 0))
        if result.get("capped"):
            agent_capped.inc()
        if result.get("llm_failed"):
            llm_failures.inc()
        errors = result.get("tool_errors", 0)
        if errors:
            tool_errors.inc(errors)
    except Exception:
        # Instrumentation must never break the request it is measuring.
        logger.exception("failed recording agent-run metrics")


def sync_llm_counters(model, state: dict) -> None:
    """Move a FallbackChatModel's internal tallies into Prometheus counters.

    The model counts its own failures and fallbacks so it can stay independent of
    the metrics layer. Counters only ever increase, so this forwards the *delta*
    since the last call, tracked in ``state``.
    """
    if model is None:
        return
    try:
        for attribute, counter, key in (
            ("failure_count", llm_failures, "failures"),
            ("fallback_count", llm_fallbacks, "fallbacks"),
        ):
            total = getattr(model, attribute, 0) or 0
            delta = total - state.get(key, 0)
            if delta > 0:
                counter.inc(delta)
                state[key] = total
    except Exception:
        logger.exception("failed syncing LLM counters")


# --- test helpers -------------------------------------------------------------


def read_counter(name: str) -> float:
    """Current value of a counter, or 0.0 if it is not registered."""
    value = REGISTRY.get_sample_value(name)
    if value is None:
        # prometheus_client exposes counters with a _total suffix; accept either.
        value = REGISTRY.get_sample_value(f"{name}_total")
    return value if value is not None else 0.0


def read_histogram_count(name: str) -> float:
    """Number of observations recorded in a histogram."""
    value = REGISTRY.get_sample_value(f"{name}_count")
    return value if value is not None else 0.0
