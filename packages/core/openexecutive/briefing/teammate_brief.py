"""A teammate's own morning brief.

The owner's brief (``workflows.morning_brief``) reads the whole company. A
teammate's reads only what is theirs, the same slice their Home shows
(``today._viewer_slice``): the cards waiting on them, their departments'
goals at risk, what the Executive did for them, and what they asked of their
own brief in a chat. Nothing private to anyone else is read, and nothing of
theirs reaches anyone else: the grounding audit row is kept to them.

It is written only when something is new for them since their last one, and
it uses the same item memory as the owner's (``briefing.brief_items``), so a
card they were already told about is a closing "Still open" line, not news.
On a day with nothing new it sends nothing at all.

The scheduler sends one to every teammate right after the owner's morning
brief (``scheduler.runner._send_teammate_briefs``), each on their own chat
app, or by email when they have used the Executive before or chose email.
A teammate turns theirs off in Settings → Notifications (``morning_brief``).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from openexecutive.people.models import Person

logger = logging.getLogger(__name__)

# The brief_state / brief_items kind; one record per teammate.
KIND = "teammate_brief_morning"


def kind_for(person_id: int) -> str:
    """The per-person ``brief_state`` kind: since-window and fingerprint."""
    return f"{KIND}:{person_id}"


@dataclass
class TeammateBrief:
    text: str
    fingerprint: str
    items: dict[str, str] = field(default_factory=dict)


def gets_a_brief(person: Person) -> bool:
    """A teammate (not the owner, not a contact, not archived) who has not
    turned their morning brief off."""
    if person.id is None or person.is_principal or person.archived or person.kind != "team":
        return False
    from openexecutive.people.notifications import get_notification_settings

    return get_notification_settings(person.id).morning_brief


def chat_only(person: Person) -> bool:
    """Whether their brief goes only to a chat app: email only for someone
    who has used the Executive before or chose email, so a teammate who
    never has is not mailed a brief out of the blue."""
    return not (person.last_channel or person.preferred_channel == "email")


def _scoped_today(person_id: int) -> dict[str, Any] | None:
    """Their Home's slice: cards routed to them, their departments."""
    from openexecutive.api.routes import today as today_route
    from openexecutive.orchestrator.artifact_records import viewer_for_person

    response = today_route._build_today(include_private=False, viewer=viewer_for_person(person_id))
    viewer = today_route._viewer_for(response, person_id)
    if viewer is None:
        return None
    data = today_route._viewer_slice(response, viewer)
    data["_departments"] = list(viewer.department_slugs)
    return data


def _activity(person_id: int, since: datetime, departments: list[str]) -> list[dict[str, Any]]:
    """What the Executive did since their last brief, in their departments
    or for them, as their own Home lists it."""
    from openexecutive.api.routes import today as today_route

    items = [
        item.model_dump()
        for item in today_route._build_activity(
            20, since=since, include_alert_raised=False,
            exclude_workflows=today_route.RHYTHM_WORKFLOWS, viewer=person_id,
        ).items
    ]
    # Their departments' items, and what was done for them with no
    # department (the viewer filter already kept only theirs).
    depts = set(departments)
    return [a for a in items if a.get("department") is None or a.get("department") in depts]


async def write(person: Person, now: datetime | None = None) -> TeammateBrief | None:
    """Their brief, or None when there is nothing new for them (or nothing
    could be read). Raises only on a model failure, for the caller to log."""
    from openexecutive.briefing import brief_items, brief_state
    from openexecutive.briefing.brief_requests import render_parts
    from openexecutive.briefing.live_signals import LiveSignals
    from openexecutive.briefing.narrative import (
        QUIET_PRINCIPAL,
        STANDALONE_BRIEF_SYSTEM,
        render_briefing_context,
        synthesize_briefing_narrative,
    )
    from openexecutive.briefing.narrative_cache import local_today
    from openexecutive.briefing.reader import person_reader
    from openexecutive.memory.facts import render_facts_for_prompt

    assert person.id is not None
    pid = person.id
    now = now or datetime.now(UTC)
    kind = kind_for(pid)
    # Yesterday's brief kept for their quiet hours counts once it was sent.
    # One still waiting to go out is the one they get: a second written now
    # would repeat it and arrive right behind it.
    if await asyncio.to_thread(settle_kept, pid, now):
        return None
    since = brief_state.since_for(kind, now)
    data = await asyncio.to_thread(_scoped_today, pid)
    if data is None:
        return None
    departments = data.pop("_departments", [])
    activity = await asyncio.to_thread(_activity, pid, since, departments)
    handled = brief_state.handled_since(since, owned_by=pid)
    raise_block, _ = render_parts(pid)
    at_risk = [d for d in data["departments"] if d.get("at_risk_count") or d.get("off_track_count")]
    if not (data["proposals"] or at_risk or activity or handled or raise_block):
        return None  # nothing on their plate: no message

    fingerprint = brief_state.build_brief_fingerprint(
        today_data=data, activity=activity, handled=handled, since=since, mode="team",
        teammate_changes=raise_block,
    )
    previous = brief_state.last_delivered(kind)
    if previous is not None and previous.input_hash == fingerprint:
        return None  # nothing new since their last one: no message

    told = await asyncio.to_thread(brief_items.told, KIND, pid)
    reader = person_reader(pid)
    from openexecutive.workflows.morning_brief import _now_and_reader

    now_label, _ = _now_and_reader(now)
    facts = await asyncio.to_thread(render_facts_for_prompt, reader=pid)
    rendered = render_briefing_context(
        period_label=local_today(now), today_data=data, activity=activity,
        since=since, handled=handled, mode="team", live=LiveSignals(),
        live_window="since the last brief", now_label=now_label,
        standing_facts=facts, reader=reader, requests_of=pid, told=told,
    )
    text = await synthesize_briefing_narrative(
        today_data=data, activity=activity, period_label=local_today(now),
        standalone=True, since=since, handled=handled, mode="team",
        rendered_context=rendered, reader=reader,
    )
    if text.strip() == QUIET_PRINCIPAL:
        return None

    from openexecutive.alerts.card_refs import keep_app_links, name_cards_for
    from openexecutive.briefing.grounding import ground_brief

    text, _ = await ground_brief(
        text, context=rendered, kind=kind, system=STANDALONE_BRIEF_SYSTEM,
        # Not a surface the owner's "Left out of your brief" list reads: what
        # was held from a teammate's brief is theirs.
        surface="teammate brief", held_note=False, private_to_person=pid,
    )
    text = brief_items.drop_empty_lines(text)
    text = keep_app_links(await asyncio.to_thread(name_cards_for, text, pid))
    return TeammateBrief(
        text=text, fingerprint=fingerprint,
        items=brief_items.item_states(data, rendered, told, text),
    )


def record_delivered(person_id: int, brief: TeammateBrief, *, at: str | None = None) -> None:
    """After it reached them: the window, the fingerprint, what it told."""
    from openexecutive.briefing import brief_items, brief_state

    brief_state.record_delivered(kind_for(person_id), brief.fingerprint, brief.text, at=at)
    brief_items.record(KIND, brief.items, person_id)


def _pending_scope(person_id: int) -> str:
    return f"brief_pending:{KIND}:{person_id}"


def _read_kept(person_id: int) -> list[dict[str, Any]]:
    import json

    from openexecutive.briefing import narrative_cache

    row = narrative_cache.get(_pending_scope(person_id))
    if row is None:
        return []
    try:
        data = json.loads(row.narrative_text)
    except (TypeError, ValueError):
        return []
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


def _write_kept(person_id: int, entries: list[dict[str, Any]]) -> None:
    import json

    from openexecutive.briefing import narrative_cache

    narrative_cache.put(narrative_cache.BriefingNarrative(
        scope=_pending_scope(person_id), input_hash="",
        narrative_text=json.dumps(entries), generated_at=narrative_cache.utc_now_iso(),
    ))


def record_kept(person_id: int, brief: TeammateBrief, held_id: int) -> None:
    """A brief kept for their quiet hours (``people.held_messages``): told
    only once it is sent, which ``settle_kept`` checks on their next one.
    Each kept brief has its own entry, so a second one kept while the first
    still waits does not lose it."""
    from openexecutive.briefing import narrative_cache

    try:
        entries = [e for e in _read_kept(person_id) if e.get("held_id") != held_id]
        entries.append({
            "held_id": held_id, "text": brief.text,
            "fingerprint": brief.fingerprint, "items": brief.items,
            # Its window ends when it was written, not when it went out:
            # what happened while it waited is news for the next one.
            "written_at": narrative_cache.utc_now_iso(),
        })
        _write_kept(person_id, entries)
    except Exception:
        logger.exception("teammate_brief: couldn't keep person %s's brief", person_id)


# How long a kept brief still waiting holds back the next one. Past this
# its delivery is stuck (a failed chat app, a long leave) and a fresh brief
# is worth more than waiting behind a stale "Do today".
KEPT_FRESH_FOR = timedelta(hours=20)


def settle_kept(person_id: int, now: datetime | None = None) -> bool:
    """A kept brief that was sent since counts as delivered, as of when it
    was written (its window ends there); one that was dropped told nothing.
    Still waiting: left as it is.

    One sent after a newer brief already reached them (theirs waited while
    the next day's went straight out) does not take that newer brief's
    place: it only adds what it told that the newer one did not track, and
    leaves the window and fingerprint where the newer one set them.
    True while one written in the last ``KEPT_FRESH_FOR`` is still waiting to
    go out. Never raises."""
    from openexecutive.alerts.lifecycle import parse_aware
    from openexecutive.briefing import brief_items, brief_state
    from openexecutive.people.held_messages import status

    try:
        entries = _read_kept(person_id)
        if not entries:
            return False
        waiting: list[dict[str, Any]] = []
        unread: list[dict[str, Any]] = []
        sent: list[tuple[str, dict[str, Any]]] = []
        for entry in entries:
            try:
                state, sent_at = status(int(entry.get("held_id", 0)))
            except (TypeError, ValueError):
                continue
            if state == "waiting":
                waiting.append(entry)
            elif state == "unknown":
                # Couldn't read it: look again next time, and don't let a
                # read error cost them today's brief.
                unread.append(entry)
            elif state == "sent":
                sent.append((str(entry.get("written_at") or sent_at or ""), entry))
        kind = kind_for(person_id)
        for as_of, entry in sorted(sent, key=lambda t: t[0]):
            items = {str(k): str(v) for k, v in (entry.get("items") or {}).items()}
            last = brief_state.last_delivered(kind)
            last_at = parse_aware(last.generated_at) if last else None
            this_at = parse_aware(as_of)
            if last_at is not None and (this_at is None or last_at > this_at):
                brief_items.record(KIND, {**items, **brief_items.told(KIND, person_id)}, person_id)
                continue
            record_delivered(person_id, TeammateBrief(
                text=str(entry.get("text", "")), fingerprint=str(entry.get("fingerprint", "")),
                items=items,
            ), at=as_of or None)
        if len(waiting) + len(unread) != len(entries):
            _write_kept(person_id, waiting + unread)
        cutoff = (now or datetime.now(UTC)) - KEPT_FRESH_FOR
        return any(
            (at := parse_aware(str(e.get("written_at") or ""))) is not None and at > cutoff
            for e in waiting
        )
    except Exception:
        logger.exception("teammate_brief: couldn't settle person %s's kept brief", person_id)
        return False


__all__ = [
    "KIND",
    "TeammateBrief",
    "chat_only",
    "gets_a_brief",
    "kind_for",
    "record_delivered",
    "record_kept",
    "settle_kept",
    "write",
]
