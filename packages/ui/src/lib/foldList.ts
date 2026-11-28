// Long lists shown the way Chats shows chats (components/lists/FoldList.tsx):
// what needs the person first, then folding groups by day or by person, each
// item one line that opens in place. This is the grouping, kept apart from
// the React so it can be tested.

export type FoldTone = "good" | "warn" | "bad" | "plain" | "accent";

export interface FoldEntry {
  id: string;
  // Who it was with (or what it was), bold at the start of the line.
  who: string;
  // What it was about, in one line.
  gist: string;
  // ISO time; "" sorts last and lands under Earlier.
  at: string;
  // What "By person" groups it under; `who` when left out.
  person?: string;
  tone: FoldTone;
  // Its status in a few words (e.g. "Sent as you").
  tag?: string;
  // Stays open at the top, above the groups.
  needs?: boolean;
  // More words search should find (a summary, the number, what was said).
  words?: string;
}

export type FoldMode = "day" | "person";

export interface FoldGroup<T extends FoldEntry> {
  key: string;
  label: string;
  items: T[];
}

export type DayKey = "today" | "yesterday" | "week" | "earlier";

const DAY_LABEL: Record<DayKey, string> = {
  today: "Today",
  yesterday: "Yesterday",
  week: "Previous 7 days",
  earlier: "Earlier",
};
const DAY_ORDER: DayKey[] = ["today", "yesterday", "week", "earlier"];

function time(iso: string): number {
  const t = iso ? new Date(iso).getTime() : NaN;
  return Number.isNaN(t) ? -Infinity : t;
}

/** Which day group `iso` falls in, by the viewer's own calendar days. */
export function dayOf(iso: string, now: Date = new Date()): DayKey {
  const t = time(iso);
  if (t === -Infinity) return "earlier";
  // Calendar midnights, so a day the clocks change on still counts as one day.
  const midnight = (back: number) => new Date(now.getFullYear(), now.getMonth(), now.getDate() - back).getTime();
  if (t >= midnight(0)) return "today";
  if (t >= midnight(1)) return "yesterday";
  if (t >= midnight(6)) return "week";
  return "earlier";
}

/** Whether `entry` has every word of `query` in it (any case, any order). */
export function matches(entry: FoldEntry, query: string): boolean {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay = [entry.who, entry.person, entry.gist, entry.tag, entry.words].filter(Boolean).join(" ").toLowerCase();
  return words.every((w) => hay.includes(w));
}

function newestFirst<T extends FoldEntry>(items: T[]): T[] {
  return [...items].sort((a, b) => time(b.at) - time(a.at));
}

/** The list split up: what needs the person (newest first), then the rest
 * grouped by day (in day order) or by person (the one heard from last
 * first). Empty groups are left out. */
export function foldGroups<T extends FoldEntry>(
  items: T[],
  mode: FoldMode,
  now: Date = new Date(),
): { needs: T[]; groups: FoldGroup<T>[] } {
  const sorted = newestFirst(items);
  const needs = sorted.filter((i) => i.needs);
  const rest = sorted.filter((i) => !i.needs);
  if (mode === "day") {
    const groups = DAY_ORDER.map((key) => ({
      key: `day:${key}`,
      label: DAY_LABEL[key],
      items: rest.filter((i) => dayOf(i.at, now) === key),
    })).filter((g) => g.items.length > 0);
    return { needs, groups };
  }
  const byPerson = new Map<string, T[]>();
  for (const item of rest) {
    const name = (item.person || item.who).trim() || item.who;
    const key = name.toLowerCase();
    const list = byPerson.get(key);
    if (list) list.push(item);
    else byPerson.set(key, [item]);
  }
  const groups = [...byPerson.entries()].map(([key, list]) => ({
    key: `person:${key}`,
    label: (list[0].person || list[0].who).trim() || list[0].who,
    items: list,
  }));
  return { needs, groups };
}

/** Whether a group shows open. What the person chose wins, even while
 * searching; otherwise a search opens every group, and without one the newest
 * day is open and the rest are folded. */
export function groupOpen(
  key: string,
  index: number,
  state: { folds: Record<string, boolean>; searching: boolean; mode: FoldMode },
): boolean {
  if (key in state.folds) return state.folds[key];
  return state.searching || (state.mode === "day" && index === 0);
}

/** The time on an item's line. Inside a day group the day is already said,
 * so today and yesterday show the hour; elsewhere (Needs you, By person) the
 * line says the day itself. */
export function shortWhen(iso: string, opts: { inDay?: boolean; now?: Date } = {}): string {
  const now = opts.now ?? new Date();
  const t = time(iso);
  if (t === -Infinity) return "";
  const d = new Date(t);
  const hour = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  const day = dayOf(iso, now);
  if (day === "today") return hour;
  if (day === "yesterday") return opts.inDay ? hour : "Yesterday";
  if (day === "week") return d.toLocaleDateString(undefined, { weekday: "short" }) + (opts.inDay ? ` ${hour}` : "");
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
