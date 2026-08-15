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
          <p className="rounded-lg border border-dashed px-3 py-2 text-xs">
            You said: <span className="font-medium">{submitted.trim() || "— nothing —"}</span>
          </p>
          {verdict && (
            <div className="flex items-start gap-3 rounded-lg border p-3">
              <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full" />
              <div className="space-y-1">
                <p className="text-sm font-semibold">
                  Not quite<span className="ml-2 text-xs font-normal">graded {verdict.quality}/5</span>
                </p>
                <p className="text-xs leading-relaxed">{verdict.explanation}</p>
                <p className="text-[11px]">Next review placeholder line</p>
              </div>
            </div>
          )}
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
