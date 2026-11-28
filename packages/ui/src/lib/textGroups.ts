// The Texts page's sections: what needs a look first (a text that didn't go,
// or may not have), then what was texted for the person, then what was only
// read, folded behind its count. Most of a long list is read-only texts from
// businesses (deliveries, appointments, alerts), so they start folded.
import type { TextThread } from "./api";

export type TextGroupKey = "look" | "texted" | "read";

export interface TextGroup {
  key: TextGroupKey;
  title: string;
  threads: TextThread[];
  // How many show before "Show more"; 0 = folded behind its count.
  shownAtFirst: number;
}

const ORDER: { key: TextGroupKey; title: string; shownAtFirst: number }[] = [
  { key: "look", title: "Needs a look", shownAtFirst: Infinity },
  { key: "texted", title: "Texted for you", shownAtFirst: 5 },
  { key: "read", title: "Just read", shownAtFirst: 0 },
];

export function groupOf(status: TextThread["status"]): TextGroupKey {
  if (status === "failed" || status === "unsure") return "look";
  if (status === "sent" || status === "sending") return "texted";
  return "read";
}

/** The page's sections in order, each keeping the list's order (latest
 * first). A section with nothing in it is left out. */
export function textGroups(threads: TextThread[]): TextGroup[] {
  return ORDER.map((g) => ({ ...g, threads: threads.filter((t) => groupOf(t.status) === g.key) })).filter(
    (g) => g.threads.length > 0,
  );
}
