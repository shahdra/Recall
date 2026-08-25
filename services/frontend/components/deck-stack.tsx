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
 * The card back for the TOP card, drawn as CSS gradients rather than an image or
 * SVG file.
 *
 * A diamond lattice — two crossed sets of stripes at a 10px repeat — over a
 * primary-tinted field. This is the classic playing-card back motif, and it is
 * what makes the pile read as a deck of cards rather than as blank slabs. The
 * earlier version used a 0.07-alpha texture that was too faint to register as a
 * design at all; this is deliberately heavier.
 *
 * It is deliberately NOT applied to buried cards. Only ~4-10px of a buried card
 * shows, and tiling a 10px lattice into a 4px sliver forty times over makes the
 * slivers interfere: the pile turns into horizontal moire banding and the
 * individual cards stop being distinguishable. Buried cards get a hairline and a
 * shade instead, which is what actually reads as stacked paper. Verified by
 * rendering piles of 1/3/7/18/40 in both themes.
 *
 * currentColor is not used: these cards carry no text of their own, so the
 * pattern is tied to the primary token and stays on-brand in both themes.
 */
const CARD_BACK: React.CSSProperties = {
  backgroundColor: "hsl(var(--primary) / 0.05)",
  backgroundImage: [
    "repeating-linear-gradient(45deg, hsl(var(--primary) / 0.16) 0 1.5px, transparent 1.5px 10px)",
    "repeating-linear-gradient(-45deg, hsl(var(--primary) / 0.16) 0 1.5px, transparent 1.5px 10px)",
  ].join(","),
};

/**
 * The corner mark on the top card: three stacked layers.
 *
 * A deck-of-layers glyph rather than a brain or a book. Tested a brain outline
 * first and it collapsed into an unreadable blob at this size — at ~14px only a
 * few strokes survive, so the motif has to be geometric.
 */
function LayersMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      <path d="M12 3.5 21 8l-9 4.5L3 8z" />
      <path d="M3 12l9 4.5L21 12" />
      <path d="M3 16l9 4.5L21 16" />
    </svg>
  );
}

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
        {Array.from({ length: count }, (_, i) => {
          const isTop = i === count - 1;
          return (
            <div
              key={i}
              aria-hidden
              className="absolute inset-x-0 overflow-hidden rounded-xl border bg-card shadow-sm"
              style={{
                top: i * offset,
                height: CARD_HEIGHT,
                // Alternating fractions of a degree read as hand-stacked rather than
                // as a mechanical gradient.
                transform: `rotate(${i % 2 === 0 ? 0.4 : -0.5}deg)`,
                // Later cards draw on top, so the pile reads as building upward.
                zIndex: i,
              }}
            >
              {/* Pattern on the top card only, in its own layer — see CARD_BACK. */}
              {isTop && <div className="absolute inset-0" style={CARD_BACK} />}
              {/* The sliver of each buried card that actually shows is its top edge.
                  A hairline there separates one card from the next, which a shadow
                  alone fails to do once spacing compresses to ~4px on a deep pile. */}
              <div
                className="absolute inset-x-0 top-0 h-px bg-primary/20"
                style={{ opacity: isTop ? 0 : 1 }}
              />
              {/* Buried cards sit in the shade of the ones above them. Without this
                  a 40-card pile is 40 identically-lit strips; with it the pile has a
                  direction, and depth is legible at a glance. */}
              {!isTop && (
                <div
                  className="absolute inset-0 bg-foreground/[0.05]"
                  style={{
                    // Ramps over the nearest few cards, then holds. Letting it run
                    // the full depth of a 40-card pile turned the top two-thirds
                    // uniformly grey, which lost the individual edges instead of
                    // showing them.
                    opacity: Math.min(1, (count - 1 - i) / 5),
                  }}
                />
              )}
              {/* The top card gets the full back: an inset frame, a corner mark, and
                  a medallion holding the count. The frame is what separates "a card
                  with a pattern on it" from "the back of a card" — real card backs
                  reserve a margin rather than bleeding the motif to the edge. */}
              {isTop && (
                <>
                  <div className="pointer-events-none absolute inset-[5px] rounded-lg border border-primary/25" />
                  <LayersMark className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-primary/35" />
                  <LayersMark className="absolute bottom-2.5 right-2.5 h-3.5 w-3.5 rotate-180 text-primary/35" />
                </>
              )}
              {/* The count, on the top card only. It answers the question the pile
                  poses ("how many?") exactly, instead of leaving the largest surface
                  on screen blank.

                  The medallion behind it is load-bearing, not decoration: the lattice
                  runs straight under the digits and the count is unreadable against it
                  without a calm disc to sit on. */}
              {isTop && count > 1 && (
                <div className="absolute inset-0 flex items-center justify-center">
                  <div className="flex h-12 w-12 items-center justify-center rounded-full bg-card shadow-[0_0_0_1px_hsl(var(--primary)/0.25)]">
                    <span className="text-2xl font-semibold tabular-nums text-primary/55">
                      {count}
                    </span>
                  </div>
                </div>
              )}
            </div>
          );
        })}
        {count === 0 && (
          // No pattern here, deliberately: an empty slot should read as the absence
          // of a card, not as one more card that happens to say "nothing due".
          <div className="absolute inset-x-0 top-0 flex items-center justify-center rounded-xl border border-dashed text-xs text-muted-foreground" style={{ height: CARD_HEIGHT }}>
            nothing due
          </div>
        )}
      </div>
    </div>
  );
}
