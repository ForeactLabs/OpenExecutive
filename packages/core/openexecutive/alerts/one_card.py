"""One card per topic: the one check every card goes through.

``alerts.store.insert_alert`` calls :func:`place` for every card it writes.
The cards that take part are the ones a turn makes for someone to read: a
held step's Approve card, and a card a turn raised (``create_alert``,
through the alerts pipeline). Cards a feature writes with buttons of their
own (a meeting's, a passed-on message, a sign-in hand-over, a phishing
warning, a document) keep to themselves, and so does a card whose approval
carries out its own text (needs 'ok'). The check notes what the card is
about, then joins it with an open card on the same topic:

* the same reader (who the card is for, and whether it is private), and
* one of: written on the same turn; about the same mail thread with the
  same person, unless the model said it is another matter; or the model
  said it is the same matter (``create_alert``'s ``joins_card``).

How two cards on one topic become one:

* An Approve card wins: a card that only tells or asks joins it, its text
  added last ("Then: ..."), so one approval answers the whole topic. What
  approving does is still only the held step.
* Two Approve cards stay two (each is its own yes), except a newer mail send
  to the same people on the same thread, which replaces the one waiting:
  the older decision closes and its card goes.
* Two other cards: the newer joins the older, and a question it asks
  becomes the card's question when the older one asked none.
* The card that stays takes the graver severity of the two, and a card that
  joined is never sent on its own (``dispatcher.dispatch_all``).

The same person on a different matter (another turn, another thread, or the
model said so) keeps a card of its own. A reply ("Re: ...") from the same
person to a subject they wrote before is the same thread, even where the
mailbox filed it as a new one (:func:`note_mail`).

What the check reads about the turn comes from the turn itself (the audit
turn id, the session's sender or speaker, the mail thread). Never raises: a
second card is the worst case.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import sqlite3
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_OPEN = ("unread", "read")
# Where a held step's Approve card comes from (``decision_ledger``).
_APPROVE_SOURCE = "decision_scheduling"
_THEN = "\n\nThen: "

_CREATE = """
CREATE TABLE IF NOT EXISTS card_topics (
    alert_id INTEGER PRIMARY KEY,
    reader TEXT NOT NULL,
    person TEXT NOT NULL DEFAULT '',
    turn TEXT NOT NULL DEFAULT '',
    thread TEXT NOT NULL DEFAULT '',
    step TEXT NOT NULL DEFAULT '',
    decision_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_card_topics_reader ON card_topics(reader, created_at);
CREATE TABLE IF NOT EXISTS mail_conversations (
    thread_id TEXT PRIMARY KEY,
    sender TEXT NOT NULL,
    subject TEXT NOT NULL,
    conversation TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mail_conversations_sender ON mail_conversations(sender, subject);
"""

# What the code writing a card knows that the turn doesn't: the thread a held
# mail send is on, the step it holds, and the card the model said it joins.
_given: ContextVar[dict[str, Any] | None] = ContextVar("one_card_given", default=None)


@contextlib.contextmanager
def about(
    *, thread: str = "", step: str = "", joins: int | None = None, raised: bool = False,
) -> Iterator[None]:
    """Cards written inside the block take part in the check: a held step's
    Approve card (about ``thread``, holding ``step``), or a card a turn
    raised (``raised``; ``joins`` is the open card the model said it is the
    same matter as, 0 when it said it is another one)."""
    token = _given.set({"thread": thread, "step": step, "joins": joins, "raised": raised})
    try:
        yield
    finally:
        _given.reset(token)


@dataclass(frozen=True)
class _Card:
    id: int
    source: str
    status: str
    headline: str
    body: str
    needs: str
    reader: str
    decision_id: int | None
    step: str
    thread: str
    person: str
    turn: str
    created_at: str

    @property
    def approve(self) -> bool:
        return self.decision_id is not None


# --------------------------------------------------------------------------- #
# What the turn says the card is about
# --------------------------------------------------------------------------- #


def _session() -> Any:
    try:
        from openexecutive.orchestrator.schedule_tools import current_session

        return current_session.get()
    except Exception:  # noqa: BLE001 - no turn
        return None


def person_key(session: Any = None) -> str:
    """Who the turn is with: the mail's real sender, the person speaking, the
    department at work, or the teammate an unattended run is for. Empty when
    the turn is no one's (a scheduled job)."""
    session = session if session is not None else _session()
    if session is None:
        return ""
    sender = str(getattr(session, "email_from", "") or "").strip().lower()
    if getattr(session, "origin_channel", "") == "email" and sender:
        return f"mail:{sender}"
    speaker = getattr(session, "caller_person_id", None)
    if isinstance(speaker, int) and not isinstance(speaker, bool):
        return f"person:{speaker}"
    try:
        from openexecutive.tenancy import department_work

        slug = department_work.active()
    except Exception:  # noqa: BLE001 - departments at work are optional
        slug = None
    if slug:
        return f"department:{slug}"
    runner = getattr(session, "run_for_person_id", None)
    if isinstance(runner, int) and not isinstance(runner, bool):
        return f"person:{runner}"
    return ""


def mail_thread(thread_id: str) -> str:
    """The topic of one mail thread."""
    thread_id = str(thread_id or "").strip()
    return f"mail:{thread_id}" if thread_id else ""


def _subject_key(subject: str) -> str:
    return " ".join(_RE_PREFIX.sub("", str(subject or "")).split()).lower()


# A reply, not a forward: a forward carries someone else's mail.
_REPLY = re.compile(r"^\s*(re|aw)\s*:", re.IGNORECASE)
# How long a mail thread is remembered for joining a follow-up to it.
_CONVERSATION_DAYS = 90


def _earlier(conn: sqlite3.Connection, thread_id: str, sender: str, subject: str) -> str:
    """The conversation a new thread ``thread_id`` from ``sender`` would join:
    their latest one on this subject, when ``subject`` is a reply."""
    key = _subject_key(subject)
    if not key or not _REPLY.match(str(subject or "")):
        return ""
    if conn.execute("SELECT 1 FROM mail_conversations WHERE thread_id = ?", (thread_id,)).fetchone():
        return ""
    row = conn.execute(
        "SELECT conversation FROM mail_conversations WHERE sender = ? AND subject = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (sender, key),
    ).fetchone()
    return str(row["conversation"]) if row is not None else ""


def continues(thread_id: str, sender: str, subject: str, *, db_path: Path | None = None) -> bool:
    """Whether mail on the new thread ``thread_id`` would join an earlier
    conversation of ``sender``'s (:func:`note_mail`): the caller proves the
    sender first, since anyone can write a From: line. Never raises."""
    thread_id, sender = str(thread_id or "").strip(), str(sender or "").strip().lower()
    if not thread_id or not sender:
        return False
    try:
        from openexecutive.alerts import store

        with contextlib.closing(_conn(store._resolve_db_path(db_path))) as conn:
            return bool(_earlier(conn, thread_id, sender, subject))
    except Exception:  # noqa: BLE001
        logger.debug("one_card: couldn't look for an earlier conversation", exc_info=True)
        return False


def note_mail(
    thread_id: str, sender: str, subject: str, *, proven: bool = False, db_path: Path | None = None,
) -> None:
    """Note which conversation the mail thread ``thread_id`` belongs to. A
    reply (``Re:``) from ``sender`` to a subject they wrote before belongs to
    that earlier conversation, whatever thread the mailbox filed it in, when
    the mailbox ``proven`` the sender (a forged From: line starts its own);
    any other mail starts its own (two new mails that share a subject are
    two asks). Never raises."""
    thread_id = str(thread_id or "").strip()
    sender = str(sender or "").strip().lower()
    if not thread_id or not sender:
        return
    try:
        from openexecutive.alerts import store

        path = store._resolve_db_path(db_path)
        with contextlib.closing(_conn(path)) as conn, conn:
            if conn.execute("SELECT 1 FROM mail_conversations WHERE thread_id = ?", (thread_id,)).fetchone():
                return
            conversation = (_earlier(conn, thread_id, sender, subject) if proven else "") or thread_id
            conn.execute(
                "DELETE FROM mail_conversations WHERE created_at < ?",
                ((datetime.now(UTC) - timedelta(days=_CONVERSATION_DAYS)).isoformat(),),
            )
            conn.execute(
                "INSERT OR IGNORE INTO mail_conversations (thread_id, sender, subject, conversation, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (thread_id, sender, _subject_key(subject), conversation, _now()),
            )
    except Exception:  # noqa: BLE001 - a second card is the worst case
        logger.warning("one_card: couldn't note mail thread %s", thread_id, exc_info=True)


def _conversation(conn: sqlite3.Connection, thread: str) -> str:
    """``thread`` as the conversation it belongs to (:func:`note_mail`)."""
    if not thread.startswith("mail:"):
        return thread
    row = conn.execute(
        "SELECT conversation FROM mail_conversations WHERE thread_id = ?", (thread[len("mail:"):],)
    ).fetchone()
    return mail_thread(str(row["conversation"])) if row is not None else thread


def _turn_thread(session: Any) -> str:
    if session is None or getattr(session, "origin_channel", "") != "email":
        return ""
    return mail_thread(str(getattr(session, "email_thread_id", "") or ""))


def _turn_id() -> str:
    try:
        from openexecutive.audit.context import get_active_turn_id

        return str(get_active_turn_id() or "")
    except Exception:  # noqa: BLE001
        return ""


def _decision_of(tags: list[str]) -> int | None:
    from openexecutive.memory.decision_ledger import DECISION_INSTANCE_TAG_PREFIX

    for tag in tags:
        if tag.startswith(DECISION_INSTANCE_TAG_PREFIX):
            with contextlib.suppress(ValueError):
                return int(tag[len(DECISION_INSTANCE_TAG_PREFIX):])
    return None


def _reader(routed_to: Any, tags: list[str]) -> str:
    """Who reads the card: one person, or the team; and whether it is the
    principal's alone. Cards for different readers never join."""
    from openexecutive.alerts.models import PRIVATE_ALERT_TAG

    who = str(routed_to) if isinstance(routed_to, int) and not isinstance(routed_to, bool) else "team"
    return f"{who}{':private' if PRIVATE_ALERT_TAG in tags else ''}"


def reader_for(person_id: int | None, *, private: bool) -> str:
    """The reader key of a card for ``person_id`` (None: the team)."""
    from openexecutive.alerts.models import PRIVATE_ALERT_TAG

    return _reader(person_id, [PRIVATE_ALERT_TAG] if private else [])


# --------------------------------------------------------------------------- #
# The check
# --------------------------------------------------------------------------- #


def _conn(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(_CREATE)
    return conn


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _card(row: sqlite3.Row) -> _Card:
    return _Card(
        id=int(row["id"]), source=str(row["source"]), status=str(row["status"]),
        headline=str(row["headline"] or ""), body=str(row["body"] or ""), needs=str(row["needs"] or ""),
        reader=str(row["reader"]), decision_id=row["decision_id"], step=str(row["step"] or ""),
        thread=str(row["thread"] or ""), person=str(row["person"] or ""), turn=str(row["turn"] or ""),
        created_at=str(row["created_at"]),
    )


_SELECT = (
    "SELECT a.id, a.source, a.status, a.headline, a.body, a.needs, t.reader, t.decision_id, t.step, "
    "t.thread, t.person, t.turn, t.created_at FROM alerts a JOIN card_topics t ON t.alert_id = a.id "
)


def _with_then(existing: str, joined: str) -> str:
    from openexecutive.alerts.store import _with_follow_up

    return _with_follow_up(existing, joined)


def _said(card: _Card) -> str:
    """What a joining card adds to the one it joins: its headline, and its
    text when that says more."""
    body = " ".join(card.body.split())
    head = " ".join(card.headline.split())
    if not body or body.startswith(head[:60]):
        return body or head
    return f"{head}: {body}"


def _join(conn: sqlite3.Connection, card: _Card, into: _Card) -> None:
    """``card`` joins ``into``: its text is added last on ``into``, and it
    closes pointing at it."""
    from openexecutive.alerts.models import SEVERITY_RANK

    survivor = conn.execute("SELECT body, needs, severity FROM alerts WHERE id = ?", (into.id,)).fetchone()
    joining = conn.execute("SELECT severity FROM alerts WHERE id = ?", (card.id,)).fetchone()
    body = _with_then(str(survivor["body"] or ""), _said(card))
    # A graver card (a fraud warning) makes the card it joins as grave.
    severity = max(
        (str(survivor["severity"]), str(joining["severity"])), key=lambda sev: SEVERITY_RANK.get(sev, 0),
    )
    # Text the reader hasn't seen makes an opened card new again.
    conn.execute(
        "UPDATE alerts SET body = ?, severity = ?, occurrence_count = COALESCE(occurrence_count, 1) + 1, "
        "last_seen_at = ?, status = CASE WHEN status = 'read' THEN 'unread' ELSE status END WHERE id = ?",
        (body, severity, _now(), into.id),
    )
    if not into.approve and card.needs in ("answer", "choice") and str(survivor["needs"] or "") not in ("answer", "choice"):
        # The card that stays asks what the joining one asked.
        ask = conn.execute("SELECT needs, question, choices FROM alerts WHERE id = ?", (card.id,)).fetchone()
        conn.execute(
            "UPDATE alerts SET needs = ?, question = ?, choices = ? WHERE id = ?",
            (ask["needs"], ask["question"], ask["choices"], into.id),
        )
    _close(conn, card.id, into.id)


def _close(conn: sqlite3.Connection, card_id: int, into_id: int) -> None:
    conn.execute(
        "UPDATE alerts SET superseded_by_alert_id = ?, status = 'dismissed' WHERE id = ?",
        (into_id, card_id),
    )


def _replace(older: _Card, newer: _Card, path: Path) -> None:
    """A newer held step replaces ``older`` on the same thread: its decision
    closes (only while it still waits) and its card goes with it. Runs after
    the check's own write, since the decision is in the same database."""
    from openexecutive.alerts.models import SEVERITY_RANK
    from openexecutive.memory.decision_ledger import STATUS_CLOSED_EXTERNALLY, mark_resolved

    if older.decision_id is None or not mark_resolved(older.decision_id, STATUS_CLOSED_EXTERNALLY, db_path=path):
        return
    with contextlib.closing(_conn(path)) as conn, conn:
        # Opened already or not, its Approve would point at a closed decision.
        conn.execute(
            f"UPDATE alerts SET superseded_by_alert_id = ?, status = 'dismissed' "
            f"WHERE id = ? AND status IN ({','.join('?' * len(_OPEN))})",
            (newer.id, older.id, *_OPEN),
        )
        # What had joined the older card moves to the newer one with it.
        old = conn.execute("SELECT body, severity FROM alerts WHERE id = ?", (older.id,)).fetchone()
        new = conn.execute("SELECT body, severity FROM alerts WHERE id = ?", (newer.id,)).fetchone()
        if old is not None and new is not None and _THEN in str(old["body"]):
            severity = max(
                (str(new["severity"]), str(old["severity"])), key=lambda sev: SEVERITY_RANK.get(sev, 0),
            )
            body = str(new["body"])
            for follow_up in str(old["body"]).split(_THEN)[1:]:
                if follow_up not in body.split(_THEN)[1:]:
                    body = _with_then(body, follow_up)
            conn.execute("UPDATE alerts SET body = ?, severity = ? WHERE id = ?", (body, severity, newer.id))
        conn.execute("UPDATE alerts SET superseded_by_alert_id = ? WHERE superseded_by_alert_id = ?", (newer.id, older.id))
    try:
        from openexecutive.audit import log_event

        log_event(
            "department_action_replaced",
            f"A newer step on the same thread replaced decision {older.decision_id}",
            actor="executive",
            details={"decision_id": older.decision_id, "replaced_by": newer.decision_id},
            private=True,
        )
    except Exception:  # noqa: BLE001 - audit only
        logger.debug("one_card: audit row failed", exc_info=True)


def _same_topic(card: _Card, other: _Card, joins: int | None) -> bool:
    if card.turn and card.turn == other.turn:
        return True
    if joins == 0:
        # The model said this is another matter: the thread doesn't join it.
        return False
    if card.thread and card.thread == other.thread and card.person and card.person == other.person:
        return True
    return joins is not None and joins == other.id


# Cards whose Approve carries out what their own text says ('ok'): another
# card's words would change what approving does, so they keep to themselves.
_OWN_ACTION = "ok"


def place(alert_id: int, *, db_path: Path | None = None) -> int:
    """Note what card ``alert_id`` is about and join it with an open card on
    the same topic (see the module doc). Returns the id of the card that
    stands for it now. Never raises."""
    try:
        return _place(alert_id, db_path)
    except Exception:  # noqa: BLE001 - a second card is the worst case
        logger.warning("one_card: couldn't place card %s", alert_id, exc_info=True)
        return alert_id


def _place(alert_id: int, db_path: Path | None) -> int:
    from openexecutive.alerts import store

    path = store._resolve_db_path(db_path)
    row = store.get_alert(alert_id, db_path=path)
    if row is None:
        return alert_id
    given = _given.get() or {}
    tags = list(row.topic_tags or [])
    # Only a held step's Approve card and a card a turn raised take part:
    # cards a feature writes (a meeting's, a passed-on message, a sign-in
    # hand-over, a phishing warning) keep their own buttons and readers.
    if not (given.get("raised") or (row.source == _APPROVE_SOURCE and _decision_of(tags) is not None)):
        return alert_id
    if row.needs == _OWN_ACTION:
        return alert_id
    session = _session()
    joins = given.get("joins")
    joins = joins if isinstance(joins, int) and not isinstance(joins, bool) and joins >= 0 else None
    replaced: list[_Card] = []
    with contextlib.closing(_conn(path)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT OR REPLACE INTO card_topics (alert_id, reader, person, turn, thread, step, decision_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                alert_id, _reader(row.routed_to_person_id, tags), person_key(session), _turn_id(),
                _conversation(conn, str(given.get("thread") or "") or _turn_thread(session)),
                str(given.get("step") or ""),
                _decision_of(tags), _now(),
            ),
        )
        mine = conn.execute(_SELECT + "WHERE a.id = ?", (alert_id,)).fetchone()
        if mine is None or str(mine["status"]) not in _OPEN:
            return alert_id
        card = _card(mine)
        if not (card.turn or card.thread or joins):
            return alert_id
        own_action = {
            int(r["id"]) for r in conn.execute(
                f"SELECT id FROM alerts WHERE needs = ? AND status IN ({','.join('?' * len(_OPEN))})",
                (_OWN_ACTION, *_OPEN),
            ).fetchall()
        }
        others = [
            other for other in (
                _card(r) for r in conn.execute(
                    _SELECT + f"WHERE t.reader = ? AND a.id != ? AND a.status IN ({','.join('?' * len(_OPEN))}) "
                    "ORDER BY t.created_at, a.id",
                    (card.reader, alert_id, *_OPEN),
                ).fetchall()
            )
            if other.id not in own_action
        ]
        if card.approve and card.thread:
            # A newer send to the same people on the same thread, asked on a
            # turn with the same person, replaces the one waiting.
            replaced = [
                o for o in others
                if o.approve and (o.thread, o.step, o.person) == (card.thread, card.step, card.person)
            ]
        same = [o for o in others if o not in replaced and _same_topic(card, o, joins)]
        into = card
        if card.approve:
            for other in same:
                if not other.approve:
                    _join(conn, other, card)
        elif same:
            approves = [o for o in same if o.approve]
            into = (approves or same)[-1 if approves else 0]
            _join(conn, card, into)
            logger.info("one_card: card %d joined card %d", alert_id, into.id)
    for older in replaced:
        _replace(older, card, path)
    return into.id


def approve_waiting(thread: str, *, reader: str, step: str, db_path: Path | None = None) -> bool:
    """Whether an Approve card holding ``step`` on ``thread`` still waits for
    ``reader``: what a newer one on that thread would replace. Never raises:
    an error is no."""
    if not thread:
        return False
    try:
        from openexecutive.alerts import store

        path = store._resolve_db_path(db_path)
        if not path.exists():
            return False
        with contextlib.closing(_conn(path)) as conn:
            row = conn.execute(
                _SELECT + f"WHERE t.reader = ? AND t.thread = ? AND t.step = ? AND t.decision_id IS NOT NULL "
                f"AND a.status IN ({','.join('?' * len(_OPEN))}) LIMIT 1",
                (reader, _conversation(conn, thread), step, *_OPEN),
            ).fetchone()
        return row is not None
    except Exception:  # noqa: BLE001
        logger.debug("one_card: couldn't look for a waiting card", exc_info=True)
        return False


def open_cards_from(
    person: str, *, turn: str, reader: str, db_path: Path | None = None,
) -> list[tuple[int, str, str]]:
    """``(id, headline, question)`` of the open cards for ``reader`` that a
    turn with ``person`` raised before this turn, oldest first: what the
    model is asked about before another card from them (``create_alert``'s
    ``joins_card``). Only the reader's own: no other person's, and nothing
    private unless the new card is."""
    if not person:
        return []
    try:
        from openexecutive.alerts import store

        path = store._resolve_db_path(db_path)
        if not path.exists():
            return []
        with contextlib.closing(_conn(path)) as conn:
            rows = conn.execute(
                "SELECT a.id, a.headline, a.question FROM alerts a JOIN card_topics t ON t.alert_id = a.id "
                f"WHERE t.person = ? AND t.reader = ? AND t.turn != ? AND a.needs != ? "
                f"AND a.status IN ({','.join('?' * len(_OPEN))}) ORDER BY t.created_at, a.id LIMIT 10",
                (person, reader, turn, _OWN_ACTION, *_OPEN),
            ).fetchall()
        return [(int(r["id"]), str(r["headline"] or ""), str(r["question"] or "")) for r in rows]
    except Exception:  # noqa: BLE001
        logger.debug("one_card: couldn't list the open cards", exc_info=True)
        return []


_RE_PREFIX = re.compile(r"^\s*((re|fwd?|aw)\s*:\s*)+", re.IGNORECASE)


# Mail sends: a newer one to the same people on the same thread replaces the
# one still waiting.
MAIL_SENDS = frozenset({"google_workspace__send_gmail_message", "ms365__send_mail"})


def held_topic(tool: str, steps: list[Any], *, mcp: bool) -> tuple[str, str]:
    """``about()``'s ``(thread, step)`` for a held step: a single mail send is
    about its thread, and its step names the tool and who it goes to, so
    only a newer send to the same people replaces it. Anything else is
    about no thread."""
    if not mcp or tool not in MAIL_SENDS or len(steps) != 1 or not isinstance(steps[0], dict):
        return "", tool
    to = steps[0].get("to")
    addresses = to if isinstance(to, list) else [to] if isinstance(to, str) else []
    recipients = ",".join(sorted({str(a).strip().lower() for a in addresses if str(a).strip()}))
    return step_thread(steps[0]), f"{tool}|{recipients}"


def step_thread(tool_input: Any) -> str:
    """The thread a held mail send is on: its thread id, or for a reply
    (Re:) with none, its recipients and subject. Two new mails that share a
    subject are two asks, so they have none."""
    if not isinstance(tool_input, dict):
        return ""
    thread = mail_thread(str(tool_input.get("thread_id") or ""))
    if thread:
        return thread
    raw = str(tool_input.get("subject") or "")
    to = tool_input.get("to")
    addresses = to if isinstance(to, list) else [to] if isinstance(to, str) else []
    recipients = ",".join(sorted({str(a).strip().lower() for a in addresses if str(a).strip()}))
    subject = _RE_PREFIX.sub("", raw).strip().lower()
    if not recipients or not subject or not _RE_PREFIX.match(raw):
        return ""
    return "re:" + json.dumps([recipients, subject])
