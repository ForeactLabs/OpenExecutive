"use client";

// The part a held step's card shares (Take the lead, a teammate's held send):
// the options (the first is the Executive's pick), what was said on the card,
// "Or tell me…" and Skip. What the person says goes to POST
// /decisions/{id}/say, which rewrites the step's fields from it (Approve then
// sends them as the edit) and, on a go, approves it the same as a tap.

import { useState } from "react";

import { type LeadCardField, type StepSayResult, sayOnStep } from "@/lib/api";
import { buttonClass } from "@/components/ui/Button";

import { SayBox, Turns } from "./CardTalk";

// The key Skip runs under (an option's key is its label).
const SKIP = "\u0000skip";
// How long "Done" shows on the button before the card goes.
const DONE_SHOWN_MS = 1200;

export interface StepOption {
  label: string;
  // What it does beyond the step itself ("From now on: …").
  detail?: string;
  run: () => Promise<unknown>;
}

export default function StepTalk({
  decisionId,
  options,
  seen,
  turns: saved = [],
  canSay,
  disabled = false,
  onSaid,
  onResolved,
  onSkip,
}: {
  decisionId: number;
  options: StepOption[];
  // The step's fields as the card shows them, so a go is judged against them.
  seen: Record<string, string>;
  turns?: { who: "you" | "me"; text: string }[];
  canSay: boolean;
  disabled?: boolean;
  // Words were taken: the step's fields as they read now.
  onSaid: (fields: LeadCardField[]) => void;
  onResolved: () => void;
  onSkip: () => Promise<unknown>;
}) {
  const [turns, setTurns] = useState(saved);
  const [sending, setSending] = useState<string | null>(null);
  // What was pressed and is running (an option's label, or "skip"), so that
  // button says it's working instead of only greying out: a text can take
  // up to half a minute to go.
  const [running, setRunning] = useState<string | null>(null);
  // It went: the card says so for a moment before it goes.
  const [done, setDone] = useState(false);
  const busy = running !== null;
  const [error, setError] = useState<string | null>(null);
  // A go that was tried and failed: what happened stays on the card until
  // they've read it.
  const [tried, setTried] = useState(false);

  async function run(key: string, action: () => Promise<unknown>) {
    setRunning(key);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong. Try again.");
      setRunning(null);
      return;
    }
    if (key === SKIP) {
      onResolved();
      return;
    }
    setDone(true);
    setTimeout(onResolved, DONE_SHOWN_MS);
  }

  const off = disabled || busy || sending !== null || tried;

  return (
    <div>
      <Turns turns={turns} sending={sending} />
      {options.length > 0 && (
        <div className="mt-3 flex flex-col gap-2" role="group" aria-label="Options">
          {options.map((o, i) => (
            <button
              key={o.label}
              type="button"
              disabled={off}
              onClick={() => void run(o.label, o.run)}
              aria-busy={running === o.label}
              className={`${buttonClass(i === 0 ? "primary" : "secondary", "md")} h-auto w-full flex-col items-start justify-start py-2 text-left ${running === o.label ? "disabled:cursor-wait disabled:opacity-100" : ""}`}
            >
              {running === o.label ? (
                <span className="inline-flex items-center gap-2">
                  {done ? (
                    <span aria-hidden="true">✓</span>
                  ) : (
                    <span aria-hidden="true" className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-current border-r-transparent" />
                  )}
                  <span>{done ? "Done" : "Working on it…"}</span>
                </span>
              ) : (
                <span>{o.label}</span>
              )}
              {o.detail && <span className="mt-0.5 break-words text-sm font-normal opacity-80">{o.detail}</span>}
            </button>
          ))}
        </div>
      )}
      {tried && (
        <div className="mt-3">
          <button type="button" onClick={onResolved} className={buttonClass("secondary", "md")}>
            Got it
          </button>
        </div>
      )}
      {canSay && !tried && (
        <SayBox
          id={`step-${decisionId}`}
          disabled={off}
          onSending={setSending}
          onFailed={() => setSending(null)}
          say={async (text) => {
            const r: StepSayResult = await sayOnStep(decisionId, text, seen);
            setTurns((t) => [...t, { who: "you", text }, { who: "me", text: r.reply }]);
            setSending(null);
            if (r.approved?.status === "failed") setTried(true);
            else if (r.approved) onResolved();
            else onSaid(r.fields);
          }}
        />
      )}
      <div className="mt-2 flex justify-end">
        <button
          type="button"
          disabled={off}
          onClick={() => void run(SKIP, onSkip)}
          aria-busy={running === SKIP}
          className="text-sm text-fg-muted hover:text-fg disabled:opacity-50"
        >
          {running === SKIP ? "Skipping…" : "Skip"}
        </button>
      </div>
      <p aria-live="polite" className="sr-only">{done ? "Done" : busy ? "Working on it" : ""}</p>
      {error && <p role="alert" className="mt-2 text-sm text-red-500">{error}</p>}
    </div>
  );
}
