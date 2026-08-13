# Recall

An AI study tutor that turns any study material into an adaptive quiz — generating
flashcards, quizzing by text or voice, grading answers with explanations, and
scheduling reviews with the SM-2 spaced-repetition algorithm.

See [`docs/spec.md`](docs/spec.md) for the design and
[`docs/plan.md`](docs/plan.md) for the implementation plan.

## Services

| Service | Purpose | Port |
|---|---|---|
| `services/tutor-agent` | Orchestrator: manual ReAct loop, HTTP API, hosts the Card-Generator and Grader sub-agents | 8000 |
| `services/study-mcp` | Own MCP server: deck/card/SM-2/progress/memory tools over DynamoDB + S3 | 9000 |
| `services/frontend` | Web UI: upload/paste material, flip cards, speak or type answers, view progress | 3000 |

## Local development

Requires Python 3.11.

```bash
# per service
cd services/study-mcp
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest -v
```

## Testing

```bash
pytest services/study-mcp services/tutor-agent -v      # unit tests
pytest services/tutor-agent/tests/integration -m integration -v   # real MCP transport
```

Integration tests need DynamoDB Local:

```bash
docker run -d -p 8001:8000 amazon/dynamodb-local
```
