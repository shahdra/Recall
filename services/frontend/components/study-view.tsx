"use client";

import { Check, Loader2, Mic, Square, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { startSession, submitAnswer, transcribe } from "@/lib/api";
import type { Card, GradeResponse } from "@/lib/types";
import { useRecorder } from "@/lib/use-recorder";
import { cn, formatInterval } from "@/lib/utils";

interface Props {
  userId: string;
  /** Bumped by the upload screen so a newly-made deck loads without a reload. */
  reloadKey: number;
}

export default function StudyView({ userId, reloadKey }: Props) {
  const [cards, setCards] = useState<Card[]>([]);
  const [index, setIndex] = useState(0);
  const [answer, setAnswer] = useState("");
  const [verdict, setVerdict] = useState<GradeResponse | null>(null);
  const [revealed, setRevealed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [grading, setGrading] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [emptyMessage, setEmptyMessage] = useState<string | null>(null);

  const recorder = useRecorder();
  const answerRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    let active = true;

    async function load() {
      setLoading(true);
      try {
        const session = await startSession(userId);
        if (!active) return;
        setCards(session.cards);
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
  }, [userId, reloadKey]);

  const card = cards[index];

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
    if (index + 1 < cards.length) {
      setIndex(index + 1);
    } else {
      setCards([]);
      setEmptyMessage("That's everything due — nice work.");
    }
    answerRef.current?.focus();
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

  if (loading) {
    return (
      <div className="flex flex-col items-center gap-3 py-16 text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
        <p className="text-sm">Loading your cards…</p>
      </div>
    );
  }

  if (!card) {
    return (
      <div className="space-y-2 py-16 text-center">
        <p className="text-3xl">🎉</p>
        <p className="font-medium">{emptyMessage ?? "Nothing due right now."}</p>
        <p className="text-sm text-muted-foreground">
          Add more material, or come back when your next review is due.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between text-sm text-muted-foreground">
        <span>
          Card {index + 1} of {cards.length}
        </span>
        <span className="rounded-full bg-muted px-2.5 py-0.5 text-xs">{card.topic}</span>
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
            {index + 1 < cards.length ? "Next card" : "Finish session"}
          </button>
        </div>
      )}
    </div>
  );
}
