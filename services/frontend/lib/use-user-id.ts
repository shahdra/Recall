"use client";

import { useEffect, useState } from "react";

/** localStorage key. Changing this orphans every existing learner's cards. */
const USER_STORAGE_KEY = "recall.user_id";

/**
 * Identify the learner.
 *
 * Auth is out of scope (docs/spec.md), so a per-browser id stands in for an
 * account. Persisting it in localStorage is what makes long-term memory
 * observable: reload the page and the tutor still knows what you struggle with.
 *
 * Was a function in app-shell.tsx, called once and passed down as a prop. Now that
 * pages are routes rather than tabs there is no common parent to hold it, so each
 * page calls this for itself.
 */
function loadUserId(): string {
  if (typeof window === "undefined") return "";
  const existing = window.localStorage.getItem(USER_STORAGE_KEY);
  if (existing) return existing;
  const generated = `learner-${Math.random().toString(36).slice(2, 10)}`;
  window.localStorage.setItem(USER_STORAGE_KEY, generated);
  return generated;
}

/**
 * Returns "" on the first render and the real id after mount.
 *
 * The empty first value is not avoidable: there is no localStorage during server
 * rendering, and reading it during render would produce a hydration mismatch.
 * Every caller must therefore skip fetching while the id is empty — the effects
 * that consume it already guard on that.
 */
export function useUserId(): string {
  const [userId, setUserId] = useState("");
  useEffect(() => {
    setUserId(loadUserId());
  }, []);
  return userId;
}
