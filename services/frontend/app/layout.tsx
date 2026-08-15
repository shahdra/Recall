import type { Metadata } from "next";
import { Inter } from "next/font/google";
import { Toaster } from "sonner";
import "./globals.css";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Recall — adaptive study tutor",
  description:
    "Turn any study material into an adaptive quiz that learns what you don't know.",
};

/**
 * Applies the saved (or OS-preferred) theme before the first paint.
 *
 * This has to be a blocking inline script rather than a React effect. Effects run
 * AFTER the first paint, so choosing the theme there renders light for one frame and
 * then repaints dark — a visible white flash on every load for anyone using dark
 * mode. Being inline and synchronous in <head> means the class is on <html> before
 * the browser draws anything.
 *
 * No saved choice falls back to the OS preference, so a first visit matches the rest
 * of the user's system. An explicit choice is stored and from then on wins over the
 * OS — see components/theme-toggle.tsx.
 *
 * Wrapped in try/catch because localStorage access throws in some private-browsing
 * modes; the OS preference is used in that case and nothing breaks.
 */
const THEME_INIT = `
(function () {
  try {
    var saved = localStorage.getItem("recall-theme");
    var dark = saved
      ? saved === "dark"
      : window.matchMedia("(prefers-color-scheme: dark)").matches;
    if (dark) document.documentElement.classList.add("dark");
  } catch (e) {
    if (window.matchMedia("(prefers-color-scheme: dark)").matches) {
      document.documentElement.classList.add("dark");
    }
  }
})();
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  // suppressHydrationWarning: the script above mutates <html>'s class list before
  // React hydrates, so the server-rendered markup and the live DOM legitimately
  // differ on this one attribute. Without it React logs a mismatch warning for what
  // is the intended behaviour.
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT }} />
      </head>
      <body className={inter.className}>
        {children}
        <Toaster richColors position="top-center" />
      </body>
    </html>
  );
}
