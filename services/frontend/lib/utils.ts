import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Read a File as base64 without the data-URL prefix.
 *
 * The agent expects raw base64 in `file_b64`, and FileReader gives
 * "data:<mime>;base64,<payload>", so the header is stripped here.
 */
export function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result as string;
      const comma = result.indexOf(",");
      resolve(comma === -1 ? result : result.slice(comma + 1));
    };
    reader.onerror = () => reject(new Error("Couldn't read that file."));
    reader.readAsDataURL(file);
  });
}

export function blobToBase64(blob: Blob): Promise<string> {
  return fileToBase64(new File([blob], "audio", { type: blob.type }));
}

/** "in 1 day" / "in 6 days" — how SM-2's interval is shown to the learner. */
export function formatInterval(days: number | null): string {
  if (days === null || days === undefined) return "";
  if (days <= 0) return "again today";
  if (days === 1) return "in 1 day";
  return `in ${days} days`;
}

export function formatPercent(value: number | undefined): string {
  if (value === undefined || value === null || Number.isNaN(value)) return "—";
  return `${Math.round(value * 100)}%`;
}
