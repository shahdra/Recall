"use client";

import { Loader2, Mic, RotateCcw, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
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

  /**
   * Release the microphone when this component goes away.
   *
   * Without this, navigating away mid-recording — clicking "All decks", the back
   * button, anything that unmounts the session — leaves the MediaStream's tracks
   * live. The browser keeps showing its recording indicator and the OS keeps the
   * mic held open, with no UI left to stop it: the only way out is closing the tab.
   *
   * `cancel` (not `stop`) because we are discarding, not submitting: it detaches the
   * MediaRecorder's onstop handler before stopping, so no orphaned callback tries to
   * set state on an unmounted component.
   *
   * `recorder.cancel` is a useCallback whose only dependency is itself stable, so
   * this effect does not re-run on every render.
   */
  useEffect(() => {
    return () => recorder.cancel();
  }, [recorder.cancel]);

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
