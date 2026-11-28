// Which texts need a look on the Texts page: one that didn't go, or may not
// have. Those stay open at the top of the list (components/lists/FoldList.tsx);
// what was texted for the person and what was only read fold by day or person.
import type { TextThread } from "./api";

export type TextGroupKey = "look" | "texted" | "read";

export function groupOf(status: TextThread["status"]): TextGroupKey {
  if (status === "failed" || status === "unsure") return "look";
  if (status === "sent" || status === "sending") return "texted";
  return "read";
}
