# Recall — Design Specification

> **Repo note:** Recall is a **brand-new, standalone project** built from scratch in
> its own repository. **PolyAIFursa is a reference only** — patterns and code
> (manual ReAct loop, FastAPI + Prometheus instrumentation, MCP server structure,
> Kubernetes manifests, ArgoCD GitOps, CI/CD workflows, Grafana/Prometheus setup)
> are copied and adapted from it, but Recall does not extend the PolyAIFursa repo.
> This document (and `docs/plan.md`) belong in the new Recall repo and must be
> committed and approved by course staff via PR **before coding begins**.

---

## 1. Problem Statement & Business Value

Students forget most of what they study within days (the Ebbinghaus forgetting
curve). Passive re-reading feels productive but produces poor long-term
retention. The evidence-based remedy is **active recall + spaced repetition**:
quizzing yourself, with harder material resurfacing more often. In practice few
students do this, because hand-building flashcards is tedious and manually
tracking *what to review and when* is a chore.

**Recall** is an AI study tutor that removes that friction. A student uploads
study material (pasted text or a PDF); Recall generates flashcards and quiz
questions, quizzes the student (by text **or voice**), grades answers with
explanations, and uses the **SM-2 spaced-repetition algorithm** to schedule each
card's next review. It remembers, across sessions, what each learner struggles
with and adapts accordingly.

**Measurable value:** better retention per hour studied. Concretely measurable as
quiz accuracy trend, cards mastered over time, review streaks, and reduction in
per-topic miss-rate — all surfaced on the observability dashboard.

**One-liner:** *Recall turns any study material into an adaptive quiz that learns
what you don't know and keeps drilling it until you do.*

---

## 2. Scope

### Committed (day one)

- Multi-agent tutor: **Orchestrator** + **Card-Generator** + **Grader** sub-agents
- **study-mcp** server (own MCP server) with deck/card/scheduling/memory tools
- **SM-2** spaced-repetition scheduling (deterministic)
- **Long-term memory**: a per-learner profile injected into the system prompt
- Input: **pasted text and PDF upload** (PDF parsed to text, stored in S3)
- **Deepgram voice answers** (Deepgram Nova speech-to-text)
- **Web UI**: upload/paste, flip cards, speak/type answers, progress view
- **Kubernetes on EC2** (dev + prod namespaces), **Terraform** for all AWS
- **CI/CD** (tests on every PR, build, ArgoCD deploy dev→prod)
- **Observability**: metrics, Grafana dashboard, alerts
- **Unit + integration tests**; a test-plan document
- **SNS** daily "cards due" reminder
- One reusable **Agent Skill**

### Staged — Phase 2 stretch goal (designed in, built only if time allows)

- **RAG**: chunk + embed uploaded material into a vector store; retrieve relevant
  passages for card generation and an "ask your notes" grounded-Q&A feature.
  Rationale for staging: RAG roughly doubles app-layer complexity and adds an
  always-on vector-store cost; the Card-Generator works without it by reading
  material directly. The design accommodates it without rework.

### Explicitly out of scope

- No-code/black-box agent frameworks (`create_react_agent`, `AgentExecutor`) —
  the ReAct loop is implemented manually, following the PolyAI teaching principle
- Multi-user auth/accounts beyond a simple `user_id` (single-tenant demo scope)

---

## 3. Architecture

Three containerized services, deployed to Kubernetes (dev + prod namespaces):

```
recall/
  services/
    tutor-agent/   Orchestrator: manual ReAct loop, HTTP API, hosts 2 sub-agents
    study-mcp/     OWN MCP server: deck/card/SM-2/progress/memory tools over AWS
    frontend/      Web UI: upload/paste, flip cards, speak/type answers, progress
    retrieval/     (PHASE 2 / RAG) chunk, embed, store, retrieve — may fold into study-mcp
```

### 3.1 The agents (one LLM, three prompted ReAct loops)

There is one underlying LLM (via LangChain `init_chat_model`, as in PolyAI). Each
"agent" is a system prompt + a set of tools + a loop that calls the LLM until the
task is done. Sub-agents are exposed to the orchestrator **as tools**.

| Component | System-prompt role | Tools it can call |
|---|---|---|
| **Orchestrator** | Runs the tutoring session, manages conversation flow, decides what to do next | `generate_cards`, `grade_answer` (sub-agents-as-tools) + study-mcp tools (`get_due_cards`, `grade_card`, `get_progress`, `create_deck`) |
| **Card-Generator** | Turns study material into high-quality flashcards `{front, back, topic}` | `add_card` (via study-mcp) |
| **Grader** | Judges a student's answer, explains *why*, assigns an SM-2 quality grade 0–5 | (none — pure reasoning, returns a verdict) |

**Persona (orchestrator):** a patient, encouraging tutor that explains *why* an
answer is right or wrong, never does the student's homework for them, and keeps
replies concise and plain-text.

### 3.2 Invariants (design principles)

1. **The LLM only decides; Python does all I/O.** Every DynamoDB/S3/Deepgram/MCP
   call is code, triggered by a tool the LLM chose. (Mirrors PolyAI's "LLM never
   sees image data" rule.)
2. **SM-2 math is deterministic and lives outside the LLM.** The Grader produces
   a grade `q`; the `grade_card` tool does the scheduling arithmetic.
3. **Long-term memory closes the loop.** Every session reads *and* writes the
   learner profile, so the tutor adapts across sessions.
4. **Keep it explicit, not magic.** The ReAct loop is hand-written and readable.

### 3.3 AWS services (all provisioned via Terraform)

- **S3** — raw uploaded materials (PDF/text)
- **DynamoDB** — `Decks`, `Cards` (with SM-2 state), `LearnerProfile`
- **SNS** — daily "N cards due" reminder
- **(Phase 2) Vector store** — pgvector on RDS *or* OpenSearch, for RAG

---

## 4. Data Model

### S3
Raw materials keyed by user + upload id, e.g. `uploads/{user_id}/{upload_id}.pdf`.
Large files never live in the database.

### DynamoDB — `Decks`
```
PK: user_id      SK: deck_id
title, source_s3_key (null if pasted), card_count, created_at
```

### DynamoDB — `Cards` (carries SM-2 state)
```
PK: deck_id      SK: card_id
front, back, topic
ease_factor   (float, starts 2.5)
interval_days (int, starts 0)
repetitions   (int, starts 0)
due_date      (ISO; GSI partition for efficient "due now" queries)
last_reviewed (ISO)
history       (list of { ts, grade, was_correct })
```
A **GSI on `due_date`** lets `get_due_cards` fetch everything due without scanning.

### DynamoDB — `LearnerProfile` (long-term memory)
```
PK: user_id
weak_topics  ({ topic: miss_rate })
preferences  ({ answer_style, tone })
stats        ({ total_reviews, accuracy, streak_days, last_active })
notes        (free-text observations about the learner)
```
Read at session start and injected into the orchestrator's system prompt; updated
after each session from the review history.

---

## 5. The SM-2 Algorithm

The Grader returns a quality grade `q` (0 = blank … 5 = perfect recall). On each
review, `grade_card` runs:

```
if q < 3:                       # incorrect
    repetitions   = 0
    interval_days = 1           # resurface tomorrow
else:                           # correct
    if   repetitions == 0: interval_days = 1
    elif repetitions == 1: interval_days = 6
    else:                  interval_days = round(interval_days * ease_factor)
    repetitions += 1

ease_factor = ease_factor + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
ease_factor = max(1.3, ease_factor)

due_date = today + interval_days
```

Behavior: missed cards reset to a 1-day interval and their ease drops (they nag
you); repeatedly-correct cards grow `1 → 6 → ~15 → ~37 → ~90` days (they fade
out). Fully deterministic → precisely unit-testable → the core "measurable value"
story. **The LLM never performs this math.**

---

## 6. Data Flow

### Flow A — Ingest → generate deck
1. User uploads a PDF / pastes text in the Web UI.
2. Frontend → `POST /decks {title, file|text}` → tutor-agent.
3. tutor-agent: parse PDF→text (if needed), store raw file in S3, call study-mcp
   `create_deck`; orchestrator LLM calls `generate_cards` → **Card-Generator**.
4. Card-Generator: reads material → returns `[{front,back,topic}, …]`; each card
   persisted via study-mcp `add_card` (SM-2 state initialized: EF 2.5, due today).
5. tutor-agent returns `{deck_id, card_count}` → UI confirms.

### Flow B — Study session
1. User starts a session; frontend → `POST /session/start {deck_id}`.
2. tutor-agent reads `LearnerProfile` → injects into system prompt; calls
   `get_due_cards`; presents card front.
3. User answers by **typing** or **speaking** (voice → Deepgram API → text).
4. Frontend → `POST /session/answer {card_id, student_answer}`.
5. Orchestrator calls `grade_answer` → **Grader** returns `{is_correct,
   explanation, quality 0–5}`.
6. tutor-agent calls `grade_card(card_id, quality)` → SM-2 updates
   interval/EF/due_date in DynamoDB, appends history, updates `LearnerProfile`.
7. UI shows ✓/✗ + explanation + "next review in N days"; loops to next due card.
8. On session end, tutor-agent updates profile stats (accuracy, streak).

### Flow C — Daily reminder
A scheduled trigger (K8s CronJob or EventBridge) queries cards due today per user
→ publishes to **SNS** → "📚 You have N cards due today."

---

## 7. Error Handling & Resilience

| Failure | Handling |
|---|---|
| LLM call fails (timeout/5xx/throttle) | Exponential-backoff retry (2–3×) with a rate limiter; then a clear "briefly unavailable" message — never a stack trace |
| LLM provider down | **Fallback model** from a configured primary→secondary list; logged + shown on dashboard |
| Agent loop runaway | **Hard iteration cap** (~8 tool rounds) → graceful stop + best-effort answer |
| Card-Generator returns malformed output | Pydantic-validate; retry once with stricter prompt; else keep the valid cards + warn |
| Grader returns invalid grade | Validate; **safe-default to "incorrect" (q≈2)** so the card resurfaces; log |
| Bad PDF (encrypted/scanned/corrupt/oversized) | Caught → "couldn't read this PDF, try pasting text"; enforce max file size |
| Transcription failure (Deepgram) | Non-critical → fall back to "couldn't hear that, please type"; typed path always works |
| DynamoDB/S3 error | boto3 retries + clean 5xx with a `request_id`; UI shows "couldn't save, retrying" |
| No cards due | Friendly state, not an error: "nothing due 🎉 — study ahead?" |
| MCP server unreachable at startup | Log a warning and run with reduced tools (as PolyAI does), don't crash |

**Cross-cutting:** every external call (LLM, Deepgram, boto3, MCP) is wrapped;
endpoints return structured `{error, code, request_id}` — never a traceback;
**graceful-degradation ladder**: voice→text, RAG→direct-read, primary→fallback
model; `grade_card` writes are idempotent per card.

---

## 8. Testing Strategy

### Unit tests (fast; LLM and external services mocked)
- **SM-2 algorithm** — table-driven tests asserting exact `interval / ease /
  repetitions / due_date` outputs across all grade paths (no mocking needed).
- **study-mcp tools** — `create_deck`, `add_card`, `get_due_cards`, `grade_card`,
  `get_progress`, `list_decks` with **DynamoDB/S3 mocked via `moto`**.
- **Card-Generator** — canned LLM responses (including malformed) exercise the
  schema-validation and retry paths.
- **Grader** — correct/incorrect answers map to expected grades; invalid LLM
  output triggers the safe-default path.
- **Orchestrator loop** — iteration cap, tool routing, graceful termination
  (LLM + tools mocked).
- **PDF parsing / error paths** — fixture files for good and bad PDFs.

### Integration tests (required — real MCP transport)
- Start the **real study-mcp server**; connect the agent over **actual
  MCP-over-HTTP** (same harness pattern as PolyAI's img-proc-mcp tests).
- Assert a full round trip: `add_card` → row lands in DynamoDB (moto/local) →
  `get_due_cards` returns it → `grade_card` updates the schedule. LLM stays mocked.

### Test plan document
`docs/test-plan.md`: what is tested, at which layer, what is mocked, and success
criteria (e.g. 100% coverage of SM-2; every MCP tool has unit + integration
coverage; CI green on every PR; coverage reported via Codecov).

---

## 9. Observability

**"Healthy" = the service is up AND the tutor is actually teaching.**

### Metrics (Prometheus via `prometheus-fastapi-instrumentator`)
- Request rate, latency p50/p95, error rate per endpoint
- LLM call latency + failure/fallback count
- Agent iterations per request + cap-hit count
- Transcription failure count (`recall_transcription_failures_total`)
- Product metrics: cards generated, quizzes graded, quiz accuracy
- Tool call count + tool error/timeout rate

### Dashboard (Grafana)
One board, two rows — **System health** (latency, errors, pod status, HPA) and
**Learning metrics** (cards created, accuracy trend, active learners).

### Alerts
LLM failure/fallback spike; endpoint error rate over threshold; p95 latency high;
tool-timeout spike; (optional) sudden accuracy drop signalling a bad prompt/model.

---

## 10. Kubernetes & Deployment

- **Dev + prod namespaces**, separate config per environment (ConfigMaps + Secrets).
- **Liveness/readiness probes** on `/health` for each service.
- **Resource requests/limits** on every pod.
- **HPA** on tutor-agent (CPU or request-rate based).
- **Secrets** for the Deepgram API key (Bedrock uses IAM, not a key); **ConfigMaps** for model choice and URLs.
- **ArgoCD GitOps** deploys, promoting dev → prod (pattern adapted from PolyAI).

---

## 11. CI/CD

- Run all tests on every **pull request**; report via GitHub Actions summary +
  Codecov.
- Build and push container images per service on merge.
- **ArgoCD** deploys to `dev`, then `prod` (adapted from PolyAI workflows).

---

## 12. Extra-Credit Features

- **Web UI** (committed) — chat + flashcard interface with voice input.
- **Multi-agent** (committed) — Orchestrator + Card-Generator + Grader.
- **Agent Skill** (committed) — one reusable skill, e.g. "generate a deck from a
  syllabus" or "triage a learner's weak topics."
- **SNS reminders** (committed) — daily "cards due" notification.
- **RAG "ask your notes"** (Phase 2 stretch) — grounded Q&A over uploaded material.

---

## 13. Open Decisions (to settle during implementation)

- **Phase-2 vector store:** pgvector on RDS (simpler/cheaper, recommended) vs.
  OpenSearch.
- **Phase-2 embeddings:** Bedrock Titan (stays in AWS, no extra key) vs. OpenAI
  (Deepgram is speech-only, so an embeddings key would be new either way).
- ~~**Primary/fallback model pair**~~ — **SETTLED (2026-08-02):**
  primary `bedrock:amazon.nova-lite-v1:0`, fallback
  `bedrock:us.anthropic.claude-haiku-4-5-20251001-v1:0`. Both verified to invoke
  and to support tool calling in account 228281126655. The fallback is a
  different provider family on purpose, so a Nova-side outage cannot take both
  down. Note the course IAM policy `bedrock-restrict-developers` **explicitly
  denies** everything outside an eight-model allowlist — `amazon.nova-2-lite-v1:0`
  is denied, and `anthropic.claude-3-haiku` (legacy) and
  `meta.llama3-1-8b-instruct` (needs an inference profile) fail for other
  reasons. `mistral-7b-instruct` is allowed but returns no tool calls, so it
  cannot drive the ReAct loop.
