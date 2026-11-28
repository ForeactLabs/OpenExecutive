"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import TextsCard from "@/components/delegate/TextsCard";
import FoldList from "@/components/lists/FoldList";
import SettingsCard from "@/components/settings/SettingsCard";
import { PageSpinner } from "@/components/ui/Spinner";
import { getTexts, getTextThreads, TEXTS_CHANGED_EVENT, type TextThread, type TextThreads } from "@/lib/api";
import type { FoldEntry, FoldTone } from "@/lib/foldList";
import { groupOf } from "@/lib/textGroups";
import { remember, STORAGE_KEYS } from "@/lib/firstRun";

// Texts: the person's own texts the Executive read and sent for them,
// through Google Messages in their browser (delegation/texts.py). Shown like
// Chats (components/lists/FoldList.tsx): a text that didn't go, or may not
// have, stays open under Needs a look (lib/textGroups.ts); the rest fold by
// day or by person, one line per conversation that opens to the Executive's
// own line on what it did, and what was said. Nobody else's
// texts show here. "Text someone" hands the ask to the Executive in a new
// chat, as if typed there, so Approvals decide like any other send. The menu
// shows Texts only while it is on for this person (AppSidebar).

const RECHECK_MS = 15_000;
// The home page asks at most 2000 characters; leave room for "Text <who> (<number>): ".
const MAX_WORDS = 1600;
const MAX_WHO = 120;
// Where Texts is set later, with the rest of what the Executive does as you.
const SETTINGS_HREF = "/delegate#as-you";

const STATUS: Record<TextThread["status"], { words: string; tone: FoldTone }> = {
  sent: { words: "Sent as you", tone: "good" },
  sending: { words: "On its way", tone: "accent" },
  unsure: { words: "Not sure it went", tone: "warn" },
  failed: { words: "Didn't go", tone: "bad" },
  read: { words: "Read for you", tone: "plain" },
};

type TextEntry = FoldEntry & { thread: TextThread };

/** One conversation as a line of the list. */
function textEntry(thread: TextThread): TextEntry {
  const status = STATUS[thread.status] ?? STATUS.read;
  const last = thread.texts[thread.texts.length - 1];
  return {
    id: thread.id,
    who: thread.who,
    gist: thread.note || last?.text || "",
    at: thread.at,
    tone: status.tone,
    tag: status.words,
    needs: groupOf(thread.status) === "look",
    words: [thread.number, ...thread.texts.map((t) => t.text)].join("\n"),
    thread,
  };
}

function when(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

/** What a new chat is asked, as if the person typed it there. */
function textAsk(who: string, number: string, what: string): string {
  const to = [who.trim(), number.trim() ? `(${number.trim()})` : ""].filter(Boolean).join(" ");
  return `Text ${to}: ${what.trim()}`;
}

function TextSomeone() {
  const router = useRouter();
  const [who, setWho] = useState("");
  const [number, setNumber] = useState("");
  const [what, setWhat] = useState("");
  const ready = (who.trim() || number.trim()) && what.trim();

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!ready) return;
    // Asked in a new chat as if typed there (the home page reads it once).
    remember(STORAGE_KEYS.firstQuestion, textAsk(who, number, what));
    router.push("/");
  };

  const field = "w-full rounded-lg border border-line bg-surface px-3 py-2 text-[15px] text-fg placeholder:text-fg-muted";
  return (
    <SettingsCard>
      <form onSubmit={submit} className="space-y-3">
        <p className="text-[17px] font-semibold text-fg">Text someone</p>
        <div className="grid gap-3 sm:grid-cols-2">
          <input value={who} onChange={(e) => setWho(e.target.value)} placeholder="Who (e.g. Dana Ruiz)" aria-label="Who to text" maxLength={MAX_WHO} className={field} />
          <input value={number} onChange={(e) => setNumber(e.target.value)} placeholder="Number, if you have it" aria-label="Their number" inputMode="tel" maxLength={40} className={field} />
        </div>
        <textarea
          value={what}
          onChange={(e) => setWhat(e.target.value)}
          rows={2}
          placeholder="What to say, e.g. tell Dana I'm 10 minutes late and ask if 4:15 still works"
          aria-label="What to say"
          maxLength={MAX_WORDS}
          className={field}
        />
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-fg-muted">The Executive asks first if your Approvals say to.</p>
          <button
            type="submit"
            disabled={!ready}
            className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
          >
            Text
          </button>
        </div>
      </form>
    </SettingsCard>
  );
}

function ThreadDetail({ thread }: { thread: TextThread }) {
  const [open, setOpen] = useState(false);
  const showNumber = thread.number && thread.number !== thread.who;
  return (
    <div className="space-y-2">
      <p className="text-sm text-fg-muted">
        {showNumber ? `${thread.number} · ` : ""}
        {when(thread.at)}
        {thread.is_group ? " · Group" : ""}
      </p>
      {thread.note ? <p className="text-[15px] leading-relaxed text-fg">{thread.note}</p> : null}
      {thread.texts.length > 0 ? (
        <div>
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="text-sm font-medium text-accent hover:underline"
          >
            {open ? "Hide what was said" : "What was said"}
          </button>
          {open ? (
            <ol className="mt-2 space-y-1.5 rounded-xl bg-surface-elevated p-3 text-[15px]">
              {thread.texts.map((t, i) => (
                <li key={i} className="leading-relaxed">
                  <span className="font-semibold text-fg">{t.outgoing ? "You" : t.sender || thread.who}: </span>
                  <span className="text-fg-muted whitespace-pre-wrap">{t.text}</span>
                </li>
              ))}
            </ol>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export default function TextsPage() {
  const [data, setData] = useState<TextThreads | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  // Whether this company has Texts at all (an admin's switch), so the page
  // walks the person through setup only where there is something to set up.
  const [available, setAvailable] = useState<boolean | undefined>(undefined);
  const [flips, setFlips] = useState(0);

  useEffect(() => {
    const onFlip = () => setFlips((n) => n + 1);
    window.addEventListener(TEXTS_CHANGED_EVENT, onFlip);
    return () => window.removeEventListener(TEXTS_CHANGED_EVENT, onFlip);
  }, []);

  useEffect(() => {
    const ctrl = new AbortController();
    getTexts(ctrl.signal)
      .then((t) => setAvailable(Boolean(t?.available)))
      // A failed look isn't "off": the setup card says what went wrong itself.
      .catch(() => {
        if (!ctrl.signal.aborted) setAvailable(true);
      });
    return () => ctrl.abort();
  }, [flips]);

  useEffect(() => {
    const ctrl = new AbortController();
    const look = () =>
      getTextThreads(ctrl.signal).then(
        (next) => {
          setData(next);
          setError(null);
        },
        (e: unknown) => {
          if (ctrl.signal.aborted) return;
          setError(e instanceof Error ? e.message : "Couldn't load your texts.");
          setData((now) => (now === undefined ? null : now));
        },
      );
    void look();
    const timer = window.setInterval(() => void look(), RECHECK_MS);
    return () => {
      ctrl.abort();
      window.clearInterval(timer);
    };
  }, [flips]);

  const all = data?.conversations ?? [];

  return (
    <main className="flex-1 min-w-0 min-h-0 overflow-y-auto">
      <div className="max-w-3xl mx-auto px-4 sm:px-6 pt-6 sm:pt-8 pb-16 space-y-4">
        <div>
          <h1 className="text-2xl sm:text-3xl tracking-tight font-semibold">Texts</h1>
          <p className="mt-1 text-[15px] text-fg-muted">
            Texts your Executive read and sent for you from your own number, through Google Messages in Chrome.
            Only you see them, and they&apos;re kept for 30 days. Change how it works in{" "}
            <Link href={SETTINGS_HREF} className="text-accent hover:underline">
              Delegate → As you
            </Link>
            .
          </p>
        </div>
        {data === undefined || available === undefined ? (
          <PageSpinner label="Loading your texts" />
        ) : !data?.enabled && !available && !error ? (
          <SettingsCard>
            <p className="text-[15px] text-fg">Texts aren&apos;t turned on here yet.</p>
          </SettingsCard>
        ) : (
          <>
            {/* One card for setup, kept mounted as Texts turns on. */}
            {available ? <TextsCard setupOnly /> : null}
            {/* A failed first load still leaves the walkthrough above it. */}
            {error && !data?.enabled ? (
              <SettingsCard>
                <p className="text-[15px] text-rose-500">{error}</p>
              </SettingsCard>
            ) : null}
            {data?.enabled ? (
              <>
                <TextSomeone />
                {error ? <p className="text-sm text-rose-500">{error}</p> : null}
                {all.length === 0 ? (
                  <SettingsCard>
                    <p className="text-[15px] text-fg-muted">
                      No texts yet. New ones show here as they come in on your phone, with what the Executive did about each.
                    </p>
                  </SettingsCard>
                ) : (
                  <FoldList
                    items={all.map(textEntry)}
                    detail={(e) => <ThreadDetail thread={e.thread} />}
                    storageKey="oe.texts.groupBy"
                    searchLabel="Search texts"
                    needsTitle="Needs a look"
                  />
                )}
              </>
            ) : null}
          </>
        )}
      </div>
    </main>
  );
}
