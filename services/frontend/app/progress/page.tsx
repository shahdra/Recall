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
