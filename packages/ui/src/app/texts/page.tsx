"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import SettingsCard from "@/components/settings/SettingsCard";
import { PageSpinner } from "@/components/ui/Spinner";
import { getTextThreads, type TextThread, type TextThreads } from "@/lib/api";
import { remember, STORAGE_KEYS } from "@/lib/firstRun";

// Texts: the person's own texts the Executive read and sent for them,
// through Google Messages in their browser (delegation/texts.py). Each
// conversation, latest first: who, how its latest text from them went, the
// Executive's own line on what it did, and what was said. Nobody else's
// texts show here. "Text someone" hands the ask to the Executive in a new
// chat, as if typed there, so Approvals decide like any other send. The menu
// shows Texts only while it is on for this person (AppSidebar).

const RECHECK_MS = 15_000;
const SETUP_HREF = "/delegate#as-you";

type Filter = "all" | "texted" | "read";

const STATUS: Record<TextThread["status"], { words: string; tone: string }> = {
  sent: { words: "Sent as you", tone: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300" },
  sending: { words: "On its way", tone: "bg-accent/15 text-accent" },
  unsure: { words: "Not sure it went", tone: "bg-amber-500/15 text-amber-700 dark:text-amber-300" },
  failed: { words: "Didn't go", tone: "bg-rose-500/15 text-rose-700 dark:text-rose-300" },
  read: { words: "Read for you", tone: "bg-surface-hover text-fg-muted" },
};

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
          <input value={who} onChange={(e) => setWho(e.target.value)} placeholder="Who (e.g. Dana Ruiz)" aria-label="Who to text" className={field} />
          <input value={number} onChange={(e) => setNumber(e.target.value)} placeholder="Number, if you have it" aria-label="Their number" inputMode="tel" className={field} />
        </div>
        <textarea
          value={what}
          onChange={(e) => setWhat(e.target.value)}
          rows={2}
          placeholder="What to say, e.g. tell Dana I'm 10 minutes late and ask if 4:15 still works"
          aria-label="What to say"
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

function ThreadItem({ thread }: { thread: TextThread }) {
  const [open, setOpen] = useState(false);
  const status = STATUS[thread.status] ?? STATUS.read;
  const showNumber = thread.number && thread.number !== thread.who;
  return (
    <li className="px-5 py-4">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
        <div className="min-w-0">
          <p className="text-[17px] font-semibold text-fg">
            {thread.who}
            {thread.is_group ? <span className="ml-2 text-sm font-normal text-fg-muted">Group</span> : null}
          </p>
          <p className="text-sm text-fg-muted">
            {showNumber ? `${thread.number} · ` : ""}
            {when(thread.at)}
          </p>
        </div>
        <span className={`whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-semibold ${status.tone}`}>
          {status.words}
        </span>
      </div>
      {thread.note ? <p className="mt-2 text-[15px] leading-relaxed text-fg">{thread.note}</p> : null}
      {thread.texts.length > 0 ? (
        <div className="mt-2">
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="text-sm font-medium text-accent hover:underline"
          >
            {open ? "Hide what was said" : "What was said"}
          </button>
          {open ? (
            <ol className="mt-2 space-y-1.5 rounded-xl bg-surface p-3 text-[15px]">
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
    </li>
  );
}

export default function TextsPage() {
  const [data, setData] = useState<TextThreads | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");

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
  }, []);

  const all = data?.conversations ?? [];
  const shown = all.filter((t) =>
    filter === "all" ? true : filter === "read" ? t.status === "read" : t.status !== "read",
  );
  const chip = (key: Filter, label: string) => (
    <button
      key={key}
      type="button"
      onClick={() => setFilter(key)}
      aria-pressed={filter === key}
      className={`rounded-full border px-3 py-1 text-sm ${
        filter === key ? "border-fg bg-fg text-fg-inverse" : "border-line text-fg-muted hover:text-fg"
      }`}
    >
      {label}
    </button>
  );

  return (
    <main className="flex-1 min-w-0 min-h-0 overflow-y-auto">
      <div className="max-w-3xl mx-auto px-4 sm:px-6 pt-6 sm:pt-8 pb-16 space-y-4">
        <div>
          <h1 className="text-2xl sm:text-3xl tracking-tight font-semibold">Texts</h1>
          <p className="mt-1 text-[15px] text-fg-muted">
            Texts your Executive read and sent for you from your own number, through Google Messages in Chrome.
            Only you see them.
          </p>
        </div>
        {data === undefined ? (
          <PageSpinner label="Loading your texts" />
        ) : !data?.enabled ? (
          <SettingsCard>
            <p className="text-[15px] text-fg">
              {error ?? "Texts are off for you."}{" "}
              <Link href={SETUP_HREF} className="font-medium text-accent hover:underline">
                Set up Texts
              </Link>
            </p>
          </SettingsCard>
        ) : (
          <>
            <TextSomeone />
            <div className="flex flex-wrap gap-2">
              {chip("all", "All")}
              {chip("texted", "Texted for you")}
              {chip("read", "Read for you")}
            </div>
            {error ? <p className="text-sm text-rose-500">{error}</p> : null}
            {shown.length === 0 ? (
              <SettingsCard>
                <p className="text-[15px] text-fg-muted">
                  {all.length === 0
                    ? "No texts yet. New ones show here as they come in on your phone, with what the Executive did about each."
                    : "Nothing here for this filter."}
                </p>
              </SettingsCard>
            ) : (
              <ul className="rounded-2xl border border-line bg-surface-elevated divide-y divide-line">
                {shown.map((t) => (
                  <ThreadItem key={t.id} thread={t} />
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </main>
  );
}
