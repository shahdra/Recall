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
from prompts import card_gen_prompt

logger = logging.getLogger(__name__)

DEFAULT_MAX_CARDS = 40

TOPIC_REQUEST_MAX_CHARS = 200
"""Above this, treat the input as study material even if it reads like a request.

A "teach me X" ask is a phrase; real material is paragraphs. The length test is
what makes the two distinguishable without a UI flag, and it is deliberately
generous: misreading material as a topic request would let the model invent facts
outside the student's syllabus, so only clearly-short input qualifies."""

_TEACH_ME_PATTERNS = (
    "teach me",
    "teach us",
    "explain",
    "i want to learn",
    "i want to study",
    "help me learn",
    "help me study",
    "quiz me on",
    "test me on",
    "cards about",
    "cards on",
    "flashcards about",
    "flashcards on",
    "introduction to",
    "intro to",
    "basics of",
    "fundamentals of",
    "learn about",
)


def is_topic_request(material: str) -> bool:
    """True when the student named a subject rather than supplying material.

    Two signals must agree: the text is short enough to be a request rather than
    a source, and it either asks to be taught or is a bare subject name. Pasted
    notes that merely happen to contain the word "explain" stay grounded because
    they fail the length test.
    """
    text = (material or "").strip()
    if not text or len(text) > TOPIC_REQUEST_MAX_CHARS:
        return False

    lowered = text.lower()
    if any(pattern in lowered for pattern in _TEACH_ME_PATTERNS):
        return True

    # A bare subject name ("Kubernetes networking", "Ottoman tax reform") carries
    # no facts to build cards from, so it can only be a topic request. Sentence
    # punctuation implies prose, which is material however short.
    return len(text.split()) <= 6 and not any(mark in text for mark in ".!?;:")


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


_RETRY_SUFFIX = """

Your previous reply could not be parsed as JSON. Reply with ONLY the raw JSON
object. No prose, no markdown fences, no explanation. Start with {{ and end with }}."""

_MIN_RETRY_CARDS = 10
"""Floor for the truncation retry. Below this the deck is too thin to be worth
studying, so a model that cannot emit ten cards has a different problem."""

_TRUNCATION_MIN_CHARS = 500
"""A reply this long that still parses to nothing was almost certainly cut off
mid-JSON rather than malformed from the start."""


def _looks_truncated(text: str) -> bool:
    """True when an unparseable reply looks like it ran out of output tokens."""
    stripped = (text or "").strip().rstrip("`").rstrip()
    if len(stripped) < _TRUNCATION_MIN_CHARS:
        return False
    # Complete JSON ends with its closing brace or bracket; a cut-off reply stops
    # mid-string or mid-object.
    return not stripped.endswith(("}", "]"))


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


def generate_cards(
    material: str,
    llm,
    max_cards: int = DEFAULT_MAX_CARDS,
    grounded: bool | None = None,
) -> list[dict]:
    """Generate flashcards from study material, or from a named subject.

    Args:
        material: Study material to build cards from, or a subject to be taught
            ("teach me Kubernetes", "Ottoman tax reform").
        llm: A chat model exposing ``.invoke(messages)``. Injected so tests fake it.
        max_cards: Upper bound on returned cards.
        grounded: Force the mode. True keeps the cards inside ``material``; False
            lets the model teach from its own knowledge. Defaults to detecting it
            with :func:`is_topic_request`.

    Returns:
        Validated ``{"front", "back", "topic"}`` dicts — possibly empty, never None.
        Never raises: an unusable model reply degrades to an empty deck, which the
        caller surfaces to the learner as a warning.
    """
    if not material or not material.strip():
        return []

    if grounded is None:
        grounded = not is_topic_request(material)

    system = card_gen_prompt(max_cards, grounded=grounded)
    # Label the input for what it is. Calling a bare "teach me Kubernetes" study
    # material invites the model to make cards *about the request* rather than
    # about the subject.
    user = (
        f"Study material:\n\n{material}"
        if grounded
        else f"Subject to teach:\n\n{material}"
    )
    logger.info(
        "generating up to %d cards (%s)",
        max_cards,
        "grounded in material" if grounded else "from model knowledge",
    )

    requested = max_cards
    for attempt in range(3):
        system_now = system if attempt == 0 else system + _RETRY_SUFFIX
        try:
            response = llm.invoke(
                [
                    {"role": "system", "content": system_now},
                    {"role": "user", "content": user},
                ]
            )
        except Exception:
            logger.exception("card generation LLM call failed")
            return []

        text = message_text(response)
        cards = _parse_cards(text, max_cards)
        if cards:
            return cards

        # A long reply that yields nothing is the signature of a response cut off
        # at max_tokens: the JSON never closes, so it cannot be parsed. Asking for
        # a smaller deck is what makes it fit. Without this, a large max_cards
        # silently returns an empty deck on models with a low output cap.
        if _looks_truncated(text) and requested > _MIN_RETRY_CARDS:
            requested = max(_MIN_RETRY_CARDS, requested // 2)
            system = card_gen_prompt(requested, grounded=grounded)
            max_cards = requested
            logger.warning(
                "reply looks truncated (%d chars); retrying with max_cards=%d",
                len(text),
                requested,
            )
            continue

        logger.warning("card generation produced no valid cards (attempt %d)", attempt + 1)

    return []
