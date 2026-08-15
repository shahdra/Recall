"use client";

import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { startSession } from "@/lib/api";
import type { LearnerProfile } from "@/lib/types";
import { formatPercent } from "@/lib/utils";

interface Props {
  userId: string;
}

/** The weakest topics, worst first — mirrors how the tutor's prompt is built. */
function weakestTopics(profile: LearnerProfile): Array<[string, number]> {
  return Object.entries(profile.weak_topics ?? {})
    .filter(([, rate]) => Number.isFinite(rate))
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5);
}

export default function ProgressView({ userId }: Props) {
  const [profile, setProfile] = useState<LearnerProfile | null>(null);
  const [dueCount, setDueCount] = useState(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!userId) return;
    let active = true;

    async function load() {
      setLoading(true);
      try {
        // /session/start already returns the profile alongside what is due, so
        // one call covers this whole screen.
        const session = await startSession(userId);
        if (!active) return;
        setProfile(session.profile ?? {});
        setDueCount(session.cards.length);
      } catch (error) {
        if (!active) return;
        toast.error(error instanceof Error ? error.message : "Couldn't load progress.");
      } finally {
        if (active) setLoading(false);
      }
    }

    load();
    return () => {
      active = false;
    };
  }, [userId]);

  if (loading) {
    return (
      <div className="flex flex-col items-center gap-3 py-16 text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
        <p className="text-sm">Loading your progress…</p>
      </div>
    );
  }

  const stats = profile?.stats ?? {};
  const reviews = Number(stats.total_reviews ?? 0);
  const topics = profile ? weakestTopics(profile) : [];

  if (!reviews) {
    return (
      <div className="space-y-2 py-16 text-center">
        <p className="font-medium">No reviews yet</p>
        <p className="text-sm text-muted-foreground">
          Answer a few cards and your accuracy and weak topics will show up here.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold">Your progress</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Recall remembers this between sessions and drills what you miss most.
        </p>
      </div>

      <dl className="grid grid-cols-3 gap-3">
        {[
          { label: "Cards reviewed", value: String(reviews) },
          { label: "Accuracy", value: formatPercent(Number(stats.accuracy)) },
          { label: "Due now", value: String(dueCount) },
        ].map((tile) => (
          <div key={tile.label} className="rounded-xl border bg-card p-4">
            <dt className="text-xs text-muted-foreground">{tile.label}</dt>
            <dd className="mt-1 text-2xl font-semibold tabular-nums">{tile.value}</dd>
          </div>
        ))}
      </dl>

      <div className="space-y-3">
        <h3 className="text-sm font-semibold">Topics to work on</h3>
        {topics.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Nothing stands out yet — keep going and weak spots will surface here.
          </p>
        ) : (
          <ul className="space-y-2">
            {topics.map(([topic, missRate]) => (
              <li key={topic} className="rounded-lg border bg-card p-3">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-sm font-medium">{topic}</span>
                  <span className="text-xs tabular-nums text-muted-foreground">
                    missed {formatPercent(missRate)} of the time
                  </span>
                </div>
                {/* A bar rather than a bare number: relative severity across
                    topics is the thing worth seeing at a glance. */}
                <div
                  className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted"
                  role="presentation"
                >
                  <div
                    className="h-full rounded-full bg-danger"
                    style={{ width: `${Math.min(100, Math.round(missRate * 100))}%` }}
                  />
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {profile?.notes ? (
        <div className="rounded-xl border bg-muted/40 p-4">
          <h3 className="text-sm font-semibold">What your tutor has noticed</h3>
          <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
            {profile.notes}
          </p>
        </div>
      ) : null}
    </div>
  );
}
