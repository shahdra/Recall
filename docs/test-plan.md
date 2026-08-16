# Recall — test plan

What is tested, at which layer, what is faked, and how we know it works.

Every number below was measured on 2026-08-16, not estimated. Commands to reproduce
them are in [Reproducing these numbers](#reproducing-these-numbers).

---

## 1. The shape of the suite

**285 automated tests.** 269 unit, 16 integration.

| Suite | Tests | Coverage | What it proves |
|---|---:|---:|---|
| `services/study-mcp` | 61 | 95% | SM-2 scheduling and the DynamoDB storage layer |
| `services/tutor-agent` | 194 | 88% | The agent loop, grading, card generation, HTTP contract |
| `services/reminder` | 14 | 99% | The daily digest CronJob |
| `services/tutor-agent` (integration) | 16 | — | Agent ↔ study-mcp over a real MCP transport |
| `services/frontend` | 0 | — | **No suite.** See [§5](#5-the-frontend-has-no-tests-and-that-is-a-decision). |

Per-module coverage, worst first, because the aggregate hides where the gaps are:

| Module | Stmts | Miss | Cover | Note |
|---|---:|---:|---:|---|
| `tutor-agent/llm_json.py` | 34 | 10 | 71% | Retry/repair paths for malformed model JSON |
| `tutor-agent/voice.py` | 36 | 9 | 75% | Deepgram error branches; needs a live key to exercise |
| `study-mcp/app.py` | 138 | 27 | 80% | MCP tool wiring; the tools themselves are covered |
| `tutor-agent/app.py` | 296 | 50 | 83% | Mostly error handlers and S3 branches |
| `tutor-agent/metrics.py` | 45 | 7 | 84% | Counter increments on rare failure paths |
| `tutor-agent/ingest.py` | 52 | 7 | 87% | PDF edge cases |
| `tutor-agent/agent_loop.py` | 71 | 7 | 90% | |
| `tutor-agent/prompts.py` | 84 | 5 | 94% | |
| `tutor-agent/card_generator.py` | 91 | 4 | 96% | |
| `tutor-agent/llm.py` | 86 | 3 | 97% | |
| `reminder/reminder.py` | 59 | 2 | 97% | |
| `study-mcp/storage.py` | 59 | 1 | 98% | |
| `tutor-agent/grader.py` | 37 | 0 | **100%** | |
| `study-mcp/sm2.py` | 24 | 0 | **100%** | The one module where 100% was a requirement |

---

## 2. SM-2 is covered to 100%, on purpose

`study-mcp/sm2.py` — 24 statements, 0 missed.

It earns that because it is the only module in the system that is **pure, total, and
irreversible in effect**. Given a quality score and a card's current state it returns
the next interval; nothing about it touches the network, the clock, or a database. So
there is no excuse for an untested branch — and a wrong interval silently corrupts a
learner's schedule for weeks before anyone notices, because the symptom is "these cards
feel like they come back at the wrong time", not an error.

What the 61 study-mcp tests pin down about it:

- **The quality thresholds.** `quality < 3` is a lapse: repetitions reset to 0 and the
  interval returns to 1 day. `quality >= 3` advances.
- **The interval ladder.** First success → 1 day, second → 6 days, then
  `previous_interval × ease_factor` rounded.
- **The ease-factor floor.** SM-2 clamps at 1.3. Without the clamp a repeatedly-failed
  card's interval collapses toward daily forever; the test asserts the floor holds
  after many consecutive lapses.
- **Determinism.** The same input always gives the same output — no clock reads inside
  the function, which is what lets every other test assert exact numbers.

---

## 3. What is faked, and what is deliberately not

The rule: **fake what costs money or is non-deterministic; use the real thing for
anything whose contract we depend on.**

| Dependency | Unit tests | Integration tests | Why |
|---|---|---|---|
| **Bedrock (the LLM)** | Faked | Faked | Non-deterministic and billed per call. A test that asserts on model prose is a test that fails when the model is retrained. |
| **DynamoDB** | `moto` (in-process AWS mock) | **Real DynamoDB Local** | `moto` is fast enough for unit tests; the integration suite uses the real engine because `moto` does not enforce every GSI projection rule, and the GSI is where the schema bugs live. |
| **S3** | `moto` | `moto` | Only used to archive the uploaded file; nothing reads it back. |
| **SNS** | `moto` | `moto` | The reminder's only side effect is one `publish` call; asserting it was made with the right message is the whole contract. |
| **MCP transport** | Faked | **Real** — a live study-mcp subprocess over MCP-over-HTTP | See §4. |
| **Deepgram (speech-to-text)** | Faked | Faked | Needs a paid key; the failure paths are what matter and they are asserted against a fake. |

**Bedrock is never called by any test.** That is a hard rule, not a preference: the
account's `bedrock-restrict-developers` policy would make a test suite that hits real
models both slow and occasionally denied, and a CI run that costs money per execution
gets disabled the first time someone notices the bill.

---

## 4. The integration tests use a real MCP transport

`services/tutor-agent/tests/integration/test_mcp_roundtrip.py` — 16 tests, marked
`@pytest.mark.integration` so they are excluded from the default run.

They launch **a real study-mcp subprocess** and talk to it over **real
MCP-over-HTTP**, backed by **real DynamoDB Local**. Only the LLM is faked.

This layer exists because of a specific class of bug the unit tests cannot see. The
agent reaches study-mcp over MCP and imports none of its code — no shared types, no
shared module. So every unit test on either side mocks the other side's shape *as the
author believed it to be*, and both suites stay green while the two drift apart. The
integration suite is the only place the real wire format is exercised: tool names,
argument names, the JSON the tools actually return.

Concretely, it catches:

- A tool renamed on one side and not the other.
- An argument name changed (`user_id` → `userId`).
- A return shape that changed — a bare list becoming `{"cards": [...]}`.
- DynamoDB schema drift: a Query against `due-index` that works against `moto` but
  fails against the real engine.

**Run them explicitly:**

```bash
cd services/tutor-agent
RECALL_TEST_DDB_ENDPOINT=http://localhost:8001 \
PYTHONPATH=$(pwd)/../study-mcp \
  .venv/bin/pytest tests/integration/ -v -m integration
```

They need DynamoDB Local on port 8001 — `docker compose up -d dynamodb` provides it.
CI starts it as a service container (`.github/workflows/test.yaml`).

---

## 5. The frontend has no tests, and that is a decision

`services/frontend` has **no test suite**. `@playwright/test` is in `package.json`,
but there is no config, no `test` script, and CI never invokes it.

The verification the frontend actually gets:

1. **`npx tsc --noEmit`** — the real gate. It is what proves a component's props match
   its consumers' usage across a refactor, which for a typed React codebase catches the
   large majority of what a shallow render test would.
2. **`npm run build`** — compiles every route and fails on a build-breaking error.
3. **A scripted manual click-through** in the running compose stack, listed in
   `services/frontend/README.md`.

**Why:** the frontend's logic is mostly layout and animation, both of which need eyes
rather than assertions, and its one piece of real behaviour — the answer flow's phase
machine — is exercised end to end by the manual walkthrough. A shallow-render suite
would mostly assert that JSX contains the strings it obviously contains.

**The honest cost:** there is no regression net. A change to one component can silently
break another's behaviour, and nothing catches it until someone clicks. This was
weighed and accepted; if the frontend grows real client-side logic (offline queueing,
optimistic updates), that trade should be revisited. The candidate then is Playwright
against the compose stack, since routing and animation are what matter here and neither
is visible to a unit test.

---

## 6. What CI runs

`.github/workflows/test.yaml`, on every pull request and every push to `main`:

| Job | Runs |
|---|---|
| `unit` (matrix: study-mcp, tutor-agent) | `pytest -m "not integration"` with coverage, uploaded to Codecov |
| `integration` | The 16 MCP roundtrip tests against a DynamoDB Local service container |

Both jobs run with **no AWS credentials and no `DEEPGRAM_API_KEY`** — deliberately.
The unit suite must pass with every external service absent, so a green CI run means
"the code is correct", not "the code is correct on a machine that happens to have
credentials".

`.github/workflows/cd.yaml` builds and pushes images on merge; it does not re-run
tests, because the same commit's `test.yaml` run already gated it.

**Not in CI:** the frontend (see §5), `terraform validate`, and `kubeconform` on the
manifests. The latter two are run by hand before committing infrastructure changes and
are a genuine gap — a malformed manifest reaches `main` today and is caught by ArgoCD
refusing to sync, which is later and noisier than a CI failure.

---

## 7. Success criteria

The bar for calling the test suite adequate:

- [x] **SM-2 at 100% coverage.** Measured: 24/24 statements.
- [x] **Every MCP tool has unit coverage and appears in the integration suite.** All
      11 tools are exercised; `mcp_tools: 11` in `/health` is the count the agent
      discovers at runtime.
- [x] **CI green on every PR**, with no credentials present.
- [x] **Coverage reported to Codecov** per service, so a regression is visible in the
      PR rather than discovered later.
- [x] **The agent↔study-mcp contract is tested over a real transport**, not two
      independently-mocked halves.
- [x] **No test calls a paid API.** Bedrock and Deepgram are faked everywhere.
- [ ] **Frontend behaviour is covered automatically.** Not met, by decision (§5).
- [ ] **Infrastructure manifests validated in CI.** Not met; done by hand today.

The two unmet items are recorded rather than quietly dropped. Both are known trades,
and both would be the first things to add if this project continued.

---

## Reproducing these numbers

```bash
# Unit suites with coverage
cd services/study-mcp   && .venv/bin/pytest -q -m "not integration" --cov=. --cov-report=term
cd services/tutor-agent && .venv/bin/pytest -q -m "not integration" --cov=. --cov-report=term
cd services/reminder    && ../tutor-agent/.venv/bin/pytest -q --cov=. --cov-report=term

# SM-2 alone, the 100% claim
cd services/study-mcp && .venv/bin/pytest -q -m "not integration" --cov=sm2 --cov-report=term

# Integration (needs DynamoDB Local: docker compose up -d dynamodb)
cd services/tutor-agent
RECALL_TEST_DDB_ENDPOINT=http://localhost:8001 PYTHONPATH=$(pwd)/../study-mcp \
  .venv/bin/pytest tests/integration/ -v -m integration

# Frontend, such as it is
cd services/frontend && npx tsc --noEmit && npm run build
```

`services/reminder` has no virtualenv of its own; it borrows the tutor-agent's, which
already has `moto` and `boto3`. Worth knowing before assuming the suite is broken.
