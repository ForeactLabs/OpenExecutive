"""What a brief already told its reader, item by item.

The brief's only memory used to be one hash of everything it would say
(``brief_state.build_brief_fingerprint``): any change anywhere and the whole
brief was written again, every open card retold in new words. So a card
waiting on the reader for two weeks was the Top call for two weeks.

This keeps, per brief and per reader, a short state for each item it named
(a card, a goal at risk, a person the Executive waits on, a dated item): the
facts that would make it news again (its headline, what it needs, its review
verdict, its due date), never a time. The next brief splits what is still
open into what changed (or was never told) and what it already told
unchanged, which goes on one closing "Still open" line. Recorded only after a
brief is delivered, next to the fingerprint (``scheduler.runner``).

Stored in ``briefing_narrative`` under its own scope, so it needs no table.
Never raises: on any failure the brief reads as if nothing was told before.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from openexecutive.alerts.lifecycle import parse_aware

logger = logging.getLogger(__name__)

SCOPE_PREFIX = "brief_items:"

# A dated item further out than this is told once, when it is new or moves,
# and then left to Home and the weekly view: a deadline a year away is not
# today's news.
HORIZON = timedelta(days=14)

# The items kept per brief: a bound, so a long backlog can't grow the row
# without limit.
_MAX_ITEMS = 300


def scope_for(kind: str, person_id: int | None = None) -> str:
    """The cache scope of ``kind``'s told items, per reader: the principal's
    brief has none, a teammate's carries their id."""
    base = f"{SCOPE_PREFIX}{kind}"
    return base if person_id is None else f"{base}:{person_id}"


def _digest(*parts: object) -> str:
    blob = json.dumps([str(p or "") for p in parts], ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def card_key(p: dict[str, Any]) -> str | None:
    alert_id = p.get("alert_id")
    return f"card:{alert_id}" if isinstance(alert_id, int) else None


def card_state(p: dict[str, Any]) -> str:
    """What would make a card news again: its text, what it needs, the
    review's verdict and note, why now, its due date. Never its age."""
    return _digest(
        p.get("headline"), p.get("suggested_action"), p.get("review_verdict"),
        p.get("review_note"), p.get("why_now"), str(p.get("due_at") or "")[:10],
        p.get("severity"),
    )


def goal_key(dept: dict[str, Any], goal: dict[str, Any]) -> str:
    return f"goal:{dept.get('slug', '')}:{_digest(goal.get('key_result'))}"


def goal_state(goal: dict[str, Any]) -> str:
    return _digest(goal.get("status"), goal.get("current"), goal.get("target"))


def dept_key(dept: dict[str, Any]) -> str:
    return f"dept:{dept.get('slug', '')}"


def dept_state(dept: dict[str, Any]) -> str:
    return _digest(dept.get("at_risk_count", 0), dept.get("off_track_count", 0))


def person_key(p: dict[str, Any]) -> str:
    return f"awaiting:{p.get('id', 0)}"


def person_state(p: dict[str, Any]) -> str:
    return _digest(p.get("awaiting_count", 0), p.get("soonest_sla_at"))


def due_key(d: dict[str, Any]) -> str:
    return f"due:{d.get('loop_id', 0)}"


def due_state(d: dict[str, Any]) -> str:
    return _digest(d.get("state"), d.get("due_date"), d.get("description"))


def item_states(today_data: dict[str, Any]) -> dict[str, str]:
    """Every item ``today_data`` names, keyed, with its state."""
    out: dict[str, str] = {}
    for p in today_data.get("proposals") or []:
        key = card_key(p)
        if key:
            out[key] = card_state(p)
    for d in today_data.get("departments") or []:
        if not (d.get("at_risk_count", 0) or d.get("off_track_count", 0)):
            continue
        out[dept_key(d)] = dept_state(d)
        for g in d.get("attention_goals") or []:
            out[goal_key(d, g)] = goal_state(g)
    for p in today_data.get("people") or []:
        if p.get("awaiting_count", 0):
            out[person_key(p)] = person_state(p)
    for d in today_data.get("due_soon") or []:
        out[due_key(d)] = due_state(d)
    if len(out) > _MAX_ITEMS:
        out = dict(list(out.items())[:_MAX_ITEMS])
    return out


def told(kind: str, person_id: int | None = None) -> dict[str, str]:
    """What the last delivered brief of ``kind`` told this reader, by item.
    Empty on a cold store or any failure."""
    from openexecutive.briefing import narrative_cache

    try:
        row = narrative_cache.get(scope_for(kind, person_id))
    except Exception:
        logger.exception("brief_items: read failed for %s", kind)
        return {}
    if row is None:
        return {}
    try:
        data = json.loads(row.narrative_text)
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def record(kind: str, states: dict[str, str], person_id: int | None = None) -> None:
    """Keep ``states`` as what this brief told its reader. Only after the
    brief was delivered: an unsent brief told nobody anything."""
    from openexecutive.briefing import narrative_cache

    try:
        narrative_cache.put(narrative_cache.BriefingNarrative(
            scope=scope_for(kind, person_id),
            input_hash="",
            narrative_text=json.dumps(states, sort_keys=True),
            generated_at=narrative_cache.utc_now_iso(),
        ))
    except Exception:
        logger.exception("brief_items: record failed for %s", kind)


def unchanged(key: str | None, state: str, told_items: dict[str, str] | None) -> bool:
    """Whether the reader was told this item before, exactly as it is now."""
    return bool(told_items) and key is not None and (told_items or {}).get(key) == state


def far_off(p: dict[str, Any], now: datetime) -> bool:
    """A card whose due date is past the horizon."""
    due = parse_aware(p.get("due_at"))
    return due is not None and due - now > HORIZON


# --------------------------------------------------------------------------- #
# Lines that say nothing
# --------------------------------------------------------------------------- #

# A line whose whole point is that a section is empty ("Nothing new in the
# inbox", "Nobody is waiting on you") or that talks about the brief itself
# (the digest, its own reply rates). The prompts forbid both; the model still
# wrote them in 7 of 15 briefs, so they go in code too. Matched on the line's
# text after any bullet or heading, start of line only, so a real item that
# happens to contain "nothing" stays.
_EMPTY_LINE = re.compile(
    r"^(?:"
    r"nothing\s+(?:else\s+|new\s+)?(?:is\s+)?(?:new|on\b|flagged|overdue|due|stuck|scheduled|"
    r"completed|changed|came|has\s+come|to\s+report|in\s+(?:the|your)\b|since\b)"
    r"|nothing\s+new\b"
    r"|no\s+(?:new\s+)?(?:calendar|meetings?|stuck|events?|inbox|mail|email|change|news|"
    r"development|update)s?\b.*\b(?:today|since|context|brief|overnight|in\s+this)\b"
    r"|no\s+new\s+development"
    r"|nobody\s+(?:is\s+)?(?:currently\s+)?(?:waiting|is\s+waiting)"
    r"|no\s*one\s+(?:is\s+)?(?:currently\s+)?waiting"
    r"|none\.?$"
    r"|quiet\s+(?:on\s+the\s+calendar|inbox|overnight)"
    r")",
    re.IGNORECASE,
)
_SELF_TALK = re.compile(
    r"(?:end[- ]of[- ]day\s+digest|conversation\s+log|research\s+dms?\b|"
    r"rather\s+than\s+sending\s+more|this\s+brief\b|my\s+(?:last|previous|morning)\s+brief|"
    r"top[- ]call\s+card|8:00\s*am\s+top\s+call)",
    re.IGNORECASE,
)
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
# "**Heading**", "**Heading:**", "**Heading** —" on a line of its own.
_HEADING = re.compile(r"^\s*\*\*[^*\n]{1,60}\*\*\s*:?\s*[—-]?\s*$")
# A heading with its text on the same line: "**Today** - Stuck: …".
_INLINE_HEADING = re.compile(r"^(\s*\*\*[^*\n]{1,60}\*\*\s*:?\s*[—-]?\s*)(.+)$")


def _says_nothing(text: str) -> bool:
    from openexecutive.briefing.narrative import NOTHING_NEW_BRIEF

    body = _BULLET.sub("", text).strip().lstrip("—-:").strip()
    if not body or body.startswith(NOTHING_NEW_BRIEF):
        return False
    if "](" in body:  # a line with a link points at something real
        return False
    return bool(_EMPTY_LINE.match(body) or _SELF_TALK.search(body))


def drop_empty_lines(text: str) -> str:
    """``text`` without lines that only say a section is empty or talk about
    the brief itself, and without a heading left with nothing under it.
    Text with nothing to drop comes back unchanged."""
    lines = text.splitlines()
    kept: list[str] = []
    changed = False
    for line in lines:
        inline = _INLINE_HEADING.match(line)
        if inline and not _HEADING.match(line) and _says_nothing(inline.group(2)):
            kept.append(inline.group(1).rstrip())
            changed = True
            continue
        if not _HEADING.match(line) and _says_nothing(line):
            changed = True
            continue
        kept.append(line)
    if not changed:
        return text
    # Drop a heading followed (before the next heading or the end) only by
    # blank lines.
    out: list[str] = []
    for i, line in enumerate(kept):
        if _HEADING.match(line):
            rest = kept[i + 1:]
            body = []
            for nxt in rest:
                if _HEADING.match(nxt) or _INLINE_HEADING.match(nxt):
                    break
                body.append(nxt)
            if not any(b.strip() for b in body):
                continue
        out.append(line)
    result = "\n".join(out)
    result = re.sub(r"\n{3,}", "\n\n", result).strip()
    return result or text


__all__ = [
    "HORIZON",
    "card_key",
    "card_state",
    "dept_key",
    "dept_state",
    "drop_empty_lines",
    "due_key",
    "due_state",
    "far_off",
    "goal_key",
    "goal_state",
    "item_states",
    "person_key",
    "person_state",
    "record",
    "scope_for",
    "told",
    "unchanged",
]
