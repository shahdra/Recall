"use client";

import { cn } from "@/lib/utils";

interface Props {
  /** Cards due in this deck. One rendered edge per card. */
  count: number;
  /** Deck name, shown above the pile. */
  label?: string;
  /** Secondary line under the label, e.g. "7 due". */
  sublabel?: string;
  className?: string;
}

/** Spacing between edges when the pile is small, in px. */
const BASE_OFFSET = 10;
/** Height the whole pile may occupy, in px. */
const MAX_HEIGHT = 260;
/** Height of the topmost card face, in px. */
const CARD_HEIGHT = 108;

/**
 * A deck drawn as a solitaire pile: one visible edge per due card.
 *
 * Edges are NOT capped. Cards per deck cap at 40 upstream
 * (services/tutor-agent/card_generator.py DEFAULT_MAX_CARDS), so the worst case is
 * 40 nodes — cheap enough that capping would trade honesty for nothing.
 *
 * Spacing compresses instead. At a fixed 10px, 40 cards would be a 400px column —
 * taller than a phone viewport and taller than the card beside it. Compressing to a
 * fixed MAX_HEIGHT keeps the whole pile visible at a glance, which is the point of
 * showing its size. A dense pile also READS as a big pile, so the compression
 * carries information rather than only saving space.
 */
export default function DeckStack({ count, label, sublabel, className }: Props) {
  const offset =
    count > 1
      ? Math.min(BASE_OFFSET, (MAX_HEIGHT - CARD_HEIGHT) / (count - 1))
      : BASE_OFFSET;

  // The container must be tall enough for the last edge plus the card itself, or
  // the pile overflows whatever sits below it.
  const height = CARD_HEIGHT + offset * Math.max(0, count - 1);

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      {label && (
        <div className="space-y-0.5">
          <p className="truncate text-sm font-semibold">{label}</p>
          {sublabel && <p className="text-xs text-muted-foreground">{sublabel}</p>}
        </div>
      )}

      <div className="relative w-full" style={{ height }}>
        {Array.from({ length: count }, (_, i) => (
          <div
            key={i}
            aria-hidden
            className="absolute inset-x-0 rounded-xl border bg-card shadow-sm"
            style={{
              top: i * offset,
              height: CARD_HEIGHT,
              // Alternating fractions of a degree read as hand-stacked rather than
              // as a mechanical gradient.
              transform: `rotate(${i % 2 === 0 ? 0.4 : -0.5}deg)`,
              // Later cards draw on top, so the pile reads as building upward.
              zIndex: i,
            }}
          />
        ))}
        {count === 0 && (
          <div className="absolute inset-x-0 top-0 flex items-center justify-center rounded-xl border border-dashed text-xs text-muted-foreground" style={{ height: CARD_HEIGHT }}>
            nothing due
          </div>
        )}
      </div>
    </div>
  );
}
