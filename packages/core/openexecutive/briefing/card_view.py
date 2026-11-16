"""What a Needs you card shows: its words and its buttons.

The one place that decides it. Every screen that shows a card draws it from
:func:`card_view`, so they all show the same card. Each keeps its own
layout; none works out a card's words or buttons from its raw fields.

A button is one of a fixed list of actions (:data:`Action`), each done the
same way everywhere:

- ``approve``: approve the card with the plan it shows (``POST
  /alerts/{id}/approve``, or ``/decisions/{id}/approve`` for a booking).
- ``edit_approve``: edit the text first, then approve exactly that.
- ``dismiss``: Dismiss, or Got it on a card that's only to know.
- ``snooze``: hide it until a time picked from the app's snooze choices.
- ``rerun``: queue a failed run again (``run_id``).
- ``open``: an app page (``href``, a web path).
- ``open_url``: an outside https address (``href``).
- ``chat``: start a chat with ``prompt`` (``memory`` is the line it keeps).
- ``call``: ``POST href`` with no body, then the card says ``done``.

``place`` says where it sits: ``primary`` (the one main button), ``menu``
(the ⋯ menu), ``side`` (apart, on the right: Dismiss) or ``answer`` (a row of
answers in the card's body).

``panel`` names a card answered on its own panel, which the app draws whole:
``new_sender`` (who is this?), ``lead`` (Take the lead: Approve, Edit,
Decline), ``held`` (a teammate's send a department held), ``ask`` (a
question with an answer box).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, Field

Action = Literal[
    "approve", "edit_approve", "dismiss", "snooze", "rerun", "open", "open_url", "chat", "call",
]
Place = Literal["primary", "menu", "side", "answer"]
Tone = Literal["neutral", "amber", "sky", "rose", "emerald"]
Panel = Literal["", "new_sender", "lead", "held", "ask"]
Kind = Literal[
    "new_sender", "lead", "held", "ask", "rerun", "document", "signal", "decision", "proposal",
]

# Tags the card logic reads. Each names where it's set.
ARTIFACT_TAG = "artifact"  # orchestrator draft_artifact
# browser/handover.py: a "Waiting on you" card from the browser.
BROWSER_HANDOVER_TAG = "browser-handover"
# orchestrator.take_the_lead: a training card, and a held Take the lead action.
LEAD_TRAINING_TAG = "take_the_lead:training"
LEAD_ACTION_TAG = "decision_class:take_the_lead_action"
# orchestrator.department_gate: a teammate's send their department held.
HELD_ACTION_TAG = "decision_class:department_action"

# A card body longer than this is folded to two lines while the card is closed.
LONG_BODY_CHARS = 90
MEMORY_LINE_MAX_CHARS = 300

# What the card needs from its reader (Alert.needs), as its top chip says it.
NEEDS_CHIP = {
    "answer": "Needs your answer",
    "choice": "Pick one",
    "ok": "Needs your OK",
    "fyi": "For you to know",
}

ARTIFACT_BADGE = {"html": "Web page", "docx": "Word doc", "xlsx": "Spreadsheet", "link": "Link"}


class CardChip(BaseModel):
    label: str
    tone: Tone = "neutral"
    # Shown on hover, where there is one.
    title: str = ""


class CardButton(BaseModel):
    action: Action
    label: str
    place: Place
    href: str = ""
    run_id: str = ""
    prompt: str = ""
    memory: str = ""
    # ``call``: what the card says once it worked, and while it runs.
    done: str = ""
    busy_label: str = ""


class CardReview(BaseModel):
    """The review's one line: "Since you last looked", "Updated", "Likely stale"."""

    title: str
    text: str
    tone: Tone = "neutral"


class CardView(BaseModel):
    kind: Kind
    panel: Panel = ""
    # Who it's routed to, by full name, when it is.
    routed_to: str = ""
    # The top row: what it needs from you, then As me or where it came from.
    need: str = ""
    as_me: bool = False
    source: str = ""
    # The chip under the title: a deadline or "Likely stale".
    status: CardChip | None = None
    # The text under the title, and whether it folds while the card is closed.
    description: str = ""
    long: bool = False
    # What Approve does, and the box under it that changes it.
    plan_title: str = ""
    plan: str = ""
    plan_reply: bool = False
    # A watch's card: a reply steers the watch.
    steer: bool = False
    waiting_because: str = ""
    # Opened card.
    radar: str = ""
    review: CardReview | None = None
    chips: list[CardChip] = Field(default_factory=list)
    surfaced: str = ""
    tags: list[CardChip] = Field(default_factory=list)
    document: bool = False
    reviewed: str = ""
    buttons: list[CardButton] = Field(default_factory=list)


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _parse(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        at = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def age_label(iso: str | None, now: datetime | None = None) -> str:
    """"5m", "3h", "2d"; "" when unreadable."""
    at = _parse(iso)
    if at is None:
        return ""
    mins = max(0, round((_now(now) - at).total_seconds() / 60))
    if mins < 60:
        return f"{mins}m"
    hours = round(mins / 60)
    if hours < 48:
        return f"{hours}h"
    return f"{round(hours / 24)}d"


def days_until(iso: str | None, now: datetime | None = None) -> int | None:
    """Whole days to a deadline: 0 within the day, negative once past (floor,
    so an 11-hour-old deadline is -1, overdue, never due today)."""
    at = _parse(iso)
    if at is None:
        return None
    return int((at - _now(now)).total_seconds() // 86400)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def memory_line(action: str, subject: str) -> str:
    """The line a chat started from a card keeps of it."""
    s = _clip(" ".join(subject.split()), MEMORY_LINE_MAX_CHARS)
    return f'{action}.\n"{s}"' if s else f"{action}."


def safe_https(url: str | None) -> str:
    """An https address with no name or password in it, else ""."""
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        return ""
    return url


def _host(url: str) -> str:
    try:
        return urlsplit(url).hostname or url
    except ValueError:
        return url


def is_signal(item: Any) -> bool:
    """A signal from outside (the watch list, an app's new entries, a watched
    stock's move): for the reader to know."""
    return (
        item.category == "monitoring"
        or bool(item.surfaced_reason)
        or any(t.startswith("external:") for t in item.topic_tags or [])
    )


def status_chip(item: Any, now: datetime | None = None) -> CardChip | None:
    """The chip a closed card shows: its deadline, else "Likely stale"."""
    due = days_until(item.due_at, now)
    if due is not None:
        if due < 0:
            return CardChip(label="Overdue", tone="rose")
        return CardChip(label="Due today" if due == 0 else f"Due in {due}d", tone="amber")
    if item.review_verdict == "likely_stale":
        return CardChip(label="Likely stale", tone="amber")
    return None


def plan_title(changed: bool, changed_by: str | None = None) -> str:
    """The heading over what Approve does."""
    if not changed:
        return "If you approve"
    return f"If you approve · Updated by {changed_by}" if changed_by else "If you approve · Updated"


def _description(item: Any, artifact: bool, monitoring: bool) -> str:
    if artifact:
        return (item.suggested_action or "").strip()
    if monitoring:
        return ""
    body = (item.body or "").strip()
    headline = (item.headline or "").strip()
    if not body or body == headline:
        return ""
    if headline and body.startswith(headline):
        rest = body[len(headline):].lstrip(" \t\n.:;,—–-")
        return rest[:1].upper() + rest[1:]
    return body


def _lifecycle(item: Any, now: datetime | None) -> tuple[CardReview | None, list[CardChip], str]:
    verdict = item.review_verdict or ""
    stale = verdict == "likely_stale"
    review = None
    if item.review_note:
        title = "Likely stale" if stale else "Updated" if verdict == "changed" else "Since you last looked"
        review = CardReview(title=title, text=item.review_note, tone="amber" if stale else "neutral")
    chips: list[CardChip] = []
    if (item.occurrence_count or 1) > 1:
        seen = age_label(item.last_seen_at, now)
        chips.append(CardChip(
            label=f"seen ×{item.occurrence_count}",
            title=f"Last seen {seen} ago" if seen else "",
        ))
    due = days_until(item.due_at, now)
    if item.why_now or due is not None:
        when = "" if due is None else "overdue" if due < 0 else "due today" if due == 0 else f"due in {due}d"
        chips.append(CardChip(label=" · ".join(p for p in (item.why_now, when) if p), tone="amber"))
    if stale and not item.review_note:
        chips.append(CardChip(label="Likely stale", tone="amber"))
    if (item.superseded_count or 0) > 0:
        chips.append(CardChip(label=f"{item.superseded_count} folded in"))
    if verdict == "changed" and not item.review_note:
        chips.append(CardChip(label="Updated by the Executive", tone="sky"))
    if verdict == "drafted":
        chips.append(CardChip(label="Draft ready in your queue", tone="amber"))
    reviewed = ""
    if item.last_reviewed_at:
        reviewed = f"Reviewed {age_label(item.last_reviewed_at, now)} ago"
        if verdict and verdict != "likely_stale":
            reviewed += f" · {verdict}"
    return review, chips, reviewed


def card_view(
    item: Any,
    *,
    assignee_name: str = "",
    now: datetime | None = None,
) -> CardView:
    """What one card (a ``ProposalItem``) shows.

    ``assignee_name`` is the full name of the person it's routed to, when it
    is.
    """
    tags = list(item.topic_tags or [])
    decision = item.decision_instance_id is not None
    if item.roster_request is not None:
        return CardView(kind="new_sender", panel="new_sender", need="Who is this?")
    if getattr(item, "source", "") == "roster_request":
        # A new sender's card whose request was already answered: only
        # Dismiss. What a stranger wrote never seeds a chat through Approve.
        return CardView(kind="new_sender", need="Who is this?", buttons=[
            CardButton(action="dismiss", label="Dismiss", place="side"),
        ])
    if decision and (LEAD_TRAINING_TAG in tags or LEAD_ACTION_TAG in tags):
        return CardView(kind="lead", panel="lead", need="Needs your OK", as_me=True)
    if decision and HELD_ACTION_TAG in tags:
        return CardView(kind="held", panel="held", need="Needs your OK")

    artifact = ARTIFACT_TAG in tags
    monitoring = item.category == "monitoring"
    signal = is_signal(item)
    stale = item.review_verdict == "likely_stale"
    failed_run = item.failed_run_id or ""
    needs = item.needs or ""
    asks = needs in ("answer", "choice")
    no_approve = asks or needs == "fyi" or (signal and needs != "ok")
    got_it = needs == "fyi" or (signal and not needs)
    handed_over = BROWSER_HANDOVER_TAG in tags
    first = assignee_name.split(" ")[0] if assignee_name else ""

    def button(action: Action, label: str, **kw: Any) -> CardButton:
        return CardButton(action=action, label=label, place="menu", **kw)

    nudge = (
        button(
            "chat", f"Nudge {first or 'owner'}",
            prompt=(
                f"Nudge {assignee_name or 'the owner'} about this item — it has gone quiet: "
                f'"{_clip(" ".join(item.headline.split()), MEMORY_LINE_MAX_CHARS)}"'
                "\n\nSend a short, friendly check-in via message_person "
                "and tell me what you sent."
            ),
            memory=memory_line(f"Nudge {assignee_name or 'the owner'} about this item", item.headline),
        )
        if item.recommended_move == "nudge" and not artifact and not monitoring
        else None
    )
    workflow = (
        button("open", f"Run {item.suggested_workflow.replace('_', ' ')}", href=f"/jobs/{quote(item.suggested_workflow, safe='')}")
        if item.recommended_move == "suggest_workflow" and item.suggested_workflow
        and not artifact and not monitoring
        else None
    )
    rerun = button("rerun", "Run again", run_id=failed_run) if failed_run else None
    open_run = (
        button("open", "Open the run", href=f"/jobs/runs/{quote(failed_run, safe='')}") if failed_run else None
    )
    approve = (
        button("approve", "Done" if handed_over else "Approve")
        if not no_approve and not monitoring and not artifact and not failed_run
        else None
    )
    edit_approve = (
        button("edit_approve", "Edit & approve")
        if not handed_over and not no_approve and not monitoring and not artifact
        and not decision and not failed_run
        else None
    )
    dismiss = button("dismiss", "Got it" if got_it else "Dismiss")
    open_doc = (
        button("open", "Open document", href="/artifacts/" + quote(f"alert:{item.alert_id}", safe=""))
        if artifact
        else None
    )
    link = safe_https(item.artifact_url) if artifact and item.artifact_format == "link" else ""
    open_in_app = button("open_url", f"Open in app ({_host(link)})", href=link) if link else None
    person_page = (
        button("open", f"Open {first}'s page", href=f"/people/{item.routed_to_person_id}")
        if assignee_name and item.routed_to_person_id is not None
        else None
    )
    snooze = None if artifact else button("snooze", "Snooze")

    primary: CardButton | None
    if artifact:
        primary = open_doc
    elif asks:
        primary = None
    elif got_it:
        primary = dismiss
    elif monitoring:
        primary = None
    elif stale:
        primary = dismiss
    else:
        primary = rerun or nudge or workflow or approve

    buttons: list[CardButton] = []
    if primary is not None:
        buttons.append(primary.model_copy(update={"place": "primary"}))
    for b in (rerun, open_run, nudge, workflow, approve, edit_approve, snooze, open_in_app, person_page):
        if b is not None and b is not primary:
            buttons.append(b)
    if dismiss is not primary:
        buttons.append(dismiss.model_copy(update={"place": "side"}))

    # The top row: what it needs, then where it came from (As me when the
    # Executive is the one who'd do it).
    if artifact:
        need = ARTIFACT_BADGE.get(item.artifact_format or "", "Document")
    elif needs in NEEDS_CHIP:
        need = NEEDS_CHIP[needs]
    elif failed_run:
        need = "Didn't finish"
    elif got_it:
        need = "For you to know"
    elif approve:
        need = "Needs your OK"
    else:
        need = ""
    source = (
        "Workflow" if failed_run
        else "Watch list" if signal
        else ""
    )

    kind = _kind(item, artifact, failed_run, asks, decision)
    plan = item.suggested_action or ""
    show_plan = bool(plan) and not monitoring and not artifact
    review, chips, reviewed = _lifecycle(item, now)
    description = _description(item, artifact, monitoring)
    return CardView(
        kind=kind,
        panel="ask" if asks else "",
        routed_to=assignee_name if item.routed_to_person_id is not None else "",
        need=need,
        as_me=not source,
        source=source,
        status=None if monitoring else status_chip(item, now),
        description=description,
        long=len(description) > LONG_BODY_CHARS,
        plan_title=plan_title(bool(item.plan_changed), item.plan_changed_by) if show_plan else "",
        plan=plan if show_plan else "",
        plan_reply=approve is not None and bool(item.plan_editable) and not decision and not handed_over,
        steer=bool(item.watch_slug) and not asks,
        waiting_because=item.waiting_because if approve is not None else "",
        radar=item.body if monitoring and item.body and item.body != item.headline else "",
        review=review,
        chips=chips,
        surfaced=item.surfaced_reason or "",
        tags=[
            CardChip(label=t, tone="sky" if t.startswith("external:") else "neutral")
            for t in tags
            if t != ARTIFACT_TAG and (":" not in t or t.startswith("external:"))
        ],
        document=artifact,
        reviewed=reviewed,
        buttons=buttons,
    )


def _kind(
    item: Any, artifact: bool, failed_run: str, asks: bool, decision: bool,
) -> Kind:
    """What sort of card it is, for the lists that group cards by it."""
    if decision:
        return "decision"
    if failed_run:
        return "rerun"
    if artifact:
        return "document"
    if item.category == "monitoring":
        return "signal"
    if asks:
        return "ask"
    return "proposal"


def attach_views(
    items: list[Any],
    names: dict[int, str],
    *,
    now: datetime | None = None,
) -> None:
    """Set ``view`` on every card. ``names`` maps person id to full name."""
    for item in items:
        routed = item.routed_to_person_id
        item.view = card_view(
            item,
            assignee_name=names.get(routed, "") if routed is not None else "",
            now=now,
        )
