// One line of what a held card would do, as the server words it
// (``orchestrator/card_details.py``): a label and its value, plus a link
// (a Drive file shown by its name) or rows of cells (a sheet's values).
export interface CardDetail {
  field: string;
  value: string;
  href?: string;
  table?: string[][];
}

const SAFE_LINK = /^https:\/\/(?:drive|docs)\.google\.com\//;

/** Read one detail from a payload, keeping only a Google link and rows of text. */
export function cardDetail(d: { field?: unknown; label?: unknown; value?: unknown; href?: unknown; table?: unknown }): CardDetail {
  const out: CardDetail = { field: String(d.field ?? d.label ?? ""), value: String(d.value ?? "") };
  if (typeof d.href === "string" && SAFE_LINK.test(d.href)) out.href = d.href;
  if (Array.isArray(d.table) && d.table.length > 0 && d.table.every((row) => Array.isArray(row))) {
    out.table = (d.table as unknown[][]).map((row) => row.map((cell) => String(cell ?? "")));
  }
  return out;
}

export function cardDetails(raw: unknown): CardDetail[] {
  return Array.isArray(raw) ? raw.filter((d) => d && typeof d === "object").map(cardDetail) : [];
}
