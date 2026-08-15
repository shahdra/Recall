"use client";

import { Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

/** localStorage key. Must stay in step with the inline script in app/layout.tsx. */
const KEY = "recall-theme";

/**
 * Light/dark switch.
 *
 * Until this existed the dark palette in globals.css was dead code: Tailwind is
 * configured `darkMode: ["class"]`, and nothing ever put `class="dark"` on <html>.
 *
 * The INITIAL theme is applied by a blocking inline script in app/layout.tsx, not
 * here. A React effect runs after the first paint, so choosing the theme in this
 * component would render light for one frame and then repaint dark — a white flash
 * on every load for anyone using dark mode. This component only reads what that
 * script already decided, then toggles it.
 */
export default function ThemeToggle() {
  // Starts null so neither icon is drawn until mounted. There is no `document` during
  // server rendering, and guessing would render a sun that flips to a moon on
  // hydration — a visible glitch and a hydration mismatch warning.
  const [dark, setDark] = useState<boolean | null>(null);

  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
  }, []);

  function toggle() {
    const next = !document.documentElement.classList.contains("dark");
    document.documentElement.classList.toggle("dark", next);
    // An explicit choice is remembered and from then on beats the OS setting, so
    // someone demoing on a light-set laptop can force dark and have it stick.
    try {
      window.localStorage.setItem(KEY, next ? "dark" : "light");
    } catch {
      // Private browsing can throw on write. The toggle still works for this
      // session; it just will not be remembered, which is a fine degradation.
    }
    setDark(next);
  }

  return (
    <button
      type="button"
      onClick={toggle}
      // The label says what the button DOES, not what the theme currently is —
      // "Dark mode" on a button is ambiguous about which way it goes.
      aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
      title={dark ? "Switch to light mode" : "Switch to dark mode"}
      className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border bg-card text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
    >
      {/* Before mount `dark` is null and neither icon renders. The button keeps its
          size regardless, so the header does not shift when the icon appears. */}
      {dark === null ? null : dark ? (
        <Sun className="h-4 w-4" />
      ) : (
        <Moon className="h-4 w-4" />
      )}
    </button>
  );
}
