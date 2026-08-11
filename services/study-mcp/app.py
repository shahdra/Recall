"""study-mcp: Recall's own MCP server.

Exposes deck, card, scheduling, progress, and learner-memory tools to the
tutor-agent over MCP. Each tool is a thin wrapper over a ``_``-prefixed logic
function, so unit tests exercise the logic directly while the Phase 4
integration suite drives the same code through the real MCP transport.

The LLM chooses which tool to call and with what arguments; everything that
touches DynamoDB or does SM-2 arithmetic happens here, in Python.
"""

import os
import uuid
from datetime import date, timedelta

import boto3
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

import sm2
import storage

MAX_QUALITY = 5
MIN_QUALITY = 0

DEMO_MODE = os.environ.get("RECALL_DEMO_MODE", "").lower() in ("1", "true", "yes")
"""Enables the clock controls below, which let a demo skip the days a real
spaced-repetition schedule would take to play out.

Off unless explicitly switched on, so production gets the real clock simply by
not setting the variable. The gate lives in the tools themselves rather than only
in the UI: this shifts time for *every* user of the process, which is fine for a
laptop demo and wrong anywhere shared.
"""

MAX_CLOCK_ADVANCE_DAYS = 365
"""Cap on a single advance. Guards against a fat-fingered 100000 pushing every
card so far out that the demo database is effectively unusable."""

_clock_offset_days = 0
"""Days the simulated date runs ahead of the real one. In-memory on purpose: a
restart returns to real time, so a forgotten demo cannot skew things forever."""

mcp = FastMCP("study-mcp")


def _build_tables() -> storage.Tables:
    """Build the Tables handle from the environment.

    Deferred behind a function so importing this module needs no AWS
    credentials — tests replace ``TABLES`` before any call is made.
    """
    # Prefer the DynamoDB-scoped override. The unscoped AWS_ENDPOINT_URL applies
    # to *every* AWS service in boto3, so setting it for DynamoDB Local also
    # redirects Bedrock — the tutor-agent then sends model requests to the local
    # database and gets an opaque InternalFailure.
    endpoint = (
        os.environ.get("AWS_ENDPOINT_URL_DYNAMODB")
        or os.environ.get("RECALL_DYNAMODB_ENDPOINT")
        or None
    )
    return storage.Tables(
        resource=boto3.resource(
            "dynamodb",
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
            endpoint_url=endpoint,
        ),
        cards=os.environ.get("RECALL_CARDS_TABLE", "Cards"),
        decks=os.environ.get("RECALL_DECKS_TABLE", "Decks"),
        profiles=os.environ.get("RECALL_PROFILE_TABLE", "LearnerProfile"),
    )


class _LazyTables:
    """Stands in for a Tables handle until something actually needs AWS.

    Without this, importing the module would construct a boto3 resource at
    import time and fail in any environment without credentials.
    """

    def __init__(self):
        self._real: storage.Tables | None = None

    def _resolve(self) -> storage.Tables:
        if self._real is None:
            self._real = _build_tables()
        return self._real

    def __getattr__(self, name):
        return getattr(self._resolve(), name)


TABLES = _LazyTables()


def _today_iso() -> str:
    """Today, or the simulated date when a demo has advanced the clock.

    Every date in the service resolves through here — due-card queries, new
    cards, grade scheduling, review timestamps — so one offset moves all of them
    together and the schedule stays internally consistent.
    """
    return (date.today() + timedelta(days=_clock_offset_days)).isoformat()


def _clock_state() -> dict:
    """The clock's current state, shared by both demo tools and the health route."""
    return {
        "demo_mode": DEMO_MODE,
        "offset_days": _clock_offset_days,
        "simulated_date": _today_iso(),
        "real_date": date.today().isoformat(),
    }


def _advance_clock(days: int = 1) -> dict:
    """Move the simulated date forward by whole days."""
    global _clock_offset_days
    if not DEMO_MODE:
        # An error payload rather than an exception: a disabled backdoor should
        # be inert and legible, not a 500 that looks like a broken service.
        return {"error": "demo mode is disabled", **_clock_state()}
    days = int(days)
    # Rewinding is reset_clock's job. A negative offset would make already-graded
    # cards un-due and read as the scheduler losing reviews.
    if days < 1 or days > MAX_CLOCK_ADVANCE_DAYS:
        return {
            "error": f"days must be between 1 and {MAX_CLOCK_ADVANCE_DAYS}",
            **_clock_state(),
        }
    _clock_offset_days += days
    return _clock_state()


def _reset_clock() -> dict:
    """Return to the real date so a demo can be re-run from a clean slate."""
    global _clock_offset_days
    if not DEMO_MODE:
        return {"error": "demo mode is disabled", **_clock_state()}
    _clock_offset_days = 0
    return _clock_state()


def _plus_days(days: int) -> str:
    """Offset from _today_iso, not from date.today().

    Deriving both from one source keeps them consistent if the clock rolls past
    midnight mid-request, and lets tests freeze the date in one place.
    """
    return (date.fromisoformat(_today_iso()) + timedelta(days=days)).isoformat()


# --- Tool logic ---------------------------------------------------------------


def _create_deck(user_id: str, title: str, source_s3_key=None, deck_id=None) -> dict:
    deck_id = deck_id or str(uuid.uuid4())
    storage.put_deck(TABLES, user_id, deck_id, title, source_s3_key, 0, _today_iso())
    return {"deck_id": deck_id}


def _add_card(deck_id, user_id, front, back, topic, card_id=None) -> dict:
    """Add a card, due immediately so a freshly made deck is studyable at once."""
    card_id = card_id or str(uuid.uuid4())
    storage.put_card(
        TABLES,
        deck_id,
        card_id,
        front,
        back,
        topic,
        due_date=_today_iso(),
        user_id=user_id,
    )
    return {"card_id": card_id}


def _get_due_cards(user_id: str) -> dict:
    return {"cards": storage.query_due_cards(TABLES, user_id, _today_iso())}


def _grade_card(deck_id: str, card_id: str, quality: int) -> dict:
    """Record a review and reschedule via SM-2.

    Quality is clamped rather than rejected: a bad grade from the LLM should
    still produce a sane schedule instead of a 500.
    """
    quality = max(MIN_QUALITY, min(MAX_QUALITY, int(quality)))
    card = storage.get_card(TABLES, deck_id, card_id)

    result = sm2.schedule(
        ease_factor=card["ease_factor"],
        interval_days=card["interval_days"],
        repetitions=card["repetitions"],
        quality=quality,
    )
    due = _plus_days(result["interval_days"])

    storage.update_card_schedule(
        TABLES,
        deck_id,
        card_id,
        ease_factor=result["ease_factor"],
        interval_days=result["interval_days"],
        repetitions=result["repetitions"],
        due_date=due,
        history_entry={
            "ts": _today_iso(),
            "grade": quality,
            "was_correct": quality >= sm2.PASSING_GRADE,
        },
    )
    return {
        "interval_days": result["interval_days"],
        "due_date": due,
        "ease_factor": round(result["ease_factor"], 3),
    }


def _list_decks(user_id: str) -> dict:
    return {"decks": storage.list_decks(TABLES, user_id)}


def _get_progress(user_id: str) -> dict:
    """Aggregate review history across a learner's decks.

    Accuracy and per-topic miss rates are computed from card history rather than
    stored counters, so the numbers can never drift from the underlying reviews.
    """
    reviews = 0
    correct = 0
    topic_totals: dict[str, int] = {}
    topic_misses: dict[str, int] = {}

    for deck in storage.list_decks(TABLES, user_id):
        for card in storage.query_cards_by_deck(TABLES, deck["deck_id"]):
            topic = card.get("topic", "untagged")
            for entry in card.get("history", []):
                reviews += 1
                topic_totals[topic] = topic_totals.get(topic, 0) + 1
                if entry.get("was_correct"):
                    correct += 1
                else:
                    topic_misses[topic] = topic_misses.get(topic, 0) + 1

    weak_topics = {
        topic: topic_misses[topic] / topic_totals[topic]
        for topic in topic_misses
        if topic_totals[topic] > 0
    }
    return {
        "total_reviews": reviews,
        "accuracy": (correct / reviews) if reviews else 0.0,
        "weak_topics": weak_topics,
    }


def _get_profile(user_id: str) -> dict:
    return storage.get_profile(TABLES, user_id)


def _update_profile(user_id: str, notes=None, weak_topics=None, preferences=None,
                    stats=None) -> dict:
    """Merge fields into a learner's profile, leaving unspecified fields intact."""
    profile = storage.get_profile(TABLES, user_id)
    if notes is not None:
        profile["notes"] = notes
    if weak_topics is not None:
        profile["weak_topics"] = weak_topics
    if preferences is not None:
        profile["preferences"] = {**profile.get("preferences", {}), **preferences}
    if stats is not None:
        profile["stats"] = {**profile.get("stats", {}), **stats}
    storage.put_profile(TABLES, user_id, profile)
    return {"ok": True}


# --- MCP tool surface (what the agent discovers) ------------------------------


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    """Liveness/readiness probe for Kubernetes."""
    return JSONResponse({"status": "ok", "clock": _clock_state()})


@mcp.tool
def create_deck(user_id: str, title: str, source_s3_key: str | None = None) -> dict:
    """Create a new empty study deck for a user."""
    return _create_deck(user_id, title, source_s3_key)


@mcp.tool
def add_card(deck_id: str, user_id: str, front: str, back: str, topic: str) -> dict:
    """Add a flashcard to a deck. Initializes SM-2 state and makes it due today."""
    return _add_card(deck_id, user_id, front, back, topic)


@mcp.tool
def get_due_cards(user_id: str) -> dict:
    """Return the cards currently due for review for a user."""
    return _get_due_cards(user_id)


@mcp.tool
def grade_card(deck_id: str, card_id: str, quality: int) -> dict:
    """Record a review grade (0-5) and reschedule the card via SM-2."""
    return _grade_card(deck_id, card_id, quality)


@mcp.tool
def list_decks(user_id: str) -> dict:
    """List all of a user's study decks."""
    return _list_decks(user_id)


@mcp.tool
def get_progress(user_id: str) -> dict:
    """Report a learner's review count, accuracy, and weakest topics."""
    return _get_progress(user_id)


@mcp.tool
def get_profile(user_id: str) -> dict:
    """Read a learner's long-term profile (weak topics, preferences, notes)."""
    return _get_profile(user_id)


@mcp.tool
def update_profile(
    user_id: str,
    notes: str | None = None,
    weak_topics: dict | None = None,
    preferences: dict | None = None,
    stats: dict | None = None,
) -> dict:
    """Update a learner's long-term profile. Only the fields given are changed."""
    return _update_profile(user_id, notes, weak_topics, preferences, stats)


# --- Demo-only clock controls -------------------------------------------------
#
# Registered only when RECALL_DEMO_MODE is set, so in production these tools do
# not exist: the agent never discovers them, the LLM cannot be talked into
# calling one, and the tutor-agent reports demo_mode=false because the tool is
# absent. The gate inside _advance_clock/_reset_clock is the second layer, for
# the case where the tools are registered but the flag is later read as false.
if DEMO_MODE:

    @mcp.tool
    def advance_clock(days: int = 1) -> dict:
        """Move the simulated date forward (demo only).

        Skips the wait a spaced-repetition schedule needs to become visible:
        advancing past a card's due date makes it genuinely due, via the same
        query the real clock drives.
        """
        return _advance_clock(days)

    @mcp.tool
    def reset_clock() -> dict:
        """Return the simulated date to the real one (demo only)."""
        return _reset_clock()

    @mcp.tool
    def get_clock() -> dict:
        """Read the simulated date without changing it (demo only)."""
        return _clock_state()


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "9000")),
    )
