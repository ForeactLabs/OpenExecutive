"""Home's "I can also take on": one list of real steps the Executive offers
to do next, for one person.

Every offer is one step from a short fixed list (``KINDS``: an email, a
follow-up, a workflow, a watch, a doc, a meeting, a message to a teammate,
a change in a connected app), shown with where it came from and exactly
what it will touch (``preview``). The list is built here from three places,
plus any registered with ``register_source``:

- ``made``: what the daily pass (``briefing.take_on_maker``) found in the
  person's own mail, calls, chats, work list, goals and connected apps;
- ``repeat``: an ask they made on several days (``workflows.repeat_asks``),
  offered as a workflow;
- ``watch``: a watch the research pass suggested and was unsure of
  (``monitoring.store.list_pending_suggestions``).

What the daily pass reads and when it may run can be widened the same way
(``hooks``): a deployment that knows more about a person (a teammate's own
calendar, checks of connected business apps, a switch for background work)
sets those hooks, and everything else stays as it is here.

One state table records what each person did with each offer: Do it (its
run, and what came of it), Or tell me (the offer as they changed it), and
Hide with a reason. Hide learns across the whole list: a kind of step
turned down as not useful twice in ``LEARN_DAYS`` stops being offered for
that long; Already done keeps that offer away; Later holds it for
``LATER_DAYS``. And whatever was turned down, done or set aside is
remembered by what it is about, not by its words (``close_to``): the same
idea, reworded or offered as another kind of step, is never offered again
(``about_already``, read by the list and by the daily pass). An offer
the pass wrote that nobody used leaves ``KEEP_DAYS`` after it was first
shown, so nothing piles up. One from a place that still has it (a repeat
ask, a pending watch suggestion, a registered source) stays while that
place still offers it.

Nothing here decides whether a step runs or waits. Do it starts one chat
turn as the person (``api.routes.take_on``), so the step meets the same
role, Approvals level, Your rules and In training as anything they type.

Privacy: rows are keyed by the person's email and only ever read for them.
Reads never raise into a page: an unreadable store reads as no offers.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from openexecutive.config import background_turn_limit
from openexecutive.utils import db

logger = logging.getLogger(__name__)

OFFERS_TABLE = "take_on_offers"
STATE_TABLE = "take_on_state"
PASSES_TABLE = "take_on_passes"

Kind = Literal["email", "followup", "workflow", "watch", "doc", "meeting", "message", "app"]
KINDS: tuple[Kind, ...] = ("email", "followup", "workflow", "watch", "doc", "meeting", "message", "app")
# The chip on each offer, in words.
KIND_LABEL: dict[str, str] = {
    "email": "Email",
    "followup": "Follow-up",
    "workflow": "Workflow",
    "watch": "Watch",
    "doc": "Doc",
    "meeting": "Meeting",
    "message": "Message",
    "app": "App",
}

# Where an offer came from: the three here, or a registered source's name.
Origin = str
ORIGINS: tuple[str, ...] = ("made", "repeat", "watch")
# "card": the step went to a card in Needs you, as the gate decided.
Status = Literal["", "working", "done", "failed", "card", "asked"]
# "skipped": Skip on the card the step went to; not counted as turned down.
HideReason = Literal["not_useful", "already_done", "later", "skipped"]

# An offer nobody used leaves this long after it was first shown.
KEEP_DAYS = 3
# A finished one stays, with what came of it, for a day.
DONE_DAYS = 1
# Later holds an offer for a week.
LATER_DAYS = 7
# Not useful twice on one kind of step, within this many days, stops that
# kind for as long.
LEARN_DAYS = 30
LEARN_AFTER = 2
# A run still "working" past ``config.background_turn_limit`` lost its turn
# (a restart). It may have done part of the step, so its chat is the place to look.
STALE_OUTCOME = "It stopped before it finished. Check its chat before trying again."
# What Do it says before the step, in the chat it starts as the person.
DO_PREFIX = (
    "I pressed Do it on my Home page, so this is my go-ahead: do it now, without asking me to "
    "confirm the plan first. Do exactly this one step, as shown, and nothing more. If something "
    "truly needs me, ask only that. Then tell me in a line what you did.\n\n"
)

TITLE_MAX = 200
LABEL_MAX = 24
VALUE_MAX = 300
SOURCE_MAX = 80
PREVIEW_MAX = 4
# Two offers are about the same thing when they share at least this share
# of the words that say what the shorter one is about, and at least this
# many of them.
SAME_SHARE = 0.55
SAME_WORDS = 4
# Words that say how or what kind of step, not what it is about.
_FILLER = frozenset(
    ["a", "an", "the", "and", "or", "but", "of", "to", "in", "on", "at", "by", "for", "with", "from", "into", "about", "as", "is", "are", "was", "were", "be", "been", "it", "its", "this", "that", "these", "those", "so", "than", "then", "there", "their", "them", "they", "he", "she", "his", "her", "him", "you", "your", "yours", "we", "our", "us", "me", "my", "can", "will", "would", "could", "should", "may", "might", "do", "does", "did", "done", "not", "no", "yes", "if", "when", "whether", "which", "who", "whom", "what", "how", "why", "where", "up", "out", "over", "under", "again", "any", "all", "each", "some", "more", "most", "other", "such", "only", "own", "same", "too", "very", "just", "also", "still", "next", "new", "now", "get", "got", "let", "know", "need", "needs", "make", "made", "set", "keep", "check", "checks", "run", "runs", "send", "ask", "asks", "tell", "tells", "see", "find", "chase", "email", "emails", "message", "messages", "book", "watch", "watches", "write", "draft", "doc", "meeting", "app", "change", "update", "flag", "flags", "confirm", "ready", "weekly", "every", "week", "monday", "day", "days", "time", "once", "before", "after", "since", "against", "toward", "towards", "track", "tracked", "tracking", "daily", "monthly", "anything", "something", "everything", "eye", "com", "www", "http", "https"]
)
# An offer's key: its origin, then its id there.
KEY_RE = re.compile(r"^[a-z][a-z_]{0,23}:[A-Za-z0-9_.\-]{1,80}$")


@dataclass
class Offer:
    """One offer, as Home shows it."""

    key: str
    origin: Origin
    kind: Kind
    title: str
    source: str
    source_detail: str = ""
    preview: list[tuple[str, str]] = field(default_factory=list)
    created_at: str = ""
    # What happened since they pressed Do it.
    status: Status = ""
    outcome: str = ""
    # What it did, from the run's own record: (what, link to see it).
    done: list[tuple[str, str]] = field(default_factory=list)
    session_id: str | None = None
    decision_id: int | None = None
    # Or tell me: what they said, and that the offer is their changed one.
    said: str = ""
    changed: bool = False
    finished_at: str = ""
    # When this Do it began (set by ``start``): its run writes what came of
    # it only while the row is still on that run.
    started_at: str = ""


@dataclass
class Source:
    """Another place offers come from (``register_source``). ``read`` gives
    the offers for one person (their People id, and whether they are the
    company's main person); ``taken_up`` hears that one finished done
    (directly, or once its card was approved); ``set_aside`` hears that one was hidden (not useful or
    already done), so the place it came from stops offering it too. Each is
    called inside a try: a failing source reads as no offers."""

    origin: str
    read: Callable[[int | None, bool], list[Offer]]
    taken_up: Callable[[str], None] | None = None
    set_aside: Callable[[str], None] | None = None


_sources: dict[str, Source] = {}


def register_source(source: Source) -> None:
    """Add (or replace) a place offers come from. Its offers' keys must
    start with ``<origin>:``."""
    if not re.fullmatch(r"[a-z][a-z_]{0,23}", source.origin) or source.origin in ORIGINS:
        raise ValueError(f"take_on: bad source name {source.origin!r}")
    _sources[source.origin] = source


def source_for(key: str) -> Source | None:
    return _sources.get(key.split(":", 1)[0])


def _no_apps(principal: bool) -> tuple[list[str], list[str]]:
    return [], []


def _not_paused() -> bool:
    from openexecutive.scheduler.pause import is_paused

    return not is_paused()


@dataclass
class Hooks:
    """What a deployment may widen for the daily pass
    (``briefing.take_on_maker``). The defaults read only what every install
    has.

    - ``background_on``: whether background model calls may run now (by
      default, unless the Executive is paused);
    - ``background_model``: the model for them (None: the fast model);
    - ``watching_on``: whether the watch list is being checked, so a watch
      already on it runs on its own;
    - ``teammate_signals``: a teammate's own mail and calendar
      (``briefing.live_signals.LiveSignals``) for ``(person, since, now)``,
      or None: without it a teammate's pass reads their chats, cards, goals
      and team only;
    - ``apps``: (connected business app names, what their checks found
      lately) for the main person or a teammate."""

    background_on: Callable[[], bool] = _not_paused
    background_model: Callable[[], str | None] = lambda: None
    watching_on: Callable[[], bool] = lambda: True
    teammate_signals: Callable[[Any, datetime, datetime], Any] | None = None
    apps: Callable[[bool], tuple[list[str], list[str]]] = _no_apps


hooks = Hooks()


_db = db.company_path


_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {OFFERS_TABLE} (
  person TEXT NOT NULL,
  key TEXT NOT NULL,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  source TEXT NOT NULL,
  source_detail TEXT NOT NULL DEFAULT '',
  preview TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  PRIMARY KEY (person, key)
);
CREATE TABLE IF NOT EXISTS {STATE_TABLE} (
  person TEXT NOT NULL,
  key TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT '',
  first_seen TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT '',
  session_id TEXT,
  started_at TEXT,
  finished_at TEXT,
  outcome TEXT NOT NULL DEFAULT '',
  done TEXT NOT NULL DEFAULT '[]',
  decision_id INTEGER,
  changed TEXT,
  said TEXT NOT NULL DEFAULT '',
  hidden_reason TEXT NOT NULL DEFAULT '',
  hidden_at TEXT,
  offer TEXT,
  PRIMARY KEY (person, key)
);
CREATE TABLE IF NOT EXISTS {PASSES_TABLE} (
  person TEXT PRIMARY KEY,
  last_at TEXT NOT NULL,
  input_hash TEXT NOT NULL DEFAULT ''
);
"""

# Rows by column name, committed when the block ends cleanly, always closed.
_connect = db.opener(lambda db_path: _db(db_path), schema=_SCHEMA)


def who(email: str) -> str:
    return (email or "").strip().lower()


def _now() -> datetime:
    return datetime.now(UTC)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        at = datetime.fromisoformat(value)
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def line(value: Any, limit: int) -> str:
    """One printable line, cut to ``limit``."""
    text = " ".join(str(value or "").split())
    text = "".join(c for c in text if c.isprintable())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def clean_preview(items: Any) -> list[tuple[str, str]]:
    """``[[label, value], …]`` (or dicts) as clean pairs, at most ``PREVIEW_MAX``."""
    out: list[tuple[str, str]] = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict):
            label, value = item.get("label"), item.get("value")
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            label, value = item
        else:
            continue
        label_text, value_text = line(label, LABEL_MAX), line(value, VALUE_MAX)
        if label_text and value_text:
            out.append((label_text, value_text))
        if len(out) >= PREVIEW_MAX:
            break
    return out


def about(title: str, preview: list[tuple[str, str]] | None = None) -> str:
    """What an offer is about, in words: its title and what it touches."""
    return " ".join([title, *(value for _, value in preview or [])])


def _words(text: str) -> set[str]:
    out: set[str] = set()
    # An email address is one word: its pieces say nothing on their own. A
    # time of day says when, not what.
    text = re.sub(r"\b\d{1,2}:\d{2}\b", " ", text.lower())
    for w in re.findall(r"[a-z0-9._%+\-]{1,64}@[a-z0-9.\-]{1,253}\.[a-z]{2,24}|[a-z0-9]+", text):
        w = w[:-1] if len(w) > 4 and w.endswith("s") and "@" not in w else w
        if w in _FILLER or (len(w) < 3 and not any(c.isdigit() for c in w)):
            continue
        out.add(w)
    return out


def close_to(text: str, others: list[str]) -> bool:
    """Whether ``text`` is about the same thing as any of ``others``, however
    it is worded and whatever kind of step it is: most of the words that say
    what it is about (names, things, numbers) are shared, and enough of them
    that one shared name (a product, a person) is not enough on its own."""
    mine = _words(text)
    for theirs in map(_words, others):
        shared = len(mine & theirs)
        if shared >= SAME_WORDS and shared >= SAME_SHARE * min(len(mine), len(theirs)):
            return True
    return False


def made_key(title: str) -> str:
    """The key of a made offer: its title, normalised, so the same offer
    found again is the same row (and a hidden one stays hidden)."""
    norm = " ".join(re.sub(r"[^a-z0-9 ]", " ", title.lower()).split())
    return "made:" + hashlib.sha256(norm.encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# Made offers (the daily pass)
# --------------------------------------------------------------------------- #


def put_made(email: str, offers: list[Offer], *, db_path: Path | None = None) -> int:
    """Keep the pass's offers for ``email``. One already kept keeps its row
    (and when it was made), so a pass never makes an offer young again.
    Returns how many were new."""
    person = who(email)
    if not person:
        return 0
    now = _now().isoformat()
    added = 0
    with _connect(db_path) as conn:
        _prune(conn, person, _now())
        for offer in offers:
            cur = conn.execute(
                f"INSERT OR IGNORE INTO {OFFERS_TABLE} "  # noqa: S608 — constant table name
                "(person, key, kind, title, source, source_detail, preview, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (person, offer.key, offer.kind, line(offer.title, TITLE_MAX), line(offer.source, SOURCE_MAX),
                 line(offer.source_detail, SOURCE_MAX), json.dumps(offer.preview), now),
            )
            added += cur.rowcount
    return added


def _prune(conn: sqlite3.Connection, person: str, now: datetime) -> None:
    """Drop what nothing reads any more: offers and their rows untouched for
    ``LEARN_DAYS``, the longest any read looks back. A step still running or
    on a card stays until it ends (a card they skipped has ended)."""
    since = (now - timedelta(days=LEARN_DAYS)).isoformat()
    conn.execute(
        f"DELETE FROM {STATE_TABLE} WHERE person = ? "  # noqa: S608
        "AND NOT (status IN ('working', 'card') AND hidden_reason = '') "
        "AND MAX(first_seen, COALESCE(started_at, ''), COALESCE(finished_at, ''), COALESCE(hidden_at, '')) < ?",
        (person, since),
    )
    conn.execute(
        f"DELETE FROM {OFFERS_TABLE} WHERE person = ? AND created_at < ? "  # noqa: S608
        f"AND key NOT IN (SELECT key FROM {STATE_TABLE} WHERE person = ?)",
        (person, since, person),
    )


def _made(person: str, now: datetime, db_path: Path | None) -> list[Offer]:
    # Long enough for one put off with Later on its last shown day to come
    # back for its days: shown (KEEP_DAYS), away (LATER_DAYS), back
    # (KEEP_DAYS), then done for a day.
    since = (now - timedelta(days=LATER_DAYS + 2 * KEEP_DAYS + DONE_DAYS)).isoformat()
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM {OFFERS_TABLE} WHERE person = ? AND created_at >= ? ORDER BY created_at DESC",  # noqa: S608
            (person, since),
        ).fetchall()
    out: list[Offer] = []
    for r in rows:
        kind = r["kind"] if r["kind"] in KINDS else "doc"
        try:
            preview = clean_preview(json.loads(r["preview"]))
        except ValueError:
            preview = []
        out.append(Offer(
            key=r["key"], origin="made", kind=kind, title=r["title"], source=r["source"],  # type: ignore[arg-type]
            source_detail=r["source_detail"], preview=preview, created_at=r["created_at"],
        ))
    return out


def last_pass(email: str, *, db_path: Path | None = None) -> tuple[datetime | None, str]:
    """When the daily pass last ran for ``email``, and over what."""
    try:
        with _connect(db_path) as conn:
            row = conn.execute(
                f"SELECT last_at, input_hash FROM {PASSES_TABLE} WHERE person = ?", (who(email),),  # noqa: S608
            ).fetchone()
    except (sqlite3.Error, OSError):
        logger.warning("take_on: couldn't read the last pass", exc_info=True)
        return None, ""
    return (_parse(row["last_at"]), row["input_hash"]) if row else (None, "")


def record_pass(email: str, input_hash: str, *, db_path: Path | None = None) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            f"INSERT INTO {PASSES_TABLE} (person, last_at, input_hash) VALUES (?, ?, ?) "  # noqa: S608
            "ON CONFLICT(person) DO UPDATE SET last_at = excluded.last_at, input_hash = excluded.input_hash",
            (who(email), _now().isoformat(), input_hash),
        )


# --------------------------------------------------------------------------- #
# The other places offers come from
# --------------------------------------------------------------------------- #


_DAYS = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday", "fri": "Friday",
         "sat": "Saturday", "sun": "Sunday"}


def cadence_words(cadence: str) -> str:
    """"every Monday at 8 AM" for ``weekly@mon@13:00`` (UTC), in the
    company's time; "on a schedule" when unreadable."""
    parts = cadence.split("@")
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", parts[-1] if parts else "")
    if not m or parts[0] not in ("daily", "weekly") or (parts[0] == "weekly" and len(parts) != 3):
        return "on a schedule"
    try:
        from openexecutive.memory.workspace_settings import get_user_timezone

        tz: tzinfo = get_user_timezone()
    except Exception:
        tz = UTC
    now = _now()
    at = now.replace(hour=int(m.group(1)) % 24, minute=int(m.group(2)) % 60, second=0, microsecond=0)
    if parts[0] == "weekly":
        day = list(_DAYS).index(parts[1].lower()) if parts[1].lower() in _DAYS else -1
        if day < 0:
            return "on a schedule"
        at += timedelta(days=(day - at.weekday()) % 7)
    local = at.astimezone(tz)
    hour = local.strftime("%I").lstrip("0") + (f":{local:%M}" if local.minute else "") + f" {local:%p}"
    return f"every {local:%A} at {hour}" if parts[0] == "weekly" else f"every day at {hour}"


def _repeat(person_id: int | None) -> list[Offer]:
    """An ask made on several days, as a workflow (``workflows.repeat_asks``)."""
    if person_id is None:
        return []
    from openexecutive.workflows import repeat_asks

    out: list[Offer] = []
    for o in repeat_asks.open_offers(person_id):
        ask = line(o.get("ask"), 160)
        cadence = cadence_words(str(o.get("cadence") or ""))
        days = len(o.get("asked_at") or [])
        out.append(Offer(
            key=f"repeat:{int(o['id'])}", origin="repeat", kind="workflow",
            title=line(f"I can do “{ask}” {cadence}, on my own", TITLE_MAX),
            source="Your chats",
            source_detail=f"You asked on {days} different days" if days > 1 else "You asked for it",
            preview=[("Does", ask), ("When", cadence), ("Sends to", "you")],
        ))
    return out


def _watch(person_id: int | None, principal: bool) -> list[Offer]:
    """A watch the research pass suggested and was unsure of. One routed to
    a department's head is that head's to take on; the rest are the
    principal's (``route_to_person_id`` None)."""
    from openexecutive.monitoring import store as ms

    out: list[Offer] = []
    for item in ms.list_pending_suggestions():
        if item.id is None:
            continue
        if item.route_to_person_id is None and not principal:
            continue
        if item.route_to_person_id is not None and item.route_to_person_id != person_id:
            continue
        # A short note names what it watches ("Diesel prices"). A note that is
        # a sentence ("Stripe Apple Pay errors went unseen by...") read garbled
        # after "I can watch" (11-25): the title names the page then, and the
        # sentence is its own Why line.
        notes = " ".join((item.notes or "").split())
        named = bool(notes) and len(notes) <= 60 and not re.search(r"[.!?]", notes)
        what = notes if named else re.sub(r"^https?://(www\.)?", "", item.target.strip()).rstrip("/")
        preview = [("Checks", line(item.target, VALUE_MAX))]
        if notes and not named:
            preview.append(("Why", line(notes, VALUE_MAX)))
        preview.append(("Tells you", "when something big changes"))
        out.append(Offer(
            key=f"watch:{item.slug}", origin="watch", kind="watch",
            # As written: a note can start with a name ("Stripe changelog",
            # "AWS status page").
            title=line(f"I can watch {what or item.target}", TITLE_MAX),
            source="News I watch",
            source_detail="I found it while checking for you",
            preview=preview,
        ))
    return out


# --------------------------------------------------------------------------- #
# State: Do it, Or tell me, Hide
# --------------------------------------------------------------------------- #


def _states(person: str, db_path: Path | None) -> dict[str, sqlite3.Row]:
    with _connect(db_path) as conn:
        rows = conn.execute(f"SELECT * FROM {STATE_TABLE} WHERE person = ?", (person,)).fetchall()  # noqa: S608
    return {r["key"]: r for r in rows}


def _ensure(conn: sqlite3.Connection, person: str, key: str, kind: str, now: datetime) -> None:
    conn.execute(
        f"INSERT OR IGNORE INTO {STATE_TABLE} (person, key, kind, first_seen) VALUES (?, ?, ?, ?)",  # noqa: S608
        (person, key, kind, now.isoformat()),
    )


def turned_down_kinds(email: str, *, db_path: Path | None = None, now: datetime | None = None) -> set[str]:
    """Kinds of step this person called not useful ``LEARN_AFTER`` times in
    the last ``LEARN_DAYS``: none of that kind is offered for now."""
    now = now or _now()
    since = (now - timedelta(days=LEARN_DAYS)).isoformat()
    try:
        with _connect(db_path) as conn:
            rows = conn.execute(
                f"SELECT kind, COUNT(*) AS n FROM {STATE_TABLE} "  # noqa: S608 — constant table name
                "WHERE person = ? AND hidden_reason = 'not_useful' AND hidden_at >= ? GROUP BY kind",
                (who(email), since),
            ).fetchall()
    except (sqlite3.Error, OSError):
        logger.warning("take_on: couldn't read what was turned down", exc_info=True)
        return set()
    return {r["kind"] for r in rows if int(r["n"]) >= LEARN_AFTER and r["kind"]}


def recent_titles(email: str, *, db_path: Path | None = None, days: int = LEARN_DAYS) -> dict[str, list[str]]:
    """What this person already did or turned down lately, for the daily
    pass to leave out: ``{"done": […], "not_useful": […], "already_done": […],
    "later": […], "offered": […]}``. ``later`` is set aside and still away;
    ``offered`` is on the list now, so a top-up offers something else."""
    now = _now()
    since = (now - timedelta(days=days)).isoformat()
    out: dict[str, list[str]] = {"done": [], "not_useful": [], "already_done": [], "later": [], "offered": []}
    try:
        with _connect(db_path) as conn:
            rows = conn.execute(
                f"SELECT s.status, s.hidden_reason, s.hidden_at, o.title FROM {STATE_TABLE} s "  # noqa: S608
                f"JOIN {OFFERS_TABLE} o ON o.person = s.person AND o.key = s.key "
                "WHERE s.person = ? AND (s.finished_at >= ? OR s.hidden_at >= ?)",
                (who(email), since, since),
            ).fetchall()
    except (sqlite3.Error, OSError):
        return out
    away = (now - timedelta(days=LATER_DAYS)).isoformat()
    for r in rows:
        if r["hidden_reason"] in ("not_useful", "already_done"):
            out[r["hidden_reason"]].append(r["title"])
        elif r["hidden_reason"] == "later" and (r["hidden_at"] or "") >= away:
            out["later"].append(r["title"])
        elif r["status"] == "done":
            out["done"].append(r["title"])
    out["offered"] = [title for _, title in on_list(email, db_path=db_path)]
    return out


def _row_about(r: sqlite3.Row) -> str:
    """What a state row's offer is about: as they changed it, else as Do it
    or Hide found it, else as the pass made it."""
    for raw in (r["changed"], r["offer"]):
        try:
            d = json.loads(raw or "")
        except ValueError:
            continue
        if isinstance(d, dict) and line(d.get("title"), TITLE_MAX):
            return about(line(d.get("title"), TITLE_MAX), clean_preview(d.get("preview")))
    if r["title"]:
        try:
            preview = clean_preview(json.loads(r["preview"] or "[]"))
        except ValueError:
            preview = []
        return about(r["title"], preview)
    return ""


def _put_aside(person: str, now: datetime, db_path: Path | None) -> dict[str, str]:
    """key -> what it is about, for each offer this person turned down
    (Not useful, Already done, Skip on its card) or did lately, set aside
    with Later and still away, or pressed Do it on."""
    since = (now - timedelta(days=LEARN_DAYS)).isoformat()
    away = (now - timedelta(days=LATER_DAYS)).isoformat()
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT s.key, s.changed, s.offer, o.title, o.preview FROM {STATE_TABLE} s "  # noqa: S608
            f"LEFT JOIN {OFFERS_TABLE} o ON o.person = s.person AND o.key = s.key "
            "WHERE s.person = ? AND ("
            "(s.hidden_reason IN ('not_useful', 'already_done', 'skipped') AND s.hidden_at >= ?) "
            "OR (s.hidden_reason = 'later' AND s.hidden_at >= ?) "
            "OR (s.hidden_reason = '' AND s.status != '' AND COALESCE(s.finished_at, s.started_at, s.first_seen) >= ?))",
            (person, since, away, since),
        ).fetchall()
    return {r["key"]: text for r in rows if (text := _row_about(r))}


def about_already(email: str, *, db_path: Path | None = None) -> list[str]:
    """What each offer this person turned down, did, set aside or has on
    the list now is about, so the daily pass offers none of them again in
    other words (``close_to``). Never raises."""
    person, now = who(email), _now()
    try:
        aside = list(_put_aside(person, now, db_path).values())
        states = _states(person, db_path)
        shown = [_apply(m, states.get(m.key), now) for m in _made(person, now, db_path)]
    except (sqlite3.Error, OSError):
        logger.warning("take_on: couldn't read what was already offered", exc_info=True)
        return []
    return aside + [about(o.title, o.preview) for o in shown if o is not None and not o.status]


def on_list(email: str, *, db_path: Path | None = None) -> list[tuple[str, str]]:
    """(kind, title) of each offer on the person's list now that nobody has
    pressed a button on, by the list's own rules: what a pass counts so the
    list mixes its kinds."""
    now = _now()
    try:
        states = _states(who(email), db_path)
        shown = [_apply(m, states.get(m.key), now) for m in _made(who(email), now, db_path)]
    except (sqlite3.Error, OSError):
        return []
    return [(o.kind, o.title) for o in shown if o is not None and not o.status]


def _link(url: Any) -> str:
    """A link the card may open: one of the app's own pages, or https. A
    backslash or a control character is never in one: a browser reads
    ``/\\host`` as another site."""
    text = str(url or "").strip()
    if not text or "\\" in text or any(not c.isprintable() or c.isspace() for c in text):
        return ""
    parts = urlsplit(text)
    if text.startswith("/") and not parts.scheme and not parts.netloc and not text.startswith("//"):
        return text
    return text if parts.scheme == "https" and parts.netloc else ""


def _done_list(raw: str | None) -> list[tuple[str, str]]:
    try:
        items = json.loads(raw or "[]")
    except ValueError:
        return []
    out: list[tuple[str, str]] = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, list) and len(item) == 2:
            out.append((line(item[0], 160), _link(item[1])))
    return [d for d in out if d[0]][:4]


def _apply(offer: Offer, state: sqlite3.Row | None, now: datetime) -> Offer | None:
    """The offer as this person left it, or None when it's gone for them."""
    if state is None:
        made = _parse(offer.created_at) if offer.created_at else None
        return None if made and now - made > timedelta(days=KEEP_DAYS) else offer
    seen = _parse(state["first_seen"])
    if state["hidden_reason"]:
        hidden = _parse(state["hidden_at"])
        if state["hidden_reason"] != "later" or (hidden and now - hidden < timedelta(days=LATER_DAYS)):
            return None
        # Back from Later: it gets its days on the list again from now.
        if hidden:
            seen = hidden + timedelta(days=LATER_DAYS)
    if state["changed"]:
        try:
            changed = json.loads(state["changed"])
            offer.title = line(changed.get("title"), TITLE_MAX) or offer.title
            offer.preview = clean_preview(changed.get("preview")) or offer.preview
            if changed.get("kind") in KINDS:
                offer.kind = changed["kind"]
            offer.changed = True
            offer.said = state["said"] or ""
        except ValueError:
            pass
    status = state["status"] or ""
    if status == "working":
        began = _parse(state["started_at"])
        if began and now - began > background_turn_limit():
            status, offer.outcome = "failed", STALE_OUTCOME
    if status in ("done", "failed", "asked"):
        # A run that went stale never finished: it ages from when it began.
        ended = _parse(state["finished_at"]) or _parse(state["started_at"])
        if ended and now - ended > timedelta(days=DONE_DAYS):
            return None
    offer.status = status  # type: ignore[assignment]
    offer.outcome = offer.outcome or state["outcome"] or ""
    offer.done = _done_list(state["done"])
    offer.session_id = state["session_id"]
    offer.decision_id = state["decision_id"]
    if not status and offer.origin == "made" and seen and now - seen > timedelta(days=KEEP_DAYS):
        return None
    return offer


def offers_for(
    email: str,
    person_id: int | None,
    *,
    principal: bool,
    db_path: Path | None = None,
    now: datetime | None = None,
) -> list[Offer]:
    """Everything ``email`` is offered now, best first. Marks each as seen,
    so it leaves ``KEEP_DAYS`` on."""
    now = now or _now()
    person = who(email)
    if not person:
        return []
    candidates: list[Offer] = []
    reads: list[tuple[str, Callable[[], list[Offer]]]] = [
        ("made", lambda: _made(person, now, db_path)),
        ("repeat", lambda: _repeat(person_id)),
        ("watch", lambda: _watch(person_id, principal)),
    ]
    reads += [(s.origin, lambda s=s: s.read(person_id, principal)) for s in list(_sources.values())]  # type: ignore[misc]
    for name, read in reads:
        try:
            candidates.extend(o for o in read() if o.key.startswith(f"{o.origin}:") and o.origin == name)
        except Exception:
            logger.warning("take_on: couldn't read %s offers", name, exc_info=True)
    try:
        states = _states(person, db_path)
        quiet = turned_down_kinds(person, db_path=db_path, now=now)
        aside = _put_aside(person, now, db_path)
    except (sqlite3.Error, OSError):
        logger.warning("take_on: couldn't read the state", exc_info=True)
        return []
    out: list[Offer] = []
    seen_keys: set[str] = set()
    # What each untouched offer kept so far is about: one about the same
    # thing as another on the list shows once. One they started or changed
    # with Or tell me is theirs now and always shows.
    listed: list[str] = []
    for offer in candidates:
        if offer.key in seen_keys:
            continue
        seen_keys.add(offer.key)
        state = states.get(offer.key)
        untouched = not (state is not None and (state["status"] or state["changed"]))
        # A watch suggestion is answered one by one (a hide declines that
        # one on the Watch list), so a kind turned down never quiets the
        # others still waiting there.
        if offer.kind in quiet and untouched and offer.origin != "watch":
            continue
        kept = _apply(offer, state, now)
        if kept is None:
            continue
        if untouched:
            what = about(kept.title, kept.preview)
            if close_to(what, [t for k, t in aside.items() if k != kept.key] + listed):
                continue
            listed.append(what)
        out.append(kept)
    # One they pressed Do it on stays with what came of it, even when the
    # place it came from has moved on.
    for key, state in states.items():
        if key in seen_keys or not state["status"] or state["hidden_reason"]:
            continue
        if (snap := _from_snapshot(key, state["offer"])) is not None and (kept := _apply(snap, state, now)):
            out.append(kept)
    fresh = [o for o in out if o.key not in states]
    if fresh:
        try:
            with _connect(db_path) as conn:
                for o in fresh:
                    _ensure(conn, person, o.key, o.kind, now)
        except (sqlite3.Error, OSError):
            logger.warning("take_on: couldn't mark offers seen", exc_info=True)
    rank = {"working": 0, "card": 0, "asked": 0, "failed": 1, "": 2, "done": 3}
    # Within a status, the places offers come from take turns (the pass's
    # first, then a repeat ask, then a watch suggestion, …), so Home's few
    # show one of each that is waiting.
    turn: dict[str, int] = {}
    order: dict[str, int] = {}
    for o in out:
        group = f"{rank.get(o.status, 2)}:{o.origin}"
        order[o.key] = turn.get(group, 0)
        turn[group] = order[o.key] + 1
    out.sort(key=lambda o: (rank.get(o.status, 2), order[o.key]))
    return out


def find(email: str, key: str, person_id: int | None, *, principal: bool, db_path: Path | None = None) -> Offer | None:
    """One of ``email``'s current offers, by key."""
    if not KEY_RE.match(key):
        return None
    offers = offers_for(email, person_id, principal=principal, db_path=db_path)
    return next((o for o in offers if o.key == key), None)


def _snapshot(offer: Offer) -> str:
    """The offer as Do it found it, so it keeps showing what came of it
    after the place it came from has moved on (a repeat ask taken up)."""
    return json.dumps({
        "origin": offer.origin, "kind": offer.kind, "title": offer.title, "source": offer.source,
        "source_detail": offer.source_detail, "preview": [list(p) for p in offer.preview],
    })


def _from_snapshot(key: str, raw: str | None) -> Offer | None:
    try:
        d = json.loads(raw or "")
    except ValueError:
        return None
    origin = key.split(":", 1)[0]
    if not isinstance(d, dict) or d.get("origin") != origin or not line(d.get("title"), TITLE_MAX):
        return None
    return Offer(
        key=key, origin=origin, kind=d["kind"] if d.get("kind") in KINDS else "doc",
        title=line(d.get("title"), TITLE_MAX), source=line(d.get("source"), 80),
        source_detail=line(d.get("source_detail"), 120), preview=clean_preview(d.get("preview")),
    )


def start(email: str, offer: Offer, *, db_path: Path | None = None) -> bool:
    """Record Do it. False when it's already working, on a card, or done. A
    run left working past ``config.background_turn_limit`` shows as failed, so it can start
    again. On a start, ``offer.started_at`` names this run."""
    person, now = who(email), _now()
    stale = (now - background_turn_limit()).isoformat()
    with _connect(db_path) as conn:
        _ensure(conn, person, offer.key, offer.kind, now)
        cur = conn.execute(
            f"UPDATE {STATE_TABLE} SET status = 'working', started_at = ?, finished_at = NULL, "  # noqa: S608
            "outcome = '', done = '[]', session_id = NULL, decision_id = NULL, offer = ?, "
            "hidden_reason = '', hidden_at = NULL "
            "WHERE person = ? AND key = ? AND (status NOT IN ('working', 'card', 'done') "
            "OR (status = 'working' AND started_at < ?))",
            (now.isoformat(), _snapshot(offer), person, offer.key, stale),
        )
        if cur.rowcount > 0:
            offer.started_at = now.isoformat()
        return cur.rowcount > 0


def finished(email: str, since: datetime, *, db_path: Path | None = None) -> list[Offer]:
    """What ``email`` started from the list and that finished done since
    ``since``, newest first. Done steps leave the list and show in Done for
    you instead. Never raises."""
    person = who(email)
    if not person:
        return []
    try:
        with _connect(db_path) as conn:
            rows = conn.execute(
                f"SELECT key, offer, changed, outcome, done, session_id, decision_id, started_at, finished_at FROM {STATE_TABLE} "  # noqa: S608
                "WHERE person = ? AND status = 'done' AND finished_at >= ? ORDER BY finished_at DESC",
                (person, since.isoformat()),
            ).fetchall()
    except (sqlite3.Error, OSError):
        logger.warning("take_on: couldn't read finished steps", exc_info=True)
        return []
    out: list[Offer] = []
    for r in rows:
        offer = _from_snapshot(r["key"], r["offer"])
        if offer is None:
            continue
        try:
            changed = json.loads(r["changed"] or "{}")
        except ValueError:
            changed = {}
        if isinstance(changed, dict):
            offer.title = line(changed.get("title"), TITLE_MAX) or offer.title
        offer.status = "done"
        offer.outcome = r["outcome"] or ""
        offer.done = _done_list(r["done"])
        offer.session_id = r["session_id"]
        offer.decision_id = r["decision_id"]
        offer.started_at = r["started_at"] or ""
        offer.finished_at = r["finished_at"] or ""
        out.append(offer)
    return out


def set_session(email: str, key: str, session_id: str, *, db_path: Path | None = None) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            f"UPDATE {STATE_TABLE} SET session_id = ? WHERE person = ? AND key = ?",  # noqa: S608
            (session_id, who(email), key),
        )


def finish(
    email: str,
    key: str,
    status: Status,
    *,
    outcome: str = "",
    done: list[tuple[str, str]] | None = None,
    decision_id: int | None = None,
    from_card: int | None = None,
    from_run: str | None = None,
    db_path: Path | None = None,
) -> bool:
    """What came of Do it: done (with what it did), on a card for them
    (the gate's), asked them a question back (with the question), or failed
    (with why). With ``from_card``, only while the row is still on that
    card, so two readers settling the same card write it once. With
    ``from_run`` (the run's ``started_at``), only while that run is still the
    one working, so a run that ran late and was started again can't write
    over the new one. Returns whether a row changed. Never raises."""
    where = "WHERE person = ? AND key = ?"
    args: list[Any] = [who(email), key]
    if from_card is not None:
        where += " AND status = 'card' AND decision_id = ?"
        args.append(from_card)
    if from_run is not None:
        where += " AND status = 'working' AND started_at = ?"
        args.append(from_run)
    try:
        with _connect(db_path) as conn:
            cur = conn.execute(
                f"UPDATE {STATE_TABLE} SET status = ?, outcome = ?, done = ?, decision_id = ?, finished_at = ? "  # noqa: S608
                + where,
                (status, line(outcome, 300), json.dumps([[line(t, 160), _link(u)] for t, u in (done or [])][:4]),
                 decision_id, _now().isoformat(), *args),
            )
            return cur.rowcount > 0
    except (sqlite3.Error, OSError):
        logger.warning("take_on: couldn't record %s finishing", key, exc_info=True)
        return False


def _settle_stale(conn: sqlite3.Connection, person: str, key: str, now: datetime) -> None:
    """Write down what the list already shows for a run left working past
    ``config.background_turn_limit`` (a restart mid-turn): failed, aging from
    when it began. Hide, Or tell me and its Undo then treat it as the failed
    step it reads as."""
    conn.execute(
        f"UPDATE {STATE_TABLE} SET status = 'failed', outcome = ? "  # noqa: S608
        "WHERE person = ? AND key = ? AND status = 'working' AND started_at < ?",
        (STALE_OUTCOME, person, key, (now - background_turn_limit()).isoformat()),
    )


def hide(email: str, offer: Offer | str, reason: HideReason, *, from_card: int | None = None,
         db_path: Path | None = None) -> bool:
    """Hide an offer. Given only a key that's already off the list: when it
    was put aside with a Hide (on another screen), its row keeps the newer
    reason, so a Later turned into Not useful doesn't come back. One that left
    another way (done, skipped, aged out) stays as it is, as does one Do it
    has started, unless it is on the card ``from_card`` (a Skip there).
    Returns whether a row took the reason."""
    person, now = who(email), _now()
    if not person:
        return False
    with _connect(db_path) as conn:
        _settle_stale(conn, person, offer if isinstance(offer, str) else offer.key, now)
        if isinstance(offer, str):
            cur = conn.execute(
                f"UPDATE {STATE_TABLE} SET hidden_reason = ?, hidden_at = ? "  # noqa: S608
                "WHERE person = ? AND key = ? AND status IN ('', 'failed', 'asked') "
                "AND hidden_reason IN ('not_useful', 'already_done', 'later')",
                (reason, now.isoformat(), person, offer),
            )
            return cur.rowcount > 0
        _ensure(conn, person, offer.key, offer.kind, now)
        # What it was about stays with it, wherever it came from, so the same
        # thing in other words isn't offered again (``close_to``).
        # Not once Do it has started it (on another screen meanwhile): the
        # step carries on and shows what came of it.
        cur = conn.execute(
            f"UPDATE {STATE_TABLE} SET hidden_reason = ?, hidden_at = ?, kind = ?, "  # noqa: S608
            "offer = COALESCE(offer, ?) WHERE person = ? AND key = ? "
            "AND (status NOT IN ('working', 'card', 'done') OR (status = 'card' AND decision_id = ?))",
            (reason, now.isoformat(), offer.kind, _snapshot(offer), person, offer.key, from_card),
        )
        if not cur.rowcount:
            return False
        if reason == "later":
            # Later on one that failed or asked back puts it back as it was
            # first offered, so it comes back in a week like any other.
            conn.execute(
                f"UPDATE {STATE_TABLE} SET status = '', finished_at = NULL, outcome = '', done = '[]', "  # noqa: S608
                "session_id = NULL, decision_id = NULL WHERE person = ? AND key = ? AND status IN ('failed', 'asked')",
                (person, offer.key),
            )
    return True


def change(email: str, offer: Offer, title: str, preview: list[tuple[str, str]], said: str,
           *, kind: str | None = None, db_path: Path | None = None) -> bool:
    """Or tell me: keep the offer as they changed it (and, when they asked
    for another kind of step, that kind). Not once Do it has started it.
    Returns whether it was changed."""
    person, now = who(email), _now()
    changed: dict[str, Any] = {"title": line(title, TITLE_MAX), "preview": preview}
    if kind in KINDS and kind != offer.kind:
        changed["kind"] = kind
    with _connect(db_path) as conn:
        _ensure(conn, person, offer.key, offer.kind, now)
        _settle_stale(conn, person, offer.key, now)
        cur = conn.execute(
            f"UPDATE {STATE_TABLE} SET changed = ?, said = ? WHERE person = ? AND key = ? "  # noqa: S608
            "AND status NOT IN ('working', 'card', 'done')",
            (json.dumps(changed), line(said, 400), person, offer.key),
        )
        return cur.rowcount > 0


def undo_change(email: str, key: str, *, db_path: Path | None = None) -> None:
    person = who(email)
    with _connect(db_path) as conn:
        _settle_stale(conn, person, key, _now())
        conn.execute(
            f"UPDATE {STATE_TABLE} SET changed = NULL, said = '' WHERE person = ? AND key = ? "  # noqa: S608
            "AND status NOT IN ('working', 'card', 'done')",
            (person, key),
        )


def ask_text(offer: Offer) -> str:
    """What Do it asks for: only what the card shows, so the step is the
    one they saw."""
    lines = [offer.title]
    lines += [f"{label}: {value}" for label, value in offer.preview]
    if offer.source:
        lines.append(f"Why you offered it: {offer.source}" + (f" ({offer.source_detail})" if offer.source_detail else ""))
    return "\n".join(lines)


__all__ = [
    "KEEP_DAYS",
    "KINDS",
    "KIND_LABEL",
    "Hooks",
    "Offer",
    "Source",
    "about",
    "about_already",
    "ask_text",
    "cadence_words",
    "change",
    "clean_preview",
    "close_to",
    "find",
    "finish",
    "finished",
    "hide",
    "hooks",
    "last_pass",
    "line",
    "made_key",
    "offers_for",
    "put_made",
    "on_list",
    "recent_titles",
    "record_pass",
    "register_source",
    "set_session",
    "source_for",
    "start",
    "turned_down_kinds",
    "undo_change",
    "who",
]
