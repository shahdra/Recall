"""tutor-agent: Recall's orchestrator and HTTP API.

Three things meet here. The **orchestrator** runs the hand-written ReAct loop over
the tools it can reach. The two **sub-agents** (Card-Generator and Grader) are
exposed to that loop *as tools*, so the orchestrator decides when to generate
cards or grade an answer without knowing how either works. And **study-mcp**'s
tools are discovered over MCP at startup and added to the same registry.

The deck and session endpoints drive that machinery directly rather than through
the LLM: creating a deck is a fixed pipeline (parse, store, generate, persist),
and a fixed pipeline should not depend on a model choosing to follow it. ``/chat``
is where the orchestrator gets to reason freely.

Every external call is wrapped, and errors come back as
``{error, code, request_id}`` — never a traceback.
"""

import base64
import binascii
import logging
import os
import uuid
from contextlib import asynccontextmanager

import boto3
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agent_loop import arun_agent
from card_generator import generate_cards
from grader import grade_answer
from ingest import IngestError, extract_text
from llm import build_llm
from voice import VoiceUnavailable, build_client, transcribe

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
S3_BUCKET = os.environ.get("RECALL_S3_BUCKET", "")
STUDY_MCP_URL = os.environ.get("STUDY_MCP_URL", "http://study-mcp:9000/mcp")

# Populated at startup. Module-level so tests can replace them wholesale.
LLM = None
MCP_TOOLS: dict = {}
VOICE_CLIENT = None

# Task 3.7 replaces this with prompts.py plus learner-profile injection.
ORCHESTRATOR_PROMPT = """You are Recall, a patient and encouraging study tutor.

You quiz students on their own material using spaced repetition. You explain why
an answer is right or wrong. You never simply hand over answers to homework, and
you keep replies short and plain — no markdown.

Use your tools to look up what is due, to grade answers, and to check progress.
Never guess at a student's data: call a tool and read it."""


def _s3_client():
    return boto3.client("s3", region_name=AWS_REGION)


# --- sub-agents exposed to the orchestrator as tools --------------------------


@tool
def generate_cards_tool(material: str) -> str:
    """Turn study material into flashcards. Returns the cards as JSON."""
    cards = generate_cards(material, LLM)
    return str(cards)


@tool
def grade_answer_tool(question: str, correct_answer: str, student_answer: str) -> str:
    """Grade a student's answer against the correct one. Returns a JSON verdict."""
    return str(grade_answer(question, correct_answer, student_answer, LLM))


# LangChain derives a tool's name from the function name; the plan pins the names
# the orchestrator sees, so set them explicitly.
generate_cards_tool.name = "generate_cards"
grade_answer_tool.name = "grade_answer"

SUB_AGENT_TOOLS = {
    "generate_cards": generate_cards_tool,
    "grade_answer": grade_answer_tool,
}


async def _discover_mcp_tools() -> dict:
    """Discover study-mcp's tools over MCP.

    A failure here is logged and swallowed: the agent runs with a reduced toolset
    rather than refusing to start, so ``/health`` stays up and the operator can
    see what is missing.
    """
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        client = MultiServerMCPClient(
            {"study": {"url": STUDY_MCP_URL, "transport": "streamable_http"}}
        )
        tools = await client.get_tools()
        discovered = {t.name: t for t in tools}
        logger.info("discovered %d study-mcp tools: %s",
                    len(discovered), sorted(discovered))
        return discovered
    except Exception:
        logger.exception(
            "could not reach study-mcp at %s; running with reduced tools", STUDY_MCP_URL
        )
        return {}


def _build_llm_or_none():
    """Build the chat model, or None if no model is reachable.

    Returning None rather than raising keeps the process alive so ``/health`` can
    report the problem — a pod that crash-loops tells an operator far less.
    """
    try:
        return build_llm()
    except Exception:
        logger.exception("could not initialize any language model")
        return None


def _build_voice_or_none():
    """Build the Deepgram client, or None if voice is unavailable."""
    try:
        client = build_client()
        logger.info("voice transcription enabled")
        return client
    except VoiceUnavailable:
        logger.warning("DEEPGRAM_API_KEY not set; voice answers disabled")
    except Exception:
        logger.exception("voice client failed to initialize; voice answers disabled")
    return None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global LLM, MCP_TOOLS, VOICE_CLIENT

    LLM = _build_llm_or_none()
    MCP_TOOLS = await _discover_mcp_tools()
    VOICE_CLIENT = _build_voice_or_none()

    yield


app = FastAPI(title="Recall tutor-agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- error handling -----------------------------------------------------------


class ApiError(Exception):
    """An error we can explain to the user."""

    def __init__(self, message: str, status_code: int = 400, code: str = "bad_request"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


@app.exception_handler(ApiError)
async def _api_error_handler(request: Request, exc: ApiError):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.message,
            "code": exc.code,
            "request_id": request.headers.get("x-request-id", str(uuid.uuid4())),
        },
    )


@app.exception_handler(Exception)
async def _unhandled_error_handler(request: Request, exc: Exception):
    """Log the detail, tell the user nothing internal."""
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
    logger.exception("unhandled error (request_id=%s)", request_id)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Something went wrong on our end. Please try again.",
            "code": "internal_error",
            "request_id": request_id,
        },
    )


def _require_tool(name: str):
    tool_obj = MCP_TOOLS.get(name)
    if tool_obj is None:
        raise ApiError(
            "The study service is unavailable right now. Please try again shortly.",
            status_code=503,
            code="mcp_unavailable",
        )
    return tool_obj


def _unwrap_tool_result(raw):
    """Normalize an MCP tool result into a Python object.

    MCP returns content blocks — ``[{"type": "text", "text": "{...}"}]`` — rather
    than a bare value, so unwrap the text block and parse it. Plain dicts and JSON
    strings are passed through so fakes and future adapters both work.
    """
    import json

    if isinstance(raw, dict):
        return raw

    if isinstance(raw, list):
        texts = [
            block.get("text", "")
            for block in raw
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        if not texts:
            return raw  # a list of real values, not content blocks
        raw = "\n".join(texts)

    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            logger.warning("tool returned non-JSON text: %r", raw[:200])
            return {}

    return {}


async def _call_tool(name: str, **kwargs) -> dict:
    """Call an MCP tool and parse its result.

    Async because MCP tools from langchain-mcp-adapters are async-only: their
    sync ``invoke`` raises ``NotImplementedError``.
    """
    tool_obj = _require_tool(name)
    try:
        raw = await tool_obj.ainvoke(kwargs)
    except Exception as exc:
        logger.exception("study-mcp tool %s failed", name)
        raise ApiError(
            "The study service had trouble with that. Please try again.",
            status_code=502,
            code="mcp_error",
        ) from exc
    return _unwrap_tool_result(raw)


def _decode_b64(value: str, what: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ApiError(f"That {what} wasn't valid base64.", code="bad_encoding") from exc


# --- request models -----------------------------------------------------------


class DeckRequest(BaseModel):
    user_id: str
    title: str
    text: str | None = None
    file_b64: str | None = None
    content_type: str | None = None


class SessionStartRequest(BaseModel):
    user_id: str
    deck_id: str | None = None


class AnswerRequest(BaseModel):
    user_id: str
    deck_id: str
    card_id: str
    card_front: str
    card_back: str
    student_answer: str = Field(default="")


class ChatRequest(BaseModel):
    user_id: str
    message: str


class TranscribeRequest(BaseModel):
    audio_b64: str


# --- endpoints ----------------------------------------------------------------


@app.get("/health")
async def health():
    """Liveness/readiness probe. Reports dependency state for debugging."""
    return {
        "status": "ok",
        "llm": LLM is not None,
        "mcp_tools": len(MCP_TOOLS),
        "voice": VOICE_CLIENT is not None,
    }


@app.post("/decks")
async def create_deck(request: DeckRequest):
    """Ingest material, store the original in S3, and generate a deck of cards."""
    if not request.text and not request.file_b64:
        raise ApiError("Send either some text or a file to make cards from.")

    source_s3_key = None

    if request.file_b64:
        data = _decode_b64(request.file_b64, "file")
        try:
            material = extract_text(data, request.content_type or "")
        except IngestError as exc:
            # The message is already written for the learner.
            raise ApiError(str(exc), code="bad_upload") from exc

        if S3_BUCKET:
            source_s3_key = f"uploads/{request.user_id}/{uuid.uuid4()}"
            try:
                _s3_client().put_object(
                    Bucket=S3_BUCKET,
                    Key=source_s3_key,
                    Body=data,
                    ContentType=request.content_type or "application/octet-stream",
                )
            except Exception:
                # Losing the archive copy should not cost the learner their deck.
                logger.exception("could not archive upload to S3")
                source_s3_key = None
    else:
        material = request.text

    deck = await _call_tool(
        "create_deck",
        user_id=request.user_id,
        title=request.title,
        source_s3_key=source_s3_key,
    )
    deck_id = deck.get("deck_id")

    cards = generate_cards(material, LLM)
    for card in cards:
        await _call_tool(
            "add_card",
            deck_id=deck_id,
            user_id=request.user_id,
            front=card["front"],
            back=card["back"],
            topic=card.get("topic", "general"),
        )

    response = {
        "deck_id": deck_id,
        "card_count": len(cards),
        "source_s3_key": source_s3_key,
    }
    if not cards:
        response["warning"] = (
            "I couldn't make cards from that material. Try a longer or clearer "
            "excerpt."
        )
    return response


@app.post("/session/start")
async def session_start(request: SessionStartRequest):
    """Begin a study session: read the learner's profile and fetch what is due."""
    profile = await _call_tool("get_profile", user_id=request.user_id)
    due = await _call_tool("get_due_cards", user_id=request.user_id)
    cards = due.get("cards", [])

    response = {"cards": cards, "profile": profile}
    if not cards:
        response["message"] = "Nothing due right now — want to study ahead?"
    return response


@app.post("/session/answer")
async def session_answer(request: AnswerRequest):
    """Grade an answer and reschedule the card.

    The Grader assigns a quality 0-5; ``grade_card`` in study-mcp does the SM-2
    arithmetic. Neither this endpoint nor the LLM computes a date.
    """
    verdict = grade_answer(
        request.card_front, request.card_back, request.student_answer, LLM
    )

    schedule = await _call_tool(
        "grade_card",
        deck_id=request.deck_id,
        card_id=request.card_id,
        quality=verdict["quality"],
    )

    return {
        "is_correct": verdict["is_correct"],
        "explanation": verdict["explanation"],
        "quality": verdict["quality"],
        "interval_days": schedule.get("interval_days"),
        "due_date": schedule.get("due_date"),
    }


@app.post("/chat")
async def chat(request: ChatRequest):
    """Free-form tutoring turn, run through the orchestrator's ReAct loop."""
    tools = {**SUB_AGENT_TOOLS, **MCP_TOOLS}
    messages = [
        {"role": "system", "content": ORCHESTRATOR_PROMPT},
        {"role": "user", "content": f"[user_id: {request.user_id}] {request.message}"},
    ]

    result = await arun_agent(messages, LLM, tools)

    return {
        "response": result["response"],
        "iterations": result["iterations"],
        "tools_called": result["tools_called"],
        "capped": result["capped"],
        "llm_failed": result["llm_failed"],
    }


@app.post("/transcribe")
async def transcribe_audio(request: TranscribeRequest):
    """Transcribe a spoken answer. Returns empty text rather than failing."""
    audio = _decode_b64(request.audio_b64, "audio")

    if VOICE_CLIENT is None:
        return {
            "text": "",
            "message": "Voice input isn't available right now — please type your answer.",
        }

    text = transcribe(audio, VOICE_CLIENT)
    response = {"text": text}
    if not text:
        response["message"] = "I couldn't make that out — please type your answer."
    return response


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), log_config=None
    )
