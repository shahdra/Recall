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
