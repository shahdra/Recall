"""Coax JSON out of a chat model's reply.

Lightweight models rarely return bare JSON — they wrap it in prose, fence it in
markdown, or add a trailing remark. Rather than fight that with ever-stricter
prompts, we parse tolerantly and validate strictly afterwards.
"""

import json
import re

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> dict | list | None:
    """Return the first JSON object or array in ``text``, or None if there is none.

    Tries, in order: the whole string, any fenced code block, then the widest
    brace- or bracket-delimited span.
    """
    if not text or not text.strip():
        return None

    candidates = [text]
    candidates += _FENCE.findall(text)

    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, (dict, list)):
            return parsed
    return None


def message_text(response) -> str:
    """Flatten a chat response's content to plain text.

    Content may be a string or, for some providers, a list of content blocks.
    """
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and "text" in block:
                parts.append(block["text"])
        return "\n".join(parts)
    return str(content)
