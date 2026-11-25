// Home's "I can also take on" (core api/routes/take_on.py): one list of real
// steps the Executive offers to do next, with Do it, Or tell me and Hide. Pure
// fetches and a parser, so the node test runner can load it.

const API_BASE = "/api/backend";

export type OfferStatus = "" | "working" | "done" | "failed" | "card" | "asked";
export type HideReason = "not_useful" | "already_done" | "later";

export interface Offer {
  key: string;
  kind: string;
  kind_label: string;
  title: string;
  source: string;
  source_detail: string;
  preview: { label: string; value: string }[];
  status: OfferStatus;
  outcome: string;
  done: { text: string; link: string }[];
  session_id: string | null;
  decision_id: number | null;
  changed: boolean;
  said: string;
}

export interface OfferList {
  offers: Offer[];
  home_max: number;
  /** New offers are being made right now, so the list looks again shortly. */
  making: boolean;
  /** What it already does for them on its own, so it is never offered. */
  on_its_own: string[];
}

const STATUSES: OfferStatus[] = ["", "working", "done", "failed", "card", "asked"];

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

export function parseOffer(raw: unknown): Offer | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const key = str(r.key);
  const title = str(r.title);
  if (!key || !title) return null;
  const status = STATUSES.includes(r.status as OfferStatus) ? (r.status as OfferStatus) : "";
  const pairs = (v: unknown) => (Array.isArray(v) ? v : []).filter((x) => x && typeof x === "object");
  return {
    key,
    kind: str(r.kind),
    kind_label: str(r.kind_label),
    title,
    source: str(r.source),
    source_detail: str(r.source_detail),
    preview: pairs(r.preview)
      .map((p) => ({ label: str((p as Record<string, unknown>).label), value: str((p as Record<string, unknown>).value) }))
      .filter((p) => p.label && p.value),
    status,
    outcome: str(r.outcome),
    done: pairs(r.done)
      .map((d) => ({ text: str((d as Record<string, unknown>).text), link: str((d as Record<string, unknown>).link) }))
      .filter((d) => d.text),
    session_id: str(r.session_id) || null,
    decision_id: typeof r.decision_id === "number" ? r.decision_id : null,
    changed: r.changed === true,
    said: str(r.said),
  };
}

export function parseOfferList(raw: unknown): OfferList {
  const r = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const offers = (Array.isArray(r.offers) ? r.offers : []).map(parseOffer).filter((o): o is Offer => o !== null);
  const own = (Array.isArray(r.on_its_own) ? r.on_its_own : []).filter((x): x is string => typeof x === "string" && !!x);
  return {
    offers,
    home_max: typeof r.home_max === "number" && r.home_max > 0 ? r.home_max : 3,
    making: r.making === true,
    on_its_own: own,
  };
}

/** "drafting replies to your inbox, following up on emails nobody answered." */
export function ownLine(own: string[]): string {
  return own.length ? `${own.join(", ")}.` : "";
}

/** What Home's "I can also take on" shows for this many items (offers,
 * steps and "Give me more to work with" moves): the list, or with none, no
 * section on Home and the "nothing new" line on See all. */
export function takeOnView(items: number, all: boolean): "list" | "nothing" | "hidden" {
  if (items > 0) return "list";
  return all ? "nothing" : "hidden";
}

/** Whether any offer is still being worked on, so the list re-reads. */
export function anyWorking(offers: Offer[]): boolean {
  return offers.some((o) => o.status === "working");
}

/** "Your inbox · Dana, Nov 20" */
export function sourceLine(o: Offer): string {
  return [o.source, o.source_detail].filter(Boolean).join(" · ");
}

/** An offer that's no longer on the list (hidden on another screen, or aged
 *  out): the card should just go. */
export class OfferGone extends Error {}

async function detail(res: Response, fallback: string): Promise<Error> {
  if (res.status === 404) return new OfferGone("That's no longer on your list.");
  try {
    const data = await res.json();
    if (typeof data?.detail === "string") return new Error(data.detail);
  } catch {
    // not JSON
  }
  return new Error(fallback);
}

/** The caller's offers, or null when they can't be read. */
export async function getOffers(signal?: AbortSignal): Promise<OfferList | null> {
  const res = await fetch(`${API_BASE}/take-on`, { signal, cache: "no-store" });
  if (!res.ok) return null;
  return parseOfferList(await res.json());
}

async function post(key: string, action: string, body: unknown, fallback: string): Promise<Offer | null> {
  const res = await fetch(`${API_BASE}/take-on/${encodeURIComponent(key)}/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw await detail(res, fallback);
  if (res.status === 204) return null;
  return parseOffer(await res.json());
}

export function doOffer(key: string): Promise<Offer | null> {
  return post(key, "do", undefined, "It couldn't be started. Try again.");
}

export function sayOffer(key: string, text: string): Promise<Offer | null> {
  return post(key, "say", { text }, "I couldn't change it just now. Try again.");
}

export function undoOfferChange(key: string): Promise<Offer | null> {
  return post(key, "undo", undefined, "Couldn't undo that. Try again.");
}

export async function hideOffer(key: string, reason: HideReason): Promise<void> {
  await post(key, "hide", { reason }, "Couldn't hide that. Try again.");
}
