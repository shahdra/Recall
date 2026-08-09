"use client";

import { FileText, Loader2, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { createDeckFromFile, createDeckFromText } from "@/lib/api";
import { cn, fileToBase64 } from "@/lib/utils";

const MAX_FILE_BYTES = 10 * 1024 * 1024; // matches ingest.MAX_UPLOAD_BYTES

interface Props {
  userId: string;
  onDeckCreated: (cardCount: number) => void;
}

export default function UploadView({ userId, onDeckCreated }: Props) {
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  function handleFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    const chosen = event.target.files?.[0];
    event.target.value = ""; // allow re-picking the same file
    if (!chosen) return;

    // Check the size here rather than making the learner wait for an upload that
    // the agent will only reject.
    if (chosen.size > MAX_FILE_BYTES) {
      toast.error("That file is larger than 10 MB. Try a smaller excerpt.");
      return;
    }
    setFile(chosen);
    if (!title.trim()) setTitle(chosen.name.replace(/\.[^.]+$/, ""));
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;

    const deckTitle = title.trim() || "Untitled deck";
    if (!file && !text.trim()) {
      toast.error("Paste some text or choose a PDF first.");
      return;
    }

    setBusy(true);
    try {
      const result = file
        ? await createDeckFromFile(
            userId,
            deckTitle,
            await fileToBase64(file),
            file.type || "application/pdf",
          )
        : await createDeckFromText(userId, deckTitle, text.trim());

      if (result.card_count === 0) {
        toast.error(result.warning ?? "I couldn't make cards from that material.");
        return;
      }

      toast.success(
        `Made ${result.card_count} card${result.card_count === 1 ? "" : "s"}.`,
      );
      setText("");
      setFile(null);
      setTitle("");
      onDeckCreated(result.card_count);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold">Add study material</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Paste your notes or upload a PDF. Recall turns them into flashcards and
          schedules reviews for you.
        </p>
      </div>

      <div className="space-y-1.5">
        <label htmlFor="deck-title" className="text-sm font-medium">
          Deck name
        </label>
        <input
          id="deck-title"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder="Cell biology, week 3"
          className="w-full rounded-lg border bg-card px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
        />
      </div>

      <div className="space-y-1.5">
        <label htmlFor="deck-text" className="text-sm font-medium">
          Your notes
        </label>
        <textarea
          id="deck-text"
          value={text}
          onChange={(event) => setText(event.target.value)}
          disabled={Boolean(file)}
          rows={8}
          placeholder="Paste the material you're studying…"
          className="w-full resize-y rounded-lg border bg-card px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40 disabled:opacity-50"
        />
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <input
          ref={fileInputRef}
          type="file"
          accept="application/pdf,text/plain,text/markdown"
          onChange={handleFileChange}
          className="hidden"
        />
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          className="inline-flex items-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm font-medium hover:bg-muted"
        >
          <Upload className="h-4 w-4" />
          Choose a PDF
        </button>

        {file && (
          <span className="inline-flex items-center gap-2 rounded-lg bg-muted px-3 py-2 text-sm">
            <FileText className="h-4 w-4 shrink-0" />
            <span className="max-w-[16rem] truncate">{file.name}</span>
            <button
              type="button"
              onClick={() => setFile(null)}
              className="text-muted-foreground underline hover:text-foreground"
            >
              remove
            </button>
          </span>
        )}
      </div>

      <button
        type="submit"
        disabled={busy}
        className={cn(
          "inline-flex w-full items-center justify-center gap-2 rounded-lg px-4 py-2.5",
          "bg-primary text-sm font-semibold text-primary-foreground",
          "hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60",
        )}
      >
        {busy ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin" />
            Making cards…
          </>
        ) : (
          "Make flashcards"
        )}
      </button>
      {busy && (
        <p className="text-center text-xs text-muted-foreground">
          Reading your material and writing questions. This can take a few seconds.
        </p>
      )}
    </form>
  );
}
