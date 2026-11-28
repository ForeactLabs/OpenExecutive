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

Texts older than ``KEEP_DAYS`` are dropped. Sign-in codes are never kept
(``sign_in_codes``): the extension doesn't send them, and one that comes
anyway is dropped here.
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
from openexecutive.delegation.sign_in_codes import is_sign_in_code
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
# A send's ``echoed`` when its words came back marked as theirs ("You: …"),
# not only as the same words (1): enough to say an unsure send went.
ECHOED_AS_YOURS = 2

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
    the install has texts off (each person's switch row stays). A send
    marked unsure whose words came back is sent."""
    with _connect(db_path) as conn:
        if not available():
            for table in (TEXTS_TABLE, TEXT_CONVERSATIONS_TABLE, TEXT_SENDS_TABLE):
                conn.execute(f"DELETE FROM {table}")
            return
        _prune(conn, None)
        _drop_codes(conn)
        # One the browser wasn't sure of whose words came back went after all
        # (``record``), including one settled before that was looked at.
        went = [int(r["id"]) for r in conn.execute(
            f"SELECT id FROM {TEXT_SENDS_TABLE} WHERE status=? AND echoed=?", (UNSURE, ECHOED_AS_YOURS),
        ).fetchall()]
        for send_id in went:
            conn.execute(f"UPDATE {TEXT_SENDS_TABLE} SET status=?, error='' WHERE id=?", (SENT, send_id))
    for send_id in went:
        _went_after_all(send_id, db_path)
    expire_all(db_path=db_path)


def _drop_codes(conn: sqlite3.Connection) -> None:
    """Drop sign-in codes kept before they were left out (``record``)."""
    rows = conn.execute(f"SELECT id, text FROM {TEXTS_TABLE} WHERE outgoing=0").fetchall()
    codes = [(int(r["id"]),) for r in rows if is_sign_in_code(str(r["text"] or ""))]
    if codes:
        conn.executemany(f"DELETE FROM {TEXTS_TABLE} WHERE id=?", codes)
        # A conversation that held only a code goes too, as record never
        # makes one for a code.
        conn.execute(
            f"DELETE FROM {TEXT_CONVERSATIONS_TABLE} WHERE id NOT IN (SELECT conversation_id FROM {TEXTS_TABLE}) "
            f"AND id NOT IN (SELECT conversation_id FROM {TEXT_SENDS_TABLE} WHERE conversation_id IS NOT NULL)"
        )


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
    # The browser's guess that the person sent it themselves (a "You:"
    # preview). Never trusted for who said it: anyone can text those words,
    # so only the Executive's own sends count as outgoing (``_echo_of``). It
    # only skips the turn on that text (kept as already looked at).
    outgoing: bool = False
    # Already in the list when the browser first looked (texts just turned
    # on): kept to read, never answered as if it just came.
    earlier: bool = False


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
    went: list[int] = []
    now = _now()
    cutoff = now - timedelta(days=KEEP_DAYS)
    with _connect(db_path) as conn:
        # Turned off since the check above: off forgets them, so keep none.
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        on = conn.execute(f"SELECT enabled FROM {TEXT_SETTINGS_TABLE} WHERE person_id=?", (person_id,)).fetchone()
        if on is None or not on["enabled"]:
            return []
        _prune(conn, person_id)
        for item in items[:MAX_REPORT]:
            if not str(item.conversation or "").strip() or not str(item.ref or "").strip():
                continue
            if item.at < cutoff:
                continue
            # A sign-in code is never kept, nor its conversation touched.
            if is_sign_in_code(item.text):
                continue
            conversation_id = _upsert_conversation(conn, person_id, item, now)
            # Which way a text went: only a text the Executive itself just sent
            # is theirs. The page's "You: …" is words anyone can type, so it
            # never makes a text theirs (the turn is told what it may mean).
            echo = _echo_of(conn, person_id, conversation_id, item.text, now)
            outgoing = echo is not None
            ref, sender, words = _clip(item.ref, 300), _clip(item.sender, 200), _clip(item.text, MAX_TEXT)
            if echo is not None:
                # Marked as theirs ("You: …", which the browser flags) too, it
                # is ``ECHOED_AS_YOURS``: the same words from the other side
                # alone are not (nor a first look's rows, flagged without it).
                as_yours = bool(item.outgoing) and not item.earlier and _LEADING_WHO.match(item.text or "") is not None
                conn.execute(
                    f"UPDATE {TEXT_SENDS_TABLE} SET echoed=? WHERE id=?",
                    (ECHOED_AS_YOURS if as_yours else 1, echo),
                )
                if as_yours:
                    # Its words came back as theirs: a send the browser
                    # wasn't sure of went after all.
                    cur = conn.execute(
                        f"UPDATE {TEXT_SENDS_TABLE} SET status=?, error='' WHERE id=? AND status=?",
                        (SENT, echo, UNSURE),
                    )
                    if cur.rowcount == 1:
                        went.append(echo)
                # A send to a new number now has its conversation.
                conn.execute(
                    f"UPDATE {TEXT_SENDS_TABLE} SET conversation_id=? WHERE id=? AND conversation_id IS NULL",
                    (conversation_id, echo),
                )
                # One row per send: the one its settle writes (``send:<id>``),
                # whichever comes first, in the words it sent.
                ref, sender = f"send:{echo}", ""
                sent = conn.execute(f"SELECT text FROM {TEXT_SENDS_TABLE} WHERE id=?", (echo,)).fetchone()
                words = str(sent["text"]) if sent is not None else words
            cur = conn.execute(
                f"INSERT OR IGNORE INTO {TEXTS_TABLE} "
                "(person_id, conversation_id, ref, sender, text, outgoing, at, handled) VALUES (?,?,?,?,?,?,?,?)",
                # The page's "You:" hint never makes a text theirs, but it
                # skips the turn on it: the worst a stranger typing "You:"
                # gets is no reply on its own (it is still kept and read).
                (person_id, conversation_id, ref, sender, words, int(outgoing), _iso(min(item.at, now)),
                 int(outgoing or item.outgoing or item.earlier)),
            )
            if cur.rowcount and cur.lastrowid is not None and not (outgoing or item.outgoing or item.earlier):
                fresh.append(int(cur.lastrowid))
        conn.execute(
            f"DELETE FROM {TEXTS_TABLE} WHERE person_id=? AND id NOT IN "
            f"(SELECT id FROM {TEXTS_TABLE} WHERE person_id=? ORDER BY at DESC, id DESC LIMIT ?)",
            (person_id, person_id, MAX_KEPT),
        )
    for send_id in went:
        _went_after_all(send_id, db_path)
    return fresh


# A list preview's "You: " in a language the browser didn't know.
_LEADING_WHO = re.compile(r"^\s*[^\s:：]{1,12}\s*[:：]\s*")
# How long after a send its own words can come back as the list's preview.
_ECHO_WITHIN = timedelta(minutes=10)


def _same_words(a: str, b: str) -> bool:
    return " ".join((a or "").split()).casefold() == " ".join((b or "").split()).casefold()


def _echo_of(conn: sqlite3.Connection, person_id: int, conversation_id: str, text: str, now: datetime) -> int | None:
    """The send these words are the echo of: a text the Executive sent in
    this conversation (or to a new number) in the last few minutes, settled
    or still going, whose words haven't come back yet. Each send comes back
    once: the same words again are someone's reply. None when they aren't."""
    if not (text or "").strip():
        return None
    # Only a send of the last few minutes (since the browser took it or said
    # how it went, as it can wait in the queue), in this conversation, or one
    # to a new number still on its way or unsure: a reply an hour later in the same
    # words is someone's reply.
    rows = conn.execute(
        f"SELECT id, text, conversation_id, to_number FROM {TEXT_SENDS_TABLE} WHERE person_id=? AND status<>? "
        "AND echoed=0 AND COALESCE(done_at, created_at) >= ? AND (conversation_id=? OR (conversation_id IS NULL AND status IN (?, ?, ?))) "
        "ORDER BY id",
        (person_id, FAILED, _iso(now - _ECHO_WITHIN), conversation_id, PENDING, HANDED, UNSURE),
    ).fetchall()
    bare = _LEADING_WHO.sub("", text, count=1)
    same = [r for r in rows if _same_words(r["text"], text) or _same_words(r["text"], bare)]
    for r in same:
        if r["conversation_id"] == conversation_id:
            return int(r["id"])
    # A send to a new number comes back only in the conversation it opened:
    # one with that number, or one with nothing in it yet.
    if not same:
        return None
    conv = conn.execute(
        f"SELECT title, number FROM {TEXT_CONVERSATIONS_TABLE} WHERE id=?", (conversation_id,),
    ).fetchone()
    shown = digits((conv["number"] or conv["title"]) if conv is not None else "")
    for r in same:
        if len(shown) >= 7 and digits(r["to_number"] or "") == shown:
            return int(r["id"])
    if len(shown) >= 7:
        # A number that isn't the one it went to: someone else's conversation.
        return None
    earlier = conn.execute(f"SELECT 1 FROM {TEXTS_TABLE} WHERE conversation_id=? LIMIT 1", (conversation_id,)).fetchone()
    return int(same[0]["id"]) if earlier is None else None


def _upsert_conversation(conn: sqlite3.Connection, person_id: int, item: Incoming, now: datetime) -> str:
    key = _clip(item.conversation, 300)
    row = conn.execute(
        f"SELECT id FROM {TEXT_CONVERSATIONS_TABLE} WHERE person_id=? AND key=?", (person_id, key),
    ).fetchone()
    number = _clip(item.number, 40)
    # When its latest text came, not when the browser said so: a first look
    # tells a month of conversations at once, and the list is newest first.
    # Never moved back by an older text told late.
    at = _iso(min(item.at, now))
    if row is not None:
        conn.execute(
            f"UPDATE {TEXT_CONVERSATIONS_TABLE} SET title=?, number=CASE WHEN ?<>'' THEN ? ELSE number END, "
            "is_group=?, updated_at=MAX(updated_at, ?) WHERE id=?",
            (_clip(item.title, 200), number, number, int(item.is_group), at, row["id"]),
        )
        return str(row["id"])
    new_id = "tc_" + secrets.token_hex(8)
    conn.execute(
        f"INSERT INTO {TEXT_CONVERSATIONS_TABLE} (id, person_id, key, title, number, is_group, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (new_id, person_id, key, _clip(item.title, 200), number, int(item.is_group), at),
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


def link_log(send_id: int, log_id: int, *, db_path: Path | None = None) -> None:
    """Tie a send to the log row that says it is on its way. Already
    settled (the browser was quick): the row hears how it went now."""
    with _connect(db_path) as conn:
        conn.execute(f"UPDATE {TEXT_SENDS_TABLE} SET log_id=? WHERE id=?", (log_id, send_id))
        row = conn.execute(f"SELECT * FROM {TEXT_SENDS_TABLE} WHERE id=?", (send_id,)).fetchone()
    if row is not None and row["status"] in SETTLED:
        _settle_log(row, db_path)


def _settle_log(row: sqlite3.Row, db_path: Path | None = None) -> None:
    """Tell a settled send's log row how it went. Never raises."""
    try:
        log_id = row["log_id"]
        if log_id is None:
            return
        from openexecutive.orchestrator import take_the_lead

        take_the_lead.device_settled(int(log_id), str(row["status"]), db_path=db_path)
    except Exception:
        logging.getLogger(__name__).exception("texts: couldn't say on its row how send %s went", row["id"])


def _went_after_all(send_id: int, db_path: Path | None = None) -> None:
    """A send the browser wasn't sure of came back as theirs, so it went:
    its log row says so, the card that said it may not have is closed, and a
    chat that was told it may not have hears it went. Never raises."""
    try:
        with _connect(db_path) as conn:
            row = conn.execute(f"SELECT * FROM {TEXT_SENDS_TABLE} WHERE id=?", (send_id,)).fetchone()
        if row is None:
            return
        _settle_log(row, db_path)
        send = _send(row)
        if send.tell_session == TELL_ON_CARD:
            from openexecutive.alerts.store import set_status_by_external

            set_status_by_external(TEXTS_SOURCE, f"text_send:{send.id}", "resolved", db_path=db_path)
        else:
            _tell(send, db_path=db_path)
    except Exception:
        logging.getLogger(__name__).exception("texts: couldn't say send %s went after all", send_id)


def send_who(send: Send, *, db_path: Path | None = None) -> str:
    """Who a send is to, in words."""
    if send.conversation_id:
        conv = conversation(send.conversation_id, db_path=db_path)
        if conv is not None:
            return conv.who
    name = number_who(send.to_number, db_path=db_path)
    # The number stays beside a People name (only its last ten digits matched).
    return (f"{name} ({send.to_number})" if name != send.to_number else name) or "them"


def number_who(number: str, *, db_path: Path | None = None) -> str:
    """A number in words: the name on People (contacts included) when the
    number is theirs, else the number itself. Never raises."""
    number = (number or "").strip()
    if not number:
        return ""
    conv = Conversation(id="", person_id=0, key="", title="", number=number, is_group=False, updated_at="")
    person = known_person(conv, db_path=db_path)
    name = str(getattr(person, "full_name", "") or "").strip() if person is not None else ""
    return name or number


def outcome_words(send: Send, who: str) -> str:
    """How a settled send went, in a line for the person."""
    if send.status == SENT:
        return f'Texted {who}: "{send.text}"'
    if send.status == UNSURE:
        why = send.error.strip().rstrip(".") or "Chrome never said"
        return f'I don\'t know whether my text to {who} "{send.text}" went: {why}. Check your phone.'
    reason = send.error.strip().rstrip(".") if send.error.strip() else "the browser didn't say why"
    return f'I couldn\'t text {who} "{send.text}": {reason}. Nothing went.'


# ``tell_later`` for a turn no chat shows (texts_watch, an approved card):
# a text that didn't go is told on a card of the person's own instead.
TELL_ON_CARD = "card"
TEXTS_SOURCE = "texts"


def _tell(send: Send, *, db_path: Path | None = None) -> None:
    """Say how a send went in the chat that was told it was on its way, or,
    for a turn no chat shows, on a card of their own when it didn't go.
    Never raises."""
    if not send.tell_session or send.status not in SETTLED:
        return
    if send.tell_session == TELL_ON_CARD:
        if send.status != SENT:
            _tell_on_card(send, db_path=db_path)
        return
    try:
        from openexecutive.memory import episodic
        from openexecutive.memory.session_store import save_message, update_session_timestamp

        path = db_path or episodic.DB_PATH
        save_message(send.tell_session, "assistant", outcome_words(send, send_who(send, db_path=db_path)), db_path=path)
        update_session_timestamp(send.tell_session, db_path=path)
    except Exception:
        logging.getLogger(__name__).exception("texts: couldn't say how send %s went", send.id)


def _tell_on_card(send: Send, *, db_path: Path | None = None) -> None:
    """A card for the person alone: a text it said was on its way didn't go
    (or may not have). Never raises."""
    try:
        from openexecutive.alerts.store import insert_alert

        words = outcome_words(send, send_who(send, db_path=db_path))
        insert_alert(
            source=TEXTS_SOURCE,
            external_id=f"text_send:{send.id}",
            severity="medium",
            headline=words[:160],
            body=words,
            dedup_key=f"text_send:{send.id}",
            routed_to_person_id=send.person_id,
            db_path=db_path,
        )
    except Exception:
        logging.getLogger(__name__).exception("texts: couldn't put how send %s went on a card", send.id)


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
                # One whose words already came back as theirs went.
                cur = conn.execute(
                    f"UPDATE {TEXT_SENDS_TABLE} SET status=CASE WHEN echoed=? THEN ? ELSE ? END, "
                    "error=CASE WHEN echoed=? THEN '' ELSE ? END, done_at=? WHERE id=? AND status=?",
                    (ECHOED_AS_YOURS, SENT, now_status, ECHOED_AS_YOURS, error, _iso(_now()), r["id"], waiting),
                )
                if cur.rowcount == 1:
                    row = conn.execute(f"SELECT * FROM {TEXT_SENDS_TABLE} WHERE id=?", (r["id"],)).fetchone()
                    expired.append(row)
    for gone in expired:
        _settle_log(gone, db_path)
        _tell(_send(gone), db_path=db_path)


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
                f"UPDATE {TEXT_SENDS_TABLE} SET status=?, done_at=? WHERE id=? AND status=?",
                (HANDED, _iso(datetime.now(UTC)), r["id"], PENDING),
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
        # Read and claim in one write: an echo ``record`` sees in between
        # never leaves a send that went as unsure.
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            f"SELECT * FROM {TEXT_SENDS_TABLE} WHERE id=? AND person_id=?", (send_id, person_id),
        ).fetchone()
        if row is None:
            return None
        if row["status"] not in (PENDING, HANDED):
            return _send(row)
        # Its words already came back as theirs (``record``): it went, even
        # if the browser couldn't see it go.
        if status == UNSURE and row["echoed"] == ECHOED_AS_YOURS:
            status, error = SENT, ""
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
    _settle_log(row, db_path)
    _tell(settled, db_path=db_path)
    return settled


def last_send_id(person_id: int, *, db_path: Path | None = None) -> int:
    """The id of the person's latest send (0 for none): to tell whether a
    turn texted anyone."""
    with _connect(db_path) as conn:
        row = conn.execute(f"SELECT MAX(id) AS m FROM {TEXT_SENDS_TABLE} WHERE person_id=?", (person_id,)).fetchone()
    return int(row["m"] or 0)


def on_its_way(conversation_id: str, *, db_path: Path | None = None) -> list[str]:
    """The words of sends in this conversation the browser hasn't sent yet."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT text FROM {TEXT_SENDS_TABLE} WHERE conversation_id=? AND status IN (?, ?) ORDER BY id",
            (conversation_id, PENDING, HANDED),
        ).fetchall()
    return [str(r["text"]) for r in rows]


def get_send(send_id: int, *, db_path: Path | None = None) -> Send | None:
    with _connect(db_path) as conn:
        row = conn.execute(f"SELECT * FROM {TEXT_SENDS_TABLE} WHERE id=?", (send_id,)).fetchone()
    return _send(row) if row is not None else None


# --------------------------------------------------------------------------- #
# The Texts page: each conversation, what the Executive did, what was said
# --------------------------------------------------------------------------- #

# The most characters of the Executive's line on a conversation kept.
MAX_NOTE = 600


def save_note(person_id: int, conversation_id: str, note: str, *, db_path: Path | None = None) -> None:
    """Keep the Executive's line on what it did about a conversation (its
    turn's last line, ``texts_watch``). Only the person's own conversation."""
    line = " ".join(str(note or "").split())
    if not line:
        return
    with _connect(db_path) as conn:
        conn.execute(
            f"UPDATE {TEXT_CONVERSATIONS_TABLE} SET note=?, note_at=? WHERE id=? AND person_id=?",
            (_clip(line, MAX_NOTE), _iso(_now()), conversation_id, person_id),
        )


@dataclass(frozen=True)
class Thread:
    """One row on the Texts page: a conversation, or a text on its way to a
    number with no conversation yet."""

    id: str
    who: str
    number: str
    is_group: bool
    at: str
    # The Executive's own line on what it did, or "".
    note: str
    # How the latest text it sent here went (``SENT``, ``FAILED``, ``UNSURE``,
    # or ``"sending"`` while on its way), or ``"read"`` when it sent none.
    status: str
    # The latest texts, oldest first.
    texts: list[Text]


def _status(send_status: str | None) -> str:
    if send_status is None:
        return "read"
    return "sending" if send_status in (PENDING, HANDED) else send_status


def threads(person_id: int, *, limit: int = 50, per: int = 20, db_path: Path | None = None) -> list[Thread]:
    """The person's own conversations for the Texts page, latest first. Each
    one's status comes from its latest send, and once that send is newer
    than the Executive's line on it (a card they approved later), the line
    says how that send went. A send to a new number shows as its own row
    until the browser says which conversation it went to, or a conversation
    with that number is there: then it counts as that conversation's."""
    out: list[Thread] = []
    with _connect(db_path) as conn:
        # Latest by whatever happened last: a text in, a send, or a note.
        convs = conn.execute(
            f"SELECT c.* FROM {TEXT_CONVERSATIONS_TABLE} c WHERE c.person_id=? ORDER BY MAX("
            "c.updated_at, COALESCE(c.note_at, ''), "
            f"COALESCE((SELECT MAX(s.created_at) FROM {TEXT_SENDS_TABLE} s WHERE s.conversation_id=c.id), '')"
            ") DESC LIMIT ?",
            (person_id, limit),
        ).fetchall()
        loose = conn.execute(
            f"SELECT * FROM {TEXT_SENDS_TABLE} WHERE person_id=? AND conversation_id IS NULL "
            "ORDER BY id DESC LIMIT ?",
            (person_id, limit),
        ).fetchall()
        # A send to a number with a conversation here belongs to it.
        by_number: dict[str, sqlite3.Row] = {}
        for c in convs:
            key = digits(str(c["number"] or "") or str(c["title"] or ""))[-10:]
            if len(key) >= 7 and not c["is_group"]:
                by_number.setdefault(key, c)
        folded: dict[str, list[sqlite3.Row]] = {}
        alone: list[sqlite3.Row] = []
        for r in loose:
            home = by_number.get(digits(str(r["to_number"] or ""))[-10:])
            if home is not None:
                folded.setdefault(str(home["id"]), []).append(r)
            else:
                alone.append(r)
        for c in convs:
            last = conn.execute(
                f"SELECT * FROM {TEXT_SENDS_TABLE} WHERE person_id=? AND conversation_id=? "
                "ORDER BY id DESC LIMIT 1",
                (person_id, c["id"]),
            ).fetchone()
            for r in folded.get(str(c["id"]), []):
                if last is None or int(r["id"]) > int(last["id"]):
                    last = r
            rows = conn.execute(
                f"SELECT * FROM {TEXTS_TABLE} WHERE person_id=? AND conversation_id=? ORDER BY at DESC, id DESC LIMIT ?",
                (person_id, c["id"], per),
            ).fetchall()
            note, note_at = str(c["note"] or ""), str(c["note_at"] or "")
            at = max(str(c["updated_at"] or ""), str(last["created_at"]) if last else "", note_at)
            who = _conversation(c).who
            if last is not None and note and str(last["created_at"]) > note_at:
                # Its line was written before this send (it waited on a card):
                # say how the send went instead, once it has.
                send = _send(last)
                if send.status in SETTLED:
                    note = outcome_words(send, who)
            out.append(Thread(
                id=str(c["id"]), who=who, number=str(c["number"] or ""),
                is_group=bool(c["is_group"]), at=at, note=note,
                status=_status(last["status"] if last else None),
                texts=[_text(r) for r in reversed(rows)],
            ))
    for r in alone:
        out.append(Thread(
            id=f"send-{r['id']}", who=str(r["to_number"] or "someone"), number=str(r["to_number"] or ""),
            is_group=False, at=str(r["created_at"]), note="", status=_status(str(r["status"])),
            texts=[Text(id=0, conversation_id="", sender="", text=str(r["text"]), outgoing=True,
                        at=str(r["created_at"]))],
        ))
    out.sort(key=lambda t: t.at, reverse=True)
    return out[:limit]


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


def known_person(conv: Conversation, *, db_path: Path | None = None) -> Any | None:
    """The person on People (contacts included) this conversation is with,
    or None (a group, someone not on People). By phone number only: the name
    Google Messages shows can be one the sender chose (an SMS sender ID, an
    RCS business name), so a conversation with no number is nobody known.
    A group is never a person. Never raises."""
    if conv.is_group:
        return None
    try:
        from openexecutive.people.store import list_people

        people = [p for p in list_people(db_path=db_path, include_contacts=True) if not getattr(p, "archived", False)]
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
    return None
