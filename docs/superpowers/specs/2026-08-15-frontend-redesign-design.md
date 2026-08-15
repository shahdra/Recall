# Frontend redesign — pages, solitaire stacks, card motion

**Date:** 2026-08-15
**Status:** approved for implementation
**Scope:** `services/frontend` only. No backend, infrastructure, or API changes.

## Context

The frontend is a single route (`app/page.tsx` → `components/app-shell.tsx`) that
swaps three views with a `tab` state variable. Everything works, but the study screen
shows one static card with the answer appended below it after grading, and decks are
chosen from a `<select>`.

This redesign does two things: turns the three views into real pages with real URLs,
and makes the study experience card-like — decks as visible piles, one card at a time
growing into center stage, flipping to reveal the answer.

Verified against the code, because two facts shape the whole design:

- **`POST /session/start` returns every due card plus deck metadata in one call**
  (`lib/api.ts:89`, `services/tutor-agent/app.py:543`). There is no per-deck session
  endpoint, so per-deck filtering is client-side and needs no backend work.
- **Cards per deck cap at 40** (`card_generator.py:22`, `DEFAULT_MAX_CARDS`). This is
  what makes an uncapped one-edge-per-card stack affordable — 40 nodes, not thousands.

## Non-goals

- No backend changes. No new endpoints, no schema changes.
- No new dependencies. All motion is CSS transforms plus Tailwind keyframes; a
  library like Framer Motion would add ~30 KB for what the browser composites natively.
- The Progress screen's content is unchanged.
- Dark mode is out of scope here. The dark palette in `globals.css` is currently dead
  code (`darkMode: ["class"]` with nothing setting the class) and stays that way; a
  toggle is a separate change.

---

## 1. Routing

Five routes replace the single page:

| Route | Purpose |
|---|---|
| `/` | Home — three choices |
| `/add` | Add materials |
| `/study` | Due decks, each as a solitaire pile |
| `/study/[deckId]` | Studying one deck |
| `/progress` | Progress |

`components/app-shell.tsx` is **deleted**. It holds two pieces of state that need new
homes:

**`userId`** — read from `localStorage` on mount, passed down as a prop. Becomes a
`useUserId()` hook in `lib/use-user-id.ts`, called by each page that needs it. The
localStorage key and the generated-id format are unchanged, so existing learners keep
their cards. The hook returns `""` until mounted (there is no `localStorage` during
server rendering), and every consumer already handles an empty user id by not
fetching.

**`reloadKey`** — a counter bumped after a deck is created, so the Study and Progress
tabs refetch instead of showing stale data. **This disappears entirely.** Navigating to
a route mounts its page, and mounting fetches. `/add` calls
`router.push("/study")` on success, which is a fresh mount. `progress-view.tsx` and
`upload-view.tsx` lose the prop.

**"All decks" becomes a route.** Today it is a `__all__` sentinel in a `<select>`.
It becomes `/study/all` — a real URL like any other deck's. The reserved id is
documented in the page and cannot collide, because deck ids are UUIDs.

Each page carries a back link to `/`. The header stops being navigation: it is a title
and nothing else.

### Direct entry must work

Any of these URLs can be opened cold — from a refresh, a bookmark, or the back
button. So `/study/[deckId]` fetches `/session/start` itself and filters to its deck,
rather than reading cards handed down from `/study`.

The cost is one redundant fetch when navigating `/study` → `/study/[deckId]`. The
alternative — holding cards in a layout-level context — still needs the fetch path for
cold entry, so it means writing both. Per-page fetching is one path that always works.

---

## 2. Home (`/`)

Three large cards: **Add materials**, **Study**, **Progress**. Each is a link, not a
button, so middle-click and open-in-new-tab behave.

Each shows a one-line description of what it does. Study additionally shows the number
of cards due, fetched on mount — the one number that decides whether you came here to
study at all. If the fetch fails the count is omitted rather than shown as zero: a
silent "0 due" would read as "nothing to do" when the truth is "we could not tell."

Hover lifts each card slightly. Keyboard focus is visible.

---

## 3. Add materials (`/add`)

Wraps the existing `components/upload-view.tsx` — deck name, notes textarea, PDF
upload, and the "Make flashcards" button, with the explanation of how it works. That
component is unchanged apart from its `onDeckCreated` callback, which now navigates:

```
onDeckCreated → toast(`${cardCount} cards ready`) → router.push("/study")
```

---

## 4. Study (`/study`)

One `<DeckStack>` per deck with cards due, plus an "All decks" pile that interleaves
everything. Each pile shows the deck name above it and a count label.

Piles are laid out in a responsive grid. Clicking one navigates to
`/study/[deckId]`.

Empty state: when nothing is due, the existing friendly message and the demo-clock
control (see §7), because "nothing due → press +1 day → cards appear" is the clearest
demonstration that the schedule is real.

---

## 5. `<DeckStack>` — the solitaire pile

**One rendered edge per due card, uncapped.** A 3-card deck shows 3 edges; a 40-card
deck shows 40. The count label stays above regardless.

**Spacing compresses as the pile grows** so the stack occupies a fixed height
whatever the count:

```
BASE_OFFSET = 10   // px between edges when the pile is small
MAX_HEIGHT  = 260  // px the whole pile may occupy
CARD_HEIGHT = 108  // px of the topmost card face

offset = min(BASE_OFFSET, (MAX_HEIGHT - CARD_HEIGHT) / max(1, count - 1))
```

So a 5-card pile spaces at the full 10 px (40 px tall), while a 40-card pile tightens
to ~3.9 px (still 260 px tall). Every card keeps its own visible edge, and the whole
pile stays visible at a glance — which is the point of showing the size. A fixed
offset would make 40 cards a 400 px column, taller than a phone viewport and taller
than the card beside it.

A dense pile also *reads* as a big pile, so the compression carries information rather
than merely saving space.

Each edge alternates a fraction of a degree of rotation, so the pile reads as
hand-stacked rather than as a mechanical gradient.

---

## 6. Studying a deck (`/study/[deckId]`)

Two independent areas.

### Right: the due pile

A `<DeckStack>` of the cards still to answer in this deck. It shrinks by one edge
each time a card is answered, so progress is visible without reading the counter.

### Center: one card

Exactly one card at a time — never a growing pile of answered cards.

### The card

A two-sided flip card. Front is the question. Back is the real answer, **what the
learner submitted**, and the grade: correct/incorrect, quality out of 5, the
explanation, and when the next review falls.

Showing the submitted answer beside the right one is new. Today it is lost the moment
the grade lands, which makes "why was I marked wrong?" unanswerable from the screen.

Both faces are absolutely positioned, so the container gets its height from a hidden
copy of the longer face's content. The card therefore sizes to whichever side is
taller and never clips.

### Phases

A single union type, not several booleans — the illegal combinations matter
("grading and also revealed" is a bug):

```
answering → grading → revealed → leaving → (answering, next card)
```

| Phase | Card | Textarea | Controls |
|---|---|---|---|
| `answering` | front; clicking does nothing | editable, focused | Check · I don't know · mic |
| `grading` | still front, unchanged | **disabled** | Check → spinner; others disabled |
| `revealed` | **auto-flips** to back | hidden | Next card · Flip |
| `leaving` | shrinks and fades | resets | — |

Three consequences, all deliberate:

**The card does not flip on the click.** It flips when grading returns — 1–3 s later,
since that is a Bedrock call. So the flip doubles as the signal that grading finished.
Flipping earlier would show the answer while the verdict was still loading beneath it.

**The answer is final once submitted.** The textarea disables the instant Check is
pressed, and the submitted text is frozen in state at that moment. Grading has run and
SM-2 has already written the new interval, so editing afterwards would mean the back
face reports something other than what was graded.

**Flipping back is free, both ways.** A Flip button appears after the reveal, and
clicking the card toggles too. Flipping is display state only: it never re-grades and
never touches the schedule.

If grading **fails**, the phase returns to `answering` and the submitted text is
cleared. Nothing was graded, so the answer must stay editable — leaving it disabled
would strand the learner on a card they cannot answer.

### Motion

| Moment | Motion | Duration |
|---|---|---|
| Card arrives | grows from small, travelling from the pile's position to center | ~450 ms |
| Grade returns | flips 180° on the Y axis | ~620 ms |
| Next clicked | shrinks and fades away, then the next grows in | ~450 ms |

The exit is symmetric with the entrance and carries no directional meaning — the
grade is already stated on the card face.

Each animation is restarted by changing a React `key`, because remounting is the only
reliable way to replay a CSS animation that has already run; toggling a class does
nothing the second time.

Under `prefers-reduced-motion` every transition collapses to ~0 ms rather than being
removed. The state change still happens — the answer still appears, the next card
still arrives — it just does not travel. Removing the animation outright would leave
someone unable to tell that the card changed.

### End of deck

A **"Choose next deck"** button returning to `/study`. When other decks still have
cards due it says so, so finishing a deck reads as a milestone rather than a dead end.

### Responsive

Below `lg` (1024 px) the pile moves above the card as a horizontal strip. A
side-by-side layout has no room on a phone, and the card is what matters there.

---

## 7. Progress (`/progress`)

Wraps `components/progress-view.tsx` unchanged, minus the `reloadKey` prop.

## The demo clock

The `⏩ +1 day` control (`RECALL_DEMO_MODE`) currently lives in the study view. It
moves to `/study`, and appears on both the populated and empty states there — the
empty state especially, since that is where it demonstrates the most.

It does **not** appear on `/study/[deckId]`: moving the clock mid-deck would change
which cards are due underneath an in-progress session.

---

## 8. Files

**New**

```
app/add/page.tsx
app/study/page.tsx
app/study/[deckId]/page.tsx
app/progress/page.tsx
components/deck-stack.tsx      one solitaire pile + count label
components/flashcard.tsx       the two-sided flip card
components/study-session.tsx   center stage, phases, motion, grading
lib/use-user-id.ts
```

**Rewritten**

```
app/page.tsx                   home, three choices
```

**Modified**

```
components/upload-view.tsx     onDeckCreated navigates
components/progress-view.tsx   drops reloadKey
app/globals.css                3D + motion utilities
tailwind.config.ts             keyframes
```

**Deleted**

```
components/app-shell.tsx       tab state replaced by routes
components/study-view.tsx      589 lines; session logic moves to study-session.tsx
```

The split matters for more than tidiness: `study-view.tsx` is already 589 lines, and
adding a flip card, two stacks, and the motion cycle would push one file past 800.
Each new component has one job — `deck-stack` draws a pile, `flashcard` draws two
faces, `study-session` owns the phase machine — and can be reasoned about alone.

---

## 9. Verification

1. `npx tsc --noEmit` — clean.
2. `npm run build` — compiles, lints, and confirms the bundle has not grown
   materially (no new dependencies, so it should not).
3. Full `docker compose up` stack, then click through:
   - `/` → each of the three destinations
   - `/add` → make a deck → lands on `/study` with the new deck present
   - `/study` → pile edge counts match the labels
   - a deck → answer → flip → flip back → Next → grows in → last card → Choose next deck
   - **Direct entry**: reload on `/study/[deckId]`; it must load, not error
   - **Back button** at every step
   - "I don't know" → grades 0, still flips, reschedules for tomorrow
   - Enter submits, Shift+Enter newlines, Enter advances after reveal
4. Confirm no application-code changes outside `services/frontend`.

## Risks

- **`/study/[deckId]` for a deck with nothing due** — reachable via a stale bookmark
  or by finishing a deck in another tab. Renders the end-of-deck state rather than an
  error or an empty screen.
- **An unknown `deckId`** — same treatment: the end-of-deck state with a route back to
  `/study`. Not a 404, because a deck whose cards are all answered is a normal
  outcome, not a broken URL.
- **The extra fetch on deck entry** — accepted, per §1.
- **`tmp/` holds design mockups** and is currently untracked; it should be gitignored
  or removed rather than committed as part of this work.
