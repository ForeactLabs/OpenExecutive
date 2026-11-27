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
    """What would make a card news again: its headline, what it needs, the
    review's verdict, its due date and severity. Never its age, and never
    the review's note or why-now: the alert review rewrites both in new
    words on every pass (one runs right before each brief), so hashing
    them would retell an unchanged card every morning."""
    return _digest(
        p.get("headline"), p.get("suggested_action"), p.get("review_verdict"),
        str(p.get("due_at") or "")[:10], p.get("severity"),
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


def _named(text: object, rendered: str | None, limit: int = 60) -> bool:
    """Whether ``text`` (its first ``limit`` characters, on one line) is in
    the context the brief was written from. Without a context, everything
    counts as named."""
    if rendered is None:
        return True
    from openexecutive.briefing.brief_state import quoted

    line = " ".join(str(text or "").split())
    if not line:
        return False
    return line[:limit] in rendered or quoted(line, limit) in rendered


_MENTION_STOP = frozenset({"with", "from", "that", "this", "your", "their", "about", "into", "over"})


def _mentioned(text: object, said: str | None) -> bool:
    """Whether the delivered brief ``said`` mentions ``text``: one of its
    first content words (two when it has two or more), since the brief
    words an item in its own way. Without ``said``, everything counts."""
    if said is None:
        return True
    words = [
        w for w in re.findall(r"[a-z][a-z'-]{3,}", str(text or "").lower())
        if w not in _MENTION_STOP
    ][:6]
    if not words:
        return False
    lowered = said.lower()
    hits = sum(1 for w in words if w in lowered)
    return hits >= min(2, len(words))


def item_states(
    today_data: dict[str, Any],
    rendered: str | None = None,
    told_items: dict[str, str] | None = None,
    said: str | None = None,
) -> dict[str, str]:
    """Every item ``today_data`` names, keyed, with its state.

    With ``rendered`` (the context the brief was written from), only what it
    showed the reader, plus what ``told_items`` already held unchanged: a
    card cut by a list's limit ("and 7 more on Home") was never told, so it
    stays news, and one told before and unchanged stays told even on a day
    its closing line was cut short. With ``said`` (the brief as delivered),
    an item the context showed but the brief left out (its word limit,
    grounding, a dropped line) is not told either."""

    def keep(key: str, state: str, label: object) -> bool:
        if (told_items or {}).get(key) == state:
            return True
        return _named(label, rendered) and _mentioned(label, said)

    out: dict[str, str] = {}
    for p in today_data.get("proposals") or []:
        key = card_key(p)
        if key:
            state = card_state(p)
            if keep(key, state, p.get("headline")):
                out[key] = state
    for d in today_data.get("departments") or []:
        if not (d.get("at_risk_count", 0) or d.get("off_track_count", 0)):
            continue
        goals = d.get("attention_goals") or []
        kept_goal = False
        for g in goals:
            key, state = goal_key(d, g), goal_state(g)
            if keep(key, state, g.get("key_result")):
                out[key] = state
                kept_goal = True
        key, state = dept_key(d), dept_state(d)
        if kept_goal or keep(key, state, d.get("title") or d.get("slug")):
            out[key] = state
    for p in today_data.get("people") or []:
        if p.get("awaiting_count", 0):
            key, state = person_key(p), person_state(p)
            if keep(key, state, p.get("full_name")):
                out[key] = state
    for d in today_data.get("due_soon") or []:
        key, state = due_key(d), due_state(d)
        if keep(key, state, d.get("description")):
            out[key] = state
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
    r"nothing\s+(?:else\s+|new\s+)?(?:is\s+)?(?:new|flagged|overdue|due|stuck|scheduled|"
    r"completed|changed|came|has\s+come|to\s+report|in\s+(?:the|your)\b|since\b|"
    r"on\s+(?:the\s+|your\s+|my\s+)?(?:calendar|plate|radar|side)\b[^,;]{0,25}$)"
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
# A capitalised word past the first ("Nothing new from Dana on the lease")
# names someone or something: the line is news, not an empty part.
_NAMES_SOMETHING = re.compile(r"\s[A-Z][a-z]")
_MORE_CLAUSES = re.compile(r";|,\s*(?:so|but|and|which|while|though)\b|\s[—–]\s|\.\s+\S")
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
    if _SELF_TALK.search(body):
        return True
    # A line that goes on past the empty part ("No change from Acme since
    # Friday; Dana still needs your sign-off") carries news: keep it.
    if _MORE_CLAUSES.search(body) or _NAMES_SOMETHING.search(body):
        return False
    return bool(_EMPTY_LINE.match(body))


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
