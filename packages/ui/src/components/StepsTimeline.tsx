"use client";

import { useRouter } from "next/navigation";

import SidePanel from "@/components/ui/SidePanel";
import {
  chipText,
  clock,
  duration,
  groupSteps,
  stepCount,
  summaryLine,
  type TimelineStep,
  type TurnTimeline,
} from "@/lib/turnTimeline";

const MARKS: Record<TimelineStep["kind"], string> = { look: "•", work: "≈", do: "✎" };

function StepMark({ step }: { step: TimelineStep }) {
  if (step.state === "current") {
    return (
      <span
        className="inline-block h-6 w-6 rounded-full border-2 border-accent border-r-transparent animate-spin"
        aria-hidden="true"
      />
    );
  }
  if (step.state === "waits") {
    return (
      <span
        className="flex h-6 w-6 items-center justify-center rounded-full bg-amber-100 text-xs font-bold text-amber-700 dark:bg-amber-500/20 dark:text-amber-300"
        aria-hidden="true"
      >
        !
      </span>
    );
  }
  return (
    <span
      className="flex h-6 w-6 items-center justify-center rounded-full bg-accent/10 text-xs font-bold text-accent"
      aria-hidden="true"
    >
      {step.kind === "do" ? "✓" : MARKS[step.kind]}
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
            {timeline.reads.map((r) => (
              <span key={r} className="rounded-full border border-line bg-surface-elevated px-2.5 py-0.5 text-xs font-medium text-fg">
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
                const said = step.state === "current" ? "in progress" : step.state === "waits" ? "waiting for your OK" : "done";
                return (
                  <li key={index} className="relative flex gap-3 pb-3.5" aria-label={`${step.label} (${said})`}>
                    {index < total - 1 && (
                      <span className="absolute left-3 top-7 bottom-0 w-0.5 bg-line" aria-hidden="true" />
                    )}
                    <span className="relative flex w-6 flex-shrink-0 justify-center pt-0.5">
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
                      ) : step.state === "waits" ? (
                        <p className="mt-0.5 text-sm text-fg-muted">Waiting for your OK</p>
                      ) : step.detail ? (
                        <p className="mt-0.5 text-sm text-fg-muted">{step.detail}</p>
                      ) : null}
                      {step.found?.map((f) => (
                        <p key={f} className="mt-0.5 text-sm text-fg">
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
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-haspopup="dialog"
      className="mb-3 inline-flex max-w-full items-center gap-2 rounded-full border border-accent/20 bg-accent/10 py-1 pl-1.5 pr-3 text-xs font-semibold text-accent hover:bg-accent/15 cursor-pointer"
    >
      {running ? (
        <span className="ml-0.5 inline-block h-3.5 w-3.5 flex-shrink-0 rounded-full border-2 border-accent border-r-transparent animate-spin" aria-hidden="true" />
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
