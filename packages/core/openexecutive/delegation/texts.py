"""The person's own texts, read and sent through Google Messages in their browser.

Google Messages for web (messages.google.com) shows the texts on the
person's Android phone once they pair it, and sends from their phone, from
their own number. The Open Executive browser extension (``packages/extension``)
keeps that page open in its own window and tells this machine what it shows
(``api.routes.texts``): new texts as they arrive, and whether the page is
paired. The Executive reads them as that person's, and sends a text by asking
their extension to type it there (``orchestrator.text_tools``).

**Privacy.** Everything here is one person's. Nothing lists, reads or sends in
another person's conversations: an id that isn't theirs is not found.

**One gate.** Nothing here decides whether a text goes. A send is an ordinary
step (``send_text``) that Approvals, Your rules and In training decide like
any other (``orchestrator.take_the_lead``); this module only keeps what the
browser said and what it was asked to send.

**Install switch.** ``OE_TEXTS_OFF=1`` (or the browser being off,
``OE_BROWSER_OFF=1``) turns texts off for the install: the setting isn't
shown, the extension reads nothing, the Executive isn't offered the text
tools, and what was kept is dropped when the API starts (``tidy``). Each
person's own switch is kept, so turning it back on resumes it.

Texts older than ``KEEP_DAYS`` are dropped.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import secrets
import sqlite3
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from openexecutive.delegation.schema import (
    TEXT_CONVERSATIONS_TABLE,
    TEXT_SENDS_TABLE,
    TEXT_SETTINGS_TABLE,
    TEXTS_TABLE,
    ensure_schema,
)
from openexecutive.utils import db

KEEP_DAYS = 30
# The most texts kept per person; the oldest go first.
MAX_KEPT = 5000
MAX_TEXT = 4000
MAX_SEND = 1600
# The most new texts one report from the browser carries.
MAX_REPORT = 100

# A send, from asked to settled.
PENDING = "pending"
# The browser took it and is typing it: never handed out again, so a browser
# that couldn't say how it went never sends it twice.
HANDED = "handed_to_browser"
SENT = "sent"
FAILED = "failed"
# Handed to the browser, which never said how it went: it may or may not have gone.
UNSURE = "unsure"
SETTLED = frozenset({SENT, FAILED, UNSURE})

_db = db.company_path

# Set to "1" to turn texts off for this install.
ENV_OFF = "OE_TEXTS_OFF"
# Texts need the browser extension, which this turns off too.
BROWSER_OFF = "OE_BROWSER_OFF"
# A text the browser hasn't taken by then isn't sent: the moment has passed.
SEND_WITHIN = timedelta(hours=1)
# New texts older than this aren't looked at (a browser that was closed).
LOOK_WITHIN = timedelta(days=1)
# The extension asks for the next send at least this often while Chrome is
# open; past it, nobody is there to send one.
CONNECTED_S = 60.0


def available() -> bool:
    """Whether texts are on for this install."""
    return os.environ.get(ENV_OFF, "").strip() != "1" and os.environ.get(BROWSER_OFF, "").strip() != "1"


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db(db_path)))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(when: datetime) -> str:
    return when.astimezone(UTC).isoformat()


def _clip(text: Any, limit: int) -> str:
    return str(text or "").replace("\x00", "")[:limit]


# --------------------------------------------------------------------------- #
# The switch
# --------------------------------------------------------------------------- #


@dataclass
class Settings:
    person_id: int
    enabled: bool = False
    # What their browser last said: Google Messages is paired with their phone.
    paired: bool = False
    seen_at: str | None = None
    # The install switch (``available``), read when loaded.
    available: bool = True

    @property
    def active(self) -> bool:
        """Their switch is on and so is the install's."""
        return self.enabled and self.available


def get_settings(person_id: int, *, db_path: Path | None = None) -> Settings:
    with _connect(db_path) as conn:
        row = conn.execute(f"SELECT * FROM {TEXT_SETTINGS_TABLE} WHERE person_id=?", (person_id,)).fetchone()
    if row is None:
        return Settings(person_id, available=available())
    return Settings(
        person_id=person_id,
        enabled=bool(row["enabled"]),
        paired=bool(row["paired"]),
        seen_at=row["seen_at"],
        available=available(),
    )


def save_settings(person_id: int, *, enabled: bool, db_path: Path | None = None) -> Settings:
    """Turn the person's texts on or off. Off drops what was kept."""
    current = get_settings(person_id, db_path=db_path)
    current.enabled = enabled
    _write(current, db_path)
    if not enabled:
        forget(person_id, db_path=db_path)
    return current


def browser_seen(person_id: int, *, paired: bool, db_path: Path | None = None) -> Settings:
    """What their browser says about Google Messages, each time it reports."""
    # Only the browser's own columns: never the switch, which the person
    # may be turning off at this moment.
    with _connect(db_path) as conn:
        conn.execute(
            f"UPDATE {TEXT_SETTINGS_TABLE} SET paired=?, seen_at=?, updated_at=? WHERE person_id=?",
            (int(paired), _iso(_now()), _iso(_now()), person_id),
        )
    return get_settings(person_id, db_path=db_path)


def _write(s: Settings, db_path: Path | None) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            f"INSERT INTO {TEXT_SETTINGS_TABLE} (person_id, enabled, paired, seen_at, updated_at) "
            "VALUES (?,?,?,?,?) ON CONFLICT(person_id) DO UPDATE SET "
            "enabled=excluded.enabled, paired=excluded.paired, seen_at=excluded.seen_at, "
            "updated_at=excluded.updated_at",
            (s.person_id, int(s.enabled), int(s.paired), s.seen_at, _iso(_now())),
        )


def forget(person_id: int, *, db_path: Path | None = None) -> None:
    """Drop every text, conversation and send of ``person_id`` (they turned
    it off). The switch row stays."""
    with _connect(db_path) as conn:
        for table in (TEXTS_TABLE, TEXT_CONVERSATIONS_TABLE, TEXT_SENDS_TABLE):
            conn.execute(f"DELETE FROM {table} WHERE person_id=?", (person_id,))


def tidy(*, db_path: Path | None = None) -> None:
    """At start: drop what is past ``KEEP_DAYS``, and everything kept while
    the install has texts off (each person's switch row stays)."""
    with _connect(db_path) as conn:
        if not available():
            for table in (TEXTS_TABLE, TEXT_CONVERSATIONS_TABLE, TEXT_SENDS_TABLE):
                conn.execute(f"DELETE FROM {table}")
            return
        _prune(conn, None)
    expire_all(db_path=db_path)


def _prune(conn: sqlite3.Connection, person_id: int | None) -> None:
    """Drop texts, sends and conversations past ``KEEP_DAYS`` (one person's,
    or everyone's)."""
    cutoff = _iso(_now() - timedelta(days=KEEP_DAYS))
    who, args = ("person_id=? AND ", (person_id,)) if person_id is not None else ("", ())
    conn.execute(f"DELETE FROM {TEXTS_TABLE} WHERE {who}at < ?", (*args, cutoff))
    conn.execute(f"DELETE FROM {TEXT_SENDS_TABLE} WHERE {who}created_at < ?", (*args, cutoff))
    conn.execute(
        f"DELETE FROM {TEXT_CONVERSATIONS_TABLE} WHERE {who}updated_at < ? AND id NOT IN "
        f"(SELECT conversation_id FROM {TEXTS_TABLE}) AND id NOT IN "
        f"(SELECT conversation_id FROM {TEXT_SENDS_TABLE} WHERE conversation_id IS NOT NULL)",
        (*args, cutoff),
    )


# --------------------------------------------------------------------------- #
# What came in
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Incoming:
    """One text the browser saw in Google Messages."""

    ref: str  # the browser's id for it: the same text twice is kept once
    conversation: str  # Google Messages' key for the conversation
    title: str  # who it is with, as Google Messages shows it
    sender: str
    text: str
    at: datetime
    number: str = ""
    is_group: bool = False
    # Sent by the person themselves (from their phone): kept for context,
    # never looked at as new.
    outgoing: bool = False


@dataclass(frozen=True)
class Conversation:
    id: str
    person_id: int
    key: str
    title: str
    number: str
    is_group: bool
    updated_at: str

    @property
    def who(self) -> str:
        return self.title or self.number or "someone"


@dataclass(frozen=True)
class Text:
    id: int
    conversation_id: str
    sender: str
    text: str
    outgoing: bool
    at: str


def _conversation(row: sqlite3.Row) -> Conversation:
    return Conversation(
        id=row["id"], person_id=row["person_id"], key=row["key"], title=row["title"], number=row["number"],
        is_group=bool(row["is_group"]), updated_at=row["updated_at"],
    )


def _text(row: sqlite3.Row) -> Text:
    return Text(
        id=row["id"], conversation_id=row["conversation_id"], sender=row["sender"], text=row["text"],
        outgoing=bool(row["outgoing"]), at=row["at"],
    )


def record(person_id: int, items: list[Incoming], *, db_path: Path | None = None) -> list[int]:
    """Keep what the browser saw, while the person has texts on. Returns the
    ids of the incoming texts that are new (one seen before is kept once).
    Drops what is older than ``KEEP_DAYS``."""
    if not get_settings(person_id, db_path=db_path).active:
        return []
    fresh: list[int] = []
    now = _now()
    cutoff = now - timedelta(days=KEEP_DAYS)
    with _connect(db_path) as conn:
        _prune(conn, person_id)
        for item in items[:MAX_REPORT]:
            if not str(item.conversation or "").strip() or not str(item.ref or "").strip():
                continue
            if item.at < cutoff:
                continue
            conversation_id = _upsert_conversation(conn, person_id, item, now)
            # Which way a text went: only a text the Executive itself just sent
            # is theirs. The page's "You: …" is words anyone can type, so it
            # never makes a text theirs (the turn is told what it may mean).
            outgoing = _just_sent(conn, person_id, conversation_id, item.text, now)
            cur = conn.execute(
                f"INSERT OR IGNORE INTO {TEXTS_TABLE} "
                "(person_id, conversation_id, ref, sender, text, outgoing, at, handled) VALUES (?,?,?,?,?,?,?,?)",
                (
                    person_id, conversation_id, _clip(item.ref, 300), _clip(item.sender, 200),
                    _clip(item.text, MAX_TEXT), int(outgoing), _iso(min(item.at, now)), int(outgoing),
                ),
            )
            if cur.rowcount and cur.lastrowid is not None and not outgoing:
                fresh.append(int(cur.lastrowid))
        conn.execute(
            f"DELETE FROM {TEXTS_TABLE} WHERE person_id=? AND id NOT IN "
            f"(SELECT id FROM {TEXTS_TABLE} WHERE person_id=? ORDER BY at DESC, id DESC LIMIT ?)",
            (person_id, person_id, MAX_KEPT),
        )
    return fresh


# A list preview's "You: " in a language the browser didn't know.
_LEADING_WHO = re.compile(r"^\s*[^\s:：]{1,12}\s*[:：]\s*")
# How long after a send its own words can come back as the list's preview.
_ECHO_WITHIN = timedelta(minutes=10)


def _same_words(a: str, b: str) -> bool:
    return " ".join((a or "").split()).casefold() == " ".join((b or "").split()).casefold()


def _just_sent(conn: sqlite3.Connection, person_id: int, conversation_id: str, text: str, now: datetime) -> bool:
    """Whether these words are a text the Executive sent in this conversation
    (or to a new number) in the last few minutes, settled or still going."""
    if not (text or "").strip():
        return False
    # Only a send of the last few minutes, in this conversation, or one to a
    # new number still on its way: a reply an hour later in the same words is
    # someone's reply.
    rows = conn.execute(
        f"SELECT text FROM {TEXT_SENDS_TABLE} WHERE person_id=? AND status<>? AND created_at >= ? "
        "AND (conversation_id=? OR (conversation_id IS NULL AND status IN (?, ?)))",
        (person_id, FAILED, _iso(now - _ECHO_WITHIN), conversation_id, PENDING, HANDED),
    ).fetchall()
    bare = _LEADING_WHO.sub("", text, count=1)
    return any(_same_words(r["text"], text) or _same_words(r["text"], bare) for r in rows)


def _upsert_conversation(conn: sqlite3.Connection, person_id: int, item: Incoming, now: datetime) -> str:
    key = _clip(item.conversation, 300)
    row = conn.execute(
        f"SELECT id FROM {TEXT_CONVERSATIONS_TABLE} WHERE person_id=? AND key=?", (person_id, key),
    ).fetchone()
    number = _clip(item.number, 40)
    if row is not None:
        conn.execute(
            f"UPDATE {TEXT_CONVERSATIONS_TABLE} SET title=?, number=CASE WHEN ?<>'' THEN ? ELSE number END, "
            "is_group=?, updated_at=? WHERE id=?",
            (_clip(item.title, 200), number, number, int(item.is_group), _iso(now), row["id"]),
        )
        return str(row["id"])
    new_id = "tc_" + secrets.token_hex(8)
    conn.execute(
        f"INSERT INTO {TEXT_CONVERSATIONS_TABLE} (id, person_id, key, title, number, is_group, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (new_id, person_id, key, _clip(item.title, 200), number, int(item.is_group), _iso(now)),
    )
    return new_id


def conversation(conversation_id: str, *, db_path: Path | None = None) -> Conversation | None:
    with _connect(db_path) as conn:
        row = conn.execute(f"SELECT * FROM {TEXT_CONVERSATIONS_TABLE} WHERE id=?", (conversation_id,)).fetchone()
    return _conversation(row) if row is not None else None


def conversations(person_id: int, *, limit: int = 20, db_path: Path | None = None) -> list[Conversation]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM {TEXT_CONVERSATIONS_TABLE} WHERE person_id=? ORDER BY updated_at DESC LIMIT ?",
            (person_id, limit),
        ).fetchall()
    return [_conversation(r) for r in rows]


def texts(conversation_id: str, *, limit: int = 20, db_path: Path | None = None) -> list[Text]:
    """The conversation's latest texts, oldest first."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM {TEXTS_TABLE} WHERE conversation_id=? ORDER BY at DESC, id DESC LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
    return [_text(r) for r in reversed(rows)]


def unhandled(person_id: int, *, db_path: Path | None = None) -> list[Text]:
    """New incoming texts the Executive hasn't looked at yet, oldest first."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM {TEXTS_TABLE} WHERE person_id=? AND handled=0 AND outgoing=0 AND at >= ? "
            "ORDER BY at, id LIMIT 50",
            (person_id, _iso(_now() - LOOK_WITHIN)),
        ).fetchall()
    return [_text(r) for r in rows]


def mark_handled(person_id: int, ids: list[int], *, handled: bool = True, db_path: Path | None = None) -> None:
    """Mark texts looked at, or (``handled=False``) not yet, so a turn that
    failed looks at them again with the next new text."""
    if not ids:
        return
    with _connect(db_path) as conn:
        conn.executemany(
            f"UPDATE {TEXTS_TABLE} SET handled=? WHERE person_id=? AND id=?",
            [(1 if handled else 0, person_id, i) for i in ids],
        )


# --------------------------------------------------------------------------- #
# What it sends
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Send:
    id: int
    person_id: int
    conversation_id: str | None
    to_number: str
    text: str
    status: str
    error: str
    created_at: str
    tell_session: str | None = None


def _send(row: sqlite3.Row) -> Send:
    return Send(
        id=row["id"], person_id=row["person_id"], conversation_id=row["conversation_id"],
        to_number=row["to_number"], text=row["text"], status=row["status"], error=row["error"],
        created_at=row["created_at"], tell_session=row["tell_session"],
    )


def tell_later(send_id: int, session_id: str, *, db_path: Path | None = None) -> None:
    """The chat ``session_id`` was told this send is on its way: say there
    how it went once the browser settles it (``_tell``). Already settled: say
    it now."""
    with _connect(db_path) as conn:
        conn.execute(f"UPDATE {TEXT_SENDS_TABLE} SET tell_session=? WHERE id=?", (session_id, send_id))
        row = conn.execute(f"SELECT * FROM {TEXT_SENDS_TABLE} WHERE id=?", (send_id,)).fetchone()
    if row is not None and row["status"] in SETTLED:
        _tell(_send(row), db_path=db_path)


def send_who(send: Send, *, db_path: Path | None = None) -> str:
    """Who a send is to, in words."""
    if send.conversation_id:
        conv = conversation(send.conversation_id, db_path=db_path)
        if conv is not None:
            return conv.who
    return send.to_number or "them"


def outcome_words(send: Send, who: str) -> str:
    """How a settled send went, in a line for the person."""
    if send.status == SENT:
        return f'Texted {who}: "{send.text}"'
    if send.status == UNSURE:
        why = send.error.strip().rstrip(".") or "Chrome never said"
        return f'I don\'t know whether my text to {who} "{send.text}" went: {why}. Check your phone.'
    reason = send.error.strip().rstrip(".") if send.error.strip() else "the browser didn't say why"
    return f'I couldn\'t text {who} "{send.text}": {reason}. Nothing went.'


def _tell(send: Send, *, db_path: Path | None = None) -> None:
    """Say how a send went in the chat that was told it was on its way.
    Never raises."""
    if not send.tell_session or send.status not in SETTLED:
        return
    try:
        from openexecutive.memory import episodic
        from openexecutive.memory.session_store import save_message, update_session_timestamp

        path = db_path or episodic.DB_PATH
        save_message(send.tell_session, "assistant", outcome_words(send, send_who(send, db_path=db_path)), db_path=path)
        update_session_timestamp(send.tell_session, db_path=path)
    except Exception:
        logging.getLogger(__name__).exception("texts: couldn't say how send %s went", send.id)


def queue_send(
    person_id: int, text: str, *, conversation_id: str | None = None, to_number: str = "",
    db_path: Path | None = None,
) -> Send:
    """A text for their browser to send: in one of their conversations, or to
    a number (a new conversation)."""
    if not conversation_id and not digits(to_number):
        raise ValueError("a send needs a conversation or a number")
    with _connect(db_path) as conn:
        cur = conn.execute(
            f"INSERT INTO {TEXT_SENDS_TABLE} (person_id, conversation_id, to_number, text, status, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                person_id, conversation_id or None, "" if conversation_id else _clip(to_number, 40),
                _clip(text, MAX_SEND), PENDING, _iso(_now()),
            ),
        )
        row = conn.execute(f"SELECT * FROM {TEXT_SENDS_TABLE} WHERE id=?", (cur.lastrowid,)).fetchone()
    return _send(row)


def _expire(person_id: int, db_path: Path | None) -> None:
    """A send not taken within ``SEND_WITHIN`` fails instead of going late,
    and the chat that waits on it hears so."""
    expired = []
    with _connect(db_path) as conn:
        for waiting, within, now_status, error in (
            (PENDING, SEND_WITHIN, FAILED, "Chrome wasn't open on your computer within the hour"),
            # Taken by the browser, which never said how it went (one taken
            # late in the hour still gets time to finish).
            (HANDED, 2 * SEND_WITHIN, UNSURE, "Chrome never said whether it went"),
        ):
            late = conn.execute(
                f"SELECT id FROM {TEXT_SENDS_TABLE} WHERE person_id=? AND status=? AND created_at < ?",
                (person_id, waiting, _iso(_now() - within)),
            ).fetchall()
            for r in late:
                cur = conn.execute(
                    f"UPDATE {TEXT_SENDS_TABLE} SET status=?, error=?, done_at=? WHERE id=? AND status=?",
                    (now_status, error, _iso(_now()), r["id"], waiting),
                )
                if cur.rowcount == 1:
                    row = conn.execute(f"SELECT * FROM {TEXT_SENDS_TABLE} WHERE id=?", (r["id"],)).fetchone()
                    expired.append(_send(row))
    for gone in expired:
        _tell(gone, db_path=db_path)


def expire_all(*, db_path: Path | None = None) -> None:
    """Fail or settle every overdue send, whoever's (``_expire``): a send
    stays on time even when their browser never asks again. Never raises."""
    try:
        with _connect(db_path) as conn:
            people = [int(r[0]) for r in conn.execute(
                f"SELECT DISTINCT person_id FROM {TEXT_SENDS_TABLE} WHERE status IN (?, ?)", (PENDING, HANDED),
            ).fetchall()]
        for person_id in people:
            _expire(person_id, db_path)
    except Exception:
        logging.getLogger(__name__).exception("texts: expiring sends failed")


def expire_later() -> None:
    """Look for overdue sends once each could be due (``SEND_WITHIN`` for one
    never taken, twice that for one taken and never answered), in this
    process. A restart looks at start (``tidy``). Never raises."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    for after in (SEND_WITHIN, 2 * SEND_WITHIN):
        loop.call_later(
            after.total_seconds() + 60,
            lambda: loop.run_in_executor(None, lambda: expire_all()),
        )


def take_send(person_id: int, *, db_path: Path | None = None) -> tuple[Send, str] | None:
    """The oldest send waiting for their browser, taken now (``HANDED``) so it
    is handed out once, with Google Messages' key for its conversation ("" for
    a new one, to ``to_number``)."""
    _expire(person_id, db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT s.*, c.key AS conv_key FROM {TEXT_SENDS_TABLE} s "
            f"LEFT JOIN {TEXT_CONVERSATIONS_TABLE} c ON c.id = s.conversation_id "
            "WHERE s.person_id=? AND s.status=? ORDER BY s.id LIMIT 5",
            (person_id, PENDING),
        ).fetchall()
        for r in rows:
            # Claimed on its own: two of the person's browsers asking at once
            # never both get the same send.
            cur = conn.execute(
                f"UPDATE {TEXT_SENDS_TABLE} SET status=? WHERE id=? AND status=?", (HANDED, r["id"], PENDING),
            )
            if cur.rowcount == 1:
                return _send(r), str(r["conv_key"] or "")
    return None


def settle_send(
    person_id: int, send_id: int, *, status: str, error: str = "", conversation_key: str = "",
    db_path: Path | None = None,
) -> Send | None:
    """The browser says how a send went. A send already settled stays as it
    was; one that went is kept in its conversation as theirs (a text to a new
    number, in the conversation the browser says it went to)."""
    if status not in SETTLED:
        raise ValueError(status)
    now = _now()
    with _connect(db_path) as conn:
        row = conn.execute(
            f"SELECT * FROM {TEXT_SENDS_TABLE} WHERE id=? AND person_id=?", (send_id, person_id),
        ).fetchone()
        if row is None:
            return None
        if row["status"] not in (PENDING, HANDED):
            return _send(row)
        # Claimed once: a second answer for the same send (two tabs, a retry)
        # changes nothing and tells no one again.
        claimed = conn.execute(
            f"UPDATE {TEXT_SENDS_TABLE} SET status=?, error=?, done_at=? WHERE id=? AND status IN (?, ?)",
            (status, _clip(error, 300), _iso(now), send_id, PENDING, HANDED),
        )
        if claimed.rowcount != 1:
            row = conn.execute(f"SELECT * FROM {TEXT_SENDS_TABLE} WHERE id=?", (send_id,)).fetchone()
            return _send(row)
        conversation_id = row["conversation_id"]
        if status == SENT and not conversation_id and conversation_key.strip():
            conversation_id = _upsert_conversation(
                conn, person_id,
                Incoming(ref="", conversation=conversation_key, title=row["to_number"], sender="", text="",
                         at=now, number=row["to_number"]),
                now,
            )
            conn.execute(f"UPDATE {TEXT_SENDS_TABLE} SET conversation_id=? WHERE id=?", (conversation_id, send_id))
        if status == SENT and conversation_id:
            conn.execute(
                f"INSERT OR IGNORE INTO {TEXTS_TABLE} "
                "(person_id, conversation_id, ref, sender, text, outgoing, at, handled) VALUES (?,?,?,?,?,1,?,1)",
                (person_id, conversation_id, f"send:{send_id}", "", row["text"], _iso(now)),
            )
        row = conn.execute(f"SELECT * FROM {TEXT_SENDS_TABLE} WHERE id=?", (send_id,)).fetchone()
    settled = _send(row)
    _tell(settled, db_path=db_path)
    return settled


def get_send(send_id: int, *, db_path: Path | None = None) -> Send | None:
    with _connect(db_path) as conn:
        row = conn.execute(f"SELECT * FROM {TEXT_SENDS_TABLE} WHERE id=?", (send_id,)).fetchone()
    return _send(row) if row is not None else None


# --------------------------------------------------------------------------- #
# Their browser asking for the next send
# --------------------------------------------------------------------------- #

# Held in this process: an install runs one API process.
_asked: dict[int, float] = {}
_ready: dict[int, asyncio.Event] = {}


def connected(person_id: int) -> bool:
    """Whether their browser asked for a send lately (Chrome is open)."""
    return time.monotonic() - _asked.get(person_id, -1e9) < CONNECTED_S


def wake(person_id: int) -> None:
    """A send is waiting for ``person_id``: an ask that is waiting returns now."""
    event = _ready.get(person_id)
    if event is not None:
        event.set()


async def next_send(
    person_id: int,
    *,
    wait_s: float,
    gone: Callable[[], Awaitable[bool]] | None = None,
    db_path: Path | None = None,
) -> tuple[Send, str] | None:
    """The next send for their browser, waiting up to ``wait_s`` for one.
    ``gone`` says whether the browser has stopped waiting: then nothing is
    taken, so a send is never handed to a browser that isn't there to get it."""
    _asked[person_id] = time.monotonic()
    deadline = time.monotonic() + max(0.0, wait_s)
    while True:
        event = _ready.setdefault(person_id, asyncio.Event())
        event.clear()
        if gone is not None and await gone():
            return None
        taken = await asyncio.to_thread(take_send, person_id, db_path=db_path)
        left = deadline - time.monotonic()
        if taken is not None or left <= 0:
            _asked[person_id] = time.monotonic()
            return taken
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(event.wait(), timeout=left)


# --------------------------------------------------------------------------- #
# Who a conversation is with
# --------------------------------------------------------------------------- #

_DIGITS_RE = re.compile(r"\D+")


def digits(number: str) -> str:
    """A phone number's digits, the last ten (so +1 and a leading 0 match)."""
    return _DIGITS_RE.sub("", number or "")[-10:]


def looks_like_number(text: str) -> bool:
    """Whether a conversation's title is a phone number rather than a name."""
    stripped = re.sub(r"[\s()+\-.]", "", text or "")
    return bool(stripped) and stripped.isdigit() and len(stripped) >= 3


def known_person(conv: Conversation) -> Any | None:
    """The person on People (contacts included) this conversation is with,
    or None (a group, someone not on People). By phone number; else by the
    name Google Messages shows, which comes from the contacts on the person's
    own phone, when exactly one person on People has that full name. A group,
    whose members can name it, is never a person. Never raises."""
    if conv.is_group:
        return None
    try:
        from openexecutive.people.store import list_people

        people = [p for p in list_people(include_contacts=True) if not getattr(p, "archived", False)]
    except Exception:
        return None
    want = digits(conv.number or (conv.title if looks_like_number(conv.title) else ""))
    if len(want) >= 7:
        for p in people:
            for detail in getattr(p, "details", None) or []:
                kind = getattr(detail, "kind", None) if not isinstance(detail, dict) else detail.get("kind")
                value = getattr(detail, "value", None) if not isinstance(detail, dict) else detail.get("value")
                if kind in ("phone", "mobile") and digits(str(value or "")) == want:
                    return p
    name = " ".join((conv.title or "").split()).casefold()
    if name and not looks_like_number(conv.title):
        named = [p for p in people if " ".join((getattr(p, "full_name", "") or "").split()).casefold() == name]
        if len(named) == 1:
            return named[0]
    return None
