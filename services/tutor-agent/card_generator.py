"""The Card-Generator sub-agent.

Turns study material into flashcards. Exposed to the orchestrator as a tool, so
the orchestrator decides *when* to generate cards and this module decides *what*
the cards are.

The contract is deliberately forgiving: a partly-malformed batch yields the cards
that did parse rather than nothing, and a hopeless batch yields an empty list
rather than an exception. Losing a study deck to a stray comma would be a worse
outcome than a short deck.
"""

import logging

from pydantic import BaseModel, Field, ValidationError, field_validator

from llm_json import extract_json, message_text

logger = logging.getLogger(__name__)

DEFAULT_MAX_CARDS = 15


class Card(BaseModel):
    """One flashcard. Blank fronts or backs are rejected — they teach nothing."""

    front: str = Field(min_length=1)
    back: str = Field(min_length=1)
    topic: str = Field(default="general")

    @field_validator("front", "back", "topic", mode="before")
    @classmethod
    def _strip(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("front", "back")
    @classmethod
    def _not_blank(cls, value):
        if not value:
            raise ValueError("must not be blank")
        return value


CARD_GEN_PROMPT = """You write flashcards for a student.

Given study material, produce clear question/answer flashcards. Rules:
- One fact per card. Keep the front a single question.
- The back is the answer, not an essay. One or two sentences.
- Tag each card with a short lowercase `topic` drawn from the material.
- Never invent facts absent from the material.
- Produce at most {max_cards} cards.

Reply with JSON only, in exactly this shape:
{{"cards": [{{"front": "...", "back": "...", "topic": "..."}}]}}"""

_RETRY_SUFFIX = """

Your previous reply could not be parsed as JSON. Reply with ONLY the raw JSON
object. No prose, no markdown fences, no explanation. Start with {{ and end with }}."""


def _parse_cards(text: str, max_cards: int) -> list[dict]:
    """Pull whatever valid cards exist out of a model reply."""
    payload = extract_json(text)
    if payload is None:
        return []

    if isinstance(payload, dict):
        raw = payload.get("cards", [])
    elif isinstance(payload, list):
        raw = payload
    else:
        return []

    if not isinstance(raw, list):
        return []

    cards: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            cards.append(Card(**entry).model_dump())
        except (ValidationError, TypeError):
            logger.warning("dropping malformed card: %s", entry)
        if len(cards) >= max_cards:
            break
    return cards


def generate_cards(material: str, llm, max_cards: int = DEFAULT_MAX_CARDS) -> list[dict]:
    """Generate flashcards from study material.

    Args:
        material: The text to build cards from.
        llm: A chat model exposing ``.invoke(messages)``. Injected so tests fake it.
        max_cards: Upper bound on returned cards.

    Returns:
        Validated ``{"front", "back", "topic"}`` dicts — possibly empty, never None.
        Never raises: an unusable model reply degrades to an empty deck, which the
        caller surfaces to the learner as a warning.
    """
    if not material or not material.strip():
        return []

    system = CARD_GEN_PROMPT.format(max_cards=max_cards)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Study material:\n\n{material}"},
    ]

    for attempt in range(2):
        if attempt == 1:
            # Retry once with an explicitly stricter instruction.
            messages = [
                {"role": "system", "content": system + _RETRY_SUFFIX},
                {"role": "user", "content": f"Study material:\n\n{material}"},
            ]
        try:
            response = llm.invoke(messages)
        except Exception:
            logger.exception("card generation LLM call failed")
            return []

        cards = _parse_cards(message_text(response), max_cards)
        if cards:
            return cards
        logger.warning("card generation produced no valid cards (attempt %d)", attempt + 1)

    return []
