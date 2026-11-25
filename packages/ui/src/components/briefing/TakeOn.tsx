"use client";

// Home's "I can also take on" (core briefing/take_on.py): a few real steps
// the Executive offers to take next (TakeOnOffer.tsx), and See all for the
// rest. With nothing in it, Home leaves the section out; See all says so.

import Link from "next/link";
import { useEffect } from "react";

import { takeOnView } from "@/lib/takeOnApi";

import { OfferCard, OnMyOwn, useOffers } from "./TakeOnOffer";

export const TAKE_ON_ID = "take-on";
const NOTHING_YET = "Nothing new to take on right now. I'll add more as things come in.";

export default function TakeOn({
  onOpenChat,
  onFinished,
  onCount,
  all = false,
}: {
  onOpenChat?: (sessionId: string) => void;
  /** A step finished and left the list for Done for you. */
  onFinished?: () => void;
  /** See all: every offer, not just Home's few. */
  all?: boolean;
  /** How many it shows. */
  onCount?: (n: number | null) => void;
}) {
  const { offers: loaded, limit, own, replace, gone } = useOffers(onFinished);
  const offers = loaded ?? [];
  const shown = all ? offers : offers.slice(0, limit);
  const view = takeOnView(shown.length, all);
  const count = loaded === null ? null : shown.length;
  useEffect(() => {
    onCount?.(count);
  }, [count, onCount]);
  if (loaded === null || view === "hidden") return null;
  if (view === "nothing") {
    return (
      <>
        <p className="text-[15px] text-fg-muted">{NOTHING_YET}</p>
        <OnMyOwn own={own} />
      </>
    );
  }
  const cards = (
    <div className="grid gap-3">
      {shown.map((o) => (
        <OfferCard key={o.key} offer={o} onChanged={replace} onGone={gone} onOpenChat={onOpenChat} />
      ))}
    </div>
  );
  if (all) {
    return (
      <>
        {cards}
        <OnMyOwn own={own} />
      </>
    );
  }
  return (
    <section id={TAKE_ON_ID} aria-labelledby="sec-take-on" className="mb-8 scroll-mt-4">
      <div className="mb-3 flex items-baseline gap-3">
        <h2 id="sec-take-on" className="text-lg font-semibold text-fg">
          I can also take on
        </h2>
        {offers.length > shown.length && (
          <Link href="/take-on" className="ml-auto text-sm font-medium text-accent hover:underline">
            See all ({offers.length})
          </Link>
        )}
      </div>
      {cards}
      <OnMyOwn own={own} />
    </section>
  );
}
