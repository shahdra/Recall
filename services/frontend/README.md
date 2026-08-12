# Recall frontend

Next.js 14 (App Router) + TypeScript + Tailwind. Three screens over the
tutor-agent's HTTP API.

| Screen | What it does | Endpoints |
|---|---|---|
| **Add material** | Paste notes or upload a PDF; shows the card count or a warning | `POST /decks` |
| **Study** | Question → type or speak an answer → ✓/✗ with an explanation and the next review date | `POST /session/start`, `POST /session/answer`, `POST /transcribe` |
| **Progress** | Cards reviewed, accuracy, cards due, weakest topics | `POST /session/start` |

## How the agent URL is resolved

`lib/api.ts` derives it at runtime rather than at build time:

- `NEXT_PUBLIC_AGENT_URL` wins if set (useful for local development).
- Otherwise, same hostname as the page, with **agent port = frontend port + 500**
  (dev NodePort 30300 → 30800, prod 31300 → 31800).
- Falls back to `http://localhost:8000` during SSR.

`NEXT_PUBLIC_*` values are inlined into the client bundle at build time, so
baking the URL in would mean one image per environment and a rebuild whenever a
node's IP changed. Deriving it at runtime keeps a single image working everywhere.

## Learner identity

Auth is out of scope (`docs/spec.md`), so a random `user_id` is generated per
browser and kept in `localStorage`. That is what makes long-term memory
observable: reload the page and the tutor still knows your weak topics.

## Local development

```bash
npm install
npm run dev            # http://localhost:3000, agent assumed at :8000
```

Point it somewhere else with:

```bash
NEXT_PUBLIC_AGENT_URL=http://127.0.0.1:8010 npm run dev
```

## Manual smoke test

Start the backend from the repo root:

```bash
docker run -d --rm --name recall-ddb -p 8001:8000 amazon/dynamodb-local
./scripts/setup-local-dynamodb.sh
./scripts/start-local.sh          # study-mcp :9001, tutor-agent :8010
```

Serve the frontend on **7510** so the port-derivation rule (`+500`) points it at
the agent on 8010:

```bash
npm run build
npx next start -p 7510            # http://localhost:7510
```

Then walk through:

1. **Add material** — paste a few paragraphs, name the deck, press *Make
   flashcards*. Expect a toast with the card count and an automatic switch to
   Study. (Card generation calls the real model, so allow a few seconds.)
2. **Study** — answer correctly and check the ✓, the explanation, and "Next
   review in N days". Answer wrongly and confirm it comes back in 1 day.
3. **Mic** — press the mic, speak, press stop. The transcript should fill the
   answer box. On failure you get a message telling you to type instead, and
   typing still works.
4. **Progress** — cards reviewed, accuracy, and any weak topics should match what
   you just did.
5. **Reload the page** — Progress should still show your history, proving the
   learner profile persisted.

### Automated versions

Two Playwright harnesses cover the same ground and are meant to be run by hand
against a live stack (they are not part of CI, and are excluded from the image):

```bash
node smoke.mjs         # walks all three screens, writes /tmp/shot-*.png
node voice-check.mjs   # browser WebM/Opus recording -> /transcribe
```

`voice-check.mjs` exists because the Python tests could only verify WAV and M4A;
Chrome and Firefox record **WebM/Opus**, which needs a real browser to produce.
It proves the format path, not transcript accuracy — Chromium's fake audio device
does not reliably play a supplied file. Accuracy is covered by
`services/tutor-agent/tests/integration/test_voice_live.py`.

## Container

```bash
docker build -t recall-frontend .
docker run --rm -p 3000:3000 recall-frontend
```

Uses Next's `output: "standalone"`, so the runtime image carries only the traced
server dependencies rather than the whole `node_modules` tree, and runs as the
unprivileged `node` user.
