"use client";

import { BarChart3, BookOpen, Upload } from "lucide-react";
import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";
import ProgressView from "./progress-view";
import StudyView from "./study-view";
import UploadView from "./upload-view";

type Tab = "upload" | "study" | "progress";

const TABS: Array<{ id: Tab; label: string; icon: typeof Upload }> = [
  { id: "upload", label: "Add material", icon: Upload },
  { id: "study", label: "Study", icon: BookOpen },
  { id: "progress", label: "Progress", icon: BarChart3 },
];

const USER_STORAGE_KEY = "recall.user_id";

/**
 * Identify the learner.
 *
 * Auth is out of scope (docs/spec.md), so a per-browser id stands in for an
 * account. Persisting it in localStorage is what makes long-term memory
 * observable: reload the page and the tutor still knows what you struggle with.
 */
function loadUserId(): string {
  if (typeof window === "undefined") return "";
  const existing = window.localStorage.getItem(USER_STORAGE_KEY);
  if (existing) return existing;
  const generated = `learner-${Math.random().toString(36).slice(2, 10)}`;
  window.localStorage.setItem(USER_STORAGE_KEY, generated);
  return generated;
}

export default function AppShell() {
  const [tab, setTab] = useState<Tab>("upload");
  const [userId, setUserId] = useState("");
  // Bumped whenever a deck is created, so Study and Progress refetch instead of
  // showing what they loaded before the new cards existed.
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    setUserId(loadUserId());
  }, []);

  function handleDeckCreated() {
    setReloadKey((key) => key + 1);
    setTab("study");
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col px-4 py-6">
      <header className="mb-6">
        <h1 className="text-2xl font-bold tracking-tight">Recall</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Turns your material into a quiz that learns what you don&apos;t know.
        </p>
      </header>

      <nav className="mb-6 flex gap-1 rounded-xl border bg-card p-1" aria-label="Screens">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            aria-current={tab === id ? "page" : undefined}
            className={cn(
              "inline-flex flex-1 items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
              tab === id
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            <Icon className="h-4 w-4" />
            <span className="hidden sm:inline">{label}</span>
          </button>
        ))}
      </nav>

      <div className="flex-1">
        {/* Wait for the browser-generated id before any screen fetches, so no
            request goes out with an empty user_id. */}
        {!userId ? null : tab === "upload" ? (
          <UploadView userId={userId} onDeckCreated={handleDeckCreated} />
        ) : tab === "study" ? (
          <StudyView userId={userId} reloadKey={reloadKey} />
        ) : (
          <ProgressView userId={userId} reloadKey={reloadKey} />
        )}
      </div>

      <footer className="mt-8 text-center text-xs text-muted-foreground">
        Scheduling by SM-2 spaced repetition.
        {userId ? <span className="ml-1 opacity-60">({userId})</span> : null}
      </footer>
    </main>
  );
}
