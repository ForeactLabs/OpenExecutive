"use client";

// A long list shown the way Chats shows chats: a search box, By day or By
// person, what needs the person open at the top, then groups that fold with
// a count. Each item is one line (who, what it was about, its status, when)
// and opens in place to show the rest (`detail`). Used by Texts, and by any
// page with a long history of the same kind of thing. Grouping: lib/foldList.ts.

import { useMemo, useState, useSyncExternalStore, type ReactNode } from "react";

import Icon from "@/components/Icon";
import { foldGroups, matches, shortWhen, type FoldEntry, type FoldMode, type FoldTone } from "@/lib/foldList";

const DOT: Record<FoldTone, string> = {
  good: "bg-emerald-500",
  warn: "bg-amber-500",
  bad: "bg-rose-500",
  accent: "bg-accent",
  plain: "bg-line",
};

export const FOLD_TAG: Record<FoldTone, string> = {
  good: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  warn: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  bad: "bg-rose-500/15 text-rose-700 dark:text-rose-300",
  accent: "bg-accent/15 text-accent",
  plain: "bg-surface-hover text-fg-muted",
};

const CARD = "rounded-2xl border border-line bg-surface-elevated overflow-hidden divide-y divide-line";

// By day or by person, remembered per browser for each list (as Chats does);
// the server render is always By day.
const modeListeners = new Set<() => void>();
const chosen = new Map<string, FoldMode>();

function readMode(key: string): FoldMode {
  const kept = chosen.get(key);
  if (kept) return kept;
  try {
    return window.localStorage.getItem(key) === "person" ? "person" : "day";
  } catch {
    return "day";
  }
}

function saveMode(key: string, next: FoldMode): void {
  chosen.set(key, next);
  try {
    window.localStorage.setItem(key, next);
  } catch {
    // Private mode: the choice lasts this visit.
  }
  modeListeners.forEach((fn) => fn());
}

function subscribeMode(fn: () => void): () => void {
  modeListeners.add(fn);
  return () => modeListeners.delete(fn);
}

function Row<T extends FoldEntry>({
  item,
  open,
  onToggle,
  inDay,
  byPerson,
  detail,
}: {
  item: T;
  open: boolean;
  onToggle: () => void;
  inDay: boolean;
  byPerson: boolean;
  detail: (item: T) => ReactNode;
}) {
  const when = shortWhen(item.at, { inDay });
  // Inside a person's own group, the line starts with when instead of who.
  const lead = byPerson ? shortWhen(item.at) || item.who : item.who;
  return (
    <div>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="w-full flex flex-wrap sm:flex-nowrap items-center gap-x-2.5 gap-y-0.5 px-4 py-2.5 min-h-[44px] text-left hover:bg-surface-overlay transition-colors cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent/50"
      >
        <span className={`flex-shrink-0 h-2 w-2 rounded-full ${DOT[item.tone]}`} aria-hidden="true" />
        <span className="min-w-0 max-w-[70%] sm:max-w-[35%] truncate text-[15px] font-semibold text-fg">{lead}</span>
        <span className="order-last sm:order-none basis-full sm:basis-auto pl-[18px] sm:pl-0 flex-1 min-w-0 truncate text-sm text-fg-muted">
          {item.gist}
        </span>
        {item.tag ? (
          <span className={`hidden sm:inline flex-shrink-0 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-semibold ${FOLD_TAG[item.tone]}`}>
            {item.tag}
          </span>
        ) : null}
        {!byPerson || inDay ? (
          <span className="ml-auto sm:ml-0 flex-shrink-0 text-xs text-fg-subtle tabular-nums whitespace-nowrap">{when}</span>
        ) : null}
      </button>
      {open ? <div className="bg-surface px-4 pb-4 pt-2 sm:pl-[34px]">{detail(item)}</div> : null}
    </div>
  );
}

export default function FoldList<T extends FoldEntry>({
  items,
  detail,
  storageKey,
  personLabel = "By person",
  searchLabel = "Search",
  needsTitle = "Needs you",
  empty,
}: {
  items: T[];
  // What an item shows when it is opened.
  detail: (item: T) => ReactNode;
  // Where By day / By person is remembered for this list.
  storageKey: string;
  personLabel?: string;
  searchLabel?: string;
  needsTitle?: string;
  // Shown when a search finds nothing.
  empty?: string;
}) {
  const mode = useSyncExternalStore(subscribeMode, () => readMode(storageKey), () => "day" as const);
  const [query, setQuery] = useState("");
  // Groups the person opened or folded; the rest follow the default.
  const [folds, setFolds] = useState<Record<string, boolean>>({});
  const [openItems, setOpenItems] = useState<Set<string>>(() => new Set());

  const choose = (next: FoldMode) => {
    saveMode(storageKey, next);
    setFolds({});
  };

  const shown = useMemo(() => items.filter((i) => matches(i, query)), [items, query]);
  const { needs, groups } = useMemo(() => foldGroups(shown, mode), [shown, mode]);
  const searching = query.trim().length > 0;

  const toggleItem = (id: string) =>
    setOpenItems((now) => {
      const next = new Set(now);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  // Searching opens every group; otherwise the newest day is open, the rest folded.
  const isOpen = (key: string, index: number) =>
    searching || (key in folds ? folds[key] : mode === "day" ? index === 0 : false);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[12rem]">
          <Icon
            name="search"
            size="w-4 h-4"
            className="absolute left-3.5 top-1/2 -translate-y-1/2 text-fg-subtle pointer-events-none"
          />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={searchLabel}
            aria-label={searchLabel}
            className="w-full h-11 rounded-xl bg-surface-elevated border border-line pl-10 pr-3 text-[15px] text-fg placeholder:text-fg-subtle focus:outline-none focus:border-accent/60 focus:ring-2 focus:ring-accent/20"
          />
        </div>
        <div role="tablist" aria-label="Group by" className="flex-shrink-0 inline-flex items-center gap-1 p-1 rounded-full border border-line">
          {(["day", "person"] as const).map((m) => (
            <button
              key={m}
              type="button"
              role="tab"
              aria-selected={mode === m}
              onClick={() => choose(m)}
              className={`min-h-[30px] px-3.5 text-sm font-medium rounded-full transition cursor-pointer ${
                mode === m ? "bg-accent/10 text-accent" : "text-fg-muted hover:text-fg hover:bg-surface-overlay"
              }`}
            >
              {m === "day" ? "By day" : personLabel}
            </button>
          ))}
        </div>
      </div>

      {needs.length > 0 ? (
        <section aria-label={needsTitle}>
          <h2 className="px-1 mb-2 text-xs font-bold uppercase tracking-wider text-amber-700 dark:text-amber-300">
            {needsTitle}
          </h2>
          <div className={CARD}>
            {needs.map((item) => (
              <Row
                key={item.id}
                item={item}
                open={openItems.has(item.id)}
                onToggle={() => toggleItem(item.id)}
                inDay={false}
                byPerson={false}
                detail={detail}
              />
            ))}
          </div>
        </section>
      ) : null}

      {groups.map((group, index) => {
        const open = isOpen(group.key, index);
        const latest = group.items[0];
        const byPerson = mode === "person";
        return (
          <section key={group.key}>
            <button
              type="button"
              onClick={() => setFolds((f) => ({ ...f, [group.key]: !open }))}
              aria-expanded={open}
              className="w-full flex items-center gap-2 min-h-[44px] px-1 text-left text-fg-muted hover:text-fg transition-colors cursor-pointer"
            >
              <Icon
                name="chevron-right"
                size="w-3.5 h-3.5"
                className={`flex-shrink-0 transition-transform ${open ? "rotate-90" : ""}`}
              />
              <span className="truncate text-[15px] font-bold text-fg">{group.label}</span>
              <span className="flex-shrink-0 rounded-full bg-accent/10 px-2 text-xs font-bold leading-5 text-accent">
                {group.items.length}
              </span>
              {!open && latest ? (
                <span className="hidden sm:inline min-w-0 flex-1 truncate text-sm text-fg-subtle">
                  {byPerson ? latest.gist : `${latest.who}: ${latest.gist}`}
                </span>
              ) : null}
              {byPerson && latest ? (
                <span className="ml-auto pl-2 flex-shrink-0 text-xs text-fg-subtle">{shortWhen(latest.at)}</span>
              ) : null}
            </button>
            {open ? (
              <div className={`mt-1 ${CARD}`}>
                {group.items.map((item) => (
                  <Row
                    key={item.id}
                    item={item}
                    open={openItems.has(item.id)}
                    onToggle={() => toggleItem(item.id)}
                    inDay={!byPerson}
                    byPerson={byPerson}
                    detail={detail}
                  />
                ))}
              </div>
            ) : null}
          </section>
        );
      })}

      {shown.length === 0 && items.length > 0 ? (
        <p className="px-1 py-6 text-[15px] text-fg-muted">{empty ?? "Nothing matches."}</p>
      ) : null}
    </div>
  );
}
