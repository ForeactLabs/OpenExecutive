"use client";

// Delegate → As you → Texts: the Executive reads the person's own texts as
// they arrive and texts from their own number, through Google Messages in
// Chrome (delegation/texts.py, the browser extension's texts.js). Setup is
// three steps: the extension, the switch, Google Messages paired with their
// phone. Android only for now: on an iPhone, texts aren't reachable from
// Chrome.
import Link from "next/link";
import { useEffect, useRef, useState, type ReactNode } from "react";

import Switch from "@/components/Switch";
import { BROWSER_SETTINGS_HREF } from "@/components/browser/BrowserExtensionCard";
import SettingsCard from "@/components/settings/SettingsCard";
import { buttonClass } from "@/components/ui/Button";
import {
  getBrowserStatus,
  getTexts,
  TEXTS_CHANGED_EVENT,
  updateTexts,
  type BrowserStatus,
  type Texts,
} from "@/lib/api";

const GOOGLE_MESSAGES = "https://messages.google.com/web";
const RECHECK_MS = 5000;
// While Texts is on and Google Messages isn't paired yet, the person is
// likely scanning the code right now: look more often so the tick shows soon.
const PAIRING_RECHECK_MS = 2000;

type Phone = "android" | "iphone";

function Step({ done, n, title, children }: { done: boolean; n: number; title: string; children?: ReactNode }) {
  return (
    <li className="flex gap-3 py-3">
      <span
        aria-hidden
        className={`mt-0.5 flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full text-[13px] font-semibold ${
          done ? "bg-emerald-500 text-white" : "bg-zinc-200 text-fg dark:bg-zinc-700"
        }`}
      >
        {done ? "✓" : n}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-[15px] font-medium text-fg">
          {title}
          {done && <span className="sr-only"> (done)</span>}
        </p>
        {children && <div className="mt-1 text-[14px] text-fg-muted">{children}</div>}
      </div>
    </li>
  );
}

/** `setupOnly`: the Texts page's walkthrough, gone once Texts is set up
 *  (Delegate keeps the full card, switch and all, to come back to). */
export default function TextsCard({ setupOnly = false }: { setupOnly?: boolean } = {}) {
  const [state, setState] = useState<Texts | null>(null);
  const [browser, setBrowser] = useState<BrowserStatus | null>(null);
  const [phone, setPhone] = useState<Phone>("android");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Whether to look often: set from the last read, read by the timer.
  const waiting = useRef(false);
  // Each look and each save gets a number; an answer lands only if no newer
  // one has, so a look sent before a click never undoes it.
  const asked = useRef(0);
  const textsSeen = useRef(0);
  // No look while a save is out: it could read the row from before it.
  const saving = useRef(false);
  useEffect(() => {
    waiting.current = Boolean(state?.enabled && !state.paired && browser?.connected);
  }, [state, browser]);

  useEffect(() => {
    const ctl = new AbortController();
    // Looks may overlap (a slow one never stops the next), but an older
    // answer never lands after a newer one.
    let browserSeen = 0;
    const load = () => {
      if (saving.current) return;
      const look = ++asked.current;
      getTexts(ctl.signal)
        .then((s) => {
          if (look < textsSeen.current) return;
          textsSeen.current = look;
          setState(s);
          setError(null);
        })
        .catch((err: unknown) => {
          if (!ctl.signal.aborted && look >= textsSeen.current)
            setError(err instanceof Error ? err.message : "Couldn't load this setting.");
        });
      getBrowserStatus()
        .then((b) => {
          if (look < browserSeen) return;
          browserSeen = look;
          setBrowser(b);
        })
        .catch(() => undefined);
    };
    load();
    // Setup happens in other tabs (the store, Google Messages): keep up,
    // more often while pairing, and at once on coming back to this tab.
    let timer: ReturnType<typeof setTimeout>;
    const next = () => {
      timer = setTimeout(() => {
        // A hidden tab waits: coming back looks at once.
        if (document.visibilityState === "visible") load();
        next();
      }, waiting.current ? PAIRING_RECHECK_MS : RECHECK_MS);
    };
    next();
    const back = () => {
      if (document.visibilityState === "visible") load();
    };
    document.addEventListener("visibilitychange", back);
    return () => {
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", back);
      ctl.abort();
    };
  }, []);

  if (!state) return error ? <SettingsCard><p className="text-[15px] text-fg-muted">{error}</p></SettingsCard> : null;
  // Off for this install: nothing to set.
  if (!state.available) return null;

  const toggle = async (next: boolean) => {
    setBusy(true);
    // Any look already out started before this click: drop its answer.
    const save = ++asked.current;
    saving.current = true;
    try {
      const saved = await updateTexts(next);
      window.dispatchEvent(new Event(TEXTS_CHANGED_EVENT));
      if (save >= textsSeen.current) {
        textsSeen.current = save;
        setState(saved);
      }
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't save that.");
    } finally {
      saving.current = false;
      setBusy(false);
    }
  };

  const extension = Boolean(browser?.connected);
  const ready = state.enabled && extension && state.paired;
  if (setupOnly && ready) return null;

  return (
    <SettingsCard
      title={<span id="texts-title">{setupOnly ? "Set up Texts" : "Texts"}</span>}
      description="I read your texts as they arrive and can reply or text someone new, from your own number. Your Approvals decide what goes on its own. Only you see them."
      action={<Switch checked={state.enabled} disabled={busy} onChange={(next) => void toggle(next)} labelledBy="texts-title" />}
    >
      <div role="radiogroup" aria-label="Your phone" className="mt-4 inline-flex rounded-lg border border-line p-0.5">
        {(["android", "iphone"] as const).map((p) => (
          <button
            key={p}
            type="button"
            role="radio"
            aria-checked={phone === p}
            onClick={() => setPhone(p)}
            className={`rounded-md px-3 py-1.5 text-[14px] ${phone === p ? "bg-zinc-200 font-medium text-fg dark:bg-zinc-700" : "text-fg-muted"}`}
          >
            {p === "android" ? "Android" : "iPhone"}
          </button>
        ))}
      </div>

      {phone === "iphone" ? (
        <p className="mt-4 text-[14px] text-fg-muted">
          Texts on an iPhone are coming soon. They need a small app on your Mac, since an iPhone&apos;s texts can&apos;t be
          reached from Chrome.
        </p>
      ) : ready ? (
        <p className="mt-4 text-[14px] text-fg-muted">
          On, through Google Messages in Chrome
          {state.conversations > 0 ? `, ${state.conversations} conversation${state.conversations === 1 ? "" : "s"} so far` : ""}.
          Keep Chrome open for new texts.
        </p>
      ) : (
        <ol className="mt-3 divide-y divide-line">
          <Step done={extension} n={1} title="Open Executive for Chrome">
            {extension ? (
              "On in this browser."
            ) : browser?.store_url ? (
              <a href={browser.store_url} target="_blank" rel="noreferrer" className={`${buttonClass("primary", "sm")} mt-1`}>
                Add to Chrome
              </a>
            ) : (
              <Link href={BROWSER_SETTINGS_HREF} className="underline">
                Set it up in Settings → Browser
              </Link>
            )}
          </Step>
          <Step done={state.enabled} n={2} title="Turn on Texts">
            {state.enabled ? "On." : "With the switch above."}
          </Step>
          <Step done={state.paired} n={3} title="Pair Google Messages with your phone">
            {state.paired ? (
              "Paired."
            ) : (
              <>
                <a href={GOOGLE_MESSAGES} target="_blank" rel="noreferrer" className="underline">
                  Open Google Messages
                </a>{" "}
                in Chrome. On your phone: Messages › your picture › Device pairing › QR code scanner.
                {state.enabled && extension && (
                  <span role="status" className="mt-2 flex items-center gap-2 text-fg">
                    <span
                      aria-hidden
                      className="h-3.5 w-3.5 flex-shrink-0 animate-spin rounded-full border-2 border-zinc-300 border-t-accent"
                    />
                    Waiting for your phone. After you scan the code, this ticks by itself once Chrome sees it
                    paired. Keep the Google Messages tab open.
                  </span>
                )}
              </>
            )}
          </Step>
        </ol>
      )}
      <p className="mt-4 text-[13px] text-fg-muted">
        Texts go from your phone, so it needs to be on and online, and Chrome open on your computer.
      </p>
      {error && <p className="mt-2 text-[14px] text-red-500">{error}</p>}
    </SettingsCard>
  );
}
