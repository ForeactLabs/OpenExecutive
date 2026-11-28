// What a chat turn did, step by step: the "What I did" timeline. The backend
// (orchestrator/turn_timeline.py) sends the whole picture as a `timeline`
// event each time it changes, and saves the last one with the reply
// (`steps` on a saved message), so a running turn and a finished one show
// the same thing.

export type StepKind = "look" | "work" | "do";
// "tried": a step that would change something, with nothing saying it did.
// "stopped": the step running when the turn was stopped or timed out.
export type StepState = "done" | "current" | "waits" | "tried" | "stopped";

export interface TimelineStep {
  label: string;
  kind: StepKind;
  // Seconds into the turn.
  at: number;
  state: StepState;
  // What it read in this step ("4 emails").
  detail?: string;
  // What it found worth knowing, one line each.
  found?: string[];
  // The approval card the step waits on, when it has one.
  card_id?: number;
  // What it touched (orchestrator/step_icons.py): the step's picture.
  icon?: StepIcon;
  // The website it was on: its own icon (GET /favicon/{site}) shows instead.
  site?: string;
}

export const STEP_ICONS = [
  "mail", "calendar", "web", "page", "click", "doc", "person", "chat",
  "phone", "memory", "think", "task", "workflow", "card", "app",
] as const;
export type StepIcon = (typeof STEP_ICONS)[number];

// A public domain name, as the server checks it (step_icons.valid_site):
// never an address with a path, a port or an IP.
const SITE = /^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)(?:\.(?!-)[a-z0-9-]{1,63}(?<!-))+$/;

export function validSite(site: unknown): site is string {
  if (typeof site !== "string" || !SITE.test(site)) return false;
  // The last name starts with a letter: never an IP in any spelling.
  return /^[a-z]/.test(site.slice(site.lastIndexOf(".") + 1));
}

export interface TurnTimeline {
  task: string;
  seconds: number;
  done: boolean;
  steps: TimelineStep[];
  // What the whole turn read ("4 emails", "1 file", "Your calendar").
  reads?: string[];
}

const KINDS: readonly StepKind[] = ["look", "work", "do"];
const STATES: readonly StepState[] = ["done", "current", "waits", "tried", "stopped"];

function str(raw: unknown): string {
  return typeof raw === "string" ? raw.trim() : "";
}

// Read a `timeline` event or a saved message's `steps` as carefully as any
// other stored field: anything malformed is dropped, never thrown.
export function timelineFrom(raw: unknown): TurnTimeline | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (!Array.isArray(r.steps)) return null;
  const steps: TimelineStep[] = [];
  for (const s of r.steps) {
    if (!s || typeof s !== "object") continue;
    const step = s as Record<string, unknown>;
    const label = str(step.label);
    if (!label) continue;
    const kind = KINDS.includes(step.kind as StepKind) ? (step.kind as StepKind) : "look";
    const state = STATES.includes(step.state as StepState) ? (step.state as StepState) : "done";
    const out: TimelineStep = {
      label,
      kind,
      at: typeof step.at === "number" && Number.isFinite(step.at) ? Math.max(0, step.at) : 0,
      state,
    };
    const detail = str(step.detail);
    if (detail) out.detail = detail;
    if (Array.isArray(step.found)) {
      const found = step.found.map(str).filter(Boolean);
      if (found.length > 0) out.found = found;
    }
    if (typeof step.card_id === "number" && Number.isInteger(step.card_id)) out.card_id = step.card_id;
    if (STEP_ICONS.includes(step.icon as StepIcon)) out.icon = step.icon as StepIcon;
    if (validSite(step.site)) out.site = step.site;
    steps.push(out);
  }
  if (steps.length === 0) return null;
  const reads = Array.isArray(r.reads) ? r.reads.map(str).filter(Boolean) : [];
  const done = r.done !== false;
  return {
    task: str(r.task),
    seconds: typeof r.seconds === "number" && Number.isFinite(r.seconds) ? Math.max(0, Math.round(r.seconds)) : 0,
    done,
    // A saved turn has nothing still running.
    steps: done ? steps.map((s) => (s.state === "current" ? { ...s, state: "done" } : s)) : steps,
    ...(reads.length > 0 ? { reads } : {}),
  };
}

// "52s", "1m 12s".
export function duration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rest = s % 60;
  return rest ? `${m}m ${rest}s` : `${m}m`;
}

// "0:19", for a turn still running.
export function clock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function stepCount(n: number): string {
  return n === 1 ? "1 step" : `${n} steps`;
}

// The chip on a reply: "8 steps · 52s", or while it works, "4 steps · 0:19"
// with what it is doing now.
export function chipText(t: TurnTimeline, elapsed?: number): { now: string | null; count: string } {
  if (t.done) return { now: null, count: `${stepCount(t.steps.length)} · ${duration(t.seconds)}` };
  const current = t.steps.find((s) => s.state === "current");
  const done = t.steps.filter((s) => s.state !== "current").length;
  return {
    now: current?.label ?? null,
    count: `${stepCount(done)} · ${clock(elapsed ?? t.seconds)}`,
  };
}

const GROUP_NAMES: Record<StepKind, string> = {
  look: "Looked at",
  work: "Worked out",
  do: "Did",
};
const GROUP_NAMES_LIVE: Record<StepKind, string> = {
  look: "Looking at",
  work: "Working out",
  do: "Doing",
};

// The steps in a run of groups, in the order they happened: a new group
// starts whenever the kind changes, so the timeline stays in time order.
export function groupSteps(
  t: TurnTimeline,
): { name: string; steps: { step: TimelineStep; index: number }[] }[] {
  const names = t.done ? GROUP_NAMES : GROUP_NAMES_LIVE;
  const out: { name: string; kind: StepKind; steps: { step: TimelineStep; index: number }[] }[] = [];
  t.steps.forEach((step, index) => {
    const last = out[out.length - 1];
    if (last && last.kind === step.kind) last.steps.push({ step, index });
    else out.push({ name: names[step.kind], kind: step.kind, steps: [{ step, index }] });
  });
  return out.map(({ name, steps }) => ({ name, steps }));
}

function joinWords(items: string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

// The short "what I did" above the steps, from what the turn reported:
// what it read, what it did, and what still waits on the person.
export function summaryLine(t: TurnTimeline): string {
  const parts: string[] = [];
  const reads = (t.reads ?? []).map((r) => r.replace(/^Your /, "your ").replace(/^Connected /, "connected "));
  if (reads.length > 0) parts.push(`${t.done ? "Read" : "Reading"} ${joinWords(reads)}.`);
  const worked = t.steps.filter((s) => s.kind === "work").length;
  if (worked > 0) parts.push(t.done ? `Worked it through in ${stepCount(worked)}.` : "Working it through.");
  const did = t.steps.filter((s) => s.kind === "do" && s.state === "done").length;
  if (did > 0) parts.push(did === 1 ? "Did 1 thing." : `Did ${did} things.`);
  const waits = t.steps.filter((s) => s.state === "waits").length;
  // A saved reply's waits may have been answered since: say it asked.
  if (waits > 0) {
    parts.push(t.done ? `${stepCount(waits)} asked for your OK.` : waits === 1 ? "1 step waits for your OK." : `${waits} steps wait for your OK.`);
  }
  const tried = t.steps.filter((s) => s.state === "tried").length;
  if (tried > 0) parts.push(tried === 1 ? "1 step didn't go through." : `${tried} steps didn't go through.`);
  if (t.steps.some((s) => s.state === "stopped")) parts.push("Stopped before it finished.");
  if (parts.length === 0) parts.push(t.done ? `Took ${stepCount(t.steps.length)}.` : "Working on it.");
  return parts.join(" ");
}
