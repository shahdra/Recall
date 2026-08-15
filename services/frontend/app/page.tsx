"use client";

import { BarChart3, BookOpen, Upload } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import ThemeToggle from "@/components/theme-toggle";
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
      <header className="mb-8 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Recall</h1>
          <p className="mt-1.5 text-muted-foreground">
            Turns your material into a quiz that learns what you don&apos;t know.
          </p>
        </div>
        <ThemeToggle />
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
