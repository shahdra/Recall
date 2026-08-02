# Recall Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Repo note:** Recall is a **new, standalone repository**. PolyAIFursa is a
> reference only — copy and adapt patterns from it (manual ReAct loop, FastAPI +
> Prometheus, FastMCP server, K8s manifests, ArgoCD, CI/CD, Grafana) but build
> Recall fresh. All paths below are relative to the new Recall repo root.

**Goal:** Build Recall — a multi-agent AI study tutor that generates flashcards from uploaded material, quizzes the learner (text or voice), grades answers, and schedules reviews with SM-2 spaced repetition — deployed on Kubernetes/EC2 with Terraform-provisioned AWS, CI/CD, and observability.

**Architecture:** Three containerized services — `tutor-agent` (orchestrator with a manual ReAct loop hosting Card-Generator and Grader sub-agents), `study-mcp` (own MCP server exposing deck/card/SM-2/progress/memory tools over DynamoDB + S3), and `frontend` (web UI). The LLM only decides and picks tool arguments; Python code performs all I/O; SM-2 scheduling is deterministic and runs outside the LLM.

**Tech Stack:** Python 3.11, FastAPI, LangChain (`init_chat_model`, manual ReAct loop), FastMCP, `langchain-mcp-adapters`, boto3 (S3/DynamoDB/SNS), `moto` (AWS mocks in tests), `pypdf` (PDF parsing), Deepgram SDK (speech-to-text), `prometheus-fastapi-instrumentator`, pytest, Docker, Kubernetes (on EC2), Terraform, ArgoCD, GitHub Actions, Prometheus + Grafana.

## Global Constraints

- **Language/runtime:** Python 3.11 for all backend services.
- **No black-box agent frameworks.** Implement the ReAct loop manually. Do NOT use `create_react_agent`, `AgentExecutor`, or equivalent wrappers.
- **The LLM never performs I/O or SM-2 math.** It only chooses tools and arguments. All DynamoDB/S3/SNS/Deepgram calls are Python; SM-2 arithmetic is a pure function.
- **TDD:** every code task writes a failing test first, then the minimal implementation. Commit after each task.
- **All AWS resources via Terraform.** No manual console clicking.
- **Two environments:** `dev` and `prod` K8s namespaces with separate config.
- **Model config:** model id comes from a `MODEL` env var; a `FALLBACK_MODEL` env var names the secondary. `temperature=0`.
- **AWS region default:** `us-east-1`. Never hard-code account-specific ARNs; read resource names from env (`RECALL_DECKS_TABLE`, `RECALL_CARDS_TABLE`, `RECALL_PROFILE_TABLE`, `RECALL_S3_BUCKET`, `RECALL_SNS_TOPIC_ARN`).
- **AWS account is shared with the course.** Tag every Terraform resource with `Project=recall` and never delete resources by broad/common tags.

---

## Phase 0 — Repo scaffold & CI skeleton

Produces: a new repo that installs, lints, and runs an empty test suite green in CI.

### Task 0.1: Initialize repository structure

**Files:**
- Create: `README.md`, `.gitignore`, `docs/spec.md` (copy from brainstorm), `docs/plan.md` (this file)
- Create: `services/study-mcp/`, `services/tutor-agent/`, `services/frontend/` (empty package dirs with `__init__.py`)
- Create: `pyproject.toml` or per-service `requirements.txt`

- [ ] **Step 1: Create the directory tree and placeholder files**

```
recall/
  README.md
  .gitignore                # Python, .venv, .env, __pycache__, .coverage
  docs/spec.md
  docs/plan.md
  services/
    study-mcp/{app.py,requirements.txt,__init__.py,tests/}
    tutor-agent/{app.py,requirements.txt,__init__.py,tests/}
    frontend/
```

- [ ] **Step 2: Write each service's `requirements.txt`**

`services/study-mcp/requirements.txt`:
```
fastmcp
boto3
pydantic
pytest
moto[dynamodb,s3]
httpx
```

`services/tutor-agent/requirements.txt`:
```
fastapi
uvicorn
langchain
langchain-core
langchain-mcp-adapters
boto3
pypdf
openai
prometheus-fastapi-instrumentator
prometheus-client
pydantic
python-dotenv
httpx
pytest
moto[dynamodb,s3]
```

- [ ] **Step 3: Commit**

```bash
git init && git add -A && git commit -m "chore: scaffold Recall repo structure"
```

### Task 0.2: CI skeleton (tests run on PR)

**Files:**
- Create: `.github/workflows/test.yaml`

**Interfaces:**
- Produces: a CI job that installs each service's deps and runs `pytest`. Later phases add build/deploy workflows.

- [ ] **Step 1: Write the workflow** (adapt PolyAI's `test.yaml`)

```yaml
name: tests
on: [pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        service: [study-mcp, tutor-agent]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -r services/${{ matrix.service }}/requirements.txt
      - run: pytest services/${{ matrix.service }} -v
```

- [ ] **Step 2: Add a trivial passing test per service so CI is green**

`services/study-mcp/tests/test_smoke.py`:
```python
def test_smoke():
    assert True
```
(same for `tutor-agent`)

- [ ] **Step 3: Verify locally**

Run: `pytest services/study-mcp services/tutor-agent -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add .github services/*/tests/test_smoke.py && git commit -m "ci: add test workflow skeleton"
```

---

## Phase 1 — SM-2 algorithm (pure, deterministic)

Produces: a fully unit-tested scheduling function with zero external dependencies. This is the core "measurable value" and the easiest thing to test, so it goes first.

### Task 1.1: Implement and test the SM-2 scheduler

**Files:**
- Create: `services/study-mcp/sm2.py`
- Test: `services/study-mcp/tests/test_sm2.py`

**Interfaces:**
- Produces: `def schedule(ease_factor: float, interval_days: int, repetitions: int, quality: int) -> dict` returning `{"ease_factor": float, "interval_days": int, "repetitions": int}`. Callers add `due_date = today + interval_days`.

- [ ] **Step 1: Write failing tests**

```python
from sm2 import schedule

def test_wrong_answer_resets_interval_to_one():
    out = schedule(ease_factor=2.5, interval_days=15, repetitions=3, quality=1)
    assert out["interval_days"] == 1
    assert out["repetitions"] == 0

def test_first_correct_sets_interval_to_one():
    out = schedule(ease_factor=2.5, interval_days=0, repetitions=0, quality=5)
    assert out["interval_days"] == 1
    assert out["repetitions"] == 1

def test_second_correct_sets_interval_to_six():
    out = schedule(ease_factor=2.5, interval_days=1, repetitions=1, quality=5)
    assert out["interval_days"] == 6
    assert out["repetitions"] == 2

def test_third_correct_multiplies_by_ease():
    out = schedule(ease_factor=2.5, interval_days=6, repetitions=2, quality=5)
    assert out["interval_days"] == 15   # round(6 * 2.5)
    assert out["repetitions"] == 3

def test_ease_factor_floored_at_1_3():
    out = schedule(ease_factor=1.3, interval_days=1, repetitions=1, quality=0)
    assert out["ease_factor"] >= 1.3

def test_ease_increases_on_perfect_recall():
    out = schedule(ease_factor=2.5, interval_days=6, repetitions=2, quality=5)
    assert out["ease_factor"] > 2.5
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest services/study-mcp/tests/test_sm2.py -v`
Expected: FAIL (`ModuleNotFoundError: sm2`)

- [ ] **Step 3: Implement**

```python
# services/study-mcp/sm2.py
def schedule(ease_factor: float, interval_days: int, repetitions: int, quality: int) -> dict:
    """Pure SM-2 step. quality is 0..5 (0=blank, 5=perfect). No I/O, no dates."""
    if quality < 3:
        repetitions = 0
        interval_days = 1
    else:
        if repetitions == 0:
            interval_days = 1
        elif repetitions == 1:
            interval_days = 6
        else:
            interval_days = round(interval_days * ease_factor)
        repetitions += 1

    ease_factor = ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    ease_factor = max(1.3, ease_factor)

    return {"ease_factor": ease_factor, "interval_days": interval_days, "repetitions": repetitions}
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest services/study-mcp/tests/test_sm2.py -v`
Expected: PASS (all 6)

- [ ] **Step 5: Commit**

```bash
git add services/study-mcp/sm2.py services/study-mcp/tests/test_sm2.py
git commit -m "feat(study-mcp): add SM-2 scheduling algorithm with tests"
```

---

## Phase 2 — study-mcp: storage layer & MCP tools

Produces: the own MCP server, tested with `moto` (mocked DynamoDB/S3), exposing all deck/card/scheduling/progress/memory tools.

### Task 2.1: DynamoDB/S3 storage module

**Files:**
- Create: `services/study-mcp/storage.py`
- Test: `services/study-mcp/tests/test_storage.py`

**Interfaces:**
- Produces (all take a boto3 resource/table so tests can inject moto):
  - `put_deck(tables, user_id, deck_id, title, source_s3_key, card_count, created_at) -> None`
  - `put_card(tables, deck_id, card_id, front, back, topic, due_date) -> None` (initializes SM-2 state: ease 2.5, interval 0, repetitions 0, history [])
  - `get_card(tables, deck_id, card_id) -> dict`
  - `update_card_schedule(tables, deck_id, card_id, ease_factor, interval_days, repetitions, due_date, history_entry) -> None`
  - `query_due_cards(tables, user_id, today_iso) -> list[dict]` (uses the `due_date` GSI)
  - `get_profile(tables, user_id) -> dict` / `put_profile(tables, user_id, profile) -> None`
- `tables` is a small dataclass/dict holding table names + a boto3 resource.

- [ ] **Step 1: Write failing tests (moto-mocked)**

```python
import boto3, pytest
from moto import mock_aws
import storage

@pytest.fixture
def tables():
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        cards = ddb.create_table(
            TableName="Cards",
            KeySchema=[{"AttributeName":"deck_id","KeyType":"HASH"},
                       {"AttributeName":"card_id","KeyType":"RANGE"}],
            AttributeDefinitions=[
                {"AttributeName":"deck_id","AttributeType":"S"},
                {"AttributeName":"card_id","AttributeType":"S"},
                {"AttributeName":"user_id","AttributeType":"S"},
                {"AttributeName":"due_date","AttributeType":"S"}],
            GlobalSecondaryIndexes=[{
                "IndexName":"due-index",
                "KeySchema":[{"AttributeName":"user_id","KeyType":"HASH"},
                             {"AttributeName":"due_date","KeyType":"RANGE"}],
                "Projection":{"ProjectionType":"ALL"}}],
            BillingMode="PAY_PER_REQUEST")
        yield storage.Tables(resource=ddb, cards="Cards")

def test_put_and_get_card_initializes_sm2_state(tables):
    storage.put_card(tables, "d1", "c1", "front", "back", "bio",
                     due_date="2026-01-01", user_id="u1")
    card = storage.get_card(tables, "d1", "c1")
    assert card["ease_factor"] == pytest.approx(2.5)
    assert card["repetitions"] == 0
    assert card["front"] == "front"

def test_query_due_cards_returns_only_due(tables):
    storage.put_card(tables, "d1", "c1", "f", "b", "bio", due_date="2026-01-01", user_id="u1")
    storage.put_card(tables, "d1", "c2", "f", "b", "bio", due_date="2099-01-01", user_id="u1")
    due = storage.query_due_cards(tables, "u1", today_iso="2026-06-01")
    ids = {c["card_id"] for c in due}
    assert ids == {"c1"}
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest services/study-mcp/tests/test_storage.py -v`
Expected: FAIL (`AttributeError: module 'storage' has no attribute 'Tables'`)

- [ ] **Step 3: Implement `storage.py`**

```python
# services/study-mcp/storage.py
from dataclasses import dataclass
from decimal import Decimal

@dataclass
class Tables:
    resource: object
    cards: str = "Cards"
    decks: str = "Decks"
    profiles: str = "LearnerProfile"

def _t(tables, name): return tables.resource.Table(name)

def put_card(tables, deck_id, card_id, front, back, topic, due_date, user_id):
    _t(tables, tables.cards).put_item(Item={
        "deck_id": deck_id, "card_id": card_id, "user_id": user_id,
        "front": front, "back": back, "topic": topic,
        "ease_factor": Decimal("2.5"), "interval_days": 0, "repetitions": 0,
        "due_date": due_date, "last_reviewed": None, "history": [],
    })

def get_card(tables, deck_id, card_id):
    item = _t(tables, tables.cards).get_item(
        Key={"deck_id": deck_id, "card_id": card_id}).get("Item")
    if item is None:
        raise KeyError(f"card {deck_id}/{card_id} not found")
    item["ease_factor"] = float(item["ease_factor"])
    return item

def update_card_schedule(tables, deck_id, card_id, ease_factor, interval_days,
                         repetitions, due_date, history_entry):
    _t(tables, tables.cards).update_item(
        Key={"deck_id": deck_id, "card_id": card_id},
        UpdateExpression=("SET ease_factor=:e, interval_days=:i, repetitions=:r, "
                          "due_date=:d, last_reviewed=:l, "
                          "history=list_append(history, :h)"),
        ExpressionAttributeValues={
            ":e": Decimal(str(ease_factor)), ":i": interval_days, ":r": repetitions,
            ":d": due_date, ":l": history_entry["ts"], ":h": [history_entry]})

def query_due_cards(tables, user_id, today_iso):
    from boto3.dynamodb.conditions import Key
    resp = _t(tables, tables.cards).query(
        IndexName="due-index",
        KeyConditionExpression=Key("user_id").eq(user_id) & Key("due_date").lte(today_iso))
    return resp.get("Items", [])

def put_deck(tables, user_id, deck_id, title, source_s3_key, card_count, created_at):
    _t(tables, tables.decks).put_item(Item={
        "user_id": user_id, "deck_id": deck_id, "title": title,
        "source_s3_key": source_s3_key, "card_count": card_count, "created_at": created_at})

def get_profile(tables, user_id):
    return _t(tables, tables.profiles).get_item(
        Key={"user_id": user_id}).get("Item", {"user_id": user_id,
        "weak_topics": {}, "preferences": {}, "stats": {}, "notes": ""})

def put_profile(tables, user_id, profile):
    profile["user_id"] = user_id
    _t(tables, tables.profiles).put_item(Item=profile)
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest services/study-mcp/tests/test_storage.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/study-mcp/storage.py services/study-mcp/tests/test_storage.py
git commit -m "feat(study-mcp): add DynamoDB/S3 storage layer with moto tests"
```

### Task 2.2: MCP server exposing the tools

**Files:**
- Create: `services/study-mcp/app.py`
- Test: `services/study-mcp/tests/test_tools.py`

**Interfaces:**
- Produces MCP tools (thin wrappers over `storage.py` + `sm2.schedule`):
  - `create_deck(user_id, title, source_s3_key=None) -> {"deck_id": str}`
  - `add_card(deck_id, user_id, front, back, topic) -> {"card_id": str}`
  - `get_due_cards(user_id) -> {"cards": [{card_id, deck_id, front, ...}]}`
  - `grade_card(deck_id, card_id, quality) -> {"interval_days": int, "due_date": str}`
  - `get_progress(user_id) -> {"accuracy": float, "total_reviews": int, "weak_topics": {...}}`
  - `list_decks(user_id) -> {"decks": [...]}`
- Also a `/health` GET route (for K8s probes).
- The tool functions call an internal `_today_iso()` helper so tests can monkeypatch the date.

- [ ] **Step 1: Write failing tests** (call the tool functions directly, moto-mocked tables injected via a module-level `TABLES` the test overrides)

```python
import app

def test_grade_card_wrong_answer_due_tomorrow(tables, monkeypatch):
    monkeypatch.setattr(app, "TABLES", tables)
    monkeypatch.setattr(app, "_today_iso", lambda: "2026-06-01")
    app._create_deck("u1", "Bio", None, deck_id="d1")
    app._add_card("d1", "u1", "Q", "A", "bio", card_id="c1")
    out = app._grade_card("d1", "c1", quality=1)
    assert out["due_date"] == "2026-06-02"   # +1 day

def test_grade_card_correct_grows_interval(tables, monkeypatch):
    monkeypatch.setattr(app, "TABLES", tables)
    monkeypatch.setattr(app, "_today_iso", lambda: "2026-06-01")
    app._create_deck("u1", "Bio", None, deck_id="d1")
    app._add_card("d1", "u1", "Q", "A", "bio", card_id="c1")
    out = app._grade_card("d1", "c1", quality=5)
    assert out["interval_days"] == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest services/study-mcp/tests/test_tools.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `app.py`** (adapt PolyAI's `img-proc-mcp/app.py` FastMCP pattern; split pure logic into `_`-prefixed functions that the MCP `@mcp.tool` wrappers call, so tests hit the logic without MCP transport)

```python
# services/study-mcp/app.py
import os, uuid
from datetime import date, timedelta
import boto3
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse
import storage, sm2

mcp = FastMCP("study-mcp")
TABLES = storage.Tables(resource=boto3.resource(
    "dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1")),
    cards=os.environ.get("RECALL_CARDS_TABLE", "Cards"),
    decks=os.environ.get("RECALL_DECKS_TABLE", "Decks"),
    profiles=os.environ.get("RECALL_PROFILE_TABLE", "LearnerProfile"))

def _today_iso() -> str: return date.today().isoformat()
def _plus_days(days: int) -> str: return (date.today() + timedelta(days=days)).isoformat()

@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})

def _create_deck(user_id, title, source_s3_key, deck_id=None):
    deck_id = deck_id or str(uuid.uuid4())
    storage.put_deck(TABLES, user_id, deck_id, title, source_s3_key, 0, _today_iso())
    return {"deck_id": deck_id}

def _add_card(deck_id, user_id, front, back, topic, card_id=None):
    card_id = card_id or str(uuid.uuid4())
    storage.put_card(TABLES, deck_id, card_id, front, back, topic,
                     due_date=_today_iso(), user_id=user_id)
    return {"card_id": card_id}

def _grade_card(deck_id, card_id, quality):
    card = storage.get_card(TABLES, deck_id, card_id)
    result = sm2.schedule(card["ease_factor"], int(card["interval_days"]),
                          int(card["repetitions"]), int(quality))
    due = _plus_days(result["interval_days"])
    storage.update_card_schedule(
        TABLES, deck_id, card_id, result["ease_factor"], result["interval_days"],
        result["repetitions"], due,
        history_entry={"ts": _today_iso(), "grade": int(quality),
                       "was_correct": quality >= 3})
    return {"interval_days": result["interval_days"], "due_date": due}

# MCP tool wrappers (what the agent discovers over MCP):
@mcp.tool
def create_deck(user_id: str, title: str, source_s3_key: str = None) -> dict:
    """Create a new empty study deck for a user."""
    return _create_deck(user_id, title, source_s3_key)

@mcp.tool
def add_card(deck_id: str, user_id: str, front: str, back: str, topic: str) -> dict:
    """Add a flashcard to a deck. Initializes SM-2 scheduling state."""
    return _add_card(deck_id, user_id, front, back, topic)

@mcp.tool
def get_due_cards(user_id: str) -> dict:
    """Return the cards currently due for review for a user."""
    return {"cards": storage.query_due_cards(TABLES, user_id, _today_iso())}

@mcp.tool
def grade_card(deck_id: str, card_id: str, quality: int) -> dict:
    """Record a review grade (0-5) and reschedule the card via SM-2."""
    return _grade_card(deck_id, card_id, quality)

if __name__ == "__main__":
    mcp.run(transport="streamable_http", host="0.0.0.0", port=9000)
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest services/study-mcp/tests/test_tools.py -v`
Expected: PASS

- [ ] **Step 5: Add `get_progress`, `list_decks`, and profile-update tools** following the same `_`-logic + `@mcp.tool` wrapper pattern; write a test for `get_progress` asserting accuracy is computed from card histories.

- [ ] **Step 6: Commit**

```bash
git add services/study-mcp/app.py services/study-mcp/tests/test_tools.py
git commit -m "feat(study-mcp): expose deck/card/SM-2 tools over MCP"
```

### Task 2.3: study-mcp Dockerfile

**Files:**
- Create: `services/study-mcp/Dockerfile`

- [ ] **Step 1: Write Dockerfile** (adapt PolyAI's agent Dockerfile)

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 9000
CMD ["python", "app.py"]
```

- [ ] **Step 2: Commit**

```bash
git add services/study-mcp/Dockerfile && git commit -m "build(study-mcp): add Dockerfile"
```

---

## Phase 3 — tutor-agent: sub-agents, manual ReAct loop, HTTP API

Produces: the orchestrator service with the two sub-agents, MCP tool discovery, and a `/chat` + deck/session HTTP API. LLM mocked in all tests.

### Task 3.1: Card-Generator sub-agent

**Files:**
- Create: `services/tutor-agent/card_generator.py`
- Test: `services/tutor-agent/tests/test_card_generator.py`

**Interfaces:**
- Produces: `def generate_cards(material: str, llm) -> list[dict]` returning validated `[{"front": str, "back": str, "topic": str}]`. `llm` is injected (a LangChain chat model) so tests pass a fake. Invalid/malformed model output triggers one retry with a stricter prompt; if still invalid, returns whatever valid cards parsed (possibly empty) — never raises.
- Uses a Pydantic model `Card(front, back, topic)` and `CardList(cards: list[Card])`.

- [ ] **Step 1: Write failing tests** (fake LLM returns canned JSON, incl. a malformed case)

```python
from card_generator import generate_cards

class FakeLLM:
    def __init__(self, responses): self.responses = list(responses); self.calls = 0
    def invoke(self, messages):
        r = self.responses[min(self.calls, len(self.responses)-1)]; self.calls += 1
        class M: content = r
        return M()

def test_generates_valid_cards():
    llm = FakeLLM(['{"cards":[{"front":"Q1","back":"A1","topic":"bio"}]}'])
    cards = generate_cards("some material", llm)
    assert cards == [{"front":"Q1","back":"A1","topic":"bio"}]

def test_malformed_then_retry_succeeds():
    llm = FakeLLM(['not json', '{"cards":[{"front":"Q","back":"A","topic":"t"}]}'])
    cards = generate_cards("m", llm)
    assert len(cards) == 1
    assert llm.calls == 2

def test_all_malformed_returns_empty_not_crash():
    llm = FakeLLM(['nope', 'still nope'])
    assert generate_cards("m", llm) == []
```

- [ ] **Step 2: Run to verify failure** → `pytest services/tutor-agent/tests/test_card_generator.py -v` → FAIL

- [ ] **Step 3: Implement** with a Pydantic schema, a JSON-extraction helper, and one stricter retry.

- [ ] **Step 4: Run to verify pass** → PASS

- [ ] **Step 5: Commit** → `git commit -m "feat(tutor-agent): add Card-Generator sub-agent with validation + retry"`

### Task 3.2: Grader sub-agent

**Files:**
- Create: `services/tutor-agent/grader.py`
- Test: `services/tutor-agent/tests/test_grader.py`

**Interfaces:**
- Produces: `def grade_answer(question: str, correct_answer: str, student_answer: str, llm) -> dict` returning `{"is_correct": bool, "explanation": str, "quality": int}` where `quality` is clamped to 0..5. If the model returns an invalid grade or malformed output, **default to `{"is_correct": False, "explanation": "...", "quality": 2}`** (safe: card resurfaces).

- [ ] **Step 1: Write failing tests**

```python
from grader import grade_answer

def test_correct_answer_high_quality():
    llm = FakeLLM(['{"is_correct":true,"explanation":"right","quality":5}'])
    out = grade_answer("Q","A","A", llm)
    assert out["is_correct"] and out["quality"] == 5

def test_invalid_grade_defaults_to_resurface():
    llm = FakeLLM(['{"is_correct":true,"quality":99}'])   # out of range
    out = grade_answer("Q","A","A", llm)
    assert out["quality"] == 2 and out["is_correct"] is False

def test_malformed_output_defaults_safely():
    llm = FakeLLM(['garbage'])
    out = grade_answer("Q","A","wrong", llm)
    assert out["quality"] == 2
```

- [ ] **Step 2: Run → FAIL**
- [ ] **Step 3: Implement** with Pydantic validation + safe default.
- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit** → `git commit -m "feat(tutor-agent): add Grader sub-agent with safe-default grading"`

### Task 3.3: PDF parsing

**Files:**
- Create: `services/tutor-agent/ingest.py`
- Test: `services/tutor-agent/tests/test_ingest.py` (+ fixture PDFs)

**Interfaces:**
- Produces: `def extract_text(data: bytes, content_type: str) -> str`. Handles `application/pdf` via `pypdf`, `text/plain` as-is. Raises `IngestError(message)` (a defined exception) with a human message on corrupt/encrypted/empty PDFs.

- [ ] **Step 1: Write failing tests** — a good PDF fixture yields text; a corrupt-bytes input raises `IngestError`.
- [ ] **Step 2: Run → FAIL**
- [ ] **Step 3: Implement** with `pypdf.PdfReader`, try/except → `IngestError`.
- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit** → `git commit -m "feat(tutor-agent): add PDF/text ingestion with error handling"`

### Task 3.4: Manual ReAct loop with iteration cap

**Files:**
- Create: `services/tutor-agent/agent_loop.py`
- Test: `services/tutor-agent/tests/test_agent_loop.py`

**Interfaces:**
- Produces: `def run_agent(messages: list, llm, tools: dict, max_iterations: int = 8) -> dict` returning `{"response": str, "iterations": int, "tools_called": list, "capped": bool}`. Mirrors PolyAI's `run_agent`: invoke LLM → if `tool_calls`, run each via `tools[name].invoke(call)`, append `ToolMessage`, loop; else return content. Stops at `max_iterations` with `capped=True`.

- [ ] **Step 1: Write failing tests** (fake LLM emits a tool call then a final answer; a second fake loops forever to prove the cap)

```python
from agent_loop import run_agent

def test_calls_tool_then_returns_answer():
    # fake LLM: first response has a tool_call, second is plain text
    ...
    out = run_agent(msgs, llm, tools)
    assert out["tools_called"] == ["get_due_cards"]
    assert "done" in out["response"]

def test_iteration_cap_prevents_runaway():
    # fake LLM always returns a tool_call
    out = run_agent(msgs, always_tool_llm, tools, max_iterations=3)
    assert out["capped"] is True
    assert out["iterations"] == 3
```

- [ ] **Step 2: Run → FAIL**
- [ ] **Step 3: Implement** the manual loop (copy structure from PolyAI `run_agent`, strip image-specific bits).
- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit** → `git commit -m "feat(tutor-agent): manual ReAct loop with iteration cap"`

### Task 3.5: LLM init with fallback model

**Files:**
- Create: `services/tutor-agent/llm.py`
- Test: `services/tutor-agent/tests/test_llm.py`

**Interfaces:**
- Produces: `def build_llm(model: str, fallback: str | None, init=init_chat_model) -> object`. Tries `init(model)`; on exception tries `init(fallback)`; if both fail, raises `RuntimeError`. `init` injected for testing.

- [ ] **Step 1: Failing tests** — primary succeeds → used; primary raises, fallback succeeds → fallback used; both raise → `RuntimeError`.
- [ ] **Step 2–4: FAIL → implement → PASS**
- [ ] **Step 5: Commit** → `git commit -m "feat(tutor-agent): LLM init with fallback model"`

### Task 3.6: FastAPI app wiring endpoints + MCP discovery + sub-agents-as-tools

**Files:**
- Create: `services/tutor-agent/app.py`
- Test: `services/tutor-agent/tests/test_app.py`

**Interfaces:**
- Consumes: `card_generator.generate_cards`, `grader.grade_answer`, `agent_loop.run_agent`, `ingest.extract_text`, `llm.build_llm`; study-mcp tools discovered via `MultiServerMCPClient` (pattern from PolyAI `app.py:165`).
- Produces HTTP endpoints:
  - `GET /health` → `{"status":"ok"}`
  - `POST /decks` body `{user_id, title, text?, file_b64?, content_type?}` → parses/stores in S3, calls `create_deck`, runs Card-Generator, persists cards via `add_card` → `{deck_id, card_count}`
  - `POST /session/start` body `{user_id, deck_id}` → reads profile, `get_due_cards` → `{cards}`
  - `POST /session/answer` body `{user_id, deck_id, card_id, student_answer}` → Grader → `grade_card` → `{is_correct, explanation, due_date}`
- Registers `generate_cards`/`grade_answer` as `@tool`s wrapping the sub-agents (sub-agents-as-tools).
- Startup: `build_llm`, discover study-mcp tools (log a warning and continue with reduced tools if unreachable — PolyAI pattern at `app.py:178`).

- [ ] **Step 1: Write failing endpoint tests** with FastAPI `TestClient`, LLM mocked and study-mcp calls mocked (moto tables or a fake MCP tool registry). Assert `POST /decks` returns a `card_count` and `POST /session/answer` returns an `is_correct` + `due_date`.
- [ ] **Step 2: Run → FAIL**
- [ ] **Step 3: Implement `app.py`** (FastAPI + CORS + the endpoints; wire sub-agents as tools; MCP discovery with graceful fallback).
- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit** → `git commit -m "feat(tutor-agent): FastAPI endpoints, MCP discovery, sub-agents-as-tools"`

### Task 3.7: System prompts + long-term memory injection

**Files:**
- Modify: `services/tutor-agent/app.py`
- Create: `services/tutor-agent/prompts.py`
- Test: `services/tutor-agent/tests/test_prompts.py`

**Interfaces:**
- Produces: `ORCHESTRATOR_PROMPT`, `CARD_GEN_PROMPT`, `GRADER_PROMPT` constants and `def build_system_prompt(profile: dict) -> str` that injects `weak_topics`, `preferences`, and `notes` from the learner profile into the orchestrator prompt.

- [ ] **Step 1: Failing test** — `build_system_prompt` includes a weak topic string when the profile lists one.
- [ ] **Step 2–4: FAIL → implement → PASS**
- [ ] **Step 5: Commit** → `git commit -m "feat(tutor-agent): system prompts + learner-profile memory injection"`

### Task 3.8: Deepgram voice transcription endpoint

**Files:**
- Create: `services/tutor-agent/voice.py`
- Modify: `services/tutor-agent/app.py` (add `POST /transcribe`)
- Test: `services/tutor-agent/tests/test_voice.py`

**Interfaces:**
- Produces: `def transcribe(audio_bytes: bytes, client, model="nova-3") -> str` calling Deepgram `listen.v1.media.transcribe_file` via injected `client`; on any error returns `""` (caller then tells the user to type). `POST /transcribe` body `{audio_b64}` → `{text}`.

- [ ] **Step 1: Failing tests** — fake client returns text → returned; fake client raises → `""` (graceful).
- [ ] **Step 2–4: FAIL → implement → PASS**
- [ ] **Step 5: Commit** → `git commit -m "feat(tutor-agent): Deepgram voice transcription with graceful fallback"`

### Task 3.9: Prometheus metrics

**Files:**
- Modify: `services/tutor-agent/app.py`
- Test: `services/tutor-agent/tests/test_metrics.py`

**Interfaces:**
- Produces: `Instrumentator().instrument(app).expose(app)` (adds `/metrics`) plus custom counters/histograms: `recall_cards_generated_total`, `recall_quizzes_graded_total`, `recall_quiz_correct_total`, `recall_llm_failures_total`, `recall_agent_iterations` (histogram), `recall_transcription_failures_total`.

- [ ] **Step 1: Failing test** — `GET /metrics` returns 200 and contains `recall_cards_generated_total`.
- [ ] **Step 2–4: FAIL → implement → PASS**
- [ ] **Step 5: Commit** → `git commit -m "feat(tutor-agent): Prometheus metrics"`

### Task 3.10: tutor-agent Dockerfile

**Files:**
- Create: `services/tutor-agent/Dockerfile`

- [ ] **Step 1: Write Dockerfile** (Python 3.11-slim, expose 8000, `CMD uvicorn app:app --host 0.0.0.0 --port 8000` — but keep the LLM/MCP init compatible; match PolyAI's approach).
- [ ] **Step 2: Commit** → `git commit -m "build(tutor-agent): add Dockerfile"`

---

## Phase 4 — Integration tests (real MCP transport)

Produces: the required agent↔study-mcp integration test over the real MCP protocol.

### Task 4.1: Agent-to-MCP round-trip integration test

**Files:**
- Create: `services/tutor-agent/tests/integration/test_mcp_roundtrip.py`
- Create: `services/tutor-agent/tests/integration/conftest.py`

**Interfaces:**
- Consumes: the real `study-mcp` server started as a subprocess on a test port with moto-backed (or DynamoDB-Local) tables; the agent connects via `MultiServerMCPClient` over `streamable_http`.

- [ ] **Step 1: Write the integration test**

```python
# Starts study-mcp on :9001, discovers tools over real MCP, exercises a round trip.
import pytest
@pytest.mark.integration
async def test_add_then_due_then_grade_over_mcp(mcp_server):
    tools = await client.get_tools()      # real MCP handshake
    names = {t.name for t in tools}
    assert {"create_deck","add_card","get_due_cards","grade_card"} <= names
    deck = await call(tools, "create_deck", user_id="u1", title="Bio")
    await call(tools, "add_card", deck_id=deck["deck_id"], user_id="u1",
               front="Q", back="A", topic="bio")
    due = await call(tools, "get_due_cards", user_id="u1")
    assert len(due["cards"]) == 1
    graded = await call(tools, "grade_card",
                        deck_id=deck["deck_id"], card_id=due["cards"][0]["card_id"], quality=5)
    assert graded["interval_days"] == 1
```

- [ ] **Step 2: Write `conftest.py`** — a fixture that launches `python app.py` (study-mcp) as a subprocess with test env (moto/DynamoDB-Local), waits for `/health`, yields, then tears down.
- [ ] **Step 3: Run** → `pytest services/tutor-agent/tests/integration -v -m integration` → PASS
- [ ] **Step 4: Add an `integration` marker + a separate CI job** that runs it.
- [ ] **Step 5: Commit** → `git commit -m "test: agent↔study-mcp integration over real MCP transport"`

---

## Phase 5 — Frontend

Produces: a chat + flashcard web UI (upload/paste, flip, type/speak answer, progress). Adapt PolyAI's `frontend`.

### Task 5.1: Frontend app

**Files:**
- Create: `services/frontend/` (framework mirroring PolyAI's frontend)

**Interfaces:**
- Consumes: tutor-agent endpoints `/decks`, `/session/start`, `/session/answer`, `/transcribe`.

- [ ] **Step 1: Scaffold UI** with three screens: **Upload** (paste text or choose PDF), **Study** (card front → reveal → self/typed/spoken answer → shows ✓/✗ + explanation + "next in N days"), **Progress** (accuracy, cards due, weak topics).
- [ ] **Step 2: Wire the mic button** → record → `POST /transcribe` → fill the answer box; fall back to typing on failure.
- [ ] **Step 3: Manual smoke test** against a locally running tutor-agent (document the steps in the frontend README).
- [ ] **Step 4: Add Dockerfile.**
- [ ] **Step 5: Commit** → `git commit -m "feat(frontend): upload/study/progress UI with voice input"`

---

## Phase 6 — Infrastructure (Terraform + Kubernetes + ArgoCD)

Produces: all AWS resources as code and K8s manifests for dev + prod, deployed via ArgoCD. Adapt PolyAI's `infra/`.

### Task 6.1: Terraform for AWS resources

**Files:**
- Create: `infra/terraform/{main.tf,variables.tf,dynamodb.tf,s3.tf,sns.tf,outputs.tf}`

**Interfaces:**
- Produces: S3 bucket; DynamoDB tables `Decks`, `Cards` (with `due-index` GSI on `user_id`+`due_date`), `LearnerProfile`; SNS topic. All tagged `Project=recall`.

- [ ] **Step 1: Write `dynamodb.tf`** defining the three tables + the GSI exactly matching `storage.py` keys.
- [ ] **Step 2: Write `s3.tf`, `sns.tf`, `variables.tf`, `outputs.tf`** (output table names, bucket, topic ARN for K8s config).
- [ ] **Step 3: `terraform validate` + `terraform plan`** (document expected plan; do not apply without instructor approval — shared account, see Global Constraints).
- [ ] **Step 4: Commit** → `git commit -m "infra: Terraform for S3, DynamoDB, SNS"`

### Task 6.2: Kubernetes manifests (dev + prod)

**Files:**
- Create: `infra/k8s/base/{tutor-agent,study-mcp,frontend}/{deployment,service}.yaml`
- Create: `infra/k8s/overlays/{dev,prod}/...` (kustomize) — or dev/prod dirs mirroring PolyAI's layout

**Interfaces:**
- Consumes: Terraform outputs (table names, bucket, topic) → K8s ConfigMaps; API keys → Secrets.

- [ ] **Step 1: Deployments** for the three services with **liveness/readiness probes** on `/health`, **resource requests/limits**, env from ConfigMap/Secret.
- [ ] **Step 2: Services** (ClusterIP; frontend exposed per PolyAI's ingress/LB pattern).
- [ ] **Step 3: HPA** for `tutor-agent` (CPU or request-rate).
- [ ] **Step 4: ConfigMaps** (model id, fallback, URLs, table names) + **Secrets** (LLM + OpenAI keys) — separate values per dev/prod.
- [ ] **Step 5: SNS reminder CronJob** — a K8s CronJob that queries due cards and publishes to SNS.
- [ ] **Step 6: Commit** → `git commit -m "infra: K8s manifests for dev and prod with probes, limits, HPA"`

### Task 6.3: ArgoCD application

**Files:**
- Create: `infra/argo/{recall-dev.yaml,recall-prod.yaml}`

- [ ] **Step 1: Write ArgoCD Application manifests** pointing at the dev and prod overlays (adapt PolyAI's `infra/k8s/argo`).
- [ ] **Step 2: Commit** → `git commit -m "infra: ArgoCD applications for dev and prod"`

---

## Phase 7 — CI/CD, observability dashboard, Agent Skill, docs

Produces: full build/deploy pipeline, Grafana dashboard + alerts, the reusable skill, and the test-plan doc.

### Task 7.1: Build & deploy workflows

**Files:**
- Create: `.github/workflows/build-tutor-agent.yaml`, `build-study-mcp.yaml`, `build-frontend.yaml`

- [ ] **Step 1: Write build workflows** (adapt PolyAI's `build-*.yaml`): on merge to main, build + push image, bump the image tag in the K8s overlay so ArgoCD deploys.
- [ ] **Step 2: Add Codecov reporting** to `test.yaml`.
- [ ] **Step 3: Commit** → `git commit -m "ci: build/push workflows + Codecov"`

### Task 7.2: Grafana dashboard + Prometheus alerts

**Files:**
- Create: `infra/grafana/dashboards/recall.json`
- Create: `infra/prometheus/alerts.yaml`

- [ ] **Step 1: Dashboard** — two rows: System health (latency p50/p95, error rate, pod status, HPA) and Learning metrics (cards generated, quiz accuracy = `recall_quiz_correct_total / recall_quizzes_graded_total`, active learners). Use the dataviz skill for color/layout.
- [ ] **Step 2: Alerts** — LLM failure/fallback spike (`recall_llm_failures_total` rate), endpoint error rate, p95 latency, tool timeout rate.
- [ ] **Step 3: Commit** → `git commit -m "observability: Grafana dashboard + Prometheus alerts"`

### Task 7.3: Agent Skill

**Files:**
- Create: `skills/generate-deck-from-syllabus/SKILL.md`

- [ ] **Step 1: Write the skill** — a reusable workflow that, given a syllabus, calls the tutor-agent to create one deck per topic. Follow the Superpowers skill format.
- [ ] **Step 2: Commit** → `git commit -m "feat: add generate-deck-from-syllabus agent skill"`

### Task 7.4: Test-plan document

**Files:**
- Create: `docs/test-plan.md`

- [ ] **Step 1: Write** what is tested, at which layer, what is mocked, and success criteria (SM-2 100% covered; every MCP tool has unit + integration coverage; CI green on every PR; coverage via Codecov).
- [ ] **Step 2: Commit** → `git commit -m "docs: add test plan"`

---

## Phase 8 (STRETCH / Phase 2) — RAG "ask your notes"

> Build only after Phases 0–7 are complete and demoable. Designed so no earlier task needs rework.

### Task 8.1: Retrieval module + vector store
- Chunk material, embed (Bedrock Titan or OpenAI), store vectors (pgvector on RDS — provision via Terraform), retrieve top-k for a query.
- Card-Generator optionally consumes retrieved chunks for long material; add an `ask_notes(user_id, question)` endpoint that retrieves + answers with citations.
- Unit-test chunking + retrieval ranking with a fake embedder; integration-test the query path.

---

## Self-Review

**Spec coverage** (each spec section → task):
- §1 Problem/value → framed in plan Goal + §7.4 test plan; no code task needed.
- §2 Scope committed → Phases 1–7; RAG staged → Phase 8. ✅
- §3 Architecture (3 services, 3 agents, invariants, AWS) → Tasks 2.x, 3.x, 6.1. ✅
- §4 Data model (S3 + 3 tables + GSI) → Task 2.1 (storage), Task 6.1 (Terraform). ✅
- §5 SM-2 → Task 1.1. ✅
- §6 Data flow (ingest/study/reminder) → Tasks 3.3, 3.6, 6.2 step 5 (SNS CronJob). ✅
- §7 Error handling (retry/fallback/cap/degradation) → Tasks 3.1 (retry), 3.2 (safe default), 3.4 (cap), 3.5 (fallback), 3.8 (transcription graceful), 3.3 (bad PDF), 3.6 (MCP unreachable). ✅
- §8 Testing (unit + real-MCP integration + test plan) → Phases 1–3 unit, Task 4.1 integration, Task 7.4 test plan. ✅
- §9 Observability (metrics/dashboard/alerts) → Tasks 3.9, 7.2. ✅
- §10 K8s (probes/limits/HPA/secrets/configmaps) → Task 6.2. ✅
- §11 CI/CD → Tasks 0.2, 4.1 step 4, 7.1. ✅
- §12 Extra credit (Web UI, multi-agent, skill, SNS) → Tasks 5.1, 3.1/3.2 + 3.6, 7.3, 6.2 step 5. ✅
- §13 Open decisions → deferred to Task 6.1 (vector store) / Phase 8; do not block committed scope. ✅

**Placeholder scan:** Code steps in Phases 1–3 (the novel logic) include full code. Phases 5–7 (infra/UI, heavily adapted from PolyAI reference) use directive steps rather than inlined YAML/JS — acceptable since the reference repo supplies the concrete templates; the interfaces they must satisfy (env var names, endpoints, table keys) are pinned in Global Constraints and earlier tasks.

**Type consistency:** `storage.py` signatures (Task 2.1) match their callers in `app.py` (Task 2.2); `sm2.schedule` return keys (`ease_factor/interval_days/repetitions`) match `_grade_card`'s usage; sub-agent return dicts (`generate_cards` → list of `{front,back,topic}`; `grade_answer` → `{is_correct,explanation,quality}`) match how `app.py` (Task 3.6) consumes them; MCP tool names in Task 2.2 match the integration assertions in Task 4.1.
