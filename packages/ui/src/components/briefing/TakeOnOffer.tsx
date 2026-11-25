"use client";

// Home's "I can also take on": one offer card and the list's reads
// (core briefing/take_on.py). Each offer is a real step the Executive can
// take next, with where it came from and exactly what it touches. Do it runs
// the step as you right here: Working, then it leaves the list for Done for
// you, or the same card turns into Approve / Skip when your Approvals say it
// waits. Or tell me changes the offer in your words first. Hide asks why, and
// the list learns from it.

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { approveDecision, rejectDecision } from "@/lib/api";
import { AS_ME_HREF, AS_YOU_HREF } from "@/lib/reach";
import {
  anyWorking,
  doOffer,
  getOffers,
  hideOffer,
  OfferGone,
  ownLine,
  sayOffer,
  sourceLine,
  undoOfferChange,
  type HideReason,
  type Offer,
} from "@/lib/takeOnApi";

import { buttonClass } from "../ui/Button";
import { SayBox } from "./CardTalk";
import { Spinner } from "../ui/Spinner";

const POLL_MS = 5000;
const POLL_MAX_MS = 20 * 60 * 1000;

const HIDE_REASONS: { reason: HideReason; label: string }[] = [
  { reason: "not_useful", label: "Not useful" },
  { reason: "already_done", label: "Already done" },
  { reason: "later", label: "Later" },
];

export function Chip({ text }: { text: string }) {
  return (
    <span className="text-[11px] font-bold uppercase tracking-wider text-accent">{text}</span>
  );
}

export function OfferCard({
  offer,
  onChanged,
  onGone,
  onOpenChat,
}: {
  offer: Offer;
  onChanged: (o: Offer) => void;
  onGone: (key: string) => void;
  onOpenChat?: (sessionId: string) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hiding, setHiding] = useState(false);

  const run = async (what: string, fn: () => Promise<void>) => {
    setBusy(what);
    setError(null);
    try {
      await fn();
    } catch (e) {
      // Already off the list (hidden on another screen): the card just goes.
      if (e instanceof OfferGone) return onGone(offer.key);
      setError(e instanceof Error ? e.message : "Something went wrong. Try again.");
    } finally {
      setBusy(null);
    }
  };

  const doIt = () =>
    run("do", async () => {
      const o = await doOffer(offer.key);
      onChanged(o ?? { ...offer, status: "working" });
    });
  const say = async (text: string) => {
    setError(null);
    try {
      const o = await sayOffer(offer.key, text);
      if (o) onChanged(o);
    } catch (e) {
      if (e instanceof OfferGone) return onGone(offer.key);
      throw e;
    }
  };
  const undo = () =>
    run("undo", async () => {
      const o = await undoOfferChange(offer.key);
      if (o) onChanged(o);
    });
  const hide = (reason: HideReason) =>
    run("hide", async () => {
      await hideOffer(offer.key, reason);
      onGone(offer.key);
    });
  const approve = () =>
    run("approve", async () => {
      if (offer.decision_id) await approveDecision(offer.decision_id);
      onChanged({ ...offer, status: "working", outcome: "" });
    });
  const skip = () =>
    run("skip", async () => {
      if (offer.decision_id) await rejectDecision(offer.decision_id);
      onGone(offer.key);
    });

  const seeHow =
    offer.session_id && onOpenChat ? (
      <button type="button" onClick={() => onOpenChat(offer.session_id as string)} className={buttonClass("ghost", "sm")}>
        See how
      </button>
    ) : null;

  return (
    <div className="flex flex-col gap-1.5 rounded-2xl border border-line bg-surface-elevated p-4">
      <div className="flex items-center gap-2">
        <Chip text={offer.kind_label || "Step"} />
        {offer.changed && (
          <span className="rounded-full bg-amber-500/10 px-2 py-0.5 text-[11px] font-semibold text-amber-800 dark:text-amber-300">
            Changed
          </span>
        )}
      </div>
      <p className="text-[15px] font-semibold leading-snug text-fg">{offer.title}</p>
      {sourceLine(offer) && <p className="text-sm text-fg-muted">From {sourceLine(offer)}</p>}
      {offer.preview.length > 0 && (
        <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 rounded-xl bg-surface px-3 py-2 text-sm">
          {offer.preview.map((p) => (
            <div key={p.label + p.value} className="contents">
              <dt className="text-fg-muted">{p.label}</dt>
              <dd className="min-w-0 break-words text-fg">{p.value}</dd>
            </div>
          ))}
        </dl>
      )}

      {offer.status === "working" && (
        <p className="mt-2 flex items-center gap-2 text-sm text-fg-muted" role="status">
          <Spinner size="sm" /> Working on it…
        </p>
      )}

      {offer.status === "card" && (
        <div className="mt-2 space-y-2" role="status">
          <p className="text-sm text-fg">{offer.outcome || "This one waits for your OK."}</p>
          <div className="flex flex-wrap gap-2">
            <button type="button" disabled={busy !== null} onClick={() => void approve()} className={buttonClass("primary", "sm")}>
              {busy === "approve" ? "Approving…" : "Approve"}
            </button>
            <button type="button" disabled={busy !== null} onClick={() => void skip()} className={buttonClass("secondary", "sm")}>
              Skip
            </button>
            {seeHow}
          </div>
        </div>
      )}

      {offer.status === "asked" && (
        <div className="mt-2 space-y-2" role="status">
          <p className="text-sm text-fg">{offer.outcome || "I have a question before I start."}</p>
          <div className="flex flex-wrap gap-2">
            {offer.session_id && onOpenChat && (
              <button type="button" onClick={() => onOpenChat(offer.session_id as string)} className={buttonClass("primary", "sm")}>
                Answer in chat
              </button>
            )}
            <button type="button" disabled={busy !== null} onClick={() => void doIt()} className={buttonClass("secondary", "sm")}>
              {busy === "do" ? "Starting…" : "Start again"}
            </button>
          </div>
        </div>
      )}

      {offer.status === "failed" && (
        <div className="mt-2 space-y-2" role="status">
          <p className="text-sm text-rose-700 dark:text-rose-300">{offer.outcome || "It didn't finish."}</p>
          <div className="flex flex-wrap gap-2">
            <button type="button" disabled={busy !== null} onClick={() => void doIt()} className={buttonClass("primary", "sm")}>
              {busy === "do" ? "Starting…" : "Try again"}
            </button>
            {seeHow}
          </div>
        </div>
      )}

      {offer.status === "" && (
        <>
          <div className="mt-auto flex flex-wrap items-center gap-2 pt-2">
            <button type="button" disabled={busy !== null} onClick={() => void doIt()} className={buttonClass("primary", "sm")}>
              {busy === "do" ? "Starting…" : "Do it"}
            </button>
            {offer.changed && (
              <button type="button" disabled={busy !== null} onClick={() => void undo()} className={buttonClass("ghost", "sm")}>
                Undo change
              </button>
            )}
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => setHiding((v) => !v)}
              aria-expanded={hiding}
              className="ml-auto text-sm text-fg-muted hover:text-fg"
            >
              Hide
            </button>
          </div>
          {hiding && (
            <div className="flex flex-wrap items-center gap-2 text-sm" role="group" aria-label="Why hide it?">
              <span className="text-fg-muted">Why?</span>
              {HIDE_REASONS.map((r) => (
                <button
                  key={r.reason}
                  type="button"
                  disabled={busy !== null}
                  onClick={() => void hide(r.reason)}
                  className={buttonClass("secondary", "sm")}
                >
                  {r.label}
                </button>
              ))}
            </div>
          )}
          <SayBox id={`take-on-${offer.key}`} disabled={busy !== null} say={say} />
        </>
      )}
      {error && (
        <p className="text-sm text-rose-600 dark:text-rose-300" role="status">
          {error}
        </p>
      )}
    </div>
  );
}

export function useOffers(onFinished?: () => void) {
  const [offers, setOffers] = useState<Offer[] | null>(null);
  const [limit, setLimit] = useState(3);
  const [making, setMaking] = useState(false);
  const [own, setOwn] = useState<string[]>([]);
  const since = useRef(0);
  const running = useRef<Set<string>>(new Set());
  const finished = useRef(onFinished);
  useEffect(() => {
    finished.current = onFinished;
  }, [onFinished]);

  const load = useCallback(async (signal?: AbortSignal) => {
    const list = await getOffers(signal).catch(() => null);
    if (list) {
      // A step that was working and has left the list is done: it now
      // shows in Done for you, so Home reads that again.
      const now = new Set(list.offers.filter((o) => o.status === "working").map((o) => o.key));
      const left = [...running.current].some((k) => !list.offers.some((o) => o.key === k));
      running.current = now;
      if (left) finished.current?.();
      setOffers(list.offers);
      setLimit(list.home_max);
      setMaking(list.making);
      setOwn(list.on_its_own);
    } else {
      setOffers((o) => o ?? []);
      setMaking(false);
    }
  }, []);

  useEffect(() => {
    const ctrl = new AbortController();
    void load(ctrl.signal);
    // Back on the page: read the list again, so one hidden or done on another
    // screen meanwhile isn't still shown here.
    const shown = () => {
      if (document.visibilityState === "visible") void load();
    };
    document.addEventListener("visibilitychange", shown);
    return () => {
      ctrl.abort();
      document.removeEventListener("visibilitychange", shown);
    };
  }, [load]);

  const working = anyWorking(offers ?? []) || making;
  useEffect(() => {
    if (!working) return;
    since.current = Date.now();
    const timer = setInterval(() => {
      if (Date.now() - since.current > POLL_MAX_MS) return;
      void load();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [working, load]);

  const replace = (o: Offer) => {
    if (o.status === "working") running.current.add(o.key);
    setOffers((list) => (list ?? []).map((x) => (x.key === o.key ? o : x)));
  };
  const gone = (key: string) => setOffers((list) => (list ?? []).filter((x) => x.key !== key));
  return { offers, limit, own, replace, gone, reload: load };
}

/** What it already does on its own, so the list never offers it. */
export function OnMyOwn({ own }: { own: string[] }) {
  if (own.length === 0) return null;
  return (
    <p className="mt-4 border-t border-line pt-3 text-sm text-fg-muted">
      <span className="font-semibold text-fg">Already on my own:</span> {ownLine(own)}{" "}
      <Link href={AS_ME_HREF} className="text-accent hover:underline">
        Take the lead
      </Link>
      {" · "}
      <Link href={AS_YOU_HREF} className="text-accent hover:underline">
        As you
      </Link>
    </p>
  );
}
