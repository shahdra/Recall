# Frontend Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single tab-swapped page with five real routes, and rebuild the study experience so decks are visible solitaire piles and one card at a time grows into center stage and flips to reveal its answer and grade.

**Architecture:** Next.js App Router file-based routes replace the `tab` state in `app-shell.tsx`. Each page fetches its own data on mount, so any URL survives a refresh. The card flip and stack motion are CSS transforms plus Tailwind keyframes — no animation library. The study session's answer flow is a four-phase state machine (`answering → grading → revealed → leaving`) that makes illegal states unrepresentable.

**Tech Stack:** Next.js 14 (App Router), React 18, TypeScript, Tailwind CSS 3.4, lucide-react, sonner.

**Spec:** `docs/superpowers/specs/2026-08-15-frontend-redesign-design.md`

## Global Constraints

- **No new dependencies.** All motion is CSS transforms and Tailwind keyframes.
- **No backend changes.** No new endpoints, no schema changes, nothing outside `services/frontend`.
- **`POST /session/start` returns every due card plus deck metadata in one call.** Per-deck filtering is client-side.
- **Cards per deck cap at 40** (`services/tutor-agent/card_generator.py:22`). This is why stack edges are uncapped.
- **`RECALL_DEMO_MODE` must stay absent from prod manifests** — do not add it anywhere.
- **Dark mode is out of scope.** `darkMode: ["class"]` stays unwired; do not add a toggle.
- **Reserved deck id:** `all` is the "All decks" route. Real deck ids are UUIDs, so no collision.
- **Preserve the localStorage key** `recall.user_id` and the `learner-xxxxxxxx` id format, or existing learners lose their cards.

## Verification approach

There is **no frontend test suite**: Playwright is in `package.json` but has no config, no test script, and CI never runs it. Per the user's decision, each task is verified by:

1. `cd services/frontend && npx tsc --noEmit` — must be silent.
2. `npm run build` — must compile and lint clean.
3. A manual click-through in the running compose stack, scripted per task.

**This leaves no regression net.** A change in task 8 can silently break task 3's behaviour and nothing will catch it. Mitigation: task 9 is a full end-to-end click-through of every route and interaction, run once at the end.

Start the stack once before task 2 and leave it running:

```bash
cd /Users/saed/shahd/Recall
docker compose up -d
# after each frontend change:
docker compose up -d --build frontend
```

The app serves on `http://localhost:3000`, the agent on `http://localhost:3500`.

---

### Task 1: The `useUserId` hook

Extracts the learner-identity logic from `app-shell.tsx` so every page can call it. Nothing else can be built until pages have a user id.

**Files:**
- Create: `services/frontend/lib/use-user-id.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: `useUserId(): string` — returns `""` until mounted, then the persisted id.

- [ ] **Step 1: Create the hook**

Create `services/frontend/lib/use-user-id.ts`:

```ts
"use client";

import { useEffect, useState } from "react";

/** localStorage key. Changing this orphans every existing learner's cards. */
const USER_STORAGE_KEY = "recall.user_id";

/**
 * Identify the learner.
 *
 * Auth is out of scope (docs/spec.md), so a per-browser id stands in for an
 * account. Persisting it in localStorage is what makes long-term memory
 * observable: reload the page and the tutor still knows what you struggle with.
 *
 * Was a function in app-shell.tsx, called once and passed down as a prop. Now that
 * pages are routes rather than tabs there is no common parent to hold it, so each
 * page calls this for itself.
 */
function loadUserId(): string {
  if (typeof window === "undefined") return "";
  const existing = window.localStorage.getItem(USER_STORAGE_KEY);
  if (existing) return existing;
  const generated = `learner-${Math.random().toString(36).slice(2, 10)}`;
  window.localStorage.setItem(USER_STORAGE_KEY, generated);
  return generated;
}

/**
 * Returns "" on the first render and the real id after mount.
 *
 * The empty first value is not avoidable: there is no localStorage during server
 * rendering, and reading it during render would produce a hydration mismatch.
 * Every caller must therefore skip fetching while the id is empty — the effects
 * that consume it already guard on that.
 */
export function useUserId(): string {
  const [userId, setUserId] = useState("");
  useEffect(() => {
    setUserId(loadUserId());
  }, []);
  return userId;
}
```

- [ ] **Step 2: Typecheck**

Run: `cd services/frontend && npx tsc --noEmit`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add services/frontend/lib/use-user-id.ts
git commit -m "refactor(frontend): extract useUserId hook

Pages become routes in the next commits, so there is no longer a common
parent component to hold the learner id and pass it down. Each page calls
this instead. Same localStorage key and id format, so existing learners
keep their cards."
```

---

### Task 2: CSS and Tailwind motion primitives

Everything visual depends on these. Adding them first means later tasks only write markup.

**Files:**
- Modify: `services/frontend/app/globals.css:46-57`
- Modify: `services/frontend/tailwind.config.ts:32-37`

**Interfaces:**
- Produces: utility classes `perspective-card`, `preserve-3d`, `backface-hidden`, `rotate-y-180`, `card-motion`; animations `animate-card-grow`, `animate-card-shrink`.

- [ ] **Step 1: Replace the utilities layer in globals.css**

Find the existing `@layer utilities` block (lines 46-57) and replace it entirely:

```css
@layer utilities {
  /* 3D flip primitives. Tailwind ships no transform-style or backface utilities,
     so these are the minimum needed for a two-sided card. */
  .preserve-3d {
    transform-style: preserve-3d;
  }
  .backface-hidden {
    backface-visibility: hidden;
    /* Safari still needs the prefix. Without it BOTH faces render and the back
       shows through the front as mirrored text. */
    -webkit-backface-visibility: hidden;
  }
  .rotate-y-180 {
    transform: rotateY(180deg);
  }
  .perspective-card {
    perspective: 1500px;
  }
}

/* Reduced motion: collapse card transitions to instant rather than removing them.
   The state change still happens — the answer still appears, the next card still
   arrives — it just does not travel. Removing the animation outright would leave
   someone who prefers less motion unable to tell that the card changed. */
@media (prefers-reduced-motion: reduce) {
  .card-motion,
  .card-motion * {
    transition-duration: 0.01ms !important;
    animation-duration: 0.01ms !important;
  }
}
```

- [ ] **Step 2: Add keyframes to tailwind.config.ts**

In `theme.extend`, immediately after the `borderRadius` block, add:

```ts
      // Study-screen motion. Hand-rolled rather than adding Framer Motion: every
      // one of these is a transform, which the browser composites on the GPU, and
      // the library would add ~30KB to a bundle with no animation dependency today.
      keyframes: {
        // A card arriving at center stage: grows from small while travelling in
        // from the pile's side of the screen.
        "card-grow": {
          "0%": { transform: "translateX(18%) scale(0.42)", opacity: "0" },
          "60%": { opacity: "1" },
          "100%": { transform: "none", opacity: "1" },
        },
        // An answered card leaving. Symmetric with the entrance and carrying no
        // directional meaning — the grade is already stated on the card face.
        "card-shrink": {
          "0%": { transform: "none", opacity: "1" },
          "100%": { transform: "scale(0.42)", opacity: "0" },
        },
      },
      animation: {
        "card-grow": "card-grow 0.45s cubic-bezier(0.2, 0.8, 0.25, 1)",
        "card-shrink": "card-shrink 0.45s cubic-bezier(0.4, 0, 0.7, 0.2) forwards",
      },
```

- [ ] **Step 3: Verify the classes compile**

Run: `cd services/frontend && npm run build`
Expected: `✓ Compiled successfully`.

Tailwind only emits utilities it finds in scanned files, so `animate-card-grow` will not appear in the CSS until a component uses it. That is expected — this step only proves the config parses.

- [ ] **Step 4: Commit**

```bash
git add services/frontend/app/globals.css services/frontend/tailwind.config.ts
git commit -m "feat(frontend): 3D and card-motion CSS primitives

backface-visibility needs the -webkit- prefix or Safari renders both faces
and the back shows through as mirrored text.

Reduced motion collapses transitions to ~0ms rather than removing them, so
the state change is still perceptible without the travel."
```

---

### Task 3: `<DeckStack>` — the solitaire pile

One rendered edge per due card, uncapped, with spacing that compresses so the pile holds a fixed height.

**Files:**
- Create: `services/frontend/components/deck-stack.tsx`

**Interfaces:**
- Consumes: `cn` from `@/lib/utils`.
- Produces: default export `DeckStack`, props `{ count: number; label?: string; sublabel?: string; className?: string }`.

- [ ] **Step 1: Create the component**

Create `services/frontend/components/deck-stack.tsx`:

```tsx
"use client";

import { cn } from "@/lib/utils";

interface Props {
  /** Cards due in this deck. One rendered edge per card. */
  count: number;
  /** Deck name, shown above the pile. */
  label?: string;
  /** Secondary line under the label, e.g. "7 due". */
  sublabel?: string;
  className?: string;
}

/** Spacing between edges when the pile is small, in px. */
const BASE_OFFSET = 10;
/** Height the whole pile may occupy, in px. */
const MAX_HEIGHT = 260;
/** Height of the topmost card face, in px. */
const CARD_HEIGHT = 108;

/**
 * A deck drawn as a solitaire pile: one visible edge per due card.
 *
 * Edges are NOT capped. Cards per deck cap at 40 upstream
 * (services/tutor-agent/card_generator.py DEFAULT_MAX_CARDS), so the worst case is
 * 40 nodes — cheap enough that capping would trade honesty for nothing.
 *
 * Spacing compresses instead. At a fixed 10px, 40 cards would be a 400px column —
 * taller than a phone viewport and taller than the card beside it. Compressing to a
 * fixed MAX_HEIGHT keeps the whole pile visible at a glance, which is the point of
 * showing its size. A dense pile also READS as a big pile, so the compression
 * carries information rather than only saving space.
 */
export default function DeckStack({ count, label, sublabel, className }: Props) {
  const offset =
    count > 1
      ? Math.min(BASE_OFFSET, (MAX_HEIGHT - CARD_HEIGHT) / (count - 1))
      : BASE_OFFSET;

  // The container must be tall enough for the last edge plus the card itself, or
  // the pile overflows whatever sits below it.
  const height = CARD_HEIGHT + offset * Math.max(0, count - 1);

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      {label && (
        <div className="space-y-0.5">
          <p className="truncate text-sm font-semibold">{label}</p>
          {sublabel && <p className="text-xs text-muted-foreground">{sublabel}</p>}
        </div>
      )}

      <div className="relative w-full" style={{ height }}>
        {Array.from({ length: count }, (_, i) => (
          <div
            key={i}
            aria-hidden
            className="absolute inset-x-0 rounded-xl border bg-card shadow-sm"
            style={{
              top: i * offset,
              height: CARD_HEIGHT,
              // Alternating fractions of a degree read as hand-stacked rather than
              // as a mechanical gradient.
              transform: `rotate(${i % 2 === 0 ? 0.4 : -0.5}deg)`,
              // Later cards draw on top, so the pile reads as building upward.
              zIndex: i,
            }}
          />
        ))}
        {count === 0 && (
          <div className="absolute inset-x-0 top-0 flex items-center justify-center rounded-xl border border-dashed text-xs text-muted-foreground" style={{ height: CARD_HEIGHT }}>
            nothing due
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Typecheck**

Run: `cd services/frontend && npx tsc --noEmit`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add services/frontend/components/deck-stack.tsx
git commit -m "feat(frontend): DeckStack solitaire pile

One edge per due card, uncapped: cards cap at 40 upstream, so the worst case
is 40 nodes and capping would trade honesty for nothing.

Spacing compresses to hold a fixed 260px height instead. 40 cards at a fixed
10px offset would be a 400px column, taller than the card beside it."
```

---

### Task 4: `<Flashcard>` — the two-sided flip card

**Files:**
- Create: `services/frontend/components/flashcard.tsx`

**Interfaces:**
- Consumes: `Card`, `GradeResponse` from `@/lib/types`; `cn`, `formatInterval` from `@/lib/utils`; the utilities from Task 2.
- Produces: default export `Flashcard`; named export `type CardPhase = "answering" | "grading" | "revealed" | "leaving"`. Props: `{ card: Card; phase: CardPhase; verdict: GradeResponse | null; submitted: string; flipped: boolean; onFlip: () => void }`.

- [ ] **Step 1: Create the component**

Create `services/frontend/components/flashcard.tsx`:

```tsx
"use client";

import { Check, X } from "lucide-react";

import type { Card, GradeResponse } from "@/lib/types";
import { cn, formatInterval } from "@/lib/utils";

/**
 * Where the learner is on the current card. One union rather than several
 * booleans, because the illegal combinations matter: "grading and also revealed"
 * or "revealed with no verdict" would each be a bug, and a union makes them
 * unrepresentable.
 */
export type CardPhase = "answering" | "grading" | "revealed" | "leaving";

interface Props {
  card: Card;
  phase: CardPhase;
  /** Non-null from `revealed` onward. */
  verdict: GradeResponse | null;
  /** What the learner submitted, frozen at submit time and echoed on the back. */
  submitted: string;
  /** Which face shows. Owned by the parent so flip-back is its decision. */
  flipped: boolean;
  onFlip: () => void;
}

export default function Flashcard({
  card,
  phase,
  verdict,
  submitted,
  flipped,
  onFlip,
}: Props) {
  // Clicking flips, but ONLY once the answer is out. Before that a click would let
  // the learner peek at the answer they are about to be graded on.
  const canFlip = phase === "revealed";

  return (
    <div className="perspective-card">
      <div
        className={cn(
          "card-motion preserve-3d relative w-full",
          "transition-transform duration-[620ms] [transition-timing-function:cubic-bezier(.22,.84,.28,1)]",
          flipped && "rotate-y-180",
          canFlip && "cursor-pointer",
        )}
        onClick={canFlip ? onFlip : undefined}
      >
        {/*
          Both faces are absolutely positioned, so the container would collapse to
          zero height. This hidden copy of the longer face's content is what gives
          the card its height, meaning it sizes to whichever side is taller and
          never clips. A fixed height would waste space on short cards and crop long
          explanations.
        */}
        <div aria-hidden className="invisible flex flex-col gap-3 p-6">
          <p className="text-xl font-semibold leading-snug">{card.front}</p>
          <p className="leading-relaxed">{card.back}</p>
          {submitted && <p className="text-sm">{submitted}</p>}
          {verdict && <p className="text-sm leading-relaxed">{verdict.explanation}</p>}
          <p className="text-xs">Next review placeholder line</p>
        </div>

        {/* ---- FRONT: the question ---- */}
        <div className="backface-hidden absolute inset-0 flex flex-col gap-3 overflow-auto rounded-2xl border bg-card p-6 shadow-lg">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            Question
          </p>
          <p className="text-xl font-semibold leading-snug">{card.front}</p>
          <div className="mt-auto flex items-center justify-between gap-2 text-xs text-muted-foreground">
            <span className="rounded-full bg-muted px-2.5 py-0.5">{card.topic}</span>
            {phase === "grading" && <span>checking…</span>}
            {canFlip && <span>tap to flip</span>}
          </div>
        </div>

        {/* ---- BACK: real answer, what you said, and the grade ---- */}
        <div className="backface-hidden rotate-y-180 absolute inset-0 flex flex-col gap-3 overflow-auto rounded-2xl border bg-card p-6 shadow-lg">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            Answer
          </p>
          <p className="leading-relaxed">{card.back}</p>

          {/* The learner's own words beside the right answer. Today this is lost the
              moment the grade lands, which makes "why was I marked wrong?"
              unanswerable from the screen. */}
          <p className="rounded-lg border border-dashed bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
            You said:{" "}
            <span className="font-medium text-foreground">
              {submitted.trim() || "— nothing —"}
            </span>
          </p>

          {verdict && (
            <div
              className={cn(
                "flex items-start gap-3 rounded-lg border p-3",
                verdict.is_correct
                  ? "border-success/40 bg-success/10"
                  : "border-danger/40 bg-danger/10",
              )}
            >
              <span
                className={cn(
                  "mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full",
                  verdict.is_correct
                    ? "bg-success text-success-foreground"
                    : "bg-danger text-danger-foreground",
                )}
              >
                {verdict.is_correct ? <Check className="h-3.5 w-3.5" /> : <X className="h-3.5 w-3.5" />}
              </span>
              <div className="space-y-1">
                <p className="text-sm font-semibold">
                  {verdict.is_correct ? "Correct" : "Not quite"}
                  <span className="ml-2 text-xs font-normal text-muted-foreground">
                    graded {verdict.quality}/5
                  </span>
                </p>
                <p className="text-xs leading-relaxed">{verdict.explanation}</p>
                {verdict.interval_days !== null && (
                  <p className="text-[11px] text-muted-foreground">
                    Next review {formatInterval(verdict.interval_days)}
                    {verdict.due_date ? ` (${verdict.due_date})` : ""}.
                  </p>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Typecheck**

Run: `cd services/frontend && npx tsc --noEmit`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add services/frontend/components/flashcard.tsx
git commit -m "feat(frontend): two-sided flip card

The back now shows what the learner submitted beside the right answer. Today
that text is lost the moment the grade lands, which makes 'why was I marked
wrong?' unanswerable from the screen.

Card height comes from a hidden copy of the longer face, so it sizes to
whichever side is taller and never clips."
```

---

### Task 5: `<StudySession>` — center stage, phases, grading

The largest task. Holds the phase machine and the answer flow, replacing the session logic in `study-view.tsx`.

**Files:**
- Create: `services/frontend/components/study-session.tsx`

**Interfaces:**
- Consumes: `Flashcard`, `CardPhase` (Task 4); `DeckStack` (Task 3); `submitAnswer`, `transcribe` from `@/lib/api`; `useRecorder`; `Card` from `@/lib/types`.
- Produces: default export `StudySession`, props `{ userId: string; cards: Card[]; deckTitle: string; onFinished: () => void }`.

- [ ] **Step 1: Create the component**

Create `services/frontend/components/study-session.tsx`:

```tsx
"use client";

import { Loader2, Mic, RotateCcw, Square } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { submitAnswer, transcribe } from "@/lib/api";
import type { Card, GradeResponse } from "@/lib/types";
import { useRecorder } from "@/lib/use-recorder";
import { cn } from "@/lib/utils";
import DeckStack from "./deck-stack";
import Flashcard, { type CardPhase } from "./flashcard";

interface Props {
  userId: string;
  /** Every card due in this deck, in study order. */
  cards: Card[];
  deckTitle: string;
  /** Called when the last card is answered — the page routes back to /study. */
  onFinished: () => void;
}

/** Must match the card-shrink animation duration in tailwind.config.ts. */
const EXIT_MS = 450;

export default function StudySession({ userId, cards, deckTitle, onFinished }: Props) {
  const [index, setIndex] = useState(0);
  const [answer, setAnswer] = useState("");
  const [verdict, setVerdict] = useState<GradeResponse | null>(null);
  const [phase, setPhase] = useState<CardPhase>("answering");
  const [flipped, setFlipped] = useState(false);
  /** Frozen at submit time so the back face reports what was actually graded. */
  const [submitted, setSubmitted] = useState("");
  const [transcribing, setTranscribing] = useState(false);

  const recorder = useRecorder();
  const answerRef = useRef<HTMLTextAreaElement>(null);

  const card = cards[index];
  const remaining = cards.length - index;
  const isLast = index === cards.length - 1;

  async function handleSubmit(event?: React.FormEvent) {
    event?.preventDefault();
    // Guarding on the phase rather than on booleans also blocks a double-submit
    // during the exit animation.
    if (!card || phase !== "answering") return;

    setSubmitted(answer);
    setPhase("grading");
    try {
      const result = await submitAnswer(userId, card, answer);
      setVerdict(result);
      // The flip IS the "grading finished" signal: it fires on the response rather
      // than on the click, so the answer never appears before the verdict that
      // goes with it.
      setPhase("revealed");
      setFlipped(true);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Couldn't grade that.");
      // Nothing was graded, so the answer must stay editable and re-submittable.
      // Leaving it disabled would strand the learner on a card they cannot answer.
      setPhase("answering");
      setSubmitted("");
    }
  }

  /** Display only. Never re-grades, never touches the schedule. */
  function handleFlip() {
    if (phase !== "revealed") return;
    setFlipped((f) => !f);
  }

  function handleNext() {
    if (phase !== "revealed") return;
    setPhase("leaving");
    window.setTimeout(() => {
      if (isLast) {
        onFinished();
        return;
      }
      setIndex((i) => i + 1);
      setVerdict(null);
      setFlipped(false);
      setSubmitted("");
      setAnswer("");
      setPhase("answering");
      answerRef.current?.focus();
    }, EXIT_MS);
  }

  async function handleMicClick() {
    if (recorder.recording) {
      setTranscribing(true);
      try {
        const audioB64 = await recorder.stop();
        if (!audioB64) {
          toast.error("I didn't catch any audio — please type your answer.");
          return;
        }
        const result = await transcribe(audioB64);
        if (!result.text) {
          // Voice is a convenience; typing always works.
          toast.error(result.message ?? "I couldn't make that out — please type it.");
          return;
        }
        setAnswer((current) => (current ? `${current} ${result.text}` : result.text));
        answerRef.current?.focus();
      } catch (error) {
        toast.error(error instanceof Error ? error.message : "Transcription failed.");
      } finally {
        setTranscribing(false);
      }
      return;
    }

    try {
      await recorder.start();
    } catch {
      toast.error("I couldn't use your microphone — please type your answer.");
    }
  }

  if (!card) return null;

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_240px] lg:items-start">
      {/* CENTER: exactly one card, plus its controls. Ordered second on mobile so
          the pile appears above it, since side-by-side has no room on a phone. */}
      <div className="order-2 space-y-4 lg:order-1">
        {/*
          Keyed on the card id so React builds a fresh node per card. Without the
          key the incoming card would inherit the outgoing one's transform and
          appear already shrunk away.
        */}
        <div
          key={card.card_id}
          className={cn(
            "card-motion",
            phase === "leaving" ? "animate-card-shrink" : "animate-card-grow",
          )}
        >
          <Flashcard
            card={card}
            phase={phase}
            verdict={verdict}
            submitted={submitted}
            flipped={flipped}
            onFlip={handleFlip}
          />
        </div>

        {phase === "answering" || phase === "grading" ? (
          <form onSubmit={handleSubmit} className="space-y-3">
            <label htmlFor="answer" className="text-sm font-medium">
              Your answer
            </label>
            <div className="flex items-end gap-2">
              <textarea
                id="answer"
                ref={answerRef}
                value={answer}
                onChange={(event) => setAnswer(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    handleSubmit();
                  }
                }}
                rows={3}
                // Disabled the instant Check is pressed. The answer has been sent
                // and SM-2 is being written against it, so it is committed.
                disabled={phase === "grading"}
                placeholder="Type your answer, or use the mic…"
                className="flex-1 resize-y rounded-lg border bg-card px-3 py-2 text-sm outline-none transition-opacity focus:ring-2 focus:ring-primary/40 disabled:bg-muted/50 disabled:opacity-60"
              />
              {!recorder.unsupported && (
                <button
                  type="button"
                  onClick={handleMicClick}
                  disabled={transcribing || phase === "grading"}
                  aria-label={recorder.recording ? "Stop recording" : "Record your answer"}
                  className={cn(
                    "flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border",
                    recorder.recording
                      ? "animate-pulse border-danger bg-danger text-danger-foreground"
                      : "bg-card hover:bg-muted",
                    (transcribing || phase === "grading") && "opacity-60",
                  )}
                >
                  {transcribing ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : recorder.recording ? (
                    <Square className="h-4 w-4" />
                  ) : (
                    <Mic className="h-4 w-4" />
                  )}
                </button>
              )}
            </div>

            {recorder.recording && (
              <p className="text-xs text-muted-foreground">
                Listening… tap the square when you&apos;re done.
              </p>
            )}

            <div className="flex gap-2">
              <button
                type="submit"
                disabled={phase === "grading"}
                className="inline-flex flex-1 items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground hover:opacity-90 disabled:opacity-60"
              >
                {phase === "grading" ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Checking…
                  </>
                ) : (
                  "Check my answer"
                )}
              </button>
              <button
                type="button"
                onClick={() => handleSubmit()}
                disabled={phase === "grading"}
                className="rounded-lg border bg-card px-4 py-2.5 text-sm font-medium hover:bg-muted disabled:opacity-60"
              >
                I don&apos;t know
              </button>
            </div>
          </form>
        ) : (
          /* After the reveal the verdict lives on the card's back face, so this is
             navigation only — which is why the card grew and this shrank. */
          <div className="flex gap-2">
            <button
              type="button"
              onClick={handleNext}
              disabled={phase === "leaving"}
              autoFocus
              className="flex-1 rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground hover:opacity-90 disabled:opacity-60"
            >
              {isLast ? "Choose next deck" : "Next card"}
            </button>
            <button
              type="button"
              onClick={handleFlip}
              disabled={phase === "leaving"}
              className="inline-flex items-center gap-1.5 rounded-lg border bg-card px-4 py-2.5 text-sm font-medium hover:bg-muted disabled:opacity-60"
            >
              <RotateCcw className="h-3.5 w-3.5" />
              {flipped ? "Question" : "Answer"}
            </button>
          </div>
        )}
      </div>

      {/* RIGHT: the pile still to answer. Shrinks by one edge per card, so progress
          is visible without reading the counter. */}
      <div className="order-1 lg:order-2">
        <DeckStack
          count={remaining}
          label={deckTitle}
          sublabel={`${remaining} card${remaining === 1 ? "" : "s"} to go`}
        />
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Typecheck**

Run: `cd services/frontend && npx tsc --noEmit`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add services/frontend/components/study-session.tsx
git commit -m "feat(frontend): StudySession center stage and phase machine

answering -> grading -> revealed -> leaving, as a union rather than
booleans: 'grading and also revealed' would be a bug, and a union makes it
unrepresentable.

The card flips when grading RETURNS, not on the click. That is a 1-3s
Bedrock call, so the flip doubles as the finished signal; flipping earlier
would show the answer while the verdict was still loading.

On a grading failure the phase returns to answering and the submitted text
clears -- nothing was graded, so leaving the textarea disabled would strand
the learner on a card they cannot answer."
```

---

### Task 6: Home, Add, and Progress pages

Three routes at once: they share the same shape (a page shell wrapping existing work) and none is independently rejectable.

**Files:**
- Modify: `services/frontend/app/page.tsx` (full rewrite)
- Create: `services/frontend/app/add/page.tsx`
- Create: `services/frontend/app/progress/page.tsx`
- Modify: `services/frontend/components/progress-view.tsx:11-14,24,53`

**Interfaces:**
- Consumes: `useUserId` (Task 1); existing `UploadView`, `ProgressView`; `startSession` from `@/lib/api`.
- Produces: routes `/`, `/add`, `/progress`.

- [ ] **Step 1: Drop `reloadKey` from progress-view**

In `services/frontend/components/progress-view.tsx`, change the props interface (lines 11-14) to:

```tsx
interface Props {
  userId: string;
}
```

Change the signature on line 24 to `export default function ProgressView({ userId }: Props) {` and the effect dependency array on line 53 from `[userId, reloadKey]` to `[userId]`.

`reloadKey` existed because tab switching does not remount. Routing does, and mounting fetches.

- [ ] **Step 2: Rewrite the home page**

Replace `services/frontend/app/page.tsx` entirely:

```tsx
"use client";

import { BarChart3, BookOpen, Upload } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { startSession } from "@/lib/api";
import { useUserId } from "@/lib/use-user-id";

export default function Home() {
  const userId = useUserId();
  const [due, setDue] = useState<number | null>(null);

  useEffect(() => {
    if (!userId) return;
    let active = true;
    startSession(userId)
      .then((session) => {
        if (active) setDue(session.cards.length);
      })
      // Deliberately silent, and `due` stays null rather than becoming 0. A "0 due"
      // badge would read as "nothing to do" when the truth is "we could not tell".
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [userId]);

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col justify-center px-4 py-10">
      <header className="mb-8">
        <h1 className="text-3xl font-bold tracking-tight">Recall</h1>
        <p className="mt-1.5 text-muted-foreground">
          Turns your material into a quiz that learns what you don&apos;t know.
        </p>
      </header>

      <div className="grid gap-4 sm:grid-cols-3">
        <HomeCard
          href="/add"
          icon={<Upload className="h-5 w-5" />}
          title="Add material"
          description="Paste notes or upload a PDF to make a new deck."
        />
        <HomeCard
          href="/study"
          icon={<BookOpen className="h-5 w-5" />}
          title="Study"
          description="Answer the cards that are due today."
          badge={due !== null ? `${due} due` : undefined}
        />
        <HomeCard
          href="/progress"
          icon={<BarChart3 className="h-5 w-5" />}
          title="Progress"
          description="See your weakest topics and review history."
        />
      </div>
    </main>
  );
}

function HomeCard({
  href,
  icon,
  title,
  description,
  badge,
}: {
  href: string;
  icon: React.ReactNode;
  title: string;
  description: string;
  badge?: string;
}) {
  // A Link, not a button, so middle-click and open-in-new-tab behave.
  return (
    <Link
      href={href}
      className="group flex flex-col gap-2 rounded-2xl border bg-card p-5 shadow-sm transition-[transform,box-shadow,border-color] duration-200 hover:-translate-y-1 hover:border-primary/40 hover:shadow-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
    >
      <div className="flex items-center justify-between">
        <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10 text-primary">
          {icon}
        </span>
        {badge && (
          <span className="rounded-full bg-primary px-2.5 py-0.5 text-xs font-semibold text-primary-foreground">
            {badge}
          </span>
        )}
      </div>
      <p className="font-semibold">{title}</p>
      <p className="text-sm text-muted-foreground">{description}</p>
    </Link>
  );
}
```

- [ ] **Step 3: Create the add page**

Create `services/frontend/app/add/page.tsx`:

```tsx
"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import UploadView from "@/components/upload-view";
import { useUserId } from "@/lib/use-user-id";

export default function AddPage() {
  const userId = useUserId();
  const router = useRouter();

  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col px-4 py-6">
      <BackLink />
      <h1 className="mb-6 text-2xl font-bold tracking-tight">Add material</h1>
      <UploadView
        userId={userId}
        onDeckCreated={(cardCount) => {
          toast.success(`${cardCount} card${cardCount === 1 ? "" : "s"} ready.`);
          // Navigating replaces the old reloadKey mechanism: /study mounts fresh
          // and fetches, so the new deck is there without any refetch signal.
          router.push("/study");
        }}
      />
    </main>
  );
}

function BackLink() {
  return (
    <Link
      href="/"
      className="mb-4 inline-flex w-fit items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
    >
      <ArrowLeft className="h-4 w-4" />
      Home
    </Link>
  );
}
```

- [ ] **Step 4: Create the progress page**

Create `services/frontend/app/progress/page.tsx`:

```tsx
"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";

import ProgressView from "@/components/progress-view";
import { useUserId } from "@/lib/use-user-id";

export default function ProgressPage() {
  const userId = useUserId();

  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col px-4 py-6">
      <Link
        href="/"
        className="mb-4 inline-flex w-fit items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
      >
        <ArrowLeft className="h-4 w-4" />
        Home
      </Link>
      <h1 className="mb-6 text-2xl font-bold tracking-tight">Progress</h1>
      <ProgressView userId={userId} />
    </main>
  );
}
```

- [ ] **Step 5: Typecheck and build**

Run: `cd services/frontend && npx tsc --noEmit && npm run build`
Expected: no tsc output; build shows routes `/`, `/add`, `/progress`.

- [ ] **Step 6: Manual click-through**

```bash
cd /Users/saed/shahd/Recall && docker compose up -d --build frontend
```

Open `http://localhost:3000` and confirm:
- Three cards; Study shows a "N due" badge (or none if the agent is down).
- Each card navigates to its route; the Home back link returns.
- Browser back works from each page.
- `/progress` still renders the profile.
- `/add` creates a deck and lands on `/study` — which will 404 until Task 7. That is expected here.

- [ ] **Step 7: Commit**

```bash
git add services/frontend/app/page.tsx services/frontend/app/add/page.tsx services/frontend/app/progress/page.tsx services/frontend/components/progress-view.tsx
git commit -m "feat(frontend): home, add, and progress routes

reloadKey is dropped rather than moved. It existed only because tab
switching does not remount components; routing does, and mounting fetches.

The home page's due count stays null on a failed fetch rather than showing
0 -- a '0 due' badge would read as 'nothing to do' when the truth is 'we
could not tell'."
```

---

### Task 7: The study page

**Files:**
- Create: `services/frontend/app/study/page.tsx`

**Interfaces:**
- Consumes: `DeckStack` (Task 3); `useUserId` (Task 1); `startSession`, `getHealth`, `advanceClock`, `resetClock` from `@/lib/api`; `Card`, `DueDeck` from `@/lib/types`.
- Produces: route `/study`; links to `/study/[deckId]` and `/study/all`.

- [ ] **Step 1: Create the page**

Create `services/frontend/app/study/page.tsx`:

```tsx
"use client";

import { ArrowLeft, Info, Loader2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import DeckStack from "@/components/deck-stack";
import { advanceClock, getHealth, resetClock, startSession } from "@/lib/api";
import type { DueDeck } from "@/lib/types";
import { useUserId } from "@/lib/use-user-id";

const DEMO_CLOCK_HELP =
  "Demo only: moves the tutor's simulated date forward one day so you can see " +
  "the spaced-repetition schedule play out without waiting. Cards become due " +
  "exactly when the algorithm scheduled them. Your real reviews are unaffected " +
  "— press reset to return to today.";

export default function StudyPage() {
  const userId = useUserId();
  const [decks, setDecks] = useState<DueDeck[]>([]);
  const [totalDue, setTotalDue] = useState(0);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<string | null>(null);
  const [simulatedDate, setSimulatedDate] = useState<string | null>(null);
  const [clockBusy, setClockBusy] = useState(false);
  /** Bumped to re-run the load after the clock moves. */
  const [clockKey, setClockKey] = useState(0);

  useEffect(() => {
    if (!userId) return;
    let active = true;
    setLoading(true);
    startSession(userId)
      .then((session) => {
        if (!active) return;
        setDecks(session.decks ?? []);
        setTotalDue(session.cards.length);
        setMessage(session.cards.length ? null : (session.message ?? "Nothing due."));
      })
      .catch((error) => {
        if (!active) return;
        toast.error(error instanceof Error ? error.message : "Couldn't load your decks.");
        setMessage("Couldn't load your decks.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [userId, clockKey]);

  // Whether the demo clock exists is a property of the deployment, not the
  // learner, so this runs once rather than on every reload.
  useEffect(() => {
    let active = true;
    getHealth().then((health) => {
      if (!active) return;
      if (health?.demo_mode) setSimulatedDate(health.simulated_date ?? null);
    });
    return () => {
      active = false;
    };
  }, []);

  const moveClock = useCallback(async (which: "advance" | "reset") => {
    setClockBusy(true);
    try {
      const state = which === "advance" ? await advanceClock(1) : await resetClock();
      if (state.error) {
        toast.error(state.error);
        return;
      }
      setSimulatedDate(state.simulated_date);
      setClockKey((k) => k + 1);
      toast.success(
        which === "advance" ? `Now simulating ${state.simulated_date}.` : "Back to today.",
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Couldn't move the clock.");
    } finally {
      setClockBusy(false);
    }
  }, []);

  return (
    <main className="mx-auto flex min-h-screen max-w-4xl flex-col px-4 py-6">
      <Link
        href="/"
        className="mb-4 inline-flex w-fit items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
      >
        <ArrowLeft className="h-4 w-4" />
        Home
      </Link>

      <h1 className="mb-2 text-2xl font-bold tracking-tight">Study</h1>
      <p className="mb-6 text-sm text-muted-foreground">
        {totalDue > 0
          ? `${totalDue} card${totalDue === 1 ? "" : "s"} due. Pick a deck to begin.`
          : "Nothing due right now."}
      </p>

      {/* Rendered on the empty state as well as the populated one: "nothing is due,
          press +1 day, cards appear" is the clearest demonstration that the
          schedule is real, and a control that vanished when the queue emptied
          could not show it. */}
      {simulatedDate && (
        <div className="mb-6 flex flex-wrap items-center gap-2 rounded-lg border border-dashed bg-muted/40 px-3 py-2 text-xs">
          <button
            type="button"
            onClick={() => moveClock("advance")}
            disabled={clockBusy}
            title={DEMO_CLOCK_HELP}
            className="inline-flex items-center gap-1.5 rounded-md border bg-card px-2.5 py-1 font-medium hover:bg-muted disabled:opacity-60"
          >
            {clockBusy ? <Loader2 className="h-3 w-3 animate-spin" /> : "⏩"}
            +1 day
          </button>
          <span className="text-muted-foreground">
            simulating <span className="font-medium text-foreground">{simulatedDate}</span>
          </span>
          <span
            title={DEMO_CLOCK_HELP}
            className="inline-flex cursor-help items-center gap-1 rounded-full border border-dashed px-2 py-0.5 text-muted-foreground"
          >
            <Info className="h-3 w-3" />
            demo only
          </span>
          <button
            type="button"
            onClick={() => moveClock("reset")}
            disabled={clockBusy}
            className="ml-auto text-muted-foreground underline hover:text-foreground disabled:opacity-60"
          >
            reset
          </button>
        </div>
      )}

      {loading ? (
        <div className="flex flex-col items-center gap-3 py-16 text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" />
          <p className="text-sm">Loading your decks…</p>
        </div>
      ) : totalDue === 0 ? (
        <div className="space-y-2 py-12 text-center">
          <p className="text-3xl">🎉</p>
          <p className="font-medium">{message ?? "Nothing due right now."}</p>
          <p className="text-sm text-muted-foreground">
            {simulatedDate
              ? "Add more material, or skip a day to bring your next reviews forward."
              : "Add more material, or come back when your next review is due."}
          </p>
          <Link
            href="/add"
            className="mt-2 inline-block rounded-lg border bg-card px-4 py-2 text-sm font-medium hover:bg-muted"
          >
            Add material
          </Link>
        </div>
      ) : (
        <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {/* "All decks" leads: mixing decks is harder in the moment and retains
              better than blocking one subject at a time. */}
          {decks.length > 1 && (
            <DeckLink
              href="/study/all"
              count={totalDue}
              title="All decks"
              sublabel={`${totalDue} due · interleaved`}
            />
          )}
          {decks.map((deck) => (
            <DeckLink
              key={deck.deck_id}
              href={`/study/${deck.deck_id}`}
              count={deck.due_count}
              title={deck.title}
              sublabel={`${deck.due_count} due`}
            />
          ))}
        </div>
      )}
    </main>
  );
}

function DeckLink({
  href,
  count,
  title,
  sublabel,
}: {
  href: string;
  count: number;
  title: string;
  sublabel: string;
}) {
  return (
    <Link
      href={href}
      className="group rounded-xl p-1 transition-transform duration-200 hover:-translate-y-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
    >
      <DeckStack count={count} label={title} sublabel={sublabel} />
    </Link>
  );
}
```

- [ ] **Step 2: Typecheck and build**

Run: `cd services/frontend && npx tsc --noEmit && npm run build`
Expected: no tsc output; build lists `/study`.

- [ ] **Step 3: Manual click-through**

```bash
cd /Users/saed/shahd/Recall && docker compose up -d --build frontend
```

At `http://localhost:3000/study` confirm:
- One pile per deck, plus "All decks" when more than one deck is due.
- **Count the edges on a pile against its label** — they must match exactly.
- A deck with many cards shows a visibly denser pile than one with few.
- The `⏩ +1 day` control appears (compose sets `RECALL_DEMO_MODE=true`) and reloads the decks.
- With nothing due: the 🎉 state, the clock control still present, and an "Add material" link.
- Clicking a pile navigates to `/study/<id>` — 404 until Task 8, expected.

- [ ] **Step 4: Commit**

```bash
git add services/frontend/app/study/page.tsx
git commit -m "feat(frontend): study page with per-deck solitaire piles

Replaces the <select> deck filter. Each pile's edge count is its due count,
so deck size is visible rather than read.

The demo clock lives here and NOT on a deck in progress: moving the clock
mid-deck would change which cards are due underneath an open session.

'All decks' is now the route /study/all rather than a __all__ sentinel, so
it is a real URL like any other deck's."
```

---

### Task 8: The studying-deck page, and remove the old shell

**Files:**
- Create: `services/frontend/app/study/[deckId]/page.tsx`
- Delete: `services/frontend/components/app-shell.tsx`
- Delete: `services/frontend/components/study-view.tsx`

**Interfaces:**
- Consumes: `StudySession` (Task 5); `useUserId` (Task 1); `startSession`; `Card`, `DueDeck` from `@/lib/types`.
- Produces: route `/study/[deckId]`.

- [ ] **Step 1: Create the page**

Create `services/frontend/app/study/[deckId]/page.tsx`:

```tsx
"use client";

import { ArrowLeft, Loader2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import StudySession from "@/components/study-session";
import { startSession } from "@/lib/api";
import type { Card } from "@/lib/types";
import { useUserId } from "@/lib/use-user-id";

/** Reserved deck id meaning "every due card, interleaved". Real ids are UUIDs. */
const ALL_DECKS = "all";

export default function StudyDeckPage() {
  const userId = useUserId();
  const router = useRouter();
  const params = useParams<{ deckId: string }>();
  const deckId = params.deckId;

  const [cards, setCards] = useState<Card[]>([]);
  const [deckTitle, setDeckTitle] = useState("");
  const [loading, setLoading] = useState(true);
  const [otherDue, setOtherDue] = useState(0);

  useEffect(() => {
    if (!userId) return;
    let active = true;
    setLoading(true);
    // Fetches for itself rather than receiving cards from /study, so a refresh,
    // a bookmark, or the back button all work. /session/start returns every due
    // card in one call, so filtering here needs no new endpoint.
    startSession(userId)
      .then((session) => {
        if (!active) return;
        const all = session.cards;
        const mine =
          deckId === ALL_DECKS ? all : all.filter((c) => c.deck_id === deckId);
        setCards(mine);
        setOtherDue(all.length - mine.length);
        setDeckTitle(
          deckId === ALL_DECKS
            ? "All decks"
            : (session.decks?.find((d) => d.deck_id === deckId)?.title ??
               mine[0]?.deck_title ??
               "This deck"),
        );
      })
      .catch((error) => {
        if (!active) return;
        toast.error(error instanceof Error ? error.message : "Couldn't load this deck.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [userId, deckId]);

  if (loading) {
    return (
      <main className="mx-auto flex min-h-screen max-w-4xl items-center justify-center px-4">
        <div className="flex flex-col items-center gap-3 text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" />
          <p className="text-sm">Loading your cards…</p>
        </div>
      </main>
    );
  }

  // Reached by finishing the deck, or by a stale bookmark, or by an unknown deck
  // id. All three get the same screen: a deck whose cards are all answered is a
  // normal outcome, not a broken URL, so this is not a 404.
  if (!cards.length) {
    return (
      <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center px-4 py-6 text-center">
        <p className="text-4xl">✅</p>
        <p className="mt-3 font-medium">Deck finished.</p>
        <p className="mt-1 text-sm text-muted-foreground">
          {otherDue > 0
            ? `${otherDue} card${otherDue === 1 ? "" : "s"} still due in your other decks.`
            : "That's everything due — nice work."}
        </p>
        <Link
          href="/study"
          className="mx-auto mt-5 rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground hover:opacity-90"
        >
          {otherDue > 0 ? "Choose next deck" : "Back to study"}
        </Link>
      </main>
    );
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-4xl flex-col px-4 py-6">
      <Link
        href="/study"
        className="mb-4 inline-flex w-fit items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
      >
        <ArrowLeft className="h-4 w-4" />
        All decks
      </Link>

      <StudySession
        userId={userId}
        cards={cards}
        deckTitle={deckTitle}
        // Routing back rather than rendering the finished state inline: /study
        // refetches, so the pile counts are correct rather than one card stale.
        onFinished={() => router.push("/study")}
      />
    </main>
  );
}
```

- [ ] **Step 2: Delete the old shell and study view**

```bash
cd /Users/saed/shahd/Recall
rm services/frontend/components/app-shell.tsx services/frontend/components/study-view.tsx
```

Nothing imports them: `app/page.tsx` was rewritten in Task 6, and `study-view.tsx` was only ever used by `app-shell.tsx`.

- [ ] **Step 3: Typecheck and build**

Run: `cd services/frontend && npx tsc --noEmit && npm run build`
Expected: no tsc output. If tsc reports an unresolved import of either deleted file, a Task 6 edit was missed — fix that import rather than restoring the file.

- [ ] **Step 4: Commit**

```bash
git add -A services/frontend
git commit -m "feat(frontend): studying-deck page; drop the tab shell

The page fetches and filters for itself rather than receiving cards from
/study, so a refresh, a bookmark, and the back button all work. The cost is
one redundant fetch on deck entry; a shared context would still need this
path for cold entry, so it would mean maintaining both.

An unknown or finished deck id renders the finished state, not a 404 -- a
deck whose cards are all answered is a normal outcome.

app-shell.tsx and study-view.tsx are deleted; routes replace the tab state
and the session logic now lives in study-session.tsx."
```

---

### Task 9: Full end-to-end verification

No test suite means nothing has checked the tasks against each other. This is that check.

**Files:** none — verification only.

- [ ] **Step 1: Rebuild and confirm the stack is healthy**

```bash
cd /Users/saed/shahd/Recall
docker compose up -d --build frontend
sleep 5
docker compose ps
curl -s http://localhost:3500/health
```

Expected: four services up; health reports `"status":"ok"` with `mcp_tools` above 0.

- [ ] **Step 2: Walk the whole flow**

At `http://localhost:3000`, confirm each of these:

1. Home shows three cards and a due count on Study.
2. **Add material** → paste a few sentences, name the deck, Make flashcards → toast, lands on `/study`, new deck present.
3. `/study` → pile edge counts match their labels.
4. Click a deck → the card **grows in** from small.
5. Type an answer, press **Check my answer** → the card does **not** move; the button spins.
6. When the grade returns → the card **flips by itself**; the back shows the real answer, *what you typed*, the verdict, the grade, and the next review date.
7. The textarea is **gone** (not merely disabled) — the answer is committed.
8. Click **Answer/Question** → flips back and forth freely.
9. Click **Next card** → the card **shrinks away**, the next **grows in**, and the right-hand pile is **one edge shorter**.
10. On the last card the button reads **Choose next deck**; clicking it returns to `/study`.
11. **"I don't know"** → grades 0/5, still flips, next review tomorrow.
12. **Enter** submits; **Shift+Enter** makes a newline; **Enter** after reveal advances.
13. **Refresh on `/study/<deckId>`** → loads the deck, does not error.
14. **Back button** from every page reaches the previous one.
15. `/study/does-not-exist` → the finished state with a link back, not a crash.
16. Narrow the window below 1024px → the pile moves **above** the card.
17. `/progress` still renders the profile and weak topics.

- [ ] **Step 3: Confirm nothing outside the frontend changed**

```bash
cd /Users/saed/shahd/Recall
git diff --stat main -- . ':!services/frontend' ':!docs/superpowers'
```

Expected: empty. Any output means backend or infrastructure code was touched, which is out of scope.

- [ ] **Step 4: Commit any fixes found**

If steps 2-3 surfaced defects, fix them and commit with a message naming the step that caught it, e.g.:

```bash
git commit -m "fix(frontend): pile did not shrink after the last card

Caught by task 9 step 9. remaining was computed from cards.length rather
than from the live index."
```

---

## Notes for the executor

**The compose stack must be running** for any manual step. `docker compose up -d` once, then `docker compose up -d --build frontend` after each frontend change — the frontend is a production Next.js build inside the image, so edits are not hot-reloaded.

**If a card never flips**, check the browser console for a failed `/session/answer` call before suspecting the animation. The flip is triggered by the response, so a failed request correctly leaves the card unflipped and returns the phase to `answering`.

**If both card faces render at once**, `-webkit-backface-visibility` is missing from `globals.css` — Safari needs the prefix.

**Do not add a dark-mode toggle.** It is deliberately out of scope; `darkMode: ["class"]` stays unwired.
