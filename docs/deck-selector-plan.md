# Plan — deck selector in the study view

## Goal

While studying, know which deck a card belongs to, and be able to study one deck
at a time. Today `get_due_cards` returns every due card for a `user_id` across
all decks as one interleaved queue, and the UI shows only `card.topic` — so with
two decks due there is nothing marking the switch from Kubernetes to cell
biology.

## Behaviour to build

A dropdown above the card:

```
┌─────────────────────────────────┐
│ All decks (23 due)          ▾  │  ← default when >1 deck has cards due
├─────────────────────────────────┤
│ All decks (23 due)              │
│ Kubernetes (14 due)             │
│ Cell biology (9 due)            │
└─────────────────────────────────┘
```

- **More than one deck due** → dropdown lists "All decks" plus each deck with its
  due count. Defaults to **All decks**, keeping the queue interleaved (mixed
  practice retains better than blocking one subject at a time; this preserves
  that as the default while still giving the filter).
- **Exactly one deck due** → no dropdown. "All decks" would be a choice between
  one thing and itself.
- **Nothing due** → no dropdown, existing 🎉 empty state unchanged.
- **Finishing a filtered deck** → auto-advance to the deck with the most cards
  still due, so the session front-loads the heavy work instead of trailing off.
  Only when every deck is done does the 🎉 state appear.
- **Deck name on the card** → small label beside the existing topic chip, visible
  while answering. Shown when studying All decks (where you cannot infer it);
  redundant when a single deck is selected, since the dropdown already says it.

Switching decks mid-card discards the unanswered card and starts the new deck at
its first card. Nothing is lost — an unanswered card stays due.

## Changes

### 1. `services/tutor-agent/app.py` — join deck titles onto due cards

`session_start` currently returns `{"cards": [...], "profile": {...}}`. Cards
carry `deck_id` (the partition key) but not the title, which lives in the `Decks`
table.

- Additionally call the existing `list_decks` MCP tool, build a
  `{deck_id: title}` map, and set `deck_title` on each card.
- Add a `decks` array to the response: `{deck_id, title, due_count}`, built from
  the due cards so it lists only decks with something due — that is exactly what
  the dropdown needs, and it saves the browser recomputing counts.
- **Degrade, never fail:** if `list_decks` errors or a `deck_id` has no matching
  deck, fall back to `deck_title: "Untitled deck"` and still return the cards. A
  missing label must never cost a study session. This mirrors the existing
  profile-summarization behaviour.

Server-side rather than in the browser so there is one place to test, and the
smoke test can assert on the payload.

### 2. `services/frontend/lib/types.ts`

- `Card.deck_title?: string`
- New `DueDeck { deck_id, title, due_count }`
- `SessionStartResponse.decks?: DueDeck[]`

All optional, so a frontend built against an older agent still compiles.

### 3. `services/frontend/components/study-view.tsx`

- Keep the full due list in state; derive the studied queue from the selected
  deck (`null` = all). Avoids refetching on every switch — the cards are already
  in hand.
- Render the dropdown only when `decks.length > 1`.
- `handleNext` at end-of-queue: if a filtered deck is finished and other decks
  still have unanswered cards, select the largest and continue; else show 🎉.
- Show `deck_title` beside the topic chip when studying All decks.
- "Card 4 of 15" already recomputes from the derived queue, so it becomes
  per-deck automatically when one is selected.

## Tests

**`services/tutor-agent/tests/test_app.py`**
- due cards come back with `deck_title` filled from the deck table
- `decks` lists only decks with due cards, with correct `due_count`
- a card whose deck is missing gets `"Untitled deck"`, and the session still returns cards
- `list_decks` raising does not fail `/session/start`
- nothing due → `decks` empty, `message` still present

**`services/frontend/smoke.mjs`** (real browser, real Bedrock)
- create two decks, then assert the dropdown appears and lists both with counts
- select one deck, verify the card count drops to that deck's due count
- answer through it and assert the selector advances to the other deck rather
  than showing 🎉

The two-deck case is the whole point of the feature and cannot be covered by
unit tests, so it goes in the smoke test.

## Out of scope

- Reordering *within* a deck (still due-date order from the GSI).
- Persisting the selected deck across reloads.
- Deck management (rename/delete) — not asked for.

## Risk

Low. Additive: new optional fields, one new MCP call on session start, no schema
change, no change to SM-2 or grading. Worst case on a backend error is the
current behaviour — an unlabelled interleaved queue.
