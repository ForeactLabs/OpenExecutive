"use client";

import { useRouter } from "next/navigation";

import { useState } from "react";

import Icon, { type IconName } from "@/components/Icon";
import SidePanel from "@/components/ui/SidePanel";
import {
  chipText,
  clock,
  duration,
  groupSteps,
  stepCount,
  summaryLine,
  type StepIcon,
  type TimelineStep,
  type TurnTimeline,
} from "@/lib/turnTimeline";

// What each step touched, as a picture (orchestrator/step_icons.py); a step
// with none shows its group's.
const PICTURES: Record<StepIcon, IconName> = {
  mail: "mail",
  calendar: "calendar",
  web: "globe",
  page: "eye",
  click: "cursor",
  doc: "doc",
  person: "user",
  chat: "chat",
  phone: "phone",
  memory: "bookmark",
  think: "sparkles",
  task: "check-circle",
  workflow: "bolt",
  card: "bell",
  app: "grid",
};
const BY_KIND: Record<TimelineStep["kind"], IconName> = { look: "search", work: "sparkles", do: "check-circle" };

// A website's own icon, fetched by the server; the step's picture if it has none.
function SiteIcon({ site, fallback }: { site: string; fallback: IconName }) {
  const [failed, setFailed] = useState(false);
  if (failed) return <Icon name={fallback} size="h-4 w-4" />;
  return (
    // eslint-disable-next-line @next/next/no-img-element -- a tiny icon from our own API, not an optimizable asset
    <img
      src={`/api/backend/favicon/${encodeURIComponent(site)}`}
      alt=""
      className="h-5 w-5 rounded-sm object-contain"
      onError={() => setFailed(true)}
    />
  );
}

// The step's picture in a circle, with a corner badge for how it went: a
// tick for a change it made, a clock for one waiting on your OK, a dash for
// one that didn't go through and a square for one stopped. A running step
// has a spinning ring.
function StepMark({ step }: { step: TimelineStep }) {
  const picture = step.icon ? PICTURES[step.icon] : BY_KIND[step.kind];
  // The site's own icon for a step on the site itself (opening it); one on
  // its page shows what it did there (an eye, a pointer).
  const site = step.site && (!step.icon || step.icon === "web") ? step.site : "";
  const dim = step.state === "tried" || step.state === "stopped";
  const tone = site
    ? "bg-white border border-line"
    : dim
      ? "bg-surface-overlay text-fg-subtle"
      : step.state === "waits"
        ? "bg-amber-100 text-amber-700 dark:bg-amber-500/20 dark:text-amber-300"
        : step.kind === "work"
          ? "bg-violet-50 text-violet-600 dark:bg-violet-500/15 dark:text-violet-300"
          : "bg-accent/10 text-accent";
  const badge =
    step.state === "waits"
      ? { name: "clock" as IconName, cls: "bg-amber-500" }
      : step.state === "tried"
        ? { name: "minus" as const, cls: "bg-fg-subtle" }
        : step.state === "stopped"
          ? { name: "stop" as IconName, cls: "bg-fg-subtle" }
          : step.state === "done" && step.kind === "do"
            ? { name: "check" as IconName, cls: "bg-emerald-500" }
            : null;
  return (
    <span
      className={`relative flex h-8 w-8 items-center justify-center rounded-full ${tone} ${dim && site ? "grayscale opacity-60" : ""}`}
      aria-hidden="true"
    >
      {site ? <SiteIcon key={site} site={site} fallback={picture} /> : <Icon name={picture} size="h-4 w-4" />}
      {step.state === "current" && (
        <span className="absolute -inset-[3px] rounded-full border-2 border-accent border-r-transparent animate-spin" />
      )}
      {badge && (
        <span
          className={`absolute -bottom-1 -right-1 flex h-4 w-4 items-center justify-center rounded-full border-2 border-surface-elevated text-white ${badge.cls}`}
        >
          {badge.name === "minus" ? (
            <span className="block h-0.5 w-1.5 rounded bg-white" />
          ) : (
            <Icon name={badge.name} size="h-2.5 w-2.5" strokeWidth={3} fill={badge.name === "stop" ? "currentColor" : "none"} />
          )}
        </span>
      )}
    </span>
  );
}

function showCard(id: number, close: () => void, openNeedsYou: () => void) {
  close();
  // The card sits under the reply in the chat (ChatActionCards); when it has
  // gone from there, Needs you still has it.
  requestAnimationFrame(() => {
    const el = document.querySelector(`[data-decision-id="${id}"]`);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
    else openNeedsYou();
  });
}

// The "What I did" timeline: a short summary of the turn, what it read, and
// every step in time order, grouped by what it was (looked at, worked out,
// did). Open while the turn runs too: the current step spins.
export function StepsTimelineBody({ timeline, onClose }: { timeline: TurnTimeline; onClose: () => void }) {
  const router = useRouter();
  const groups = groupSteps(timeline);
  const total = timeline.steps.length;
  return (
    <div>
      <div className="mb-4 rounded-2xl border border-line bg-surface-overlay/50 px-4 py-3 text-sm leading-relaxed text-fg">
        {timeline.task && <p className="font-semibold">{timeline.task}</p>}
        <p>{summaryLine(timeline)}</p>
        {timeline.reads && timeline.reads.length > 0 && (
          <div className="mt-2.5 flex flex-wrap gap-1.5">
            {timeline.reads.map((r, i) => (
              <span key={`${i}-${r}`} className="rounded-full border border-line bg-surface-elevated px-2.5 py-0.5 text-xs font-medium text-fg">
                {r}
              </span>
            ))}
          </div>
        )}
      </div>
      <ol className="list-none p-0 m-0">
        {groups.map((group, g) => (
          <li key={`${g}-${group.name}`}>
            <p className="mb-1.5 mt-3 ml-9 text-[11px] font-bold uppercase tracking-wider text-fg-subtle">{group.name}</p>
            <ol className="list-none p-0 m-0">
              {group.steps.map(({ step, index }) => {
                const said = step.state === "current" ? "in progress" : step.state === "waits" ? (timeline.done ? "asked for your OK" : "waiting for your OK") : step.state === "tried" ? "didn't go through" : step.state === "stopped" ? "stopped" : "done";
                return (
                  <li key={index} className="relative flex gap-3 pb-3.5" aria-label={`${step.label} (${said})`}>
                    {index < total - 1 && (
                      <span className="absolute left-[15px] top-9 bottom-0.5 w-0.5 bg-line" aria-hidden="true" />
                    )}
                    <span className="relative flex w-8 flex-shrink-0 justify-center">
                      <StepMark step={step} />
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="flex gap-2 text-sm font-semibold text-fg">
                        <span className="min-w-0 break-words">{step.label}</span>
                        {step.state !== "current" && (
                          <span className="ml-auto flex-shrink-0 text-xs font-normal text-fg-subtle tabular-nums">
                            {clock(step.at)}
                          </span>
                        )}
                      </p>
                      {step.state === "current" ? (
                        <p className="mt-0.5 text-sm text-fg-muted">Working on it…</p>
                      ) : step.state === "tried" ? (
                        <p className="mt-0.5 text-sm text-fg-muted">Didn&apos;t go through</p>
                      ) : step.state === "stopped" ? (
                        <p className="mt-0.5 text-sm text-fg-muted">Stopped before it finished</p>
                      ) : step.state === "waits" ? (
                        <p className="mt-0.5 text-sm text-fg-muted">{timeline.done ? "Asked for your OK" : "Waiting for your OK"}</p>
                      ) : step.detail ? (
                        <p className="mt-0.5 text-sm text-fg-muted">{step.detail}</p>
                      ) : null}
                      {step.found?.map((f, i) => (
                        <p key={`${i}-${f}`} className="mt-0.5 text-sm text-fg">
                          {f}
                        </p>
                      ))}
                      {step.state === "waits" && step.card_id !== undefined && (
                        <button
                          type="button"
                          onClick={() => showCard(step.card_id as number, onClose, () => router.push("/today"))}
                          className="mt-1 text-xs font-semibold text-accent hover:underline cursor-pointer"
                        >
                          See the card ›
                        </button>
                      )}
                    </div>
                  </li>
                );
              })}
            </ol>
          </li>
        ))}
      </ol>
    </div>
  );
}

// The chip on a reply, and on a turn while it works: "✓ 8 steps · 52s · See
// what I did", or the step it is on now with "4 steps · 0:19". Tapping it
// opens the timeline (StepsPanel), which the chat keeps open from the running
// turn to its finished reply.
export default function StepsChip({
  timeline,
  elapsed,
  nowLabel,
  onOpen,
}: {
  timeline: TurnTimeline;
  elapsed?: number;
  // What it is doing now, in place of the current step's label (e.g. writing
  // the answer once its steps are done).
  nowLabel?: string | null;
  onOpen: () => void;
}) {
  const chip = chipText(timeline, elapsed);
  const now = timeline.done ? null : nowLabel || chip.now;
  const { count } = chip;
  const running = !timeline.done;
  // A step that was stopped or didn't go through: no green tick.
  const unsettled = timeline.steps.some((s) => s.state === "stopped" || s.state === "tried");
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-haspopup="dialog"
      className="mb-3 inline-flex max-w-full items-center gap-2 rounded-full border border-accent/20 bg-accent/10 py-1 pl-1.5 pr-3 text-xs font-semibold text-accent hover:bg-accent/15 cursor-pointer"
    >
      {running ? (
        <span className="ml-0.5 inline-block h-3.5 w-3.5 flex-shrink-0 rounded-full border-2 border-accent border-r-transparent animate-spin" aria-hidden="true" />
      ) : unsettled ? (
        <span className="flex h-4 w-4 flex-shrink-0 items-center justify-center rounded-full bg-surface-overlay text-[10px] font-bold text-fg-muted" aria-hidden="true">
          –
        </span>
      ) : (
        <span className="flex h-4 w-4 flex-shrink-0 items-center justify-center rounded-full bg-emerald-500 text-[10px] text-white" aria-hidden="true">
          ✓
        </span>
      )}
      {now && <span className="min-w-0 truncate" aria-live="polite">{now}</span>}
      <span className={`flex-shrink-0 ${now ? "font-medium opacity-80" : ""}`}>{now ? `· ${count}` : count}</span>
      <span className="flex-shrink-0 font-medium opacity-80">{now ? "›" : "· See what I did ›"}</span>
    </button>
  );
}

// The timeline in a side panel (full screen on a phone).
export function StepsPanel({
  timeline,
  elapsed,
  open,
  onClose,
}: {
  timeline: TurnTimeline | null;
  elapsed?: number;
  open: boolean;
  onClose: () => void;
}) {
  if (!timeline) return null;
  const running = !timeline.done;
  const subtitle = running
    ? `${stepCount(timeline.steps.filter((s) => s.state !== "current").length)} done · ${clock(elapsed ?? timeline.seconds)} so far`
    : `${stepCount(timeline.steps.length)} · ${duration(timeline.seconds)}`;
  return (
    <SidePanel open={open} onClose={onClose} title={running ? "Working on it" : "What I did"} subtitle={subtitle}>
      <StepsTimelineBody timeline={timeline} onClose={onClose} />
    </SidePanel>
  );
}
