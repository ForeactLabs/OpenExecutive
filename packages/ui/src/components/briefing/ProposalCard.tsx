"use client";

import Link from "next/link";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import RosterRequestCard from "@/components/RosterRequestCard";
import Button, { buttonClass } from "@/components/ui/Button";
import OverflowMenu, { type OverflowItem } from "@/components/ui/OverflowMenu";
import {
  type CardButton,
  type PersonBriefItem,
  type PlanResult,
  type ProposalItem,
  type SteerResult,
  cardCall,
  undoCardPlan,
} from "@/lib/api";
import { type SnoozeChoice, snoozeChoices } from "@/lib/snooze";

import CardAsk from "./CardAsk";
import CardPlanReply from "./CardPlanReply";
import CardSteer from "./CardSteer";
import CardTopRow from "./CardTopRow";
import HeldActionCard from "./HeldActionCard";
import LeadTrainingCard from "./LeadTrainingCard";
import { Chip, TONE_TEXT, type ContinueHandler } from "./shared";

// One button as the card offers it: a press, or a link.
interface CardAction {
  label: string;
  onSelect?: () => void;
  href?: string;
  external?: boolean;
  danger?: boolean;
}

// A card in "Needs you" (and in the Across-the-team panel). What it says and
// which buttons it has come from the server (proposal.view,
// briefing/card_view.py), the same view every screen draws: this only lays
// it out. Closed, it shows the top row, the title, the text under it and its
// status chip, with the main button, the ⋯ menu and Dismiss apart on the
// right; a click opens it to show the review line, the chips, what approving
// does, the tags and when it was last reviewed.
export default function ProposalCard({
  proposal,
  people: _people,
  onContinue,
  onApprove,
  onDismiss,
  onSnooze,
  onRerun,
  onApproveWithEdits,
  onAnswer,
  onSteered,
  onSteerUndone,
  busy = false,
  defaultBodyExpanded = false,
  emphasized = false,
}: {
  proposal: ProposalItem;
  // Kept for the callers; the names on the card come with its view.
  people: PersonBriefItem[];
  onContinue?: ContinueHandler;
  onApprove?: (p: ProposalItem) => void;
  onDismiss?: (p: ProposalItem) => void;
  // Hide the card until a later time; it comes back on its own.
  onSnooze?: (p: ProposalItem, choice: SnoozeChoice) => void;
  // Queue a failed scheduled run again (a card's Run again button).
  onRerun?: (p: ProposalItem) => void;
  onApproveWithEdits?: (p: ProposalItem, editedBody: string) => void;
  // Answer a card that asks something; rejects with the reason when it
  // couldn't be sent.
  onAnswer?: (p: ProposalItem, answer: string) => Promise<void>;
  // A watch's card (watch_slug): the person replied to steer the watch.
  // The card stays to show what changed; the page drops the others it closed.
  onSteered?: (p: ProposalItem, result: SteerResult) => void;
  onSteerUndone?: (p: ProposalItem, result: SteerResult) => void;
  busy?: boolean;
  // Show the whole body while closed (no clamp) — the first card in the
  // queue, so its full text reads without a click.
  defaultBodyExpanded?: boolean;
  // The first card in the queue: an accent border so the single sharpest
  // item stands out.
  emphasized?: boolean;
}) {
  // Edit & approve: the body becomes a textarea pre-filled with the
  // proposal body, and Send approval sends exactly that text
  // (POST /alerts/{id}/approve).
  const [editing, setEditing] = useState(false);
  const [editedBody, setEditedBody] = useState("");
  const [expanded, setExpanded] = useState(false);
  // A reply on a watch's card closed it: its buttons go until Undo.
  const [steered, setSteered] = useState(false);
  // The plan after a reply on the card changed it (or Undo change put one
  // back), over the card as it was loaded (`from`): once the page reloads
  // the card with another plan, the card's own wins.
  const [planNow, setPlanNow] = useState<{ plan: string; changed: boolean; title: string; from: string } | null>(null);
  const [undoing, setUndoing] = useState(false);
  const [undoError, setUndoError] = useState<string | null>(null);
  // Bumped by Undo change, so the reply box drops what it last said.
  const [undos, setUndos] = useState(0);
  // An answer button (`call`) that's running, and what the card said after.
  const [calling, setCalling] = useState<string | null>(null);
  const [answered, setAnswered] = useState<string | null>(null);
  const [callError, setCallError] = useState<string | null>(null);
  const view = proposal.view;
  // Cards answered on their own card. A roster request is never answered
  // through chat, so what a stranger wrote never seeds a turn.
  if (view?.panel === "new_sender" && proposal.roster_request) {
    return (
      <RosterRequestCard
        request={proposal.roster_request}
        at={proposal.created_at}
        emphasized={emphasized}
        onResolved={() => onDismiss?.(proposal)}
      />
    );
  }
  if (view?.panel === "lead") {
    return <LeadTrainingCard proposal={proposal} emphasized={emphasized} onResolved={() => onDismiss?.(proposal)} />;
  }
  if (view?.panel === "held") {
    return <HeldActionCard proposal={proposal} emphasized={emphasized} onResolved={() => onDismiss?.(proposal)} />;
  }
  function startEditing() {
    setEditedBody(proposal.body || proposal.headline);
    setEditing(true);
  }
  function cancelEditing() {
    setEditing(false);
    setEditedBody("");
  }
  function submitEdit() {
    if (!onApproveWithEdits) return;
    const text = editedBody.trim();
    if (!text) return;
    onApproveWithEdits(proposal, text);
  }
  const loadedPlan = view?.plan ?? "";
  const local = planNow && planNow.from === loadedPlan ? planNow : null;
  const plan = local?.plan ?? loadedPlan;
  const planChanged = local?.changed ?? Boolean(proposal.plan_changed);
  const planTitle = local?.title ?? view?.plan_title ?? "";
  const description = view?.description ?? "";
  const clamp = Boolean(view?.long) && !expanded && !defaultBodyExpanded;
  const suggestedActionBlock = plan ? (
    <div
      className={`rounded-xl border px-3.5 py-2.5 ${
        planChanged ? "border-accent/70 bg-accent/10" : "border-accent/30 bg-accent/5"
      }`}
    >
      <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-accent">{planTitle}</div>
      <p className="text-sm leading-snug text-fg whitespace-pre-wrap break-words">{plan}</p>
    </div>
  ) : null;

  if (editing) {
    return (
      <div
        id={`alert-${proposal.alert_id}`}
        className="rounded-2xl border border-accent/50 bg-surface-elevated p-4 sm:p-5"
      >
        <div className="mb-3 flex items-start justify-between gap-2">
          <span className="text-xs font-semibold uppercase tracking-wide text-fg-muted">
            Editing proposal — sent word for word
          </span>
          {view?.routed_to && <span className="flex-shrink-0 text-sm text-accent">→ {view.routed_to}</span>}
        </div>
        {/* What the exec will do on approval, so the user sees what they're
            authorizing while editing the message body. */}
        {suggestedActionBlock && <div className="mb-3">{suggestedActionBlock}</div>}
        <textarea
          value={editedBody}
          onChange={(e) => setEditedBody(e.target.value)}
          rows={6}
          disabled={busy}
          aria-label="Proposal text to send"
          className="w-full rounded-xl border border-line bg-surface p-3 text-[15px] leading-snug text-fg whitespace-pre-wrap focus:outline-none focus:ring-2 focus:ring-accent/40 disabled:opacity-50"
          autoFocus
        />
        <div className="mt-3 flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={cancelEditing} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" onClick={submitEdit} disabled={busy || !editedBody.trim()}>
            Send approval
          </Button>
        </div>
      </div>
    );
  }

  async function undoPlan() {
    if (undoing) return;
    setUndoing(true);
    setUndoError(null);
    try {
      const r = await undoCardPlan(proposal.alert_id);
      setPlanNow({ plan: r.plan, changed: r.changed, title: r.plan_title, from: loadedPlan });
      setUndos((n) => n + 1);
    } catch (e) {
      setUndoError(e instanceof Error ? e.message : "Couldn't undo that. Try again.");
    } finally {
      setUndoing(false);
    }
  }

  async function call(b: CardButton) {
    setCalling(b.label);
    setCallError(null);
    try {
      await cardCall(b.href);
      setAnswered(b.done || "Done.");
    } catch (e) {
      setCallError(e instanceof Error ? e.message : "That didn't work.");
    } finally {
      setCalling(null);
    }
  }

  // Each button as this page carries it out; one whose handler this page
  // doesn't have isn't shown. Snooze is one item per time to pick.
  function actionsFor(b: CardButton): CardAction[] {
    switch (b.action) {
      case "approve":
        // The plan as this card shows it, which the server checks.
        return onApprove ? [{ label: b.label, onSelect: () => onApprove({ ...proposal, suggested_action: plan }) }] : [];
      case "edit_approve":
        return onApproveWithEdits ? [{ label: b.label, onSelect: startEditing }] : [];
      case "dismiss":
        return onDismiss ? [{ label: b.label, onSelect: () => onDismiss(proposal) }] : [];
      case "snooze":
        return onSnooze ? snoozeChoices().map((c) => ({ label: c.label, onSelect: () => onSnooze(proposal, c) })) : [];
      case "rerun":
        return onRerun ? [{ label: b.label, onSelect: () => onRerun(proposal) }] : [];
      case "open":
        return [{ label: b.label, href: b.href }];
      case "open_url":
        return [{ label: b.label, href: b.href, external: true }];
      case "chat":
        return onContinue ? [{ label: b.label, onSelect: () => onContinue(b.prompt, b.memory) }] : [];
      case "call":
        return [{ label: b.label, onSelect: () => void call(b) }];
    }
  }
  const buttons = view?.buttons ?? [];
  const primary = buttons.filter((b) => b.place === "primary").flatMap(actionsFor)[0] ?? null;
  const menu = buttons.filter((b) => b.place === "menu").flatMap(actionsFor);
  const sideDismiss = buttons.filter((b) => b.place === "side").flatMap(actionsFor)[0] ?? null;
  const answers = buttons.filter((b) => b.place === "answer");
  const showActions = Boolean(onApprove || onDismiss || onApproveWithEdits);
  const asks = view?.panel === "ask" && Boolean(onAnswer);
  const canReplan = Boolean(view?.plan_reply) && Boolean(onApprove);

  const review = view?.review ?? null;
  const chips = view?.chips ?? [];
  const tags = view?.tags ?? [];
  const radarBody = view?.radar ?? "";
  const hasDetails = Boolean(
    review ||
      chips.length > 0 ||
      (suggestedActionBlock && !canReplan) ||
      view?.surfaced ||
      tags.length > 0 ||
      view?.reviewed ||
      view?.document ||
      radarBody ||
      view?.long,
  );
  const flag = view?.status ?? null;
  const header = (
    <>
      <CardTopRow
        need={view?.need || undefined}
        mode={view?.as_me ? "take_the_lead" : undefined}
        source={view?.source || undefined}
        at={proposal.created_at}
        waitingOn={proposal.waiting_on}
      />
      <div
        className={`text-base sm:text-[17px] font-semibold leading-snug text-fg whitespace-pre-wrap break-words${
          expanded ? "" : " line-clamp-3"
        }`}
      >
        {proposal.headline}
      </div>
      {description && (
        <p
          className={`mt-1.5 text-[15px] leading-relaxed text-fg-muted whitespace-pre-wrap break-words${
            clamp ? " line-clamp-2" : ""
          }`}
          title={clamp ? description : undefined}
        >
          {description}
        </p>
      )}
      {(flag || hasDetails) && (
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-sm text-fg-muted">
          {flag && <Chip tone={flag.tone}>{flag.label}</Chip>}
          {hasDetails && (
            <span className="inline-flex items-center gap-1 text-fg-subtle">
              {expanded ? "Less" : "Details"}
              <span aria-hidden="true" className={`text-[10px] transition-transform ${expanded ? "rotate-90" : ""}`}>
                ▸
              </span>
            </span>
          )}
        </div>
      )}
    </>
  );

  return (
    <div
      id={`alert-${proposal.alert_id}`}
      className={`rounded-2xl border bg-surface-elevated p-4 sm:p-5 transition-colors ${
        emphasized ? "border-accent/60 ring-1 ring-accent/25 shadow-sm" : "border-line"
      }`}
    >
      {hasDetails ? (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          className="block w-full cursor-pointer rounded-lg text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
        >
          {header}
        </button>
      ) : (
        <div>{header}</div>
      )}

      {view?.waiting_because && (
        <p className="mt-2 text-sm leading-snug text-fg-muted break-words">
          <span className="font-semibold text-fg">Waiting because:</span> {view.waiting_because}
        </p>
      )}

      {canReplan && (
        <>
          {suggestedActionBlock && <div className="mt-3">{suggestedActionBlock}</div>}
          <CardPlanReply
            key={undos}
            alertId={proposal.alert_id}
            changed={planChanged}
            disabled={busy || undoing}
            onChanged={(r: PlanResult) => {
              setUndoError(null);
              setPlanNow({ plan: r.plan, changed: r.changed, title: r.plan_title, from: loadedPlan });
            }}
          />
        </>
      )}

      {expanded && (
        <div className="mt-3 space-y-2.5">
          {radarBody && (
            <div>
              <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-sky-700 dark:text-sky-300">
                Why this is on your radar
              </div>
              <p className="text-sm text-fg-muted whitespace-pre-wrap break-words">{radarBody}</p>
            </div>
          )}
          {review && (
            <p className={`text-sm leading-snug ${review.tone === "amber" ? TONE_TEXT.amber : "text-fg-muted"}`}>
              <span className="mr-1.5 text-xs font-semibold uppercase tracking-wide">{review.title}</span>
              {review.text}
            </p>
          )}
          {chips.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {chips.map((c) => (
                <Chip key={c.label} tone={c.tone} title={c.title || undefined}>
                  {c.label}
                </Chip>
              ))}
            </div>
          )}
          {!canReplan && suggestedActionBlock}
          {view?.surfaced && <p className="text-sm text-fg-muted leading-snug">{view.surfaced}</p>}
          {tags.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {tags.map((t) => (
                <Chip key={t.label} tone={t.tone}>
                  {t.label}
                </Chip>
              ))}
            </div>
          )}
          {view?.document && (
            // Full authored document, rendered as Markdown in a bounded,
            // scrollable region so a long brief can't blow out the card.
            <div className="max-h-80 overflow-y-auto rounded-xl border border-line bg-surface/40 px-3.5 py-2.5 prose prose-invert prose-sm max-w-none text-sm text-fg-muted leading-relaxed [&_*]:break-words">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{proposal.body || proposal.headline}</ReactMarkdown>
            </div>
          )}
          {view?.reviewed && <p className="text-xs text-fg-subtle">{view.reviewed}</p>}
        </div>
      )}

      {asks && onAnswer && (
        <CardAsk
          alertId={proposal.alert_id}
          question={proposal.question ?? ""}
          choices={proposal.needs === "choice" ? (proposal.choices ?? []) : []}
          onAnswer={(answer) => onAnswer(proposal, answer)}
        />
      )}

      {view?.steer && onDismiss && (
        <CardSteer
          alertId={proposal.alert_id}
          watchSlug={proposal.watch_slug ?? ""}
          onSteered={(r) => {
            setSteered(true);
            onSteered?.(proposal, r);
          }}
          onUndone={(r) => {
            setSteered(false);
            onSteerUndone?.(proposal, r);
          }}
        />
      )}

      {answers.length > 0 &&
        (answered ? (
          <p className="mt-3 text-sm text-fg-muted">{answered}</p>
        ) : (
          <div className="mt-3">
            <div className="flex flex-wrap gap-2" role="group" aria-label="Your answer">
              {answers.flatMap(actionsFor).map((a, i) =>
                a.href ? (
                  <Link key={a.label} href={a.href} className={buttonClass(i === 0 ? "primary" : "secondary", "sm")}>
                    {a.label}
                  </Link>
                ) : (
                  <button
                    key={a.label}
                    type="button"
                    disabled={calling != null}
                    onClick={a.onSelect}
                    className={buttonClass(i === 0 ? "primary" : "secondary", "sm")}
                  >
                    {calling === a.label ? answers.find((b) => b.label === a.label)?.busy_label || a.label : a.label}
                  </button>
                ),
              )}
            </div>
            {callError && (
              <p role="alert" className="mt-2 text-sm text-red-500">
                {callError}
              </p>
            )}
          </div>
        ))}

      {showActions && !steered && (
        <div className="mt-4 flex flex-wrap items-center gap-2">
          {primary &&
            (primary.href ? (
              primary.external ? (
                <a href={primary.href} target="_blank" rel="noopener noreferrer" className={buttonClass("primary", "md")}>
                  {primary.label}
                </a>
              ) : (
                <Link href={primary.href} className={buttonClass("primary", "md")}>
                  {primary.label}
                </Link>
              )
            ) : (
              <Button variant="primary" onClick={primary.onSelect} disabled={busy}>
                {primary.label}
              </Button>
            ))}
          {canReplan && planChanged && (
            <Button variant="ghost" onClick={() => void undoPlan()} disabled={busy || undoing}>
              {undoing ? "Undoing…" : "Undo change"}
            </Button>
          )}
          {menu.length > 0 && (
            <OverflowMenu
              label="More actions for this item"
              items={menu.map(
                (a): OverflowItem => ({
                  label: a.label,
                  onSelect: a.onSelect,
                  href: a.href,
                  external: a.external,
                  danger: a.danger,
                  disabled: busy && !a.href,
                }),
              )}
            />
          )}
          {sideDismiss && (
            // Dismiss is its own button, apart on the right, in the same
            // place on every card.
            <Button variant="ghost" className="ml-auto" onClick={sideDismiss.onSelect} disabled={busy}>
              {sideDismiss.label}
            </Button>
          )}
        </div>
      )}
      {undoError && (
        <p role="alert" className="mt-1 text-sm text-red-500">
          {undoError}
        </p>
      )}
    </div>
  );
}
