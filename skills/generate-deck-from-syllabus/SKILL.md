---
name: generate-deck-from-syllabus
description: Turn a syllabus, course outline, or reading list into one Recall flashcard deck per topic by calling the tutor-agent's HTTP API. Use this skill whenever the user wants bulk deck creation from a structured document — e.g. "make decks from this syllabus", "build a deck for each week of the course", "turn this course outline into flashcards", "I have a reading list, make me decks" — or when they ask to seed Recall with material for a demo. Trigger even if they only say "make decks from this" while a syllabus-shaped document is in context.
---

# Generate Recall decks from a syllabus

One deck per topic, created by POSTing each topic's material to the tutor-agent. The
agent does the card generation with an LLM; this skill's job is splitting the syllabus
sensibly, calling the API correctly, and reporting honestly what came back.

## Read this first: the API is not what you would guess

Verified against `services/tutor-agent/app.py`. Three things routinely surprise people:

**`POST /session/start` accepts a `deck_id` and silently ignores it.**
`SessionStartRequest` (app.py:410) declares `deck_id: str | None = None`, but
`session_start` (app.py:543) never reads it — it always returns every due card across
every deck. If you want one deck's cards, filter client-side on `card["deck_id"]`.
Passing `deck_id` and trusting it would give you the whole queue while looking like it
worked.

**A deck can be created successfully with zero cards.** When the material is too
short or too vague the response is still HTTP 200, with `card_count: 0` and a
`warning` key (app.py:535). An empty deck is a real outcome to report, not an error to
retry blindly.

**Cards per deck cap at 40** (`card_generator.py:22`, `DEFAULT_MAX_CARDS`). A 5000-word
topic does not produce 200 cards; it produces at most 40. Split by topic rather than
sending the whole syllabus as one blob, or you get 40 cards spanning twelve weeks and
no way to study one week at a time.

## The endpoint

`POST {AGENT_URL}/decks`

```json
{
  "user_id": "learner-abc123",
  "title": "Week 3 — Kubernetes networking",
  "text": "…the topic's material…"
}
```

| Field | Required | Notes |
|---|---|---|
| `user_id` | yes | The learner. Decks are per-user; a wrong id creates decks nobody can see. |
| `title` | yes | Becomes the deck name shown in the UI. |
| `text` | one of | The material to make cards from. |
| `file_b64` | one of | Base64 PDF/text instead of `text`. Needs `content_type`. |
| `content_type` | with `file_b64` | e.g. `application/pdf`. |

Send `text` **or** `file_b64`. Sending neither returns 400 *"Send either some text or
a file to make cards from."* (app.py:478).

**Success (200):**

```json
{ "deck_id": "uuid", "card_count": 12, "source_s3_key": null }
```

`source_s3_key` is non-null only for `file_b64` uploads with an S3 bucket configured.
A `warning` key appears when `card_count` is 0.

**Failure:** every error is `{"error": "...", "code": "...", "request_id": "..."}` —
never a traceback. The `error` string is already written for a human, so surface it
verbatim rather than paraphrasing.

## Finding the agent URL

In order of preference:

1. **Deployed:** `https://recall.fursa.click` (prod) or `https://dev.recall.fursa.click`.
   The agent shares the hostname with the frontend, split by path — `/decks` routes to
   the agent. See `infra/k8s/{dev,prod}/ingress/ingress.yaml`.
2. **Local compose:** `http://localhost:3500`.
3. **NodePort, no DNS:** `http://<node-ip>:30800` (dev) or `:31800` (prod).

Confirm before bulk-creating anything:

```bash
curl -s $AGENT_URL/health
# {"status":"ok","llm":true,"mcp_tools":11,...}
```

`llm: false` or `mcp_tools: 0` means deck creation will fail — the agent is up but
cannot generate. Stop and say so rather than firing twelve doomed requests.

## Splitting the syllabus

This is the part that decides whether the decks are useful.

**One deck per topic the syllabus itself names.** A week, a unit, a lecture, a chapter
— whatever unit the document is organised around. Do not invent a taxonomy; the
learner will look for the names they already know.

**Each deck needs enough material to generate from.** A bare heading ("Week 4:
Storage") produces an empty deck. If a topic is only a title plus a reading
reference, either merge it with its neighbour or tell the user it is too thin — do not
send it and report a deck with 0 cards as if it were a success.

**Title format: keep the syllabus's own numbering.** `"Week 3 — Kubernetes
networking"` sorts and scans better than `"Kubernetes networking"` when there are
twelve of them.

**Serialise the calls.** Each one is an LLM pass over the material and takes 30–90s.
Firing twelve in parallel will hit Bedrock throttling, and the retries make it slower
than doing them in order. Report progress as you go.

## Workflow

1. **Get `user_id`.** Ask if it was not given. In the browser it is generated per-user
   and stored in localStorage under `recall.user_id`; there is no way to look it up
   server-side, so it has to come from the user.
2. **Health check** the agent URL. Abort on `llm: false`.
3. **Split** the syllabus into topics, and show the user the proposed deck list
   *before* creating anything. Twelve wrong decks are tedious to clean up — there is
   no delete endpoint on the agent.
4. **Create serially**, one POST per topic.
5. **Report a table**: topic → `card_count` → `deck_id`, and flag every deck that came
   back with a `warning` or `card_count: 0`.
6. **Verify** with `POST /session/start` (`{"user_id": "..."}`) and confirm the new
   decks appear in `decks[]` with the counts you expect.

## Example

```bash
AGENT_URL=https://dev.recall.fursa.click
USER_ID=learner-abc123

curl -s "$AGENT_URL/health" | python3 -c 'import json,sys; h=json.load(sys.stdin); print(h["status"], "llm:", h["llm"])'

curl -s -X POST "$AGENT_URL/decks" \
  -H 'Content-Type: application/json' \
  -d @- <<'JSON'
{
  "user_id": "learner-abc123",
  "title": "Week 3 — Kubernetes networking",
  "text": "A NodePort exposes a Service on a static port on every node. kube-proxy programs the rules cluster-wide, so the port answers on any node regardless of where the pod runs. A CNI must be installed before nodes report Ready."
}
JSON
# {"deck_id":"2ba55aee-…","card_count":10,"source_s3_key":null}
```

## Do not

- **Do not retry an empty deck with the same material.** `card_count: 0` means the
  material was too thin, not that the call failed. Retrying spends another LLM pass to
  get the same answer.
- **Do not send the whole syllabus as one deck** to "save calls." The 40-card cap
  makes it lossy, and one giant deck defeats spaced repetition — the learner cannot
  study Week 3 alone.
- **Do not invent a `user_id`.** A typo creates decks that exist, cost LLM calls, and
  are invisible to the learner, with no delete endpoint to undo it.
- **Do not paraphrase API errors.** The `error` field is already written for a human.
- **Do not use `deck_id` on `/session/start`.** It is ignored (see above).

## Checklist before finishing

- [ ] `/health` checked; `llm: true` and `mcp_tools > 0`.
- [ ] `user_id` came from the user, not invented.
- [ ] Deck list shown to the user before any deck was created.
- [ ] One deck per syllabus-named topic; titles keep the original numbering.
- [ ] Calls made serially, not in parallel.
- [ ] Every response's `card_count` reported, and every `warning` surfaced.
- [ ] Thin topics flagged rather than silently turned into empty decks.
- [ ] `POST /session/start` confirms the decks exist with the expected counts.
