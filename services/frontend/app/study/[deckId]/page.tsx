"use client";

import { ArrowLeft, Loader2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import StudySession from "@/components/study-session";
import ThemeToggle from "@/components/theme-toggle";
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
  const [loadError, setLoadError] = useState(false);
  const [otherDue, setOtherDue] = useState(0);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (!userId) return;
    let active = true;
    setLoading(true);
    setLoadError(false);
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
        setLoadError(true);
        toast.error(error instanceof Error ? error.message : "Couldn't load this deck.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [userId, deckId, reloadKey]);

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

  // A failed fetch is not the same outcome as a finished deck: report it
  // distinctly, or a learner whose agent was briefly unreachable gets
  // congratulated for finishing a deck they never started.
  if (loadError) {
    return (
      <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center px-4 py-6 text-center">
        <p className="mt-3 font-medium">Couldn&apos;t load this deck.</p>
        <p className="mt-1 text-sm text-muted-foreground">
          Something went wrong reaching your study data. Check your connection and try again.
        </p>
        <div className="mx-auto mt-5 flex items-center gap-3">
          <button
            type="button"
            onClick={() => setReloadKey((k) => k + 1)}
            className="rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground hover:opacity-90"
          >
            Try again
          </button>
          <Link
            href="/study"
            className="text-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
          >
            Back to study
          </Link>
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
            : "That&apos;s everything due — nice work."}
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
      <div className="mb-4 flex items-center justify-between gap-4">
        <Link
          href="/study"
          className="inline-flex w-fit items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
        >
          <ArrowLeft className="h-4 w-4" />
          All decks
        </Link>
        <ThemeToggle />
      </div>

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
