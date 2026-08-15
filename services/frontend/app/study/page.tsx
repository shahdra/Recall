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
