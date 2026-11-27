"""What came in for a person, kept a short while so the Executive knows it.

An email in their own inbox (``delegation.mail_context``), and any other
source that reports what reached them, writes one row here: who it is from,
its subject or title, a one-line gist of what it says, and what it ties to
that the Executive already knows (a meeting, someone in People, an open
task), if anything. Chat reads it with ``what_came_in``
(``orchestrator.mail_read_tools``) and the brief reads the principal's
(``briefing.live_signals.own_inbox``), so "did the refund come through?" is
answered without a search.

- **Theirs alone.** Every row is keyed on its person and read only for them,
  behind the same fence as their mailbox.
- **Short-lived.** A row older than ``KEEP`` is never listed and is deleted
  on the next write; turning a source's switch off deletes the person's rows
  from that source (``forget``: Act as me, their email).
- **Once.** ``item_key`` is the source's own id (an email's message id, a
  phone alert's app and key), one row per person. A source whose item changes
  in place (a flight delay growing) passes ``refresh=True``: the row takes the
  new words, counts the update and is new again, so it gets one more look. The same item seen another way (a phone alert for an
  email already read here) shares its ``match_key`` (sender name and the
  start of the subject) and is dropped when it came within ``MATCH_WINDOW``
  of one from another source. Acting on an item is claimed once
  (``claim_to_act``).

Never raises: a row not written costs the context, never the mail.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from openexecutive.delegation.schema import NOTICED_TABLE, ensure_schema
from openexecutive.utils import db

logger = logging.getLogger(__name__)

KEEP = timedelta(days=2)
MATCH_WINDOW = timedelta(minutes=30)
MAX_LISTED = 40
_FIELD_CHARS = 200
_GIST_CHARS = 240
_TIE_CHARS = 120
# Message and thread ids are kept whole: Outlook's run to 512 characters.
ID_CHARS = 512
_MATCH_SUBJECT_CHARS = 40
_REPLY_PREFIX_RE = re.compile(r"^\s*((re|fw|fwd|aw|wg|sv|tr)\s*:\s*)+", re.IGNORECASE)

EMAIL = "email"


@dataclass(frozen=True)
class Noticed:
    source: str
    item_key: str
    sender_name: str
    sender: str
    subject: str
    gist: str
    tie: str
    thread_id: str
    received_at: str
    acted: bool
    updates: int = 0
    wants_act: bool = False


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db.company_path(db_path)))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def _one_line(value: str, cap: int) -> str:
    from openexecutive.delegation.ghostwriter import one_line

    return one_line(str(value or ""), cap)


def match_key(sender_name: str, subject: str) -> str:
    """The same item, however it reached the person: the sender's name and
    the start of the subject, letters and digits only, reply prefixes off."""
    name = re.sub(r"[^a-z0-9]", "", (sender_name or "").lower())
    words = _REPLY_PREFIX_RE.sub("", subject or "").lower()
    words = re.sub(r"[^a-z0-9]", "", words)[:_MATCH_SUBJECT_CHARS]
    return f"{name}|{words}" if name or words else ""


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _prune(conn: sqlite3.Connection, now: datetime) -> None:
    cutoff = _iso(now - KEEP)
    conn.execute(
        f"DELETE FROM {NOTICED_TABLE} WHERE noted_at < ? OR received_at < ?",  # noqa: S608 — constant table name
        (cutoff, cutoff),
    )


def prune(*, now: datetime | None = None, db_path: Path | None = None) -> None:
    """Delete what is older than ``KEEP``, for everyone, even when nothing
    new is written. Never raises."""
    try:
        conn = _connect(db_path)
        try:
            _prune(conn, now or datetime.now(UTC))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.warning("noticed: pruning failed", exc_info=True)


def note(
    person_id: int,
    *,
    source: str,
    item_key: str,
    sender_name: str = "",
    sender: str = "",
    subject: str = "",
    gist: str = "",
    tie: str = "",
    thread_id: str = "",
    received_at: datetime | None = None,
    refresh: bool = False,
    wants_act: bool = False,
    now: datetime | None = None,
    db_path: Path | None = None,
) -> bool:
    """Keep one item that came in for ``person_id``. False when it is
    already kept (by ``item_key``) unchanged, or another source already
    brought the same item (``match_key``, within ``MATCH_WINDOW``), or it
    couldn't be written. With ``refresh`` an item kept already whose
    subject or gist changed takes the new words and is new again (True)."""
    moment = now or datetime.now(UTC)
    arrived = received_at or moment
    key = (item_key or "").strip()[:ID_CHARS]
    if not key:
        return False
    name = _one_line(sender_name, 80)
    title = _one_line(subject, _FIELD_CHARS)
    matched = match_key(name, title)
    try:
        conn = _connect(db_path)
        try:
            _prune(conn, moment)
            if refresh:
                cursor = conn.execute(
                    f"UPDATE {NOTICED_TABLE} SET subject = ?, gist = ?, tie = ?, match_key = ?, "  # noqa: S608
                    "received_at = ?, acted_at = NULL, updates = updates + 1, wants_act = ? "
                    "WHERE person_id = ? AND item_key = ? AND (subject != ? OR gist != ?)",
                    (
                        title, _one_line(gist, _GIST_CHARS), _one_line(tie, _TIE_CHARS), matched,
                        _iso(arrived), int(wants_act), person_id, key, title, _one_line(gist, _GIST_CHARS),
                    ),
                )
                if cursor.rowcount > 0:
                    conn.commit()
                    return True
            if matched:
                same = conn.execute(
                    f"SELECT 1 FROM {NOTICED_TABLE} WHERE person_id = ? AND match_key = ? "  # noqa: S608
                    "AND source != ? AND received_at BETWEEN ? AND ? LIMIT 1",
                    (
                        person_id, matched, source,
                        _iso(arrived - MATCH_WINDOW), _iso(arrived + MATCH_WINDOW),
                    ),
                ).fetchone()
                if same is not None:
                    return False
            cursor = conn.execute(
                f"INSERT OR IGNORE INTO {NOTICED_TABLE} (person_id, source, item_key, match_key, "  # noqa: S608
                "sender_name, sender, subject, gist, tie, thread_id, received_at, noted_at, wants_act) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    person_id, source, key, matched, name, _one_line(sender, 160).lower(), title,
                    _one_line(gist, _GIST_CHARS), _one_line(tie, _TIE_CHARS), _one_line(thread_id, ID_CHARS),
                    _iso(arrived), _iso(moment), int(wants_act),
                ),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()
    except Exception:
        logger.warning("noticed: keeping an item failed", exc_info=True)
        return False


def set_gist(
    person_id: int, item_key: str, *, gist: str, tie: str = "", db_path: Path | None = None,
) -> None:
    """Fill in what ``item_key`` says and ties to, once it has been read."""
    try:
        conn = _connect(db_path)
        try:
            conn.execute(
                f"UPDATE {NOTICED_TABLE} SET gist = ?, tie = ? WHERE person_id = ? AND item_key = ?",  # noqa: S608
                (_one_line(gist, _GIST_CHARS), _one_line(tie, _TIE_CHARS), person_id, item_key),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.warning("noticed: saving a gist failed", exc_info=True)


def kept_keys(person_id: int, item_keys: list[str], *, db_path: Path | None = None) -> set[str]:
    """Which of ``item_keys`` are kept for ``person_id`` already."""
    if not item_keys:
        return set()
    try:
        conn = _connect(db_path)
        try:
            marks = ",".join("?" for _ in item_keys)
            rows = conn.execute(
                f"SELECT item_key FROM {NOTICED_TABLE} WHERE person_id = ? AND item_key IN ({marks})",  # noqa: S608
                (person_id, *item_keys),
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        logger.warning("noticed: reading kept items failed", exc_info=True)
        return set()
    return {str(r["item_key"]) for r in rows}


def recent(
    person_id: int,
    *,
    since: datetime | None = None,
    source: str | None = None,
    words: str = "",
    limit: int = MAX_LISTED,
    waiting: bool = False,
    now: datetime | None = None,
    db_path: Path | None = None,
) -> list[Noticed]:
    """``person_id``'s items, newest first (``waiting``: only those to act
    on that nothing acted on yet): since ``since`` (at most
    ``KEEP`` back), from ``source`` when given, holding all of ``words``
    in the sender, subject or gist when given."""
    moment = now or datetime.now(UTC)
    floor = max(since, moment - KEEP) if since is not None else moment - KEEP
    sql = (
        f"SELECT * FROM {NOTICED_TABLE} WHERE person_id = ? AND received_at >= ? "  # noqa: S608
    )
    params: list[object] = [person_id, _iso(floor)]
    if source:
        sql += "AND source = ? "
        params.append(source)
    if waiting:
        sql += "AND wants_act = 1 AND acted_at IS NULL "
    for word in words.lower().split()[:6]:
        sql += "AND LOWER(sender_name || ' ' || sender || ' ' || subject || ' ' || gist) LIKE ? "
        params.append(f"%{word}%")
    sql += "ORDER BY received_at DESC, id DESC LIMIT ?"
    params.append(max(1, min(int(limit), 200)))
    try:
        if not db.company_path(db_path).exists():
            return []
        conn = _connect(db_path)
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
    except Exception:
        logger.warning("noticed: listing items failed", exc_info=True)
        return []
    return [
        Noticed(
            source=str(r["source"]), item_key=str(r["item_key"]), sender_name=str(r["sender_name"]),
            sender=str(r["sender"]), subject=str(r["subject"]), gist=str(r["gist"]), tie=str(r["tie"]),
            thread_id=str(r["thread_id"]), received_at=str(r["received_at"]), acted=r["acted_at"] is not None,
            updates=int(r["updates"] or 0), wants_act=bool(r["wants_act"]),
        )
        for r in rows
    ]


def waiting_to_act(
    person_id: int, *, source: str | None = None, now: datetime | None = None, db_path: Path | None = None,
) -> list[Noticed]:
    """``person_id``'s items that something should be done about and that
    nothing acted on yet (a new one, or one whose turn failed), oldest first."""
    found = recent(person_id, source=source, limit=200, waiting=True, now=now, db_path=db_path)
    return list(reversed(found))


def claim_to_act(
    person_id: int, item_keys: list[str], *, now: datetime | None = None, db_path: Path | None = None,
) -> list[str]:
    """Mark ``item_keys`` acted on, and return the ones nothing acted on
    before: only those get a turn."""
    moment = _iso(now or datetime.now(UTC))
    claimed: list[str] = []
    try:
        conn = _connect(db_path)
        try:
            for key in item_keys:
                cursor = conn.execute(
                    f"UPDATE {NOTICED_TABLE} SET acted_at = ? "  # noqa: S608
                    "WHERE person_id = ? AND item_key = ? AND acted_at IS NULL",
                    (moment, person_id, key),
                )
                if cursor.rowcount > 0:
                    claimed.append(key)
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.warning("noticed: claiming items failed", exc_info=True)
        return []
    return claimed


def release(person_id: int, item_keys: list[str], *, db_path: Path | None = None) -> None:
    """Undo ``claim_to_act`` for a turn that failed before it did anything."""
    if not item_keys:
        return
    try:
        conn = _connect(db_path)
        try:
            marks = ",".join("?" for _ in item_keys)
            conn.execute(
                f"UPDATE {NOTICED_TABLE} SET acted_at = NULL "  # noqa: S608
                f"WHERE person_id = ? AND item_key IN ({marks})",
                (person_id, *item_keys),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.warning("noticed: releasing items failed", exc_info=True)


def forget(person_id: int, *, source: str | None = None, db_path: Path | None = None) -> int:
    """Delete what was kept for ``person_id``: from ``source`` alone when
    given (its switch turned off), otherwise everything."""
    try:
        if not db.company_path(db_path).exists():
            return 0
        conn = _connect(db_path)
        try:
            if source:
                cursor = conn.execute(
                    f"DELETE FROM {NOTICED_TABLE} WHERE person_id = ? AND source = ?",  # noqa: S608
                    (person_id, source),
                )
            else:
                cursor = conn.execute(
                    f"DELETE FROM {NOTICED_TABLE} WHERE person_id = ?",  # noqa: S608 — constant table name
                    (person_id,),
                )
            conn.commit()
            return int(cursor.rowcount or 0)
        finally:
            conn.close()
    except Exception:
        logger.warning("noticed: forgetting items failed", exc_info=True)
        return 0


__all__ = [
    "EMAIL", "KEEP", "MATCH_WINDOW", "Noticed", "claim_to_act", "forget", "kept_keys", "match_key",
    "note", "prune", "recent", "release", "set_gist", "waiting_to_act",
]
