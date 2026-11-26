"use client";

import Link from "next/link";

import { forYouRows } from "@/lib/briefingSummary";

// The quiet rows under the cards in Home's For you (lib/briefingSummary.ts
// forYouRows): whose OK the team's cards wait on, each count to act on, what
// changed on the watches, and, working solo, this week's review. A row opens
// its side panel or its page.
export function ForYouRows({
  waiting,
  actOn,
  watched,
  onOpenTeam,
  onOpenMonitoring,
  onWeeklyReview,
}: {
  waiting: { title: string; detail: string } | null;
  actOn: { key: string; text: string; href: string }[];
  watched: { title: string; detail: string } | null;
  onOpenTeam: () => void;
  onOpenMonitoring: () => void;
  /** Solo: this week's review is ready; opens it. */
  onWeeklyReview?: () => void;
}) {
  const rows = forYouRows({ waiting, actOn, watched });
  if (rows.length === 0 && !onWeeklyReview) return null;
  const rowClass =
    "group flex w-full items-center gap-3 px-4 py-3 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40";
  const inner = (title: string, detail: string) => (
    <>
      <div className="min-w-0 flex-1">
        <div className="text-[15px] font-medium text-fg transition-colors group-hover:text-accent">{title}</div>
        {detail && <div className="mt-0.5 text-sm text-fg-muted line-clamp-2">{detail}</div>}
      </div>
      <span aria-hidden className="text-lg text-fg-muted">
        ›
      </span>
    </>
  );
  return (
    <div className="mt-4 divide-y divide-line overflow-hidden rounded-2xl border border-line bg-surface-elevated">
      {rows.map((row) =>
        row.open === "link" ? (
          <Link key={row.key} href={row.href ?? "/"} className={rowClass}>
            {inner(row.title, row.detail)}
          </Link>
        ) : (
          <button
            key={row.key}
            type="button"
            onClick={row.open === "team" ? onOpenTeam : onOpenMonitoring}
            aria-haspopup="dialog"
            className={rowClass}
          >
            {inner(row.title, row.detail)}
          </button>
        ),
      )}
      {onWeeklyReview && (
        <button type="button" onClick={onWeeklyReview} aria-haspopup="dialog" className={rowClass}>
          {inner("This week's review is ready", "")}
        </button>
      )}
    </div>
  );
}
