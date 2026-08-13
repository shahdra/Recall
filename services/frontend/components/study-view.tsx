"use client";

import { Check, Info, Loader2, Mic, Square, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import {
  advanceClock,
  getHealth,
  resetClock,
  startSession,
  submitAnswer,
  transcribe,
} from "@/lib/api";
import type { Card, DueDeck, GradeResponse } from "@/lib/types";
import { useRecorder } from "@/lib/use-recorder";
import { cn, formatInterval } from "@/lib/utils";

interface Props {
  userId: string;
  /** Bumped by the upload screen so a newly-made deck loads without a reload. */
  reloadKey: number;
}

/** Sentinel for "study every due card, interleaved across decks". */
const ALL_DECKS = "__all__";

const DEMO_CLOCK_HELP =
  "Demo only: moves the tutor's simulated date forward one day so you can see " +
  "the spaced-repetition schedule play out without waiting. Cards become due " +
  "exactly when the algorithm scheduled them. Your real reviews are unaffected " +
  "— press reset to return to today.";

export default function StudyView({ userId, reloadKey }: Props) {
  const [cards, setCards] = useState<Card[]>([]);
  const [decks, setDecks] = useState<DueDeck[]>([]);
  const [selectedDeck, setSelectedDeck] = useState<string>(ALL_DECKS);
  /** card_ids already answered this session. Tracked rather than a running index
      so switching decks does not lose which cards are behind you. */
  const [doneIds, setDoneIds] = useState<Set<string>>(new Set());
  const [index, setIndex] = useState(0);
  const [answer, setAnswer] = useState("");
  const [verdict, setVerdict] = useState<GradeResponse | null>(null);
  const [revealed, setRevealed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [grading, setGrading] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [emptyMessage, setEmptyMessage] = useState<string | null>(null);
  /** Non-null only when the agent reports the demo clock is available. */
  const [simulatedDate, setSimulatedDate] = useState<string | null>(null);
  const [clockBusy, setClockBusy] = useState(false);
  /** Bumped to re-run the session load after the clock moves. */
  const [clockKey, setClockKey] = useState(0);

  const recorder = useRecorder();
  const answerRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    let active = true;

    async function load() {
      setLoading(true);
      try {
        const session = await startSession(userId);
        if (!active) return;
        const dueDecks = session.decks ?? [];
        setCards(session.cards);
        setDecks(dueDecks);
        // With one deck due there is nothing to choose, so select it and let the
        // dropdown stay hidden. With several, default to the interleaved queue:
        // mixing decks is harder in the moment and retains better than studying
        // one subject at a time.
        setSelectedDeck(dueDecks.length === 1 ? dueDecks[0].deck_id : ALL_DECKS);
        setDoneIds(new Set());
        setIndex(0);
        setVerdict(null);
        setRevealed(false);
        setAnswer("");
        setEmptyMessage(session.cards.length ? null : (session.message ?? "Nothing due."));
      } catch (error) {
        if (!active) return;
        toast.error(error instanceof Error ? error.message : "Couldn't start a session.");
        setEmptyMessage("Couldn't load your cards.");
      } finally {
        if (active) setLoading(false);
      }
    }

    load();
    return () => {
      active = false;
    };
  }, [userId, reloadKey, clockKey]);

  // Whether the demo clock exists is a property of the deployment, not of the
  // learner, so this runs once rather than on every session reload.
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

  /** Cards still to answer, filtered to the selected deck. Derived rather than
      refetched: every due card is already in hand, so switching decks is free. */
  const queue = useMemo(
    () =>
      cards.filter(
        (c) =>
          !doneIds.has(c.card_id) &&
          (selectedDeck === ALL_DECKS || c.deck_id === selectedDeck),
      ),
    [cards, doneIds, selectedDeck],
  );

  /** Decks with cards still unanswered — what the dropdown should offer. */
  const remainingDecks = useMemo(() => {
    const left = new Map<string, number>();
    for (const c of cards) {
      if (!doneIds.has(c.card_id)) left.set(c.deck_id, (left.get(c.deck_id) ?? 0) + 1);
    }
    return decks
      .filter((d) => left.has(d.deck_id))
      .map((d) => ({ ...d, due_count: left.get(d.deck_id)! }))
      .sort((a, b) => b.due_count - a.due_count || a.title.localeCompare(b.title));
  }, [cards, decks, doneIds]);

  const card = queue[index];
  const studyingOneDeck = selectedDeck !== ALL_DECKS;
  const totalRemaining = cards.length - doneIds.size;

  /** Cards from the current selection already answered — the counter's offset. */
  const deckDone = useMemo(
    () =>
      cards.filter(
        (c) =>
          doneIds.has(c.card_id) &&
          (selectedDeck === ALL_DECKS || c.deck_id === selectedDeck),
      ).length,
    [cards, doneIds, selectedDeck],
  );

  async function handleSubmit(event?: React.FormEvent) {
    event?.preventDefault();
    if (!card || grading || verdict) return;

    setGrading(true);
    try {
      const result = await submitAnswer(userId, card, answer);
      setVerdict(result);
      setRevealed(true);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Couldn't grade that.");
    } finally {
      setGrading(false);
    }
  }

  function handleNext() {
    setVerdict(null);
    setRevealed(false);
    setAnswer("");

    // Retire the card just answered. The queue is derived from doneIds, so the
    // next card slides into the current index without advancing it.
    const answered = card;
    const done = new Set(doneIds);
    if (answered) done.add(answered.card_id);
    setDoneIds(done);

    // Recomputed against `done` rather than read off remainingDecks, which is
    // memoized on the pre-update doneIds and would still count this card.
    const unanswered = cards.filter((c) => !done.has(c.card_id));
    if (unanswered.some((c) => c.deck_id === selectedDeck || !studyingOneDeck)) {
      setIndex(0);
      answerRef.current?.focus();
      return;
    }

    // This deck is finished. Move to the heaviest deck that still has cards
    // rather than ending the session, so finishing a deck is a milestone and not
    // a dead end.
    const nextCounts = new Map<string, number>();
    for (const c of unanswered) {
      nextCounts.set(c.deck_id, (nextCounts.get(c.deck_id) ?? 0) + 1);
    }
    const nextDeck = decks
      .filter((d) => nextCounts.has(d.deck_id))
      .sort(
        (a, b) =>
          nextCounts.get(b.deck_id)! - nextCounts.get(a.deck_id)! ||
          a.title.localeCompare(b.title),
      )[0];
    if (studyingOneDeck && nextDeck) {
      setSelectedDeck(nextDeck.deck_id);
      setIndex(0);
      toast.success(`Deck finished — moving on to ${nextDeck.title}.`);
      answerRef.current?.focus();
      return;
    }

    setEmptyMessage("That's everything due — nice work.");
    answerRef.current?.focus();
  }

  async function handleAdvanceClock() {
    if (clockBusy) return;
    setClockBusy(true);
    try {
      const state = await advanceClock(1);
      if (state.error) {
        toast.error(state.error);
        return;
      }
      setSimulatedDate(state.simulated_date);
      // Reloading the session is the point: cards whose due date has now arrived
      // become due through the same query the real clock drives.
      setClockKey((key) => key + 1);
      toast.success(`Now simulating ${state.simulated_date}.`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Couldn't move the clock.");
    } finally {
      setClockBusy(false);
    }
  }

  async function handleResetClock() {
    if (clockBusy) return;
    setClockBusy(true);
    try {
      const state = await resetClock();
      if (state.error) {
        toast.error(state.error);
        return;
      }
      setSimulatedDate(state.simulated_date);
      setClockKey((key) => key + 1);
      toast.success("Back to today.");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Couldn't reset the clock.");
    } finally {
      setClockBusy(false);
    }
  }

  function handleDeckChange(deckId: string) {
    // Any unanswered card stays due, so abandoning one mid-question loses
    // nothing — no need to warn or block the switch.
    setSelectedDeck(deckId);
    setIndex(0);
    setVerdict(null);
    setRevealed(false);
    setAnswer("");
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

  /**
   * The demo-only time-travel control.
   *
   * Rendered on the empty state as well as over a card, because "nothing is due,
   * click +1 day, cards appear" is the clearest demonstration that the schedule
   * is real — a control that vanished when the queue emptied could not show it.
   */
  const clockControl = simulatedDate ? (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-dashed bg-muted/40 px-3 py-2 text-xs">
      <button
        type="button"
        onClick={handleAdvanceClock}
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
      {/* The explanation is on an always-visible tag rather than hidden behind
          hover alone, so an audience watching a projector understands what the
          button does without the presenter having to narrate it. */}
      <span
        title={DEMO_CLOCK_HELP}
        className="inline-flex cursor-help items-center gap-1 rounded-full border border-dashed px-2 py-0.5 text-muted-foreground"
      >
        <Info className="h-3 w-3" />
        demo only
      </span>
      <button
        type="button"
        onClick={handleResetClock}
        disabled={clockBusy}
        className="ml-auto text-muted-foreground underline hover:text-foreground disabled:opacity-60"
      >
        reset
      </button>
    </div>
  ) : null;

  if (loading) {
    return (
      <div className="flex flex-col items-center gap-3 py-16 text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
        <p className="text-sm">Loading your cards…</p>
      </div>
    );
  }

  // An empty queue with cards left elsewhere means this deck is done, not the
  // session — offer the rest rather than a premature 🎉.
  if (!card && totalRemaining > 0) {
    return (
      <div className="space-y-4 py-12 text-center">
        {clockControl && <div className="text-left">{clockControl}</div>}
        <p className="text-3xl">✅</p>
        <p className="font-medium">Deck finished.</p>
        <p className="text-sm text-muted-foreground">
          {totalRemaining} card{totalRemaining === 1 ? "" : "s"} still due in your
          other decks.
        </p>
        <div className="flex flex-wrap justify-center gap-2 pt-1">
          {remainingDecks.map((deck) => (
            <button
              key={deck.deck_id}
              type="button"
              onClick={() => handleDeckChange(deck.deck_id)}
              className="rounded-lg border bg-card px-3 py-2 text-sm font-medium hover:bg-muted"
            >
              {deck.title} ({deck.due_count})
            </button>
          ))}
        </div>
      </div>
    );
  }

  if (!card) {
    return (
      <div className="space-y-4">
        {clockControl}
        <div className="space-y-2 py-12 text-center">
          <p className="text-3xl">🎉</p>
          <p className="font-medium">{emptyMessage ?? "Nothing due right now."}</p>
          <p className="text-sm text-muted-foreground">
            {simulatedDate
              ? "Add more material, or skip a day to bring your next reviews forward."
              : "Add more material, or come back when your next review is due."}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {clockControl}
      {/* Only worth a control when there is a real choice: with one deck due it
          would be a picker with a single option. */}
      {remainingDecks.length > 1 && (
        <div className="space-y-1.5">
          <label htmlFor="deck-filter" className="text-sm font-medium">
            Studying
          </label>
          <select
            id="deck-filter"
            value={selectedDeck}
            onChange={(event) => handleDeckChange(event.target.value)}
            className="w-full rounded-lg border bg-card px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
          >
            <option value={ALL_DECKS}>All decks ({totalRemaining} due)</option>
            {remainingDecks.map((deck) => (
              <option key={deck.deck_id} value={deck.deck_id}>
                {deck.title} ({deck.due_count} due)
              </option>
            ))}
          </select>
        </div>
      )}

      <div className="flex items-center justify-between gap-2 text-sm text-muted-foreground">
        {/* Counts up through the deck rather than down the remaining queue:
            "Card 1 of 13" then "Card 1 of 12" reads as losing ground. The
            denominator is cards answered plus cards left, so it is stable within
            a deck and rebases when the selection changes. */}
        <span>
          Card {deckDone + index + 1} of {deckDone + queue.length}
        </span>
        <span className="flex flex-wrap items-center justify-end gap-1.5">
          {/* Redundant when a single deck is selected — the dropdown already
              names it — so shown only for the interleaved queue, where the deck
              is otherwise unknowable. */}
          {!studyingOneDeck && card.deck_title && (
            <span className="max-w-[12rem] truncate rounded-full border px-2.5 py-0.5 text-xs">
              {card.deck_title}
            </span>
          )}
          <span className="rounded-full bg-muted px-2.5 py-0.5 text-xs">{card.topic}</span>
        </span>
      </div>

      {/* The card. Front always visible; the back appears once graded, so the
          learner cannot peek before committing to an answer. */}
      <div className="rounded-xl border bg-card p-6 shadow-sm">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Question
        </p>
        <p className="mt-2 text-lg font-medium leading-snug">{card.front}</p>

        {revealed && (
          <div className="mt-5 border-t pt-4">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Answer
            </p>
            <p className="mt-2 leading-snug">{card.back}</p>
          </div>
        )}
      </div>

      {!verdict ? (
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
              placeholder="Type your answer, or use the mic…"
              className="flex-1 resize-y rounded-lg border bg-card px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
            />
            {!recorder.unsupported && (
              <button
                type="button"
                onClick={handleMicClick}
                disabled={transcribing}
                aria-label={recorder.recording ? "Stop recording" : "Record your answer"}
                title={recorder.recording ? "Stop recording" : "Record your answer"}
                className={cn(
                  "flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border",
                  recorder.recording
                    ? "animate-pulse border-danger bg-danger text-danger-foreground"
                    : "bg-card hover:bg-muted",
                  transcribing && "opacity-60",
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
              Listening… tap the square when you're done.
            </p>
          )}

          <div className="flex gap-2">
            <button
              type="submit"
              disabled={grading}
              className="inline-flex flex-1 items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground hover:opacity-90 disabled:opacity-60"
            >
              {grading ? (
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
              disabled={grading}
              className="rounded-lg border bg-card px-4 py-2.5 text-sm font-medium hover:bg-muted disabled:opacity-60"
            >
              I don&apos;t know
            </button>
          </div>
        </form>
      ) : (
        <div className="space-y-4">
          <div
            className={cn(
              "flex items-start gap-3 rounded-xl border p-4",
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
              {verdict.is_correct ? (
                <Check className="h-4 w-4" />
              ) : (
                <X className="h-4 w-4" />
              )}
            </span>
            <div className="space-y-1">
              <p className="font-medium">
                {verdict.is_correct ? "Correct" : "Not quite"}
                <span className="ml-2 text-xs font-normal text-muted-foreground">
                  graded {verdict.quality}/5
                </span>
              </p>
              <p className="text-sm leading-relaxed">{verdict.explanation}</p>
              {verdict.interval_days !== null && (
                <p className="text-xs text-muted-foreground">
                  Next review {formatInterval(verdict.interval_days)}
                  {verdict.due_date ? ` (${verdict.due_date})` : ""}.
                </p>
              )}
            </div>
          </div>

          <button
            type="button"
            onClick={handleNext}
            autoFocus
            className="w-full rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground hover:opacity-90"
          >
            {queue.length > 1
              ? "Next card"
              : totalRemaining > 1
                ? "Next deck"
                : "Finish session"}
          </button>
        </div>
      )}
    </div>
  );
}
