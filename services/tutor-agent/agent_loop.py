"""The ReAct loop, written by hand.

This is deliberately not ``create_react_agent`` or ``AgentExecutor``. The whole
loop is thirty lines and one ``while``, and writing it out means every failure
mode — a hallucinated tool name, a tool that raises, a model that never stops
calling tools — is handled somewhere you can point at.

The shape is the classic Reason-Act cycle:

    ask the model  ->  did it request tools?
                         yes: run them, append the results, ask again
                         no:  that is the answer, return it

Two rules keep it honest. The model chooses tools but never touches I/O itself,
and the loop cannot run forever: ``max_iterations`` bounds it, and hitting that
bound returns a real answer rather than an exception.
"""

import json
import logging

from llm_json import message_text

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITERATIONS = 8
"""Enough for a realistic tutoring turn (fetch due cards, grade, update profile)
with headroom; low enough that a confused model cannot burn the request budget."""

_LLM_FAILURE_MESSAGE = (
    "I'm having trouble reaching my language model right now. "
    "Your progress is saved — please try again in a moment."
)

_CAPPED_MESSAGE = (
    "I got a bit tangled working that out. Could you rephrase, or try a "
    "smaller step?"
)


def _tool_result_message(call_id: str, name: str, content: str) -> dict:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "name": name,
        "content": content,
    }


def _stringify(result) -> str:
    """Tool results go back to the model as text."""
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, default=str)
    except (TypeError, ValueError):
        return str(result)


def run_agent(
    messages: list,
    llm,
    tools: dict,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> dict:
    """Run the ReAct loop until the model answers or the cap is hit.

    Args:
        messages: Conversation so far, including the system prompt.
        llm: Chat model exposing ``.bind_tools(list)`` and ``.invoke(messages)``.
        tools: Name -> tool object with ``.invoke(args)``. May be empty.
        max_iterations: Hard ceiling on model calls.

    Returns:
        ``response`` (text for the learner), ``iterations``, ``tools_called``,
        ``capped``, ``tool_errors``, ``llm_failed``, and the final ``messages``.
        Never raises — a caller rendering a chat turn should always get text.
    """
    conversation = list(messages)
    tools_called: list[str] = []
    tool_errors = 0
    iterations = 0

    model = llm.bind_tools(list(tools.values())) if tools else llm

    while iterations < max_iterations:
        iterations += 1

        try:
            response = model.invoke(conversation)
        except Exception:
            logger.exception("LLM invocation failed on iteration %d", iterations)
            return {
                "response": _LLM_FAILURE_MESSAGE,
                "iterations": iterations,
                "tools_called": tools_called,
                "capped": False,
                "tool_errors": tool_errors,
                "llm_failed": True,
                "messages": conversation,
            }

        tool_calls = getattr(response, "tool_calls", None) or []

        if not tool_calls:
            # No tools requested: the model is answering, so we are done.
            text = message_text(response).strip()
            return {
                "response": text or _CAPPED_MESSAGE,
                "iterations": iterations,
                "tools_called": tools_called,
                "capped": False,
                "tool_errors": tool_errors,
                "llm_failed": False,
                "messages": conversation,
            }

        conversation.append(response)

        for call in tool_calls:
            name = call.get("name", "")
            args = call.get("args", {}) or {}
            call_id = call.get("id", "")
            tools_called.append(name)

            tool = tools.get(name)
            if tool is None:
                # The model invented a tool. Tell it so, rather than crashing;
                # it usually recovers on the next turn.
                logger.warning("model requested unknown tool %r", name)
                tool_errors += 1
                conversation.append(
                    _tool_result_message(
                        call_id, name, f"Error: no such tool {name!r}."
                    )
                )
                continue

            try:
                result = tool.invoke(args)
            except Exception as exc:
                logger.exception("tool %r failed", name)
                tool_errors += 1
                conversation.append(
                    _tool_result_message(call_id, name, f"Error: {exc}")
                )
                continue

            conversation.append(_tool_result_message(call_id, name, _stringify(result)))

    # Cap reached. Say something useful instead of raising.
    logger.warning("agent hit the %d-iteration cap", max_iterations)
    return {
        "response": _CAPPED_MESSAGE,
        "iterations": iterations,
        "tools_called": tools_called,
        "capped": True,
        "tool_errors": tool_errors,
        "llm_failed": False,
        "messages": conversation,
    }
